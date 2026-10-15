"""Findings from recorded evidence: severity rules, stable ids, unknowns, and the real LinkWatch demo capsule."""
import json
from pathlib import Path

from depguard import findings, impact, indicators, scanner, usage

DEMO = json.loads((Path(__file__).parents[2] / "demo-output" / "capsule.json").read_text(encoding="utf-8"))


def ind(pkg, rule, sev="warn", ev="e"):
    return {"package": pkg, "indicator": rule, "severity": sev, "evidence": [ev]}


def scan_of(**pins):
    return {"dependencies": [{"name": n, "specifier": f"=={v}" if v else "", "pinned_version": v, "file": "requirements.txt", "line": i + 1, "resolved_version": None}
                             for i, (n, v) in enumerate(pins.items())]}


def vm(name, version, **kw):
    e = {"name": name, "version": version, "status": "ok", "source": f"https://pypi.org/pypi/{name}/{version}/json", "fetched_at": "T",
         "vulnerabilities": [], "yanked": False, "requires_python": None}
    return {f"{name}=={version}": {**e, **kw}}


def test_severity_rules_pick_the_highest_applicable():
    fs = findings.build(scan_of(a="1.0", b="1.0", c="1.0", d="1.0"),
                        [ind("a", "observed_in_baseline_output", "high"), ind("a", "private_api_import"),
                         ind("b", "conflicting_constraints"), ind("b", "no_static_import_found", "info"),
                         ind("c", "no_static_import_found", "info"), ind("d", "observed_in_baseline_output", "warn")],
                        baseline_status="failed")
    sev = {f["package"]: f["severity"] for f in fs}
    assert sev == {"a": "high", "b": "medium", "c": "low", "d": "medium"}   # a deprecation WARNING is weaker than a failure
    assert [f["package"] for f in fs] == ["a", "b", "d", "c"]                # sorted by severity, then name


def test_listed_advisory_for_the_exact_version_is_critical_and_cites_its_source():
    fs = findings.build(scan_of(jinja2="2.11.3"), [], version_metadata=vm("jinja2", "2.11.3", vulnerabilities=[{"id": "PYSEC-1", "fixed_in": ["3.1.4"], "withdrawn": None}]), baseline_status="passed")
    f = fs[0]
    assert f["severity"] == "critical" and f["severity_basis"] == ["confirmed_vulnerability"]
    e = f["evidence"][0]
    assert e["kind"] == "metadata" and "PYSEC-1" in e["text"] and e["source"].startswith("https://pypi.org/pypi/jinja2/2.11.3") and e["retrieved_at"] == "T"


def test_inactivity_alone_is_low_severity_and_never_called_unsafe():
    meta = {"x": {"status": "ok", "source": "s", "fetched_at": "T", "latest_upload_time": "2015-01-01T00:00:00Z", "requires_python": None}}
    f = findings.build(scan_of(x="1.0"), [], metadata=meta, baseline_status="passed")[0]
    assert f["severity"] == "low" and f["evidence"][0]["kind"] == "heuristic"
    assert "not proof" in " ".join(f["why_it_matters"]).lower()   # the explanation itself must not call inactivity unsafe


def test_packages_without_indicators_have_no_finding_and_no_issue_is_not_a_clean_bill():
    assert findings.build(scan_of(a="1.0"), [], baseline_status="passed") == []


def test_unknowns_are_listed_per_finding():
    f = findings.build(scan_of(a=None), [ind("a", "private_api_import")], baseline_status="not_run")[0]
    joined = " ".join(f["unknown"])
    assert "No exact version" in joined and "Release recency" in joined and "Baseline tests were not run" in joined
    g = findings.build(scan_of(a="1.0"), [ind("a", "private_api_import")], baseline_status="passed")[0]
    assert any("a==1.0: not checked" in u for u in g["unknown"])            # version known, advisory check not run
    h = findings.build(scan_of(a="1.0"), [ind("a", "private_api_import")], version_metadata=vm("a", "1.0"), baseline_status="passed")[0]
    assert not any("not checked" in u for u in h["unknown"])


def test_ids_are_stable_and_actions_start_open():
    a = findings.build(scan_of(a="1.0"), [ind("a", "private_api_import")])[0]
    b = findings.build(scan_of(a="2.0", z="1"), [ind("a", "private_api_import")])[0]
    assert a["id"] == b["id"] == findings.finding_id("a") and a["status"] == "open" and a["actions"] == ["repair", "ignore", "review_later"]
    assert len(a["verification_requirements"]) == 3 and a["candidates"] == []


def test_real_linkwatch_findings_match_the_known_evidence():
    scan = {"dependencies": DEMO["dependencies"]["items"]}
    fs = {f["package"]: f for f in findings.build(scan, DEMO["risk_evidence"]["indicators"], baseline_status="failed")}
    assert fs["jinja2"]["severity"] == "high" and "observed_in_baseline_output" in fs["jinja2"]["severity_basis"]
    assert fs["pydantic"]["severity"] == "medium" and any(e["rule"] == "conflicting_constraints" for e in fs["pydantic"]["evidence"])
    assert fs["certifi"]["severity"] == "medium" and fs["certifi"]["detected_version"]["pinned"] is None
    assert fs["requests"]["severity"] == "medium" and "private" in fs["requests"]["why_it_matters"][0].lower()
    for quiet in ("six", "idna", "nose"):   # declared, no static import found: uncertain, low severity, heuristic, never 'unused'
        assert fs[quiet]["severity"] == "low" and fs[quiet]["evidence"][0]["kind"] == "heuristic" and "NOT proof" in " ".join(fs[quiet]["why_it_matters"])
    assert all(f["verification_requirements"] and f["unknown"] for f in fs.values())   # nothing is presented as fully known


def test_findings_attach_impact_data_when_available(tmp_path):
    (tmp_path / "requirements.txt").write_text("six\n")
    (tmp_path / "app.py").write_text("import six\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_app.py").write_text("import app\n")
    sc = scanner.scan(tmp_path)
    use = usage.map_usage(tmp_path, {"six"})
    ind_ = indicators.build(sc, use, None)["indicators"] + [ind("six", "private_api_import")]
    f = findings.build(sc, ind_, impact=impact.build(tmp_path, {"six"}))[0]
    assert f["usage"][0]["file"] == "app.py" and f["impact"]["tests_indirect"][0]["file"] == "tests/test_app.py" and f["impact"]["affected_files"]
