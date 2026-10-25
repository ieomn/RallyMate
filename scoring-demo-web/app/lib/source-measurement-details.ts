import { hasModelMeasurementContext, modelMeasurementEvidenceOf, type ModelMeasurementEvidence } from "./model-measurement";

/** Bounded display evidence only. These fields never authorize a grade. */
export type SourceCoverage = {
  scope: string; required_joint_names: string[]; valid_frame_count: number; total_frame_count: number;
  fraction: number; threshold: number; operator: ">="; passes: boolean; source_rule?: string;
  group_semantics?: string; interpretation?: string;
  point_validity?: { minimum_confidence: number; semantics: string };
  groups?: Array<{ joint_names: string[]; valid_frame_count: number; total_frame_count: number; fraction: number; meets_70_percent: boolean }>;
};
type EvidenceValue = string | number | boolean | (number | null)[] | string[] | number[][] | SourceEvidence;
export type SourceEvidence = { [key: string]: EvidenceValue };
export type ModelGeometryCoverage = {
  semantics: "model_estimate_geometry_not_verified_visibility"; required_joint_names: string[];
  valid_frame_count: number; total_frame_count: number; fraction: number; passes: boolean;
  threshold: number; operator: ">="; visibility_verified: false; source_visibility_70_percent_verified: null;
};
export type SourceMeasurementDetails = Partial<ModelMeasurementEvidence> & {
  measurement_definition?: string; definition?: string; source_ref?: string; source_frames?: number[];
  coverage?: number | SourceCoverage; evidence?: SourceEvidence;
  coverage_semantics?: "model_estimate_sample_coverage_not_verified_visibility";
  model_geometry_coverage?: ModelGeometryCoverage;
  required_joints_used?: string[]; selected_side?: "left" | "right";
};
const object = (value: unknown): Record<string, unknown> => value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const integer = (value: unknown): value is number => Number.isSafeInteger(value) && Number(value) >= 0;
const text = (value: unknown, limit = 2000): string => typeof value === "string" && value.length <= limit
  ? Array.from(value, char => char.charCodeAt(0) < 32 || char.charCodeAt(0) === 127 ? " " : char).join("").replace(/\s+/g, " ").trim() : "";
const frames = (value: unknown): value is number[] => Array.isArray(value) && value.length > 0 && value.length <= 20000 && value.every(integer);
const joints = (value: unknown): value is string[] => Array.isArray(value) && value.length > 0 && value.length <= 200 && value.every(name => typeof name === "string" && /^[a-z][a-z0-9_]{0,63}$/.test(name)) && new Set(value).size === value.length;

function modelGeometryCoverageOf(raw: unknown, proof: ModelMeasurementEvidence): ModelGeometryCoverage | undefined {
  const item = object(raw), names = item.required_joint_names;
  if (item.semantics !== "model_estimate_geometry_not_verified_visibility" || item.visibility_verified !== false
    || item.source_visibility_70_percent_verified !== null || !joints(names)
    || names.some(name => !proof.geometry_mask[name]) || item.threshold !== .7 || item.operator !== ">=") return undefined;
  const total = (proof.source_window.source_frames as number[]).length;
  const valid = Array.from({ length: total }, (_, i) => names.every(name => proof.geometry_mask[name][i])).filter(Boolean).length;
  const fraction = valid / total, passes = valid * 10 >= total * 7;
  if (item.total_frame_count !== total || item.valid_frame_count !== valid || !finite(item.fraction)
    || Math.abs(item.fraction - fraction) > 1e-6 || item.passes !== passes) return undefined;
  return { semantics: "model_estimate_geometry_not_verified_visibility", required_joint_names: [...names],
    valid_frame_count: valid, total_frame_count: total, fraction, passes, threshold: .7, operator: ">=",
    visibility_verified: false, source_visibility_70_percent_verified: null };
}

function coverageOf(value: unknown): SourceCoverage | undefined {
  const item = object(value), valid = item.valid_frame_count, total = item.total_frame_count, fraction = item.fraction;
  if (!integer(valid) || !integer(total) || total === 0 || valid > total || !finite(fraction) || fraction < 0 || fraction > 1
    || Math.abs(fraction - valid / total) > 1e-6 || !joints(item.required_joint_names)
    || item.threshold !== .7 || item.operator !== ">=" || item.passes !== (valid * 10 >= total * 7)
    || item.scope !== "inclusive_window_all_sampled_source_frames") return undefined;
  const result: SourceCoverage = { scope: item.scope, required_joint_names: [...item.required_joint_names], valid_frame_count: valid,
    total_frame_count: total, fraction, threshold: .7, operator: ">=", passes: item.passes as boolean };
  for (const key of ["source_rule", "group_semantics", "interpretation"] as const) if (text(item[key])) result[key] = text(item[key]);
  const validity = object(item.point_validity);
  if (finite(validity.minimum_confidence) && validity.minimum_confidence >= 0 && validity.minimum_confidence <= 1 && text(validity.semantics))
    result.point_validity = { minimum_confidence: validity.minimum_confidence, semantics: text(validity.semantics) };
  if (Array.isArray(item.groups) && item.groups.length <= 20) {
    const groups: NonNullable<SourceCoverage["groups"]> = [];
    for (const raw of item.groups) {
      const group = object(raw), count = group.valid_frame_count;
      if (!joints(group.joint_names) || !integer(count) || count > total || group.total_frame_count !== total
        || !finite(group.fraction) || group.fraction < 0 || group.fraction > 1 || Math.abs(group.fraction - count / total) > 1e-6 || group.meets_70_percent !== (count * 10 >= total * 7)) return undefined;
      groups.push({ joint_names: [...group.joint_names], valid_frame_count: count, total_frame_count: total,
        fraction: group.fraction, meets_70_percent: group.meets_70_percent as boolean });
    }
    result.groups = groups;
  }
  return result;
}

const stringKeys = ["formula", "window_scope", "scope", "feature_version", "coordinate_convention", "direction_source",
  "phase_key", "phase_semantics", "phase_contract_version", "status", "repair_reason", "selection_semantics", "support_rule_semantics",
  "denominator", "numerator", "denominator_policy", "derivative_policy", "source_event_id", "successor_event_id", "anchor_phase_key",
  "measurement_version", "coordinate_system", "normalization", "gate_origin", "sign_convention", "time_basis"];
const numberKeys = ["takeoff_proxy_ms", "observed_duration_ms", "angle_period_deg", "body_scale", "original_preload_ms", "source_preload_ms",
  "source_preload_start_ms", "confirmation_end_ms", "minimum_directional_changes_each_side", "numerical_coordinate_epsilon", "valid_ratio_samples",
  "minimum_denominator", "valid_samples", "edge_fraction", "edge_sample_count", "duration_s", "std", "rate", "valid_interval_count", "total_interval_count"];
const booleanKeys = ["gaze_evaluated", "incoming_ball_alignment_evaluated", "technical_stability_evaluated", "incoming_ball_direction_available",
  "direction_correctness_evaluated", "source_clause_complete", "is_ground_truth", "is_metric_or_3d", "original_event_modified", "candidates_remain_unvalidated"];
const nestedKeys = ["computation_diagnostics", "source_phase", "speed_observations", "speed_continuity_observations"];
function evidenceOf(value: unknown, sourceFrames: number[] | undefined, depth = 0, phaseDiagnostic = false): SourceEvidence | undefined {
  const item = object(value), result: SourceEvidence = {};
  const frameSet = sourceFrames ? new Set(sourceFrames) : undefined;
  for (const key of stringKeys) if (text(item[key])) result[key] = text(item[key]);
  for (const key of numberKeys) if (finite(item[key]) && item[key] >= 0) result[key] = item[key];
  // Stable-reference coordinates may be negative after the camera transform.
  // Only these two evidence coordinates admit signed values; counts and clocks do not.
  for (const key of ["hip_reference_y_start_px", "hip_reference_y_end_px"]) if (finite(item[key])) result[key] = item[key];
  for (const key of ["minimum_samples", "source_track_id", "selection_epoch", "camera_reference_epoch"])
    if (integer(item[key]) && (key !== "minimum_samples" || item[key] > 0)) result[key] = item[key];
  for (const key of ["minimum_duration_ms", "fixed_torso_scale_px"]) if (finite(item[key]) && item[key] > 0) result[key] = item[key];
  if (finite(item.source_duration_ms) && item.source_duration_ms >= 0) result.source_duration_ms = item.source_duration_ms;
  const sourceTimes = item.source_timestamps_ms;
  if (sourceFrames && Array.isArray(sourceTimes) && sourceTimes.length === sourceFrames.length && sourceTimes.length <= 20000
    && sourceTimes.every((time, index) => finite(time) && time >= 0 && (index === 0 || time > sourceTimes[index - 1]))) {
    result.source_timestamps_ms = [...sourceTimes];
  }
  const dimensions = item.frame_dimensions_px;
  if (Array.isArray(dimensions) && dimensions.length === 2 && dimensions.every(v => finite(v) && v > 0)) result.frame_dimensions_px = [...dimensions];
  for (const key of booleanKeys) if (typeof item[key] === "boolean") result[key] = item[key];
  for (const key of ["source_frames", "used_source_frames", "endpoint_source_frames"]) {
    const value = item[key];
    // Turning-point confirmation includes later support frames than the metric
    // window. Preserve this declared candidate diagnostic, never as its proof.
    const candidateSupport = phaseDiagnostic && key === "source_frames" && item.phase_contract_version === "source-preload-turning-point-v1.0.0"
      && item.is_ground_truth === false && item.original_event_modified === false && frames(value)
      && value.every((frame, index) => index === 0 || frame > value[index - 1]);
    if (frames(value) && (phaseDiagnostic && key === "source_frames" ? candidateSupport : frameSet && value.every(frame => frameSet.has(frame)))) result[key] = [...value];
  }
  const pairs = item.used_adjacent_source_frame_pairs;
  const orderedFrames = sourceFrames?.every((frame, index) => index === 0 || frame > sourceFrames[index - 1]);
  const positions = orderedFrames ? new Map(sourceFrames!.map((frame, index) => [frame, index])) : undefined;
  if (Array.isArray(pairs) && pairs.length <= 20000 && positions && pairs.every(pair => Array.isArray(pair) && pair.length === 2
    && pair.every(integer) && pair[1] > pair[0] && positions.has(pair[0]) && positions.has(pair[1])
    && positions.get(pair[1]) === positions.get(pair[0])! + 1)) result.used_adjacent_source_frame_pairs = pairs.map(pair => [...pair]);
  for (const key of ["early_position", "late_position", "median_velocity", "composite_vector"]) {
    const value = item[key];
    if (Array.isArray(value) && value.length === 2 && value.every(finite)) result[key] = [...value];
  }
  const timestamps = item.timestamps_ms, speeds = item.speed_body_s;
  if (Array.isArray(timestamps) && timestamps.length <= 20000 && timestamps.every(v => finite(v) && v >= 0)) {
    result.timestamps_ms = [...timestamps];
    // Null marks an unmeasured interval. Keep its index; neither interpolate nor turn it into zero.
    if (Array.isArray(speeds) && speeds.length === timestamps.length && speeds.every(v => v === null || finite(v) && v >= 0)) result.speed_body_s = [...speeds];
  }
  const gate = object(item.source_event_quality_gate);
  if (text(gate.policy_version, 100) && gate.semantics === "existing_measurement_evidence_gate_not_technical_fault_penalty"
    && Array.isArray(gate.hard_fail_flags) && gate.hard_fail_flags.length <= 32
    && gate.hard_fail_flags.every(flag => typeof flag === "string" && /^[a-z][a-z0-9_]{0,99}$/.test(flag))) {
    result.source_event_quality_gate = { policy_version: text(gate.policy_version, 100), semantics: gate.semantics, hard_fail_flags: [...gate.hard_fail_flags] };
  }
  if (depth < 2) for (const key of nestedKeys) { const nested = evidenceOf(item[key], sourceFrames, depth + 1, key === "source_phase"); if (nested) result[key] = nested; }
  return Object.keys(result).length ? result : undefined;
}

export function sourceMeasurementDetailsOf(value: unknown): SourceMeasurementDetails {
  const item = object(value), result: SourceMeasurementDetails = {};
  const model = hasModelMeasurementContext(item);
  const proof = model ? modelMeasurementEvidenceOf(item) : null;
  const sideFeatures = ["first_step_displacement_body", "launch_foot_motion_duration_ms", "launch_foot_slowdown_to_post_hip_direction_ms",
    "launch_foot_speed_drop_body_s", "launch_foot_speed_peak_body_s", "post_step_hip_direction_consistency"];
  const hasSide = "selected_side" in item || "required_joints_used" in item;
  const selectedJoints = item.required_joints_used;
  const validSide = sideFeatures.includes(String(item.feature_name)) && ["left", "right"].includes(String(item.selected_side))
    && Array.isArray(selectedJoints) && selectedJoints.length === 3
    && ["left_hip", "right_hip", `${item.selected_side}_ankle`].every((name, i) => selectedJoints[i] === name);
  if (proof && (!hasSide || validSide)) {
    Object.assign(result, proof);
    if (validSide) { result.required_joints_used = [...item.required_joints_used as string[]]; result.selected_side = item.selected_side as "left" | "right"; }
    const geometry = modelGeometryCoverageOf(item.model_geometry_coverage, proof);
    if (geometry) result.model_geometry_coverage = geometry;
  }
  // Contextual motion has its own declared model-estimate semantics, without a
  // source-card window proof. This branch must never admit FS source/legacy rows.
  const provenance = object(item.measurement_provenance);
  const contextualModel = item.source === "motion_joint_measurement" && !("source_window" in item) && !("geometry_mask" in item)
    && provenance.pose_source === "sam3d" && provenance.observation_semantics === "monocular_model_estimate"
    && provenance.assessment_status === "measurement_only" && provenance.scoring_allowed === false
    && provenance.confidence_available === false && provenance.visibility_verified === false
    && integer(provenance.model_estimate_sample_count) && provenance.model_estimate_sample_count <= 20000;
  if (contextualModel) result.measurement_provenance = {
    pose_source: "sam3d", observation_semantics: "monocular_model_estimate", assessment_status: "measurement_only",
    scoring_allowed: false, confidence_available: false, visibility_verified: false,
    model_estimate_sample_count: provenance.model_estimate_sample_count,
    ...(text(provenance.reason_zh) ? { reason_zh: text(provenance.reason_zh) } : {}),
  };
  for (const key of ["measurement_definition", "definition"] as const) if (text(item[key])) result[key] = text(item[key]);
  const ref = text(item.source_ref, 100);
  if (/^CARD-(?:FS|GS):word\/document\.xml:P\d{4,6}$/.test(ref)) result.source_ref = ref;
  if (frames(item.source_frames)) result.source_frames = [...item.source_frames];
  const sampleCoverage = finite(item.coverage) && item.coverage >= 0 && item.coverage <= 1 ? item.coverage : undefined;
  const coverage = contextualModel ? sampleCoverage : model ? undefined : sampleCoverage ?? coverageOf(item.coverage);
  if (coverage !== undefined) result.coverage = coverage;
  if (contextualModel && coverage !== undefined) result.coverage_semantics = "model_estimate_sample_coverage_not_verified_visibility";
  const evidence = evidenceOf(item.evidence, result.source_frames);
  if (evidence) result.evidence = evidence;
  else if (item.evidence && typeof item.evidence === "object" && !Array.isArray(item.evidence) && Object.keys(item.evidence).length === 0) result.evidence = {};
  return result;
}

/** Concise human-readable evidence, with no raw JSON or stage/quality upgrade. */
export function sourceMeasurementDetailLines(value: SourceMeasurementDetails): string[] {
  const lines: string[] = [];
  const definition = value.measurement_definition || value.definition;
  if (definition) lines.push(`实际计算：${definition}`);
  if (value.source_ref) lines.push(`原文段落：${value.source_ref}`);
  if (value.source_frames?.length) lines.push(`来源帧：${value.source_frames.length} 条引用，首帧 ${value.source_frames[0]}，末帧 ${value.source_frames.at(-1)}；仅使用已记录帧。`);
  if (value.measurement_provenance?.pose_source === "sam3d") {
    lines.push("SAM 单目模型估计；未提供关节置信度，真实可见性未知，不能据此判定原文等级。");
    const coverage = value.model_geometry_coverage;
    if (coverage) lines.push(`模型几何覆盖：${coverage.valid_frame_count}/${coverage.total_frame_count} 帧具备所需坐标；70% 为计算支持门槛，不代表原文可见性已验证。`);
  }
  if (value.coverage && typeof value.coverage === "object") lines.push(`局部窗口关节：${value.coverage.valid_frame_count}/${value.coverage.total_frame_count} 帧同时有效；70% 仅为可见性门槛，不代表技术合格。`);
  if (typeof value.coverage === "number" && value.coverage_semantics === "model_estimate_sample_coverage_not_verified_visibility") lines.push(`模型样本覆盖：${Number((value.coverage * 100).toFixed(2))}%；真实可见性未知，未提供原文卡逐帧窗口证明。`);
  const evidence = value.evidence;
  if (evidence?.window_scope === "FS01_candidate_start_to_takeoff_proxy_not_confirmed_pre_split_stage") lines.push("窗口为候选起点至起跳代理；未确认正式垫步前阶段，不判断实际视线、时机或稳定达标。");
  if (evidence?.scope === "FS02_candidate_event_direction_only") lines.push("仅为候选事件的图像启动方向；未评价与来球方向是否一致。");
  if (typeof evidence?.observed_duration_ms === "number") lines.push(`参与速度计算的相邻有效帧用时：${Number(evidence.observed_duration_ms.toFixed(3))} 毫秒；不跨缺测补帧。`);
  if (evidence?.angle_period_deg === 180) lines.push("双耳轴按 180° 周期计算波动；不代表眼球注视方向。");
  if (evidence?.coordinate_system === "stable_camera_reference_image_x_right_y_down" && evidence.sign_convention === "image_down_positive"
    && evidence.normalization === "median_observed_full_torso_scale_fixed_per_candidate_window"
    && evidence.source_clause_complete === false && evidence.is_ground_truth === false && evidence.is_metric_or_3d === false) {
    lines.push("仅测量预加载候选窗内稳定相机参考中的髋纵向位移，向下为正；躯干尺度使用同窗双肩、双髋的固定中位数，与相对踝的身体尺度量独立。未确认正式阶段或完整原文条件，不生成等级。");
    if (typeof evidence.fixed_torso_scale_px === "number") lines.push(`固定躯干尺度：${Number(evidence.fixed_torso_scale_px.toFixed(3))} 像素。`);
    if (evidence.time_basis === "bound_source_pts" && typeof evidence.source_duration_ms === "number")
      lines.push(`源帧精确时间跨度：${Number(evidence.source_duration_ms.toFixed(3))} 毫秒；时长门槛用于计算支持，不是原文等级阈值。`);
  }
  return lines;
}
