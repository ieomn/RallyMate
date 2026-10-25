import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import fs from "node:fs";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

function compiledUrl(fileUrl) {
  // Keep source JSON imports shared with production, without a generated copy.
  if (fileUrl.pathname.endsWith(".json")) return `data:text/javascript;base64,${Buffer.from(`export default ${fs.readFileSync(fileUrl, "utf8")}`).toString("base64")}`;
  let js = ts.transpileModule(fs.readFileSync(fileUrl, "utf8"), { compilerOptions: {
    module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX,
  } }).outputText;
  js = js.replace(/from "([^"]+)"/g, (_match, name) => {
    const local = new URL(name.endsWith(".json") ? name : `${name}.ts`, fileUrl);
    return `from ${JSON.stringify(name.startsWith(".") ? compiledUrl(fs.existsSync(local) ? local : new URL(`${name}.tsx`, fileUrl)) : import.meta.resolve(name))}`;
  });
  return `data:text/javascript;base64,${Buffer.from(js).toString("base64")}`;
}
const { default: Panel, createFootCrossingPanelController, canReplayFootCrossing, footCrossingCandidateKey, footCrossingParentSourceNotes, FootCrossingCoverageSummary } =
  await import(compiledUrl(new URL("../app/FootCrossingCandidatesPanel.tsx", import.meta.url)));
const sha = "a".repeat(64);
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; };
function candidate(frame = 170) {
  return { source_frames: [frame, frame + 1], processed_indexes: [frame, frame + 1],
    source_bindings: [frame, frame + 1].map(index => ({ frame_index: index, processed_index: index,
      video_sha256: sha, pts: index * 20, time_base_num: 1, time_base_den: 600,
      video_timestamp_ms: Math.round(index * 1000 / 30), source_timestamp_ms: index * 1000 / 30 })),
    source_interval_ms: [frame, frame + 1].map(index => ({ numerator: index * 100, denominator: 3, approx_ms: index * 100 / 3 })),
    signed_ankle_order_px: [-8, 9], candidate_quality_status: "unvalidated",
    parent_event_count: 0, parent_source_association_counts: {},
    parent_source_association_policy: "existing_source_conflicts_excluded;legacy_track_only_association_is_weak_not_fully_verified",
    axis: { unit_vector: [1, 0], hip_displacement_length_px: 2, numeric_resolution_floor_px: 1e-12 },
    local_joint_diagnostics: { participating_joints: ["left_hip", "right_hip", "left_ankle", "right_ankle"],
      processed_to_source: [frame, frame + 1].map(index => ({ processed_index: index, source_frame_index: index })),
      keypoint_jump_candidate_frames_by_joint: {}, left_right_swap_candidate_joint_pairs: [] } };
}
function available(job = "job-test") {
  return { schema_version: "foot-crossing-candidates-response-v1", job_id: job, status: "available",
    reason_code: "available_candidates", identity: { video_sha256: sha, artifact_sha256: "b".repeat(64),
      manifest_sha256: "c".repeat(64), input_bundle_sha256: "d".repeat(64), job_summary_sha256: "e".repeat(64),
      source_job_id: "native-source", video_id: "native-video", algorithm_version: "projected-ankle-order-candidates-v1.0.1",
      artifact_version: "foot-crossing-candidates-artifact-v1.0.1", input_binding_mode: "verified_publication_snapshot" },
    counts: { candidates: 2 }, output_limits: { candidates_truncated: false }, candidates: [candidate(), candidate(175)],
    scoring_allowed: false, formal_step_verified: false, technical_grade: null };
}
function context(load, values = {}) {
  return { jobId: "job-test", sessionKey: "session-1", signal: new AbortController().signal,
    load, replay: null, ...values };
}
function replay() {
  const calls = [];
  return { calls, binding: { jobId: "job-test", sessionKey: "session-1", mediaKey: "media-1", kind: "annotated",
    videoSha256: sha, originMs: 0, mappingVerified: true },
    seek(request, signal) { calls.push({ request, signal }); return { status: "pending" }; }, cancel() {} };
}

test("default collapsed and offline rendering make no GET", async () => {
  let calls = 0;
  const ctx = context(async () => { calls++; return available(); });
  const html = renderToStaticMarkup(createElement(Panel, { context: ctx }));
  assert.match(html, /aria-expanded="false"/);
  assert.doesNotMatch(html, /原帧 170|原始候选/);
  assert.equal(calls, 0);
  const offline = createFootCrossingPanelController();
  await offline.open();
  assert.equal(offline.getSnapshot().phase, "offline");
  assert.equal(offline.getSnapshot().response, null);
  assert.equal(calls, 0);
  assert.match(renderToStaticMarkup(createElement(Panel)), /需要打开对应的在线报告/);
  offline.dispose();
});

test("one GET per expansion, close clears evidence, reopening reads anew", async () => {
  let calls = 0;
  const pending = deferred();
  const controller = createFootCrossingPanelController(context(() => { calls++; return pending.promise; }));
  assert.equal(calls, 0);
  const opened = controller.open();
  assert.equal(controller.getSnapshot().phase, "loading");
  await controller.open();
  assert.equal(calls, 1);
  pending.resolve(available()); await opened;
  assert.equal(controller.getSnapshot().phase, "ready");
  controller.select(1);
  assert.equal(controller.getSnapshot().selected, 1);
  controller.close();
  assert.equal(controller.getSnapshot().response, null);
  assert.equal(controller.getSnapshot().selected, 0);
  await controller.open();
  assert.equal(calls, 2);
  controller.dispose();
});

test("late GET after close cannot overwrite a reopened response even if transport ignores abort", async () => {
  const first = deferred(), second = deferred(), signals = [];
  const controller = createFootCrossingPanelController(context(signal => {
    signals.push(signal); return signals.length === 1 ? first.promise : second.promise;
  }));
  const oldRead = controller.open();
  controller.close();
  assert.equal(signals[0].aborted, true);
  const newRead = controller.open();
  const current = available(); current.identity.artifact_sha256 = "f".repeat(64);
  second.resolve(current); await newRead;
  first.resolve(available()); await oldRead;
  assert.equal(controller.getSnapshot().response, current);
  controller.dispose();
});

test("report generation and disposal abort reads; stale results cannot leak across same job", async () => {
  for (const action of ["session", "signal", "dispose", "parent-abort"]) {
    const pending = deferred(), report = new AbortController(); let requestSignal;
    const ctx = context(signal => { requestSignal = signal; return pending.promise; }, { signal: report.signal });
    const controller = createFootCrossingPanelController(ctx);
    const read = controller.open();
    if (action === "session") controller.setContext({ ...ctx, sessionKey: "session-2" });
    else if (action === "signal") controller.setContext({ ...ctx, signal: new AbortController().signal });
    else if (action === "dispose") controller.dispose();
    else report.abort();
    assert.equal(requestSignal.aborted, true, action);
    pending.resolve(available()); await read;
    assert.equal(controller.getSnapshot().response, null, action);
    assert.equal(controller.getSnapshot().open, false, action);
    controller.dispose();
  }
});

test("unavailable and request errors remain distinct from an available zero", async () => {
  const unavailable = { ...available(), status: "not_computed", identity: null, counts: null, output_limits: null, candidates: [] };
  const controller = createFootCrossingPanelController(context(async () => unavailable));
  await controller.open();
  assert.equal(controller.getSnapshot().response.status, "not_computed");
  assert.equal(controller.getSnapshot().response.counts, null);
  controller.close();
  controller.setContext(context(async () => { throw new Error("private server detail"); }));
  await controller.open();
  assert.equal(controller.getSnapshot().phase, "error");
  assert.equal(controller.getSnapshot().response, null);
  assert.doesNotMatch(controller.getSnapshot().message, /private server detail/);
  controller.close();
  controller.setContext(context(async () => available("another-job")));
  await controller.open();
  assert.equal(controller.getSnapshot().phase, "error");
  assert.equal(controller.getSnapshot().response, null);
  controller.dispose();
});

test("replay needs verified mapping and matching report, session and source SHA", () => {
  const handle = replay(), response = available(), row = response.candidates[0];
  const ctx = context(async () => response, { replay: handle });
  assert.equal(canReplayFootCrossing(ctx, response, row), true);
  for (const fields of [{ mappingVerified: false }, { originMs: null }, { originMs: NaN },
    { videoSha256: null }, { videoSha256: "f".repeat(64) }, { jobId: "other-job" }, { sessionKey: "other-session" }]) {
    assert.equal(canReplayFootCrossing({ ...ctx, replay: { ...handle, binding: { ...handle.binding, ...fields } } }, response, row), false);
  }
  const wrong = structuredClone(row); wrong.source_bindings[1].video_sha256 = "f".repeat(64);
  assert.equal(canReplayFootCrossing(ctx, response, wrong), false);
  assert.equal(canReplayFootCrossing(ctx, available("other-job"), row), false);
});

test("selection, closing, replay replacement and report abort synchronously cancel pending seek", async () => {
  for (const action of ["selection", "close", "replay", "report", "dispose"]) {
    const handle = replay(), report = new AbortController(); let reads = 0;
    const ctx = context(async () => { reads++; return available(); }, { signal: report.signal, replay: handle });
    const controller = createFootCrossingPanelController(ctx);
    await controller.open(); controller.seek();
    assert.equal(handle.calls.length, 1, action);
    const call = handle.calls[0];
    assert.equal(call.request.jobId, ctx.jobId);
    assert.equal(call.request.sessionKey, ctx.sessionKey);
    assert.equal(call.request.videoSha256, sha);
    assert.equal(call.request.videoTimestampMs, 5667);
    assert.equal(call.request.candidateKey, footCrossingCandidateKey(available().candidates[0]));
    assert.ok(call.request.identityKey);
    assert.equal(call.signal.aborted, false);
    if (action === "selection") controller.select(1);
    else if (action === "close") controller.close();
    else if (action === "replay") controller.setContext({ ...ctx, replay: replay() });
    else if (action === "report") report.abort();
    else controller.dispose();
    assert.equal(call.signal.aborted, true, action);
    assert.equal(reads, 1, action);
    controller.dispose();
  }
});

test("unverified media cannot seek, and handle changes do not refetch candidates", async () => {
  const handle = replay(); handle.binding.mappingVerified = false; handle.binding.originMs = null;
  let reads = 0;
  const ctx = context(async () => { reads++; return available(); }, { replay: handle });
  const controller = createFootCrossingPanelController(ctx);
  await controller.open(); controller.seek();
  assert.equal(handle.calls.length, 0);
  controller.setContext({ ...ctx, replay: replay(), load: async () => { throw new Error("must not refetch"); } });
  assert.equal(controller.getSnapshot().phase, "ready");
  assert.equal(reads, 1);
  controller.dispose();
});

test("new artifact identity in the same report never inherits an earlier pending seek", async () => {
  const first = available(), next = available(); next.identity.artifact_sha256 = "f".repeat(64);
  const handle = replay(); let reads = 0;
  const ctx = context(async () => ++reads === 1 ? first : next, { replay: handle });
  const controller = createFootCrossingPanelController(ctx);
  await controller.open(); controller.seek();
  const previous = handle.calls[0];
  controller.close();
  assert.equal(previous.signal.aborted, true);
  await controller.open(); controller.seek();
  assert.equal(reads, 2);
  assert.notEqual(handle.calls[1].request.identityKey, previous.request.identityKey);
  assert.equal(handle.calls[1].signal.aborted, false);
  assert.equal(controller.getSnapshot().selected, 0);
  controller.dispose();
  assert.equal(handle.calls[1].signal.aborted, true);
});

test("parent source notes distinguish weak RTM evidence, excluded conflicts and source consistency", () => {
  const row = candidate();
  row.candidate_quality_status = "suspect";
  row.parent_event_count = 1;
  row.parent_source_association_counts = { weak_source_track_association: 1 };
  row.local_joint_diagnostics.keypoint_jump_candidate_frames_by_joint = { right_ankle: [171] };
  const original = structuredClone(row);
  const weak = footCrossingParentSourceNotes(row).join("");
  assert.match(weak, /1 个父事件仅弱来源关联/);
  assert.match(weak, /局部疑点供复核，未完成双帧来源验证/);
  assert.deepEqual(row, original, "wording must not change the candidate or its local risk");
  row.parent_source_association_counts = { mismatch: 1 };
  assert.match(footCrossingParentSourceNotes(row).join(""), /已排除其否决和疑点判定作用；当前候选仍保留/);
  row.parent_source_association_counts = { unverified: 1 };
  assert.match(footCrossingParentSourceNotes(row).join(""), /缺少可用来源关联，未用于否决或疑点判定/);
  row.parent_source_association_counts = { pair_source_bindings_verified: 1 };
  assert.match(footCrossingParentSourceNotes(row).join(""), /来源绑定核对一致；这不证明实际身份或动作正确/);
  assert.deepEqual(footCrossingParentSourceNotes(candidate()), []);
});

// One exact published response; all modified count scenarios below are synthetic
// presentation fixtures, not further published results or full-video conclusions.
const real97AllExcludedBytes = fs.readFileSync(new URL("./fixtures/foot-crossing-real97-all-excluded-response.json", import.meta.url));
const real97AllExcluded = JSON.parse(real97AllExcludedBytes.toString("utf8"));
const { parseFootCrossingResponse } = await import(compiledUrl(new URL("../app/lib/foot-crossing-candidates.ts", import.meta.url)));
function syntheticCoverage({ frames, candidates = 0, noExchange = 0, excluded = 0, reasons, rows = [], quality = {} }) {
  const response = structuredClone(real97AllExcluded);
  response.reason_code = candidates ? "available_candidates" : "available_zero_candidates";
  response.counts = { frames, adjacent_sequence_pairs: Math.max(0, frames - 1), candidates,
    no_exchange: noExchange, excluded, candidate_quality: quality, reasons };
  response.candidates = rows;
  response.output_limits = { ...response.output_limits, candidate_limit: rows.length,
    candidates_truncated: candidates > rows.length, exclusions_truncated: excluded > response.output_limits.exclusion_limit,
    stored_candidate_count: rows.length, total_candidate_count: candidates };
  return response;
}
function renderCoverageWithoutMutation(response) {
  const before = structuredClone(response);
  const parsed = parseFootCrossingResponse(response, response.job_id);
  const html = renderToStaticMarkup(createElement(FootCrossingCoverageSummary, { response: parsed }));
  assert.deepEqual(response, before, "presentation must not mutate original counts, risk or scoring flags");
  assert.deepEqual(parsed, before, "rendered response must preserve every saved field");
  assert.equal(parsed.scoring_allowed, false);
  assert.equal(parsed.formal_step_verified, false);
  assert.equal(parsed.technical_grade, null);
  return html;
}

test("coverage display: actual real97 all-excluded result is unable to judge", () => {
  assert.equal(real97AllExcludedBytes.length, 4432);
  assert.equal(createHash("sha256").update(real97AllExcludedBytes).digest("hex"),
    "78764f4e04b91cccbad3d6ffa39314c6e94bb69da2198ad1fdbe195b981cdf0d");
  const html = renderCoverageWithoutMutation(real97AllExcluded);
  assert.match(html, /本次无法判断图像换序/);
  assert.match(html, /所有相邻帧对均被排除/);
  assert.match(html, /不能据此认定没有实际交叉步/);
  assert.match(html, /帧数 2911/);
  assert.match(html, /相邻帧对 2910 对/);
  assert.match(html, /可判定帧对 0 对/);
  assert.match(html, /未换序 0 对/);
  assert.match(html, /排除 2910 对/);
  assert.match(html, /原帧与视频时间绑定不可用（source_time_binding_unavailable）：2910 对/);
  assert.doesNotMatch(html, /已保存计算中没有换序候选|可判定部分未发现|PTS.*缺失/);
});

test("coverage display: genuine no-exchange zero applies only to assessable pairs", () => {
  const html = renderCoverageWithoutMutation(syntheticCoverage({ frames: 6, noExchange: 5,
    reasons: { same_projected_order: 5 } }));
  assert.match(html, /可判定帧对 5 对/);
  assert.match(html, /未换序 5 对/);
  assert.match(html, /排除 0 对/);
  assert.match(html, /可判定部分未发现换序候选；不能据此认定没有实际交叉步/);
  assert.doesNotMatch(html, /本次无法判断|排除原因|未保存可展开/);
});

test("coverage display: partial exclusion retains reason counts alongside assessable zero", () => {
  const html = renderCoverageWithoutMutation(syntheticCoverage({ frames: 7, noExchange: 4, excluded: 2,
    reasons: { same_projected_order: 4, source_time_binding_unavailable: 1, future_exclusion_reason: 1 } }));
  assert.match(html, /可判定帧对 4 对/);
  assert.match(html, /排除 2 对/);
  assert.match(html, /可判定部分未发现换序候选/);
  assert.match(html, /排除部分未参与图像换序判断/);
  assert.match(html, /原帧与视频时间绑定不可用（source_time_binding_unavailable）：1 对/);
  assert.match(html, /其他排除原因（future_exclusion_reason）：1 对/);
  assert.doesNotMatch(html, /本次无法判断|same_projected_order/);
});

test("coverage display: no adjacent pairs cannot be interpreted as no exchange", () => {
  for (const frames of [0, 1]) {
    const html = renderCoverageWithoutMutation(syntheticCoverage({ frames, reasons: {} }));
    assert.match(html, /本次无法判断图像换序：没有相邻帧对/);
    assert.match(html, /相邻帧对 0 对/);
    assert.match(html, /可判定帧对 0 对/);
    assert.doesNotMatch(html, /可判定部分未发现|所有相邻帧对均被排除|排除原因/);
  }
});

test("coverage display: nonzero candidates with zero retention keep missing-detail semantics", () => {
  const html = renderCoverageWithoutMutation(syntheticCoverage({ frames: 3, candidates: 2,
    reasons: { strict_projected_order_exchange: 2 }, quality: { unvalidated: 2 } }));
  assert.match(html, /可判定帧对 2 对/);
  assert.match(html, /已保存 0 条 \/ 原始候选 2 条/);
  assert.match(html, /按原帧顺序截断/);
  assert.match(html, /此工件未保存可展开的候选明细/);
  assert.doesNotMatch(html, /本次无法判断|未发现换序候选/);
});

test("coverage display: retained suspect candidate and partial exclusions preserve saved local risk", () => {
  const savedPair = JSON.parse(fs.readFileSync(new URL("./fixtures/foot-crossing-real-pair-projection.json", import.meta.url), "utf8"));
  const response = syntheticCoverage({ frames: 4, candidates: 1, noExchange: 1, excluded: 1,
    reasons: { strict_projected_order_exchange: 1, same_projected_order: 1, source_time_binding_unavailable: 1 },
    rows: [structuredClone(savedPair.candidate)], quality: { suspect: 1 } });
  const html = renderCoverageWithoutMutation(response);
  assert.match(html, /可判定帧对 2 对/);
  assert.match(html, /已保存 1 条 \/ 原始候选 1 条/);
  assert.match(html, /原帧与视频时间绑定不可用（source_time_binding_unavailable）：1 对/);
  assert.doesNotMatch(html, /本次无法判断|未发现换序候选|未保存可展开/);
  assert.deepEqual(response.candidates[0], savedPair.candidate);
});
