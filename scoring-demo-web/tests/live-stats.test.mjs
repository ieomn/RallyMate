import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import ts from "typescript";

const source = fs.readFileSync(new URL("../app/lib/live-stats.ts", import.meta.url), "utf8");
const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const { summarizeLiveStats, buildTrajectoryDisplayModel, analysisPresentation, summarizeActionCandidates } = await import(`data:text/javascript;base64,${Buffer.from(js).toString("base64")}`);

const trajectory = {
  source: { frame_count: 2911, coordinate_space: "normalized_frame_0_1" },
  ball: { observed: [{ x: .2, y: .3 }], observed_count: 251, coverage_fraction: .0862, confidence: { mean: .601 }, prediction_status: "heuristic_preview", predicted: [{ x: .3, y: .4 }], velocity: { direction_image_deg: -81.6, segment_count: 5 } },
  racket: { observed: [], observed_count: 74 },
};

test("trajectory display keeps observed points separate from heuristic extrapolation", () => {
  const model = buildTrajectoryDisplayModel(trajectory);
  assert.equal(model.observedCount, 251);
  assert.equal(model.predictedCount, 1);
  assert.equal(model.predictionLabel, "启发式外推");
  assert.match(model.predictionNote, /不代表真实落点/);
});

test("generic GS counts and nested hit fields never become confirmed contact totals", () => {
  const result = { hit_statistics: { status: "observed", total_count: 300, count_semantics: "upstream_GS_event_count_only; null_when_not_emitted" }, features: { shot_count: 200, contact_count: 100 } };
  assert.equal(summarizeLiveStats(result, trajectory, { hit_count: 99 }).contactCount, null);
  assert.equal(summarizeLiveStats({ hit_statistics: { status: "unsupported", total_count: 0 } }, trajectory).shotCount, null);
  assert.equal(summarizeLiveStats(null, trajectory).actionSegments, null);
});

test("only confirmed contact contract accepts nonnegative integer counts", () => {
  const hit_statistics = { status: "observed", count_semantics: "confirmed_contact_events", total_count: 7 };
  assert.equal(summarizeLiveStats({ hit_statistics }, trajectory).contactCount, 7);
  assert.equal(summarizeLiveStats({ hit_statistics: { ...hit_statistics, total_count: 0 } }, trajectory).contactCount, 0);
  for (const total_count of [-1, 1.5, Infinity, NaN, "7"]) assert.equal(summarizeLiveStats({ hit_statistics: { ...hit_statistics, total_count } }, trajectory).contactCount, null);
});

test("action candidates preserve time intervals without turning swings into contacts", () => {
  const candidate = { candidate_id: "serve-1", status: "candidate", family: "serve", start_ms: 5066, peak_ms: 5632, end_ms: 6266, contact_confirmed: false };
  const result = { action_recognition: { status: "candidates_detected", candidates: [candidate, { ...candidate, candidate_id: "invalid", peak_ms: 7000 }] } };
  const view = summarizeActionCandidates(result);
  assert.equal(view.candidates.length, 1);
  assert.equal(view.candidates[0].label, "发球动作候选");
  assert.equal(view.candidates[0].startMs, 5066);
  assert.equal(view.candidates[0].endMs, 6266);
  assert.equal(summarizeLiveStats(result, trajectory).contactCount, null);
});

test("terminal analysis states never retain pending recognition messages", () => {
  for (const status of ["failed", "cancelled", "unsupported", "succeeded"]) {
    const state = analysisPresentation({ result: null, pending: true, status, error: status === "failed" ? "worker stopped" : null });
    assert.notEqual(state.state, "pending");
    assert.doesNotMatch(state.emptyTitle, /等待|进行中/);
  }
  assert.equal(analysisPresentation({ result: null, pending: true, status: "queued" }).title, "视频已进入分析队列");
  assert.equal(analysisPresentation({ result: { status: "ready" }, pending: false }).state, "complete");
  assert.equal(analysisPresentation({ result: null, pending: false }).state, "idle");
});

test("stats never infer a hit count from detections or action segments", () => {
  const stats = summarizeLiveStats({ actions: [{ event_code: "FS01", name_zh: "分腿垫步", detected_segments: 33 }] }, trajectory, {
    counts: { detections: { ball: 1519, racket: 1527 }, frames_with_class: { ball: 1245, racket: 1216 } },
    processing: { processed_frames: 2911 },
  });
  assert.equal(stats.actionSegments, 33);
  assert.equal(stats.ballDetections, 1519);
  assert.equal(stats.shotCount, null);
  assert.equal(stats.contactCount, null);
  assert.equal(stats.shotCountSource, "unavailable");
});
