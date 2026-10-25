import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import ts from "typescript";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

function compiledUrl(fileUrl) {
  // Keep source JSON imports shared with production, without a generated copy.
  if (fileUrl.pathname.endsWith(".json")) return `data:text/javascript;base64,${Buffer.from(`export default ${fs.readFileSync(fileUrl, "utf8")}`).toString("base64")}`;
  let js = ts.transpileModule(fs.readFileSync(fileUrl, "utf8"), { fileName: fileUrl.pathname, compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  js = js.replace(/from "([^"]+)"/g, (_match, name) => {
    const local = new URL(name.endsWith(".json") ? name : `${name}.ts`, fileUrl);
    return `from ${JSON.stringify(name.startsWith(".") ? compiledUrl(fs.existsSync(local) ? local : new URL(`${name}.tsx`, fileUrl)) : import.meta.resolve(name))}`;
  });
  return `data:text/javascript;base64,${Buffer.from(js).toString("base64")}`;
}
const { normalizePosePreview, poseFrameAt, poseEdges, poseWindowStart, poseReplayTimingAllowed } = await import(compiledUrl(new URL("../app/lib/pose-playback.ts", import.meta.url)));
const { default: FocusOverlay } = await import(compiledUrl(new URL("../app/MotionFocusOverlay.tsx", import.meta.url)));
const { motionFocusAt } = await import(compiledUrl(new URL("../app/lib/motion-focus.ts", import.meta.url)));
const { default: Viewer } = await import(compiledUrl(new URL("../app/BallTrajectoryViewer.tsx", import.meta.url)));
const { createApiClient } = await import(compiledUrl(new URL("../app/lib/api-client.ts", import.meta.url)));
const { proxyAnalysis } = await import(compiledUrl(new URL("../worker/gateway.ts", import.meta.url)));

const point = (name, x = .4, y = .5, confidence = .9) => ({ name, x, y, confidence });
const frame = (timestamp_ms = 0, overrides = {}) => ({ frame_index: timestamp_ms, timestamp_ms, valid_until_ms: timestamp_ms + 33, person_track_id: 8, selection_epoch: 1, selection_status: "selected", width: 1080, height: 1920, keypoints: [point("left_shoulder", .35, .3), point("left_elbow", .3, .45), point("left_wrist", .4, .5)], ...overrides });
const payload = (frames = [frame()]) => ({ schema_version: "1.0.0", result_kind: "observed_pose_playback", job_id: "pose-job-123", coordinate_space: "normalized_frame_0_1", joint_schema: "halpe26_named", min_keypoint_confidence: .35, skeleton_edges: [["left_shoulder", "left_elbow"], ["left_elbow", "left_wrist"]], source: { requested_start_ms: 0, requested_end_ms: 10000, width: 1080, height: 1920, returned_frames: frames.length, is_sampled: false, is_partial: false, timestamp_semantics: "source_frame_timestamp_ms" }, frames });

const modelFrame = (index = 0) => frame(index * 40, {
  frame_index: index, processed_index: index, valid_until_ms: (index + 1) * 40,
  pose_source: "sam3d", source_keypoint_format: "mhr70", observation_semantics: "monocular_model_estimate",
  source_binding: { video_sha256: "a".repeat(64), frame_index: index, processed_index: index, pts: index,
    time_base_num: 1, time_base_den: 25, timestamp_ms: index * 40, person_track_id: 8, selection_epoch: 0, camera_reference_epoch: 0 },
  keypoints: [{ name: "left_wrist", x: .4, y: .5, raw_x_px: 432, raw_y_px: 960, in_frame: true,
    confidence: null, confidence_semantics: "not_provided_by_sam3d", source: "model_estimate", observation_semantics: "model_estimate" }],
});

test("explicit source-bound model estimates draw with null confidence and no legacy recovery", () => {
  const raw = payload([modelFrame(0), modelFrame(1), modelFrame(2)]);
  raw.frames[1].keypoints = [];
  raw.display_frames = raw.frames.map(row => ({ ...row, keypoints: [point("left_wrist")] }));
  raw.display_continuity = { version: "short-joint-gap-v1.0.0", used_for_scoring: false, max_gap_ms: 150,
    confidence_semantics: "minimum_anchor_confidence_not_model_estimate", interpolated_keypoints: 1, affected_frames: 1 };
  const parsed = normalizePosePreview(raw);
  assert.equal(parsed.frames[0].keypoints[0].confidence, null);
  assert.equal(parsed.frames[0].keypoints[0].source, "model_estimate");
  assert.equal(parsed.frames[0].source_keypoint_format, "mhr70");
  assert.deepEqual(normalizePosePreview(parsed), parsed);
  assert.equal(motionFocusAt(parsed, 0).markers.length, 1);
  assert.equal(motionFocusAt(parsed, 40), null);
  assert.equal(parsed.display_frames, undefined);
  assert.equal(motionFocusAt(parsed, 80).trails.length, 0);
});

test("model estimate preview rejects clipped, unbound and nonfinite points without relaxing observed confidence", () => {
  for (const change of [{ in_frame: false }, { raw_x_px: -1, x: 0 }, { raw_x_px: 1080, x: 1 }, { raw_x_px: NaN },
    { raw_x_px: undefined }, { confidence: 1 }, { source: "observed" }, { name: "left_heel" }, { x: .9 }]) {
    const row = modelFrame(); row.keypoints[0] = { ...row.keypoints[0], ...change };
    assert.deepEqual(normalizePosePreview(payload([row])).frames[0].keypoints, [], JSON.stringify(change));
  }
  for (const change of [{ source_binding: {} }, { source_binding: { ...modelFrame().source_binding, pts: 1 } },
    { source_keypoint_format: "halpe26" }, { processed_index: 2 }, { person_track_id: 9 }]) {
    assert.deepEqual(normalizePosePreview(payload([{ ...modelFrame(), ...change }])).frames[0].keypoints, []);
  }
  assert.deepEqual(normalizePosePreview(payload([frame(0, { keypoints: [point("left_wrist", .4, .5, null)] })])).frames[0].keypoints, []);
});

test("model estimate trails break across source camera epoch, selection epoch and video identity", () => {
  assert.equal(motionFocusAt(normalizePosePreview(payload([modelFrame(0), modelFrame(1)])), 40).trails.length, 1);
  for (const change of [{ camera_reference_epoch: 1 }, { selection_epoch: 1 }, { video_sha256: "b".repeat(64) }]) {
    const rows = [modelFrame(0), modelFrame(1)]; Object.assign(rows[1].source_binding, change);
    assert.equal(motionFocusAt(normalizePosePreview(payload(rows)), 40).trails.length, 0);
  }
});

test("nonzero original PTS stays bound while model overlay uses relative playback time", () => {
  const row = modelFrame(0);
  row.source_timestamp_ms = 12000;
  Object.assign(row.source_binding, { pts: 300, timestamp_ms: 12000 });
  const parsed = normalizePosePreview(payload([row]));
  assert.equal(parsed.frames[0].source_binding.pts, 300);
  assert.equal(parsed.frames[0].timestamp_ms, 0);
  assert.equal(motionFocusAt(parsed, 0).markers.length, 1);
  assert.equal(motionFocusAt(parsed, 12000), null);
  row.source_timestamp_ms = 12001;
  assert.deepEqual(normalizePosePreview(payload([row])).frames[0].keypoints, []);
});

test("SAM trails reject a source clock jump or reversal despite smooth relative playback", () => {
  for (const [sourceBefore, sourceAfter, expected] of [[12000, 12040, 1], [12000, 32040, 0], [12000, 12000, 0], [12000, 11960, 0]]) {
    const rows = [modelFrame(0), modelFrame(1)];
    for (const [index, sourceTime] of [sourceBefore, sourceAfter].entries()) {
      rows[index].source_timestamp_ms = sourceTime;
      Object.assign(rows[index].source_binding, { pts: sourceTime / 40, timestamp_ms: sourceTime });
    }
    const focus = motionFocusAt(normalizePosePreview(payload(rows)), 40);
    assert.equal(focus.markers.length, 1);
    assert.equal(focus.trails.length, expected);
  }
});

test("pose preview rejects incompatible coordinates, timestamps and foreign jobs", () => {
  assert.ok(normalizePosePreview(payload(), "pose-job-123"));
  for (const invalid of [{ ...payload(), job_id: "other-job" }, { ...payload(), coordinate_space: "body_scaled" }, { ...payload(), joint_schema: "guessed_26" }, { ...payload(), source: { ...payload().source, timestamp_semantics: "fps_guess" } }, { ...payload(), source: { ...payload().source, requested_end_ms: 11000 } }]) assert.equal(normalizePosePreview(invalid, "pose-job-123"), null);
});

test("missing and low-confidence joints never become endpoints or fabricated connections", () => {
  const raw = payload([frame(0, { keypoints: [point("left_shoulder"), point("left_elbow", .3, .4, .1), point("left_wrist", 2), point("right_wrist", .5, Infinity), point("invented_center"), point("right_hip", .5, .8, NaN)] })]);
  const parsed = normalizePosePreview(raw);
  assert.deepEqual(parsed.frames[0].keypoints.map(point => point.name), ["left_shoulder"]);
  assert.deepEqual(poseEdges(parsed.frames[0], parsed.skeleton_edges), []);
  raw.frames[0].keypoints.push(point("left_shoulder", .8));
  assert.deepEqual(normalizePosePreview(raw).frames[0].keypoints, []);
});

test("replay uses the preceding observed frame only and expires at its true validity end", () => {
  const parsed = normalizePosePreview(payload([frame(10), frame(330)]));
  assert.equal(poseFrameAt(parsed, 9), null);
  assert.equal(poseFrameAt(parsed, 10).timestamp_ms, 10);
  assert.equal(poseFrameAt(parsed, 42).timestamp_ms, 10);
  assert.equal(poseFrameAt(parsed, 43), null);
  assert.equal(poseFrameAt(parsed, 200), null);
  assert.equal(poseFrameAt(parsed, 330).timestamp_ms, 330);
  const long = normalizePosePreview(payload([frame(0, { valid_until_ms: 9999 })]));
  assert.equal(poseFrameAt(long, 99).timestamp_ms, 0);
  assert.equal(poseFrameAt(long, 100), null);
});

test("empty, ambiguous and changed-subject frames cannot carry over another person's joints", () => {
  const parsed = normalizePosePreview(payload([frame(0, { valid_until_ms: 100 }), frame(33, { selection_status: "unavailable", keypoints: [] }), frame(66, { person_track_id: 15, selection_epoch: 2, keypoints: [point("right_hip", .8, .7)] })]));
  assert.equal(poseFrameAt(parsed, 32).person_track_id, 8);
  assert.equal(poseFrameAt(parsed, 33), null);
  assert.equal(poseFrameAt(parsed, 65), null);
  assert.deepEqual(poseFrameAt(parsed, 66).keypoints.map(point => point.name), ["right_hip"]);
  const ambiguous = normalizePosePreview(payload([frame(0, { selection_status: "unavailable" })]));
  assert.equal(poseFrameAt(ambiguous, 0), null);
  const duplicate = normalizePosePreview(payload([frame(0), frame(0, { person_track_id: 15 })]));
  assert.equal(poseFrameAt(duplicate, 0), null);
});

test("sampled windows never extend pose validity through discarded frames", () => {
  const raw = payload([frame(0), frame(330)]); raw.source.is_sampled = true;
  const parsed = normalizePosePreview(raw);
  assert.equal(poseFrameAt(parsed, 100), null);
  assert.equal(poseFrameAt(parsed, 329), null);
  assert.deepEqual([0, 9999, 10000, 19999, 20000, -3, NaN].map(poseWindowStart), [0, 0, 10000, 10000, 20000, 0, 0]);
});

test("portrait and landscape motion markers retain source coordinates without drawing the body skeleton", () => {
  for (const [width, height] of [[1080, 1920], [1920, 1080]]) {
    const parsed = normalizePosePreview(payload([frame(0, { width, height })]));
    const html = renderToStaticMarkup(createElement(FocusOverlay, { focus: motionFocusAt(parsed, 0, width / height) }));
    assert.match(html, new RegExp(`viewBox="0 0 ${width} ${height}"`));
    // Matches the video's object-fit:contain when the stage's max-height letterboxes it.
    assert.match(html, /preserveAspectRatio="xMidYMid meet"/);
    assert.match(html, /vector-effect="non-scaling-stroke"/);
    assert.equal((html.match(/<circle /g) ?? []).length, 2);
    assert.equal((html.match(/<line /g) ?? []).length, 0);
    assert.match(html, new RegExp(`cx="${.4 * width}" cy="${.5 * height}"`));
    assert.doesNotMatch(html, /<rect|<text|person_track_id|track.?8|bbox/);
  }
});

test("viewer starts without overlays and retains optional motion marker controls inside analysis tools", () => {
  const withPose = renderToStaticMarkup(createElement(Viewer, { videoSrc: "/video.mp4", trajectory: null, posePreview: payload(), jobId: "pose-job-123", poseTimingPreserved: true }));
  assert.match(withPose, /data-pose="off"/);
  assert.doesNotMatch(withPose, /<svg class="motion-focus-overlay"/);
  assert.match(withPose, /<input type="checkbox"\/>动作标记/);
  assert.match(withPose, /<input type="checkbox" disabled=""\/>球路/);
  assert.ok(withPose.indexOf("回放显示设置") > withPose.indexOf("<details"));
  const enabled = renderToStaticMarkup(createElement(Viewer, { videoSrc: "/video.mp4", trajectory: null, posePreview: payload(), jobId: "pose-job-123", poseTimingPreserved: true, initialOverlays: { pose: true } }));
  assert.match(enabled, /data-pose="on"/);
  assert.match(enabled, /<svg class="motion-focus-overlay"/);
  const legacy = renderToStaticMarkup(createElement(Viewer, { videoSrc: "/video.mp4", trajectory: null, poseTimingPreserved: true }));
  assert.match(legacy, /<video/);
  assert.match(legacy, /此报告未提供姿态观测，视频仍可回放/);
  assert.match(legacy, /<input type="checkbox" disabled=""\/>动作标记/);
  assert.doesNotMatch(legacy, /<svg class="motion-focus-overlay"/);
});

test("source-timed motion markers pause for unknown or constant-rate replays but allow original and verified video", () => {
  const manifest = timing_preserved => ({ runtime: { annotated_video_compatibility: { timing_preserved, timing_warning: "constant_fps_fallback_replay_may_not_match_source_timing" } } });
  assert.equal(poseReplayTimingAllowed(false, manifest(true)), true);
  for (const result of [null, {}, manifest(false), manifest(undefined), manifest("true")]) {
    assert.equal(poseReplayTimingAllowed(false, result), false);
    assert.equal(poseReplayTimingAllowed(true, result), true);
  }
  const html = renderToStaticMarkup(createElement(Viewer, { videoSrc: "/annotated.mp4", trajectory: null, posePreview: payload(), jobId: "pose-job-123", poseTimingPreserved: poseReplayTimingAllowed(false, manifest(false)), initialOverlays: { pose: true } }));
  assert.match(html, /<video/);
  assert.match(html, /data-pose="paused"/);
  assert.match(html, /data-overlay="off"/);
  assert.match(html, /回放时间对应尚未确认，动作标记已暂停/);
  assert.match(html, /<input type="checkbox" disabled=""\/>动作标记/);
  assert.doesNotMatch(html, /<svg class="motion-focus-overlay"/);
});

test("pose API requests are capped to a ten-second window with at most 600 frames", async () => {
  let requested;
  const client = createApiClient({ baseUrl: "" }, async url => {
    requested = String(url);
    const start = Number(new URL(requested, "https://local.invalid").searchParams.get("start_ms"));
    const response = payload([frame(start)]);
    Object.assign(response.source, { requested_start_ms: start, requested_end_ms: start + 10000 });
    return Response.json(response);
  });
  await client.getPosePreview("pose-job-123", { startMs: 12345.7, durationMs: 999999, sampleLimit: 999999 });
  assert.equal(requested, "/v1/jobs/pose-job-123/pose-preview?start_ms=12345&duration_ms=10000&sample_limit=600");
});

test("motion trails show only recent observed wrists, with sparse hand and ankle markers", () => {
  const frames = Array.from({ length: 25 }, (_, index) => frame(index * 33, { keypoints: [point("left_wrist", .3 + index * .004), point("right_wrist", .7 - index * .004), point("left_ankle", .4, .9), point("left_shoulder")] }));
  const parsed = normalizePosePreview(payload(frames));
  const before = structuredClone(parsed);
  const focus = motionFocusAt(parsed, 660);
  assert.deepEqual(focus.markers.map(point => point.name), ["left_wrist", "right_wrist", "left_ankle"]);
  assert.equal(focus.trails.length, 2);
  for (const trail of focus.trails) {
    assert.equal(trail.points.at(-1).timestamp_ms, 660);
    assert.ok(trail.points.every(point => point.timestamp_ms >= 160 && point.timestamp_ms <= 660));
  }
  assert.deepEqual(parsed, before);
  assert.equal(motionFocusAt(parsed, 900), null);
  assert.equal(motionFocusAt(null, 0), null);
});

test("motion trails break at identity, time, resolution, visibility and sudden-position discontinuities", () => {
  const invalidOlder = [
    { person_track_id: 9 }, { selection_epoch: 2 }, { selection_status: "unavailable" },
    { width: 1920, height: 1080 }, { valid_until_ms: 32 },
    { keypoints: [point("left_wrist", .4, .5, .1)] },
    { keypoints: [point("left_wrist", .4, .5, .9), point("left_wrist", .5, .5, .9)] },
    { keypoints: [point("left_wrist", .9, .9)] },
  ];
  for (const overrides of invalidOlder) {
    const parsed = normalizePosePreview(payload([frame(0, overrides), frame(33)]));
    assert.equal(motionFocusAt(parsed, 33).trails.length, 0, JSON.stringify(overrides));
  }
  const gap = normalizePosePreview(payload([frame(0, { valid_until_ms: 999 }), frame(200)]));
  assert.equal(motionFocusAt(gap, 200).trails.length, 0);
  const unidentified = normalizePosePreview(payload([frame(0, { person_track_id: null, selection_status: "single_visible_person" }), frame(33, { person_track_id: null, selection_status: "single_visible_person" })]));
  assert.equal(motionFocusAt(unidentified, 33).markers.length, 1);
  assert.equal(motionFocusAt(unidentified, 33).trails.length, 0);
});

test("display-only interpolation cannot supply a hand marker or connect its motion path", () => {
  const parsed = normalizePosePreview(displayGap());
  assert.equal(motionFocusAt(parsed, 40), null);
  assert.equal(motionFocusAt(parsed, 80).trails.length, 0);
  assert.equal(motionFocusAt(parsed, 79), null);
});

test("motion markers respect declared sub-millisecond source-PTS rounding but never a later frame", () => {
  const raw = payload([frame(42)]);
  raw.source.timestamp_rounding_tolerance_ms = .5;
  const parsed = normalizePosePreview(raw);
  assert.equal(motionFocusAt(parsed, 41), null);
  assert.equal(motionFocusAt(parsed, 1000 / 24).markers.length, 1);
});

test("gateway adds only the bounded pose-preview read endpoint without forwarding browser credentials", async () => {
  const saved = globalThis.fetch; let called = 0;
  globalThis.fetch = async (url, init) => { called++; assert.match(String(url), /\/pose-preview\?start_ms=0$/); assert.equal(init.headers.get("authorization"), "Bearer server-key"); assert.equal(init.headers.get("cookie"), null); return Response.json(payload()); };
  const env = { RALLYMATE_API_ORIGIN: "http://127.0.0.1:8001", RALLYMATE_API_KEY: "server-key" };
  try {
    const response = await proxyAnalysis(new Request("http://localhost/v1/jobs/pose-job-123/pose-preview?start_ms=0", { headers: { authorization: "Bearer browser-key", cookie: "session=private" } }), env);
    assert.equal(response.status, 200);
    assert.equal((await proxyAnalysis(new Request("http://localhost/v1/jobs/pose-job-123/pose-preview", { method: "POST" }), env)).status, 404);
    assert.equal((await proxyAnalysis(new Request("http://localhost/v1/jobs/pose-job-123/pose-private"), env)).status, 404);
    assert.equal(called, 1);
  } finally { globalThis.fetch = saved; }
});

function displayGap() {
  const raw = payload([frame(0, { frame_index: 0, valid_until_ms: 40 }), frame(40, { frame_index: 1, valid_until_ms: 80, keypoints: [point("left_shoulder"), point("left_elbow")] }), frame(80, { frame_index: 2, valid_until_ms: 120 })]);
  raw.display_continuity = { version: "short-joint-gap-v1.0.0", max_gap_ms: 150, interpolated_keypoints: 1, affected_frames: 1, confidence_semantics: "minimum_anchor_confidence_not_model_estimate", used_for_scoring: false };
  raw.display_frames = structuredClone(raw.frames).map(row => ({ ...row, keypoints: row.keypoints.map(point => ({ ...point, source: "observed" })) }));
  raw.display_frames[1].keypoints.push({ ...point("left_wrist"), source: "interpolated", anchors: { before_timestamp_ms: 0, after_timestamp_ms: 80, before_frame_index: 0, after_frame_index: 2, before_processed_index: 0, after_processed_index: 2, person_track_id: 8, selection_epoch: 1 } });
  return raw;
}

test("bounded display-only points reconnect a joint without changing raw observations or frame lifetime", () => {
  const raw = displayGap(), before = structuredClone(raw);
  const parsed = normalizePosePreview(raw);
  assert.equal(parsed.frames[1].keypoints.length, 2);
  assert.equal(poseFrameAt(parsed, 40).keypoints.length, 3);
  assert.equal(poseFrameAt(parsed, 40).keypoints.at(-1).source, "interpolated");
  assert.equal(parsed.display_continuity.interpolated_keypoints, 1);
  assert.equal(parsed.display_continuity.used_for_scoring, false);
  assert.equal(poseFrameAt(parsed, 120), null);
  assert.deepEqual(raw, before);
  assert.equal(normalizePosePreview(parsed).display_continuity.interpolated_keypoints, 1);
  const html = renderToStaticMarkup(createElement(Viewer, { videoSrc: "/video.mp4", videoTimeOriginMs: 40, trajectory: null, posePreview: raw, jobId: "pose-job-123", poseTimingPreserved: true, initialOverlays: { pose: true } }));
  assert.doesNotMatch(html, /短暂缺点已平滑衔接|motion-focus-overlay/);
  assert.match(html, /当前时刻暂无可靠动作标记/);
});

test("display points with long gaps, foreign identity, missing anchors or promoted scoring are rejected", () => {
  for (const mutate of [
    raw => { raw.display_frames[1].keypoints.at(-1).anchors.after_timestamp_ms = 151; },
    raw => { raw.display_frames[1].keypoints.at(-1).anchors.person_track_id = 9; },
    raw => { raw.display_frames[1].keypoints.at(-1).anchors.selection_epoch = 2; },
    raw => { raw.display_frames[1].keypoints.at(-1).anchors = undefined; },
    raw => { raw.display_frames[1].keypoints.at(-1).source = "observed"; },
    raw => { raw.display_continuity.used_for_scoring = true; },
    raw => { raw.display_frames[1].person_track_id = 9; },
    raw => { raw.frames[0].keypoints = [point("left_shoulder")]; },
    raw => { raw.frames[1].keypoints = []; },
    raw => { raw.display_frames[1].keypoints.push(structuredClone(raw.display_frames[1].keypoints.at(-1))); },
  ]) {
    const raw = displayGap(); mutate(raw);
    const parsed = normalizePosePreview(raw);
    assert.equal(poseFrameAt(parsed, 40)?.keypoints.some(point => point.name === "left_wrist") ?? false, false);
    assert.equal(parsed.display_continuity?.interpolated_keypoints ?? 0, 0);
  }
});

test("display supplements cannot bridge an intervening subject switch even if both endpoints match", () => {
  const raw = displayGap();
  raw.frames.splice(1, 0, frame(20, { frame_index: 1, valid_until_ms: 40, person_track_id: 9, selection_epoch: 2 }));
  raw.frames[2].frame_index = 2; raw.frames[3].frame_index = 3;
  raw.display_frames[1].frame_index = 2;
  const anchors = raw.display_frames[1].keypoints.at(-1).anchors;
  anchors.after_frame_index = 3; anchors.after_processed_index = 3;
  const parsed = normalizePosePreview(raw);
  assert.equal(poseFrameAt(parsed, 40).keypoints.some(point => point.name === "left_wrist"), false);
});

test("bounded server anchors can straddle a ten-second window without extending its observations", () => {
  const raw = displayGap();
  const target = raw.frames[1]; target.timestamp_ms = 10000; target.frame_index = 250; target.valid_until_ms = 10040;
  const shown = raw.display_frames[1]; shown.timestamp_ms = 10000; shown.frame_index = 250; shown.valid_until_ms = 10040;
  Object.assign(shown.keypoints.at(-1).anchors, { before_timestamp_ms: 9960, after_timestamp_ms: 10040, before_frame_index: 249, after_frame_index: 251, before_processed_index: 249, after_processed_index: 251 });
  raw.source.requested_start_ms = 10000; raw.source.requested_end_ms = 20000;
  raw.frames = [target]; raw.display_frames = [shown];
  const parsed = normalizePosePreview(raw);
  assert.equal(poseFrameAt(parsed, 10000).keypoints.at(-1).source, "interpolated");
  assert.equal(poseFrameAt(parsed, 10040), null);
});

test("only explicit SimCC response provenance can restore a filtered observed joint", () => {
  const raw = displayGap();
  raw.display_frames[1].keypoints = [...raw.display_frames[1].keypoints.slice(0, 2), { ...point("left_wrist", .4, .5, 1), source: "observed", score_semantics: "rtmpose_simcc_response_clipped_for_display", raw_confidence: 1.15 }];
  const parsed = normalizePosePreview(raw);
  assert.equal(parsed.frames[1].keypoints.length, 2);
  assert.equal(poseFrameAt(parsed, 40).keypoints.length, 3);
  assert.equal(poseFrameAt(parsed, 40).keypoints.at(-1).source, "observed");
  assert.equal(poseFrameAt(parsed, 40).keypoints.at(-1).raw_confidence, 1.15);
  assert.equal(parsed.display_continuity.response_score_recovered_keypoints, 1);
  assert.equal(motionFocusAt(parsed, 40).markers[0].raw_confidence, 1.15);
  assert.deepEqual(motionFocusAt(parsed, 80).trails[0].points.map(point => point.timestamp_ms), [0, 40, 80]);
  for (const change of [{ score_semantics: "guessed" }, { raw_confidence: .9 }, { raw_confidence: Infinity }, { confidence: .9 }]) {
    const invalid = structuredClone(raw); Object.assign(invalid.display_frames[1].keypoints.at(-1), change);
    assert.equal(poseFrameAt(normalizePosePreview(invalid), 40).keypoints.length, 2);
    assert.equal(motionFocusAt(normalizePosePreview(invalid), 40), null);
  }
});

function identityRepair() {
  const raw = displayGap();
  raw.frames[1] = frame(40, { frame_index: 1, valid_until_ms: 80, person_track_id: 9, selection_epoch: 2, keypoints: [point("left_shoulder", .9), point("left_elbow", .9), point("left_wrist", .9)] });
  raw.frames[2].selection_epoch = 3;
  raw.display_continuity.identity_repair_version = "short-target-dropout-v1.0.0";
  raw.display_continuity.repaired_identity_frames = 1;
  raw.display_frames[1] = { ...structuredClone(raw.frames[1]), person_track_id: 8, selection_status: "display_interpolated", observed_person_track_id: 9, observed_selection_epoch: 2,
    identity_repair: { version: "short-target-dropout-v1.0.0", method: "same_target_detection_dropout", original_track_id: 9, original_selection_epoch: 2, target_track_id: 8, before_frame_index: 0, after_frame_index: 2, before_timestamp_ms: 0, after_timestamp_ms: 80, before_selection_epoch: 1, after_selection_epoch: 3, primary_player_id: 1 },
    keypoints: raw.frames[0].keypoints.map(point => ({ ...point, source: "interpolated", anchors: { before_timestamp_ms: 0, after_timestamp_ms: 80, before_frame_index: 0, after_frame_index: 2, before_processed_index: 0, after_processed_index: 2, person_track_id: 8, selection_epoch: 2, before_selection_epoch: 1, after_selection_epoch: 3 } })) };
  raw.display_frames[2].selection_epoch = 3;
  return raw;
}

test("explicit brief target dropout uses only the target anchors and preserves the raw background observation", () => {
  const raw = identityRepair(), parsed = normalizePosePreview(raw);
  assert.equal(parsed.frames[1].person_track_id, 9);
  assert.equal(parsed.frames[1].keypoints[0].x, .9);
  const shown = poseFrameAt(parsed, 40);
  assert.equal(shown.person_track_id, 8);
  assert.equal(shown.selection_epoch, 2);
  assert.equal(shown.selection_status, "display_interpolated");
  assert.equal(shown.observed_person_track_id, 9);
  assert.ok(shown.keypoints.every(point => point.source === "interpolated" && point.x !== .9));
  assert.equal(parsed.display_continuity.repaired_identity_frames, 1);
  assert.equal(normalizePosePreview(parsed).display_continuity.repaired_identity_frames, 1);
  assert.equal(motionFocusAt(parsed, 40), null);
  assert.equal(motionFocusAt(parsed, 80).trails.length, 0);
  const incorrect = identityRepair(); incorrect.display_frames[1].keypoints[0].x = .9;
  assert.equal(poseFrameAt(normalizePosePreview(incorrect), 40).keypoints.some(point => point.x === .9), false);
});

test("identity repair without its matching original observation and target anchors cannot switch subjects", () => {
  for (const mutate of [
    raw => { raw.display_continuity.identity_repair_version = "unknown"; },
    raw => { raw.display_frames[1].identity_repair.original_track_id = 7; },
    raw => { raw.display_frames[1].identity_repair.original_selection_epoch = 0; },
    raw => { raw.display_frames[1].observed_person_track_id = 7; },
    raw => { raw.display_frames[1].identity_repair.after_timestamp_ms = 151; },
  ]) {
    const raw = identityRepair(); mutate(raw);
    assert.equal(poseFrameAt(normalizePosePreview(raw), 40).person_track_id, 9);
  }
  const wrongAnchor = identityRepair(); wrongAnchor.frames[2].person_track_id = 9;
  assert.equal(poseFrameAt(normalizePosePreview(wrongAnchor), 40), null);
});

test("an explicitly annotated known-target dropout may display target anchors while its raw empty pose stays empty", () => {
  const raw = identityRepair();
  Object.assign(raw.frames[1], { person_track_id: null, selection_status: "unavailable", keypoints: [] });
  raw.display_frames[1].observed_person_track_id = null;
  Object.assign(raw.display_frames[1].identity_repair, { original_track_id: null, dropout_kind: "pose_unavailable" });
  const parsed = normalizePosePreview(raw);
  assert.equal(parsed.frames[1].keypoints.length, 0);
  assert.equal(poseFrameAt(parsed, 40).person_track_id, 8);
  assert.equal(poseFrameAt(parsed, 40).identity_repair.original_track_id, null);
  assert.equal(poseFrameAt(normalizePosePreview(parsed), 40).identity_repair.dropout_kind, "pose_unavailable");
  delete raw.display_frames[1].identity_repair.dropout_kind;
  assert.equal(poseFrameAt(normalizePosePreview(raw), 40), null);
});

test("declared half-millisecond PTS rounding selects 144 distinct frames at 24fps without widening legacy lookup", () => {
  const rows = Array.from({ length: 144 }, (_, index) => frame(Math.round(index * 1000 / 24), { frame_index: index, valid_until_ms: Math.round((index + 1) * 1000 / 24) }));
  const raw = payload(rows), legacy = normalizePosePreview(raw);
  assert.equal(poseFrameAt(legacy, 1000 / 24).frame_index, 0);
  raw.source.timestamp_rounding_tolerance_ms = .5;
  const parsed = normalizePosePreview(raw);
  const selected = Array.from({ length: 144 }, (_, index) => poseFrameAt(parsed, index * 1000 / 24)?.frame_index);
  assert.deepEqual(selected, Array.from({ length: 144 }, (_, index) => index));
  assert.equal(poseFrameAt(parsed, -0.01), null);
  assert.equal(poseFrameAt(parsed, 0).frame_index, 0);
  assert.equal(poseFrameAt(parsed, 6000), null);
  assert.equal(poseFrameAt(parsed, 4041.666666).frame_index, 97);
  for (const invalid of [1, -1, Infinity, "0.5"]) {
    raw.source.timestamp_rounding_tolerance_ms = invalid;
    assert.equal(poseFrameAt(normalizePosePreview(raw), 1000 / 24).frame_index, 0);
  }
});

test("PTS rounding cannot extend expired poses or borrow beyond the declared half millisecond", () => {
  const raw = payload([frame(10, { valid_until_ms: 43 }), frame(330)]); raw.source.timestamp_rounding_tolerance_ms = .5;
  const parsed = normalizePosePreview(raw);
  assert.equal(poseFrameAt(parsed, 9.49), null);
  assert.equal(poseFrameAt(parsed, 9.5).timestamp_ms, 10);
  assert.equal(poseFrameAt(parsed, 43), null);
  assert.equal(poseFrameAt(parsed, 329.49), null);
  assert.equal(poseFrameAt(parsed, 329.5).timestamp_ms, 330);
});
