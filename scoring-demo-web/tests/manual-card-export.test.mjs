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
test("manual FS provenance appears in portable tables and complete source bindings survive backup", () => {
  const input = completed(fsUnit.report);
  const backup = JSON.parse(JSON.stringify(buildReportBackup(input)));
  assert.deepEqual(cardExecutionOf(backup.result.card_execution), cardExecutionOf(fsUnit.report));
  const section = buildPracticeReport(input).sections.find(row => row.title === "逐卡视频实例与条件");
  assert.equal(section.rows.length, 35);
  assert.ok(section.rows.every(row => row.at(-1).includes("人工逐卡窗口") && row.at(-1).includes("非专家技术、触地或受力真值") && row.at(-1).includes("±100 毫秒")));
});
test("sampled measurement wrist remains distinct from dominant hand in exports and full proof stays in backup", () => {
  for (const { report: raw } of gsReal.reports) {
    const input = completed(raw), backup = JSON.parse(JSON.stringify(buildReportBackup(input)));
    assert.deepEqual(cardExecutionOf(backup.result.card_execution), cardExecutionOf(raw));
    const section = buildPracticeReport(input).sections.find(row => row.title === "逐卡视频实例与条件");
    assert.ok(section.rows.every(row => row.at(-1).includes("测量腕") && row.at(-1).includes("非优势手判定") && row.at(-1).includes("非持续持拍证明")));
    if (raw.source_run_id.includes("2-hand_backhand")) assert.ok(section.rows.every(row => row.at(-1).includes("双手")));
  }
});
test("real FS review evidence survives portable backup with no proxy or original score", () => {
  const input = completed(fsReal.report), backup = JSON.parse(JSON.stringify(buildReportBackup(input)));
  assert.deepEqual(cardExecutionOf(backup.result.card_execution), cardExecutionOf(fsReal.report));
  assert.equal(backup.result.card_execution.summary.proxy_scored_instances, 0);
  const section = buildPracticeReport(input).sections.find(row => row.title === "逐卡视频实例与条件");
  assert.equal(section.rows.length, 3); assert.ok(section.rows.every(row => row.at(-1).includes("人工逐卡窗口")));
});
