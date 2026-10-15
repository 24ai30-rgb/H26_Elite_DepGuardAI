"""Pre-publish checks: secret detection/redaction, repository hygiene, change review. Fake credentials are assembled at runtime."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from depguard.prepublish import hygiene, review, secrets

AWS = "AKIA" + "ABCDEFGHIJKLMNOP"                       # 20 chars, AWS format
GH = "ghp_" + "Zx9Qw3Er5Ty7Ui1Op4As6Df8Gh2Jk0Lz3Xc5V"    # 40 chars, GitHub classic token format
PEM = "-----BEGIN " + "RSA PRIVATE KEY-----"
PW = "Tr0ub4dor" + "&3xyz"                              # heuristic-tier fixtures, also assembled so no scanner flags this file
KEY = "abcd1234" + "efgh5678"


# ---------------------------------------------------------------- secrets
def test_known_formats_are_high_confidence_and_values_are_never_returned():
    out = secrets.scan_text(f"aws = '{AWS}'\ntoken: {GH}\n{PEM}\n", "cfg.py")
    assert {(s["type"], s["confidence"], s["line"]) for s in out} == {("aws_access_key_id", "high_format", 1), ("github_token", "high_format", 2), ("private_key_block", "high_format", 3)}
    dumped = json.dumps(out)
    assert AWS not in dumped and GH not in dumped and "Zx9Qw3Er5Ty" not in dumped            # nothing recoverable from the report
    assert all(len(s["fingerprint"]) == 8 and s["length"] > 0 for s in out)
    assert secrets.scan_text(f"a={AWS}", "x")[0]["fingerprint"] == secrets.scan_text(f"b = {AWS}", "y")[0]["fingerprint"]   # stable for de-duplication


def test_heuristic_matches_are_only_suspected_and_placeholders_are_ignored():
    out = secrets.scan_text(f'password = "{PW}"\napi_key: "{KEY}"\n', "app.py")
    assert [s["confidence"] for s in out] == ["suspected", "suspected"]
    ignored = secrets.scan_text('password = "your_password_here"\napi_key = "<API_KEY>"\nsecret = "changeme123"\ntoken = "xxxxxxxxxxxx"\n', "x")
    assert ignored == []
    assert secrets.scan_text("password = 'short'", "x") == []                                 # too short to be meaningful


def test_scan_files_skips_binary_oversized_and_vendor_dirs(tmp_path):
    (tmp_path / "a.py").write_text(f"k = '{AWS}'\n")
    (tmp_path / "bin.dat").write_bytes(b"\x00\x01" + AWS.encode())
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "x.js").write_text(f"{AWS}")
    (tmp_path / "big.txt").write_text(AWS + "\n" + "x" * 1_100_000)
    found = secrets.scan_files(tmp_path)
    assert [(s["file"], s["line"]) for s in found] == [("a.py", 1)]
    assert secrets.scan_files(tmp_path, ["a.py", "missing.py"])[0]["file"] == "a.py"


def test_redact_masks_every_format_for_logs():
    text = f"token {GH} and key {AWS} and password = \"Tr0ub4dor&3xyz\" {PEM}"
    r = secrets.redact(text)
    assert GH not in r and AWS not in r and "Tr0ub4dor" not in r and PEM not in r
    assert "[REDACTED:github_token]" in r and "password = \"[REDACTED:credential_assignment]" in r.replace("'", '"')


# ---------------------------------------------------------------- hygiene
def test_hygiene_flags_env_keys_caches_big_files_without_touching_anything(tmp_path):
    for f in (".env", ".env.example", "config/.env.production", "server.pem", "app/__pycache__/x.pyc", ".venv/lib/x.py", "node_modules/a/b.js", "run.log", "src/main.py"):
        (tmp_path / f).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / f).write_text("SECRET_TOKEN=do-not-show\n")
    (tmp_path / "big.bin").write_bytes(b"0" * (hygiene.BIG_BYTES + 1))
    snapshot = {p.relative_to(tmp_path).as_posix(): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    out = {c["id"]: c for c in hygiene.check(tmp_path)}
    assert set(out["env_file_tracked"]["files"]) == {".env", "config/.env.production"}          # .env.example is allowed
    assert out["sensitive_file_tracked"]["files"] == ["server.pem"] and out["oversized_files"]["files"] == ["big.bin"] and "no_gitignore" in out
    assert "do-not-show" not in json.dumps(out)                                                  # contents never shown
    assert {p.relative_to(tmp_path).as_posix(): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == snapshot   # nothing deleted or changed


def test_recommendations_are_manual_steps_not_actions(tmp_path):
    (tmp_path / ".env").write_text("x")
    rec = hygiene.check(tmp_path)[0]["recommendation"]
    assert "git rm --cached" in rec and "History is not rewritten by DepGuard" in rec


@pytest.mark.skipif(not shutil.which("git"), reason="git not installed")
def test_hygiene_uses_git_ls_files_so_ignored_files_do_not_count(tmp_path):
    def git(*a):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=tmp_path, check=True, capture_output=True)
    git("init", "-q")
    (tmp_path / ".gitignore").write_text("__pycache__/\n.env\n")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "x.pyc").write_text("x")
    (tmp_path / ".env").write_text("A=1")
    (tmp_path / "app.py").write_text("x=1\n")
    git("add", "-A")
    assert hygiene.tracked_files(tmp_path)[1] == "git ls-files"
    assert hygiene.check(tmp_path) == []                                                           # ignored files are not tracked: clean
    git("add", "-f", ".env")
    ids = [c["id"] for c in hygiene.check(tmp_path)]
    assert ids == ["env_file_tracked"]


# ---------------------------------------------------------------- change review
def patch(pkg="jinja2", status="verified", find="Jinja2==2.11.3", replace="Jinja2==3.1.6", path="requirements.txt", added=1, removed=1):
    return {"id": "p-1", "package": pkg, "verification": {"status": status}, "edits": [{"file": path, "find": find, "replace": replace}],
            "files": [{"path": path, "lines_added": added, "lines_removed": removed}]}


@pytest.fixture
def work(tmp_path):
    (tmp_path / "requirements.txt").write_text("Jinja2==3.1.6\n")
    (tmp_path / "app.py").write_text("import jinja2\n")
    (tmp_path / ".gitignore").write_text("__pycache__/\n")
    return tmp_path


def by_id(r):
    return {c["id"]: c for c in r["checks"]}


def test_clean_verified_change_passes_but_still_says_tests_do_not_prove_compatibility(work):
    r = review.review(work, [patch()])
    assert r["status"] in ("pass", "warn") and r["stop_recommended"] is False
    v = by_id(r)["verification:p-1"]
    assert v["status"] == "pass" and "do not prove full compatibility" in v["detail"]
    assert by_id(r)["secrets_in_changes"]["status"] == "pass"


def test_failed_verification_recommends_stopping_with_an_explicit_reason(work):
    r = review.review(work, [patch(status="failed")])
    assert r["stop_recommended"] and any("tests for the jinja2 patch failed" in s for s in r["stop_reasons"]) and by_id(r)["verification:p-1"]["status"] == "fail"


@pytest.mark.parametrize("status", ["blocked", "not_run", "partially_verified"])
def test_unverified_states_warn_but_do_not_block(work, status):
    r = review.review(work, [patch(status=status)])
    assert r["stop_recommended"] is False and by_id(r)["verification:p-1"]["status"] == "warn"
    assert "NOT verified" in by_id(r)["verification:p-1"]["detail"] or status == "partially_verified"


def test_high_confidence_credential_in_a_changed_file_recommends_stopping_without_leaking_it(work):
    (work / "requirements.txt").write_text(f"Jinja2==3.1.6\n# token {GH}\n")
    r = review.review(work, [patch()])
    assert r["stop_recommended"] and by_id(r)["secrets_in_changes"]["status"] == "fail" and GH not in json.dumps(r)


def test_low_confidence_or_unrelated_secrets_never_block(work):
    (work / "other.py").write_text(f"k = '{AWS}'\npassword = '{PW}'\n")
    (work / "requirements.txt").write_text('Jinja2==3.1.6\n')
    r = review.review(work, [patch()])
    assert r["stop_recommended"] is False and by_id(r)["secrets_elsewhere"]["status"] == "warn" and AWS not in json.dumps(r)
    (work / "other.py").unlink()
    (work / "requirements.txt").write_text(f'Jinja2==3.1.6\n# password = "{PW}"\n')
    r = review.review(work, [patch()])
    assert r["stop_recommended"] is False and by_id(r)["secrets_in_changes"]["status"] == "warn"      # suspected only: advisory


def test_declarations_docs_and_deletions_are_checked(work):
    (work / "app.py").write_text("import jinja2\nimport certifi\n")
    (work / "README.md").write_text("Install Jinja2==2.11.3 first.\n")
    r = review.review(work, [patch(added=1, removed=12)])
    c = by_id(r)
    assert c["declarations"]["status"] == "warn" and "certifi" in c["declarations"]["detail"]
    assert c["docs"]["status"] == "warn" and "README.md still mentions Jinja2==2.11.3" in c["docs"]["detail"]
    assert c["unintended_deletions"]["status"] == "warn" and r["stop_recommended"] is False


def test_preexisting_user_changes_are_reported_as_untouched(work):
    r = review.review(work, [patch()], original_info={"dirty": True, "modified_files": ["a.py"], "untracked_files": ["n.txt", "m.txt"]})
    d = by_id(r)["preexisting_changes"]["detail"]
    assert "1 modified and 2 untracked" in d and "did not touch them" in d


def test_review_never_deletes_or_modifies_anything(work):
    before = {p.name: p.read_bytes() for p in work.iterdir() if p.is_file()}
    review.review(work, [patch(status="failed")])
    assert {p.name: p.read_bytes() for p in work.iterdir() if p.is_file()} == before
