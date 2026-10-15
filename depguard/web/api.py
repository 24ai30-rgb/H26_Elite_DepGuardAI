"""HTTP handlers for the project workflow. Thin: validation of the request shape + calls into project.py, where the safety rules live.

Every handler takes (st, body) and returns a JSON-able dict, or raises ValueError / GitHubError / InputError (mapped to HTTP errors by the server).
Long operations (analysis, Gemma, test runs) run as ONE background job at a time; the UI polls the job in /api/state.
"""
import threading
import time
from datetime import datetime
from pathlib import Path

from .. import advisor, cli, history, repo_input
from ..git_integration.github import GitHubClient, GitHubError, default_opener
from ..prepublish import secrets
from . import project as W


# ---------------------------------------------------------------- jobs
def start_job(st, kind: str, label: str, fn, **extra) -> dict:
    with st.lock:
        if st.job and st.job["status"] == "running":
            raise ValueError(f"another operation is already running ({st.job.get('label') or st.job.get('kind')}); wait for it to finish")
        st.job = {"status": "running", "kind": kind, "label": label, "started": time.time(), **extra}
    job = st.job

    def work():
        try:
            job["result"] = fn()
            job.update(status="done", finished=time.time())
            st.log(kind, f"{label}: finished")
        except Exception as e:   # shown in the UI; secrets are redacted before they can reach it
            job.update(status="error", error=secrets.redact(f"{type(e).__name__}: {e}"), finished=time.time())
            st.log("error", f"{label}: {secrets.redact(str(e))[:160]}")
    threading.Thread(target=work, daemon=True).start()
    return job


def active(st) -> W.Project:
    if not st.project:
        raise ValueError("no repository is open: upload a ZIP, choose a local repository, or connect GitHub first")
    return st.project


def _register(st, project: W.Project) -> dict:
    st.projects[project.id] = project
    st.project = project
    st.log("input", f"Opened {project.label} ({project.method})")
    return project.summary()


# ---------------------------------------------------------------- repository inputs
def input_zip(st, data: bytes, filename: str) -> dict:
    return _register(st, W.register_zip(st.runs(), data, filename))


def input_local(st, body: dict) -> dict:
    root = st.resolve_repo(str(body.get("path", "")))
    return _register(st, W.register_local(st.runs(), root))


def _client(st, authenticated_only=False) -> GitHubClient:
    if st.offline:
        raise GitHubError("network", "offline mode: GitHub is not contacted")
    if authenticated_only and not (st.gh and st.gh.authenticated):
        raise GitHubError("auth", "connect GitHub with a token first")
    return st.gh or GitHubClient(None, opener=st.gh_opener)


def github_auth(st, body: dict) -> dict:
    token = str(body.get("token", "")).strip()
    if not token or len(token) > 255 or any(c.isspace() for c in token):
        raise GitHubError("invalid", "enter a GitHub token (fine-grained, limited to the one repository: Contents and Pull requests read/write)")
    if st.offline:
        raise GitHubError("network", "offline mode: GitHub is not contacted")
    client = GitHubClient(token, opener=st.gh_opener)
    login = client.user()["login"]          # raises GitHubError(auth/network/...) with the token redacted
    st.gh, st.gh_login = client, login
    st.log("github", f"Authorized as {login} (token kept in memory only)")
    return {"authenticated": True, "login": login}


def github_logout(st, body: dict) -> dict:
    st.gh, st.gh_login = None, None
    return {"authenticated": False}


def github_repo(st, body: dict) -> dict:
    c = _client(st)
    owner, name = str(body.get("owner", "")).strip(), str(body.get("name", "")).strip()
    repo = c.repo(owner, name)
    return {**repo, "branches": c.branches(owner, name), "authenticated": c.authenticated}


def input_github(st, body: dict) -> dict:
    c = _client(st)
    return _register(st, W.register_github(st.runs(), c, str(body.get("owner", "")).strip(), str(body.get("name", "")).strip(), (body.get("branch") or None)))


def project_select(st, body: dict) -> dict:
    p = st.projects.get(str(body.get("id", "")))
    if not p:
        raise ValueError("unknown project")
    st.project = p
    if p.capsule_path and p.capsule_path.exists():
        st.capsule_path, st.capsule_label = p.capsule_path, f"analysis of {p.label}"
    return p.summary()


def project_remove(st, body: dict) -> dict:
    p = st.projects.pop(str(body.get("id", "")), None)
    if not p:
        raise ValueError("unknown project")
    W.cleanup(p)
    if st.project is p:
        st.project = None
    return {"ok": True}


# ---------------------------------------------------------------- analysis
def start_analysis(st, body: dict, runs_dir_name=None) -> dict:
    """Read-only analysis of the active project (or a path inside the authorized roots)."""
    if body.get("project_id"):
        project_select(st, {"id": body["project_id"]})
    elif body.get("path"):
        input_local(st, {"path": body["path"]})
    p = active(st)
    if p.untrusted and not body.get("run_untrusted") and body.get("tests"):
        raise ValueError("this repository came from outside (ZIP or GitHub). A full analysis installs its requirements and runs its tests, which EXECUTES its code "
                         "with your user rights (a virtual environment is not a sandbox). Confirm explicitly (run_untrusted), or run the non-executing scan only.")
    out = st.runs() / (runs_dir_name or datetime.now().strftime("%Y%m%d-%H%M%S"))
    use_ai, tests, model = bool(body.get("ai")), bool(body.get("tests")), st.model
    fetch = bool(body.get("metadata"))
    if fetch and st.offline:
        raise ValueError("offline mode: the package index is not contacted; analyse without fetching metadata")

    def run():
        ident = {**cli.capsule.identity(p.root), "path": str(p.root)}
        try:
            cap = cli.analyze(p.root, out, model, use_ai, tests, fetch, meta_cache=st.meta_path(), repository_input={**p.input, "label": p.label}, per_dependency=False, progress=st.set_phase)
        except Exception as e:
            history.add(st.history_path(), "analysis", "error", ident, {}, error=f"{type(e).__name__}: {e}")
            raise
        p.capsule_path = out / "capsule.json"
        p.baseline = None
        st.capsule_path, st.capsule_label = p.capsule_path, f"analysis of {p.label}"
        lv = [(k, v["level"]) for k, v in cap["risk_evidence"]["package_levels"].items()]
        history.add(st.history_path(), "analysis", "ok", ident, history.summarize(lv, len(cap["usage"]["undeclared_imports"]), cap.get("health_score"),
                                                                                   cap["executed_tests"]["baseline"]["status"], cap["ai_recommendations"]["status"]),
                    capsule=str(p.capsule_path))
        return {"findings": len(cap.get("findings", [])), "capsule": str(p.capsule_path)}
    return start_job(st, "analysis", f"Analysis of {p.label}", run, path=str(p.root), ai=use_ai, tests=tests)


# ---------------------------------------------------------------- findings and repair
def finding_decision(st, body: dict) -> dict:
    p = active(st)
    d = W.decide(p, str(body.get("id", "")), str(body.get("status", "")), body.get("reason"))
    st.log("decision", f"Finding marked {d['status']}")
    return d


def repair_start(st, body: dict) -> dict:
    """Mode A: the user selected Repair on ONE finding. Produces a proposal only."""
    p, fid = active(st), str(body.get("finding_id", ""))
    W.find_finding(p, fid)
    return start_job(st, "repair", "Repair proposal (user-triggered)", lambda: (st.set_phase("asking Gemma 4 for ONE proposal (a small local model can take a minute)"), W.repair_mode_a(p, fid, st.model, st.propose_fn, body.get("feedback")))[1])


def repair_batch(st, body: dict) -> dict:
    """Mode B: explicitly enabled preparation of selected findings. Proposes and verifies; never applies."""
    p = active(st)
    ids = [str(x) for x in (body.get("finding_ids") or [])]
    if body.get("enabled") is not True:
        raise ValueError("automatic fix preparation must be explicitly enabled")
    for i in ids:
        W.find_finding(p, i)
    return start_job(st, "prepare", f"Preparing fixes for {len(ids)} finding(s)", lambda: W.repair_mode_b(p, ids, True, st.model, st.propose_fn, st.verify_fn or W.default_verify_fn(progress=st.set_phase)))


def patch_verify(st, body: dict) -> dict:
    p, pid = active(st), str(body.get("patch_id", ""))
    p.state.patch(pid)
    return start_job(st, "verify", "Testing the proposed patch in isolation", lambda: W.verify_patch_action(p, pid, st.verify_fn or W.default_verify_fn(progress=st.set_phase)))


def patch_revise(st, body: dict) -> dict:
    p, pid = active(st), str(body.get("patch_id", ""))
    p.state.patch(pid)
    return start_job(st, "repair", "Revising the proposal", lambda: (st.set_phase("asking Gemma 4 for a revised proposal"), W.revise_patch(p, pid, str(body.get("feedback", ""))[:1000], st.model, st.propose_fn))[1])


def patch_reject(st, body: dict) -> dict:
    return W.reject_patch(active(st), str(body.get("patch_id", "")), body.get("reason"))


def patch_apply(st, body: dict) -> dict:
    r = W.apply_patch(active(st), str(body.get("patch_id", "")), str(body.get("confirm", "")), bool(body.get("acknowledge_unverified")))
    st.log("apply", f"Patch {r['patch_id']} applied to the working copy (not the original); snapshot {r['snapshot']}")
    return r


def patch_rollback(st, body: dict) -> dict:
    return W.rollback_patch(active(st), str(body.get("patch_id", "")), str(body.get("confirm", "")))


def settings(st, body: dict) -> dict:
    p = active(st)
    if "max_attempts" in body:
        p.state.set_max_attempts(body["max_attempts"])
    if body.get("reset_attempts_for"):
        p.state.reset_attempts(str(body["reset_attempts_for"]))
    return {"max_attempts": p.state.max_attempts}


# ---------------------------------------------------------------- pre-publish and publication
def prepublish(st, body: dict) -> dict:
    return W.run_prepublish(active(st), [str(x) for x in (body.get("patch_ids") or [])])


def publish_plan(st, body: dict) -> dict:
    return W.publish_plan(active(st), _client(st, True), [str(x) for x in (body.get("patch_ids") or [])], body.get("base_branch") or None)


def publish_branch(st, body: dict) -> dict:
    return W.publish_branch(active(st), _client(st, True), str(body.get("confirm", "")), bool(body.get("acknowledge_stop")))


def publish_pr(st, body: dict) -> dict:
    return W.publish_pr(active(st), _client(st, True), str(body.get("confirm", "")), body.get("title") or None)


def capsule_refresh(st, body: dict) -> dict:
    path = W.write_final_capsule(active(st))
    return {"path": str(path), "dependencies_dir": str(path.parent / "dependencies")}


ROUTES = {
    "/api/input/local": input_local, "/api/github/auth": github_auth, "/api/github/logout": github_logout, "/api/github/repo": github_repo,
    "/api/input/github": input_github, "/api/project/select": project_select, "/api/project/remove": project_remove,
    "/api/finding/decision": finding_decision, "/api/repair/start": repair_start, "/api/repair/batch": repair_batch,
    "/api/patch/verify": patch_verify, "/api/patch/revise": patch_revise, "/api/patch/reject": patch_reject, "/api/patch/apply": patch_apply,
    "/api/patch/rollback": patch_rollback, "/api/repair/settings": settings, "/api/prepublish": prepublish, "/api/publish/plan": publish_plan,
    "/api/publish/branch": publish_branch, "/api/publish/pr": publish_pr, "/api/capsule/refresh": capsule_refresh,
}
