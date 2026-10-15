"""Tests must never touch the real dashboard data folder (history, metadata cache, experiment copies)."""
import pytest

from depguard import history, metadata
from depguard.web import server


@pytest.fixture(autouse=True)
def _isolated_dashboard_data(tmp_path, monkeypatch):
    data = tmp_path / "_dashboard_data"
    monkeypatch.setattr(server, "RUNS", data)
    monkeypatch.setattr(metadata, "DATA_DIR", data)
    monkeypatch.setattr(metadata, "DEFAULT_CACHE", data / "metadata-cache.json")
    monkeypatch.setattr(history, "DEFAULT_PATH", data / "history.json")
    yield
