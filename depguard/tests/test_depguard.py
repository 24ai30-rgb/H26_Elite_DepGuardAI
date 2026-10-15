"""Routine DepGuard tests. AI is mocked; real inference is tested separately (test_live_gemma.py)."""
import copy
import json

import jsonschema
import pytest

from depguard import advisor, capsule, cli, indicators, migrate, scanner, usage, verify


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "requirements.txt").write_text("pydantic==1.10.13\nrequests>=2  # c\nbad line!!\n-r other.txt\n")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="x"\nrequires-python=">=3.10"\ndependencies=["pydantic>=2","six"]\n')
    (tmp_path / "app.py").write_text(
        "import requests\nfrom dateutil import parser\nimport certifi\nfrom requests.utils import _private\n"
        "import importlib\nm = importlib.import_module('x')\nimport os\n")
    return tmp_path


# ---- scanner
def test_scanner_parses_and_reports_issues(repo):
    s = scanner.scan(repo)
    names = {d["name"] for d in s["dependencies"]}
    assert {"pydantic", "requests", "six"} <= names
    assert next(d for d in s["dependencies"] if d["name"] == "pydantic" and d["file"] == "requirements.txt")["pinned_version"] == "1.10.13"
    assert s["python_requires"] == ">=3.10"
    assert any("unparseable" in i["issue"] for i in s["issues"])
    assert any("not followed" in i["issue"] for i in s["issues"])
    assert s["conflicts"] and s["conflicts"][0]["package"] == "pydantic"


def test_scanner_missing_and_malformed(tmp_path):
    assert "no supported manifest" in scanner.scan(tmp_path)["issues"][0]["issue"]
    (tmp_path / "pyproject.toml").write_text("[project\n")
    assert "malformed" in scanner.scan(tmp_path)["issues"][0]["issue"]


# ---- usage
def test_usage_lines_aliases_dynamic(repo):
    u = usage.map_usage(repo, {"requests", "python-dateutil", "six"})
    assert u["usages"]["requests"][0]["line"] == 1
    assert u["usages"]["python-dateutil"][0]["file"] == "app.py"
    assert u["undeclared_imports"] == ["certifi"]
    assert u["no_import_found"] == ["six"]  # reported as "no import found", never "unused"
    assert u["dynamic_import_sites"] == [{"file": "app.py", "line": 6}]
    assert u["private_api_imports"][0]["name"] == "requests.utils._private"
    assert "os" not in u["usages"]


# ---- indicators
def test_indicators_unknown_not_invented(repo):
    s = scanner.scan(repo)
    u = usage.map_usage(repo, {d["name"] for d in s["dependencies"]})
    ind = indicators.build(s, u, None)
    maint = [i for i in ind["indicators"] if i["indicator"] == "maintenance_activity"]
    assert maint and all("not checked" in i["evidence"][0] for i in maint)
    assert ind["package_levels"]["pydantic"]["level"] == "medium"
    assert ind["package_levels"]["six"]["level"] == "no_issue_observed"


def test_failed_baseline_output_raises_level(repo):
    s = scanner.scan(repo)
    u = usage.map_usage(repo, set())
    v = {"baseline": {"status": "failed", "output": "ImportError: cannot import name 'x' from 'pydantic'"}}
    assert indicators.build(s, u, v)["package_levels"]["pydantic"]["level"] == "high"


def test_error_line_attributed_to_preceding_frame_windows_and_posix():
    for sep in ("\\", "/"):
        out = "\n".join([f"venv{sep}Lib{sep}site-packages{sep}jinja2{sep}x.py:3: in <module>",
                         "E   ImportError: cannot import name 'soft_unicode'"])
        assert "soft_unicode" in indicators.attribute_output(out, {"jinja2"})["jinja2"][0]


def test_verify_scrubs_local_paths(monkeypatch, tmp_path):
    import sys
    monkeypatch.setattr(verify, "_verify", lambda *a, **k: {"workdir": str(tmp_path), "out": f"x {tmp_path}/f {sys.executable}"})
    r = verify.verify(tmp_path, None, "l")
    assert str(tmp_path) not in json.dumps(r) and "<tmp>" in r["out"] and "<python>" in r["out"]


# ---- advisor: invalid responses
GOOD = {"recommendations": [{"package": "six", "action": "remove", "replacement_candidates": [], "rationale": "r",
                              "migration_steps": ["s"], "compatibility_concerns": [], "manifest_edits": [],
                              "suggested_tests": ["t"]}]}


def test_parse_response_valid_and_invalid():
    assert advisor.parse_response(json.dumps(GOOD)) == GOOD
    bad = copy.deepcopy(GOOD)
    bad["recommendations"][0]["action"] = "yolo"
    with pytest.raises(jsonschema.ValidationError):
        advisor.parse_response(json.dumps(bad))
    with pytest.raises(ValueError):
        advisor.parse_response("not json")


def _fake_post(content):
    def post(path, body=None, timeout=0):
        return {"details": {}, "license": "Apache"} if path == "/api/show" else {"message": {"content": content}}
    return post


def _patch_runtime(monkeypatch, content):
    monkeypatch.setattr(advisor, "_post", _fake_post(content))
    monkeypatch.setattr(advisor.urllib.request, "urlopen",
                        lambda *a, **k: __import__("io").BytesIO(b'{"version":"0.0-test"}'))


def test_advise_invalid_then_ok(monkeypatch):
    _patch_runtime(monkeypatch, "{}")
    assert advisor.advise("m", "p")["status"] == "invalid_response"
    _patch_runtime(monkeypatch, json.dumps(GOOD))
    assert advisor.advise("m", "p")["status"] == "ok"


def test_advise_blocked_when_runtime_missing(monkeypatch):
    def boom(*a, **k):
        raise advisor.urllib.error.URLError("down")
    monkeypatch.setattr(advisor.urllib.request, "urlopen", boom)
    r = advisor.advise("m", "p")
    assert r["status"] == "blocked" and "recommendations" not in r  # no pretend fallback


# ---- safe patch paths
def test_edits_validated(repo):
    ok, bad = migrate.validate_edits(repo, [
        {"file": "requirements.txt", "find": "pydantic==1.10.13", "replace": "pydantic>=2"},
        {"file": "../evil.txt", "find": "a", "replace": "b"},
        {"file": "requirements.txt", "find": "not-present", "replace": "x"},
        {"file": "app.py", "find": "import", "replace": "x"},  # ambiguous
        {"file": "nope.py", "find": "a", "replace": "b"},
        {"file": "app.py"}])
    assert len(ok) == 1 and len(bad) == 5
    assert "escapes" in bad[0]["reason"]


def test_edit_breaking_manifest_syntax_rejected(repo):
    ok, bad = migrate.validate_edits(repo, [
        {"file": "pyproject.toml", "find": '"pydantic>=2",', "replace": '"pydantic>=2"'},  # drops comma
        {"file": "requirements.txt", "find": "pydantic==1.10.13", "replace": "pydantic>=!!"}])
    assert not ok and "invalid TOML" in bad[0]["reason"] and "unparseable" in bad[1]["reason"]


def test_preview_is_readonly_and_apply_writes_only_workdir(repo):
    e = [{"file": "requirements.txt", "find": "pydantic==1.10.13", "replace": "pydantic>=2"}]
    before = (repo / "requirements.txt").read_text()
    assert "+pydantic>=2" in migrate.preview(repo, e)
    assert (repo / "requirements.txt").read_text() == before
    migrate.apply_edits(repo, e)
    assert "pydantic>=2" in (repo / "requirements.txt").read_text()
    with pytest.raises(ValueError):
        migrate.apply_edits(repo, [{"file": "../x", "find": "a", "replace": "b"}])


def test_apply_requires_approval(repo, tmp_path, monkeypatch, capsys):
    cap = {"migration_proposal": {"accepted_edits": [
        {"file": "requirements.txt", "find": "pydantic==1.10.13", "replace": "pydantic>=2"}]}}
    cp = tmp_path / "c.json"
    cp.write_text(json.dumps(cap))
    monkeypatch.setattr("builtins.input", lambda *_: "no")
    assert cli.apply(cp, repo, False) == 1
    assert "pydantic==1.10.13" in (repo / "requirements.txt").read_text()
    monkeypatch.setattr("builtins.input", lambda *_: "yes")
    assert cli.apply(cp, repo, False) == 0
    assert "pydantic>=2" in (repo / "requirements.txt").read_text()


# ---- verification states
def test_compare_and_states():
    base = {"status": "failed", "tests": {"a": "passed", "b": "failed", "c": "passed"}}
    new = {"status": "failed", "tests": {"a": "passed", "b": "passed", "c": "failed"}}
    c = verify.compare(base, new)
    assert c["fixed"] == ["b"] and c["regressed"] == ["c"]
    assert c["baseline_counts"] == {"passed": 2, "failed": 1, "skipped": 0}


def test_run_records_exit_code_and_timeout():
    import sys
    r = verify.run([sys.executable, "-c", "import sys;print('hi');sys.exit(3)"], ".", 30)
    assert r["exit_code"] == 3 and "hi" in r["output"]
    assert verify.run([sys.executable, "-c", "import time;time.sleep(5)"], ".", 1)["exit_code"] == -1


def test_junit_parse(tmp_path):
    p = tmp_path / "j.xml"
    p.write_text('<testsuite><testcase classname="t" name="ok"/><testcase classname="t" name="bad"><failure/></testcase>'
                 '<testcase classname="t" name="sk"><skipped/></testcase></testsuite>')
    assert verify._junit(p) == {"t::ok": "passed", "t::bad": "failed", "t::sk": "skipped"}
    assert verify._junit(tmp_path / "missing.xml") == {}


# ---- capsule
def test_capsule_end_to_end_without_ai_or_tests(repo, tmp_path):
    cap = cli.analyze(repo, tmp_path / "out", "m", use_ai=False, run_tests=False)
    capsule.validate(cap)
    assert (tmp_path / "out" / "capsule.json").exists() and "AI recommendations" in (tmp_path / "out" / "capsule.md").read_text(encoding="utf-8")
    assert cap["executed_tests"]["baseline"]["status"] == "not_run"
    assert cap["ai_recommendations"]["status"] == "not_requested"


def test_capsule_schema_rejects_bad_and_secrets(repo, tmp_path):
    cap = cli.analyze(repo, tmp_path / "out", "m", use_ai=False, run_tests=False)
    bad = copy.deepcopy(cap)
    bad["executed_tests"]["baseline"]["status"] = "maybe"
    with pytest.raises(jsonschema.ValidationError):
        capsule.validate(bad)
    del cap["limitations"]
    with pytest.raises(jsonschema.ValidationError):
        capsule.validate(cap)
    leaky = cli.analyze(repo, tmp_path / "o2", "m", use_ai=False, run_tests=False)
    leaky["limitations"].append("token ghp_" + "a" * 30)
    with pytest.raises(ValueError):
        capsule.validate(leaky)


def test_remote_credentials_stripped(tmp_path, monkeypatch):
    monkeypatch.setattr(capsule, "_git", lambda r, *a: "https://user:tok@github.com/x/y.git" if "get-url" in a else "")
    assert capsule.identity(tmp_path)["remote"] == "https://github.com/x/y.git"
