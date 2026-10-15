"""Findings: one per at-risk dependency, built ONLY from recorded evidence. Detection never changes files.

Severity (documented, rule-based; the highest rule that applies wins):
  critical  the package index lists an advisory for the EXACT version in use (confirmed by the cited source, not an audit)
  high      the baseline tests show an error/deprecation naming the package; the exact version (or latest release) cannot run on Python 3.12/3.13;
            the pinned release was yanked; the package is marked Inactive
  medium    conflicting version constraints; an import with no manifest declaration; a private API import; a deprecation warning in test output
  low       release inactivity (a signal, NEVER proof of risk); declared with no static import found (uncertain); nothing else known
A package with no indicator produces no finding. 'Unknown' items are listed per finding and never count in the package's favour.
"""
import hashlib

SEVERITIES = ("critical", "high", "medium", "low")

RULES = {   # indicator -> (severity, basis, why it matters)
    "confirmed_vulnerability": ("critical", "metadata", "The package index lists a security advisory for the exact version this project uses."),
    "observed_in_baseline_output": ("high", "observed", "Running the project's own tests shows an error or deprecation that names this package, so it affects behaviour today."),
    "requires_python_excludes": ("high", "metadata", "The package metadata says it does not support Python 3.12/3.13, the versions this project targets."),
    "yanked_release": ("high", "metadata", "The release in use was withdrawn by its maintainers."),
    "inactive_classifier": ("high", "metadata", "The package declares itself Inactive, so fixes are unlikely."),
    "conflicting_constraints": ("medium", "observed", "Manifests disagree about this package, so installs can differ between environments."),
    "imported_but_undeclared": ("medium", "observed", "The code imports it but no manifest declares it; it only works while something else happens to install it."),
    "private_api_import": ("medium", "observed", "The code uses a private (underscore) name, which can change or vanish in any release."),
    "stale_latest_release": ("low", "heuristic", "No release for a long time. Inactivity is a signal, not proof the package is unsafe."),
    "no_static_import_found": ("low", "heuristic", "Declared but no static import was found. This is uncertain and is NOT proof the package is unused."),
}
VERIFY_REQ = ["Run the project's tests on the original dependency configuration (baseline) and record the result.",
              "Apply the proposed change to an isolated copy, install, and run the same tests; compare outcomes.",
              "Review the diff, the remaining risks, and what the tests do not cover before approving."]


def finding_id(package: str) -> str:
    return "f-" + hashlib.sha1(package.encode()).hexdigest()[:10]


def _sev_rank(s: str) -> int:
    return SEVERITIES.index(s)


def build(scan: dict, indicators: list, impact: dict | None = None, metadata: dict | None = None, version_metadata: dict | None = None,
          baseline_status: str | None = None, health_pkgs: dict | None = None) -> list:
    """Return findings sorted by severity then package. `indicators` are rule-based (indicators.build) and may include metadata-derived rules."""
    metadata, version_metadata, impact = metadata or {}, version_metadata or {}, (impact or {}).get("packages", {})
    deps = {}
    for d in scan["dependencies"]:
        deps.setdefault(d["name"], []).append(d)
    by_pkg = {}
    for i in indicators:
        if i["indicator"] in RULES and i["package"] != "*":
            by_pkg.setdefault(i["package"], []).append(i)
    # version-level and latest-release rules from fetched metadata
    from . import health as H
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    for pkg in set(deps) | set(by_pkg):
        meta = metadata.get(pkg)
        for rule, ev in (H._metadata_findings(meta, now) if meta else []):
            if rule in RULES:
                by_pkg.setdefault(pkg, []).append({"package": pkg, "indicator": rule, "severity": "info", "evidence": [ev],
                                                   "_source": meta["source"], "_retrieved": meta["fetched_at"]})
        for key, e in version_metadata.items():
            if key.split("==")[0] == pkg and e.get("status") == "ok":
                for rule, ev in H._version_findings(e):
                    by_pkg.setdefault(pkg, []).append({"package": pkg, "indicator": rule, "severity": "info", "evidence": [ev],
                                                       "_source": e["source"], "_retrieved": e["fetched_at"]})
    findings = []
    for pkg, inds in by_pkg.items():
        seen, ev_list, sev = set(), [], "low"
        for i in inds:
            rule = i["indicator"]
            s, basis, _ = RULES[rule]
            if rule == "observed_in_baseline_output" and i["severity"] == "warn":
                s = "medium"   # a deprecation warning is weaker than a failure
            if (rule, i["evidence"][0]) in seen:
                continue
            seen.add((rule, i["evidence"][0]))
            if _sev_rank(s) < _sev_rank(sev):
                sev = s
            ev_list.append({"rule": rule, "kind": basis, "severity": s, "text": "; ".join(i["evidence"][:2])[:400],
                            "source": i.get("_source") or "repository files / test output", "retrieved_at": i.get("_retrieved")})
        entries = deps.get(pkg, [])
        pinned = next((d["pinned_version"] for d in entries if d.get("pinned_version")), None)
        resolved = next((d.get("resolved_version") for d in entries if d.get("resolved_version")), None)
        used = pinned or resolved
        unknown = []
        if not used:
            unknown.append("No exact version is known (not pinned, not resolved): advisory and yanked status cannot be checked.")
        elif f"{pkg}=={used}" not in version_metadata or version_metadata[f"{pkg}=={used}"].get("status") != "ok":
            unknown.append(f"Known-advisory / yanked status of {pkg}=={used}: not checked (needs a network fetch).")
        if not metadata.get(pkg):
            unknown.append("Release recency, Requires-Python and license metadata: not fetched.")
        if baseline_status not in ("passed", "failed"):
            unknown.append("Baseline tests were not run, so test-derived evidence is missing.")
        top = max(ev_list, key=lambda e: -_sev_rank(e["severity"]))
        first_rule = top["rule"]
        title_map = {"confirmed_vulnerability": "has an advisory listed for the version in use", "observed_in_baseline_output": "shows errors or warnings in the baseline tests",
                     "requires_python_excludes": "does not support the target Python versions", "yanked_release": "release in use was yanked",
                     "inactive_classifier": "is marked Inactive", "conflicting_constraints": "has conflicting version constraints",
                     "imported_but_undeclared": "is imported but not declared", "private_api_import": "is used through a private API",
                     "stale_latest_release": "has had no recent release", "no_static_import_found": "is declared but no import was found"}
        imp = impact.get(pkg, {})
        vm = version_metadata.get(f"{pkg}=={used}") if used else None
        advisories = [{"id": v.get("id"), "fixed_in": v.get("fixed_in", [])} for v in (vm or {}).get("vulnerabilities", []) if not v.get("withdrawn")] if vm and vm.get("status") == "ok" else []
        findings.append({
            "id": finding_id(pkg), "package": pkg, "title": f"{pkg}{' ' + used if used else ''} {title_map[first_rule]}", "severity": sev,
            "severity_basis": sorted({e["rule"] for e in ev_list if e["severity"] == sev}),
            "detected_version": {"declared": sorted({d["specifier"] for d in entries if d["specifier"]}), "pinned": pinned, "resolved": resolved, "used_for_checks": used},
            "evidence": ev_list, "usage": imp.get("direct", []), "impact": {k: imp.get(k) for k in ("summary", "affected_files", "tests_direct", "tests_indirect", "uncertainty")} if imp else None,
            "why_it_matters": [RULES[e["rule"]][2] for e in ev_list][:4], "unknown": unknown, "verification_requirements": list(VERIFY_REQ),
            "declared_in": sorted({f"{d['file']}:{d['line']}" for d in entries}),
            "health_score": (health_pkgs or {}).get(pkg),
            "advisories": advisories, "candidates": [], "status": "open", "actions": ["repair", "ignore", "review_later"],
        })
    findings.sort(key=lambda f: (_sev_rank(f["severity"]), f["package"]))
    return findings
