/** Strict reader for frame-recomputed card reports. Summary counts are derived.
 * Temporal contracts and the generated GS reader contract are checked against
 * the backend catalogue. A registered rule is never counted as an execution.
 */
import gsContract from "./gs-card-display-contract";
import footworkContract from "./footwork-cycle-display-contract";
import fsWindowContract from "./fs-card-window-display-contract";
import { footworkCycleOf, validFootworkCycle, footworkValuesAgree, type FootworkCycleEvidence } from "./footwork-cycle-evidence";
import { evidenceObject, racketSideOf, validRacketSide, validFsWindow, validFormalGsStageSource, type EvidenceObject, type RacketSideEvidence } from "./manual-card-evidence";
import { validateGsPreload, type PreloadValidation } from "./gs-preload-evidence";
import { validateFsStepMotion, type StepMotionValidation } from "./fs-step-motion-evidence";
import { formalScoreSemantics, isFormalScoringPolicy } from "./formal-card-scoring-policy";
import { validateTemporalLocalDip, validFormalTemporalSource, type LocalDipValidation } from "./temporal-local-dip-evidence";
import microContract from "./directional-micro-display-contract";
import { directionalMicroApplies, validateDirectionalMicro, type DirectionalMicroValidation } from "./directional-micro-adjustment-evidence";

export const CARD_EXECUTION_VERSION = "card-execution-v1.0.0";
export const CARD_RULE_VERSION = "card-rules-v1.0.0";
const MEASUREMENT_VERSION = "temporal-card-evidence-v1.0.0";
export const SOURCE_CARD_COUNT = 298;
export const CARD_RUN_LABELS = { scored: "已评分", proxy_scored: "有视觉代理结果", review_required: "需要复核", unavailable: "证据不足", not_executed: "尚未接入计算", not_observed: "未观察到此类动作" } as const;
export type CardRunStatus = keyof typeof CARD_RUN_LABELS;
type Obj = Record<string, unknown>;
type ConditionSpec = [string, "gte" | "lte" | "between" | "eq", number | [number, number], string];
type GsSpec = { stage_code: string; rule_kind: string; required_joint_names: string[]; conditions: ConditionSpec[]; source: CardInstance["source"] };
const GS_CARD_CONTRACTS = gsContract.cards as unknown as Record<string, GsSpec>;
type FootworkSpec = Omit<GsSpec, "stage_code"> & { limitation: string };
const FOOTWORK_CARD_CONTRACTS = footworkContract.cards as unknown as Record<string, FootworkSpec>;
type FsWindowSpec = FootworkSpec & { requires_complete_step_cycle: boolean };
const FS_WINDOW_CONTRACTS = fsWindowContract.cards as unknown as Record<string, FsWindowSpec>;
export const TEMPORAL_CARD_CONDITIONS: Record<string, ConditionSpec[]> = {
  "FS01-M02": [["hip_drop_torso", "gte", .03, "torso"], ["left_knee_flexion_change_deg", "gte", 4, "deg"], ["right_knee_flexion_change_deg", "gte", 4, "deg"], ["joint_descent_fraction", "gte", .7, "fraction"], ["speed_step_variation_ratio", "lte", 1.5, "ratio"]],
  "FS01-M05": [["hip_inside_support_fraction", "gte", .8, "fraction"], ["body_speed_std_torso_s", "lte", .3, "torso/s"], ["successor_interval_ms", "between", [0, 500], "ms"]],
  "FS09-M03": [["hip_speed_drop_torso_s", "gte", .2, "torso/s"], ["hip_speed_drop_fraction", "gte", .25, "fraction"], ["left_knee_flexion_change_deg", "gte", 4, "deg"], ["right_knee_flexion_change_deg", "gte", 4, "deg"], ["hip_drop_torso", "gte", .03, "torso"], ["trunk_tilt_std_deg", "lte", 10, "deg"], ["joint_braking_fraction", "gte", .5, "fraction"]],
  "FS09-M04": [["hip_inside_support_fraction", "gte", .8, "fraction"], ["supported_low_motion_duration_ms", "gte", 120, "ms"], ["shoulder_hip_angular_speed_drop_deg_s", "gte", 5, "deg/s"]],
  "FS09-M05": [["body_speed_std_torso_s", "lte", .3, "torso/s"], ["supported_low_motion_duration_ms", "gte", 120, "ms"], ["successor_interval_ms", "between", [0, 500], "ms"]],
};
// GS membership comes from the 248 versioned source entries, not a permissive regex.
const CARD_IDS = new Set([...Object.keys(GS_CARD_CONTRACTS), ...Array.from({ length: 10 }, (_, event) =>
  Array.from({ length: 5 }, (_, stage) => `FS${String(event + 1).padStart(2, "0")}-M${String(stage + 1).padStart(2, "0")}`)).flat()]);
const obj = (value: unknown): Obj => value && typeof value === "object" && !Array.isArray(value) ? value as Obj : {};
const text = (value: unknown, limit = 240) => typeof value === "string" ? value.slice(0, limit) : "";
const number = (value: unknown): number | null => typeof value === "number" && Number.isFinite(value) ? value : null;
const integer = (value: unknown): number | null => Number.isSafeInteger(value) && Number(value) >= 0 ? Number(value) : null;
const list = (value: unknown, limit = 1000): unknown[] => Array.isArray(value) ? value.slice(0, limit) : [];
const strings = (value: unknown) => [...new Set(list(value, 50).map(v => text(v)).filter(Boolean))];
const equalEvidence = (a: unknown, b: unknown): boolean => Array.isArray(a) ? Array.isArray(b) && a.length === b.length && a.every((v, i) => equalEvidence(v, b[i]))
  : a && typeof a === "object" ? Object.keys(a).length === Object.keys(obj(b)).length && Object.entries(a).every(([key, value]) => equalEvidence(value, obj(b)[key])) : a === b;
const gradePoints = { A: 100, B: 80, C: 60, D: 40, E: 20 } as const;
const LIMITATION_LABELS: Record<string, string> = {
  "Only five temporal calculators are wired by this adapter.": "当前已接入五张步伐卡的连续窗口计算，其余卡片仍按证据状态分别列出。",
  "Automatic event candidates are not independently verified action labels.": "自动动作候选尚未逐段独立确认动作类型，需要结合原视频复核。",
  "Proxy thresholds are uncalibrated engineering rules, not Word-grade thresholds.": "代理阈值是尚未专家标定的工程规则，不能用于宣称原卡技术等级。",
  "Five temporal FS calculators and explicitly annotated GS stages are connected; no automatic ten-stage GS locator is registered.": "当前接入五张步伐卡和有明确人工阶段标注的击球卡，尚未实现自动击球十阶段定位。",
  manual_stage_annotations_are_estimates_not_expert_ground_truth: "人工阶段标注是带时间误差的估计，尚非专家确认的真值。",
  source_stage_technical_behavior_not_established_by_2d_proxy: "二维几何代理不足以证明原卡阶段要求的完整技术行为。",
  no_automatic_ten_stage_gs_locator_registered: "尚未接入自动击球十阶段定位。",
  missing_stage_never_inferred_from_three_generic_swing_phases: "缺失阶段保留为空，未用三个通用挥拍阶段替代十个原卡阶段。",
  monocular_geometry_does_not_establish_gaze_force_racket_contact_or_visibility: "单目几何不能直接确认真实视线、受力、球拍接触或关节可见性。",
  manual_cycle_windows_are_nonexpert_estimates_not_step_family_ground_truth: "人工步周期窗口是候选估计，不代表专家已确认交叉步或并步类型。",
  continuous_image_motion_does_not_establish_contact_force_target_arrival_or_original_grade: "连续画面运动不能证明真实触地、受力、到位或原卡技术等级。",
  no_automatic_cycle_detector_registered: "尚未接入自动完整步周期定位。",
  record_level_entry_does_not_verify_artifact_files: "此观测记录回放本身不声明已重新读取原视频及核验源文件。",
  manual_window_is_nonexpert_candidate_not_contact_force_or_step_family_ground_truth: "人工窗口不是专家步型、触地或受力真值。",
  "Explicit manual FS03/FS04 candidate windows are connected; no automatic step-family or ground-contact verifier is registered.": "当前支持有明确人工窗口来源的交叉步和并步候选，尚未自动确认步型或真实触地。",
  "2D hip midpoint is not physical centre of mass": "二维髋部中点不能视作真实身体重心。",
  "low bilateral ankle motion with projected support is not verified ground contact": "双踝低速且髋投影在支撑区间内，仍不能证明真实触地。",
  "model geometry does not establish original-card joint visibility": "模型给出的几何坐标不能直接证明原卡要求的关节可见性。",
  "pose candidate boundaries are not ground truth": "姿态候选的时间边界尚非人工确认的真值。",
  individual_manual_card_windows_are_estimates_not_technical_truth: "逐卡人工窗口是边界估计，不是专家技术真值。",
  no_automatic_fs_phase_or_technique_locator_added: "尚未接入自动步伐阶段或技术类型定位。",
  complete_step_cycle_never_inferred_from_manual_window: "人工窗口不用于推定完整步周期。",
  manual_observable_cards_remain_ungraded: "需要人工确认的原卡信息保留复核，不生成分数。",
  engineering_proxy_thresholds_uncalibrated: "视觉代理采用尚未专家标定的工程阈值。",
  image_axis_not_metric_court_direction: "所标场地线只是图像投影轴，不是标定后的球场方向。",
  visible_ball_position_not_predicted_impact_truth: "所标目标是此帧可见球的位置，不是预测击球点。",
  distance_reasonableness_not_established: "此测量未确定人与球的距离是否符合技术要求。",
  visual_motion_not_foot_contact_or_technical_step: "脚部画面运动不代表真实触地或已确认的技术步型。",
  original_qualitative_grade_not_established: "原卡定性技术等级尚未建立。",
  manual_image_references_not_metric_court_or_target_truth: "人工标记的是图像中的场地线与可见目标，不能直接当作标定后的球场方向或真实击球目标。",
  application_thresholds_uncalibrated: "应用评分阈值尚未经过专家标定。",
};
type Grade = keyof typeof gradePoints;
export interface CardCondition { key: string; metric: string; value: number | null; operator: string; threshold: number | [number, number] | null; unit: string; passed: boolean | null }
export interface CardSourceStage {
  stage_code: string; annotation_id: string; document_sha256: string; digest_kind: string;
  binding: { method: string; version: string; source: string; uncertainty_ms: number | null; semantics: string };
}
export interface CardCycleAnnotation {
  document_sha256: string; document_version: string; event_id: string; event_code: string;
  start_source_frame: number; end_source_frame: number; requested_window_ms: { start_ms: number; end_ms: number };
  binding: { method: string; version: string; source: string; uncertainty_ms: number; semantics: string };
}
export interface CardInstance {
  indicator_id: string; event_id: string; video_id: string; person_track_id: number; rule_version: string; measurement_version: string;
  status: "scored" | "proxy_scored" | "review_required" | "unavailable"; original_grade: null; source_grade_supported: false; grade: Grade | null; proxy_grade: Grade | null; score: number | null;
  scoring_policy_version: string | null; advisory_reason_codes: string[]; advisory_flags: string[];
  window: { start_ms: number; end_ms: number } | null; reason_codes: string[]; conditions: CardCondition[];
  quality: { total_frame_count: number; valid_frame_count: number; fraction: number | null; visibility_verified: false; required_joint_names: string[] };
  source_stage: CardSourceStage | null;
  source_stage_evidence: EvidenceObject | null;
  source_stage_context: EvidenceObject | null;
  foot_cycle: FootworkCycleEvidence | null;
  cycle_measurements: Record<string, number | null> | null;
  source_cycle_annotation: CardCycleAnnotation | null;
  racket_side_evidence: RacketSideEvidence | null;
  source_window_annotation: EvidenceObject | null;
  source_window_evidence: EvidenceObject | null;
  source_window_context: EvidenceObject | null;
  application_rule_version: string | null;
  directional_micro_validation: DirectionalMicroValidation | null;
  measurement_provenance: EvidenceObject | null;
  model_scoring_policy_version: string | null;
  temporal_evidence: EvidenceObject | null;
  local_dip_validation: LocalDipValidation | null;
  auxiliary_evidence: EvidenceObject | null;
  auxiliary_parent_context: EvidenceObject | null;
  step_motion_validation: StepMotionValidation | null;
  auxiliary_validation: PreloadValidation | null;
  limitations: string[];
  limitation_codes: string[];
  fallback: { trigger: string; strategy: string; selected: { start_ms: number; end_ms: number } | null } | null;
  threshold_basis: string; semantics: string; source: { document_id: string; document_sha256: string; table: string; rubric_sha256: string };
}
export interface ExecutedCard { indicator_id: string; event_code: string; name_zh: string; rule_version: string; rule_kind: string; runtime_engine: string | null; application_rule_version: string | null; run_status: CardRunStatus; reason_codes: string[]; instances: CardInstance[]; instance_count: number }
export interface CardExecutionReport {
  version: typeof CARD_EXECUTION_VERSION; rule_version: typeof CARD_RULE_VERSION; source_run_id: string; cards: ExecutedCard[]; scoring_policy_version: string | null;
  status: "available" | "unavailable"; reason_codes: string[];
  summary: { catalogue_cards: number; expected_source_cards: number; computed_cards: number; formal_scored_cards: number; formal_scored_instances: number; proxy_scored_cards: number; proxy_scored_instances: number; review_instances: number; unavailable_instances: number; original_scored_cards: 0; manual_scored_cards: 0; run_card_status_counts: Record<string, number>; conflicting_instances: number; rejected_cards: number };
  overall_score: number | null; missing_policy: "exclude_from_score_keep_in_coverage_no_missing_zero"; limitations: string[]; limitation_codes: string[];
  directional_micro_adjustment_execution: EvidenceObject | null;
  directional_micro_adjustment_windows: EvidenceObject[];
}

function windowOf(value: unknown) {
  const row = obj(value), start = number(row.start_ms), end = number(row.end_ms);
  return start !== null && end !== null && start >= 0 && end > start ? { start_ms: start, end_ms: end } : null;
}
function conditionOf(value: unknown): CardCondition {
  const row = obj(value), scalar = number(row.threshold), bounds = list(row.threshold, 3);
  const threshold: CardCondition["threshold"] = scalar !== null ? scalar : bounds.length === 2 && bounds.every(v => number(v) !== null) && Number(bounds[0]) <= Number(bounds[1]) ? [Number(bounds[0]), Number(bounds[1])] : null;
  return { key: text(row.key), metric: text(row.metric), value: number(row.value), operator: text(row.operator), threshold, unit: text(row.unit), passed: typeof row.passed === "boolean" ? row.passed : null };
}
function computedPass(condition: CardCondition): boolean | null {
  if (condition.value === null || condition.threshold === null) return null;
  if (condition.operator === "between" && Array.isArray(condition.threshold)) return condition.value >= condition.threshold[0] && condition.value <= condition.threshold[1];
  if (typeof condition.threshold !== "number") return null;
  return condition.operator === "gte" ? condition.value >= condition.threshold : condition.operator === "lte" ? condition.value <= condition.threshold : condition.operator === "eq" ? condition.value === condition.threshold : null;
}
function invalidate(instance: CardInstance, reason: string): CardInstance {
  return withPreloadValidation({ ...instance, status: "unavailable", grade: null, proxy_grade: null, score: null, reason_codes: [...new Set([...instance.reason_codes, reason])], conditions: instance.conditions.map(c => ({ ...c, passed: null })) });
}
function withPreloadValidation(instance: CardInstance): CardInstance {
  const motion = validateFsStepMotion(instance.source_window_annotation, instance.source_window_evidence, instance.source_window_context, instance);
  const measuredMicro = validateDirectionalMicro(instance.source_window_annotation, instance.source_window_evidence, instance.source_window_context, instance);
  const micro = measuredMicro && instance.reason_codes.includes("directional_micro_report_source_mismatch")
    ? { ...measuredMicro, status: "invalid" as const, reason_codes: ["directional_micro_report_source_mismatch"], values: {}, feet: null, condition_values_match: false } : measuredMicro;
  const dip = isFormalScoringPolicy(instance.scoring_policy_version) ? validateTemporalLocalDip(instance.temporal_evidence, instance) : null;
  const invalidDip = dip?.status === "invalid", invalidMicro = micro?.status === "invalid";
  return { ...instance, ...(invalidDip || invalidMicro ? { status: "unavailable" as const, grade: null, proxy_grade: null, score: null,
      reason_codes: [...new Set([...instance.reason_codes, ...(invalidDip ? ["local_dip_source_evidence_invalid"] : []), ...(invalidMicro ? ["directional_micro_source_evidence_invalid"] : [])])] } : {}), local_dip_validation: dip, auxiliary_validation: validateGsPreload(instance.auxiliary_evidence, instance, instance.auxiliary_parent_context),
    conditions: motion?.status === "invalid" || invalidDip || invalidMicro ? instance.conditions.map(condition => ({ ...condition, value: null, passed: null })) : instance.conditions,
    directional_micro_validation: micro,
    step_motion_validation: motion };
}
function sourceStageOf(row: Obj, id: string): CardSourceStage | null {
  const normalized = obj(row.source_stage), annotation = obj(row.source_annotation);
  const source = Object.keys(normalized).length ? normalized : annotation;
  if (!Object.keys(source).length) return null;
  const binding = obj(source.binding);
  const stage: CardSourceStage = { stage_code: text(source.stage_code ?? obj(row.window).stage_code),
    annotation_id: text(source.annotation_id), document_sha256: text(source.document_sha256), digest_kind: text(source.digest_kind),
    binding: { method: text(binding.method), version: text(binding.version), source: text(binding.source),
      uncertainty_ms: number(binding.uncertainty_ms), semantics: text(binding.semantics) } };
  if (!GS_CARD_CONTRACTS[id] || stage.stage_code !== GS_CARD_CONTRACTS[id].stage_code || stage.annotation_id !== row.event_id
      || !/^[a-fA-F0-9]{64}$/.test(stage.document_sha256) || stage.digest_kind !== "canonical_json_sorted_utf8_v1"
      || stage.binding.method !== "manual_anchor" || !stage.binding.version || !stage.binding.source
      || stage.binding.uncertainty_ms === null || stage.binding.uncertainty_ms < 0
      || stage.binding.semantics !== "reviewer_estimated_source_stage_not_expert_ground_truth") return null;
  return stage;
}
function validGsEvidence(instance: CardInstance, contract: GsSpec): boolean {
  const stage = instance.source_stage;
  return !!stage && stage.stage_code === contract.stage_code && stage.annotation_id === instance.event_id
    && /^[a-fA-F0-9]{64}$/.test(stage.document_sha256) && stage.digest_kind === "canonical_json_sorted_utf8_v1"
    && stage.binding.method === "manual_anchor" && !!stage.binding.version && !!stage.binding.source
    && stage.binding.uncertainty_ms !== null && stage.binding.uncertainty_ms >= 0
    && stage.binding.semantics === "reviewer_estimated_source_stage_not_expert_ground_truth"
    && Object.entries(contract.source).every(([key, value]) => instance.source[key as keyof CardInstance["source"]] === value)
    && JSON.stringify([...instance.quality.required_joint_names].sort()) === JSON.stringify(contract.required_joint_names);
}
function cycleAnnotationOf(value: unknown, cycle: FootworkCycleEvidence | null, id: string, eventId: string): CardCycleAnnotation | null {
  const row = obj(value), binding = obj(row.binding), window = windowOf(row.requested_window_ms);
  const start = integer(row.start_source_frame), end = integer(row.end_source_frame), uncertainty = number(binding.uncertainty_ms);
  if (!cycle || !window || start === null || end === null || start >= end || uncertainty === null || uncertainty < 0
      || row.document_version !== "footwork-cycle-annotations-v1.0.0" || !/^[a-f0-9]{64}$/i.test(text(row.document_sha256))
      || row.event_id !== eventId || row.event_code !== id.split("-")[0]
      || start !== cycle.step_window_binding.start_source_frame || end !== cycle.step_window_binding.end_source_frame
      || binding.method !== "manual_anchor" || !text(binding.version) || !text(binding.source)
      || binding.version !== cycle.step_window_binding.version || binding.source !== cycle.step_window_binding.evidence_reference
      || binding.semantics !== "manual_visual_candidate_window_not_expert_technique_or_contact_truth"
      || Math.abs(window.start_ms - Number(cycle.source_timestamps_ms[0])) > .51
      || Math.abs(window.end_ms - Number(cycle.source_timestamps_ms.at(-1))) > .51) return null;
  return { document_sha256: text(row.document_sha256), document_version: text(row.document_version), event_id: eventId,
    event_code: text(row.event_code), start_source_frame: start, end_source_frame: end, requested_window_ms: window,
    binding: { method: "manual_anchor", version: text(binding.version), source: text(binding.source), uncertainty_ms: uncertainty, semantics: text(binding.semantics) } };
}
function instanceOf(value: unknown, id: string, sourceRunId: string, reportPolicy: string | null): CardInstance | null {
  const row = obj(value), evidence = obj(row.temporal_evidence ?? row.evidence), quality = obj(row.quality), source = obj(row.source);
  if (row.indicator_id !== id || !text(row.event_id) || !text(row.video_id) || integer(row.person_track_id) === null || sourceRunId && row.video_id !== sourceRunId) return null;
  const total = integer(quality.total_frame_count) ?? 0, valid = integer(quality.valid_frame_count) ?? 0;
  const fallbackRaw = obj(evidence.fallback ?? row.fallback);
  const fallback = text(fallbackRaw.trigger) ? { trigger: text(fallbackRaw.trigger), strategy: text(fallbackRaw.strategy), selected: windowOf(fallbackRaw.selected) } : null;
  const conditions = list(row.conditions, 30).map(conditionOf);
  const cycle = FOOTWORK_CARD_CONTRACTS[id] ? footworkCycleOf(row.foot_cycle ?? evidence) : null;
  const cycleValues = obj(row.cycle_measurements ?? row.measurements);
  const auxiliary = row.auxiliary_evidence === undefined || row.auxiliary_evidence === null ? null : evidenceObject(row.auxiliary_evidence) ?? {};
  const auxiliaryParent = auxiliary ? evidenceObject(row.auxiliary_parent_context ?? { action_id: row.action_id ?? "", window: row.window ?? {},
    ...(obj(row.source_annotation).requested_window_ms !== undefined ? { requested_window_ms: obj(row.source_annotation).requested_window_ms } : {}) }) : null;
  const policy = text(row.scoring_policy_version) || null, formal = isFormalScoringPolicy(reportPolicy) && policy === reportPolicy;
  const state = row.status === "scored" ? "scored" : row.status === "proxy_scored" ? "proxy_scored" : row.status === "review_required" && !formal ? "review_required" : "unavailable";
  const result: CardInstance = { indicator_id: id, event_id: text(row.event_id), video_id: text(row.video_id), person_track_id: Number(row.person_track_id),
    rule_version: text(row.rule_version), measurement_version: text(row.measurement_version), status: state, original_grade: null, source_grade_supported: false,
    grade: null, proxy_grade: null, score: null, scoring_policy_version: policy, advisory_reason_codes: strings(row.advisory_reason_codes), advisory_flags: strings(row.advisory_flags),
    window: windowOf(row.window ?? evidence.requested_window_ms), reason_codes: strings(row.reason_codes), conditions,
    quality: { total_frame_count: total, valid_frame_count: valid <= total ? valid : 0, fraction: total > 0 && valid <= total ? valid / total : null, visibility_verified: false, required_joint_names: strings(quality.required_joint_names) },
    source_stage: sourceStageOf(row, id),
    source_stage_evidence: GS_CARD_CONTRACTS[id] ? evidenceObject(row.source_stage_evidence) : null,
    source_stage_context: GS_CARD_CONTRACTS[id] ? evidenceObject(row.source_stage_context ?? row.window) : null,
    foot_cycle: cycle,
    cycle_measurements: FOOTWORK_CARD_CONTRACTS[id] ? Object.fromEntries(conditions.map(c => [c.metric, number(cycleValues[c.metric])])) : null,
    source_cycle_annotation: FOOTWORK_CARD_CONTRACTS[id] ? cycleAnnotationOf(row.source_cycle_annotation ?? row.source_annotation, cycle, id, text(row.event_id)) : null,
    racket_side_evidence: GS_CARD_CONTRACTS[id] ? racketSideOf(row) : null,
    source_window_annotation: FS_WINDOW_CONTRACTS[id] ? evidenceObject(row.source_window_annotation) : null,
    source_window_evidence: FS_WINDOW_CONTRACTS[id] ? evidenceObject(row.source_window_evidence) : null,
    source_window_context: FS_WINDOW_CONTRACTS[id] && row.source_window_annotation ? evidenceObject(row.source_window_context ?? row.window) : null,
    application_rule_version: text(row.application_rule_version) || null, directional_micro_validation: null,
    measurement_provenance: evidenceObject(row.measurement_provenance),
    model_scoring_policy_version: text(row.model_scoring_policy_version) || null,
    temporal_evidence: TEMPORAL_CARD_CONDITIONS[id] ? evidenceObject(row.temporal_evidence ?? row.evidence) : null, local_dip_validation: null,
    auxiliary_evidence: auxiliary, auxiliary_parent_context: auxiliaryParent, auxiliary_validation: null, step_motion_validation: null,
    limitations: [...new Set([...(FOOTWORK_CARD_CONTRACTS[id] ? [FOOTWORK_CARD_CONTRACTS[id].limitation] : []), ...strings(row.limitation_codes ?? row.limitations)].map(value => limitationLabel(value, formal)))],
    limitation_codes: strings(row.limitation_codes ?? row.limitations),
    fallback, threshold_basis: text(row.threshold_basis), semantics: text(row.semantics), source: { document_id: text(source.document_id), document_sha256: text(source.document_sha256), table: text(source.table), rubric_sha256: text(source.rubric_sha256) } };
  if (row.original_grade !== null || row.source_grade_supported !== false) return invalidate(result, "source_grade_claim_not_supported");
  if (policy !== reportPolicy || reportPolicy !== null && !isFormalScoringPolicy(reportPolicy)
      || state === "scored" && !formal || state === "proxy_scored" && formal) return invalidate(result, "scoring_policy_mismatch");
  if (state !== "proxy_scored" && state !== "scored") return withPreloadValidation({ ...result, conditions: conditions.map(c => ({ ...c, passed: null })) });
  const gsSpec = GS_CARD_CONTRACTS[id];
  const footworkSpec = FOOTWORK_CARD_CONTRACTS[id];
  const fsWindowSpec = FS_WINDOW_CONTRACTS[id];
  const micro = directionalMicroApplies(id, row);
  const specs: ConditionSpec[] | undefined = micro ? microContract.conditions.map(c => [c.metric, c.operator, c.threshold, c.unit]) as ConditionSpec[]
    : gsSpec?.conditions ?? footworkSpec?.conditions ?? fsWindowSpec?.conditions ?? TEMPORAL_CARD_CONDITIONS[id], byMetric = new Map(conditions.map(c => [c.metric, c]));
  const expected = specs?.every(([metric, operator, threshold, unit]) => {
    const c = byMetric.get(metric);
    return c && c.operator === operator && c.unit === unit && JSON.stringify(c.threshold) === JSON.stringify(threshold) && c.passed !== null && c.passed === computedPass(c);
  });
  const fraction = conditions.length ? conditions.filter(c => c.passed === true).length / conditions.length : 0;
  const grade: Grade = fraction === 1 ? "A" : fraction >= .75 ? "B" : fraction >= .5 ? "C" : fraction > 0 ? "D" : "E";
  if (!sourceRunId || !specs || conditions.length !== specs.length || byMetric.size !== specs.length || !expected || !result.window || fallback?.selected && (!formal || validateTemporalLocalDip(result.temporal_evidence, result)?.status !== "measured_local_dip")
      || result.rule_version !== CARD_RULE_VERSION || result.measurement_version !== (micro ? microContract.measurement_version : gsSpec ? gsContract.measurement_version : footworkSpec ? footworkContract.measurement_version : fsWindowSpec ? fsWindowContract.measurement_version : MEASUREMENT_VERSION)
      || formal && row.grade !== grade || row.proxy_grade !== grade || row.score !== gradePoints[grade]
      || gsSpec && (gsSpec.rule_kind !== "visual_proxy" || !validGsEvidence(result, gsSpec)
        || formal && !validFormalGsStageSource(result.source_stage_evidence, result.source_stage_context, result)
        || !validRacketSide(result.racket_side_evidence, result, result.source_stage, conditions.some(c => c.metric === "wrist_speed_increase_torso_s")))
      || micro && (!formal || result.application_rule_version !== microContract.application_rule_version
        || microContract.conditions.some(spec => byMetric.get(spec.metric)?.key !== spec.key)
        || result.threshold_basis !== "application_thresholds_uncalibrated_not_source_or_literature_standard"
        || !Object.entries((microContract.cards as Record<string, { source: CardInstance["source"] }>)[id].source).every(([key, value]) => result.source[key as keyof CardInstance["source"]] === value)
        || validateDirectionalMicro(result.source_window_annotation, result.source_window_evidence, result.source_window_context, result)?.status !== "measured_directional_motion")
      || fsWindowSpec && !micro && (fsWindowSpec.rule_kind !== "visual_proxy"
        || result.threshold_basis !== "engineering_v1_uncalibrated"
        || !Object.entries(fsWindowSpec.source).every(([key, value]) => result.source[key as keyof CardInstance["source"]] === value)
        || JSON.stringify([...result.quality.required_joint_names].sort()) !== JSON.stringify(fsWindowSpec.required_joint_names)
        || !validFsWindow(result.source_window_annotation, result.source_window_evidence, result.source_window_context, result))
      || footworkSpec && (footworkSpec.rule_kind !== "visual_proxy"
        || !Object.entries(footworkSpec.source).every(([key, value]) => result.source[key as keyof CardInstance["source"]] === value)
        || JSON.stringify([...result.quality.required_joint_names].sort()) !== JSON.stringify(footworkSpec.required_joint_names)
        || !validFootworkCycle(result.foot_cycle, { ...result, total_frames: total, valid_frames: valid })
        || !result.cycle_measurements || conditions.some(c => c.value !== result.cycle_measurements![c.metric])
        || !footworkValuesAgree(result.foot_cycle!, result.cycle_measurements!)
        || !!(row.source_cycle_annotation ?? row.source_annotation) && !result.source_cycle_annotation)
      || formal && !!TEMPORAL_CARD_CONDITIONS[id] && !validFormalTemporalSource(result.temporal_evidence, result)
      || total < 3 || valid > total || valid * 10 < total * 7 || result.reason_codes.length
      || row.semantics !== (formal ? formalScoreSemantics : "engineering_visual_condition_completion_not_original_technical_grade")
      || !/^CARD-(?:FS|GS)$/.test(result.source.document_id) || !/^[a-fA-F0-9]{64}$/.test(result.source.document_sha256) || !/^[a-fA-F0-9]{64}$/.test(result.source.rubric_sha256)) {
    return invalidate(result, "proxy_report_evidence_invalid_recompute_required");
  }
  return withPreloadValidation({ ...result, grade: formal ? grade : null, proxy_grade: grade, score: gradePoints[grade] });
}

export function cardExecutionOf(value: unknown): CardExecutionReport | null {
  const raw = obj(value);
  if (raw.version !== CARD_EXECUTION_VERSION || raw.rule_version !== CARD_RULE_VERSION || !Array.isArray(raw.cards) || raw.cards.length > 1000) return null;
  if (raw.cards.reduce((sum: number, row: unknown) => sum + (Array.isArray(obj(row).instances) ? (obj(row).instances as unknown[]).length : 0), 0) > 30000
      || raw.cards.some(row => Array.isArray(obj(row).instances) && (obj(row).instances as unknown[]).length > 5000)) return null;
  const sourceRunId = text(raw.source_run_id), reportPolicy = text(raw.scoring_policy_version) || null, formal = isFormalScoringPolicy(reportPolicy);
  const microExecution = evidenceObject(raw.directional_micro_adjustment_execution ?? obj(raw.input).directional_micro_adjustment_execution);
  const microExecutionConflict = raw.directional_micro_adjustment_execution != null && obj(raw.input).directional_micro_adjustment_execution != null
    && !equalEvidence(raw.directional_micro_adjustment_execution, obj(raw.input).directional_micro_adjustment_execution);
  const microWindows = list(raw.directional_micro_adjustment_windows).map(evidenceObject).filter((row): row is EvidenceObject => row !== null);
  const cards: ExecutedCard[] = [], seenCards = new Map<string, ExecutedCard>();
  let conflicts = 0, rejected = 0;
  for (const candidate of raw.cards) {
    const row = obj(candidate), id = text(row.indicator_id);
    if (!CARD_IDS.has(id) || row.event_code !== id.split("-")[0] || row.rule_version !== CARD_RULE_VERSION || !text(row.name_zh) || !["visual_proxy", "manual_review"].includes(text(row.rule_kind))) { rejected++; continue; }
    const micro = id in microContract.cards && row.application_rule_version === microContract.application_rule_version && row.runtime_engine === microContract.runtime_engine;
    const runtimeEngine = micro ? microContract.runtime_engine : GS_CARD_CONTRACTS[id] ? gsContract.runtime_engine : FOOTWORK_CARD_CONTRACTS[id] ? footworkContract.runtime_engine : FS_WINDOW_CONTRACTS[id] ? fsWindowContract.runtime_engine : MEASUREMENT_VERSION;
    if (GS_CARD_CONTRACTS[id] && row.rule_kind !== GS_CARD_CONTRACTS[id].rule_kind) { rejected++; continue; }
    if (FOOTWORK_CARD_CONTRACTS[id] && row.rule_kind !== FOOTWORK_CARD_CONTRACTS[id].rule_kind) { rejected++; continue; }
    if (FS_WINDOW_CONTRACTS[id] && row.rule_kind !== FS_WINDOW_CONTRACTS[id].rule_kind) { rejected++; continue; }
    const seenInstances = new Map<string, CardInstance>(), conflicting = new Set<string>();
    for (const value of list(row.instances, 5000)) {
      const instance = instanceOf(value, id, sourceRunId, reportPolicy);
      if (!instance) continue;
      const key = `${instance.video_id}\u0000${instance.event_id}\u0000${instance.person_track_id}`;
      const prior = seenInstances.get(key);
      if (prior && JSON.stringify(prior) !== JSON.stringify(instance)) conflicting.add(key);
      seenInstances.set(key, instance);
    }
    conflicts += conflicting.size;
    const instances = [...seenInstances].map(([key, instance]) => conflicting.has(key) ? invalidate(instance, "conflicting_duplicate_event_instance") : instance)
      .map(instance => raw.status === "unavailable" ? invalidate(instance, "card_execution_report_unavailable") : instance)
      .map(instance => instance.status === "scored" && instance.directional_micro_validation && (microExecutionConflict || !validMicroReportCopies(instance, microExecution, microWindows))
        ? invalidate(instance, "directional_micro_report_source_mismatch") : instance)
      .map(instance => ["scored", "proxy_scored"].includes(instance.status) && (row.runtime_engine !== runtimeEngine || (micro ? row.rule_kind !== "manual_review" || instance.application_rule_version !== microContract.application_rule_version : row.rule_kind !== "visual_proxy")) ? invalidate(instance, "proxy_report_evidence_invalid_recompute_required") : instance);
    const fallbackStatus: CardRunStatus = row.run_status === "unavailable" ? "unavailable" : row.run_status === "not_observed" ? "not_observed" : row.rule_kind === "manual_review" || row.run_status === "review_required" ? formal ? "unavailable" : "review_required" : "not_executed";
    const status: CardRunStatus = instances.some(i => i.status === "scored") ? "scored" : instances.some(i => i.status === "proxy_scored") ? "proxy_scored" : instances.some(i => i.status === "review_required") ? "review_required" : instances.length ? "unavailable" : fallbackStatus;
    const card: ExecutedCard = { indicator_id: id, event_code: text(row.event_code), name_zh: text(row.name_zh), rule_version: CARD_RULE_VERSION, rule_kind: text(row.rule_kind),
      runtime_engine: row.runtime_engine === runtimeEngine ? runtimeEngine : null, application_rule_version: text(row.application_rule_version) || null, run_status: status, reason_codes: strings(row.reason_codes), instances, instance_count: instances.length };
    const prior = seenCards.get(id);
    if (prior) {
      if (JSON.stringify(prior) !== JSON.stringify(card)) { prior.run_status = "unavailable"; prior.reason_codes = [...new Set([...prior.reason_codes, "conflicting_duplicate_card"])]; prior.instances = prior.instances.map(i => invalidate(i, "conflicting_duplicate_card")); conflicts++; }
      continue;
    }
    seenCards.set(id, card); cards.push(card);
  }
  const allInstances = cards.flatMap(c => c.instances), counts: Record<string, number> = {};
  const formalCards = cards.map(card => card.instances.filter(instance => instance.status === "scored" && instance.score !== null))
    .filter(instances => instances.length).map(instances => instances.reduce((sum, instance) => sum + instance.score!, 0) / instances.length);
  conflicts = allInstances.filter(instance => instance.reason_codes.includes("conflicting_duplicate_event_instance")).length + cards.filter(card => card.reason_codes.includes("conflicting_duplicate_card")).length;
  for (const card of cards) counts[card.run_status] = (counts[card.run_status] ?? 0) + 1;
  return { version: CARD_EXECUTION_VERSION, rule_version: CARD_RULE_VERSION, source_run_id: sourceRunId, cards, scoring_policy_version: reportPolicy,
    status: raw.status === "unavailable" ? "unavailable" : "available", reason_codes: strings(raw.reason_codes),
    summary: { catalogue_cards: cards.length, expected_source_cards: SOURCE_CARD_COUNT, computed_cards: cards.filter(c => c.instances.length).length,
      formal_scored_cards: formalCards.length, formal_scored_instances: allInstances.filter(i => i.status === "scored").length,
      proxy_scored_cards: cards.filter(c => c.instances.some(i => i.status === "proxy_scored")).length, proxy_scored_instances: allInstances.filter(i => i.status === "proxy_scored").length,
      review_instances: allInstances.filter(i => i.status === "review_required").length, unavailable_instances: allInstances.filter(i => i.status === "unavailable").length,
      original_scored_cards: 0, manual_scored_cards: 0, run_card_status_counts: counts, conflicting_instances: conflicts, rejected_cards: rejected },
    overall_score: formal && formalCards.length ? formalCards.reduce((sum, score) => sum + score, 0) / formalCards.length : null,
    missing_policy: "exclude_from_score_keep_in_coverage_no_missing_zero", limitations: strings(raw.limitation_codes ?? raw.limitations).map(value => limitationLabel(value, formal)),
    limitation_codes: strings(raw.limitation_codes ?? raw.limitations),
    directional_micro_adjustment_execution: microExecution, directional_micro_adjustment_windows: microWindows };
}

function validMicroReportCopies(instance: CardInstance, execution: EvidenceObject | null, windows: EvidenceObject[]): boolean {
  const annotation = instance.source_window_annotation;
  if (!annotation || !execution || execution.version !== microContract.runtime_engine || execution.application_rule_version !== microContract.application_rule_version
      || execution.annotation_version !== microContract.annotation_version || execution.annotation_document_sha256 !== annotation.document_sha256
      || execution.video_sha256 !== annotation.video_sha256 || execution.annotation_digest_kind !== "canonical_json_sorted_utf8_v1"
      || execution.saved_grades_trusted !== false || execution.artifact_files_verified !== true || execution.decoder_verified_from_original_video !== true
      || !list(execution.replaced_card_ids).includes(instance.indicator_id)) return false;
  const candidates = windows.filter(row => row.annotation_id === instance.event_id && row.indicator_id === instance.indicator_id);
  if (candidates.length !== 1) return false;
  const { window, source_frames: frames, ...copy } = candidates[0];
  return equalEvidence(copy, annotation) && equalEvidence(window, instance.source_window_context)
    && equalEvidence(frames, instance.source_window_evidence?.source_frames);
}

export function selectedCardInstance(instances: CardInstance[], selectedEvent: string, formal: boolean): CardInstance | undefined {
  return instances.find(row => `${row.video_id}:${row.event_id}:${row.person_track_id}` === selectedEvent)
    ?? (formal ? instances.find(row => row.status === "scored" && row.score !== null) : undefined) ?? instances[0];
}

function limitationLabel(value: string, formal: boolean): string {
  const label = LIMITATION_LABELS[value] ?? value;
  if (!formal) return label;
  if (label === LIMITATION_LABELS.manual_observable_cards_remain_ungraded) return "该项尚无可计算观测，未生成分数。";
  if (label === LIMITATION_LABELS["Automatic event candidates are not independently verified action labels."]) return "自动动作候选尚未独立确认动作类型，原始来源记录随测量保留。";
  return label;
}

export const CARD_REASON_LABELS: Record<string, string> = {
  model_estimate_measurement_only: "单目模型估计仅保留测量并需复核，不生成代理分",
  fs_card_manual_window_missing: "缺少这张步伐卡的明确人工窗口",
  fs_window_primary_person_binding_unverified: "窗口内主球员绑定尚未核验",
  fs_window_identity_or_camera_epoch_changed: "窗口跨越选人或相机参考分段",
  fs_window_original_quality_identity_unverified: "源质量观测尚未绑定同一球员",
  fs_window_original_identity_requires_review: "原始身份质量标记需要复核",
  fs_window_original_quality_unverified: "缺少已核验的原始质量观测",
  fs_window_original_quality_requires_review: "原始质量观测要求人工复核",
  fs_window_original_quality_coverage_incomplete: "原始质量观测未覆盖完整窗口",
  complete_step_cycle_not_bound: "尚未绑定完整步周期",
  racket_side_unresolved: "测量腕侧别尚未绑定到此阶段",
  event_type_not_observed_in_run: "本次没有观察到对应动作类型", required_observable_needs_manual_review: "该指标需要人工确认画面之外或姿态无法证明的信息",
  runtime_stage_or_metric_binding_missing: "此卡的阶段定位或计算还没有接入本次视频", fallback_local_dip_action_type_unverified: "找到了局部髋部下降，但尚不能确认垫步动作，需要复核",
  required_temporal_value_missing: "缺少必要的连续时间测量", temporal_evidence_incomplete: "动作窗口证据不完整", required_condition_evidence_missing: "必要条件的观测不足",
  joint_union_geometry_below_70_percent: "所需关节同时有效的帧不足70%", proxy_report_evidence_invalid_recompute_required: "报告证据校验未通过，需要重新计算",
  source_grade_claim_not_supported: "此报告不支持原卡技术等级", conflicting_duplicate_event_instance: "同一事件的重复结果冲突，已取消评分", conflicting_duplicate_card: "同一评分卡的重复记录冲突，已取消评分",
  card_execution_report_unavailable: "本次评分卡计算尚不可用，请重新计算或检查输入证据",
  raw_card_execution_evidence_unavailable: "逐卡计算所需的原始帧证据缺失或校验未通过",
  insufficient_window_samples: "该阶段可用的连续采样太少",
  continuous_window_below_80_ms: "连续观测不足80毫秒，无法可靠计算变化",
  pre_takeoff_window_too_short_for_turn_evidence: "起跳候选之前的观测过短，无法确认完整下降过程",
  pre_takeoff_descent_turn_not_observed: "起跳候选之前未观察到完整下降和转折",
  descent_turn_boundary_censored: "下降过程的转折靠近片段边界，前后证据不完整",
  source_preload_start_boundary_censored: "预加载起点被片段边界截断",
  required_joint_coverage_below_70_percent: "所需关节同时有效的帧不足70%",
  required_joint_gap_inside_continuous_window: "连续窗口内有必要关节缺测，未跨过缺口推算",
  torso_below_12_source_pixels: "画面中的躯干小于12像素，尺度测量不足",
  temporal_boundary_quality_requires_review: "事件边界或姿态覆盖不足，需要复核时间窗",
  temporal_required_joint_quality_requires_review: "必要关节存在跳点或左右混淆，需要复核",
  subject_identity_unverified: "无法确认计算窗口中的主球员身份",
  subject_continuity_unverified: "无法确认窗口始终属于同一球员",
  source_clock_binding_unverified: "原始视频时钟无法核验，暂不计算时间变化",
  event_conflicting_or_not_in_snapshot: "事件来源不一致或记录冲突，暂不评分",
  stable_reference_transform_unavailable: "相机参考坐标不可用，暂不计算身体位移",
  exact_window_endpoints_missing: "时间窗起点或终点没有实际观测",
  successor_endpoint_observation_missing: "后继动作边界缺少观测，无法计算衔接间隔",
  gs_stage_annotation_missing: "本次尚未标注此击球阶段，未套用其他阶段的时间窗",
  gs_technique_not_annotated_in_source_run: "本次没有此击球类型的来源标注",
  required_observable_not_recoverable_from_pose: "人体姿态不能直接证明此项技术行为，需要人工复核",
  stage_source_observations_missing: "所标注击球阶段缺少原始帧观测",
  stage_primary_person_binding_unverified: "阶段标注的球员与逐帧选择不一致",
  stage_identity_or_camera_epoch_changed: "击球阶段跨过人物选择或机位切换",
  stage_window_truncated_by_source: "击球阶段时间窗超出可用原始片段",
  source_decoder_window_inventory_incomplete: "击球阶段所需的原始帧不完整，未缩短窗口或跨过缺帧评分",
  source_stage_binding_missing_or_mismatched: "击球阶段来源缺失或与评分卡不匹配",
  source_step_window_binding_required: "缺少可追溯的人工步周期窗口来源",
  automatic_cycle_detector_not_registered: "尚未接入自动步周期定位，不能将外部标记当作已确认周期",
  source_step_window_empty: "人工步周期窗口没有原始帧",
  step_window_binding_identity_or_frame_mismatch: "步周期的事件、球员或原帧边界不一致",
  step_window_source_video_mismatch: "步周期窗口与原视频摘要不一致",
  complete_visual_motion_cycle_not_observed: "尚未观察到前后低运动区间完整的脚部运动周期",
  sequential_lead_follow_cycle_not_observed: "尚未观察到明确的先迈步、再跟随及先扩宽后收窄顺序",
  ankle_order_not_persistently_resolved: "双踝先后顺序或中间换序观测不足，不能用端点推定交叉",
  ball_direction_and_target_evidence_required: "缺少来球与目标区域证据，需要人工复核方向判断",
  continuous_endpoint_window_missing: "连续步周期窗口过短，无法取得可靠的起止观测",
  cycle_required_evidence_requires_review: "本周期必要关节或身份存在质量疑点，需要复核",
  fixed_torso_scale_invalid_or_below_12_pixels: "固定躯干尺度无效或画面尺度不足12像素",
  condition_measurement_unavailable: "本卡仍有条件缺少可计算的实值",
  cycle_family_not_manually_annotated: "本次尚未标注此类步周期窗口",
  cycle_evidence_incomplete: "步周期证据不完整，需要复核",
  required_cycle_measurement_missing: "步周期仍有必要测量缺失，暂不评分",
  required_external_observable_not_recovered_from_pose: "人体姿态不能证明本卡所需的外部方向或目标信息",
  cycle_common_required_joint_contract_mismatch: "步周期所需关节与版本契约不一致，需要重新计算",
  cycle_grade_policy_mismatch: "步周期条件完成度规则与当前版本不一致",
};
const PHASE_LABELS: Record<string, string> = { takeoff_proxy_ms: "起跳候选", landing_proxy_ms: "落地候选", stable_control_onset_ms: "重新稳定候选", peak_speed_ms: "速度峰值", deceleration_peak_ms: "减速峰值" };
const FORMAL_REASON_LABELS: Record<string, string> = {
  fallback_local_dip_action_type_unverified: "局部髋部下降已测量；此证据不确认垫步动作类型",
  temporal_boundary_quality_requires_review: "原事件带有边界或姿态覆盖提示，完整计算子窗的依据随结果保留",
  temporal_required_joint_quality_requires_review: "原必要关节带有跳点或左右混淆候选标记",
  primary_track_coverage_low: "原事件球员轨迹覆盖偏低", primary_pose_coverage_low: "原事件姿态覆盖偏低",
  pose_kinematic_coverage_low: "原事件运动学观测覆盖偏低", keypoint_jump_candidates_present: "原姿态带有关节跳点候选",
  left_right_swap_candidates_present: "原姿态带有左右混淆候选", primary_identity_ambiguous: "选人记录含候选身份歧义标记",
  source_track_switch_candidates_present: "原轨迹含候选切换标记",
  model_estimate_measurement_only: "测量来自单目模型估计，原始模型来源随结果保留",
  fs_window_original_identity_requires_review: "原始身份质量记录含疑点标记", fs_window_original_quality_requires_review: "原始质量记录含疑点标记",
  required_observable_needs_manual_review: "该指标缺少姿态数据以外的必要观测",
  required_observable_not_recoverable_from_pose: "人体姿态不能直接证明此项技术行为",
  ball_direction_and_target_evidence_required: "缺少来球与目标区域观测",
  cycle_required_evidence_requires_review: "本周期必要关节或身份记录含质量疑点", cycle_evidence_incomplete: "步周期证据不完整",
  directional_micro_source_evidence_invalid: "方向微调的来源或数值校验未通过，相关分数已排除",
};
export const cardReasonLabel = (code: string, formal = false) => {
  if (formal === true && FORMAL_REASON_LABELS[code]) return FORMAL_REASON_LABELS[code];
  if (CARD_REASON_LABELS[code]) return CARD_REASON_LABELS[code];
  const [reason, phase] = code.split(":");
  if (["phase_not_observed", "phase_not_observed_or_outside_event"].includes(reason)) return `未定位${PHASE_LABELS[phase] ?? "所需阶段"}边界`;
  if (reason === "phase_boundary_censored") return `${PHASE_LABELS[phase] ?? "所需阶段"}边界被片段截断`;
  return formal === true ? `来源记录：${code}` : "计算证据未通过校验，需要复核原始动作片段";
};
