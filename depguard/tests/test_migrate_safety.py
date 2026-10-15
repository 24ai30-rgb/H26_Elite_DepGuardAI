"""Snapshot, applied-diff verification and rollback."""
import pytest

from depguard import migrate

EDITS = [{"file": "requirements.txt", "find": "jinja2==2.11.3", "replace": "jinja2==3.1.6"},
         {"file": "pyproject.toml", "find": '"jinja2==2.11.3"', "replace": '"jinja2==3.1.6"'}]


@pytest.fixture
def work(tmp_path):
    (tmp_path / "requirements.txt").write_text("six\njinja2==2.11.3\n")
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\ndependencies=[\n    "jinja2==2.11.3",\n    "six",\n]\n')
    return tmp_path


def test_apply_snapshots_first_and_applied_diff_matches_preview(work):
    before = {f: (work / f).read_text() for f in ("requirements.txt", "pyproject.toml")}
    r = migrate.apply_with_snapshot(work, EDITS)
    assert r["diff_matches_preview"] is True and r["applied"] == ["requirements.txt", "pyproject.toml"]
    assert "-jinja2==2.11.3" in r["diff"] and "+jinja2==3.1.6" in r["diff"]
    for f, text in before.items():  # the snapshot holds the exact originals
        assert (work / ".depguard-backup").glob("*/" + f) and next((work / ".depguard-backup").glob("*/" + f)).read_text() == text
        assert "3.1.6" in (work / f).read_text()
    assert any("git checkout" in s for s in r["rollback"])


def test_rollback_restores_originals_byte_for_byte(work):
    before = {f: (work / f).read_bytes() for f in ("requirements.txt", "pyproject.toml")}
    r = migrate.apply_with_snapshot(work, EDITS)
    out = migrate.rollback(work, r["backup"])
    assert sorted(out["restored"]) == ["pyproject.toml", "requirements.txt"] and out["diff_vs_snapshot"] == ""
    assert {f: (work / f).read_bytes() for f in before} == before


def test_rollback_rejects_snapshots_outside_backup_dir(work, tmp_path):
    other = tmp_path.parent / "elsewhere"
    other.mkdir(exist_ok=True)
    (other / "requirements.txt").write_text("evil")
    for bad in (other, work, work / ".depguard-backup", work / ".depguard-backup" / ".." / ".."):
        with pytest.raises(ValueError):
            migrate.rollback(work, bad)
    assert (work / "requirements.txt").read_text() == "six\njinja2==2.11.3\n"


def test_invalid_edit_refused_before_anything_is_written(work):
    bad = [EDITS[0], {"file": "pyproject.toml", "find": '"jinja2==2.11.3",', "replace": '"jinja2==3.1.6"'}]  # drops comma
    with pytest.raises(ValueError, match="invalid TOML"):
        migrate.apply_with_snapshot(work, bad)
    assert not (work / ".depguard-backup").exists() and "2.11.3" in (work / "requirements.txt").read_text()


def test_stale_edit_refused_second_time(work):
    migrate.apply_with_snapshot(work, EDITS)
    with pytest.raises(ValueError):
        migrate.apply_with_snapshot(work, EDITS)
