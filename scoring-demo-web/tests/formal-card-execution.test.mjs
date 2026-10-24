import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import ts from "typescript";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

const cache = new Map();
function compiledUrl(fileUrl) {
  if (cache.has(fileUrl.href)) return cache.get(fileUrl.href);
  if (fileUrl.pathname.endsWith(".json")) return `data:text/javascript;base64,${Buffer.from(`export default ${fs.readFileSync(fileUrl, "utf8")}`).toString("base64")}`;
  let js = ts.transpileModule(fs.readFileSync(fileUrl, "utf8"), { fileName: fileUrl.pathname, compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  js = js.replace(/from "([^"]+)"/g, (_match, name) => { const local = new URL(name.endsWith(".json") ? name : `${name}.ts`, fileUrl); return `from ${JSON.stringify(name.startsWith(".") ? compiledUrl(fs.existsSync(local) ? local : new URL(`${name}.tsx`, fileUrl)) : import.meta.resolve(name))}`; });
  const url = `data:text/javascript;base64,${Buffer.from(js).toString("base64")}`; cache.set(fileUrl.href, url); return url;
}
const { cardExecutionOf } = await import(compiledUrl(new URL("../app/lib/card-execution.ts", import.meta.url)));
const { default: Panel } = await import(compiledUrl(new URL("../app/CardExecutionPanel.tsx", import.meta.url)));
const { default: Overview } = await import(compiledUrl(new URL("../app/TrainingReportOverview.tsx", import.meta.url)));
const fixture = JSON.parse(fs.readFileSync(new URL("./fixtures/fs-step-motion-formal-synthetic-contract.json", import.meta.url), "utf8"));
const policy = "automatic-formal-card-scoring-v1.0.0";
const original = (variant = "complete", id = "FS08-M02") => structuredClone(fixture.cases.find(c => c.variant === variant && c.indicator_id === id).report);
const instance = report => report.cards[0].instances[0];
const parse = raw => instance(cardExecutionOf(raw));
function reject(raw, label) { const i = parse(raw); assert.equal(i.score, null, label); assert.equal(i.grade, null, label); assert.notEqual(i.status, "scored", label); return i; }

test("fresh synthetic formal reports retain backend grades while historical fixture stays unchanged", () => {
  assert.equal(fixture.real_step_motion_instances, 0);
  for (const {variant, report} of fixture.cases) {
    const parsed = cardExecutionOf(report), raw = instance(report), i = instance(parsed);
    assert.equal(parsed.scoring_policy_version, policy);
    assert.equal(i.status, raw.status, variant + ": " + i.reason_codes.join(","));
    assert.equal(i.score, raw.score, variant); assert.equal(i.grade, raw.grade, variant);
    assert.equal(i.original_grade, null); assert.equal(i.source_grade_supported, false);
    assert.deepEqual(cardExecutionOf(parsed), parsed, variant);
  }
  const historical = JSON.parse(fs.readFileSync(new URL("./fixtures/fs-step-motion-synthetic-contract.json", import.meta.url), "utf8"));
  assert.equal(instance(historical.cases[0].report).status, "proxy_scored");
});

test("advisory quality and source-bound model measurements are formally scored without suppressing sources", () => {
  for (const variant of ["original_quality", "bound_synthetic_model"]) {
    const raw = original(variant), i = parse(raw);
    assert.equal(i.status, "scored", i.reason_codes.join(",")); assert.equal(i.grade, "A"); assert.equal(i.score, 100);
    assert.equal(i.step_motion_validation.status, "measured_visual_motion");
    assert.deepEqual(i.source_window_evidence, instance(raw).source_window_evidence);
    if (variant === "bound_synthetic_model") assert.deepEqual(i.measurement_provenance, instance(raw).measurement_provenance);
    const html = renderToStaticMarkup(createElement(Panel, { value: raw }));
    assert.match(html, /正式评分/); assert.match(html, /100/);
  }
});

test("formal policy never waives missing sources, clock, common joints or mathematically incomplete episodes", () => {
  for (const variant of ["wrong_declared_side", "joint_gap", "missing_motion_observation", "two_motions", "short_middle", "out_and_back", "other_net_dominates"]) {
    const raw = original(variant), i = instance(raw);
    i.status = "scored"; i.grade = i.proxy_grade = "A"; i.score = 100; i.reason_codes = [];
    i.semantics = "application_condition_completion_score";
    i.conditions.forEach(c => { c.passed = true; c.value = c.operator === "lte" ? 0 : c.threshold; });
    reject(raw, variant);
  }
  const mutations = [i => i.source_window_evidence.source_frame_bindings.pop(),
    i => i.source_window_evidence.source_frame_bindings[1].pts++,
    i => i.source_window_evidence.step_motion.source_geometry_series[1].normalized_points.right_ankle[0] += .1,
    i => i.source_window_evidence.step_motion.source_geometry_series[1].point_confidences.right_ankle = .1,
    i => i.source_window_evidence.original_event_quality[0].source_track_diagnostics.confirmed_id_switch_count = 1];
  for (const mutate of mutations) { const raw = original(); mutate(instance(raw)); reject(raw, String(mutate)); }
});

test("policy, stored grade and conditions cannot manufacture a formal score", () => {
  for (const mutate of [r => delete r.scoring_policy_version, r => instance(r).scoring_policy_version = "future",
    r => instance(r).grade = "D", r => instance(r).score = 20,
    r => instance(r).conditions[0].passed = false, r => instance(r).conditions[0].value = null]) {
    const raw = original(); mutate(raw); reject(raw, String(mutate));
  }
});

test("formal totals are equal-card means and duplicate instances never inflate coverage", () => {
  const a = original(), b = original("complete", "FS07-M02");
  const e = instance(original("small_displacement")); e.event_id += "-second";
  e.source_window_annotation.annotation_id = e.event_id;
  e.window.event_id = e.event_id;
  a.cards[0].instances.push(e, structuredClone(instance(a)));
  a.cards.push(b.cards[0]); a.summary = {formal_scored_cards: 999, formal_scored_instances: 999}; a.overall_score = 999;
  const parsed = cardExecutionOf(a);
  assert.equal(parsed.summary.formal_scored_cards, 2);
  assert.equal(parsed.summary.formal_scored_instances, 3);
  assert.equal(parsed.overall_score, 80);
});

test("formal score panel is open in the real overview entry and does not wait for legacy review guidance",()=>{
 const raw=original("bound_synthetic_model");const html=renderToStaticMarkup(createElement(Overview,{result:{job_id:raw.source_run_id,card_execution:raw},motion:null,pending:false,title:"ready",description:"ready"}));
 assert.match(html,/class="indicator-assessment card-execution-panel" open=""/);assert.match(html,/正式总分/);assert.match(html,/A · 100/);assert.doesNotMatch(html,/逐卡计算与复核|测量证据参考分|待教练标定/);
});

test("model detector epochs and primary selection epochs remain distinct and independently bound",()=>{
 for(const [variant,modelEpoch,primaryEpoch] of [["sam0_primary_derived1",0,1],["mixed_sam0_primary0",0,0],["sam1_primary0",1,0]]) {
  const raw=original(variant),i=parse(raw);assert.equal(i.status,"scored");assert.equal(i.score,100);
  const e=i.source_window_evidence,p=e.step_motion.source_geometry_series.find(r=>r.pose_source==="sam3d"),b=e.source_frame_bindings.find(r=>r.frame_index===p.frame_index);
  assert.equal(p.source_binding.selection_epoch,modelEpoch);assert.equal(p.source_model_selection_epoch,modelEpoch);assert.equal(p.selection_epoch,primaryEpoch);assert.equal(b.selection_epoch,primaryEpoch);
  assert.equal(b.source_model_selection_epoch,modelEpoch);
 }
 const mutations=[(p)=>p.source_model_selection_epoch++,(p,b)=>b.source_model_selection_epoch++,(p)=>p.selection_epoch++,(p)=>p.source_binding.selection_epoch++,(p)=>delete p.model_epoch_semantics,(p,b)=>b.model_epoch_semantics="caller-claim",(p)=>{delete p.source_binding.selection_epoch;p.source_model_selection_epoch=0;}];
 for(const mutate of mutations){const raw=original("sam0_primary_derived1"),e=instance(raw).source_window_evidence;mutate(e.step_motion.source_geometry_series[0],e.source_frame_bindings[0]);reject(raw,String(mutate));}
});

test("formal FS windows cannot omit a whole or partial derived epoch ledger",()=>{
 const mutations=[i=>delete i.window.derived_selection_epoch,i=>i.source_window_evidence.source_frame_bindings.forEach(b=>delete b.derived_selection_epoch),i=>delete i.source_window_evidence.source_frame_bindings[1].derived_selection_epoch,i=>i.source_window_evidence.source_frame_bindings[1].derived_selection_epoch++];
 for(const mutate of mutations){const raw=original("sam0_primary_derived1");mutate(instance(raw));reject(raw,String(mutate));}
});
