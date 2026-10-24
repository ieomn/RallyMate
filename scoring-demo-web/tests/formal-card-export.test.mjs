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
const fixtures = ["fs-step-motion-formal-synthetic-contract.json","footwork-cycle-formal-synthetic-contract.json","gs-formal-synthetic-contract.json","temporal-formal-real-0a3-contract.json"].map(name=>JSON.parse(fs.readFileSync(new URL("./fixtures/"+name,import.meta.url),"utf8")));
const reports=fixtures.flatMap(f=>f.report?[f.report]:f.cases.map(c=>c.report));
const completed=raw=>({mode:"live",uploadState:"complete",job:{id:raw.source_run_id,status:"succeeded"},evidence:{jobId:raw.source_run_id,result:{job_id:raw.source_run_id,card_execution:raw}}});
test("every formal report preserves all validated state and source proof across backup re-reading",()=>{
 for(const raw of reports){const backup=JSON.parse(JSON.stringify(buildReportBackup(completed(raw))));assert.deepEqual(cardExecutionOf(backup.result.card_execution),cardExecutionOf(raw));}
});
test("readable exports use recomputed formal totals and grades, not legacy review or stored summaries",()=>{
 const raw=structuredClone(fixtures.at(-1).report);raw.overall_score=999;raw.summary.formal_scored_cards=999;
 const report=buildPracticeReport(completed(raw));const summary=report.sections.find(s=>s.title==="逐卡计算结果");assert.match(summary.paragraphs.join(" "),/正式总分：68/);assert.doesNotMatch(summary.paragraphs.join(" "),/待复核|999/);
 const row=report.sections.find(s=>s.title==="逐卡视频实例与条件").rows.find(r=>r[0].startsWith("FS01-M02"));assert.match(row[2],/D \/ 40（正式评分）/);
});
