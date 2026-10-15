"""Does a proposed version constraint actually exclude the releases the package index lists as affected?

A heuristic comparison of the constraint's LOWEST allowed version with the `fixed_in` versions the index lists for the advisories of the old version.
It is not an audit: the index data can be incomplete, and an installer normally picks the newest allowed release regardless of the lower bound.
It exists so that a patch that 'upgrades' to a bound which still permits affected releases is never presented as having fixed the advisories.
"""
import re

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

NOTE = ("Heuristic comparison of the version bound with the fixed-in versions the package index lists; not an audit. "
        "Installers usually pick the newest allowed release, but the bound itself decides what is permitted.")
LOWER_OPS = (">=", "==", "~=", "===", ">")


def _versions(vals):
    out = []
    for v in vals or []:
        try:
            out.append(Version(str(v)))
        except InvalidVersion:
            pass
    return out


def lowest_allowed(req: Requirement):
    lows = []
    for s in req.specifier:
        if s.operator in LOWER_OPS and "*" not in s.version:
            try:
                lows.append(Version(s.version))
            except InvalidVersion:
                pass
    return max(lows) if lows else None


def requirement_in(text: str, package: str):
    """The first line of `text` that is a requirement on `package` (quotes and trailing commas stripped), or None."""
    for line in text.splitlines():
        t = line.strip().rstrip(",").strip()
        if len(t) >= 2 and t[0] in "\"'" and t[-1] == t[0]:   # TOML array items are quoted; a marker like python_version >= '3.9' must keep its own quotes
            t = t[1:-1]
        try:
            r = Requirement(t)
        except InvalidRequirement:
            continue
        if canonicalize_name(r.name) == canonicalize_name(package):
            return r
    return None


def check(package: str, new_text: str, advisories: list) -> dict:
    """-> {status, detail, ...}. status: covers_all | partial | unbounded | no_advisories | not_a_requirement"""
    if not advisories:
        return {"status": "no_advisories", "detail": "The index lists no advisory for the old version, so there is nothing to compare against. That is not proof of safety.", "note": NOTE}
    req = requirement_in(new_text, package)
    if req is None:
        return {"status": "not_a_requirement", "detail": f"The edit does not set a requirement on {package}; advisories were not compared.", "note": NOTE}
    low = lowest_allowed(req)
    if low is None:
        return {"status": "unbounded", "lowest_allowed": None, "detail": f"'{req}' has no lower bound, so it still permits the releases the index lists as affected.",
                "still_permitted": [a["id"] for a in advisories], "note": NOTE}
    pending = []
    for a in advisories:
        fixed = _versions(a.get("fixed_in"))
        if not fixed:
            pending.append({"id": a.get("id"), "fixed_in": []})
        elif low < max(fixed):
            pending.append({"id": a.get("id"), "fixed_in": sorted(str(v) for v in fixed)})
    if not pending:
        return {"status": "covers_all", "lowest_allowed": str(low), "detail": f"'{req}' starts at {low}, at or above every fix the index lists ({len(advisories)} advisory(ies)).", "still_permitted": [], "note": NOTE}
    worst = max((v for p in pending for v in _versions(p["fixed_in"])), default=None)
    return {"status": "partial", "lowest_allowed": str(low), "still_permitted": [p["id"] for p in pending],
            "detail": f"'{req}' still permits {low}, which the index lists as affected by {len(pending)} of {len(advisories)} advisory(ies)"
                      + (f" (fixed only in {worst} or later)" if worst else " (no fixed version listed)") + ". Raise the lower bound to exclude them.",
            "pending": pending, "note": NOTE}


def check_patch(patch: dict, finding: dict) -> list:
    """One result per edit that sets a requirement on the finding's package."""
    adv = finding.get("advisories") or []
    out = []
    for e in patch.get("edits", []):
        if requirement_in(e["replace"], patch["package"]) is not None or adv:
            r = check(patch["package"], e["replace"], adv)
            if r["status"] != "not_a_requirement":
                out.append({"file": e["file"], **r})
    return out
