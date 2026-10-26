/** Checks the emitted cycle evidence; never replaces original-video decoding. */
import contract from "./footwork-cycle-display-contract";
import { isAdvisoryQualityFlag, isFormalScoringPolicy } from "./formal-card-scoring-policy";

type Obj = Record<string, unknown>;
const object = (value: unknown): Obj => value !== null && typeof value === "object" && !Array.isArray(value) ? value as Obj : {};
const array = (value: unknown): unknown[] => Array.isArray(value) ? value : [];
const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const integer = (value: unknown): value is number => Number.isSafeInteger(value) && Number(value) >= 0;
const close = (a: unknown, b: unknown) => finite(a) && finite(b) && Math.abs(a - b) <= 1e-6 + 1e-12 * Math.max(Math.abs(a), Math.abs(b));
const numbers = (value: unknown, count: number) => Array.isArray(value) && value.length === count && value.every(finite);
const median = (items: unknown[]) => { const sorted = items.map(Number).sort((a, b) => a - b), mid = Math.floor(sorted.length / 2); return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2; };

function resolvedOrderExchanges(series: Obj, times: unknown[], frames: unknown[]): boolean {
  const signs = array(series.ankle_order_sign), stable: Array<{ start: number; end: number; sign: unknown }> = [];
  for (let start = 0; start < signs.length;) {
    let end = start;
    while (end + 1 < signs.length && signs[end + 1] === signs[start]) end++;
    if (signs[start] !== 0) {
      // A brief opposite order must not disappear from the exchange count.
      if (Number(times[end]) - Number(times[start]) < contract.minimum_motion_interval_ms) return false;
      stable.push({ start, end, sign: signs[start] });
    }
    start = end + 1;
  }
  if (!stable.length || stable[0].start !== 0 || stable.at(-1)?.end !== signs.length - 1) return false;
  const expected: Obj[] = [], explained = new Set<number>();
  for (let i = 1; i < stable.length; i++) {
    const before = stable[i - 1], after = stable[i];
    if (before.sign === after.sign) continue;
    const middle: unknown[] = [];
    for (let j = before.end + 1; j < after.start; j++) if (signs[j] === 0) { middle.push(frames[j]); explained.add(j); }
    if (!middle.length) return false;
    expected.push({ before_source_frame: frames[before.end], after_source_frame: frames[after.start],
      before_order: before.sign, after_order: after.sign, near_zero_source_frames: middle });
  }
  // Zero is unresolved order, unless opposite stable runs explain its passage.
  if (signs.some((value, i) => value === 0 && !explained.has(i))) return false;
  const observed = array(series.ankle_order_exchanges).map(object);
  return observed.length === expected.length && expected.every((exchange, i) => Object.entries(exchange)
    .every(([key, value]) => JSON.stringify(value) === JSON.stringify(observed[i][key])));
}

export interface FootworkCycleEvidence extends Obj {
  step_window_binding: Obj; source_frames: unknown[]; source_frame_bindings: unknown[];
  timestamps_ms: unknown[]; source_timestamps_ms: unknown[]; series: Obj; event_quality: Obj;
}

export function footworkCycleOf(value: unknown): FootworkCycleEvidence | null {
  const row = object(value);
  if (typeof row.cycle_policy_version !== "string" || !Object.keys(object(row.step_window_binding)).length) return null;
  // Bound nested evidence before retaining it in portable backups. Invalid
  // numbers become null and can never satisfy the proxy validator below.
  let budget = 150000;
  function copy(item: unknown, depth = 0): unknown {
    if (--budget < 0 || depth > 12) throw new Error("cycle_evidence_limit");
    if (item === null || typeof item === "boolean") return item;
    if (typeof item === "number") return finite(item) ? item : null;
    if (typeof item === "string") return item.slice(0, 1000);
    if (Array.isArray(item)) {
      if (item.length > 10000) throw new Error("cycle_evidence_limit");
      return item.map(child => copy(child, depth + 1));
    }
    return Object.fromEntries(Object.entries(object(item)).filter(([key]) => !["__proto__", "constructor", "prototype"].includes(key)).map(([key, child]) => [key, copy(child, depth + 1)]));
  }
  try {
    const safe = object(copy(row));
    return { ...safe, step_window_binding: object(safe.step_window_binding), source_frames: array(safe.source_frames),
      source_frame_bindings: array(safe.source_frame_bindings), timestamps_ms: array(safe.timestamps_ms),
      source_timestamps_ms: array(safe.source_timestamps_ms), series: object(safe.series), event_quality: object(safe.event_quality) };
  } catch { return null; }
}

export function cycleFrameTime(cycle: FootworkCycleEvidence, frame: unknown): number | null {
  const row = cycle.source_frame_bindings.map(object).find(row => row.frame_index === frame);
  return row && row.status === "verified_decoder_binding" && finite(row.video_timestamp_ms) ? row.video_timestamp_ms : null;
}

export function cycleMotionSteps(cycle: FootworkCycleEvidence): Array<Obj & { side: string }> {
  const candidates = object(cycle.series.motion_step_candidates);
  return ["left", "right"].flatMap(side => array(candidates[side]).map(value => ({ side, ...object(value) })));
}

export function validFootworkCycle(cycle: FootworkCycleEvidence | null, context: {
  indicator_id: string; event_id: string; video_id: string; person_track_id: number;
  window: { start_ms: number; end_ms: number } | null; total_frames: number; valid_frames: number;
  scoring_policy_version?: string | null;
}): boolean {
  if (!cycle || !context.window) return false;
  const binding = cycle.step_window_binding, series = cycle.series, frames = cycle.source_frames;
  const times = cycle.source_timestamps_ms, videoTimes = cycle.timestamps_ms, n = frames.length;
  const formal = isFormalScoringPolicy(context.scoring_policy_version);
  if (n < 7 || n !== context.total_frames || context.valid_frames !== n || !frames.every(integer)
      || frames.some((frame, i) => i > 0 && Number(frame) !== Number(frames[i - 1]) + 1)
      || !numbers(times, n) || !numbers(videoTimes, n) || cycle.source_frame_bindings.length !== n
      || videoTimes[0] !== context.window.start_ms || videoTimes[n - 1] !== context.window.end_ms
      || cycle.cycle_policy_version !== contract.cycle_policy_version || cycle.time_basis !== "decoder_pts"
      || cycle.motion_times_basis !== "source_decoder_pts_absolute_ms" || cycle.interpolation_used !== false
      || cycle.observations_are_ground_contact !== false || cycle.reference !== "one_camera_epoch_fixed_median_torso"
      || cycle.anchor_semantics !== "manual_visual_candidate_window_not_expert_technique_or_contact_truth"
      || binding.method !== "manual_anchor" || typeof binding.version !== "string" || !binding.version
      || typeof binding.evidence_reference !== "string" || !binding.evidence_reference
      || binding.event_id !== context.event_id || binding.video_id !== context.video_id || binding.primary_player_id !== context.person_track_id
      || binding.start_source_frame !== frames[0] || binding.end_source_frame !== frames[n - 1]
      || typeof binding.video_sha256 !== "string" || !/^[a-f0-9]{64}$/i.test(binding.video_sha256)
      || cycle.event_quality.policy_version !== contract.cycle_policy_version || !Array.isArray(cycle.event_quality.review_flags)
      || array(cycle.event_quality.review_flags).some(flag => !formal || !isAdvisoryQualityFlag(flag))) return false;
  const source = cycle.source_frame_bindings.map(object), first = source[0];
  if (formal) {
    const quality = cycle.event_quality, diagnostics = object(quality.event_identity_diagnostics), flags = array(quality.raw_input_quality_flags);
    const confirmed = diagnostics.confirmed_id_switch_count;
    if (quality.quality_policy_version !== contract.advisory_quality_policy_version
        || !Array.isArray(quality.raw_input_quality_flags) || flags.some(flag => typeof flag !== "string")
        || !Array.isArray(quality.blocking_flags) || quality.blocking_flags.length || !Array.isArray(quality.advisory_flags)
        || quality.advisory_flags.some(flag => !isAdvisoryQualityFlag(flag))
        || JSON.stringify(diagnostics.source_track_ids) !== JSON.stringify([first.person_track_id])
        || typeof diagnostics.primary_identity_ambiguous !== "boolean" || !integer(diagnostics.source_track_switch_candidate_count)
        || confirmed != null && (!integer(confirmed) || confirmed !== 0)
        || flags.some(flag => !isAdvisoryQualityFlag(flag) && ((contract.quality_review_flags as readonly unknown[]).includes(flag)
          || /confirmed_id_switch|id_switch_confirmed|censored/.test(String(flag)) || String(flag).startsWith("required_phase_missing:")))) return false;
  }
  let offsetLower = -Infinity, offsetUpper = Infinity;
  for (let i = 0; i < n; i++) {
    const row = source[i];
    if (row.status !== "verified_decoder_binding" || row.frame_index !== frames[i] || row.timestamp_source !== "decoder_pts"
        || row.video_sha256 !== binding.video_sha256 || row.primary_player_id !== context.person_track_id
        || ![row.processed_index, row.person_track_id, row.selection_epoch, row.camera_reference_epoch].every(integer)
        || !Number.isSafeInteger(row.pts) || !integer(row.time_base_num) || !integer(row.time_base_den) || !row.time_base_num || !row.time_base_den
        || ["person_track_id", "selection_epoch", "camera_reference_epoch", "time_base_num", "time_base_den"].some(key => row[key] !== first[key])
        || row.video_timestamp_ms !== videoTimes[i]) return false;
    const exact = Number(row.pts) * Number(row.time_base_num) * 1000 / Number(row.time_base_den);
    if (!close(exact, times[i]) || !finite(row.source_timestamp_ms) || Math.abs(exact - row.source_timestamp_ms) > .51
        || i > 0 && (Number(times[i]) <= Number(times[i - 1]) || Number(times[i]) - Number(times[i - 1]) > 160
          || Number(videoTimes[i]) <= Number(videoTimes[i - 1]))) return false;
    const offset = exact - Number(videoTimes[i]); offsetLower = Math.max(offsetLower, offset - .51); offsetUpper = Math.min(offsetUpper, offset + .51);
  }
  if (offsetLower > offsetUpper || !finite(series.fixed_torso_scale) || series.fixed_torso_scale <= 0
      || series.endpoint_fraction !== .2 || series.endpoint_frame_count !== Math.max(3, Math.ceil(n * .2))
      || series.speed_endpoint_pair_count !== series.endpoint_frame_count
      || series.semantics !== "continuous_image_plane_motion_only_not_ground_contact_support_force_or_tennis_step_ground_truth"
      || !numbers(series.ankle_width_torso, n) || !numbers(series.ankle_signed_horizontal_gap_torso, n)
      || !numbers(series.ankle_order_sign, n) || !numbers(series.hip_speed_torso_s, n - 1)
      || !["hip_xy_reference", "left_ankle_xy_reference", "right_ankle_xy_reference"].every(key => array(series[key]).length === n && array(series[key]).every(value => numbers(value, 2)))) return false;
  const pairs = array(series.pair_source_frames);
  if (pairs.length !== n - 1 || pairs.some((pair, i) => JSON.stringify(pair) !== JSON.stringify([frames[i], frames[i + 1]]))) return false;
  const speeds = object(series.ankle_speed_torso_s), candidates = object(series.motion_step_candidates);
  if (!["left", "right"].every(side => numbers(speeds[side], n - 1) && Array.isArray(candidates[side]))) return false;
  const sign = (value: number) => value > contract.order_deadband_torso ? 1 : value < -contract.order_deadband_torso ? -1 : 0;
  if (array(series.ankle_order_sign).some((value, i) => value !== sign(Number(array(series.ankle_signed_horizontal_gap_torso)[i])))) return false;
  const indexOfFrame = (value: unknown) => integer(value) ? frames.indexOf(value) : -1;
  const complete: Obj[] = [];
  for (const side of ["left", "right"]) for (const value of array(candidates[side])) {
    const step = object(value), start = indexOfFrame(step.start_source_frame), end = indexOfFrame(step.end_source_frame);
    const before = array(step.preceding_source_frames), after = array(step.following_source_frames);
    const a = indexOfFrame(before[0]), b = indexOfFrame(after.at(-1));
    if (start < 0 || end <= start || a < 0 || b < end || !before.length || !after.length
        || JSON.stringify(before) !== JSON.stringify(frames.slice(a, start + 1)) || JSON.stringify(after) !== JSON.stringify(frames.slice(end, b + 1))
        || !close(step.start_ms, times[start]) || !close(step.end_ms, times[end])
        || !close(step.motion_duration_ms, Number(times[end]) - Number(times[start]))
        || !close(step.preceding_low_motion_duration_ms, Number(times[start]) - Number(times[a]))
        || !close(step.following_low_motion_duration_ms, Number(times[b]) - Number(times[end]))
        || !numbers(step.delta_xy_torso, 2) || !close(step.displacement_torso, Math.hypot(...array(step.delta_xy_torso).map(Number)))
        || array(speeds[side]).slice(start, end).some(value => Number(value) <= contract.low_ankle_speed_torso_s)
        || [...array(speeds[side]).slice(a, start), ...array(speeds[side]).slice(end, b)].some(value => Number(value) > contract.low_ankle_speed_torso_s)) return false;
    const done = [step.motion_duration_ms, step.preceding_low_motion_duration_ms, step.following_low_motion_duration_ms].every(value => Number(value) >= contract.minimum_motion_interval_ms)
      && Math.abs(Number(array(step.delta_xy_torso)[0])) >= contract.order_deadband_torso;
    if (step.complete_visual_motion_cycle !== done) return false;
    if (done) complete.push({ side, ...step });
  }
  if (!complete.length || series.complete_visual_motion_cycle_count !== complete.length) return false;
  const followPairs = array(series.sequential_follow_pairs);
  if (!Array.isArray(series.sequential_follow_pairs) || !Array.isArray(series.ankle_order_exchanges) || !Array.isArray(series.unresolved_order_exchanges)) return false;
  const hips = array(series.hip_xy_reference).map(array), endpointCount = Number(series.endpoint_frame_count);
  const direction = Math.sign(median(hips.slice(-endpointCount).map(point => point[0])) - median(hips.slice(0, endpointCount).map(point => point[0])));
  for (const value of followPairs) {
    const pair = object(value), lead = complete.find(step => step.side === pair.lead_side && step.start_source_frame === pair.lead_start_source_frame);
    const follow = complete.find(step => step.side === pair.follow_side && step.start_source_frame === pair.follow_start_source_frame);
    const peak = indexOfFrame(pair.width_peak_source_frame);
    if (!lead || !follow || lead.side === follow.side || Number(lead.start_ms) >= Number(follow.start_ms)
        || Number(follow.start_ms) > Number(lead.end_ms) + contract.minimum_motion_interval_ms
        || Number(array(lead.delta_xy_torso)[0]) * direction <= 0 || Number(array(follow.delta_xy_torso)[0]) * direction <= 0
        || (Number(array(array(series[`${String(lead.side)}_ankle_xy_reference`])[indexOfFrame(lead.start_source_frame)])[0])
          - Number(array(array(series[`${String(follow.side)}_ankle_xy_reference`])[indexOfFrame(lead.start_source_frame)])[0])) * direction <= 0
        || !close(pair.onset_delay_ms, Number(follow.start_ms) - Number(lead.start_ms))
        || peak <= indexOfFrame(lead.start_source_frame) || peak >= indexOfFrame(follow.end_source_frame)
        || Number(array(series.ankle_width_torso)[peak]) - Number(array(series.ankle_width_torso)[indexOfFrame(lead.start_source_frame)]) < contract.order_deadband_torso
        || Number(array(series.ankle_width_torso)[peak]) - Number(array(series.ankle_width_torso)[indexOfFrame(follow.end_source_frame)]) < contract.order_deadband_torso) return false;
  }
  if (context.indicator_id === "FS04-M04" && !followPairs.length) return false;
  for (const value of array(series.ankle_order_exchanges)) {
    const exchange = object(value), before = indexOfFrame(exchange.before_source_frame), after = indexOfFrame(exchange.after_source_frame);
    const middle = array(exchange.near_zero_source_frames), linked = array(exchange.complete_motion_source_windows);
    if (before < 0 || after <= before || !middle.length || !linked.length || exchange.before_order === exchange.after_order
        || array(series.ankle_order_sign)[before] !== exchange.before_order || array(series.ankle_order_sign)[after] !== exchange.after_order
        || middle.some(frame => indexOfFrame(frame) <= before || indexOfFrame(frame) >= after || array(series.ankle_order_sign)[indexOfFrame(frame)] !== 0)
        || linked.some(window => !complete.some(step => JSON.stringify(window) === JSON.stringify([step.start_source_frame, step.end_source_frame]))
          || middle.some(frame => Number(frame) < Number(array(window)[0]) || Number(frame) > Number(array(window)[1])))) return false;
  }
  return !["FS03-M03", "FS04-M03", "FS04-M04"].includes(context.indicator_id)
    || array(series.unresolved_order_exchanges).length === 0 && resolvedOrderExchanges(series, times, frames);
}

export function footworkValuesAgree(cycle: FootworkCycleEvidence, values: Record<string, number | null>): boolean {
  const series = cycle.series, count = Number(series.endpoint_frame_count), scale = Number(series.fixed_torso_scale);
  const endpointDistance = (value: unknown) => {
    const points = array(value), first = points.slice(0, count).map(array), last = points.slice(-count).map(array);
    return Math.hypot(...[0, 1].map(axis => median(last.map(point => point[axis])) - median(first.map(point => point[axis])))) / scale;
  };
  const hips = array(series.hip_xy_reference).map(array), left = array(series.left_ankle_xy_reference).map(array), right = array(series.right_ankle_xy_reference).map(array);
  const width = array(series.ankle_width_torso), speed = array(series.hip_speed_torso_s);
  const known: Record<string, number> = {
    hip_displacement_torso: endpointDistance(hips),
    ankle_max_displacement_torso: Math.max(endpointDistance(left), endpointDistance(right)),
    hip_speed_drop_torso_s: median(speed.slice(0, count)) - median(speed.slice(-count)),
    "hip_inside_ankle_support_fraction.mean": hips.filter((hip, i) => Number(hip[0]) >= Math.min(Number(left[i][0]), Number(right[i][0])) && Number(hip[0]) <= Math.max(Number(left[i][0]), Number(right[i][0]))).length / hips.length,
    ankle_order_exchange_count: array(series.ankle_order_exchanges).length,
    ankle_width_contraction_torso: Math.max(...width.slice(1, -1).map(Number)) - median(width.slice(-count)),
  };
  return Object.entries(values).every(([metric, value]) => !(metric in known) || close(value, known[metric]));
}
