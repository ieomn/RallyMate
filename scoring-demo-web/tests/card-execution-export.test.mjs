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
const { getReportExportGate, buildPracticeReport, buildReportBackup } = await import(compiledUrl(new URL("../app/lib/report-export.ts", import.meta.url)));
const real = JSON.parse(fs.readFileSync(new URL("./fixtures/card-execution-real.json", import.meta.url), "utf8"));
function completed(report) {
  return { mode: "live", uploadState: "complete", job: { id: "job-12345678", status: "succeeded" },
    evidence: { jobId: "job-12345678", result: { job_id: "job-12345678", card_execution: report } } };
}

test("card-only analysis exports real proxy instances and null review states without a source grade", () => {
  const input = completed(real);
  assert.equal(getReportExportGate(input).allowed, true);
  const expected = cardExecutionOf(real);
  const backup = JSON.parse(JSON.stringify(buildReportBackup(input)));
  assert.deepEqual(cardExecutionOf(backup.result.card_execution), expected);
  const section = buildPracticeReport(input).sections.find(row => row.title === "逐卡计算结果");
  assert.equal(section.rows.length, 3);
  assert.ok(section.paragraphs.some(value => value.includes("不补零")));
  assert.ok(section.paragraphs.some(value => value.includes("完整技术评分：0")));
  const instances = buildPracticeReport(input).sections.find(row => row.title === "逐卡视频实例与条件");
  assert.ok(instances.rows.some(row => row.includes("需要复核")));
  assert.ok(instances.rows.some(row => row.includes("D / 40（视觉代理）")));
});

test("backup rejects a changed score and does not turn missing raw evidence into an empty completed analysis", () => {
  const tampered = structuredClone(real);
  tampered.cards[1].instances[0].score = 99;
  const backup = buildReportBackup(completed(tampered));
  assert.equal(backup.result.card_execution.summary.proxy_scored_instances, 1);
  assert.equal(backup.result.card_execution.cards[1].instances[0].score, null);
  const missing = { version: real.version, rule_version: real.rule_version, status: "unavailable", cards: [],
    reason_codes: ["raw_card_execution_evidence_unavailable"], overall_score: null };
  assert.equal(getReportExportGate(completed(missing)).allowed, false);
});
