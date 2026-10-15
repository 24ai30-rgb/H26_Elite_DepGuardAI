"""Dependency Exit Capsule: assemble, validate against the JSON schema, render JSON + Markdown."""
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import jsonschema

SCHEMA = json.loads((Path(__file__).parent / "capsule.schema.json").read_text(encoding="utf-8"))
SECRET_RE = re.compile(r"(ghp_\w{20,}|github_pat_\w{20,}|AKIA[0-9A-Z]{16}|sk-[A-Za-z0-9]{20,}|://[^/\s:@]+:[^/\s@]+@)")
STATIC_LIMITS = [
    "Maintenance activity / release recency was not checked (no package-index metadata queried).",
    "Vulnerability scanning is intentionally out of scope.",
    "Verification uses a throwaway venv and copied tree, not a container; target code runs as the current user.",
    "AI recommendations are model output and are not facts; only 'executed_tests' reflect real runs.",
]


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


def build(root, scan, usage, indicators, ai, proposal, base, new, comparison, gaps) -> dict:
    resolved = base.get("resolved", {})
    items = [{**d, "resolved_version": resolved.get(d["name"])} for d in scan["dependencies"]]
    return {
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
        "limitations": STATIC_LIMITS,
    }


def validate(cap: dict) -> None:
    jsonschema.validate(cap, SCHEMA, format_checker=jsonschema.FormatChecker())
    if SECRET_RE.search(json.dumps(cap)):
        raise ValueError("capsule appears to contain a credential/secret pattern; refusing to write")


def write(cap: dict, outdir: Path) -> tuple[Path, Path]:
    validate(cap)
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    j, m = outdir / "capsule.json", outdir / "capsule.md"
    j.write_text(json.dumps(cap, indent=2), encoding="utf-8")
    m.write_text(to_markdown(cap), encoding="utf-8")
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
    L += ["", "### Suggested tests (model, not run)", ""] + [f"- {t}" for t in c["suggested_tests"]]
    L += ["", "## 5. Compatibility gaps", ""] + [f"- {g}" for g in c["compatibility_gaps"]]
    L += ["", "## 6. Limitations", ""] + [f"- {x}" for x in c["limitations"] + c["usage"]["limitations"]]
    return "\n".join(L) + "\n"
