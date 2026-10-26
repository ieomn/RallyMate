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
test("readable exports show one recomputed formal total and keep internal rows only in backup",()=>{
 const raw=structuredClone(fixtures.at(-1).report);raw.overall_score=999;raw.summary.formal_scored_cards=999;
 const report=buildPracticeReport(completed(raw)),totals=report.sections.filter(s=>s.title.startsWith("步伐动作得分："));
 assert.equal(totals.length,1);assert.equal(totals[0].title,"步伐动作得分：68 / 100");
 assert.doesNotMatch(totals[0].paragraphs.join(" "),/待复核|999/);
 assert.equal(report.sections.some(s=>s.title.startsWith("逐卡")),false);
 assert.doesNotMatch(JSON.stringify(report),/FS01-M02|D \/ 40|原卡技术等级/);
 const backup=buildReportBackup(completed(raw)),row=backup.result.card_execution.cards.find(c=>c.indicator_id==="FS01-M02").instances[0];
 assert.equal(row.grade,"D");assert.equal(row.score,40);
 assert.deepEqual(backup.result.card_execution,cardExecutionOf(raw));
});
