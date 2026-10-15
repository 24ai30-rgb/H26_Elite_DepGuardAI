"""The pruned walker never enters vendored directories (a bundled .venv/node_modules would otherwise cost minutes)."""
import os
import time

from depguard import impact, repo_input, usage, walk
from depguard.prepublish import hygiene, secrets


def make(tmp_path, n=3000):
    (tmp_path / "app.py").write_text("import six\n")
    (tmp_path / "requirements.txt").write_text("six\n")
    for d in (".venv/lib", "node_modules/x", "app.egg-info"):
        (tmp_path / d).mkdir(parents=True)
    for i in range(n):
        (tmp_path / ".venv/lib" / f"m{i}.py").write_text("import nonexistent_pkg\n")
    (tmp_path / "node_modules/x/a.py").write_text("import nonexistent_pkg\n")
    (tmp_path / "app.egg-info/PKG.py").write_text("import nonexistent_pkg\n")
    return tmp_path


def test_iter_files_never_enters_skipped_dirs(tmp_path, monkeypatch):
    make(tmp_path)
    visited = []
    real = os.walk

    def spy(top, *a, **k):
        for dirpath, dirs, files in real(top, *a, **k):
            visited.append(dirpath)
            yield dirpath, dirs, files
    monkeypatch.setattr(walk.os, "walk", spy)
    got = [p.name for p in walk.iter_files(tmp_path)]
    assert got == ["app.py", "requirements.txt"] and all(".venv" not in v and "node_modules" not in v and "egg-info" not in v for v in visited)


def test_every_scan_ignores_vendored_code_and_stays_fast(tmp_path):
    make(tmp_path)
    t = time.time()
    assert list(usage._py_files(tmp_path)) == [tmp_path / "app.py"]
    assert "nonexistent-pkg" not in impact.build(tmp_path, {"six"})["packages"]
    assert repo_input.tree_fingerprint(tmp_path) == repo_input.tree_fingerprint(tmp_path)
    assert secrets.scan_files(tmp_path) == []
    assert time.time() - t < 5


def test_fingerprint_ignores_vendored_changes_but_not_source_changes(tmp_path):
    make(tmp_path, n=5)
    f = repo_input.tree_fingerprint(tmp_path)
    (tmp_path / ".venv/lib/m0.py").write_text("changed = True\n")
    assert repo_input.tree_fingerprint(tmp_path) == f
    (tmp_path / "app.py").write_text("import six  # edit\n")
    assert repo_input.tree_fingerprint(tmp_path) != f


def test_hygiene_reports_a_bundled_venv_as_one_entry_without_walking_it(tmp_path):
    make(tmp_path)
    files, source = hygiene.tracked_files(tmp_path)
    assert source.startswith("folder listing") and len(files) < 10 and any(f.startswith(".venv/") for f in files)
    out = {c["id"]: c for c in hygiene.check(tmp_path)}
    assert "generated_files_tracked" in out and out["generated_files_tracked"]["count"] >= 1
