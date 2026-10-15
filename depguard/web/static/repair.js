"use strict";
/* Project workflow pages: 1 Repository, 2 Findings, 4 Repair workspace, 6 Publish. Loaded before app.js (uses S, esc, api, card, badge, ... at call time).
   Every value from the server or the repository goes through esc() before it reaches innerHTML. */

const SEV = ["critical", "high", "medium", "low"];
const sevKind = { critical: "bad", high: "bad", medium: "warn", low: "na" };
const sevBadge = s => `<span class="badge b-${sevKind[s] || "na"} sev-${esc(s)}"><i></i>${esc(s)}</span>`;
const kindTag = k => ({ observed: '<span class="tag-obs">Observed</span>', metadata: '<span class="tag-obs">Package metadata</span>', heuristic: '<span class="tag-heur">Heuristic</span>', unknown: '<span class="tag-unk">Unknown</span>' }[k] || "");
const decKind = { open: "na", ignored: "na", review_later: "info", repair_proposed: "ai", repair_failed: "bad", repaired: "ok" };
const decBadge = s => badge(stText(s), decKind[s] || "na");
const patchKind = { proposed: "ai", accepted_for_verification: "info", verifying: "info", verified: "ok", partially_verified: "warn", failed: "bad", blocked: "warn", applied: "ok", rejected: "na", stale: "warn" };
const patchBadge = s => badge(stText(s), patchKind[s] || "na");
const originTag = o => o === "ai" ? tag("ai") : '<span class="tag-obs">Deterministic</span>';
const findingsOf = () => (cap() && cap().findings) || null;
const patchesOf = () => (cap() && cap().repair && cap().repair.patches) || [];
const projectOf = () => S.data.project;

function jobCard() {
  const j = S.data.job; if (!j) return "";
  if (j.status === "running") return notice("obs", `<div><span class="spin"></span> <b>${esc(j.label || j.kind)}</b> <span class="tiny muted">running for ${Math.round(Date.now() / 1000 - j.started)} s.</span>${j.phase ? `<div class="tiny" style="margin-top:4px">Now: ${esc(j.phase)}</div>` : ""}<div class="tiny faint" style="margin-top:4px">Gemma and test runs can take minutes; this page refreshes by itself. Only one operation runs at a time.</div></div>`);
  if (j.status === "error") return notice("bad", `<div><b>${esc(j.label || j.kind)} failed.</b><div class="mono tiny">${esc(j.error)}</div></div>`);
  return "";
}
async function apiRaw(path, blob, headers) {
  const r = await fetch(path, { method: "POST", headers: { "X-DepGuard": "1", "Content-Type": "application/zip", ...headers }, body: blob });
  const j = await r.json().catch(() => ({ error: "invalid server response" }));
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}
const methodLabel = m => ({ zip: "ZIP upload", local_git: "Local Git repository", local_folder: "Local folder", github: "GitHub", github_clone: "GitHub clone" }[m] || m);

/* ================= 1 · Repository ================= */
function pScanner() {
  const d = S.data, P = d.project, gh = d.github, tab = S.inTab || "zip";
  const tabs = `<div class="tabs" role="tablist">${[["zip", "Upload ZIP"], ["local", "Local repository"], ["github", "GitHub"]].map(([k, t]) => `<button role="tab" class="${tab === k ? "on" : ""}" data-act="inTab" data-tab="${k}">${t}</button>`).join("")}</div>`;
  let body = "";
  if (tab === "zip") body = `<div class="muted" style="margin-bottom:10px">The archive is validated before anything is extracted (path traversal, symlinks, size, entry count, compression ratio) and then unpacked into an isolated temporary workspace. Nothing in it is executed.</div>
    <div class="row"><input type="file" id="zip-file" accept=".zip,application/zip" aria-label="ZIP archive"><button class="btn primary" data-act="zipUpload" ${S.inBusy ? "disabled" : ""}>${S.inBusy ? '<span class="spin"></span>' : ""}Upload &amp; open</button></div>
    <div class="tiny muted" style="margin-top:8px">Limit ${esc(d.limits.max_zip_mb)} MB. A ZIP may not include Git history, so the revision is identified by a content fingerprint instead of a commit.</div>`;
  else if (tab === "local") body = `<div class="muted" style="margin-bottom:10px">Choose a repository inside an authorized root. DepGuard reads it and makes a <b>copy of the working tree, including your uncommitted changes</b>, for any proposed change. Your original files are never modified.</div>
    <div class="row"><input id="local-path" class="grow mono" list="roots" value="${esc(S.localPath ?? d.project_root)}" aria-label="Local repository path" spellcheck="false"><datalist id="roots">${d.allowed_roots.map(r => `<option value="${esc(r)}">`).join("")}</datalist><button class="btn primary" data-act="openLocal" ${S.inBusy ? "disabled" : ""}>Open</button></div>
    <div class="tiny muted" style="margin-top:8px">Authorized roots: ${d.allowed_roots.map(r => `<code>${esc(r)}</code>`).join(", ")}. Add more with <code>--allow PATH</code>.</div>`;
  else {
    const R = S.ghRepo;
    body = `<div class="muted" style="margin-bottom:10px">Public repositories need no token. To read private repositories or to publish, connect a <b>fine-grained token limited to the one repository</b> with <i>Contents</i> and <i>Pull requests</i> read/write. The token is kept <b>in memory only</b>, sent only to api.github.com, and never shown or stored.</div>
      ${d.network.offline_flag ? notice("warn", `<div class="tiny">Offline mode is on: GitHub is not contacted.</div>`) : ""}
      <div class="row" style="margin-bottom:10px">${gh.authenticated ? `${badge("connected as " + gh.login, "ok")}<button class="btn sm" data-act="ghLogout">Disconnect</button>`
        : `<input id="gh-token" type="password" class="grow mono" placeholder="GitHub token (optional for public repositories)" autocomplete="off" spellcheck="false" aria-label="GitHub token"><button class="btn" data-act="ghAuth">Connect</button>`}</div>
      <div class="row"><input id="gh-repo" class="grow mono" placeholder="owner/repo  or  https://github.com/owner/repo" value="${esc(S.ghInput ?? "rishrek/LinkWatch")}" aria-label="GitHub repository" spellcheck="false"><button class="btn" data-act="ghLoad" ${S.inBusy ? "disabled" : ""}>Load branches</button></div>
      ${R ? `<div class="row" style="margin-top:10px"><span>${esc(R.full_name)} ${R.private ? badge("private", "warn") : badge("public", "info")}</span><select id="gh-branch" aria-label="Branch">${R.branches.map(b => `<option value="${esc(b.name)}" ${b.name === (S.ghBranch || R.default_branch) ? "selected" : ""}>${esc(b.name)} @ ${esc(b.sha.slice(0, 7))}</option>`).join("")}</select>
        <button class="btn primary" data-act="ghOpen" ${S.inBusy ? "disabled" : ""}>${S.inBusy ? '<span class="spin"></span>' : ""}Open this commit</button></div>
        <div class="tiny muted" style="margin-top:6px">The exact commit SHA is recorded so every finding is traceable to the analysed revision. Rate limits apply (60 requests/hour without a token).</div>` : ""}`;
  }
  const open = card("Open a repository", `${tabs}${body}${S.inErr ? notice("bad", `<div style="margin-top:10px"><b>Could not open it.</b> ${esc(S.inErr)}</div>`) : ""}`, tag("obs"));

  let proj = card("Active repository", empty("No repository open", "Upload a ZIP, open a local repository, or connect to GitHub. Findings, repairs and the capsule all belong to the open repository."));
  if (P) {
    const i = P.input;
    proj = card(`Active repository: ${esc(P.label)}`, `<dl class="kv"><dt>Source</dt><dd>${badge(methodLabel(P.method), "info")} ${P.untrusted ? badge("foreign code: not executed by a scan", "warn") : ""}</dd>
      <dt>Revision</dt><dd class="mono">${i.commit ? esc(i.commit) : '<span class="muted">no commit (content fingerprint)</span>'} ${i.branch ? `<span class="faint">on ${esc(i.branch)}</span>` : ""}<div class="tiny faint">content fingerprint ${esc(i.fingerprint)}</div></dd>
      <dt>Working tree</dt><dd>${i.dirty === true ? `${badge("uncommitted changes present", "warn")} <span class="tiny muted">${(i.modified_files || []).length} modified, ${(i.untracked_files || []).length} untracked. They are preserved: DepGuard works on a copy.</span>` : i.dirty === false ? "clean" : '<span class="muted">not a Git working tree</span>'}</dd>
      <dt>Path</dt><dd class="mono tiny" style="overflow-wrap:anywhere">${esc(P.root)}</dd></dl>
      ${(i.notes || []).length ? `<ul class="list flow tiny">${i.notes.map(n => `<li>• ${esc(n)}</li>`).join("")}</ul>` : ""}
      <div class="row" style="margin-top:10px"><button class="btn sm" data-act="projRm" data-id="${esc(P.id)}">Close &amp; delete the extracted copy</button>${d.projects.filter(x => x.id !== P.id).map(x => `<button class="btn sm" data-act="projSel" data-id="${esc(x.id)}">Switch to ${esc(x.label)}</button>`).join("")}</div>`, P.has_capsule ? tag("obs") : "");
  }
  const run = P ? card("Analyse (read-only)", `<div class="muted" style="margin-bottom:10px">Detects dependencies, usage and risks, maps the impact on modules and tests, asks local Gemma 4 for advice on every at-risk finding, and builds the capsule. <b>It changes no file and proposes no patch.</b></div>
    <div class="row"><label class="chk"><input type="checkbox" id="opt-tests" ${S.optTests === false ? "" : "checked"}> Run the project's tests for a baseline (installs requirements; executes its code)</label>
    <label class="chk"><input type="checkbox" id="opt-ai" ${S.optAi === false ? "" : "checked"} ${d.model.available ? "" : "disabled"}> Ask Gemma 4 (${esc(d.model.model)})</label>
    <label class="chk"><input type="checkbox" id="opt-meta" ${S.optMeta ? "checked" : ""} ${d.network.offline_flag ? "disabled" : ""}> Fetch package metadata, listed advisories and yanked status (network; cached with source and time)</label>
    <button class="btn primary" data-act="runAnalysis" ${d.job && d.job.status === "running" ? "disabled" : ""}>Run analysis…</button></div>
    ${d.model.available ? "" : notice("warn", `<div class="tiny">Gemma 4 unavailable: ${esc(d.model.blocker)}. The analysis can still run without AI; nothing will pretend to be AI output.</div>`)}
    ${P.has_capsule ? `<div style="margin-top:10px"><button class="btn" data-act="goto" data-to="inventory">Review the findings →</button></div>` : ""}`) : "";
  return pageHead("1 · Repository", "Choose how to bring in a repository, then analyse it. Every later step works on a copy; the original is never changed.") + `<div class="stack">${jobCard()}${open}${proj}${run}</div>`;
}

/* ================= 2 · Findings ================= */
function pFindings() {
  const c = cap(), d = S.data;
  if (!c) return pageHead("2 · Findings", "") + card("Findings", empty("No analysis yet", "Open a repository and run an analysis first.", `<button class="btn primary" data-act="goto" data-to="scanner">Open a repository</button>`));
  const F = findingsOf();
  if (!F) return pageHead("2 · Findings", "") + notice("warn", `<div><b>This capsule has no findings section.</b> It was made by an older version. Run a new analysis to get findings with Repair / Ignore / Review later, or look at the dependency inventory.<div style="margin-top:8px"><button class="btn" data-act="goto" data-to="deps">Dependency inventory</button> <button class="btn primary" data-act="goto" data-to="scanner">Open a repository</button></div></div>`);
  const sev = S.fSev || "all", st = S.fStatus || "all", txt = (S.fText || "").toLowerCase(), sort = S.fSort || "severity";
  let rows = F.filter(f => (sev === "all" || f.severity === sev) && (st === "all" || f.status === st) && (!txt || f.package.includes(txt) || f.title.toLowerCase().includes(txt)));
  const rank = f => SEV.indexOf(f.severity);
  rows = rows.slice().sort(sort === "package" ? (a, b) => a.package.localeCompare(b.package) : sort === "status" ? (a, b) => a.status.localeCompare(b.status) || rank(a) - rank(b) : (a, b) => rank(a) - rank(b) || a.package.localeCompare(b.package));
  const cnt = k => F.filter(f => f.severity === k).length;
  const summary = `<div class="sumgrid">${SEV.map(k => `<div class="card stat sum ${k === "critical" || k === "high" ? (cnt(k) ? "bad" : "") : k === "medium" && cnt(k) ? "warn" : ""}"><div class="l">${k}</div><div class="v">${cnt(k)}</div><div class="d">${k === "critical" ? "advisory listed for the exact version" : k === "high" ? "breaks tests / unsupported / yanked" : k === "medium" ? "conflicts, undeclared, private API" : "signals only, uncertain"}</div></div>`).join("")}
    <div class="card stat sum"><div class="l">Decisions</div><div class="v" style="font-size:16px;padding:6px 0">${F.filter(f => f.status === "open").length} open</div><div class="d">${F.filter(f => f.status === "ignored").length} ignored · ${F.filter(f => f.status === "review_later").length} later · ${F.filter(f => f.status === "repaired").length} repaired</div></div></div>`;
  const ctl = `<div class="row" style="margin:14px 0"><input id="f-text" class="grow" placeholder="Filter by package or title…" value="${esc(S.fText || "")}" aria-label="Filter findings">
    <select id="f-sev" aria-label="Severity"><option value="all">All severities</option>${SEV.map(k => `<option ${sev === k ? "selected" : ""}>${k}</option>`).join("")}</select>
    <select id="f-status" aria-label="Status"><option value="all">All statuses</option>${["open", "ignored", "review_later", "repair_proposed", "repair_failed", "repaired"].map(k => `<option value="${k}" ${st === k ? "selected" : ""}>${stText(k)}</option>`).join("")}</select>
    <select id="f-sort" aria-label="Sort"><option value="severity" ${sort === "severity" ? "selected" : ""}>Sort: severity</option><option value="package" ${sort === "package" ? "selected" : ""}>Sort: package</option><option value="status" ${sort === "status" ? "selected" : ""}>Sort: status</option></select></div>`;
  const sel = S.selF || (S.selF = new Set());
  const table = rows.length ? `<div class="tscroll"><table><thead><tr><th></th><th>Severity</th><th>Dependency</th><th>Finding</th><th>Evidence</th><th>Impact</th><th>Status</th></tr></thead><tbody>${rows.map(f => {
    const kinds = ["observed", "metadata", "heuristic"].map(k => [k, f.evidence.filter(e => e.kind === k).length]).filter(x => x[1]);
    return `<tr class="click" data-act="fOpen" data-id="${esc(f.id)}" tabindex="0"><td><input type="checkbox" data-act="fSel" data-id="${esc(f.id)}" ${sel.has(f.id) ? "checked" : ""} aria-label="Select ${esc(f.package)}"></td>
      <td>${sevBadge(f.severity)}</td><td><b>${esc(f.package)}</b><div class="tiny faint mono">${esc(f.detected_version.used_for_checks || "version unknown")}</div></td>
      <td>${esc(f.title)}<div class="tiny faint">${f.unknown.length} unknown/unchecked item(s)</div></td>
      <td class="tiny">${kinds.map(([k, n]) => `${kindTag(k)} ×${n}`).join(" ")}</td>
      <td class="tiny">${f.impact ? `${(f.impact.affected_files || []).length} file(s) · ${(f.impact.tests_direct || []).length + (f.impact.tests_indirect || []).length} test file(s)` : "—"}</td><td>${decBadge(f.status)}</td></tr>`;
  }).join("")}</tbody></table></div>` : empty("No findings match", "Clear the filters to see all.");
  const mb = F.length ? card("Prepare fixes automatically (Mode B)", `<div class="muted" style="margin-bottom:8px">For the findings ticked above: Gemma proposes a fix, DepGuard validates it, tests it in isolation, and may revise it after a failure (limit <b>${esc((d.repair_settings || {}).max_attempts || 2)}</b> attempts per finding). <b>Nothing is applied</b>: you review every diff and approve each fix yourself.</div>
    <div class="row"><label class="chk"><input type="checkbox" id="enB" ${S.enB ? "checked" : ""}> I enable automatic preparation for the ${sel.size} selected finding(s)</label><button class="btn" data-act="prepareB" ${sel.size && S.enB && !(d.job && d.job.status === "running") ? "" : "disabled"}>Prepare fixes</button>
    <label class="tiny muted">attempt limit <select id="attLimit" aria-label="Attempt limit">${[1, 2, 3, 4, 5].map(n => `<option ${n === ((d.repair_settings || {}).max_attempts || 2) ? "selected" : ""}>${n}</option>`).join("")}</select></label></div>`) : "";
  return pageHead("2 · Findings", `${F.length} at-risk dependencies from ${esc(d.capsule_source.label)}. Detection never changes a file. Choose <b>Repair</b>, <b>Ignore</b> or <b>Review later</b> for each.`, `<button class="btn" data-act="goto" data-to="deps">Dependency inventory</button>`)
    + `<div class="stack">${jobCard()}${summary}${ctl}${card("Findings", table, tag("obs"), "tight")}${mb}</div>`;
}

function findingDrawer(id) {
  const c = cap(), f = (findingsOf() || []).find(x => x.id === id); if (!f) return;
  const rec = c.ai_recommendations && c.ai_recommendations.recommendations ? c.ai_recommendations.recommendations.find(r => r.package.toLowerCase() === f.package) : null;
  const ps = patchesOf().filter(p => p.finding_id === f.id);
  const imp = f.impact || {};
  const dr = $("#drawer");
  dr.innerHTML = `<div class="panel" role="dialog" aria-label="${esc(f.package)}"><div class="row"><h1 style="margin:0;font-size:18px">${esc(f.title)}</h1><button class="btn sm" style="margin-left:auto" data-act="close">Close</button></div>
    <div class="row">${sevBadge(f.severity)} ${decBadge(f.status)} <span class="tiny muted">severity because: ${f.severity_basis.map(x => esc(stText(x))).join(", ")}</span></div>
    ${f.decision && f.decision.reason ? notice("obs", `<div class="tiny"><b>Your note:</b> ${esc(f.decision.reason)}</div>`) : ""}
    <div><h3>Why it matters</h3><ul class="list flow tiny">${f.why_it_matters.map(w => `<li>• ${esc(w)}</li>`).join("")}</ul></div>
    <div><h3>Evidence</h3>${f.evidence.map(e => `<div class="notice ${e.kind === "heuristic" ? "warn" : "obs"}" style="margin-bottom:8px"><div class="tiny">${kindTag(e.kind)} <b>${esc(stText(e.rule))}</b><div style="margin-top:4px">${esc(e.text)}</div><div class="faint" style="margin-top:4px">source: ${esc(e.source)}${e.retrieved_at ? ` · retrieved ${esc(e.retrieved_at)}` : ""}</div></div></div>`).join("")}</div>
    <dl class="kv"><dt>Versions</dt><dd class="mono tiny">declared ${esc((f.detected_version.declared || []).join(", ") || "—")} · pinned ${esc(f.detected_version.pinned || "—")} · resolved ${esc(f.detected_version.resolved || "—")}</dd><dt>Declared in</dt><dd class="mono tiny">${esc((f.declared_in || []).join(", ") || "not declared")}</dd></dl>
    <div><h3>Usage and impact (static)</h3>${(f.usage || []).length ? `<ul class="list flow tiny">${f.usage.map(u => `<li class="mono">${esc(u.file)}:${u.line} <span class="muted">${esc(u.module || "")}${u.names && u.names.length ? " → " + esc(u.names.join(", ")) : ""}${u.optional ? " (optional import)" : ""}</span></li>`).join("")}</ul>` : '<div class="tiny muted">No static import found. This is not proof of non-use.</div>'}
      ${imp.summary ? `<div class="tiny" style="margin-top:6px">${esc(imp.summary)}</div>` : ""}
      ${(imp.tests_indirect || []).length || (imp.tests_direct || []).length ? `<div class="tiny muted" style="margin-top:6px">Tests that reach it: ${[...(imp.tests_direct || []), ...(imp.tests_indirect || []).map(t => t.file)].map(esc).join(", ")}</div>` : ""}
      ${(imp.uncertainty || []).length ? `<details style="margin-top:8px"><summary>Why this may be incomplete (${imp.uncertainty.length})</summary><div class="dbody"><ul>${imp.uncertainty.map(u => `<li>${esc(u)}</li>`).join("")}</ul></div></details>` : ""}</div>
    <div><h3>Unknown or not checked</h3><ul class="list flow tiny">${f.unknown.map(u => `<li>• ${esc(u)}</li>`).join("") || "<li>none recorded</li>"}</ul></div>
    ${rec || (f.candidates || []).length ? `<div><h3>Replacement candidates and migration ideas ${tag("ai")}</h3>${(f.candidates || []).length ? `<div class="row">${f.candidates.map(x => `<span class="badge b-ai">${esc(x.name)}</span>`).join("")}</div>` : ""}${rec ? `<p class="tiny">${esc(rec.rationale)}</p>${rec.migration_steps.length ? `<ol class="tiny" style="padding-left:18px">${rec.migration_steps.map(s => `<li>${esc(s)}</li>`).join("")}</ol>` : ""}${rec.compatibility_concerns.length ? `<div class="tiny muted">Compatibility concerns (model-claimed): ${rec.compatibility_concerns.map(esc).join("; ")}</div>` : ""}` : ""}
      <div class="tiny faint" style="margin-top:6px">Generated by the model; unverified until tested. <a href="#compare" data-act="cmpfor" data-name="${esc(f.package)}">Compare replacements →</a></div></div>` : ""}
    <div><h3>Verification requirements</h3><ul class="list flow tiny">${f.verification_requirements.map(v => `<li>• ${esc(v)}</li>`).join("")}</ul></div>
    ${ps.length ? `<div><h3>Patches for this finding</h3>${ps.map(p => `<div class="row tiny"><span class="mono">${esc(p.id)}</span> ${patchBadge(p.status)} <span class="muted">${esc((p.verification || {}).status || "not tested")}</span> <a href="#repair" data-act="goto" data-to="repair">open →</a></div>`).join("")}</div>` : ""}
    <div class="card" style="padding:12px"><h3>Your decision</h3>
      <div class="row"><button class="btn primary" data-act="fRepair" data-id="${esc(f.id)}" ${S.data.job && S.data.job.status === "running" ? "disabled" : ""}>Repair…</button><button class="btn" data-act="fLater" data-id="${esc(f.id)}">Review later</button>${f.status !== "open" ? `<button class="btn" data-act="fReopen" data-id="${esc(f.id)}">Reopen</button>` : ""}</div>
      <div class="row" style="margin-top:8px"><input id="ign-reason" class="grow" placeholder="Optional reason for ignoring" aria-label="Reason for ignoring"><button class="btn" data-act="fIgnore" data-id="${esc(f.id)}">Ignore</button></div>
      <div class="tiny muted" style="margin-top:8px"><b>Repair</b> asks Gemma 4 for ONE targeted proposal and shows you a diff; nothing is tested or applied until you say so. <b>Ignore</b> and <b>Review later</b> keep the finding in the report with its status.</div></div></div>`;
  dr.hidden = false;
  $(".panel .btn", dr).focus();
}

/* ================= 4 · Repair workspace ================= */
function pRepair() {
  const c = cap(), d = S.data;
  if (!c) return pageHead("4 · Repair workspace", "") + card("Repair workspace", empty("No analysis yet", "", `<button class="btn primary" data-act="goto" data-to="scanner">Open a repository</button>`));
  const ps = patchesOf(), att = (c.repair && c.repair.attempts) || [];
  const head = notice("obs", `<div class="tiny"><b>How this works.</b> <b>Mode A:</b> pick Repair on a finding; you get one proposal and decide each step. <b>Mode B:</b> enable automatic preparation for chosen findings; DepGuard proposes, validates and tests, then you review. In both modes <b>nothing is applied to your repository</b>. Approved patches go to a separate working copy${d.project ? ` (<code>${esc(d.project.work || "created on first repair")}</code>)` : ""} and can be exported as a normal patch.</div>`);
  if (!ps.length) return pageHead("4 · Repair workspace", "Proposed patches, their tests and your decisions.") + `<div class="stack">${jobCard()}${head}${card("Patches", empty("No patch yet", "Open a finding and choose Repair, or select findings and prepare fixes automatically.", `<button class="btn primary" data-act="goto" data-to="inventory">Go to findings</button>`))}${attemptsCard(att)}</div>`;
  const cards = ps.map(p => patchCard(p, d)).join("");
  return pageHead("4 · Repair workspace", `${ps.length} patch(es). Statuses come from recorded actions and test runs.`) + `<div class="stack">${jobCard()}${head}${cards}${attemptsCard(att)}</div>`;
}
function attemptsCard(att) {
  if (!att.length) return "";
  return card("Repair attempts", `<div class="tscroll"><table><thead><tr><th>#</th><th>Finding</th><th>Mode</th><th>Origin</th><th>Outcome</th><th>Note</th></tr></thead><tbody>${att.map(a => `<tr><td>${esc(a.attempt)}</td><td class="mono tiny">${esc(a.finding_id)}</td><td>${esc(a.mode)}</td><td>${esc(a.origin)}</td><td>${patchBadge(a.outcome)}</td><td class="tiny muted">${esc(a.notes || "")}</td></tr>`).join("")}</tbody></table></div>`, tag("obs"), "tight");
}
function patchCard(p, d) {
  const v = p.verification, busy = d.job && d.job.status === "running";
  const files = p.files.map(f => `<div class="card" style="padding:10px;margin-bottom:10px"><div class="row"><b class="mono">${esc(f.path)}</b>${originTag(f.origin)}${patchBadge(f.verification_status === "not_run" ? "proposed" : f.verification_status)}<span class="tiny muted">+${f.lines_added} −${f.lines_removed}</span></div>
    <div class="tiny muted" style="margin:6px 0">Why: ${esc(f.reason || "—")}</div>${(f.evidence || []).length ? `<div class="tiny faint">Evidence: ${f.evidence.map(esc).join(" · ")}</div>` : ""}<pre style="margin-top:8px;max-height:260px">${diffHtml(f.diff)}</pre></div>`).join("");
  const ver = v ? `<div class="notice ${v.status === "verified" ? "ok" : v.status === "failed" ? "bad" : "warn"}"><div class="tiny"><b>Verification: ${esc(stText(v.status))}</b> (${tag("ver")}) ${v.summary ? `· ${esc(v.summary)}` : ""}<ul class="list flow" style="margin-top:6px">${(v.reasons || []).map(r => `<li>• ${esc(r)}</li>`).join("")}</ul>
      ${(v.commands || []).length ? `<details style="margin-top:6px"><summary>Commands, exit codes, durations</summary><div class="dbody"><table><tbody>${v.commands.map(x => `<tr><td class="mono tiny">${esc(x.command.join(" "))}</td><td>${x.exit_code === 0 ? badge("0", "ok") : badge(x.exit_code, "bad")}</td><td class="tiny">${x.duration_s != null ? esc(x.duration_s) + " s" : ""}</td></tr>`).join("")}</tbody></table></div></details>` : ""}
      ${v.environment ? `<div class="faint" style="margin-top:6px">Environment: Python ${esc(v.environment.python)} · ${esc(v.environment.platform)} · ${esc(v.environment.isolation)}</div>` : ""}${(v.failure_excerpt || []).length ? `<pre class="errout" style="margin-top:6px">${esc(v.failure_excerpt.join("\n"))}</pre>` : ""}
      <div style="margin-top:6px"><b>Passing tests do not prove complete compatibility;</b> they cover only the project's own tests.</div></div></div>` : notice("warn", `<div class="tiny"><b>Not tested yet.</b> A proposal is a suggestion, not a verified fix.</div>`);
  const ai = p.origin === "ai" ? `<details><summary>Model's assumptions, concerns and suggested tests (AI-generated, unverified)</summary><div class="dbody"><div class="tiny"><b>Assumptions:</b> ${(p.assumptions || []).map(esc).join("; ") || "none"}</div><div class="tiny"><b>Compatibility concerns:</b> ${(p.compatibility_concerns || []).map(esc).join("; ") || "none"}</div><div class="tiny"><b>Suggested tests (not run):</b> ${(p.suggested_tests || []).map(esc).join("; ") || "none"}</div><div class="tiny"><b>Limits stated by the model:</b> ${esc(p.confidence_limitations || "—")}</div></div></details>` : "";
  const base = `<div class="tiny faint">Patch <span class="mono">${esc(p.id)}</span> · built on ${p.base.commit ? "commit " + esc(p.base.commit.slice(0, 12)) + " + the working tree as it was" : "a repository without a commit"} (content fingerprint ${esc(p.base.input_fingerprint || p.base.fingerprint)}) · ${esc(p.created_at)}</div>`;
  const adv = (p.advisory_check || []).map(a => notice(a.status === "covers_all" ? "ok" : a.status === "no_advisories" ? "obs" : "warn", `<div class="tiny"><b>Listed advisories vs the new constraint (${esc(a.file)}):</b> ${esc(a.detail)}<div class="faint" style="margin-top:4px">${esc(a.note)}</div></div>`)).join("");
  const can = s => !busy && s;
  let acts = "";
  if (p.status === "proposed") acts = `<button class="btn primary" data-act="pVerify" data-id="${esc(p.id)}" ${can(true) ? "" : "disabled"}>Accept &amp; test in isolation</button><input id="fb-${esc(p.id)}" placeholder="What should change?" style="min-width:200px" aria-label="Revision request"><button class="btn" data-act="pRevise" data-id="${esc(p.id)}" ${can(true) ? "" : "disabled"}>Request revision</button><button class="btn" data-act="pReject" data-id="${esc(p.id)}">Reject</button>`;
  else if (["verified", "partially_verified", "failed", "blocked"].includes(p.status)) {
    const needAck = p.status === "failed" || p.status === "blocked";
    acts = `<div class="notice warn" style="width:100%"><div class="tiny"><b>Applying changes files in the working copy</b> (not your original). ${needAck ? "<b>This patch is NOT verified as working.</b>" : ""}</div>
      <div class="row" style="margin-top:8px">${needAck ? `<label class="chk"><input type="checkbox" id="ack-${esc(p.id)}"> I understand it is not verified</label>` : ""}<input id="ap-${esc(p.id)}" placeholder="type APPLY" style="width:120px" aria-label="Type APPLY"><button class="btn danger" data-act="pApply" data-id="${esc(p.id)}" data-need-ack="${needAck}" disabled id="apb-${esc(p.id)}">Apply to working copy</button></div></div>
      <button class="btn" data-act="pVerify" data-id="${esc(p.id)}" ${can(true) ? "" : "disabled"}>Re-test</button><button class="btn" data-act="pReject" data-id="${esc(p.id)}">Reject</button>`;
  } else if (p.status === "applied") acts = `<a class="btn" href="/api/patch/export?id=${esc(p.id)}" download>Export patch</a><input id="rb-${esc(p.id)}" placeholder="type ROLLBACK" style="width:130px" aria-label="Type ROLLBACK"><button class="btn danger" data-act="pRollback" data-id="${esc(p.id)}" id="rbb-${esc(p.id)}" disabled>Roll back</button><button class="btn" data-act="goto" data-to="publish">Go to publishing →</button>`;
  else if (p.status === "stale") acts = notice("warn", `<div class="tiny"><b>Stale.</b> The files changed since this patch was built (or it was rolled back). Renewed review is required: create a new proposal.</div>`);
  return card(`<span class="mono">${esc(p.package)}</span> ${patchBadge(p.status)} ${originTag(p.origin)}`, `<p style="margin:0 0 8px">${esc(p.summary || "")}</p><div class="tiny muted" style="margin-bottom:8px">${esc(p.rationale || "")}</div>${base}${adv}<div style="margin-top:10px">${files}</div>${ai}${ver}<div class="row" style="margin-top:10px">${acts}</div>`);
}

/* ================= 6 · Publish ================= */
function pPublish() {
  const c = cap(), d = S.data, P = d.project;
  if (!c || !P) return pageHead("6 · Publish", "") + card("Publish", empty("Nothing to publish yet", "Open a repository, analyse it, and apply a reviewed patch first.", `<button class="btn primary" data-act="goto" data-to="scanner">Open a repository</button>`));
  const ps = patchesOf(), applied = ps.filter(p => p.status === "applied"), pub = d.publish || {};
  const sel = S.pubSel || (S.pubSel = new Set(applied.map(p => p.id)));
  const exportCard = card("Local export (always available)", `<div class="muted" style="margin-bottom:8px">Use your normal Git workflow: review, apply the patch yourself, commit and push. DepGuard never commits or pushes in this path.</div>
    ${ps.length ? `<ul class="list">${ps.map(p => `<li><div class="grow"><span class="mono">${esc(p.id)}</span> · ${esc(p.package)} ${patchBadge(p.status)} <span class="tiny muted">verification: ${esc((p.verification || {}).status || "not tested")}</span></div><a class="btn sm" href="/api/patch/export?id=${esc(p.id)}" download>Download .patch</a></li>`).join("")}</ul>` : '<div class="tiny muted">No patches yet.</div>'}
    <div class="tiny muted" style="margin-top:8px">Apply with <code>git apply --check file.patch</code> then <code>git apply file.patch</code>. The patch header records the base commit or fingerprint it was built on; re-run the analysis if your tree has moved.</div>
    <div class="row" style="margin-top:10px"><button class="btn" data-act="capsuleWrite">Write final capsule files</button><a class="btn" href="#capsule" data-act="goto" data-to="capsule">Reports &amp; capsule →</a></div>`);
  const i = P.input;
  const git = card("Repository state", `<dl class="kv"><dt>Source</dt><dd>${badge(methodLabel(P.method), "info")}</dd><dt>Branch / commit</dt><dd class="mono">${esc(i.branch || "n/a")} @ ${esc((i.commit || "n/a").slice(0, 12))}</dd><dt>Your working tree</dt><dd>${i.dirty ? `${(i.modified_files || []).length} modified, ${(i.untracked_files || []).length} untracked file(s): preserved, DepGuard only changed its own copy` : "no uncommitted changes recorded"}</dd><dt>Working copy</dt><dd class="mono tiny" style="overflow-wrap:anywhere">${esc(P.work || "not created yet")}</dd></dl>`);
  const pp = c.prepublish;
  const preCard = card("Pre-publish checks (supplementary)", `<div class="muted tiny" style="margin-bottom:8px">Looks for secrets, tracked caches or environment files, unintended changes, unverified fixes and inconsistent declarations. Advisory: low-confidence warnings never block anything; a stop is recommended only for high-confidence problems. Secret values are never shown.</div>
    ${applied.length || ps.length ? `<div class="row" style="margin-bottom:8px">${ps.map(p => `<label class="chk"><input type="checkbox" data-act="pubSel" data-id="${esc(p.id)}" ${sel.has(p.id) ? "checked" : ""}> ${esc(p.package)} <span class="mono tiny">${esc(p.id)}</span></label>`).join("")}<button class="btn" data-act="runPre" ${sel.size ? "" : "disabled"}>Run checks</button></div>` : '<div class="tiny muted">Create a patch to run checks.</div>'}
    ${pp ? `${pp.stop_recommended ? notice("bad", `<div><b>Stopping is recommended.</b><ul class="list flow tiny">${pp.stop_reasons.map(r => `<li>• ${esc(r)}</li>`).join("")}</ul></div>`) : notice(pp.status === "warn" ? "warn" : "ok", `<div class="tiny"><b>No high-confidence blocker.</b> ${esc(pp.note)}</div>`)}
    <div class="tscroll" style="margin-top:8px"><table><thead><tr><th>Check</th><th>Result</th><th>Confidence</th><th>Detail</th></tr></thead><tbody>${pp.checks.map(k => `<tr><td>${esc(k.title)}</td><td>${badge(k.status, k.status === "pass" ? "ok" : k.status === "fail" ? "bad" : k.status === "warn" ? "warn" : "na")}</td><td class="tiny">${esc(k.confidence)}</td><td class="tiny">${esc(k.detail)}${k.recommendation ? `<div class="faint">→ ${esc(k.recommendation)}</div>` : ""}${(k.files || []).length ? `<div class="mono faint">${k.files.map(esc).join(", ")}</div>` : ""}</td></tr>`).join("")}</tbody></table></div>` : ""}`, tag("obs"));
  let ghCard;
  if (P.method !== "github") ghCard = card("GitHub pull request", `<div class="tiny muted">Pull requests are available for repositories opened from GitHub. This repository was opened from <b>${esc(methodLabel(P.method))}</b>: use the exported patch with your normal Git workflow.</div>`);
  else {
    const plan = pub.plan, br = pub.branch, prr = pub.pr, stop = pp && pp.stop_recommended;
    ghCard = card(`GitHub: ${esc((P.github || {}).owner)}/${esc((P.github || {}).repo)}`, `
      ${d.github.authenticated ? `<div class="row">${badge("connected as " + d.github.login, "ok")}</div>` : notice("warn", `<div class="tiny">Not connected. Connect a token on the Repository page to publish.</div>`)}
      <div class="row" style="margin:10px 0"><label class="tiny muted">Base branch <input id="base-branch" value="${esc(S.baseBranch ?? (P.github || {}).branch ?? "")}" style="width:160px" aria-label="Base branch"></label><button class="btn" data-act="planPub" ${applied.length && d.github.authenticated ? "" : "disabled"}>Prepare publication plan</button></div>
      ${!applied.length ? '<div class="tiny muted">Apply a reviewed patch to the working copy first; only applied changes can be published.</div>' : ""}
      ${plan ? `<div class="card" style="padding:12px"><dl class="kv"><dt>Target</dt><dd>${esc(plan.repository)}</dd><dt>Base</dt><dd class="mono">${esc(plan.base_branch)} @ ${esc(plan.base_sha.slice(0, 12))} <span class="faint">(analysed ${esc((plan.analysed_sha || "").slice(0, 12))})</span></dd><dt>New branch</dt><dd class="mono">${esc(plan.proposed_branch)}</dd>
        <dt>Files</dt><dd class="mono tiny">${plan.files.map(f => esc(f.path)).join(", ")}</dd><dt>Verification</dt><dd>${sBadge(plan.verification.status)} <span class="tiny muted">${esc(plan.verification.summary || "")}</span></dd></dl>
        ${plan.warnings.map(w => notice("warn", `<div class="tiny">${esc(w)}</div>`)).join("")}${plan.publish_blocker ? notice("bad", `<div class="tiny"><b>Cannot publish:</b> ${esc(plan.publish_blocker)}</div>`) : ""}
        <h3 style="margin-top:12px">Approval 1 of 2: push a NEW branch with one commit</h3><div class="tiny muted">The default branch is never modified. Nothing is merged or force-pushed.</div>
        <div class="row" style="margin-top:6px">${stop ? `<label class="chk"><input type="checkbox" id="ack-stop"> I acknowledge the stop recommendation above</label>` : ""}<input id="pub-confirm" placeholder="type PUBLISH" style="width:130px" aria-label="Type PUBLISH"><button class="btn danger" id="pub-btn" data-act="pubBranch" disabled>Create branch &amp; commit</button></div>
        ${br ? notice("ok", `<div class="tiny"><b>Branch created:</b> <code>${esc(br.branch)}</code> · <a href="${esc(br.commit_url)}" target="_blank" rel="noopener noreferrer">commit ${esc(br.commit_sha.slice(0, 7))}</a></div>`) : ""}
        ${br ? `<h3 style="margin-top:12px">Approval 2 of 2: open the pull request</h3><div class="row" style="margin-top:6px"><input id="pr-title" class="grow" placeholder="Pull request title (optional)" aria-label="Pull request title"><input id="pr-confirm" placeholder="type OPEN PR" style="width:130px" aria-label="Type OPEN PR"><button class="btn danger" id="pr-btn" data-act="pubPr" disabled>Open pull request</button></div>` : ""}
        ${prr ? notice("ok", `<div class="tiny"><b>Pull request opened:</b> <a href="${esc(prr.html_url)}" target="_blank" rel="noopener noreferrer">#${esc(prr.number)}</a>. DepGuard never merges it.</div>`) : ""}</div>` : ""}
      ${S.pubErr ? notice("bad", `<div class="tiny"><b>Not done.</b> ${esc(S.pubErr)}</div>`) : ""}`);
  }
  return pageHead("6 · Publish", "Export locally, run the supplementary checks, and optionally prepare a pull request. Each outward step needs its own approval.") + `<div class="stack">${jobCard()}${exportCard}<div class="grid g2">${git}${preCard}</div>${ghCard}</div>`;
}

/* ================= events ================= */
document.addEventListener("click", async ev => {
  const t = ev.target.closest("[data-act]"); if (!t) return;
  const a = t.dataset.act, id = t.dataset.id;
  const run = async fn => { try { await fn(); } catch (e) { toast(e.message, "bad"); await load(true); } };
  if (a === "inTab") { S.inTab = t.dataset.tab; S.inErr = null; render(); }
  else if (a === "zipUpload") run(async () => {
    const f = $("#zip-file").files[0]; if (!f) throw new Error("choose a .zip file first");
    if (f.size > S.data.limits.max_zip_mb * 1048576) throw new Error(`the archive is larger than ${S.data.limits.max_zip_mb} MB`);
    S.inBusy = true; S.inErr = null; render();
    try { await apiRaw("/api/input/zip", f, { "X-Filename": f.name }); toast("Archive validated and extracted", "ok"); } catch (e) { S.inErr = e.message; }
    S.inBusy = false; await load(true);
  });
  else if (a === "openLocal") run(async () => { S.localPath = $("#local-path").value; S.inErr = null; S.inBusy = true; render(); try { await api("/api/input/local", { path: S.localPath }); toast("Repository opened", "ok"); } catch (e) { S.inErr = e.message; } S.inBusy = false; await load(true); });
  else if (a === "ghAuth") run(async () => { const tok = $("#gh-token").value; S.inErr = null; try { await api("/api/github/auth", { token: tok }); toast("Connected to GitHub", "ok"); } catch (e) { S.inErr = e.message; } finally { const el = $("#gh-token"); if (el) el.value = ""; } await load(true); });
  else if (a === "ghLogout") run(async () => { await api("/api/github/logout", {}); S.ghRepo = null; await load(true); });
  else if (a === "ghLoad") run(async () => {
    S.ghInput = $("#gh-repo").value; const m = S.ghInput.trim().replace(/^https:\/\/github\.com\//, "").replace(/\.git$/, "").replace(/\/$/, "").split("/");
    S.inErr = null; S.inBusy = true; render();
    try { if (m.length !== 2 || !m[0] || !m[1]) throw new Error("enter owner/repo"); S.ghRepo = await api("/api/github/repo", { owner: m[0], name: m[1] }); S.ghBranch = S.ghRepo.default_branch; } catch (e) { S.inErr = e.message; S.ghRepo = null; }
    S.inBusy = false; render();
  });
  else if (a === "ghOpen") run(async () => { const R = S.ghRepo, br = $("#gh-branch").value, [o, n] = R.full_name.split("/"); S.inErr = null; S.inBusy = true; render(); try { await api("/api/input/github", { owner: o, name: n, branch: br }); toast("Commit downloaded and extracted. Nothing was executed.", "ok"); } catch (e) { S.inErr = e.message; } S.inBusy = false; await load(true); });
  else if (a === "projSel") run(async () => { await api("/api/project/select", { id }); await load(true); });
  else if (a === "projRm") run(async () => { if (!confirm("Close this repository and delete DepGuard's extracted copy and working copy? Your original files are not touched.")) return; await api("/api/project/remove", { id }); await load(true); });
  else if (a === "runAnalysis") run(async () => {
    S.optTests = $("#opt-tests").checked; S.optAi = $("#opt-ai").checked; S.optMeta = $("#opt-meta").checked; const P = S.data.project;
    if (S.optTests && P.untrusted && !confirm("This repository came from outside (" + methodLabel(P.method) + ").\n\nRunning its tests installs its requirements and EXECUTES its code with your user rights. A virtual environment is not a sandbox.\n\nOnly continue if you trust it.")) return;
    if (!confirm("Run the read-only analysis on:\n" + P.label + "\n\n" + (S.optTests ? "It installs requirements into a temporary environment and runs the tests for a baseline. " : "Tests will NOT run (no baseline). ") + "No file is modified and no patch is proposed.")) return;
    await api("/api/analyze", { project_id: P.id, tests: S.optTests, ai: S.optAi, metadata: S.optMeta, run_untrusted: P.untrusted && S.optTests }); toast("Analysis started"); await load(true);
  });
  else if (a === "fOpen") { if (ev.target.type === "checkbox") return; findingDrawer(id); }
  else if (a === "fSel") { S.selF = S.selF || new Set(); ev.target.checked ? S.selF.add(id) : S.selF.delete(id); render(); }
  else if (a === "fRepair") run(async () => { if (!confirm("Ask Gemma 4 for ONE repair proposal for this finding?\n\nIt will only produce a proposal and a diff in a separate working copy. Nothing is tested or applied until you choose to.")) return; $("#drawer").hidden = true; await api("/api/repair/start", { finding_id: id }); location.hash = "repair"; await load(true); });
  else if (a === "fIgnore") run(async () => { await api("/api/finding/decision", { id, status: "ignored", reason: $("#ign-reason").value }); $("#drawer").hidden = true; toast("Marked ignored: it stays in the report with your reason", "ok"); await load(true); });
  else if (a === "fLater") run(async () => { await api("/api/finding/decision", { id, status: "review_later" }); $("#drawer").hidden = true; toast("Marked review later", "ok"); await load(true); });
  else if (a === "fReopen") run(async () => { await api("/api/finding/decision", { id, status: "open" }); $("#drawer").hidden = true; await load(true); });
  else if (a === "prepareB") run(async () => {
    const ids = [...S.selF]; if (!confirm(`Prepare fixes for ${ids.length} finding(s)?\n\nGemma will propose, DepGuard will validate and test each in isolation (revising after a failure up to the attempt limit). NOTHING is applied; you review every diff.`)) return;
    await api("/api/repair/batch", { finding_ids: ids, enabled: S.enB === true }); location.hash = "repair"; await load(true);
  });
  else if (a === "pVerify") run(async () => { await api("/api/patch/verify", { patch_id: id }); toast("Testing in an isolated environment…"); await load(true); });
  else if (a === "pRevise") run(async () => { await api("/api/patch/revise", { patch_id: id, feedback: ($("#fb-" + id) || {}).value || "" }); await load(true); });
  else if (a === "pReject") run(async () => { if (!confirm("Reject this patch? The finding returns to open.")) return; await api("/api/patch/reject", { patch_id: id, reason: "rejected by user" }); await load(true); });
  else if (a === "pApply") run(async () => {
    const need = t.dataset.needAck === "true", ack = need ? $("#ack-" + id).checked : false;
    if (need && !ack) throw new Error("acknowledge that this patch is not verified");
    if (!confirm("Apply this patch to DepGuard's working copy?\n\nYour original repository is not modified. The previous file contents are snapshotted for rollback.")) return;
    S.applyOut = await api("/api/patch/apply", { patch_id: id, confirm: $("#ap-" + id).value, acknowledge_unverified: ack }); toast("Applied to the working copy", "ok"); await load(true);
  });
  else if (a === "pRollback") run(async () => { await api("/api/patch/rollback", { patch_id: id, confirm: $("#rb-" + id).value }); toast("Rolled back", "ok"); await load(true); });
  else if (a === "pubSel") { S.pubSel = S.pubSel || new Set(); ev.target.checked ? S.pubSel.add(id) : S.pubSel.delete(id); render(); }
  else if (a === "runPre") run(async () => { S.pubErr = null; await api("/api/prepublish", { patch_ids: [...S.pubSel] }); toast("Checks complete (advisory)", "ok"); await load(true); });
  else if (a === "planPub") run(async () => { S.pubErr = null; S.baseBranch = $("#base-branch").value; try { await api("/api/publish/plan", { patch_ids: [...(S.pubSel || [])], base_branch: S.baseBranch }); } catch (e) { S.pubErr = e.message; } await load(true); });
  else if (a === "pubBranch") run(async () => { S.pubErr = null; if (!confirm("Push a NEW branch with one commit to GitHub?\n\nThe default branch is not modified and nothing is merged.")) return; try { await api("/api/publish/branch", { confirm: $("#pub-confirm").value, acknowledge_stop: !!($("#ack-stop") || {}).checked }); } catch (e) { S.pubErr = e.message; } await load(true); });
  else if (a === "pubPr") run(async () => { S.pubErr = null; if (!confirm("Open the pull request now? It will not be merged by DepGuard.")) return; try { await api("/api/publish/pr", { confirm: $("#pr-confirm").value, title: $("#pr-title").value }); } catch (e) { S.pubErr = e.message; } await load(true); });
  else if (a === "capsuleWrite") run(async () => { const r = await api("/api/capsule/refresh", {}); toast("Capsule written: " + r.path, "ok"); });
});
document.addEventListener("input", ev => {
  const id = ev.target.id || "";
  if (id === "f-text") { S.fText = ev.target.value; render(); }
  else if (id.startsWith("ap-")) { const b = $("#apb-" + id.slice(3)); if (b) b.disabled = ev.target.value !== "APPLY"; }
  else if (id.startsWith("rb-") && $("#rbb-" + id.slice(3))) $("#rbb-" + id.slice(3)).disabled = ev.target.value !== "ROLLBACK";
  else if (id === "pub-confirm") $("#pub-btn").disabled = ev.target.value !== "PUBLISH";
  else if (id === "pr-confirm") $("#pr-btn").disabled = ev.target.value !== "OPEN PR";
});
document.addEventListener("change", async ev => {
  const id = ev.target.id;
  if (id === "f-sev") { S.fSev = ev.target.value; render(); }
  else if (id === "f-status") { S.fStatus = ev.target.value; render(); }
  else if (id === "f-sort") { S.fSort = ev.target.value; render(); }
  else if (id === "enB") { S.enB = ev.target.checked; render(); }
  else if (id === "gh-branch") S.ghBranch = ev.target.value;
  else if (id === "attLimit") { try { await api("/api/repair/settings", { max_attempts: Number(ev.target.value) }); await load(true); } catch (e) { toast(e.message, "bad"); } }
});


/* ---- helpers shared with the Verify and Reports pages ---- */
function patchVerifyCard() {
  const ps = patchesOf().filter(p => p.verification);
  if (!ps.length) return "";
  return card("Repair patches: recorded verification", `<div class="tscroll"><table><thead><tr><th>Patch</th><th>Dependency</th><th>Result</th><th>Exit</th><th>Failure kind</th><th>Environment</th><th>Commands</th></tr></thead><tbody>${ps.map(p => {
    const v = p.verification;
    return `<tr><td class="mono tiny">${esc(p.id)}<div>${patchBadge(p.status)}</div></td><td>${esc(p.package)}</td><td>${badge(stText(v.status), v.status === "verified" ? "ok" : v.status === "failed" ? "bad" : "warn")}<div class="tiny muted">${(v.reasons || []).map(esc).join("; ")}</div></td>
      <td class="mono">${esc(v.exit_code ?? "n/a")}</td><td class="tiny">${esc(stText(v.failure_kind || "none"))}</td><td class="tiny">${v.environment ? `Python ${esc(v.environment.python)}<div class="faint">${esc(v.environment.isolation)}</div>` : "not recorded"}</td>
      <td class="tiny">${(v.commands || []).map(x => `<div class="mono">${esc(x.command.slice(-3).join(" "))} → ${esc(x.exit_code)}${x.duration_s != null ? ` (${esc(x.duration_s)} s)` : ""}</div>`).join("")}</td></tr>`;
  }).join("")}</tbody></table></div><div class="tiny muted" style="padding:10px 14px">Each patch was tested in its own temporary copy and virtual environment (not a container), against the same baseline. <a href="#repair">Open the repair workspace →</a></div>`, tag("ver"), "tight");
}
function perDepCard(c) {
  const F = c.findings;
  if (!F || !F.length) return "";
  return card("Per-dependency capsules", `<div class="tiny muted" style="margin-bottom:8px">One capsule per at-risk dependency (each validates against the same schema), alongside the combined capsule above.</div>
    <div class="tscroll"><table><tbody>${F.map(f => `<tr><td><b>${esc(f.package)}</b> ${sevBadge(f.severity)} ${decBadge(f.status)}</td><td style="text-align:right;white-space:nowrap"><a class="btn sm" href="/api/report?format=json&package=${encodeURIComponent(f.package)}" download>JSON</a> <a class="btn sm" href="/api/report?format=md&package=${encodeURIComponent(f.package)}" download>Markdown</a></td></tr>`).join("")}</tbody></table></div>`, tag("obs"), "tight");
}
