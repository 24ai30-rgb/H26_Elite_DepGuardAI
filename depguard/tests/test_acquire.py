"""GitHub acquisition: strict URL validation, hardened clone command, containment, cleanup. No network is used."""
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from depguard import acquire


@pytest.mark.parametrize("url", [
    "https://github.com/rishrek/LinkWatch", "https://github.com/rishrek/LinkWatch.git", "https://github.com/rishrek/LinkWatch/",
    "  https://github.com/a/b  ",
])
def test_valid_urls(url):
    assert acquire.parse_url(url)[0] in ("rishrek", "a")


@pytest.mark.parametrize("url", [
    "", "http://github.com/a/b", "https://gitlab.com/a/b", "https://github.com.evil.com/a/b", "https://evil.com/github.com/a/b",
    "https://user:pw@github.com/a/b", "https://token@github.com/a/b", "https://github.com:8443/a/b", "https://github.com/a/b?x=1",
    "https://github.com/a/b#frag", "https://github.com/a", "https://github.com/a/b/c", "https://github.com/a/..", "https://github.com/../b",
    "git@github.com:a/b.git", "ssh://github.com/a/b", "file:///C:/repo", "https://github.com/a/b;rm -rf /", "https://github.com/a/b c",
    "https://github.com/-a/b", "https://github.com/a/.git", "javascript:alert(1)", "https://github.com/a/b\nhttps://evil.com",
])
def test_rejected_urls(url):
    with pytest.raises(ValueError):
        acquire.parse_url(url)


def test_clone_command_is_hardened_and_url_is_after_double_dash():
    cmd = acquire.clone_command("https://github.com/a/b.git", Path("dest"))
    joined = " ".join(cmd)
    for must in ("protocol.allow=never", "protocol.https.allow=always", "--depth 1", "--no-recurse-submodules", "--template=", "filter.lfs.smudge=", "core.fsmonitor=false"):
        assert must in joined
    assert cmd.index("--") < cmd.index("https://github.com/a/b.git")


def fake_runner(code=0, files=None, stderr=""):
    def run(cmd, **kw):
        assert kw["env"]["GIT_TERMINAL_PROMPT"] == "0" and kw["env"]["GIT_ALLOW_PROTOCOL"] == "https" and kw["stdin"] == subprocess.DEVNULL
        dest = Path(cmd[-1])
        if code == 0:
            dest.mkdir(parents=True)
            for name, data in (files or {"requirements.txt": "six\n"}).items():
                (dest / name).write_text(data)
        return SimpleNamespace(returncode=code, stdout="", stderr=stderr)
    return run


def test_successful_clone_lands_in_a_child_of_the_root_and_nothing_executes(tmp_path):
    r = acquire.clone("https://github.com/a/b", tmp_path / "acq", runner=fake_runner(files={"requirements.txt": "six\n", "evil.py": "raise SystemExit('executed!')"}))
    p = Path(r["path"])
    assert p.is_relative_to((tmp_path / "acq").resolve()) or p.is_relative_to(tmp_path / "acq")
    assert (p / "requirements.txt").exists() and r["owner"] == "a" and r["repo"] == "b" and "nothing from the repository is executed" in r["note"]
    assert acquire.list_acquired(tmp_path / "acq")[0]["path"] == r["path"]


def test_failed_clone_leaves_nothing_behind(tmp_path):
    with pytest.raises(ValueError, match="git clone failed .*128.*not found"):
        acquire.clone("https://github.com/a/b", tmp_path / "acq", runner=fake_runner(128, stderr="fatal: repository not found"))
    assert acquire.list_acquired(tmp_path / "acq") == [] and list((tmp_path / "acq").iterdir()) == []


def test_timeout_and_oversize_are_cleaned_up(tmp_path):
    def slow(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 1)
    with pytest.raises(ValueError, match="timed out"):
        acquire.clone("https://github.com/a/b", tmp_path / "acq", runner=slow, timeout=1)
    with pytest.raises(ValueError, match="limit"):
        acquire.clone("https://github.com/a/b", tmp_path / "acq", runner=fake_runner(files={"big.bin": "x" * 2_000_000}), max_mb=1)
    assert list((tmp_path / "acq").iterdir()) == []


def test_invalid_url_never_runs_git(tmp_path):
    def boom(*a, **k):
        raise AssertionError("git was invoked for an invalid URL")
    with pytest.raises(ValueError):
        acquire.clone("https://evil.com/a/b", tmp_path / "acq", runner=boom)


def test_remove_only_deletes_inside_the_acquisition_folder(tmp_path):
    root = tmp_path / "acq"
    r = acquire.clone("https://github.com/a/b", root, runner=fake_runner())
    keep = tmp_path / "precious"
    keep.mkdir()
    (keep / "f.txt").write_text("keep")
    for bad in (keep, tmp_path, root, root.parent / "x", Path(r["path"]) / ".." / ".." / "precious"):
        with pytest.raises(ValueError):
            acquire.remove(bad, root)
    assert (keep / "f.txt").exists() and Path(r["path"]).exists()
    acquire.remove(r["path"], root)
    assert acquire.list_acquired(root) == [] and keep.exists()


def test_remove_handles_read_only_files(tmp_path):
    import os
    import stat
    root = tmp_path / "acq"
    r = acquire.clone("https://github.com/a/b", root, runner=fake_runner())
    f = Path(r["path"]) / "requirements.txt"
    os.chmod(f, stat.S_IREAD)
    acquire.remove(r["path"], root)
    assert not Path(r["holder"]).exists()
