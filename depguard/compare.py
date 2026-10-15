"""Replacement comparison built from documented evidence. Never asserts equivalence.

Each cell is {value, basis, note?} with basis one of:
  observed  - read from the repository / this machine     metadata - fetched package metadata (source + time recorded)
  heuristic - a documented rule of thumb                  unknown  - not checked / no evidence
"""
import importlib.metadata as im

from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import Version

from . import metadata as md

PYTHONS = ("3.12", "3.13")
FILE_SUFFIXES = (".txt", ".toml", ".py", ".cfg", ".ini", ".md", ".json", ".yml", ".yaml", ".lock", ".in", ".rst", ".log")


def candidate_names(strings):
    """Turn AI/user candidate strings into package names. Returns (names, rejected)."""
    names, bad = [], []
    for s in strings:
        s = (s or "").strip()
        try:
            n = Requirement(s).name
        except InvalidRequirement:
            bad.append(s)
            continue
        if n.lower().endswith(FILE_SUFFIXES):   # a small model sometimes lists file names as 'replacement candidates'
            bad.append(s)
            continue
        if md.valid_name(n) and canonicalize_name(n) not in names:
            names.append(canonicalize_name(n))
    return names, bad


def _cell(value, basis, note=None):
    return {"value": value, "basis": basis, **({"note": note} if note else {})}


def _installed(name):
    try:
        return _cell(im.version(name), "observed", "installed in the DepGuard environment (not necessarily the target's)")
    except im.PackageNotFoundError:
        return _cell("not installed locally", "observed", "checked this machine's DepGuard environment only")


def _meta_status(m):
    if not m:
        return _cell("not fetched", "unknown", "package metadata needs an explicit network request (or a cached copy)")
    if m.get("status") != "ok":
        return _cell("unavailable: " + m.get("error", "unknown error"), "unknown")
    return _cell(f"latest {m['latest_version']}, uploaded {m['latest_upload_time'] or 'unknown'}", "metadata",
                 f"source {m['source']} fetched {m['fetched_at']}")


def _python(m):
    if not m or m.get("status") != "ok":
        return _cell("unknown", "unknown", "Requires-Python not fetched")
    rp = m.get("requires_python")
    if not rp:
        return _cell("Requires-Python not declared", "metadata", f"classifiers: {', '.join(m['python_classifiers']) or 'none'}")
    ok = {v: SpecifierSet(rp).contains(v + ".0") for v in PYTHONS}
    return _cell(f"Requires-Python {rp}: " + ", ".join(f"{v} {'allowed' if a else 'EXCLUDED'}" for v, a in ok.items()), "metadata",
                 f"classifiers: {', '.join(m['python_classifiers']) or 'none'}")


def _constraints(m, deps):
    if not m or m.get("status") != "ok":
        return _cell("unknown", "unknown", "requires_dist not fetched")
    pins = {d["name"]: d["pinned_version"] for d in deps if d["pinned_version"]}
    declared = {d["name"] for d in deps}
    lines = []
    for r in m.get("requires_dist", []):
        try:
            req = Requirement(r)
        except InvalidRequirement:
            continue
        if req.marker and "extra" in str(req.marker):
            continue
        n = canonicalize_name(req.name)
        if n in pins and req.specifier and not req.specifier.contains(Version(pins[n]), prereleases=True):
            lines.append(f"CONFLICT: requires {req.name}{req.specifier} but the repo pins {n}=={pins[n]}")
        elif n in declared:
            lines.append(f"requires {r} (repo declares {n}; compatibility with its constraint not contradicted)")
        else:
            lines.append(f"requires {r} (new dependency, not declared in the repo)")
    return _cell(lines or ["no runtime requirements listed"], "metadata", f"source {m['source']} fetched {m['fetched_at']}")


def _license(m):
    if not m or m.get("status") != "ok":
        return _cell("unknown", "unknown", "license metadata not fetched")
    if "license" not in m:   # a cache entry from before license capture existed: absence of the key is not "no license declared"
        return _cell("unknown", "unknown", "this cached entry predates license capture; fetch metadata again")
    lic, cls = m.get("license"), m.get("license_classifiers") or []
    if not lic and not cls:
        return _cell("not declared in the package metadata", "metadata", f"source {m['source']} fetched {m['fetched_at']}")
    value = f"{lic} (classifiers: {', '.join(cls)})" if lic and cls else lic or f"classifier only: {', '.join(cls)} (the package gives no license text or expression)"
    return _cell(value, "metadata",
                 f"as declared by the package, source {m['source']} fetched {m['fetched_at']}; not legal advice, redistribution terms are not verified")


def _effort(usages):
    files = {u["file"] for u in usages}
    syms = {n for u in usages for n in u["names"]}
    sites = len(usages)
    bucket = "small" if sites <= 3 and len(files) <= 2 else "moderate" if sites <= 10 else "large"
    return _cell(f"{sites} import site(s) in {len(files)} file(s), {len(syms)} distinct imported name(s): {bucket}", "heuristic",
                 f"counts are observed; the '{bucket}' bucket is a rule of thumb (<=3 sites & <=2 files small, <=10 moderate)")


def _tests(name, experiments, patches=()):
    ps = [p for p in patches if p["package"].lower() == name and p.get("status") not in ("rejected", "stale") and p.get("verification")]
    if ps:
        return _cell([f"patch {p['id']} ({p['origin']}): {p['verification']['status']}: {p['verification'].get('summary') or '; '.join(p['verification'].get('reasons', []))}"
                      + (f", pytest exit {p['verification']['exit_code']}" if p["verification"].get("exit_code") is not None else "") for p in ps], "observed",
                     "executed on the project's own tests in an isolated copy; AI-generated patches stay unverified claims beyond that; passing tests do not prove full compatibility")
    hits = [e for e in experiments if any(name in (ed["replace"] or "").lower() for ed in e["edits"])]
    if not hits:
        return _cell("not run", "unknown", "no recorded experiment or repair patch tests this package")
    def fmt(c):
        return f"{c['passed']} passed/{c['failed']} failed"
    return _cell([f"{e['name']}: baseline {fmt(e['comparison']['baseline_counts'])} -> proposed {fmt(e['comparison']['proposed_counts'])} "
                  f"({e['proposed']['status']})" for e in hits], "observed",
                 "executed experiment; passing tests do not prove full compatibility")


def compare(package, candidates, usages, deps, meta, experiments=(), patches=()):
    """meta: {name: metadata entry}. usages: static import sites of `package`. deps: declared entries."""
    cur = canonicalize_name(package)
    cols = [cur] + [c for c in candidates if c != cur]
    syms = sorted({n for u in usages for n in u["names"]})
    rows = {k: {} for k in ("availability", "metadata", "python", "constraints", "license", "api", "effort", "tests")}
    for c in cols:
        m = meta.get(c)
        rows["availability"][c] = _installed(c)
        rows["metadata"][c] = _meta_status(m)
        rows["python"][c] = _python(m)
        rows["constraints"][c] = _constraints(m, deps)
        rows["license"][c] = _license(m)
        rows["tests"][c] = _tests(c, experiments, patches)
        if c == cur:
            rows["api"][c] = _cell(f"{len(usages)} static import site(s); names used: {', '.join(syms) or 'plain module import'}", "observed",
                                   "; ".join(f"{u['file']}:{u['line']}" for u in usages[:6]) or None)
            rows["effort"][c] = _cell("none (current dependency)", "observed")
        else:
            rows["api"][c] = _cell("equivalence NOT established", "unknown",
                                   f"the repo uses {', '.join(syms) or 'the module'}; whether {c} provides compatible APIs is unchecked (similar names are not evidence)")
            rows["effort"][c] = _effort(usages)
    labels = {"availability": "Local availability", "metadata": "Package metadata", "python": "Supported Python versions",
              "constraints": "Declared dependency constraints", "license": "License (as declared in package metadata)", "api": "API / import differences", "effort": "Migration effort", "tests": "Test results"}
    unresolved = []
    for c in cols:
        if not meta.get(c) or meta[c].get("status") != "ok":
            unresolved.append(f"{c}: package metadata not available, so Python support and constraints are unknown")
        if c != cur:
            unresolved.append(f"{c}: API compatibility with the symbols in use is unchecked; no test run covers this replacement"
                              if rows["tests"][c]["value"] == "not run" else f"{c}: API equivalence beyond the executed tests is unchecked")
    return {"package": cur, "columns": cols,
            "criteria": [{"key": k, "label": labels[k], "cells": rows[k]} for k in labels],
            "unresolved": unresolved,
            "statement": "This table lists evidence only. It does not claim any candidate is equivalent or safe to adopt."}
