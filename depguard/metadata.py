"""Optional package-index metadata (PyPI JSON API). Network is used ONLY when explicitly requested.

Every cached entry records its source URL and fetch time. Failures are recorded as `unavailable` with the error and
never replaced by guessed data; a previously fetched good entry is kept if a refresh fails.
"""
import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(__file__).parent / "web" / "runs"   # local data (git-ignored); shared with history.json
DEFAULT_CACHE = DATA_DIR / "metadata-cache.json"
INDEX = "https://pypi.org/pypi/{}/json"
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")


def valid_name(name: str) -> bool:
    return bool(NAME_RE.match(name or ""))


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_cache(path: Path) -> dict:
    """Missing or corrupted cache -> empty dict (a corrupted file is moved aside, never silently trusted)."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        try:
            os.replace(path, str(path) + ".corrupt")
        except OSError:
            pass
        return {}


def ensure_ignored(directory: Path) -> None:
    """Local data directories ignore themselves so caches/history are never committed by accident."""
    g = Path(directory) / ".gitignore"
    if not g.exists():
        g.write_text("*\n!.gitignore\n", encoding="utf-8")


def save_cache(path: Path, cache: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ensure_ignored(path.parent)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def fetch_one(name: str, timeout=10, opener=urllib.request.urlopen) -> dict:
    url = INDEX.format(name)
    try:
        with opener(url, timeout=timeout) as r:
            d = json.load(r)
        info = d["info"]
        uploads = [u["upload_time_iso_8601"] for u in d.get("urls", []) if u.get("upload_time_iso_8601")]
        cls = info.get("classifiers") or []
        return {"name": name.lower(), "status": "ok", "source": url, "fetched_at": _now(),
                "latest_version": info.get("version"), "requires_python": info.get("requires_python"),
                "requires_dist": info.get("requires_dist") or [],
                "latest_upload_time": max(uploads) if uploads else None,
                "inactive_classifier": "Development Status :: 7 - Inactive" in cls,
                "python_classifiers": [c.rsplit(" :: ", 1)[-1] for c in cls if c.startswith("Programming Language :: Python :: ")
                                       and c.rsplit(" :: ", 1)[-1][:1].isdigit()],
                "license": (str(info.get("license_expression") or info.get("license") or "")[:200]) or None,
                "license_classifiers": [c.rsplit(" :: ", 1)[-1] for c in cls if c.startswith("License ::")],
                "os_classifiers": [c.rsplit(" :: ", 1)[-1] for c in cls if c.startswith("Operating System ::")]}
    except urllib.error.HTTPError as e:
        err = f"HTTP {e.code} from the package index"
    except (urllib.error.URLError, OSError) as e:
        err = f"network unavailable: {getattr(e, 'reason', e)}"
    except (ValueError, KeyError) as e:
        err = f"unexpected response: {type(e).__name__}"
    return {"name": name.lower(), "status": "unavailable", "source": url, "attempted_at": _now(), "error": err}


def fetch(names, cache_path: Path, offline=False, refresh=False, opener=urllib.request.urlopen) -> dict:
    """Return {name: entry} for the requested names. Offline: cache only, others reported unavailable."""
    cache = load_cache(cache_path)
    out = {}
    for n in sorted({x.lower() for x in names}):
        if not valid_name(n):
            out[n] = {"name": n, "status": "unavailable", "source": None, "attempted_at": _now(), "error": "invalid package name"}
            continue
        have = cache.get(n)
        if have and have.get("status") == "ok" and (offline or (not refresh and "license" in have)):   # entries cached before license capture are refetched online
            out[n] = have
        elif offline:
            out[n] = {"name": n, "status": "unavailable", "source": INDEX.format(n), "attempted_at": _now(),
                      "error": "offline mode: not fetched and not in the local cache"}
        else:
            e = fetch_one(n, opener=opener)
            if e["status"] == "ok" or not (have and have.get("status") == "ok"):
                cache[n] = e
                out[n] = e
            else:  # refresh failed: keep the earlier real data, say so
                out[n] = {**have, "note": f"refresh failed ({e['error']}); showing earlier fetch"}
    if not offline:
        save_cache(cache_path, cache)
    return out


def cached(names, cache_path: Path) -> dict:
    c = load_cache(cache_path)
    return {n: c[n] for n in {x.lower() for x in names} if c.get(n, {}).get("status") == "ok"}


# ---------------------------------------------------------------- exact-version evidence (advisories, yanked, license, OS)
VERSION_INDEX = "https://pypi.org/pypi/{}/{}/json"


def version_key(name: str, version: str) -> str:
    return f"{name.lower()}=={version}"


def _valid_version(v: str) -> bool:
    from packaging.version import InvalidVersion, Version
    try:
        Version(v or "")
        return bool(re.fullmatch(r"[A-Za-z0-9.!+_-]{1,60}", v))
    except InvalidVersion:
        return False


def fetch_version_one(name: str, version: str, timeout=10, opener=urllib.request.urlopen) -> dict:
    """Facts the package index states about ONE exact release. `vulnerabilities` lists advisories the index attaches to that release;
    an empty list means 'none listed by the source at fetched_at', never 'secure'."""
    url = VERSION_INDEX.format(name, version)
    try:
        with opener(url, timeout=timeout) as r:
            d = json.load(r)
        info = d["info"]
        cls = info.get("classifiers") or []
        files = d.get("urls", [])
        return {"name": name.lower(), "version": version, "status": "ok", "source": url, "fetched_at": _now(),
                "requires_python": info.get("requires_python"), "yanked": bool(info.get("yanked")), "yanked_reason": info.get("yanked_reason"),
                "upload_time": max([u["upload_time_iso_8601"] for u in files if u.get("upload_time_iso_8601")], default=None),
                "license": (info.get("license_expression") or info.get("license") or None) and str(info.get("license_expression") or info.get("license"))[:200],
                "license_classifiers": [c.rsplit(" :: ", 1)[-1] for c in cls if c.startswith("License ::")],
                "os_classifiers": [c.rsplit(" :: ", 1)[-1] for c in cls if c.startswith("Operating System ::")],
                "vulnerabilities": [{"id": v.get("id"), "aliases": v.get("aliases", []), "summary": (v.get("summary") or v.get("details") or "")[:300],
                                     "fixed_in": v.get("fixed_in", []), "link": v.get("link"), "withdrawn": v.get("withdrawn")} for v in (d.get("vulnerabilities") or [])],
                "vulnerability_scope": "advisories the package index lists for this exact release (not an independent audit)"}
    except urllib.error.HTTPError as e:
        err = f"HTTP {e.code} from the package index"
    except (urllib.error.URLError, OSError) as e:
        err = f"network unavailable: {getattr(e, 'reason', e)}"
    except (ValueError, KeyError) as e:
        err = f"unexpected response: {type(e).__name__}"
    return {"name": name.lower(), "version": version, "status": "unavailable", "source": url, "attempted_at": _now(), "error": err}


def fetch_versions(pairs, cache_path: Path, offline=False, refresh=False, opener=urllib.request.urlopen) -> dict:
    """pairs: iterable of (name, exact_version). Returns {'name==version': entry}. Same cache file and rules as `fetch`."""
    cache = load_cache(cache_path)
    out = {}
    for name, version in sorted({(n.lower(), v) for n, v in pairs if v}):
        key = version_key(name, version)
        if not valid_name(name) or not _valid_version(version):
            out[key] = {"name": name, "version": version, "status": "unavailable", "source": None, "attempted_at": _now(), "error": "invalid package name or version"}
            continue
        have = cache.get(key)
        if have and have.get("status") == "ok" and (offline or not refresh):
            out[key] = have
        elif offline:
            out[key] = {"name": name, "version": version, "status": "unavailable", "source": VERSION_INDEX.format(name, version), "attempted_at": _now(),
                        "error": "offline mode: not fetched and not in the local cache"}
        else:
            e = fetch_version_one(name, version, opener=opener)
            if e["status"] == "ok" or not (have and have.get("status") == "ok"):
                cache[key] = e
                out[key] = e
            else:
                out[key] = {**have, "note": f"refresh failed ({e['error']}); showing earlier fetch"}
    if not offline and out:
        save_cache(cache_path, cache)
    return out


def cached_versions(pairs, cache_path: Path) -> dict:
    c = load_cache(cache_path)
    return {version_key(n, v): c[version_key(n, v)] for n, v in pairs if v and c.get(version_key(n, v), {}).get("status") == "ok"}
