import type { DemoResultResponse, MotionAnalysis, ProcessCriterion, ProcessDimension, ProcessDimensionId, ProcessEvaluation } from "./api-types";
import { reportMoments } from "./training-report";
import { processFootworkCriterion } from "./process-footwork";
import { processPreparationCriterion } from "./process-preparation";
import { footworkEpisodes } from "./footwork-review";
import { relativeReferenceOf, relativeRuleScore, relativeScoringPolicy } from "./relative-score";
import { modelMeasurementEvidenceOf, modelGeometryCoverage } from "./model-measurement";

export const PROCESS_DIMENSIONS: ReadonlyArray<{ id: ProcessDimensionId; name_zh: string }> = [
  { id: "movement", name_zh: "移动质量" },
  { id: "preparation", name_zh: "准备与引拍" },
  { id: "swing", name_zh: "挥拍协调" },
  { id: "contact", name_zh: "触球控制" },
  { id: "follow_through", name_zh: "随挥与衔接" },
  { id: "transition", name_zh: "两次之间的衔接" },
];

/** Proposed source-stage grouping; the original six evidence records remain intact. */
export const TRAINING_DIMENSIONS: ReadonlyArray<{ id: ProcessDimensionId; name_zh: string }> = [
  { id: "movement", name_zh: "移动质量" },
  { id: "preparation", name_zh: "准备与引拍" },
  { id: "swing", name_zh: "挥拍协调" },
  { id: "contact", name_zh: "触球控制" },
  { id: "follow_through", name_zh: "随挥与衔接" },
];

export function trainingDimensions(dimensions: ProcessDimension[]): ProcessDimension[] {
  const fallback = emptyProcessDimensions();
  return TRAINING_DIMENSIONS.map(item => ({ ...(dimensions.find(row => row.id === item.id) ?? fallback.find(row => row.id === item.id)!), name_zh: item.name_zh }));
}

const record = (value: unknown): Record<string, unknown> => value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const text = (value: unknown): string => typeof value === "string" ? value.trim() : "";
const scoreOf = (value: unknown): number | null => finite(value) && value >= 0 && value <= 100 ? value : null;
const countOf = (value: unknown) => Number.isSafeInteger(value) && Number(value) >= 0 ? Number(value) : 0;
const motionSource = "action_recognition.motion_analysis.episodes.process_motion";
const scoredPolicies = [...relativeScoringPolicy.rules, ...relativeScoringPolicy.excluded_legacy_rules];
const legacyRuleIds = new Set(relativeScoringPolicy.excluded_legacy_rules.map(rule => rule.id));
const rules: Record<string, { dimension: ProcessDimensionId; unit: string; min: number; max: number; source: string; score?: (value: number) => number }> = {
  ...Object.fromEntries(scoredPolicies.map(rule => [rule.id, { ...rule, dimension: rule.dimension as ProcessDimensionId, score: (value: number) => relativeRuleScore(rule, value) }])),
  preparation_duration: { dimension: "preparation", unit: "ms", min: 0, max: Infinity, source: `${motionSource}.phases` },
  preparation_direction: { dimension: "preparation", unit: "deg", min: -180, max: 180, source: `${motionSource}.phases.wrist.direction_angle_deg` },
  swing_direction: { dimension: "swing", unit: "deg", min: -180, max: 180, source: `${motionSource}.phases.wrist.direction_angle_deg` },
  swing_direction_consistency: { dimension: "swing", unit: "ratio", min: 0, max: 1, source: `${motionSource}.direction.direction_consistency_ratio` },
  swing_direction_reversals: { dimension: "swing", unit: "count", min: 0, max: Infinity, source: `${motionSource}.direction.reversal_count` },
  hip_to_shoulder_peak_delay: { dimension: "swing", unit: "ms", min: -Infinity, max: Infinity, source: `${motionSource}.sequence.hip_to_shoulder_ms` },
  shoulder_to_wrist_peak_delay: { dimension: "swing", unit: "ms", min: -Infinity, max: Infinity, source: `${motionSource}.sequence.shoulder_to_wrist_ms` },
  terminal_speed_ratio: { dimension: "follow_through", unit: "ratio", min: 0, max: 1, source: `${motionSource}.follow_through.end_to_peak_speed_ratio` },
  follow_duration: { dimension: "follow_through", unit: "ms", min: 0, max: Infinity, source: `${motionSource}.phases` },
  between_motion_interval: { dimension: "transition", unit: "ms", min: 0, max: Infinity, source: `${motionSource}.transition_from_previous` },
};

export function emptyProcessDimensions(): ProcessDimension[] {
  return PROCESS_DIMENSIONS.map(item => ({ ...item, score: null, status: "unavailable", summary_zh: "本维度尚无可靠过程依据。", criteria: [] }));
}

const sourceCardCounts: Record<ProcessDimensionId, number> = { movement: 70, preparation: 78, swing: 72, contact: 25, follow_through: 53, transition: 0 };
function sourceBasisOf(value: unknown, dimension: ProcessDimensionId): ProcessDimension["source_basis"] {
  const raw = record(value);
  const ids = Array.isArray(raw.card_ids) ? raw.card_ids.filter((id): id is string => typeof id === "string" && /^(?:FS\d{2}-M\d{2}|GS\d{2}-M\d{2}-\d{2})$/.test(id)) : [];
  if (raw.grouping_origin !== "proposed_source_stage_grouping" || raw.proposed_not_original !== true || raw.card_count !== sourceCardCounts[dimension] || ids.length !== raw.card_count || new Set(ids).size !== ids.length || !text(raw.source_reference_version) || !/^[a-f\d]{64}$/i.test(text(raw.source_reference_sha256))) return undefined;
  return { version: text(raw.version), card_ids: ids, card_count: Number(raw.card_count), grouping_origin: "proposed_source_stage_grouping", proposed_not_original: true, grouping_rule_zh: text(raw.grouping_rule_zh), source_reference_version: text(raw.source_reference_version), source_reference_sha256: text(raw.source_reference_sha256), source_anchors: Array.isArray(raw.source_anchors) ? raw.source_anchors : [], source_blockers: Array.isArray(raw.source_blockers) ? raw.source_blockers : [], source_documents: Array.isArray(raw.source_documents) ? raw.source_documents : [] };
}

function selectionOf(row: Record<string, unknown>, input: unknown[], criteria: ProcessCriterion[]): Pick<ProcessDimension, "criteria_selection" | "measurement_input_scope"> {
  const raw = record(row.criteria_selection), scope = record(row.measurement_input_scope);
  const integer = (value: unknown): value is number => Number.isSafeInteger(value) && Number(value) >= 0;
  const allMeasuredPreserved = input.filter(item => record(item).status === "measured").length === criteria.filter(item => item.status === "measured").length;
  if (!["all_scoring_criteria_and_uniform_time_ordered_unscored_footwork_per_feature", "all_scoring_criteria_and_uniform_time_ordered_unscored_measurements_per_feature"].includes(String(raw.policy)) || raw.count_scope !== "all_validated_input_episodes_before_display_sampling" || raw.max_footwork_observations_per_feature !== 12
    || !integer(raw.full_count) || !integer(raw.returned_count) || raw.returned_count !== input.length || criteria.length !== input.length || !allMeasuredPreserved
    || raw.full_count < raw.returned_count || raw.is_truncated !== (raw.full_count > raw.returned_count)) return {};
  const criteria_selection: NonNullable<ProcessDimension["criteria_selection"]> = { full_count: raw.full_count, returned_count: raw.returned_count, is_truncated: raw.is_truncated, policy: raw.policy as NonNullable<ProcessDimension["criteria_selection"]>["policy"], max_footwork_observations_per_feature: 12, count_scope: raw.count_scope };
  const validScope = scope.source === "footwork_review" && integer(scope.available_event_count) && integer(scope.returned_event_count) && scope.available_event_count >= scope.returned_event_count && typeof scope.is_truncated === "boolean";
  return { criteria_selection, ...(validScope ? { measurement_input_scope: { source: "footwork_review" as const, available_event_count: Number(scope.available_event_count), returned_event_count: Number(scope.returned_event_count), is_truncated: scope.is_truncated as boolean, reason_zh: text(scope.reason_zh) } } : {}) };
}

function dimensionScope(id: ProcessDimensionId, measured: number, score: number | null, modelOnly: boolean): Pick<ProcessDimension, "score_scope" | "score_scope_zh" | "score_covers_all_source_cards" | "status_reason" | "status_reason_zh"> {
  const scopes: Record<ProcessDimensionId, string> = {
    movement: "仅启动方向与独立训练目标的一致性参考；不代表垫步、调整、支撑、制动等全部移动质量。",
    preparation: "报告已测准备时长、腕部方向及逐关节局部几何；三段运动估计不等于原卡正式阶段，尚缺适用动作确认与完整等级条件。",
    swing: "腕速加速趋势只保留为历史曲线诊断，已退出评分；挥拍协调与动力传递技术规则尚未实现。",
    contact: "触球控制关注触球时的姿态、拍面与球拍控制；当前尚未实现该维度的量化评分。",
    follow_through: "腕速减速趋势只保留为历史曲线诊断，已退出评分；收拍、回位与衔接技术规则尚未实现。",
    transition: "仅报告相邻候选动作的时间间隔；不据间隔推断回位质量或准备就绪。",
  };
  const reason = score !== null ? ["engineering_subcriterion_only", "当前数值仅是已实现子项的工程参考，尚不是整维技术分。"]
    : id === "preparation" ? ["rule_not_implemented", measured ? "已取得准备候选测量；准备技术评分规则尚未实现，持拍手、动作类型与正式阶段的适用条件也需明确。" : "准备技术评分规则尚未实现；本次也没有可用准备候选测量。"]
    : id === "movement" ? ["independent_reference_missing", measured ? "已取得步伐运动测量；当前计分子项缺少通过绑定检查的独立目标与启动测量组合，其他移动技术规则尚未实现。" : "启动方向一致性缺少通过绑定检查的独立目标与启动测量组合；其他移动技术规则尚未实现。"]
    : id === "contact" ? ["rule_not_implemented", "触球控制量化规则尚未实现；当前没有将触球姿态、拍面控制与经验证触球事件绑定评价。"]
    : id === "transition" ? [measured ? "observed_interval_not_recovery_quality" : "continuous_between_motion_evidence_missing", measured ? "动作间隔仅作观察，不能直接换算衔接质量分。" : "缺少连续动作间隔证据；该补充项也未定义技术评分。"]
    : ["selection_biased_proxy_excluded", "腕速趋势受同一曲线分段选择影响，已退出评分；保留可用测量及历史诊断，完整技术评分规则尚未实现。"];
  return { score_scope: id === "movement" ? "engineering_subcriterion_reference" : "observation_only", score_scope_zh: scopes[id], score_covers_all_source_cards: false,
    status_reason: reason[0], status_reason_zh: reason[1] + (modelOnly ? "证据属性：已有单目模型估计的二维过程测量；关键点置信度和可见性未提供。" : "") };
}

/** Legacy phase summaries carry declared model provenance, not per-frame visibility proof. */
function phaseModelProvenance(value: unknown): Record<string, unknown> | null {
  const raw = record(value);
  if (raw.pose_source !== "sam3d" || raw.observation_semantics !== "monocular_model_estimate" || raw.assessment_status !== "measurement_only"
    || raw.scoring_allowed !== false || raw.confidence_available !== false || raw.visibility_verified !== false
    || countOf(raw.model_estimate_sample_count) < 1) return null;
  return { pose_source: "sam3d", observation_semantics: "monocular_model_estimate", assessment_status: "measurement_only",
    scoring_allowed: false, confidence_available: false, visibility_verified: false, model_estimate_sample_count: countOf(raw.model_estimate_sample_count),
    ...(text(raw.reason_zh) ? { reason_zh: text(raw.reason_zh) } : {}) };
}

/** A missing/invalid observation is never restored by an incidental numeric field. */
function criterionOf(value: unknown, dimension: ProcessDimensionId): ProcessCriterion | null {
  const raw = record(value);
  if (!text(raw.id) || !text(raw.label_zh)) return null;
  if (text(raw.id).startsWith("footwork_")) return dimension === "movement" ? processFootworkCriterion(raw) : null;
  if (["preparation_joint_", "preparation_candidate_", "preparation_translation_"].some(prefix => text(raw.id).startsWith(prefix))) return dimension === "preparation" ? processPreparationCriterion(raw) : null;
  const validWindow = finite(raw.start_ms) && finite(raw.end_ms) && raw.start_ms >= 0 && raw.end_ms > raw.start_ms;
  const rule = rules[text(raw.id)];
  const validValue = rule && rule.dimension === dimension && finite(raw.value) && raw.value >= rule.min && raw.value <= rule.max && raw.unit === rule.unit && raw.source === rule.source;
  const durationMatches = !["preparation_duration", "follow_duration", "between_motion_interval"].includes(text(raw.id)) || finite(raw.value) && raw.value > 0 && Math.abs(raw.value - (Number(raw.end_ms) - Number(raw.start_ms))) <= 1;
  const delayMatches = !text(raw.id).endsWith("peak_delay") || finite(raw.value) && Math.abs(raw.value) <= Number(raw.end_ms) - Number(raw.start_ms);
  const samplesValid = !["acceleration_trend", "deceleration_trend"].includes(text(raw.id)) || countOf(raw.sample_count) >= 3;
  const integerCount = text(raw.id) !== "swing_direction_reversals" || Number.isSafeInteger(raw.value);
  const modelContext = raw.assessment_status !== undefined || raw.measurement_provenance !== undefined;
  const geometry = text(raw.id) === "movement_target_alignment" && raw.assessment_status === "mathematical_reference" ? modelMeasurementEvidenceOf(raw) : null;
  const boundGeometry = geometry && validWindow && Number(geometry.source_window.start_ms) >= Number(raw.start_ms)
    && Number(geometry.source_window.end_ms) <= Number(raw.end_ms) && modelGeometryCoverage(geometry, ["left_hip", "right_hip"]) >= .5 ? geometry : null;
  const provenance = text(raw.id) === "movement_target_alignment" ? boundGeometry?.measurement_provenance ?? null
    : modelContext && ["measurement_only", "mathematical_reference"].includes(text(raw.assessment_status)) ? phaseModelProvenance(raw.measurement_provenance) : null;
  const measured = raw.status === "measured" && raw.source_conflict !== true && validValue && validWindow && durationMatches && delayMatches && samplesValid && integerCount && Boolean(text(raw.episode_id)) && (!modelContext || provenance !== null);
  const rawScore = scoreOf(raw.score);
  const legacy = legacyRuleIds.has(text(raw.id));
  const score = !legacy && (!modelContext || raw.assessment_status === "mathematical_reference" && provenance !== null) && measured && rule.score && text(raw.rule_zh) && rawScore !== null && Math.abs(rawScore - rule.score(Number(raw.value))) <= .051 ? rawScore : null;
  return {
    id: text(raw.id), label_zh: legacy ? scoredPolicies.find(row => row.id === raw.id)!.name_zh : text(raw.label_zh), status: measured ? "measured" : "unavailable",
    value: measured ? Number(raw.value) : null, unit: text(raw.unit) || null,
    score,
    ...(raw.source_conflict === true ? { source_conflict: true as const } : {}),
    ...(legacy ? { legacy_diagnostic: { version: "relative-reference-v1.0.0" as const, score: measured ? Math.round(rule.score!(Number(raw.value)) * 10) / 10 : null,
      semantics: "selection_biased_motion_curve_diagnostic" as const, is_technical_quality: false as const, contributes_to_current_score: false as const, exclusion_reason: "selection_biased_proxy_excluded" as const } } : {}),
    reason_zh: modelContext && !provenance ? "该项模型测量缺少一致的来源声明，暂不采用，也不生成分数。" : legacy ? measured ? "保留腕速曲线测量作历史诊断；分段与测量来自同一曲线，存在选择偏差，不参与当前技术参考分。" : "该项历史曲线测量未通过有效性校验，不采用诊断值。" : measured || raw.status === "unavailable" ? text(raw.reason_zh) : "该项测量缺少有效数值、来源或时间区间，暂不采用。",
    rule_zh: legacy ? `${scoredPolicies.find(row => row.id === raw.id)!.formula_zh}；仅作历史曲线诊断，不参与当前总分。` : text(raw.rule_zh), source: text(raw.source),
    start_ms: measured ? Number(raw.start_ms) : null, end_ms: measured ? Number(raw.end_ms) : null,
    episode_id: text(raw.episode_id) || null,
    ...(text(raw.related_episode_id) ? { related_episode_id: text(raw.related_episode_id) } : {}),
    ...(raw.sample_count !== undefined ? { sample_count: countOf(raw.sample_count) } : {}),
    ...(modelContext ? { assessment_status: score !== null ? "mathematical_reference" as const : "measurement_only" as const, ...(provenance ? { measurement_provenance: provenance } : {}) } : {}),
    ...(boundGeometry ? boundGeometry : {}),
    ...(text(raw.id).endsWith("peak_delay") ? { timing_resolution_ms: finite(raw.timing_resolution_ms) && raw.timing_resolution_ms > 0 ? raw.timing_resolution_ms : null, ordering_resolved: raw.ordering_resolved === true } : {}),
  };
}

/** Validate the process contract independently from the older evidence-quality scores. */
export function processEvaluationOf(value: unknown): ProcessEvaluation | null {
  const raw = record(value);
  if (!["process-evaluation-v1.0.0", "process-evaluation-v1.1.0", "process-evaluation-v1.2.0", "process-evaluation-v2.0.0"].includes(text(raw.version)) || raw.semantics !== "observable_motion_process_reference" || raw.is_calibrated !== false || raw.overall_score !== null || !Array.isArray(raw.dimensions)) return null;
  if (raw.relative_reference !== undefined) {
    const reference = record(raw.relative_reference);
    const current = reference.version === "relative-reference-v3.0.0";
    const known = current || ["relative-reference-v1.0.0", "relative-reference-v2.0.0"].includes(text(reference.version));
    if (!known || reference.is_calibrated !== false || !Array.isArray(reference.dimensions)) return null;
    if (current ? reference.semantics !== "observed_measurement_reference"
      || reference.aggregation !== "independent_episode_mean_then_rule_mean_then_observed_dimension_mean"
      || reference.overall_score !== null && scoreOf(reference.overall_score) === null
      : reference.semantics !== "neutral_prior_relative_reference" || reference.neutral_score !== 50 || reference.prior_sample_weight !== 3
      || reference.aggregation !== "independent_episode_mean_then_rule_mean_then_five_equal_dimensions" || scoreOf(reference.overall_score) === null) return null;
  }
  const issues = new Set<string>();
  if (Array.isArray(raw.validation_issues) && raw.validation_issues.length) {
    // Retained only as a bounded warning; these strings never grant validity or points.
    for (const issue of raw.validation_issues.slice(0, 25)) if (typeof issue === "string" && issue.length > 0 && issue.length <= 250) issues.add(issue);
  }
  if (raw.dimensions.some(item => !PROCESS_DIMENSIONS.some(dimension => dimension.id === record(item).id))) issues.add("存在未识别的维度，未将其纳入评分。");
  const dimensions = emptyProcessDimensions().map(fallback => {
    const matches = (raw.dimensions as unknown[]).map(record).filter(item => item.id === fallback.id);
    if (matches.length !== 1) { if (matches.length > 1) issues.add(`${fallback.name_zh}数据重复，未采用该维度。`); return fallback; }
    const row = matches[0];
    if (!["measured", "partial", "unavailable"].includes(text(row.status)) || !Array.isArray(row.criteria)) { issues.add(`${fallback.name_zh}状态或测量列表无效。`); return fallback; }
    const input = Array.isArray(row.criteria) ? row.criteria : [];
    const ids = new Set<string>();
    let invalidScored = false;
    const criteria: ProcessCriterion[] = [];
    for (const item of input) {
      const source = record(item), parsed = criterionOf(item, fallback.id);
      if (parsed?.source_conflict) issues.add(`${fallback.name_zh}同一动作的测量相互冲突，未采用该动作。`);
      if (!parsed || source.status === "measured" && parsed.status !== "measured") issues.add(`${fallback.name_zh}存在未通过来源、数值或时间校验的测量，未采用这些项目。`);
      if (source.score !== null && source.score !== undefined && parsed?.score === null && !legacyRuleIds.has(text(source.id))) issues.add(`${fallback.name_zh}存在无法核验的子项分数，未采用该分数。`);
      const localWindow = parsed?.measurement_semantics === "joint_specific_2d_measurements_not_technical_grade" || parsed?.id.startsWith("preparation_translation_");
      const key = parsed ? `${parsed.episode_id ?? ""}:${parsed.id}${localWindow ? `:${parsed.start_ms}:${parsed.end_ms}` : ""}` : "";
      if (parsed && ids.has(key) && !localWindow) {
        const previous = criteria.find(row => row.id === parsed.id && row.episode_id === parsed.episode_id);
        if (previous && ["status", "value", "unit", "source", "start_ms", "end_ms", "sample_count", "measurement_provenance"].some(field => JSON.stringify(previous[field as keyof ProcessCriterion]) !== JSON.stringify(parsed[field as keyof ProcessCriterion]))) {
          previous.status = "unavailable"; previous.value = null; previous.score = null;
          previous.start_ms = null; previous.end_ms = null;
          if (previous.legacy_diagnostic) previous.legacy_diagnostic.score = null;
          previous.source_conflict = true;
          previous.reason_zh = "同一动作的同类测量出现冲突，参考总分不采用此动作。";
          issues.add(`${fallback.name_zh}同一动作的测量相互冲突，未采用该动作。`);
        }
      }
      if (!parsed || ids.has(key)) { if (source.score !== null && source.score !== undefined) invalidScored = true; continue; }
      ids.add(key);
      if (source.score !== null && source.score !== undefined && parsed.score === null) invalidScored = true;
      // An unavailable dimension cannot contribute measured children or points.
      criteria.push(row.status === "unavailable" ? { ...parsed, status: "unavailable", value: null, score: null, start_ms: null, end_ms: null,
        ...(parsed.legacy_diagnostic ? { legacy_diagnostic: { ...parsed.legacy_diagnostic, score: null } } : {}) } : parsed);
    }
    const measuredCount = criteria.filter(item => item.status === "measured").length;
    const scored = criteria.filter(item => item.score !== null);
    const rawScore = scoreOf(row.score);
    const distinctScoredRules = new Set(scored.map(item => item.id)).size;
    const mean = scored.reduce((sum, item) => sum + item.score!, 0) / scored.length;
    const score = !invalidScored && distinctScoredRules === 1 && row.status !== "unavailable" && row.aggregation === "mean_of_independent_samples_for_one_explicit_rule" && rawScore !== null && Math.abs(rawScore - mean) <= .051 ? rawScore : null;
    const status = !measuredCount ? "unavailable" : row.status === "measured" && measuredCount === criteria.length && score !== null ? "measured" : "partial";
    const totalRules = fallback.id === "contact" ? raw.version !== "process-evaluation-v1.0.0" ? 3 : 2 : new Set([...Object.entries(rules).filter(([, rule]) => rule.dimension === fallback.id).map(([id]) => id), ...criteria.filter(item => item.measurement_semantics && item.status === "measured").map(item => item.id)]).size;
    const totalSamples = new Set(criteria.filter(item => item.episode_id).map(item => item.episode_id)).size;
    const selection = selectionOf(row, input, criteria);
    const sampled = selection.criteria_selection?.is_truncated === true && ["movement", "preparation"].includes(fallback.id);
    const fullMeasured = sampled && Number.isSafeInteger(row.measured_criterion_count) && Number(row.measured_criterion_count) >= measuredCount && Number(row.measured_criterion_count) <= selection.criteria_selection!.full_count ? Number(row.measured_criterion_count) : measuredCount;
    const returnedSamples = new Set(criteria.filter(item => item.status === "measured").map(item => item.episode_id)).size;
    const fullSamples = sampled && selection.measurement_input_scope && Number.isSafeInteger(row.sample_count) && Number(row.sample_count) >= returnedSamples && Number(row.sample_count) <= selection.measurement_input_scope.returned_event_count ? Number(row.sample_count) : returnedSamples;
    const fullTotalSamples = sampled && selection.measurement_input_scope && Number.isSafeInteger(row.total_sample_count) && Number(row.total_sample_count) >= totalSamples && Number(row.total_sample_count) <= selection.measurement_input_scope.returned_event_count ? Number(row.total_sample_count) : totalSamples;
    const modelOnly = measuredCount > 0 && criteria.filter(item => item.status === "measured").every(item => item.assessment_status === "measurement_only" && record(item.measurement_provenance).pose_source === "sam3d" && record(item.measurement_provenance).observation_semantics === "monocular_model_estimate");
    const scope = dimensionScope(fallback.id, measuredCount, score, modelOnly);
    return {
      ...fallback, status, score, criteria,
      ...scope,
      ...selection,
      source_basis: sourceBasisOf(row.source_basis, fallback.id),
      summary_zh: score !== null ? `${scored.length} / ${fullTotalSamples} 段样本计分，${distinctScoredRules} / ${totalRules} 个过程子项计分。${scope.score_scope_zh}` : `${fullMeasured ? `已读取事件内有 ${fullMeasured} 项过程测量${sampled ? `，本页展示 ${measuredCount} 项代表测量` : ""}。` : ""}${scope.status_reason_zh}`,
      scored_rule_count: distinctScoredRules,
      total_rule_count: totalRules,
      sample_count: fullSamples,
      measured_criterion_count: fullMeasured,
      returned_measured_criterion_count: measuredCount,
      scored_sample_count: scored.length,
      total_sample_count: fullTotalSamples,
      aggregation: score === null ? "none" : "mean_of_independent_samples_for_one_explicit_rule",
    } satisfies ProcessDimension;
  });
  const relative = relativeReferenceOf(dimensions);
  for (const dimension of dimensions) {
    const computed = relative.dimensions.find(row => row.id === dimension.id);
    if (computed) { dimension.scoring_audit = computed.scoring_audit; dimension.gap = computed.gap; }
  }
  return { version: raw.version as ProcessEvaluation["version"], semantics: "observable_motion_process_reference", is_calibrated: false, overall_score: null, dimensions, relative_reference: relative, display_dimension_ids: TRAINING_DIMENSIONS.map(item => item.id), ...(issues.size ? { validation_issues: [...issues] } : {}) };
}

/** Missing data and a rejected contract have distinct visible outcomes. */
export function processEvaluationValidationOf(value: unknown, prepared?: ProcessEvaluation | null): { status: "valid" | "missing" | "invalid"; reason_zh: string; issues: string[] } {
  const report = prepared === undefined ? processEvaluationOf(value) : prepared;
  if (report && !report.validation_issues?.length) return { status: "valid", reason_zh: "过程测量契约校验通过；分数按当前共享规则重算。", issues: [] };
  if (report) return { status: "invalid", reason_zh: "部分数据校验未通过：仅采用已通过校验的测量，其余项目不参与评分。", issues: report.validation_issues ?? [] };
  if (value === null || value === undefined) return { status: "missing", reason_zh: "报告未提供过程评分数据；缺少评分依据的项目保持未评分。", issues: [] };
  return { status: "invalid", reason_zh: "数据校验未通过：评分版本、语义或结构不受支持，未采用其中的分数。缺少评分依据的项目保持未评分。", issues: [] };
}

export const processScoreText = (value: number | null): string => value === null ? "—" : Number(value.toFixed(1)).toString();
export const processDimensionState = (dimension: ProcessDimension): string => dimension.score !== null ? "仅独立目标子项" : dimension.status_reason === "selection_biased_proxy_excluded" ? "曲线诊断不参与计分" : dimension.status_reason === "independent_reference_missing" ? "缺少独立目标输入" : dimension.status_reason === "rule_not_implemented" ? "技术评分规则未实现" : dimension.criteria.some(item => item.status === "measured") ? "已测 · 暂未评分" : "待补依据";

export function processCriterionGroups(criteria: ProcessCriterion[]): Array<{ id: string; startMs: number | null; criteria: ProcessCriterion[] }> {
  const groups = new Map<string, { id: string; startMs: number | null; criteria: ProcessCriterion[] }>();
  for (const criterion of criteria) {
    const id = criterion.episode_id ?? "unassigned";
    const group = groups.get(id) ?? { id, startMs: criterion.start_ms, criteria: [] };
    group.criteria.push(criterion);
    if (criterion.start_ms !== null) group.startMs = group.startMs === null ? criterion.start_ms : Math.min(group.startMs, criterion.start_ms);
    groups.set(id, group);
  }
  return [...groups.values()].sort((a, b) => (a.startMs ?? Infinity) - (b.startMs ?? Infinity));
}

export function processPeakSequence(criteria: ProcessCriterion[]): string | null {
  const first = criteria.find(item => item.id === "hip_to_shoulder_peak_delay" && item.status === "measured");
  const second = criteria.find(item => item.id === "shoulder_to_wrist_peak_delay" && item.status === "measured");
  if (!first || !second || first.value === null || second.value === null || !first.episode_id || first.episode_id !== second.episode_id || first.start_ms !== second.start_ms || first.end_ms !== second.end_ms || !first.ordering_resolved || !second.ordering_resolved || !finite(first.timing_resolution_ms) || !finite(second.timing_resolution_ms) || first.timing_resolution_ms <= 0 || second.timing_resolution_ms <= 0) return null;
  const peaks = [{ name: "髋", time: 0 }, { name: "肩", time: first.value }, { name: "腕", time: first.value + second.value }].sort((a, b) => a.time - b.time);
  if (first.end_ms === null || first.start_ms === null || peaks[2].time - peaks[0].time > first.end_ms - first.start_ms) return null;
  if (peaks.some((peak, index) => index > 0 && peak.time - peaks[index - 1].time <= Math.max(first.timing_resolution_ms!, second.timing_resolution_ms!))) return null;
  return peaks.map((peak, index) => `${index ? peak.time === peaks[index - 1].time ? " ≈ " : " → " : ""}${peak.name}`).join("");
}

export function processCriterionReplay(criterion: ProcessCriterion, result: DemoResultResponse | null, motion?: MotionAnalysis | null): { startMs: number } | null {
  if (criterion.status !== "measured" || criterion.start_ms === null || criterion.end_ms === null || !criterion.episode_id) return null;
  if (criterion.measurement_semantics === "image_plane_proxy_not_source_card_grade") {
    const episode = footworkEpisodes(result?.footwork_review).find(item => item.event_id === criterion.episode_id);
    if (!episode || episode.person_track_id !== criterion.person_track_id || episode.video_id !== criterion.video_id || episode.start_ms !== criterion.start_ms || episode.end_ms !== criterion.end_ms) return null;
  }
  if (criterion.measurement_semantics === "preparation_candidate_2d_not_formal_stage_or_grade" || criterion.measurement_semantics === "joint_specific_2d_measurements_not_technical_grade") {
    if (!criterion.video_id || result?.job_id !== criterion.video_id) return null;
    const families = motion?.families ?? result?.action_recognition?.motion_analysis?.families;
    const matches = Object.values(families ?? {}).flatMap(family => family.episodes ?? []).filter(episode => episode.episode_id === criterion.episode_id);
    if (!matches.length || matches.some(episode => episode.person_track_id !== criterion.person_track_id)) return null;
  }
  const moments = reportMoments(result, motion);
  const matching = moments.find(moment => moment.id === `rotation:${criterion.episode_id}` || moment.id === `footwork:${criterion.episode_id}`);
  if (criterion.id === "between_motion_interval") {
    const previous = moments.find(moment => moment.id === `rotation:${criterion.related_episode_id}`);
    return previous && matching && criterion.start_ms === previous.endMs && criterion.end_ms === matching.startMs ? { startMs: criterion.start_ms } : null;
  }
  return matching && criterion.start_ms >= matching.startMs && criterion.end_ms <= matching.endMs ? { startMs: criterion.start_ms } : null;
}

/** Connect only adjacent observed axes; missing values are gaps, never zeroes. */
export function processRadarGeometry(dimensions: ProcessDimension[]) {
  const pointAt = (index: number, score: number) => {
    const angle = index * 2 * Math.PI / Math.max(1, dimensions.length) - Math.PI / 2;
    return { x: 180 + Math.cos(angle) * 90 * score / 100, y: 150 + Math.sin(angle) * 90 * score / 100 };
  };
  const points = dimensions.map((dimension, index) => {
    const score = dimension && dimension.status !== "unavailable" ? scoreOf(dimension.score) : null;
    return score === null ? null : { ...pointAt(index, score), id: dimension.id, score };
  });
  const lines = points.flatMap((point, index) => {
    const next = points[(index + 1) % points.length];
    return point && next ? [{ from: point, to: next }] : [];
  });
  return { points, lines, complete: points.every(Boolean), pointAt };
}
