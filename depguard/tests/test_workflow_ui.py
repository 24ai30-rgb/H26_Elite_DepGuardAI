"""Workflow overview logic (workflow.js) against the real demo capsule: the known evidence must be preserved and never inflated. Needs Node."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(not NODE, reason="node not installed")


@pytest.fixture(scope="module")
def out():
    r = subprocess.run([NODE, str(Path(__file__).parent / "workflow_model_check.js")], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_known_evidence_is_preserved_in_the_verdicts(out):
    v = {k: (s, d) for k, s, d in out["demo"]["verdicts"]}
    assert v["baseline"][0] == "failed" and "9 passed · 1 failed" in v["baseline"][1]            # original: 9 passed / 1 failed
    assert v["ai"][0] == "failed" and "exit 4" in v["ai"][1] and "NOT a successful migration" in v["ai"][1]
    assert v["exp0"][0] == "passed" and "10 passed · 0 failed" in v["exp0"][1]                    # isolated experiment: 10 / 0
    assert "does not prove full compatibility" in v["exp0"][1] and "not applied" in v["exp0"][1]


def test_failed_verification_is_prominent_and_next_action_points_to_it(out):
    d = out["demo"]
    assert d["steps"]["verify"] == "failed" and d["anyFailed"] is True and d["next"] == "verify"


def test_step_statuses_reflect_recorded_data(out):
    s = out["demo"]["steps"]
    assert s == {"scan": "done", "risks": "attention", "gemma": "attention", "repair": "attention", "verify": "failed", "publish": "pending", "capsule": "done"}
    assert "2 high" in out["details"]["risks"] and "3 medium" in out["details"]["risks"]
    assert "includes 1 undeclared import: certifi" in out["details"]["risks"]
    assert "4 recommendations" in out["details"]["gemma"] and "unverified AI claims" in out["details"]["gemma"]
    assert "3 edit(s) proposed, 2 rejected" in out["details"]["repair"] and "nothing applied" in out["details"]["repair"] and "legacy" in out["details"]["repair"]


def test_findings_stay_open_unless_independently_tested(out):
    o = out["demo"]["open"]
    assert o["jinja2"].startswith("tested|fix verified in an isolated experiment")      # only Jinja2 has a passing isolated experiment
    assert o["pydantic"] == "failed|AI fix failed verification" and o["setuptools"].startswith("failed|")
    assert o["certifi"] == "open|open" and o["requests"] == "open|open"                   # undeclared certifi + private requests API remain findings


def test_missing_evidence_is_listed_and_nothing_overclaims(out):
    m = " | ".join(out["demo"]["missing"])
    assert "not checked" in m and "Repository activity" in m and "NOT proof" in m
    assert "not a container or sandbox" in m
    assert out["withMeta"].startswith("Package index metadata exists for 2 package(s)")


def test_empty_state_has_no_invented_progress(out):
    e = out["empty"]
    assert set(e["steps"].values()) == {"pending"} and e["verdicts"] == [] and e["open"] == {} and e["next"] == "scanner"


def test_never_success_without_tests(out):
    n = out["nothingTested"]
    assert n["steps"]["verify"] == "pending" and n["anyFailed"] is False  # baseline fails but no proposal was tested: not started, never success
    p = out["combinedPassed"]
    assert p["steps"]["verify"] == "done" and any(s == "passed" for k, s, _ in p["verdicts"] if k == "ai")
    assert "passing tests do not prove full compatibility" in dict((k, d) for k, _, d in p["verdicts"])["ai"]


def test_invalid_capsule_and_blocked_model(out):
    assert out["invalid"]["steps"]["capsule"] == "failed"
    assert out["noai"]["steps"]["gemma"] == "blocked" and "Ollama down" in out["noai"]["detail"]


# ---------------------------------------------------------------- new capsules: findings, patches, publication
def test_findings_summary_counts_decisions_and_unresolved_attention(out):
    f = out["findingsOnly"]
    assert f["steps"]["risks"] == "attention" and "3 finding(s): 0 critical · 1 high · 1 medium · 1 low" in f["risks"] and "2 open, 1 ignored" in f["risks"]
    assert "No repair requested yet" in f["repair"] and f["steps"]["repair"] == "pending" and f["next"] == "inventory"
    assert out["allDecided"]["risks"] == "done"            # ignored/repaired serious findings are decided, nothing left needing attention


def test_a_proposed_patch_is_attention_not_progress_and_next_action_points_to_it(out):
    p = out["proposedPatch"]
    assert p["steps"]["repair"] == "attention" and "1 proposed" in p["repairDetail"] and p["next"] == "repair"
    assert p["steps"]["verify"] == "pending" and p["steps"]["publish"] == "pending"


def test_failed_patch_verification_is_failed_and_prominent(out):
    f = out["failedPatch"]
    assert f["steps"]["verify"] == "failed" and f["anyFailed"] and f["next"] == "verify"
    patch = [v for v in f["verdicts"] if v[0] == "patch:p-1"][0]
    assert patch[1] == "failed" and "the proposed run still fails" in patch[2] and "exit 4" in patch[2]


def test_rejected_or_stale_patches_do_not_keep_verification_red(out):
    assert out["rejectedFailedPatch"]["verify"] == "pending"


def test_partial_verification_is_labelled_partial_not_clean(out):
    v = [x for x in out["partialPatch"] if x[0] == "patch:p-1"][0]
    assert v[1] == "passed" and "partially verified: 1 test was skipped" in v[2] and "does not prove full compatibility" in v[2]


def test_applied_patch_then_publication_steps(out):
    a = out["appliedPatch"]
    assert a["steps"]["repair"] == "done" and a["steps"]["verify"] == "done" and a["steps"]["publish"] == "attention" and a["next"] == "publish"
    assert "1 applied patch(es) ready to export or publish" in a["publishDetail"] and "pre-publish checks: not run" in a["publishDetail"]
    assert "pre-publish checks: warn" in out["appliedWithPre"]
    assert out["published"]["status"] == "done" and "Pull request #7 opened" in out["published"]["detail"]


def test_missing_evidence_mentions_advisories_unsupported_formats_and_lockfiles(out):
    m = " | ".join(out["advisoryMissing"])
    assert "checked for 1 exact version(s)" in m and "NOT proof of safety" in m and "Unsupported formats present and not analysed: Pipfile" in m and "No lockfile was found" in m
    assert "Known-advisory / yanked status of exact versions: not checked" in " | ".join(out["noAdvisory"])
