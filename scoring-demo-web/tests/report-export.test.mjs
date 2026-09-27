import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import ts from "typescript";

const source = fs.readFileSync(new URL("../app/lib/report-export.ts", import.meta.url), "utf8");
const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const { getReportExportGate, buildPracticeReport, renderReportMarkdown, renderReportHtml, buildReportBackup, reportDownloadName } = await import(`data:text/javascript;base64,${Buffer.from(js).toString("base64")}`);

function completed() {
  return {
    mode: "live", uploadState: "complete", job: { id: "job-12345678", status: "completed", original_filename: "发球练习.mp4" },
    evidence: {
      jobId: "job-12345678", error: null,
      result: { job_id: "job-12345678", training_evaluation: { score_0_to_100: null, summary_zh: "有可复核的挥拍候选。", indicator_evaluations: [{ indicator_id: "balance", name_zh: "身体平衡", score_0_to_100: 71, observation_zh: "收拍后出现移动。", suggestion_zh: "低强度复核。", limitations_zh: ["二维姿态代理。"] }] }, actions: [{ family: "footwork", event_code: "FS10", name_zh: "回位", detected_segments: 3 }] },
      assessment: { job_id: "job-12345678", overall_evidence_score_0_to_100: 45, formal_score_available: false, formal_score_message_zh: "没有正式技术等级。", techniques: [{ technique_id: "serve", family: "serve", name_zh: "发球", observed: false, status: "not_observed", evidence_score_0_to_100: 0, limitations_zh: ["缺少触球依据。"] }] },
      trajectory: { job_id: "job-12345678", status: "ready", source: { frames_path: "/root/autodl-tmp/rallymate/service_data/runs/private/frames.jsonl", frame_count: 300, width: 1920, height: 1080 }, ball: { observed_count: 17, coverage_fraction: 0.2, confidence: { mean: 0.7 }, prediction_status: "heuristic_preview", reconstruction: { summary: { observed_count: 17, interpolated_count: 4, segment_count: 2, coverage_fraction: .2 }, segments: [{ segment_id: 1, start_ms: 1100, end_ms: 1800, observed_count: 9, interpolated_count: 2 }], limitations_zh: ["不推断真实落点。"] } }, racket: { observed_count: 20 }, limitations: [] },
    },
    summary: { job_id: "job-12345678", input: { video_path: "/root/private/source.mp4", video: { duration_ms: 12000, width: 1920, height: 1080, frame_count: 300 } }, processing: { processed_frames: 300, elapsed_seconds: 30 }, action_recognition: { status: "candidates_detected", confirmed_contact_count: null, candidates: [{ family: "serve", start_ms: 1200, peak_ms: 1900, end_ms: 2500, contact_confirmed: false, evidence: { racket_associated_frames: 7, pose_samples: 12 }, limitations_zh: ["抛球未确认。"] }, { family: "baseline", start_ms: 4200, peak_ms: 4500, end_ms: 4800, evidence: {} }], limitations_zh: ["仅候选时间区间。"] } },
  };
}

test("pending, failed, stale-task and empty result exports fail closed even with leftover data", () => {
  for (const overrides of [
    { mode: "live-pending", uploadState: "processing" },
    { uploadState: "error" },
    { uploadState: "ready" },
    { job: { id: "job-12345678", status: "running" } },
    { job: { id: "job-12345678", status: "failed" } },
    { job: { id: "other-12345678", status: "completed" } },
  ]) {
    const input = { ...completed(), ...overrides };
    assert.equal(getReportExportGate(input).allowed, false);
    assert.throws(() => buildPracticeReport(input));
    assert.throws(() => buildReportBackup(input));
  }
  const empty = { ...completed(), summary: null, evidence: { jobId: "job-12345678", result: { job_id: "job-12345678" } } };
  assert.equal(getReportExportGate(empty).allowed, false);
  assert.throws(() => buildPracticeReport(empty), /空报告/);
});

test("a completed task exports readable observations, actual video metadata, candidate intervals and trajectory limits", () => {
  const report = buildPracticeReport(completed(), "2026-09-24T12:00:00Z");
  const md = renderReportMarkdown(report);
  assert.match(md, /发球练习/);
  assert.match(md, /1920 × 1080/);
  assert.match(md, /12 秒/);
  assert.match(md, /发球动作候选/);
  assert.match(md, /底线挥拍候选/);
  assert.match(md, /1\\\.2–2\\\.5 秒/);
  assert.match(md, /关联球拍帧数：7/);
  assert.match(md, /身体平衡/);
  assert.match(md, /71 \/ 100/);
  assert.match(md, /未确认触球/);
  assert.match(md, /不代表真实落点/);
  assert.match(md, /不是动作得分/);
  assert.equal(reportDownloadName(report, "html"), "rallymate-analysis-job-12345678.html");
  assert.doesNotMatch(md, /live-pending|\/root\/|frames\.jsonl/);
});

test("motion report exports measured stages and explicit return limitations without legacy candidate clutter", () => {
  const input = completed();
  input.evidence.result.action_recognition = { motion_analysis: { schema_version: "1.0.0", contact_confirmed: false, families: { baseline: { reason_zh: "已提供运动测量", episodes: [{ episode_id: "x", family: "baseline", contact_confirmed: false, start_ms: 1000, end_ms: 2200, classification: { label_zh: "正手挥拍", status: "rule_inferred", reason_zh: "持拍手明确。" }, phases: [{ label_zh: "加速", start_ms: 1200, end_ms: 1800 }], metrics: { peak_wrist_speed_torso_per_s: 3.2, elbow_extension_deg: 40 }, limitations_zh: ["类型尚未经过专项准确率验证。"] }] }, return: { reason_zh: "缺少对手发球与来球顺序。", episodes: [] } } } };
  const report = buildPracticeReport(input);
  const html = renderReportHtml(report);
  assert.match(html, /底线运动分析/);
  assert.match(html, /正手挥拍/);
  assert.match(html, /手腕峰值速度（躯干长度\/秒）：3\.2/);
  assert.match(html, /缺少对手发球与来球顺序/);
  assert.match(html, /规则推断参考/);
  assert.doesNotMatch(html, /发球与底线候选证据区间/);
  const episode = input.evidence.result.action_recognition.motion_analysis.families.baseline.episodes[0];
  episode.analysis_status = "partial";
  episode.phases.push({ phase: "follow_through", label_zh: "随挥", status: "unavailable", start_ms: null, end_ms: null });
  episode.metric_notes_zh = ["随挥降速证据不足。"];
  const partialHtml = renderReportHtml(buildPracticeReport(input));
  assert.match(partialHtml, /阶段证据不完整/);
  assert.match(partialHtml, /随挥证据不足/);
  assert.match(partialHtml, /随挥降速证据不足/);
  assert.match(partialHtml, /不代表触球时刻/);
  assert.doesNotMatch(partialHtml, /随挥 未提供有效时间区间/);
});

test("a successful result with missing optional evidence stays exportable and discloses the missing evidence", () => {
  const input = completed();
  input.evidence.trajectory = null;
  input.evidence.error = "轨迹接口暂时不可用";
  input.summary.action_recognition = { status: "no_candidates", candidates: [] };
  assert.equal(getReportExportGate(input).allowed, true);
  const md = renderReportMarkdown(buildPracticeReport(input));
  assert.match(md, /轨迹接口未返回数据/);
  assert.match(md, /不能据此认定视频里没有击球/);
  assert.match(md, /轨迹接口暂时不可用/);
});

test("backend footwork event codes supply missing families without guessing unknown action names", () => {
  const input = completed();
  input.evidence.result.actions = [
    { event_code: "FS01", name_zh: "准备与分腿垫步", detected_segments: 2 },
    { event_code: "FS02", name_zh: "第一步启动", detected_segments: 2 },
    { event_code: "FS09", name_zh: "制动与重新稳定", detected_segments: 2 },
    { event_code: "FS99", name_zh: "发球", detected_segments: 1 },
  ];
  const rows = buildPracticeReport(input).sections.find(section => section.title === "本次动作观察").rows;
  assert.deepEqual(rows.slice(0, 3).map(row => row[1]), ["步伐", "步伐", "步伐"]);
  assert.equal(rows[3][1], "未分类");
});

test("a serve recognition candidate takes precedence over legacy not_observed without inventing readiness", () => {
  const input = completed();
  Object.assign(input.evidence.assessment.techniques[0], { status: "not_observed", observed: false, recognition_status: "candidate", candidate_count: 2 });
  const report = buildPracticeReport(input);
  const row = report.sections.find(section => section.title === "技术证据覆盖").rows[0];
  assert.equal(row[0], "发球");
  assert.equal(row[2], "候选（未确认触球）");
  assert.equal(row[3], "未提供");
  assert.match(renderReportMarkdown(report), /发球 \| 发球 \| 候选（未确认触球）/);
  assert.match(renderReportHtml(report), /<td>候选（未确认触球）<\/td>/);
});

test("HTML is self-contained, printable and treats imported strings as text, never executable markup", () => {
  const input = completed();
  input.evidence.result.training_evaluation.summary_zh = '<script src="https://evil.example/x.js">alert(1)</script><img src=x onerror=alert(1)>';
  input.evidence.result.training_evaluation.indicator_evaluations[0].name_zh = '[偷取](https://evil.example/x) <svg onload=alert(2)> | x';
  const report = buildPracticeReport(input);
  const html = renderReportHtml(report);
  assert.match(html, /<!doctype html>/);
  assert.match(html, /Content-Security-Policy/);
  assert.match(html, /script-src 'none'/);
  assert.match(html, /@media print/);
  assert.match(html, /&lt;script/);
  assert.doesNotMatch(html, /<script\b|<img\b|<svg\b|<iframe\b|<link\b|https:\/\/evil/i);
  assert.doesNotMatch(renderReportMarkdown(report), /(?<!\\)\[偷取\]\(|<svg\b|<script\b/);
});

test("readable reports and compatible JSON backup omit server paths, credentials and transport configuration", () => {
  const input = completed();
  input.evidence.result.api_key = "DO_NOT_EXPORT_KEY";
  input.evidence.result.artifact_urls = { private: "http://127.0.0.1/private" };
  input.evidence.assessment.secret = "DO_NOT_EXPORT_SECRET";
  input.summary.input.upstream_metadata = { original_filename: "original.mp4", token: "DO_NOT_EXPORT_TOKEN" };
  input.evidence.result.training_evaluation.summary_zh = '文件 /root/autodl-tmp/private/a.mp4 与 C:\\Users\\Private\\a.mp4；RALLYMATE_API_KEY=KEY_VALUE Bearer BEARER_VALUE';
  const report = buildPracticeReport(input);
  const backup = buildReportBackup(input);
  const combined = renderReportHtml(report) + renderReportMarkdown(report) + JSON.stringify(backup);
  assert.doesNotMatch(combined, /DO_NOT_EXPORT|KEY_VALUE|BEARER_VALUE|\/root\/|C:\\\\Users|127\.0\.0\.1|frames_path|video_path|artifact_urls/);
  assert.equal(backup.schemaVersion, "rallymate-practice-report/1");
  assert.equal(backup.jobId, "job-12345678");
  assert.equal(backup.result.training_evaluation.indicator_evaluations[0].score_0_to_100, 71);
  assert.equal(backup.assessment.techniques[0].name_zh, "发球");
  assert.equal(backup.summary.action_recognition.candidates[0].contact_confirmed, false);
  assert.equal(backup.trajectory.source.frame_count, 300);
});

test("offline demo export is explicitly labelled and cannot stand in for a live analysis", () => {
  const demo = { scenario: { id: "demo-one", mode: "demo", label: "示例", description: "模拟展示", caveat: "并非视频测量" }, results: [{ card: { name: "平衡" }, score: 80, status: "scored", verdict: "模拟解释" }] };
  const input = { mode: "demo", uploadState: "idle", evidence: {}, demoReport: demo };
  const report = buildPracticeReport(input);
  assert.equal(report.isDemo, true);
  assert.equal(report.jobId, null);
  assert.match(renderReportHtml(report), /DEMO · 模拟数据 · 不是用户视频分析/);
  assert.match(reportDownloadName(report, "md"), /^rallymate-demo-/);
  assert.equal(getReportExportGate({ ...input, mode: "live", uploadState: "complete" }).allowed, false);
  assert.equal(getReportExportGate({ ...input, demoReport: { ...demo, scenario: { ...demo.scenario, id: "live-pending" } } }).allowed, false);
});
