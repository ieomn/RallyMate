/** Strict reader for frame-recomputed card reports. Summary counts are derived.
 * Temporal contracts and the generated GS reader contract are checked against
 * the backend catalogue. A registered rule is never counted as an execution.
 */
import gsContract from "./gs-card-display-contract";

export const CARD_EXECUTION_VERSION = "card-execution-v1.0.0";
export const CARD_RULE_VERSION = "card-rules-v1.0.0";
const MEASUREMENT_VERSION = "temporal-card-evidence-v1.0.0";
export const SOURCE_CARD_COUNT = 298;
export const CARD_RUN_LABELS = { proxy_scored: "有视觉代理结果", review_required: "需要复核", unavailable: "证据不足", not_executed: "尚未接入计算", not_observed: "未观察到此类动作" } as const;
export type CardRunStatus = keyof typeof CARD_RUN_LABELS;
type Obj = Record<string, unknown>;
type ConditionSpec = [string, "gte" | "lte" | "between", number | [number, number], string];
type GsSpec = { stage_code: string; rule_kind: string; required_joint_names: string[]; conditions: ConditionSpec[]; source: CardInstance["source"] };
const GS_CARD_CONTRACTS = gsContract.cards as unknown as Record<string, GsSpec>;
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
const gradePoints = { A: 100, B: 80, C: 60, D: 40, E: 20 } as const;
type Grade = keyof typeof gradePoints;
export interface CardCondition { key: string; metric: string; value: number | null; operator: string; threshold: number | [number, number] | null; unit: string; passed: boolean | null }
export interface CardSourceStage {
  stage_code: string; annotation_id: string; document_sha256: string; digest_kind: string;
  binding: { method: string; version: string; source: string; uncertainty_ms: number | null; semantics: string };
}
export interface CardInstance {
  indicator_id: string; event_id: string; video_id: string; person_track_id: number; rule_version: string; measurement_version: string;
  status: "proxy_scored" | "review_required" | "unavailable"; original_grade: null; source_grade_supported: false; proxy_grade: Grade | null; score: number | null;
  window: { start_ms: number; end_ms: number } | null; reason_codes: string[]; conditions: CardCondition[];
  quality: { total_frame_count: number; valid_frame_count: number; fraction: number | null; visibility_verified: false; required_joint_names: string[] };
  source_stage: CardSourceStage | null;
  fallback: { trigger: string; strategy: string; selected: { start_ms: number; end_ms: number } | null } | null;
  threshold_basis: string; semantics: string; source: { document_id: string; document_sha256: string; table: string; rubric_sha256: string };
}
export interface ExecutedCard { indicator_id: string; event_code: string; name_zh: string; rule_version: string; rule_kind: string; runtime_engine: string | null; run_status: CardRunStatus; reason_codes: string[]; instances: CardInstance[]; instance_count: number }
export interface CardExecutionReport {
  version: typeof CARD_EXECUTION_VERSION; rule_version: typeof CARD_RULE_VERSION; source_run_id: string; cards: ExecutedCard[];
  status: "available" | "unavailable"; reason_codes: string[];
  summary: { catalogue_cards: number; expected_source_cards: number; computed_cards: number; proxy_scored_cards: number; proxy_scored_instances: number; review_instances: number; unavailable_instances: number; original_scored_cards: 0; manual_scored_cards: 0; run_card_status_counts: Record<string, number>; conflicting_instances: number; rejected_cards: number };
  overall_score: null; missing_policy: "exclude_from_score_keep_in_coverage_no_missing_zero"; limitations: string[];
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
  return condition.operator === "gte" ? condition.value >= condition.threshold : condition.operator === "lte" ? condition.value <= condition.threshold : null;
}
function invalidate(instance: CardInstance, reason: string): CardInstance {
  return { ...instance, status: "unavailable", proxy_grade: null, score: null, reason_codes: [...new Set([...instance.reason_codes, reason])], conditions: instance.conditions.map(c => ({ ...c, passed: null })) };
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
function instanceOf(value: unknown, id: string, sourceRunId: string): CardInstance | null {
  const row = obj(value), evidence = obj(row.evidence), quality = obj(row.quality), source = obj(row.source);
  if (row.indicator_id !== id || !text(row.event_id) || !text(row.video_id) || integer(row.person_track_id) === null || sourceRunId && row.video_id !== sourceRunId) return null;
  const total = integer(quality.total_frame_count) ?? 0, valid = integer(quality.valid_frame_count) ?? 0;
  const fallbackRaw = obj(row.fallback ?? evidence.fallback);
  const fallback = text(fallbackRaw.trigger) ? { trigger: text(fallbackRaw.trigger), strategy: text(fallbackRaw.strategy), selected: windowOf(fallbackRaw.selected) } : null;
  const conditions = list(row.conditions, 30).map(conditionOf);
  const state = row.status === "proxy_scored" ? "proxy_scored" : row.status === "review_required" ? "review_required" : "unavailable";
  const result: CardInstance = { indicator_id: id, event_id: text(row.event_id), video_id: text(row.video_id), person_track_id: Number(row.person_track_id),
    rule_version: text(row.rule_version), measurement_version: text(row.measurement_version), status: state, original_grade: null, source_grade_supported: false,
    proxy_grade: null, score: null, window: windowOf(row.window ?? evidence.requested_window_ms), reason_codes: strings(row.reason_codes), conditions,
    quality: { total_frame_count: total, valid_frame_count: valid <= total ? valid : 0, fraction: total > 0 && valid <= total ? valid / total : null, visibility_verified: false, required_joint_names: strings(quality.required_joint_names) },
    source_stage: sourceStageOf(row, id),
    fallback, threshold_basis: text(row.threshold_basis), semantics: text(row.semantics), source: { document_id: text(source.document_id), document_sha256: text(source.document_sha256), table: text(source.table), rubric_sha256: text(source.rubric_sha256) } };
  if (row.original_grade !== null || row.source_grade_supported !== false) return invalidate(result, "source_grade_claim_not_supported");
  if (state !== "proxy_scored") return { ...result, conditions: conditions.map(c => ({ ...c, passed: null })) };
  const gsSpec = GS_CARD_CONTRACTS[id];
  const specs = gsSpec?.conditions ?? TEMPORAL_CARD_CONDITIONS[id], byMetric = new Map(conditions.map(c => [c.metric, c]));
  const expected = specs?.every(([metric, operator, threshold, unit]) => {
    const c = byMetric.get(metric);
    return c && c.operator === operator && c.unit === unit && JSON.stringify(c.threshold) === JSON.stringify(threshold) && c.passed !== null && c.passed === computedPass(c);
  });
  const fraction = conditions.length ? conditions.filter(c => c.passed === true).length / conditions.length : 0;
  const grade: Grade = fraction === 1 ? "A" : fraction >= .75 ? "B" : fraction >= .5 ? "C" : fraction > 0 ? "D" : "E";
  if (!sourceRunId || !specs || conditions.length !== specs.length || byMetric.size !== specs.length || !expected || !result.window || fallback?.selected
      || result.rule_version !== CARD_RULE_VERSION || result.measurement_version !== (gsSpec ? gsContract.measurement_version : MEASUREMENT_VERSION) || row.proxy_grade !== grade || row.score !== gradePoints[grade]
      || gsSpec && (gsSpec.rule_kind !== "visual_proxy" || !validGsEvidence(result, gsSpec))
      || total < 3 || valid > total || valid * 10 < total * 7 || result.reason_codes.length
      || row.semantics !== "engineering_visual_condition_completion_not_original_technical_grade"
      || !/^CARD-(?:FS|GS)$/.test(result.source.document_id) || !/^[a-fA-F0-9]{64}$/.test(result.source.document_sha256) || !/^[a-fA-F0-9]{64}$/.test(result.source.rubric_sha256)) {
    return invalidate(result, "proxy_report_evidence_invalid_recompute_required");
  }
  return { ...result, proxy_grade: grade, score: gradePoints[grade] };
}

export function cardExecutionOf(value: unknown): CardExecutionReport | null {
  const raw = obj(value);
  if (raw.version !== CARD_EXECUTION_VERSION || raw.rule_version !== CARD_RULE_VERSION || !Array.isArray(raw.cards) || raw.cards.length > 1000) return null;
  if (raw.cards.reduce((sum: number, row: unknown) => sum + (Array.isArray(obj(row).instances) ? (obj(row).instances as unknown[]).length : 0), 0) > 30000
      || raw.cards.some(row => Array.isArray(obj(row).instances) && (obj(row).instances as unknown[]).length > 5000)) return null;
  const sourceRunId = text(raw.source_run_id), cards: ExecutedCard[] = [], seenCards = new Map<string, ExecutedCard>();
  let conflicts = 0, rejected = 0;
  for (const candidate of raw.cards) {
    const row = obj(candidate), id = text(row.indicator_id);
    if (!CARD_IDS.has(id) || row.event_code !== id.split("-")[0] || row.rule_version !== CARD_RULE_VERSION || !text(row.name_zh) || !["visual_proxy", "manual_review"].includes(text(row.rule_kind))) { rejected++; continue; }
    const runtimeEngine = GS_CARD_CONTRACTS[id] ? gsContract.runtime_engine : MEASUREMENT_VERSION;
    if (GS_CARD_CONTRACTS[id] && row.rule_kind !== GS_CARD_CONTRACTS[id].rule_kind) { rejected++; continue; }
    const seenInstances = new Map<string, CardInstance>(), conflicting = new Set<string>();
    for (const value of list(row.instances, 5000)) {
      const instance = instanceOf(value, id, sourceRunId);
      if (!instance) continue;
      const key = `${instance.video_id}\u0000${instance.event_id}\u0000${instance.person_track_id}`;
      const prior = seenInstances.get(key);
      if (prior && JSON.stringify(prior) !== JSON.stringify(instance)) conflicting.add(key);
      seenInstances.set(key, instance);
    }
    conflicts += conflicting.size;
    const instances = [...seenInstances].map(([key, instance]) => conflicting.has(key) ? invalidate(instance, "conflicting_duplicate_event_instance") : instance)
      .map(instance => raw.status === "unavailable" ? invalidate(instance, "card_execution_report_unavailable") : instance)
      .map(instance => instance.status === "proxy_scored" && (row.runtime_engine !== runtimeEngine || row.rule_kind !== "visual_proxy") ? invalidate(instance, "proxy_report_evidence_invalid_recompute_required") : instance);
    const fallbackStatus: CardRunStatus = row.run_status === "unavailable" ? "unavailable" : row.run_status === "not_observed" ? "not_observed" : row.rule_kind === "manual_review" || row.run_status === "review_required" ? "review_required" : "not_executed";
    const status: CardRunStatus = instances.some(i => i.status === "proxy_scored") ? "proxy_scored" : instances.some(i => i.status === "review_required") ? "review_required" : instances.length ? "unavailable" : fallbackStatus;
    const card: ExecutedCard = { indicator_id: id, event_code: text(row.event_code), name_zh: text(row.name_zh), rule_version: CARD_RULE_VERSION, rule_kind: text(row.rule_kind),
      runtime_engine: row.runtime_engine === runtimeEngine ? runtimeEngine : null, run_status: status, reason_codes: strings(row.reason_codes), instances, instance_count: instances.length };
    const prior = seenCards.get(id);
    if (prior) {
      if (JSON.stringify(prior) !== JSON.stringify(card)) { prior.run_status = "unavailable"; prior.reason_codes = [...new Set([...prior.reason_codes, "conflicting_duplicate_card"])]; prior.instances = prior.instances.map(i => invalidate(i, "conflicting_duplicate_card")); conflicts++; }
      continue;
    }
    seenCards.set(id, card); cards.push(card);
  }
  const allInstances = cards.flatMap(c => c.instances), counts: Record<string, number> = {};
  conflicts = allInstances.filter(instance => instance.reason_codes.includes("conflicting_duplicate_event_instance")).length + cards.filter(card => card.reason_codes.includes("conflicting_duplicate_card")).length;
  for (const card of cards) counts[card.run_status] = (counts[card.run_status] ?? 0) + 1;
  return { version: CARD_EXECUTION_VERSION, rule_version: CARD_RULE_VERSION, source_run_id: sourceRunId, cards,
    status: raw.status === "unavailable" ? "unavailable" : "available", reason_codes: strings(raw.reason_codes),
    summary: { catalogue_cards: cards.length, expected_source_cards: SOURCE_CARD_COUNT, computed_cards: cards.filter(c => c.instances.length).length,
      proxy_scored_cards: cards.filter(c => c.instances.some(i => i.status === "proxy_scored")).length, proxy_scored_instances: allInstances.filter(i => i.status === "proxy_scored").length,
      review_instances: allInstances.filter(i => i.status === "review_required").length, unavailable_instances: allInstances.filter(i => i.status === "unavailable").length,
      original_scored_cards: 0, manual_scored_cards: 0, run_card_status_counts: counts, conflicting_instances: conflicts, rejected_cards: rejected },
    overall_score: null, missing_policy: "exclude_from_score_keep_in_coverage_no_missing_zero", limitations: strings(raw.limitations) };
}

export const CARD_REASON_LABELS: Record<string, string> = {
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
};
const PHASE_LABELS: Record<string, string> = { takeoff_proxy_ms: "起跳候选", landing_proxy_ms: "落地候选", stable_control_onset_ms: "重新稳定候选", peak_speed_ms: "速度峰值", deceleration_peak_ms: "减速峰值" };
export const cardReasonLabel = (code: string) => {
  if (CARD_REASON_LABELS[code]) return CARD_REASON_LABELS[code];
  const [reason, phase] = code.split(":");
  if (["phase_not_observed", "phase_not_observed_or_outside_event"].includes(reason)) return `未定位${PHASE_LABELS[phase] ?? "所需阶段"}边界`;
  if (reason === "phase_boundary_censored") return `${PHASE_LABELS[phase] ?? "所需阶段"}边界被片段截断`;
  return "计算证据未通过校验，需要复核原始动作片段";
};
