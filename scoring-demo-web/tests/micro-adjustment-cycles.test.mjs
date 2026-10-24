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
const { cardExecutionOf } = await import(compiledUrl(new URL("../app/lib/card-execution.ts", import.meta.url)));
const { validateMicroCycles, recomputeMicroEpisodes } = await import(compiledUrl(new URL("../app/lib/micro-adjustment-cycles-evidence.ts", import.meta.url)));
const { default: MicroEvidence } = await import(compiledUrl(new URL("../app/MicroAdjustmentCyclesEvidence.tsx", import.meta.url)));
const compressed = fs.readFileSync(new URL("./fixtures/micro-cycle-native-e420-unified.json.gz", import.meta.url));
const bytes = gunzipSync(compressed), full = JSON.parse(bytes);
const provenance = JSON.parse(fs.readFileSync(new URL("./fixtures/micro-cycle-native-e420-unified.provenance.json", import.meta.url), "utf8"));
const mathematics = JSON.parse(gunzipSync(fs.readFileSync(new URL("./fixtures/micro-cycle-mathematics.json.gz", import.meta.url))));
const instance = (r, id = "FS05-M02") => r.cards.find(c => c.indicator_id === id).instances[0];
const evidence = i => i.source_window_evidence.micro_adjustment_cycles;
const direct = i => validateMicroCycles(i.source_window_annotation, i.source_window_evidence, i.source_window_context ?? i.window, i);
const justM02 = () => structuredClone({ ...full, cards: full.cards.filter(c => c.indicator_id === "FS05-M02") });
function reject(change) {
  const r = justM02(), i = instance(r); change(i, r);
  const v = instance(cardExecutionOf(r)); assert.equal(v.score, null); assert.equal(v.grade, null); assert.equal(v.proxy_grade, null);
  assert.equal(v.status, "unavailable"); return v;
}

test("actual native e420 current/legacy HTTP bytes keep 298 rows, 188 instances and the independent card mean", () => {
  assert.equal(createHash("sha256").update(bytes).digest("hex"), provenance.response_sha256);
  assert.equal(createHash("sha256").update(compressed).digest("hex"), provenance.gzip_sha256);
  assert.ok(Object.values(provenance.matching_response_files).every(s => s === provenance.response_sha256));
  assert.equal(provenance.actual_http_response_equal, true);
  assert.equal(full.source_run_id, "e4204ec3-7d84-4a90-a961-9dad01b43c2e");
  const p = cardExecutionOf(full);
  assert.equal(p.cards.length, 298); assert.equal(p.summary.formal_scored_cards, 8);
  assert.equal(p.summary.formal_scored_instances, 114); assert.equal(p.summary.unavailable_instances, 74);
  assert.equal(p.summary.rejected_cards, 0); assert.ok(Math.abs(p.overall_score - full.overall_score) < 1e-12);
  assert.deepEqual(cardExecutionOf(p), p);
  assert.deepEqual(p.micro_adjustment_cycle_execution, full.input.micro_adjustment_cycle_execution);
  assert.deepEqual(p.micro_adjustment_cycle_windows, full.micro_adjustment_cycle_windows);
  for (const id of ["FS05-M02", "FS05-M03", "FS05-M04"]) { assert.equal(instance(p, id).grade, "C"); assert.equal(instance(p, id).score, 60); }
});

test("fixed 112–136 real observation counts one complete episode and retains the partial ankle and all path", () => {
  const i = instance(cardExecutionOf(justM02())), v = i.micro_cycle_validation, raw = evidence(instance(full));
  assert.equal(v.status, "measured_visual_adjustment_episodes");
  assert.equal(v.total_frame_count, 25); assert.equal(v.values.complete_visual_adjustment_episode_count, 1);
  assert.ok(Math.abs(v.values.both_ankles_max_path_length_torso - .30026233094030963) < 1e-12);
  assert.equal(v.auxiliary.complete_visual_ankle_cycle_count, 1); assert.equal(v.auxiliary.incomplete_ankle_motion_run_count, 1);
  assert.ok(Math.abs(v.auxiliary.observed_complete_episode_frequency_hz - 1.25) < 1e-12);
  assert.deepEqual(v.cycles.map(c => [c.cycle_id,c.source_frames,c.complete_visual_motion_cycle,c.reason_codes]), raw.cycles.map(c => [c.cycle_id,c.source_frames,c.complete_visual_motion_cycle,c.reason_codes]));
  assert.deepEqual(v.episodes, raw.episodes);
  assert.ok(v.cycles.find(c => c.moving_ankle === "left").reason_codes.includes("preceding_low_motion_censored_at_window_start"));
  assert.deepEqual(i.conditions.map(c => c.passed), [false, true]);
  for (const row of raw.source_geometry_series) { assert.equal(row.source_binding.selection_epoch, 0); assert.equal(row.selection_epoch, 1); }
  assert.equal(i.original_grade, null); assert.equal(i.source_grade_supported, false);
});

test("explicitly mathematical fixtures distinguish sequential, same-foot, simultaneous and zero-motion counts", () => {
  assert.match(mathematics.semantics, /Synthetic/);
  for (const [mode, count] of [["sequential", 2], ["same_foot", 2], ["simultaneous", 1], ["static", 0]]) {
    const i = mathematics.cases[mode], v = direct(i);
    assert.equal(v.status, "measured_visual_adjustment_episodes", `${mode}: ${v.reason_codes}`);
    assert.equal(v.values.complete_visual_adjustment_episode_count, count);
    assert.deepEqual(v.cycles, evidence(i).cycles); assert.deepEqual(v.episodes, evidence(i).episodes);
  }
  assert.equal(mathematics.cases.static.score, 20); assert.equal(direct(mathematics.cases.real_record_only).status, "measured_visual_adjustment_episodes");
});

test("positive-overlap components are transitive including partial bridges; shared endpoints and partial-only are separate", () => {
  const c = (id, a, b, complete = true) => ({ cycle_id: id, motion_start_source_frame: a, motion_end_source_frame: b, complete_visual_motion_cycle: complete });
  const input = [c("a", 10, 20), c("bridge", 18, 24, false), c("b", 22, 30), c("touch", 30, 40), c("partial", 41, 48, false)];
  const e = recomputeMicroEpisodes(input.reverse()); assert.equal(e.length, 3);
  assert.deepEqual(e[0].cycle_ids, ["a", "bridge", "b"]); assert.deepEqual(e[0].complete_cycle_ids, ["a", "b"]);
  assert.deepEqual(e[0].incomplete_cycle_ids, ["bridge"]); assert.equal(e[2].complete_visual_adjustment_episode, false);
});

test("saved counts, flags, truncated paths, missing cycles, changed phases and auxiliary frequency cannot authorize scoring", () => {
  for (const change of [
    i => evidence(i).measurements.complete_visual_adjustment_episode_count++,
    i => evidence(i).measurements.both_ankles_max_path_length_torso = .01,
    i => evidence(i).episodes[0].complete_visual_adjustment_episode = !evidence(i).episodes[0].complete_visual_adjustment_episode,
    i => evidence(i).cycles[0].complete_visual_motion_cycle = true,
    i => evidence(i).cycles.shift(), i => evidence(i).feet.left.intervals.pop(),
    i => evidence(i).feet.right.phases[0].enough_duration = false,
    i => evidence(i).cycles[1].local_motion_evidence.endpoint_count = 1,
    i => evidence(i).auxiliary.observed_complete_episode_frequency_hz++,
    i => evidence(i).source_torso_scales[4]++, i => evidence(i).fixed_torso_scale++,
    i => evidence(i).duration_s++, i => evidence(i).episode_policy = "shared_endpoint_counts_as_overlap",
    i => evidence(i).low_speed_threshold_torso_s = .1,
    i => {i.conditions[0].value = 2; i.conditions[0].passed = true; i.grade = i.proxy_grade = "A"; i.score = 100;},
    i => i.conditions.push({...i.conditions[0],key:"frequency",metric:"observed_complete_episode_frequency_hz"}),
    i => i.conditions[0].threshold = 1,
  ]) reject(change);
});

test("geometry, normalization, confidence, model dual epochs and processed continuity fail closed", () => {
  for (const change of [
    i => delete evidence(i).local_normalization,
    i => evidence(i).local_normalization[0].source_geometry_verified = false,
    i => evidence(i).local_normalization[0].input_scale++,
    i => evidence(i).source_geometry_series[0].normalized_points.left_ankle[0]++,
    i => evidence(i).reference_series[0].points.left_ankle[0]++,
    i => {evidence(i).source_geometry_series[0].point_confidences.left_ankle = .1; evidence(i).local_normalization[0].input_geometry.point_confidences.left_ankle = .1;},
    i => {for(const a of [evidence(i).source_geometry_series[0],evidence(i).local_normalization[0].input_geometry]) {delete a.source_binding; delete a.model_epoch_semantics;}},
    i => evidence(i).source_geometry_series[0].source_binding.person_track_id++,
    i => evidence(i).source_geometry_series[0].source_binding.pts++,
    i => evidence(i).source_geometry_series[0].source_binding.selection_epoch++,
    i => {for(const a of [evidence(i).source_geometry_series[0],evidence(i).local_normalization[0].input_geometry]) a.source_binding.processed_index += 7;},
    i => {for(const a of [evidence(i).source_geometry_series[5],evidence(i).local_normalization[5].input_geometry]) a.source_binding.processed_index += 7;},
    i => evidence(i).source_geometry_series[4].selection_epoch++,
    i => evidence(i).source_geometry_series.pop(), i => evidence(i).valid_source_frames.pop(),
    i => {for(const a of evidence(i).source_geometry_series) delete a.model_epoch_semantics;},
  ]) reject(change);
});

test("complete original denominator, selected person, camera, PTS, full derived ledger and quality are mandatory", () => {
  for (const change of [
    i => i.source_window_evidence.source_frame_bindings[5].selected_person_track_id++,
    i => i.source_window_evidence.source_frame_bindings[5].selection_epoch++,
    i => i.source_window_evidence.source_frame_bindings[5].camera_reference_epoch++,
    i => i.source_window_evidence.source_frame_bindings[5].pts++,
    i => {for(const row of i.source_window_evidence.source_frame_bindings) delete row.derived_selection_epoch; for(const row of evidence(i).source_frame_bindings) delete row.derived_selection_epoch; delete i.window.derived_selection_epoch;},
    i => i.source_window_evidence.original_event_quality = [],
    i => { const q=i.source_window_evidence.original_event_quality[0]; q.confirmed_id_switch_count=1; q.source_track_diagnostics.confirmed_id_switch_count=1; },
    i => i.source_window_evidence.source_frames.pop(),
    i => i.source_window_annotation.start_source_frame++,
    i => i.source_window_annotation.document_version = "fs-card-annotations-v1.0.0",
    i => i.source_window_evidence.step_cycle_bound = true,
    i => evidence(i).contact_verified = true,
  ]) reject(change);
});

test("unified source copies, annotation hash, artifact checks and policy cannot be detached", () => {
  for (const change of [
    (_i,r) => r.micro_adjustment_cycle_windows=[],
    (_i,r) => r.micro_adjustment_cycle_windows.push(structuredClone(r.micro_adjustment_cycle_windows[0])),
    (_i,r) => r.micro_adjustment_cycle_windows[0].window.selection_epoch++,
    (_i,r) => r.micro_adjustment_cycle_windows[0].binding.uncertainty_ms++,
    (_i,r) => delete r.input.micro_adjustment_cycle_execution,
    (_i,r) => r.input.micro_adjustment_cycle_execution.annotation_document_sha256="a".repeat(64),
    (_i,r) => r.input.micro_adjustment_cycle_execution.artifact_files_verified=false,
    (_i,r) => r.input.micro_adjustment_cycle_execution.decoder_verified_from_original_video=false,
    (_i,r) => r.input.micro_adjustment_cycle_execution.replaced_card_ids=[],
    (_i,r) => r.input.micro_adjustment_cycle_execution.status="unavailable",
    (_i,r) => r.input.micro_adjustment_cycle_execution.service_eligible=false,
    (_i,r) => delete r.input.micro_adjustment_cycle_execution.input_artifact_sha256["events.jsonl"],
    (_i,r) => r.input.micro_adjustment_cycle_execution.decoder_frame_count=2,
    (_i,r) => {r.micro_adjustment_cycle_execution=structuredClone(r.input.micro_adjustment_cycle_execution); r.input.micro_adjustment_cycle_execution.annotation_document_sha256="a".repeat(64);},
    (i,r) => { delete i.scoring_policy_version; delete r.scoring_policy_version; },
  ]) reject(change);
});

test("UI distinguishes failed count, partial motion and path; no review requirement or contact cadence claim", () => {
  const i=instance(cardExecutionOf(justM02()));
  const html=renderToStaticMarkup(createElement(MicroEvidence,{instance:i}));
  assert.match(html,/完整视觉运动片段 1 个/); assert.match(html,/截断或不完整运动/); assert.match(html,/不是真实触地步频/);
  assert.match(html,/左脚全窗路径/); assert.match(html,/右脚全窗路径/); assert.doesNotMatch(html,/复核/);
});

test("portable export preserves complete M02 sources, phases and episodes and is idempotent", async () => {
  const {buildReportBackup,buildPracticeReport}=await import(compiledUrl(new URL("../app/lib/report-export.ts",import.meta.url)));
  const input={mode:"live",uploadState:"complete",job:{id:full.source_run_id,status:"completed",original_filename:"native-e420.mp4"},
    evidence:{jobId:full.source_run_id,error:null,result:{job_id:full.source_run_id,card_execution:full}},summary:null};
  const expected=cardExecutionOf(full),backup=JSON.parse(JSON.stringify(buildReportBackup(input,"2026-10-24T00:00:00Z")));
  assert.deepEqual(backup.result.card_execution,expected); assert.deepEqual(cardExecutionOf(backup.result.card_execution),expected);
  const report=buildPracticeReport(input,"2026-10-24T00:00:00Z"), section=report.sections.find(s=>s.title==="逐卡视频实例与条件");
  const row=section.rows.find(r=>r[0].startsWith("FS05-M02")); assert.match(row[2],/C \/ 60/);
  assert.match(row.at(-1),/完整视觉运动片段 1 个/); assert.match(row.at(-1),/双脚全窗路径最大值/);
});
