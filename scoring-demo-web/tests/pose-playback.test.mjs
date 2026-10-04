import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import ts from "typescript";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

function compiledUrl(fileUrl) {
  let js = ts.transpileModule(fs.readFileSync(fileUrl, "utf8"), { fileName: fileUrl.pathname, compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  js = js.replace(/from "([^"]+)"/g, (_match, name) => {
    const local = new URL(`${name}.ts`, fileUrl);
    return `from ${JSON.stringify(name.startsWith(".") ? compiledUrl(fs.existsSync(local) ? local : new URL(`${name}.tsx`, fileUrl)) : import.meta.resolve(name))}`;
  });
  return `data:text/javascript;base64,${Buffer.from(js).toString("base64")}`;
}
const { normalizePosePreview, poseFrameAt, poseEdges, poseWindowStart, poseReplayTimingAllowed } = await import(compiledUrl(new URL("../app/lib/pose-playback.ts", import.meta.url)));
const { default: Skeleton } = await import(compiledUrl(new URL("../app/PoseSkeletonOverlay.tsx", import.meta.url)));
const { default: Viewer } = await import(compiledUrl(new URL("../app/BallTrajectoryViewer.tsx", import.meta.url)));
const { createApiClient } = await import(compiledUrl(new URL("../app/lib/api-client.ts", import.meta.url)));
const { proxyAnalysis } = await import(compiledUrl(new URL("../worker/gateway.ts", import.meta.url)));

const point = (name, x = .4, y = .5, confidence = .9) => ({ name, x, y, confidence });
const frame = (timestamp_ms = 0, overrides = {}) => ({ frame_index: timestamp_ms, timestamp_ms, valid_until_ms: timestamp_ms + 33, person_track_id: 8, selection_epoch: 1, selection_status: "selected", width: 1080, height: 1920, keypoints: [point("left_shoulder", .35, .3), point("left_elbow", .3, .45), point("left_wrist", .4, .5)], ...overrides });
const payload = (frames = [frame()]) => ({ schema_version: "1.0.0", result_kind: "observed_pose_playback", job_id: "pose-job-123", coordinate_space: "normalized_frame_0_1", joint_schema: "halpe26_named", min_keypoint_confidence: .35, skeleton_edges: [["left_shoulder", "left_elbow"], ["left_elbow", "left_wrist"]], source: { requested_start_ms: 0, requested_end_ms: 10000, width: 1080, height: 1920, returned_frames: frames.length, is_sampled: false, is_partial: false, timestamp_semantics: "source_frame_timestamp_ms" }, frames });

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

test("portrait and landscape skeletons use source aspect ratios and never draw boxes or identity labels", () => {
  for (const [width, height] of [[1080, 1920], [1920, 1080]]) {
    const parsed = normalizePosePreview(payload([frame(0, { width, height })]));
    const html = renderToStaticMarkup(createElement(Skeleton, { frame: parsed.frames[0], preview: parsed, aspectRatio: width / height }));
    assert.match(html, new RegExp(`viewBox="0 0 ${width} ${height}"`));
    assert.match(html, /vector-effect="non-scaling-stroke"/);
    assert.equal((html.match(/<circle /g) ?? []).length, 3);
    assert.equal((html.match(/<line /g) ?? []).length, 4);
    assert.doesNotMatch(html, /<rect|<text|person_track_id|track.?8|bbox/);
  }
});

test("viewer defaults skeleton and ball controls to visible and retains old-job video without pose", () => {
  const withPose = renderToStaticMarkup(createElement(Viewer, { videoSrc: "/video.mp4", trajectory: null, posePreview: payload(), jobId: "pose-job-123", poseTimingPreserved: true }));
  assert.match(withPose, /data-pose="on"/);
  assert.match(withPose, /<svg class="pose-skeleton-overlay"/);
  assert.match(withPose, /<input type="checkbox" checked=""\/>人体骨架/);
  assert.match(withPose, /<input type="checkbox" disabled="" checked=""\/>球路/);
  assert.ok(withPose.indexOf("回放显示设置") < withPose.indexOf("<details"));
  const legacy = renderToStaticMarkup(createElement(Viewer, { videoSrc: "/video.mp4", trajectory: null, poseTimingPreserved: true }));
  assert.match(legacy, /<video/);
  assert.match(legacy, /当前时刻暂无可靠人体姿态/);
  assert.doesNotMatch(legacy, /<svg class="pose-skeleton-overlay"/);
});

test("source-timed skeletons pause for unknown or constant-rate replays but allow original and verified video", () => {
  const manifest = timing_preserved => ({ runtime: { annotated_video_compatibility: { timing_preserved, timing_warning: "constant_fps_fallback_replay_may_not_match_source_timing" } } });
  assert.equal(poseReplayTimingAllowed(false, manifest(true)), true);
  for (const result of [null, {}, manifest(false), manifest(undefined), manifest("true")]) {
    assert.equal(poseReplayTimingAllowed(false, result), false);
    assert.equal(poseReplayTimingAllowed(true, result), true);
  }
  const html = renderToStaticMarkup(createElement(Viewer, { videoSrc: "/annotated.mp4", trajectory: null, posePreview: payload(), jobId: "pose-job-123", poseTimingPreserved: poseReplayTimingAllowed(false, manifest(false)) }));
  assert.match(html, /<video/);
  assert.match(html, /data-pose="paused"/);
  assert.match(html, /data-overlay="on"/);
  assert.match(html, /回放时间对应尚未确认，人体骨架已暂停/);
  assert.match(html, /<input type="checkbox" disabled=""\/>人体骨架/);
  assert.doesNotMatch(html, /<svg class="pose-skeleton-overlay"/);
});

test("pose API requests are capped to a ten-second window with at most 600 frames", async () => {
  let requested;
  const client = createApiClient({ baseUrl: "" }, async url => { requested = String(url); return Response.json(payload()); });
  await client.getPosePreview("pose-job-123", { startMs: 12345.7, durationMs: 999999, sampleLimit: 999999 });
  assert.equal(requested, "/v1/jobs/pose-job-123/pose-preview?start_ms=12345&duration_ms=10000&sample_limit=600");
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
