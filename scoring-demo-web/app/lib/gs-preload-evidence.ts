/** Auxiliary measurements have no grade path. Raw input survives for diagnosis;
 * presentation and exports use this independently recomputed validation only. */
import contract from "./gs-preload-display-contract";
import { objectOf, type EvidenceObject } from "./manual-card-evidence";

const rows = (value: unknown): unknown[] => Array.isArray(value) ? value : [];
const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const integer = (value: unknown): value is number => Number.isSafeInteger(value) && Number(value) >= 0;
const nonempty = (value: unknown): value is string => typeof value === "string" && value.trim().length > 0;
const sha = (value: unknown) => typeof value === "string" && /^[a-f0-9]{64}$/i.test(value);
const near = (a: unknown, b: unknown) => finite(a) && finite(b) && Math.abs(a - b) <= 1e-8 * Math.max(1, Math.abs(a), Math.abs(b));
const clockNear = (a: unknown, b: unknown) => finite(a) && finite(b) && Math.abs(a - b) <= .51;
const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);
const exactKeys = (value: EvidenceObject, expected: string[]) => same(Object.keys(value).sort(), [...expected].sort());
const median = (values: number[]) => { const sorted = [...values].sort((a, b) => a - b), n = sorted.length; return (sorted[Math.floor((n - 1) / 2)] + sorted[Math.floor(n / 2)]) / 2; };
type Context = { indicator_id: string; event_id: string; video_id: string; person_track_id: number; status: string;
  window: { start_ms: number; end_ms: number } | null;
  source_stage: { stage_code: string; annotation_id: string; document_sha256: string; digest_kind: string } | null;
  source: { document_id: string; document_sha256: string; table: string; rubric_sha256: string } };
export interface PreloadValidation {
  status: "measured_auxiliary" | "unavailable" | "invalid";
  reason_codes: string[]; total_frame_count: number | null; valid_frame_count: number | null;
  observations: { metric: string; unit: string; value: number | null; engineering_reference: number }[];
  joint_descent_fraction: number | null;
}
function empty(status: PreloadValidation["status"], reasons: string[]): PreloadValidation {
  return { status, reason_codes: [...new Set(reasons)], total_frame_count: null, valid_frame_count: null,
    observations: [], joint_descent_fraction: null };
}
function exactTime(row: EvidenceObject): number | null {
  return integer(row.frame_index) && integer(row.pts) && integer(row.time_base_num) && row.time_base_num > 0
    && integer(row.time_base_den) && row.time_base_den > 0 && Number.isSafeInteger(row.pts * row.time_base_num)
    ? row.pts * row.time_base_num * 1000 / row.time_base_den : null;
}

export function validateGsPreload(value: EvidenceObject | null, context: Context, parent: EvidenceObject | null): PreloadValidation | null {
  if (!value) return null;
  const a = value, specs = contract.cards as Record<string, { stage_code: string; rule_kind: string; conditions: unknown[]; source: Context["source"] }>;
  const spec = specs[context.indicator_id], source = objectOf(a.source_annotation), observation = objectOf(source.observation);
  const w = objectOf(a.window), stage = objectOf(source.stage_window_ms), parentWindow = objectOf(parent?.window);
  const invalid = (reason: string) => empty("invalid", [reason]);
  if (!spec || spec.rule_kind !== "manual_review" || spec.conditions.length || !spec.stage_code.endsWith("-M01")
      || a.version !== contract.version || a.measurement_version !== contract.measurement_version || a.semantics !== contract.semantics
      || !["measured_auxiliary", "unavailable"].includes(String(a.status))
      || a.original_grade !== null || a.proxy_grade !== null || a.score !== null || a.source_grade_supported !== false
      || a.split_step_confirmed !== false || a.interpolation_used !== false
      || ["passed", "scoring_eligible", "grade"].some(key => key in a)
      || Object.entries(spec.source).some(([key, v]) => context.source[key as keyof Context["source"]] !== v)) return invalid("preload_contract_or_grade_claim_invalid");
  if (!context.source_stage || !context.window || !parent || !nonempty(parent.action_id)
      || context.source_stage.stage_code !== spec.stage_code || context.source_stage.annotation_id !== context.event_id
      || source.document_version !== "gs-stage-annotations-v1.0.0" || source.digest_kind !== "canonical_json_sorted_utf8_v1"
      || !sha(source.document_sha256) || source.document_sha256 !== context.source_stage.document_sha256
      || source.stage_code !== spec.stage_code || source.expert_adjudicated !== false || source.file_verification !== "parent_report_input_flags"
      || observation.version !== contract.annotation_version || observation.method !== "manual_anchor" || observation.semantics !== contract.semantics
      || !exactKeys(observation, ["version", "method", "annotation_id", "action_id", "indicator_id", "video_sha256", "person_track_id", "start_frame", "end_frame", "source", "source_description", "uncertainty_ms", "semantics"])
      || observation.indicator_id !== context.indicator_id || observation.annotation_id !== context.event_id || observation.action_id !== parent.action_id
      || observation.person_track_id !== context.person_track_id || !sha(observation.video_sha256)
      || !nonempty(observation.source) || [...observation.source].length > 512
      || !nonempty(observation.source_description) || [...observation.source_description].length > 512
      || !finite(observation.uncertainty_ms) || observation.uncertainty_ms < 0
      || w.video_id !== context.video_id || w.event_id !== context.event_id || w.event_code !== context.indicator_id.split("-")[0]
      || w.person_track_id !== context.person_track_id || w.stage_code !== spec.stage_code || w.video_sha256 !== observation.video_sha256
      || parentWindow.video_id !== context.video_id || parentWindow.event_id !== context.event_id || parentWindow.person_track_id !== context.person_track_id
      || parentWindow.stage_code !== spec.stage_code || parentWindow.video_sha256 !== observation.video_sha256
      || !clockNear(parentWindow.start_ms, context.window.start_ms) || !clockNear(parentWindow.end_ms, context.window.end_ms)) return invalid("preload_source_binding_invalid");
  const requested = parent.requested_window_ms === undefined ? null : objectOf(parent.requested_window_ms);
  const stageMatchesParent = requested === null
    ? clockNear(stage.start_ms, parentWindow.start_ms) && clockNear(stage.end_ms, parentWindow.end_ms)
    : finite(requested.start_ms) && finite(requested.end_ms) && requested.start_ms >= 0 && requested.start_ms < requested.end_ms
      && stage.start_ms === requested.start_ms && stage.end_ms === requested.end_ms
      && Number(parentWindow.start_ms) >= requested.start_ms - .51 && Number(parentWindow.end_ms) <= requested.end_ms + .51;
  const first = objectOf(observation.start_frame), last = objectOf(observation.end_frame), start = exactTime(first), end = exactTime(last);
  if (start === null || end === null || start >= end || Number(first.frame_index) >= Number(last.frame_index)
      || ![first, last].every(row => exactKeys(row, ["frame_index", "pts", "time_base_num", "time_base_den"]))
      || !stageMatchesParent || !finite(stage.start_ms) || !finite(stage.end_ms) || stage.start_ms < 0 || stage.start_ms > start || stage.end_ms < end
      || !finite(w.start_ms) || !finite(w.end_ms) || w.start_ms < 0 || w.end_ms <= w.start_ms
      || start < context.window.start_ms - .51 || end > context.window.end_ms + .51
      || !clockNear(w.start_ms, start) || !clockNear(w.end_ms, end)) return invalid("preload_stage_containment_invalid");
  const bindings = rows(a.source_frame_bindings).map(objectOf), series = objectOf(a.series), q = objectOf(a.quality);
  const frames = rows(series.source_frames), times = rows(series.source_timestamps_ms), n = Number(last.frame_index) - Number(first.frame_index) + 1;
  if (n > 20000 || bindings.length !== n || frames.length !== n || times.length !== n
      || q.total_frame_count !== n || q.denominator !== "all_expected_decoder_frames_in_closed_local_window"
      || !same(q.required_joint_names, contract.required_joint_names) || q.visibility_verified !== false || q.image_bounds_verified !== false
      || series.coordinate_basis !== "camera_reference_pixels") return invalid("preload_original_denominator_invalid");
  const actualTimes: number[] = [], recordTimes: number[] = [];
  let sourceComplete = integer(w.selection_epoch) && integer(w.camera_reference_epoch) && w.camera_compensated === true
    && parentWindow.camera_compensated === true
    && w.selection_epoch === parentWindow.selection_epoch && w.camera_reference_epoch === parentWindow.camera_reference_epoch;
  for (let i = 0; i < n; i++) {
    const b = bindings[i], time = exactTime(b);
    if (frames[i] !== Number(first.frame_index) + i || b.frame_index !== frames[i] || b.video_sha256 !== observation.video_sha256
        || time === null || !near(times[i], time) || !clockNear(b.timestamp_ms, time)
        || b.time_base_num !== first.time_base_num || b.time_base_den !== first.time_base_den
        || i > 0 && (time <= actualTimes[i - 1] || Number(b.timestamp_ms) <= recordTimes[i - 1])) return invalid("preload_source_pts_invalid");
    if ((i === 0 || i === n - 1) && ["frame_index", "pts", "time_base_num", "time_base_den"].some(key => b[key] !== (i === 0 ? first : last)[key])) return invalid("preload_source_pts_invalid");
    sourceComplete = sourceComplete && b.selected_person_track_id === context.person_track_id && b.selection_epoch === w.selection_epoch && b.camera_reference_epoch === w.camera_reference_epoch;
    actualTimes.push(time); recordTimes.push(Number(b.timestamp_ms));
  }
  if (!clockNear(recordTimes[0], w.start_ms) || !clockNear(recordTimes.at(-1), w.end_ms)) return invalid("preload_source_pts_invalid");
  const jointFrames = objectOf(q.joint_valid_frames), masks: Set<number>[] = [];
  for (const joint of contract.required_joint_names) {
    const jointRows = rows(jointFrames[joint]);
    if (!Array.isArray(jointFrames[joint]) || jointRows.some((frame, i) => !integer(frame) || !frames.includes(frame) || i > 0 && Number(jointRows[i - 1]) >= frame)) return invalid("preload_joint_union_invalid");
    masks.push(new Set(jointRows as number[]));
  }
  const jointValid = (frames as number[]).map(frame => masks.every(mask => mask.has(frame))), valid = jointValid.filter(Boolean).length;
  if (q.valid_frame_count !== valid || !near(q.fraction, valid / n) || q.geometry_gate_passed !== (n >= 3 && valid * 10 >= n * 7)
      || q.continuous_joint_geometry_complete !== (valid === n)) return invalid("preload_joint_union_invalid");
  const hip = rows(series.hip_y_camera_reference), left = rows(series.left_knee_flexion_deg), right = rows(series.right_knee_flexion_deg), scales = rows(series.torso_scale_by_frame);
  const numericSeries = [hip, left, right, scales];
  const flags = rows(series.simultaneous_descent_intervals), pairs = rows(series.pair_source_frames);
  if (numericSeries.some(values => values.length !== n || values.some(v => v !== null && !finite(v)))
      || left.concat(right).some(v => finite(v) && (v < 0 || v > 180)) || scales.some(v => finite(v) && v < 0)
      || flags.length !== n - 1 || pairs.length !== n - 1 || series.endpoint_frame_count !== (n >= 7 ? Math.max(3, Math.ceil(n * .2)) : null)) return invalid("preload_series_invalid");
  let simultaneousTime = 0;
  for (let i = 0; i < n - 1; i++) {
    const paired = jointValid[i] && jointValid[i + 1] && [left[i], left[i + 1], right[i], right[i + 1]].every(finite);
    const expected = paired ? Number(hip[i + 1]) > Number(hip[i]) && Number(left[i + 1]) > Number(left[i]) && Number(right[i + 1]) > Number(right[i]) : null;
    if (!same(pairs[i], [frames[i], frames[i + 1]]) || flags[i] !== expected) return invalid("preload_joint_timing_invalid");
    if (expected) simultaneousTime += actualTimes[i + 1] - actualTimes[i];
  }
  if (a.joint_descent_semantics !== "duration_weighted_adjacent_intervals_with_hip_y_and_both_knee_flexions_increasing") return invalid("preload_joint_timing_invalid");
  const observations = rows(a.auxiliary_observations).map(objectOf);
  if (observations.length !== contract.references.length || observations.some((row, i) => {
    const [metric, unit, threshold] = contract.references[i], ref = objectOf(row.engineering_reference);
    return row.metric !== metric || row.unit !== unit || ref.operator !== "gte" || ref.value !== threshold
      || row.passed !== null || row.scoring_eligible !== false || row.value !== null && !finite(row.value);
  })) return invalid("preload_observation_contract_invalid");
  const reasons = rows(a.reason_codes);
  if (!Array.isArray(a.reason_codes) || reasons.some(v => !nonempty(v))) return invalid("preload_series_invalid");
  const gaps = actualTimes.slice(1).map((time, i) => time - actualTimes[i]), duration = end - start;
  if (a.status === "measured_auxiliary") {
    if (context.status !== "review_required" || reasons.length || !sourceComplete || n < 7 || valid !== n || duration < 80
        || gaps.some(gap => gap > Math.min(160, median(gaps) * 1.8))
        || numericSeries.some(values => !values.every(finite)) || scales.some(v => Number(v) <= 0)) return invalid("preload_continuous_measurement_invalid");
    const count = Math.max(3, Math.ceil(n * .2)), scale = median(scales as number[]);
    const delta = (values: unknown[]) => median(values.slice(-count) as number[]) - median(values.slice(0, count) as number[]);
    const calculated = [delta(hip) / scale, delta(left), delta(right)], fraction = simultaneousTime / duration;
    if (!near(series.fixed_torso_scale, scale) || observations.some((row, i) => !near(row.value, calculated[i])) || !near(a.joint_descent_fraction, fraction)) return invalid("preload_arithmetic_mismatch");
    return { status: "measured_auxiliary", reason_codes: [], total_frame_count: n, valid_frame_count: valid, joint_descent_fraction: fraction,
      observations: contract.references.map(([metric, unit, threshold], i) => ({ metric: String(metric), unit: String(unit), value: calculated[i], engineering_reference: Number(threshold) })) };
  }
  if (!reasons.length || observations.some(row => row.value !== null) || a.joint_descent_fraction !== null) return invalid("preload_unavailable_must_not_expose_measurements");
  return { ...empty("unavailable", reasons as string[]), total_frame_count: n, valid_frame_count: valid,
    observations: contract.references.map(([metric, unit, threshold]) => ({ metric: String(metric), unit: String(unit), value: null, engineering_reference: Number(threshold) })) };
}

export function preloadSummary(validation: PreloadValidation | null): string {
  if (!validation) return "";
  if (validation.status === "invalid") return "局部下降辅助输入未通过校验；不显示为有效测量，不评分";
  const count = `${validation.valid_frame_count}/${validation.total_frame_count} 帧八关节共同有效`;
  if (validation.status === "unavailable") return `局部下降辅助观测缺测；${count}；不评分`;
  const names: Record<string, string> = { hip_downward_displacement_torso: "局部髋中点下降", "left_knee_flexion_deg.delta": "左膝投影屈曲变化", "right_knee_flexion_deg.delta": "右膝投影屈曲变化" };
  const measurements = validation.observations.map(row => `${names[row.metric] ?? row.metric}=${row.value?.toFixed(4)} ${row.unit === "deg" ? "度" : "躯干单位"}（工程参考≥${row.engineering_reference}，不判通过）`).join("；");
  return `局部下降辅助观测；${count}；${measurements}；共同下降时间比=${(Number(validation.joint_descent_fraction) * 100).toFixed(2)}%；不是垫步确认，不评分`;
}

export function preloadSourceSummary(raw: EvidenceObject | null, validation: PreloadValidation | null): string {
  if (!raw || !validation) return "";
  if (validation.status === "invalid") return "；辅助输入来源未通过校验";
  const observation = objectOf(objectOf(raw.source_annotation).observation);
  return `；人工局部下降窗口 ±${observation.uncertainty_ms} 毫秒；${observation.source}；${observation.version}；不是专家垫步真值`;
}
