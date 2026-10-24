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
const fixture = JSON.parse(fs.readFileSync(new URL("./fixtures/footwork-cycle-formal-synthetic-contract.json", import.meta.url), "utf8"));
const original=(variant="complete",code="FS04")=>structuredClone(fixture.cases.find(c=>c.variant===variant&&c.event_code===code).report);
const instance=r=>r.cards.find(c=>c.indicator_id==="FS04-M04").instances[0];
test("five freshly computed cycle reports retain exact formal statuses without changing foot-order geometry",()=>{
 for(const {variant,report}of fixture.cases){const parsed=cardExecutionOf(report);for(const c of report.cards){const actual=parsed.cards.find(v=>v.indicator_id===c.indicator_id);assert.equal(actual.instances[0].status,c.instances[0].status,c.indicator_id+variant+JSON.stringify(actual.instances[0].reason_codes));assert.equal(actual.instances[0].score,c.instances[0].score);}}
});
test("candidate identity and quality warnings are advisory only with complete matching raw identity",()=>{
 for(const variant of ["advisory","candidate_identity"]){const raw=original(variant),i=instance(cardExecutionOf(raw));assert.equal(i.status,"scored");assert.equal(i.score,100);assert.deepEqual(i.foot_cycle.event_quality,instance(raw).evidence.event_quality);}
 const mutations=[e=>e.event_quality.event_identity_diagnostics.confirmed_id_switch_count=1,e=>e.event_quality.event_identity_diagnostics.source_track_ids=[2,3],e=>e.event_quality.event_identity_diagnostics.source_track_ids=[7],e=>e.event_quality.raw_input_quality_flags.push("phase_boundary_censored:start"),e=>e.source_frame_bindings[4].selection_epoch++,e=>e.source_frame_bindings[4].pts++,e=>e.series.ankle_order_sign[0]=0];
 for(const mutate of mutations){const raw=original("candidate_identity");mutate(instance(raw).evidence);assert.equal(instance(cardExecutionOf(raw)).score,null,String(mutate));}
});
