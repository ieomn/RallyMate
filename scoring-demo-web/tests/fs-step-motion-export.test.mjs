import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import ts from "typescript";

const cache = new Map();
function compiledUrl(fileUrl) {
  if (cache.has(fileUrl.href)) return cache.get(fileUrl.href);
  if (fileUrl.pathname.endsWith(".json")) return `data:text/javascript;base64,${Buffer.from(`export default ${fs.readFileSync(fileUrl, "utf8")}`).toString("base64")}`;
  let js = ts.transpileModule(fs.readFileSync(fileUrl, "utf8"), { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
  js = js.replace(/from "([^"]+)"/g, (_match, name) => `from ${JSON.stringify(name.startsWith(".") ? compiledUrl(new URL(name.endsWith(".json") ? name : `${name}.ts`, fileUrl)) : import.meta.resolve(name))}`);
  const url = `data:text/javascript;base64,${Buffer.from(js).toString("base64")}`; cache.set(fileUrl.href, url); return url;
}
const { cardExecutionOf } = await import(compiledUrl(new URL("../app/lib/card-execution.ts", import.meta.url)));
const { buildPracticeReport, buildReportBackup } = await import(compiledUrl(new URL("../app/lib/report-export.ts", import.meta.url)));
const fixture = JSON.parse(fs.readFileSync(new URL("./fixtures/fs-step-motion-synthetic-contract.json", import.meta.url), "utf8"));
const instance = report => report.cards[0].instances[0];
const completed = raw => ({ mode: "live", uploadState: "complete", job: { id: raw.source_run_id, status: "succeeded" }, evidence: { jobId: raw.source_run_id, result: { job_id: raw.source_run_id, card_execution: raw } } });

test("all synthetic step source and geometry records survive portable backup with recomputed grades", () => {
  for (const { report: raw } of fixture.cases) {
    const backup = JSON.parse(JSON.stringify(buildReportBackup(completed(raw))));
    assert.deepEqual(cardExecutionOf(backup.result.card_execution), cardExecutionOf(raw));
    assert.deepEqual(instance(backup.result.card_execution).source_window_evidence, instance(raw).source_window_evidence);
    assert.equal(instance(backup.result.card_execution).original_grade, null);
  }
});

test("step timing and moving-foot proof stay in backup without displaying an internal card table", () => {
  const raw = fixture.cases[0].report;
  const report = buildPracticeReport(completed(raw));
  assert.equal(report.sections.some(s => s.title.startsWith("逐卡")), false);
  const backup = buildReportBackup(completed(raw));
  assert.deepEqual(instance(backup.result.card_execution).source_window_evidence.step_motion, instance(raw).source_window_evidence.step_motion);
  assert.equal(instance(backup.result.card_execution).original_grade, null);
});

test("a forged displacement stays diagnostic, loses its score and is not exported as an effective condition value", () => {
  const raw = structuredClone(fixture.cases[0].report), i = instance(raw);
  i.conditions[0].value = 12345; i.source_window_evidence.step_motion.feet.right.net_displacement_torso = 12345;
  const backup = JSON.parse(JSON.stringify(buildReportBackup(completed(raw))));
  assert.equal(instance(backup.result.card_execution).score, null);
  assert.equal(instance(backup.result.card_execution).source_window_evidence.step_motion.feet.right.net_displacement_torso, 12345);
  const report = buildPracticeReport(completed(raw));
  assert.equal(report.sections.some(s => s.title.startsWith("逐卡")), false);
  assert.doesNotMatch(JSON.stringify(report), /12345/);
  assert.equal(instance(backup.result.card_execution).step_motion_validation.status, "invalid");
});

test("the numeric path-length exception does not expose strings or unrelated private path fields", () => {
  const raw = structuredClone(fixture.cases[0].report), proof = instance(raw).source_window_evidence.step_motion;
  proof.feet.right.path_length_torso = "C:/private/user/file";
  proof.feet.right.local_path = "C:/private/user/file";
  const backup = JSON.parse(JSON.stringify(buildReportBackup(completed(raw))));
  assert.equal(instance(backup.result.card_execution).score, null);
  const foot = instance(backup.result.card_execution).source_window_evidence.step_motion.feet.right;
  assert.equal(foot.path_length_torso, undefined); assert.equal(foot.local_path, undefined);
});
