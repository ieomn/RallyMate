import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import ts from "typescript";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
const cache = new Map();
function compiledUrl(file) {
  if (cache.has(file.href)) return cache.get(file.href);
  const source = fs.readFileSync(file, "utf8");
  let js = file.pathname.endsWith(".json") ? `export default ${source}` : ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
  js = js.replace(/from "([^"]+)"/g, (_, name) => `from ${JSON.stringify(name.startsWith(".") ? compiledUrl(new URL(name.endsWith(".json") ? name : `${name}.ts`, file)) : import.meta.resolve(name))}`);
  const url = `data:text/javascript;base64,${Buffer.from(js).toString("base64")}`;
  cache.set(file.href, url); return url;
}
const { gradeLevel, GATES } = await import(compiledUrl(new URL("../app/scoring/level-grading.ts", import.meta.url)));
const { levelAssessmentOf, levelAssessmentsOf } = await import(compiledUrl(new URL("../app/lib/level-assessment.ts", import.meta.url)));
const { buildScoreReport } = await import(compiledUrl(new URL("../app/scoring/engine.ts", import.meta.url)));

test("Python and frontend agree on every level, both backhands, gates and family fallback", () => {
  const scores = { GS01: 98, GS02: 40, GS03: 10, FS05: 82, SS02: 75, RS01: 65, RS02: 60, RS03: 20, NS01: 66, DS01: 50, OS01: 88, TS08: 77 };
  const gates = Object.fromEntries(Object.values(GATES).flat().map(key => [key, true]));
  const cases = [];
  for (const targetLevel of [1,2,3,4,5]) for (const backhandStyle of ["two_handed", "one_handed"]) cases.push({ scores, options: { targetLevel, backhandStyle, gates } });
  cases.push({ scores: {}, options: { targetLevel: 2, domainScores: { SS: 81.5, FS: 40 } } }, { scores: {}, options: { targetLevel: 1, domainScores: { SS: 70 } } }, { scores: {}, options: { targetLevel: 5 } });
  cases.push({ scores: { GS01: .03125 }, options: { targetLevel: 1 } });
  const root = fileURLToPath(new URL("../../", import.meta.url));
  const python = spawnSync(process.env.PYTHON ?? "python", ["-c", `import json,sys\nfrom rallymate_scoring.level_grading import grade_level\ncases=json.load(sys.stdin)\nresults=[]\nfor c in cases:\n o=c['options']\n results.append(grade_level(c['scores'],target_level=o.get('targetLevel',1),backhand_style=o.get('backhandStyle','two_handed'),gates=o.get('gates'),domain_scores=o.get('domainScores')))\nprint(json.dumps(results))`], { cwd: root, encoding: "utf8", input: JSON.stringify(cases), env: { ...process.env, PYTHONPATH: `${root}/src` } });
  assert.equal(python.status, 0, python.stderr);
  JSON.parse(python.stdout).forEach((result, index) => {
    assert.deepEqual(gradeLevel(cases[index].scores, cases[index].options), result);
    assert.ok(levelAssessmentOf(result), `case ${index} validates`);
  });
});

test("changing a gated eligibility never erases the measured score", () => {
  const pending = gradeLevel({ GS01: 90 }, { targetLevel: 5 });
  assert.equal(pending.overall_score, 90); assert.equal(pending.eligible_level, 2);
  const failed = gradeLevel({ GS01: 90 }, { targetLevel: 5, gates: { body_coordination_ok: false } });
  assert.equal(failed.overall_score, 90); assert.equal(failed.gatekeepers.intermediate.status, "failed");
});

test("frontend contract rejects inconsistent totals, wrong backhand weights and forged gate outcomes", () => {
  const source = gradeLevel({ GS01: 90, GS02: 30 }, { targetLevel: 5 });
  for (const mutate of [v => v.overall_score = 99, v => v.eligible_level = 5, v => v.domains.GS.module_weight = .70,
    v => v.gatekeepers.intermediate.status = "passed", v => v.domains.GS.active_weights.GS03 = .2,
    v => v.weighted_coverage = 1, v => v.domains.GS.observed_weights.GS01 = .1,
    v => v.domains.GS.score = null, v => v.policy_version = "unknown-v99"]) {
    const changed = structuredClone(source); mutate(changed); assert.equal(levelAssessmentOf(changed), null);
  }
  assert.deepEqual(levelAssessmentsOf([source, source]), []);
});

test("old baseScore and scenario identifiers cannot create or perturb real scores", () => {
  const card = { id: "GS01-M03-01", domain: "GS", eventCode: "GS01", stageName: "转肩", requiredPoints: "", dependencies: ["pose"], grades: {} };
  const scenario = { id: "a", mode: "demo", baseScore: 94, eventConfidence: .9, dependencyCoverage: { pose: 1, ball: 0, racket: 0, court: 0, tracking: 1 } };
  assert.equal(buildScoreReport([card], scenario).overallScore, null);
  const measured = { ...scenario, mode: "real", cardScores: { [card.id]: 74.5 } };
  assert.equal(buildScoreReport([card], measured).overallScore, 74.5);
  assert.equal(buildScoreReport([card], { ...measured, id: "different", baseScore: 10 }).overallScore, 74.5);
});

test("configured subtechnique weighting replaces event equal averaging", () => {
  const scenario = { id: "weights", mode: "real", baseScore: null, eventConfidence: 1, dependencyCoverage: { pose: 1, ball: 1, racket: 1, court: 1, tracking: 1 }, targetLevel: 2,
    techniqueScores: { GS01: 100, GS02: 30, GS03: 0 } };
  const report = buildScoreReport([], scenario);
  assert.equal(report.overallScore, 70);
  assert.equal(report.domains.GS.score, 70);
  assert.deepEqual(report.formula.dimensionWeights, {});
});
