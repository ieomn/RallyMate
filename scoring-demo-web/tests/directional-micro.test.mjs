import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import { gunzipSync } from "node:zlib";
import { createHash } from "node:crypto";
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
const { cardExecutionOf, selectedCardInstance, cardReasonLabel } = await import(compiledUrl(new URL("../app/lib/card-execution.ts", import.meta.url)));
const { default: Panel } = await import(compiledUrl(new URL("../app/CardExecutionPanel.tsx", import.meta.url)));
const { default: MicroEvidence } = await import(compiledUrl(new URL("../app/DirectionalMicroEvidence.tsx", import.meta.url)));
const compressed = fs.readFileSync(new URL("./fixtures/directional-micro-native-e420-unified.json.gz", import.meta.url));
const response = gunzipSync(compressed), full = JSON.parse(response);
const metadata = JSON.parse(fs.readFileSync(new URL("./fixtures/directional-micro-native-e420-unified.provenance.json", import.meta.url), "utf8"));
const microIds = ["FS05-M03", "FS05-M04"];
const microOnly = () => structuredClone({ ...full, cards: full.cards.filter(c => microIds.includes(c.indicator_id)) });
const instance = (r, id = "FS05-M03") => r.cards.find(c => c.indicator_id === id).instances[0];
const evidence = i => i.source_window_evidence.directional_micro_adjustment;
function reject(mutate, label) {
  const raw = microOnly(); mutate(instance(raw), raw); const parsed = cardExecutionOf(raw), i = instance(parsed);
  assert.equal(i.score, null, label); assert.equal(i.grade, null, label); assert.equal(i.proxy_grade, null, label);
  assert.notEqual(i.status, "scored", label);
  return i;
}

test("full real native e420 API bytes recompute all 298 cards and equal-card totals", () => {
  assert.equal(createHash("sha256").update(compressed).digest("hex"), metadata.gzip_sha256);
  assert.equal(createHash("sha256").update(response).digest("hex"), metadata.response_sha256);
  assert.equal(metadata.actual_http_response_equal, true);
  assert.equal(full.source_run_id, "e4204ec3-7d84-4a90-a961-9dad01b43c2e");
  const p = cardExecutionOf(full);
  assert.equal(p.cards.length, 298); assert.equal(p.summary.formal_scored_cards, 7); assert.equal(p.summary.formal_scored_instances, 113);
  assert.equal(p.summary.unavailable_instances, 74); assert.ok(Math.abs(p.overall_score - 56.70248775838838) < 1e-12);
  assert.equal(p.summary.rejected_cards, 0); assert.deepEqual(cardExecutionOf(p), p);
  assert.deepEqual(p.directional_micro_adjustment_execution, full.input.directional_micro_adjustment_execution);
  assert.deepEqual(p.directional_micro_adjustment_windows, full.directional_micro_adjustment_windows);
  assert.deepEqual(p.limitation_codes, full.limitations);
});

test("fixed real 112–136 gives two C60s with negative approach and intact local/model geometry", () => {
  const p = cardExecutionOf(microOnly());
  for (const id of microIds) {
    const i = instance(p, id), raw = instance(full, id), v = i.directional_micro_validation;
    assert.equal(i.status, "scored", i.reason_codes.join(",")); assert.equal(i.grade, "C"); assert.equal(i.score, 60);
    assert.deepEqual(i.conditions.map(c => c.passed), [false, true, true]); assert.ok(i.conditions[0].value < 0);
    assert.equal(i.original_grade, null); assert.equal(i.source_grade_supported, false);
    assert.equal(v.status, "measured_directional_motion"); assert.equal(v.total_frame_count, 25); assert.equal(v.endpoint_count, 5);
    assert.equal(v.moving_foot, "right"); assert.equal(v.feet.right.complete_visual_motion_cycle, true); assert.equal(v.feet.left.complete_visual_motion_cycle, false);
    assert.deepEqual(i.source_window_evidence, raw.source_window_evidence); assert.deepEqual(i.source_window_annotation, raw.source_window_annotation);
    assert.equal(evidence(i).local_normalization[0].input_geometry.normalization_basis, "right_shoulder_right_torso");
    assert.equal(evidence(i).source_geometry_series[0].source_binding.selection_epoch, 0); assert.equal(evidence(i).source_frame_bindings[0].selection_epoch, 1);
    assert.deepEqual(i.source_window_evidence.source_frames, Array.from({length:25}, (_,n)=>112+n));
    const html = renderToStaticMarkup(createElement(MicroEvidence, {instance:i}));
    assert.match(html,/三个独立条件/); assert.match(html,/远离此帧可见球/); assert.match(html,/不是预测击球点/);
  }
});

test("manual annotation or score fields cannot replace missing source or formal rule", () => {
  for (const mutate of [
    i=>delete i.source_window_evidence.directional_micro_adjustment,
    i=>delete i.source_window_annotation.direction_reference,
    i=>{delete evidence(i).direction_reference.target_point_px;delete i.source_window_annotation.direction_reference.target_point_px;},
    (_i,r)=>delete r.scoring_policy_version, i=>delete i.scoring_policy_version,
    i=>i.application_rule_version="future",i=>i.measurement_version="future",(_i,r)=>r.cards[0].runtime_engine="manual-claim",
    i=>evidence(i).direction_reference.quality_waiver=true,i=>i.original_grade="C",
  ]) reject(mutate,String(mutate));
});

test("old/local full torso coordinates and every one of the six common joints are independently checked", () => {
  for (const mutate of [
    i=>evidence(i).local_normalization[5].input_center[0]+=5,i=>evidence(i).local_normalization[5].input_scale*=1.1,
    i=>evidence(i).local_normalization[5].input_geometry.normalized_points.left_hip[0]+=.1,
    i=>evidence(i).source_geometry_series[5].normalized_points.left_hip[0]+=.1,
    i=>evidence(i).reference_series[5].points.right_ankle[0]+=5,i=>evidence(i).source_geometry_series[5].normalization_basis="left_shoulder_left_torso",
    i=>evidence(i).source_geometry_series[5].normalized_points.left_shoulder=null,
    i=>evidence(i).source_geometry_series[5].point_confidences.left_ankle=.1,
    i=>evidence(i).local_normalization[5].source_geometry_verified=false,i=>evidence(i).source_geometry_series.pop(),
  ]) reject(mutate,String(mutate));
  const invalid=reject(i=>evidence(i).source_geometry_series[5].normalized_points.left_shoulder=null,"missing geometry display");
  const html=renderToStaticMarkup(createElement(MicroEvidence,{instance:invalid}));
  assert.doesNotMatch(html,/六个必要关节均由原帧连续提供/);assert.match(html,/尚无通过校验的完整六关节连续测量/);
});

test("raw model namespace, primary epochs, full derived ledger and exact original clocks cannot be bypassed", () => {
  for (const mutate of [
    i=>evidence(i).source_geometry_series[5].source_binding.frame_index++,i=>evidence(i).source_geometry_series[5].source_binding.selection_epoch=1,
    i=>evidence(i).source_geometry_series[5].source_model_selection_epoch=1,i=>delete evidence(i).source_geometry_series[5].model_epoch_semantics,
    i=>evidence(i).source_geometry_series[5].source_binding.video_sha256="a".repeat(64),i=>evidence(i).source_geometry_series[5].source_binding.person_track_id=2,
    i=>evidence(i).source_geometry_series[5].source_binding.camera_reference_epoch++,i=>i.source_window_evidence.source_pts[5].pts++,
    i=>i.source_window_evidence.source_frame_bindings[5].selection_epoch++,
    i=>{delete i.window.derived_selection_epoch;i.source_window_evidence.source_frame_bindings.forEach(b=>delete b.derived_selection_epoch);evidence(i).source_frame_bindings.forEach(b=>delete b.derived_selection_epoch);},
    i=>i.source_window_evidence.original_event_quality[0].raw_input_quality_flags.push("phase_boundary_censored"),
    i=>i.source_window_evidence.original_event_quality[0].confirmed_id_switch_count=1,
  ]) reject(mutate,String(mutate));
});

test("processed indices bind the anchor and progress with source frames without requiring equal absolute indices", () => {
  for(const n of [0,5]) reject(i=>{
    evidence(i).source_geometry_series[n].source_binding.processed_index+=7;
    evidence(i).local_normalization[n].input_geometry.source_binding.processed_index+=7;
  },`processed-${n}`);
  const raw=microOnly(),i=instance(raw),e=evidence(i);
  e.source_record_frame.processed_index+=100;
  e.source_geometry_series.forEach(r=>r.source_binding.processed_index+=100);
  e.local_normalization.forEach(r=>r.input_geometry.source_binding.processed_index+=100);
  assert.equal(instance(cardExecutionOf(raw)).score,60,"consistent nonzero processed origin remains valid mathematics");
});

test("axis, ball, image dimensions and camera transform retain their original source meaning", () => {
  for (const mutate of [
    i=>{evidence(i).direction_reference.axis_role="transverse_projection";i.source_window_annotation.direction_reference.axis_role="transverse_projection";},
    i=>{evidence(i).direction_reference.source_frame.pts++;i.source_window_annotation.direction_reference.source_frame.pts++;},
    i=>evidence(i).source_record_frame.index++,i=>evidence(i).projected_reference.frame_dimensions_px[0]=512,
    i=>{evidence(i).direction_reference.target_point_px[0]+=100;i.source_window_annotation.direction_reference.target_point_px[0]+=100;},
    i=>evidence(i).projected_reference.matrix_to_reference[0][1]+=.1,i=>evidence(i).projected_reference.axis_points_reference_px[0][0]+=1,
    i=>{evidence(i).direction_reference.uncertainty_px=500;i.source_window_annotation.direction_reference.uncertainty_px=500;},
    (_i,r)=>r.directional_micro_adjustment_windows[0].direction_reference.target_point_px[0]+=100,
    (_i,r)=>r.input.directional_micro_adjustment_execution.annotation_document_sha256="a".repeat(64),
    (_i,r)=>{r.directional_micro_adjustment_execution=structuredClone(r.input.directional_micro_adjustment_execution);r.input.directional_micro_adjustment_execution.annotation_document_sha256="a".repeat(64);},
    (_i,r)=>delete r.directional_micro_adjustment_windows,
  ]) reject(mutate,String(mutate));
});

test("phase booleans saved measurements thresholds and grades never authorize scores", () => {
  for (const mutate of [
    i=>evidence(i).feet.right.phases[1].duration_ms=20,i=>evidence(i).feet.left.complete_visual_motion_cycle=true,
    i=>{for(const o of [evidence(i).moving_foot_observation,i.source_window_annotation.step_motion_observation,i.window.step_motion_observation]) o.moving_foot="left";},
    i=>evidence(i).fixed_torso_scale*=2,i=>evidence(i).endpoint_count=1,
    i=>evidence(i).measurements.hip_axis_target_error_reduction_torso=.2,
    i=>{i.conditions[0].value=.2;i.conditions[0].passed=true;i.grade=i.proxy_grade="A";i.score=100;},
    i=>i.conditions[0].passed=true,i=>i.conditions[0].threshold=-1,i=>i.conditions[0].value=null,i=>i.score=100,
  ]) reject(mutate,String(mutate));
});

test("formal defaults show a real scored temporal instance while explicit missing selection and legacy order remain intact", () => {
  const p=cardExecutionOf(full),card=p.cards.find(c=>c.indicator_id==="FS01-M02"),first=card.instances[0];
  assert.equal(first.status,"unavailable");const selected=selectedCardInstance(card.instances,"",true);assert.equal(selected.status,"scored");
  assert.equal(selectedCardInstance(card.instances,"",false),first);
  assert.equal(selectedCardInstance(card.instances,`${first.video_id}:${first.event_id}:${first.person_track_id}`,true),first);
  assert.equal(card.instances[0],first);
  const html=renderToStaticMarkup(createElement(Panel,{value:full}));assert.match(html,new RegExp(`${selected.grade} · ${selected.score} / 100`));assert.doesNotMatch(html,/<strong>— 未评分/);
  assert.doesNotMatch(html.replace(/<pre[\s\S]*?<\/pre>/g,""),/需要复核|需人工复核|保留复核|manual_image_references_not_metric_court_or_target_truth|application_thresholds_uncalibrated/);
});

test("formal source notes preserve raw codes but do not ask for review; legacy language is unchanged", () => {
  for(const code of ["fallback_local_dip_action_type_unverified","temporal_boundary_quality_requires_review","temporal_required_joint_quality_requires_review","future_source_note"]){
    assert.doesNotMatch(cardReasonLabel(code,true),/复核/);assert.match(cardReasonLabel(code,false),/复核/);
  }
  const temporal=structuredClone(full.cards.find(c=>c.indicator_id==="FS01-M02"));
  const selected=temporal.instances.find(i=>i.status==="scored"&&i.evidence?.fallback?.selected);assert.ok(selected,"real local dip instance required");
  selected.advisory_reason_codes=[...(selected.advisory_reason_codes??[]),"temporal_boundary_quality_requires_review"];selected.advisory_flags=[...(selected.advisory_flags??[]),"primary_identity_ambiguous"];temporal.instances=[selected];
  const report={...full,cards:[temporal]},i=cardExecutionOf(report).cards[0].instances[0];assert.equal(i.status,"scored");assert.deepEqual(i.advisory_reason_codes,selected.advisory_reason_codes);
  const html=renderToStaticMarkup(createElement(Panel,{value:report})).replace(/<pre[\s\S]*?<\/pre>/g,"");assert.doesNotMatch(html,/复核/);assert.match(html,/局部髋部下降|局部下降/);
});

test("portable JSON backup keeps complete directional sources while readable export exposes only the aggregate", async () => {
  const {buildReportBackup,buildPracticeReport,renderReportMarkdown}=await import(compiledUrl(new URL("../app/lib/report-export.ts",import.meta.url)));
  const input={mode:"live",uploadState:"complete",job:{id:full.source_run_id,status:"completed",original_filename:"native-e420.mp4"},
    evidence:{jobId:full.source_run_id,error:null,result:{job_id:full.source_run_id,card_execution:full}},summary:null};
  const backup=JSON.parse(JSON.stringify(buildReportBackup(input,"2026-10-24T00:00:00Z")));
  const parsed=cardExecutionOf(full);assert.deepEqual(backup.result.card_execution,parsed);
  assert.deepEqual(cardExecutionOf(backup.result.card_execution),parsed);
  const report=buildPracticeReport(input,"2026-10-24T00:00:00Z");
  assert.equal(report.sections.some(s=>s.title.startsWith("逐卡")),false);
  const rows=backup.result.card_execution.cards.filter(c=>["FS05-M03","FS05-M04"].includes(c.indicator_id)).flatMap(c=>c.instances);assert.equal(rows.length,2);
  for(const row of rows){assert.equal(row.grade,"C");assert.equal(row.score,60);assert.equal(row.conditions.length,3);}
  const markdown=renderReportMarkdown(report);assert.match(markdown,/步伐动作得分/);assert.doesNotMatch(markdown,/113 条实例|原卡技术等级|FS05\\-M0/);
});
