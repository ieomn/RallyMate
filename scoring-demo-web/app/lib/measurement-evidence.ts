import type { DemoResultResponse, IndicatorEvaluation } from "./api-types";

const count = (value: unknown) => typeof value === "number" && Number.isFinite(value) && value >= 0 ? Math.floor(value) : 0;
export const hasMeasurement = (item: IndicatorEvaluation) => item.available !== false && (count(item.measured_instance_count) > 0 || Array.isArray(item.representative_measurements) && item.representative_measurements.some(value => value && Number.isFinite(value.median_value)));
export const candidateName = (name: string) => name.includes("候选") ? name : `${name}（候选）`;
export const MEASUREMENT_CONTRACT = "isotropic-frame-long-edge-v1.1.0";
const record = (value: unknown): Record<string, unknown> => value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
export const EVIDENCE_SCORE_SEMANTICS = "measurement_evidence_quality";
/** Only explicitly identified evidence scores are compatible with this display. */
export function evidenceReferenceScore(value: unknown): number | null {
  const item = record(value), score = item.score_0_to_100;
  return item.score_semantics === EVIDENCE_SCORE_SEMANTICS && item.available !== false && typeof score === "number" && Number.isFinite(score) && score >= 0 && score <= 100 ? score : null;
}
export const measurementUpdateRequired = (result: DemoResultResponse) => record(result.measurement_contract).contract_version !== MEASUREMENT_CONTRACT;
export function measurementWarnings(result: DemoResultResponse): string[] {
  const warnings = Array.isArray(result.measurement_warnings_zh) ? result.measurement_warnings_zh.filter((value): value is string => typeof value === "string") : [];
  return [...new Set([...(measurementUpdateRequired(result) ? ["历史测量使用旧坐标算法，请重新分析视频；旧数值保留供复核，不能与新结果比较。"] : []), ...warnings])];
}

export function measurementIndicators(result?: DemoResultResponse | null): IndicatorEvaluation[] {
  const actions = Array.isArray(result?.actions) ? result.actions : [];
  const values = [...actions.flatMap(action => Array.isArray(action?.indicator_evaluations) ? action.indicator_evaluations : []), ...(Array.isArray(result?.training_evaluation?.indicator_evaluations) ? result.training_evaluation.indicator_evaluations : [])].filter(item => item && typeof item.indicator_id === "string");
  return [...new Map(values.map(item => [item.indicator_id, item])).values()];
}

export function measurementCounts(result?: DemoResultResponse | null) {
  const indicators = measurementIndicators(result);
  return { measured: indicators.filter(hasMeasurement).length, total: indicators.length };
}

/** Summary imports stay in the real-data view; coverage never becomes a demo score. */
export function measurementResultFromSummary(summary: Record<string, unknown>): DemoResultResponse {
  if (summary.status && !["completed", "succeeded", "ready"].includes(String(summary.status))) throw new Error("该摘要尚未成功完成分析，不能作为完成结果导入。");
  const loop = record(summary.minimum_scoring_loop), processing = record(summary.processing);
  const contract = record(loop.coordinate_contract), cameraMotion = record(processing.camera_motion), camera = record(cameraMotion.status_counts), timing = record(processing.source_timing);
  const warnings = ["已导入任务摘要；仅展示文件中保存的观测，缺少逐项特征的指标不补造。"];
  if (count(camera.moving)) warnings.push(`背景运动 ${count(camera.moving)} 帧，其中 ${Math.min(count(camera.moving), count(cameraMotion.compensated_frames))} 帧完成画面运动校正；未获可靠校正的区间不用于移动表现。这是二维画面校正，不是三维校正。`);
  if (count(camera.unavailable)) warnings.push("部分区间无法核实相机是否固定；这些区间不用于固定机位的位移候选分析。");
  if (count(timing.fallback_timestamp_frames)) warnings.push("部分帧的真实时间无法确认；相关速度与时序测量需重新采集证据。");
  const names: Record<string, string> = { FS01: "准备与分腿垫步", FS02: "第一步启动", FS09: "制动与恢复" };
  return normalizeMeasurementResult({
    status: "ready", result_kind: "imported_stage1_summary", summary,
    ...(typeof summary.job_id === "string" && /^[a-zA-Z0-9_-]{8,80}$/.test(summary.job_id) ? { job_id: summary.job_id } : {}),
    measurement_contract: contract, measurement_warnings_zh: warnings,
    action_recognition: record(summary.action_recognition),
    actions: Object.entries(record(loop.event_counts)).filter(([code, value]) => code in names && Number.isSafeInteger(value) && Number(value) >= 0).map(([event_code, value]) => ({
      event_code, family: "footwork", name_zh: names[event_code], detected_segments: Number(value), indicator_evaluations: [],
    })),
  });
}

/** Preserve declared evidence scores; historical untyped scores are not technical grades. */
export function normalizeMeasurementResult(result: DemoResultResponse): DemoResultResponse {
  const normalizeIndicator = (item: IndicatorEvaluation): IndicatorEvaluation => ({
    ...item, score_0_to_100: hasMeasurement(item) ? evidenceReferenceScore(item) : null, technical_score_0_to_100: null,
    technical_grade: null, formal_grade: null,
    score_semantics: item.score_semantics === EVIDENCE_SCORE_SEMANTICS ? EVIDENCE_SCORE_SEMANTICS : "descriptive_measurement_evidence_no_aggregate_score", technical_score_status: "calibration_required",
    available: hasMeasurement(item), component_weights: item.score_semantics === EVIDENCE_SCORE_SEMANTICS ? item.component_weights : {}, effective_component_weights: item.score_semantics === EVIDENCE_SCORE_SEMANTICS ? item.effective_component_weights : {},
    level_zh: hasMeasurement(item) ? "有可复核测量" : "测量证据不足",
    summary_zh: `${count(item.measured_instance_count)}/${count(item.total_instance_count)} 个候选片段可测；技术评分待教练标定。`,
    observation_zh: item.observation_zh && !/(?:参考分|[0-9]+\s*[/／]\s*100|表现较稳定|动作做得[好差])/u.test(item.observation_zh) ? item.observation_zh : "请结合逐项测量和候选片段回放复核。",
    suggestion_zh: "先复核候选动作和测量证据；当前不据此给出技术优劣判断。",
  });
  const indicators = measurementIndicators(result).map(normalizeIndicator);
  const measured = indicators.filter(hasMeasurement).length;
  const training = result.training_evaluation;
  return {
    ...result,
    measurement_update_required: measurementUpdateRequired(result),
    measurement_warnings_zh: measurementWarnings(result),
    training_evaluation: training ? {
      ...training, score_0_to_100: measured > 0 ? evidenceReferenceScore(training) : null, technical_score_0_to_100: null,
      technical_grade: null, formal_grade: null,
      score_semantics: training.score_semantics === EVIDENCE_SCORE_SEMANTICS ? EVIDENCE_SCORE_SEMANTICS : "descriptive_measurement_evidence_no_aggregate_score", technical_score_status: "calibration_required",
      available: measured > 0, evaluated_indicator_count: measured, total_indicator_count: indicators.length,
      label_zh: "测量证据参考分（Beta）", level_zh: measured ? "有可复核测量" : "测量证据不足",
      summary_zh: `${indicators.length} 项中有 ${measured} 项有测量；候选动作待复核，技术评分待教练标定。`,
      meaning_zh: "参考分反映测量证据的完整程度；高分不代表动作正确或技术水平更高，技术评分待教练标定。",
      strengths_zh: [], priorities_zh: [], component_weights: training.score_semantics === EVIDENCE_SCORE_SEMANTICS ? training.component_weights : {},
      action_evaluations: undefined, indicator_evaluations: indicators,
    } : undefined,
    actions: result.actions?.map(action => ({
      ...action, name_zh: action.event_code.startsWith("FS") ? candidateName(action.name_zh) : action.name_zh,
      summary_zh: `${count(action.detected_segments)} 个候选片段，动作类型待复核。`,
      performance_assessment: { score_0_to_100: action.indicator_evaluations?.some(hasMeasurement) ? evidenceReferenceScore(action.performance_assessment) : null, score_semantics: EVIDENCE_SCORE_SEMANTICS, technical_score_0_to_100: null, technical_grade: null, formal_grade: null, level_zh: "测量证据参考分（Beta）" },
      indicator_evaluations: action.indicator_evaluations?.map(normalizeIndicator),
      formation_assessment: undefined,
    })),
    final_demo_score: undefined, display_score: undefined, analysis_quality: undefined,
  };
}
