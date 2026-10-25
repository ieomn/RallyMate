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
    React, AbortSignal, URL, useCallback: value => value, useMemo: compute => compute(), useEffect() {},
    verifiedMeshMedia: () => null,
    useState(initial) { const slot = index++; if (!(slot in state)) state[slot] = initial; return [state[slot], value => { state[slot] = value; }]; },
    BallTrajectoryViewer: "viewer", BallTrajectorySummary: "summary",
    poseReplayTimingAllowed: (local, result) => local || result?.runtime?.annotated_video_compatibility?.timing_preserved === true,
  });
  const app = {
    render() { index = 0; tree = renderPanel(props); },
    get viewer() { return nodes(tree).find(node => node.type === "viewer").props; },
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
  app.choose("标注回放");
  assert.equal(app.viewer.videoSrc, `/v1/jobs/${jobId}/artifacts/annotated.mp4`);
  assert.equal(app.viewer.videoKind, "annotated"); assert.equal(app.viewer.candidateReplayBinding.kind, "annotated");
  assert.equal(app.viewer.candidateReplayBinding.videoSha256, null);
  assert.equal(app.viewer.poseTimingPreserved, true);
  app.choose("原视频"); assert.equal(app.viewer.videoSrc, original.url);
  assert.equal(app.viewer.candidateReplayBinding.mappingVerified, false);
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
  }
  const imported = mount({ evidence: { candidateSource: undefined } });
  assert.equal(imported.button("原视频"), undefined); assert.equal(imported.viewer.videoKind, null);
  assert.equal(imported.viewer.videoSrc, null); assert.equal(imported.viewer.loadPoseWindow, undefined); assert.equal(imported.viewer.poster, null);
});

test("an original load error stays explicit until a user switches or retries, never relabeling annotations", () => {
  const app = mount(); app.viewer.onVideoError(); app.render();
  assert.equal(app.viewer.videoSrc, null); assert.equal(app.viewer.videoKind, "original");
  app.choose("标注回放"); assert.equal(app.viewer.videoKind, "annotated"); assert.ok(app.viewer.videoSrc.endsWith("annotated.mp4"));
  app.choose("原视频"); assert.equal(app.viewer.videoSrc, original.url);
});
