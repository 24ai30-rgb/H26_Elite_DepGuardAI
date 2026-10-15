"""Dependency Exit Capsule: assemble, validate against the JSON schema, render JSON + Markdown."""
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import jsonschema

from . import findings as findings_mod, health, report_html
from .prepublish import secrets as secrets_mod

SCHEMA = json.loads((Path(__file__).parent / "capsule.schema.json").read_text(encoding="utf-8"))
SECRET_RE = re.compile(r"(ghp_\w{20,}|github_pat_\w{20,}|AKIA[0-9A-Z]{16}|sk-[A-Za-z0-9]{20,}|://[^/\s:@]+:[^/\s@]+@)")
STATIC_LIMITS = [   # the wording for a run that fetched nothing; build()/enrich() swap lines 0 and 1 when the index really was queried
    "Maintenance activity / release recency was not checked (no usable package-index metadata).",
    "Vulnerability scanning is intentionally out of scope.",
    "Verification uses a throwaway venv and copied tree, not a container; target code runs as the current user.",
    "AI recommendations are model output and are not facts; only 'executed_tests' reflect real runs.",
]
RECENCY_FETCHED = ("Release recency is only the latest upload date the package index reports (source and fetch time are in package_metadata); "
                   "repository activity such as commits and issues is never checked.")
VULN_FROM_INDEX = ("Advisories are only those the package index lists for the exact versions in use (source and fetch time are recorded per finding); "
                   "this is not a vulnerability audit, and 'no advisory listed' does not mean secure.")


def _git(root, *args):
    try:
        r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=20)
        return r.stdout.strip() if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def identity(root: Path) -> dict:
    remote = _git(root, "remote", "get-url", "origin")
    status = _git(root, "status", "--porcelain")
    return {"path_name": Path(root).resolve().name,
            "remote": re.sub(r"://[^/@]+@", "://", remote) if remote else None,  # strip credentials
            "commit": _git(root, "rev-parse", "HEAD"), "dirty": None if status is None else bool(status)}


def build(root, scan, usage, indicators, ai, proposal, base, new, comparison, gaps, metadata=None) -> dict:
    resolved = base.get("resolved", {})
    items = [{**d, "resolved_version": resolved.get(d["name"])} for d in scan["dependencies"]]
    cap = {
        "capsule_version": "1",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "repository": identity(root),
        "dependencies": {"manifests": scan["manifests"], "python_requires": scan["python_requires"],
                         "items": items, "parse_issues": scan["issues"]},
        "risk_evidence": indicators,
        "usage": {k: usage[k] for k in ("usages", "undeclared_imports", "no_import_found", "private_api_imports",
                                        "dynamic_import_sites", "limitations")},
        "ai_recommendations": {k: v for k, v in ai.items() if k != "model"},
        "migration_proposal": proposal,
        "suggested_tests": [t for r in ai.get("recommendations", []) for t in r["suggested_tests"]],
        "executed_tests": {"baseline": base or {"status": "not_run"}, "proposed": new or {"status": "not_run"},
                           "comparison": comparison},
        "compatibility_gaps": gaps,
        "model": ai.get("model", {"available": False, "model": "none"}),
        "limitations": list(STATIC_LIMITS),  # copy: callers must not mutate the module constant
    }
    ok_meta = {k: v for k, v in (metadata or {}).items()}
    if any(v.get("status") == "ok" for v in ok_meta.values()):
        cap["limitations"][0] = RECENCY_FETCHED   # the capsule must not say "not checked" next to data that was checked
    if ok_meta:
        cap["package_metadata"] = ok_meta
    cap["health_score"] = health.from_capsule(cap, ok_meta)
    return cap


def _redact(o):
    """Mask credential-format text in every string before it is stored or rendered."""
    if isinstance(o, str):
        return secrets_mod.redact(o)
    if isinstance(o, list):
        return [_redact(x) for x in o]
    if isinstance(o, dict):
        return {k: _redact(v) for k, v in o.items()}
    return o


def _patch_view(p: dict) -> dict:
    """A patch as stored in the capsule: everything reviewable, without bulky raw test output."""
    v = p.get("verification")
    if v:
        run = v.get("run") or {}
        v = {**{k: v.get(k) for k in ("status", "reasons", "summary", "fixed", "regressions", "baseline_status", "proposed_status", "exit_code", "failure_kind")},
             "commands": [{"command": s["command"][-6:], "exit_code": s["exit_code"], "duration_s": s.get("duration_s")} for s in run.get("steps", [])],
             "environment": run.get("environment"), "failure_excerpt": [l.strip() for l in (run.get("output") or "").splitlines() if l.startswith("E ")][:5]}
    keep = ("id", "finding_id", "package", "origin", "status", "created_at", "updated", "base", "files", "summary", "rationale", "assumptions", "compatibility_concerns",
            "suggested_tests", "replacement_candidates", "confidence_limitations", "rejected_edits", "model", "action", "advisory_check")
    return {**{k: p.get(k) for k in keep if k in p}, "verification": v, "diff": p.get("diff", "")}


def enrich(cap: dict, *, findings=None, impact=None, state=None, prepublish=None, repository_input=None, version_metadata=None,
           transitive=None, runtime=None) -> dict:
    """Add the optional sections. Findings carry the user's decision; ignored findings stay in the capsule with their reason."""
    cap = dict(cap)
    if findings is not None:
        out = []
        for f in findings:
            d = state.decision(f["id"]) if state else {"status": f.get("status", "open"), "reason": None, "history": []}
            out.append({**f, "status": d["status"], "decision": {"reason": d["reason"], "updated": d.get("updated"), "history": d.get("history", [])}})
        cap["findings"] = out
        cap["severity_criteria"] = {"doc": findings_mod.__doc__.strip().split("\n\n")[0] if findings_mod.__doc__ else "", "levels": list(findings_mod.SEVERITIES)}
    if impact is not None:
        cap["impact"] = impact
    if state is not None:
        cap["repair"] = {"decisions": {k: {kk: vv for kk, vv in v.items() if kk != "history"} for k, v in state.data["decisions"].items()},
                         "patches": [_patch_view(p) for p in state.data["patches"].values()], "attempts": state.data["attempts"], "max_attempts": state.max_attempts}
    if prepublish is not None:
        cap["prepublish"] = prepublish
    if repository_input is not None:
        cap["repository_input"] = repository_input
    if version_metadata:
        cap["version_metadata"] = version_metadata
        if "limitations" in cap and any(v.get("status") == "ok" for v in version_metadata.values()):
            cap["limitations"] = [VULN_FROM_INDEX if x == STATIC_LIMITS[1] else x for x in cap["limitations"]]
        cap["health_score"] = health.from_capsule(cap, cap.get("package_metadata"), version_metadata)
    if transitive is not None:
        cap["transitive_dependencies"] = transitive
    if runtime is not None:
        cap["runtime_check"] = runtime
    return _redact(cap)


def per_dependency(cap: dict, pkg: str) -> dict:
    """The slice of a combined capsule that concerns ONE dependency. It validates against the same schema."""
    pkg = pkg.lower()
    mentions = lambda text: pkg in str(text).lower()
    c = json.loads(json.dumps(cap))
    c["scope"] = {"type": "dependency", "package": pkg}
    c["dependencies"]["items"] = [d for d in c["dependencies"]["items"] if d["name"] == pkg]
    c["risk_evidence"] = {"indicators": [i for i in c["risk_evidence"]["indicators"] if i["package"] in (pkg, "*")],
                          "package_levels": {k: v for k, v in c["risk_evidence"]["package_levels"].items() if k == pkg}}
    u = c["usage"]
    c["usage"] = {**u, "usages": {k: v for k, v in u["usages"].items() if k == pkg}, "undeclared_imports": [x for x in u["undeclared_imports"] if x == pkg],
                  "no_import_found": [x for x in u["no_import_found"] if x == pkg], "private_api_imports": [x for x in u.get("private_api_imports", []) if x["package"] == pkg]}
    ai = c["ai_recommendations"]
    if ai.get("recommendations"):
        ai["recommendations"] = [r for r in ai["recommendations"] if r["package"].lower() == pkg]
    mp = c["migration_proposal"]
    mp["accepted_edits"] = [e for e in mp["accepted_edits"] if mentions(e["find"] + e["replace"])]
    mp["rejected_edits"] = [e for e in mp["rejected_edits"] if mentions(e["find"])]
    c["suggested_tests"] = [t for r in (ai.get("recommendations") or []) for t in r["suggested_tests"]]
    c["independent_experiments"] = [e for e in c.get("independent_experiments", []) if any(mentions(d["find"] + d["replace"]) for d in e["edits"])]
    c["compatibility_gaps"] = [g for g in c["compatibility_gaps"] if mentions(g)]
    if "findings" in c:
        c["findings"] = [f for f in c["findings"] if f["package"] == pkg]
    if "impact" in c:
        c["impact"] = {**c["impact"], "packages": {k: v for k, v in c["impact"]["packages"].items() if k == pkg}}
    if "repair" in c:
        fids = {f["id"] for f in c.get("findings", [])}
        r = c["repair"]
        c["repair"] = {**r, "decisions": {k: v for k, v in r["decisions"].items() if k in fids}, "patches": [p for p in r["patches"] if p["package"].lower() == pkg],
                       "attempts": [a for a in r["attempts"] if a["finding_id"] in fids]}
    if "version_metadata" in c:
        c["version_metadata"] = {k: v for k, v in c["version_metadata"].items() if k.split("==")[0] == pkg}
    if "package_metadata" in c:
        c["package_metadata"] = {k: v for k, v in c["package_metadata"].items() if k == pkg}
    if "transitive_dependencies" in c:
        c["transitive_dependencies"] = [t for t in c["transitive_dependencies"] if t["name"] == pkg]
    c["limitations"] = c["limitations"] + [f"This capsule covers only '{pkg}'. Test runs shown are whole-repository runs; the combined capsule has the full context."]
    return c


def validate(cap: dict) -> None:
    jsonschema.validate(cap, SCHEMA, format_checker=jsonschema.FormatChecker())
    if SECRET_RE.search(json.dumps(cap)):
        raise ValueError("capsule appears to contain a credential/secret pattern; refusing to write")


def write(cap: dict, outdir: Path, per_dependency_capsules: bool = False) -> tuple[Path, Path]:
    """Validate, then write capsule.json/.md/.html. With per_dependency_capsules, also dependencies/<package>/capsule.* for every finding."""
    validate(cap)
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    j, m = outdir / "capsule.json", outdir / "capsule.md"
    j.write_text(json.dumps(cap, indent=2), encoding="utf-8")
    m.write_text(to_markdown(cap), encoding="utf-8")
    (outdir / "capsule.html").write_text(report_html.to_html(cap), encoding="utf-8")
    if per_dependency_capsules:
        for f in cap.get("findings", []):
            sub = per_dependency(cap, f["package"])
            validate(sub)
            d = outdir / "dependencies" / re.sub(r"[^a-z0-9._-]", "_", f["package"])
            d.mkdir(parents=True, exist_ok=True)
            (d / "capsule.json").write_text(json.dumps(sub, indent=2), encoding="utf-8")
            (d / "capsule.md").write_text(to_markdown(sub), encoding="utf-8")
    return j, m


def to_markdown(c: dict) -> str:
    L = [f"# Dependency Exit Capsule: {c['repository']['path_name']}", "",
         f"Generated {c['generated_at']} · commit `{c['repository']['commit']}` · dirty: {c['repository']['dirty']}", "",
         "## 1. Observed facts (deterministic)", "",
         f"Manifests: {', '.join(c['dependencies']['manifests'])} · requires-python: `{c['dependencies']['python_requires']}`", "",
         "| Package | Declared | Pinned | Resolved | Level | Contributing |", "|---|---|---|---|---|---|"]
    lv = c["risk_evidence"]["package_levels"]
    for d in c["dependencies"]["items"]:
        x = lv.get(d["name"], {"level": "n/a", "contributing": []})
        L.append(f"| {d['name']} | `{d['specifier'] or '-'}` ({d['file']}:{d['line']}) | {d['pinned_version'] or '-'} | "
                 f"{d['resolved_version'] or 'unknown'} | {x['level']} | {', '.join(x['contributing']) or '-'} |")
    L += ["", "### Evidence", ""]
    for i in c["risk_evidence"]["indicators"]:
        if i["severity"] != "info":
            L.append(f"- **{i['package']}** · {i['indicator']} ({i['severity']}): " + "; ".join(i["evidence"][:3]))
    u = c["usage"]
    L += ["", "### Usage", "", f"- Undeclared imports: {u['undeclared_imports'] or 'none'}",
          f"- No static import found (not proof of non-use): {u['no_import_found'] or 'none'}",
          f"- Dynamic import sites: {len(u['dynamic_import_sites'])}", ""]
    for p, locs in u["usages"].items():
        L.append(f"- `{p}`: " + ", ".join(f"{x['file']}:{x['line']}" for x in locs[:8]))
    h = c.get("health_score")
    if h:
        L += ["", f"### Health score (HEURISTIC, not a guarantee): {h['score']}/100 · evidence coverage {h['evidence_coverage']['percent']}%", "",
              h["disclaimer"], ""]
        for p in h["packages"]:
            if p["deductions"]:
                L.append(f"- **{p['package']}** {p['score']}/100: " + "; ".join(f"-{d['points']} {d['rule']} ({d['basis']})" for d in p["deductions"]))
        L += [f"- repo-level: -{d['points']} {d['rule']}" for d in h["repo_deductions"]]
        L += ["- Unchecked: " + "; ".join(u["check"] for u in h["unchecked"])]
    ai, m = c["ai_recommendations"], c["model"]
    L += ["", "## 2. AI recommendations (model output, NOT verified fact)", "",
          f"Model: `{m.get('model')}` · runtime: {m.get('runtime')} {m.get('runtime_version')} · "
          f"{m.get('parameter_size')} {m.get('quantization')} · {m.get('access')}",
          f"Status: **{ai['status']}** {ai.get('reason', '')}", ""]
    for r in ai.get("recommendations", []):
        L += [f"### {r['package']}: {r['action']}", r["rationale"], "",
              "Candidates: " + (", ".join(r["replacement_candidates"]) or "-"), ""]
        L += [f"{n}. {s}" for n, s in enumerate(r["migration_steps"], 1)]
        L += [f"- concern: {x}" for x in r["compatibility_concerns"]] + [""]
    mp = c["migration_proposal"]
    L += ["## 3. Migration proposal (not applied)", "",
          f"Status: {mp['status']} · accepted {len(mp['accepted_edits'])}, rejected {len(mp['rejected_edits'])}", "",
          "```diff", mp["diff"] or "(none)", "```", ""]
    L += [f"- rejected edit `{r['file']}`: {r['reason']}" for r in mp["rejected_edits"]]
    ex = c["executed_tests"]
    L += ["", "## 4. Executed tests (actually run)", ""]
    for k in ("baseline", "proposed"):
        r = ex[k]
        L.append(f"- **{k}**: {r['status']}")
        for s in r.get("steps", []):
            L.append(f"  - `{' '.join(s['command'][-4:])}` → exit {s['exit_code']}")
    if ex["comparison"]:
        L.append(f"- comparison: {json.dumps(ex['comparison'])}")
    for x in c.get("independent_experiments", []):
        L += ["", f"### Independent experiment: {x['name']} (not AI; {x['origin']})", "", "```diff", x["diff"], "```"]
        for k in ("baseline", "proposed"):
            L.append(f"- **{k}**: {x[k]['status']} · counts {x['comparison'][k + '_counts'] if k + '_counts' in x['comparison'] else ''}")
            L += [f"  - `{' '.join(s['command'][-4:])}` → exit {s['exit_code']}" for s in x[k].get("steps", [])]
        L.append(f"- comparison: {json.dumps(x['comparison'])}")
    L += ["", "### Suggested tests (model, not run)", ""] + [f"- {t}" for t in c["suggested_tests"]]
    if c.get("repository_input"):
        ri = c["repository_input"]
        L += ["", "## Repository input", "", f"Method: {ri['method']} · branch: {ri.get('branch') or 'n/a'} · commit: {ri.get('commit') or 'n/a'} · content fingerprint: {ri.get('fingerprint') or 'n/a'}"]
    if c.get("findings"):
        L += ["", "## Findings and decisions", ""]
        for f in c["findings"]:
            L.append(f"- **[{f['severity']}] {f['package']}**: {f['title']} · status **{f['status']}**" + (f" ({f['decision']['reason']})" if f.get("decision", {}).get("reason") else ""))
            for e in f["evidence"][:3]:
                L.append(f"  - {e['kind']}: {e['text'][:160]} _(source: {e['source']}{', retrieved ' + e['retrieved_at'] if e.get('retrieved_at') else ''})_")
            for u_ in f.get("unknown", [])[:3]:
                L.append(f"  - unknown: {u_}")
    if c.get("repair") and c["repair"]["patches"]:
        L += ["", "## Patches and repair attempts", ""]
        for p in c["repair"]["patches"]:
            v = p.get("verification") or {}
            L.append(f"- patch `{p['id']}` for **{p['package']}** ({p['origin']}) · status **{p['status']}** · verification **{v.get('status', 'not_run')}**: {'; '.join(v.get('reasons', [])) or 'not tested'}")
            L += [f"  - {f['path']} ({f['verification_status']}): {f['reason'][:140]}" for f in p["files"]]
        L += [f"- attempt {a['attempt']} on `{a['finding_id']}`: {a['mode']} / {a['origin']} / {a['outcome']}" for a in c["repair"]["attempts"]]
    if c.get("prepublish"):
        L += ["", "## Pre-publish checks (supplementary)", "", f"Status: **{c['prepublish']['status']}**" + (" · stop recommended: " + "; ".join(c["prepublish"]["stop_reasons"]) if c["prepublish"].get("stop_recommended") else "")]
        L += [f"- {k['title']}: {k['status']} ({k['confidence']})" for k in c["prepublish"]["checks"] if k["status"] != "pass"]
    L += ["", "## 5. Compatibility gaps", ""] + [f"- {g}" for g in c["compatibility_gaps"]]
    L += ["", "## 6. Limitations", ""] + [f"- {x}" for x in c["limitations"] + c["usage"]["limitations"]]
    return "\n".join(L) + "\n"
