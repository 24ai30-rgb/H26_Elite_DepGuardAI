"""HTML/Markdown/JSON reports derive from one validated capsule; repo-controlled text must be escaped."""
import json

import pytest

from depguard import capsule, cli, report_html

EVIL = '<script>alert("x")</script>'


@pytest.fixture
def cap(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "requirements.txt").write_text("six\n")
    (repo / "a.py").write_text("import six\n")
    c = cli.analyze(repo, tmp_path / "out", "m", use_ai=False, run_tests=False, meta_cache=tmp_path / "none.json")
    return c, tmp_path


def test_write_produces_json_md_html_from_same_capsule(cap):
    c, tmp = cap
    out = tmp / "out"
    data = json.loads((out / "capsule.json").read_text())
    html = (out / "capsule.html").read_text(encoding="utf-8")
    assert data["health_score"]["score"] == c["health_score"]["score"]
    assert f"{c['health_score']['score']}/100" in html and f"{c['health_score']['score']}/100" in (out / "capsule.md").read_text(encoding="utf-8")
    assert html.startswith("<!doctype html>") and "<style>" in html and "http://" not in html.split("<body>")[0]  # self-contained


def test_html_labels_all_four_kinds_and_states_unverified_ai(cap):
    html = report_html.to_html(cap[0])
    for label in ("Observed fact", "AI-generated, unverified", "Verified by execution", "Unknown / not checked"):
        assert label in html
    assert "Maintenance activity and release recency: not checked" in html and "NOT proof of non-use" in html
    assert "not run" in html.lower()  # tests were not run -> reported as not run, never as passed


def test_untrusted_repo_content_is_escaped_everywhere(cap):
    c, _ = cap
    c["repository"]["path_name"] = EVIL
    c["dependencies"]["items"][0]["name"] = EVIL
    c["risk_evidence"]["indicators"].append({"package": EVIL, "indicator": EVIL, "severity": "warn", "evidence": [EVIL]})
    c["usage"]["usages"][EVIL] = [{"file": EVIL, "line": 1, "module": EVIL, "names": []}]
    c["migration_proposal"]["diff"] = "+" + EVIL + "\n-" + EVIL
    c["compatibility_gaps"].append(EVIL)
    c["ai_recommendations"] = {"status": "ok", "recommendations": [{
        "package": EVIL, "action": "keep", "replacement_candidates": [EVIL], "rationale": EVIL, "migration_steps": [EVIL],
        "compatibility_concerns": [EVIL], "manifest_edits": [], "suggested_tests": [EVIL]}]}
    html = report_html.to_html(c)
    assert "<script>" not in html and "&lt;script&gt;" in html


def test_health_deductions_and_unchecked_are_in_capsule_and_valid(cap):
    c, _ = cap
    capsule.validate(c)
    h = c["health_score"]
    assert h["kind"] == "heuristic" and h["unchecked"] and "baseline" in h["unchecked"][0]["check"] + h["unchecked"][1]["check"]
    assert "package_metadata" not in c  # never fetched -> no field, no invented data


def test_cached_metadata_is_included_with_its_source_and_time(tmp_path):
    from depguard import metadata
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "requirements.txt").write_text("six\n")
    (repo / "a.py").write_text("import six\n")
    cache = tmp_path / "c.json"
    cache.write_text(json.dumps({"six": {"name": "six", "status": "ok", "source": "https://pypi.org/pypi/six/json", "fetched_at": "2026-10-15T00:00:00+00:00",
                                         "latest_version": "1.17.0", "latest_upload_time": "2024-12-04T11:16:22Z", "requires_python": ">=2.7",
                                         "requires_dist": [], "inactive_classifier": False, "python_classifiers": []}}))
    c = cli.analyze(repo, tmp_path / "out", "m", use_ai=False, run_tests=False, meta_cache=cache)
    capsule.validate(c)
    assert c["package_metadata"]["six"]["source"].startswith("https://pypi.org/")
    six = next(p for p in c["health_score"]["packages"] if p["package"] == "six")
    assert six["metadata_checked"] is True
    assert "package_metadata" in json.dumps(c) and "https://pypi.org/pypi/six/json" in report_html.to_html(c)
    assert metadata.cached(["six"], cache)["six"]["latest_version"] == "1.17.0"


# ---------------------------------------------------------------- the capsule must not contradict its own data
def test_limitations_say_what_was_actually_checked(tmp_path):
    import io

    from depguard import metadata

    def fake(payload):
        return lambda url, timeout=0: io.BytesIO(json.dumps(payload).encode())

    pkg = {"info": {"version": "1.17.0", "requires_python": ">=2.7", "classifiers": ["Programming Language :: Python :: 3.12"]},
           "urls": [{"upload_time_iso_8601": "2024-12-04T11:12:13Z"}]}
    ver = {"info": {"version": "1.17.0", "requires_python": ">=2.7", "yanked": False, "classifiers": []}, "vulnerabilities": []}
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "requirements.txt").write_text("six==1.17.0\n")
    (repo / "a.py").write_text("import six\n")

    offline = cli.analyze(repo, tmp_path / "o1", "m", use_ai=False, run_tests=False, meta_cache=tmp_path / "none.json")
    assert offline["limitations"][0].startswith("Maintenance activity / release recency was not checked")      # nothing fetched: still true
    assert offline["limitations"][1] == capsule.STATIC_LIMITS[1] and "package_metadata" not in offline

    cache = tmp_path / "cache.json"                                          # the index was queried (through the real fetchers, no network)
    metadata.fetch(["six"], cache, opener=fake(pkg))
    metadata.fetch_versions([("six", "1.17.0")], cache, opener=fake(ver))
    fetched = cli.analyze(repo, tmp_path / "o2", "m", use_ai=False, run_tests=False, fetch_metadata=True, meta_cache=cache)
    text = " | ".join(fetched["limitations"])
    assert fetched["package_metadata"] and fetched["version_metadata"]
    assert "was not checked" not in text and "intentionally out of scope" not in text                       # no contradiction with the data above
    assert "latest upload date" in text and "never checked" in text and "not a vulnerability audit" in text
    assert fetched["limitations"][2:] == offline["limitations"][2:] and len(fetched["limitations"]) == len(offline["limitations"])
    capsule.validate(fetched)
    assert "was not checked" not in (tmp_path / "o2" / "capsule.md").read_text(encoding="utf-8").split("## 6. Limitations")[1]
    assert capsule.STATIC_LIMITS[0].startswith("Maintenance activity / release recency was not checked")   # the shared constant was not mutated
