// Loads depguard/web/static/advisor.js in a sandbox and prints advisorModel results as JSON. Used by test_advisor_ui.py.
const fs = require("fs"), vm = require("vm"), path = require("path");
const src = fs.readFileSync(path.join(__dirname, "..", "web", "static", "advisor.js"), "utf8");
const ctx = vm.createContext({ document: { addEventListener() {} }, S: {}, stText: s => String(s || "unknown").replace(/_/g, " "), console });
vm.runInContext(src + "\nglobalThis.__x = { advisorModel, errorLines, pytestCmd };", ctx);
const { advisorModel, errorLines, pytestCmd } = ctx.__x;
const demo = JSON.parse(fs.readFileSync(path.join(__dirname, "..", "..", "demo-output", "capsule.json"), "utf8"));
const clone = o => JSON.parse(JSON.stringify(o));
const sum = M => ({ counts: M.counts, statuses: M.recs.map(r => [r.r.package, r.status, r.accepted, r.edits.length]), exps: M.exps.map(x => [x.e.name, x.status]) });

const out = { demo: sum(advisorModel(demo)) };
out.demoErr = errorLines(demo.executed_tests.proposed);
out.demoCmd = pytestCmd(demo.executed_tests.proposed);

const blocked = clone(demo); blocked.executed_tests.proposed.status = "environment_blocked";
out.blocked = sum(advisorModel(blocked));
const passed = clone(demo); passed.executed_tests.proposed.status = "passed";
out.passed = sum(advisorModel(passed));
const noexp = clone(demo); delete noexp.independent_experiments;
out.noexp = sum(advisorModel(noexp));
const noai = clone(demo); noai.ai_recommendations = { status: "blocked", reason: "x" };
out.noai = sum(advisorModel(noai));
console.log(JSON.stringify(out));
