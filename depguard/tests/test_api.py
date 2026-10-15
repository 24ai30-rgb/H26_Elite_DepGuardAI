"""The project workflow through the real HTTP server (localhost ephemeral port). Model, verifier and GitHub are doubles; no network."""
import base64
import io
import json
import threading
import time
import urllib.error
import urllib.request
import zipfile
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
from fakes import BASE_SHA, TOKEN, FakeGitHub

from depguard import repo_input
from depguard.web import server

REQ = "toml==0.10.2\nJinja2==2.11.3\nsix\n"
PYP = '[project]\nname = "demo"\ndependencies = [\n    "toml==0.10.2",\n    "Jinja2==2.11.3",\n    "six",\n]\n'
APP = "from jinja2 import Environment\nfrom jinja2.utils import _missing\nimport certifi\n"
FILES = {"requirements.txt": REQ, "pyproject.toml": PYP, "app.py": APP, "tests/test_app.py": "import app\n\n\ndef test_x():\n    assert True\n"}
EDITS = [{"file": "requirements.txt", "find": "Jinja2==2.11.3", "replace": "Jinja2==3.1.6", "reason": "markupsafe"},
         {"file": "pyproject.toml", "find": '"Jinja2==2.11.3",', "replace": '"Jinja2==3.1.6",', "reason": "consistent"}]
PASS = {"status": "passed", "tests": {"t::x": "passed"}, "exit_code": 0, "output": "", "steps": [{"command": ["python", "-m", "pytest"], "exit_code": 0, "duration_s": 1.2}],
        "environment": {"python": "3.12.10", "platform": "test", "isolation": "venv"}}
BASE = {"status": "failed", "tests": {"t::x": "failed"}, "exit_code": 1, "output": "E   ImportError", "steps": []}


def zip_bytes(files, prefix="demo/", extra=None):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for k, v in files.items():
            z.writestr(prefix + k, v)
        for k, v in (extra or {}).items():
            z.writestr(k, v)
    return b.getvalue()


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "RUNS", tmp_path / "runs")
    allowed = tmp_path / "allowed"
    local = allowed / "local"
    for n, t in FILES.items():
        (local / n).parent.mkdir(parents=True, exist_ok=True)
        (local / n).write_text(t)
    st = server.State([allowed])
    gh = FakeGitHub()
    st.gh_opener = gh
    st.propose_fn = lambda model, ctx, feedback=None: {"status": "ok", "model": {"model": "gemma4:e2b"}, "data": {
        "package": ctx["package"], "action": "upgrade", "summary": "upgrade jinja2", "rationale": "because", "edits": EDITS, "replacement_candidates": ["jinja2>=3"],
        "assumptions": ["a"], "compatibility_concerns": ["c"], "suggested_tests": ["t"], "confidence_limitations": "small model"}}
    st.verify_fn = lambda root, edits: PASS if edits else BASE
    srv = ThreadingHTTPServer(("127.0.0.1", 0), None)
    port = srv.server_address[1]
    srv.RequestHandlerClass = server.make_handler(st, port)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield {"url": f"http://127.0.0.1:{port}", "st": st, "gh": gh, "local": local, "tmp": tmp_path}
    srv.shutdown()
    srv.server_close()


def req(app, path, body=None, raw=None, headers=None, method=None):
    h = {"X-DepGuard": "1", **(headers or {})}
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    if raw is None and body is not None:
        h["Content-Type"] = "application/json"
    r = urllib.request.Request(app["url"] + path, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(r) as resp:
            return resp.status, resp.read(), resp.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers


def call(app, path, body=None, **kw):
    c, d, _ = req(app, path, body, **kw)
    return c, json.loads(d or b"{}")


def wait(app, timeout=60):
    t = time.time()
    while app["st"].job and app["st"].job["status"] == "running" and time.time() - t < timeout:
        time.sleep(0.05)
    return app["st"].job


def state(app):
    return call(app, "/api/state")[1]


def analyse(app, **kw):
    code, _ = call(app, "/api/analyze", {"ai": False, "tests": False, **kw})
    assert code == 202
    j = wait(app)
    assert j["status"] == "done", j
    return state(app)


def findings(app):
    return {f["package"]: f for f in state(app)["capsule"]["findings"]}


# ---------------------------------------------------------------- inputs
def test_zip_upload_creates_an_isolated_untrusted_project(app):
    code, d = call(app, "/api/input/zip", raw=zip_bytes(FILES), headers={"X-Filename": "demo.zip"})
    assert code == 200 and d["method"] == "zip" and d["untrusted"] and d["input"]["commit"] is None and d["input"]["fingerprint"]
    assert state(app)["project"]["id"] == d["id"]
    assert Path(d["root"]).is_relative_to((app["tmp"] / "runs" / "uploads").resolve())


@pytest.mark.parametrize("data,why", [
    (zip_bytes(FILES, extra={"../evil.txt": "x"}), "unsafe entry"), (zip_bytes({"README.md": "x"}), "usable project"), (b"not a zip", "not a valid ZIP"), (b"", "incomplete or empty")])
def test_hostile_uploads_are_rejected_without_leaving_files(app, data, why):
    code, d = call(app, "/api/input/zip", raw=data)
    assert code == 400 and why in d["error"] and state(app)["project"] is None
    assert not (app["tmp"] / "evil.txt").exists() and list((app["tmp"] / "runs" / "uploads").glob("*")) == []


def test_oversized_upload_is_refused_before_reading_it(app, monkeypatch):
    monkeypatch.setattr(repo_input, "MAX_ZIP_BYTES", 100)
    assert call(app, "/api/input/zip", raw=b"x" * 500)[0] == 413


def test_local_input_must_be_inside_authorized_roots_and_reports_state(app):
    code, d = call(app, "/api/input/local", {"path": str(app["local"])})
    assert code == 200 and d["method"] in ("local_folder", "local_git") and not d["untrusted"] and d["input"]["fingerprint"]
    assert call(app, "/api/input/local", {"path": str(app["tmp"])})[0] == 400
    assert call(app, "/api/input/local", {"path": str(app["local"] / ".." / ".." )})[0] == 400


def test_new_routes_require_the_csrf_header_and_a_local_host(app):
    for route in ("/api/input/local", "/api/github/auth", "/api/finding/decision", "/api/repair/start", "/api/patch/apply", "/api/publish/branch", "/api/capsule/refresh"):
        r = urllib.request.Request(app["url"] + route, data=b"{}", method="POST")
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(r)
        assert e.value.code == 403
    assert req(app, "/api/state", headers={"Host": "evil.com"})[0] == 403


def test_workflow_calls_without_an_open_repository_explain_what_to_do(app):
    for route in ("/api/finding/decision", "/api/repair/start", "/api/patch/apply", "/api/prepublish", "/api/publish/plan"):
        code, d = call(app, route, {})
        assert code in (400, 401) and ("no repository is open" in d["error"] or "connect GitHub" in d["error"])


# ---------------------------------------------------------------- GitHub auth and input
def test_github_token_is_never_returned_stored_in_state_or_leaked_in_errors(app):
    code, d = call(app, "/api/github/auth", {"token": TOKEN})
    assert code == 200 and d == {"authenticated": True, "login": "dev"}
    dump = json.dumps(state(app))
    assert TOKEN not in dump and state(app)["github"] == {"authenticated": True, "login": "dev"}
    code, d = call(app, "/api/github/auth", {"token": "bad"})
    assert code == 401 and d["kind"] == "auth" and "Bearer" not in d["error"]
    assert call(app, "/api/github/auth", {"token": ""})[0] == 400 and call(app, "/api/github/auth", {"token": "a b"})[0] == 400
    assert call(app, "/api/github/logout", {})[1] == {"authenticated": False} and state(app)["github"]["authenticated"] is False


def test_github_repo_branches_and_input_record_the_exact_commit(app):
    app["gh"].zip_bytes = zip_bytes(FILES, prefix=f"o-r-{BASE_SHA[:7]}/")
    code, r = call(app, "/api/github/repo", {"owner": "o", "name": "r"})                                       # public repos work without a token
    assert code == 200 and r["default_branch"] == "main" and r["branches"] == [{"name": "main", "sha": BASE_SHA}] and r["authenticated"] is False
    code, d = call(app, "/api/input/github", {"owner": "o", "name": "r", "branch": "main"})
    assert code == 200 and d["method"] == "github" and d["input"]["commit"] == BASE_SHA and d["input"]["branch"] == "main" and d["github"]["sha"] == BASE_SHA and d["untrusted"]
    assert call(app, "/api/github/repo", {"owner": "../x", "name": "r"})[0] == 400


def test_github_errors_map_to_clear_statuses(app, monkeypatch):
    import urllib.error as ue
    from fakes import http_error

    def boom(code, headers=None):
        def op(r, timeout=0):
            raise http_error(r.full_url, code, b"{}", headers)
        return op
    for code, status, kind, hdr in [(404, 404, "not_found", None), (403, 429, "rate_limit", {"x-ratelimit-remaining": "0"}), (401, 401, "auth", None)]:
        app["st"].gh_opener = boom(code, hdr)
        c, d = call(app, "/api/github/repo", {"owner": "o", "name": "r"})
        assert c == status and d["kind"] == kind
    def down(r, timeout=0):
        raise ue.URLError("unreachable")
    app["st"].gh_opener = down
    c, d = call(app, "/api/github/repo", {"owner": "o", "name": "r"})
    assert c == 502 and d["kind"] == "network"


def test_offline_mode_never_contacts_github(app):
    app["st"].offline = True
    app["gh"].calls.clear()
    assert call(app, "/api/github/auth", {"token": TOKEN})[0] == 502 and call(app, "/api/github/repo", {"owner": "o", "name": "r"})[0] == 502
    assert app["gh"].calls == []


# ---------------------------------------------------------------- the full local workflow
def open_local(app):
    assert call(app, "/api/input/local", {"path": str(app["local"])})[0] == 200
    return analyse(app)


def test_analysis_job_produces_findings_and_makes_the_capsule_live(app):
    s = open_local(app)
    f = {x["package"]: x for x in s["capsule"]["findings"]}
    assert {"jinja2", "certifi"} <= set(f) and f["jinja2"]["status"] == "open" and s["validation"]["valid"] and s["capsule_source"]["label"].startswith("analysis of")
    assert s["capsule"]["repository_input"]["fingerprint"] and s["capsule"]["migration_proposal"]["status"] == "none"
    assert s["history"][0]["kind"] == "analysis" and s["history"][0]["status"] == "ok"


def test_ignore_review_later_and_reopen_keep_findings_in_the_report(app):
    open_local(app)
    f = findings(app)
    assert call(app, "/api/finding/decision", {"id": f["jinja2"]["id"], "status": "ignored", "reason": "vendored"})[0] == 200
    assert call(app, "/api/finding/decision", {"id": f["certifi"]["id"], "status": "review_later"})[0] == 200
    s = state(app)
    got = {x["package"]: (x["status"], x["decision"]["reason"]) for x in s["capsule"]["findings"]}
    assert got["jinja2"] == ("ignored", "vendored") and got["certifi"][0] == "review_later" and s["validation"]["valid"]
    code, body, _ = req(app, "/api/report?format=md")
    assert b"status **ignored** (vendored)" in body                                                             # reports keep ignored findings visible
    assert call(app, "/api/finding/decision", {"id": "f-nope", "status": "ignored"})[0] == 400
    assert call(app, "/api/finding/decision", {"id": f["jinja2"]["id"], "status": "wat"})[0] == 400


def test_mode_a_through_http_from_proposal_to_applied_working_copy(app):
    open_local(app)
    fid = findings(app)["jinja2"]["id"]
    original = (app["local"] / "requirements.txt").read_text()
    assert call(app, "/api/repair/start", {"finding_id": fid})[0] == 202
    j = wait(app)
    assert j["status"] == "done" and j["result"]["outcome"] == "proposed"
    pid = j["result"]["patch"]["id"]
    patches = state(app)["capsule"]["repair"]["patches"]
    assert [p["status"] for p in patches] == ["proposed"] and patches[0]["files"][0]["origin"] == "ai" and patches[0]["verification"] is None
    code, d = call(app, "/api/patch/apply", {"patch_id": pid, "confirm": "APPLY"})
    assert code == 400 and "verify the patch first" in d["error"]
    assert call(app, "/api/patch/verify", {"patch_id": pid})[0] == 202
    assert wait(app)["result"]["status"] == "verified"
    live = state(app)["capsule"]["repair"]["patches"][0]
    assert live["status"] == "verified" and live["verification"]["status"] == "verified" and live["verification"]["environment"]["python"] == "3.12.10"
    assert live["verification"]["commands"][0]["duration_s"] == 1.2
    assert call(app, "/api/patch/apply", {"patch_id": pid, "confirm": "yes"})[0] == 400
    code, d = call(app, "/api/patch/apply", {"patch_id": pid, "confirm": "APPLY"})
    assert code == 200 and d["diff_matches_preview"] and (app["local"] / "requirements.txt").read_text() == original        # the ORIGINAL is untouched
    assert "Jinja2==3.1.6" in (Path(state(app)["project"]["work"]) / "requirements.txt").read_text()
    code, text, hdr = req(app, f"/api/patch/export?id={pid}")
    assert code == 200 and b"diff --git a/requirements.txt" in text and "attachment" in hdr["Content-Disposition"] and b"verification: verified" in text
    assert state(app)["capsule"]["repair"]["decisions"][fid]["status"] == "repaired"


def test_mode_b_requires_enabling_and_never_applies(app):
    open_local(app)
    fid = findings(app)["jinja2"]["id"]
    assert call(app, "/api/repair/batch", {"finding_ids": [fid]})[0] == 400
    assert call(app, "/api/repair/batch", {"finding_ids": [fid], "enabled": "yes"})[0] == 400
    assert call(app, "/api/repair/batch", {"finding_ids": [fid], "enabled": True})[0] == 202
    j = wait(app)
    assert j["status"] == "done" and j["result"][0]["patch"]["status"] == "verified"
    assert (app["local"] / "requirements.txt").read_text() == REQ and "Jinja2==2.11.3" in (Path(state(app)["project"]["work"]) / "requirements.txt").read_text()


def test_only_one_long_operation_runs_at_a_time(app):
    open_local(app)
    fid = findings(app)["jinja2"]["id"]
    gate = threading.Event()
    app["st"].propose_fn = lambda m, c, f=None: gate.wait(5) or {"status": "blocked", "reason": "x"}
    assert call(app, "/api/repair/start", {"finding_id": fid})[0] == 202
    code, d = call(app, "/api/repair/start", {"finding_id": fid})
    assert code == 400 and "already running" in d["error"]
    gate.set()
    wait(app)


def test_model_unavailable_is_reported_without_a_patch(app):
    open_local(app)
    fid = findings(app)["jinja2"]["id"]
    app["st"].propose_fn = lambda m, c, f=None: {"status": "blocked", "reason": "Ollama is not running"}
    call(app, "/api/repair/start", {"finding_id": fid})
    j = wait(app)
    assert j["result"]["outcome"] == "model_unavailable" and j["result"]["patch"] is None and state(app)["capsule"]["repair"]["patches"] == []


def test_reject_prepublish_and_capsule_refresh(app):
    open_local(app)
    fid = findings(app)["jinja2"]["id"]
    call(app, "/api/repair/start", {"finding_id": fid})
    pid = wait(app)["result"]["patch"]["id"]
    call(app, "/api/patch/verify", {"patch_id": pid})
    wait(app)
    code, r = call(app, "/api/prepublish", {"patch_ids": [pid]})
    assert code == 200 and r["stop_recommended"] is False and any(c["id"] == f"verification:{pid}" for c in r["checks"])
    assert state(app)["capsule"]["prepublish"]["status"] in ("pass", "warn")
    code, d = call(app, "/api/capsule/refresh", {})
    out = Path(d["path"]).parent
    final = json.loads((out / "capsule.json").read_text())
    assert final["repair"]["patches"][0]["id"] == pid and final["prepublish"] and (out / "dependencies" / "jinja2" / "capsule.json").exists()
    assert call(app, "/api/patch/reject", {"patch_id": pid, "reason": "no"})[1]["status"] == "rejected"
    assert call(app, "/api/patch/apply", {"patch_id": pid, "confirm": "APPLY"})[0] == 400
    assert call(app, "/api/repair/settings", {"max_attempts": 9})[0] == 400 and call(app, "/api/repair/settings", {"max_attempts": 3})[1] == {"max_attempts": 3}


# ---------------------------------------------------------------- GitHub publication through HTTP
def open_github(app):
    app["gh"].zip_bytes = zip_bytes(FILES, prefix=f"o-r-{BASE_SHA[:7]}/")
    assert call(app, "/api/github/auth", {"token": TOKEN})[0] == 200
    assert call(app, "/api/input/github", {"owner": "o", "name": "r", "branch": "main"})[0] == 200
    return analyse(app)


def test_github_publication_end_to_end_with_two_separate_approvals(app):
    open_github(app)
    fid = findings(app)["jinja2"]["id"]
    call(app, "/api/repair/start", {"finding_id": fid})
    pid = wait(app)["result"]["patch"]["id"]
    call(app, "/api/patch/verify", {"patch_id": pid})
    wait(app)
    assert call(app, "/api/publish/plan", {"patch_ids": [pid]})[0] == 400                                       # not applied yet
    call(app, "/api/patch/apply", {"patch_id": pid, "confirm": "APPLY"})
    code, plan = call(app, "/api/publish/plan", {"patch_ids": [pid]})
    assert code == 200 and plan["can_publish"] and plan["base_sha"] == BASE_SHA and plan["verification"]["status"] == "passed"
    posts = lambda: len([c for c in app["gh"].calls if c[0] == "POST"])
    n = posts()
    assert call(app, "/api/publish/branch", {"confirm": "nope"})[0] == 400 and call(app, "/api/publish/pr", {"confirm": "OPEN PR"})[0] == 400 and posts() == n
    code, br = call(app, "/api/publish/branch", {"confirm": "PUBLISH"})
    assert code == 200 and br["branch"] == plan["proposed_branch"] and app["gh"].refs
    assert call(app, "/api/publish/pr", {"confirm": "PUBLISH"})[0] == 400
    code, res = call(app, "/api/publish/pr", {"confirm": "OPEN PR"})
    assert code == 200 and res["html_url"].endswith("/pull/7")
    s = state(app)
    assert s["publish"]["pr"]["number"] == 7 and s["publish"]["branch"]["branch"] == plan["proposed_branch"]
    assert TOKEN not in json.dumps(s) and all(TOKEN not in json.dumps(c[3] or {}) and TOKEN not in c[1] for c in app["gh"].calls)


def test_publication_blocked_when_the_branch_moved_and_for_local_projects(app):
    open_github(app)
    fid = findings(app)["jinja2"]["id"]
    call(app, "/api/repair/start", {"finding_id": fid})
    pid = wait(app)["result"]["patch"]["id"]
    call(app, "/api/patch/verify", {"patch_id": pid})
    wait(app)
    call(app, "/api/patch/apply", {"patch_id": pid, "confirm": "APPLY"})
    app["gh"].base_sha = "b" * 40
    code, plan = call(app, "/api/publish/plan", {"patch_ids": [pid]})
    assert code == 200 and plan["can_publish"] is False and "moved since analysis" in plan["publish_blocker"]
    assert call(app, "/api/publish/branch", {"confirm": "PUBLISH"})[0] in (400, 403, 409) and app["gh"].refs == {}


def test_github_publication_requires_authorization(app):
    app["gh"].zip_bytes = zip_bytes(FILES, prefix=f"o-r-{BASE_SHA[:7]}/")
    assert call(app, "/api/input/github", {"owner": "o", "name": "r"})[0] == 200
    analyse(app)
    code, d = call(app, "/api/publish/plan", {"patch_ids": ["p-x"]})
    assert code == 401 and d["kind"] == "auth"


def test_per_dependency_capsule_download_is_a_valid_scoped_slice(app):
    open_local(app)
    code, body, hdr = req(app, "/api/report?format=json&package=jinja2")
    sub = json.loads(body)
    from depguard import capsule
    capsule.validate(sub)
    assert code == 200 and sub["scope"] == {"type": "dependency", "package": "jinja2"} and [f["package"] for f in sub["findings"]] == ["jinja2"]
    assert "jinja2" in hdr["Content-Disposition"] and sub["dependencies"]["items"] and all(d["name"] == "jinja2" for d in sub["dependencies"]["items"])
    code, md, _ = req(app, "/api/report?format=md&package=jinja2")
    assert code == 200 and b"jinja2" in md
    for bad in ("../x", "nope", "requests"):          # unknown or invalid package names: never a path, never a guess
        assert req(app, f"/api/report?format=json&package={bad}")[0] == 409


def test_metadata_option_is_opt_in_and_blocked_offline(app, monkeypatch):
    from depguard.web import api as web_api
    seen = []
    monkeypatch.setattr(web_api.cli, "analyze", lambda *a, **k: seen.append(a[5]) or {
        "risk_evidence": {"package_levels": {}}, "usage": {"undeclared_imports": []}, "executed_tests": {"baseline": {"status": "not_run"}},
        "ai_recommendations": {"status": "not_requested"}, "findings": []})
    assert call(app, "/api/input/local", {"path": str(app["local"])})[0] == 200
    assert call(app, "/api/analyze", {"ai": False, "tests": False})[0] == 202
    wait(app)
    assert call(app, "/api/analyze", {"ai": False, "tests": False, "metadata": True})[0] == 202
    wait(app)
    assert seen == [False, True]                                  # the package index is contacted only when asked
    app["st"].offline = True
    code, d = call(app, "/api/analyze", {"ai": False, "tests": False, "metadata": True})
    assert code == 400 and "offline mode" in d["error"] and len(seen) == 2


def test_running_jobs_expose_what_they_are_doing_and_redact_secrets(app):
    open_local(app)
    fid = findings(app)["jinja2"]["id"]
    gate = threading.Event()
    seen = {}

    def slow(model, ctx, feedback=None):
        seen["phase"] = app["st"].job.get("phase")
        gate.wait(5)
        return {"status": "blocked", "reason": "x"}
    app["st"].propose_fn = slow
    assert call(app, "/api/repair/start", {"finding_id": fid})[0] == 202
    for _ in range(100):
        if "phase" in seen:
            break
        time.sleep(0.05)
    job = state(app)["job"]
    assert job["status"] == "running" and "asking Gemma 4" in job["phase"] and "asking Gemma 4" in seen["phase"]
    app["st"].set_phase("token " + TOKEN + " leaked?")
    assert TOKEN not in json.dumps(state(app)["job"]) and "[REDACTED:github_token]" in state(app)["job"]["phase"]
    gate.set()
    wait(app)
    app["st"].set_phase("after the job ended")                                   # no running job: a late phase update changes nothing
    assert "after the job" not in json.dumps(state(app)["job"])
