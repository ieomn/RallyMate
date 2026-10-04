import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import ts from "typescript";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

function compiledUrl(fileUrl) {
  let js = ts.transpileModule(fs.readFileSync(fileUrl, "utf8"), { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  js = js.replace(/^import\s+["'][^"']+\.css["'];?\s*$/gm, "");
  js = js.replace(/import (\w+) from "([^"\n]+\.json)";?/g, (_match, binding, name) => `const ${binding} = ${fs.readFileSync(new URL(name, fileUrl), "utf8")};`);
  js = js.replace(/from "([^"]+)"/g, (_match, name) => {
    const local = new URL(`${name}.ts`, fileUrl);
    return `from ${JSON.stringify(name.startsWith(".") ? compiledUrl(fs.existsSync(local) ? local : new URL(`${name}.tsx`, fileUrl)) : import.meta.resolve(name))}`;
  });
  return `data:text/javascript;base64,${Buffer.from(js).toString("base64")}`;
}
const { analysisReportOf, reportFocus, reportMoments, recentReports, replayMoment } = await import(compiledUrl(new URL("../app/lib/training-report.ts", import.meta.url)));
const { footworkEpisodes } = await import(compiledUrl(new URL("../app/lib/footwork-review.ts", import.meta.url)));
const { motionAnalysisOf } = await import(compiledUrl(new URL("../app/lib/motion-analysis.ts", import.meta.url)));
const { buildPracticeReport, buildReportBackup, renderReportMarkdown } = await import(compiledUrl(new URL("../app/lib/report-export.ts", import.meta.url)));
const { default: LiveResults } = await import(compiledUrl(new URL("../app/LiveResults.tsx", import.meta.url)));

const measure = overrides => ({ feature_name: "travel", name_zh: "移动距离", value: .2, unit: "body", confidence: .9, status: "measured", reason_codes: [], reason_zh: "连续测量", review_hint_zh: "对照起止画面", window: { start_ms: 1000, end_ms: 1600, scope: "event_interval" }, feature_version: "test-v1", required_joints: ["left_hip", "right_hip"], view_semantics: "image_plane_proxy", ...overrides });
const footwork = () => ({ schema_version: "1.1.0", status: "available", episodes: [{ event_id: "step-1", event_code: "FS02", name_zh: "第一步启动", start_ms: 1000, end_ms: 1600, indicators: [{ indicator_id: "FS02-M01", name_zh: "启动", feature_status: "unavailable", scoring_status: "unavailable", features: [], measurements: [measure(), measure({ feature_name: "target", name_zh: "目标方向", status: "unavailable", value: 77, confidence: null, reason_zh: "来球方向未确认" })] }] }] });
const serverReport = () => ({ version: "training-report-v1.0.0", headline_zh: "训练报告已生成", score_semantics: "measurement_evidence_quality", reference_score_0_to_100: 79, technical_score_0_to_100: null, recommendation_semantics: "evidence_linked_review_and_capture_not_technical_error_diagnosis", focus_areas: [{ id: "footwork-review", title_zh: "先回看启动", summary_zh: "移动距离可测，目标方向缺失。", kind: "review", status: "partial", basis_zh: "同段独立测量", start_ms: 1000, end_ms: 1600, metric_ids: ["FS02-M01"], is_technical_error_diagnosis: false }], layers: [{ id: "measurement", label_zh: "逐项测量", status: "available", reason_zh: "独立判断", source: "footwork_review" }], measurement_summary: { footwork_measured_feature_instances: 1, footwork_missing_feature_instances: 1, scope: "returned_replay_episodes", is_truncated: false } });
function motionResult() {
  const evidence = { status: "measured", scope: "continuous_local_window", source_version: "image-plane-rotation-v1.1.0", view_semantics: "image_plane_proxy", is_3d_rotation: false, value: 24, start_ms: 1100, end_ms: 1500, continuous_samples: 10, coverage_fraction: 1, time_coverage_fraction: 1 };
  return { status: "ready", action_recognition: { motion_analysis: { schema_version: "1.1.0", status: "available", contact_confirmed: false, families: { baseline: { episodes: [{ episode_id: "swing-1", family: "baseline", start_ms: 1000, peak_ms: 1300, end_ms: 1800, classification: { status: "unclassified", label_zh: "挥拍待分类" }, contact_confirmed: false, phases: [], metrics: {}, rotation_analysis: { status: "unavailable", is_3d_rotation: false, is_formal_coach_score: false, score: null, metric_evidence: {}, local_windows: [{ window_id: "shoulder-0", axis: "shoulder", start_ms: 1100, end_ms: 1500, metrics: { shoulder_line_change_deg: 24 }, metric_evidence: { shoulder_line_change_deg: evidence } }] } }] } } } } };
}

test("independent footwork survives a failed sibling and never revives missing numbers", () => {
  const rows = footworkEpisodes(footwork());
  assert.equal(rows.length, 1);
  assert.equal(rows[0].indicators[0].measurements[0].value, .2);
  assert.equal(rows[0].indicators[0].measurements[1].value, null);
  const invalid = footwork(); invalid.episodes[0].indicators[0].measurements[0].window.end_ms = 1900;
  assert.equal(footworkEpisodes(invalid)[0].indicators[0].measurements[0].value, null);
  const target = footwork(); target.episodes[0].indicators[0].measurements = [measure({ feature_name: "target", view_semantics: "independent_target_context", feature_version: null, value: 0 })];
  assert.equal(footworkEpisodes(target)[0].indicators[0].measurements[0].value, 0);
});

test("local rotation windows survive insufficient whole-episode coverage under the new motion schema", () => {
  const parsed = motionAnalysisOf(motionResult());
  const episode = parsed.families.baseline.episodes[0];
  assert.equal(episode.rotation_analysis.status, "unavailable");
  assert.equal(episode.metrics.shoulder_line_change_deg, null);
  assert.equal(episode.rotation_analysis.local_windows[0].metrics.shoulder_line_change_deg, 24);
  assert.equal(reportMoments(null, parsed)[0].startMs, 1000);
  const invalid = motionResult(); invalid.action_recognition.motion_analysis.families.baseline.episodes[0].rotation_analysis.local_windows[0].metric_evidence.shoulder_line_change_deg.source_version = "unverified";
  assert.equal(motionAnalysisOf(invalid).families.baseline.episodes[0].rotation_analysis.local_windows.length, 0);
});

test("server focus keeps its evidence semantics and only links a validated replay interval", () => {
  const result = { analysis_report: serverReport(), footwork_review: footwork() };
  assert.equal(reportFocus(result)[0].title, "先回看启动");
  assert.equal(reportFocus(result)[0].moment.startMs, 1000);
  result.analysis_report.focus_areas[0].end_ms = 90000;
  assert.equal(reportFocus(result)[0].moment, undefined);
  result.analysis_report.focus_areas[0].is_technical_error_diagnosis = true;
  assert.equal(analysisReportOf(result.analysis_report).focus_areas.length, 0);
  assert.equal(analysisReportOf({ ...serverReport(), technical_score_0_to_100: 79 }), null);
  assert.equal(analysisReportOf({ ...serverReport(), version: "future-v9" }), null);
});

test("report overview and timeline remain visible while calculations and process start folded", () => {
  const result = { ...motionResult(), analysis_report: serverReport(), footwork_review: footwork() };
  const html = renderToStaticMarkup(createElement(LiveResults, { result, assessment: null, catalog: null, pending: false, trajectory: null }));
  assert.match(html, /这次先关注/);
  assert.match(html, /训练时间线/);
  assert.match(html, /class="report-timeline" id="timeline"/);
  assert.match(html, /先回看启动/);
  assert.match(html, /<details class="report-disclosure" id="rules"><summary>/);
  assert.match(html, /<details class="report-layer-details"><summary>/);
  assert.match(html, /查看局部连续测量/);
  assert.doesNotMatch(html, /<details[^>]*open=/);
});

test("an empty report still provides a real timeline navigation destination and an honest empty state", () => {
  const html = renderToStaticMarkup(createElement(LiveResults, { result: null, assessment: null, catalog: null, pending: false, trajectory: null }));
  assert.match(html, /class="report-timeline" id="timeline"/);
  assert.match(html, /暂无可定位片段/);
  assert.match(html, /已提供的测量仍可在测量详情中查看/);
});

test("readable export and JSON preserve new report context and independent measurements", () => {
  const result = { ...motionResult(), job_id: "report-job-1", analysis_report: serverReport(), footwork_review: footwork() };
  const input = { mode: "live", uploadState: "complete", evidence: { result } };
  const markdown = renderReportMarkdown(buildPracticeReport(input));
  assert.match(markdown, /先回看启动/);
  assert.match(markdown, /移动距离：0\\.2 body/);
  assert.match(markdown, /目标方向：未提供/);
  assert.match(markdown, /24 °/);
  assert.doesNotMatch(markdown, /目标方向：77/);
  const restored = buildReportBackup(input).result;
  assert.equal(restored.analysis_report.reference_score_0_to_100, 79);
  assert.equal(restored.analysis_report.technical_score_0_to_100, null);
  assert.equal(restored.footwork_review.episodes[0].indicators[0].measurements[0].value, .2);
  assert.equal(analysisReportOf(restored.analysis_report).focus_areas[0].title_zh, "先回看启动");
});

test("replay uses source milliseconds and keeps the task boundary", () => {
  const events = []; const oldWindow = globalThis.window, oldDocument = globalThis.document;
  globalThis.window = { dispatchEvent: event => events.push(event) }; globalThis.document = { getElementById: () => null };
  try { replayMoment({ startMs: 1234 }, "report-job-1"); assert.deepEqual(events[0].detail, { timestampMs: 1234, jobId: "report-job-1" }); } finally { globalThis.window = oldWindow; globalThis.document = oldDocument; }
});

test("recent reports reject corrupt entries and retain only bounded local labels", () => {
  const row = { id: "report-job-1", name: "练习视频", viewedAt: "2026-10-04T10:00:00Z" };
  assert.deepEqual(recentReports([row, row, { ...row, id: "../bad" }, { ...row, id: "another-job", viewedAt: "bad-date" }]), [row]);
  assert.deepEqual(recentReports({}), []);
});
