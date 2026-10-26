import contract from "./directional-micro-display-contract";
import stepContract from "./fs-step-motion-display-contract";
import { objectOf as obj, validFsWindowSource, type EvidenceObject } from "./manual-card-evidence";
import { recomputeStepMotionFoot, type StepMotionFoot } from "./fs-step-motion-evidence";
import { isFormalScoringPolicy } from "./formal-card-scoring-policy";

type Point = [number, number];
type Context = { indicator_id: string; event_id: string; video_id: string; person_track_id: number;
  scoring_policy_version?: string | null; application_rule_version?: string | null; measurement_version: string;
  window: { start_ms: number; end_ms: number } | null; conditions: { metric: string; value: number | null }[];
  quality: { total_frame_count: number; valid_frame_count: number; required_joint_names: string[] } };
export interface DirectionalMicroValidation {
  status: "measured_directional_motion" | "unavailable" | "invalid";
  reason_codes: string[]; axis_role: string | null; moving_foot: "left" | "right" | null;
  total_frame_count: number | null; fixed_torso_scale: number | null; endpoint_count: number | null;
  values: Record<string, number>; feet: { left: StepMotionFoot; right: StepMotionFoot } | null;
  condition_values_match: boolean;
}
const rows = (v: unknown): unknown[] => Array.isArray(v) ? v : [];
const finite = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
const integer = (v: unknown): v is number => Number.isSafeInteger(v) && Number(v) >= 0;
const text = (v: unknown): v is string => typeof v === "string" && !!v.trim();
const near = (a: unknown, b: unknown) => finite(a) && finite(b) && Math.abs(a - b) <= 1e-8 + Math.abs(b) * 1e-8;
const clockNear = (a: unknown, b: unknown) => finite(a) && finite(b) && Math.abs(a - b) <= .51;
const sha = (v: unknown) => typeof v === "string" && /^[a-f0-9]{64}$/i.test(v);
const same = (a: unknown, b: unknown): boolean => finite(a) ? near(b, a) : Array.isArray(a) ? Array.isArray(b) && a.length === b.length && a.every((v, i) => same(v, b[i]))
  : a && typeof a === "object" ? Object.keys(a).length === Object.keys(obj(b)).length && Object.entries(a).every(([k, v]) => same(v, obj(b)[k])) : a === b;
const keys = (v: EvidenceObject, names: string[]) => Object.keys(v).length === names.length && names.every(k => k in v);
const point = (v: unknown): Point | null => Array.isArray(v) && v.length === 2 && v.every(n => finite(n) && Math.abs(n) <= 1e9) ? v as Point : null;
const middle = (a: Point, b: Point): Point => [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
const distance = (a: Point, b: Point) => Math.hypot(a[0] - b[0], a[1] - b[1]);
const median = (values: number[]) => { const a = [...values].sort((x, y) => x - y); return (a[Math.floor((a.length - 1) / 2)] + a[Math.floor(a.length / 2)]) / 2; };
const endpoint = (a: Point[]): Point => [median(a.map(p => p[0])), median(a.map(p => p[1]))];
const dot = (a: Point, b: Point) => a[0] * b[0] + a[1] * b[1];
const subtract = (a: Point, b: Point): Point => [a[0] - b[0], a[1] - b[1]];
const empty = (status: DirectionalMicroValidation["status"], reason: string): DirectionalMicroValidation => ({ status, reason_codes: [reason],
  axis_role: null, moving_foot: null, total_frame_count: null, fixed_torso_scale: null, endpoint_count: null, values: {}, feet: null, condition_values_match: false });

export function directionalMicroApplies(id: string, row: EvidenceObject): boolean {
  return id in contract.cards && (row.application_rule_version != null || row.measurement_version === contract.measurement_version
    || !!obj(row.source_window_evidence).directional_micro_adjustment);
}

/** Recompute image projections and both ankle paths. Neither a manual foot
 * declaration nor a saved completion flag establishes a complete motion. */
export function validateDirectionalMicro(annotation: EvidenceObject | null, evidence: EvidenceObject | null, w: EvidenceObject | null,
  context: Context): DirectionalMicroValidation | null {
  if (!(context.indicator_id in contract.cards) || !directionalMicroApplies(context.indicator_id, { ...context, source_window_evidence: evidence })) return null;
  const invalid = (why: string) => empty("invalid", why);
  if (!evidence?.directional_micro_adjustment) return empty("unavailable", "directional_micro_evidence_missing");
  const raw = obj(evidence.directional_micro_adjustment), ref = obj(raw.direction_reference);
  if (raw.status !== "measured_directional_motion") return empty("unavailable", "directional_micro_continuous_source_missing");
  if (!annotation || !w || !context.window || !isFormalScoringPolicy(context.scoring_policy_version)
      || context.application_rule_version !== contract.application_rule_version || context.measurement_version !== contract.measurement_version
      || raw.version !== contract.measurement_version || raw.source_grade_supported !== false || raw.contact_verified !== false
      || raw.target_reasonableness_verified !== false || raw.court_metric_direction_verified !== false || raw.geometry_is_visibility !== false
      || raw.interpolation_used !== false || raw.geometry_gate_passed !== true || raw.denominator !== "all_decoder_samples_in_bound_window"
      || !same(raw.reason_codes, []) || ["score", "grade", "passed", "original_grade", "scoring_eligible"].some(k => k in raw)
      || !validFsWindowSource(annotation, evidence, w, context, contract.annotation_version, true)) return invalid("directional_micro_source_contract_invalid");
  const n = context.quality.total_frame_count, frames = rows(evidence.source_frames), bindings = rows(evidence.source_frame_bindings).map(obj);
  const source = rows(raw.source_geometry_series).map(obj), original = rows(raw.local_normalization).map(obj), reference = rows(raw.reference_series).map(obj);
  if (n < 7 || n > 20000 || context.quality.valid_frame_count !== n || raw.total_frame_count !== n || raw.valid_frame_count !== n
      || !same(frames, raw.source_frames) || !same(frames, raw.valid_source_frames) || !same(bindings, raw.source_frame_bindings)
      || !same(contract.required_joint_names, raw.required_joint_names)
      || !same([...contract.required_joint_names].sort(), [...context.quality.required_joint_names].sort())
      || [source, original, reference].some(a => a.length !== n)) return invalid("directional_micro_full_denominator_invalid");
  const requiredRef = ["version", "method", "semantics", "axis_role", "video_sha256", "person_track_id", "selection_epoch",
    "derived_selection_epoch", "camera_reference_epoch", "source_frame", "axis_points_px", "uncertainty_px", "source_description", "target_point_px", "target_kind"];
  const spec = (contract.cards as Record<string, { axis_role: string }>)[context.indicator_id];
  if (!keys(ref, requiredRef) || ref.version !== contract.reference_version || ref.method !== "manual_visible_line"
      || ref.semantics !== contract.reference_semantics || ref.axis_role !== spec.axis_role || ref.target_kind !== "visible_ball_center"
      || !finite(ref.uncertainty_px) || ref.uncertainty_px < 0 || !text(ref.source_description) || ref.source_description.length > 512
      || !same(ref, annotation.direction_reference)
      || ["video_sha256", "person_track_id", "selection_epoch", "derived_selection_epoch", "camera_reference_epoch"].some(k => ref[k] !== w[k])) return invalid("directional_micro_reference_invalid");
  const anchor = obj(ref.source_frame), anchorIndex = frames.indexOf(anchor.frame_index);
  if (anchorIndex < 0 || !keys(anchor, ["frame_index", "pts", "time_base_num", "time_base_den"])
      || Object.keys(anchor).some(k => anchor[k] !== bindings[anchorIndex][k])) return invalid("directional_micro_reference_frame_mismatch");
  const observation = obj(raw.moving_foot_observation), side = observation.moving_foot;
  if (!keys(observation, ["version", "method", "moving_foot", "source_description", "source_frames", "semantics"])
      || observation.version !== stepContract.annotation_version || observation.method !== "manual_visual_observation"
      || observation.semantics !== stepContract.annotation_semantics || !text(observation.source_description)
      || !["left", "right"].includes(String(side)) || !same(observation, annotation.step_motion_observation) || !same(observation, w.step_motion_observation)) return invalid("directional_micro_moving_foot_source_invalid");
  const observed = rows(observation.source_frames).map(obj);
  if (!observed.length || observed.length > n || observed.some((p, i) => !keys(p, ["frame_index", "pts", "time_base_num", "time_base_den"])
      || i > 0 && Number(p.frame_index) <= Number(observed[i - 1].frame_index)
      || !bindings.some(b => Object.keys(p).every(k => p[k] === b[k])))) return invalid("directional_micro_observation_pts_invalid");
  const points: Record<string, Point>[] = [], times: number[] = [], modelEpochs = new Set<number>();
  const sourceFrame = obj(raw.source_record_frame);
  for (let i = 0; i < n; i++) {
    const b = bindings[i], s = source[i], local = original[i], input = obj(local.input_geometry), p = obj(reference[i].points), accepted: Record<string, Point> = {};
    if (reference[i].frame_index !== frames[i] || s.frame_index !== frames[i] || local.frame_index !== frames[i] || input.frame_index !== frames[i]
        || !keys(p, contract.required_joint_names) || !integer(b.pts) || !integer(b.time_base_num) || !integer(b.time_base_den)
        || !b.time_base_num || !b.time_base_den || !Number.isSafeInteger(b.pts * b.time_base_num)) return invalid("directional_micro_geometry_invalid");
    times.push(b.pts * b.time_base_num * 1000 / b.time_base_den);
    for (const j of contract.required_joint_names) { const value = point(p[j]); if (!value) return invalid("directional_micro_geometry_invalid"); accepted[j] = value; }
    const center = middle(accepted.left_shoulder, accepted.right_shoulder), hips = middle(accepted.left_hip, accepted.right_hip);
    const scale = Math.max(distance(center, hips), distance(accepted.left_shoulder, accepted.right_shoulder) * .75);
    const oldSide = input.normalization_basis === "left_shoulder_left_torso" ? "left" : input.normalization_basis === "right_shoulder_right_torso" ? "right" : null;
    if (!oldSide && input.normalization_basis !== "full_torso") return invalid("directional_micro_original_basis_invalid");
    const oldCenter = oldSide ? accepted[oldSide + "_shoulder"] : center;
    const oldScale = oldSide ? distance(oldCenter, accepted[oldSide + "_hip"]) : scale;
    if (scale < 12 || oldScale < 12 || local.output_basis !== "full_torso" || local.source_geometry_verified !== true
        || s.normalization_basis !== "full_torso" || !same(local.input_center, oldCenter) || !near(local.input_scale, oldScale)
        || !same(local.output_center, center) || !near(local.output_scale, scale)) return invalid("directional_micro_normalization_invalid");
    for (const [geometry, origin, divisor] of [[s, center, scale], [input, oldCenter, oldScale]] as [EvidenceObject, Point, number][]) {
      const normalized = obj(geometry.normalized_points), confidences = obj(geometry.point_confidences), sb = obj(geometry.source_binding);
      if (!keys(normalized, contract.required_joint_names) || !keys(confidences, contract.required_joint_names)) return invalid("directional_micro_geometry_invalid");
      const model = geometry.observation_semantics === "monocular_model_estimate" || geometry.pose_source === "sam3d";
      if (model) {
        if (geometry.observation_semantics !== "monocular_model_estimate" || geometry.pose_source !== "sam3d"
            || geometry.model_epoch_semantics !== "detector_track_epoch_primary_selection_applied_downstream"
            || b.model_epoch_semantics !== geometry.model_epoch_semantics || !integer(geometry.source_model_selection_epoch)
            || b.source_model_selection_epoch !== geometry.source_model_selection_epoch || geometry.selection_epoch !== w.selection_epoch
            || !sha(sb.video_sha256) || String(sb.video_sha256).toLowerCase() !== String(w.video_sha256).toLowerCase()
            || sb.frame_index !== frames[i] || sb.person_track_id !== context.person_track_id || sb.selection_epoch !== geometry.source_model_selection_epoch
            || sb.camera_reference_epoch !== w.camera_reference_epoch || !integer(sb.processed_index) || !integer(sourceFrame.processed_index)
            || sb.processed_index !== sourceFrame.processed_index + Number(frames[i]) - Number(anchor.frame_index) || !clockNear(sb.timestamp_ms, times[i])
            || ["pts", "time_base_num", "time_base_den"].some(k => sb[k] !== b[k])) return invalid("directional_micro_model_source_invalid");
        modelEpochs.add(Number(sb.selection_epoch));
      } else if (geometry.observation_semantics != null) return invalid("directional_micro_observation_semantics_invalid");
      for (const j of contract.required_joint_names) {
        const c = confidences[j];
        if (!(finite(c) && c >= .35 && c <= 1 || c === null && model)
            || !same(normalized[j], accepted[j].map((v, axis) => (v - origin[axis]) / divisor))) return invalid("directional_micro_joint_geometry_invalid");
      }
    }
    if (["point_confidences", "observation_semantics", "pose_source", "source_binding", "model_epoch_semantics", "selection_epoch", "source_model_selection_epoch"].some(k => !same(s[k], input[k]))) return invalid("directional_micro_source_rewritten");
    points.push(accepted);
  }
  if (modelEpochs.size > 1) return invalid("directional_micro_model_epoch_changed");
  const projected = obj(raw.projected_reference), matrix = rows(projected.matrix_to_reference).map(rows), dimensions = rows(projected.frame_dimensions_px);
  if (matrix.length !== 2 || matrix.some(row => row.length !== 3 || !row.every(finite)) || dimensions.length !== 2 || !dimensions.every(d => integer(d) && d > 0)
      || sourceFrame.index !== anchor.frame_index || sourceFrame.timestamp_source !== "decoder_pts" || !integer(sourceFrame.processed_index)
      || !clockNear(sourceFrame.timestamp_ms, times[anchorIndex]) || sourceFrame.width !== dimensions[0] || sourceFrame.height !== dimensions[1]
      || projected.perpendicular_is_court_transverse !== false) return invalid("directional_micro_original_image_invalid");
  const m = matrix as number[][], det = m[0][0] * m[1][1] - m[0][1] * m[1][0];
  if (det < .25 || det > 4 || Math.abs(m[0][0] ** 2 + m[1][0] ** 2 - det) > 1e-8 + 1e-5 * det
      || Math.abs(m[0][1] ** 2 + m[1][1] ** 2 - det) > 1e-8 + 1e-5 * det
      || Math.abs(m[0][0] * m[0][1] + m[1][0] * m[1][1]) > 1e-8) return invalid("directional_micro_camera_transform_invalid");
  const axisPoints = rows(ref.axis_points_px).map(point), target = point(ref.target_point_px);
  if (axisPoints.length !== 2 || axisPoints.some(p => !p) || !target
      || [...axisPoints, target].some(p => p!.some((v, axis) => v < 0 || v >= Number(dimensions[axis])))
      || distance(axisPoints[0]!, axisPoints[1]!) <= 2 * Number(ref.uncertainty_px)) return invalid("directional_micro_image_reference_unresolved");
  const transform = (p: Point): Point => [m[0][0] * p[0] + m[0][1] * p[1] + m[0][2], m[1][0] * p[0] + m[1][1] * p[1] + m[1][2]];
  const transformed = axisPoints.map(p => transform(p!)), length = distance(transformed[0], transformed[1]);
  const axis = subtract(transformed[1], transformed[0]).map(v => v / length) as Point, perpendicular: Point = [-axis[1], axis[0]];
  if (!same(transformed, projected.axis_points_reference_px) || !same(axis, projected.unit_axis) || !same(perpendicular, projected.image_perpendicular)) return invalid("directional_micro_projected_reference_mismatch");
  const hips = points.map(p => middle(p.left_hip, p.right_hip)), scale = median(points.map((p, i) => distance(middle(p.left_shoulder, p.right_shoulder), hips[i])));
  const count = Math.max(3, Math.ceil(n * .2)), before = endpoint(hips.slice(0, count)), after = endpoint(hips.slice(-count)), delta = subtract(after, before);
  if (!finite(scale) || scale <= 0 || !near(raw.fixed_torso_scale, scale) || raw.endpoint_count !== count) return invalid("directional_micro_fixed_scale_invalid");
  const feet = { left: recomputeStepMotionFoot(points.map(p => p.left_ankle), times, bindings, frames as number[], scale, count),
    right: recomputeStepMotionFoot(points.map(p => p.right_ankle), times, bindings, frames as number[], scale, count) };
  if (!same(feet, raw.feet) || !feet[side as "left" | "right"].complete_visual_motion_cycle) return invalid("directional_micro_motion_cycle_invalid");
  const declared = feet[side as "left" | "right"], projectedTarget = transform(target), net = Math.hypot(...delta);
  const values = { hip_axis_displacement_torso: dot(delta, axis) / scale, hip_image_perpendicular_displacement_torso: dot(delta, perpendicular) / scale,
    hip_net_displacement_torso: net / scale, hip_axis_alignment_fraction: net > 0 ? Math.abs(dot(delta, axis)) / net : 0,
    hip_axis_target_error_reduction_torso: (Math.abs(dot(subtract(projectedTarget, before), axis)) - Math.abs(dot(subtract(projectedTarget, after), axis))) / scale,
    moving_ankle_net_displacement_torso: declared.net_displacement_torso, moving_ankle_path_length_torso: declared.path_length_torso,
    both_ankles_max_net_displacement_torso: Math.max(feet.left.net_displacement_torso, feet.right.net_displacement_torso),
    both_ankles_max_path_length_torso: Math.max(feet.left.path_length_torso, feet.right.path_length_torso) };
  if (!same(values, raw.measurements) || context.conditions.length !== contract.conditions.length || contract.conditions.some(c => !near(context.conditions.find(row => row.metric === c.metric)?.value, values[c.metric as keyof typeof values]))) return invalid("directional_micro_condition_arithmetic_mismatch");
  return { status: "measured_directional_motion", reason_codes: [], axis_role: String(ref.axis_role), moving_foot: side as "left" | "right",
    total_frame_count: n, fixed_torso_scale: scale, endpoint_count: count, values, feet, condition_values_match: true };
}

export function directionalMicroSummary(value: DirectionalMicroValidation | null): string {
  if (!value) return "";
  if (value.status !== "measured_directional_motion") return "方向微调缺少完整来源，未生成分数";
  return `${value.axis_role === "longitudinal_projection" ? "所标纵向投影轴" : "所标横向投影轴"}；沿轴接近可见球 ${value.values.hip_axis_target_error_reduction_torso.toFixed(3)} 躯干单位；双脚路程最大值 ${value.values.both_ankles_max_path_length_torso.toFixed(3)} 躯干单位；轴向一致比例 ${value.values.hip_axis_alignment_fraction.toFixed(3)}；可见球位置不是预测击球点，未建立技术等级或触地真值`;
}
