"use strict";
/* AI Advisor page: Gemma 4 claims kept apart from independently verified experiments.
   Loaded before app.js; uses S, esc, card, badge, tag, ... from app.js/pages.js at call time. */

/* Pure data model: derives every status from recorded capsule fields; nothing is invented. */
function advisorModel(c) {
  const a = c.ai_recommendations, comb = c.executed_tests.proposed, mp = c.migration_proposal;
  const same = (x, y) => x.file === y.file && x.find === y.find;
  const combined = comb.status === "passed" || comb.status === "failed" ? comb.status : null;
  const recs = (a.recommendations || []).map(r => {
    const edits = r.manifest_edits.map(e => {
      const acc = mp.accepted_edits.find(x => same(x, e) && x.replace === e.replace);
      const rej = mp.rejected_edits.find(x => same(x, e));
      return { ...e, state: acc ? "accepted" : rej ? "rejected" : "unevaluated", reason: rej ? rej.reason : null };
    });
    const accepted = edits.filter(e => e.state === "accepted").length;
    let status = "unverified", why = null;
    if (accepted && combined) status = combined;
    else if (!r.manifest_edits.length) why = "No concrete edit was proposed, so there was nothing to test.";
    else if (!accepted) why = "Every proposed edit was rejected by validation before any test ran.";
    else why = `The combined proposal's run is "${stText(comb.status)}", so there is no pass/fail evidence.`;
    const evidence = c.risk_evidence.indicators.filter(i => i.package === r.package.toLowerCase() && i.severity !== "info");
    return { r, edits, accepted, status, why, evidence, imports: (c.usage.usages[r.package.toLowerCase()] || []).length };
  });
  const exps = (c.independent_experiments || []).map((e, i) => ({ e, i, status: e.proposed.status === "passed" || e.proposed.status === "failed" ? e.proposed.status : "unverified" }));
  const tested = [...(combined ? [{ kind: "ai", status: combined }] : []), ...exps.filter(x => x.status !== "unverified").map(x => ({ kind: "exp", status: x.status }))];
  const n = (l, s) => l.filter(x => x.status === s).length;
  return {
    recs, exps, combined, comb,
    counts: {
      recs: recs.length, unverified: n(recs, "unverified"), coveredByCombined: recs.filter(x => x.status !== "unverified").length,
      testedProposals: tested.length, passed: n(tested, "passed"), failed: n(tested, "failed"),
      filter: { all: recs.length + exps.length, unverified: n(recs, "unverified") + n(exps, "unverified"), passed: n(recs, "passed") + n(exps, "passed"), failed: n(recs, "failed") + n(exps, "failed") },
    },
  };
}

const errorLines = run => {
  const L = (run.output || "").split("\n");
  const e = L.filter(l => l.startsWith("E ")).slice(0, 4);
  return (e.length ? e : L.filter(l => l.trim()).slice(-5)).map(l => l.trim());
};
const pytestCmd = run => { const s = (run.steps || []).filter(x => x.command.includes("pytest")).pop(); return s ? ["python", ...s.command.slice(1)].join(" ") : null; };
const vlabBtn = (runId, label) => `<button class="btn sm" data-act="vlab" data-run="${esc(runId)}">${esc(label)} →</button>`;
const vKind = { passed: "ok", failed: "bad", unverified: "na" };
const vText = { passed: "tested · passed", failed: "tested · failed", unverified: "unverified" };

function runEvidence(run, runId, lead) {
  const lines = errorLines(run), bad = run.status !== "passed";
  return `<div class="runev"><div class="tiny"><b>${esc(lead)}</b> pytest exit <span class="mono">${esc(run.exit_code ?? "n/a")}</span>${run.failure_kind ? ` <span class="faint">(${esc(stText(run.failure_kind))})</span>` : ""}${bad && lines[0] ? ` · <span class="mono errline">${esc(lines[0])}</span>` : ""}</div>
    <details><summary>Command and captured output</summary><div class="dbody"><div class="tiny muted">Command</div><div class="mono tiny cmd">${esc(pytestCmd(run) || "not recorded")}</div>${bad ? `<pre class="errout" aria-label="Captured error output">${esc(lines.join("\n"))}</pre>` : '<div class="tiny faint" style="margin-top:6px">Run passed; no error output.</div>'}</div></details>
    ${vlabBtn(runId, "Open in Verification Lab")}</div>`;
}

function recCard(m, M) {
  const r = m.r, st = m.status;
  const ev = m.evidence.length
    ? m.evidence.slice(0, 2).map(i => `<div class="tiny"><b>${esc(stText(i.indicator))}</b> <span class="faint">${esc((i.evidence[0] || "").slice(0, 140))}</span></div>`).join("")
    : `<span class="tiny faint">no risk indicator observed${m.imports ? "" : "; no static import found"}</span>`;
  const verif = st === "unverified"
    ? `<div class="tiny">${esc(m.why)}</div>`
    : `<div class="tiny">Tested <b>only as part of the combined AI proposal</b> (${M.counts.coveredByCombined} recommendations changed together). Result ${sBadge(st)}; it cannot be attributed to this recommendation alone.</div>${runEvidence(M.comb, "run-ai-proposed", "Combined-proposal run:")}`;
  const editHtml = m.edits.length
    ? m.edits.map(e => `<div class="edit"><div class="tiny mono">${esc(e.file)} ${e.state === "accepted" ? badge("accepted · was tested", "info") : e.state === "rejected" ? badge("rejected · not tested", "bad") : badge("not evaluated", "na")}</div><pre>- ${esc(e.find)}\n+ ${esc(e.replace)}</pre>${e.reason ? `<div class="tiny faint">${esc(e.reason)}</div>` : ""}</div>`).join("")
    : `<span class="tiny faint">No concrete edits proposed.</span>`;
  const list = (items, none) => items.length ? `<ul>${items.map(s => `<li>${esc(s)}</li>`).join("")}</ul>` : `<span class="tiny faint">${none}</span>`;
  return `<article class="rec rec-${st}" aria-label="${esc(r.package)} recommendation, ${vText[st]}">
    <header class="rec-h"><span class="mono rec-pkg">${esc(r.package)}</span>${badge(r.action, r.action === "keep" || r.action === "investigate" ? "na" : "ai")}<span class="spacer"></span>${badge(vText[st], vKind[st])}</header>
    <p class="rec-why">${esc(r.rationale)}</p>
    <dl class="kv kv-sm"><dt>Observed evidence</dt><dd>${ev}</dd>
      <dt>Proposed edits</dt><dd>${m.edits.length ? `${m.edits.length} (${m.accepted} accepted, ${m.edits.length - m.accepted} rejected)` : "none"}</dd>
      <dt>Verification</dt><dd>${verif}</dd></dl>
    ${r.replacement_candidates.length ? `<div class="row" style="gap:6px;margin:8px 0 0"><span class="tiny muted">Candidates (unverified):</span>${r.replacement_candidates.map(x => `<span class="badge b-ai">${esc(x)}</span>`).join("")}</div>` : ""}
    <details><summary>Proposed edits (${m.edits.length})</summary><div class="dbody">${editHtml}</div></details>
    <details><summary>Compatibility concerns, model-claimed (${r.compatibility_concerns.length})</summary><div class="dbody">${list(r.compatibility_concerns, "none stated")}</div></details>
    <details><summary>Suggested tests, not run (${r.suggested_tests.length})</summary><div class="dbody">${list(r.suggested_tests, "none")}</div></details>
    ${r.migration_steps.length ? `<details><summary>Migration steps, model text (${r.migration_steps.length})</summary><div class="dbody"><ol>${r.migration_steps.map(s => `<li>${esc(s)}</li>`).join("")}</ol></div></details>` : ""}
    <div class="rec-f"><a href="#inventory" class="tiny" data-act="goto" data-to="inventory">Open this finding to repair →</a> · <a href="#compare" class="tiny" data-act="cmpfor" data-name="${esc(r.package)}">Compare replacements →</a></div></article>`;
}

function expCard(x) {
  const e = x.e, k = e.comparison, st = x.status;
  const cnt = c => `${c.passed} passed / ${c.failed} failed`;
  return `<article class="rec rec-${st}" aria-label="${esc(e.name)} experiment, ${vText[st]}">
    <header class="rec-h"><span class="mono rec-pkg">${esc(e.name)}</span>${badge("isolated experiment", "ver")}<span class="spacer"></span>${badge(st === "unverified" ? "not conclusive" : "verified · " + st, vKind[st])}</header>
    <p class="rec-why">${esc(e.origin)}. Not AI-generated. Run in a separate temporary copy and environment; the original manifests were not touched.</p>
    <dl class="kv kv-sm"><dt>Edits</dt><dd>${e.edits.map(d => `<div class="tiny mono">${esc(d.file)}: <span class="del">${esc(d.find)}</span> → <span class="add">${esc(d.replace)}</span></div>`).join("")}</dd>
      <dt>Baseline</dt><dd>${sBadge(e.baseline.status)} ${esc(cnt(k.baseline_counts))} · exit ${esc(e.baseline.exit_code ?? "n/a")}</dd>
      <dt>Proposed</dt><dd>${sBadge(e.proposed.status)} ${esc(cnt(k.proposed_counts))} · exit ${esc(e.proposed.exit_code ?? "n/a")}</dd>
      <dt>Result</dt><dd class="tiny">${k.fixed.length ? `fixed: ${k.fixed.map(esc).join(", ")}; ` : ""}regressed: ${k.regressed.length}. <b>Passing tests do not prove full compatibility</b>; this covers only the project's own test suite.</dd></dl>
    ${runEvidence(e.baseline, `run-exp-${x.i}-baseline`, "Baseline run:")}${runEvidence(e.proposed, `run-exp-${x.i}-proposed`, "Proposed run:")}</article>`;
}

function pAdvisor() {
  const c = cap(); if (!c) return needCapsule("AI Advisor");
  const a = c.ai_recommendations, cm = c.model, m = S.data.model, M = advisorModel(c), K = M.counts;
  const F = S.advFilter || "all", show = s => F === "all" || s === F;
  const expDone = M.exps.filter(x => x.status !== "unverified");
  const sum = `<div class="sumgrid" role="group" aria-label="Summary">
    <div class="card stat sum"><div class="l">AI recommendations</div><div class="v">${K.recs}</div><div class="d">all AI-generated claims</div></div>
    <div class="card stat sum"><div class="l">Unverified</div><div class="v" style="color:var(--muted)">${K.unverified}</div><div class="d">no test evidence recorded</div></div>
    <div class="card stat sum"><div class="l">Proposals tested</div><div class="v">${K.testedProposals}</div><div class="d">${M.combined ? "1 combined AI proposal" : "no AI proposal tested"}${expDone.length ? ` + ${expDone.length} independent experiment(s)` : ""}</div></div>
    <div class="card stat sum ok"><div class="l">Passed</div><div class="v">${K.passed}</div><div class="d">${expDone.filter(x => x.status === "passed").map(x => esc(x.e.name)).join(", ") || "none"}</div></div>
    <div class="card stat sum bad"><div class="l">Failed</div><div class="v">${K.failed}</div><div class="d">${[M.combined === "failed" ? "combined AI proposal" : "", ...expDone.filter(x => x.status === "failed").map(x => esc(x.e.name))].filter(Boolean).join(", ") || "none"}</div></div></div>
    ${M.combined ? `<div class="tiny muted" style="margin:8px 0 0">Gemma's edits were tested <b>once, as a single combined change</b> (${K.coveredByCombined} of ${K.recs} recommendations had an accepted edit). A failed combined run is not attributable to any one recommendation.</div>` : ""}`;
  const chip = (k, l) => `<button class="chip" data-act="advfilter" data-f="${k}" aria-pressed="${F === k}">${l} <span class="n">${K.filter[k]}</span></button>`;
  const filters = `<div class="chips" role="group" aria-label="Filter recommendations">${chip("all", "All")}${chip("unverified", "Unverified")}${chip("passed", "Passed")}${chip("failed", "Failed")}</div>`;
  const rs = M.recs.filter(x => show(x.status)), es = M.exps.filter(x => show(x.status));
  let aiSec;
  if (a.status !== "ok") aiSec = card("Recommendations", empty(a.status === "not_requested" ? "AI was not requested for this run" : "No recommendations: " + stText(a.status), esc(a.reason || "The deterministic parts of the capsule are unaffected. No fallback is presented as AI.")));
  else aiSec = `<section aria-labelledby="h-ai"><div class="sec-h"><h2 id="h-ai">AI-generated recommendations</h2>${tag("ai")}<span class="tiny muted">Gemma 4, local. Suggestions, not facts. The model supplies no confidence scores.</span></div>
    ${rs.length ? `<div class="recgrid">${rs.map(x => recCard(x, M)).join("")}</div>` : `<div class="card">${empty("No recommendations match this filter", `${K.recs} exist under “All”.`)}</div>`}</section>`;
  const expSec = `<section aria-labelledby="h-ex"><div class="sec-h"><h2 id="h-ex">Independently verified experiments</h2>${tag("ver")}<span class="tiny muted">Human-specified and tested in isolation. Kept separate from the AI proposal.</span></div>
    ${es.length ? `<div class="recgrid">${es.map(expCard).join("")}</div>` : `<div class="card">${empty(M.exps.length ? "No experiments match this filter" : "No independent experiment recorded", M.exps.length ? "" : "Run python -m depguard.experiments.jinja2_only to record one.")}</div>`}</section>`;
  const model = `<details class="modeld"><summary>Model details</summary><dl class="kv kv-sm" style="padding:12px"><dt>Model</dt><dd class="mono">${esc(cm.model)}</dd><dt>Runtime</dt><dd>${esc(cm.runtime || "—")} ${esc(cm.runtime_version || "")}</dd><dt>Parameters / quant</dt><dd>${esc(cm.parameter_size || "—")} · ${esc(cm.quantization || "—")}</dd><dt>Access</dt><dd>${esc(cm.access || "—")}</dd><dt>Source</dt><dd>${esc(cm.source || "—")}</dd><dt>License text</dt><dd class="tiny">${esc(cm.license_excerpt || "—")} <span class="faint">(bundled with the local model; not cross-checked)</span></dd><dt>Status now</dt><dd>${m.available ? badge("model reachable", "ok") : badge("unavailable", "bad")} <span class="tiny muted">${m.available ? "" : esc(m.blocker)}</span></dd></dl></details>`;
  return pageHead("AI Advisor", "Gemma 4 recommendations, kept apart from independently verified experiments. Every status shown comes from a recorded run.") + `<div class="stack">${sum}${filters}${aiSec}${expSec}${model}</div>`;
}

document.addEventListener("click", ev => {
  const t = ev.target.closest("[data-act]"); if (!t) return;
  if (t.dataset.act === "advfilter") { S.advFilter = t.dataset.f; render(); }
  else if (t.dataset.act === "vlab") { S.scrollTo = t.dataset.run; if (S.route === "verify") render(); else location.hash = "verify"; }
});
