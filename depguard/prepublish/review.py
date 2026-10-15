"""Change review before publishing: aggregates secrets, hygiene, verification state and consistency into one honest report.

`stop_recommended` is set ONLY for high-confidence problems in what would be published (a credential-format match in the changed files, or a patch whose tests failed).
Everything else is advisory; low-confidence or unsupported checks never block anything on their own. DepGuard itself never deletes files, rewrites history or force-pushes.
"""
import re
from pathlib import Path

from . import hygiene, secrets
from .. import scanner, usage as usage_mod


def _check(id_, title, status, confidence, detail, rec=None, **kw):
    return {"id": id_, "title": title, "status": status, "confidence": confidence, "detail": detail, "recommendation": rec, **kw}


def review(workdir: Path, patches: list, original_info: dict | None = None, declared_check: bool = True, tracked: tuple | None = None) -> dict:
    """workdir: the working copy WITH the approved patches applied (or the original for a dry review). patches: approved patch objects."""
    workdir = Path(workdir)
    checks, stops = [], []
    changed = sorted({f["path"] for p in patches for f in p["files"]})

    # 1. credentials in what would be published (changed files) and anywhere in the tree (advisory)
    in_changes = secrets.scan_files(workdir, changed)
    elsewhere = [s for s in secrets.scan_files(workdir) if s["file"] not in changed]
    hi = [s for s in in_changes if s["confidence"] == "high_format"]
    checks.append(_check("secrets_in_changes", "Credentials in the files to be published", "fail" if hi else "warn" if in_changes else "pass",
                         "confirmed" if hi else "suspected" if in_changes else "info",
                         (f"{len(in_changes)} match(es) in changed files" if in_changes else "no credential-format matches in the changed files") + " (values are never displayed).",
                         "Remove the value, rotate it, and load it from the environment." if in_changes else None,
                         matches=[{k: v for k, v in s.items() if k != "recommendation"} for s in in_changes]))
    if hi:
        stops.append("A text matching the exact format of a known credential appears in files that would be published. Remove it first; this is a high-confidence finding.")
    checks.append(_check("secrets_elsewhere", "Credential-like text elsewhere in the repository", "warn" if elsewhere else "pass", "suspected" if elsewhere else "info",
                         f"{len(elsewhere)} match(es) outside the changed files (advisory; values are never displayed)." if elsewhere else "none found", "Review whether they are real credentials." if elsewhere else None,
                         matches=[{k: v for k, v in s.items() if k != "recommendation"} for s in elsewhere[:20]]))

    # 2. hygiene
    hy = hygiene.check(workdir, *(tracked or (None, None)))   # `tracked`: (files, source) from the ORIGINAL repository (the copy has no .git)
    for h in hy:
        checks.append(_check(h["id"], h["title"], "warn", h["confidence"], h["detail"], h["recommendation"], files=h["files"], count=h["count"]))
    if not hy:
        checks.append(_check("hygiene", "Tracked-file hygiene", "pass", "info", "no environment files, caches, build artifacts or oversized files found among tracked files"))

    # 3. verification state of each patch
    for p in patches:
        v = (p.get("verification") or {}).get("status", "not_run")
        bad = v in ("failed", "blocked", "not_run")
        checks.append(_check(f"verification:{p['id']}", f"Verification of {p['package']} patch", "fail" if v == "failed" else "warn" if bad or v == "partially_verified" else "pass",
                             "confirmed", f"verification status: {v}. " + ("This fix is NOT verified as working." if bad else "Passing tests do not prove full compatibility."),
                             "Do not publish a patch whose tests failed; revise or reject it." if v == "failed" else "Treat the change as unverified in the pull request." if bad else None))
        if v == "failed":
            stops.append(f"The tests for the {p['package']} patch failed; publishing it as a fix would be misleading.")

    # 3b. advisories: does the proposed constraint still permit releases the index lists as affected?
    for p in patches:
        for ac in p.get("advisory_check", []):
            if ac["status"] in ("partial", "unbounded"):
                checks.append(_check(f"advisory:{p['id']}:{ac['file']}", f"Advisories still permitted by the {p['package']} constraint", "warn", "confirmed", ac["detail"],
                                     "Raise the lower bound to the highest fixed version, then re-test.", still_permitted=ac.get("still_permitted", [])))
    # 4. unrelated / unexpected changes (a patch may only touch manifests and files that use the dependency)
    expected = {f["path"] for p in patches for f in p["files"]}
    extra = [f for p in patches for f in p.get("unexpected_files", [])]
    checks.append(_check("unrelated_changes", "Unrelated or unexpected changes", "warn" if extra else "pass", "confirmed", f"unexpected files: {', '.join(extra)}" if extra else f"changes are limited to {len(expected)} approved file(s)"))
    big_removals = [f["path"] for p in patches for f in p["files"] if f["lines_removed"] > 5 and f["lines_removed"] > 3 * max(1, f["lines_added"])]
    checks.append(_check("unintended_deletions", "Unintended deletions", "warn" if big_removals else "pass", "suspected" if big_removals else "info",
                         f"many more lines removed than added in: {', '.join(big_removals)}" if big_removals else "no file loses far more lines than it gains"))

    # 5. dependency declarations after the change
    if declared_check:
        try:
            sc = scanner.scan(workdir)
            u = usage_mod.map_usage(workdir, {d["name"] for d in sc["dependencies"]})
            checks.append(_check("declarations", "Missing or inconsistent dependency declarations", "warn" if (u["undeclared_imports"] or sc["conflicts"]) else "pass", "confirmed",
                                 "; ".join(([f"imported but undeclared: {', '.join(u['undeclared_imports'])}"] if u["undeclared_imports"] else []) + [c["detail"] for c in sc["conflicts"]]) or "manifests agree and every import is declared",
                                 "Declare the missing packages and reconcile the constraints." if (u["undeclared_imports"] or sc["conflicts"]) else None))
        except Exception as e:  # a review must not crash on an odd repository
            checks.append(_check("declarations", "Missing or inconsistent dependency declarations", "not_checked", "info", f"could not analyse: {type(e).__name__}"))

    # 6. documentation that still mentions the old version of a changed package
    stale = []
    for p in patches:
        for e in p["edits"]:
            old = re.sub(r"^.*?([A-Za-z0-9_.-]+==[0-9A-Za-z.!+_-]+).*$", r"\1", e["find"].strip())
            if "==" not in old:
                continue
            for doc in ("README.md", "README.rst", "docs/index.md"):
                t = workdir / doc
                if t.is_file() and old.lower() in t.read_text(encoding="utf-8", errors="replace").lower():
                    stale.append(f"{doc} still mentions {old}")
    checks.append(_check("docs", "Documentation consistent with the change", "warn" if stale else "pass", "suspected" if stale else "info",
                         "; ".join(sorted(set(stale))) if stale else "no documentation mentions an old version of a changed package (a text search; documentation may still need review)"))

    # 7. pre-existing user changes
    if original_info and original_info.get("dirty"):
        checks.append(_check("preexisting_changes", "Your own uncommitted changes", "pass", "info",
                             f"The original working tree has {len(original_info.get('modified_files', []))} modified and {len(original_info.get('untracked_files', []))} untracked file(s). DepGuard did not touch them; the patch applies to a copy."))
    worst = "fail" if stops else "warn" if any(c["status"] == "warn" for c in checks) else "pass"
    return {"status": worst, "stop_recommended": bool(stops), "stop_reasons": stops, "checks": checks,
            "note": "Advisory report. Low-confidence or unsupported warnings never block publication; a stop is recommended only for the high-confidence reasons listed. DepGuard does not delete files, rewrite history or force-push."}
