"""Advisory-coverage check, junk-candidate filtering, git-aware hygiene wording. Uses the real Jinja2 2.11.3 advisory fix versions seen in the live run."""
import pytest

from depguard import compare, findings
from depguard.prepublish import hygiene, review
from depguard.repair import advisory, patch as P, planner as PL
from depguard.repair.state import RepairState

# Real data from the package index for jinja2==2.11.3 (id, fixed_in), as recorded by the live analysis.
JINJA = [{"id": "GHSA-h5c8-rqwp-cp95", "fixed_in": ["3.1.3"]}, {"id": "GHSA-h75v-3vvj-5mfj", "fixed_in": ["3.1.4"]},
         {"id": "GHSA-q2x7-8rv6-6q7h", "fixed_in": ["3.1.5"]}, {"id": "GHSA-cpwx-vrp4-4pq7", "fixed_in": ["3.1.6"]}]


def test_the_live_runs_lower_bound_still_permits_affected_releases():
    r = advisory.check("jinja2", '"Jinja2>=3.1.3",', JINJA)           # exactly what Gemma proposed
    assert r["status"] == "partial" and r["lowest_allowed"] == "3.1.3"
    assert r["still_permitted"] == ["GHSA-h75v-3vvj-5mfj", "GHSA-q2x7-8rv6-6q7h", "GHSA-cpwx-vrp4-4pq7"]   # only the first advisory is excluded by >=3.1.3
    assert "still permits 3.1.3" in r["detail"] and "fixed only in 3.1.6 or later" in r["detail"] and "not an audit" in r["note"]


@pytest.mark.parametrize("text,status,low", [
    ("Jinja2>=3.1.6", "covers_all", "3.1.6"), ("Jinja2==3.1.6", "covers_all", "3.1.6"), ("Jinja2~=3.1.6", "covers_all", "3.1.6"), ("Jinja2>=3.1.7", "covers_all", "3.1.7"),
    ("Jinja2>=3.1.5,<4", "partial", "3.1.5"), ("Jinja2>3.1.5", "partial", "3.1.5"), ("Jinja2<4", "unbounded", None), ("Jinja2", "unbounded", None),
    ('    "Jinja2>=3.1.6",', "covers_all", "3.1.6"), ("jinja2 >= 3.1.6 ; python_version >= '3.9'", "covers_all", "3.1.6"),
])
def test_bounds_are_compared_with_the_highest_listed_fix(text, status, low):
    r = advisory.check("jinja2", text, JINJA)
    assert r["status"] == status and r.get("lowest_allowed") == low


def test_missing_data_is_reported_as_such_never_as_covered():
    assert advisory.check("jinja2", "Jinja2>=3.1.6", [])["status"] == "no_advisories" and "not proof of safety" in advisory.check("jinja2", "Jinja2>=3.1.6", [])["detail"]
    assert advisory.check("jinja2", "six>=1", JINJA)["status"] == "not_a_requirement"
    no_fix = advisory.check("jinja2", "Jinja2>=3.1.6", [{"id": "X", "fixed_in": []}])
    assert no_fix["status"] == "partial" and "no fixed version listed" in no_fix["detail"]          # an advisory without a fix can never be called covered


# ---------------------------------------------------------------- integrated into patches
def finding_with(advisories):
    return {"id": findings.finding_id("jinja2"), "package": "jinja2", "title": "t", "severity": "critical", "advisories": advisories,
            "detected_version": {"declared": [], "pinned": "2.11.3", "resolved": None, "used_for_checks": "2.11.3"},
            "evidence": [{"rule": "confirmed_vulnerability", "kind": "metadata", "severity": "critical", "text": "x", "source": "s"}],
            "usage": [], "declared_in": ["requirements.txt:2"], "why_it_matters": [], "unknown": [], "impact": None, "verification_requirements": []}


def proposal(replace):
    return lambda model, ctx, fb=None: {"status": "ok", "model": {"model": "m"}, "data": {"package": "jinja2", "action": "upgrade", "summary": "s", "rationale": "r", "replacement_candidates": [],
            "assumptions": [], "compatibility_concerns": [], "suggested_tests": [], "confidence_limitations": "l",
            "edits": [{"file": "requirements.txt", "find": "Jinja2==2.11.3", "replace": replace, "reason": "x"}]}}


def test_every_proposed_patch_carries_the_advisory_check_and_the_capsule_view_keeps_it(tmp_path):
    from depguard import capsule
    (tmp_path / "requirements.txt").write_text("toml==0.10.2\nJinja2==2.11.3\n")
    state = RepairState()
    r = PL.propose(tmp_path, finding_with(JINJA), state, "m", proposal("Jinja2>=3.1.3"), mode="A")
    ac = r["patch"]["advisory_check"]
    assert ac[0]["file"] == "requirements.txt" and ac[0]["status"] == "partial" and len(ac[0]["still_permitted"]) == 3
    assert capsule._patch_view(state.patch(r["patch"]["id"]))["advisory_check"] == ac
    ok = PL.propose(tmp_path, finding_with(JINJA), RepairState(), "m", proposal("Jinja2>=3.1.6"), mode="A")
    assert ok["patch"]["advisory_check"][0]["status"] == "covers_all"


def test_prepublish_warns_when_the_constraint_still_permits_affected_releases(tmp_path):
    (tmp_path / "requirements.txt").write_text("Jinja2>=3.1.3\n")
    p = {"id": "p-1", "package": "jinja2", "verification": {"status": "verified"}, "edits": [{"file": "requirements.txt", "find": "Jinja2==2.11.3", "replace": "Jinja2>=3.1.3"}],
         "files": [{"path": "requirements.txt", "lines_added": 1, "lines_removed": 1}], "advisory_check": [{"file": "requirements.txt", **advisory.check("jinja2", "Jinja2>=3.1.3", JINJA)}]}
    r = review.review(tmp_path, [p])
    c = {k["id"]: k for k in r["checks"]}["advisory:p-1:requirements.txt"]
    assert c["status"] == "warn" and "still permits 3.1.3" in c["detail"] and len(c["still_permitted"]) == 3 and r["stop_recommended"] is False   # advisory, not a block


def test_findings_expose_the_listed_advisories_for_the_version_in_use():
    vm = {"jinja2==2.11.3": {"name": "jinja2", "version": "2.11.3", "status": "ok", "source": "s", "fetched_at": "T", "yanked": False, "requires_python": None,
                             "vulnerabilities": [{"id": "A", "fixed_in": ["3.1.3"], "withdrawn": None}, {"id": "W", "fixed_in": ["9"], "withdrawn": "2025-01-01"}]}}
    f = findings.build({"dependencies": [{"name": "jinja2", "specifier": "==2.11.3", "pinned_version": "2.11.3", "file": "r.txt", "line": 1, "resolved_version": None}]}, [], version_metadata=vm)[0]
    assert f["advisories"] == [{"id": "A", "fixed_in": ["3.1.3"]}]                  # withdrawn advisories are not counted
    assert findings.build({"dependencies": [{"name": "x", "specifier": "", "pinned_version": None, "file": "r", "line": 1}]}, [{"package": "x", "indicator": "private_api_import", "severity": "warn", "evidence": ["e"]}])[0]["advisories"] == []


# ---------------------------------------------------------------- junk candidates
def test_file_names_are_never_package_candidates():
    names, bad = compare.candidate_names(["requirements.txt", "pyproject.toml", "mako", "Jinja2>=3", "setup.py", "backports.zoneinfo", "notes.md"])
    assert names == ["mako", "jinja2", "backports-zoneinfo"] and bad == ["requirements.txt", "pyproject.toml", "setup.py", "notes.md"]


# ---------------------------------------------------------------- hygiene wording
def test_folder_listing_does_not_claim_files_are_tracked(tmp_path):
    (tmp_path / "run.log").write_text("x")
    c = {x["id"]: x for x in hygiene.check(tmp_path)}["generated_files_tracked"]
    assert "present in the folder" in c["title"] and "not a Git repository listing" in c["detail"] and c["confidence"] == "suspected"
    g = {x["id"]: x for x in hygiene.check(tmp_path, ["run.log"], "git ls-files")}["generated_files_tracked"]
    assert g["title"].endswith("is tracked") and g["confidence"] == "confirmed" and "tracked file(s)" in g["detail"]


def test_review_uses_the_originals_tracked_list_so_untracked_files_are_not_called_tracked(tmp_path):
    (tmp_path / "debug.log").write_text("x")                                           # present in the copy but untracked in the original
    p = {"id": "p-1", "package": "x", "verification": {"status": "verified"}, "edits": [], "files": []}
    ids = [c["id"] for c in review.review(tmp_path, [p], tracked=(["app.py"], "git ls-files"))["checks"]]
    assert "generated_files_tracked" not in ids
    ids = [c["id"] for c in review.review(tmp_path, [p], tracked=(["app.py", "debug.log"], "git ls-files"))["checks"]]
    assert "generated_files_tracked" in ids
