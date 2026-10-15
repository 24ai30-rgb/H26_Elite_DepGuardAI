"use strict";
/* The core workflow: Repository -> Findings -> Ask Gemma -> Repair -> Verify -> Publish -> Reports & capsule.
   workflowModel() derives every status from recorded capsule / project data; nothing is invented. */

const WORKFLOW = [
  { id: "scan", route: "scanner", n: 1, title: "Repository" },
  { id: "risks", route: "inventory", n: 2, title: "Findings" },
  { id: "gemma", route: "advisor", n: 3, title: "Ask Gemma 4" },
  { id: "repair", route: "repair", n: 4, title: "Repair" },
  { id: "verify", route: "verify", n: 5, title: "Verify" },
  { id: "publish", route: "publish", n: 6, title: "Publish" },
  { id: "capsule", route: "capsule", n: 7, title: "Reports & capsule" },
];

function workflowModel(d) {
  const c = d.capsule, valid = c && d.validation && d.validation.valid;
  const live = d.scan;
  const st = {};
  const F = c && c.findings, patches = ((c && c.repair && c.repair.patches) || []);
  // ---- source of dependency facts: the loaded capsule, else the live scan
  let names = [], levels = {}, manifests = [], origin = null;
  if (c) { names = [...new Set(c.dependencies.items.map(i => i.name))]; levels = c.risk_evidence.package_levels; manifests = c.dependencies.manifests; origin = "capsule"; }
  else if (live) { names = [...new Set(live.scan.dependencies.map(i => i.name))]; levels = live.risk_evidence.package_levels; manifests = live.scan.manifests; origin = "live scan"; }
  const lv = n => (levels[n] || {}).level;
  const undeclaredList = c ? c.usage.undeclared_imports : live ? live.usage.undeclared_imports : [];
  const rated = [...new Set([...names, ...undeclaredList])];   // same population as the dependency inventory page
  const hi = rated.filter(n => lv(n) === "high").length, med = rated.filter(n => lv(n) === "medium").length;

  const P = d.project;
  st.scan = origin ? { status: "done", detail: `${names.length} declared packages from ${manifests.join(", ")} (${origin})${P ? ` · ${P.label} via ${String(P.method).replace(/_/g, " ")}${P.input && P.input.commit ? " @ " + P.input.commit.slice(0, 7) : ""}` : ""}` } : { status: "pending", detail: "No repository scanned yet" };
  if (!origin) st.risks = { status: "pending", detail: "Scan a repository first" };
  else if (F) {
    const n = k => F.filter(f => f.severity === k).length, s = k => F.filter(f => f.status === k).length;
    const unresolved = F.filter(f => ["critical", "high", "medium"].includes(f.severity) && ["open", "repair_proposed", "repair_failed"].includes(f.status)).length;
    st.risks = { status: unresolved ? "attention" : "done", detail: `${F.length} finding(s): ${n("critical")} critical · ${n("high")} high · ${n("medium")} medium · ${n("low")} low. Decisions: ${s("open")} open, ${s("ignored")} ignored, ${s("review_later")} later, ${s("repaired")} repaired` };
  } else st.risks = { status: hi + med ? "attention" : "done", detail: `${hi} high · ${med} medium${undeclaredList.length ? ` (includes ${undeclaredList.length} undeclared import: ${undeclaredList.join(", ")})` : ""}` };

  const ai = c && c.ai_recommendations;
  const recs = ai && ai.recommendations ? ai.recommendations : [];
  const mp = c && c.migration_proposal;
  const comb = c && c.executed_tests.proposed;
  const combined = comb && (comb.status === "passed" || comb.status === "failed") ? comb.status : null;
  const accepted = mp ? mp.accepted_edits : [];
  const covered = recs.filter(r => r.manifest_edits.some(e => accepted.some(a => a.file === e.file && a.find === e.find && a.replace === e.replace))).length;
  const untested = recs.length - (combined ? covered : 0);
  if (!c) st.gemma = { status: "pending", detail: "Gemma advice appears after a full analysis" };
  else if (ai.status === "ok" || ai.status === "partial") st.gemma = { status: untested || ai.status === "partial" ? "attention" : "done", detail: `${recs.length} recommendations · ${untested} without test evidence · all unverified AI claims${ai.status === "partial" ? " · some packages unanswered" : ""}` };
  else st.gemma = { status: ai.status === "not_requested" ? "pending" : "blocked", detail: ai.status === "not_requested" ? "AI was not requested for this run" : `Gemma 4 ${String(ai.status).replace(/_/g, " ")}: ${ai.reason || ""}` };

  // ---- repair: user-requested patches (legacy capsules: the earlier combined proposal)
  const live_p = patches.filter(p => !["rejected", "stale"].includes(p.status));
  const cntp = k => patches.filter(p => p.status === k).length;
  if (!c) st.repair = { status: "pending", detail: "No proposal yet" };
  else if (patches.length) st.repair = { status: cntp("applied") ? "done" : "attention", detail: `${patches.length} patch(es): ${cntp("proposed")} proposed · ${cntp("verified") + cntp("partially_verified")} verified · ${cntp("failed") + cntp("blocked")} failed/blocked · ${cntp("applied")} applied (working copy only) · ${cntp("rejected") + cntp("stale")} rejected/stale` };
  else if (accepted.length || (mp.rejected_edits || []).length) st.repair = { status: "attention", detail: `legacy combined proposal: ${accepted.length} edit(s) proposed, ${mp.rejected_edits.length} rejected by validation · nothing applied` };
  else st.repair = { status: "pending", detail: F && F.length ? "No repair requested yet: choose Repair, Ignore or Review later on a finding" : "No edits proposed" };

  // ---- verification outcomes, all from recorded runs
  const verdicts = [];
  if (c) {
    const cnt = r => { const v = Object.values(r.tests || {}); return `${v.filter(x => x === "passed").length} passed · ${v.filter(x => x === "failed").length} failed`; };
    const b = c.executed_tests.baseline;
    verdicts.push({ key: "baseline", label: "Baseline (original repository)", status: b.status, detail: b.status === "not_run" ? "not run" : `${cnt(b)} · pytest exit ${b.exit_code ?? "n/a"}`, route: "verify", run: "run-ai-baseline" });
    if (comb.status !== "not_run" || !patches.length) verdicts.push({ key: "ai", label: "Gemma 4 combined proposal", status: comb.status, detail: comb.status === "not_run" ? "not tested" : `pytest exit ${comb.exit_code ?? "n/a"}${comb.status === "passed" ? " · passing tests do not prove full compatibility" : " · NOT a successful migration"}`, route: "verify", run: "run-ai-proposed" });
    (c.independent_experiments || []).forEach((e, i) => verdicts.push({ key: "exp" + i, label: `Independent experiment: ${e.name}`, status: e.proposed.status, detail: `${cnt(e.proposed)} · pytest exit ${e.proposed.exit_code ?? "n/a"} · isolated copy, not applied; does not prove full compatibility`, route: "verify", run: `run-exp-${i}-proposed` }));
    live_p.filter(p => p.verification).forEach(p => {
      const v = p.verification, partial = v.status === "partially_verified";
      verdicts.push({ key: "patch:" + p.id, label: `Repair patch: ${p.package}`, status: v.status === "verified" || partial ? "passed" : v.status === "blocked" ? "environment_blocked" : "failed",
        detail: `${partial ? "partially verified: " : ""}${(v.reasons || []).join("; ")}${v.exit_code != null ? ` · pytest exit ${v.exit_code}` : ""} · does not prove full compatibility`, route: "repair", run: "" });
    });
  }
  // The baseline failing is context (the premise of a migration); the step's status reflects the proposals, plus a blocked baseline.
  const bad = v => v.status === "failed" || v.status === "environment_blocked";
  const anyFailed = verdicts.some(v => v.key !== "baseline" && bad(v)) || verdicts.some(v => v.key === "baseline" && v.status === "environment_blocked");
  const anyPassed = verdicts.some(v => v.status === "passed" && v.key !== "baseline");
  st.verify = !c ? { status: "pending", detail: "No verification recorded" }
    : { status: anyFailed ? "failed" : anyPassed ? "done" : "pending", detail: verdicts.filter(v => v.key !== "baseline").map(v => `${v.label.replace("Independent experiment: ", "").replace("Repair patch: ", "patch ")}: ${v.status.replace(/_/g, " ")}`).join(" · ") || "no proposal tested" };

  // ---- publish
  const applied = patches.filter(p => p.status === "applied"), pub = d.publish || {}, pp = c && c.prepublish;
  st.publish = !c ? { status: "pending", detail: "Nothing to publish yet" }
    : pub.pr ? { status: "done", detail: `Pull request #${pub.pr.number} opened (branch ${pub.branch ? pub.branch.branch : "?"})` }
      : applied.length ? { status: "attention", detail: `${applied.length} applied patch(es) ready to export or publish · pre-publish checks: ${pp ? pp.status + (pp.stop_recommended ? " (stop recommended)" : "") : "not run"}` }
        : { status: "pending", detail: "Apply a reviewed patch to the working copy to export or publish it" };
  st.capsule = !c ? { status: "pending", detail: "No capsule yet" } : valid ? { status: "done", detail: `Valid · JSON, Markdown and HTML${F ? ` · ${F.length} per-dependency capsule(s)` : ""}` } : { status: "failed", detail: `Invalid: ${d.validation.error}` };

  const steps = WORKFLOW.map(w => ({ ...w, ...st[w.id] }));

  // ---- findings and whether any tested change addresses them
  const exps = c ? (c.independent_experiments || []) : [];
  const mentions = (edits, pkg) => edits.some(e => `${e.find} ${e.replace}`.toLowerCase().includes(pkg));
  const open = c ? [...new Set(c.risk_evidence.indicators.filter(i => i.severity !== "info").map(i => i.package))].map(pkg => {
    const fx = exps.find(e => e.proposed.status === "passed" && mentions(e.edits, pkg));
    const ai = accepted.length && mentions(accepted, pkg);
    return {
      package: pkg, findings: c.risk_evidence.indicators.filter(i => i.package === pkg && i.severity !== "info").map(i => i.indicator),
      state: fx ? "fix verified in an isolated experiment (not applied)" : ai ? (combined === "failed" ? "AI fix failed verification" : combined === "passed" ? "AI fix passed tests (limited evidence)" : "AI fix proposed, untested") : "open",
      kind: fx ? "tested" : ai && combined === "failed" ? "failed" : "open",
    };
  }) : [];

  // ---- what is NOT known
  const meta = d.metadata || {};
  const nmeta = Object.values(meta).filter(m => m.status === "ok").length;
  const missing = [];
  if (c || live) {
    missing.push(nmeta ? `Package index metadata exists for ${nmeta} package(s); the rest is unchecked.` : "Package maintenance / release recency: not checked (no package metadata fetched).");
    missing.push("Repository activity (commits, issues, maintainers): never checked.");
    const u = c ? c.usage : live.usage;
    if (u.no_import_found.length) missing.push(`No static import found for ${u.no_import_found.join(", ")}: uncertain, NOT proof they are unused.`);
    if (u.dynamic_import_sites.length) missing.push(`${u.dynamic_import_sites.length} dynamic import site(s) cannot be resolved statically.`);
  }
  if (c) missing.push("Verification uses a virtual environment in a temporary copy, not a container or sandbox; only the project's own tests are exercised.");
  if (c && !c.executed_tests.baseline.environment) missing.push("This capsule predates environment recording: interpreter and platform of the test runs are not recorded.");
  if (c && F) {
    const nv = c.version_metadata ? Object.keys(c.version_metadata).length : 0;
    missing.push(nv ? `Advisory / yanked status was checked for ${nv} exact version(s) (index-listed, not an audit); no listed advisory is NOT proof of safety.` : "Known-advisory / yanked status of exact versions: not checked (no exact-version metadata fetched).");
    const un = (c.dependencies.unsupported_formats || []);
    if (un.length) missing.push(`Unsupported formats present and not analysed: ${un.map(x => x.file).join(", ")}.`);
    if (!(c.dependencies.lockfiles || []).length) missing.push("No lockfile was found, so resolved versions come only from the verification environment.");
  }

  let next;
  const openSerious = F && F.some(f => ["critical", "high", "medium"].includes(f.severity) && f.status === "open");
  if (!origin) next = { label: "Open a repository", route: "scanner" };
  else if (!c) next = { label: "Run an analysis", route: "scanner" };
  else if (anyFailed) next = { label: "Review failed verification", route: "verify" };
  else if (patches.some(p => p.status === "proposed")) next = { label: "Review proposed patches", route: "repair" };
  else if (openSerious) next = { label: "Review findings", route: "inventory" };
  else if (applied.length) next = { label: "Export or publish", route: "publish" };
  else next = { label: "Export the Exit Capsule", route: "capsule" };
  return { steps, verdicts, open, missing, next, anyFailed, hasData: !!origin, hasCapsule: !!c };
}

function stepper(current) {
  if (!S.data) return "";
  const M = workflowModel(S.data);
  const note = !S.data.project && S.data.capsule ? `<div class="tiny muted" style="margin:-10px 0 14px 4px">Showing <b>${esc(S.data.capsule_source.label)}</b>: no repository is open. Open one on step 1 to analyse your own.</div>` : "";
  return note ? `<div>${stepperNav(M)}${note}</div>` : stepperNav(M);
}

function stepperNav(M) {
  const mark = { done: "✓", failed: "✕", attention: "!", blocked: "!", pending: "" };
  const current = S.route;
  return `<nav class="stepper" aria-label="Workflow">${M.steps.map(s => `<a href="#${s.route}" class="step st-${s.status} ${s.route === current ? "cur" : ""}" ${s.route === current ? 'aria-current="step"' : ""} title="${esc(s.detail)}"><span class="dotn">${mark[s.status] || s.n}</span><span class="lbl">${s.n}. ${esc(s.title)}</span></a>`).join("")}</nav>`;
}

function pOverview() {
  const d = S.data, M = workflowModel(d);
  const stKind = { done: "ok", failed: "bad", attention: "warn", blocked: "bad", pending: "na" };
  const stLabel = { done: "done", failed: "failed", attention: "needs attention", blocked: "blocked", pending: "not started" };
  const c = d.capsule;
  const hero = M.hasData ? "" : card("Start here", empty("No repository open yet", "DepGuard finds at-risk dependencies in a real repository, explains them, lets you choose Repair, Ignore or Review later for each, tests every proposed fix against a baseline in isolated copies, and prepares a patch or a pull request only when you approve. Everything ends in an evidence-backed Exit Capsule.", `<button class="btn primary" data-act="goto" data-to="scanner">Open a repository</button> <button class="btn" data-act="select" data-which="demo">Load the committed LinkWatch capsule</button>`));
  const failed = M.verdicts.filter(v => v.status === "failed" || v.status === "environment_blocked");
  const banner = failed.length ? notice("bad", `<div><b>Verification failed. ${failed.length} run(s) did not pass.</b><ul class="list flow tiny" style="margin-top:6px">${failed.map(v => `<li>• <b>${esc(v.label)}</b>: ${sBadge(v.status)} ${esc(v.detail)} ${v.run ? `<a href="#verify" data-act="vlab" data-run="${esc(v.run)}">see the run →</a>` : `<a href="#${esc(v.route)}">see it →</a>`}</li>`).join("")}</ul><div class="tiny" style="margin-top:6px">A failed proposal must not be treated as a successful migration.</div></div>`) : "";
  const verdicts = M.verdicts.length ? card("Verification outcomes", `<div class="tscroll"><table><thead><tr><th>Run</th><th>Result</th><th>Detail</th></tr></thead><tbody>${M.verdicts.map(v => `<tr><td><b>${esc(v.label)}</b></td><td>${sBadge(v.status)}</td><td class="tiny">${esc(v.detail)} ${v.run ? `<a href="#verify" data-act="vlab" data-run="${esc(v.run)}">view →</a>` : `<a href="#${esc(v.route)}">view →</a>`}</td></tr>`).join("")}</tbody></table></div>`, tag("ver"), "tight") : "";
  const steps = `<div class="stepgrid">${M.steps.map(s => `<section class="card stepcard st-${s.status}"><div class="row" style="justify-content:space-between"><span class="stepn">${s.n}</span>${badge(stLabel[s.status], stKind[s.status])}</div><h2>${esc(s.title)}</h2><div class="tiny muted" style="min-height:34px">${esc(s.detail)}</div><a class="btn sm" href="#${s.route}">Open →</a></section>`).join("")}</div>`;
  const findings = M.open.length ? card("Findings and their status", `<div class="tscroll"><table><thead><tr><th>Package</th><th>Observed finding</th><th>Status of any tested fix</th></tr></thead><tbody>${M.open.map(f => `<tr class="click" data-act="pkg" data-name="${esc(f.package)}"><td><b>${esc(f.package)}</b></td><td class="tiny">${f.findings.map(x => esc(stText(x))).join(", ")}</td><td>${badge(f.state, f.kind === "tested" ? "ok" : f.kind === "failed" ? "bad" : "warn")}</td></tr>`).join("")}</tbody></table></div><div class="tiny muted" style="padding:10px 14px">A finding stays open until a change is independently tested. "Verified in isolation" means the project's tests passed on a separate copy; it does not prove full compatibility.</div>`, tag("obs"), "tight") : "";
  const missing = M.missing.length ? notice("warn", `<div><b>Missing or uncertain evidence</b><ul class="list flow tiny" style="margin-top:6px">${M.missing.map(m => `<li>• ${esc(m)}</li>`).join("")}</ul></div>`) : "";
  const h = (typeof healthOf === "function") ? healthOf() : null;
  const sec = `<details class="modeld"><summary>Secondary information (not part of the core workflow)</summary><div style="padding:12px" class="tiny"><ul class="list flow">
    <li>• Heuristic health score: ${h ? `<b>${h.score}/100</b>, evidence coverage ${h.evidence_coverage.percent}%. A heuristic, not a guarantee. <a href="#deps">details under Dependency inventory</a>` : "not available"}</li>
    <li>• Network: scanning, reports and Gemma 4 advice work offline; package metadata, GitHub access and package installation during verification need a connection and are never fetched automatically${d.network.offline_flag ? " (offline mode is ON)" : ""}.</li>
    <li>• Scan history: ${d.history.length} local record(s). <a href="#history">open</a> · LinkWatch status: <a href="#linkwatch">open</a> · Replacement comparison: <a href="#compare">open</a></li></ul></div></details>`;
  const sub = c ? `Target: <b>${esc(c.repository.path_name)}</b> · ${c.repository_input && c.repository_input.commit ? `commit <span class="mono">${esc(c.repository_input.commit.slice(0, 7))}</span>` : c.repository.commit ? `commit <span class="mono">${esc(c.repository.commit.slice(0, 7))}</span>` : "no commit (content fingerprint)"} · ${esc(d.capsule_source.label)}` : "Repository → Findings → Ask Gemma → Repair → Verify → Publish → Reports & capsule";
  return pageHead("Workflow overview", sub, `<button class="btn primary" data-act="goto" data-to="${M.next.route}">${esc(M.next.label)} →</button>`)
    + `<div class="stack">${hero}${banner}${steps}${verdicts}${findings}${missing}${sec}</div>`;
}
