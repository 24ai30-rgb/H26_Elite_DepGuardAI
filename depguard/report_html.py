"""Self-contained HTML report rendered from a (validated) capsule. All repository-derived text is escaped."""
import json
import html


def e(v):
    """Escape any value (None, numbers, repo-derived text) for HTML."""
    return html.escape(str(v), quote=True)


CSS = """body{font:14px/1.5 system-ui,Segoe UI,sans-serif;background:#0b0d10;color:#e6e9ed;margin:0;padding:24px;max-width:1040px;margin:auto}
h1{font-size:22px}h2{font-size:17px;margin-top:30px;border-bottom:1px solid #222932;padding-bottom:6px}h3{font-size:14px}
table{border-collapse:collapse;width:100%;margin:8px 0}th,td{border:1px solid #222932;padding:6px 10px;text-align:left;vertical-align:top}th{background:#11151a}
pre{background:#0f1318;border:1px solid #222932;padding:10px;overflow:auto;white-space:pre-wrap}code{font-family:ui-monospace,Consolas,monospace}
.tag{font-size:11px;font-weight:600;padding:2px 7px;border-radius:5px;text-transform:uppercase;letter-spacing:.05em;white-space:nowrap}
.obs{color:#58a6ff;background:#0f1b2b;border:1px solid #1f3d63}.ai{color:#b392f0;background:#1a1530;border:1px solid #43386a}
.ver{color:#3fb950;background:#12251a;border:1px solid #24502f}.unk{color:#8b95a1;border:1px solid #2d3540}
.box{border:1px solid #2d3540;border-radius:8px;padding:10px 14px;margin:10px 0}.ai-box{border-color:#43386a;background:#15112a}.add{color:#9be2a8}.del{color:#ffb3af}
a{color:#7c9cff}@media print{body{background:#fff;color:#000}.tag{border:1px solid #000;color:#000;background:#fff}}"""
TAGS = {"obs": "Observed fact", "ai": "AI-generated, unverified", "ver": "Verified by execution", "unk": "Unknown / not checked"}


def tag(k):
    return f'<span class="tag {k}">{TAGS[k]}</span>'


def _diff(text):
    if not text:
        return "<pre>(no diff)</pre>"
    out = []
    for l in text.splitlines():
        cls = "add" if l.startswith("+") and not l.startswith("+++") else "del" if l.startswith("-") and not l.startswith("---") else ""
        out.append(f'<span class="{cls}">{e(l)}</span>' if cls else e(l))
    return "<pre>" + "\n".join(out) + "</pre>"


def _run(title, r):
    if not r or r.get("status") == "not_run":
        return f"<h3>{e(title)}</h3><p>Not run. {tag('unk')}</p>"
    steps = "".join(f"<tr><td><code>{e(' '.join(s['command'][-4:]))}</code></td><td>{e(s['exit_code'])}</td></tr>" for s in r.get("steps", []))
    envi = r.get("environment")
    env = f"<p>Environment: Python {e(envi['python'])} on {e(envi['platform'])}. {e(envi['isolation'])}.</p>" if envi else "<p>Environment details were not recorded for this run.</p>"
    cnt = {k: sum(v == k for v in r.get("tests", {}).values()) for k in ("passed", "failed", "skipped")}
    return (f"<h3>{e(title)} {tag('ver')}</h3><p>Status <b>{e(r['status'])}</b>, pytest exit {e(r.get('exit_code', 'n/a'))}"
            f"{', failure kind ' + e(r['failure_kind']) if r.get('failure_kind') else ''}. Tests: {cnt['passed']} passed, {cnt['failed']} failed, {cnt['skipped']} skipped.</p>"
            f"{env}<table><tr><th>Command</th><th>Exit</th></tr>{steps}</table>")


def to_html(c: dict) -> str:
    repo, dep = c["repository"], c["dependencies"]
    h = c.get("health_score")
    P = [f"<h1>Dependency Exit Capsule: {e(repo['path_name'])}</h1>",
         f"<p>Generated {e(c['generated_at'])} · commit <code>{e(repo['commit'])}</code> · remote {e(repo['remote'] or 'none')} · dirty: {e(repo['dirty'])}</p>",
         '<div class="box">Legend: ' + " ".join(tag(k) for k in TAGS) + "</div>"]
    if h:
        rows = "".join(f"<tr><td>{e(p['package'])}</td><td>{p['score']}</td><td>" + (
            "<br>".join(f"-{d['points']} {e(d['rule'])} <small>({e(d['basis'])})</small>" for d in p["deductions"]) or "no deductions") + "</td></tr>"
            for p in h["packages"])
        P += [f"<h2>Health score {tag('obs')}</h2><div class='box'><b>{h['score']}/100</b> — heuristic. Evidence coverage {h['evidence_coverage']['percent']}%. {e(h['disclaimer'])}</div>",
              f"<table><tr><th>Package</th><th>Score</th><th>Deductions</th></tr>{rows}</table>",
              "<h3>Unchecked (never counted against a package)</h3><ul>" + "".join(f"<li>{e(u['check'])}: {e(u['reason'])}</li>" for u in h["unchecked"]) + "</ul>"]
    items = "".join(f"<tr><td>{e(d['name'])}</td><td><code>{e(d['specifier'] or '-')}</code> {e(d['file'])}:{e(d['line'])}</td>"
                    f"<td>{e(d['resolved_version'] or 'unknown')}</td></tr>" for d in dep["items"])
    P += [f"<h2>Dependencies {tag('obs')}</h2><p>Manifests: {e(', '.join(dep['manifests']))} · requires-python {e(dep['python_requires'])}</p>",
          f"<table><tr><th>Package</th><th>Declared</th><th>Resolved (test env)</th></tr>{items}</table>",
          "<p>Maintenance activity and release recency: "
          + (f"fetched for {len(c['package_metadata'])} package(s), see metadata below." if c.get("package_metadata") else "not checked (no package metadata was fetched).")
          + f" {tag('unk')}</p>", "<h2>Evidence</h2><ul>"]
    P += [f"<li><b>{e(i['package'])}</b> {e(i['indicator'])} ({e(i['severity'])}): {e('; '.join(i['evidence'][:3]))}</li>"
          for i in c["risk_evidence"]["indicators"] if i["severity"] != "info"] + ["</ul>"]
    u = c["usage"]
    P += [f"<h3>Usage {tag('obs')}</h3><ul><li>Undeclared imports: {e(', '.join(u['undeclared_imports']) or 'none')}</li>"
          f"<li>No static import found (heuristic, NOT proof of non-use): {e(', '.join(u['no_import_found']) or 'none')} {tag('unk')}</li>"
          f"<li>Dynamic import sites: {len(u['dynamic_import_sites'])}</li></ul>"]
    for p, locs in u["usages"].items():
        P.append(f"<p><code>{e(p)}</code>: " + e(", ".join(f"{x['file']}:{x['line']}" for x in locs[:8])) + "</p>")
    ai = c["ai_recommendations"]
    P += [f"<h2>AI recommendations {tag('ai')}</h2><div class='box ai-box'>Generated by {e(c['model'].get('model'))} (local). Suggestions only: not facts, no confidence scores are supplied. Status: <b>{e(ai['status'])}</b> {e(ai.get('reason', ''))}</div>"]
    for r in ai.get("recommendations", []):
        P.append(f"<h3>{e(r['package'])}: {e(r['action'])}</h3><p>{e(r['rationale'])}</p><p>Candidates: {e(', '.join(r['replacement_candidates']) or '-')}</p>"
                 "<ol>" + "".join(f"<li>{e(s)}</li>" for s in r["migration_steps"]) + "</ol><ul>" + "".join(f"<li>concern (model-claimed): {e(s)}</li>" for s in r["compatibility_concerns"]) + "</ul>")
    mp = c["migration_proposal"]
    P += [f"<h2>Migration proposal (not applied) {tag('ai')}</h2>{_diff(mp['diff'])}",
          "<ul>" + "".join(f"<li>rejected edit {e(r['file'])}: {e(r['reason'])}</li>" for r in mp["rejected_edits"]) + "</ul>"]
    ex = c["executed_tests"]
    P += ["<h2>Executed tests</h2>", _run("Baseline", ex["baseline"]), _run("AI-proposed edits", ex["proposed"])]
    for x in c.get("independent_experiments", []):
        P += [f"<h2>Independent experiment: {e(x['name'])}</h2><div class='box'>Provenance: {e(x['origin'])}. Not AI-generated. Passing tests do not prove full compatibility.</div>",
              _diff(x["diff"]), _run("Experiment baseline", x["baseline"]), _run("Experiment proposed", x["proposed"]),
              f"<p>Comparison: <code>{e(json.dumps(x['comparison']))}</code></p>"]
    if c.get("package_metadata"):
        P.append(f"<h2>Package metadata {tag('obs')}</h2><table><tr><th>Package</th><th>Status</th><th>Source / time</th></tr>" + "".join(
            f"<tr><td>{e(k)}</td><td>{e(v['status'])} {e(v.get('latest_version') or v.get('error') or '')}</td><td>{e(v.get('source'))} {e(v.get('fetched_at') or v.get('attempted_at'))}</td></tr>"
            for k, v in c["package_metadata"].items()) + "</table>")
    P += ["<h2>Compatibility gaps</h2><ul>" + "".join(f"<li>{e(g)}</li>" for g in c["compatibility_gaps"]) + "</ul>",
          "<h2>Limitations</h2><ul>" + "".join(f"<li>{e(x)}</li>" for x in c["limitations"] + c["usage"]["limitations"]) + "</ul>"]
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f"<title>DepGuard capsule: {e(repo['path_name'])}</title><style>{CSS}</style></head><body>" + "\n".join(P) + "</body></html>")
