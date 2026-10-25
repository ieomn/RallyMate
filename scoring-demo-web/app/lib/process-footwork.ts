import type { ProcessCriterion } from "./api-types";
import { hasModelMeasurementContext, modelMeasurementEvidenceOf, modelGeometryCoverage } from "./model-measurement";

// Mirrors the backend's reviewed observation set. These values never create scores.
export const FOOTWORK_OBSERVATION_UNITS: Record<string, Record<string, string>> = {
  "FS01-M02": { hip_center_y_body: "body", left_knee_flexion_deg: "deg", right_knee_flexion_deg: "deg" },
  "FS01-M03": { bilateral_foot_rise_min_body: "body", bilateral_foot_rise_synchrony_ms: "ms" },
  "FS01-M04": { post_slowdown_stance_width_body: "body", hip_center_lateral_variability_body: "body" },
  "FS01-M05": { body_center_speed_body_s: "body/s", hip_center_relative_to_ankle_support: "ratio" },
  "FS02-M02": { body_center_speed_body_s: "body/s", launch_direction_deg: "deg" },
  "FS02-M03": { support_knee_extension_velocity_deg_s: "deg/s", hip_acceleration_along_launch_direction_body_s2: "body/s2" },
  "FS02-M04": { launch_foot_speed_peak_body_s: "body/s", launch_foot_relative_displacement_body: "body" },
  "FS02-M05": { first_step_displacement_body: "body", post_step_hip_direction_consistency: "ratio" },
  "FS09-M01": { hip_center_speed_body_s: "body/s", hip_center_motion_direction_deg: "deg" },
  "FS09-M02": { braking_ankle_speed_drop_body_s: "body/s", hip_center_deceleration_body_s2: "body/s2" },
  "FS09-M03": { hip_center_speed_drop_body_s: "body/s", left_knee_flexion_change_deg: "deg", right_knee_flexion_change_deg: "deg" },
  "FS09-M04": { stability_duration_ms: "ms", stance_width_body: "body" },
  "FS09-M05": { double_support_proxy_duration_ms: "ms", torso_lean_variability_deg: "deg" },
};
const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const text = (value: unknown): value is string => typeof value === "string" && value.trim().length > 0;
const integer = (value: unknown): value is number => Number.isSafeInteger(value) && Number(value) >= 0;

export function processFootworkCriterion(raw: Record<string, unknown>): ProcessCriterion | null {
  const indicator = raw.indicator_id, feature = raw.feature_name;
  if (!text(indicator) || !text(feature) || !FOOTWORK_OBSERVATION_UNITS[indicator]?.[feature]
    || raw.id !== `footwork_${indicator}_${feature}` || raw.source !== `indicator_features.${indicator}.features.${feature}`
    || raw.unit !== FOOTWORK_OBSERVATION_UNITS[indicator][feature] || raw.status !== "measured" || raw.score !== null || !finite(raw.value)
    || !finite(raw.start_ms) || !finite(raw.end_ms) || raw.start_ms < 0 || raw.end_ms <= raw.start_ms
    || !text(raw.episode_id) || !text(raw.video_id) || !integer(raw.person_track_id) || !text(raw.feature_version)
    || raw.window_scope !== "event_interval" || raw.measurement_semantics !== "image_plane_proxy_not_source_card_grade"
    || !Array.isArray(raw.source_frames) || !raw.source_frames.length || !raw.source_frames.every(integer)) return null;
  const quality = raw.quality && typeof raw.quality === "object" ? raw.quality as Record<string, unknown> : {};
  const model = hasModelMeasurementContext(raw), evidence = model ? modelMeasurementEvidenceOf(raw) : null;
  if (quality.measurement_status !== "measured"
    || (model ? evidence === null || quality.confidence !== null || raw.assessment_status !== "measurement_only" || raw.full_source_rule_satisfied !== false
      : !finite(quality.confidence) || quality.confidence < 0 || quality.confidence > 1)
    || !Array.isArray(quality.reason_codes) || quality.reason_codes.length > 0
    || !["measured", "unavailable"].includes(String(quality.aggregate_feature_status))
    || quality.review_version !== "footwork-independent-measurements-v1.1.0"
    || !Array.isArray(quality.required_joints) || !quality.required_joints.length || !quality.required_joints.every(text)) return null;
  if (evidence && (Number(evidence.source_window.start_ms) < raw.start_ms || Number(evidence.source_window.end_ms) > raw.end_ms
    || (raw.source_frames as number[]).some(frame => !(evidence.source_window.source_frames as number[]).includes(frame))
    || modelGeometryCoverage(evidence, quality.required_joints as string[]) < .5)) return null;
  // Signed changes, support projections outside [0,1] and negative direction cosines are legitimate.
  if (["launch_direction_deg", "hip_center_motion_direction_deg", "left_knee_flexion_change_deg", "right_knee_flexion_change_deg"].includes(feature) && Math.abs(raw.value) > 180
    || ["left_knee_flexion_deg", "right_knee_flexion_deg"].includes(feature) && (raw.value < 0 || raw.value > 180)
    || feature === "post_step_hip_direction_consistency" && Math.abs(raw.value) > 1
    || raw.unit === "ms" && (raw.value < 0 || raw.value > raw.end_ms - raw.start_ms + 1)) return null;
  return {
    id: String(raw.id), label_zh: text(raw.label_zh) && /[\u3400-\u9fff]/u.test(raw.label_zh) ? raw.label_zh : "步伐测量",
    status: "measured", value: raw.value, unit: String(raw.unit), score: null,
    reason_zh: text(raw.reason_zh) ? raw.reason_zh : "事件内二维步伐测量，不是原卡技术等级。",
    rule_zh: text(raw.rule_zh) ? raw.rule_zh : "仅作观察，不换算技术分。", source: String(raw.source),
    start_ms: raw.start_ms, end_ms: raw.end_ms, episode_id: raw.episode_id,
    indicator_id: indicator, feature_name: feature, video_id: raw.video_id, person_track_id: raw.person_track_id,
    source_frames: [...new Set(raw.source_frames as number[])].sort((a, b) => a - b), feature_version: raw.feature_version,
    window_scope: "event_interval", measurement_semantics: "image_plane_proxy_not_source_card_grade",
    quality: { measurement_status: "measured", confidence: model ? null : quality.confidence as number, reason_codes: [], aggregate_feature_status: quality.aggregate_feature_status as "measured" | "unavailable", required_joints: quality.required_joints as string[], review_version: "footwork-independent-measurements-v1.1.0" },
    ...(evidence ? { ...evidence, assessment_status: "measurement_only" as const, full_source_rule_satisfied: false as const } : {}),
  };
}
