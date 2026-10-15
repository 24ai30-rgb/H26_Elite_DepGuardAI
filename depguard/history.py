"""Local scan history (JSON, atomic writes). Stores summaries and capsule locations only: no source code, no secrets."""
import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .metadata import DATA_DIR, ensure_ignored

DEFAULT_PATH = DATA_DIR / "history.json"

MAX_RECORDS = 200
_lock = threading.Lock()
ID_RE = re.compile(r"^[0-9a-f]{12}$")


def _read(path: Path):
    """-> (records, warning). Missing file is normal; corrupted file is moved aside and reported."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [r for r in data if isinstance(r, dict) and ID_RE.match(str(r.get("id", "")))], None
        raise ValueError("history file is not a list")
    except FileNotFoundError:
        return [], None
    except (OSError, ValueError) as e:
        try:
            os.replace(path, str(path) + ".corrupt")
            where = " (moved to history.json.corrupt)"
        except OSError:
            where = ""
        return [], f"history file was unreadable and has been reset{where}: {e}"


def _write(path: Path, records):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ensure_ignored(path.parent)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(records[:MAX_RECORDS], indent=2), encoding="utf-8")
    os.replace(tmp, path)


def load(path: Path):
    with _lock:
        return _read(path)


def add(path: Path, kind: str, status: str, repo: dict, summary: dict, capsule: str | None = None, error: str | None = None) -> str:
    rec = {"id": uuid.uuid4().hex[:12], "time": datetime.now(timezone.utc).isoformat(timespec="seconds"), "kind": kind,
           "status": status, "repo": {k: repo.get(k) for k in ("path_name", "path", "commit", "remote")}, "summary": summary,
           "capsule": capsule, "error": (error or None) and error[:500]}
    with _lock:
        records, _ = _read(path)
        _write(path, [rec] + records)
    return rec["id"]


def delete(path: Path, rec_id: str) -> bool:
    """Remove one record (only the history entry; capsule files are never touched)."""
    if not ID_RE.match(rec_id or ""):
        raise ValueError("invalid record id")
    with _lock:
        records, _ = _read(path)
        kept = [r for r in records if r["id"] != rec_id]
        if len(kept) == len(records):
            return False
        _write(path, kept)
        return True


def get(path: Path, rec_id: str):
    return next((r for r in load(path)[0] if r["id"] == rec_id), None)


def summarize(pkgs, undeclared, health=None, baseline_status=None, ai_status=None) -> dict:
    """pkgs: iterable of (name, level). Counts only; nothing from source files."""
    lv = [l for _, l in pkgs]
    return {"dependencies": len(lv), "high": lv.count("high"), "medium": lv.count("medium"), "undeclared_imports": undeclared,
            "health_score": None if not health else health["score"], "baseline_status": baseline_status, "ai_status": ai_status}
