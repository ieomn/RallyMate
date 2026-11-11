import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import ts from "typescript";
import { tennisScopedFixture } from "./fixtures/tennis-assessment.mjs";
Error.stackTraceLimit = 0;

const cache=new Map();
function compiledUrl(file) {
  if(cache.has(file.href))return cache.get(file.href);
  const source=fs.readFileSync(file,"utf8");
  let js=file.pathname.endsWith(".json")?`export default ${source}`:ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText;
  js=js.replace(/from "([^"]+)"/g,(_,name)=>`from ${JSON.stringify(name.startsWith(".")?compiledUrl(new URL(name.endsWith(".json")?name:`${name}.ts`,file)):import.meta.resolve(name))}`);
  const url=`data:text/javascript;base64,${Buffer.from(js).toString("base64")}`;cache.set(file.href,url);return url;
}
const {validatedTennisAssessment}=await import(compiledUrl(new URL("../app/lib/tennis-assessment.ts",import.meta.url)));
const {buildReportBackup}=await import(compiledUrl(new URL("../app/lib/report-export.ts",import.meta.url)));
const {readReportJson}=await import(compiledUrl(new URL("../app/lib/report-file.ts",import.meta.url)));
const {specialistMotionOf}=await import(compiledUrl(new URL("../app/lib/specialist-motion.ts",import.meta.url)));
const rubric={version:"specialist-motion-assessment-v1.0.0",coordinate_space:"hip_centered_torso_normalized_body_basis",threshold_origin:"engineering_initial_values_not_coach_calibration",
  preparation_path_ratio:{full_score_at_or_below:.65,zero_score_at_or_above:1.8},follow_path_ratio:{full_score_at_or_below:.85,zero_score_at_or_above:2},
  drive_efficiency:{zero_score_at_or_below:.4,full_score_at_or_above:.9},active_striking_path_minimum_torso:.02,camera_observation_required_for_native_paths:false,short_swing_alone_classifies_defense_or_volley:false};
function report() {
  const row=tennisScopedFixture();row.rubric.specialist_motion=rubric;
  row.dimensions[1].score=100;
  row.dimensions[1].metrics=[{id:"specialist_compact_preparation",label:"紧凑引拍",value:.3,unit:"path_ratio_to_striking",score:100,weight:1,observation_count:2,target:"保持紧凑"}];
  row.overall_score=78;
  for(const [index,e] of row.episodes.entries()) {
    e.family="return";e.source_selection_epoch=0;e.camera_reference_epoch=0;
    delete e.metrics.preparation_metric;e.metrics.specialist_compact_preparation={value:.3,score:100};
    const first=index*100;
    e.specialist_motion={version:"specialist-motion-assessment-v1.0.0",family:"return",status:"scored",replace_metrics:["coil"],
      context_profile:{module:"RS",technique:null,swing_policy:"compact_control",classification_scope:"context_conditioned_not_video_verified",reasons:["subtype_evidence_missing_preserve_family_score"]},
      evidence:{family_basis:"source_bound_opponent_serve_and_incoming_ball",active_wrist:"right",source_video_sha256:row.source_video_sha256,
        subject_id:e.subject_id,person_track_id:e.person_track_id,source_selection_epoch:0,camera_reference_epoch:0,start_ms:e.start_ms,end_ms:e.end_ms,
        source_frames:Array.from({length:31},(_,i)=>first+i),native_source_verified:true,camera_reference_required_for_local_paths:false,
        path_scope:"observed_native_motion_phases_not_complete_recovery",coordinate_space:"hip_centered_torso_normalized_body_basis",
        preparation_path_torso:.3,striking_path_torso:1,follow_through_path_torso:null,terminal_to_peak_speed_ratio:null,striking_net_displacement_torso:1,recovery_end_verified:false,
        phase_frame_bounds:{preparation:[first,first+10],striking:[first+10,first+20],follow_through:[first+20,first+30]}}};
  }
  return row;
}
const context=r=>({jobId:r.source_run_id,videoSha256:r.source_video_sha256,durationMs:9000});

test("measured return provenance and numeric path evidence survive full JSON roundtrip",async()=>{
  const source=report(),parsed=validatedTennisAssessment(source,context(source));assert.ok(parsed);
  assert.deepEqual(parsed.episodes[0].specialist_motion,source.episodes[0].specialist_motion);
  assert.deepEqual(parsed.rubric.specialist_motion,rubric);
  const result={job_id:source.source_run_id,input:{video:{sha256:source.source_video_sha256,duration_ms:9000}},tennis_assessment:parsed};
  const encoded=JSON.stringify(buildReportBackup({mode:"live",uploadState:"complete",job:{id:source.source_run_id,status:"succeeded"},evidence:{jobId:source.source_run_id,result}}));
  const restored=await readReportJson({size:Buffer.byteLength(encoded),text:async()=>encoded});
  assert.deepEqual(restored.result.tennis_assessment.episodes[0].specialist_motion,parsed.episodes[0].specialist_motion);
  assert.deepEqual(validatedTennisAssessment(restored.result.tennis_assessment,context(source)),parsed);
});

test("foreign optional diagnostics are omitted without erasing a valid generic score",()=>{
  const source=tennisScopedFixture();source.episodes[0].specialist_motion=report().episodes[0].specialist_motion;
  source.episodes[0].specialist_motion.evidence.source_video_sha256="b".repeat(64);
  const parsed=validatedTennisAssessment(source,context(source));assert.ok(parsed);assert.equal(parsed.overall_score,70);
  assert.equal(parsed.episodes[0].specialist_motion,undefined);
});

test("used specialist grades cannot survive foreign source, altered path or forged score",()=>{
  for(const mutate of [e=>e.specialist_motion.evidence.source_video_sha256="b".repeat(64),
    e=>e.specialist_motion.evidence.subject_id="other",e=>e.specialist_motion.evidence.preparation_path_torso=2,
    e=>e.metrics.specialist_compact_preparation.score=30,e=>e.specialist_motion.evidence.source_frames[2]=10000]){
    const source=report();mutate(source.episodes[0]);assert.equal(validatedTennisAssessment(source,context(source)),null);
  }
});

test("direct-drive and terminal-control curves are checked against their own evidence",()=>{
  const r=report(),e=r.episodes[0],special=e.specialist_motion;
  e.metrics={specialist_direct_drive:{value:.7,score:60},specialist_terminal_control:{value:.6,score:50},specialist_short_follow_through:{value:.4,score:100}};
  special.replace_metrics=["coil_release","release_sequence","pattern_projected_sequence","follow_path"];
  special.evidence.follow_through_path_torso=.4;
  special.evidence.striking_net_displacement_torso=.7;special.evidence.terminal_to_peak_speed_ratio=.6;special.evidence.recovery_end_verified=true;
  const scope={sourceVideoSha256:r.source_video_sha256,subjectId:e.subject_id,personTrackId:1,selectionEpoch:0,cameraEpoch:0,startMs:e.start_ms,endMs:e.end_ms,family:"return",metrics:e.metrics};
  assert.ok(specialistMotionOf(special,scope));
  special.evidence.recovery_end_verified=false;assert.equal(specialistMotionOf(special,scope),null);
});
