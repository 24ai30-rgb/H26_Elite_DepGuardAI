import json
import threading

import pytest

from depguard import history

REPO = {"path_name": "r", "path": "/x/r", "commit": "abc", "remote": None, "source_code": "SECRET_SHOULD_NOT_STORE"}


def test_add_list_get_delete_roundtrip(tmp_path):
    p = tmp_path / "h.json"
    a = history.add(p, "scan", "ok", REPO, {"dependencies": 3})
    b = history.add(p, "analysis", "error", REPO, {}, capsule="out/capsule.json", error="x" * 900)
    recs, warn = history.load(p)
    assert warn is None and [r["id"] for r in recs] == [b, a]  # newest first
    assert len(recs[0]["error"]) == 500 and recs[0]["capsule"] == "out/capsule.json"
    assert "source_code" not in recs[0]["repo"]  # only whitelisted identity fields are stored
    assert history.delete(p, a) is True and history.delete(p, a) is False
    assert [r["id"] for r in history.load(p)[0]] == [b] and history.get(p, b)["kind"] == "analysis"


def test_missing_file_is_empty_without_warning(tmp_path):
    assert history.load(tmp_path / "none.json") == ([], None)


@pytest.mark.parametrize("content", ["{broken", '{"a": 1}', "[1, 2]"])
def test_corrupted_history_reset_and_reported(tmp_path, content):
    p = tmp_path / "h.json"
    p.write_text(content)
    recs, warn = history.load(p)
    if content == "[1, 2]":
        assert recs == [] and warn is None  # non-record items are dropped quietly
    else:
        assert recs == [] and "unreadable" in warn and (tmp_path / "h.json.corrupt").exists()
    history.add(p, "scan", "ok", REPO, {})  # still usable afterwards
    assert len(history.load(p)[0]) == 1


def test_delete_rejects_bad_ids_and_does_not_touch_files(tmp_path):
    p = tmp_path / "h.json"
    cap = tmp_path / "capsule.json"
    cap.write_text("{}")
    history.add(p, "analysis", "ok", REPO, {}, capsule=str(cap))
    for bad in ("../../etc", "", "ZZZ", "a" * 40):
        with pytest.raises(ValueError):
            history.delete(p, bad)
    rid = history.load(p)[0][0]["id"]
    history.delete(p, rid)
    assert cap.exists()


def test_concurrent_adds_do_not_lose_records(tmp_path):
    p = tmp_path / "h.json"
    ts = [threading.Thread(target=history.add, args=(p, "scan", "ok", REPO, {})) for _ in range(12)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(history.load(p)[0]) == 12 and isinstance(json.loads(p.read_text()), list)


def test_summarize_counts_only():
    s = history.summarize([("a", "high"), ("b", "medium"), ("c", "no_issue_observed")], 1, {"score": 80}, "failed", "ok")
    assert s == {"dependencies": 3, "high": 1, "medium": 1, "undeclared_imports": 1, "health_score": 80,
                 "baseline_status": "failed", "ai_status": "ok"}
