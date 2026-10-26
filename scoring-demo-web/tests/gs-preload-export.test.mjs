import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import ts from "typescript";

const cache = new Map();
function compiledUrl(fileUrl) {
  if (cache.has(fileUrl.href)) return cache.get(fileUrl.href);
  if (fileUrl.pathname.endsWith(".json")) return `data:text/javascript;base64,${Buffer.from(`export default ${fs.readFileSync(fileUrl, "utf8")}`).toString("base64")}`;
  let js = ts.transpileModule(fs.readFileSync(fileUrl, "utf8"), { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
  js = js.replace(/from "([^"]+)"/g, (_match, name) => `from ${JSON.stringify(name.startsWith(".") ? compiledUrl(new URL(name.endsWith(".json") ? name : `${name}.ts`, fileUrl)) : import.meta.resolve(name))}`);
  const url = `data:text/javascript;base64,${Buffer.from(js).toString("base64")}`;
  cache.set(fileUrl.href, url);
  return url;
}
const { cardExecutionOf } = await import(compiledUrl(new URL("../app/lib/card-execution.ts", import.meta.url)));
const { buildPracticeReport, buildReportBackup } = await import(compiledUrl(new URL("../app/lib/report-export.ts", import.meta.url)));
const fixture = JSON.parse(fs.readFileSync(new URL("./fixtures/gs-preload-synthetic-contract.json", import.meta.url), "utf8"));
function completed(raw) { return { mode: "live", uploadState: "complete", job: { id: raw.source_run_id, status: "succeeded" }, evidence: { jobId: raw.source_run_id, result: { job_id: raw.source_run_id, card_execution: raw } } }; }
test("all raw auxiliary source series survive backup and no synthetic case gains a score", () => {
  for (const raw of [fixture.report, ...fixture.additional_synthetic_cases.map(row => row.report)]) {
    const expected = cardExecutionOf(raw), backup = JSON.parse(JSON.stringify(buildReportBackup(completed(raw))));
    assert.deepEqual(cardExecutionOf(backup.result.card_execution), expected);
    assert.deepEqual(backup.result.card_execution.cards[0].instances[0].auxiliary_evidence, raw.cards[0].instances[0].auxiliary_evidence);
    assert.equal(backup.result.card_execution.summary.proxy_scored_instances, 0);
  }
});
test("auxiliary values and uncertainty remain complete in backup and never appear as internal score tables", () => {
  const input = completed(fixture.report);
  assert.equal(buildPracticeReport(input).sections.some(row => row.title.startsWith("逐卡")), false);
  const backup = buildReportBackup(input), row = backup.result.card_execution.cards[0].instances[0];
  assert.equal(row.score, null);
  assert.deepEqual(row.auxiliary_evidence, fixture.report.cards[0].instances[0].auxiliary_evidence);
  assert.equal(row.auxiliary_validation.status, "measured_auxiliary");
});
test("invalid raw scores stay in diagnostic backup but never appear as valid auxiliary table values", () => {
  const raw = structuredClone(fixture.report), auxiliary = raw.cards[0].instances[0].auxiliary_evidence;
  auxiliary.score = 100; auxiliary.auxiliary_observations[0].value = 12345;
  const input = completed(raw), backup = JSON.parse(JSON.stringify(buildReportBackup(input)));
  assert.equal(backup.result.card_execution.cards[0].instances[0].auxiliary_evidence.score, 100);
  assert.equal(backup.result.card_execution.cards[0].instances[0].score, null);
  assert.equal(backup.result.card_execution.cards[0].instances[0].auxiliary_validation.status, "invalid");
  const report = buildPracticeReport(input);
  assert.equal(report.sections.some(row => row.title.startsWith("逐卡")), false);
  assert.doesNotMatch(JSON.stringify(report), /12345|100.00%/);
});
