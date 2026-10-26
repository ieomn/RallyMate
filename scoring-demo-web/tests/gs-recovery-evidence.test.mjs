import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import { gunzipSync } from "node:zlib";
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
const { default: Evidence } = await import(compiledUrl(new URL("../app/ManualCardEvidence.tsx", import.meta.url)));
const { default: CardExecutionPanel } = await import(compiledUrl(new URL("../app/CardExecutionPanel.tsx", import.meta.url)));
const { buildReportBackup, buildPracticeReport } = await import(compiledUrl(new URL("../app/lib/report-export.ts", import.meta.url)));

const { recoveryPolicy, validateGsRecovery } = await import(compiledUrl(new URL('../app/lib/gs-recovery-evidence.ts', import.meta.url)));
const fixtures = JSON.parse(gunzipSync(fs.readFileSync(new URL('./fixtures/gs-recovery-contract-v1.json.gz', import.meta.url))));
const row = (r, id='GS02-M10-02') => r.cards.find(c => c.indicator_id === id);
const item = (r, id='GS02-M10-02') => row(r,id).instances[0];
// This wrapper tests the unified reader against unchanged synthetic service
// instances. It is not a unified production/API/media execution claim.
const report = (name='projected_return') => ({...structuredClone(fixtures.cases.find(c=>c.name===name).report), version:'card-execution-v1.0.0'});
const body = [...recoveryPolicy.rotation_cards, ...recoveryPolicy.stability_cards];
const ids = [...body, ...Object.keys(recoveryPolicy.manual_requirements)];
function mutation(name, mutate, caseName='projected_return', id='GS02-M10-02') {
  const r=report(caseName), i=item(r,id); mutate(i,r); const p=cardExecutionOf(r), actual=item(p,id);
  assert.equal(actual.score,null,name); assert.equal(actual.grade,null,name); assert.equal(actual.proxy_grade,null,name);
  assert.equal(actual.status,'unavailable',name); assert.notEqual(actual.recovery_validation?.status,'measured_recovery',name);
  assert.deepEqual(cardExecutionOf(p),p,`${name}: repeated normalization`);
}
function syncStage(i) {
  const p=i.recovery_evidence, ref=p.reference;
  if(ref) {
    const offset=ref.continuity_source_geometry_series.length-p.stage.source_geometry_series.length;
    p.stage.source_geometry_series.forEach((g,k)=>{ref.continuity_source_geometry_series[offset+k]=structuredClone(g);});
  }
}
function backupInput(raw) { return {mode:'live',uploadState:'complete',job:{id:raw.source_run_id,status:'completed',original_filename:'synthetic-contract-only.mp4'},
  evidence:{jobId:raw.source_run_id,error:null,result:{job_id:raw.source_run_id,card_execution:raw}},summary:null}; }

test('producer synthetic service cases preserve every expected status, grade and score through independent geometry calculation',()=>{
  assert.match(fixtures.scope,/synthetic/i);
  for(const c of fixtures.cases){const r=report(c.name), bytes=JSON.stringify(r), p=cardExecutionOf(r);
    assert.equal(p.cards.length,248,c.name); assert.equal(p.summary.rejected_cards,0,c.name);
    for(const [id,e] of Object.entries(c.expected)){const actual=item(p,id); for(const key of ['status','grade','score'])assert.equal(actual[key],e[key],`${c.name}/${id}/${key}`);
      assert.equal(actual.original_grade,null);assert.equal(actual.source_grade_supported,false);
      if(e.status==='scored'){assert.equal(actual.recovery_validation.status,'measured_recovery');assert.ok(actual.recovery_validation.condition_values_match);}
    }
    assert.equal(JSON.stringify(r),bytes);assert.deepEqual(cardExecutionOf(p),p,c.name);
  }
});

test('wrong versions, saved flags, source claims and missing current conditions cannot authorize a recovery score',()=>{
  for(const [name,fn] of [
    ['version',i=>{i.recovery_evidence.version='future';}],['policy',i=>{i.recovery_evidence.policy_version='future';}],
    ['revision',i=>{i.scoring_revision_id='future';}],['no evidence',i=>{delete i.recovery_evidence;}],
    ['original grade',i=>{i.original_grade='A';}],['threshold',i=>{i.conditions[0].threshold=-999;}],
    ['condition value',i=>{i.conditions[0].value=999;}],['condition missing',i=>{i.conditions.pop();}],
    ['proof flag',i=>{i.recovery_evidence.status='unavailable';}],['stored arithmetic',i=>{i.recovery_evidence.measurements.shoulder_reference_error_reduction_deg.statistics.value=999;}],
  ]){const r=report(),i=item(r);fn(i);const actual=item(cardExecutionOf(r));assert.equal(actual.score,null,name);assert.equal(actual.grade,null,name);}
});

test('geometry recomputation rejects missing common joints, confidence lies, changed normalized/reference geometry and fixed scales',()=>{
  for(const [name,fn] of [
    ['missing point',g=>{delete g.points.left_hip;}],['confidence',g=>{g.point_confidences.left_hip=.1;}],
    ['NaN',g=>{g.reference_points.left_shoulder[0]=NaN;}],['normalized mismatch',g=>{g.points.left_shoulder[0]+=.2;}],
    ['reference mismatch',g=>{g.reference_points.left_shoulder[0]+=20;}],['old center',g=>{g.center[0]+=1;}],
    ['old scale',g=>{g.scale+=1;}],['basis',g=>{g.normalization_basis='shoulder_only';}],
    ['model claim',g=>{g.observation_semantics='monocular_model_estimate';g.pose_source='sam3d';g.point_confidences.left_hip=null;delete g.source_binding;}],
  ]) mutation(name,i=>{fn(i.recovery_evidence.stage.source_geometry_series[2]);syncStage(i);});
  mutation('saved scale',i=>{i.recovery_evidence.stage.fixed_torso_scale*=2;});
  mutation('endpoint fraction',i=>{i.recovery_evidence.stage.endpoint_count=2;});
  mutation('saved axis',i=>{i.recovery_evidence.stage.axis_degrees.shoulder[0]=90;});
  mutation('saved speed',i=>{i.recovery_evidence.stage.hip_speeds_torso_s[0]=99;});
});

test('detector raw binding also matches every original frame and is not just a video SHA',()=>{
  for(const key of ['frame_index','person_track_id','selection_epoch','camera_reference_epoch','pts','time_base_num','time_base_den','timestamp_ms'])
    mutation(`detector raw ${key}`,i=>{i.recovery_evidence.stage.source_geometry_series[2].source_binding[key]+=1;syncStage(i);});
  mutation('bridge detector raw binding',i=>{i.recovery_evidence.reference.continuity_source_geometry_series[4].source_binding.person_track_id+=1;});
});

test('complete stage source frames and primary/derived/camera/PTS ledger remain mandatory',()=>{
  for(const key of ['derived_selection_epoch','selection_epoch','camera_reference_epoch','pts','time_base_num','time_base_den','selected_person_track_id'])
    mutation(key,i=>{i.source_stage_evidence.source_frame_bindings[2][key]+=1;});
  mutation('remove complete derived column',i=>{for(const b of i.source_stage_evidence.source_frame_bindings)delete b.derived_selection_epoch;});
  mutation('remove first frame',i=>{i.source_stage_evidence.source_frame_bindings.shift();});
  mutation('remove last frame',i=>{i.source_stage_evidence.source_frame_bindings.pop();});
  mutation('remove internal frame',i=>{i.source_stage_evidence.source_frame_bindings.splice(2,1);});
  mutation('video',i=>{i.source_stage_evidence.source_frame_bindings[2].video_sha256='b'.repeat(64);});
  mutation('source duplicate',i=>{i.recovery_evidence.stage.source_frame_bindings[2].timestamp_ms+=1;});
});

test('independent pre-action reference and every intervening provenance row are required without inventing missing samples',()=>{
  const mutations=[
    ['reference doc',i=>{i.recovery_evidence.reference.binding.document_sha256='b'.repeat(64);}],
    ['action start',i=>{i.source_annotation.action_window.start_ms+=10;}],
    ['reference anchor',i=>{i.recovery_evidence.reference.binding.start_frame.pts+=1;}],
    ['reference copied stage',i=>{i.recovery_evidence.reference.source_geometry_series=i.recovery_evidence.stage.source_geometry_series;}],
    ['bridge ledger missing',i=>{i.recovery_evidence.reference.continuity_timeline.splice(4,1);}],
    ['bridge sample missing only',i=>{i.recovery_evidence.reference.continuity_source_geometry_series[4]=null;}],
    ['bridge geometry removed',i=>{i.recovery_evidence.reference.continuity_source_geometry_series.splice(4,1);}],
    ['bridge camera',i=>{i.recovery_evidence.reference.continuity_timeline[4].camera_reference_epoch+=1;}],
    ['bridge raw identity',i=>{i.recovery_evidence.reference.continuity_source_geometry_series[4].track+=1;}],
    ['bridge selected conflict',i=>{i.recovery_evidence.reference.continuity_timeline[4].rejected_pose_person_track_id=i.person_track_id;}],
  ];for(const [name,fn]of mutations)mutation(name,fn);
});

test('bound model zero epoch stays separate from primary two; broken raw model source and hidden bridge changes cannot score',()=>{
  const name='model_bound_raw_epoch_zero_primary_two', source=item(report(name));
  assert.equal(source.window.selection_epoch,2);assert.equal(source.recovery_evidence.stage.source_geometry_series[0].source_binding.selection_epoch,0);
  for(const key of ['frame_index','processed_index','person_track_id','selection_epoch','camera_reference_epoch','pts','time_base_num','time_base_den','timestamp_ms'])
    mutation(`raw model ${key}`,i=>{i.recovery_evidence.stage.source_geometry_series[2].source_binding[key]+=1;syncStage(i);},name);
  mutation('unbound model',i=>{delete i.recovery_evidence.stage.source_geometry_series[2].source_binding;syncStage(i);},name);
  mutation('bridge model changed',i=>{i.recovery_evidence.reference.continuity_source_geometry_series[4].source_binding.selection_epoch+=1;},name);
  mutation('model namespace removed',i=>{for(const g of i.recovery_evidence.stage.source_geometry_series){delete g.model_epoch_semantics;delete g.source_model_selection_epoch;}syncStage(i);},name);
});

test('all thirteen historical M10 scores retire with exact original condition evidence and no new low grade',()=>{
  const r=report(),base=structuredClone(item(r));
  for(const id of ids){const c=row(r,id);assert.ok(c,id);const i=structuredClone(base);Object.assign(i,{indicator_id:id,measurement_version:'card-kinematics-v1.0.0',scoring_revision_id:null,recovery_evidence:null});
    i.conditions=[{key:'historical_wrist',metric:'wrist_separation_torso.delta',operator:'lte',threshold:-.15,unit:'torso',value:-.5,passed:true,evidence:{original_series:[1,2,3],source_frames:[1,2,3]}}];
    c.instances=[i];c.rule_kind='visual_proxy';}
  const p=cardExecutionOf(r);assert.equal(p.cards.length,248);assert.equal(p.summary.rejected_cards,0);
  for(const id of ids){const i=item(p,id);assert.equal(i.score,null,id);assert.equal(i.grade,null);assert.equal(i.proxy_grade,null);assert.equal(i.status,'unavailable');
    assert.equal(i.scoring_revision_id,recoveryPolicy.scoring_revision_id);assert.equal(i.conditions.length,0);assert.equal(i.superseded_conditions[0].passed,null);
    assert.deepEqual(i.superseded_condition_evidence[0].evidence,item(r,id).conditions[0].evidence);}
  assert.deepEqual(cardExecutionOf(p),p);
});

test('manual hand/racket or complete readiness cards never gain a score from forged auxiliary evidence',()=>{
  for(const id of Object.keys(recoveryPolicy.manual_requirements)){const r=report(),c=row(r,id),i=structuredClone(item(r));Object.assign(i,{indicator_id:id,status:'scored',score:100,grade:'A',proxy_grade:'A',missing_required_observables:[]});
    i.auxiliary_observations={version:recoveryPolicy.auxiliary_version,scoring_eligible:true,measurements:{control:100},status:'measured'};c.instances=[i];
    const actual=item(cardExecutionOf(r),id);assert.equal(actual.score,null,id);assert.equal(actual.grade,null);assert.deepEqual(actual.missing_required_observables,recoveryPolicy.manual_requirements[id][1]);}
});

test('source copies cannot disagree after normalization and invalid copies never display a measured summary',()=>{
  const r=report(),i=item(r);i.recovery_parent_context={action_id:i.action_id,source_annotation:structuredClone(i.source_annotation)};i.source_annotation.action_window.start_ms+=1;
  const p=cardExecutionOf(r),actual=item(p);assert.equal(actual.score,null);assert.equal(actual.recovery_validation.status,'invalid');
  assert.notEqual(validateGsRecovery(actual.recovery_evidence,actual.recovery_parent_context,actual).status,'measured_recovery');
  const markup=renderToStaticMarkup(createElement(Evidence,{instance:actual}));assert.doesNotMatch(markup,/共同有效/);assert.match(markup,/未经验证的输入记录/);
});

test('legal short source windows remain unavailable with original observations and honest missing-data text',()=>{
  const raw=report('three_frame_candidate'),p=cardExecutionOf(raw),i=item(p);assert.equal(i.recovery_validation.status,'unavailable');
  assert.ok(i.reason_codes.includes('recovery_continuous_window_too_short'));assert.equal(i.recovery_evidence.stage.total_frame_count,3);
  assert.deepEqual(i.recovery_evidence,item(raw).recovery_evidence);assert.equal(i.score,null);
  const markup=renderToStaticMarkup(createElement(Evidence,{instance:i}));assert.match(markup,/本卡未评分/);assert.doesNotMatch(markup,/需要复核|共同有效/);
});

test('new recovery provenance, full geometry, superseded records and values survive backup and second normalization exactly',()=>{
  for(const c of fixtures.cases){const raw=report(c.name),p=cardExecutionOf(raw),backup=buildReportBackup(backupInput(raw),'2026-10-24T09:00:00Z');
    assert.deepEqual(cardExecutionOf(backup.result.card_execution),p,c.name);
    for(const id of Object.keys(c.expected)){const i=item(p,id),stored=item(backup.result.card_execution,id);assert.deepEqual(stored.recovery_evidence,i.recovery_evidence,c.name+id);}
    assert.match(JSON.stringify(buildPracticeReport(backupInput(raw))),/准备|恢复/);
  }
});


test('formal panel presents recovery grades and keeps missing observations selectable without a review prompt',()=>{
  const raw=report('stable_rotated_not_returned');raw.cards=raw.cards.filter(c=>c.indicator_id==='GS02-M10-02');
  const i=item(raw);const absent=structuredClone(i);absent.event_id='missing-stage';absent.source_annotation.annotation_id='missing-stage';absent.status='unavailable';absent.grade=null;absent.proxy_grade=null;absent.score=null;
  absent.reason_codes=['recovery_continuous_window_too_short'];raw.cards[0].instances.unshift(absent);
  const html=renderToStaticMarkup(createElement(CardExecutionPanel,{value:raw}));
  assert.match(html,/40/);assert.match(html,/肩髋轴相对独立动作前准备参照/);assert.doesNotMatch(html,/需要复核|人工复核/);
  assert.match(html,/option/);assert.match(html,/missing-stage/);
});


test('an impossible M10 common denominator clears its grade and is idempotent through backup normalization',()=>{
  const raw=report(),i=item(raw);i.quality.valid_frame_count=11;i.recovery_evidence.stage.valid_frame_count=11;
  const parsed=cardExecutionOf(raw),actual=item(parsed);
  assert.equal(actual.status,'unavailable');assert.equal(actual.score,null);assert.equal(actual.grade,null);
  assert.equal(actual.quality.total_frame_count,10);assert.equal(actual.quality.valid_frame_count,0);assert.equal(actual.quality.fraction,0);
  assert.equal(actual.recovery_evidence.stage.valid_frame_count,11,'invalid original proof stays diagnostic');
  assert.deepEqual(cardExecutionOf(parsed),parsed);
  const backup=buildReportBackup(backupInput(raw),'2026-10-24T09:00:00Z');assert.deepEqual(cardExecutionOf(backup.result.card_execution),parsed);
});
