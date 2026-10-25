import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import ts from "typescript";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

const cache = new Map();
function compiledUrl(fileUrl) {
  // Keep source JSON imports shared with production, without a generated copy.
  if (fileUrl.pathname.endsWith(".json")) return `data:text/javascript;base64,${Buffer.from(`export default ${fs.readFileSync(fileUrl, "utf8")}`).toString("base64")}`;
  if (cache.has(fileUrl.href)) return cache.get(fileUrl.href);
  let js = ts.transpileModule(fs.readFileSync(fileUrl, "utf8"), { fileName: fileUrl.pathname, compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  js = js.replace(/from "([^"]+)"/g, (_match, name) => { const local = new URL(name.endsWith(".json") ? name : `${name}.ts`, fileUrl); return `from ${JSON.stringify(name.startsWith(".") ? compiledUrl(fs.existsSync(local) ? local : new URL(`${name}.tsx`, fileUrl)) : import.meta.resolve(name))}`; });
  const url = `data:text/javascript;base64,${Buffer.from(js).toString("base64")}`; cache.set(fileUrl.href, url); return url;
}
const { productText } = await import(compiledUrl(new URL("../app/lib/presentation-text.ts", import.meta.url)));
const { indicatorAssessmentOf } = await import(compiledUrl(new URL("../app/lib/indicator-assessment.ts", import.meta.url)));
const { default: IndicatorAssessmentPanel } = await import(compiledUrl(new URL("../app/IndicatorAssessmentPanel.tsx", import.meta.url)));
const { trainingGuidance } = await import(compiledUrl(new URL("../app/lib/training-guidance.ts", import.meta.url)));
const { buildPracticeReport, buildReportBackup } = await import(compiledUrl(new URL("../app/lib/report-export.ts", import.meta.url)));
const { readReportJson } = await import(compiledUrl(new URL("../app/lib/report-file.ts", import.meta.url)));
const sourceCatalogue = JSON.parse(fs.readFileSync(new URL("../../src/rallymate_scoring/data/technical_review_rules.json", import.meta.url), "utf8"));
const actualSource = JSON.parse(fs.readFileSync(new URL("./fixtures/fs01-fs02-source-observations.json", import.meta.url), "utf8"));
const samSource = JSON.parse(fs.readFileSync(new URL("./fixtures/sam-source-observations.json", import.meta.url), "utf8"));

const motionDetails = JSON.parse(fs.readFileSync(new URL("./fixtures/model-motion-null-speed.json", import.meta.url), "utf8"));
const preloadDetails = JSON.parse(fs.readFileSync(new URL("./fixtures/preload-reference-evidence.json", import.meta.url), "utf8"));
const { sourceMeasurementDetailsOf, sourceMeasurementDetailLines } = await import(compiledUrl(new URL("../app/lib/source-measurement-details.ts", import.meta.url)));
const indicator = overrides => ({ indicator_id: "GS01-M01-01", name_zh: "双手持拍", event_code: "GS01", stage_code: "GS01-M01", status: "contextual_measurement", explanation_zh: "保留相关动作测量，未等同完整原文阶段。", calculation_zh: "同窗独立测量", observation_ids: ["motion:one"], blockers: [], source: { document_id: "CARD-GS", sha256: "a".repeat(64), table: "T002", rubric_sha256: "b".repeat(64) }, grade: { status: "calibration_required", label_zh: "暂不生成等级", reason_zh: "没有完整数值分界" }, ...overrides });
const window = overrides => ({ start_ms: 1100, end_ms: 1400, start: 12, end: 18, delta: 6, min: 12, max: 18, median: 15, mean: 15, range: 6, ...overrides });
const observation = overrides => ({ observation_id: "motion:one", source: "motion_joint_measurement", status: "partial", feature_name: "shoulder_axis", label_zh: "肩线角度", value: null, unit: "deg", episode_id: "swing-1", start_ms: 1000, end_ms: 2000, statistics: {}, windows: [window()], scope: "contextual_motion_phase", definition_zh: "二维肩线局部角度", ...overrides });
const assessment = overrides => ({ version: "word-indicator-assessment-v1.0.0", source_binding: { reference_version: "test", source_documents: [] }, indicators: [indicator()], observations: [observation()], summary_zh: "逐项解释", limitations_zh: [], ...overrides });

test("candidate reference displacement preserves exact signed evidence, zero and units without adding a grade", () => {
  for (const [feature_name, unit, value, label] of [["preload_hip_reference_dy_px", "px", -40, "像素"], ["preload_hip_reference_dy_torso", "torso", 0, "固定躯干尺度"]]) {
    const row = observation({ ...structuredClone(preloadDetails), feature_name, unit, value, source: "source_aligned", status: "measured", windows: [],
      measurement_definition: "稳定参考髋中点纵向变化；同窗固定躯干尺度。", start_ms: 40, end_ms: 160 });
    const raw = assessment({ observations: [row] }), parsed = indicatorAssessmentOf(raw);
    assert.equal(parsed.observations[0].value, value);
    for (const key of ["source_ref", "source_frames", "evidence", "measurement_definition"]) assert.deepEqual(parsed.observations[0][key], row[key], key);
    assert.deepEqual(indicatorAssessmentOf(parsed), parsed);
    const html = renderToStaticMarkup(createElement(IndicatorAssessmentPanel, { value: raw, durationMs: 1000 }));
    assert.match(html, new RegExp(label)); assert.match(html, /向下为正/); assert.match(html, /未确认正式阶段/);
    assert.match(html, /源帧精确时间跨度：120 毫秒/); assert.match(html, /技术等级尚未判定/);
  }
});

test("reference evidence whitelist keeps signed coordinates only and rejects malformed clocks, dimensions and counts", () => {
  const valid = structuredClone(preloadDetails);
  assert.deepEqual(sourceMeasurementDetailsOf(valid), valid);
  valid.evidence.hip_reference_y_start_px = 0;
  assert.equal(sourceMeasurementDetailsOf(valid).evidence.hip_reference_y_start_px, 0);
  const defects = {
    hip_reference_y_start_px: NaN, hip_reference_y_end_px: Infinity, minimum_samples: 1.5,
    minimum_duration_ms: -80, source_track_id: -1, selection_epoch: .5, camera_reference_epoch: Number.MAX_SAFE_INTEGER + 1,
    source_duration_ms: -1, fixed_torso_scale_px: 0, frame_dimensions_px: [1920, -1080],
    source_timestamps_ms: [75040, 75080, null, 75160], arbitrary_signed: -99, api_key: "secret", is_metric_or_3d: "false",
  };
  for (const [key, value] of Object.entries(defects)) {
    const raw = structuredClone(preloadDetails); raw.evidence[key] = value;
    assert.equal(sourceMeasurementDetailsOf(raw).evidence[key], undefined, key);
  }
  for (const times of [[0, 1, 2], [0, 1, 1, 3], [-1, 0, 1, 2], [0, 2, 1, 3]]) {
    const raw = structuredClone(preloadDetails); raw.evidence.source_timestamps_ms = times;
    assert.equal(sourceMeasurementDetailsOf(raw).evidence.source_timestamps_ms, undefined);
  }
  const fractional = structuredClone(preloadDetails);
  fractional.evidence.source_timestamps_ms = [7100, 7120, 7140, 7160].map(pts => pts / 600 * 1000);
  fractional.evidence.source_duration_ms = 100;
  assert.deepEqual(sourceMeasurementDetailsOf(fractional), fractional);
  assert.equal(sourceMeasurementDetailsOf(fractional).measurement_provenance, undefined);
  assert.equal(sourceMeasurementDetailsOf(fractional).source_window, undefined);
});

test("actual contextual SAM motion keeps declared model semantics and sample coverage without inventing a source-card proof", async () => {
  const raw = motionDetails.motion, parsed = indicatorAssessmentOf(assessment({ observations: [raw] }));
  const row = parsed.observations[0];
  assert.deepEqual(row.measurement_provenance, raw.measurement_provenance);
  assert.equal(row.coverage, raw.coverage);
  assert.equal(row.coverage_semantics, "model_estimate_sample_coverage_not_verified_visibility");
  assert.equal(row.source_window, undefined); assert.equal(row.geometry_mask, undefined);
  assert.match(sourceMeasurementDetailLines(row).join(" "), /模型样本覆盖.*真实可见性未知/);
  assert.deepEqual(indicatorAssessmentOf(parsed), parsed);
  let result = { ...samSource, indicator_assessment: assessment({ observations: [raw] }) };
  for (let i = 0; i < 2; i++) {
    const text = JSON.stringify(buildReportBackup({ mode: "live", uploadState: "complete", evidence: { result, jobId: result.job_id } }));
    result = (await readReportJson({ size: Buffer.byteLength(text), text: async () => text })).result;
    assert.deepEqual(indicatorAssessmentOf(result.indicator_assessment), parsed);
  }
  for (const change of [
    value => { value.source = "source_aligned"; }, value => { value.source = "legacy_feature"; },
    value => { value.source_window = {}; }, value => { value.geometry_mask = {}; },
    value => { value.measurement_provenance.confidence_available = true; },
    value => { value.measurement_provenance.model_estimate_sample_count = -1; },
  ]) {
    const bad = structuredClone(raw); change(bad);
    assert.equal(sourceMeasurementDetailsOf(bad).measurement_provenance, undefined);
    assert.equal(sourceMeasurementDetailsOf(bad).coverage, undefined);
  }
});

test("actual speed diagnostics retain null gaps and finite values in order, and quality gates use an explicit allowlist", () => {
  const raw = motionDetails.nullable_speed;
  const clean = sourceMeasurementDetailsOf(raw);
  assert.deepEqual(clean.evidence.speed_continuity_observations.speed_body_s, raw.evidence.speed_continuity_observations.speed_body_s);
  assert.deepEqual(sourceMeasurementDetailsOf(clean), clean);
  const withZero = structuredClone(raw); withZero.evidence.speed_continuity_observations.speed_body_s[3] = 0;
  assert.equal(sourceMeasurementDetailsOf(withZero).evidence.speed_continuity_observations.speed_body_s[3], 0);
  const bad = structuredClone(raw); bad.evidence.speed_continuity_observations.speed_body_s[3] = Infinity;
  assert.equal(sourceMeasurementDetailsOf(bad).evidence.speed_continuity_observations.speed_body_s, undefined);
  const mismatched = structuredClone(raw); mismatched.evidence.speed_continuity_observations.speed_body_s.pop();
  assert.equal(sourceMeasurementDetailsOf(mismatched).evidence.speed_continuity_observations.speed_body_s, undefined);
  const gate = structuredClone(motionDetails.event_quality); gate.evidence.source_event_quality_gate.secret = "ignored";
  assert.deepEqual(sourceMeasurementDetailsOf(gate).evidence.source_event_quality_gate, motionDetails.event_quality.evidence.source_event_quality_gate);
  gate.evidence.source_event_quality_gate.hard_fail_flags = ["<script>"];
  assert.equal(sourceMeasurementDetailsOf(gate).evidence?.source_event_quality_gate, undefined);
});

test("preload confirmation frames remain a separate unvalidated phase diagnostic outside the metric window", () => {
  const raw = motionDetails.preload_confirmation, clean = sourceMeasurementDetailsOf(raw);
  assert.deepEqual(clean.source_frames, [271, 272, 273, 274, 275]);
  assert.deepEqual(clean.source_window.source_frames, clean.source_frames);
  assert.deepEqual(clean.evidence.source_phase.source_frames, [271, 272, 273, 274, 275, 276, 277, 278, 279, 280]);
  assert.equal(clean.evidence.source_phase.is_ground_truth, false);
  assert.deepEqual(sourceMeasurementDetailsOf(clean), clean);
  for (const edit of [
    item => { item.evidence.source_phase.is_ground_truth = true; },
    item => { item.evidence.source_phase.source_frames = [271, 275, 274]; },
    item => { item.evidence.source_phase.phase_contract_version = "invented"; },
  ]) { const bad = structuredClone(raw); edit(bad); assert.equal(sourceMeasurementDetailsOf(bad).evidence.source_phase.source_frames, undefined); }
  const wrong = structuredClone(raw); wrong.evidence.used_source_frames = [271, 280];
  assert.equal(sourceMeasurementDetailsOf(wrong).evidence.used_source_frames, undefined);
});
const render = (value, props = {}) => renderToStaticMarkup(createElement(IndicatorAssessmentPanel, { value, jobId: "test-job-12345678", durationMs: 3000, ...props }));

test("implementation reasons remain in internal UI and JSON without leaking card catalogues into readable reports", async () => {
  const blockers = [
    { code: "event_type_recognizer_not_implemented", message_zh: "当前算法尚未支持该动作类型识别（GS05）；不能据此判定视频中没有该动作。" },
    { code: "complete_grade_verifier_not_implemented", message_zh: "完整原文动作条件及 A～E 自动判定尚未实现并验证。" },
  ];
  const row = indicator({ indicator_id: "GS05-M01-01", event_code: "GS05", status: "event_type_unresolved", observation_ids: [],
    explanation_zh: blockers[0].message_zh, blockers,
    grade: { status: "calibration_required", reason_zh: blockers[1].message_zh } });
  const raw = assessment({ indicators: [row], observations: [] });
  const parsed = indicatorAssessmentOf(raw);
  assert.equal(parsed.indicators[0].status_label_zh, "算法尚未支持该类型");
  assert.equal(parsed.indicators[0].status, "event_type_unresolved");
  assert.deepEqual(parsed.indicators[0].blockers, blockers);
  assert.match(render(raw), /算法尚未支持该类型/);
  assert.match(render(raw), /完整动作条件及 A～E 自动判定尚未实现并验证/);
  assert.doesNotMatch(render(raw), /原文/);
  const result = { ...samSource, indicator_assessment: raw };
  const input = { mode: "live", uploadState: "complete", evidence: { result, jobId: result.job_id } };
  const report = buildPracticeReport(input);
  assert.doesNotMatch(JSON.stringify(report.sections), /完整指标判定|完整原文动作条件及 A～E 自动判定/);
  const backup = JSON.stringify(buildReportBackup(input));
  const imported = (await readReportJson({ size: Buffer.byteLength(backup), text: async () => backup })).result;
  assert.deepEqual(indicatorAssessmentOf(imported.indicator_assessment), parsed);
  assert.equal(parsed.observations.length, 0);
  assert.equal(parsed.indicators[0].grade.status, "calibration_required");
});

test("unsupported labels preserve source conflicts, missing boundaries and existing measurement states", () => {
  const gap = { code: "event_type_recognizer_not_implemented", message_zh: "当前自动识别尚未实现。" };
  for (const [status, label] of [["source_rule_conflict", "动作要求待明确"], ["source_boundaries_missing", "阶段边界不完整"], ["legacy_proxy_measured", "已有历史代理测量"]]) {
    const parsed = indicatorAssessmentOf(assessment({ indicators: [indicator({ status, blockers: [gap] })] }));
    assert.equal(parsed.indicators[0].status_label_zh, label);
    assert.deepEqual(parsed.indicators[0].observation_ids, ["motion:one"]);
  }
  assert.equal(indicatorAssessmentOf(assessment({ indicators: [indicator({ status: "event_type_unresolved" })] })).indicators[0].status_label_zh, "动作类型待确认");
});

test("an observed candidate with insufficient evidence keeps its reason through UI, reports and backup despite a recognizer gap", async () => {
  const explanation = "已找到相关动作候选，但本项所需的有效关节、连续窗口或专用证据不足。";
  const blockers = [
    { code: "required_measurement_evidence_missing", message_zh: "已有候选窗口，但没有通过质量门槛的连续关节测量。" },
    { code: "event_type_recognizer_not_implemented", message_zh: "该动作类型的自动识别覆盖仍未完成；保留本次已有候选证据。" },
    { code: "complete_grade_verifier_not_implemented", message_zh: "完整原文动作条件及 A～E 自动判定尚未实现并验证。" },
  ];
  const raw = assessment({ indicators: [indicator({ indicator_id: "GS05-M01-01", event_code: "GS05",
    status: "evidence_insufficient", explanation_zh: explanation, observation_ids: [], observation_count: 0, blockers,
    grade: { status: "calibration_required", reason_zh: blockers[2].message_zh } })], observations: [] });
  const parsed = indicatorAssessmentOf(raw), card = parsed.indicators[0];
  assert.equal(card.status, "evidence_insufficient");
  assert.equal(card.status_label_zh, "当前证据不足");
  assert.equal(card.explanation_zh, explanation);
  assert.deepEqual(card.blockers, blockers);
  assert.deepEqual(card.observation_ids, []);
  assert.equal(card.observation_count, 0);
  assert.deepEqual(parsed.observations, []);
  assert.deepEqual(card.grade, { status: "calibration_required", label_zh: "技术等级尚未判定", reason_zh: blockers[2].message_zh });
  const html = render(raw);
  assert.match(html, /class="indicator-state">当前证据不足<\/span>/);
  assert.ok(html.includes(explanation));
  for (const blocker of blockers) assert.ok(html.includes(productText(blocker.message_zh)));
  assert.doesNotMatch(html, /class="indicator-state">算法尚未支持该类型|class="indicator-observation"/);
  const result = { job_id: "test-job-12345678", input: { video: { duration_ms: 3000 } }, indicator_assessment: raw };
  const input = { mode: "live", uploadState: "complete", job: { id: result.job_id, status: "completed" }, evidence: { result, jobId: result.job_id } };
  const section = buildPracticeReport(input).sections.find(item => item.title === "完整指标判定");
  assert.equal(section, undefined);
  assert.ok(buildPracticeReport(input).sections.some(item => item.title === "训练评分：未评分"));
  const backup = JSON.stringify(buildReportBackup(input));
  const imported = (await readReportJson({ size: Buffer.byteLength(backup), text: async () => backup })).result;
  assert.deepEqual(indicatorAssessmentOf(imported.indicator_assessment), parsed);
  assert.deepEqual(indicatorAssessmentOf(parsed), parsed);
});

test("the actual Word catalogue keeps all 298 FS and GS indicator ids and source tables", () => {
  const raw = assessment({ indicators: sourceCatalogue.rules.map(row => indicator({ indicator_id: row.indicator_id, event_code: row.event_code, name_zh: row.name_zh, source: { document_id: row.source_document_id, sha256: row.source_document_sha256, table: row.source_table, rubric_sha256: row.rubric_sha256 } })) });
  const parsed = indicatorAssessmentOf(raw);
  assert.equal(parsed.indicators.length, 298); assert.equal(parsed.indicators.filter(row => row.indicator_id.startsWith("FS")).length, 50); assert.equal(parsed.indicators.filter(row => row.indicator_id.startsWith("GS")).length, 248);
  assert.equal(parsed.indicators[0].source.table, sourceCatalogue.rules[0].source_table);
});

test("partial source windows survive a missing global value without becoming whole-phase changes", () => {
  const parsed = indicatorAssessmentOf(assessment());
  assert.equal(parsed.observations.length, 1); assert.equal(parsed.observations[0].value, null); assert.equal(parsed.observations[0].windows[0].statistics.delta, 6);
  assert.deepEqual(indicatorAssessmentOf(JSON.parse(JSON.stringify(parsed))), parsed);
  const html = render(assessment());
  assert.match(html, /分段测量/); assert.match(html, /局部画面，不作为整个阶段的起止变化/); assert.match(html, /1.10–1.40 秒/); assert.match(html, /15/);
  assert.doesNotMatch(html, /1.00–2.00 秒|参考 15 分|<details[^>]*open/);
});

test("invalid, out-of-phase and arithmetically inconsistent local windows never become observations", () => {
  for (const invalid of [window({ start_ms: 900 }), window({ end_ms: 2100 }), window({ delta: 8 }), window({ median: NaN }), window({ end_ms: 1100 })]) assert.equal(indicatorAssessmentOf(assessment({ observations: [observation({ windows: [invalid] })] })).observations.length, 0);
  const html = render(assessment(), { durationMs: 1000 });
  assert.doesNotMatch(html, /回看依据/);
  assert.equal(indicatorAssessmentOf({ ...assessment(), version: "future-unknown" }), null);
});

test("numeric zero is retained, duplicate ids are rejected and arbitrary grades are never displayed", () => {
  const raw = assessment({ indicators: [indicator({ grade: { status: "graded", label_zh: "A" } }), indicator()], observations: [observation({ status: "measured", value: 0, windows: [] }), observation()] });
  const parsed = indicatorAssessmentOf(raw);
  assert.equal(parsed.indicators.length, 1); assert.equal(parsed.observations.length, 1); assert.equal(parsed.observations[0].value, 0);
  assert.equal(parsed.indicators[0].grade.status, "calibration_required"); assert.equal(parsed.indicators[0].grade.label_zh, "技术等级尚未判定");
});

test("display sampling preserves full counts and explicitly labels representative observations", () => {
  const raw = assessment({ full_observation_count: 120, returned_observation_count: 1, is_truncated: true, selection_policy: "uniform_time_order", observation_selection: { reason_zh: "按时间均匀抽样，保留首尾。", source_input_already_truncated: false }, indicators: [indicator({ observation_count: 120, returned_observation_count: 1, observations_truncated: true })] });
  const parsed = indicatorAssessmentOf(raw);
  assert.equal(parsed.full_observation_count, 120); assert.equal(parsed.returned_observation_count, 1); assert.equal(parsed.is_truncated, true);
  assert.equal(parsed.indicators[0].observation_count, 120); assert.equal(parsed.indicators[0].observations_truncated, true);
  assert.match(render(raw), /1 \/ 120 条代表观测/);
  assert.deepEqual(indicatorAssessmentOf(JSON.parse(JSON.stringify(parsed))), parsed);
});

test("guidance and portable exports retain partial evidence without assigning full-stage values or technical errors", () => {
  const result = { job_id: "test-job-12345678", input: { video: { duration_ms: 3000 } }, indicator_assessment: assessment() };
  const guidance = trainingGuidance(result);
  assert.match(guidance[0].detail, /1 段测量/); assert.equal(guidance[0].moment.startMs, 1100); assert.equal(guidance[0].moment.endMs, 1400);
  assert.doesNotMatch(guidance[0].detail, /动作错误|动作正确|技术好|NaN|null/);
  const input = { mode: "live", uploadState: "complete", job: { id: result.job_id, status: "completed" }, evidence: { jobId: result.job_id, result } };
  const report = buildPracticeReport(input);
  assert.ok(report.sections.every(item => !["完整指标判定", "指标观测与视频依据", "局部连续测量片段"].includes(item.title)));
  assert.match(JSON.stringify(report.sections), /1 段测量/);
  const backup = buildReportBackup(input);
  assert.deepEqual(indicatorAssessmentOf(backup.result.indicator_assessment), indicatorAssessmentOf(result.indicator_assessment));
});

test("training suggestions translate feature keys and body-scale units while unknown keys retain an honest generic label", () => {
  const describe = overrides => trainingGuidance({ indicator_assessment: assessment({ observations: [observation({ value: 4.67, ...overrides })] }) })[0].detail;
  const speed = describe({ feature_name: "body_center_speed_body_s", label_zh: "body_center_speed_body_s", unit: "body/s" });
  assert.match(speed, /身体中心移动速度为 4.67 身体尺度\/秒/); assert.doesNotMatch(speed, /body_center|body\/s/);
  const acceleration = describe({ feature_name: "hip_center_deceleration_body_s2", label_zh: "hip_center_deceleration_body_s2", unit: "body/s2" });
  assert.match(acceleration, /髋中心减速度为 4.67 身体尺度\/秒²/);
  const stance = describe({ feature_name: "stance_width_body", label_zh: "stance_width_body", unit: "body" });
  assert.match(stance, /双脚站距为 4.67 身体尺度/);
  const unknown = describe({ feature_name: "unknown_future_feature", label_zh: "unknown_future_feature", unit: "unknown_unit" });
  assert.match(unknown, /动作测量值为 4.67/); assert.doesNotMatch(unknown, /unknown_future_feature|unknown_unit/);
  const localized = describe({ feature_name: "new_measure", label_zh: "已有中文测量名称", unit: "毫秒" });
  assert.match(localized, /已有中文测量名称为 4.67 毫秒/);
  const partial = describe({ feature_name: "unknown_future_feature", label_zh: "unknown_future_feature", value: null });
  assert.match(partial, /动作测量值取得 1 段测量/); assert.doesNotMatch(partial, /unknown_future_feature/);
});

test("actual FS01 and FS02 samples retain calculation, paragraph, frames and local evidence through parsing and backup", () => {
  const parsed = indicatorAssessmentOf(actualSource.indicator_assessment);
  assert.deepEqual(parsed.indicators.map(row => [row.indicator_id, row.observation_count, row.returned_observation_count]), [["FS01-M01", 58, 24], ["FS02-M01", 34, 12]]);
  for (const row of parsed.observations) {
    const original = actualSource.indicator_assessment.observations.find(item => item.observation_id === row.observation_id);
    for (const key of ["value", "measurement_definition", "source_ref", "source_frames", "coverage", "evidence"]) assert.deepEqual(row[key], original[key], `${row.feature_name}/${key}`);
  }
  assert.deepEqual(indicatorAssessmentOf(JSON.parse(JSON.stringify(parsed))), parsed);
  const input = { mode: "live", uploadState: "complete", evidence: { result: actualSource, jobId: actualSource.job_id } };
  const restored = JSON.parse(JSON.stringify(buildReportBackup(input))).result;
  assert.deepEqual(indicatorAssessmentOf(restored.indicator_assessment), parsed);
  const html = render(actualSource.indicator_assessment, { durationMs: 98000 });
  assert.match(html, /计算说明/); assert.doesNotMatch(html, /P0080|原文/); assert.match(html, /相邻有效帧累计路程/);
  assert.match(html, /未确认正式垫步前阶段/); assert.match(html, /24 \/ 58 条代表观测/);
  assert.match(html, /技术等级尚未判定/); assert.doesNotMatch(html, /<details[^>]*open|技术等级暂未标定|"used_adjacent_source_frame_pairs"/);
});

test("source evidence rejects unknown fields, invalid numbers, broken frame links and inconsistent coverage without inventing evidence", () => {
  const raw = structuredClone(actualSource.indicator_assessment);
  const row = raw.observations.find(row => row.feature_name === "head_center_mean_speed_frame_long_edge_s");
  row.value = 0;
  row.evidence = { formula: "known formula", observed_duration_ms: Infinity, used_adjacent_source_frame_pairs: [[1, 99999]],
    endpoint_source_frames: [-1], technical_grade: "A", token: "secret-value", source_clause_complete: false, arbitrary: { x: 1 } };
  row.source_ref = "https://untrusted.test/source";
  row.source_frames = [1, 2, NaN];
  row.coverage.fraction = .123;
  const actual = indicatorAssessmentOf(raw).observations.find(item => item.observation_id === row.observation_id);
  assert.equal(actual.value, 0); assert.equal(actual.source_ref, undefined); assert.equal(actual.source_frames, undefined); assert.equal(actual.coverage, undefined);
  assert.deepEqual(actual.evidence, { formula: "known formula", source_clause_complete: false });
  assert.deepEqual(indicatorAssessmentOf(indicatorAssessmentOf(raw)), indicatorAssessmentOf(raw));
});

test("adjacent sampled records preserve sparse source frame stride without accepting skipped or reversed evidence", () => {
  const raw = structuredClone(actualSource.indicator_assessment);
  const row = raw.observations.find(row => row.feature_name === "head_center_mean_speed_frame_long_edge_s");
  row.source_frames = [10, 12, 14, 16];
  row.evidence = { used_adjacent_source_frame_pairs: [[10, 12], [14, 16]], formula: "adjacent sampled records only" };
  const details = () => indicatorAssessmentOf(raw).observations.find(item => item.observation_id === row.observation_id);
  assert.deepEqual(details().evidence.used_adjacent_source_frame_pairs, [[10, 12], [14, 16]]);
  assert.deepEqual(indicatorAssessmentOf(indicatorAssessmentOf(raw)), indicatorAssessmentOf(raw));
  for (const invalid of [[[10, 14]], [[12, 10]], [[16, 18]], [[10, 10]]]) {
    row.evidence.used_adjacent_source_frame_pairs = invalid;
    assert.equal(details().evidence.used_adjacent_source_frame_pairs, undefined);
  }
  row.source_frames = [10, 12, 12, 14]; row.evidence.used_adjacent_source_frame_pairs = [[12, 14]];
  assert.equal(details().evidence.used_adjacent_source_frame_pairs, undefined);
});

test("real SAM source observations retain source clocks, geometry and unknown visibility through repeated backup", async () => {
  const parsed = indicatorAssessmentOf(samSource.indicator_assessment);
  assert.equal(parsed.observations.length, 3);
  for (const row of parsed.observations) {
    const original = samSource.indicator_assessment.observations.find(item => item.observation_id === row.observation_id);
    for (const key of ["value", "measurement_provenance", "source_window", "geometry_mask", "source_track_id", "model_geometry_coverage"])
      assert.deepEqual(row[key], original[key], key);
    assert.equal(row.coverage, undefined);
    assert.equal(row.measurement_provenance.confidence_available, false);
    assert.equal(row.model_geometry_coverage.source_visibility_70_percent_verified, null);
  }
  assert.deepEqual(indicatorAssessmentOf(JSON.parse(JSON.stringify(parsed))), parsed);
  const backup = async value => { const text = JSON.stringify(buildReportBackup({ mode: "live", uploadState: "complete", evidence: { result: value, jobId: value.job_id } }));
    return (await readReportJson({ size: Buffer.byteLength(text), text: async () => text })).result; };
  assert.deepEqual(indicatorAssessmentOf((await backup(await backup(samSource))).indicator_assessment), parsed);
  const html = render(samSource.indicator_assessment, { durationMs: 97033 });
  assert.match(html, /SAM 单目模型估计/); assert.match(html, /模型几何覆盖/); assert.match(html, /不代表真实可见性已验证/);
  assert.doesNotMatch(html, /局部窗口关节.*仅为可见性门槛/);
});

test("SAM source imports fail closed on incomplete proof and forged geometry visibility", () => {
  for (const defect of ["confidence", "source_track", "pts", "mask", "geometry", "visibility", "frame", "bounds"]) {
    const raw = structuredClone(samSource.indicator_assessment), row = raw.observations[0];
    if (defect === "confidence") row.measurement_provenance.confidence_available = true;
    if (defect === "source_track") row.source_track_id += 1;
    if (defect === "pts") row.source_window.source_bindings[0].pts += 1;
    if (defect === "mask") row.geometry_mask.nose[0] = 1;
    if (defect === "geometry") row.model_geometry_coverage.valid_frame_count -= 1;
    if (defect === "visibility") row.model_geometry_coverage.source_visibility_70_percent_verified = true;
    if (defect === "frame") row.source_frames[0] = 999999;
    if (defect === "bounds") row.start_ms += 1;
    assert.ok(!indicatorAssessmentOf(raw).observations.some(item => item.observation_id === row.observation_id), defect);
  }
});

test("SAM single-side proxy keeps its exact joint selection and rejects invented reductions", () => {
  const raw = structuredClone(samSource.indicator_assessment), row = raw.observations[0];
  Object.assign(row, { source: "legacy_feature", feature_name: "first_step_displacement_body", selected_side: "left",
    required_joints_used: ["left_hip", "right_hip", "left_ankle"] });
  const parsed = indicatorAssessmentOf(raw).observations.find(item => item.observation_id === row.observation_id);
  assert.equal(parsed.selected_side, "left"); assert.deepEqual(parsed.required_joints_used, row.required_joints_used);
  assert.deepEqual(indicatorAssessmentOf(indicatorAssessmentOf(raw)), indicatorAssessmentOf(raw));
  for (const defect of [{ selected_side: "right" }, { required_joints_used: ["left_ankle"] }, { feature_name: "stance_width_body" }]) {
    const bad = structuredClone(raw); Object.assign(bad.observations[0], defect);
    assert.ok(!indicatorAssessmentOf(bad).observations.some(item => item.observation_id === row.observation_id));
  }
});

test("recomputed numeric proxy grades remain visible without becoming source grades", () => {
  const events = [{ event_id: "one", grade: "B", rule_version: "test-math-v1", rule: { indicator_id: "GS01-M01-01" } }];
  const parsed = indicatorAssessmentOf(assessment({ indicators: [indicator({ grade: {
    status: "proxy_scored", semantics: "explicit_numeric_proxy_not_complete_source_grade",
    events, grade_counts: { A: 999 }, reason_zh: "数值代理不等于完整原卡判级。",
  } })], observations: [] }));
  assert.equal(parsed.indicators[0].grade.status, "proxy_scored");
  assert.deepEqual(parsed.indicators[0].grade.grade_counts, { A: 0, B: 1, C: 0, D: 0, E: 0 });
  assert.equal(parsed.indicators[0].grade.label_zh, "数值代理等级");
  const html = renderToStaticMarkup(createElement(IndicatorAssessmentPanel, { value: assessment({
    indicators: [indicator({ grade: { status: "proxy_scored", semantics: "explicit_numeric_proxy_not_complete_source_grade", events } })], observations: [] }),
  }));
  assert.match(html, /B 级 1 次/);
  assert.match(html, /仅描述本次可计算项目/);
});

test("proxy event conflicts or missing semantics never produce a displayed grade", () => {
  const event = { event_id: "one", grade: "B", rule_version: "test-math-v1", rule: { indicator_id: "GS01-M01-01" } };
  for (const change of [{ semantics: undefined }, { events: [event, { ...event, grade: "A" }] },
    { events: [{ ...event, rule: { indicator_id: "GS02-M01-01" } }] }]) {
    const parsed = indicatorAssessmentOf(assessment({ indicators: [indicator({ grade: {
      status: "proxy_scored", semantics: "explicit_numeric_proxy_not_complete_source_grade", events: [event], ...change,
    } })], observations: [] }));
    assert.equal(parsed.indicators[0].grade.status, "calibration_required");
  }
});
