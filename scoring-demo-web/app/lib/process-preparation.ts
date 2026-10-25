import type { ProcessCriterion } from "./api-types";

const obj = (value: unknown): Record<string, unknown> => value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const integer = (value: unknown): value is number => Number.isSafeInteger(value) && Number(value) >= 0;
const text = (value: unknown): value is string => typeof value === "string" && value.trim().length > 0;
const motionSource = "action_recognition.motion_analysis.episodes.process_motion";
const rotationSource = "action_recognition.motion_analysis.episodes.rotation_analysis";
const jointUnits: Record<string, string> = {
  left_knee_flexion_deg: "deg", right_knee_flexion_deg: "deg", left_elbow_angle_deg: "deg", right_elbow_angle_deg: "deg",
  trunk_tilt_deg: "deg", ankle_distance_over_hip_width: "ratio", hip_inside_ankle_support_fraction: "fraction",
  hip_to_ankle_midpoint_torso: "torso", wrist_separation_torso: "torso", head_to_shoulders_distance_torso: "torso",
  shoulder_axis_deg: "deg", hip_axis_deg: "deg", shoulder_hip_separation_deg: "deg",
};
const candidateUnits: Record<string, string> = {
  net_displacement_torso: "torso", path_length_torso: "torso", mean_speed_torso_per_s: "torso/s",
  start_speed_torso_per_s: "torso/s", end_speed_torso_per_s: "torso/s",
  shoulder_line_change_deg: "deg", hip_line_change_deg: "deg", peak_shoulder_angular_speed_deg_s: "deg/s", peak_hip_angular_speed_deg_s: "deg/s",
};
const translationUnits: Record<string, string> = {
  hip_center_dx_torso: "torso", hip_center_dy_torso: "torso",
  hip_center_mean_velocity_x_torso_per_s: "torso/s", hip_center_mean_velocity_y_torso_per_s: "torso/s",
  left_knee_flexion_change_deg: "deg", right_knee_flexion_change_deg: "deg",
};

/** Observations preserve their source windows and never acquire a technical score. */
export function processPreparationCriterion(raw: Record<string, unknown>): ProcessCriterion | null {
  const feature = raw.feature_name, ref = obj(raw.reference);
  const translation = text(raw.id) && raw.id.startsWith("preparation_translation_");
  const candidate = translation || text(raw.id) && raw.id.startsWith("preparation_candidate_");
  const units = translation ? translationUnits : candidate ? candidateUnits : jointUnits;
  if (!text(feature) || !units[feature] || raw.id !== `${translation ? "preparation_translation" : candidate ? "preparation_candidate" : "preparation_joint"}_${feature}`
    || raw.unit !== units[feature] || raw.status !== "measured" || raw.score !== null || !finite(raw.value)
    || !text(raw.label_zh) || !text(raw.episode_id) || !text(raw.rule_zh) || !text(raw.reason_zh)
    || !integer(raw.start_ms) || !integer(raw.end_ms) || raw.end_ms <= raw.start_ms
    || !integer(raw.person_track_id) || ref.person_track_id !== raw.person_track_id || !integer(ref.selection_epoch)
    || ref.camera_reference_epoch !== null && !integer(ref.camera_reference_epoch)
    || typeof ref.camera_compensated !== "boolean" || !text(ref.normalization_basis)
    || !Array.isArray(raw.source_frames) || raw.source_frames.length < 3 || !raw.source_frames.every(integer)
    || !raw.source_frames.every((frame, i, all) => i === 0 || frame > all[i - 1])
    || raw.sample_count !== raw.source_frames.length || raw.assessment_status !== "measurement_only" || raw.full_source_rule_satisfied !== false) return null;
  if (candidate) {
    const rotation = feature.includes("line_change") || feature.includes("angular_speed");
    const phase = obj(raw.phase_window), evidence = obj(raw.original_metric_evidence);
    const source = translation ? `${motionSource}.preparation_translation.measurements.${feature}` : rotation ? `${rotationSource}.phase_measurements.metrics.${feature}` : `${motionSource}.phases.wrist.${feature}`;
    const cards = translation ? ["GS01-M03-04", "GS05-M01-03"] : [rotation ? "GS01-M03-01" : "GS01-M03-02"];
    const anchors = translation ? ["CARD-GS:word/document.xml:P0975", "CARD-GS:word/document.xml:P9325"] : [rotation ? "CARD-GS:word/document.xml:P0843" : "CARD-GS:word/document.xml:P0887"];
    if (raw.source !== source || !text(raw.video_id) || !translation && raw.value < 0 || raw.phase !== "preparation"
      || raw.window_scope !== "continuous_window_in_estimated_preparation" || raw.measurement_semantics !== "preparation_candidate_2d_not_formal_stage_or_grade"
      || raw.feature_version !== (translation ? "preparation-translation-v1.0.0" : "preparation-candidate-projection-v1.0.0") || raw.source_card_applicability !== "unconfirmed"
      || !integer(phase.start_ms) || !integer(phase.end_ms) || phase.start_ms > raw.start_ms || phase.end_ms < raw.end_ms
      || !Array.isArray(raw.source_timestamps_ms) || raw.source_timestamps_ms.length !== raw.source_frames.length
      || !raw.source_timestamps_ms.every(translation ? finite : integer)
      || !translation && (raw.source_timestamps_ms[0] !== raw.start_ms || raw.source_timestamps_ms.at(-1) !== raw.end_ms)
      || !raw.source_timestamps_ms.every((time, i, all) => i === 0 || time > all[i - 1])
      || JSON.stringify(raw.related_source_cards) !== JSON.stringify(cards) || JSON.stringify(raw.source_refs) !== JSON.stringify(anchors)
      || !translation && evidence.status !== "measured" || evidence.start_ms !== raw.start_ms || evidence.end_ms !== raw.end_ms
      || (translation ? evidence.value !== raw.value : rotation ? evidence.value !== raw.value || evidence.unit !== raw.unit || evidence.phase !== "preparation" : obj(evidence.wrist)[feature] !== raw.value)) return null;
    if (translation && (!Array.isArray(raw.video_timestamps_ms) || raw.video_timestamps_ms.length !== raw.source_frames.length
      || ref.camera_compensated !== true
      || raw.video_timestamps_ms[0] !== raw.start_ms || raw.video_timestamps_ms.at(-1) !== raw.end_ms
      || !raw.video_timestamps_ms.every(integer) || !raw.video_timestamps_ms.every((time, i, all) => i === 0 || time > all[i - 1])
      || JSON.stringify(evidence.source_frames) !== JSON.stringify(raw.source_frames)
      || JSON.stringify(evidence.source_timestamps_ms) !== JSON.stringify(raw.source_timestamps_ms)
      || JSON.stringify(evidence.video_timestamps_ms) !== JSON.stringify(raw.video_timestamps_ms)
      || JSON.stringify(evidence.reference) !== JSON.stringify(ref)
      || !finite(raw.fixed_scale_px) || raw.fixed_scale_px < 12 || raw.fixed_scale_px !== evidence.fixed_scale_px
      || !["decoder_timestamp_ms", "bound_source_pts"].includes(String(raw.time_basis)) || raw.time_basis !== evidence.time_basis
      || !finite(raw.source_duration_ms) || raw.source_duration_ms < 80 || evidence.source_duration_ms !== raw.source_duration_ms
      || Math.abs(Number(raw.source_timestamps_ms.at(-1)) - Number(raw.source_timestamps_ms[0]) - raw.source_duration_ms) > .000001)) return null;
    if (translation && (raw.time_basis !== (ref.pose_source === "sam3d" ? "bound_source_pts" : "decoder_timestamp_ms")
      || (raw.source_timestamps_ms as number[]).some((time, i, all) => Math.abs((time - all[0]) - (Number((raw.video_timestamps_ms as number[])[i]) - Number(raw.start_ms))) > 1.01))) return null;
  } else {
    const stats = obj(raw.statistics);
    const original = stats[feature.endsWith("fraction") ? "mean" : "median"];
    if (raw.source !== `${motionSource}.source_observations`
      || raw.window_scope !== "continuous_local_window_in_estimated_preparation"
      || raw.measurement_semantics !== "joint_specific_2d_measurements_not_technical_grade"
      || !finite(original) || Math.abs(original - raw.value) > .000051) return null;
  }
  const provenance = obj(raw.measurement_provenance);
  if (ref.pose_source === "sam3d" || provenance.pose_source === "sam3d") {
    if (ref.pose_source !== "sam3d" || ref.observation_semantics !== "monocular_model_estimate"
      || provenance.pose_source !== "sam3d" || provenance.observation_semantics !== "monocular_model_estimate"
      || provenance.scoring_allowed !== false || provenance.confidence_available !== false || provenance.visibility_verified !== false
      || !text(ref.video_sha256) || !/^[a-f\d]{64}$/i.test(ref.video_sha256) || !integer(ref.source_selection_epoch)
      || !finite(raw.source_time_origin_ms)) return null;
    for (const [edge, frame, videoTime] of [["start", raw.source_frames[0], raw.start_ms], ["end", raw.source_frames.at(-1), raw.end_ms]] as const) {
      const binding = obj(raw[`source_binding_${edge}`]);
      if (binding.video_sha256 !== ref.video_sha256 || binding.person_track_id !== raw.person_track_id
        || binding.selection_epoch !== ref.source_selection_epoch || binding.camera_reference_epoch !== ref.camera_reference_epoch
        || binding.frame_index !== frame || binding.video_timestamp_ms !== videoTime
        || !integer(binding.processed_index) || !integer(binding.pts) || !integer(binding.time_base_num) || binding.time_base_num === 0 || !integer(binding.time_base_den) || binding.time_base_den === 0
        || !finite(binding.timestamp_ms) || Math.abs(binding.timestamp_ms - binding.pts * binding.time_base_num / binding.time_base_den * 1000) > .51
        || Math.abs(binding.timestamp_ms - raw.source_time_origin_ms - videoTime) > 1.01) return null;
    }
  }
  return {
    id: String(raw.id), label_zh: raw.label_zh, status: "measured", value: raw.value, unit: String(raw.unit), score: null,
    reason_zh: raw.reason_zh, rule_zh: raw.rule_zh, source: String(raw.source), start_ms: raw.start_ms, end_ms: raw.end_ms,
    episode_id: raw.episode_id, feature_name: feature, person_track_id: raw.person_track_id, sample_count: raw.sample_count as number,
    source_frames: [...raw.source_frames] as number[], reference: { ...ref },
    ...(text(raw.video_id) ? { video_id: raw.video_id } : {}),
    window_scope: raw.window_scope as ProcessCriterion["window_scope"], measurement_semantics: raw.measurement_semantics as ProcessCriterion["measurement_semantics"],
    assessment_status: "measurement_only", full_source_rule_satisfied: false,
    ...(candidate ? { feature_version: String(raw.feature_version), source_timestamps_ms: [...raw.source_timestamps_ms as number[]],
      phase: "preparation" as const, phase_window: { start_ms: Number(obj(raw.phase_window).start_ms), end_ms: Number(obj(raw.phase_window).end_ms) },
      applicability_missing: Array.isArray(raw.applicability_missing) ? raw.applicability_missing.filter(text) : [],
      related_source_cards: [...raw.related_source_cards as string[]], source_refs: [...raw.source_refs as string[]],
      source_card_applicability: "unconfirmed" as const, original_metric_evidence: { ...obj(raw.original_metric_evidence) } } : { statistics: { ...obj(raw.statistics) } }),
    ...(Object.keys(obj(raw.measurement_provenance)).length ? { measurement_provenance: { ...obj(raw.measurement_provenance) } } : {}),
    ...(Object.keys(obj(raw.source_binding_start)).length ? { source_binding_start: { ...obj(raw.source_binding_start) } } : {}),
    ...(Object.keys(obj(raw.source_binding_end)).length ? { source_binding_end: { ...obj(raw.source_binding_end) } } : {}),
    ...(finite(raw.source_time_origin_ms) ? { source_time_origin_ms: raw.source_time_origin_ms } : {}),
    ...(translation ? { video_timestamps_ms: [...raw.video_timestamps_ms as number[]], time_basis: raw.time_basis as "decoder_timestamp_ms" | "bound_source_pts", fixed_scale_px: Number(raw.fixed_scale_px), source_duration_ms: Number(raw.source_duration_ms), full_phase_coverage: raw.full_phase_coverage === true } : {}),
  };
}
