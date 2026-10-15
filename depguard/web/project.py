"""Dashboard orchestration for ONE project: repository input -> analysis capsule -> user-controlled repair -> verification -> pre-publish -> publication.

Safety rules enforced here (not only in the UI):
* the analysed repository is never modified; all patches go to a designated working copy
* nothing is applied without the typed phrase, and only after verification was attempted (failed/blocked needs an explicit acknowledgement)
* publication needs its own approvals (branch, then PR), the analysed commit must still be the branch head, and published bytes must equal the reviewed patch
"""
import hashlib
import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .. import acquire, advisor, capsule as capsule_mod, repo_input, verify
from ..git_integration import pull_request as pr
from ..git_integration.github import GitHubClient, GitHubError
from ..prepublish import review as review_mod
from ..repair import patch as P, planner
from ..repair.state import RepairState, StateError

APPLY_PHRASE = "APPLY"
UNTRUSTED_DIRS = ("acquired", "uploads", "github")   # content that came from outside; running its tests executes foreign code


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Project:
    def __init__(self, runs: Path, method: str, root: Path, input_info: dict, holder: Path | None = None, github: dict | None = None, label: str | None = None):
        self.id = uuid.uuid4().hex[:10]
        self.runs = Path(runs)
        self.method, self.root, self.input, self.holder, self.github = method, Path(root), input_info, holder, github
        self.label = label or self.root.name
        self.created = _now()
        self.work: Path | None = None
        self.work_base: dict | None = None
        self.state = RepairState(self.runs / "sessions" / f"{self.id}.json")
        self.capsule_path: Path | None = None
        self.baseline: dict | None = None
        self.prepublish: dict | None = None
        self.plan: dict | None = None
        self.branch_result: dict | None = None
        self.pr_result: dict | None = None

    @property
    def untrusted(self) -> bool:
        return self.method in ("zip", "github", "github_clone")

    def summary(self) -> dict:
        return {"id": self.id, "method": self.method, "label": self.label, "root": str(self.root), "input": self.input, "created": self.created,
                "work": str(self.work) if self.work else None, "has_capsule": bool(self.capsule_path and self.capsule_path.exists()), "untrusted": self.untrusted,
                "github": {k: v for k, v in (self.github or {}).items() if k != "token"}}


# ---------------------------------------------------------------- repository inputs
def register_zip(runs: Path, data: bytes, filename: str = "upload.zip") -> Project:
    if len(data) > repo_input.MAX_ZIP_BYTES:
        raise repo_input.InputError(f"archive is larger than {repo_input.MAX_ZIP_BYTES // 2**20} MB")
    up = Path(runs) / "uploads"
    up.mkdir(parents=True, exist_ok=True)
    tmp = up / f"{uuid.uuid4().hex}.zip.part"
    tmp.write_bytes(data)
    try:
        ex = repo_input.extract_zip(tmp, up)
    finally:
        tmp.unlink(missing_ok=True)
    root = Path(ex["path"])
    info = {"method": "zip", "branch": None, "commit": None, "fingerprint": repo_input.tree_fingerprint(root), "dirty": None, "filename": Path(filename).name[:80],
            "manifests": ex["manifests"], "notes": ex["notes"]}
    return Project(runs, "zip", root, info, holder=Path(ex["holder"]), label=Path(filename).stem[:60] or root.name)


def register_local(runs: Path, path: Path) -> Project:
    path = Path(path)
    g = repo_input.git_info(path)
    downloaded = path.resolve().is_relative_to((Path(runs) / "acquired").resolve())   # cloned earlier through "download from GitHub": foreign code
    info = {"method": "github_clone" if downloaded else "local_git" if g["is_git"] else "local_folder", "branch": g["branch"], "commit": g["commit"], "fingerprint": repo_input.tree_fingerprint(path),
            "dirty": g["dirty"], "modified_files": g["modified"], "untracked_files": g["untracked"], "remote": g["remote"]}
    return Project(runs, info["method"], path, info)


def register_github(runs: Path, client: GitHubClient, owner: str, name: str, branch: str | None = None) -> Project:
    repo = client.repo(owner, name)
    branch = branch or repo["default_branch"]
    sha = client.commit_sha(owner, name, branch)
    gh = Path(runs) / "github"
    gh.mkdir(parents=True, exist_ok=True)
    tmp = gh / f"{uuid.uuid4().hex}.zip.part"
    try:
        client.download_zip(owner, name, sha, tmp)
        ex = repo_input.extract_zip(tmp, gh)
    finally:
        tmp.unlink(missing_ok=True)
    root = Path(ex["path"])
    info = {"method": "github", "repository": repo["full_name"], "branch": branch, "commit": sha, "fingerprint": repo_input.tree_fingerprint(root), "dirty": False,
            "manifests": ex["manifests"], "notes": [f"Downloaded commit {sha[:12]} of {repo['full_name']}@{branch}; nothing was executed.", *ex["notes"]]}
    return Project(runs, "github", root, info, holder=Path(ex["holder"]), label=repo["full_name"],
                   github={"owner": owner, "repo": name, "branch": branch, "sha": sha, "default_branch": repo["default_branch"], "private": repo["private"], "html_url": repo["html_url"]})


def cleanup(project: Project) -> None:
    for d in (project.work.parent if project.work else None, project.holder):
        if d and Path(d).exists():
            repo_input.remove_workspace(d, Path(d).parent)


# ---------------------------------------------------------------- capsule / findings
def read_capsule(project: Project) -> dict | None:
    if not project.capsule_path or not project.capsule_path.exists():
        return None
    return json.loads(project.capsule_path.read_text(encoding="utf-8"))


def live_capsule(project: Project, cap: dict) -> dict:
    """The analysis capsule plus the current decisions, patches, attempts and pre-publish result (computed, not written)."""
    return capsule_mod.enrich(cap, findings=[{k: v for k, v in f.items() if k not in ("decision",)} for f in cap.get("findings", [])] if "findings" in cap else None,
                              state=project.state, prepublish=project.prepublish)


def write_final_capsule(project: Project) -> Path:
    cap = read_capsule(project)
    if cap is None:
        raise ValueError("run an analysis first")
    full = live_capsule(project, cap)
    capsule_mod.write(full, project.capsule_path.parent, per_dependency_capsules=True)
    return project.capsule_path


def find_finding(project: Project, fid: str) -> dict:
    cap = read_capsule(project)
    for f in (cap or {}).get("findings", []):
        if f["id"] == fid:
            return f
    raise ValueError("unknown finding (run an analysis first)")


# ---------------------------------------------------------------- finding decisions
def decide(project: Project, fid: str, status: str, reason: str | None) -> dict:
    find_finding(project, fid)
    if status not in ("ignored", "review_later", "open"):
        raise ValueError("decision must be 'ignored', 'review_later' or 'open'")
    return project.state.set_decision(fid, status, reason)


# ---------------------------------------------------------------- working copy + baseline
def ensure_work(project: Project) -> Path:
    if project.work and project.work.exists():
        return project.work
    w = repo_input.make_working_copy(project.root, project.runs / "work")
    project.work, project.work_base = Path(w["path"]), w["base"]
    return project.work


def get_baseline(project: Project, verify_fn) -> dict:
    """The original configuration's tests, run once. Reuses the analysis run when it recorded per-test results."""
    if project.baseline:
        return project.baseline
    cap = read_capsule(project)
    rec = (cap or {}).get("executed_tests", {}).get("baseline")
    if rec and rec.get("tests") and rec.get("status") in ("passed", "failed"):
        project.baseline = rec
    else:
        project.baseline = verify_fn(ensure_work(project), None)
    return project.baseline


def default_verify_fn(label="repair", progress=None):
    return lambda root, edits: verify.verify(root, edits, label, progress=progress)


def _base(project: Project) -> dict:
    ensure_work(project)
    return {"commit": project.input.get("commit"), "branch": project.input.get("branch"), "input_fingerprint": project.input.get("fingerprint"), "method": project.method}


# ---------------------------------------------------------------- repair (Mode A / Mode B)
def repair_mode_a(project: Project, fid: str, model: str, propose_fn=advisor.propose_repair, feedback: str | None = None) -> dict:
    f = find_finding(project, fid)
    r = planner.propose(ensure_work(project), f, project.state, model, propose_fn, mode="A", feedback=feedback, base=_base(project))
    return _result(project, r)


def repair_mode_b(project: Project, fids: list, enabled: bool, model: str, propose_fn=advisor.propose_repair, verify_fn=None) -> list:
    """Explicitly enabled batch: prepare + verify each selected finding. NEVER applies anything."""
    if enabled is not True:
        raise ValueError("automatic fix preparation must be explicitly enabled")
    if not fids:
        raise ValueError("select at least one finding")
    verify_fn = verify_fn or default_verify_fn()
    out = []
    baseline = get_baseline(project, verify_fn)
    for fid in fids:
        f = find_finding(project, fid)
        out.append({"finding_id": fid, **_result(project, planner.prepare_fix(ensure_work(project), f, project.state, model, propose_fn, verify_fn, baseline, base=_base(project)))})
    return out


def _result(project: Project, r: dict) -> dict:
    return {"outcome": r.get("outcome"), "detail": r.get("detail"), "stopped_because": r.get("stopped_because"),
            "patch": capsule_mod._patch_view(r["patch"]) if r.get("patch") else None, "verification": (r.get("verification") or {}) and {k: v for k, v in r["verification"].items() if k != "run"},
            "remaining_attempts": None}


def verify_patch_action(project: Project, pid: str, verify_fn=None) -> dict:
    verify_fn = verify_fn or default_verify_fn()
    p = project.state.patch(pid)
    chk = P.check_applicable(ensure_work(project), p) if p["status"] != "applied" else {"ok": True}
    if not chk["ok"]:
        project.state.set_patch_status(pid, "stale")
        raise ValueError(chk["reason"])
    v = planner.verify_patch(project.work, p, project.state, verify_fn, get_baseline(project, verify_fn))
    return {k: x for k, x in v.items() if k != "run"} | {"patch_id": pid}


def revise_patch(project: Project, pid: str, feedback: str, model: str, propose_fn=advisor.propose_repair) -> dict:
    old = project.state.patch(pid)
    f = find_finding(project, old["finding_id"])
    r = planner.propose(ensure_work(project), f, project.state, model, propose_fn, mode="A", feedback=feedback or "The user asked for a different approach.", base=_base(project))
    if r.get("patch"):
        project.state.set_patch_status(pid, "rejected", rejected_reason="superseded by a revision requested by the user")
    return _result(project, r)


def reject_patch(project: Project, pid: str, reason: str | None) -> dict:
    p = project.state.set_patch_status(pid, "rejected", rejected_reason=(reason or "")[:300])
    project.state.set_decision(p["finding_id"], "open", reason or "proposal rejected")
    return {"patch_id": pid, "status": "rejected"}


def apply_patch(project: Project, pid: str, confirm: str, acknowledge_unverified: bool = False) -> dict:
    if confirm != APPLY_PHRASE:
        raise ValueError(f"explicit approval missing: type {APPLY_PHRASE}")
    p = project.state.patch(pid)
    if p["status"] in ("proposed", "accepted_for_verification", "verifying"):
        raise ValueError("verify the patch first (even a blocked or failed verification is recorded) before applying it")
    if p["status"] in ("rejected", "stale", "applied"):
        raise ValueError(f"a patch in status '{p['status']}' cannot be applied")
    if p["status"] in ("failed", "blocked") and not acknowledge_unverified:
        raise ValueError(f"this patch is '{p['status']}'. Applying it requires explicitly acknowledging that it is NOT verified as working")
    work = ensure_work(project)
    chk = P.check_applicable(work, p)
    if not chk["ok"]:
        project.state.set_patch_status(pid, "stale")
        raise ValueError(chk["reason"])
    res = P.apply_patch(work, p)
    project.state.set_patch_status(pid, "applied", applied={"at": _now(), "snapshot": res["backup"], "diff_matches_preview": res["diff_matches_preview"]})
    project.state.set_decision(p["finding_id"], "repaired")
    project.prepublish = None   # the tree changed: earlier checks no longer apply
    return {"patch_id": pid, "applied_to": str(work), "applied_files": res["applied"], "snapshot": res["backup"], "diff_matches_preview": res["diff_matches_preview"],
            "diff": res["diff"], "rollback": res["rollback"], "note": "Applied to the designated working copy only; the analysed repository is untouched. Export the patch to use it in your own Git workflow."}


def rollback_patch(project: Project, pid: str, confirm: str) -> dict:
    if confirm != "ROLLBACK":
        raise ValueError("explicit approval missing: type ROLLBACK")
    p = project.state.patch(pid)
    snap = (p.get("applied") or {}).get("snapshot")
    if p["status"] != "applied" or not snap:
        raise ValueError("this patch was not applied")
    from .. import migrate
    res = migrate.rollback(project.work, snap)
    project.state.set_patch_status(pid, "stale", rolled_back=_now())
    project.state.set_decision(p["finding_id"], "repair_proposed")
    project.prepublish = None
    return {"patch_id": pid, "restored": res["restored"]}


def export_patch(project: Project, pid: str) -> str:
    return P.export_unified(project.state.patch(pid))


# ---------------------------------------------------------------- pre-publish
def run_prepublish(project: Project, pids: list) -> dict:
    patches = [project.state.patch(p) for p in pids]
    if not patches:
        raise ValueError("select at least one patch")
    work = ensure_work(project)
    tmp = project.runs / "review" / uuid.uuid4().hex[:8]
    shutil.copytree(work, tmp, ignore=repo_input.COPY_IGNORE)
    try:
        for p in patches:
            if p["status"] != "applied":
                P.apply_patch(tmp, {**p, "base": {**p["base"], "file_hashes": repo_input.file_hashes(tmp, [f["path"] for f in p["files"]])}})
        from ..prepublish import hygiene
        res = review_mod.review(tmp, patches, original_info=project.work_base and {"dirty": project.input.get("dirty"), "modified_files": project.input.get("modified_files", []), "untracked_files": project.input.get("untracked_files", [])},
                                tracked=hygiene.tracked_files(project.root))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    res["patch_ids"] = pids
    project.prepublish = res
    return res


# ---------------------------------------------------------------- publication (two separate approvals)
def aggregate_verification(patches: list) -> dict:
    order = ["failed", "blocked", "not_run", "partially_verified", "verified"]
    sts = [(p.get("verification") or {}).get("status", "not_run") for p in patches]
    worst = min(sts, key=order.index)
    return {"status": "passed" if worst == "verified" else worst, "summary": "; ".join(f"{p['package']}: {s}" for p, s in zip(patches, sts))}


def publish_plan(project: Project, client: GitHubClient, pids: list, base_branch: str | None = None) -> dict:
    g = project.github
    if not g:
        raise ValueError("GitHub publication is only available for repositories opened from GitHub; export the patch instead")
    patches = [project.state.patch(p) for p in pids]
    if not patches or any(p["status"] != "applied" for p in patches):
        raise ValueError("apply the selected patches to the working copy first; only reviewed, applied changes can be published")
    files = [{"path": f["path"], "status": "modified"} for p in patches for f in p["files"]]
    plan = pr.plan(client, g["owner"], g["repo"], base_branch or g["branch"], files, aggregate_verification(patches), [p["package"] for p in patches])
    if plan["base_sha"] != g["sha"]:
        plan["can_publish"] = False
        plan["publish_blocker"] = f"The branch moved since analysis (analysed {g['sha'][:7]}, now {plan['base_sha'][:7]}). The patch was built on the older commit; run the analysis again on the current commit."
    if project.prepublish and project.prepublish.get("stop_recommended"):
        plan["warnings"].append("Pre-publish checks recommend stopping: " + "; ".join(project.prepublish["stop_reasons"]))
    plan["patch_ids"] = pids
    plan["analysed_sha"] = g["sha"]
    project.plan, project.branch_result, project.pr_result = plan, None, None
    return plan


def _files_to_publish(project: Project, plan: dict) -> dict:
    out = {}
    for pid in plan["patch_ids"]:
        p = project.state.patch(pid)
        for path in p["result_hashes"]:
            b = (project.work / path).read_bytes()
            if hashlib.sha256(b).hexdigest() != p["result_hashes"][path]:
                raise GitHubError("conflict", f"{path} in the working copy no longer matches the reviewed patch; review again")
            out[path] = b
    return out


def publish_branch(project: Project, client: GitHubClient, confirm: str, acknowledge_stop: bool = False) -> dict:
    plan = project.plan
    if not plan:
        raise ValueError("prepare a publication plan first")
    if project.prepublish and project.prepublish.get("stop_recommended") and not acknowledge_stop:
        raise ValueError("pre-publish checks recommend stopping (" + "; ".join(project.prepublish["stop_reasons"]) + "). Acknowledge explicitly to continue.")
    g = project.github
    patches = [project.state.patch(p) for p in plan["patch_ids"]]
    msg = "DepGuard: " + "; ".join(p["summary"] or p["package"] for p in patches)[:200]
    res = pr.publish_branch(client, g["owner"], g["repo"], plan, _files_to_publish(project, plan), msg, confirm)
    project.branch_result = res
    return res


def publish_pr(project: Project, client: GitHubClient, confirm: str, title: str | None = None) -> dict:
    plan, br = project.plan, project.branch_result
    if not plan or not br:
        raise ValueError("publish the branch first")
    g = project.github
    patches = [project.state.patch(p) for p in plan["patch_ids"]]
    cap = read_capsule(project) or {}
    titles = [f["title"] for f in cap.get("findings", []) if f["id"] in {p["finding_id"] for p in patches}] or [p["package"] for p in patches]
    gaps = [c for p in patches for c in p.get("compatibility_concerns", [])] + [f"{p['package']}: {r}" for p in patches for r in (p.get("verification") or {}).get("reasons", [])]
    gaps += [f"{p['package']}: {a['detail']}" for p in patches for a in p.get("advisory_check", []) if a["status"] in ("partial", "unbounded")]
    body = pr.pr_body(titles, "\n".join(f"- {p['package']}: {p['rationale'] or p['summary']}" for p in patches),
                      "\n".join(f"- {f['path']} ({f['origin']}, {f['verification_status']})" for p in patches for f in p["files"]),
                      plan["verification"], gaps, "exported from DepGuard (capsule.json); not committed to this branch")
    res = pr.open_pull_request(client, g["owner"], g["repo"], plan, br, title or f"DepGuard: {', '.join(titles)[:120]}", body, confirm)
    project.pr_result = res
    return res
