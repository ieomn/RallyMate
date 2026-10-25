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
const fsUnit = JSON.parse(fs.readFileSync(new URL("./fixtures/fs-card-window-contract-unit.json", import.meta.url), "utf8"));
const gsReal = JSON.parse(fs.readFileSync(new URL("./fixtures/gs-racket-side-real.json", import.meta.url), "utf8"));
const fsReal = JSON.parse(fs.readFileSync(new URL("./fixtures/fs-card-window-real.json", import.meta.url), "utf8"));
function completed(raw) {
  return { mode: "live", uploadState: "complete", job: { id: raw.source_run_id, status: "succeeded" },
    evidence: { jobId: raw.source_run_id, result: { job_id: raw.source_run_id, card_execution: raw } } };
}
test("manual FS provenance stays in complete backups while internal windows are absent from readable tables", () => {
  const input = completed(fsUnit.report);
  const backup = JSON.parse(JSON.stringify(buildReportBackup(input)));
  assert.deepEqual(cardExecutionOf(backup.result.card_execution), cardExecutionOf(fsUnit.report));
  assert.equal(buildPracticeReport(input).sections.some(row => row.title.startsWith("逐卡")), false);
  const instances = backup.result.card_execution.cards.flatMap(card => card.instances);
  assert.equal(instances.length, 35);
  for (const row of instances) {
    assert.equal(row.source_window_annotation.binding.method, "manual_anchor");
    assert.equal(row.source_window_annotation.binding.uncertainty_ms, 100);
    assert.equal(row.source_window_annotation.binding.semantics, "manual_card_window_not_expert_technique_contact_or_force_truth");
  }
});
test("sampled measurement wrist remains distinct from dominant hand in backups and is not exposed as a score", () => {
  for (const { report: raw } of gsReal.reports) {
    const input = completed(raw), backup = JSON.parse(JSON.stringify(buildReportBackup(input)));
    assert.deepEqual(cardExecutionOf(backup.result.card_execution), cardExecutionOf(raw));
    assert.equal(buildPracticeReport(input).sections.some(row => row.title.startsWith("逐卡")), false);
    for (const row of backup.result.card_execution.cards.flatMap(card => card.instances)) {
      assert.equal(row.racket_side_evidence.observation.dominant_hand_established, false);
      assert.equal(row.racket_side_evidence.observation.continuous_racket_hold_established, false);
      if (raw.source_run_id.includes("2-hand_backhand")) assert.equal(row.racket_side_evidence.observation.observed_racket_hands, "both");
    }
  }
});
test("real FS review evidence survives portable backup with no proxy or original score", () => {
  const input = completed(fsReal.report), backup = JSON.parse(JSON.stringify(buildReportBackup(input)));
  assert.deepEqual(cardExecutionOf(backup.result.card_execution), cardExecutionOf(fsReal.report));
  assert.equal(backup.result.card_execution.summary.proxy_scored_instances, 0);
  assert.equal(buildPracticeReport(input).sections.some(row => row.title.startsWith("逐卡")), false);
  const rows = backup.result.card_execution.cards.flatMap(card => card.instances);
  assert.equal(rows.length, 3); assert.ok(rows.every(row => row.source_window_annotation.binding.method === "manual_anchor"));
});
