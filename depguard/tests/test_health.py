"""Health score: transparent deductions, unknown is never penalised."""
import json
from datetime import datetime, timezone
from pathlib import Path

from depguard import health

NOW = datetime(2026, 10, 15, tzinfo=timezone.utc)


def ind(pkg, kind, sev="warn"):
    return {"package": pkg, "indicator": kind, "severity": sev, "evidence": ["e"]}


def test_clean_repo_scores_100_and_unknown_not_penalised():
    h = health.compute(["a", "b"], [ind("a", "maintenance_activity", "info")], "passed", today=NOW)
    assert h["score"] == 100 and h["kind"] == "heuristic"
    assert any("package index metadata" in u["check"] for u in h["unchecked"])  # unchecked, but not deducted
    assert all(not p["deductions"] for p in h["packages"])


def test_deductions_use_documented_weights_and_are_listed():
    h = health.compute(["a", "b"], [ind("a", "observed_in_baseline_output", "high"), ind("a", "conflicting_constraints"),
                                    ind("b", "no_static_import_found", "info")], "failed", today=NOW)
    a = next(p for p in h["packages"] if p["package"] == "a")
    assert a["score"] == 100 - 35 - 20 and {d["rule"] for d in a["deductions"]} == {"observed_in_baseline_output", "conflicting_constraints"}
    b = next(p for p in h["packages"] if p["package"] == "b")
    assert b["score"] == 95 and b["deductions"][0]["basis"] == "heuristic"
    assert h["score"] == round((45 + 95) / 2)


def test_warn_vs_high_baseline_weight():
    w = health.compute(["a"], [ind("a", "observed_in_baseline_output", "warn")], "passed", today=NOW)
    assert w["packages"][0]["score"] == 85


def test_missing_baseline_is_unchecked_and_lowers_coverage():
    a = health.compute(["a"], [], "not_run", today=NOW)
    b = health.compute(["a"], [], "passed", today=NOW)
    assert any(u["check"] == "baseline test output" for u in a["unchecked"])
    assert a["evidence_coverage"]["percent"] < b["evidence_coverage"]["percent"]


def test_metadata_rules_need_real_metadata_with_source_and_time():
    meta = {"a": {"status": "ok", "source": "https://pypi.org/pypi/a/json", "fetched_at": "2026-10-15T00:00:00+00:00",
                  "requires_python": "<3.12", "latest_upload_time": "2020-01-01T00:00:00Z", "inactive_classifier": True},
            "b": {"status": "unavailable", "error": "offline"}}
    h = health.compute(["a", "b"], [], "passed", metadata=meta, today=NOW)
    a = next(p for p in h["packages"] if p["package"] == "a")
    assert {d["rule"] for d in a["deductions"]} == {"inactive_classifier", "requires_python_excludes", "stale_latest_release"}
    assert "pypi.org" in a["deductions"][0]["evidence"][0]
    b = next(p for p in h["packages"] if p["package"] == "b")
    assert b["score"] == 100 and not b["metadata_checked"]  # unavailable metadata is not a penalty


def test_pip_check_conflict_deducts_at_repo_level():
    assert health.compute(["a"], [], "passed", pip_check_failed=True, today=NOW)["score"] == 70


def test_from_real_demo_capsule_is_deterministic_and_explained():
    cap = json.loads((Path(__file__).parents[2] / "demo-output" / "capsule.json").read_text(encoding="utf-8"))
    h = health.from_capsule(cap, metadata={})
    assert 0 <= h["score"] < 100
    jinja = next(p for p in h["packages"] if p["package"] == "jinja2")
    assert any(d["rule"] == "observed_in_baseline_output" for d in jinja["deductions"])
    assert health.from_capsule(cap, metadata={}) == h


# ---------------------------------------------------------------- exact-version evidence (advisories, yanked)
def vmeta(name, version, **kw):
    base = {"name": name, "version": version, "status": "ok", "source": f"https://pypi.org/pypi/{name}/{version}/json", "fetched_at": "2026-10-15T00:00:00+00:00",
            "vulnerabilities": [], "yanked": False, "requires_python": None}
    return {f"{name}=={version}": {**base, **kw}}


def test_listed_advisory_for_the_exact_version_is_a_confirmed_observed_deduction_with_source():
    vm = vmeta("jinja2", "2.11.3", vulnerabilities=[{"id": "PYSEC-1", "fixed_in": ["3.1.4"], "withdrawn": None}])
    h = health.compute(["jinja2"], [], "passed", version_metadata=vm, today=NOW)
    d = h["packages"][0]["deductions"][0]
    assert d["rule"] == "confirmed_vulnerability" and d["points"] == 40 and d["basis"] == "observed"
    assert "PYSEC-1 (fixed in 3.1.4)" in d["evidence"][0] and "pypi.org/pypi/jinja2/2.11.3" in d["evidence"][0] and "not an independent audit" in d["evidence"][0]
    assert h["packages"][0]["version_checked"] is True


def test_no_listed_advisory_is_not_a_good_result_and_withdrawn_ones_do_not_count():
    clean = health.compute(["six"], [], "passed", version_metadata=vmeta("six", "1.17.0"), today=NOW)
    assert clean["packages"][0]["deductions"] == [] and not any("exact version" in u["check"] for u in clean["unchecked"])   # checked: nothing deducted
    withdrawn = vmeta("six", "1.0", vulnerabilities=[{"id": "X", "withdrawn": "2025-01-01"}])
    assert health.compute(["six"], [], "passed", version_metadata=withdrawn, today=NOW)["packages"][0]["deductions"] == []
    assert "never means 'secure'" in health.DISCLAIMER   # the wording itself must not imply safety


def test_unchecked_exact_version_status_is_listed_not_penalised():
    h = health.compute(["a", "b"], [], "passed", version_metadata=vmeta("a", "1.0"), today=NOW)
    u = next(x for x in h["unchecked"] if "exact version" in x["check"])
    assert "1 of 2" in u["reason"] and all(not p["deductions"] for p in h["packages"])


def test_yanked_release_and_version_python_exclusion_deduct_once():
    vm = vmeta("old", "1.0", yanked=True, yanked_reason="broken", requires_python="<3.12")
    meta = {"old": {"status": "ok", "source": "s", "fetched_at": "f", "requires_python": "<3.12", "latest_upload_time": None}}
    h = health.compute(["old"], [], "passed", metadata=meta, version_metadata=vm, today=NOW)
    rules = [d["rule"] for d in h["packages"][0]["deductions"]]
    assert sorted(rules) == ["requires_python_excludes", "yanked_release"]   # the Python exclusion is not counted twice
    assert h["packages"][0]["score"] == 100 - 30 - 30
