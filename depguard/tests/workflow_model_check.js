// Loads depguard/web/static/workflow.js in a sandbox and prints workflowModel results. Used by test_workflow_ui.py.
const fs = require("fs"), vm = require("vm"), path = require("path");
const src = fs.readFileSync(path.join(__dirname, "..", "web", "static", "workflow.js"), "utf8");
const ctx = vm.createContext({ S: {}, esc: s => String(s), console });
vm.runInContext(src + "\nglobalThis.__x = { workflowModel };", ctx);
const { workflowModel } = ctx.__x;
const demo = JSON.parse(fs.readFileSync(path.join(__dirname, "..", "..", "demo-output", "capsule.json"), "utf8"));
const clone = o => JSON.parse(JSON.stringify(o));
const base = { capsule: demo, validation: { valid: true, error: null }, scan: null, metadata: {}, history: [], network: {}, acquired: [] };
const brief = M => ({
  steps: Object.fromEntries(M.steps.map(s => [s.id, s.status])),
  verdicts: M.verdicts.map(v => [v.key, v.status, v.detail]),
  open: Object.fromEntries(M.open.map(f => [f.package, f.kind + "|" + f.state])),
  missing: M.missing, next: M.next.route, anyFailed: M.anyFailed,
});
const out = { demo: brief(workflowModel(base)), details: Object.fromEntries(workflowModel(base).steps.map(s => [s.id, s.detail])) };
out.empty = brief(workflowModel({ capsule: null, validation: { valid: null }, scan: null, metadata: {}, history: [], network: {}, acquired: [] }));
const passed = clone(base); passed.capsule = clone(demo); passed.capsule.executed_tests.proposed.status = "passed";
out.combinedPassed = brief(workflowModel(passed));
const notTested = clone(base); notTested.capsule = clone(demo); notTested.capsule.executed_tests.proposed = { status: "not_run" };
notTested.capsule.independent_experiments = [];
out.nothingTested = brief(workflowModel(notTested));
const invalid = clone(base); invalid.validation = { valid: false, error: "bad" };
out.invalid = brief(workflowModel(invalid));
const noai = clone(base); noai.capsule = clone(demo); noai.capsule.ai_recommendations = { status: "blocked", reason: "Ollama down" };
out.noai = { steps: brief(workflowModel(noai)).steps, detail: workflowModel(noai).steps.find(s => s.id === "gemma").detail };
const meta = clone(base); meta.metadata = { jinja2: { status: "ok" }, six: { status: "ok" } };
out.withMeta = workflowModel(meta).missing[0];
// ---- findings + repair state (new capsules)
const mkF = (id, pkg, severity, status) => ({ id, package: pkg, severity, status, title: pkg + " problem" });
const mkP = (id, pkg, status, vstatus, reasons) => ({ id, package: pkg, finding_id: "f-" + pkg, status, verification: vstatus ? { status: vstatus, reasons: reasons || [], exit_code: vstatus === "failed" ? 4 : 0 } : null });
const fresh = () => { const x = clone(base); x.capsule = clone(demo); x.capsule.executed_tests.proposed = { status: "not_run" }; x.capsule.migration_proposal = { status: "none", accepted_edits: [], rejected_edits: [], diff: "" }; x.capsule.independent_experiments = []; return x; };
const nf = fresh(); nf.capsule.findings = [mkF("f-a", "a", "high", "open"), mkF("f-b", "b", "medium", "ignored"), mkF("f-c", "c", "low", "open")];
nf.capsule.repair = { decisions: {}, patches: [], attempts: [] };
out.findingsOnly = { ...brief(workflowModel(nf)), risks: workflowModel(nf).steps.find(s => s.id === "risks").detail, repair: workflowModel(nf).steps.find(s => s.id === "repair").detail };
const allDecided = fresh(); allDecided.capsule.findings = [mkF("f-a", "a", "high", "repaired"), mkF("f-b", "b", "medium", "ignored")]; allDecided.capsule.repair = { decisions: {}, patches: [], attempts: [] };
out.allDecided = brief(workflowModel(allDecided)).steps;
const proposed = fresh(); proposed.capsule.findings = [mkF("f-a", "a", "high", "repair_proposed")]; proposed.capsule.repair = { decisions: {}, patches: [mkP("p-1", "a", "proposed", null)], attempts: [] };
out.proposedPatch = { ...brief(workflowModel(proposed)), repairDetail: workflowModel(proposed).steps.find(s => s.id === "repair").detail };
const failedPatch = fresh(); failedPatch.capsule.findings = [mkF("f-a", "a", "high", "repair_failed")]; failedPatch.capsule.repair = { decisions: {}, patches: [mkP("p-1", "a", "failed", "failed", ["the proposed run still fails"])], attempts: [] };
out.failedPatch = brief(workflowModel(failedPatch));
const rejectedFailed = fresh(); rejectedFailed.capsule.findings = []; rejectedFailed.capsule.repair = { decisions: {}, patches: [mkP("p-1", "a", "rejected", "failed", ["x"])], attempts: [] };
out.rejectedFailedPatch = brief(workflowModel(rejectedFailed)).steps;       // a rejected patch must not keep verification 'failed'
const applied = fresh(); applied.capsule.findings = [mkF("f-a", "a", "high", "repaired")]; applied.capsule.repair = { decisions: {}, patches: [mkP("p-1", "a", "applied", "verified", ["all tests passed"])], attempts: [] };
out.appliedPatch = { ...brief(workflowModel(applied)), publishDetail: workflowModel(applied).steps.find(s => s.id === "publish").detail };
applied.capsule.prepublish = { status: "warn", stop_recommended: false, checks: [] };
out.appliedWithPre = workflowModel(applied).steps.find(s => s.id === "publish").detail;
applied.publish = { plan: {}, branch: { branch: "depguard/x" }, pr: { number: 7 } };
out.published = workflowModel(applied).steps.find(s => s.id === "publish");
const partial = fresh(); partial.capsule.findings = [mkF("f-a", "a", "high", "repair_proposed")]; partial.capsule.repair = { decisions: {}, patches: [mkP("p-1", "a", "partially_verified", "partially_verified", ["1 test was skipped"])], attempts: [] };
out.partialPatch = brief(workflowModel(partial)).verdicts;
const advis = fresh(); advis.capsule.version_metadata = { "a==1": {} }; advis.capsule.findings = [mkF("f-a", "a", "high", "open")]; advis.capsule.dependencies.unsupported_formats = [{ file: "Pipfile" }]; advis.capsule.dependencies.lockfiles = [];
out.advisoryMissing = workflowModel(advis).missing;
const noadv = fresh(); noadv.capsule.findings = [mkF("f-a", "a", "high", "open")];
out.noAdvisory = workflowModel(noadv).missing;
console.log(JSON.stringify(out));
