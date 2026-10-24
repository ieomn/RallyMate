/** Recompute a single observed low/move/low episode. This never establishes
 * contact, a front foot, target direction, or a technical step classification. */
import contract from "./fs-step-motion-display-contract";
import { validDerivedSelectionLedger, isFormalScoringPolicy } from "./formal-card-scoring-policy";
import type { EvidenceObject } from "./manual-card-evidence";

const obj = (value: unknown): EvidenceObject => value && typeof value === "object" && !Array.isArray(value) ? value as EvidenceObject : {};
const rows = (value: unknown): unknown[] => Array.isArray(value) ? value : [];
const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const integer = (value: unknown): value is number => Number.isSafeInteger(value) && Number(value) >= 0;
const near = (a: unknown, b: unknown) => finite(a) && finite(b) && Math.abs(a - b) <= 1e-8 * Math.max(1, Math.abs(a), Math.abs(b));
const close = (a: unknown, b: unknown) => finite(a) && finite(b) && Math.abs(a - b) <= .51;
const sha = (value: unknown) => typeof value === "string" && /^[a-f0-9]{64}$/i.test(value);
const text = (value: unknown): value is string => typeof value === "string" && value.trim().length > 0 && [...value].length <= 512;
const median = (values: number[]) => { const sorted = [...values].sort((a, b) => a - b), n = sorted.length; return (sorted[Math.floor((n - 1) / 2)] + sorted[Math.floor(n / 2)]) / 2; };
const matches = (expected: unknown, actual: unknown): boolean => typeof expected === "number" ? near(expected, actual)
  : Array.isArray(expected) ? Array.isArray(actual) && expected.length === actual.length && expected.every((v, i) => matches(v, actual[i]))
    : expected && typeof expected === "object" ? Object.entries(expected).every(([k, v]) => matches(v, obj(actual)[k])) : expected === actual;
const exactKeys = (value: EvidenceObject, keys: readonly string[]) => Object.keys(value).length === keys.length && keys.every(k => k in value);
type Point = [number, number];
const point = (value: unknown, bounded = false): Point | null => Array.isArray(value) && value.length === 2 && value.every(v => finite(v) && (!bounded || Math.abs(v) <= 1e6)) ? value as Point : null;
const distance = (a: Point, b: Point) => Math.hypot(a[0] - b[0], a[1] - b[1]);
const middle = (a: Point, b: Point): Point => [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
const endpoint = (path: Point[]): Point => [median(path.map(p => p[0])), median(path.map(p => p[1]))];
function timeOf(row: EvidenceObject): number | null {
  return integer(row.frame_index) && integer(row.pts) && integer(row.time_base_num) && row.time_base_num > 0
    && integer(row.time_base_den) && row.time_base_den > 0 && Number.isSafeInteger(row.pts * row.time_base_num)
    ? row.pts * row.time_base_num * 1000 / row.time_base_den : null;
}

type Context = { scoring_policy_version?: string | null; indicator_id: string; event_id: string; video_id: string; person_track_id: number;
  window: { start_ms: number; end_ms: number } | null;
  quality: { total_frame_count: number; valid_frame_count?: number; required_joint_names: string[] };
  conditions?: { metric: string; value: number | null }[] };
export type StepMotionPhase = { moving: boolean; first_interval: number; last_interval: number; source_frames: number[];
  duration_ms: number; enough_frames: boolean; enough_duration: boolean };
export type StepMotionFoot = { intervals: { source_frames: number[]; duration_ms: number; speed_torso_s: number; moving: boolean }[];
  phases: StepMotionPhase[]; endpoint_count: number; endpoint_medians: { start: Point; end: Point };
  endpoint_low_cloud_radii_torso: number[] | null; net_displacement_torso: number; path_length_torso: number;
  complete_visual_motion_cycle: boolean; reason_codes: string[] };
export interface StepMotionValidation {
  status: "measured_visual_motion" | "unavailable" | "invalid"; reason_codes: string[];
  moving_foot: "left" | "right" | null; total_frame_count: number | null; valid_frame_count: number | null;
  fixed_torso_scale: number | null; endpoint_count: number | null;
  model_estimate_sample_count: number;
  feet: { left: StepMotionFoot; right: StepMotionFoot } | null;
  values: Record<string, number | null>; condition_values_match: boolean;
}
const empty = (status: StepMotionValidation["status"], reason: string): StepMotionValidation => ({ status, reason_codes: [reason],
  moving_foot: null, total_frame_count: null, valid_frame_count: null, fixed_torso_scale: null, endpoint_count: null,
  feet: null, values: {}, condition_values_match: false, model_estimate_sample_count: 0 });

export function recomputeStepMotionFoot(path: Point[], times: number[], pts: EvidenceObject[], frames: number[], scale: number, count: number): StepMotionFoot {
  const intervals = path.slice(1).map((p, i) => { const duration = times[i + 1] - times[i], speed = distance(path[i], p) * 1000 / (duration * scale);
    return { source_frames: frames.slice(i, i + 2), duration_ms: duration, speed_torso_s: speed, moving: speed > contract.low_speed_threshold_torso_s }; });
  const runs: StepMotionPhase[] = [];
  intervals.forEach((pair, index) => {
    if (!runs.length || runs.at(-1)!.moving !== pair.moving) runs.push({ moving: pair.moving, first_interval: index, last_interval: index,
      source_frames: [], duration_ms: 0, enough_frames: false, enough_duration: false });
    else runs.at(-1)!.last_interval = index;
  });
  for (const run of runs) {
    const a = run.first_interval, b = run.last_interval + 1;
    run.source_frames = frames.slice(a, b + 1); run.duration_ms = times[b] - times[a];
    run.enough_frames = b - a + 1 >= contract.minimum_phase_frame_count;
    run.enough_duration = BigInt(Number(pts[b].pts) - Number(pts[a].pts)) * BigInt(Number(pts[a].time_base_num)) * BigInt(1000)
      >= BigInt(contract.minimum_phase_duration_ms) * BigInt(Number(pts[a].time_base_den));
  }
  const start = endpoint(path.slice(0, count)), end = endpoint(path.slice(-count)), reasons: string[] = [];
  let radii: number[] | null = null;
  if (runs.length !== 3 || runs[0].moving || !runs[1].moving || runs[2].moving) reasons.push("step_motion_requires_single_low_move_low");
  else {
    if (!runs.every(r => r.enough_frames && r.enough_duration)) reasons.push("step_motion_phase_too_short");
    if (count > runs[0].last_interval + 2 || path.length - count < runs[2].first_interval) reasons.push("step_motion_endpoint_window_overlaps_movement");
    radii = [Math.max(...path.slice(0, runs[0].last_interval + 2).map(p => distance(p, start))) / scale,
      Math.max(...path.slice(runs[2].first_interval).map(p => distance(p, end))) / scale];
    if (distance(start, end) / scale <= radii[0] + radii[1]) reasons.push("step_motion_endpoint_positions_not_distinguishable");
  }
  return { intervals, phases: runs, endpoint_count: count, endpoint_medians: { start, end }, endpoint_low_cloud_radii_torso: radii,
    net_displacement_torso: distance(start, end) / scale, path_length_torso: path.slice(1).reduce((sum, p, i) => sum + distance(path[i], p), 0) / scale,
    complete_visual_motion_cycle: reasons.length === 0, reason_codes: reasons };
}

export function validateFsStepMotion(annotation: EvidenceObject | null, evidence: EvidenceObject | null, w: EvidenceObject | null, context: Context): StepMotionValidation | null {
  const specs = contract.cards as Record<string, { conditions: unknown[][] }>, spec = specs[context.indicator_id];
  if (!spec) return null;
  if (!evidence?.step_motion) return empty("unavailable", "complete_step_cycle_not_bound");
  const raw = obj(evidence.step_motion), invalid = (reason: string) => empty("invalid", reason);
  if (!annotation || !w || !context.window || evidence.step_motion_version !== contract.version
      || raw.version !== contract.version || raw.policy_version !== contract.policy_version || raw.policy_basis !== "engineering_observation_policy_uncalibrated"
      || raw.source_grade_supported !== false || raw.contact_verified !== false || raw.target_direction_verified !== false || raw.technical_step_verified !== false
      || raw.interpolation_used !== false || evidence.interpolation_used !== false || evidence.source_behavior_or_contact_verified !== false
      || ["score", "grade", "proxy_grade", "original_grade", "passed", "scoring_eligible"].some(key => key in raw)
      || raw.low_speed_threshold_torso_s !== contract.low_speed_threshold_torso_s
      || raw.minimum_phase_duration_ms !== contract.minimum_phase_duration_ms || raw.minimum_phase_frame_count !== contract.minimum_phase_frame_count
      || raw.minimum_geometry_fraction !== .7 || raw.geometry_is_visibility !== false || raw.denominator !== "all_decoder_samples_in_bound_stage"
      || !matches(contract.required_joint_names, raw.required_joint_names)) return invalid("step_motion_contract_invalid");
  const observation = obj(raw.moving_foot_observation), side = observation.moving_foot;
  const observationKeys = ["version", "method", "moving_foot", "source_description", "source_frames", "semantics"];
  if (![observation, obj(annotation.step_motion_observation), obj(w.step_motion_observation)].every(row => exactKeys(row, observationKeys))
      || observation.version !== contract.annotation_version || observation.method !== "manual_visual_observation"
      || observation.semantics !== contract.annotation_semantics || !text(observation.source_description) || !["left", "right"].includes(String(side))
      || !matches(observation, annotation.step_motion_observation) || !matches(observation, w.step_motion_observation)
      || !sha(annotation.video_sha256) || w.video_sha256 !== annotation.video_sha256 || w.video_id !== context.video_id || w.event_id !== context.event_id
      || w.person_track_id !== context.person_track_id || w.event_code !== context.indicator_id.split("-")[0] || w.stage_code !== context.indicator_id
      || w.camera_compensated !== true || !integer(w.selection_epoch) || !integer(w.camera_reference_epoch)
      || !close(w.start_ms, context.window.start_ms) || !close(w.end_ms, context.window.end_ms)) return invalid("step_motion_source_binding_invalid");
  const pts = rows(evidence.source_pts).map(obj), bindings = rows(evidence.source_frame_bindings).map(obj), frames = rows(evidence.source_frames);
  if (!validDerivedSelectionLedger(bindings, isFormalScoringPolicy(context.scoring_policy_version), w.derived_selection_epoch)) return invalid("step_motion_derived_selection_ledger_invalid");
  const n = frames.length, source = rows(raw.source_geometry_series).map(obj);
  if (n < 3 || n > 20000 || !integer(annotation.start_source_frame) || !integer(annotation.end_source_frame)
      || n !== annotation.end_source_frame - annotation.start_source_frame + 1 || n !== context.quality.total_frame_count
      || pts.length !== n || bindings.length !== n || source.length !== n || raw.total_frame_count !== n || !matches(frames, raw.source_frames)
      || !Array.isArray(raw.reason_codes) || rows(raw.reason_codes).some(v => typeof v !== "string" || !v)) return invalid("step_motion_denominator_invalid");
  const rawPts = rows(raw.source_pts).map(obj), times: number[] = [], validFrames: number[] = [], normalized: Record<string, Point | null>[] = [];
  if (rawPts.length !== n) return invalid("step_motion_source_pts_invalid");
  const modelEpochs = new Set<number>();
  for (let i = 0; i < n; i++) {
    const p = pts[i], b = bindings[i], time = timeOf(p), row = source[i];
    if (frames[i] !== annotation.start_source_frame + i || p.frame_index !== frames[i] || row.frame_index !== frames[i] || time === null
        || ["frame_index", "pts", "time_base_num", "time_base_den"].some(k => p[k] !== rawPts[i][k] || p[k] !== b[k])
        || p.video_sha256 !== annotation.video_sha256 || b.video_sha256 !== annotation.video_sha256 || !close(b.timestamp_ms, time)
        || b.selected_person_track_id !== context.person_track_id || b.selection_epoch !== w.selection_epoch || b.camera_reference_epoch !== w.camera_reference_epoch
        || p.time_base_num !== pts[0].time_base_num || p.time_base_den !== pts[0].time_base_den
        || i > 0 && (time <= times[i - 1] || Number(b.timestamp_ms) <= Number(bindings[i - 1].timestamp_ms))) return invalid("step_motion_source_pts_invalid");
    times.push(time);
    const points = obj(row.normalized_points), confidence = obj(row.point_confidences), selected: Record<string, Point | null> = {};
    if (!exactKeys(points, contract.required_joint_names) || !exactKeys(confidence, contract.required_joint_names)) return invalid("step_motion_geometry_invalid");
    // Missing confidence is a model-specific source contract, not an authority
    // conferred by a caller writing the observation_semantics string.
    if (row.observation_semantics === "monocular_model_estimate" || row.pose_source === "sam3d") {
      const sourceBinding = obj(row.source_binding);
      const explicitEpoch = row.model_epoch_semantics !== undefined || b.model_epoch_semantics !== undefined;
      const modelEpoch = explicitEpoch ? row.source_model_selection_epoch : w.selection_epoch;
      if (explicitEpoch && (row.model_epoch_semantics !== "detector_track_epoch_primary_selection_applied_downstream"
          || b.model_epoch_semantics !== row.model_epoch_semantics || row.selection_epoch !== w.selection_epoch
          || !integer(modelEpoch) || b.source_model_selection_epoch !== modelEpoch)) return invalid("step_motion_model_epoch_namespace_invalid");
      if (integer(modelEpoch)) modelEpochs.add(modelEpoch);
      if (modelEpochs.size > 1) return invalid("step_motion_model_epoch_discontinuity");
      if (row.pose_source !== "sam3d" || row.observation_semantics !== "monocular_model_estimate"
          || sourceBinding.video_sha256 !== annotation.video_sha256 || sourceBinding.frame_index !== frames[i]
          || sourceBinding.person_track_id !== context.person_track_id || sourceBinding.selection_epoch !== modelEpoch
          || sourceBinding.camera_reference_epoch !== w.camera_reference_epoch
          || !integer(sourceBinding.processed_index) || !close(sourceBinding.timestamp_ms, time)
          || ["pts", "time_base_num", "time_base_den"].some(key => sourceBinding[key] !== p[key])) return invalid("step_motion_model_source_binding_invalid");
    }
    for (const joint of contract.required_joint_names) {
      const p = point(points[joint], true), c = confidence[joint];
      if (points[joint] !== null && !p || c !== null && !finite(c)) return invalid("step_motion_geometry_invalid");
      selected[joint] = p && (finite(c) && c >= .35 && c <= 1 || c === null && row.observation_semantics === "monocular_model_estimate") ? p : null;
    }
    normalized.push(selected);
    if (contract.required_joint_names.every(j => selected[j] !== null)) validFrames.push(Number(frames[i]));
  }
  const requested = obj(annotation.requested_window_ms), gaps = times.slice(1).map((time, i) => time - times[i]);
  if (!close(times[0], requested.start_ms) || !close(times.at(-1), requested.end_ms) || !close(times[0], w.start_ms) || !close(times.at(-1), w.end_ms)
      || gaps.some(gap => gap > Math.min(160, median(gaps) * 1.8))) return invalid("step_motion_source_pts_invalid");
  const observed = rows(observation.source_frames).map(obj);
  if (!observed.length || observed.length > n || observed.some((p, i) => !exactKeys(p, ["frame_index", "pts", "time_base_num", "time_base_den"])
      || !integer(p.frame_index) || i > 0 && Number(observed[i - 1].frame_index) >= p.frame_index
      || !pts.some(b => ["frame_index", "pts", "time_base_num", "time_base_den"].every(k => p[k] === b[k])))) return invalid("step_motion_observation_pts_mismatch");
  if (raw.valid_frame_count !== validFrames.length || !matches(validFrames, raw.valid_source_frames)
      || context.quality.valid_frame_count !== validFrames.length
      || !matches([...contract.required_joint_names].sort(), [...context.quality.required_joint_names].sort())
      || raw.geometry_gate_passed !== (validFrames.length * 10 >= n * 7)) return invalid("step_motion_joint_union_invalid");
  const reference = rows(raw.reference_series).map(obj);
  if (n < 7 || validFrames.length !== n || reference.length !== n) {
    if (raw.status !== "unavailable" || raw.complete_visual_motion_cycle !== false || !rows(raw.reason_codes).length || evidence.step_cycle_bound !== false) return invalid("step_motion_incomplete_cannot_authorize");
    return { ...empty("unavailable", "step_motion_continuous_geometry_missing"), moving_foot: side as "left" | "right", total_frame_count: n, valid_frame_count: validFrames.length };
  }
  const points: Record<string, Point>[] = [];
  for (let i = 0; i < n; i++) {
    const row = reference[i], p = obj(row.points), accepted: Record<string, Point> = {};
    if (row.frame_index !== frames[i] || !exactKeys(p, contract.required_joint_names)) return invalid("step_motion_reference_series_invalid");
    for (const joint of contract.required_joint_names) { const value = point(p[joint]); if (!value) return invalid("step_motion_reference_series_invalid"); accepted[joint] = value; }
    // _sample normalizes the camera-compensated reference plane around the
    // shoulder midpoint. Bind both stored series using that exact full-torso
    // formula; they are not independent coordinate sources.
    const center = middle(accepted.left_shoulder, accepted.right_shoulder);
    const normalizationScale = Math.max(distance(center, middle(accepted.left_hip, accepted.right_hip)), distance(accepted.left_shoulder, accepted.right_shoulder) * .75);
    if (source[i].normalization_basis !== "full_torso" || !finite(normalizationScale) || normalizationScale < 12
        || contract.required_joint_names.some(joint => !matches(accepted[joint].map((v, axis) => (v - center[axis]) / normalizationScale), normalized[i][joint]))) return invalid("step_motion_normalized_reference_mismatch");
    points.push(accepted);
  }
  const scales = points.map(p => distance(middle(p.left_shoulder, p.right_shoulder), middle(p.left_hip, p.right_hip))), scale = median(scales), count = Math.max(3, Math.ceil(n * .2));
  if (!finite(scale) || scale <= 0 || !near(raw.fixed_torso_scale, scale) || !matches(scales, raw.source_torso_scales) || raw.endpoint_count !== count) return invalid("step_motion_fixed_scale_invalid");
  const feet = { left: recomputeStepMotionFoot(points.map(p => p.left_ankle), times, pts, frames as number[], scale, count),
    right: recomputeStepMotionFoot(points.map(p => p.right_ankle), times, pts, frames as number[], scale, count) };
  const declared = feet[side as "left" | "right"], other = feet[side === "left" ? "right" : "left"], reasons = [...declared.reason_codes];
  if (other.complete_visual_motion_cycle) reasons.push("step_motion_competing_ankle_cycle");
  if (other.net_displacement_torso > declared.net_displacement_torso || other.path_length_torso > declared.path_length_torso) reasons.push("step_motion_declared_side_conflicts_with_paths");
  const complete = reasons.length === 0;
  if (!matches(feet, raw.feet) || !matches([...new Set(reasons)].sort(), [...rows(raw.reason_codes)].sort())
      || raw.complete_visual_motion_cycle !== complete || evidence.step_cycle_bound !== complete
      || raw.status !== (complete ? "measured_visual_motion" : "unavailable")) return invalid("step_motion_recomputed_evidence_mismatch");
  const result: StepMotionValidation = { status: complete ? "measured_visual_motion" : "unavailable", reason_codes: reasons, moving_foot: side as "left" | "right",
    total_frame_count: n, valid_frame_count: validFrames.length, fixed_torso_scale: scale, endpoint_count: count, feet, values: {}, condition_values_match: false,
    model_estimate_sample_count: source.filter(row => row.observation_semantics === "monocular_model_estimate").length };
  if (!complete) return result;
  const paths = [feet.left.path_length_torso, feet.right.path_length_torso];
  const tilts = normalized.flatMap(p => { const shoulder = middle(p.left_shoulder!, p.right_shoulder!), hip = middle(p.left_hip!, p.right_hip!);
    return distance(shoulder, hip) < .1 ? [] : [Math.atan2(Math.abs(hip[0] - shoulder[0]), Math.abs(hip[1] - shoulder[1])) * 180 / Math.PI]; });
  result.values = { ankle_max_displacement_torso: Math.max(feet.left.net_displacement_torso, feet.right.net_displacement_torso),
    ankle_stability_ratio: Math.max(...paths) >= .1 ? Math.min(...paths) / Math.max(...paths) : null,
    "trunk_tilt_deg.range": tilts.length * 10 >= n * 7 ? Math.max(...tilts) - Math.min(...tilts) : null };
  result.condition_values_match = !!context.conditions && spec.conditions.length === context.conditions.length && spec.conditions.every(([metric]) => {
    const row = context.conditions!.find(c => c.metric === metric); return !!row && near(row.value, result.values[String(metric)]);
  });
  if (!result.condition_values_match) return invalid("step_motion_condition_arithmetic_mismatch");
  return result;
}

export function stepMotionSummary(value: StepMotionValidation | null): string {
  if (!value) return "";
  if (value.status === "invalid") return "移动脚运动证据未通过独立复算；不能用于评分";
  if (value.status === "unavailable") return "尚未确认完整低速—移动—低速片段；缺测不记零分";
  const foot = value.feet![value.moving_foot!];
  return `人工观察的移动脚：${value.moving_foot === "left" ? "左脚" : "右脚"}；${value.valid_frame_count}/${value.total_frame_count} 原帧六关节共同有效；低速—移动—低速 ${foot.phases.map(p => `${p.duration_ms.toFixed(2)}ms/${p.source_frames.length}帧`).join("、")}；二维净位移 ${foot.net_displacement_torso.toFixed(4)} 躯干单位；不是前脚、触地、目标方向或技术步法确认${value.model_estimate_sample_count ? "；包含单目模型估计，完整测量来源随报告保留" : ""}`;
}
