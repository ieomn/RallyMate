import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import ts from "typescript";
import * as React from "react";

// Execute the actual nested ReplayPanel with stateful hooks; peripheral viewers
// are element boundaries. This exercises selection changes without media I/O.
const source = fs.readFileSync(new URL("../app/ScoreLab.tsx", import.meta.url), "utf8");
const ast = ts.createSourceFile("ScoreLab.tsx", source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const panel = ast.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === "ReplayPanel");
assert.ok(panel);
const js = ts.transpileModule(panel.getText(ast), { compilerOptions: { target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.React } }).outputText;
const timingJs = ts.transpileModule(fs.readFileSync(new URL("../app/lib/original-video-timing.ts", import.meta.url), "utf8"), { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText;
const { originalPoseTimeBinding, poseWindowMatchesOriginal } = await import(`data:text/javascript;base64,${Buffer.from(timingJs).toString("base64")}`);
const jobId = "66883aeb-e0c3-4065-80fd-f67aa108b208";
const original = { url: `/v1/jobs/${jobId}/original-video`, sha256: "a".repeat(64), bytes: 12345, source_job_id: "source-job-123" };
const nodes = node => Array.isArray(node) ? node.flatMap(nodes) : node?.props ? [node, ...nodes(node.props.children)] : [];
function mount(overrides = {}) {
  let index = 0, tree;
  const state = [];
  const evidence = { jobId, candidateSource: "online", trajectory: null, result: { job_id: jobId, original_video: original,
    artifact_urls: { "annotated.mp4": "available" }, runtime: { annotated_video_compatibility: { timing_preserved: true } } }, error: null, ...overrides.evidence };
  const props = { api: { getPosePreview: async () => null }, replaySignal: new AbortController().signal, candidateSessionKey: "session-1", onCandidateReplayHandle() {}, ...overrides, evidence };
  const renderPanel = vm.runInNewContext(`${js}; ReplayPanel`, {
    React, AbortSignal, URL, useCallback: value => value, useMemo: compute => compute(), useRef: value => ({ current: value }), useEffect() {},
    verifiedMeshMedia: () => overrides.meshMedia ?? null, motionAnalysisOf: () => null,
    useState(initial) { const slot = index++; if (!(slot in state)) state[slot] = initial; return [state[slot], value => { state[slot] = value; }]; },
    BallTrajectoryViewer: "viewer", BallTrajectorySummary: "summary", ReplayClipNavigator: "clips", OfficialMeshReplay: "mesh", SwingKinematicsPanel: "swing",
    swingVideoDurationMs: () => null,
    poseReplayTimingAllowed: (local, result) => local || result?.runtime?.annotated_video_compatibility?.timing_preserved === true,
    originalPoseTimeBinding, poseWindowMatchesOriginal,
  });
  const app = {
    render() { index = 0; tree = renderPanel(props); },
    get viewer() { return nodes(tree).find(node => node.type === "viewer").props; },
    get mesh() { return nodes(tree).find(node => node.type === "mesh")?.props; },
    get swing() { return nodes(tree).find(node => node.type === "swing")?.props; },
    button(text) { return nodes(tree).find(node => node.type === "button" && node.props.children === text); },
    choose(text) { const button = app.button(text); assert.equal(button.props.disabled, false); button.props.onClick(); app.render(); },
  };
  app.render(); return app;
}

test("verified server originals are preferred, switch to annotation and back, without certifying timing", () => {
  const app = mount();
  assert.equal(app.viewer.videoSrc, original.url); assert.equal(app.viewer.videoKind, "original");
  assert.equal(app.viewer.poseTimingPreserved, false);
  assert.equal(app.viewer.candidateReplayBinding.videoSha256, original.sha256);
  assert.equal(app.viewer.candidateReplayBinding.mappingVerified, false);
  assert.equal(app.viewer.candidateReplayBinding.originMs, null);
  assert.equal(app.swing, undefined, "3D measurements live in the report details, outside continuous playback");
  app.choose("标注回放");
  assert.equal(app.viewer.videoSrc, `/v1/jobs/${jobId}/artifacts/annotated.mp4`);
  assert.equal(app.viewer.videoKind, "annotated"); assert.equal(app.viewer.candidateReplayBinding.kind, "annotated");
  assert.equal(app.viewer.candidateReplayBinding.videoSha256, null);
  assert.equal(app.viewer.poseTimingPreserved, true);
  app.choose("原视频"); assert.equal(app.viewer.videoSrc, original.url);
  assert.equal(app.viewer.candidateReplayBinding.mappingVerified, false);
});

const boundOriginal = () => ({ ...original, pose_time_binding: {
  version: "original-video-decoder-pts-v1", job_id: jobId, source_job_id: original.source_job_id,
  video_sha256: original.sha256, frames_sha256: "b".repeat(64), timestamp_semantics: "source_relative_decoder_pts",
  source_pts_origin_ms: 12000, media_time_origin_ms: 0,
  anchor: { index: 200, processed_index: 0, pts: 500, time_base_num: 1, time_base_den: 25, source_timestamp_ms: 20000, timestamp_ms: 8000 },
} });

test("online server original PTS binding enables pose timing while candidate seeking remains independently locked", async () => {
  const media = boundOriginal(), preview = { job_id: jobId, frames: [{ timestamp_ms: 8040, pose_source: "sam3d", keypoints: [{ name: "left_wrist" }],
    source_binding: { video_sha256: original.sha256, pts: 501, time_base_num: 1, time_base_den: 25 } }] };
  const app = mount({ evidence: { result: { job_id: jobId, original_video: media } }, api: { getPosePreview: async () => preview } });
  assert.equal(app.viewer.poseTimingPreserved, true);
  assert.equal(app.viewer.videoTimeOriginMs, 0); // 8-second analysis start does not become the original media origin.
  assert.equal(app.viewer.candidateReplayBinding.mappingVerified, false);
  assert.equal(app.viewer.candidateReplayBinding.originMs, null);
  assert.equal(await app.viewer.loadPoseWindow(8000, new AbortController().signal), preview);
  for (const change of [{ video_sha256: "f".repeat(64) }, { pts: 502 }]) {
    Object.assign(preview.frames[0].source_binding, change);
    await assert.rejects(app.viewer.loadPoseWindow(8000, new AbortController().signal), /时间来源不一致/);
    preview.frames[0].source_binding = { video_sha256: original.sha256, pts: 501, time_base_num: 1, time_base_den: 25 };
  }
  preview.job_id = "another-job";
  await assert.rejects(app.viewer.loadPoseWindow(8000, new AbortController().signal), /时间来源不一致/);
});

test("SHA alone, stale jobs, wrong PTS origin and imported time claims never authorize server pose timing", () => {
  for (const change of [{ job_id: "another-job" }, { source_job_id: "another-source" }, { video_sha256: "c".repeat(64) },
    { frames_sha256: "bad" }, { source_pts_origin_ms: 0 }, { source_pts_origin_ms: NaN }, { media_time_origin_ms: 8000 },
    { timestamp_semantics: "average_fps" }, { version: "future-version" },
    { anchor: { ...boundOriginal().pose_time_binding.anchor, pts: 501 } },
    { anchor: { ...boundOriginal().pose_time_binding.anchor, time_base_den: 0 } },
    { anchor: { ...boundOriginal().pose_time_binding.anchor, processed_index: 1 } }]) {
    const media = boundOriginal(); Object.assign(media.pose_time_binding, change);
    const app = mount({ evidence: { result: { job_id: jobId, original_video: media } } });
    assert.equal(app.viewer.poseTimingPreserved, false, JSON.stringify(change));
    assert.equal(app.viewer.candidateReplayBinding.mappingVerified, false);
  }
  const result = { job_id: jobId, original_video: boundOriginal() };
  assert.equal(originalPoseTimeBinding(result, jobId, false), null);
  assert.equal(originalPoseTimeBinding(result, "other-job", true), null);
  const imported = mount({ evidence: { candidateSource: undefined, result } });
  assert.equal(imported.viewer.poseTimingPreserved, false);
  assert.equal(imported.viewer.loadPoseWindow, undefined);
});

test("a ready official surface is selected first and the original remains switchable", () => {
  const meshMedia = { video_url: `/v1/jobs/${jobId}/artifacts/sam3d-mesh.mp4`, source_relative_origin_ms: 0 };
  const app = mount({ meshMedia });
  assert.equal(app.mesh.media, meshMedia);
  app.button("原片与关键点").props.onClick(); app.render();
  assert.equal(app.mesh, undefined); assert.equal(app.viewer.videoKind, "original");
  assert.equal(app.viewer.candidateReplayBinding.mappingVerified, false);
  app.button("3D 动作").props.onClick(); app.render();
  assert.equal(app.mesh.media, meshMedia);
});

test("a local upload object URL is preferred even when annotated playback already exists", () => {
  const app = mount({ localVideoSrc: "blob:local-upload" });
  assert.equal(app.viewer.videoSrc, "blob:local-upload"); assert.equal(app.viewer.videoKind, "original");
  assert.equal(app.viewer.poseTimingPreserved, true);
  assert.equal(app.viewer.candidateReplayBinding.videoSha256, null);
  app.choose("标注回放"); assert.equal(app.viewer.videoKind, "annotated");
  app.choose("原视频"); assert.equal(app.viewer.videoSrc, "blob:local-upload");
});

test("an online absolute public URL with the exact job path is replayed only via the local gateway", () => {
  const app = mount({ evidence: { result: { job_id: jobId, original_video: { ...original, url: `https://former-cloud.example${original.url}` } } } });
  assert.equal(app.viewer.videoSrc, original.url);
  assert.equal(app.viewer.videoKind, "original");
  assert.equal(app.viewer.poseTimingPreserved, false);
  assert.equal(app.viewer.candidateReplayBinding.mappingVerified, false);
});

test("absent, malformed, cross-job, remote and imported original claims never become original players", () => {
  const variants = [
    { original_video: null }, { original_video: { ...original, sha256: "bad" } },
    { original_video: { ...original, bytes: 0 } }, { original_video: { ...original, source_job_id: null } },
    { original_video: { ...original, url: "https://other.test/video.mp4" } },
    { original_video: { ...original, url: "/v1/jobs/other-job/original-video" } },
    { job_id: "other-job", original_video: original },
  ];
  for (const fields of variants) {
    const app = mount({ evidence: { result: { job_id: jobId, artifact_urls: { "annotated.mp4": "available" }, ...fields } } });
    assert.equal(app.button("原视频")?.props.disabled ?? true, true);
    assert.equal(app.viewer.videoKind, fields.job_id === "other-job" ? null : "annotated");
    assert.equal(app.swing, undefined);
  }
  const imported = mount({ evidence: { candidateSource: undefined } });
  assert.equal(imported.button("原视频"), undefined); assert.equal(imported.viewer.videoKind, null);
  assert.equal(imported.viewer.videoSrc, null); assert.equal(imported.viewer.loadPoseWindow, undefined); assert.equal(imported.viewer.poster, null);
  assert.equal(imported.swing, undefined);
});

test("an original load error stays explicit until a user switches or retries, never relabeling annotations", () => {
  const app = mount(); app.viewer.onVideoError(); app.render();
  assert.equal(app.viewer.videoSrc, null); assert.equal(app.viewer.videoKind, "original");
  app.choose("标注回放"); assert.equal(app.viewer.videoKind, "annotated"); assert.ok(app.viewer.videoSrc.endsWith("annotated.mp4"));
  app.choose("原视频"); assert.equal(app.viewer.videoSrc, original.url);
});
