"""End-to-end project workflow on a small fixture repository: input -> analyse -> repair (A/B) -> verify -> apply -> pre-publish -> publish.
The model, the verifier and GitHub are test doubles; everything else (files, patches, state, capsule, schema) is real."""
import io
import json
import zipfile
from pathlib import Path

import pytest
from fakes import BASE_SHA, FakeGitHub

from depguard import capsule, cli, repo_input
from depguard.git_integration.github import GitHubClient, GitHubError
from depguard.repair.state import StateError
from depguard.web import project as W

REQ = "toml==0.10.2\nJinja2==2.11.3\nsix\n"
PYP = '[project]\nname = "demo"\ndependencies = [\n    "toml==0.10.2",\n    "Jinja2==2.11.3",\n    "six",\n]\n'
APP = "from jinja2 import Environment\nfrom jinja2.utils import _missing\nimport certifi\n\n\ndef build():\n    return Environment()\n"
TEST = "import app\n\n\ndef test_build():\n    assert app.build() is not None\n"
FILES = {"requirements.txt": REQ, "pyproject.toml": PYP, "app.py": APP, "tests/test_app.py": TEST}


def write_repo(root: Path, files=FILES):
    for n, t in files.items():
        p = root / n
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(t)
    return root


@pytest.fixture
def runs(tmp_path):
    return tmp_path / "runs"


@pytest.fixture
def proj(tmp_path, runs):
    src = write_repo(tmp_path / "src" / "demo")
    p = W.register_local(runs, src)
    out = tmp_path / "out"
    cli.analyze(src, out, "m", use_ai=False, run_tests=False, meta_cache=tmp_path / "meta.json", per_dependency=False)
    p.capsule_path = out / "capsule.json"
    return p


def good_edits():
    return [{"file": "requirements.txt", "find": "Jinja2==2.11.3", "replace": "Jinja2==3.1.6", "reason": "markupsafe compatibility"},
            {"file": "pyproject.toml", "find": '"Jinja2==2.11.3",', "replace": '"Jinja2==3.1.6",', "reason": "keep manifests consistent"}]


def propose_fn(edits=None, action="upgrade", status="ok"):
    calls = []

    def fn(model, ctx, feedback=None):
        calls.append((ctx, feedback))
        if status != "ok":
            return {"status": status, "reason": "x"}
        return {"status": "ok", "model": {"model": "gemma4:e2b"}, "data": {"package": "jinja2", "action": action, "summary": "upgrade jinja2", "rationale": "because",
                "edits": good_edits() if edits is None else edits, "replacement_candidates": [], "assumptions": ["a"], "compatibility_concerns": ["c"],
                "suggested_tests": ["t"], "confidence_limitations": "small model"}}
    fn.calls = calls
    return fn


BASE_RUN = {"status": "failed", "tests": {"tests.test_app::test_build": "failed"}, "exit_code": 1, "output": "", "steps": []}
PASS_RUN = {"status": "passed", "tests": {"tests.test_app::test_build": "passed"}, "exit_code": 0, "output": "", "steps": [], "environment": {"python": "3.12"}}


def vfn(run):
    calls = []

    def fn(root, edits):
        calls.append(edits)
        return run if edits else BASE_RUN
    fn.calls = calls
    return fn


def jinja_finding(p):
    return next(f for f in W.read_capsule(p)["findings"] if f["package"] == "jinja2")


# ---------------------------------------------------------------- inputs
def make_zip_bytes(files, extra=None):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for k, v in files.items():
            z.writestr(k, v)
        for k, v in (extra or {}).items():
            z.writestr(k, v)
    return b.getvalue()


def test_zip_project_is_extracted_isolated_and_labelled_untrusted(runs):
    p = W.register_zip(runs, make_zip_bytes({f"demo/{k}": v for k, v in FILES.items()}), "demo.zip")
    assert p.method == "zip" and p.untrusted and (p.root / "app.py").exists() and p.input["commit"] is None and p.input["fingerprint"]
    assert p.root.is_relative_to((runs / "uploads").resolve()) or p.root.is_relative_to(runs / "uploads")
    W.cleanup(p)
    assert not p.holder.exists()


def test_hostile_or_unusable_zips_are_rejected_and_leave_nothing(runs):
    for data in (make_zip_bytes({"demo/requirements.txt": "six"}, {"../evil.txt": "x"}), make_zip_bytes({"README.md": "hi"}), b"not a zip"):
        with pytest.raises(repo_input.InputError):
            W.register_zip(runs, data)
    assert list((runs / "uploads").iterdir()) == []
    with pytest.raises(repo_input.InputError, match="larger than"):
        W.register_zip(runs, b"x" * (repo_input.MAX_ZIP_BYTES + 1))


def test_local_folder_project_records_fingerprint(proj):
    assert proj.method in ("local_folder", "local_git") and proj.input["fingerprint"] and proj.summary()["has_capsule"]


# ---------------------------------------------------------------- findings, decisions, capsule
def test_analysis_produces_findings_with_evidence_unknowns_and_actions(proj):
    cap = W.read_capsule(proj)
    fs = {f["package"]: f for f in cap["findings"]}
    assert fs["jinja2"]["severity"] == "medium" and fs["certifi"]["severity"] == "medium"
    assert fs["jinja2"]["actions"] == ["repair", "ignore", "review_later"] and fs["jinja2"]["unknown"] and fs["jinja2"]["verification_requirements"]
    assert "app.py" in fs["jinja2"]["impact"]["affected_files"] and any(t["file"] == "tests/test_app.py" for t in fs["jinja2"]["impact"]["tests_indirect"])
    assert cap["repository_input"]["fingerprint"] and cap["ai_recommendations"]["status"] == "not_requested"
    assert cap["migration_proposal"]["status"] == "none" and cap["executed_tests"]["proposed"]["status"] == "not_run"   # analysis proposes and tests nothing


def test_ignored_and_review_later_findings_stay_visible_in_the_valid_capsule(proj, tmp_path):
    fid = jinja_finding(proj)["id"]
    W.decide(proj, fid, "ignored", "vendored, not used in production")
    W.decide(proj, W.find_finding(proj, [f["id"] for f in W.read_capsule(proj)["findings"] if f["package"] == "certifi"][0])["id"], "review_later", None)
    live = W.live_capsule(proj, W.read_capsule(proj))
    capsule.validate(live)
    st = {f["package"]: (f["status"], f["decision"]["reason"]) for f in live["findings"]}
    assert st["jinja2"] == ("ignored", "vendored, not used in production") and st["certifi"][0] == "review_later"
    path = W.write_final_capsule(proj)
    final = json.loads(path.read_text())
    assert {f["package"] for f in final["findings"]} >= {"jinja2", "certifi"} and "ignored" in (path.parent / "capsule.md").read_text()
    for pkg in ("jinja2", "certifi"):
        sub = json.loads((path.parent / "dependencies" / pkg / "capsule.json").read_text())
        capsule.validate(sub)
        assert sub["scope"] == {"type": "dependency", "package": pkg} and [f["package"] for f in sub["findings"]] == [pkg]


def test_invalid_decisions_and_unknown_findings_are_rejected(proj):
    with pytest.raises(ValueError, match="unknown finding"):
        W.decide(proj, "f-nope", "ignored", None)
    with pytest.raises(ValueError, match="decision must be"):
        W.decide(proj, jinja_finding(proj)["id"], "deleted", None)


# ---------------------------------------------------------------- Mode A
def test_mode_a_proposal_does_not_touch_the_original_or_apply_anything(proj):
    fid = jinja_finding(proj)["id"]
    src_before = {p.name: p.read_bytes() for p in proj.root.iterdir() if p.is_file()}
    r = W.repair_mode_a(proj, fid, "m", propose_fn())
    assert r["outcome"] == "proposed" and r["patch"]["status"] == "proposed" and r["patch"]["origin"] == "ai" and r["patch"]["verification"] is None
    assert proj.work != proj.root and (proj.work / "requirements.txt").read_text() == REQ                 # a separate working copy
    assert {p.name: p.read_bytes() for p in proj.root.iterdir() if p.is_file()} == src_before
    assert (proj.work / "requirements.txt").read_text() == REQ                                           # not even the copy is changed yet


def test_full_mode_a_cycle_verify_approve_apply_export_rollback(proj):
    fid = jinja_finding(proj)["id"]
    pid = W.repair_mode_a(proj, fid, "m", propose_fn())["patch"]["id"]
    with pytest.raises(ValueError, match="verify the patch first"):
        W.apply_patch(proj, pid, "APPLY")                                                                  # cannot skip verification
    v = W.verify_patch_action(proj, pid, vfn(PASS_RUN))
    assert v["status"] == "verified" and v["fixed"] == ["tests.test_app::test_build"] and "passing" not in v["summary"]
    with pytest.raises(ValueError, match="approval missing"):
        W.apply_patch(proj, pid, "yes")
    assert (proj.work / "requirements.txt").read_text() == REQ
    res = W.apply_patch(proj, pid, "APPLY")
    assert res["diff_matches_preview"] and "Jinja2==3.1.6" in (proj.work / "requirements.txt").read_text() and (proj.root / "requirements.txt").read_text() == REQ
    assert proj.state.patch(pid)["status"] == "applied" and proj.state.decision(fid)["status"] == "repaired"
    text = W.export_patch(proj, pid)
    assert "diff --git a/requirements.txt" in text and "verification: verified" in text
    with pytest.raises(ValueError, match="cannot be applied"):
        W.apply_patch(proj, pid, "APPLY")                                                                  # no double apply
    with pytest.raises(ValueError, match="approval missing"):
        W.rollback_patch(proj, pid, "yes")
    W.rollback_patch(proj, pid, "ROLLBACK")
    assert (proj.work / "requirements.txt").read_text() == REQ and proj.state.patch(pid)["status"] == "stale"


def test_failed_verification_needs_explicit_acknowledgement_to_apply(proj):
    pid = W.repair_mode_a(proj, jinja_finding(proj)["id"], "m", propose_fn())["patch"]["id"]
    v = W.verify_patch_action(proj, pid, vfn({"status": "failed", "tests": {}, "exit_code": 4, "output": "E   ModuleNotFoundError: pkg_resources", "steps": []}))
    assert v["status"] == "failed"
    with pytest.raises(ValueError, match="NOT verified as working"):
        W.apply_patch(proj, pid, "APPLY")
    assert (proj.work / "requirements.txt").read_text() == REQ
    assert W.apply_patch(proj, pid, "APPLY", acknowledge_unverified=True)["applied_files"]


def test_blocked_environment_is_reported_as_blocked_not_success(proj):
    pid = W.repair_mode_a(proj, jinja_finding(proj)["id"], "m", propose_fn())["patch"]["id"]
    v = W.verify_patch_action(proj, pid, vfn({"status": "environment_blocked", "failure_kind": "install_failure", "tests": {}, "output": "", "steps": []}))
    assert v["status"] == "blocked" and "nothing was proven" in v["reasons"][0]


def test_patch_goes_stale_when_the_working_copy_changes_after_proposal(proj):
    pid = W.repair_mode_a(proj, jinja_finding(proj)["id"], "m", propose_fn())["patch"]["id"]
    W.verify_patch_action(proj, pid, vfn(PASS_RUN))
    (proj.work / "requirements.txt").write_text(REQ + "extra==1\n")                                        # the user edited the file meanwhile
    with pytest.raises(ValueError, match="Renewed review"):
        W.apply_patch(proj, pid, "APPLY")
    assert proj.state.patch(pid)["status"] == "stale" and (proj.work / "requirements.txt").read_text() == REQ + "extra==1\n"


def test_revision_supersedes_the_old_patch_and_passes_feedback(proj):
    fid = jinja_finding(proj)["id"]
    fn = propose_fn()
    old = W.repair_mode_a(proj, fid, "m", fn)["patch"]["id"]
    new = W.revise_patch(proj, old, "only touch requirements.txt", "m", propose_fn(good_edits()[:1]))
    assert new["patch"]["id"] != old and proj.state.patch(old)["status"] == "rejected" and [f["path"] for f in new["patch"]["files"]] == ["requirements.txt"]


def test_reject_returns_the_finding_to_open_and_keeps_history(proj):
    fid = jinja_finding(proj)["id"]
    pid = W.repair_mode_a(proj, fid, "m", propose_fn())["patch"]["id"]
    W.reject_patch(proj, pid, "not what I want")
    assert proj.state.patch(pid)["status"] == "rejected" and proj.state.decision(fid)["status"] == "open" and len(proj.state.decision(fid)["history"]) >= 2
    with pytest.raises(StateError):
        proj.state.set_patch_status(pid, "accepted_for_verification")


def test_no_reliable_recommendation_and_model_failure_create_no_patch(proj):
    fid = jinja_finding(proj)["id"]
    assert W.repair_mode_a(proj, fid, "m", propose_fn([], action="no_reliable_recommendation"))["patch"] is None
    assert W.repair_mode_a(proj, fid, "m", propose_fn(status="blocked"))["outcome"] == "model_unavailable"
    assert proj.state.data["patches"] == {}


# ---------------------------------------------------------------- Mode B
def test_mode_b_must_be_explicitly_enabled_and_never_applies(proj):
    fid = jinja_finding(proj)["id"]
    with pytest.raises(ValueError, match="explicitly enabled"):
        W.repair_mode_b(proj, [fid], False, "m", propose_fn(), vfn(PASS_RUN))
    with pytest.raises(ValueError, match="at least one"):
        W.repair_mode_b(proj, [], True, "m", propose_fn(), vfn(PASS_RUN))
    out = W.repair_mode_b(proj, [fid], True, "m", propose_fn(), vfn(PASS_RUN))
    assert out[0]["stopped_because"] == "verified" and out[0]["patch"]["status"] == "verified"
    assert (proj.work / "requirements.txt").read_text() == REQ and (proj.root / "requirements.txt").read_text() == REQ   # prepared, not applied


def test_mode_b_respects_the_attempt_limit_and_records_every_attempt(proj):
    fid = jinja_finding(proj)["id"]
    proj.state.set_max_attempts(2)
    fail = {"status": "failed", "tests": {}, "exit_code": 4, "output": "E   boom", "steps": []}
    fn = propose_fn()
    out = W.repair_mode_b(proj, [fid], True, "m", fn, vfn(fail))
    assert out[0]["stopped_because"] == "attempt_limit_reached" and len(fn.calls) == 2 and fn.calls[1][1] and "boom" in fn.calls[1][1]
    assert [a["outcome"] for a in proj.state.attempts_for(fid)] == ["failed", "failed"]


# ---------------------------------------------------------------- pre-publish
def applied_patch(proj):
    pid = W.repair_mode_a(proj, jinja_finding(proj)["id"], "m", propose_fn())["patch"]["id"]
    W.verify_patch_action(proj, pid, vfn(PASS_RUN))
    W.apply_patch(proj, pid, "APPLY")
    return pid


def test_prepublish_reviews_the_patched_tree_and_is_stored_for_the_capsule(proj):
    pid = W.repair_mode_a(proj, jinja_finding(proj)["id"], "m", propose_fn())["patch"]["id"]
    W.verify_patch_action(proj, pid, vfn(PASS_RUN))
    r = W.run_prepublish(proj, [pid])                                                                       # reviewed on a temp copy: nothing applied
    assert r["stop_recommended"] is False and proj.prepublish is r and (proj.work / "requirements.txt").read_text() == REQ
    assert capsule.validate(W.live_capsule(proj, W.read_capsule(proj))) is None and W.live_capsule(proj, W.read_capsule(proj))["prepublish"]["checks"]
    with pytest.raises(ValueError, match="at least one"):
        W.run_prepublish(proj, [])


def test_applying_a_patch_invalidates_earlier_prepublish_results(proj):
    pid = W.repair_mode_a(proj, jinja_finding(proj)["id"], "m", propose_fn())["patch"]["id"]
    W.verify_patch_action(proj, pid, vfn(PASS_RUN))
    W.run_prepublish(proj, [pid])
    W.apply_patch(proj, pid, "APPLY")
    assert proj.prepublish is None


# ---------------------------------------------------------------- GitHub publication (two approvals)
def gh_project(tmp_path, runs, gh):
    gh.zip_bytes = make_zip_bytes({f"o-r-{BASE_SHA[:7]}/{k}": v for k, v in FILES.items()})
    client = GitHubClient("tok", opener=gh)
    p = W.register_github(runs, client, "o", "r", "main")
    out = tmp_path / "gout"
    cli.analyze(p.root, out, "m", use_ai=False, run_tests=False, meta_cache=tmp_path / "m.json", per_dependency=False, repository_input=p.input)
    p.capsule_path = out / "capsule.json"
    return p, client


def test_github_project_records_branch_and_exact_commit(tmp_path, runs):
    gh = FakeGitHub()
    p, _ = gh_project(tmp_path, runs, gh)
    assert p.input["commit"] == BASE_SHA and p.input["branch"] == "main" and p.untrusted and p.github["sha"] == BASE_SHA
    assert W.read_capsule(p)["repository_input"]["commit"] == BASE_SHA                                       # findings are traceable to the analysed revision
    assert all(c[2] is None or c[2].startswith("Bearer") for c in gh.calls)


def test_publication_needs_applied_patch_two_approvals_and_publishes_exactly_the_reviewed_bytes(tmp_path, runs):
    gh = FakeGitHub()
    p, client = gh_project(tmp_path, runs, gh)
    pid = W.repair_mode_a(p, jinja_finding(p)["id"], "m", propose_fn())["patch"]["id"]
    W.verify_patch_action(p, pid, vfn(PASS_RUN))
    with pytest.raises(ValueError, match="apply the selected patches"):
        W.publish_plan(p, client, [pid])                                                                      # unapplied changes are never published
    W.apply_patch(p, pid, "APPLY")
    plan = W.publish_plan(p, client, [pid])
    assert plan["can_publish"] and plan["proposed_branch"].startswith("depguard/") and plan["verification"]["status"] == "passed" and plan["analysed_sha"] == BASE_SHA
    n = len([c for c in gh.calls if c[0] == "POST"])
    for bad in ("", "yes", "OPEN PR"):
        with pytest.raises(GitHubError, match="approval missing"):
            W.publish_branch(p, client, bad)
    assert len([c for c in gh.calls if c[0] == "POST"]) == n and gh.refs == {}                                # nothing was written without approval 1
    with pytest.raises(ValueError, match="publish the branch first"):
        W.publish_pr(p, client, "OPEN PR")
    br = W.publish_branch(p, client, "PUBLISH")
    blob = [c[3] for c in gh.calls if c[1].endswith("/git/blobs")]
    assert len(blob) == 2 and gh.refs == {f"refs/heads/{plan['proposed_branch']}": "c" * 40}
    import base64
    assert any(b"Jinja2==3.1.6" in base64.b64decode(b["content"]) for b in blob)
    with pytest.raises(GitHubError, match="approval missing"):
        W.publish_pr(p, client, "PUBLISH")                                                                    # approval 1 does not authorise the PR
    res = W.publish_pr(p, client, "OPEN PR")
    assert res["html_url"].endswith("/pull/7") and br["branch"] == plan["proposed_branch"]
    body = [c[3] for c in gh.calls if c[1].endswith("/pulls")][0]["body"]
    assert "passed" in body and "does not prove full compatibility" in body and "Opened only after explicit human approval" in body


def test_publication_refuses_when_the_branch_moved_since_analysis(tmp_path, runs):
    gh = FakeGitHub()
    p, client = gh_project(tmp_path, runs, gh)
    pid = applied_patch(p)
    gh.base_sha = "b" * 40
    plan = W.publish_plan(p, client, [pid])
    assert plan["can_publish"] is False and "branch moved since analysis" in plan["publish_blocker"]
    with pytest.raises(GitHubError, match="publishing is not available|moved"):
        W.publish_branch(p, client, "PUBLISH")
    assert gh.refs == {}


def test_published_bytes_must_equal_the_reviewed_patch(tmp_path, runs):
    gh = FakeGitHub()
    p, client = gh_project(tmp_path, runs, gh)
    pid = applied_patch(p)
    W.publish_plan(p, client, [pid])
    (p.work / "requirements.txt").write_text("tampered==1\n")                                                 # changed after review
    with pytest.raises(GitHubError, match="no longer matches the reviewed patch"):
        W.publish_branch(p, client, "PUBLISH")
    assert gh.refs == {}


def test_a_high_confidence_secret_in_a_changed_file_recommends_stopping_and_needs_acknowledgement(tmp_path, runs):
    gh = FakeGitHub()
    p, client = gh_project(tmp_path, runs, gh)
    pid = applied_patch(p)
    secret = "ghp_" + "Zx9Qw3Er5Ty7Ui1Op4As6Df8Gh2Jk0Lz3Xc5V"
    f = p.work / "requirements.txt"
    f.write_bytes(f.read_bytes() + b"# " + secret.encode() + b"\n")   # a credential lands in a changed file after the patch
    review = W.run_prepublish(p, [pid])
    assert review["stop_recommended"] and any("credential" in r for r in review["stop_reasons"]) and secret not in json.dumps(review)
    plan = W.publish_plan(p, client, [pid])
    assert any("recommend stopping" in w for w in plan["warnings"]) and secret not in json.dumps(plan)
    with pytest.raises(ValueError, match="recommend stopping"):
        W.publish_branch(p, client, "PUBLISH")                                                                      # blocked until the user acknowledges
    with pytest.raises(GitHubError, match="no longer matches the reviewed patch"):
        W.publish_branch(p, client, "PUBLISH", acknowledge_stop=True)                                              # ...and even then, unreviewed bytes never ship
    assert gh.refs == {}
    live = json.dumps(W.live_capsule(p, W.read_capsule(p)))
    assert secret not in live                                                                                        # the capsule never contains the value


def test_publishing_is_unavailable_for_non_github_projects(proj):
    pid = applied_patch(proj)
    with pytest.raises(ValueError, match="only available for repositories opened from GitHub"):
        W.publish_plan(proj, GitHubClient("t", opener=FakeGitHub()), [pid])


def test_capsule_identifies_the_project_by_its_real_name_and_the_analysed_commit(tmp_path, runs):
    gh = FakeGitHub()
    p, _ = gh_project(tmp_path, runs, gh)
    # gh_project analyses with repository_input=p.input (no label): add the label exactly as the dashboard does
    out = tmp_path / "labelled"
    cli.analyze(p.root, out, "m", use_ai=False, run_tests=False, meta_cache=tmp_path / "m2.json", per_dependency=False, repository_input={**p.input, "label": p.label})
    cap = json.loads((out / "capsule.json").read_text())
    assert cap["repository"]["path_name"] == "o/r" and cap["repository"]["remote"] == "https://github.com/o/r"
    assert cap["repository"]["commit"] == BASE_SHA and cap["repository"]["dirty"] is False and cap["repository_input"]["commit"] == BASE_SHA
    capsule.validate(cap)
    z = W.register_zip(runs, make_zip_bytes({k: v for k, v in FILES.items()}), "my-project.zip")        # no top-level folder: the extract is named 'extracted'
    out2 = tmp_path / "zip"
    cli.analyze(z.root, out2, "m", use_ai=False, run_tests=False, meta_cache=tmp_path / "m3.json", per_dependency=False, repository_input={**z.input, "label": z.label})
    cap2 = json.loads((out2 / "capsule.json").read_text())
    assert z.root.name == "extracted" and cap2["repository"]["path_name"] == "my-project" and cap2["repository"]["commit"] is None
