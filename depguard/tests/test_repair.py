"""Repair modes A/B, decisions (repair / ignore / review later), bounded retries, verification classification. Model and verifier are injected."""
import json

import pytest

from depguard import findings
from depguard.repair import patch as P, planner as PL
from depguard.repair.state import RepairState, StateError

REQ = "toml==0.10.2\nJinja2==2.11.3\nsix\n"
PYP = '[project]\nname = "x"\ndependencies = [\n    "toml==0.10.2",\n    "Jinja2==2.11.3",\n    "six",\n]\n'


@pytest.fixture
def root(tmp_path):
    (tmp_path / "requirements.txt").write_text(REQ)
    (tmp_path / "pyproject.toml").write_text(PYP)
    (tmp_path / "app.py").write_text("from jinja2 import Environment\nimport certifi\n")
    return tmp_path


def finding(pkg="jinja2", rule="observed_in_baseline_output", usage=None, resolved=None):
    return {"id": findings.finding_id(pkg), "package": pkg, "title": f"{pkg} problem", "severity": "high",
            "detected_version": {"declared": [], "pinned": "2.11.3", "resolved": resolved, "used_for_checks": "2.11.3"},
            "evidence": [{"rule": rule, "kind": "observed", "severity": "high", "text": "ImportError soft_unicode", "source": "tests"}],
            "usage": usage if usage is not None else [{"file": "app.py", "line": 1}], "declared_in": ["requirements.txt:2", "pyproject.toml:5"],
            "why_it_matters": ["breaks tests"], "unknown": [], "impact": {"summary": "1 import"}, "verification_requirements": []}


def proposal(edits, action="upgrade", **kw):
    return {"package": "jinja2", "action": action, "summary": "upgrade jinja2", "rationale": "because", "edits": edits, "replacement_candidates": [],
            "assumptions": ["a"], "compatibility_concerns": ["c"], "suggested_tests": ["t"], "confidence_limitations": "small model", **kw}


GOOD_EDITS = [{"file": "requirements.txt", "find": "Jinja2==2.11.3", "replace": "Jinja2==3.1.6", "reason": "markupsafe compatibility"},
              {"file": "pyproject.toml", "find": '"Jinja2==2.11.3",', "replace": '"Jinja2==3.1.6",', "reason": "keep manifests consistent"}]


class FakeModel:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def __call__(self, model, ctx, feedback=None):
        self.calls.append({"ctx": ctx, "feedback": feedback})
        r = self.responses.pop(0) if self.responses else self.responses_default()
        return r

    @staticmethod
    def responses_default():
        return {"status": "ok", "data": proposal([]), "model": {"model": "m"}}


def ok(data):
    return {"status": "ok", "data": data, "model": {"model": "gemma4:e2b"}}


def run(status, passed=0, failed=0, skipped=0, **kw):
    tests = {**{f"t::p{i}": "passed" for i in range(passed)}, **{f"t::f{i}": "failed" for i in range(failed)}, **{f"t::s{i}": "skipped" for i in range(skipped)}}
    return {"status": status, "tests": tests, "exit_code": 0 if status == "passed" else 1, "output": kw.pop("output", ""), "steps": [], **kw}


BASE = {"status": "failed", "tests": {**{f"t::p{i}": "passed" for i in range(9)}, "t::f0": "failed"}}
FIXED_RUN = {"status": "passed", "tests": {k: "passed" for k in BASE["tests"]}, "exit_code": 0, "output": "", "steps": []}   # same tests, all passing


# ---------------------------------------------------------------- verification classification (never optimistic)
@pytest.mark.parametrize("proposed,expected", [
    (run("passed", passed=10), "verified"),
    (run("passed", passed=9, skipped=1), "partially_verified"),                 # a skipped test is unproven
    (run("passed", passed=9), "partially_verified"),                            # fewer tests ran than the baseline
    (run("failed", passed=9, failed=1), "failed"),                              # still failing, fixed nothing
    (run("failed", passed=8, failed=2), "failed"),                              # a regression
    (run("failed"), "failed"),                                                  # nothing could be collected
    (run("environment_blocked"), "blocked"),
    ({"status": "not_run"}, "not_run"),
])
def test_classification_table(proposed, expected):
    assert PL.classify_verification(BASE, proposed)["status"] == expected


def test_improvement_with_remaining_failures_is_partial_and_regressions_always_fail():
    base = {"status": "failed", "tests": {"a": "failed", "b": "failed", "c": "passed"}}
    v = PL.classify_verification(base, {"status": "failed", "tests": {"a": "passed", "b": "failed", "c": "passed"}})
    assert v["status"] == "partially_verified" and v["fixed"] == ["a"]
    v = PL.classify_verification(base, {"status": "passed", "tests": {"a": "passed", "b": "passed"}})   # c vanished
    assert v["status"] == "failed" and v["regressions"] == ["c"]


def test_no_baseline_cannot_be_fully_verified():
    v = PL.classify_verification(None, run("passed", passed=3))
    assert v["status"] == "partially_verified" and any("baseline" in r for r in v["reasons"])


# ---------------------------------------------------------------- Mode A: one attempt, nothing verified or applied
def test_mode_a_creates_a_patch_and_stops_without_verifying_or_touching_files(root):
    state, model = RepairState(), FakeModel(ok(proposal(GOOD_EDITS)))
    f = finding()
    r = PL.propose(root, f, state, "m", model, mode="A")
    p = r["patch"]
    assert r["outcome"] == "proposed" and p["status"] == "proposed" and p["verification"] is None and p["origin"] == "ai"
    assert p["files"][0]["evidence"] and p["assumptions"] == ["a"] and p["compatibility_concerns"] == ["c"] and p["suggested_tests"] == ["t"]
    assert (root / "requirements.txt").read_text() == REQ and (root / "pyproject.toml").read_text() == PYP     # nothing was applied
    assert state.decision(f["id"])["status"] == "repair_proposed" and state.attempts_for(f["id"])[0]["mode"] == "A"


def test_the_model_sees_evidence_and_bounded_source_context_only(root):
    model = FakeModel(ok(proposal(GOOD_EDITS)))
    PL.propose(root, finding(), RepairState(), "m", model, mode="A")
    ctx = model.calls[0]["ctx"]
    assert ctx["package"] == "jinja2" and ctx["editable_files"][:2] == ["pyproject.toml", "requirements.txt"] and "app.py" in ctx["editable_files"]
    assert ctx["usage_snippets"][0]["text"].startswith("from jinja2") and len(json.dumps(ctx)) <= PL.MAX_CONTEXT_CHARS + 500
    assert "unknowns" in ctx and ctx["python_target"] == "3.12 or 3.13"


def test_mode_a_revision_passes_user_feedback_and_counts_an_attempt(root):
    state, model = RepairState(), FakeModel(ok(proposal(GOOD_EDITS)), ok(proposal(GOOD_EDITS[:1])))
    f = finding()
    PL.propose(root, f, state, "m", model, mode="A")
    PL.propose(root, f, state, "m", model, mode="A", feedback="keep pyproject untouched")
    assert model.calls[1]["feedback"] == "keep pyproject untouched" and len(state.attempts_for(f["id"])) == 2


# ---------------------------------------------------------------- honest failure paths
def test_no_reliable_recommendation_is_respected_not_forced(root):
    state = RepairState()
    r = PL.propose(root, finding(), state, "m", FakeModel(ok(proposal([], action="no_reliable_recommendation", confidence_limitations="evidence insufficient"))), mode="A")
    assert r["patch"] is None and r["outcome"] == "no_reliable_recommendation" and "insufficient" in r["detail"]
    assert state.data["patches"] == {} and state.decision(finding()["id"])["status"] == "open"


@pytest.mark.parametrize("resp,outcome", [({"status": "blocked", "reason": "Ollama down"}, "model_unavailable"), ({"status": "invalid_response", "reason": "bad json"}, "invalid_response")])
def test_unavailable_or_malformed_model_never_produces_a_patch(root, resp, outcome):
    state = RepairState()
    r = PL.propose(root, finding(), state, "m", FakeModel(resp), mode="A")
    assert r["patch"] is None and r["outcome"] == outcome and state.attempts_for(finding()["id"])[0]["outcome"] == outcome


def test_invalid_edits_are_rejected_with_reasons_and_no_patch_is_created(root):
    bad = [{"file": "../../etc/passwd", "find": "x", "replace": "y", "reason": "r"}, {"file": "other.py", "find": "a", "replace": "b", "reason": "r"}]
    state = RepairState()
    r = PL.propose(root, finding(), state, "m", FakeModel(ok(proposal(bad))), mode="A")
    assert r["patch"] is None and r["outcome"] == "rejected_by_validation" and "unrelated file" in r["detail"] and state.data["patches"] == {}


def test_attempt_limit_blocks_further_proposals_until_the_user_raises_it(root):
    state, f = RepairState(max_attempts=1), finding()
    model = FakeModel(ok(proposal(GOOD_EDITS)), ok(proposal(GOOD_EDITS)))
    PL.propose(root, f, state, "m", model, mode="A")
    r = PL.propose(root, f, state, "m", model, mode="A")
    assert r["outcome"] == "attempt_limit_reached" and len(model.calls) == 1
    state.set_max_attempts(3)
    state.reset_attempts(f["id"])
    assert PL.propose(root, f, state, "m", model, mode="A")["outcome"] == "proposed" and len(state.attempts_for(f["id"])) == 2   # history kept
    with pytest.raises(StateError):
        state.set_max_attempts(99)


# ---------------------------------------------------------------- deterministic repair (no AI)
def test_undeclared_import_gets_a_deterministic_patch_without_calling_the_model(root):
    f = finding("certifi", "imported_but_undeclared", usage=[{"file": "app.py", "line": 2}], resolved="2026.1.4")
    model = FakeModel()
    r = PL.propose(root, f, RepairState(), "m", model, mode="A")
    assert model.calls == [] and r["patch"]["origin"] == "deterministic" and {x["path"] for x in r["patch"]["files"]} == {"requirements.txt", "pyproject.toml"}
    assert "+certifi==2026.1.4" in r["patch"]["files"][0]["diff"]


# ---------------------------------------------------------------- verification of one patch
def verifier(result):
    calls = []

    def fn(root, edits):
        calls.append(edits)
        return result
    fn.calls = calls
    return fn


def test_verify_records_exactly_what_ran_and_updates_the_same_attempt(root):
    state, f = RepairState(), finding()
    p = PL.propose(root, f, state, "m", FakeModel(ok(proposal(GOOD_EDITS))), mode="A")["patch"]
    v = PL.verify_patch(root, p, state, verifier(FIXED_RUN), BASE)
    assert v["status"] == "verified" and v["fixed"] == ["t::f0"] and v["summary"] == "10 passed / 0 failed"
    assert state.patch(p["id"])["status"] == "verified" and state.patch(p["id"])["files"][0]["verification_status"] == "verified"
    assert len(state.attempts_for(f["id"])) == 1 and state.attempts_for(f["id"])[0]["outcome"] == "verified"   # one attempt, not two


def test_a_failed_patch_is_labelled_failed_and_can_be_reverified(root):
    state, f = RepairState(), finding()
    p = PL.propose(root, f, state, "m", FakeModel(ok(proposal(GOOD_EDITS))), mode="A")["patch"]
    assert PL.verify_patch(root, p, state, verifier(run("failed", output="E   ModuleNotFoundError: pkg_resources")), BASE)["status"] == "failed"
    assert state.patch(p["id"])["status"] == "failed"
    assert PL.verify_patch(root, state.patch(p["id"]), state, verifier(FIXED_RUN), BASE)["status"] == "verified"


# ---------------------------------------------------------------- Mode B: prepare, verify, bounded revision, never apply
def test_mode_b_prepares_and_verifies_but_never_applies(root):
    state, f = RepairState(), finding()
    out = PL.prepare_fix(root, f, state, "m", FakeModel(ok(proposal(GOOD_EDITS))), verifier(run("passed", passed=10)), BASE)
    assert out["stopped_because"] == "verified" and out["patch"]["status"] == "verified"
    assert (root / "requirements.txt").read_text() == REQ and (root / "pyproject.toml").read_text() == PYP


def test_mode_b_revises_after_a_failure_using_the_failure_as_data_then_stops_at_the_limit(root):
    state, f = RepairState(max_attempts=2), finding()
    model = FakeModel(ok(proposal(GOOD_EDITS)), ok(proposal(GOOD_EDITS[:1])), ok(proposal(GOOD_EDITS)))
    ver = verifier(run("failed", output="E   ModuleNotFoundError: No module named 'pkg_resources'"))
    out = PL.prepare_fix(root, f, state, "m", model, ver, BASE)
    assert out["stopped_because"] == "attempt_limit_reached" and len(model.calls) == 2 and len(ver.calls) == 2    # limit respected
    assert model.calls[0]["feedback"] is None and "pkg_resources" in model.calls[1]["feedback"]                  # the failure informed the revision
    assert [a["outcome"] for a in state.attempts_for(f["id"])] == ["failed", "failed"] and state.decision(f["id"])["status"] == "repair_failed"
    assert (root / "requirements.txt").read_text() == REQ


def test_mode_b_stops_immediately_when_the_environment_is_blocked(root):
    state, f = RepairState(max_attempts=3), finding()
    model = FakeModel(ok(proposal(GOOD_EDITS)), ok(proposal(GOOD_EDITS)))
    out = PL.prepare_fix(root, f, state, "m", model, verifier(run("environment_blocked", failure_kind="install_failure")), BASE)
    assert out["stopped_because"] == "environment_blocked" and len(model.calls) == 1   # revising cannot fix a blocked environment


def test_mode_b_gives_up_cleanly_on_no_reliable_recommendation_and_model_failures(root):
    f = finding()
    out = PL.prepare_fix(root, f, RepairState(), "m", FakeModel(ok(proposal([], action="no_reliable_recommendation"))), verifier(run("passed")), BASE)
    assert out["stopped_because"] == "no_reliable_recommendation" and out["patch"] is None
    out = PL.prepare_fix(root, f, RepairState(), "m", FakeModel({"status": "blocked", "reason": "down"}), verifier(run("passed")), BASE)
    assert out["stopped_because"] == "model_unavailable"


def test_mode_b_retries_after_validation_rejection_with_the_reasons(root):
    state, f = RepairState(max_attempts=2), finding()
    model = FakeModel(ok(proposal([{"file": "README.md", "find": "a", "replace": "b", "reason": "r"}])), ok(proposal(GOOD_EDITS)))
    out = PL.prepare_fix(root, f, state, "m", model, verifier(run("passed", passed=10)), BASE)
    assert out["stopped_because"] == "verified" and "rejected by validation" in model.calls[1]["feedback"] and "unrelated file" in model.calls[1]["feedback"]


# ---------------------------------------------------------------- decisions: ignore / review later never erase findings
def test_ignore_and_review_later_keep_the_finding_visible_with_status_and_reason(tmp_path):
    s = RepairState(tmp_path / "state.json")
    s.set_decision("f-1", "ignored", "vendored copy, not used in production")
    s.set_decision("f-2", "review_later")
    s2 = RepairState(tmp_path / "state.json")      # survives a restart
    assert s2.decision("f-1")["status"] == "ignored" and s2.decision("f-1")["reason"].startswith("vendored")
    assert s2.decision("f-2")["status"] == "review_later" and s2.decision("never-seen")["status"] == "open"
    s2.set_decision("f-1", "open")
    assert [h["status"] for h in s2.decision("f-1")["history"]] == ["ignored", "open"]              # history is kept
    with pytest.raises(StateError):
        s2.set_decision("f-1", "deleted")


def test_patch_status_flow_is_enforced(root):
    s = RepairState()
    p = P.make_patch(root, "f", "jinja2", GOOD_EDITS, "ai")
    s.add_patch(p)
    with pytest.raises(StateError, match="cannot go from"):
        s.set_patch_status(p["id"], "applied")                       # a proposal cannot be applied before verification/approval
    s.set_patch_status(p["id"], "rejected")
    with pytest.raises(StateError):
        s.set_patch_status(p["id"], "accepted_for_verification")     # rejected is terminal
    with pytest.raises(StateError, match="unknown patch"):
        s.patch("nope")


def test_corrupted_state_file_is_reset_and_reported(tmp_path):
    (tmp_path / "s.json").write_text("{broken")
    s = RepairState(tmp_path / "s.json")
    assert s.warning and "unreadable" in s.warning and (tmp_path / "s.json.corrupt").exists() and s.data["decisions"] == {}
    s.set_decision("f", "ignored")
    assert RepairState(tmp_path / "s.json").decision("f")["status"] == "ignored"
