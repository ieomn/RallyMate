import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import ts from "typescript";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

const cache=new Map();
function compiledUrl(file) {
  if(cache.has(file.href)) return cache.get(file.href);
  let js=ts.transpileModule(fs.readFileSync(file,"utf8"),{fileName:file.pathname,compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText;
  js=js.replace(/import "[^"]+\.css";/g,"").replace(/from "([^"]+)"/g,(_,name)=>`from ${JSON.stringify(name.startsWith(".")?compiledUrl(new URL(`${name}.ts`,file)):import.meta.resolve(name))}`);
  const url=`data:text/javascript;base64,${Buffer.from(js).toString("base64")}`;cache.set(file.href,url);return url;
}
const {trainingSessionAnalysisOf}=await import(compiledUrl(new URL("../app/lib/training-session-analysis.ts",import.meta.url)));
const {default:Panel}=await import(compiledUrl(new URL("../app/TrainingSessionPanel.tsx",import.meta.url)));
const root=fileURLToPath(new URL("../../",import.meta.url));
const python=spawnSync(process.env.PYTHON??"python",["-c",`import json\nfrom tests.test_training_session_analysis import assessment,point\nfrom rallymate_service.training_session_analysis import build_training_session_analysis\nprint(json.dumps([build_training_session_analysis(assessment()),build_training_session_analysis(assessment(),point_events=[point('one',terminal_shot='ace')])]))`],{cwd:root,encoding:"utf8",env:{...process.env,PYTHONPATH:`${root}/src`}});
assert.equal(python.status,0,python.stderr);
const [session,match]=JSON.parse(python.stdout);

test("Python sessions preserve complete source, clips and angle range contracts",()=>{
  assert.deepEqual(trainingSessionAnalysisOf(session),session);
  assert.deepEqual(trainingSessionAnalysisOf(match),match);
  assert.equal(trainingSessionAnalysisOf(session,{source_run_id:"other"}),null);
  assert.equal(trainingSessionAnalysisOf(session,{source_video_sha256:"b".repeat(64)}),null);
  assert.equal(trainingSessionAnalysisOf(session,{subject_id:"other"}),null);
  assert.equal(trainingSessionAnalysisOf(session,{source_duration_ms:2000}),null);
});

test("corrupt clip bindings, durations, rates and range summaries are rejected",()=>{
  for(const mutate of [r=>r.training.active_clips[0].subject_id="other",r=>r.training.active_duration_ms=10000,
    r=>r.training.active_clips[0].end_ms=99999,r=>r.training.unselected_intervals=[],r=>r.match.first_serve_points_won.percent=0,
    r=>r.movement_ranges.metrics.left_knee_flexion_deg.median_episode_p90=99,
    r=>r.movement_ranges.metrics.left_knee_flexion_deg.episodes[0].source_frames=[999],
    r=>r.training_match_comparison.psychological_score=100]){
    const changed=structuredClone(session);mutate(changed);assert.equal(trainingSessionAnalysisOf(changed),null);
  }
});

test("training view shows actual clips and angles with a seek button while hiding absent point stats",()=>{
  const html=renderToStaticMarkup(createElement(Panel,{analysis:session,onSeek:()=>{}}));
  assert.match(html,/已整理 1 个动作片段/);
  assert.match(html,/回看/);
  assert.match(html,/10.0°–90.0°/);
  assert.doesNotMatch(html,/比赛记录|非受迫性失误|心理评分|Ace/);
  assert.equal(renderToStaticMarkup(createElement(Panel,{analysis:session,subjectId:"other"})),"");
});

test("confirmed match rates include denominators and never show unknown hold or break rates",()=>{
  const html=renderToStaticMarkup(createElement(Panel,{analysis:match}));
  assert.match(html,/比赛记录/);assert.match(html,/一发得分率/);assert.match(html,/100.0%/);assert.match(html,/1 \/ 1/);
  assert.doesNotMatch(html,/保发率|破发率|二发得分率/);
});
