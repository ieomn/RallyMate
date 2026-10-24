/** Recompute M10 from complete source geometry. Stored pass bits never authorize a score. */
import gsContract from "./gs-card-display-contract";
import { objectOf, validFormalGsStageSource, type EvidenceObject } from "./manual-card-evidence";
import { isFormalScoringPolicy } from "./formal-card-scoring-policy";

type Spec = [string, string, number, string];
type Point = [number, number];
type Kind = "rotation" | "stability";
type Policy = { version: string; scoring_revision_id: string; policy_version: string; reference_version: string; reference_semantics: string;
  auxiliary_version: string; rotation_cards: string[]; stability_cards: string[]; manual_requirements: Record<string, [string, string[]]>;
  rotation_conditions: Spec[]; stability_conditions: Spec[]; min_recovery_frames: number; min_reference_frames: number;
  min_window_ms: number; reference_max_axis_range_deg: number; min_axis_length_torso: number; endpoint_fraction: number; min_endpoint_frames: number };
export const recoveryPolicy = (gsContract as unknown as { recovery: Policy }).recovery;
const torso = ["left_shoulder", "right_shoulder", "left_hip", "right_hip"];
const ankles = ["left_ankle", "right_ankle"];
const obj = objectOf;
const rows = (v: unknown): unknown[] => Array.isArray(v) ? v : [];
const finite = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
const integer = (v: unknown): v is number => Number.isSafeInteger(v) && Number(v) >= 0;
const sha = (v: unknown) => typeof v === "string" && /^[a-f0-9]{64}$/i.test(v);
const nonempty = (v: unknown) => typeof v === "string" && v.trim().length > 0 && v.length <= 512;
const near = (a: unknown, b: unknown): boolean => finite(a) && finite(b) && Math.abs(a - b) <= 1e-8 * Math.max(1, Math.abs(a), Math.abs(b));
const close = (a: unknown, b: unknown) => finite(a) && finite(b) && Math.abs(a - b) <= .51;
const same = (a: unknown, b: unknown): boolean => Array.isArray(a) ? Array.isArray(b) && a.length === b.length && a.every((x, i) => same(x, b[i]))
  : a && typeof a === "object" ? Object.keys(a).length === Object.keys(obj(b)).length && Object.entries(a).every(([k, v]) => same(v, obj(b)[k]))
  : finite(a) && finite(b) ? near(a, b) : a === b;
const exact = (a: unknown, b: unknown): boolean => Array.isArray(a) ? Array.isArray(b) && a.length === b.length && a.every((x, i) => exact(x, b[i]))
  : a && typeof a === "object" ? Object.keys(a).length === Object.keys(obj(b)).length && Object.entries(a).every(([k, v]) => exact(v, obj(b)[k])) : a === b;
const point = (v: unknown): Point | null => Array.isArray(v) && v.length === 2 && v.every(finite) ? v as Point : null;
const middle = (a: Point, b: Point): Point => [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
const distance = (a: Point, b: Point) => Math.hypot(a[0] - b[0], a[1] - b[1]);
const median = (v: number[]) => { const s = [...v].sort((a, b) => a - b); return (s[Math.floor((s.length - 1) / 2)] + s[Math.floor(s.length / 2)]) / 2; };
const spread = (v: number[]) => Math.max(...v) - Math.min(...v);
const axial = (v: number) => ((v + 90) % 180 + 180) % 180 - 90;
const axis = (a: Point, b: Point, scale: number): number | null => distance(a, b) / scale < recoveryPolicy.min_axis_length_torso ? null : axial(Math.atan2(b[1] - a[1], b[0] - a[0]) * 180 / Math.PI);
const unwrap = (v: number[]) => { const result = [v[0]]; for (let i = 1; i < v.length; i++) result.push(result.at(-1)! + axial(v[i] - v[i - 1])); return result; };
function fail(reason: string): never { throw new Error(reason); }

export type RecoveryContext = { indicator_id: string; event_id: string; video_id: string; person_track_id: number; scoring_policy_version: string | null;
  window: { start_ms: number; end_ms: number } | null; source_stage: { document_sha256: string; annotation_id: string; stage_code: string } | null;
  source_stage_context: EvidenceObject | null; source_stage_evidence: EvidenceObject | null;
  reason_codes?: string[];
  quality: { total_frame_count: number; valid_frame_count: number; required_joint_names: string[] };
  conditions: { metric: string; value: number | null }[] };
export interface RecoveryTrajectory { source_frames: number[]; source_times_ms: number[]; dt_seconds: number[]; fixed_torso_scale: number;
  hip_reference_positions: Point[]; hip_displacement_vectors: Point[]; hip_speeds_torso_s: number[];
  axis_degrees: { shoulder: (number | null)[]; hip: (number | null)[] };
  unwrapped_axis_degrees: { shoulder: number[] | null; hip: number[] | null }; trunk_tilt_degrees: number[]; hip_inside_ankle_support: number[] }
export interface GsRecoveryValidation { status: "measured_recovery" | "unavailable" | "invalid"; kind: Kind | null; reason_codes: string[];
  values: Record<string, number>; condition_values_match: boolean; stage: RecoveryTrajectory | null; reference: RecoveryTrajectory | null;
  endpoint_count: number | null; reference_axis_degrees: Record<string, number> | null; reference_error_degrees: Record<string, number[]> | null }
export const isRecoveryCard = (id: string) => !!recoveryPolicy && (recoveryPolicy.rotation_cards.includes(id) || recoveryPolicy.stability_cards.includes(id) || id in recoveryPolicy.manual_requirements);
export const isRecoveryBodyCard = (id: string) => !!recoveryPolicy && (recoveryPolicy.rotation_cards.includes(id) || recoveryPolicy.stability_cards.includes(id));

function ptsTime(row: EvidenceObject): number {
  if (!["frame_index", "pts", "time_base_num", "time_base_den"].every(k => integer(row[k])) || !row.time_base_num || !row.time_base_den) fail("recovery_source_pts_invalid");
  const value = Number(row.pts) * Number(row.time_base_num) * 1000 / Number(row.time_base_den);
  return finite(value) ? value : fail("recovery_source_pts_invalid");
}

function ledger(source: EvidenceObject[], window: EvidenceObject): number[] {
  if (!source.length || !sha(window.video_sha256) || !integer(window.person_track_id) || !integer(window.selection_epoch)
      || !integer(window.derived_selection_epoch) || !integer(window.camera_reference_epoch) || window.camera_compensated !== true) fail("recovery_identity_or_camera_invalid");
  const times: number[] = [], models = new Set<number>();
  for (let i = 0; i < source.length; i++) {
    const b = source[i], time = ptsTime(b);
    if (b.video_sha256 !== window.video_sha256 || b.selected_person_track_id !== window.person_track_id
        || b.selection_epoch !== window.selection_epoch || b.derived_selection_epoch !== window.derived_selection_epoch || b.camera_reference_epoch !== window.camera_reference_epoch
        || !close(b.timestamp_ms, time) || i > 0 && (b.frame_index !== Number(source[i - 1].frame_index) + 1 || time <= times[i - 1]
          || b.time_base_num !== source[0].time_base_num || b.time_base_den !== source[0].time_base_den)) fail("recovery_source_continuity_invalid");
    if (b.rejected_pose_person_track_id === window.person_track_id || b.rejected_model_person_track_id === window.person_track_id) fail("recovery_selected_geometry_rejected");
    if (b.source_model_selection_epoch !== undefined || b.model_epoch_semantics !== undefined) {
      if (!integer(b.source_model_selection_epoch) || b.model_epoch_semantics !== "detector_track_epoch_primary_selection_applied_downstream") fail("recovery_model_epoch_invalid");
      models.add(b.source_model_selection_epoch);
    }
    times.push(time);
  }
  if (models.size > 1) fail("recovery_model_epoch_changed");
  const gaps = times.slice(1).map((v, i) => v - times[i]);
  if (gaps.length && gaps.some(v => v > Math.min(160, median(gaps) * 1.8))) fail("recovery_source_gap");
  return times;
}

function geometrySource(g: EvidenceObject, b: EvidenceObject, window: EvidenceObject, time: number,
  modelEpochs: Set<number>, modelProcessed: { frame: number; processed: number }[]): boolean {
  const binding = obj(g.source_binding);
  if (g.frame_index !== b.frame_index || g.time !== b.timestamp_ms || g.track !== window.person_track_id || g.selection_epoch !== window.selection_epoch
      || g.camera_reference_epoch !== window.camera_reference_epoch || g.camera_compensated !== true || g.timestamp_source !== "decoder_pts"
      || g.normalization_basis !== "full_torso" || binding.video_sha256 !== window.video_sha256) fail("recovery_geometry_source_mismatch");
  const model = g.observation_semantics === "monocular_model_estimate";
  if (binding.frame_index !== b.frame_index || binding.person_track_id !== window.person_track_id
      || binding.camera_reference_epoch !== window.camera_reference_epoch || !close(binding.timestamp_ms, time)
      || ["pts", "time_base_num", "time_base_den"].some(k => binding[k] !== b[k])
      || !model && binding.selection_epoch !== window.selection_epoch
      || binding.processed_index !== undefined && !integer(binding.processed_index)) fail("recovery_geometry_source_mismatch");
  if (model) {
    const explicit = g.model_epoch_semantics !== undefined || b.model_epoch_semantics !== undefined || g.source_model_selection_epoch !== undefined || b.source_model_selection_epoch !== undefined;
    const epoch = explicit ? g.source_model_selection_epoch : window.selection_epoch;
    if (g.pose_source !== "sam3d" || !integer(epoch) || explicit && (g.model_epoch_semantics !== "detector_track_epoch_primary_selection_applied_downstream"
        || b.model_epoch_semantics !== g.model_epoch_semantics || b.source_model_selection_epoch !== epoch)
        || binding.frame_index !== b.frame_index || binding.person_track_id !== window.person_track_id || binding.selection_epoch !== epoch
        || binding.camera_reference_epoch !== window.camera_reference_epoch || !integer(binding.processed_index)
        || !close(binding.timestamp_ms, time) || ["pts", "time_base_num", "time_base_den"].some(k => binding[k] !== b[k])) fail("recovery_model_source_invalid");
    modelEpochs.add(Number(epoch)); modelProcessed.push({ frame: Number(b.frame_index), processed: Number(binding.processed_index) });
  }
  return model;
}

function modelContinuity(epochs: Set<number>, processed: { frame: number; processed: number }[]) {
  if (epochs.size > 1 || processed.some(r => r.processed - processed[0].processed !== r.frame - processed[0].frame)) fail("recovery_model_source_discontinuity");
}

function trajectory(proof: EvidenceObject, window: EvidenceObject, joints: string[], minimum: number): RecoveryTrajectory {
  const source = rows(proof.source_frame_bindings).map(obj), geometry = rows(proof.source_geometry_series).map(obj), n = source.length;
  if (!exact(proof.window, window) || proof.interpolation_used !== false || proof.points_semantics !== "camera_reference_points_not_to_be_transformed_again"
      || proof.normalization_semantics !== "fixed_median_torso_for_this_complete_window" || proof.total_frame_count !== n || geometry.length !== n
      || !same(proof.required_joint_names, joints)) fail("recovery_geometry_contract_invalid");
  const times = ledger(source, window);
  if (n < minimum || times.at(-1)! - times[0] < recoveryPolicy.min_window_ms) fail("recovery_continuous_window_too_short");
  if (source[0].timestamp_ms !== window.start_ms || source.at(-1)!.timestamp_ms !== window.end_ms) fail("recovery_window_endpoint_mismatch");
  const modelEpochs = new Set<number>(), modelProcessed: { frame: number; processed: number }[] = [];
  const values: Record<string, Point>[] = [];
  for (let i = 0; i < n; i++) {
    const g = geometry[i], b = source[i], normalized = obj(g.points), reference = obj(g.reference_points), confidence = obj(g.point_confidences);
    const model = geometrySource(g, b, window, times[i], modelEpochs, modelProcessed);
    const accepted: Record<string, Point> = {};
    for (const joint of joints) {
      const p = point(normalized[joint]), r = point(reference[joint]), c = confidence[joint];
      if (!p || p.some(v => Math.abs(v) > 1e6) || !r || !(finite(c) && c >= .35 && c <= 1 || c == null && model)) fail("recovery_common_joint_missing");
      accepted[joint] = r;
    }
    const center = middle(accepted.left_shoulder, accepted.right_shoulder);
    const scale = Math.max(distance(center, middle(accepted.left_hip, accepted.right_hip)), distance(accepted.left_shoulder, accepted.right_shoulder) * .75);
    if (scale < 12 || !finite(scale) || !same(g.center, center) || !near(g.scale, scale)
        || joints.some(joint => !same(accepted[joint].map((v, k) => (v - center[k]) / scale), normalized[joint]))) fail("recovery_normalized_reference_mismatch");
    values.push(accepted);
  }
  modelContinuity(modelEpochs, modelProcessed);
  if (proof.valid_frame_count !== n || rows(proof.reason_codes).length) fail("recovery_joint_denominator_mismatch");
  const scale = median(values.map(v => distance(middle(v.left_shoulder, v.right_shoulder), middle(v.left_hip, v.right_hip))));
  if (!finite(scale) || scale <= 0) fail("recovery_fixed_scale_missing");
  const hips = values.map(v => middle(v.left_hip, v.right_hip)), dt = times.slice(1).map((v, i) => (v - times[i]) / 1000);
  const displacement = hips.slice(1).map((p, i): Point => [p[0] - hips[i][0], p[1] - hips[i][1]]);
  const axes = { shoulder: values.map(v => axis(v.left_shoulder, v.right_shoulder, scale)), hip: values.map(v => axis(v.left_hip, v.right_hip, scale)) };
  const unwrapped = { shoulder: axes.shoulder.every(finite) ? unwrap(axes.shoulder) : null, hip: axes.hip.every(finite) ? unwrap(axes.hip) : null };
  const result: RecoveryTrajectory = { source_frames: source.map(r => Number(r.frame_index)), source_times_ms: times, dt_seconds: dt, fixed_torso_scale: scale,
    hip_reference_positions: hips, hip_displacement_vectors: displacement, hip_speeds_torso_s: displacement.map((v, i) => Math.hypot(...v) / (dt[i] * scale)),
    axis_degrees: axes, unwrapped_axis_degrees: unwrapped,
    trunk_tilt_degrees: values.map((v, i) => { const s = middle(v.left_shoulder, v.right_shoulder), h = hips[i]; return Math.atan2(Math.abs(h[0] - s[0]), Math.abs(h[1] - s[1])) * 180 / Math.PI; }),
    hip_inside_ankle_support: joints.includes("left_ankle") ? values.map((v, i) => Number(Math.min(v.left_ankle[0], v.right_ankle[0]) <= hips[i][0] && hips[i][0] <= Math.max(v.left_ankle[0], v.right_ankle[0]))) : [] };
  for (const key of Object.keys(result).filter(k => k !== "source_frames")) if (!same(proof[key], result[key as keyof RecoveryTrajectory])) fail("recovery_saved_trajectory_mismatch");
  return result;
}

export function validateGsRecovery(rawValue: EvidenceObject | null | undefined, parent: EvidenceObject | null | undefined, context: RecoveryContext): GsRecoveryValidation | null {
  if (!isRecoveryBodyCard(context.indicator_id) || !rawValue) return null;
  const kind: Kind = recoveryPolicy.rotation_cards.includes(context.indicator_id) ? "rotation" : "stability";
  const empty = (status: GsRecoveryValidation["status"], reason: string): GsRecoveryValidation => ({ status, kind, reason_codes: [reason], values: {}, condition_values_match: false,
    stage: null, reference: null, endpoint_count: null, reference_axis_degrees: null, reference_error_degrees: null });
  try {
    if (context.reason_codes?.includes("gs_recovery_source_copies_mismatch")) fail("gs_recovery_source_copies_mismatch");
    const raw = rawValue, w = context.source_stage_context, annotation = obj(parent?.source_annotation), action = obj(annotation.action_window), requested = obj(annotation.requested_window_ms);
    if (!isFormalScoringPolicy(context.scoring_policy_version) || !parent || !w || !context.window || !context.source_stage
        || raw.version !== recoveryPolicy.version || raw.policy_version !== recoveryPolicy.policy_version || raw.scoring_revision_id !== recoveryPolicy.scoring_revision_id
        || raw.kind !== kind || raw.original_grade !== null || raw.source_grade_supported !== false
        || raw.semantics !== (kind === "rotation" ? "projected_axis_return_to_independent_reference" : "terminal_body_projection_stability")
        || !validFormalGsStageSource(context.source_stage_evidence, w, context)) fail("recovery_source_contract_invalid");
    if (!nonempty(parent.action_id) || parent.action_id !== action.action_id || action.technique_code !== context.indicator_id.split("-")[0]
        || action.person_track_id !== context.person_track_id || !finite(action.start_ms) || !finite(action.end_ms) || action.end_ms <= action.start_ms
        || !finite(requested.start_ms) || !finite(requested.end_ms) || requested.start_ms < action.start_ms || requested.end_ms > action.end_ms
        || requested.start_ms > context.window.start_ms + .51 || requested.end_ms < context.window.end_ms - .51
        || annotation.document_sha256 !== context.source_stage.document_sha256 || annotation.annotation_id !== context.event_id) fail("recovery_parent_action_invalid");
    const proof = obj(raw.stage), joints = kind === "rotation" ? torso : [...torso, ...ankles];
    if (!exact(proof.source_frame_bindings, context.source_stage_evidence?.source_frame_bindings)) fail("recovery_parent_source_copy_mismatch");
    const stage = trajectory(proof, w, joints, recoveryPolicy.min_recovery_frames), count = Math.max(recoveryPolicy.min_endpoint_frames, Math.ceil(stage.source_frames.length * recoveryPolicy.endpoint_fraction));
    if (proof.endpoint_count !== count || !exact(proof.endpoint_source_frames, { start: stage.source_frames.slice(0, count), end: stage.source_frames.slice(-count) })
        || context.quality.total_frame_count !== stage.source_frames.length || context.quality.valid_frame_count !== stage.source_frames.length
        || !same([...context.quality.required_joint_names].sort(), [...joints].sort())) fail("recovery_endpoint_or_quality_mismatch");
    let reference: RecoveryTrajectory | null = null, refs: Record<string, number> | null = null, errors: Record<string, number[]> | null = null;
    let values: Record<string, number>;
    if (kind === "stability") {
      if (raw.reference !== null) fail("recovery_unexpected_reference");
      values = { terminal_hip_speed_torso_s: median(stage.hip_speeds_torso_s.slice(-(count - 1))),
        terminal_hip_inside_ankle_support_fraction: stage.hip_inside_ankle_support.slice(-count).reduce((a, b) => a + b, 0) / count,
        terminal_trunk_tilt_range_deg: spread(stage.trunk_tilt_degrees.slice(-count)) };
    } else {
      const ref = obj(raw.reference), rw = obj(ref.window), binding = obj(ref.binding), bridge = rows(ref.continuity_timeline).map(obj), rt = rows(ref.source_frame_bindings).map(obj);
      if (!exact(binding, annotation.recovery_reference_observation) || binding.version !== recoveryPolicy.reference_version || binding.method !== "manual_visual_observation"
          || binding.semantics !== recoveryPolicy.reference_semantics || binding.document_version !== "gs-stage-annotations-v1.0.0" || binding.digest_kind !== "canonical_json_sorted_utf8_v1"
          || binding.document_sha256 !== context.source_stage.document_sha256 || binding.action_id !== action.action_id || binding.video_sha256 !== w.video_sha256
          || binding.person_track_id !== context.person_track_id || binding.expert_adjudicated !== false || binding.evidence_scope !== "independent_body_direction_reference_only"
          || !nonempty(binding.source) || !nonempty(binding.source_description) || !finite(binding.uncertainty_ms) || binding.uncertainty_ms < 0
          || ref.action_start_ms !== action.start_ms || !finite(rw.end_ms) || rw.end_ms >= action.start_ms || action.start_ms > context.window.start_ms
          || ["video_sha256", "video_id", "person_track_id", "selection_epoch", "derived_selection_epoch", "camera_reference_epoch", "camera_compensated"].some(k => rw[k] !== w[k])) fail("recovery_independent_reference_invalid");
      const sourceKeys = ["frame_index", "pts", "time_base_num", "time_base_den"];
      if (!rt.length || sourceKeys.some(k => obj(binding.start_frame)[k] !== rt[0][k] || obj(binding.end_frame)[k] !== rt.at(-1)![k])) fail("recovery_reference_anchor_mismatch");
      const bridgeTimes = ledger(bridge, w), bridgeGeometry = rows(ref.continuity_source_geometry_series).map(obj);
      if (bridge[0]?.frame_index !== rt[0].frame_index || bridge.at(-1)?.frame_index !== stage.source_frames.at(-1)
          || !exact(bridge.slice(0, rt.length), rt) || !exact(bridge.slice(-stage.source_frames.length), proof.source_frame_bindings)
          || bridgeGeometry.length !== bridge.length || !exact(bridgeGeometry.slice(0, rt.length), ref.source_geometry_series)
          || !exact(bridgeGeometry.slice(-stage.source_frames.length), proof.source_geometry_series)) fail("recovery_reference_bridge_incomplete");
      const bridgeEpochs = new Set<number>(), bridgeProcessed: { frame: number; processed: number }[] = [];
      bridgeGeometry.forEach((g, i) => geometrySource(g, bridge[i], w, bridgeTimes[i], bridgeEpochs, bridgeProcessed));
      modelContinuity(bridgeEpochs, bridgeProcessed);
      reference = trajectory(ref, rw, torso, recoveryPolicy.min_reference_frames);
      if (!reference.unwrapped_axis_degrees.shoulder || !reference.unwrapped_axis_degrees.hip || !stage.unwrapped_axis_degrees.shoulder || !stage.unwrapped_axis_degrees.hip) fail("recovery_projected_axis_degenerate");
      if (spread(reference.unwrapped_axis_degrees.shoulder) > recoveryPolicy.reference_max_axis_range_deg || spread(reference.unwrapped_axis_degrees.hip) > recoveryPolicy.reference_max_axis_range_deg) fail("recovery_reference_axis_unstable");
      refs = { shoulder: median(reference.unwrapped_axis_degrees.shoulder), hip: median(reference.unwrapped_axis_degrees.hip) };
      errors = { shoulder: stage.axis_degrees.shoulder.map(v => Math.abs(axial(v! - refs!.shoulder))), hip: stage.axis_degrees.hip.map(v => Math.abs(axial(v! - refs!.hip))) };
      values = { shoulder_reference_error_reduction_deg: median(errors.shoulder.slice(0, count)) - median(errors.shoulder.slice(-count)),
        hip_reference_error_reduction_deg: median(errors.hip.slice(0, count)) - median(errors.hip.slice(-count)),
        terminal_max_reference_error_deg: Math.max(...errors.shoulder.slice(-count), ...errors.hip.slice(-count)),
        terminal_max_axis_range_deg: Math.max(spread(stage.unwrapped_axis_degrees.shoulder.slice(-count)), spread(stage.unwrapped_axis_degrees.hip.slice(-count))) };
      if (!same(ref.reference_axis_degrees, refs) || !same(proof.reference_error_degrees, errors)) fail("recovery_reference_arithmetic_mismatch");
    }
    const specs = kind === "rotation" ? recoveryPolicy.rotation_conditions : recoveryPolicy.stability_conditions;
    const measurements = obj(raw.measurements);
    if (raw.status !== "measured" || rows(raw.reason_codes).length || Object.keys(measurements).length !== specs.length) fail("recovery_measured_state_mismatch");
    for (const [metric, , , unit] of specs) {
      const m = obj(measurements[metric]);
      if (m.feature_name !== metric || m.feature_version !== recoveryPolicy.version || m.unit !== unit || m.geometry_gate_passed !== true
          || !same(m.required_joint_names, joints) || m.total_frame_count !== stage.source_frames.length || m.valid_frame_count !== stage.source_frames.length
          || !exact(m.source_frames, stage.source_frames) || !same(m.statistics, { value: values[metric] }) || rows(m.reason_codes).length
          || m.visibility_verified !== false || m.image_bounds_verified !== false || m.denominator !== "all_decoder_samples_in_bound_stage"
          || m.semantics !== "image_plane_application_observation") fail("recovery_measurement_arithmetic_mismatch");
    }
    const agrees = context.conditions.length === specs.length && specs.every(([name]) => near(context.conditions.find(c => c.metric === name)?.value, values[name]));
    if (!agrees) fail("recovery_condition_arithmetic_mismatch");
    return { status: "measured_recovery", kind, reason_codes: [], values, condition_values_match: true, stage, reference, endpoint_count: count,
      reference_axis_degrees: refs, reference_error_degrees: errors };
  } catch (error) {
    const reason = error instanceof Error ? error.message : "recovery_evidence_invalid";
    return empty(rawValue.status === "unavailable" ? "unavailable" : "invalid", reason);
  }
}

export function gsRecoverySummary(value: GsRecoveryValidation | null, id: string): string {
  if (!isRecoveryCard(id)) return "";
  if (!isRecoveryBodyCard(id)) return "尚缺手与球拍关系或完整准备状态的必要来源；人体辅助观测不代替这些证据，本卡未评分。";
  if (value?.status !== "measured_recovery") return "身体恢复的完整原帧或独立准备参照尚不满足计算要求，本卡未评分；旧规则记录不恢复成绩。";
  return value.kind === "rotation" ? "肩髋轴相对独立动作前准备参照的图像投影回归，已从完整原帧重新计算；不代表三维旋转、持拍控制或完整准备状态。"
    : "完整恢复窗末段的髋速度、双踝投影区间和躯干摆动，已从原帧重新计算；不代表真实重心、接地或完整准备状态。";
}
