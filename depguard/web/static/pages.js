"use strict";
/* DepGuard dashboard pages (loaded before app.js, which supplies S, esc, card, tag, view, ... at call time). */

const basisTag = b => ({
  observed: '<span class="tag-obs">Observed</span>', metadata: '<span class="tag-obs">Package metadata</span>',
  heuristic: '<span class="tag-heur">Heuristic</span>', unknown: '<span class="tag-unk">Unknown / unchecked</span>',
}[b] || "");

const healthOf = () => { const v = view(); return v && v.live && S.data.scan ? S.data.scan.health : S.data.health; };
const metaOf = n => (S.data.metadata || {})[n];
const metaText = n => {
  const m = metaOf(n);
  if (!m) return `<span class="badge b-na">metadata: not fetched</span>`;
  if (m.status !== "ok") return `<span class="badge b-warn">unavailable</span><div class="tiny faint">${esc(m.error)}</div>`;
  return `<div>latest <b>${esc(m.latest_version)}</b>${m.latest_upload_time ? ` · ${esc(m.latest_upload_time.slice(0, 10))}` : ""}</div><div class="tiny faint">${esc(m.requires_python || "no Requires-Python")} · PyPI, fetched ${esc((m.fetched_at || "").slice(0, 10))}</div>`;
};

function healthStat() {
  const h = healthOf();
  if (!h) return `<div class="card stat"><div class="l">Health score</div><div class="v">—</div><div class="d">no valid capsule</div></div>`;
  return `<div class="card stat"><div class="l">Health score <span class="tag-heur">Heuristic</span></div><div class="v">${h.score}<span class="muted" style="font-size:14px"> /100</span></div>
    <div class="d">Evidence coverage ${h.evidence_coverage.percent}% · not a safety guarantee</div></div>`;
}

function healthCard() {
  const h = healthOf(); if (!h) return "";
  const rows = h.packages.filter(p => p.deductions.length);
  const table = rows.length ? `<div class="tscroll"><table><thead><tr><th>Package</th><th>Score</th><th>Deductions (each with its evidence basis)</th></tr></thead><tbody>${rows.map(p => `<tr><td><b>${esc(p.package)}</b></td><td class="mono">${p.score}</td><td>${p.deductions.map(d => `<div>−${d.points} · ${esc(String(d.rule).replace(/_/g, " "))} ${basisTag(d.basis)}<div class="tiny faint">${esc((d.evidence || []).join(" · ").slice(0, 220))}</div></div>`).join("")}</td></tr>`).join("")}</tbody></table></div>` : `<div class="muted">No deductions from the available evidence.</div>`;
  const rd = h.repo_deductions.map(d => `<div class="tiny">Repository-level: −${d.points} ${esc(d.rule.replace(/_/g, " "))} ${basisTag(d.basis)}</div>`).join("");
  const w = Object.entries(h.weights).map(([k, v]) => `<tr><td class="mono">${esc(k)}</td><td class="mono">${typeof v === "object" ? Object.entries(v).map(([a, b]) => a + " " + b).join(" | ") : v}</td></tr>`).join("");
  return card("Health score breakdown", `${notice("warn", `<div class="tiny"><b>${h.score}/100 is a heuristic.</b> ${esc(h.disclaimer)}</div>`)}
    <div style="margin:12px 0">${table}${rd}</div>
    <h3>Evidence coverage: ${h.evidence_coverage.percent}% (${h.evidence_coverage.checks_done} of ${h.evidence_coverage.checks_possible} possible checks)</h3>
    <div class="bar" role="img" aria-label="evidence coverage ${h.evidence_coverage.percent}%"><span style="width:${h.evidence_coverage.percent}%;background:var(--accent)"></span></div>
    <h3 style="margin-top:14px">Unchecked: never counted against any package</h3><ul class="list tiny">${h.unchecked.map(u => `<li><span class="dot"></span><div><b>${esc(u.check)}</b><div class="muted">${esc(u.reason)}</div></div></li>`).join("")}</ul>
    <details style="margin-top:12px"><summary>Rules and weights (points deducted from 100 per package)</summary><div class="tscroll"><table><tbody>${w}</tbody></table></div><div class="tiny muted" style="padding:8px 14px">Repository score = mean of package scores − repository-level deductions, floored at 0. Stale threshold ${h.stale_days} days.</div></details>`, tag("obs") + ' <span class="tag-heur">Heuristic</span>');
}

function networkCard() {
  const n = S.data.network, m = S.data.model;
  const row = (l, v) => `<tr><td class="muted">${l}</td><td>${v}</td></tr>`;
  return card("Offline mode & data sources", `<div class="tscroll"><table><tbody>
    ${row("Gemma 4 model", m.available ? `${badge("available locally", "ok")} <span class="mono tiny">${esc(m.model)}</span>` : `${badge("unavailable", "bad")} <span class="tiny muted">${esc(m.blocker)}</span>`)}
    ${row("Scanning & reports", badge("work offline", "ok"))}
    ${row("Package metadata", n.offline_flag ? badge("offline mode: cache only", "warn") : badge("needs network (PyPI), only when you click Fetch", "info"))}
    ${row("Metadata cache", `${n.cache_entries} package(s)${n.cache_newest ? ` · newest fetch ${esc(fmtTime(n.cache_newest))}` : ""}`)}
    ${row("Full analysis", "needs network for <code>pip install</code> (test environments)")}</tbody></table></div>
    <div class="tiny muted" style="margin:10px 0">${esc(n.note)}</div>
    <button class="btn" data-act="fetchmeta" ${n.offline_flag ? "disabled" : ""}>Fetch package metadata…</button>`);
}

/* ---------------- scanner ---------------- */
function pQuickScan() {
  const d = S.data, j = d.job, s = d.scan;
  const run = j && j.status === "running";
  const form = card("Select a repository", `<div class="row"><input id="repo-path" class="grow mono" list="roots" value="${esc(S.path ?? d.project_root)}" aria-label="Repository path" spellcheck="false"><datalist id="roots">${d.allowed_roots.map(r => `<option value="${esc(r)}">`).join("")}</datalist>
    <button class="btn primary" data-act="scan" ${S.scanBusy || run ? "disabled" : ""}>${S.scanBusy ? '<span class="spin"></span>' : ""}Scan manifests &amp; usage</button></div>
    <div class="tiny muted" style="margin-top:8px">Only authorized roots can be scanned: ${d.allowed_roots.map(r => `<code>${esc(r)}</code>`).join(", ")}. Authorize more by starting the server with <code>--allow PATH</code>.</div>
    ${S.scanErr ? notice("bad", `<div><b>Scan failed.</b> ${esc(S.scanErr)}</div>`) : ""}`, tag("obs"));
  const acq = card("Or download a repository from GitHub", `<div class="row"><input id="gh-url" class="grow mono" placeholder="https://github.com/owner/repo" value="${esc(S.ghUrl ?? "")}" aria-label="GitHub repository URL" spellcheck="false" autocomplete="off">
    <button class="btn" data-act="acquire" ${S.acqBusy || d.network.offline_flag ? "disabled" : ""}>${S.acqBusy ? '<span class="spin"></span>' : ""}Clone to a temporary folder</button></div>
    <div class="tiny muted" style="margin-top:8px">${d.network.offline_flag ? "Offline mode: GitHub downloads are disabled." : "<b>Needs a network connection.</b> Only https://github.com/&lt;owner&gt;/&lt;repo&gt; is accepted."} A shallow clone with submodules, hooks and git config disabled. <b>Scanning only parses files; it never runs the repository's code.</b> A full analysis of a downloaded repo does run its tests (your user rights, no sandbox) and asks for a separate confirmation.</div>
    ${S.acqErr ? notice("bad", `<div style="margin-top:10px"><b>Not downloaded.</b> ${esc(S.acqErr)}</div>`) : ""}
    ${(d.acquired || []).length ? `<div style="margin-top:12px"><h3>Downloaded copies</h3><ul class="list">${d.acquired.map(a => `<li><div class="grow mono tiny" style="overflow-wrap:anywhere">${esc(a.path)}</div><button class="btn sm" data-act="acquse" data-path="${esc(a.path)}">Use this path</button><button class="btn sm" data-act="acqrm" data-path="${esc(a.path)}">Remove</button></li>`).join("")}</ul></div>` : ""}`);
  const full = card("Full analysis", `<div class="muted" style="margin-bottom:10px">Runs the real pipeline: baseline tests in a temp venv, Gemma 4 advice, proposed-edit tests, then writes a new capsule and a history record. Needs network for <code>pip install</code> and takes several minutes. It never changes the repository.</div>
    <div class="row"><label class="chk"><input type="checkbox" id="opt-tests" ${S.optTests === false ? "" : "checked"}> Run tests (baseline + proposed)</label><label class="chk"><input type="checkbox" id="opt-ai" ${S.optAi === false ? "" : "checked"} ${d.model.available ? "" : "disabled"}> Ask Gemma 4 (${esc(d.model.model)})</label>
    <button class="btn" data-act="analyze" ${run ? "disabled" : ""}>Run full analysis…</button></div>
    ${d.model.available ? "" : notice("warn", `<div class="tiny">Gemma 4 unavailable: ${esc(d.model.blocker)}. The analysis can still run without AI.</div>`)}
    ${j ? `<div style="margin-top:12px">${notice(j.status === "error" ? "bad" : j.status === "done" ? "ok" : "obs", `<div>${run ? '<span class="spin"></span> ' : ""}<b>${esc(stText(j.status))}</b> · ${esc(j.path)}${run ? `<div class="tiny muted">Running for <span id="elapsed">${Math.round(Date.now() / 1000 - j.started)}</span>s. The pipeline does not report phases, so this is an indeterminate wait.</div>` : ""}${j.error ? `<div class="mono tiny">${esc(j.error)}</div>` : ""}${j.status === "done" ? '<div class="tiny">New capsule loaded. See Exit Capsule, Verification lab and AI Advisor.</div>' : ""}</div>`)}</div>` : ""}`);
  let res;
  if (S.scanBusy) res = card("Scanning…", `<div class="stack">${'<div class="skel"></div>'.repeat(4)}</div>`);
  else if (!s) res = card("Scan findings", empty("No live scan yet", "Run a scan to parse manifests and map imports. Without one, pages use the capsule data."));
  else {
    const u = s.usage, sc = s.scan, conf = sc.conflicts;
    const row = (t, list, basis, note) => `<tr><td>${t}</td><td>${list.length ? list.map(x => `<div class="mono tiny">${esc(x)}</div>`).join("") : '<span class="faint">none</span>'}</td><td>${basisTag(basis)}${note ? `<div class="tiny faint">${note}</div>` : ""}</td></tr>`;
    res = card("Scan findings", `<div class="muted tiny" style="margin-bottom:10px">${esc(s.path)} · ${fmtTime(s.time)}</div>
      <dl class="kv" style="margin-bottom:14px"><dt>Manifests</dt><dd>${sc.manifests.map(esc).join(", ") || "none"}</dd><dt>requires-python</dt><dd class="mono">${esc(sc.python_requires || "not declared")}</dd><dt>Declared entries</dt><dd>${sc.dependencies.length}</dd></dl>
      <div class="tscroll"><table><thead><tr><th>Finding</th><th>Evidence (file:line where available)</th><th>Basis</th></tr></thead><tbody>
      ${row("Conflicting version constraints", conf.map(c => c.detail), "observed", "read directly from the manifests")}
      ${row("Imported but not declared", u.undeclared_imports.map(p => p + " @ " + (u.usages[p] || []).slice(0, 3).map(x => x.file + ":" + x.line).join(", ")), "observed", "AST import; alias table may mis-map unusual names")}
      ${row("Declared, no static import found", u.no_import_found, "heuristic", "NOT proof of non-use: transitive, dynamic or tooling use is invisible to static analysis")}
      ${row("Private API imports", u.private_api_imports.map(p => `${p.name} @ ${p.file}:${p.line}`), "observed")}
      ${row("Dynamic import sites (not resolved)", u.dynamic_import_sites.map(p => `${p.file}:${p.line}`), "observed", "targets cannot be determined statically")}
      ${row("Manifest parse issues", sc.issues.map(i => `${i.file}:${i.line} ${i.issue}`), "observed")}
      <tr><td>Maintenance / release recency / Requires-Python</td><td><span class="faint">${Object.keys(d.metadata || {}).length ? Object.keys(d.metadata).length + " package(s) in cache" : "not fetched"}</span></td><td>${basisTag("unknown")}<div class="tiny faint">needs an explicit network fetch</div></td></tr>
      </tbody></table></div>
      <div class="row" style="margin-top:12px"><button class="btn" data-act="goto" data-to="deps" data-live="1">Inspect dependencies</button><span class="tiny muted">${esc(s.note)}</span></div>`, tag("obs"));
  }
  return pageHead("Quick scan & clone", "A fast, read-only scan of a folder, or a shallow GitHub clone, without opening a project. A scan parses files only and never executes repository code. For findings, repair and publishing use 1 · Repository.", "") + `<div class="stack">${form}${acq}${res}${full}</div>`;
}

/* ---------------- dependencies ---------------- */
function pInventory() {
  const v = view(); if (v.empty) return needCapsule("Dependency inventory");
  const q = S.filter.toLowerCase(), h = healthOf();
  const hp = n => h && h.packages.find(p => p.package === n);
  const rows = v.pkgs.filter(p => (!S.only || p.level === "high" || p.level === "medium") && p.name.includes(q));
  const body = rows.length ? `<div class="tscroll"><table><thead><tr><th>Package</th><th>Declared</th><th>Usage (static)</th><th>Package metadata</th><th>Health</th><th>Evidence level</th></tr></thead><tbody>${rows.map(p => `<tr class="click" data-act="pkg" data-name="${esc(p.name)}" tabindex="0">
    <td><b>${esc(p.name)}</b>${p.undeclared ? ' <span class="badge b-warn">undeclared</span>' : ""}</td>
    <td class="mono">${p.entries.length ? [...new Set(p.entries.map(e => e.specifier || "any"))].map(esc).join(" · ") : '<span class="faint">—</span>'}<div class="tiny faint">${p.entries.map(e => `${esc(e.file)}:${e.line}`).join(", ")}</div></td>
    <td>${p.usages.length ? `${p.usages.length} import${p.usages.length > 1 ? "s" : ""}<div class="tiny faint mono">${esc(p.usages[0].file)}:${p.usages[0].line}${p.usages.length > 1 ? " …" : ""}</div>` : '<span class="faint">no static import found</span><div class="tiny faint">heuristic, not proof of non-use</div>'}</td>
    <td>${metaText(p.name)}${(p.entries[0] && p.entries[0].resolved_version) ? `<div class="tiny faint">resolved here: ${esc(p.entries[0].resolved_version)}</div>` : ""}</td>
    <td class="mono">${hp(p.name) ? hp(p.name).score : "—"}</td>
    <td>${sBadge(p.level)}<div class="tiny faint">${p.contributing.map(stText).join(", ")}</div></td></tr>`).join("")}</tbody></table></div>` : empty("No packages match", "Clear the filter to see all dependencies.");
  const ctl = `<div class="row" style="margin-bottom:14px"><input id="flt" class="grow" placeholder="Filter packages…" value="${esc(S.filter)}" aria-label="Filter packages"><label class="chk"><input type="checkbox" id="only" ${S.only ? "checked" : ""}> At-risk only</label>
    <select id="src" aria-label="Data source"><option value="capsule" ${S.src === "capsule" ? "selected" : ""}>Source: capsule</option><option value="live" ${S.src === "live" ? "selected" : ""} ${S.data.scan ? "" : "disabled"}>Source: live scan${S.data.scan ? "" : " (none yet)"}</option></select>
    <button class="btn" data-act="fetchmeta" ${S.data.network.offline_flag ? "disabled" : ""}>Fetch metadata (network)…</button></div>`;
  const healthBlock = healthOf() ? `<details class="modeld" style="margin-top:14px"><summary>Heuristic health score (secondary): ${healthOf().score}/100, evidence coverage ${healthOf().evidence_coverage.percent}%</summary><div style="padding:12px">${healthCard()}</div></details>` : "";
  return pageHead("Dependency inventory", `${v.pkgs.length} packages from ${esc(v.origin)}. Click a row for evidence. Health scores are heuristic; "not fetched" metadata is unknown, not bad.`) + ctl + card("Packages", body, tag("obs"), "tight")
    + (v.live ? notice("warn", `<div class="tiny">Live scan has no resolved versions or test-derived indicators; load the capsule source for those.</div>`) : "") + healthBlock;
}

/* ---------------- migration ---------------- */
function pMigration() {
  const c = cap(); if (!c) return needCapsule("Migration workspace");
  const d = S.data, ps = proposals(), p = ps.find(x => x.id === S.proposal) || ps[0];
  const files = [...new Set(p.edits.map(e => e.file))];
  const sel = `<div class="row">${ps.map(x => `<button class="btn ${x.id === p.id ? "primary" : ""}" data-act="prop" data-id="${esc(x.id)}">${esc(x.title)} ${sBadge(x.status)}</button>`).join("")}</div>`;
  const verdict = p.kind === "ai"
    ? notice(p.status === "passed" ? "ok" : "bad", `<div>${tag("ai")} <b>This proposal was generated by Gemma 4 and is never applied automatically.</b> Its tested outcome: ${sBadge(p.status)}. ${p.status !== "passed" ? "Applying it would likely break the project, as recorded in the Verification lab." : "Passing tests do not prove full compatibility."}</div>`)
    : notice("ok", `<div>${tag("ver")} <b>Human-specified experiment, independently tested.</b> Baseline ${p.cmp.baseline_counts.passed} passed / ${p.cmp.baseline_counts.failed} failed → proposed ${p.cmp.proposed_counts.passed} passed / ${p.cmp.proposed_counts.failed} failed. Test pass is not proof of full compatibility.</div>`);
  const warn = notice("warn", `<div><b>Changing files.</b> The steps below modify files. By default only an <b>isolated experiment copy</b> can be changed; the repository you scanned is never a target. Apply roots: ${d.apply_roots.map(r => `<code>${esc(r)}</code>`).join(", ")}. Nothing is committed or pushed.</div>`);
  const r = S.applyRes;
  const exp = card("Step 1 · Create an isolated experiment copy", `<div class="row"><input id="exp-src" class="grow mono" value="${esc(S.expSrc ?? d.project_root)}" aria-label="Repository to copy" spellcheck="false"><button class="btn" data-act="expcopy">Create copy</button></div>
    <div class="tiny muted" style="margin-top:8px">Copies the files (not .git, virtualenvs or caches) into the dashboard's data folder.</div>
    ${S.expRes ? notice("ok", `<div style="margin-top:10px"><b>Copy ready:</b> <code>${esc(S.expRes.path)}</code></div>`) : ""}${S.expErr ? notice("bad", `<div style="margin-top:10px">${esc(S.expErr)}</div>`) : ""}`);
  const apply = card("Step 2 · Apply to the experiment copy", `<div class="muted" style="margin-bottom:10px">Writes <b>${files.length} file(s)</b> (${files.map(f => `<code>${esc(f)}</code>`).join(", ") || "none"}) after taking a snapshot of the originals, then checks that the real diff equals the preview above.</div>
    <div class="row"><input id="workdir" class="grow mono" value="${esc(S.workdir ?? (S.expRes ? S.expRes.path : ""))}" placeholder="Path to the experiment copy" aria-label="Working copy" spellcheck="false">
    <input id="confirm" placeholder="type APPLY" style="width:130px" aria-label="Type APPLY to confirm" autocomplete="off"><button class="btn danger" data-act="apply" id="apply-btn" disabled>Apply changes</button></div>
    <div class="tiny faint" style="margin-top:6px">The edit set is read from the capsule on the server by id; the browser cannot supply its own edits.</div>
    ${S.applyErr ? notice("bad", `<div style="margin-top:10px"><b>Not applied.</b> ${esc(S.applyErr)}</div>`) : ""}
    ${r ? notice(r.diff_matches_preview ? "ok" : "warn", `<div style="margin-top:10px"><b>Applied:</b> ${r.applied.map(esc).join(", ")} · snapshot <code>${esc(r.backup)}</code><div>${r.diff_matches_preview ? badge("applied diff equals the previewed diff", "ok") : badge("applied diff DIFFERS from preview: roll back and review", "bad")}</div><pre style="margin-top:8px;max-height:200px">${diffHtml(r.diff)}</pre></div>`) : ""}`);
  const rb = card("Step 3 · Rollback", `<div class="muted" style="margin-bottom:10px">Restores every file stored in a snapshot. Only snapshots inside the working copy's <code>.depguard-backup/</code> are accepted.</div>
    <div class="row"><input id="rb-backup" class="grow mono" value="${esc(S.rbPath ?? (r ? r.backup : ""))}" placeholder="Snapshot path" aria-label="Snapshot path" spellcheck="false"><input id="confirm-rb" placeholder="type ROLLBACK" style="width:140px" aria-label="Type ROLLBACK" autocomplete="off"><button class="btn danger" data-act="rollback" id="rb-btn" disabled>Roll back</button></div>
    ${S.rbErr ? notice("bad", `<div style="margin-top:10px">${esc(S.rbErr)}</div>`) : ""}${S.rbRes ? notice("ok", `<div style="margin-top:10px"><b>Restored:</b> ${S.rbRes.restored.map(esc).join(", ")} ${S.rbRes.diff_vs_snapshot === "" ? badge("identical to the snapshot", "ok") : badge("still differs from snapshot", "bad")}</div>`) : ""}
    <ul class="list flow tiny" style="margin-top:12px"><li>Manual rollback: copy the files from the snapshot folder back over the working copy.</li><li>In a git repo: <code>git diff</code> to review, <code>git checkout -- &lt;file&gt;</code> to discard.</li></ul>`);
  return pageHead("Migration workspace", "Review the proposed manifest changes, approve explicitly, apply to an isolated copy, verify the diff, and roll back if needed.") + `<div class="stack">${sel}${verdict}
    ${card("Diff preview (before → after)", `<pre>${diffHtml(p.diff)}</pre>`, p.kind === "ai" ? tag("ai") : tag("ver"))}
    ${p.rejected.length ? card("Edits rejected by validation (malformed manifests are refused before any test run)", `<ul class="list">${p.rejected.map(x => `<li><span class="dot" style="background:var(--bad)"></span><div><code>${esc(x.file)}</code> <span class="muted">${esc(x.reason)}</span></div></li>`).join("")}</ul>`, tag("obs")) : ""}
    ${warn}<div class="grid g2">${exp}${apply}</div>${rb}</div>`;
}

/* ---------------- history ---------------- */
function pHistory() {
  const d = S.data, h = d.history;
  const warn = d.history_warning ? notice("warn", `<div><b>History file problem.</b> ${esc(d.history_warning)}</div>`) : "";
  const body = h.length ? `<div class="tscroll"><table><thead><tr><th>When</th><th>Kind</th><th>Repository</th><th>Status</th><th>Findings summary</th><th>Capsule</th><th></th></tr></thead><tbody>${h.map(r => {
    const s = r.summary || {};
    return `<tr><td class="tiny">${fmtTime(r.time)}</td><td>${badge(r.kind, "info")}</td><td><b>${esc(r.repo.path_name)}</b><div class="tiny faint mono">${esc((r.repo.commit || "").slice(0, 7))} ${esc(r.repo.path || "")}</div></td>
      <td>${sBadge(r.status)}${r.error ? `<div class="tiny faint">${esc(r.error)}</div>` : ""}</td>
      <td class="tiny">${s.dependencies != null ? `${s.dependencies} deps · ${s.high} high · ${s.medium} medium · ${s.undeclared_imports} undeclared${s.health_score != null ? ` · health ${s.health_score} <span class="tag-heur">heuristic</span>` : ""}${s.baseline_status ? ` · baseline ${esc(stText(s.baseline_status))}` : ""}${s.ai_status ? ` · AI ${esc(stText(s.ai_status))}` : ""}` : '<span class="faint">no summary</span>'}</td>
      <td class="tiny">${r.capsule ? `<code>${esc(r.capsule.split(/[\\/]/).slice(-2).join("/"))}</code>` : '<span class="faint">summary only</span>'}</td>
      <td style="white-space:nowrap">${r.capsule ? `<button class="btn sm" data-act="histopen" data-id="${esc(r.id)}">Open</button> ` : ""}<button class="btn sm" data-act="histdel" data-id="${esc(r.id)}">Remove</button></td></tr>`;
  }).join("")}</tbody></table></div>` : empty("No scan history yet", "Records appear here after you run a scan or a full analysis from this dashboard. Nothing is pre-filled or simulated.", `<button class="btn primary" data-act="goto" data-to="scanner">Open scanner</button>`);
  return pageHead("Scan history", "Local record of scans and analyses run from this dashboard: timestamps, repository identity, finding counts and capsule locations. No source code or credentials are stored.") + `<div class="stack">${warn}${card("History", body, tag("obs"), "tight")}
    <div class="tiny muted">Removing a record deletes only the history entry, never the capsule files. Stored in <code>depguard/web/runs/history.json</code> (git-ignored).</div></div>`;
}

/* ---------------- replacement comparison ---------------- */
function cellHtml(c) {
  const v = Array.isArray(c.value) ? `<ul class="list flow tiny" style="gap:2px">${c.value.map(x => `<li>${esc(x)}</li>`).join("")}</ul>` : `<div>${esc(c.value)}</div>`;
  return `${v}<div style="margin-top:4px">${basisTag(c.basis)}</div>${c.note ? `<div class="tiny faint" style="margin-top:2px">${esc(c.note)}</div>` : ""}`;
}
function pCompare() {
  const c = cap(), v = view(); if (v.empty) return needCapsule("Replacement comparison");
  const X = S.cmp ||= { pkg: "", cands: "", fetch: false, res: null, err: null, busy: false };
  const declared = [...new Set(v.pkgs.filter(p => !p.undeclared).map(p => p.name))];
  if (!X.pkg) X.pkg = (v.pkgs.find(p => p.level === "high") || v.pkgs[0] || {}).name || "";
  const ai = (S.data.ai_candidates || {})[X.pkg];
  const chips = ai ? `<div style="margin-top:10px"><div class="tiny muted">Candidates suggested by Gemma 4 ${tag("ai")}</div><div class="row" style="margin-top:4px">${ai.names.map(n => `<button class="btn sm" data-act="cmpchip" data-name="${esc(n)}">+ ${esc(n)}</button>`).join("") || '<span class="faint tiny">none parsed</span>'}</div>${ai.unparseable.length ? `<div class="tiny faint" style="margin-top:6px">Not usable as package names (shown, not used): ${ai.unparseable.map(esc).join(" · ")}</div>` : ""}</div>` : `<div class="tiny faint" style="margin-top:10px">No Gemma 4 candidates recorded for this package.</div>`;
  const form = card("Choose what to compare", `<div class="row"><select id="cmp-pkg" aria-label="Current package">${declared.map(n => `<option ${n === X.pkg ? "selected" : ""}>${esc(n)}</option>`).join("")}</select>
    <input id="cmp-cands" class="grow mono" placeholder="candidate packages, comma-separated (e.g. mako, jinja2)" value="${esc(X.cands)}" aria-label="Candidate packages" spellcheck="false">
    <label class="chk"><input type="checkbox" id="cmp-fetch" ${X.fetch ? "checked" : ""} ${S.data.network.offline_flag ? "disabled" : ""}> Fetch metadata (network)</label>
    <button class="btn primary" data-act="cmprun" ${X.busy ? "disabled" : ""}>${X.busy ? '<span class="spin"></span>' : ""}Compare</button></div>${chips}
    ${S.data.network.offline_flag ? `<div class="tiny muted" style="margin-top:8px">Offline mode: only cached package metadata is used.</div>` : `<div class="tiny muted" style="margin-top:8px">Without “Fetch metadata”, no network is used and anything not already cached shows as unknown.</div>`}
    ${X.err ? notice("bad", `<div style="margin-top:10px">${esc(X.err)}</div>`) : ""}`);
  let res = "";
  const r = X.res;
  if (r) {
    res = card(`${esc(r.package)} vs ${r.columns.slice(1).map(esc).join(", ") || "(no candidates)"}`, `<div class="tscroll"><table><thead><tr><th>Criterion</th>${r.columns.map((n, i) => `<th>${esc(n)}${i === 0 ? ' <span class="faint">(current)</span>' : ""}</th>`).join("")}</tr></thead><tbody>${r.criteria.map(k => `<tr><td><b>${esc(k.label)}</b></td>${r.columns.map(n => `<td>${cellHtml(k.cells[n])}</td>`).join("")}</tr>`).join("")}</tbody></table></div>
      ${r.rejected_candidates.length ? `<div class="tiny faint" style="margin:8px 14px">Ignored (not valid package names): ${r.rejected_candidates.map(esc).join(" · ")}</div>` : ""}`, tag("obs"), "tight")
      + notice("warn", `<div><b>${esc(r.statement)}</b><div class="tiny" style="margin-top:6px">Unresolved questions:</div><ul class="list flow tiny">${r.unresolved.map(u => `<li>• ${esc(u)}</li>`).join("") || "<li>none recorded</li>"}</ul></div>`);
  } else res = card("Comparison", empty("Nothing compared yet", "Pick a package and candidates. The table lists documented evidence only: it never says a replacement is equivalent."));
  return pageHead("Replacement comparison", "Evidence-only comparison of candidate replacements: supported Python versions, declared requirements, the license the package declares, API surface in use, local availability, migration effort and executed tests.") + `<div class="stack">${form}${res}</div>`;
}

/* ---------------- capsule & reports ---------------- */
function pCapsule() {
  const d = S.data, c = d.capsule, v = d.validation; if (!c) return needCapsule("Reports & capsule");
  const e = (c.independent_experiments || [])[0];
  const vb = v.valid ? badge("Valid against capsule.schema.json", "ok") : badge("INVALID", "bad");
  const dl = (fmt, label) => `<a class="btn ${v.valid ? "" : "disabled"}" href="/api/report?format=${fmt}" download ${v.valid ? "" : 'aria-disabled="true" onclick="return false"'}>${label}</a>`;
  const head = pageHead("7 · Reports & capsule", `${esc(d.capsule_source.label)} · <span class="mono">${esc(d.capsule_source.path)}</span> · generated ${fmtTime(c.generated_at)}`,
    `<button class="btn" data-act="reload">Re-validate</button>${dl("html", "Download HTML")}${dl("json", "Download JSON")}${dl("md", "Download Markdown")}`)
    + `<div class="row" style="margin-bottom:14px">${vb}${v.error ? `<span class="tiny" style="color:var(--bad)">${esc(v.error)}</span>` : ""}<span class="tiny muted">Source:</span><button class="btn sm" data-act="select" data-which="demo">Committed demo</button><button class="btn sm" data-act="select" data-which="latest">Latest dashboard run</button></div>
    <div class="tiny muted" style="margin-bottom:14px">All three downloads are generated from this one validated capsule, so they cannot disagree. The HTML report is self-contained and escapes all repository-derived text. Invalid capsules are not exported.</div>`;
  const tabs = `<div class="tabs" role="tablist">${[["summary", "Rendered summary"], ["sections", "Observed · AI · Verified"], ["health", "Health score"], ["json", "Raw JSON"]].map(([k, t]) => `<button role="tab" class="${S.tab === k ? "on" : ""}" data-act="tab" data-tab="${k}">${t}</button>`).join("")}</div>`;
  let body;
  if (S.tab === "json") body = `<pre style="max-height:640px">${esc(JSON.stringify(c, null, 2))}</pre>`;
  else if (S.tab === "health") body = healthCard() || card("Health score", empty("Not available", "This capsule is invalid or has no health data."));
  else if (S.tab === "sections") body = `<div class="grid g3">
    ${card("Observations", `<ul class="list tiny"><li>${c.dependencies.items.length} declared entries across ${c.dependencies.manifests.map(esc).join(", ")}</li><li>${c.risk_evidence.indicators.filter(i => i.severity !== "info").length} non-informational indicators</li><li>Undeclared imports: ${c.usage.undeclared_imports.map(esc).join(", ") || "none"}</li><li>${c.compatibility_gaps.filter(g => g.startsWith("[observed]")).length} observed compatibility gaps</li><li>Package metadata: ${c.package_metadata ? Object.keys(c.package_metadata).length + " package(s), each with source and time" : "not fetched"}</li></ul>`, tag("obs"))}
    ${card("AI recommendations", `<ul class="list tiny"><li>Status: ${sBadge(c.ai_recommendations.status)}</li>${(c.ai_recommendations.recommendations || []).map(r => `<li>${esc(r.package)} → <b>${esc(r.action)}</b></li>`).join("")}<li>${c.suggested_tests.length} suggested tests (not run)</li><li>${c.compatibility_gaps.filter(g => g.startsWith("[AI")).length} AI-claimed concerns</li></ul>`, tag("ai"))}
    ${card("Independently verified", e ? `<ul class="list tiny"><li><b>${esc(e.name)}</b> <span class="faint">${esc(e.origin)}</span></li><li>Baseline: ${sBadge(e.baseline.status)} ${e.comparison.baseline_counts.passed} pass / ${e.comparison.baseline_counts.failed} fail</li><li>Proposed: ${sBadge(e.proposed.status)} ${e.comparison.proposed_counts.passed} pass / ${e.comparison.proposed_counts.failed} fail</li><li>Gemma's own proposal tested: ${sBadge(c.executed_tests.proposed.status)}</li></ul>` : empty("No experiment", ""), tag("ver"))}</div>`;
  else body = card("Capsule summary", `<div class="md">${md(d.capsule_markdown || "")}</div>`);
  return head + tabs + body + (typeof perDepCard === "function" ? perDepCard(c) : "");
}

/* ---------------- extra click/input handlers for the new pages ---------------- */
document.addEventListener("click", async ev => {
  const t = ev.target.closest("[data-act]"); if (!t) return;
  const a = t.dataset.act;
  const fail = e => toast(e.message, "bad");
  try {
    if (a === "fetchmeta") {
      if (!confirm("Fetch package metadata?\n\nThis contacts the public package index (pypi.org) over the network for each dependency name, and caches the result with its source and time. Nothing from your repository's source code is sent.")) return;
      toast("Fetching package metadata…");
      const r = await api("/api/metadata/fetch", {}); toast(`Metadata: ${r.ok}/${r.total} available` + (r.ok < r.total ? " (the rest are shown as unavailable, never guessed)" : ""), r.ok ? "ok" : "bad"); await load(true);
    } else if (a === "acquire") {
      S.ghUrl = $("#gh-url").value; S.acqErr = null; S.acqBusy = true; render();
      try { const r = await api("/api/acquire", { url: S.ghUrl }); S.path = r.path; toast(`Downloaded ${r.owner}/${r.repo} (${r.size_mb} MB). Not executed.`, "ok"); } catch (e) { S.acqErr = e.message; }
      S.acqBusy = false; await load(true);
    } else if (a === "acquse") { S.path = t.dataset.path; render(); toast("Path selected: now click Scan"); }
    else if (a === "acqrm") { if (!confirm("Remove this downloaded copy? Only the downloaded folder is deleted.")) return; await api("/api/acquire/remove", { path: t.dataset.path }); if (S.path === t.dataset.path) S.path = null; toast("Removed", "ok"); await load(true); }
    else if (a === "histopen") { await api("/api/history/open", { id: t.dataset.id }); await load(true); location.hash = "capsule"; toast("Capsule loaded from history", "ok"); }
    else if (a === "histdel") { if (!confirm("Remove this history record? The capsule files are not deleted.")) return; await api("/api/history/delete", { id: t.dataset.id }); toast("Record removed", "ok"); await load(true); }
    else if (a === "expcopy") { S.expSrc = $("#exp-src").value; S.expErr = null; try { S.expRes = await api("/api/experiment/create", { repo: S.expSrc }); S.workdir = S.expRes.path; toast("Experiment copy created", "ok"); await load(true); } catch (e) { S.expErr = e.message; S.expRes = null; render(); } }
    else if (a === "rollback") {
      S.rbPath = $("#rb-backup").value; S.rbErr = S.rbRes = null;
      if (!confirm("Restore the files from this snapshot into the working copy?")) return;
      try { S.rbRes = await api("/api/rollback", { workdir: S.workdir || $("#workdir").value, backup: S.rbPath, confirm: $("#confirm-rb").value }); toast("Rolled back", "ok"); } catch (e) { S.rbErr = e.message; }
      render();
    } else if (a === "cmpfor") { const X = S.cmp ||= { pkg: "", cands: "", fetch: false, res: null, err: null, busy: false }; X.pkg = t.dataset.name; X.res = null; X.cands = ""; $("#drawer").hidden = true; }
    else if (a === "cmpchip") { const X = S.cmp; const cur = X.cands.split(",").map(s => s.trim()).filter(Boolean); if (!cur.includes(t.dataset.name)) cur.push(t.dataset.name); X.cands = cur.join(", "); render(); }
    else if (a === "cmprun") {
      const X = S.cmp; X.pkg = $("#cmp-pkg").value; X.cands = $("#cmp-cands").value; X.fetch = $("#cmp-fetch").checked; X.err = null; X.busy = true; render();
      if (X.fetch && !confirm("Fetch metadata for these packages from the public package index (network)? Results are cached with source and time.")) { X.busy = false; render(); return; }
      try { X.res = await api("/api/compare", { package: X.pkg, candidates: X.cands.split(",").map(s => s.trim()).filter(Boolean), fetch: X.fetch }); await load(true); } catch (e) { X.err = e.message; X.res = null; }
      X.busy = false; render();
    }
  } catch (e) { fail(e); }
});
document.addEventListener("input", ev => {
  if (ev.target.id === "confirm-rb") { const b = $("#rb-btn"); if (b) b.disabled = ev.target.value !== "ROLLBACK"; }
});
document.addEventListener("change", ev => {
  if (ev.target.id === "cmp-pkg") { S.cmp.pkg = ev.target.value; S.cmp.res = null; render(); }
});
