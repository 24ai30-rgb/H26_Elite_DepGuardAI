"""DepGuard web dashboard backend (stdlib only, localhost only).

Safety model:
* binds 127.0.0.1; rejects foreign Host/Origin headers (DNS-rebinding / CSRF); POSTs need an `X-DepGuard` header
* no shell/command endpoint: the only subprocess work is the fixed DepGuard pipeline (`cli.analyze`)
* repository paths must resolve inside an allow-list (project root + `--allow` paths)
* apply: edit sets are looked up from the capsule on disk by id (never taken from the browser), re-validated,
  need typed confirmation, are written only inside an allowed root, and originals are backed up first
"""
import argparse
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .. import acquire, advisor, capsule as capsule_mod, cli, compare as compare_mod, health, history, indicators, metadata, migrate, repo_input, report_html, scanner, usage, verify
from ..git_integration.github import GitHubError, default_opener
from ..prepublish import secrets as secrets_mod
from . import api, project as project_mod

STATIC = Path(__file__).parent / "static"
PROJECT = Path(__file__).resolve().parents[2]
RUNS = Path(__file__).parent / "runs"
MODEL = "gemma4:e2b"


def experiments_dir() -> Path:
    return RUNS / "experiments"


def acquired_dir() -> Path:
    return RUNS / "acquired"


class State:
    def __init__(self, allowed, apply_roots=None, offline=False):
        self.allowed = [Path(p).resolve() for p in allowed]
        # Apply/rollback are limited to isolated experiment copies (plus explicit --allow-apply paths), never the scanned repo by default.
        self.extra_apply = [Path(p).resolve() for p in (apply_roots or [])]
        self.offline = offline
        self.lock = threading.Lock()
        self.capsule_path = PROJECT / "demo-output" / "capsule.json"
        self.capsule_label = "committed demo capsule"
        self.scan = None
        self.job = None
        self.events = []
        self.projects, self.project = {}, None          # repository inputs opened in this session
        self.gh, self.gh_login, self.gh_opener = None, None, default_opener   # GitHub client (token lives only here, in memory)
        self.model, self.propose_fn, self.verify_fn = MODEL, advisor.propose_repair, None   # injectable in tests

    def runs(self):
        return RUNS

    def set_phase(self, msg):
        """What the running job is doing right now (shown in the UI; never contains repository text beyond package names)."""
        j = self.job
        if j and j.get("status") == "running":
            j["phase"] = secrets_mod.redact(str(msg))[:200]

    def meta_path(self):
        return RUNS / "metadata-cache.json"

    def history_path(self):
        return RUNS / "history.json"

    def log(self, kind, msg):
        self.events.insert(0, {"time": datetime.now(timezone.utc).isoformat(timespec="seconds"), "kind": kind, "message": secrets_mod.redact(str(msg))})
        del self.events[50:]

    def resolve_repo(self, raw: str) -> Path:
        """Return a directory inside an allowed root, or raise ValueError."""
        p = Path(raw).expanduser().resolve() if raw else self.allowed[0]
        data_roots = [(RUNS / d).resolve() for d in ("acquired", "uploads", "github", "work")]
        if not any(p == r or p.is_relative_to(r) for r in [*self.allowed, *data_roots]):
            raise ValueError("path is outside the allowed roots: " + ", ".join(map(str, self.allowed)))
        if not p.is_dir():
            raise ValueError("not a directory")
        return p


def resolve_apply_dir(st, raw: str) -> Path:
    if not str(raw).strip():
        raise ValueError("workdir is required (no default: choose the working copy explicitly)")
    p = Path(raw).expanduser().resolve()
    roots = [experiments_dir().resolve(), *st.extra_apply]
    if not any(p == r or p.is_relative_to(r) for r in roots):
        raise ValueError("changes can only be applied to an isolated experiment copy. Create one with 'Create experiment copy', "
                         "or start the server with --allow-apply PATH for another working copy. Allowed: " + ", ".join(map(str, roots)))
    if not p.is_dir():
        raise ValueError("not a directory")
    return p


def history_path() -> Path:
    return RUNS / "history.json"


def meta_path() -> Path:
    return RUNS / "metadata-cache.json"


def git(*args):
    try:
        r = subprocess.run(["git", "-C", str(PROJECT), *args], capture_output=True, text=True, timeout=15)
        return r.stdout.strip() if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def linkwatch_cache_file() -> Path:
    d = os.environ.get("LINKWATCH_CACHE_DIR")
    if not d:
        try:
            from appdirs import user_cache_dir  # same logic as linkwatch/cache.py when installed
            d = user_cache_dir("linkwatch")
        except ImportError:
            home = Path.home()
            d = (os.path.join(os.environ.get("LOCALAPPDATA", home), "linkwatch", "linkwatch", "Cache") if sys.platform == "win32"
                 else str(home / "Library" / "Caches" / "linkwatch") if sys.platform == "darwin" else str(home / ".cache" / "linkwatch"))
    return Path(d) / "results.json"


def load_capsule(st):
    p = st.capsule_path
    if not p.exists():
        return None, {"valid": None, "error": f"{p.name} not found"}, ""
    try:
        cap = json.loads(p.read_text(encoding="utf-8"))
    except ValueError as e:
        return None, {"valid": False, "error": f"not valid JSON: {e}"}, ""
    try:
        capsule_mod.validate(cap)
        v = {"valid": True, "error": None}
    except Exception as e:  # jsonschema.ValidationError or secret-guard ValueError
        v = {"valid": False, "error": str(e).splitlines()[0][:300]}
    pr = st.project
    if pr and pr.capsule_path and Path(pr.capsule_path).resolve() == p.resolve() and v["valid"]:   # show the capsule WITH the user's current decisions, patches and checks
        try:
            cap = project_mod.live_capsule(pr, cap)
            capsule_mod.validate(cap)
            return cap, v, capsule_mod.to_markdown(cap)
        except Exception as e:
            return cap, {"valid": False, "error": "live capsule invalid: " + str(e).splitlines()[0][:300]}, ""
    md = p.with_name("capsule.md")
    return cap, v, md.read_text(encoding="utf-8") if md.exists() else capsule_mod.to_markdown(cap) if v["valid"] else ""


def capsule_health(cap):
    names = {d["name"] for d in cap["dependencies"]["items"]}
    return health.from_capsule(cap, {**cap.get("package_metadata", {}), **metadata.cached(names, meta_path())})


def network_payload(st):
    cache = metadata.load_cache(meta_path())
    ok = [v for v in cache.values() if v.get("status") == "ok"]
    return {"offline_flag": st.offline, "model_runs_locally": True, "metadata_requires_network": True, "metadata_auto_fetch": False,
            "cache_entries": len(ok), "cache_newest": max((v["fetched_at"] for v in ok), default=None), "cache_file": str(meta_path()),
            "note": "Scanning, reports and Gemma 4 advice work offline. Package metadata (release recency, Requires-Python, requirements) is only fetched "
                    "from the package index when you click 'Fetch metadata' and is cached with its source and time. Full analysis installs packages with pip, which needs network."}


def ai_candidates(cap):
    out = {}
    for r in (cap.get("ai_recommendations", {}).get("recommendations") or []):
        names, bad = compare_mod.candidate_names(r["replacement_candidates"])
        out[r["package"].lower()] = {"names": names, "unparseable": bad, "raw": r["replacement_candidates"]}
    return out


def state_payload(st):
    cap, valid, md = load_capsule(st)
    cache = linkwatch_cache_file()
    recs, hwarn = history.load(history_path())
    return {
        "project_root": str(PROJECT), "allowed_roots": [str(p) for p in st.allowed],
        "apply_roots": [str(experiments_dir()), *map(str, st.extra_apply)],
        "capsule": cap, "capsule_markdown": md, "validation": valid, "health": capsule_health(cap) if cap and valid["valid"] else None,
        "ai_candidates": ai_candidates(cap) if cap else {},
        "metadata": {**((cap or {}).get("package_metadata", {})), **metadata.cached(_names_for(st, cap), meta_path())},
        "capsule_source": {"label": st.capsule_label, "path": str(st.capsule_path.relative_to(PROJECT)) if st.capsule_path.is_relative_to(PROJECT) else str(st.capsule_path)},
        "model": advisor.model_info(MODEL), "network": network_payload(st),
        "history": recs, "history_warning": hwarn,
        "git": {"branch": git("rev-parse", "--abbrev-ref", "HEAD"), "commit": git("rev-parse", "--short", "HEAD"),
                "dirty": bool(git("status", "--porcelain")), "remote": capsule_mod.identity(PROJECT)["remote"],
                "log": (git("log", "-6", "--format=%h|%an|%ad|%s", "--date=short") or "").splitlines()},
        "acquired": acquire.list_acquired(acquired_dir()),
        "project": st.project.summary() if st.project else None, "projects": [p.summary() for p in st.projects.values()],
        "github": {"authenticated": bool(st.gh and st.gh.authenticated), "login": st.gh_login},
        "publish": ({"plan": st.project.plan, "branch": st.project.branch_result, "pr": st.project.pr_result} if st.project else None),
        "repair_settings": ({"max_attempts": st.project.state.max_attempts, "state_warning": st.project.state.warning} if st.project else None),
        "limits": {"max_zip_mb": repo_input.MAX_ZIP_BYTES // 2**20},
        "scan": st.scan, "job": st.job, "events": st.events,
        "python": sys.version.split()[0],
        "linkwatch": link_payload(cache),
    }


def link_payload(cache: Path):
    if not cache.exists():
        return {"available": False, "cache_file": str(cache), "results": []}
    try:
        return {"available": True, "cache_file": str(cache), "results": json.loads(cache.read_text(encoding="utf-8")),
                "file_modified": datetime.fromtimestamp(cache.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")}
    except (OSError, ValueError) as e:
        return {"available": False, "cache_file": str(cache), "results": [], "error": str(e)}


def do_scan(st, raw):
    root = st.resolve_repo(raw)
    ident = capsule_mod.identity(root)
    try:
        scan = scanner.scan(root)
        use = usage.map_usage(root, {d["name"] for d in scan["dependencies"]})
        ind = indicators.build(scan, use, None)
        names = {d["name"] for d in scan["dependencies"]}
        h = health.compute(names, ind["indicators"], None, metadata=metadata.cached(names, meta_path()))
    except Exception as e:  # recorded in history, then surfaced to the UI
        history.add(history_path(), "scan", "error", {**ident, "path": str(root)}, {}, error=f"{type(e).__name__}: {e}")
        raise ValueError(f"scan failed: {e}")
    st.scan = {"path": str(root), "time": datetime.now(timezone.utc).isoformat(timespec="seconds"), "scan": scan,
               "usage": use, "risk_evidence": ind, "health": h,
               "note": "Live deterministic scan only: no tests were run and no AI was used, so resolved versions and test-derived indicators are unavailable."}
    lv = [(p, v["level"]) for p, v in ind["package_levels"].items() if p in names]
    history.add(history_path(), "scan", "ok", {**ident, "path": str(root)}, history.summarize(lv, len(use["undeclared_imports"]), h))
    st.log("scan", f"Scanned {root.name}: {len(scan['dependencies'])} declared entries")
    return st.scan


def proposal_edits(cap, pid):
    if pid == "ai":
        return cap["migration_proposal"]["accepted_edits"]
    if pid.startswith("exp:"):
        return cap["independent_experiments"][int(pid[4:])]["edits"]
    raise ValueError("unknown proposal id")


def do_acquire(st, body):
    if st.offline:
        raise ValueError("offline mode: downloading from GitHub needs a network connection")
    res = acquire.clone(str(body.get("url", "")), acquired_dir())
    metadata.ensure_ignored(RUNS)
    st.log("acquire", f"Cloned {res['url']} ({res['size_mb']} MB) into a temporary folder; not executed")
    return res


def do_acquire_remove(st, body):
    acquire.remove(Path(str(body.get("path", ""))), acquired_dir())
    st.log("acquire", "Removed a downloaded repository")
    return {"ok": True}


def do_experiment(st, body):
    root = st.resolve_repo(body.get("repo", ""))
    dest = experiments_dir() / f"{root.name}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    metadata.ensure_ignored(RUNS)
    shutil.copytree(root, dest, ignore=verify.IGNORE)
    st.log("experiment", f"Created isolated experiment copy {dest}")
    return {"path": str(dest), "note": "Copy excludes .git, virtualenvs and caches. The original is untouched."}


def do_apply(st, body):
    cap, _, _ = load_capsule(st)
    if not cap:
        raise ValueError("no capsule loaded")
    if body.get("confirm") != "APPLY":
        raise ValueError("explicit confirmation missing: type APPLY")
    root = resolve_apply_dir(st, body.get("workdir", ""))
    res = migrate.apply_with_snapshot(root, proposal_edits(cap, str(body.get("proposal", ""))))
    st.log("apply", f"Applied proposal {body['proposal']} to {root} (snapshot: {res['backup']}; diff matches preview: {res['diff_matches_preview']})")
    return res


def do_rollback(st, body):
    if body.get("confirm") != "ROLLBACK":
        raise ValueError("explicit confirmation missing: type ROLLBACK")
    root = resolve_apply_dir(st, body.get("workdir", ""))
    res = migrate.rollback(root, body.get("backup", ""))
    st.log("rollback", f"Restored {', '.join(res['restored'])} in {root}")
    return res


def _names_for(st, cap):
    names = set()
    if cap:
        names |= {d["name"] for d in cap["dependencies"]["items"]}
    if st.scan:
        names |= {d["name"] for d in st.scan["scan"]["dependencies"]}
    return sorted(names)


def do_fetch_metadata(st, body):
    cap, _, _ = load_capsule(st)
    names = body.get("names") or _names_for(st, cap)
    if not isinstance(names, list) or not names or len(names) > 60:
        raise ValueError("provide 1-60 package names (or load a capsule / scan first)")
    res = metadata.fetch([str(n) for n in names], meta_path(), offline=st.offline, refresh=bool(body.get("refresh")))
    ok = sum(v["status"] == "ok" for v in res.values())
    st.log("metadata", f"Package metadata: {ok}/{len(res)} available" + (" (offline mode: cache only)" if st.offline else ""))
    return {"entries": res, "ok": ok, "total": len(res), "offline": st.offline}


def do_compare(st, body):
    pkg = str(body.get("package", "")).strip().lower()
    if not metadata.valid_name(pkg):
        raise ValueError("invalid package name")
    cands, rejected = compare_mod.candidate_names(body.get("candidates") or [])
    cands = cands[:6]
    cap, valid, _ = load_capsule(st)
    patches = []
    if cap and valid["valid"]:
        deps, use, exps = cap["dependencies"]["items"], cap["usage"]["usages"], cap.get("independent_experiments", [])
        patches = (cap.get("repair") or {}).get("patches", [])
    elif st.scan:
        deps, use, exps = st.scan["scan"]["dependencies"], st.scan["usage"]["usages"], []
    else:
        raise ValueError("load a capsule or run a scan first")
    names = [pkg, *cands]
    meta = metadata.cached(names, meta_path())
    if body.get("fetch"):
        meta.update(metadata.fetch(names, meta_path(), offline=st.offline))
    res = compare_mod.compare(pkg, cands, use.get(pkg, []), deps, meta, exps, patches)
    res["rejected_candidates"] = rejected
    res["metadata_fetched_now"] = bool(body.get("fetch")) and not st.offline
    res["offline"] = st.offline
    return res


def do_history_delete(st, body):
    if not history.delete(history_path(), str(body.get("id", ""))):
        raise ValueError("record not found")
    st.log("history", "Removed one history record (capsule files untouched)")
    return {"ok": True}


def do_history_open(st, body):
    rec = history.get(history_path(), str(body.get("id", "")))
    if not rec or not rec.get("capsule"):
        raise ValueError("this record has no capsule (scan-only records keep a summary, not a capsule)")
    p = Path(rec["capsule"]).resolve()
    if not (p.is_relative_to(RUNS.resolve()) or p.is_relative_to((PROJECT / "demo-output").resolve())) or not p.is_file():
        raise ValueError("capsule is missing or outside the dashboard's data folders")
    st.capsule_path, st.capsule_label = p, f"history record {rec['id']} ({rec['time']})"
    return {"ok": True}


def build_report(st, fmt, package=None):
    cap, valid, _ = load_capsule(st)
    if not cap or not valid["valid"]:
        raise ValueError("no valid capsule to export: " + str(valid.get("error")))
    name = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in cap["repository"]["path_name"]) or "capsule"
    if package:   # the slice of the capsule that concerns ONE dependency (validated against the same schema)
        if not metadata.valid_name(package) or package.lower() not in {f["package"] for f in cap.get("findings", [])}:
            raise ValueError("no finding for that package in this capsule")
        cap = capsule_mod.per_dependency(cap, package)
        capsule_mod.validate(cap)
        name += "-" + package.lower()
    if fmt == "json":
        return json.dumps(cap, indent=2).encode(), "application/json", f"{name}-capsule.json"
    if fmt == "md":
        return capsule_mod.to_markdown(cap).encode(), "text/markdown; charset=utf-8", f"{name}-capsule.md"
    if fmt == "html":
        return report_html.to_html(cap).encode(), "text/html; charset=utf-8", f"{name}-capsule.html"
    raise ValueError("format must be html, json or md")


GH_STATUS = {"auth": 401, "permission": 403, "not_found": 404, "rate_limit": 429, "network": 502, "api": 502, "conflict": 409, "invalid": 400}


def make_handler(st, port):
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    class H(BaseHTTPRequestHandler):
        server_version = "DepGuardWeb"

        def log_message(self, *a):  # keep the terminal quiet
            pass

        def _send(self, code, body, ctype="application/json", filename=None):
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            if filename:  # downloads: never rendered inline by the dashboard origin
                self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:")
            self.end_headers()
            self.wfile.write(data)

        def _guard(self, post):
            if self.headers.get("Host") not in hosts:
                self._send(403, {"error": "bad Host header"})
                return False
            origin = self.headers.get("Origin")
            if post and (self.headers.get("X-DepGuard") != "1" or (origin and urlparse(origin).netloc not in hosts)):
                self._send(403, {"error": "cross-origin or missing X-DepGuard header"})
                return False
            return True

        def do_GET(self):
            if not self._guard(False):
                return
            path = urlparse(self.path).path
            if path == "/api/state":
                return self._send(200, state_payload(st))
            if path == "/api/health":
                return self._send(200, {"ok": True})
            if path == "/api/patch/export":
                try:
                    pid = parse_qs(urlparse(self.path).query).get("id", [""])[0]
                    text = project_mod.export_patch(api.active(st), pid)
                except (ValueError, KeyError) as e:
                    return self._send(409, {"error": str(e)})
                return self._send(200, text.encode("utf-8"), "text/x-diff; charset=utf-8", filename=f"depguard-{re.sub(r'[^A-Za-z0-9._-]', '_', pid)}.patch")
            if path == "/api/report":
                try:
                    q = parse_qs(urlparse(self.path).query)
                    data, ctype, name = build_report(st, q.get("format", ["html"])[0], (q.get("package") or [None])[0])
                except ValueError as e:
                    return self._send(409, {"error": str(e)})
                return self._send(200, data, ctype, filename=name)
            f = (STATIC / ("index.html" if path == "/" else path.lstrip("/"))).resolve()
            if f.is_file() and f.is_relative_to(STATIC):
                return self._send(200, f.read_bytes(), mimetypes.guess_type(f.name)[0] or "application/octet-stream")
            self._send(404, {"error": "not found"})

        def do_POST(self):
            if not self._guard(True):
                return
            try:
                n = int(self.headers.get("Content-Length") or 0)
                route = urlparse(self.path).path
                if route == "/api/input/zip":   # raw bytes (application/zip), not JSON
                    if n > repo_input.MAX_ZIP_BYTES:
                        return self._send(413, {"error": f"archive is larger than {repo_input.MAX_ZIP_BYTES // 2**20} MB"})
                    data = self.rfile.read(n)
                    if len(data) != n or n == 0:
                        return self._send(400, {"error": "incomplete or empty upload"})
                    return self._send(200, api.input_zip(st, data, self.headers.get("X-Filename") or "upload.zip"))
                body = json.loads(self.rfile.read(min(n, 65536)) or b"{}")
                if route == "/api/scan":
                    return self._send(200, do_scan(st, body.get("path", "")))
                if route == "/api/analyze":
                    return self._send(202, api.start_analysis(st, body))
                if route in api.ROUTES:
                    return self._send(202 if route in ("/api/repair/start", "/api/repair/batch", "/api/patch/verify", "/api/patch/revise") else 200, api.ROUTES[route](st, body))
                routes = {"/api/apply": do_apply, "/api/rollback": do_rollback, "/api/experiment/create": do_experiment, "/api/acquire": do_acquire, "/api/acquire/remove": do_acquire_remove,
                          "/api/metadata/fetch": do_fetch_metadata, "/api/compare": do_compare,
                          "/api/history/delete": do_history_delete, "/api/history/open": do_history_open}
                if route in routes:
                    return self._send(200, routes[route](st, body))
                if route == "/api/capsule/select":
                    which = body.get("which")
                    if which == "demo":
                        st.capsule_path, st.capsule_label = PROJECT / "demo-output" / "capsule.json", "committed demo capsule"
                    elif which == "latest":
                        runs = sorted(RUNS.glob("*/capsule.json"))
                        if not runs:
                            raise ValueError("no analysis run from this dashboard yet")
                        st.capsule_path, st.capsule_label = runs[-1], "latest analysis run from this dashboard"
                    else:
                        raise ValueError("which must be 'demo' or 'latest'")
                    return self._send(200, {"ok": True})
                self._send(404, {"error": "not found"})
            except GitHubError as e:
                self._send(GH_STATUS.get(e.kind, 502), {"error": secrets_mod.redact(str(e)), "kind": e.kind})
            except (ValueError, KeyError, IndexError, OSError) as e:
                self._send(400, {"error": secrets_mod.redact(str(e))})

    return H


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m depguard.web", description="DepGuard AI dashboard (localhost only)")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--allow", action="append", default=[], help="extra repository root the UI may scan (repeatable)")
    ap.add_argument("--allow-apply", action="append", default=[], help="extra working copy the UI may apply/rollback changes in (repeatable); "
                    "by default only isolated experiment copies created by the dashboard are allowed")
    ap.add_argument("--offline", action="store_true", help="never contact the package index; metadata comes from the local cache only")
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args(argv)
    RUNS.mkdir(parents=True, exist_ok=True)
    metadata.ensure_ignored(RUNS)
    st = State([PROJECT, *a.allow], a.allow_apply, a.offline)
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(st, a.port))
    url = f"http://127.0.0.1:{a.port}/"
    print(f"DepGuard dashboard: {url}  (Ctrl+C to stop)\nScan roots: {', '.join(map(str, st.allowed))}\n"
          f"Apply roots: {experiments_dir()}" + "".join(", " + str(p) for p in st.extra_apply)
          + ("\nOFFLINE: the package index is never contacted" if st.offline else ""))
    if not a.no_browser:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0
