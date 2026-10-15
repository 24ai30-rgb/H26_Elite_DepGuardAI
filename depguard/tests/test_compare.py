from depguard import compare

USES = [{"file": "linkwatch/report.py", "line": 3, "module": "jinja2", "names": ["Environment", "FileSystemLoader"]}]
DEPS = [{"name": "jinja2", "specifier": "==2.11.3", "pinned_version": "2.11.3"},
        {"name": "markupsafe", "specifier": "==1.0", "pinned_version": "1.0"}]
META = {"jinja2": {"status": "ok", "source": "https://pypi.org/pypi/jinja2/json", "fetched_at": "T", "latest_version": "3.1.6",
                   "latest_upload_time": "2025-03-05", "requires_python": ">=3.7", "python_classifiers": ["3.12"],
                   "requires_dist": ["MarkupSafe>=2.0", "Babel>=2.7; extra == 'i18n'"]}}
EXP = [{"name": "jinja2-only", "edits": [{"replace": "Jinja2==3.1.6"}], "proposed": {"status": "passed"},
        "comparison": {"baseline_counts": {"passed": 9, "failed": 1}, "proposed_counts": {"passed": 10, "failed": 0}}}]


def cell(t, crit, col):
    return next(c for c in t["criteria"] if c["key"] == crit)["cells"][col]


def test_candidate_names_parse_and_reject_garbage():
    names, bad = compare.candidate_names(["Jinja2>=3", "mako", "manifest_edits ::", "find\": \"x\"", "", "mako"])
    assert names == ["jinja2", "mako"] and len(bad) == 3


def test_without_metadata_everything_is_unknown_not_invented():
    t = compare.compare("jinja2", ["mako"], USES, DEPS, {})
    for crit in ("metadata", "python", "constraints"):
        assert cell(t, crit, "mako")["basis"] == "unknown" and cell(t, crit, "mako")["value"] in ("unknown", "not fetched")
    assert cell(t, "api", "mako")["value"] == "equivalence NOT established"
    assert any("mako" in u for u in t["unresolved"]) and "does not claim" in t["statement"]


def test_metadata_python_constraints_and_pin_conflict_detected():
    t = compare.compare("jinja2", ["jinja2"], USES, DEPS, META, EXP)
    assert t["columns"] == ["jinja2"]  # same package is not duplicated as its own replacement
    assert "3.12 allowed" in cell(t, "python", "jinja2")["value"] and cell(t, "python", "jinja2")["basis"] == "metadata"
    lines = cell(t, "constraints", "jinja2")["value"]
    assert any(l.startswith("CONFLICT") and "markupsafe==1.0" in l for l in lines)  # pin 1.0 violates >=2.0
    assert not any("Babel" in l for l in lines)  # extras skipped
    assert cell(t, "metadata", "jinja2")["note"].startswith("source https://pypi.org")


def test_effort_counts_observed_bucket_labelled_heuristic_and_tests_from_experiments():
    t = compare.compare("jinja2", ["jinja3x"], USES, DEPS, META, EXP)
    e = cell(t, "effort", "jinja3x")
    assert e["basis"] == "heuristic" and "1 import site(s) in 1 file(s)" in e["value"] and "small" in e["value"]
    assert cell(t, "tests", "jinja3x")["value"] == "not run"
    t2 = compare.compare("jinja2", ["jinja2"], USES, DEPS, META, EXP)
    assert "9" in str(cell(t2, "tests", "jinja2")["value"]) and "10" in str(cell(t2, "tests", "jinja2")["value"])
    assert "do not prove" in cell(t2, "tests", "jinja2")["note"]


def test_availability_reports_real_local_state():
    t = compare.compare("pytest", ["definitely-not-a-real-pkg-xyz"], [], [], {})
    assert cell(t, "availability", "definitely-not-a-real-pkg-xyz")["value"] == "not installed locally"
    assert cell(t, "availability", "pytest")["basis"] == "observed"


# ---------------------------------------------------------------- license row and repair-patch test results
def test_license_comes_only_from_declared_metadata_and_is_not_legal_advice():
    meta = {"jinja2": {**META["jinja2"], "license": "BSD-3-Clause", "license_classifiers": ["BSD License"]}, "x": {**META["jinja2"], "license": None, "license_classifiers": []}}
    t = compare.compare("jinja2", ["x", "unfetched"], USES, DEPS, meta)
    j, x, u = (cell(t, "license", c) for c in ("jinja2", "x", "unfetched"))
    assert j["value"] == "BSD-3-Clause (classifiers: BSD License)" and j["basis"] == "metadata" and "not legal advice" in j["note"] and "redistribution terms are not verified" in j["note"]
    assert x["value"] == "not declared in the package metadata" and x["basis"] == "metadata"
    only_cls = compare.compare("jinja2", ["c"], USES, DEPS, {"c": {**META["jinja2"], "license": None, "license_classifiers": ["BSD License"]}})
    assert cell(only_cls, "license", "c")["value"] == "classifier only: BSD License (the package gives no license text or expression)"
    only_txt = compare.compare("jinja2", ["t"], USES, DEPS, {"t": {**META["jinja2"], "license": "MIT", "license_classifiers": []}})
    assert cell(only_txt, "license", "t")["value"] == "MIT"
    assert u["value"] == "unknown" and u["basis"] == "unknown"                                    # missing metadata stays unknown, never "fine"
    legacy = {k: v for k, v in META["jinja2"].items()}                                          # an old cache entry has no license keys at all
    assert cell(compare.compare("jinja2", ["old"], USES, DEPS, {"old": legacy}), "license", "old")["value"] == "unknown"


def test_repair_patches_supply_the_original_versus_replacement_test_evidence():
    patch = {"id": "p-1", "package": "jinja2", "origin": "ai", "status": "applied",
             "verification": {"status": "verified", "summary": "10 passed / 0 failed", "exit_code": 0, "reasons": ["all tests passed"]}}
    t = compare.compare("jinja2", ["mako"], USES, DEPS, META, EXP, [patch])
    cur = cell(t, "tests", "jinja2")
    assert cur["basis"] == "observed" and "patch p-1 (ai): verified: 10 passed / 0 failed, pytest exit 0" in cur["value"][0] and "do not prove full compatibility" in cur["note"]
    assert cell(t, "tests", "mako")["value"] == "not run"                                         # a replacement nobody tested is not run, whatever the current package did
    for status in ("rejected", "stale"):
        t2 = compare.compare("jinja2", [], USES, DEPS, META, [], [{**patch, "status": status}])
        assert cell(t2, "tests", "jinja2")["value"] == "not run"                                   # superseded patches are not evidence
    t3 = compare.compare("jinja2", [], USES, DEPS, META, [], [{**patch, "verification": None}])
    assert cell(t3, "tests", "jinja2")["value"] == "not run"                                       # an untested patch is not test evidence
