import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import ts from "typescript";

const source = fs.readFileSync(new URL("../app/lib/motion-analysis.ts", import.meta.url), "utf8");
const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const { motionAnalysisOf, motionMetricText, rotationAnalysisOf, ROTATION_METRICS } = await import(`data:text/javascript;base64,${Buffer.from(js).toString("base64")}`);
const episode = { episode_id: "a", family: "serve", start_ms: 100, peak_ms: 250, end_ms: 400, contact_confirmed: false, classification: { label_zh: "发球动作", status: "rule_inferred" }, metrics: {}, phases: [{ phase: "preparation", label_zh: "准备", start_ms: 100, end_ms: 250 }, { phase: "acceleration", label_zh: "加速", start_ms: -100, end_ms: 400 }, null], limitations_zh: [] };
const wrap = episodes => ({ action_recognition: { motion_analysis: { schema_version: "1.0.0", contact_confirmed: false, families: { serve: { episodes } } } } });

test("motion parser retains measured stages while rejecting mixed families, duplicate and invalid intervals", () => {
  const view = motionAnalysisOf(wrap([episode, episode, { ...episode, episode_id: "b", peak_ms: 800 }, { ...episode, episode_id: "c", family: "baseline" }, { ...episode, episode_id: "d", contact_confirmed: true }]));
  assert.equal(view.families.serve.episodes.length, 1);
  assert.equal(view.families.serve.episodes[0].phases.length, 1);
  assert.equal(view.contact_confirmed, false);
  assert.equal(view.status, "available");
});

test("motion parser supports assessment fallback but never invents a motion analysis from candidates", () => {
  assert.equal(motionAnalysisOf(null, wrap([episode])).families.serve.episodes.length, 1);
  assert.equal(motionAnalysisOf({ action_recognition: { candidates: [episode] } }), null);
  for (const invalid of [null, NaN, Infinity, "1", -1]) assert.equal(motionMetricText(invalid), "—");
  assert.equal(motionMetricText(0), "0.00");
});

test("partial phases preserve missing evidence instead of inventing zero-time stage boundaries", () => {
  const partial = { ...episode, analysis_status: "partial", phase_timing_status: "estimated_from_2d_motion", metric_notes_zh: ["肩线投影缩短，未输出肩线变化。", null], phases: [{ phase: "acceleration", label_zh: "加速", start_ms: 150, end_ms: 250, status: "measured" }, { phase: "follow_through", label_zh: "随挥", start_ms: null, end_ms: null, status: "unavailable" }] };
  const parsed = motionAnalysisOf(wrap([partial])).families.serve.episodes[0];
  assert.equal(parsed.analysis_status, "partial");
  assert.equal(parsed.phases.length, 2);
  assert.equal(parsed.phases[1].start_ms, null);
  assert.equal(parsed.phases[1].status, "unavailable");
  assert.deepEqual(parsed.metric_notes_zh, ["肩线投影缩短，未输出肩线变化。"]);
});

function measuredRotationEpisode() {
  const row = structuredClone(episode);
  row.metrics = Object.fromEntries(ROTATION_METRICS.map(({ key }, i) => [key, i * 12]));
  row.rotation_analysis = {
    schema_version: "1.0.0", status: "measured_2d", method: "undirected_image_plane_body_axes",
    is_3d_rotation: false, is_formal_coach_score: false, score: null, score_status: "calibration_required",
    metrics: { ...row.metrics }, limitations_zh: ["二维投影不代表三维转体或技术评分。"],
    metric_evidence: Object.fromEntries(ROTATION_METRICS.map(({ key }) => [key, {
      status: "measured", valid_samples: 10, total_samples: 10, continuous_samples: 10,
      coverage_fraction: 1, time_coverage_fraction: 1, segment_count: 1,
      start_ms: row.start_ms, end_ms: row.end_ms, reason_zh: "连续二维轴观测可复核。",
    }])),
  };
  return row;
}

const parseRotationEpisode = row => motionAnalysisOf(wrap([row])).families.serve.episodes[0];
function deepFreeze(value) {
  if (!value || typeof value !== "object" || Object.isFrozen(value)) return value;
  Object.freeze(value);
  for (const item of Object.values(value)) deepFreeze(item);
  return value;
}

test("rotation parser keeps measured zero, six image-plane metrics and the null technical score", () => {
  const source = measuredRotationEpisode();
  const parsed = parseRotationEpisode(source);
  assert.equal(parsed.rotation_analysis.status, "measured_2d");
  assert.equal(parsed.rotation_analysis.score, null);
  assert.equal(parsed.rotation_analysis.score_status, "calibration_required");
  assert.equal(parsed.rotation_analysis.is_3d_rotation, false);
  assert.equal(parsed.rotation_analysis.is_formal_coach_score, false);
  assert.deepEqual(parsed.metrics, source.metrics);
  assert.equal(parsed.metrics.shoulder_line_change_deg, 0);
  for (const { key } of ROTATION_METRICS) {
    assert.equal(parsed.rotation_analysis.metric_evidence[key].status, "measured");
    assert.equal(parsed.rotation_analysis.metric_evidence[key].start_ms, 100);
    assert.equal(parsed.rotation_analysis.metric_evidence[key].end_ms, 400);
  }
});

test("rotation parser derives partial and unavailable states from evidence rather than filling zero scores", () => {
  const partial = measuredRotationEpisode();
  partial.metrics.hip_line_change_deg = null;
  const parsed = parseRotationEpisode(partial);
  assert.equal(parsed.rotation_analysis.status, "partial");
  assert.equal(parsed.rotation_analysis.metric_evidence.hip_line_change_deg.status, "unavailable");
  assert.equal(parsed.metrics.hip_line_change_deg, null);
  assert.equal(parsed.metrics.peak_shoulder_angular_speed_deg_s, partial.metrics.peak_shoulder_angular_speed_deg_s);
  const unavailable = measuredRotationEpisode();
  unavailable.rotation_analysis.status = "unavailable";
  const blocked = parseRotationEpisode(unavailable);
  assert.equal(blocked.rotation_analysis.status, "unavailable");
  assert.equal(blocked.rotation_analysis.score, null);
  assert.equal(blocked.rotation_analysis.score_status, "insufficient_evidence");
  for (const { key } of ROTATION_METRICS) assert.equal(blocked.metrics[key], null);
});

test("rotation measurements require numeric coverage and a valid continuous replay interval", () => {
  const invalid = [
    { coverage_fraction: 0.79 }, { coverage_fraction: 1.1 }, { coverage_fraction: "0.9" },
    { coverage_fraction: NaN }, { coverage_fraction: Infinity }, { coverage_fraction: null },
    { start_ms: 99 }, { end_ms: 401 }, { start_ms: 250, end_ms: 250 },
    { start_ms: "100" }, { end_ms: null }, { start_ms: 100, end_ms: 101 },
    { time_coverage_fraction: 0.79 }, { continuous_samples: 6 },
    { continuous_samples: 11, total_samples: 10 },
  ];
  for (const patch of invalid) {
    const row = measuredRotationEpisode();
    Object.assign(row.rotation_analysis.metric_evidence.hip_line_change_deg, patch);
    const parsed = parseRotationEpisode(row);
    assert.equal(parsed.metrics.hip_line_change_deg, null, JSON.stringify(patch));
    assert.equal(parsed.rotation_analysis.metric_evidence.hip_line_change_deg.status, "unavailable");
    assert.equal(parsed.rotation_analysis.metric_evidence.hip_line_change_deg.start_ms, null);
    assert.equal(parsed.rotation_analysis.metric_evidence.hip_line_change_deg.end_ms, null);
  }
});

test("rotation metric values reject missing nonfinite negative and coercible values", () => {
  for (const value of [null, undefined, NaN, Infinity, -1, "35"]) {
    const row = measuredRotationEpisode();
    row.metrics.hip_line_change_deg = value;
    const parsed = parseRotationEpisode(row);
    assert.equal(parsed.metrics.hip_line_change_deg, null);
    assert.equal(parsed.rotation_analysis.metric_evidence.hip_line_change_deg.status, "unavailable");
  }
});

test("explicit invalid rotation claims cannot fall back to ungated legacy shoulder measurements", () => {
  for (const patch of [{ score: 88 }, { is_3d_rotation: true }, { is_formal_coach_score: true }, { status: "scored" }]) {
    const row = measuredRotationEpisode();
    row.metrics.shoulder_line_change_deg = 88;
    Object.assign(row.rotation_analysis, patch);
    const parsed = parseRotationEpisode(row);
    for (const { key } of ROTATION_METRICS) assert.equal(parsed.metrics[key], null, JSON.stringify(patch));
  }
  for (const invalid of [null, false, "", {}]) {
    const row = measuredRotationEpisode();
    row.rotation_analysis = invalid;
    const parsed = parseRotationEpisode(row);
    for (const { key } of ROTATION_METRICS) assert.equal(parsed.metrics[key], null, JSON.stringify(invalid));
  }
  const legacy = { ...structuredClone(episode), metrics: { shoulder_line_change_deg: 12 } };
  assert.equal(parseRotationEpisode(legacy).metrics.shoulder_line_change_deg, 12);
});

test("both parser entry points leave caller-owned episode and evidence objects unchanged", () => {
  const row = measuredRotationEpisode();
  row.rotation_analysis.metric_evidence.hip_line_change_deg.coverage_fraction = 0.1;
  const before = structuredClone(row);
  deepFreeze(row);
  const parsed = parseRotationEpisode(row);
  assert.equal(parsed.metrics.hip_line_change_deg, null);
  assert.deepEqual(row, before);
  assert.doesNotThrow(() => rotationAnalysisOf(row.rotation_analysis, row));
  assert.deepEqual(row, before);
});
