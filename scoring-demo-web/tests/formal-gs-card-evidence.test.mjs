import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import ts from "typescript";

const cache = new Map();
function compiledUrl(fileUrl) {
  if (cache.has(fileUrl.href)) return cache.get(fileUrl.href);
  if (fileUrl.pathname.endsWith(".json")) return `data:text/javascript;base64,${Buffer.from(`export default ${fs.readFileSync(fileUrl, "utf8")}`).toString("base64")}`;
  let js = ts.transpileModule(fs.readFileSync(fileUrl, "utf8"), { fileName: fileUrl.pathname, compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  js = js.replace(/from "([^"]+)"/g, (_match, name) => { const local = new URL(name.endsWith(".json") ? name : `${name}.ts`, fileUrl); return `from ${JSON.stringify(name.startsWith(".") ? compiledUrl(fs.existsSync(local) ? local : new URL(`${name}.tsx`, fileUrl)) : import.meta.resolve(name))}`; });
  const url = `data:text/javascript;base64,${Buffer.from(js).toString("base64")}`; cache.set(fileUrl.href, url); return url;
}
const { cardExecutionOf } = await import(compiledUrl(new URL("../app/lib/card-execution.ts", import.meta.url)));
const fixture=JSON.parse(fs.readFileSync(new URL("./fixtures/gs-formal-synthetic-contract.json",import.meta.url),"utf8"));
const original=(variant="detector")=>structuredClone(fixture.cases.find(c=>c.variant===variant).report);
const target=r=>r.cards.find(c=>c.indicator_id==="GS01-M03-05").instances[0];
test("fresh GS source stages preserve formal results and complete source ledgers",()=>{
 for(const {variant,report}of fixture.cases){const parsed=cardExecutionOf(report);for(const c of report.cards){const actual=parsed.cards.find(v=>v.indicator_id===c.indicator_id).instances[0];assert.equal(actual.status,c.instances[0].status,variant+c.indicator_id+JSON.stringify(actual.reason_codes));assert.equal(actual.score,c.instances[0].score);assert.deepEqual(actual.source_stage_evidence,c.instances[0].source_stage_evidence);}assert.deepEqual(cardExecutionOf(parsed),parsed);}
});
test("new formal GS stages require every derived ledger row, source PTS and selected track",()=>{
 const mutations=[i=>delete i.window.derived_selection_epoch,i=>i.source_stage_evidence.source_frame_bindings.forEach(b=>delete b.derived_selection_epoch),i=>delete i.source_stage_evidence.source_frame_bindings[1].derived_selection_epoch,i=>i.source_stage_evidence.source_frame_bindings[1].derived_selection_epoch++,i=>i.source_stage_evidence.source_frame_bindings.pop(),i=>i.source_stage_evidence.source_frame_bindings[1].pts++,i=>i.source_stage_evidence.source_frame_bindings[1].selected_person_track_id++,i=>i.source_stage_evidence.source_frame_bindings[1].video_sha256="b".repeat(64),i=>i.source_stage_evidence.source_pts.pop()];
 for(const mutate of mutations){const raw=original();mutate(target(raw));assert.equal(target(cardExecutionOf(raw)).score,null,String(mutate));}
});
test("formal manual preload keeps independently measured auxiliary values but never a card grade",()=>{
 const raw=original("manual_preload_auxiliary"),i=cardExecutionOf(raw).cards[0].instances[0];assert.equal(i.status,"unavailable");assert.equal(i.score,null);assert.equal(i.auxiliary_validation.status,"measured_auxiliary");
 delete raw.cards[0].instances[0].source_stage_evidence.source_frame_bindings[0].derived_selection_epoch;
 assert.equal(cardExecutionOf(raw).cards[0].instances[0].auxiliary_validation.status,"invalid");
});
