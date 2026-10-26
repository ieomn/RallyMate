/** Independent FS05-M02 arithmetic over every original frame. A complete
 * visual movement is not evidence of contact, cadence, or the FS05 phase. */
import contract from "./micro-cycle-display-contract";
import { objectOf as obj, validFsWindowSource, type EvidenceObject } from "./manual-card-evidence";
import { recomputeStepMotionFoot, type StepMotionFoot } from "./fs-step-motion-evidence";
import { isFormalScoringPolicy } from "./formal-card-scoring-policy";

type Point = [number, number];
type Context = { indicator_id: string; event_id: string; video_id: string; person_track_id: number;
  scoring_policy_version?: string | null; application_rule_version?: string | null; measurement_version: string;
  window: { start_ms: number; end_ms: number } | null; conditions: { metric: string; value: number | null }[];
  quality: { total_frame_count: number; valid_frame_count: number; required_joint_names: string[] } };
export type MicroCycle = { cycle_id: string; moving_ankle: "left" | "right"; motion_start_source_frame: number;
  motion_end_source_frame: number; source_frames: number[]; local_motion_evidence: StepMotionFoot;
  complete_visual_motion_cycle: boolean; reason_codes: string[] };
export type MicroEpisode = { episode_id: string; motion_start_source_frame: number; motion_end_source_frame: number;
  cycle_ids: string[]; complete_cycle_ids: string[]; incomplete_cycle_ids: string[]; complete_visual_adjustment_episode: boolean };
export interface MicroCycleValidation {
  status: "measured_visual_adjustment_episodes" | "unavailable" | "invalid"; reason_codes: string[];
  total_frame_count: number | null; fixed_torso_scale: number | null; duration_s: number | null;
  values: Record<string, number>; feet: { left: StepMotionFoot; right: StepMotionFoot } | null;
  cycles: MicroCycle[]; episodes: MicroEpisode[]; auxiliary: Record<string, number | string>; condition_values_match: boolean;
}
const rows = (v: unknown): unknown[] => Array.isArray(v) ? v : [];
const finite = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
const integer = (v: unknown): v is number => Number.isSafeInteger(v) && Number(v) >= 0;
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

const empty = (status: MicroCycleValidation["status"], reason: string): MicroCycleValidation => ({ status, reason_codes: [reason],
  total_frame_count: null, fixed_torso_scale: null, duration_s: null, values: {}, feet: null, cycles: [], episodes: [], auxiliary: {}, condition_values_match: false });
export function microCycleApplies(id: string, row: EvidenceObject): boolean {
  return id === contract.card_id && (row.application_rule_version != null || row.measurement_version === contract.measurement_version
    || !!obj(row.source_window_evidence).micro_adjustment_cycles);
}

/** Positive overlap, not a shared endpoint, forms a transitive component.
 * Incomplete motion remains in the union, but never makes it complete. */
export function recomputeMicroEpisodes(cycles: Pick<MicroCycle, "cycle_id" | "motion_start_source_frame" | "motion_end_source_frame" | "complete_visual_motion_cycle">[]): MicroEpisode[] {
  const episodes: MicroEpisode[] = [];
  for (const c of [...cycles].sort((a, b) => a.motion_start_source_frame - b.motion_start_source_frame
      || a.motion_end_source_frame - b.motion_end_source_frame || (a.cycle_id < b.cycle_id ? -1 : a.cycle_id > b.cycle_id ? 1 : 0))) {
    if (!episodes.length || c.motion_start_source_frame >= episodes.at(-1)!.motion_end_source_frame) episodes.push({
      episode_id: `episode-${episodes.length + 1}`, motion_start_source_frame: c.motion_start_source_frame, motion_end_source_frame: c.motion_end_source_frame,
      cycle_ids: [], complete_cycle_ids: [], incomplete_cycle_ids: [], complete_visual_adjustment_episode: false });
    const e = episodes.at(-1)!;
    e.motion_end_source_frame = Math.max(e.motion_end_source_frame, c.motion_end_source_frame);
    e.cycle_ids.push(c.cycle_id);
    (c.complete_visual_motion_cycle ? e.complete_cycle_ids : e.incomplete_cycle_ids).push(c.cycle_id);
    e.complete_visual_adjustment_episode = e.complete_cycle_ids.length > 0;
  }
  return episodes;
}

export function validateMicroCycles(annotation: EvidenceObject | null, evidence: EvidenceObject | null, w: EvidenceObject | null,
  context: Context): MicroCycleValidation | null {
  if (!microCycleApplies(context.indicator_id, { ...context, source_window_evidence: evidence })) return null;
  const invalid = (why: string) => empty("invalid", why);
  if (!evidence?.micro_adjustment_cycles) return empty("unavailable", "micro_cycle_evidence_missing");
  const raw = obj(evidence.micro_adjustment_cycles);
  if (raw.status !== "measured_visual_adjustment_episodes") return empty("unavailable", "micro_cycle_continuous_source_missing");
  if (!annotation || !w || !context.window || !isFormalScoringPolicy(context.scoring_policy_version)
      || context.application_rule_version !== contract.application_rule_version || context.measurement_version !== contract.measurement_version
      || raw.version !== contract.measurement_version || raw.source_grade_supported !== false || raw.contact_verified !== false
      || raw.technical_step_or_FS05_phase_verified !== false || raw.interpolation_used !== false || raw.geometry_gate_passed !== true
      || raw.denominator !== "all_decoder_samples_in_bound_window" || !same(raw.reason_codes, [])
      || ["score", "grade", "passed", "original_grade", "scoring_eligible"].some(k => k in raw)
      || raw.episode_policy !== contract.episode_policy || raw.low_speed_threshold_torso_s !== contract.low_speed_threshold_torso_s
      || raw.minimum_phase_duration_ms !== contract.minimum_phase_duration_ms || raw.minimum_phase_frame_count !== contract.minimum_phase_frame_count
      || annotation.step_motion_observation != null || w.step_motion_observation != null
      || !validFsWindowSource(annotation, evidence, w, context, contract.annotation_version, false)) return invalid("micro_cycle_source_contract_invalid");
  const n = context.quality.total_frame_count, frames = rows(evidence.source_frames), bindings = rows(evidence.source_frame_bindings).map(obj);
  const source = rows(raw.source_geometry_series).map(obj), original = rows(raw.local_normalization).map(obj), reference = rows(raw.reference_series).map(obj);
  if (n < 7 || n > 20000 || context.quality.valid_frame_count !== n || raw.total_frame_count !== n || raw.valid_frame_count !== n
      || !same(frames, raw.source_frames) || !same(frames, raw.valid_source_frames) || !same(bindings, raw.source_frame_bindings)
      || !same(contract.required_joint_names, raw.required_joint_names)
      || !same([...contract.required_joint_names].sort(), [...context.quality.required_joint_names].sort())
      || [source, original, reference].some(a => a.length !== n)) return invalid("micro_cycle_full_denominator_invalid");
  const points: Record<string, Point>[] = [], times: number[] = [], modelEpochs = new Set<number>();
  const firstModel = source.findIndex(s => s.observation_semantics === "monocular_model_estimate" || s.pose_source === "sam3d");
  const firstProcessed = firstModel < 0 ? null : obj(source[firstModel].source_binding).processed_index;
  for (let i = 0; i < n; i++) {
    const b = bindings[i], s = source[i], local = original[i], input = obj(local.input_geometry), p = obj(reference[i].points), accepted: Record<string, Point> = {};
    if (reference[i].frame_index !== frames[i] || s.frame_index !== frames[i] || local.frame_index !== frames[i] || input.frame_index !== frames[i]
        || !keys(p, contract.required_joint_names) || !integer(b.pts) || !integer(b.time_base_num) || !integer(b.time_base_den)
        || !b.time_base_num || !b.time_base_den || !Number.isSafeInteger(b.pts * b.time_base_num)) return invalid("micro_cycle_geometry_invalid");
    times.push(b.pts * b.time_base_num * 1000 / b.time_base_den);
    for (const j of contract.required_joint_names) { const value = point(p[j]); if (!value) return invalid("micro_cycle_geometry_invalid"); accepted[j] = value; }
    const center = middle(accepted.left_shoulder, accepted.right_shoulder), hips = middle(accepted.left_hip, accepted.right_hip);
    const scale = Math.max(distance(center, hips), distance(accepted.left_shoulder, accepted.right_shoulder) * .75);
    const oldSide = input.normalization_basis === "left_shoulder_left_torso" ? "left" : input.normalization_basis === "right_shoulder_right_torso" ? "right" : null;
    if (!oldSide && input.normalization_basis !== "full_torso") return invalid("micro_cycle_original_basis_invalid");
    const oldCenter = oldSide ? accepted[oldSide + "_shoulder"] : center;
    const oldScale = oldSide ? distance(oldCenter, accepted[oldSide + "_hip"]) : scale;
    if (scale < 12 || oldScale < 12 || local.output_basis !== "full_torso" || local.source_geometry_verified !== true
        || s.normalization_basis !== "full_torso" || !same(local.input_center, oldCenter) || !near(local.input_scale, oldScale)
        || !same(local.output_center, center) || !near(local.output_scale, scale)) return invalid("micro_cycle_normalization_invalid");
    for (const [geometry, origin, divisor] of [[s, center, scale], [input, oldCenter, oldScale]] as [EvidenceObject, Point, number][]) {
      const normalized = obj(geometry.normalized_points), confidences = obj(geometry.point_confidences), sb = obj(geometry.source_binding);
      if (!keys(normalized, contract.required_joint_names) || !keys(confidences, contract.required_joint_names)) return invalid("micro_cycle_geometry_invalid");
      const model = geometry.observation_semantics === "monocular_model_estimate" || geometry.pose_source === "sam3d";
      if (model) {
        if (geometry.observation_semantics !== "monocular_model_estimate" || geometry.pose_source !== "sam3d"
            || geometry.model_epoch_semantics !== "detector_track_epoch_primary_selection_applied_downstream"
            || b.model_epoch_semantics !== geometry.model_epoch_semantics || !integer(geometry.source_model_selection_epoch)
            || b.source_model_selection_epoch !== geometry.source_model_selection_epoch || geometry.selection_epoch !== w.selection_epoch
            || !sha(sb.video_sha256) || String(sb.video_sha256).toLowerCase() !== String(w.video_sha256).toLowerCase()
            || sb.frame_index !== frames[i] || sb.person_track_id !== context.person_track_id || sb.selection_epoch !== geometry.source_model_selection_epoch
            || sb.camera_reference_epoch !== w.camera_reference_epoch || !integer(sb.processed_index) || !integer(firstProcessed)
            || sb.processed_index !== Number(firstProcessed) + Number(frames[i]) - Number(frames[firstModel]) || !clockNear(sb.timestamp_ms, times[i])
            || ["pts", "time_base_num", "time_base_den"].some(k => sb[k] !== b[k])) return invalid("micro_cycle_model_source_invalid");
        modelEpochs.add(Number(sb.selection_epoch));
      } else if (geometry.observation_semantics != null) return invalid("micro_cycle_observation_semantics_invalid");
      for (const j of contract.required_joint_names) {
        const c = confidences[j];
        if (!(finite(c) && c >= .35 && c <= 1 || c === null && model)
            || !same(normalized[j], accepted[j].map((v, axis) => (v - origin[axis]) / divisor))) return invalid("micro_cycle_joint_geometry_invalid");
      }
    }
    if (["point_confidences", "observation_semantics", "pose_source", "source_binding", "model_epoch_semantics", "selection_epoch", "source_model_selection_epoch"].some(k => !same(s[k], input[k]))) return invalid("micro_cycle_source_rewritten");
    points.push(accepted);
  }
  if (modelEpochs.size > 1) return invalid("micro_cycle_model_epoch_changed");

  const scales = points.map(p => distance(middle(p.left_shoulder, p.right_shoulder), middle(p.left_hip, p.right_hip)));
  const scale = median(scales), count = Math.max(3, Math.ceil(n * .2));
  const duration = (times[n - 1] - times[0]) / 1000;
  if (!finite(scale) || scale <= 0 || !near(raw.fixed_torso_scale, scale) || !same(raw.source_torso_scales, scales)
      || !near(raw.duration_s, duration)) return invalid("micro_cycle_fixed_scale_invalid");
  const feet = { left: recomputeStepMotionFoot(points.map(p => p.left_ankle), times, bindings, frames as number[], scale, count),
    right: recomputeStepMotionFoot(points.map(p => p.right_ankle), times, bindings, frames as number[], scale, count) };
  const cycles: MicroCycle[] = [];
  for (const side of ["left", "right"] as const) {
    const phases = feet[side].phases, path = points.map(p => p[side + "_ankle"]);
    phases.forEach((phase, ordinal) => {
      if (!phase.moving) return;
      const first = ordinal ? phases[ordinal - 1].first_interval : phase.first_interval;
      const last = (ordinal + 1 < phases.length ? phases[ordinal + 1].last_interval : phase.last_interval) + 1;
      const localFrames = frames.slice(first, last + 1) as number[];
      const local = recomputeStepMotionFoot(path.slice(first, last + 1), times.slice(first, last + 1), bindings.slice(first, last + 1),
        localFrames, scale, Math.max(3, Math.ceil(localFrames.length * .2)));
      const reasons = [...local.reason_codes];
      if (!ordinal) reasons.push("preceding_low_motion_censored_at_window_start");
      if (ordinal === phases.length - 1) reasons.push("following_low_motion_censored_at_window_end");
      cycles.push({ cycle_id: `${side}-${ordinal}`, moving_ankle: side, motion_start_source_frame: phase.source_frames[0],
        motion_end_source_frame: phase.source_frames.at(-1)!, source_frames: localFrames, local_motion_evidence: local,
        complete_visual_motion_cycle: !reasons.length, reason_codes: [...new Set(reasons)].sort() });
    });
  }
  const episodes = recomputeMicroEpisodes(cycles), complete = episodes.filter(e => e.complete_visual_adjustment_episode).length;
  const values = { complete_visual_adjustment_episode_count: complete,
    both_ankles_max_path_length_torso: Math.max(feet.left.path_length_torso, feet.right.path_length_torso) };
  const auxiliary = { complete_visual_ankle_cycle_count: cycles.filter(c => c.complete_visual_motion_cycle).length,
    incomplete_ankle_motion_run_count: cycles.filter(c => !c.complete_visual_motion_cycle).length, all_motion_episode_count: episodes.length,
    observed_complete_episode_frequency_hz: complete / duration,
    frequency_semantics: "complete_observed_episodes_per_bound_window_second_not_actual_contact_cadence" };
  if (!same(feet, raw.feet) || !same(cycles, raw.cycles) || !same(episodes, raw.episodes)
      || !same(auxiliary, raw.auxiliary) || !same(values, raw.measurements)
      || context.conditions.length !== contract.conditions.length || contract.conditions.some(c => {
        const reported = context.conditions.find(row => row.metric === c.metric)?.value, expected = values[c.metric as keyof typeof values];
        if (c.unit === "count") return reported !== expected;
        if (!near(reported, expected) || !finite(reported)) return true;
        const bounds = c.threshold as number[];
        return (reported >= bounds[0] && reported <= bounds[1]) !== (expected >= bounds[0] && expected <= bounds[1]);
      })) return invalid("micro_cycle_arithmetic_mismatch");
  return { status: "measured_visual_adjustment_episodes", reason_codes: [], total_frame_count: n, fixed_torso_scale: scale,
    duration_s: duration, values, feet, cycles, episodes, auxiliary, condition_values_match: true };
}

export function microCycleSummary(value: MicroCycleValidation | null): string {
  if (!value) return "";
  if (value.status !== "measured_visual_adjustment_episodes") return "连续微调缺少完整来源，未生成分数";
  return `完整视觉运动片段 ${value.values.complete_visual_adjustment_episode_count} 个；双脚全窗路径最大值 ${value.values.both_ankles_max_path_length_torso.toFixed(3)} 躯干单位；完整脚部周期 ${value.auxiliary.complete_visual_ankle_cycle_count} 个，截断或不完整运动 ${value.auxiliary.incomplete_ankle_motion_run_count} 个；重叠运动合为一个片段，频率仅为窗口描述，不是真实触地步频`;
}
