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
const fixture = JSON.parse(fs.readFileSync(new URL("./fixtures/temporal-formal-real-0a3-contract.json", import.meta.url), "utf8"));
const original = () => structuredClone(fixture.report);
const target = r => r.cards.find(c => c.indicator_id === "FS01-M02").instances[0];
const proof = r => target(r).evidence.fallback.proof;
function rejected(raw, label) { const i = target(cardExecutionOf(raw)); assert.equal(i.score, null, label); assert.notEqual(i.status, "scored", label); return i; }

test("recorded trajectory fallback independently recomputes D40 and equal-card total68", () => {
 const raw = original(), parsed = cardExecutionOf(raw), i = target(parsed);
 assert.equal(i.status, "scored", JSON.stringify(i.local_dip_validation)); assert.equal(i.grade, "D"); assert.equal(i.score, 40);
 assert.equal(i.local_dip_validation.status, "measured_local_dip");
 assert.deepEqual(i.local_dip_validation.source_frames, [44,45,46,47,48,49]);
 assert.equal(parsed.summary.formal_scored_instances, 5); assert.equal(parsed.summary.unavailable_instances, 2); assert.equal(parsed.overall_score, 68);
 assert.deepEqual(i.temporal_evidence, target(raw).evidence); assert.deepEqual(cardExecutionOf(parsed), parsed);
});

test("selected local dip cannot bypass complete parent, original source clock, identity or model provenance", () => {
 const mutations = [p => p.rows.pop(), p => p.rows.shift(), p => p.rows.splice(12,1),
  p => p.rows[12].source_binding.pts++, p => p.rows[12].source_binding.selection_epoch++,
  p => p.rows[12].primary_selection.source_track_id++, p => p.rows[12].source_binding.video_sha256='a'.repeat(64),
  p => p.rows[12].model_geometry_source=null, p => p.rows[12].model_geometry_source.source_binding.frame_index++,
  p => p.rows[12].model_geometry_source.source_binding.person_track_id++, p => p.rows[12].model_geometry_source.pose_source='detector',
  p => p.rows[12].points.right_knee=null, p => p.rows[12].points.left_hip[1]+=.1,
  p => p.search_end_ms--, p => p.search_end_basis='event_end', p => p.coordinate_system='image_xy',
  p => p.rows[12].frame_transform_to_reference=[[1,1,0],[0,1,0]]];
 for (const mutate of mutations) { const raw=original();mutate(proof(raw));rejected(raw,String(mutate)); }
});

test("saving a passing flag or a later preferred dip never authorizes a score", () => {
 const mutations = [i => i.evidence.fallback.selected.start_ms++, i => i.evidence.fallback.selected.confirmation_end_ms--,
  i => i.evidence.series.body_speed_torso_s[0]*=2, i => i.evidence.series.left_knee_flexion_deg[0]+=1,
  i => i.evidence.source_frames.pop(), i => i.quality.total_frame_count++, i => i.conditions[0].value=.5,
  i => { i.grade=i.proxy_grade='A';i.score=100;i.conditions.forEach(c=>{c.passed=true;c.value=c.operator==='lte'?0:c.threshold;}); }];
 for (const mutate of mutations) { const raw=original();mutate(target(raw));rejected(raw,String(mutate)); }
 const historical=original();delete historical.scoring_policy_version;
 for(const c of historical.cards) for(const i of c.instances) {delete i.scoring_policy_version;if(i.status==='scored')i.status='proxy_scored';i.semantics='engineering_visual_condition_completion_not_original_technical_grade';}
 rejected(historical,'legacy selected fallback remains unscored');
});

test("all five formal temporal cards require complete decoder, raw-person and primary-selection chains",()=>{
 for(const rawCard of original().cards){
  const index=rawCard.instances.findIndex(i=>i.status==="scored");if(index<0)continue;
  const mutations=[e=>e.source_frame_bindings.pop(),e=>e.source_frame_bindings[1].pts++,e=>e.source_frame_bindings[1].processed_index++,e=>e.source_frame_bindings[1].primary_selection.source_track_id++,e=>e.source_frame_bindings[1].selection_epoch++,e=>e.source_track_ids=[99],e=>e.event_quality.event_identity_diagnostics.confirmed_id_switch_count=1,e=>e.event_quality.raw_input_quality_flags.push("confirmed_id_switch_present"),e=>e.source_frame_binding_version="caller-claim"];
  for(const mutate of mutations){const raw=original(),i=raw.cards.find(c=>c.indicator_id===rawCard.indicator_id).instances[index];mutate(i.evidence);const result=cardExecutionOf(raw).cards.find(c=>c.indicator_id===rawCard.indicator_id).instances[index];assert.equal(result.score,null,rawCard.indicator_id+String(mutate));}
 }
});
test("successor intervals cannot bypass their complete source gap or borrow another selected person",()=>{
 for(const id of ["FS01-M05","FS09-M05"]){for(const mutate of [s=>s.source_frame_bindings.pop(),s=>s.source_frame_bindings[1].pts++,s=>s.source_frame_bindings[1].primary_selection.source_track_id++,s=>s.successor_source_track_ids=[99],s=>s.interval_ms=0]){
  const raw=original(),i=raw.cards.find(c=>c.indicator_id===id).instances[0];mutate(i.evidence.successor);assert.equal(cardExecutionOf(raw).cards.find(c=>c.indicator_id===id).instances[0].score,null,id+String(mutate));
 }}
});

test("compact saved fallback cannot override the full source proof or erase an unresolved source phase",()=>{
 const raw=original();target(raw).fallback={trigger:"saved",strategy:"saved",selected:null};target(raw).evidence.fallback.proof.rows[12].source_binding.pts++;
 rejected(raw,"compact override");const erased=original();delete target(erased).evidence.fallback;rejected(erased,"erased fallback with unavailable original phase");
});

test("raw phase censoring cannot be erased by an empty saved blocking list",()=>{
 const keys={"FS01-M05":"landing_proxy_ms","FS09-M03":"peak_speed_ms","FS09-M04":"deceleration_peak_ms","FS09-M05":"stable_control_onset_ms"};
 for(const [id,key] of Object.entries(keys))for(const flag of ["censored","low_sample_peak"]){
  const raw=original(),i=raw.cards.find(c=>c.indicator_id===id).instances.find(i=>i.status==="scored");
  i.evidence.event_quality.raw_input_quality_flags.push(`source_${flag}:${key}`);i.evidence.event_quality.blocking_flags=[];
  assert.equal(cardExecutionOf(raw).cards.find(c=>c.indicator_id===id).instances.find(j=>j.event_id===i.event_id).score,null,id+flag);
 }
});

test("optional processed index in primary rows does not invalidate a complete decoder chain",()=>{
 const raw=original();for(const row of proof(raw).rows)delete row.primary_selection.processed_index;
 assert.equal(target(cardExecutionOf(raw)).score,40);
 proof(raw).rows[12].primary_selection.processed_index=999;
 rejected(raw,"present conflicting primary index");
});
