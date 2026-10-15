"""ZIP validation/extraction, local Git state, working copies. Real files in tmp dirs; git is used only if installed."""
import os
import shutil
import stat
import subprocess
import zipfile
from pathlib import Path

import pytest

from depguard import repo_input as ri


def make_zip(path: Path, files: dict, symlink=None, extra=None):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
        if symlink:
            zi = zipfile.ZipInfo(symlink[0])
            zi.external_attr = (stat.S_IFLNK | 0o777) << 16
            z.writestr(zi, symlink[1])
        for name, data in (extra or {}).items():
            z.writestr(name, data)
    return path


def test_good_zip_with_top_level_folder(tmp_path):
    z = make_zip(tmp_path / "p.zip", {"proj/requirements.txt": "six\n", "proj/app.py": "import six\n"})
    r = ri.extract_zip(z, tmp_path / "ws")
    assert Path(r["path"]).name == "proj" and r["manifests"] == ["requirements.txt"] and r["has_git"] is False
    assert (Path(r["path"]) / "app.py").read_text() == "import six\n"
    assert any("Git history" in n for n in r["notes"])


@pytest.mark.parametrize("name", ["../evil.txt", "a/../../evil.txt", "/etc/passwd", "C:/Windows/x", "\\\\server\\share\\x", "a\\..\\..\\evil", "con.txt", "dir/NUL", "x\x00y"])
def test_unsafe_entries_are_rejected_before_anything_is_written(tmp_path, name):
    z = make_zip(tmp_path / "bad.zip", {"proj/requirements.txt": "six\n"}, extra={name: "x"})
    with pytest.raises(ri.InputError):
        ri.extract_zip(z, tmp_path / "ws")
    assert not (tmp_path / "evil.txt").exists()
    assert not (tmp_path / "ws").exists() or list((tmp_path / "ws").iterdir()) == []


def test_symlink_entry_rejected(tmp_path):
    z = make_zip(tmp_path / "s.zip", {"p/requirements.txt": "six\n"}, symlink=("p/link", "/etc"))
    with pytest.raises(ri.InputError, match="symbolic link"):
        ri.validate_zip(z)


def test_not_a_zip_empty_and_oversized(tmp_path, monkeypatch):
    (tmp_path / "x.zip").write_text("not a zip")
    with pytest.raises(ri.InputError, match="not a valid ZIP"):
        ri.validate_zip(tmp_path / "x.zip")
    with zipfile.ZipFile(tmp_path / "e.zip", "w"):
        pass
    with pytest.raises(ri.InputError, match="empty"):
        ri.validate_zip(tmp_path / "e.zip")
    monkeypatch.setattr(ri, "MAX_ZIP_BYTES", 10)
    z = make_zip(tmp_path / "big.zip", {"p/requirements.txt": "six\n" * 50})
    with pytest.raises(ri.InputError, match="larger than"):
        ri.validate_zip(z)


def test_zip_bomb_and_entry_limits(tmp_path, monkeypatch):
    z = make_zip(tmp_path / "bomb.zip", {"p/requirements.txt": "six\n", "p/big.txt": "0" * (30 * 2**20)})
    with pytest.raises(ri.InputError, match="compression ratio"):
        ri.validate_zip(z)
    monkeypatch.setattr(ri, "MAX_UNCOMPRESSED", 1000)
    with pytest.raises(ri.InputError, match="extract to more than"):
        ri.validate_zip(make_zip(tmp_path / "m.zip", {"p/a.txt": "x" * 2000}))
    monkeypatch.setattr(ri, "MAX_ENTRIES", 2)
    with pytest.raises(ri.InputError, match="more than 2 entries"):
        ri.validate_zip(make_zip(tmp_path / "n.zip", {"a": "1", "b": "2", "c": "3"}))


def test_unusable_project_is_reported_and_cleaned_up(tmp_path):
    z = make_zip(tmp_path / "docs.zip", {"p/README.md": "hello", "p/img.png": "x"})
    with pytest.raises(ri.InputError, match="does not look like a usable project"):
        ri.extract_zip(z, tmp_path / "ws")
    assert list((tmp_path / "ws").iterdir()) == []


def test_unsupported_manifests_are_still_listed(tmp_path):
    z = make_zip(tmp_path / "p.zip", {"p/Pipfile": "[packages]", "p/poetry.lock": "", "p/a.py": "x=1"})
    r = ri.extract_zip(z, tmp_path / "ws")
    assert r["manifests"] == ["Pipfile", "poetry.lock"]  # detected; the scanner reports them as unsupported


def test_remove_workspace_is_contained(tmp_path):
    z = make_zip(tmp_path / "p.zip", {"p/requirements.txt": "six\n"})
    r = ri.extract_zip(z, tmp_path / "ws")
    keep = tmp_path / "keep"
    keep.mkdir()
    for bad in (keep, tmp_path, tmp_path / "ws"):
        with pytest.raises(ri.InputError):
            ri.remove_workspace(bad, tmp_path / "ws")
    ri.remove_workspace(r["path"], tmp_path / "ws")
    assert not Path(r["holder"]).exists() and keep.exists()


# ---------------------------------------------------------------- local git
needs_git = pytest.mark.skipif(not shutil.which("git"), reason="git not installed")


def git(cwd, *a):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "r"
    r.mkdir()
    (r / "requirements.txt").write_text("six\n")
    (r / "a.py").write_text("import six\n")
    git(r, "init", "-q", "-b", "main")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "init")
    return r


@needs_git
def test_git_info_clean_then_dirty(repo):
    i = ri.git_info(repo)
    assert i["is_git"] and i["branch"] == "main" and len(i["commit"]) == 40 and i["dirty"] is False
    (repo / "a.py").write_text("import six  # edited\n")
    (repo / "new.txt").write_text("u")
    i = ri.git_info(repo)
    assert i["dirty"] and i["modified"] == ["a.py"] and i["untracked"] == ["new.txt"]


def test_non_git_directory(tmp_path):
    (tmp_path / "x.py").write_text("")
    assert ri.git_info(tmp_path)["is_git"] is False


@needs_git
def test_working_copy_includes_uncommitted_changes_and_never_touches_original(repo, tmp_path):
    (repo / "a.py").write_text("import six  # my uncommitted work\n")
    (repo / "scratch.txt").write_text("untracked")
    before = {p.name: p.read_bytes() for p in repo.iterdir() if p.is_file()}
    w = ri.make_working_copy(repo, tmp_path / "work")
    c = Path(w["path"])
    assert (c / "a.py").read_text() == "import six  # my uncommitted work\n" and (c / "scratch.txt").exists() and not (c / ".git").exists()
    assert w["base"]["dirty"] and w["base"]["modified_files"] == ["a.py"] and w["base"]["untracked_files"] == ["scratch.txt"]
    (c / "requirements.txt").write_text("changed\n")
    assert {p.name: p.read_bytes() for p in repo.iterdir() if p.is_file()} == before  # original byte-for-byte unchanged
    assert ri.git_info(repo)["modified"] == ["a.py"]


def test_fingerprint_changes_with_content_and_ignores_caches(tmp_path):
    (tmp_path / "requirements.txt").write_text("a\n")
    f1 = ri.tree_fingerprint(tmp_path)
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "x.py").write_text("junk")
    assert ri.tree_fingerprint(tmp_path) == f1
    (tmp_path / "requirements.txt").write_text("b\n")
    assert ri.tree_fingerprint(tmp_path) != f1
    assert ri.file_hashes(tmp_path, ["requirements.txt", "missing.txt"])["missing.txt"] is None


def test_working_copy_into_a_folder_inside_the_source_does_not_recurse(tmp_path):
    """Regression (found by a real run): the dashboard's data folder lives INSIDE the repository, e.g. <repo>/tool/web/runs/work."""
    src = tmp_path / "repo"
    (src / "tool" / "web" / "runs").mkdir(parents=True)
    (src / "tool" / "web" / "runs" / "history.json").write_text("[]")
    (src / "requirements.txt").write_text("six\n")
    (src / "app.py").write_text("import six\n")
    (src / "pkg").mkdir()
    (src / "pkg" / "mod.py").write_text("x = 1\n")
    w = ri.make_working_copy(src, src / "tool" / "web" / "runs" / "work")
    c = Path(w["path"])
    assert (c / "requirements.txt").read_text() == "six\n" and (c / "pkg" / "mod.py").exists()
    assert not (c / "tool").exists()                                   # the directory containing the destination is not copied into itself
    assert sum(1 for _ in c.rglob("*") if _.is_file()) == 3            # bounded, no runaway nesting
    w2 = ri.make_working_copy(src, src / "tool" / "web" / "runs" / "work")   # a second copy is also fine
    assert w2["path"] != w["path"]
