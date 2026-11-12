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
  let js = file.pathname.endsWith(".json") ? `export default ${source}` : ts.transpileModule(source,
    { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
  js = js.replace(/from "([^"]+)"/g, (_, name) => `from ${JSON.stringify(name.startsWith(".")
    ? compiledUrl(new URL(name.endsWith(".json") ? name : `${name}.ts`, file)) : import.meta.resolve(name))}`);
  const url = `data:text/javascript;base64,${Buffer.from(js).toString("base64")}`;
  cache.set(file.href, url);
  return url;
}
const moduleAt = name => import(compiledUrl(new URL(`../app/lib/${name}.ts`, import.meta.url)));
const { levelAssessmentOf } = await moduleAt("level-assessment");
const { tennisAssessmentOf } = await moduleAt("tennis-assessment");
const { buildReportBackup } = await moduleAt("report-export");
const { readReportJson } = await moduleAt("report-file");
const { trainingScoreOf } = await moduleAt("training-score");

let historicalData;
function historicalFixture() {
  if (historicalData) return structuredClone(historicalData);
  const root = fileURLToPath(new URL("../../", import.meta.url));
  const output = spawnSync(process.env.PYTHON ?? "python", ["-c", [
    "import json",
    "from pathlib import Path",
    "from unittest.mock import patch",
    "from tests.test_tennis_assessment import inputs, calculate",
    "from rallymate_scoring.level_grading import grade_level",
    "legacy=json.loads(Path('src/rallymate_scoring/data/level_grading_legacy_v1.json').read_text(encoding='utf-8'))",
    "scores={'GS01':100,'GS02':30,'FS01':20,'FS05':100}",
    "with patch('rallymate_scoring.level_grading._policy',return_value=legacy):",
    " report=calculate(*inputs())",
    " old_levels=[grade_level(scores,target_level=i) for i in range(1,6)]",
    "current_levels=[grade_level(scores,target_level=i) for i in range(1,6)]",
    "print(json.dumps({'report':report,'old_levels':old_levels,'current_levels':current_levels}))",
  ].join("\n")], { cwd: root, encoding: "utf8", env: { ...process.env, PYTHONPATH: `${root}/src`, RALLYMATE_POSE_BACKEND: "yolo" } });
  assert.equal(output.status, 0, output.stderr);
  historicalData = JSON.parse(output.stdout);
  return structuredClone(historicalData);
}

test("historical and current numeric levels are validated against their own trusted policies", () => {
  const { old_levels, current_levels } = historicalFixture();
  assert.notDeepEqual(old_levels.map(row => row.overall_score), current_levels.map(row => row.overall_score));
  for (const old of old_levels) {
    assert.equal(old.policy_version, "level-grading-policy-v1.0.0");
    assert.deepEqual(levelAssessmentOf(old), old);
  }
  for (const current of current_levels) {
    assert.equal(current.policy_version, "level-grading-policy-v2.0.0");
    assert.deepEqual(levelAssessmentOf(current), current);
  }
});

test("a historical policy label cannot authorize unknown, changed or current default weights", () => {
  const { old_levels, current_levels } = historicalFixture();
  for (const mutate of [row => row.policy_version = "unknown-v99", row => row.policy_version = "level-grading-policy-v2.0.0",
    row => row.domains.GS.module_weight += 0.01, row => row.domains.GS.active_weights.GS01 += 0.01]) {
    const changed = structuredClone(old_levels[0]);
    mutate(changed);
    assert.equal(levelAssessmentOf(changed), null);
  }
  const mislabeled = structuredClone(current_levels[0]);
  mislabeled.policy_version = old_levels[0].policy_version;
  mislabeled.weight_source = old_levels[0].weight_source;
  mislabeled.major_weight_source = old_levels[0].major_weight_source;
  assert.equal(levelAssessmentOf(mislabeled), null);
});

test("complete legacy action reports retain their original five level scores after export and import", async () => {
  const { report } = historicalFixture();
  const result = { job_id: report.source_run_id, input: { video: { sha256: report.source_video_sha256, duration_ms: 5000 } }, tennis_assessment: report };
  const expected = tennisAssessmentOf(result);
  assert.ok(expected);
  assert.equal(expected.level_assessments.length, 5);
  const source = { mode: "live", uploadState: "complete", job: { id: result.job_id, status: "succeeded" },
    evidence: { jobId: result.job_id, result } };
  const encoded = JSON.stringify(buildReportBackup(source));
  const restored = await readReportJson({ size: Buffer.byteLength(encoded), text: async () => encoded });
  const actual = tennisAssessmentOf(restored.result);
  assert.deepEqual(actual, expected);
  assert.deepEqual(actual.level_assessments.map(row => [row.policy_version, row.overall_score]),
    report.level_assessments.map(row => [row.policy_version, row.overall_score]));
  assert.equal(trainingScoreOf(restored.result).score, report.overall_score);
});
