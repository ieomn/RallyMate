/** Recompute the earliest complete local dip from the retained parent rows.
 * These are compensated reference coordinates, never an inferred takeoff. */
import { isFormalScoringPolicy } from "./formal-card-scoring-policy";

type Obj = Record<string, unknown>;
type Point = [number, number];
const obj = (v: unknown): Obj => v && typeof v === "object" && !Array.isArray(v) ? v as Obj : {};
const array = (v: unknown): unknown[] => Array.isArray(v) ? v : [];
const finite = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
const int = (v: unknown): v is number => Number.isSafeInteger(v) && Number(v) >= 0;
const near = (a: unknown, b: unknown) => finite(a) && finite(b) && Math.abs(a - b) <= 1e-8 * Math.max(1, Math.abs(a), Math.abs(b));
const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);
const point = (v: unknown): Point | null => Array.isArray(v) && v.length === 2 && v.every(finite) ? v as Point : null;
const midpoint = (a: Point, b: Point): Point => [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
const distance = (a: Point, b: Point) => Math.hypot(a[0] - b[0], a[1] - b[1]);
const median = (v: number[]) => { const s = [...v].sort((a, b) => a - b); return (s[Math.floor((s.length - 1) / 2)] + s[Math.floor(s.length / 2)]) / 2; };
const joints = ["left_shoulder", "right_shoulder", "left_hip", "right_hip", "left_knee", "right_knee", "left_ankle", "right_ankle"];
const pts = (b: Obj): number | null => int(b.pts) && int(b.time_base_num) && b.time_base_num > 0 && int(b.time_base_den) && b.time_base_den > 0
  && Number.isSafeInteger(b.pts * b.time_base_num) ? b.pts * b.time_base_num * 1000 / b.time_base_den : null;
function transformValid(v: unknown) {
  const rows = array(v).map(array);
  if (rows.length !== 2 || rows.some(r => r.length !== 3 || !r.every(finite))) return false;
  const [[a, b], [c, d]] = rows as number[][], determinant = a * d - b * c;
  return determinant >= .25 && determinant <= 4 && [a * a + c * c, b * b + d * d].every(n => Math.abs(n - determinant) <= 1e-8 + 1e-5 * Math.abs(determinant))
    && Math.abs(a * b + c * d) <= 1e-8;
}
function flexion(hip: Point, knee: Point, ankle: Point) {
  const a: Point = [hip[0] - knee[0], hip[1] - knee[1]], b: Point = [ankle[0] - knee[0], ankle[1] - knee[1]];
  const denominator = Math.hypot(...a) * Math.hypot(...b);
  return denominator > 1e-12 ? 180 - Math.acos(Math.min(1, Math.max(-1, (a[0] * b[0] + a[1] * b[1]) / denominator))) * 180 / Math.PI : NaN;
}
type Context = { indicator_id: string; video_id: string; event_id: string; person_track_id: number; scoring_policy_version: string | null;
  window: { start_ms: number; end_ms: number } | null; quality: { total_frame_count: number; valid_frame_count: number };
  conditions: { metric: string; value: number | null }[] };
export interface LocalDipValidation { status: "measured_local_dip" | "invalid"; reason_codes: string[]; values: Record<string, number>;
  source_frames: number[]; confirmation_source_frames: number[]; model_estimate_sample_count: number }
const invalid = (reason: string): LocalDipValidation => ({ status: "invalid", reason_codes: [reason], values: {}, source_frames: [], confirmation_source_frames: [], model_estimate_sample_count: 0 });

/** Verify the retained decoder/selection chain, independently of stored flags.
 * Non-fallback card geometry is computed by the producer, not recreated here. */
export function validFormalTemporalSource(value: unknown, context: Context): boolean {
  const evidence = obj(value), quality = obj(evidence.event_quality), diagnostics = obj(quality.event_identity_diagnostics);
  const tracks = array(evidence.source_track_ids), rows = array(evidence.source_frame_bindings).map(obj), frames = array(evidence.source_frames);
  const phase = obj(evidence.phase_selection);
  if (context.indicator_id === "FS01-M02" && !Object.keys(obj(obj(evidence.fallback).selected)).length
      && (phase.phase_contract_version !== "source-preload-turning-point-v1.0.0" || phase.status !== "observed_candidate"
        || phase.source_preload_start_ms !== context.window?.start_ms || phase.source_preload_ms !== context.window?.end_ms)) return false;
  const sourceTimes = array(evidence.source_timestamps_ms), videoTimes = array(evidence.timestamps_ms), flags = array(quality.raw_input_quality_flags);
  const phaseKey = ({ "FS01-M05": "landing_proxy_ms", "FS09-M03": "peak_speed_ms", "FS09-M04": "deceleration_peak_ms", "FS09-M05": "stable_control_onset_ms" } as Record<string, string>)[context.indicator_id];
  if (phaseKey && flags.some(flag => typeof flag === "string" && flag.endsWith(":" + phaseKey) && /censored|low_sample_peak/.test(flag))) return false;
  if (!context.window || !isFormalScoringPolicy(context.scoring_policy_version) || evidence.source_frame_binding_version !== "temporal-source-frame-bindings-v1.0.0" || quality.temporal_policy_version !== "temporal-card-local-quality-v2"
      || !Array.isArray(quality.raw_input_quality_flags) || flags.some(v => typeof v !== "string")
      || !Array.isArray(quality.blocking_flags) || array(quality.blocking_flags).length || array(quality.hard_fail_flags).length || array(quality.scoring_block_flags).length
      || flags.some(flag => /confirmed.*(?:id|identity).*switch|(?:id|identity).*switch.*confirmed/.test(String(flag).toLowerCase()) && !String(flag).toLowerCase().includes("unconfirmed"))
      || !same(diagnostics.source_track_ids, tracks) || tracks.length !== 1 || !int(tracks[0])
      || typeof diagnostics.primary_identity_ambiguous !== "boolean" || !int(diagnostics.source_track_switch_candidate_count)
      || diagnostics.confirmed_id_switch_count != null && diagnostics.confirmed_id_switch_count !== 0
      || rows.length < 3 || rows.length !== context.quality.total_frame_count || context.quality.valid_frame_count !== rows.length
      || frames.length !== rows.length || videoTimes.length !== rows.length || sourceTimes.length !== rows.length
      || evidence.time_basis !== "decoder_pts" || evidence.interpolation_used !== false
      || videoTimes[0] !== context.window.start_ms || videoTimes.at(-1) !== context.window.end_ms) return false;
  function chain(bindings: Obj[], expectedFrames: unknown[], expectedVideo?: unknown[], expectedSource?: unknown[]) {
    if (!bindings.length || bindings.length !== expectedFrames.length) return false;
    const first = bindings[0], times = bindings.map(pts), origins: number[] = [];
    for (let i = 0; i < bindings.length; i++) {
      const b = bindings[i], selection = obj(b.primary_selection), t = times[i];
      if (b.status !== "verified_decoder_binding" || !int(b.frame_index) || b.frame_index !== expectedFrames[i] || !int(b.processed_index)
          || t === null || !finite(b.source_timestamp_ms) || Math.abs(b.source_timestamp_ms - t) > .51 || !finite(b.video_timestamp_ms)
          || b.timestamp_source !== "decoder_pts" || typeof b.video_sha256 !== "string" || !/^[a-f0-9]{64}$/i.test(b.video_sha256)
          || b.primary_player_id !== context.person_track_id || b.person_track_id !== tracks[0] || !int(b.selection_epoch) || !int(b.camera_reference_epoch)
          || ["video_sha256", "person_track_id", "primary_player_id", "selection_epoch", "camera_reference_epoch", "time_base_num", "time_base_den"].some(k => b[k] !== first[k])
          || selection.source_frame_index !== b.frame_index || selection.timestamp_ms !== b.video_timestamp_ms || selection.primary_player_id !== context.person_track_id
          || selection.source_track_id !== tracks[0] || selection.selection_status !== "selected" || typeof selection.identity_ambiguous !== "boolean"
          || "processed_index" in selection && selection.processed_index !== b.processed_index
          || ["selection_epoch", "camera_reference_epoch"].some(k => k in selection && selection[k] !== b[k])
          || expectedVideo && expectedVideo[i] !== b.video_timestamp_ms || expectedSource && !near(expectedSource[i], t)
          || i > 0 && (b.frame_index !== Number(bindings[i - 1].frame_index) + 1 || b.processed_index !== Number(bindings[i - 1].processed_index) + 1
            || t <= times[i - 1]! || t - times[i - 1]! > 160 || b.video_timestamp_ms <= Number(bindings[i - 1].video_timestamp_ms))) return false;
      origins.push(t - b.video_timestamp_ms);
      const model = obj(b.model_geometry_source), raw = obj(model.source_binding);
      if (Object.keys(model).length && (model.pose_source !== "sam3d" || model.observation_semantics !== "monocular_model_estimate"
          || model.confidence_available !== false || model.metadata_source !== "PoseSequence.model_estimate_bindings_contract"
          || ["frame_index", "processed_index", "pts", "time_base_num", "time_base_den", "person_track_id", "selection_epoch", "camera_reference_epoch"].some(k => raw[k] !== b[k])
          || typeof raw.video_sha256 !== "string" || raw.video_sha256.toLowerCase() !== b.video_sha256.toLowerCase()
          || !finite(raw.timestamp_ms) || Math.abs(raw.timestamp_ms - t) > .51)) return false;
    }
    return Math.max(...origins) - Math.min(...origins) <= 1.02 + 1e-9;
  }
  if (!chain(rows, frames, videoTimes, sourceTimes) || !near(evidence.source_duration_ms, pts(rows.at(-1)!)! - pts(rows[0])!)
      || pts(rows.at(-1)!)! - pts(rows[0])! < 80) return false;
  const successorCondition = context.conditions.find(c => c.metric === "successor_interval_ms");
  if (successorCondition) {
    const successor = obj(evidence.successor), gap = array(successor.source_frame_bindings).map(obj);
    if (successor.source_frame_binding_version !== "temporal-source-frame-bindings-v1.0.0" || !same(successor.source_track_ids, tracks) || !same(successor.successor_source_track_ids, tracks) || !chain(gap, array(successor.source_frames))
        || gap[0].frame_index !== rows[0].frame_index || gap[0].video_timestamp_ms !== context.window.start_ms
        || gap.at(-1)!.video_timestamp_ms !== successor.successor_start_ms
        || !near(successorCondition.value, pts(gap.at(-1)!)! - pts(gap[0])!) || !near(successor.interval_ms, successorCondition.value)
        || ["video_sha256", "person_track_id", "primary_player_id", "selection_epoch", "camera_reference_epoch"].some(k => gap[0][k] !== rows[0][k])) return false;
  }
  return true;
}

export function validateTemporalLocalDip(value: unknown, context: Context): LocalDipValidation | null {
  const evidence = obj(value), fallback = obj(evidence.fallback), selected = obj(fallback.selected);
  if (!Object.keys(selected).length) return null;
  const proof = obj(fallback.proof), parent = obj(proof.parent_event), bound = obj(proof.measurement_window_ms), raw = array(proof.rows).map(obj);
  if (!isFormalScoringPolicy(context.scoring_policy_version) || context.indicator_id !== "FS01-M02" || !context.window
      || proof.version !== "temporal-local-dip-proof-v1.0.0" || !same(proof.required_joint_names, joints)
      || proof.coordinate_contract_version !== "isotropic-frame-long-edge-v1.1.0" || proof.coordinate_system !== "camera_reference_xy_divided_by_source_long_edge"
      || proof.points_semantics !== "already_camera_compensated_reference_xy_do_not_apply_transform_again"
      || proof.expert_technical_grade_verified !== false || proof.takeoff_inferred !== false
      || fallback.takeoff_inferred !== false || fallback.original_event_modified !== false
      || fallback.strategy !== "earliest_complete_local_dip_before_landing_or_event_end"
      || fallback.phase_semantics !== "local_dip_visual_proxy_not_confirmed_pre_takeoff_load"
      || parent.video_id !== context.video_id || parent.event_id !== context.event_id || parent.event_code !== "FS01" || parent.person_track_id !== context.person_track_id
      || !finite(parent.start_ms) || !finite(parent.end_ms) || parent.start_ms >= parent.end_ms || raw.length < 5 || raw.length > 30000
      || raw[0].timestamp_ms !== parent.start_ms || raw.at(-1)!.timestamp_ms !== parent.end_ms
      || !same(bound, context.window) || !same(evidence.requested_window_ms, bound)
      || selected.start_ms !== bound.start_ms || selected.end_ms !== bound.end_ms || selected.confirmation_end_ms !== proof.confirmation_end_ms) return invalid("local_dip_source_contract_invalid");
  const tracks = array(proof.source_track_ids), flags = array(parent.quality_flags), phases = obj(parent.key_phases_ms), landing = phases.landing_proxy_ms;
  if (tracks.length !== 1 || !int(tracks[0]) || !Array.isArray(parent.quality_flags) || flags.some(v => typeof v !== "string")) return invalid("local_dip_parent_identity_invalid");
  const landingValid = finite(landing) && landing >= parent.start_ms && landing <= parent.end_ms
    && !flags.some(v => String(v).endsWith(":landing_proxy_ms") && /censored|low_sample_peak/.test(String(v)));
  const searchEnd = landingValid ? landing : parent.end_ms;
  if (proof.search_end_ms !== searchEnd || proof.search_end_basis !== (landingValid ? "landing_proxy_ms" : "event_end")) return invalid("local_dip_search_boundary_invalid");
  const applied = array(obj(proof.camera_compensation).applied_source_frames);
  const parsed = raw.map(row => {
    const b = obj(row.source_binding), selection = obj(row.primary_selection), time = pts(b), model = obj(row.model_geometry_source), mb = obj(model.source_binding);
    const modelPresent = Object.keys(model).length > 0;
    const modelValid = modelPresent && model.pose_source === "sam3d" && model.observation_semantics === "monocular_model_estimate"
      && model.metadata_source === "PoseSequence.model_estimate_bindings_contract" && model.confidence_available === false
      && ["frame_index", "processed_index", "pts", "time_base_num", "time_base_den", "person_track_id", "selection_epoch", "camera_reference_epoch"].every(k => mb[k] === b[k])
      && typeof mb.video_sha256 === "string" && typeof b.video_sha256 === "string" && mb.video_sha256.toLowerCase() === b.video_sha256.toLowerCase()
      && finite(mb.timestamp_ms) && time !== null && Math.abs(mb.timestamp_ms - time) <= .51;
    const points = Object.fromEntries(joints.map(name => {
      const p = point(obj(row.points)[name]), confidence = obj(row.confidence)[name];
      const valid = p && (modelPresent ? modelValid && obj(row.model_estimate_mask)[name] === true : finite(confidence) && confidence >= .25 && confidence <= 1);
      return [name, valid ? p : null];
    })) as Record<string, Point | null>;
    const size = array(row.frame_dimensions_px);
    const valid = int(row.frame_index) && row.frame_index === b.frame_index && int(b.processed_index) && time !== null
      && typeof b.video_sha256 === "string" && /^[a-f0-9]{64}$/i.test(b.video_sha256)
      && b.timestamp_source === "decoder_pts" && finite(b.source_timestamp_ms) && Math.abs(b.source_timestamp_ms - time) <= .51
      && b.video_timestamp_ms === row.timestamp_ms && finite(row.timestamp_ms) && b.primary_player_id === context.person_track_id && b.person_track_id === tracks[0]
      && int(b.selection_epoch) && int(b.camera_reference_epoch) && row.camera_reference_epoch === b.camera_reference_epoch
      && ["reference", "stationary", "compensated"].includes(String(row.camera_motion_status))
      && selection.source_frame_index === row.frame_index && (!("processed_index" in selection) || selection.processed_index === b.processed_index)
      && selection.timestamp_ms === row.timestamp_ms && selection.primary_player_id === context.person_track_id && selection.source_track_id === tracks[0]
      && selection.selection_status === "selected" && typeof selection.identity_ambiguous === "boolean"
      && ["selection_epoch", "camera_reference_epoch"].every(k => !(k in selection) || selection[k] === b[k])
      && size.length === 2 && size.every(v => finite(v) && v > 0) && transformValid(row.frame_transform_to_reference) && applied.includes(row.frame_index)
      && joints.every(j => points[j]);
    const hip = points.left_hip && points.right_hip ? midpoint(points.left_hip, points.right_hip) : null;
    return { row, b, time, valid, points, hip, size, modelValid };
  });
  // Inventory is the whole retained event, including the turns surrounding the dip.
  if (raw.some((r, i) => !int(r.frame_index) || !finite(r.timestamp_ms) || i > 0 && (Number(r.frame_index) !== Number(raw[i - 1].frame_index) + 1 || Number(r.timestamp_ms) <= Number(raw[i - 1].timestamp_ms)))) return invalid("local_dip_parent_inventory_incomplete");
  function windowValid(a: number, b: number) {
    const part = parsed.slice(a, b + 1), first = part[0];
    if (part.length < 3 || !part.every(r => r.valid) || first.time === null || part.at(-1)!.time === null || part.at(-1)!.time! - first.time < 80) return false;
    const origins = part.map(r => r.time! - Number(r.row.timestamp_ms));
    return Math.max(...origins) - Math.min(...origins) <= 1.02 + 1e-9 && part.every((r, i) => same(r.size, first.size)
      && ["video_sha256", "primary_player_id", "person_track_id", "selection_epoch", "camera_reference_epoch", "time_base_num", "time_base_den"].every(k => r.b[k] === first.b[k])
      && (!i || Number(r.b.processed_index) === Number(part[i - 1].b.processed_index) + 1 && r.time! > part[i - 1].time! && r.time! - part[i - 1].time! <= 160
        && Number(r.row.timestamp_ms) - Number(part[i - 1].row.timestamp_ms) <= 160));
  }
  const indexes = parsed.map((r, i) => Number(r.row.timestamp_ms) <= searchEnd ? i : -1).filter(i => i >= 0);
  const y = indexes.map(i => parsed[i].hip?.[1] ?? NaN), changes = y.slice(1).map((v, i) => v - y[i]);
  const candidates: {start_ms: number; end_ms: number; confirmation_end_ms: number; source_frames: number[]; left: number; peak: number; right: number}[] = [];
  for (let peak = 2; peak < y.length - 2; peak++) {
    if (!finite(changes[peak - 1]) || !finite(changes[peak]) || changes[peak - 1] < 0 || changes[peak] >= 0) continue;
    let left = peak, right = peak;
    while (left > 0 && finite(changes[left - 1]) && changes[left - 1] >= 0) left--;
    while (right < changes.length && finite(changes[right]) && changes[right] <= 0) right++;
    if (!left || !finite(y[left - 1]) || changes.slice(left, peak).filter(v => v > 0).length < 2 || changes.slice(peak, right).filter(v => v < 0).length < 2) continue;
    const start = Number(raw[left].timestamp_ms), end = Number(raw[peak].timestamp_ms), confirmation = Number(raw[right].timestamp_ms);
    if (end - start < 80 || !windowValid(left, right)) continue;
    candidates.push({start_ms: start, end_ms: end, confirmation_end_ms: confirmation, source_frames: raw.slice(left, right + 1).map(r => Number(r.frame_index)), left, peak, right});
  }
  const chosen = candidates[0];
  if (!chosen || !["start_ms", "end_ms", "confirmation_end_ms", "source_frames"].every(k => same(selected[k], chosen[k as keyof typeof chosen]))
      || !windowValid(chosen.left, chosen.peak)) return invalid("local_dip_earliest_complete_turn_not_proven");
  const part = parsed.slice(chosen.left, chosen.peak + 1), hips = part.map(r => r.hip!), shoulders = part.map(r => midpoint(r.points.left_shoulder!, r.points.right_shoulder!));
  const torso = hips.map((p, i) => distance(p, shoulders[i])), scale = median(torso), times = part.map(r => r.time! - part[0].time!), dt = times.slice(1).map((t, i) => t - times[i]);
  if (torso.some(t => t <= 1e-6 || t * Math.max(...part[0].size.map(Number)) < 12)) return invalid("local_dip_torso_scale_invalid");
  const knees = Object.fromEntries(["left", "right"].map(side => [side, part.map(r => flexion(r.points[side + "_hip"]!, r.points[side + "_knee"]!, r.points[side + "_ankle"]!))]));
  const centres = hips.map((p, i) => midpoint(p, shoulders[i])), speed = centres.slice(1).map((p, i) => distance(p, centres[i]) * 1000 / dt[i] / scale);
  const simultaneous = dt.map((_, i) => hips[i + 1][1] > hips[i][1] && knees.left[i + 1] > knees.left[i] && knees.right[i + 1] > knees.right[i]);
  const values = {hip_drop_torso: (hips.at(-1)![1] - hips[0][1]) / scale,
    left_knee_flexion_change_deg: knees.left.at(-1)! - knees.left[0], right_knee_flexion_change_deg: knees.right.at(-1)! - knees.right[0],
    joint_descent_fraction: dt.reduce((sum, t, i) => sum + (simultaneous[i] ? t : 0), 0) / times.at(-1)!,
    speed_step_variation_ratio: Math.max(...speed.slice(1).map((v, i) => Math.abs(v - speed[i]))) / Math.max(speed.reduce((a, b) => a + b, 0) / speed.length, .05)};
  const frames = part.map(r => Number(r.row.frame_index)), series = obj(evidence.series);
  const numericSeries: Record<string, number[]> = {hip_y_reference: hips.map(p => p[1]), left_knee_flexion_deg: knees.left, right_knee_flexion_deg: knees.right, body_speed_torso_s: speed};
  if (context.quality.total_frame_count !== part.length || context.quality.valid_frame_count !== part.length || !same(evidence.source_frames, frames)
      || !same(evidence.timestamps_ms, part.map(r => r.row.timestamp_ms)) || evidence.time_basis !== "decoder_pts"
      || !array(evidence.source_timestamps_ms).every((t, i) => near(t, part[i]?.time)) || array(evidence.source_timestamps_ms).length !== part.length
      || !near(evidence.source_duration_ms, times.at(-1)) || !near(series.fixed_torso_scale, scale)
      || !same(series.simultaneous_descent_intervals, simultaneous)
      || Object.entries(numericSeries).some(([k, values]) => array(series[k]).length !== values.length || values.some((v, i) => !near(v, array(series[k])[i])))
      || context.conditions.some(c => !near(c.value, values[c.metric as keyof typeof values]))) return invalid("local_dip_values_or_denominator_mismatch");
  return {status: "measured_local_dip", reason_codes: [], values, source_frames: frames, confirmation_source_frames: chosen.source_frames, model_estimate_sample_count: part.filter(r => r.modelValid).length};
}
