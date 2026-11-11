import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import ts from "typescript";

const source = fs.readFileSync(new URL("../app/lib/analysis-progress.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const { analysisProgress } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);

test("near-complete inference stays distinct from later measurement work", () => {
  const before = analysisProgress({ id: "a", status: "running", progress: { phase: "inference", percent: 95.9, processed_frames: 11500, total_frames: 11516 } });
  assert.equal(before.title, "正在识别视频动作");
  assert.equal(before.count, "已识别 11,500 / 11,516 帧");
  assert.equal(before.stagePercent, 11500 / 11516 * 100);
  assert.equal(before.percent, 95.9);
  assert.equal(before.reportStage, false);
  const after = analysisProgress({ id: "a", status: "running", progress: { phase: "extracting_features", percent: 94, completed_items: 106, total_items: 212 } });
  assert.equal(after.title, "正在计算动作测量");
  assert.equal(after.count, "已完成 106 / 212 项");
  assert.equal(after.stagePercent, 50);
  assert.equal(after.reportStage, false);
});

test("report generation and result loading are explicit and never guessed from percent", () => {
  assert.equal(analysisProgress({ id: "a", status: "running", progress: { phase: "inference", percent: 100 } }).reportStage, false);
  assert.equal(analysisProgress({ id: "a", status: "running", progress: { phase: "finalizing", percent: 98 } }).title, "正在生成训练报告");
  assert.equal(analysisProgress({ id: "a", status: "succeeded", progress: { percent: 100 } }).title, "正在读取训练报告");
  const finished = analysisProgress({ id: "a", status: "succeeded", started_at: "2026-10-04T10:00:00Z", completed_at: "2026-10-04T10:02:30Z", updated_at: "2026-10-04T10:02:30Z" }, Date.parse("2026-10-04T12:00:00Z"));
  assert.equal(finished.elapsed, "该任务处理耗时 2 分 30 秒");
  assert.equal(finished.stagePercent, null);
});

test("stale progress is visible without inventing a failed or completed job", () => {
  const job = { id: "a", status: "running", started_at: "2026-10-04T10:00:00Z", updated_at: "2026-10-04T10:07:00Z", progress: { phase: "extracting_features", percent: 94 } };
  const state = analysisProgress(job, Date.parse("2026-10-04T10:09:00Z"));
  assert.equal(state.percent, 94);
  assert.equal(state.stagePercent, null);
  assert.equal(state.elapsed, "服务器已处理 9 分 00 秒");
  assert.match(state.notice, /2 分钟/);
  assert.equal(analysisProgress(job, Date.parse("2026-10-04T10:09:30Z")).notice, state.notice);
  assert.equal(analysisProgress({ ...job, status: "succeeded" }, Date.parse("2026-10-04T10:09:00Z")).notice, "");
});

test("missing and malformed metadata do not produce fabricated counts or times", () => {
  for (const progress of [{ percent: NaN, processed_frames: "500", total_frames: 1000 }, { percent: -4, completed_items: null, total_items: null }]) {
    const state = analysisProgress({ id: "a", status: "running", progress, created_at: "invalid" }, 1);
    assert.equal(state.percent, 0);
    assert.equal(state.stagePercent, null);
    assert.equal(state.count, "");
    assert.equal(state.elapsed, "");
    assert.equal(state.notice, "");
  }
});

test("frame counts define inference progress without changing server metadata", () => {
  const progress = Object.freeze({ phase: "inference", percent: 44, processed_frames: 50, total_frames: 100, completed_items: 9, total_items: 10 });
  const state = analysisProgress({ id: "a", status: "running", progress });
  assert.equal(state.stagePercent, 50);
  assert.equal(state.percent, 44);
  assert.equal(progress.percent, 44);
  assert.equal(state.count, "已识别 50 / 100 帧");
  assert.match(state.detail, /动作识别完成后还需整理报告/);
  const legacy = analysisProgress({ id: "a", status: "running", stage: "inference", progress: 44, processedFrames: 50, totalFrames: 100 });
  assert.equal(legacy.stagePercent, 50);
  assert.equal(legacy.percent, 44);
});

test("a counted stage may finish while later work remains indeterminate", () => {
  const inference = analysisProgress({ id: "a", status: "running", progress: { phase: "inference", percent: 85, processed_frames: 100, total_frames: 100 } });
  assert.equal(inference.stagePercent, 100);
  assert.equal(inference.reportStage, false);
  const replay = analysisProgress({ id: "a", status: "running", progress: { phase: "preparing_replay", percent: 85, processed_frames: 100, total_frames: 100 } });
  assert.equal(replay.stagePercent, null);
  const writing = analysisProgress({ id: "a", status: "running", progress: { phase: "writing_results", percent: 97.5, completed_items: 1, total_items: 2 } });
  assert.equal(writing.stagePercent, 50);
  assert.equal(writing.reportStage, true);
});

test("missing, inconsistent and fractional counts never become an estimated percentage", () => {
  const invalidPairs = [[undefined, 100], [1, 0], [101, 100], [-1, 100], [0.5, 100], [1, 100.5], [1, Infinity], [NaN, 100], ["1", 100], [1, Number.MAX_SAFE_INTEGER + 1]];
  for (const phase of ["inference", "extracting_features"]) {
    for (const [done, total] of invalidPairs) {
      const fields = phase === "inference" ? { processed_frames: done, total_frames: total } : { completed_items: done, total_items: total };
      const state = analysisProgress({ id: "a", status: "running", progress: { phase, percent: 94, ...fields } });
      assert.equal(state.stagePercent, null);
      assert.equal(state.count, "");
    }
  }
  assert.equal(analysisProgress({ id: "a", status: "running", progress: { phase: "inference", processed_frames: 0, total_frames: 100 } }).stagePercent, 0);
});

test("queued, cancelling and terminal jobs do not reuse a stale stage percentage", () => {
  const progress = { phase: "inference", percent: 44, processed_frames: 50, total_frames: 100 };
  for (const status of ["queued", "succeeded", "completed", "failed", "cancelled"]) {
    assert.equal(analysisProgress({ id: "a", status, progress }).stagePercent, null);
  }
  assert.equal(analysisProgress({ id: "a", status: "running", cancel_requested: true, progress }).stagePercent, null);
  assert.equal(analysisProgress(null).stagePercent, null);
});

test("a 33 minute unclaimed job displays queue time and never processing time", () => {
  const created_at = "2026-11-07T09:26:00Z";
  const state = analysisProgress({ id: "a", status: "queued", created_at, updated_at: created_at,
    started_at: null, queue_position: 2, executor: { status: "ready", available: true, heartbeat_at: "2026-11-07T09:59:00Z" },
  }, Date.parse("2026-11-07T09:59:00Z"));
  assert.equal(state.elapsed, "已排队 33 分 00 秒，尚未开始推理");
  assert.equal(state.stepLabel, "等待分析");
  assert.match(state.detail, /等待队列第 2 位（不含正在处理的任务）/);
  assert.match(state.notice, /仍在排队，尚未开始推理/);
  assert.doesNotMatch(JSON.stringify(state), /服务器已处理|未收到新进度|失败|不可用/);
  assert.equal(state.stagePercent, null);
  assert.equal(analysisProgress({ status: "queued", progress: { phase: "finalizing" } }).reportStage, false);
});

test("executor failure, startup and unknown supervision have separate meanings", () => {
  const job = { id: "a", status: "queued", created_at: "2026-11-07T09:26:00Z", updated_at: "2026-11-07T09:26:00Z" };
  const now = Date.parse("2026-11-07T09:59:00Z");
  for (const status of ["unavailable", "stopped"]) {
    const state = analysisProgress({ ...job, executor: { status, available: false, heartbeat_at: null } }, now);
    assert.equal(state.title, "分析执行服务暂不可用");
    assert.match(state.detail, /尚未开始推理/);
    assert.match(state.detail, /视频与任务已保留/);
    assert.equal(state.notice, "");
  }
  for (const status of ["starting", "restarting"]) {
    const state = analysisProgress({ ...job, executor: { status, available: false, heartbeat_at: null } }, now);
    assert.match(state.title, /正在启动分析服务|正在恢复分析服务/);
    assert.doesNotMatch(state.detail, /失败|暂不可用/);
  }
  for (const executor of [undefined, { status: "unmonitored", available: false, heartbeat_at: null }]) {
    const state = analysisProgress({ ...job, executor }, now);
    assert.equal(state.title, "视频已接收，等待分析");
    assert.doesNotMatch(state.detail + state.notice, /不可用|失败|服务在线/);
  }
});

test("a live executor heartbeat is independent of business progress and network reconnects", () => {
  const now = Date.parse("2026-11-07T10:00:00Z");
  const job = { id: "a", status: "running", created_at: "2026-11-07T09:20:00Z", started_at: "2026-11-07T09:55:00Z",
    updated_at: "2026-11-07T09:58:00Z", progress: { phase: "extracting_features" },
    executor: { status: "busy", available: true, heartbeat_at: "2026-11-07T10:00:00Z" } };
  const state = analysisProgress(job, now);
  assert.equal(state.elapsed, "服务器已处理 5 分 00 秒");
  assert.match(state.notice, /执行服务在线.*2 分钟/);
  const disconnected = analysisProgress({ ...job, client_connection: "reconnecting" }, now);
  assert.equal(disconnected.title, "正在重新连接分析服务");
  assert.match(disconnected.detail, /暂时无法读取最新任务状态/);
  assert.equal(disconnected.notice, "");
  assert.equal(disconnected.elapsed, "距本次处理开始 5 分 00 秒");
  assert.doesNotMatch(disconnected.detail, /服务器任务继续运行/);
});

test("terminal jobs stop their clock and missing starts never count queue time as inference", () => {
  const job = { id: "a", status: "cancelled", created_at: "2026-11-07T09:26:00Z", started_at: null, cancelled_at: "2026-11-07T10:00:23Z" };
  assert.equal(analysisProgress(job, Date.parse("2026-11-08T10:00:00Z")).elapsed, "该任务等待耗时 34 分 23 秒");
  assert.equal(analysisProgress({ ...job, status: "running", started_at: undefined }).elapsed, "");
  assert.equal(analysisProgress({ ...job, started_at: undefined }).elapsed, "");
  assert.equal(analysisProgress({ ...job, status: "succeeded", started_at: "2026-11-07T09:55:00Z", completed_at: "2026-11-07T10:00:23Z", cancelled_at: null,
    executor: { status: "unavailable", available: false, heartbeat_at: null } }).title, "正在读取训练报告");
});

test("a safely recovered job is requeued without claiming its earlier processing was queue time", () => {
  const job = { id: "a", status: "queued", created_at: "2026-11-07T09:26:00Z", started_at: "2026-11-07T09:27:00Z",
    updated_at: "2026-11-07T09:58:00Z", executor: { status: "ready", available: true, heartbeat_at: "2026-11-07T10:00:00Z" } };
  const now = Date.parse("2026-11-07T10:00:00Z");
  const state = analysisProgress(job, now);
  assert.equal(state.elapsed, "距提交 34 分 00 秒，等待重新执行");
  assert.match(state.detail + state.notice, /已重新排队/);
  assert.doesNotMatch(JSON.stringify(state), /尚未开始推理|已排队 34/);
  for (const status of ["unavailable", "restarting"]) {
    assert.match(analysisProgress({ ...job, executor: { ...job.executor, status, available: false } }, now).detail, /等待重新执行/);
  }
});
