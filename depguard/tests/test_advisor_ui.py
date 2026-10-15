"""Advisor page logic (advisor.js) checked against the real demo capsule and edge cases. Needs Node.js; skipped otherwise."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(not NODE, reason="node not installed")


@pytest.fixture(scope="module")
def out():
    r = subprocess.run([NODE, str(Path(__file__).parent / "advisor_model_check.js")], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_demo_counts_come_from_recorded_runs(out):
    d = out["demo"]
    assert d["counts"]["recs"] == 4 and d["counts"]["unverified"] == 1 and d["counts"]["coveredByCombined"] == 3
    # one combined AI proposal (failed) + one independent experiment (passed)
    assert (d["counts"]["testedProposals"], d["counts"]["passed"], d["counts"]["failed"]) == (2, 1, 1)
    assert d["counts"]["filter"] == {"all": 5, "unverified": 1, "passed": 1, "failed": 3}


def test_each_recommendation_status_and_edit_accounting(out):
    st = {p: (s, acc, n) for p, s, acc, n in out["demo"]["statuses"]}
    assert st["jinja2"] == ("failed", 1, 2)  # 2 edits proposed: one accepted+tested, one rejected by validation
    assert st["certifi"][0] == "unverified" and st["certifi"][2] == 0  # no edits proposed -> never claimed as tested
    assert all(v[0] == "failed" for p, v in st.items() if p != "certifi")


def test_independent_experiment_kept_separate_and_passed(out):
    assert out["demo"]["exps"] == [["jinja2-only", "passed"]]


def test_failed_run_evidence_is_the_recorded_command_and_error(out):
    assert any("pkg_resources" in l for l in out["demoErr"])
    assert "-m pytest" in out["demoCmd"] and out["demoCmd"].startswith("python ")


def test_blocked_combined_run_gives_no_pass_fail_claims(out):
    b = out["blocked"]
    assert all(s == "unverified" for _, s, _, _ in b["statuses"])
    assert b["counts"]["testedProposals"] == 1 and b["counts"]["failed"] == 0  # only the independent experiment counts


def test_passed_combined_run_marks_only_recs_with_accepted_edits(out):
    p = out["passed"]
    assert {pk: s for pk, s, _, _ in p["statuses"]} == {"jinja2": "passed", "setuptools": "passed", "certifi": "unverified", "pydantic": "passed"}


def test_no_experiments_and_no_ai_are_handled(out):
    assert out["noexp"]["exps"] == [] and out["noexp"]["counts"]["testedProposals"] == 1
    assert out["noai"]["statuses"] == [] and out["noai"]["counts"]["recs"] == 0
