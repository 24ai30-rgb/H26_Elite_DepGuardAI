"""Patch validation, base-revision checks, safe apply, export. Real files in tmp dirs."""
import subprocess
import shutil

import pytest

from depguard.repair import patch as P

REQ = "toml==0.10.2\nJinja2==2.11.3\nsix\n"
PYP = '[project]\nname = "x"\ndependencies = [\n    "toml==0.10.2",\n    "Jinja2==2.11.3",\n    "six",\n]\n'
APP = "from jinja2 import Environment\n\n\ndef build():\n    return Environment()\n"


@pytest.fixture
def root(tmp_path):
    (tmp_path / "requirements.txt").write_text(REQ)
    (tmp_path / "pyproject.toml").write_text(PYP)
    (tmp_path / "app.py").write_text(APP)
    (tmp_path / "other.py").write_text("x = 1\n")
    return tmp_path


def E(file, find, replace, reason="r"):
    return {"file": file, "find": find, "replace": replace, "reason": reason}


GOOD = [E("requirements.txt", "Jinja2==2.11.3", "Jinja2==3.1.6"), E("pyproject.toml", '"Jinja2==2.11.3",', '"Jinja2==3.1.6",')]


def test_valid_patch_has_diff_per_file_base_hashes_and_stable_id(root):
    p = P.make_patch(root, "f-1", "jinja2", GOOD, "ai", summary="upgrade")
    assert [f["path"] for f in p["files"]] == ["requirements.txt", "pyproject.toml"] and all(f["origin"] == "ai" and f["verification_status"] == "not_run" for f in p["files"])
    assert "-Jinja2==2.11.3" in p["files"][0]["diff"] and "+Jinja2==3.1.6" in p["files"][0]["diff"] and p["status"] == "proposed" and p["verification"] is None
    assert set(p["base"]["file_hashes"]) == {"requirements.txt", "pyproject.toml"} and p["base"]["fingerprint"]
    assert P.make_patch(root, "f-1", "jinja2", GOOD, "ai")["id"] == p["id"]
    assert (root / "requirements.txt").read_text() == REQ   # building a patch never touches the files


@pytest.mark.parametrize("edit,why", [
    (E("../evil.txt", "a", "b"), "unrelated file"),
    (E("/etc/passwd", "a", "b"), "unrelated file"),
    (E("README.md", "a", "b"), "unrelated file"),
    (E("other.py", "x = 1", "x = 2"), "unrelated file"),                       # a source file the finding does not use
    (E("subdir/requirements.txt", "a", "b"), "unrelated file"),
    (E("requirements.txt", "NOPE", "x"), "exactly once"),
    (E("requirements.txt", "", "x"), "exactly once"),
    (E("requirements.txt", "toml==0.10.2", "toml==0.10.2"), "changes nothing"),
    (E("requirements.txt", "Jinja2==2.11.3", "Jinja2>=!!"), "unparseable requirement"),
    (E("pyproject.toml", '"Jinja2==2.11.3",', '"Jinja2==3.1.6"'), "invalid TOML"),
    (E("app.py", "return Environment()", "return Environment(", "r"), "break Python syntax"),
    (E("app.py", "return Environment()", "import os; os.system('x'); return Environment()"), "not a dependency migration"),
])
def test_unsafe_or_wrong_edits_are_rejected_with_a_reason(root, edit, why):
    ok, bad = P.validate_edits(root, [edit], allowed_sources={"app.py"})
    assert ok == [] and why in bad[0]["reason"]


def test_deleting_most_of_a_file_is_rejected(root):
    big = "\n".join(f"pkg{i}==1.0" for i in range(30)) + "\n"
    (root / "requirements.txt").write_text(big)
    find = "\n".join(f"pkg{i}==1.0" for i in range(1, 25))
    ok, bad = P.validate_edits(root, [E("requirements.txt", find, "")])
    assert ok == [] and "delete most of the file" in bad[0]["reason"]


def test_too_many_edits_and_missing_files_and_protected_folders(root):
    ok, bad = P.validate_edits(root, [E("requirements.txt", "six", "six")] * 13)
    assert ok == [] and "more than 12 edits" in bad[0]["reason"]
    assert "does not exist" in P.validate_edits(root, [E("requirements-dev.txt", "a", "b")])[1][0]["reason"]
    (root / ".git").mkdir()
    (root / ".git" / "requirements.txt").write_text("x")
    ok, bad = P.validate_edits(root, [E(".git/requirements.txt", "x", "y")])
    assert ok == []


def test_sequential_edits_to_one_file_are_checked_cumulatively(root):
    ok, bad = P.validate_edits(root, [E("requirements.txt", "Jinja2==2.11.3", "Jinja2==3.1.6"), E("requirements.txt", "Jinja2==3.1.6", "Jinja2==3.1.7")])
    assert len(ok) == 2 and not bad                                   # the second anchor exists only after the first edit
    ok, bad = P.validate_edits(root, [E("requirements.txt", "Jinja2==2.11.3", "X"), E("requirements.txt", "Jinja2==2.11.3", "Y")])
    assert len(ok) == 1 and "exactly once" in bad[0]["reason"]


def test_make_patch_with_nothing_valid_explains_why(root):
    with pytest.raises(P.PatchError, match="unrelated file"):
        P.make_patch(root, "f", "x", [E("README.md", "a", "b")], "ai")
    with pytest.raises(P.PatchError, match="no edits"):
        P.make_patch(root, "f", "x", [], "ai")


def test_stale_patch_requires_renewed_review_and_is_not_applied(root):
    p = P.make_patch(root, "f-1", "jinja2", GOOD, "ai")
    assert P.check_applicable(root, p)["ok"] is True
    (root / "requirements.txt").write_text(REQ + "extra\n")             # the user edited the file after the patch was proposed
    chk = P.check_applicable(root, p)
    assert chk["ok"] is False and chk["stale"] and chk["changed_files"] == ["requirements.txt"] and "Renewed review" in chk["reason"]
    with pytest.raises(P.PatchError, match="Renewed review"):
        P.apply_patch(root, p)
    assert (root / "requirements.txt").read_text() == REQ + "extra\n"   # nothing was overwritten


def test_apply_snapshots_preserves_other_files_and_matches_the_reviewed_diff(root):
    before_other = (root / "other.py").read_text()
    (root / "notes.txt").write_text("my untracked notes")
    p = P.make_patch(root, "f-1", "jinja2", GOOD, "ai")
    r = P.apply_patch(root, p)
    assert r["diff_matches_preview"] is True and r["patch_id"] == p["id"]
    assert "Jinja2==3.1.6" in (root / "requirements.txt").read_text() and (root / "other.py").read_text() == before_other
    assert (root / "notes.txt").read_text() == "my untracked notes"
    from depguard import migrate
    migrate.rollback(root, r["backup"])
    assert (root / "requirements.txt").read_text() == REQ


@pytest.mark.skipif(not shutil.which("git"), reason="git not installed")
def test_exported_patch_applies_with_git_apply_and_records_its_base(root, tmp_path):
    p = P.make_patch(root, "f-1", "jinja2", GOOD, "ai", base={"commit": "abc123"})
    text = P.export_unified(p)
    assert "base commit: abc123" in text and "verification: not_run" in text and text.count("diff --git") == 2
    target = tmp_path / "target"
    target.mkdir()
    (target / "requirements.txt").write_text(REQ)
    (target / "pyproject.toml").write_text(PYP)
    pf = tmp_path / "x.patch"
    pf.write_text(text, encoding="utf-8", newline="\n")
    chk = subprocess.run(["git", "apply", "--check", str(pf)], cwd=target, capture_output=True, text=True)
    assert chk.returncode == 0, chk.stderr
    subprocess.run(["git", "apply", str(pf)], cwd=target, check=True)
    assert "Jinja2==3.1.6" in (target / "requirements.txt").read_text()


def test_file_without_trailing_newline_gets_the_marker(tmp_path):
    (tmp_path / "requirements.txt").write_text("six==1.0")
    p = P.make_patch(tmp_path, "f", "six", [E("requirements.txt", "six==1.0", "six==1.1")], "deterministic")
    assert "\\ No newline at end of file" in p["files"][0]["diff"]


# ---------------------------------------------------------------- deterministic declaration (no AI)
def test_declare_dependency_adds_to_both_manifests_and_validates(root):
    edits = P.declare_dependency(root, "certifi", "2026.1.4")
    assert [e["file"] for e in edits] == ["requirements.txt", "pyproject.toml"]
    p = P.make_patch(root, "f-c", "certifi", edits, "deterministic", summary="declare certifi")
    assert all(f["origin"] == "deterministic" for f in p["files"])
    P.apply_patch(root, p)
    assert (root / "requirements.txt").read_text().splitlines()[-1] == "certifi==2026.1.4"
    import tomllib
    deps = tomllib.loads((root / "pyproject.toml").read_text())["project"]["dependencies"]
    assert deps[-2:] == ["six", "certifi==2026.1.4"]


def test_declare_dependency_handles_missing_trailing_comma_and_unknown_version(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\ndependencies = [\n    "a",\n    "b"\n]\n')
    edits = P.declare_dependency(tmp_path, "certifi", None)
    p = P.make_patch(tmp_path, "f", "certifi", edits, "deterministic")
    P.apply_patch(tmp_path, p)
    import tomllib
    assert tomllib.loads((tmp_path / "pyproject.toml").read_text())["project"]["dependencies"] == ["a", "b", "certifi"]   # comma added, no invented version


def test_declare_dependency_on_a_repo_without_manifests_proposes_nothing(tmp_path):
    assert P.declare_dependency(tmp_path, "x") == []


def test_result_hashes_describe_exactly_what_the_files_must_contain_after_apply(root):
    import hashlib
    p = P.make_patch(root, "f-1", "jinja2", GOOD, "ai")
    P.apply_patch(root, p)
    for path, h in p["result_hashes"].items():
        assert hashlib.sha256((root / path).read_bytes()).hexdigest() == h   # publishing can verify it ships what was reviewed


# ---------------------------------------------------------------- line endings are preserved exactly
def test_crlf_files_keep_crlf_and_only_the_edited_line_changes(tmp_path):
    raw = b"toml==0.10.2\r\nJinja2==2.11.3\r\nsix\r\n"
    (tmp_path / "requirements.txt").write_bytes(raw)
    p = P.make_patch(tmp_path, "f", "jinja2", [E("requirements.txt", "Jinja2==2.11.3", "Jinja2==3.1.6")], "ai")
    assert p["files"][0]["lines_added"] == 1 and p["files"][0]["lines_removed"] == 1         # not a whole-file rewrite
    P.apply_patch(tmp_path, p)
    assert (tmp_path / "requirements.txt").read_bytes() == b"toml==0.10.2\r\nJinja2==3.1.6\r\nsix\r\n"
    import hashlib
    assert hashlib.sha256((tmp_path / "requirements.txt").read_bytes()).hexdigest() == p["result_hashes"]["requirements.txt"]


def test_lf_files_stay_lf_and_mixed_endings_are_refused(tmp_path):
    (tmp_path / "requirements.txt").write_bytes(b"a==1\nb==1\n")
    P.apply_patch(tmp_path, P.make_patch(tmp_path, "f", "a", [E("requirements.txt", "a==1", "a==2")], "ai"))
    assert (tmp_path / "requirements.txt").read_bytes() == b"a==2\nb==1\n"
    (tmp_path / "requirements.txt").write_bytes(b"a==1\r\nb==1\nc==1\r\n")
    ok, bad = P.validate_edits(tmp_path, [E("requirements.txt", "a==1", "a==2")])
    assert ok == [] and "mixed line endings" in bad[0]["reason"]
