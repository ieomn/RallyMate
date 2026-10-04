import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

function compiledUrl(fileUrl) {
  let js = ts.transpileModule(fs.readFileSync(fileUrl, "utf8"), { fileName: fileUrl.pathname, compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  js = js.replace(/^import\s+["'][^"']+\.css["'];?\s*$/gm, "");
  js = js.replace(/import (\w+) from "([^"\n]+\.json)";?/g, (_match, binding, name) => `const ${binding} = ${fs.readFileSync(new URL(name, fileUrl), "utf8")};`);
  js = js.replace(/from "([^"]+)"/g, (_match, name) => {
    const local = new URL(`${name}.ts`, fileUrl);
    return `from ${JSON.stringify(name.startsWith(".") ? compiledUrl(fs.existsSync(local) ? local : new URL(`${name}.tsx`, fileUrl)) : import.meta.resolve(name))}`;
  });
  return `data:text/javascript;base64,${Buffer.from(js).toString("base64")}`;
}
const { normalizeMeasurementResult, measurementCounts, measurementResultFromSummary } = await import(compiledUrl(new URL("../app/lib/measurement-evidence.ts", import.meta.url)));
const { default: LiveResults } = await import(compiledUrl(new URL("../app/LiveResults.tsx", import.meta.url)));
const { createApiClient } = await import(compiledUrl(new URL("../app/lib/api-client.ts", import.meta.url)));
const { buildReportBackup, buildPracticeReport, getReportExportGate, renderReportMarkdown } = await import(compiledUrl(new URL("../app/lib/report-export.ts", import.meta.url)));

function historical() {
  const item = { indicator_id: "FS01-M03", event_code: "FS01", name_zh: "双脚上移代理", score_0_to_100: 97, measured_instance_count: 1, total_instance_count: 2, summary_zh: "测量证据参考分97/100", components: { required_feature_coverage_percent: 50, repeatability_percent: null }, representative_measurements: [{ feature_name: "rise", label_zh: "上移", median_value: 12, unit_zh: "% 身体尺度", sample_count: 1 }] };
  return { job_id: "synthetic-job", status: "ready", training_evaluation: { score_0_to_100: 97, available: true, summary_zh: "参考分97/100", strengths_zh: ["97分动作好"], indicator_evaluations: [item] }, actions: [{ event_code: "FS01", name_zh: "分腿垫步", detected_segments: 2, performance_assessment: { score_0_to_100: 97 }, indicator_evaluations: [item] }] };
}

test("historical scores are suppressed without discarding partial or single-sample measurements", () => {
  const source = historical(), snapshot = JSON.stringify(source), normalized = normalizeMeasurementResult(source);
  assert.equal(normalized.training_evaluation.score_0_to_100, null);
  assert.equal(normalized.training_evaluation.available, true);
  assert.equal(normalized.training_evaluation.indicator_evaluations[0].score_0_to_100, null);
  assert.equal(normalized.training_evaluation.indicator_evaluations[0].components.repeatability_percent, null);
  assert.equal(normalized.actions[0].performance_assessment.score_0_to_100, null);
  assert.deepEqual(measurementCounts(normalized), { measured: 1, total: 1 });
  assert.deepEqual(normalized.training_evaluation.strengths_zh, []);
  assert.equal(JSON.stringify(source), snapshot);
  const html = renderToStaticMarkup(createElement(LiveResults, { result: source, assessment: null, catalog: null, pending: false, trajectory: null }));
  assert.match(html, /实测指标|1 \/ 1/);
  assert.match(html, /技术评分待教练标定/);
  assert.match(html, /分腿垫步（候选）/);
  assert.match(html, /12 % 身体尺度/);
  assert.doesNotMatch(html.replace(/<section id="rule-review"[\s\S]*?<\/section>/g, ""), /97|score-ring/);
});

test("empty evidence stays readable, and missing metrics never turn into a new aggregate score", () => {
  const source = historical();
  source.training_evaluation.indicator_evaluations.push({ indicator_id: "FS01-M02", name_zh: "缺失指标", score_0_to_100: 42, measured_instance_count: 0, total_instance_count: 3 });
  const partial = normalizeMeasurementResult(source);
  assert.deepEqual(measurementCounts(partial), { measured: 1, total: 2 });
  assert.equal(partial.training_evaluation.score_0_to_100, null);
  const empty = normalizeMeasurementResult({ status: "ready", training_evaluation: { score_0_to_100: 100, indicator_evaluations: [] } });
  assert.equal(empty.training_evaluation.available, false);
  assert.deepEqual(measurementCounts(empty), { measured: 0, total: 0 });
  assert.equal(empty.training_evaluation.score_0_to_100, null);
});

test("API normalization accepts scoreless successful results and historical artifacts", async () => {
  for (const payload of [historical(), normalizeMeasurementResult(historical())]) {
    const client = createApiClient({}, async () => Response.json(payload));
    const result = await client.getDemoResult("synthetic-job");
    assert.equal(result.status, "ready");
    assert.equal(result.training_evaluation.score_0_to_100, null);
    assert.equal(result.training_evaluation.available, true);
  }
});

test("HTML/Markdown and JSON export preserve raw evidence but never revive historical scores", () => {
  const result = historical();
  const input = { mode: "live", uploadState: "complete", evidence: { result } };
  assert.equal(getReportExportGate(input).allowed, true);
  const report = buildPracticeReport(input), markdown = renderReportMarkdown(report), backup = buildReportBackup(input);
  assert.match(markdown, /技术评分待教练标定|待教练标定/);
  assert.match(markdown, /12|50%/);
  assert.doesNotMatch(markdown, /97/);
  assert.equal(backup.result.training_evaluation.score_0_to_100, null);
  assert.equal(backup.result.training_evaluation.indicator_evaluations[0].score_0_to_100, null);
  assert.equal(backup.result.training_evaluation.indicator_evaluations[0].components.required_feature_coverage_percent, 50);
});

function referenceResult(score = 81) {
  const result = historical();
  const marker = { score_0_to_100: score, score_semantics: "measurement_evidence_quality", technical_score_0_to_100: 99, technical_grade: "A", formal_grade: "A" };
  Object.assign(result.training_evaluation, marker);
  for (const action of result.actions) {
    Object.assign(action.performance_assessment, marker);
    for (const item of action.indicator_evaluations) Object.assign(item, marker);
  }
  return result;
}

test("declared evidence reference scores survive API, screen and report round trips without becoming technical grades", async () => {
  const source = referenceResult(), snapshot = JSON.stringify(source);
  const client = createApiClient({}, async () => Response.json(source));
  const result = await client.getDemoResult("synthetic-job");
  assert.equal(result.training_evaluation.score_0_to_100, 81);
  assert.equal(result.training_evaluation.indicator_evaluations[0].score_0_to_100, 81);
  assert.equal(result.actions[0].performance_assessment.score_0_to_100, 81);
  assert.deepEqual(normalizeMeasurementResult(result), result);
  assert.equal(JSON.stringify(source), snapshot);
  const html = renderToStaticMarkup(createElement(LiveResults, { result, assessment: null, catalog: null, pending: false, trajectory: null }));
  assert.match(html, /81\.0/);
  assert.match(html, /测量证据参考分（Beta）|非技术评分/);
  assert.match(html, /高分不代表动作正确/);
  assert.match(html, /<details class="inspector-section measurement-evidence"><summary/);
  assert.doesNotMatch(html.replace(/<section id="rule-review"[\s\S]*?<\/section>/g, ""), /97|99/);
  const input = { mode: "live", uploadState: "complete", evidence: { result } };
  const markdown = renderReportMarkdown(buildPracticeReport(input));
  assert.match(markdown, /81 \/ 100/);
  assert.match(markdown, /测量证据参考分（Beta）/);
  const backup = buildReportBackup(input);
  const restored = normalizeMeasurementResult(JSON.parse(JSON.stringify(backup)).result);
  for (const item of [restored.training_evaluation, restored.training_evaluation.indicator_evaluations[0], restored.actions[0].performance_assessment]) {
    assert.equal(item.score_0_to_100, 81);
    assert.equal(item.score_semantics, "measurement_evidence_quality");
    assert.equal(item.technical_score_0_to_100, null);
    assert.equal(item.technical_grade, null);
    assert.equal(item.formal_grade, null);
  }
  assert.equal(restored.training_evaluation.indicator_evaluations[0].components.repeatability_percent, null);
});

test("reference score zero is preserved, while invalid, unavailable and empty evidence remain unscored", () => {
  assert.equal(normalizeMeasurementResult(referenceResult(0)).training_evaluation.score_0_to_100, 0);
  for (const score of [-1, 101, NaN, Infinity, "81"]) {
    const result = normalizeMeasurementResult(referenceResult(score));
    assert.equal(result.training_evaluation.score_0_to_100, null);
    assert.equal(result.actions[0].performance_assessment.score_0_to_100, null);
    assert.equal(result.training_evaluation.indicator_evaluations[0].score_0_to_100, null);
  }
  const empty = referenceResult();
  empty.training_evaluation.indicator_evaluations = [];
  empty.actions[0].indicator_evaluations = [];
  const result = normalizeMeasurementResult(empty);
  assert.equal(result.training_evaluation.score_0_to_100, null);
  assert.equal(result.actions[0].performance_assessment.score_0_to_100, null);
  const unavailable = referenceResult();
  unavailable.training_evaluation.available = false;
  unavailable.training_evaluation.indicator_evaluations[0].available = false;
  assert.equal(normalizeMeasurementResult(unavailable).training_evaluation.score_0_to_100, null);
});

test("legacy imports prominently disclose old coordinates while current contracts retain capture warnings", () => {
  const legacy = historical();
  const legacyHtml = renderToStaticMarkup(createElement(LiveResults, { result: legacy, assessment: null, catalog: null, pending: false, trajectory: null }));
  assert.match(legacyHtml, /历史测量使用旧坐标算法，请重新分析视频/);
  const input = { mode: "live", uploadState: "complete", evidence: { result: legacy } };
  assert.match(renderReportMarkdown(buildPracticeReport(input)), /不能与新结果比较/);
  assert.equal(buildReportBackup(input).result.measurement_update_required, true);
  const current = { ...legacy, measurement_contract: { contract_version: "isotropic-frame-long-edge-v1.1.0" }, measurement_warnings_zh: ["相机移动区间不用于固定机位的位移候选分析。", "部分帧的真实时间无法确认。"] };
  const normalized = normalizeMeasurementResult(current);
  assert.equal(normalized.measurement_update_required, false);
  const preCompensation = { ...current, measurement_contract: { contract_version: "isotropic-frame-long-edge-v1.0.0" } };
  assert.equal(normalizeMeasurementResult(preCompensation).measurement_update_required, true);
  const currentHtml = renderToStaticMarkup(createElement(LiveResults, { result: current, assessment: null, catalog: null, pending: false, trajectory: null }));
  assert.doesNotMatch(currentHtml, /历史测量使用旧坐标算法/);
  assert.match(currentHtml, /相机移动区间|真实时间无法确认/);
});

test("an empty top-level measurement list does not hide valid action-level evidence", () => {
  const input = historical();
  input.training_evaluation.indicator_evaluations = [];
  const result = normalizeMeasurementResult(input);
  assert.deepEqual(measurementCounts(result), { measured: 1, total: 1 });
  assert.equal(result.training_evaluation.indicator_evaluations[0].representative_measurements[0].median_value, 12);
  assert.match(renderToStaticMarkup(createElement(LiveResults, { result, assessment: null, catalog: null, pending: false, trajectory: null })), /12 % 身体尺度/);
});

test("summary import with zero footwork keeps rotation measurements and exports without demo scores", () => {
  const episode = {
    episode_id: "serve-1", family: "serve", start_ms: 1000, peak_ms: 1300, end_ms: 1600, contact_confirmed: false,
    classification: { status: "rule_inferred", label_zh: "发球式挥拍", reason_zh: "二维运动代理" },
    metrics: { shoulder_line_change_deg: 22, wrist_path_torso: 1.75 }, phases: [],
    rotation_analysis: { schema_version: "1.0.0", status: "partial", is_3d_rotation: false, is_formal_coach_score: false, score: null, score_status: "calibration_required", metric_evidence: {
      shoulder_line_change_deg: { status: "measured", coverage_fraction: 1, time_coverage_fraction: 1, total_samples: 12, valid_samples: 12, continuous_samples: 12, start_ms: 1000, end_ms: 1600 },
    } },
  };
  const summary = { job_id: "summary-only", status: "completed", processing: { processed_frames: 100, camera_motion: { status_counts: { moving: 9 }, compensated_frames: 7 } }, coverage: { pose_frame_fraction: 0.8 }, minimum_scoring_loop: { event_counts: { FS01: 0, FS02: 0, FS09: 0 } }, action_recognition: { motion_analysis: { schema_version: "1.0.0", status: "available", contact_confirmed: false, families: { serve: { status: "analyzed", episodes: [episode] } } } } };
  const result = measurementResultFromSummary(summary);
  assert.deepEqual(measurementCounts(result), { measured: 0, total: 0 });
  const html = renderToStaticMarkup(createElement(LiveResults, { result, summary, assessment: null, catalog: null, pending: false, trajectory: null }));
  assert.match(html, /二维转体观察|22\.00/);
  assert.match(html, /历史测量使用旧坐标算法/);
  assert.match(html, /背景运动 9 帧，其中 7 帧/);
  assert.doesNotMatch(html, /score-ring|综合分|就绪度 \/ 100/);
  const input = { mode: "live", uploadState: "complete", evidence: { result }, summary };
  assert.equal(getReportExportGate(input).allowed, true);
  assert.match(renderReportMarkdown(buildPracticeReport(input)), /画面内肩线变化（度）：22/);
  const backup = buildReportBackup(input);
  assert.equal(backup.result.action_recognition.motion_analysis.families.serve.episodes[0].metrics.wrist_path_torso, 1.75);
  assert.equal(backup.result.measurement_update_required, true);
  const screenSource = fs.readFileSync(new URL("../app/ScoreLab.tsx", import.meta.url), "utf8");
  assert.match(screenSource, /<LiveResults\b[^>]*result=\{evidence\.result\s*\?\?\s*null\}/);
  assert.match(screenSource, /const importedResult = measurementResultFromSummary\(summary\)/);
});

test("summary imports preserve empty completed evidence without inventing measurements or completing failed jobs", () => {
  const summary = { status: "completed", processing: { processed_frames: 20 }, coverage: { pose_frame_fraction: 0 } };
  const result = measurementResultFromSummary(summary);
  assert.deepEqual(measurementCounts(result), { measured: 0, total: 0 });
  assert.equal(getReportExportGate({ mode: "live", uploadState: "complete", evidence: { result }, summary }).allowed, true);
  for (const status of ["failed", "running", "queued"]) assert.throws(() => measurementResultFromSummary({ ...summary, status }), /尚未成功完成/);
});
