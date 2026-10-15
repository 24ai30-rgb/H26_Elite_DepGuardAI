"""Metadata cache: never network unless asked, never fabricate, record source + time."""
import io
import json
import urllib.error

from depguard import metadata

PYPI = {"info": {"version": "3.1.6", "requires_python": ">=3.7", "requires_dist": ["MarkupSafe>=2.0"],
                 "classifiers": ["Programming Language :: Python :: 3.12", "Programming Language :: Python :: 3 :: Only",
                                 "Development Status :: 5 - Production/Stable"]},
        "urls": [{"upload_time_iso_8601": "2025-03-05T18:10:41Z"}, {"upload_time_iso_8601": "2025-03-05T18:10:42Z"}]}


def opener(payload=PYPI, calls=None):
    def f(url, timeout=0):
        if calls is not None:
            calls.append(url)
        return io.BytesIO(json.dumps(payload).encode())
    return f


def test_fetch_records_source_time_and_cache(tmp_path):
    calls = []
    out = metadata.fetch(["Jinja2"], tmp_path / "c.json", opener=opener(calls=calls))
    e = out["jinja2"]
    assert e["status"] == "ok" and e["source"] == "https://pypi.org/pypi/jinja2/json" and e["fetched_at"]
    assert e["latest_version"] == "3.1.6" and e["latest_upload_time"].endswith("42Z") and e["python_classifiers"] == ["3.12"]
    assert e["inactive_classifier"] is False and e["requires_dist"] == ["MarkupSafe>=2.0"]
    metadata.fetch(["jinja2"], tmp_path / "c.json", opener=opener(calls=calls))  # served from cache
    assert len(calls) == 1


def test_offline_never_touches_network(tmp_path):
    def boom(*a, **k):
        raise AssertionError("network used in offline mode")
    out = metadata.fetch(["x"], tmp_path / "c.json", offline=True, opener=boom)
    assert out["x"]["status"] == "unavailable" and "offline" in out["x"]["error"]
    assert not (tmp_path / "c.json").exists()


def test_failure_is_recorded_not_fabricated_and_keeps_old_good_data(tmp_path):
    def down(url, timeout=0):
        raise urllib.error.URLError("no route")
    e = metadata.fetch(["a"], tmp_path / "c.json", opener=down)["a"]
    assert e["status"] == "unavailable" and "network unavailable" in e["error"] and "latest_version" not in e
    metadata.fetch(["a"], tmp_path / "c.json", opener=opener())  # now a real fetch
    again = metadata.fetch(["a"], tmp_path / "c.json", refresh=True, opener=down)["a"]
    assert again["status"] == "ok" and "refresh failed" in again["note"]


def test_http_error_and_bad_json(tmp_path):
    def nf(url, timeout=0):
        raise urllib.error.HTTPError(url, 404, "nf", {}, None)
    assert "HTTP 404" in metadata.fetch(["zzz"], tmp_path / "c.json", opener=nf)["zzz"]["error"]
    assert "unexpected response" in metadata.fetch(["q"], tmp_path / "d.json", opener=opener({"nope": 1}))["q"]["error"]


def test_invalid_names_rejected_before_any_url_is_built(tmp_path):
    for bad in ("../etc/passwd", "a/b", "a b", "", "x?y=1"):
        e = metadata.fetch([bad], tmp_path / "c.json", opener=lambda *a, **k: (_ for _ in ()).throw(AssertionError()))
        assert all(v["status"] == "unavailable" for v in e.values())


def test_corrupted_cache_is_moved_aside(tmp_path):
    p = tmp_path / "c.json"
    p.write_text("{not json")
    assert metadata.load_cache(p) == {}
    assert (tmp_path / "c.json.corrupt").exists()


# ---------------------------------------------------------------- exact-version evidence
VER = {"info": {"version": "2.11.3", "requires_python": ">=2.7", "yanked": False, "license": "BSD-3-Clause",
                "classifiers": ["License :: OSI Approved :: BSD License", "Operating System :: OS Independent"]},
       "urls": [{"upload_time_iso_8601": "2021-01-31T16:33:09Z"}],
       "vulnerabilities": [{"id": "PYSEC-2024-1", "aliases": ["CVE-2024-0000"], "summary": "Sandbox escape", "fixed_in": ["3.1.4"], "link": "https://osv.dev/PYSEC-2024-1"}]}


def test_version_evidence_records_advisories_with_source_and_date(tmp_path):
    e = metadata.fetch_versions([("Jinja2", "2.11.3")], tmp_path / "c.json", opener=opener(VER))["jinja2==2.11.3"]
    assert e["status"] == "ok" and e["source"] == "https://pypi.org/pypi/jinja2/2.11.3/json"
    assert e["fetched_at"] and e["vulnerabilities"][0]["id"] == "PYSEC-2024-1" and e["vulnerabilities"][0]["fixed_in"] == ["3.1.4"]
    assert e["license"] == "BSD-3-Clause" and e["license_classifiers"] == ["BSD License"] and e["os_classifiers"] == ["OS Independent"]
    assert "not an independent audit" in e["vulnerability_scope"]


def test_no_advisories_means_none_listed_not_secure(tmp_path):
    clean = {**VER, "vulnerabilities": []}
    e = metadata.fetch_versions([("six", "1.17.0")], tmp_path / "c.json", opener=opener(clean))["six==1.17.0"]
    assert e["vulnerabilities"] == [] and "not an independent audit" in e["vulnerability_scope"]  # wording must never claim safety


def test_version_fetch_offline_invalid_and_failure(tmp_path):
    def boom(*a, **k):
        raise AssertionError("network used")
    off = metadata.fetch_versions([("a", "1.0")], tmp_path / "c.json", offline=True, opener=boom)["a==1.0"]
    assert off["status"] == "unavailable" and "offline" in off["error"]
    bad = metadata.fetch_versions([("a", "not a version!"), ("../x", "1.0")], tmp_path / "c.json", opener=boom)
    assert all(v["status"] == "unavailable" and v["error"] == "invalid package name or version" for v in bad.values())

    def down(url, timeout=0):
        raise urllib.error.URLError("no route")
    e = metadata.fetch_versions([("a", "1.0")], tmp_path / "d.json", opener=down)["a==1.0"]
    assert e["status"] == "unavailable" and "vulnerabilities" not in e  # never fabricated when the check could not run


def test_version_cache_is_reused_and_cached_only_view(tmp_path):
    calls = []
    metadata.fetch_versions([("jinja2", "2.11.3")], tmp_path / "c.json", opener=opener(VER, calls))
    metadata.fetch_versions([("jinja2", "2.11.3")], tmp_path / "c.json", opener=opener(VER, calls))
    assert len(calls) == 1
    assert list(metadata.cached_versions([("jinja2", "2.11.3"), ("jinja2", "3.0")], tmp_path / "c.json")) == ["jinja2==2.11.3"]


def test_entries_cached_before_license_capture_are_refetched_online_but_kept_offline(tmp_path):
    cache, calls = tmp_path / "c.json", []
    old = {"name": "jinja2", "status": "ok", "source": "https://pypi.org/pypi/jinja2/json", "fetched_at": "earlier", "latest_version": "3.0.0"}   # no "license" key at all

    def boom(*a, **k):
        raise AssertionError("network used in offline mode")

    def down(url, timeout=0):
        raise urllib.error.URLError("no route")

    cache.write_text(json.dumps({"jinja2": old}))
    assert metadata.fetch(["jinja2"], cache, offline=True, opener=boom)["jinja2"] == old        # offline: the cache is the only source, shown as it is
    e = metadata.fetch(["jinja2"], cache, opener=opener(calls=calls))["jinja2"]
    assert len(calls) == 1 and "license" in e and e["latest_version"] == "3.1.6"               # online: refetched, so the license is real data
    metadata.fetch(["jinja2"], cache, opener=opener(calls=calls))
    assert len(calls) == 1                                                                      # afterwards it is served from the cache again
    cache.write_text(json.dumps({"jinja2": old}))
    kept = metadata.fetch(["jinja2"], cache, opener=down)["jinja2"]
    assert kept["status"] == "ok" and "refresh failed" in kept["note"] and kept["latest_version"] == "3.0.0"   # refetch fails: earlier real data kept and flagged
