"""Dashboard server safety + API tests (real HTTP on an ephemeral localhost port, no AI, no test runs, no network)."""
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from depguard import capsule, cli, metadata
from depguard.web import server

EVIL = '<script>alert(1)</script>'


def make_app(tmp_path, monkeypatch, offline=False, extra_apply=None):
    monkeypatch.setattr(server, "RUNS", tmp_path / "runs")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "requirements.txt").write_text("jinja2==2.11.3\nsix\n")
    (repo / "pyproject.toml").write_text('[project]\nname="x"\ndependencies=["jinja2==2.11.3"]\n')
    (repo / "a.py").write_text("from jinja2 import Environment\n")
    cap = cli.analyze(repo, tmp_path / "out", "m", use_ai=False, run_tests=False, meta_cache=tmp_path / "nometa.json")
    cap["independent_experiments"] = [{
        "name": "t", "origin": "test", "diff": "", "comparison": {"baseline_counts": {"passed": 9, "failed": 1}, "proposed_counts": {"passed": 10, "failed": 0}},
        "baseline": {"status": "not_run"}, "proposed": {"status": "not_run"},
        "edits": [{"file": "requirements.txt", "find": "jinja2==2.11.3", "replace": "jinja2==3.1.6"}]}]
    capsule.write(cap, tmp_path / "out")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "requirements.txt").write_text("jinja2==2.11.3\n")
    st = server.State([repo], extra_apply, offline)
    st.capsule_path = tmp_path / "out" / "capsule.json"
    srv = ThreadingHTTPServer(("127.0.0.1", 0), None)
    port = srv.server_address[1]
    srv.RequestHandlerClass = server.make_handler(st, port)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, {"url": f"http://127.0.0.1:{port}", "repo": repo, "outside": outside, "st": st, "tmp": tmp_path}


@pytest.fixture
def app(tmp_path, monkeypatch):
    srv, a = make_app(tmp_path, monkeypatch)
    yield a
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def offline_app(tmp_path, monkeypatch):
    srv, a = make_app(tmp_path, monkeypatch, offline=True)
    yield a
    srv.shutdown()
    srv.server_close()


def raw(app, path, body=None, headers=None):
    h = {"Content-Type": "application/json", **(headers or {})}
    if body is not None:
        h.setdefault("X-DepGuard", "1")
    req = urllib.request.Request(app["url"] + path, data=json.dumps(body).encode() if body is not None else None, headers=h)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.read(), r.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers


def call(app, path, body=None, headers=None):
    code, data, _ = raw(app, path, body, headers)
    return code, json.loads(data or b"{}")


def experiment(app):
    code, d = call(app, "/api/experiment/create", {"repo": str(app["repo"])})
    assert code == 200
    return d["path"]


# ---------------- basics / safety
def test_state_serves_real_capsule_validation_health_and_network(app):
    code, d = call(app, "/api/state")
    assert code == 200 and d["validation"]["valid"] is True
    assert d["capsule"]["repository"]["path_name"] == "repo" and d["capsule_markdown"]
    assert d["health"]["kind"] == "heuristic" and 0 <= d["health"]["score"] <= 100
    n = d["network"]
    assert n["metadata_requires_network"] and n["metadata_auto_fetch"] is False and n["model_runs_locally"] and n["offline_flag"] is False


def test_index_and_static_served_traversal_blocked(app):
    for p in ("/", "/app.js", "/app.css"):
        assert urllib.request.urlopen(app["url"] + p).status == 200
    assert call(app, "/..%2f..%2fserver.py")[0] == 404
    assert call(app, "/nope")[0] == 404


def test_foreign_host_and_csrf_rejected(app):
    assert call(app, "/api/state", headers={"Host": "evil.com"})[0] == 403
    assert call(app, "/api/scan", {"path": ""}, headers={"X-DepGuard": "0"})[0] == 403
    assert call(app, "/api/scan", {"path": ""}, headers={"Origin": "http://evil.com"})[0] == 403
    for route in ("/api/apply", "/api/rollback", "/api/history/delete", "/api/metadata/fetch", "/api/compare", "/api/experiment/create"):
        assert call(app, route, {}, headers={"X-DepGuard": "0"})[0] == 403


def test_scan_restricted_to_allowed_roots_and_recorded_in_history(app):
    code, d = call(app, "/api/scan", {"path": str(app["outside"])})
    assert code == 400 and "outside the allowed roots" in d["error"]
    assert call(app, "/api/scan", {"path": str(app["repo"] / ".." / "outside")})[0] == 400
    assert call(app, "/api/scan", {"path": str(app["repo"] / "a.py")})[0] == 400
    assert call(app, "/api/state")[1]["history"] == []  # rejected scans leave no history
    code, d = call(app, "/api/scan", {"path": str(app["repo"])})
    assert code == 200 and d["usage"]["usages"]["jinja2"][0]["line"] == 1 and d["health"]["kind"] == "heuristic"
    h = call(app, "/api/state")[1]["history"]
    assert len(h) == 1 and h[0]["kind"] == "scan" and h[0]["status"] == "ok" and h[0]["summary"]["dependencies"] == 2
    assert "from jinja2" not in json.dumps(h) and "Environment" not in json.dumps(h)  # no source contents


def test_invalid_capsule_reported_not_hidden(app):
    p = app["st"].capsule_path
    cap = json.loads(p.read_text())
    del cap["limitations"]
    p.write_text(json.dumps(cap))
    d = call(app, "/api/state")[1]
    assert d["validation"]["valid"] is False and "limitations" in d["validation"]["error"] and d["health"] is None
    assert call(app, "/api/report?format=html")[0] == 409  # invalid capsules are never exported
    p.write_text("{not json")
    assert call(app, "/api/state")[1]["validation"]["valid"] is False


def test_linkwatch_results_missing_and_present(app, tmp_path, monkeypatch):
    monkeypatch.setenv("LINKWATCH_CACHE_DIR", str(tmp_path / "none"))
    assert call(app, "/api/state")[1]["linkwatch"]["available"] is False
    (tmp_path / "lw").mkdir()
    (tmp_path / "lw" / "results.json").write_text(json.dumps([{"name": "a", "up": True}]))
    monkeypatch.setenv("LINKWATCH_CACHE_DIR", str(tmp_path / "lw"))
    lw = call(app, "/api/state")[1]["linkwatch"]
    assert lw["available"] and lw["results"][0]["name"] == "a"


# ---------------- migration: isolated apply, snapshot, rollback
def test_apply_refused_on_the_scanned_repo_and_outside_roots_and_without_confirmation(app):
    req = {"proposal": "exp:0", "workdir": str(app["repo"]), "confirm": "APPLY"}
    code, d = call(app, "/api/apply", req)
    assert code == 400 and "isolated experiment copy" in d["error"]
    assert call(app, "/api/apply", {**req, "workdir": str(app["outside"])})[0] == 400
    assert call(app, "/api/apply", {**req, "workdir": ""})[0] == 400
    exp = experiment(app)
    assert call(app, "/api/apply", {"proposal": "exp:0", "workdir": exp})[0] == 400
    assert call(app, "/api/apply", {"proposal": "exp:0", "workdir": exp, "confirm": "yes"})[0] == 400
    assert "2.11.3" in (app["repo"] / "requirements.txt").read_text() and "2.11.3" in (Path(exp) / "requirements.txt").read_text()


def test_experiment_copy_is_isolated_and_inside_the_apply_root(app):
    exp = Path(experiment(app))
    assert exp.is_relative_to((app["tmp"] / "runs" / "experiments").resolve()) and (exp / "a.py").exists() and not (exp / ".git").exists()
    assert call(app, "/api/experiment/create", {"repo": str(app["outside"])})[0] == 400


def test_apply_in_experiment_uses_server_side_edits_snapshots_verifies_diff_and_rolls_back(app):
    exp = experiment(app)
    code, d = call(app, "/api/apply", {"proposal": "exp:0", "workdir": exp, "confirm": "APPLY",
                                       "edits": [{"file": "a.py", "find": "import", "replace": "evil"}]})
    assert code == 200 and d["applied"] == ["requirements.txt"] and d["diff_matches_preview"] is True
    assert "jinja2==3.1.6" in (Path(exp) / "requirements.txt").read_text()
    assert (Path(exp) / "a.py").read_text() == "from jinja2 import Environment\n"  # client-supplied edit ignored
    assert "2.11.3" in (app["repo"] / "requirements.txt").read_text()  # the scanned original is untouched
    assert call(app, "/api/apply", {"proposal": "bogus", "workdir": exp, "confirm": "APPLY"})[0] == 400
    assert call(app, "/api/apply", {"proposal": "exp:0", "workdir": exp, "confirm": "APPLY"})[0] == 400  # stale second apply refused
    req = {"workdir": exp, "backup": d["backup"]}
    assert call(app, "/api/rollback", req)[0] == 400  # needs ROLLBACK
    code, r = call(app, "/api/rollback", {**req, "confirm": "ROLLBACK"})
    assert code == 200 and r["restored"] == ["requirements.txt"] and "2.11.3" in (Path(exp) / "requirements.txt").read_text()


def test_rollback_rejects_foreign_snapshots_and_workdirs(app):
    exp = experiment(app)
    assert call(app, "/api/rollback", {"workdir": exp, "backup": str(app["outside"]), "confirm": "ROLLBACK"})[0] == 400
    assert call(app, "/api/rollback", {"workdir": str(app["repo"]), "backup": str(app["repo"]), "confirm": "ROLLBACK"})[0] == 400


def test_allow_apply_flag_extends_apply_roots(tmp_path, monkeypatch):
    srv, a = make_app(tmp_path, monkeypatch, extra_apply=[tmp_path / "repo"])
    try:
        code, d = call(a, "/api/apply", {"proposal": "exp:0", "workdir": str(a["repo"]), "confirm": "APPLY"})
        assert code == 200 and "3.1.6" in (a["repo"] / "requirements.txt").read_text()
    finally:
        srv.shutdown()
        srv.server_close()


# ---------------- history
def test_history_open_delete_and_corruption(app):
    st = app["st"]
    from depguard import history
    rid = history.add(server.history_path(), "analysis", "ok", {"path_name": "repo"}, {"dependencies": 2}, capsule=str(st.capsule_path.resolve()))
    scan_id = history.add(server.history_path(), "scan", "ok", {"path_name": "repo"}, {})
    assert call(app, "/api/history/open", {"id": scan_id})[0] == 400  # scan-only record has no capsule
    assert call(app, "/api/history/open", {"id": "nope"})[0] == 400
    history.add(server.history_path(), "analysis", "ok", {}, {}, capsule=str(app["outside"] / "requirements.txt"))
    evil = history.load(server.history_path())[0][0]["id"]
    assert call(app, "/api/history/open", {"id": evil})[0] == 400  # capsule path outside dashboard data folders
    st.capsule_path = app["tmp"] / "elsewhere.json"
    # opening a record inside the dashboard's data folders works only for capsules under runs/ or demo-output
    assert call(app, "/api/history/open", {"id": rid})[0] == 400
    assert call(app, "/api/history/delete", {"id": scan_id})[0] == 200
    assert call(app, "/api/history/delete", {"id": scan_id})[0] == 400  # already gone
    assert call(app, "/api/history/delete", {"id": "../../x"})[0] == 400
    assert Path(st.capsule_path).name == "elsewhere.json"
    server.history_path().write_text("{corrupt")
    d = call(app, "/api/state")[1]
    assert d["history"] == [] and "unreadable" in d["history_warning"]


# ---------------- metadata, offline, compare
def test_metadata_offline_mode_never_fetches_and_says_so(offline_app):
    code, d = call(offline_app, "/api/metadata/fetch", {"names": ["jinja2"]})
    assert code == 200 and d["offline"] is True and d["ok"] == 0
    e = d["entries"]["jinja2"]
    assert e["status"] == "unavailable" and "offline" in e["error"]
    assert call(offline_app, "/api/state")[1]["network"]["offline_flag"] is True


def test_metadata_fetch_validates_input_without_network(app, monkeypatch):
    monkeypatch.setattr(metadata, "fetch_one", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network used for an invalid name")))
    assert call(app, "/api/metadata/fetch", {"names": ["x"] * 61})[0] == 400
    assert call(app, "/api/metadata/fetch", {"names": "jinja2"})[0] == 400
    code, d = call(app, "/api/metadata/fetch", {"names": ["../../etc/passwd", "a b"]})
    assert code == 200 and d["ok"] == 0 and all(e["error"] == "invalid package name" for e in d["entries"].values())


def test_compare_uses_cached_metadata_only_unless_fetch_requested(app, monkeypatch):
    cache = server.meta_path()
    metadata.save_cache(cache, {"jinja2": {"name": "jinja2", "status": "ok", "source": "https://pypi.org/pypi/jinja2/json", "fetched_at": "2026-10-15T00:00:00+00:00",
                                           "latest_version": "3.1.6", "latest_upload_time": "2025-03-05T18:10:41Z", "requires_python": ">=3.7",
                                           "requires_dist": ["MarkupSafe>=2.0"], "inactive_classifier": False, "python_classifiers": ["3.12"]}})
    called = []
    monkeypatch.setattr(metadata, "fetch_one", lambda *a, **k: called.append(a) or {})
    code, d = call(app, "/api/compare", {"package": "jinja2", "candidates": ["Jinja2>=3", "mako", "garbage ::"]})
    assert code == 200 and d["columns"] == ["jinja2", "mako"] and d["rejected_candidates"] == ["garbage ::"] and not called
    py = next(c for c in d["criteria"] if c["key"] == "python")["cells"]
    assert "3.12 allowed" in py["jinja2"]["value"] and py["mako"]["basis"] == "unknown"
    api = next(c for c in d["criteria"] if c["key"] == "api")["cells"]
    assert api["mako"]["value"] == "equivalence NOT established" and "Environment" in api["jinja2"]["value"]
    assert call(app, "/api/compare", {"package": "../x", "candidates": []})[0] == 400


def test_compare_fetch_in_offline_mode_does_not_touch_network(offline_app, monkeypatch):
    monkeypatch.setattr(metadata, "fetch_one", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network used")))
    code, d = call(offline_app, "/api/compare", {"package": "jinja2", "candidates": ["mako"], "fetch": True})
    assert code == 200 and d["offline"] is True and d["metadata_fetched_now"] is False
    cell = next(c for c in d["criteria"] if c["key"] == "metadata")["cells"]["mako"]
    assert cell["basis"] == "unknown" and "offline" in cell["value"]


# ---------------- reports
def test_reports_download_all_formats_from_one_capsule_and_escape_html(app):
    code, body, hdr = raw(app, "/api/report?format=json")
    cap = json.loads(body)
    assert code == 200 and "attachment" in hdr["Content-Disposition"] and cap["repository"]["path_name"] == "repo"
    code, body, hdr = raw(app, "/api/report?format=md")
    assert code == 200 and b"Dependency Exit Capsule: repo" in body and hdr["Content-Disposition"].endswith('.md"')
    code, body, hdr = raw(app, "/api/report?format=html")
    assert code == 200 and body.startswith(b"<!doctype html>") and "text/html" in hdr["Content-Type"]
    assert f"{cap['health_score']['score']}/100".encode() in body
    cap["repository"]["path_name"] = EVIL
    cap["usage"]["undeclared_imports"] = [EVIL]
    app["st"].capsule_path.write_text(json.dumps(cap))
    code, body, hdr = raw(app, "/api/report?format=html")
    assert code == 200 and b"<script>alert" not in body and b"&lt;script&gt;" in body
    assert 'filename="' in hdr["Content-Disposition"] and "<" not in hdr["Content-Disposition"]  # sanitized filename
    assert call(app, "/api/report?format=exe")[0] == 409


# ---------------- repository acquisition (GitHub) and the untrusted-code guard
def _fake_clone(monkeypatch, calls=None, files=None):
    from types import SimpleNamespace
    from depguard import acquire
    real = acquire.clone

    def runner(cmd, **kw):
        if calls is not None:
            calls.append(cmd)
        dest = Path(cmd[-1])
        dest.mkdir(parents=True)
        for n, t in (files or {"requirements.txt": "six\njinja2==2.11.3\n", "evil.py": "raise SystemExit('EXECUTED')\n"}).items():
            (dest / n).write_text(t)
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(server.acquire, "clone", lambda url, root: real(url, root, runner=runner))


def test_acquire_rejects_bad_urls_without_running_git(app, monkeypatch):
    calls = []
    _fake_clone(monkeypatch, calls)
    for url in ("https://evil.com/a/b", "http://github.com/a/b", "https://user:pw@github.com/a/b", "git@github.com:a/b.git", ""):
        code, d = call(app, "/api/acquire", {"url": url})
        assert code == 400 and "github.com" in d["error"]
    assert calls == []


def test_acquire_refused_in_offline_mode(offline_app, monkeypatch):
    calls = []
    _fake_clone(monkeypatch, calls)
    code, d = call(offline_app, "/api/acquire", {"url": "https://github.com/a/b"})
    assert code == 400 and "offline" in d["error"] and calls == []


def test_acquire_then_scan_without_executing_then_remove(app, monkeypatch):
    calls = []
    _fake_clone(monkeypatch, calls)
    code, d = call(app, "/api/acquire", {"url": "https://github.com/a/b"})
    assert code == 200 and d["owner"] == "a" and "--no-recurse-submodules" in calls[0] and "--template=" in calls[0]
    clone = d["path"]
    assert call(app, "/api/state")[1]["acquired"][0]["path"] == clone
    code, s = call(app, "/api/scan", {"path": clone})  # scanning is parse-only: evil.py (raises SystemExit) must not run
    assert code == 200 and {x["name"] for x in s["scan"]["dependencies"]} == {"six", "jinja2"}
    assert call(app, "/api/acquire/remove", {"path": str(app["outside"])})[0] == 400  # never deletes outside the acquisition folder
    assert (app["outside"] / "requirements.txt").exists()
    assert call(app, "/api/acquire/remove", {"path": clone})[0] == 200
    assert call(app, "/api/state")[1]["acquired"] == [] and not Path(clone).exists()


def test_full_analysis_of_a_downloaded_repo_needs_explicit_untrusted_ack(app, monkeypatch):
    _fake_clone(monkeypatch)
    started = []
    from depguard.web import api as web_api
    monkeypatch.setattr(web_api.cli, "analyze", lambda *a, **k: started.append((a, k)) or {
        "risk_evidence": {"package_levels": {}}, "usage": {"undeclared_imports": []}, "executed_tests": {"baseline": {"status": "not_run"}},
        "ai_recommendations": {"status": "not_requested"}, "findings": []})
    clone = call(app, "/api/acquire", {"url": "https://github.com/a/b"})[1]["path"]
    code, d = call(app, "/api/analyze", {"path": clone, "tests": True, "ai": False})
    assert code == 400 and "EXECUTES its code" in d["error"] and started == []
    code, d = call(app, "/api/analyze", {"path": clone, "tests": False, "ai": False})   # a non-executing analysis needs no acknowledgement
    assert code == 202 and d["status"] == "running"
    wait_job(app)
    code, d = call(app, "/api/analyze", {"path": clone, "tests": True, "ai": False, "run_untrusted": True})
    assert code == 202
    wait_job(app)
    # a local, already-authorized repo keeps the existing workflow (no extra flag)
    code, d = call(app, "/api/analyze", {"path": str(app["repo"]), "tests": True, "ai": False})
    assert code == 202
    wait_job(app)
    assert len(started) == 3 and started[0][0][3] is False                                # use_ai
    assert started[2][1]["repository_input"]["method"] == "local_folder"


def wait_job(app, timeout=10):
    import time
    t = time.time()
    while app["st"].job and app["st"].job["status"] == "running" and time.time() - t < timeout:
        time.sleep(0.02)
