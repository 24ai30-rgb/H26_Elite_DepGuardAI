"""Explainable resilience indicators built only from observed evidence."""
import re

SEV = {"info": 0, "warn": 1, "high": 2}


def build(scan: dict, usage: dict, verification: dict | None) -> dict:
    ind = []

    def add(pkg, kind, sev, evidence):
        ind.append({"package": pkg, "indicator": kind, "severity": sev, "evidence": evidence})

    for c in scan["conflicts"]:
        add(c["package"], "conflicting_constraints", "warn", [c["detail"]])
    for p in usage["undeclared_imports"]:
        locs = [f"{u['file']}:{u['line']}" for u in usage["usages"][p]]
        add(p, "imported_but_undeclared", "warn", locs)
    for p in usage["no_import_found"]:
        add(p, "no_static_import_found", "info", ["declared in manifest; no static import found (not proof of non-use)"])
    for pr in usage["private_api_imports"]:
        add(pr["package"], "private_api_import", "warn", [f"{pr['file']}:{pr['line']} imports {pr['name']}"])
    if verification:
        declared = {d["name"] for d in scan["dependencies"]}
        base = verification.get("baseline", {})
        for pkg, lines in attribute_output(base.get("output", ""), declared).items():
            add(pkg, "observed_in_baseline_output", "high" if base.get("status") == "failed" else "warn", lines)
        pc = verification.get("pip_check", {})
        if pc.get("exit_code") not in (0, None):
            add("*", "pip_check_conflicts", "high", pc["output"].splitlines()[:10])
    for d in scan["dependencies"]:
        ind.append({"package": d["name"], "indicator": "maintenance_activity", "severity": "info",
                    "evidence": ["not checked: no package-index metadata was queried (offline run)"]})
    by_pkg = {}
    for i in ind:
        by_pkg.setdefault(i["package"], []).append(i)
    levels = {}
    for pkg, items in by_pkg.items():
        top = max(SEV[i["severity"]] for i in items)
        levels[pkg] = {"level": ["no_issue_observed", "medium", "high"][top],
                       "contributing": [i["indicator"] for i in items if top and SEV[i["severity"]] == top]}
    return {"indicators": ind, "package_levels": levels}


def attribute_output(output: str, declared: set) -> dict:
    """Pull error/deprecation lines of test output that name a declared package.

    An `E   SomeError` line is attributed to the declared package of the preceding site-packages frame.
    """
    out, last = {}, None

    def put(pkg, line):
        lst = out.setdefault(pkg, [])
        if line.strip()[:300] not in lst and len(lst) < 5:
            lst.append(line.strip()[:300])
    for line in output.splitlines():
        m = re.search(r"site-packages[\\/]+([A-Za-z0-9_]+)", line)
        if m and m.group(1).lower().replace("_", "-") in declared:
            last = m.group(1).lower().replace("_", "-")
        if not re.search(r"Error|Deprecat|Warning", line):
            continue
        if line.startswith("E ") and last:
            put(last, line)
            continue
        for pkg in declared:
            if re.search(rf"(?<![\w]){re.escape(pkg.replace('-', '_'))}(?![\w])", line, re.I) or (
                    pkg == "setuptools" and "pkg_resources" in line):
                put(pkg, line)
    return out
