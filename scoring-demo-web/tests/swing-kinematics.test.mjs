import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import ts from "typescript";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

const cache = new Map();
function compiledUrl(url) {
  if (url.pathname.endsWith(".json")) return `data:text/javascript;base64,${Buffer.from(`export default ${fs.readFileSync(url, "utf8")}`).toString("base64")}`;
  if (cache.has(url.href)) return cache.get(url.href);
  let js = ts.transpileModule(fs.readFileSync(url, "utf8"), { fileName: url.pathname, compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  js = js.replace(/^import\s+["'][^"']+\.css["'];?\s*$/gm, "");
  js = js.replace(/from "([^"]+)"/g, (_match, name) => {
    const local = new URL(name.endsWith(".json") ? name : `${name}.ts`, url);
    return `from ${JSON.stringify(name.startsWith(".") ? compiledUrl(fs.existsSync(local) ? local : new URL(`${name}.tsx`, url)) : import.meta.resolve(name))}`;
  });
  const result = `data:text/javascript;base64,${Buffer.from(js).toString("base64")}`;
  cache.set(url.href, result); return result;
}
const { SWING_METRICS, swingKinematicsOf, swingMetricText, swingSeekDetail, swingVideoDurationMs, validatedSwingKinematicsReport } = await import(compiledUrl(new URL("../app/lib/swing-kinematics.ts", import.meta.url)));
const { SwingKinematicsView } = await import(compiledUrl(new URL("../app/SwingKinematicsPanel.tsx", import.meta.url)));
const { buildReportBackup } = await import(compiledUrl(new URL("../app/lib/report-export.ts", import.meta.url)));
const { readReportJson } = await import(compiledUrl(new URL("../app/lib/report-file.ts", import.meta.url)));
const hash = "b".repeat(64);
const motionEpisode = () => ({ episode_id: "stroke-one", start_ms: 1000, end_ms: 2000, peak_ms: 1600, person_track_id: 4,
  contact_confirmed: false, family: "baseline", classification: { status: "rule_inferred", label_zh: "挥拍动作" }, metrics: {}, phases: [
    { phase: "preparation", label_zh: "准备", status: "measured", start_ms: 1000, end_ms: 1400 },
    { phase: "acceleration", label_zh: "加速挥拍", status: "measured", start_ms: 1400, end_ms: 1700 },
    { phase: "follow_through", status: "unavailable", start_ms: 1700, end_ms: 2000 },
  ] });
const context = () => ({ jobId: "current-job", videoSha256: hash, durationMs: 8000,
  motion: { schema_version: "1.1.0", status: "available", contact_confirmed: false, families: { baseline: { episodes: [motionEpisode()] } } } });
const metric = unit => ({ value: unit === "deg" ? 34.25 : unit === "deg/s" ? 180.25 : 6.25, unit, timestamp_ms: 1500,
  source_frames: unit === "deg" ? [45] : [44, 45], window_start_ms: 1100, window_end_ms: 1900, status: "estimated" });
const report = () => ({ version: "sam3d-swing-kinematics-v1", status: "available", source_run_id: "current-job", source_video_sha256: hash,
  source_timestamp_semantics: "original_decoder_pts", coordinate_system: "sam3d_pred_keypoints_3d", coordinate_units: "model_units",
  wrist_reference: "midhip_relative_fixed_median_torso_per_continuous_window", camera_reference: "same_camera_epoch_uncalibrated_monocular_estimate",
  camera_rotation_compensated: false, cross_video_absolute_speed_comparable: false,
  measurement_semantics: "monocular_3d_model_estimate", score: null, reason_zh: "连续三维测量", limitations_zh: [], episodes: [{
    episode_id: "stroke-one", start_ms: 1000, end_ms: 2000, person_track_id: 4, racket_hand: "right", frame_count: 31, valid_frame_count: 30, coverage: 30 / 31,
    reason_zh: "连续三维测量", quality_reason_counts: {}, metrics: Object.fromEntries(SWING_METRICS.map(spec => [spec.key, metric(spec.unit)])),
  }] });
const parse = value => swingKinematicsOf(value, context());
const render = (view, props = {}) => renderToStaticMarkup(createElement(SwingKinematicsView, { view, jobId: "current-job", selectedId: "", onSelect() {}, ...props }));

test("only current job, source video, original timestamps and model measurement semantics are accepted", () => {
  const before = report(), input = structuredClone(before), parsed = parse(input);
  assert.equal(parsed.status, "available"); assert.equal(parsed.episodes.length, 1); assert.deepEqual(input, before);
  for (const change of [
    { source_run_id: "previous-job" }, { source_video_sha256: "a".repeat(64) }, { source_video_sha256: "" },
    { source_timestamp_semantics: "frame_index_fps" }, { coordinate_system: "image_pixels" }, { coordinate_units: "meters" },
    { wrist_reference: "camera_world" }, { camera_reference: "unknown" }, { measurement_semantics: "true_force" },
    { camera_rotation_compensated: true }, { camera_rotation_compensated: undefined }, { cross_video_absolute_speed_comparable: true },
    { version: "other-version" }, { score: 99 }, { score: 0 },
  ]) assert.deepEqual(parse({ ...report(), ...change }), { status: "rejected", episodes: [] }, JSON.stringify(change));
  assert.equal(swingKinematicsOf(report(), { ...context(), videoSha256: "" }).status, "rejected");
  assert.equal(swingKinematicsOf(report(), { ...context(), motion: null }).status, "rejected");
  assert.equal(swingKinematicsOf(report(), { ...context(), videoSha256: hash.toUpperCase() }).status, "available");
});

test("episode ID, exact boundaries, track and video duration cannot be reassigned", () => {
  for (const change of [{ episode_id: "fake" }, { person_track_id: 5 }, { start_ms: 900 }, { end_ms: 2001 }, { start_ms: NaN },
    { end_ms: Infinity }, { frame_count: -1 }, { valid_frame_count: 32 }, { coverage: 0.5 }]) {
    const input = report(); Object.assign(input.episodes[0], change); assert.deepEqual(parse(input).episodes, [], JSON.stringify(change));
  }
  assert.deepEqual(swingKinematicsOf(report(), { ...context(), durationMs: 1900 }).episodes, []);
  for (const durationMs of [0, -1, Infinity, NaN]) assert.equal(swingKinematicsOf(report(), { ...context(), durationMs }).status, "rejected");
  const duplicate = report(); duplicate.episodes.push(structuredClone(duplicate.episodes[0])); assert.deepEqual(parse(duplicate).episodes, []);
  const ambiguous = context(); ambiguous.motion.families.baseline.episodes.push(motionEpisode());
  assert.deepEqual(swingKinematicsOf(report(), ambiguous).episodes, []);
});

test("invalid values, unit substitutions and peaks outside their measured window stay null", () => {
  const key = "hip_angular_speed_deg_s";
  for (const patch of [{ value: null }, { value: "180" }, { value: Infinity }, { value: NaN }, { value: -1 }, { unit: "torso/s" },
    { status: "unavailable", value: 150 }, { timestamp_ms: 1000 }, { timestamp_ms: 1901 }, { timestamp_ms: null },
    { window_start_ms: 999 }, { window_end_ms: 2001 }, { window_end_ms: 1100 }, { source_frames: [] },
    { source_frames: [45, 45] }, { source_frames: [45, 44] }, { source_frames: [44, 44.5] }, { source_frames: [44] }]) {
    const input = report(); Object.assign(input.episodes[0].metrics[key], patch);
    const parsed = parse(input); assert.equal(parsed.status, "partial"); assert.equal(parsed.episodes[0].metrics[key].value, null, JSON.stringify(patch));
    assert.equal(parsed.episodes[0].metrics[key].timestamp_ms, null); assert.equal(swingMetricText(parsed.episodes[0].metrics[key]), "—");
    assert.equal(parsed.episodes[0].metrics.shoulder_hip_separation_deg.value, 34.25);
  }
  const angle = report(); angle.episodes[0].metrics.shoulder_hip_separation_deg.value = 181;
  assert.equal(parse(angle).episodes[0].metrics.shoulder_hip_separation_deg.value, null);
});

test("a measured zero is retained but missing data never becomes zero or another metric", () => {
  const input = report(); input.episodes[0].metrics.hip_angular_speed_deg_s.value = 0;
  input.episodes[0].metrics.shoulder_angular_speed_deg_s = null;
  const episode = parse(input).episodes[0];
  assert.equal(swingMetricText(episode.metrics.hip_angular_speed_deg_s), "0");
  assert.equal(swingMetricText(episode.metrics.shoulder_angular_speed_deg_s), "—");
  input.episodes[0].racket_hand = null;
  assert.equal(parse(input).episodes[0].metrics.wrist_speed_torso_s.value, null);
});

test("missing 3D episodes remain selectable with their reason and real preparation/swing boundaries", () => {
  const input = report(); input.status = "unavailable"; input.episodes[0].valid_frame_count = 0; input.episodes[0].coverage = 0;
  input.episodes[0].reason_zh = "这一段未能持续锁定同一运动员。";
  const parsed = parse(input); assert.equal(parsed.status, "unavailable"); assert.equal(parsed.episodes.length, 1);
  assert.equal(parsed.episodes[0].reason_zh, input.episodes[0].reason_zh);
  assert.ok(Object.values(parsed.episodes[0].metrics).every(item => item.value === null && item.timestamp_ms === null));
  assert.deepEqual(parsed.episodes[0].phases.map(row => [row.label, row.startMs, row.endMs]), [["准备", 1000, 1400], ["加速挥拍", 1400, 1700]]);
  const html = render(parsed); assert.equal((html.match(/<strong>—<\/strong>/g) ?? []).length, 4);
  assert.match(html, /这一段未能持续锁定同一运动员/); assert.match(html, /选择转体与挥拍片段/);
  assert.doesNotMatch(html, /回看峰值|<strong>0<\/strong>|score=|技术优秀/);
});

test("out-of-episode and unavailable phases do not create preparation or contact anchors", () => {
  const bound = context(); bound.motion.families.baseline.episodes[0].phases.push(
    { phase: "recovery", status: "measured", start_ms: 1900, end_ms: 2200 },
    { phase: "contact", status: "measured", start_ms: 1500, end_ms: 1600 },
    { phase: "loading", status: "measured", start_ms: 1000, end_ms: Infinity },
  );
  assert.deepEqual(swingKinematicsOf(report(), bound).episodes[0].phases.map(row => row.id), ["preparation", "acceleration"]);
});

test("peak navigation carries current job and original source milliseconds without selecting another media", () => {
  const episode = parse(report()).episodes[0];
  assert.deepEqual(swingSeekDetail("current-job", episode, 1500), { jobId: "current-job", timestampMs: 1500 });
  for (const timestamp of [null, undefined, -1, "1500", Infinity, 999, 2001]) assert.equal(swingSeekDetail("current-job", episode, timestamp), null);
  assert.equal(swingSeekDetail("", episode, 1500), null);
  const source = fs.readFileSync(new URL("../app/SwingKinematicsPanel.tsx", import.meta.url), "utf8");
  assert.match(source, /rallymate:seek-video/); assert.doesNotMatch(source, /preferOriginal|setPreferredKind/);
  assert.match(source, /detail\?\.jobId !== context\.jobId/);
});

test("view shows four dimensioned maxima and one model qualification without a score", () => {
  const html = render(parse(report()));
  for (const text of ["转体与挥拍", "肩髋分离角峰值", "髋轴变化峰速", "肩轴变化峰速", "相对躯干腕速峰值", "34.25", "180.25", "6.25", "°/秒", "躯干长度/秒", "准备", "加速挥拍", "1.50 秒"]) assert.ok(html.includes(text), text);
  assert.equal((html.match(/单目三维模型估计/g) ?? []).length, 1);
  assert.doesNotMatch(html, /100分|总分|职业分|技战术评分|Word|原文|候选事件|人体真实受力/);
  assert.match(render({ status: "rejected", episodes: [] }), /未能对应/);
  assert.match(render({ status: "unavailable", episodes: [] }, { failed: true, onRetry() {} }), /重新读取/);
});

test("canonical backup round trip preserves validated measurements and never carries scores or display data", () => {
  const input = report(); input.extra_unsafe_field = "private"; input.episodes[0].metrics.hip_angular_speed_deg_s.timestamp_ms = 2100;
  const saved = validatedSwingKinematicsReport(input, context());
  assert.equal(saved.score, null); assert.equal(saved.extra_unsafe_field, undefined); assert.equal(saved.episodes[0].label, undefined);
  assert.equal(saved.episodes[0].metrics.hip_angular_speed_deg_s.value, null);
  assert.deepEqual(swingKinematicsOf(saved, context()), swingKinematicsOf(input, context()));
  assert.equal(validatedSwingKinematicsReport({ ...report(), score: 100 }, context()), null);
  assert.equal(validatedSwingKinematicsReport(report(), { ...context(), jobId: "other-job" }), null);
});

test("duration is read only as a finite original-video duration, not coerced from text", () => {
  assert.equal(swingVideoDurationMs({ input: { video: { duration_ms: 20000 } } }), 20000);
  for (const value of [null, undefined, "20000", -1, Infinity, 0]) assert.equal(swingVideoDurationMs({ input: { video: { duration_ms: value } } }), null);
});

test("report backup uses independent source metadata, preserves SHA and round trips only validated 3D values", async () => {
  const input = { mode: "live", uploadState: "complete", evidence: { jobId: "current-job", result: {
    job_id: "current-job", action_recognition: { motion_analysis: context().motion }, input: { video: { duration_ms: 8000, sha256: hash } },
    swing_kinematics: report(),
  } } };
  const backup = buildReportBackup(input), serialized = JSON.stringify(backup);
  const imported = await readReportJson({ size: Buffer.byteLength(serialized), text: async () => serialized });
  assert.equal(imported.result.input.video.sha256, hash);
  assert.equal(imported.result.swing_kinematics.episodes[0].metrics.wrist_speed_torso_s.value, 6.25);
  assert.equal(parse(imported.result.swing_kinematics).status, "available");
  assert.equal(imported.result.swing_kinematics.score, null);
  delete input.evidence.result.input.video.sha256;
  assert.equal(buildReportBackup(input).result.swing_kinematics, undefined);
  input.evidence.result.original_video = { sha256: hash };
  assert.equal(buildReportBackup(input).result.swing_kinematics.episodes.length, 1);
  input.evidence.result.input.video.sha256 = "a".repeat(64);
  const conflicting = buildReportBackup(input);
  assert.equal(conflicting.result.swing_kinematics, undefined); assert.equal(conflicting.result.input.video.sha256, undefined);
  input.evidence.result.input.video.sha256 = hash; input.evidence.result.swing_kinematics.source_run_id = "other-job";
  assert.equal(buildReportBackup(input).result.swing_kinematics, undefined);
});
