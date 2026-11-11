import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import ts from "typescript";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { tennisScopedFixture, tennisHash } from "./fixtures/tennis-assessment.mjs";

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
const { gradeLevel } = await import(compiledUrl(new URL("../app/scoring/level-grading.ts", import.meta.url)));
const { levelAssessmentOf } = await import(compiledUrl(new URL("../app/lib/level-assessment.ts", import.meta.url)));
const { validatedTennisAssessment } = await import(compiledUrl(new URL("../app/lib/tennis-assessment.ts", import.meta.url)));
const policy = JSON.parse(fs.readFileSync(new URL("../../src/rallymate_scoring/data/level_grading_v1.json", import.meta.url), "utf8"));
const context = { jobId: "tennis-test", videoSha256: tennisHash, durationMs: 9000 };
const parse = report => validatedTennisAssessment(report, context);

test("the consumer accepts every source weight row, including inactive domains", () => {
  for (const targetLevel of [1, 2, 3, 4, 5]) for (const backhandStyle of ["two_handed", "one_handed"]) {
    const report = gradeLevel({ GS01: 0, FS01: 80 }, { targetLevel, backhandStyle });
    assert.equal(report.policy_version, policy.version);
    assert.ok(levelAssessmentOf(report), `level ${targetLevel}, ${backhandStyle}`);
    for (const [domain, row] of Object.entries(report.domains)) {
      const weights = policy.levels[targetLevel].technique_weights[domain];
      if (Object.values(weights).every(weight => weight === 0)) {
        assert.deepEqual(row.active_weights, {});
        assert.equal(row.coverage, 0);
      }
    }
  }
});

test("declared policy weights cannot silently change or introduce an unknown technique", () => {
  const original = gradeLevel({ GS01: 80, GS02: 40 });
  const changed = structuredClone(original);
  changed.domains.GS.active_weights.GS01 += 0.01;
  changed.domains.GS.active_weights.GS02 -= 0.01;
  for (const code of ["GS01", "GS02"]) changed.domains.GS.observed_weights[code] = changed.domains.GS.active_weights[code] / changed.domains.GS.coverage;
  assert.equal(levelAssessmentOf(changed), null);
  const unknown = gradeLevel({}, { techniqueWeights: { GS: { GS01: 1 } } });
  unknown.domains.GS.active_weights = { GS99: 1 };
  unknown.domains.GS.missing_techniques = ["GS99"];
  assert.equal(levelAssessmentOf(unknown), null);
  assert.ok(levelAssessmentOf(gradeLevel({ GS01: 73 }, { techniqueWeights: { GS: { GS01: 1 } } })));
  for (const invalid of [null, [], "", 0]) {
    const malformed = gradeLevel({ GS01: 70 });
    malformed.domains.SS.active_weights = invalid;
    assert.equal(levelAssessmentOf(malformed), null);
  }
});

test("an episode metric cannot be counted in two dimensions with self-consistent totals", () => {
  const report = tennisScopedFixture();
  assert.ok(parse(report));
  report.dimensions[1].metrics.push(structuredClone(report.dimensions[0].metrics[0]));
  report.dimensions[1].metrics.forEach(metric => metric.weight = 0.5);
  report.dimensions[1].score = report.dimensions[1].metrics.reduce((sum, metric) => sum + metric.score, 0) / 2;
  report.overall_score = report.dimensions.reduce((sum, dimension) => sum + dimension.score, 0) / 5;
  assert.equal(parse(report), null);
});

test("scored episode measurements cannot disappear from the displayed aggregation", () => {
  const report = tennisScopedFixture();
  report.episodes[0].metrics.unlisted_measurement = { value: 0.2, score: 0 };
  assert.equal(parse(report), null);
});

test("scored validity records bind the same track and time window as their episode", () => {
  for (const mutate of [row => row.person_track_id = 2, row => row.start_ms += 1, row => row.end_ms -= 1]) {
    const report = tennisScopedFixture();
    mutate(report.episode_validity[0]);
    assert.equal(parse(report), null);
  }
});

test("current Python action reports and all five source-weight levels survive frontend validation", () => {
  const root = fileURLToPath(new URL("../../", import.meta.url));
  const result = spawnSync(process.env.PYTHON ?? "python", ["-c", [
    "import json",
    "from tests.test_tennis_assessment import inputs, calculate",
    "print(json.dumps(calculate(*inputs())))",
  ].join("\n")], { cwd: root, encoding: "utf8", env: { ...process.env, PYTHONPATH: `${root}/src`, RALLYMATE_POSE_BACKEND: "yolo" } });
  assert.equal(result.status, 0, result.stderr);
  const report = JSON.parse(result.stdout);
  const parsed = validatedTennisAssessment(report, { jobId: report.source_run_id, videoSha256: report.source_video_sha256, durationMs: 5000 });
  assert.ok(parsed, "current backend reports meet the browser contract");
  assert.equal(parsed.overall_score, report.overall_score);
  assert.equal(parsed.level_assessments.length, 5);
  assert.deepEqual(parsed.level_assessments.map(level => level.overall_score), report.level_assessments.map(level => level.overall_score));
});
