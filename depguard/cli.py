"""DepGuard AI command line: `analyze` (read-only) and `apply` (explicit human approval)."""
import argparse
import json
import sys
from pathlib import Path

from . import advisor, capsule, compare as compare_mod, findings as findings_mod, impact as impact_mod, indicators, metadata, migrate, repo_input, scanner, usage, verify

AT_RISK = ("critical", "high", "medium")   # low findings (inactivity, 'no static import') are listed but not sent for replacement advice


def transitive_from(base: dict, declared: set) -> list:
    """Packages installed in the verification environment that no manifest declares. Versions are as installed there."""
    skip = {"pip", "wheel", "setuptools", "pkg-resources"} - declared
    return [{"name": n, "version": v, "source": "installed in the verification environment; not a declared dependency (transitive, tooling, or undeclared)"}
            for n, v in sorted((base.get("resolved") or {}).items()) if n not in declared and n not in skip]


def analyze(root: Path, out: Path, model: str, use_ai=True, run_tests=True, fetch_metadata=False,
            meta_cache: Path = metadata.DEFAULT_CACHE, repository_input: dict | None = None, combined_proposal=False, per_dependency=True, progress=None) -> dict:
    """READ-ONLY analysis. Produces findings, impact graph, AI advice for every at-risk finding, and a capsule.
    No patch is generated or tested here: that happens only when a user asks to repair a finding (see depguard.repair).
    combined_proposal=True reproduces the earlier experiment (all of Gemma's edits tested together in a temporary copy).
    fetch_metadata=True contacts the package index (explicit opt-in); otherwise only already-cached entries (with source+time) are used."""
    root = Path(root).resolve()
    say = progress or (lambda m: None)
    say("1/6 scanning manifests and source usage")
    scan = scanner.scan(root)
    declared = {d["name"] for d in scan["dependencies"]}
    use = usage.map_usage(root, declared)
    say("2/6 mapping the impact on modules and tests")
    graph = impact_mod.build(root, declared | set(use["undeclared_imports"]))
    say("3/6 running the baseline tests (isolated virtual environment)" if run_tests else "3/6 baseline tests skipped")
    base = verify.verify(root, None, "baseline", progress=(lambda m: say("3/6 " + m.split("] ", 1)[-1])) if run_tests else None) if run_tests else {"status": "not_run"}
    ind = indicators.build(scan, use, {"baseline": base, "pip_check": base.get("pip_check", {})} if run_tests else None)

    say("4/6 " + ("fetching package metadata and advisories from the package index" if fetch_metadata else "reading cached package metadata (nothing is fetched)"))
    names = sorted(declared)
    meta = metadata.fetch(names, meta_cache) if fetch_metadata else metadata.cached(names, meta_cache)
    resolved = base.get("resolved") or {}
    pins = {d["name"]: d["pinned_version"] for d in scan["dependencies"] if d.get("pinned_version")}
    pairs = [(n, pins.get(n) or resolved.get(n)) for n in sorted(declared | set(use["undeclared_imports"]))]
    vmeta = metadata.fetch_versions(pairs, meta_cache) if fetch_metadata else metadata.cached_versions(pairs, meta_cache)
    hpk = {}
    findings = findings_mod.build({"dependencies": [{**d, "resolved_version": d.get("resolved_version") or resolved.get(d["name"])} for d in scan["dependencies"]]
                                   + [{"name": u, "specifier": "", "pinned_version": None, "file": "(import)", "line": 0, "resolved_version": resolved.get(u)} for u in use["undeclared_imports"] if u not in declared]},
                                  ind["indicators"], graph, meta, vmeta, base.get("status"), hpk)
    at_risk = [f["package"] for f in findings if f["severity"] in AT_RISK]

    if use_ai and at_risk:
        ai = advisor.advise_all(model, scan, use, ind, root, at_risk, findings=findings, progress=say)
    elif use_ai:
        ai = {"status": "ok", "model": advisor.model_info(model), "recommendations": [], "note": "no at-risk finding needed advice"}
    else:
        ai = {"status": "not_requested", "model": advisor.model_info(model)}
    by_pkg = {r["package"].lower(): r for r in ai.get("recommendations", [])}
    for f in findings:   # candidates are AI-generated and unverified; kept on the finding so the repair step can see them
        r = by_pkg.get(f["package"])
        names, unusable = compare_mod.candidate_names(r["replacement_candidates"] if r else [])
        f["candidates"] = [{"name": c, "source": "ai", "verified": False} for c in names]
        if unusable:
            f["unusable_ai_candidates"] = unusable   # recorded, never used: not valid package names

    proposal = {"status": "none", "accepted_edits": [], "rejected_edits": [], "diff": ""}
    new, comparison, bad = {"status": "not_run"}, None, []
    if combined_proposal:
        edits = [e for r in ai.get("recommendations", []) for e in r["manifest_edits"]]
        ok, bad = migrate.validate_edits(root, edits)
        proposal = {"status": "proposed" if ok else "none", "accepted_edits": ok, "rejected_edits": bad, "diff": migrate.preview(root, ok) if ok else ""}
        new = verify.verify(root, ok, "proposed") if run_tests and ok else {"status": "not_run"}
        comparison = verify.compare(base, new) if run_tests and ok else None
    gaps = [f"[observed] {c['detail']}" for c in scan["conflicts"]]
    gaps += [f"[observed] private API import {p['name']} at {p['file']}:{p['line']}" for p in use["private_api_imports"]]
    gaps += [f"[observed] rejected AI edit for {r['file']}: {r['reason']}" for r in bad]
    gaps += [f"[observed] unsupported format present, not analysed: {u['file']} ({u['kind']})" for u in scan.get("unsupported_formats", [])]
    if comparison and comparison["regressed"]:
        gaps.append(f"[observed] proposed change regressed tests: {comparison['regressed']}")
    gaps += [f"[AI-claimed, unverified] {c}" for r in ai.get("recommendations", []) for c in r["compatibility_concerns"]]
    gaps += [f"[unknown] {u}" for f in findings for u in f["unknown"][:1]]

    say("6/6 building and validating the capsule")
    cap = capsule.build(root, scan, use, ind, ai, proposal, base, new, comparison, gaps, meta)
    ri = repository_input or {}
    if ri.get("label"):                       # a ZIP/GitHub extract lives in a folder named 'extracted'/'repo': identify the project by its real name
        cap["repository"]["path_name"] = ri["label"]
    if ri.get("method") == "github":          # the analysed revision is the downloaded commit, not whatever git says about the extract (nothing)
        cap["repository"].update(remote=f"https://github.com/{ri['repository']}", commit=ri["commit"], dirty=False)
    info = repo_input.git_info(root)
    cap = capsule.enrich(cap, findings=findings, impact=graph, version_metadata=vmeta or None, transitive=transitive_from(base, declared),
                         repository_input=repository_input or {"method": "local_git" if info["is_git"] else "local_folder", "branch": info["branch"], "commit": info["commit"],
                                                                "fingerprint": repo_input.tree_fingerprint(root), "dirty": info["dirty"]},
                         runtime=advisor.runtime_check(model) if use_ai else None)
    cap["dependencies"]["unsupported_formats"] = scan.get("unsupported_formats", [])
    cap["dependencies"]["lockfiles"] = scan.get("lockfiles", [])
    capsule.write(cap, out, per_dependency_capsules=per_dependency)
    return cap


def apply(capsule_path: Path, workdir: Path, assume_yes: bool) -> int:
    cap = json.loads(Path(capsule_path).read_text(encoding="utf-8"))
    edits = cap["migration_proposal"]["accepted_edits"]
    if not edits:
        print("No accepted edits in capsule.")
        return 1
    workdir = Path(workdir).resolve()
    ok, bad = migrate.validate_edits(workdir, edits)
    if bad:
        print("Edits do not apply cleanly to", workdir, bad)
        return 2
    print(migrate.preview(workdir, ok))
    if not assume_yes and input(f"Apply these {len(ok)} edit(s) to {workdir}? Type 'yes': ").strip() != "yes":
        print("Aborted; nothing changed.")
        return 1
    migrate.apply_edits(workdir, ok)
    print("Applied. Nothing was committed or pushed; review with `git diff`.")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="depguard")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("analyze", help="read-only analysis of a repository")
    a.add_argument("repo", type=Path)
    a.add_argument("--out", type=Path, default=Path("depguard-out"))
    a.add_argument("--model", default="gemma4:e2b")
    a.add_argument("--no-ai", action="store_true")
    a.add_argument("--no-tests", action="store_true")
    a.add_argument("--metadata", action="store_true", help="fetch package and exact-version metadata (advisories listed, yanked, license) from the package index (needs network; off by default)")
    a.add_argument("--combined-proposal", action="store_true", help="also test all of Gemma's edits together in a temporary copy (the earlier experiment); no patch is applied anywhere")
    p = sub.add_parser("apply", help="apply a capsule's accepted edits to a working copy (asks first)")
    p.add_argument("capsule", type=Path)
    p.add_argument("--workdir", type=Path, required=True)
    p.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    args = ap.parse_args(argv)
    if args.cmd == "apply":
        return apply(args.capsule, args.workdir, args.yes)
    cap = analyze(args.repo, args.out, args.model, not args.no_ai, not args.no_tests, args.metadata, combined_proposal=args.combined_proposal)
    print(f"AI: {cap['ai_recommendations']['status']} | baseline: {cap['executed_tests']['baseline']['status']} | "
          f"proposed: {cap['executed_tests']['proposed']['status']} | capsule -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
