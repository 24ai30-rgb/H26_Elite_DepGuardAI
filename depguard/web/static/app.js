"use strict";
/* DepGuard AI dashboard. Plain JS, no build step. Every string from data goes through esc() before innerHTML. */
const $ = (s, r = document) => r.querySelector(s);
const esc = v => String(v ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const S = { data: null, err: null, src: "capsule", route: "overview", busy: false, filter: "", only: false, tab: "summary", proposal: "ai", scanErr: null, scanBusy: false, applyRes: null, applyErr: null };

const ICON = {
  overview: '<path d="M3 3h7v9H3zM14 3h7v5h-7zM14 12h7v9h-7zM3 16h7v5H3z"/>',
  scanner: '<circle cx="11" cy="11" r="7"/><path d="M21 21l-4.300-4.300"/>',
  inventory: '<path d="M4 6h16M4 12h16M4 18h16"/>',
  advisor: '<path d="M12 3l1.800 4.700L18.500 9.500 13.800 11.300 12 16l-1.800-4.700L5.500 9.500l4.700-1.800zM19 15l.8 2.200L22 18l-2.200.8L19 21l-.8-2.200L16 18l2.200-.8z"/>',
  migration: '<circle cx="6" cy="6" r="2.500"/><circle cx="6" cy="18" r="2.500"/><circle cx="18" cy="12" r="2.500"/><path d="M6 8.500v7M8.300 7l7.400 3.600M8.300 17l7.400-3.600"/>',
  verify: '<path d="M9 3h6M10 3v6L4.500 19a2 2 0 0 0 1.800 3h11.400a2 2 0 0 0 1.800-3L14 9V3"/><path d="M7.500 15h9"/>',
  capsule: '<path d="M10.500 3.500a5 5 0 0 1 7 7l-7 10a5 5 0 0 1-7-7z"/><path d="M8 8l8 8"/>',
  linkwatch: '<path d="M3 12h4l3-8 4 16 3-8h4"/>',
  history: '<path d="M3 12a9 9 0 1 0 3-6.700L3 8M3 3v5h5M12 7v5l3 2"/>',
  repair: '<path d="M14.700 6.300a4 4 0 0 1-5 5L4 17l3 3 5.700-5.700a4 4 0 0 0 5-5l-2.500 2.500-2.200-.5-.5-2.200z"/>',
  publish: '<circle cx="6" cy="5" r="2"/><circle cx="6" cy="19" r="2"/><circle cx="18" cy="9" r="2"/><path d="M6 7v10M18 11c0 4-6 3-12 6"/>',
  deps: '<path d="M4 6h16M4 12h16M4 18h10"/>',
  quickscan: '<circle cx="11" cy="11" r="7"/><path d="M21 21l-4.300-4.300M8 11h6"/>',
  compare: '<path d="M8 3L4 7l4 4M4 7h16M16 21l4-4-4-4M20 17H4"/>',
};
const PAGES = [
  ["overview", "Workflow overview", "Workflow"], ["scanner", "1 · Repository"], ["inventory", "2 · Findings"], ["advisor", "3 · Ask Gemma 4"],
  ["repair", "4 · Repair workspace"], ["verify", "5 · Verify"], ["publish", "6 · Publish"], ["capsule", "7 · Reports & capsule"],
  ["deps", "Dependency inventory", "More"], ["compare", "Replacement comparison"], ["history", "Scan history"], ["quickscan", "Quick scan & clone"],
  ["migration", "Experiments (apply demo edits)"], ["linkwatch", "LinkWatch status"],
];
const CORE = ["scanner", "inventory", "advisor", "repair", "verify", "publish", "capsule"];

/* ---------- api ---------- */
async function api(path, body) {
  const opt = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json", "X-DepGuard": "1" }, body: JSON.stringify(body) };
  const r = await fetch(path, opt);
  const j = await r.json().catch(() => ({ error: "invalid server response" }));
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}
async function load(silent) {
  try { S.data = await api("/api/state"); S.err = null; } catch (e) { S.err = e.message; }
  if (!silent) S.busy = false;
  render();
}
function toast(msg, kind) {
  const t = $("#toast"); t.textContent = msg; t.className = "toast " + (kind || ""); t.hidden = false;
  clearTimeout(toast.t); toast.t = setTimeout(() => t.hidden = true, 5000);
}

/* ---------- helpers ---------- */
const fmtTime = s => { if (!s) return "unknown"; const d = new Date(typeof s === "number" ? s * 1000 : s); return isNaN(d) ? esc(s) : d.toLocaleString(); };
const badge = (text, kind) => `<span class="badge b-${kind || "na"}"><i></i>${esc(text)}</span>`;
const stKind = s => ({ passed: "ok", ok: "ok", done: "ok", up: "ok", valid: "ok", failed: "bad", error: "bad", blocked: "bad", invalid_response: "bad", environment_blocked: "warn", not_run: "na", not_requested: "na", skipped: "warn", running: "info", high: "bad", medium: "warn", no_issue_observed: "ok" }[s] || "na");
const stText = s => String(s || "unknown").replace(/_/g, " ");
const sBadge = s => badge(stText(s), stKind(s));
const tag = k => ({ ai: '<span class="tag-ai">AI-generated · unverified</span>', obs: '<span class="tag-obs">Observed fact</span>', ver: '<span class="tag-ver">Verified by execution</span>' }[k]);
const card = (title, body, right, cls) => `<section class="card ${cls || ""}"><div class="card-h"><h2>${title}</h2><span class="right">${right || ""}</span></div>${body}</section>`;
const empty = (t, d, action) => `<div class="empty"><b>${esc(t)}</b>${d ? `<div>${d}</div>` : ""}${action ? `<div style="margin-top:14px">${action}</div>` : ""}</div>`;
const skeleton = () => `<div class="stack"><div class="skel" style="width:30%;height:28px"></div><div class="grid g4">${'<div class="skel" style="height:96px"></div>'.repeat(4)}</div><div class="skel" style="height:240px"></div></div>`;
const notice = (kind, body) => `<div class="notice ${kind}">${body}</div>`;
const cap = () => S.data && S.data.capsule;

/* Normalised dependency view from either the capsule or the live scan. */
function view() {
  const d = S.data; if (!d) return null;
  let scan, use, risk, items, origin;
  if (S.src === "live" && d.scan) { scan = d.scan.scan; use = d.scan.usage; risk = d.scan.risk_evidence; items = scan.dependencies; origin = "Live scan " + fmtTime(d.scan.time); }
  else if (d.capsule) { const c = d.capsule; scan = { ...c.dependencies, dependencies: c.dependencies.items, conflicts: [] }; use = c.usage; risk = c.risk_evidence; items = c.dependencies.items; origin = d.capsule_source.label + " · " + fmtTime(c.generated_at); }
  else return { empty: true };
  const by = {};
  for (const it of items) (by[it.name] ||= { name: it.name, entries: [] }).entries.push(it);
  for (const p of Object.values(by)) {
    p.usages = use.usages[p.name] || [];
    const lv = risk.package_levels[p.name]; p.level = lv ? lv.level : "unknown"; p.contributing = lv ? lv.contributing : [];
    p.indicators = risk.indicators.filter(i => i.package === p.name);
  }
  for (const u of use.undeclared_imports) if (!by[u]) by[u] = { name: u, entries: [], undeclared: true, usages: use.usages[u], level: (risk.package_levels[u] || {}).level || "medium", contributing: ["imported_but_undeclared"], indicators: risk.indicators.filter(i => i.package === u) };
  const pkgs = Object.values(by).sort((a, b) => ({ high: 0, medium: 1 }[a.level] ?? 2) - ({ high: 0, medium: 1 }[b.level] ?? 2) || a.name.localeCompare(b.name));
  return { scan, use, risk, pkgs, origin, live: S.src === "live" && !!d.scan, conflicts: (d.capsule && S.src !== "live") ? risk.indicators.filter(i => i.indicator === "conflicting_constraints") : (scan.conflicts || []) };
}
const atRisk = v => v.pkgs.filter(p => p.level === "high" || p.level === "medium");

/* ---------- charts (inline SVG) ---------- */
function donut(parts, size = 132) {
  const tot = parts.reduce((a, p) => a + p.v, 0) || 1, r = 48, C = 2 * Math.PI * r; let off = 0;
  const segs = parts.filter(p => p.v).map(p => { const len = p.v / tot * C, s = `<circle r="${r}" cx="64" cy="64" fill="none" stroke="${p.c}" stroke-width="14" stroke-dasharray="${len} ${C - len}" stroke-dashoffset="${-off}" transform="rotate(-90 64 64)"/>`; off += len; return s; }).join("");
  return `<svg width="${size}" height="${size}" viewBox="0 0 128 128" role="img" aria-label="Risk distribution"><circle r="${r}" cx="64" cy="64" fill="none" stroke="#1b222b" stroke-width="14"/>${segs}<text x="64" y="62" text-anchor="middle" fill="#e6e9ed" font-size="24" font-weight="600">${tot === 1 && !parts.some(p => p.v) ? 0 : parts.reduce((a, p) => a + p.v, 0)}</text><text x="64" y="80" text-anchor="middle" fill="#8b95a1" font-size="10">packages</text></svg>`;
}
const counts = t => { const v = Object.values(t || {}); return { passed: v.filter(x => x === "passed").length, failed: v.filter(x => x === "failed").length, skipped: v.filter(x => x === "skipped").length }; };
function testBar(label, run, note) {
  const c = counts(run && run.tests), tot = c.passed + c.failed + c.skipped;
  const bar = tot ? `<div class="bar" role="img" aria-label="${c.passed} passed, ${c.failed} failed"><span style="width:${c.passed / tot * 100}%;background:var(--ok)"></span><span style="width:${c.failed / tot * 100}%;background:var(--bad)"></span><span style="width:${c.skipped / tot * 100}%;background:var(--warn)"></span></div>` : `<div class="bar"></div>`;
  return `<div><div class="row" style="justify-content:space-between;margin-bottom:6px"><span>${esc(label)}</span><span>${sBadge(run ? run.status : "not_run")}</span></div>${bar}<div class="tiny muted" style="margin-top:6px">${tot ? `${c.passed} passed · ${c.failed} failed · ${c.skipped} skipped` : "no per-test results recorded"}${note ? " · " + note : ""}</div></div>`;
}

/* ---------- pages ---------- */
function pageHead(title, sub, actions) { return `<div class="page-h"><div><h1>${title}</h1><div class="sub">${sub}</div></div><div class="actions">${actions || ""}</div></div>`; }
function needCapsule(title) {
  return pageHead(title, "") + card("No capsule loaded", empty("No Dependency Exit Capsule available", "Run a full analysis from the scanner page, or load the committed demo capsule.", `<button class="btn primary" data-act="goto" data-to="scanner">Open scanner</button> <button class="btn" data-act="select" data-which="demo">Load demo capsule</button>`));
}

function pkgDrawer(name) {
  const v = view(), p = v.pkgs.find(x => x.name === name); if (!p) return;
  const ai = cap() && cap().ai_recommendations.recommendations ? cap().ai_recommendations.recommendations.find(r => r.package === name) : null;
  const dr = $("#drawer");
  dr.innerHTML = `<div class="panel" role="dialog" aria-label="${esc(name)}"><div class="row"><h1 style="margin:0">${esc(name)}</h1> ${sBadge(p.level)}<button class="btn sm" style="margin-left:auto" data-act="close">Close</button></div>
    <div>${tag("obs")}</div>
    <dl class="kv"><dt>Declared</dt><dd>${p.entries.map(e => `<div class="mono">${esc(e.specifier || "any")} <span class="faint">${esc(e.file)}:${e.line} (${esc(e.kind)})</span></div>`).join("") || "not declared"}</dd>
    <dt>Pinned</dt><dd class="mono">${esc([...new Set(p.entries.map(e => e.pinned_version).filter(Boolean))].join(", ") || "—")}</dd>
    <dt>Resolved</dt><dd class="mono">${esc(p.entries.map(e => e.resolved_version).find(Boolean) || "unknown (no test environment recorded)")}</dd>
    <dt>Package metadata</dt><dd>${metaText(p.name)}<div class="tiny faint">Repository activity (commits, issues) is never checked.</div></dd></dl>
    <div><h3>Usage locations (static)</h3>${p.usages.length ? `<ul class="list">${p.usages.map(u => `<li><span class="mono">${esc(u.file)}:${u.line}</span> <span class="muted">import <code>${esc(u.module)}</code>${u.names.length ? " → " + esc(u.names.join(", ")) : ""}</span></li>`).join("")}</ul>` : '<span class="muted">No static import found. This does not prove the package is unused.</span>'}</div>
    <div><h3>Indicators &amp; evidence</h3>${p.indicators.filter(i => i.severity !== "info").length ? p.indicators.filter(i => i.severity !== "info").map(i => `<div class="notice ${i.severity === "high" ? "bad" : "warn"}" style="margin-bottom:8px"><div><b>${esc(stText(i.indicator))}</b> ${sBadge(i.severity === "warn" ? "medium" : i.severity)}<pre class="tiny" style="margin-top:8px;max-height:160px">${esc(i.evidence.join("\n"))}</pre></div></div>`).join("") : '<span class="muted">No risk indicator triggered from the available evidence.</span>'}</div>
    ${ai ? `<div><h3>Gemma 4 said ${tag("ai")}</h3><div class="notice ai"><div><b>${esc(ai.action)}</b> — ${esc(ai.rationale)}</div></div></div>` : ""}
    <div><a class="btn sm" href="#compare" data-act="cmpfor" data-name="${esc(name)}">Compare replacements…</a></div></div>`;
  dr.hidden = false; $(".panel .btn", dr).focus();
}

function diffHtml(t) {
  if (!t) return '<span class="faint">(no diff)</span>';
  return `<div class="diff">${t.split("\n").map(l => `<span class="l ${l.startsWith("+++") || l.startsWith("---") ? "f" : l.startsWith("@@") ? "h" : l.startsWith("+") ? "a" : l.startsWith("-") ? "d" : ""}">${esc(l) || " "}</span>`).join("")}</div>`;
}
function proposals() {
  const c = cap(); if (!c) return [];
  const out = [{ id: "ai", title: "Gemma 4 combined proposal", kind: "ai", edits: c.migration_proposal.accepted_edits, diff: c.migration_proposal.diff, rejected: c.migration_proposal.rejected_edits, status: c.executed_tests.proposed.status, cmp: c.executed_tests.comparison }];
  (c.independent_experiments || []).forEach((e, i) => out.push({ id: "exp:" + i, title: "Experiment: " + e.name, kind: "ver", edits: e.edits, diff: e.diff, rejected: [], status: e.proposed.status, cmp: e.comparison }));
  return out;
}
function stepRows(steps) {
  return `<div class="tscroll"><table><thead><tr><th>Command</th><th>Exit</th></tr></thead><tbody>${(steps || []).map(s => `<tr><td class="mono tiny" style="overflow-wrap:anywhere">${esc(s.command.slice(-5).join(" "))}</td><td>${s.exit_code === 0 ? badge("0", "ok") : badge(s.exit_code, "bad")}</td></tr>`).join("")}</tbody></table></div>`;
}
function runCard(title, run, extra, id) {
  return `<div id="${id || ""}" class="anchor">${runCardInner(title, run, extra)}</div>`;
}
function runCardInner(title, run, extra) {
  if (!run || run.status === "not_run") return card(title, empty("Not run", "No test run was recorded for this step."));
  const t = counts(run.tests), names = Object.entries(run.tests || {});
  const pk = Object.keys(run.resolved || {}).length;
  return card(title, `<div class="row" style="margin-bottom:12px">${sBadge(run.status)}<span class="mono">pytest exit ${esc(run.exit_code ?? "n/a")}</span><span class="muted">${t.passed} passed · ${t.failed} failed · ${t.skipped} skipped</span></div>
    ${extra || ""}<h3>Commands run</h3>${stepRows(run.steps)}
    <dl class="kv" style="margin:12px 0"><dt>pip check</dt><dd>${run.pip_check ? `exit ${esc(run.pip_check.exit_code)} · ${esc((run.pip_check.output || "").trim().split("\n")[0])}` : "not recorded"}</dd><dt>Installed packages</dt><dd>${pk ? pk + " (versions recorded in capsule)" : "not recorded"}</dd><dt>Failure kind</dt><dd>${run.failure_kind ? `${badge(stText(run.failure_kind), run.failure_kind === "test_failures" ? "bad" : "warn")} <span class="tiny muted">${run.failure_kind === "test_failures" ? "the tests ran and some failed" : "setup/collection problem, not a normal test failure"}</span>` : run.status === "passed" ? "none" : "not recorded"}</dd>
    <dt>Environment</dt><dd>${run.environment ? `Python ${esc(run.environment.python)} · ${esc(run.environment.platform)}${run.environment.venv_pip ? " · " + esc(run.environment.venv_pip) : ""}<div class="tiny muted">${esc(run.environment.isolation)}. ${esc(run.environment.network_needed)}.</div>` : "Details were not recorded for this run (older capsule). Throwaway venv in a temp copy; paths scrubbed (&lt;tmp&gt;)."}</dd></dl>
    ${names.length ? `<details><summary>${names.length} test result(s)</summary><div class="tscroll"><table><tbody>${names.map(([n, s]) => `<tr><td class="mono tiny">${esc(n)}</td><td>${sBadge(s)}</td></tr>`).join("")}</tbody></table></div></details>` : '<div class="tiny faint">No per-test results (collection or environment failure).</div>'}
    <details style="margin-top:8px"><summary>Raw pytest output (tail)</summary><pre>${esc(run.output || "")}</pre></details>`, tag("ver"));
}
function pVerify() {
  const c = cap(); if (!c) return needCapsule("Verification lab");
  const ex = c.executed_tests, e = (c.independent_experiments || [])[0];
  const cmp = (cmpd, t) => cmpd ? card(t, `<div class="grid g3"><div class="stat"><div class="l">Fixed</div><div class="v" style="color:var(--ok)">${cmpd.fixed.length}</div><div class="d">${cmpd.fixed.map(esc).join("<br>") || "—"}</div></div><div class="stat"><div class="l">Regressed</div><div class="v" style="color:${cmpd.regressed.length ? "var(--bad)" : "var(--ok)"}">${cmpd.regressed.length}</div><div class="d">${cmpd.regressed.length > 4 ? cmpd.regressed.length + " tests (see note)" : cmpd.regressed.map(esc).join("<br>") || "—"}</div></div><div class="stat"><div class="l">Counts</div><div class="v" style="font-size:18px">${cmpd.baseline_counts.passed}/${cmpd.baseline_counts.failed} → ${cmpd.proposed_counts.passed}/${cmpd.proposed_counts.failed}</div><div class="d">passed/failed, baseline → proposed</div></div></div>${cmpd.note ? `<div class="tiny muted" style="margin-top:8px">${esc(cmpd.note)}</div>` : ""}`, tag("ver")) : "";
  return pageHead("Verification lab", "Actual commands, exit codes and results. Baseline versus proposed, kept separate for the AI proposal and the independent experiment.") + `<div class="stack">
    ${typeof patchVerifyCard === "function" ? patchVerifyCard() : ""}
    ${ex.proposed.status === "failed" || ex.proposed.status === "environment_blocked" ? notice("bad", `<div><b>The Gemma 4 combined proposal FAILED verification</b> (pytest exit ${esc(ex.proposed.exit_code ?? "n/a")}${ex.proposed.failure_kind ? ", " + esc(stText(ex.proposed.failure_kind)) : ""}). It is not a successful migration and must not be applied as-is. <a href="#verify" data-act="vlab" data-run="run-ai-proposed">Jump to the run →</a></div>`) : ""}
    ${notice("warn", `<div class="tiny"><b>Passing tests do not prove complete compatibility.</b> They only exercise what the project's own test suite covers. A run is marked passed only when pytest actually ran and exited 0; setup or collection failures are shown as such, never as success. Verification uses a virtual environment inside a temporary copy, <b>not a container or sandbox</b>.</div>`)}
    <h2>Independent experiment <span class="muted">— ${e ? esc(e.name) : "none recorded"}</span></h2>
    ${e ? `${cmp(e.comparison, "Comparison: baseline vs Jinja2-only")}<div class="grid g2">${runCard("Baseline (original manifests)", e.baseline, "", "run-exp-0-baseline")}${runCard("Proposed (" + esc(e.name) + " edits)", e.proposed, "", "run-exp-0-proposed")}</div>` : card("Experiment", empty("No independent experiment in this capsule", "Run python -m depguard.experiments.jinja2_only to record one."))}
    <h2 style="margin-top:8px">Gemma 4 proposal <span class="muted">— tested as proposed</span></h2>
    ${cmp(ex.comparison, "Comparison: baseline vs AI proposal")}<div class="grid g2">${runCard("Baseline", ex.baseline, "", "run-ai-baseline")}${runCard("AI-proposed edits", ex.proposed, "", "run-ai-proposed")}</div>
    ${card("Limitations of this verification", `<ul class="list tiny">${c.limitations.map(l => `<li><span class="dot"></span>${esc(l)}</li>`).join("")}<li><span class="dot"></span>Passing tests do not prove compatibility; only the suite's coverage is exercised.</li></ul>`)}</div>`;
}

function md(src) {
  const lines = src.split("\n"); let out = "", i = 0;
  const inline = t => esc(t).replace(/`([^`]+)`/g, "<code>$1</code>").replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>");
  while (i < lines.length) {
    const l = lines[i];
    if (l.startsWith("```")) { const buf = []; i++; while (i < lines.length && !lines[i].startsWith("```")) buf.push(lines[i++]); i++; out += `<pre>${/^diff/.test(l.slice(3)) ? diffHtml(buf.join("\n")) : esc(buf.join("\n"))}</pre>`; continue; }
    if (/^#{1,3} /.test(l)) { const n = l.match(/^#+/)[0].length; out += `<h${n}>${inline(l.replace(/^#+ /, ""))}</h${n}>`; i++; continue; }
    if (l.startsWith("|")) { const rows = []; while (i < lines.length && lines[i].startsWith("|")) rows.push(lines[i++]); const cells = r => r.split("|").slice(1, -1).map(x => x.trim()); out += `<table><thead><tr>${cells(rows[0]).map(x => `<th>${inline(x)}</th>`).join("")}</tr></thead><tbody>${rows.slice(2).map(r => `<tr>${cells(r).map(x => `<td>${inline(x)}</td>`).join("")}</tr>`).join("")}</tbody></table>`; continue; }
    if (/^\s*(- |\d+\. )/.test(l)) { const ord = /^\s*\d/.test(l), buf = []; while (i < lines.length && /^\s*(- |\d+\. )/.test(lines[i])) buf.push(lines[i++].replace(/^\s*(- |\d+\. )/, "")); out += `<${ord ? "ol" : "ul"}>${buf.map(x => `<li>${inline(x)}</li>`).join("")}</${ord ? "ol" : "ul"}>`; continue; }
    out += l.trim() ? `<p>${inline(l)}</p>` : ""; i++;
  }
  return out;
}
function pLink() {
  const l = S.data.linkwatch;
  if (!l.available || !l.results.length) return pageHead("LinkWatch status", "Latest actual site-check results from the LinkWatch cache.") + card("No results", empty("No LinkWatch results found", `Run a check to create them:<br><code>python -m linkwatch.cli check</code><br><span class="tiny faint">Looked in ${esc(l.cache_file)}${l.error ? " — " + esc(l.error) : ""}</span>`));
  const up = l.results.filter(r => r.up).length;
  return pageHead("LinkWatch status", `Latest results from LinkWatch's cache, written by <code>linkwatch check</code>. Not simulated.`) + `<div class="stack"><div class="grid g3">
    <div class="card stat ${up === l.results.length ? "ok" : "bad"}"><div class="l">Sites up</div><div class="v">${up}/${l.results.length}</div></div>
    <div class="card stat"><div class="l">Slowest response</div><div class="v">${Math.max(...l.results.map(r => r.elapsed_ms || 0))} ms</div></div>
    <div class="card stat"><div class="l">Cache file updated</div><div class="v" style="font-size:16px;padding:6px 0">${fmtTime(l.file_modified)}</div><div class="d mono" style="overflow-wrap:anywhere">${esc(l.cache_file)}</div></div></div>
    ${card("Sites", `<div class="tscroll"><table><thead><tr><th>Site</th><th>Status</th><th>HTTP</th><th>Response</th><th>Title</th><th>Checked at</th></tr></thead><tbody>${l.results.map(r => `<tr><td><b>${esc(r.name)}</b><div class="tiny faint">${esc(r.url)}</div></td><td>${r.up ? badge("UP", "ok") : badge("DOWN", "bad")}</td><td class="mono">${esc(r.status ?? "—")} ${esc(r.reason || "")}</td><td class="mono">${r.elapsed_ms != null ? esc(r.elapsed_ms) + " ms" : "—"}</td><td>${esc(r.title || "—")}</td><td class="tiny">${fmtTime(r.checked_at)}</td></tr>`).join("")}</tbody></table></div>`, "", "tight")}</div>`;
}

const RENDER = { overview: pOverview, scanner: pScanner, inventory: pFindings, deps: pInventory, advisor: pAdvisor, repair: pRepair, publish: pPublish, quickscan: pQuickScan, migration: pMigration, verify: pVerify, compare: pCompare, history: pHistory, capsule: pCapsule, linkwatch: pLink };

/* ---------- shell ---------- */
function render() {
  const nav = $("#nav"); let h = "";
  for (const [id, label, group] of PAGES) {
    if (group) h += `<div class="nav-h">${group}</div>`;
    h += `<div class="nav"><a href="#${id}" class="${S.route === id ? "on" : ""}" ${S.route === id ? 'aria-current="page"' : ""}><svg viewBox="0 0 24 24">${ICON[id] || ICON.overview}</svg>${label}</a></div>`;
  }
  nav.innerHTML = h;
  const d = S.data, page = PAGES.find(p => p[0] === S.route);
  $("#crumbs").innerHTML = d ? `<span>${esc((d.capsule ? d.capsule.repository.path_name : "workspace"))}</span><span class="faint">/</span><b>${page[1]}</b>` : `<b>DepGuard AI</b>`;
  $("#topright").innerHTML = d ? `${d.git.branch ? `<span class="badge b-na mono">⎇ ${esc(d.git.branch)} @ ${esc(d.git.commit)}${d.git.dirty ? " ●" : ""}</span>` : ""}<span class="badge ${d.model.available ? "b-ai" : "b-bad"}" title="${esc(d.model.blocker || d.model.access)}"><i></i>${d.model.available ? esc(d.model.model) + " ready" : "Gemma 4 unavailable"}</span>${d.network.offline_flag ? '<span class="badge b-warn" title="The package index is never contacted"><i></i>offline mode</span>' : ""}${d.job && d.job.status === "running" ? '<span class="badge b-info"><span class="spin"></span>analysis running</span>' : ""}` : "";
  const v = $("#view");
  if (S.err) v.innerHTML = notice("bad", `<div><b>Cannot reach the DepGuard server.</b> ${esc(S.err)}<div style="margin-top:8px"><button class="btn" data-act="reload">Retry</button></div></div>`);
  else if (!d) v.innerHTML = skeleton();
  else {
    const keep = document.activeElement && document.activeElement.id;
    v.innerHTML = (CORE.includes(S.route) ? stepper(S.route) : "") + RENDER[S.route]();
    if (keep && $("#" + keep)) { const el = $("#" + keep); el.focus(); if (el.setSelectionRange && el.type === "text") try { el.setSelectionRange(el.value.length, el.value.length); } catch (_) { } }
  }
  $("#sidebar").classList.remove("open");
  if (S.scrollTo) { const id = S.scrollTo; S.scrollTo = null; requestAnimationFrame(() => { const el = document.getElementById(id); if (el) { el.querySelectorAll("details").forEach(d => { if (/Raw pytest output/.test(d.querySelector("summary").textContent)) d.open = true; }); el.scrollIntoView({ block: "start" }); el.classList.add("flash"); setTimeout(() => el.classList.remove("flash"), 2200); } }); }
  clearTimeout(render.poll);
  if (d && d.job && d.job.status === "running") render.poll = setTimeout(() => load(true), 3000);
}
function route() { const r = (location.hash || "#overview").slice(1); S.route = RENDER[r] ? r : "overview"; render(); window.scrollTo(0, 0); }

document.addEventListener("click", async ev => {
  const t = ev.target.closest("[data-act]"); if (!t) { if (ev.target.id === "drawer") $("#drawer").hidden = true; return; }
  const a = t.dataset.act; if (a === "pkg" || a === "goto") ev.preventDefault();
  try {
    if (a === "reload") await load();
    else if (a === "goto") { if (t.dataset.live) S.src = "live"; location.hash = t.dataset.to; }
    else if (a === "pkg") pkgDrawer(t.dataset.name);
    else if (a === "close") $("#drawer").hidden = true;
    else if (a === "tab") { S.tab = t.dataset.tab; render(); }
    else if (a === "prop") { S.proposal = t.dataset.id; S.applyErr = S.applyRes = null; render(); }
    else if (a === "select") { await api("/api/capsule/select", { which: t.dataset.which }); S.applyErr = S.applyRes = null; await load(); toast("Capsule source changed", "ok"); }
    else if (a === "scan") {
      S.path = $("#repo-path").value; S.scanBusy = true; S.scanErr = null; render();
      try { await api("/api/scan", { path: S.path }); S.src = "live"; toast("Scan complete", "ok"); } catch (e) { S.scanErr = e.message; }
      S.scanBusy = false; await load();
    } else if (a === "analyze") {
      S.optTests = $("#opt-tests").checked; S.optAi = $("#opt-ai").checked; S.path = $("#repo-path").value;
      if (!confirm(`Run a full analysis on:\n${S.path}\n\nThis installs the repo's requirements into a temporary venv and runs its tests${S.optAi ? ", then queries local Gemma 4" : ""}. It does not modify the repository. Continue?`)) return;
      const norm = p => String(p || "").replace(/\\/g, "/");
      const downloaded = (S.data.acquired || []).some(a => norm(S.path).startsWith(norm(a.holder)));
      if (downloaded && !confirm("This repository was DOWNLOADED from GitHub.\n\nA full analysis installs its requirements and runs its tests. That EXECUTES code from the repository with your user rights. A virtual environment is not a sandbox.\n\nOnly continue if you trust it. Run it anyway?")) return;
      await api("/api/analyze", { path: S.path, tests: S.optTests, ai: S.optAi, run_untrusted: downloaded }); toast("Analysis started"); await load(true);
    } else if (a === "apply") {
      const p = proposals().find(x => x.id === S.proposal) || proposals()[0]; S.workdir = $("#workdir").value;
      if (!confirm(`Apply "${p.title}" to:\n${S.workdir}\n\nFiles will be modified (originals backed up to .depguard-backup/). Nothing is committed. Continue?`)) return;
      try { S.applyRes = await api("/api/apply", { proposal: p.id, workdir: S.workdir, confirm: $("#confirm").value }); S.applyErr = null; toast("Changes applied (backed up)", "ok"); } catch (e) { S.applyErr = e.message; S.applyRes = null; }
      render();
    } else if (a === "dl") {
      const c = cap(), isJ = t.dataset.what === "json", b = new Blob([isJ ? JSON.stringify(c, null, 2) : S.data.capsule_markdown], { type: isJ ? "application/json" : "text/markdown" });
      const l = document.createElement("a"); l.href = URL.createObjectURL(b); l.download = isJ ? "capsule.json" : "capsule.md"; l.click(); URL.revokeObjectURL(l.href);
    }
  } catch (e) { toast(e.message, "bad"); }
});
document.addEventListener("input", ev => {
  if (ev.target.id === "flt") { S.filter = ev.target.value; render(); }
  if (ev.target.id === "confirm") $("#apply-btn").disabled = ev.target.value !== "APPLY";
});
document.addEventListener("change", ev => {
  if (ev.target.id === "only") { S.only = ev.target.checked; render(); }
  if (ev.target.id === "src") { S.src = ev.target.value; render(); }
});
document.addEventListener("keydown", ev => { if (ev.key === "Escape") $("#drawer").hidden = true; if (ev.key === "Enter" && ev.target.matches("tr.click")) ev.target.click(); });
$("#menu").addEventListener("click", () => $("#sidebar").classList.toggle("open"));
window.addEventListener("hashchange", route);
route(); load();
