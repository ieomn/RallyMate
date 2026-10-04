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
  assert.equal(before.reportStage, false);
  const after = analysisProgress({ id: "a", status: "running", progress: { phase: "extracting_features", percent: 94, completed_items: 106, total_items: 212 } });
  assert.equal(after.title, "正在计算动作测量");
  assert.equal(after.count, "已完成 106 / 212 项");
  assert.equal(after.reportStage, false);
});

test("report generation and result loading are explicit and never guessed from percent", () => {
  assert.equal(analysisProgress({ id: "a", status: "running", progress: { phase: "inference", percent: 100 } }).reportStage, false);
  assert.equal(analysisProgress({ id: "a", status: "running", progress: { phase: "finalizing", percent: 98 } }).title, "正在生成训练报告");
  assert.equal(analysisProgress({ id: "a", status: "succeeded", progress: { percent: 100 } }).title, "正在读取训练报告");
  const finished = analysisProgress({ id: "a", status: "succeeded", started_at: "2026-10-04T10:00:00Z", completed_at: "2026-10-04T10:02:30Z", updated_at: "2026-10-04T10:02:30Z" }, Date.parse("2026-10-04T12:00:00Z"));
  assert.equal(finished.elapsed, "已用时 2 分 30 秒");
});

test("stale progress is visible without inventing a failed or completed job", () => {
  const job = { id: "a", status: "running", started_at: "2026-10-04T10:00:00Z", updated_at: "2026-10-04T10:07:00Z", progress: { phase: "extracting_features", percent: 94 } };
  const state = analysisProgress(job, Date.parse("2026-10-04T10:09:00Z"));
  assert.equal(state.percent, 94);
  assert.equal(state.elapsed, "已用时 9 分 00 秒");
  assert.match(state.notice, /2 分钟/);
  assert.equal(analysisProgress(job, Date.parse("2026-10-04T10:09:30Z")).notice, state.notice);
  assert.equal(analysisProgress({ ...job, status: "succeeded" }, Date.parse("2026-10-04T10:09:00Z")).notice, "");
});

test("missing and malformed metadata do not produce fabricated counts or times", () => {
  for (const progress of [{ percent: NaN, processed_frames: "500", total_frames: 1000 }, { percent: -4, completed_items: null, total_items: null }]) {
    const state = analysisProgress({ id: "a", status: "running", progress, created_at: "invalid" }, 1);
    assert.equal(state.percent, 0);
    assert.equal(state.count, "");
    assert.equal(state.elapsed, "");
    assert.equal(state.notice, "");
  }
});
