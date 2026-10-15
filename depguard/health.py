"""Evidence-based dependency health score. A HEURISTIC, not a safety guarantee.

Method (every number is a documented deduction from 100, per package):

  observed (a fact seen in files, manifests, tool output or fetched package metadata)
    observed_in_baseline_output   high 35 | warn 15   error/deprecation text in the baseline test output naming the package
    conflicting_constraints       20                  same package pinned/required incompatibly across manifests
    imported_but_undeclared       15                  static import with no manifest declaration
    private_api_import            10                  imports an underscore-prefixed name from the package
    inactive_classifier           25                  package metadata carries the 'Development Status :: 7 - Inactive' classifier
    requires_python_excludes      30                  latest release's Requires-Python excludes Python 3.12 or 3.13 (project targets 3.12/3.13)
  heuristic (a judgement rule that can be wrong)
    no_static_import_found         5                  declared but no static import seen (NOT proof it is unused)
    stale_latest_release          10                  latest release on the index is older than STALE_DAYS

  repo level: pip_check_conflicts 30 off the repository score when `pip check` reported conflicts.

    confirmed_vulnerability       40                  the package index lists an advisory for the exact version in use (source + date recorded; not an audit)
    yanked_release                30                  the exact version in use was yanked

Not scored: unlisted vulnerabilities (a missing advisory is NOT evidence of safety), unknown/unchecked metadata (never penalised, listed as unchecked),
repository activity (commits/issues are never checked).
Repository score = mean(package scores) - repo-level deductions, floored at 0.
"""
from datetime import datetime, timezone

from packaging.specifiers import InvalidSpecifier, SpecifierSet

STALE_DAYS = 730
TARGET_PYTHONS = ("3.12.0", "3.13.0")
WEIGHTS = {
    "observed_in_baseline_output": {"high": 35, "warn": 15},
    "conflicting_constraints": 20, "imported_but_undeclared": 15, "private_api_import": 10,
    "inactive_classifier": 25, "requires_python_excludes": 30, "confirmed_vulnerability": 40, "yanked_release": 30,
    "no_static_import_found": 5, "stale_latest_release": 10,
    "pip_check_conflicts": 30,
}
HEURISTIC = {"no_static_import_found", "stale_latest_release"}
DISCLAIMER = ("Heuristic score from the evidence listed below. It is not an objective measure of safety or quality. "
              "'No advisory listed' never means 'secure', and unchecked data is not counted against any package.")


def _metadata_findings(meta: dict, today) -> list:
    out = []
    if not meta or meta.get("status") != "ok":
        return out
    if meta.get("inactive_classifier"):
        out.append(("inactive_classifier", "Development Status :: 7 - Inactive classifier on the latest release"))
    rp = meta.get("requires_python")
    if rp:
        try:
            bad = [v[:4] for v in TARGET_PYTHONS if not SpecifierSet(rp).contains(v)]
            if bad:
                out.append(("requires_python_excludes", f"latest release Requires-Python '{rp}' excludes {', '.join(bad)}"))
        except InvalidSpecifier:
            pass
    up = meta.get("latest_upload_time")
    if up:
        try:
            age = (today - datetime.fromisoformat(up.replace("Z", "+00:00"))).days
            if age > STALE_DAYS:
                out.append(("stale_latest_release", f"latest release uploaded {age} days ago (threshold {STALE_DAYS})"))
        except ValueError:
            pass
    return out


def _version_findings(entry: dict) -> list:
    """Evidence the package index states about ONE exact release (advisories listed for it, yanked, Requires-Python of that release)."""
    out = []
    if not entry or entry.get("status") != "ok":
        return out
    live = [v for v in entry.get("vulnerabilities", []) if not v.get("withdrawn")]
    if live:
        ids = ", ".join((v.get("id") or "?") + (f" (fixed in {', '.join(v['fixed_in'])})" if v.get("fixed_in") else "") for v in live[:4])
        out.append(("confirmed_vulnerability", f"{len(live)} advisory(ies) listed for {entry['name']}=={entry['version']}: {ids}"))
    if entry.get("yanked"):
        out.append(("yanked_release", f"{entry['name']}=={entry['version']} was yanked" + (f": {entry['yanked_reason']}" if entry.get("yanked_reason") else "")))
    rp = entry.get("requires_python")
    if rp:
        try:
            bad = [v[:4] for v in TARGET_PYTHONS if not SpecifierSet(rp).contains(v)]
            if bad:
                out.append(("requires_python_excludes", f"{entry['name']}=={entry['version']} Requires-Python '{rp}' excludes {', '.join(bad)}"))
        except InvalidSpecifier:
            pass
    return out


def compute(names, indicators, baseline_status, pip_check_failed=False, metadata=None, today=None, version_metadata=None) -> dict:
    today = today or datetime.now(timezone.utc)
    metadata = metadata or {}
    names = sorted(set(names))
    tests_ran = baseline_status in ("passed", "failed")
    pk = {n: [] for n in names}
    for i in indicators:
        p, kind = i["package"], i["indicator"]
        if p not in pk or kind not in WEIGHTS:
            continue
        pts = WEIGHTS[kind][i["severity"] if i["severity"] in ("high", "warn") else "warn"] if kind == "observed_in_baseline_output" else WEIGHTS[kind]
        pk[p].append({"rule": kind, "points": pts, "basis": "heuristic" if kind in HEURISTIC else "observed", "evidence": i["evidence"][:3]})
    for n in names:
        for rule, ev in _metadata_findings(metadata.get(n), today):
            pk[n].append({"rule": rule, "points": WEIGHTS[rule], "basis": "heuristic" if rule in HEURISTIC else "observed",
                          "evidence": [ev + f" (source: {metadata[n]['source']}, fetched {metadata[n]['fetched_at']})"]})
    vchecked = set()
    for key, e in (version_metadata or {}).items():
        n = key.split("==")[0]
        if n not in pk or e.get("status") != "ok":
            continue
        vchecked.add(n)
        have = {d["rule"] for d in pk[n]}
        for rule, ev in _version_findings(e):
            if rule == "requires_python_excludes" and rule in have:
                continue   # one deduction per rule per package
            pk[n].append({"rule": rule, "points": WEIGHTS[rule], "basis": "observed",
                          "evidence": [ev + f" (source: {e['source']}, fetched {e['fetched_at']}; index-listed, not an independent audit)"]})
    per = [{"package": n, "score": max(0, 100 - sum(d["points"] for d in pk[n])), "deductions": pk[n],
            "metadata_checked": metadata.get(n, {}).get("status") == "ok", "version_checked": n in vchecked} for n in names]
    repo_ded = [{"rule": "pip_check_conflicts", "points": WEIGHTS["pip_check_conflicts"], "basis": "observed",
                 "evidence": ["pip check reported conflicts in the baseline environment"]}] if pip_check_failed else []
    mean = sum(p["score"] for p in per) / len(per) if per else 100
    score = max(0, round(mean - sum(d["points"] for d in repo_ded)))
    # evidence coverage: 4 checks per package (manifest, static imports, baseline tests, package metadata)
    done = sum(2 + tests_ran + p["metadata_checked"] for p in per)
    unchecked = [{"check": "repository activity (commits, issues, maintainers)", "reason": "never checked by DepGuard"}]
    novuln = [p["package"] for p in per if not p["version_checked"]]
    if novuln:
        unchecked.insert(0, {"check": "known-vulnerability / yanked status of the exact version",
                             "reason": f"not checked for {len(novuln)} of {len(per)} packages (needs an exact version and a network fetch); absence of a check is not absence of a problem"})
    if not tests_ran:
        unchecked.insert(0, {"check": "baseline test output", "reason": f"baseline status is '{baseline_status or 'not_run'}', so test-derived indicators are unavailable"})
    nometa = [p["package"] for p in per if not p["metadata_checked"]]
    if nometa:
        unchecked.insert(0, {"check": "package index metadata (release recency, Requires-Python, classifiers)",
                             "reason": f"not fetched for {len(nometa)} of {len(per)} packages (needs network and an explicit request)"})
    return {"kind": "heuristic", "score": score, "scale": "0-100; higher = fewer observed resilience issues",
            "disclaimer": DISCLAIMER, "weights": WEIGHTS, "stale_days": STALE_DAYS,
            "repo_deductions": repo_ded, "packages": sorted(per, key=lambda p: (p["score"], p["package"])),
            "unchecked": unchecked, "evidence_coverage": {"checks_done": done, "checks_possible": 4 * len(per),
                                                          "percent": round(100 * done / (4 * len(per))) if per else 0}}


def from_capsule(cap: dict, metadata=None, version_metadata=None) -> dict:
    ex = cap["executed_tests"]["baseline"]
    pc = ex.get("pip_check") or {}
    return compute({d["name"] for d in cap["dependencies"]["items"]}, cap["risk_evidence"]["indicators"],
                   ex.get("status"), pip_check_failed=pc.get("exit_code") not in (0, None), metadata=metadata,
                   version_metadata=version_metadata if version_metadata is not None else cap.get("version_metadata"))
