"""DepGuard AI command line: `analyze` (read-only) and `apply` (explicit human approval)."""
import argparse
import json
import sys
from pathlib import Path

from . import advisor, capsule, indicators, migrate, scanner, usage, verify


def analyze(root: Path, out: Path, model: str, use_ai=True, run_tests=True) -> dict:
    root = Path(root).resolve()
    scan = scanner.scan(root)
    use = usage.map_usage(root, {d["name"] for d in scan["dependencies"]})
    base = verify.verify(root, None, "baseline") if run_tests else {"status": "not_run"}
    ind = indicators.build(scan, use, {"baseline": base, "pip_check": base.get("pip_check", {})} if run_tests else None)

    if use_ai:
        ai = advisor.advise(model, advisor.build_prompt(scan, use, ind, root))
    else:
        ai = {"status": "not_requested", "model": advisor.model_info(model)}
    edits = [e for r in ai.get("recommendations", []) for e in r["manifest_edits"]]
    ok, bad = migrate.validate_edits(root, edits)
    proposal = {"status": "proposed" if ok else "none", "accepted_edits": ok, "rejected_edits": bad,
                "diff": migrate.preview(root, ok) if ok else ""}  # read-only preview of the original

    new = verify.verify(root, ok, "proposed") if run_tests and ok else {"status": "not_run"}
    comparison = verify.compare(base, new) if run_tests and ok else None
    gaps = [f"[observed] {c['detail']}" for c in scan["conflicts"]]
    gaps += [f"[observed] private API import {p['name']} at {p['file']}:{p['line']}" for p in use["private_api_imports"]]
    gaps += [f"[observed] rejected AI edit for {r['file']}: {r['reason']}" for r in bad]
    if comparison and comparison["regressed"]:
        gaps.append(f"[observed] proposed change regressed tests: {comparison['regressed']}")
    gaps += [f"[AI-claimed, unverified] {c}" for r in ai.get("recommendations", []) for c in r["compatibility_concerns"]]

    cap = capsule.build(root, scan, use, ind, ai, proposal, base, new, comparison, gaps)
    capsule.write(cap, out)
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
    p = sub.add_parser("apply", help="apply a capsule's accepted edits to a working copy (asks first)")
    p.add_argument("capsule", type=Path)
    p.add_argument("--workdir", type=Path, required=True)
    p.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    args = ap.parse_args(argv)
    if args.cmd == "apply":
        return apply(args.capsule, args.workdir, args.yes)
    cap = analyze(args.repo, args.out, args.model, not args.no_ai, not args.no_tests)
    print(f"AI: {cap['ai_recommendations']['status']} | baseline: {cap['executed_tests']['baseline']['status']} | "
          f"proposed: {cap['executed_tests']['proposed']['status']} | capsule -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
