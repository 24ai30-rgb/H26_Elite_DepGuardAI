"""Per-project repair state: finding decisions, patches, attempts. JSON on disk (atomic writes); a corrupted file is moved aside, never trusted.

Ignored findings are NEVER removed: they stay in the state (and in the capsule) with their status and reason.
"""
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

DECISIONS = ("open", "ignored", "review_later", "repair_proposed", "repair_failed", "repaired")
PATCH_FLOW = {   # allowed status transitions
    "proposed": {"accepted_for_verification", "rejected", "stale"},
    "accepted_for_verification": {"verifying", "rejected", "stale"},
    "verifying": {"verified", "partially_verified", "failed", "blocked", "stale"},
    "verified": {"applied", "rejected", "stale"},
    "partially_verified": {"applied", "rejected", "stale"},
    "failed": {"applied", "rejected", "accepted_for_verification", "stale"},   # applying a failed patch needs an explicit acknowledgement upstream
    "blocked": {"accepted_for_verification", "rejected", "applied", "stale"},
    "applied": {"stale"},
    "rejected": set(),
    "stale": {"rejected"},
}
DEFAULT_MAX_ATTEMPTS = 2


class StateError(ValueError):
    pass


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class RepairState:
    def __init__(self, path: Path | None = None, max_attempts: int = DEFAULT_MAX_ATTEMPTS):
        self.path = Path(path) if path else None
        self.lock = threading.RLock()
        self.warning = None
        self.data = {"version": 1, "decisions": {}, "patches": {}, "attempts": [], "settings": {"max_attempts": max_attempts}}
        if self.path:
            self._load()

    # ---------------------------------------------------------------- persistence
    def _load(self):
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(d, dict) or d.get("version") != 1 or not all(k in d for k in ("decisions", "patches", "attempts", "settings")):
                raise ValueError("unexpected structure")
            self.data = d
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as e:
            try:
                os.replace(self.path, str(self.path) + ".corrupt")
            except OSError:
                pass
            self.warning = f"repair state file was unreadable and has been reset: {e}"

    def _save(self):
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    # ---------------------------------------------------------------- findings
    def decision(self, fid: str) -> dict:
        return self.data["decisions"].get(fid) or {"status": "open", "reason": None, "updated": None, "history": []}

    def set_decision(self, fid: str, status: str, reason: str | None = None) -> dict:
        if status not in DECISIONS:
            raise StateError(f"unknown status {status!r}")
        with self.lock:
            d = self.decision(fid)
            hist = d["history"] + [{"status": status, "reason": (reason or None) and reason[:500], "at": _now()}]
            self.data["decisions"][fid] = {"status": status, "reason": (reason or None) and reason[:500], "updated": _now(), "history": hist[-20:]}
            self._save()
            return self.data["decisions"][fid]

    # ---------------------------------------------------------------- patches
    def add_patch(self, patch: dict) -> dict:
        with self.lock:
            self.data["patches"][patch["id"]] = patch
            self._save()
            return patch

    def patch(self, pid: str) -> dict:
        p = self.data["patches"].get(pid)
        if not p:
            raise StateError("unknown patch")
        return p

    def patches_for(self, fid: str) -> list:
        return [p for p in self.data["patches"].values() if p["finding_id"] == fid]

    def set_patch_status(self, pid: str, status: str, **fields) -> dict:
        with self.lock:
            p = self.patch(pid)
            if status != p["status"] and status not in PATCH_FLOW.get(p["status"], set()):
                raise StateError(f"patch cannot go from '{p['status']}' to '{status}'")
            p["status"] = status
            p.update(fields)
            p["updated"] = _now()
            self._save()
            return p

    # ---------------------------------------------------------------- attempts
    @property
    def max_attempts(self) -> int:
        return int(self.data["settings"].get("max_attempts", DEFAULT_MAX_ATTEMPTS))

    def set_max_attempts(self, n: int):
        if not isinstance(n, int) or not 1 <= n <= 5:
            raise StateError("attempt limit must be an integer from 1 to 5")
        with self.lock:
            self.data["settings"]["max_attempts"] = n
            self._save()

    def attempts_for(self, fid: str) -> list:
        return [a for a in self.data["attempts"] if a["finding_id"] == fid]

    def remaining(self, fid: str) -> int:
        """Attempts left in the current budget (attempts the user closed by granting more are kept in history but do not count)."""
        return max(0, self.max_attempts - len([a for a in self.attempts_for(fid) if not a.get("closed")]))

    def record_attempt(self, fid: str, **fields) -> dict:
        with self.lock:
            a = {"finding_id": fid, "attempt": len(self.attempts_for(fid)) + 1, "at": _now(), **fields}
            self.data["attempts"].append(a)
            self._save()
            return a

    def update_attempt_for_patch(self, patch_id: str, **fields):
        """Verification finishes the attempt that proposed the patch; it is not a second attempt."""
        with self.lock:
            for a in reversed(self.data["attempts"]):
                if a.get("patch_id") == patch_id:
                    a.update(fields)
                    self._save()
                    return a
        return None

    def reset_attempts(self, fid: str):
        """The user explicitly allows more attempts for this finding (history is kept, marked closed)."""
        with self.lock:
            for a in self.attempts_for(fid):
                a["closed"] = True
            self._save()
