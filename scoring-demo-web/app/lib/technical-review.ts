import type { DemoResultResponse, FootworkReview, HipContextualMeasurement } from "./api-types";
import { reportVideoDurationMs } from "./workspace-navigation";
import { sourceMeasurementDetailsOf, type SourceMeasurementDetails } from "./source-measurement-details";
import { hasModelMeasurementContext, modelMeasurementEvidenceOf, type ModelMeasurementEvidence } from "./model-measurement";

export const SOURCE_ASSESSMENT_VERSION = "source-aligned-assessment-v1.0.0";
export const REVIEW_SOURCE_ID = "scoring-reference-20261004";
export const TECHNICAL_GRADES = ["A", "B", "C", "D", "E"] as const;
export type TechnicalGrade = typeof TECHNICAL_GRADES[number];
export type SourceMeasurement = SourceMeasurementDetails & { feature_name: string; label_zh: string; value: number | null; unit: string; status: "measured" | "unavailable"; reason_zh: string; source_requirement_zh: string; measurement_window: { start_ms: number; end_ms: number } | null };
export type SourceVisibility = { status: string; valid_frame_ratio: number | null; valid_frame_count: number | null; total_frame_count: number | null; required_joint_ids: string[]; reason_zh: string; visibility_verified?: false; source_visibility_70_percent_verified?: null };
export type SourceReviewWindow = Partial<ModelMeasurementEvidence> & { start_ms: number; end_ms: number; event_id: string; person_track_id: number | null; measurements: SourceMeasurement[]; visibility: SourceVisibility; limitations_zh: string[] };
export type TechnicalReviewInput = {
  mutation_id: string; expected_revision: number; source_sha256: string; artifact_context_sha256: string; source_reference_version: string; source_reference_sha256: string;
  review_id?: string; target_kind: "indicator" | "visual_rule"; indicator_id: string | null; visual_rule_id: string | null;
  player_id: number; event_id: string | null; event_source: "system_event" | "manual_interval";
  start_ms: number; end_ms: number; reviewer_id: string; reviewer_name: string; reviewer_role: "coach" | "reviewer";
  observability: "observable" | "partial" | "unobservable"; status: "graded" | "observed" | "not_observed" | "unassessable"; grade: TechnicalGrade | null;
  reason_zh: string; next_step_zh: string;
};
export type TechnicalReviewEntry = Omit<TechnicalReviewInput, "mutation_id" | "expected_revision"> & {
  review_id: string; entry_revision: number; created_at: string; updated_at?: string; source_binding_current: boolean;
};
export type TechnicalReviewLedger = {
  schema_version: "technical-review-v1.0.0"; job_id: string; revision: number; source_sha256: string; artifact_context_sha256: string;
  source_reference_version: string; source_reference_sha256: string;
  video: { duration_ms: number; review_start_ms: number; review_end_ms: number };
  players: Array<{ player_id: number; start_ms?: number; end_ms?: number }>;
  events: Array<{ event_id: string; event_code: string; player_id: number; start_ms: number; end_ms: number }>;
  rules: Array<{ indicator_id: string; event_code: string; manual_grading_allowed: boolean; blockers: Array<{ code: string; message: string }>; warnings?: Array<{ code: string; message: string }> }>;
  visual_rules: Array<{ visual_rule_id: string; technique_ids: string[]; stage_id: string; phase_id: string; optional_in_source: boolean;
    optional_positive_observation: boolean; source_document_id: string; source_document_sha256: string; source_locator: string; source_text: string; rubric_sha256: string }>;
  entries: TechnicalReviewEntry[];
  semantics: "human_source_rule_review"; formal_grade: null; calibration_eligible: false;
};

const record = (value: unknown): Record<string, unknown> => value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const count = (value: unknown): value is number => finite(value) && Number.isSafeInteger(value) && value >= 0;
const text = (value: unknown, limit = 4000): string => typeof value === "string" && value.length <= limit ? value : "";
const strings = (value: unknown): string[] => Array.isArray(value) ? value.filter((item): item is string => typeof item === "string" && item.length <= 2000) : [];
const sha256 = (value: unknown) => typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
const sameSourceVideoSha256 = (left: unknown, right: unknown) => typeof left === "string" && typeof right === "string"
  && left.length === 64 && right.length === 64 && /^[0-9a-fA-F]{64}$/.test(left) && /^[0-9a-fA-F]{64}$/.test(right)
  && left.toLowerCase() === right.toLowerCase();
const uuid = (value: unknown) => typeof value === "string" && /^[a-f0-9]{8}-[a-f0-9]{4}-[1-5][a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/i.test(value);

function sameModelSamples(child: SourceMeasurementDetails, parent: ModelMeasurementEvidence): boolean {
  const window = child.source_window;
  if (!window || !child.geometry_mask) return false;
  const parentFrames = parent.source_window.source_frames as number[], childFrames = window.source_frames as number[];
  const positions = new Map(parentFrames.map((frame, index) => [frame, index]));
  const childBindings = window.source_bindings as Record<string, unknown>[], parentBindings = parent.source_window.source_bindings as Record<string, unknown>[];
  const keys = ["video_sha256", "frame_index", "processed_index", "pts", "time_base_num", "time_base_den", "timestamp_ms", "person_track_id", "selection_epoch", "camera_reference_epoch"];
  return childFrames.every((frame, i) => {
    const j = positions.get(frame);
    return j === undefined || keys.every(key => childBindings[i][key] === parentBindings[j][key])
      && ["video_timestamps_ms", "source_timestamps_ms"].every(key => (window[key] as number[])[i] === (parent.source_window[key] as number[])[j])
      && Object.entries(child.geometry_mask!).every(([name, mask]) => parent.geometry_mask[name]?.[j] === mask[i]);
  });
}

export function parseTechnicalReview(value: unknown, jobId: string): TechnicalReviewLedger {
  const data = record(value), video = record(data.video);
  const invalid = () => { throw new Error("评审服务返回的任务、来源或记录不完整，请重新读取。"); };
  if (data.schema_version !== "technical-review-v1.0.0" || data.job_id !== jobId || !count(data.revision)
    || !sha256(data.source_sha256) || !sha256(data.artifact_context_sha256) || !sha256(data.source_reference_sha256) || data.source_reference_version !== REVIEW_SOURCE_ID
    || data.semantics !== "human_source_rule_review" || data.formal_grade !== null || data.calibration_eligible !== false
    || !count(video.duration_ms) || !count(video.review_start_ms) || !count(video.review_end_ms)
    || video.review_start_ms >= video.review_end_ms || video.review_end_ms > video.duration_ms
    || !Array.isArray(data.players) || !Array.isArray(data.events) || !Array.isArray(data.rules) || !Array.isArray(data.visual_rules) || !Array.isArray(data.entries)) return invalid();
  const players = data.players.map(record), events = data.events.map(record), rules = data.rules.map(record), entries = data.entries.map(record);
  const visualRules = data.visual_rules.map(record);
  if (players.some(player => !count(player.player_id)) || new Set(players.map(player => player.player_id)).size !== players.length
    || rules.some(rule => !/^((GS\d{2}-M\d{2}-\d{2})|(FS\d{2}-M\d{2}))$/.test(String(rule.indicator_id))
      || typeof rule.manual_grading_allowed !== "boolean" || !Array.isArray(rule.blockers)
      || rule.blockers.some(raw => !text(record(raw).code) || !text(record(raw).message)))
    || new Set(rules.map(rule => rule.indicator_id)).size !== rules.length
    || visualRules.some(rule => !text(rule.visual_rule_id, 128) || !text(rule.source_text) || !text(rule.stage_id, 120)
      || !Array.isArray(rule.technique_ids) || !strings(rule.technique_ids).length || typeof rule.optional_in_source !== "boolean"
      || !sha256(rule.source_document_sha256) || !sha256(rule.rubric_sha256))
    || new Set(visualRules.map(rule => rule.visual_rule_id)).size !== visualRules.length) return invalid();
  const within = (item: Record<string, unknown>) => count(item.start_ms) && count(item.end_ms)
    && item.start_ms >= Number(video.review_start_ms) && item.end_ms <= Number(video.review_end_ms) && item.end_ms > item.start_ms;
  if (events.some(event => !text(event.event_id, 128) || !text(event.event_code, 8) || !within(event)
    || !players.some(player => player.player_id === event.player_id)) || new Set(events.map(event => event.event_id)).size !== events.length) return invalid();
  if (entries.some(entry => !uuid(entry.review_id) || !count(entry.entry_revision) || typeof entry.source_binding_current !== "boolean"
    || !count(entry.start_ms) || !count(entry.end_ms) || entry.end_ms <= entry.start_ms
    || entry.source_binding_current && (!within(entry) || !players.some(player => player.player_id === entry.player_id)
      || entry.source_sha256 !== data.source_sha256 || entry.source_reference_version !== data.source_reference_version || entry.source_reference_sha256 !== data.source_reference_sha256 || entry.artifact_context_sha256 !== data.artifact_context_sha256)
    || !text(entry.reviewer_name, 200) || !text(entry.reviewer_id, 200) || !["coach", "reviewer"].includes(String(entry.reviewer_role))
    || !["observable", "partial", "unobservable"].includes(String(entry.observability))
    || !text(entry.reason_zh) || typeof entry.next_step_zh !== "string"
    || (entry.target_kind === "visual_rule" ? entry.indicator_id !== null || entry.grade !== null || entry.event_source !== "manual_interval"
      || !text(entry.visual_rule_id, 128) || entry.source_binding_current && !visualRules.some(rule => rule.visual_rule_id === entry.visual_rule_id) || !["observed", "not_observed", "unassessable"].includes(String(entry.status))
      || entry.status !== "unassessable" && entry.observability !== "observable"
      : entry.target_kind !== "indicator" || entry.visual_rule_id !== null || !text(entry.indicator_id, 128) || entry.source_binding_current && !rules.some(rule => rule.indicator_id === entry.indicator_id)
        || !["graded", "unassessable"].includes(String(entry.status))
        || (entry.status === "graded" ? entry.observability !== "observable" || !TECHNICAL_GRADES.includes(entry.grade as TechnicalGrade)
          || entry.source_binding_current && !rules.some(rule => rule.indicator_id === entry.indicator_id && rule.manual_grading_allowed) : entry.grade !== null))
    || (entry.event_source === "manual_interval" ? entry.event_id !== null : entry.event_source !== "system_event"
      || entry.source_binding_current && !events.some(event => event.event_id === entry.event_id && event.player_id === entry.player_id
        && event.event_code === rules.find(rule => rule.indicator_id === entry.indicator_id)?.event_code
        && Number(entry.start_ms) >= Number(event.start_ms) && Number(entry.end_ms) <= Number(event.end_ms))))
    || new Set(entries.map(entry => entry.review_id)).size !== entries.length) return invalid();
  return data as TechnicalReviewLedger;
}

export function technicalReviewError(input: TechnicalReviewInput, ledger: TechnicalReviewLedger): string | null {
  const rule = ledger.rules.find(rule => rule.indicator_id === input.indicator_id);
  const visual = ledger.visual_rules.find(rule => rule.visual_rule_id === input.visual_rule_id);
  if (!uuid(input.mutation_id) || input.expected_revision !== ledger.revision || input.source_sha256 !== ledger.source_sha256 || input.artifact_context_sha256 !== ledger.artifact_context_sha256
    || input.source_reference_sha256 !== ledger.source_reference_sha256 || input.source_reference_version !== ledger.source_reference_version) return "来源或评审版本已改变，请重新读取后保存。";
  if ((input.target_kind === "indicator" ? !rule || input.visual_rule_id !== null : !visual || input.indicator_id !== null)
    || !ledger.players.some(player => player.player_id === input.player_id)) return "请选择这段视频中的人物和原文指标。";
  if (!count(input.start_ms) || !count(input.end_ms) || input.start_ms < ledger.video.review_start_ms
    || input.end_ms > ledger.video.review_end_ms || input.end_ms <= input.start_ms) return "评审时段必须位于本次实际分析的视频范围内。";
  const player = ledger.players.find(player => player.player_id === input.player_id);
  if (player && (finite(player.start_ms) && input.start_ms < player.start_ms || finite(player.end_ms) && input.end_ms > player.end_ms)) return "所选人物在这个时段没有完整的跟踪记录，请调整时段。";
  if (input.event_source === "system_event" && !ledger.events.some(event => event.event_id === input.event_id && event.player_id === input.player_id
    && event.event_code === rule?.event_code && input.start_ms >= event.start_ms && input.end_ms <= event.end_ms)) return "当前时段与所选动作或人物不一致，请重新选择。";
  if (input.event_source === "manual_interval" && input.event_id !== null) return "手动时段不能附带自动事件编号。";
  if (!input.reviewer_name.trim() || !input.reviewer_id || input.reason_zh.trim().length < 2) return "请填写评审人和具体判断依据。";
  if (input.reason_zh.length > 4000 || input.next_step_zh.length > 4000) return "判断依据和下一步建议各不能超过 4000 字。";
  if (input.target_kind === "visual_rule" && (input.event_source !== "manual_interval" || input.grade !== null
    || !["observed", "not_observed", "unassessable"].includes(input.status) || input.status !== "unassessable" && input.observability !== "observable")) return "视觉要点不产生技术等级；只有能够清楚判断时，才能记录观察到或未观察到。";
  if (input.target_kind === "indicator" && !["graded", "unassessable"].includes(input.status)) return "评分指标请选择 A～E 或无法评价。";
  if (input.status === "graded" && (!rule?.manual_grading_allowed || input.observability !== "observable"
    || !TECHNICAL_GRADES.includes(input.grade as TechnicalGrade))) return "这项证据或原文条件不足，请选择无法评价并说明原因。";
  if (input.status === "unassessable" && input.grade !== null) return "无法评价的记录不能附带 A～E 等级。";
  if (input.review_id) {
    const previous = ledger.entries.find(entry => entry.review_id === input.review_id);
    const binding = ["target_kind", "indicator_id", "visual_rule_id", "player_id", "event_id", "event_source", "start_ms", "end_ms", "reviewer_id", "reviewer_name", "reviewer_role"] as const;
    if (!previous?.source_binding_current || binding.some(key => previous[key] !== input[key])) return "修订必须保留原人物、时段、评审人和来源；如需更改，请新增评审。";
  }
  return null;
}

/** Only event-local, explicitly source-aligned measurements enter the technical review. */
export function sourceReviewWindows(result: DemoResultResponse | null, indicatorId: string): SourceReviewWindow[] {
  const assessment = record(result?.source_aligned_assessment);
  if (assessment.version !== SOURCE_ASSESSMENT_VERSION || assessment.status !== "available"
    || assessment.score_semantics !== "source_aligned_measurement_not_technical_grade"
    || assessment.technical_grade !== null || !Array.isArray(assessment.indicators)) return [];
  const matching = assessment.indicators.map(record).filter(item => item.indicator_id === indicatorId);
  if (matching.length !== 1 || !Array.isArray(matching[0].windows)) return [];
  const duration = reportVideoDurationMs(result);
  if (duration === null) return [];
  const seen = new Set<string>();
  return matching[0].windows.flatMap(value => {
    const window = record(value), visibility = record(window.visibility);
    if (!finite(window.start_ms) || !finite(window.end_ms) || window.start_ms < 0 || window.end_ms <= window.start_ms
      || duration !== null && window.end_ms > duration || !text(window.event_id, 120) || !Array.isArray(window.measurements)) return [];
    const key = `${window.event_id}:${window.start_ms}:${window.end_ms}`;
    if (seen.has(key)) return [];
    seen.add(key);
    const windowModel = hasModelMeasurementContext(window);
    const windowProof = windowModel ? modelMeasurementEvidenceOf(window) : null;
    const validModelWindow = !!windowProof && windowProof.source_window.start_ms === window.start_ms
      && windowProof.source_window.end_ms === window.end_ms && sameSourceVideoSha256(windowProof.source_window.video_sha256, assessment.video_sha256);
    const ratio = visibility.valid_frame_ratio, valid = visibility.valid_frame_count, total = visibility.total_frame_count;
    const validVisibility = !windowModel && visibility.scope === "indicator_window" && visibility.threshold_ratio === .7
      && ["sufficient", "insufficient"].includes(String(visibility.status))
      && finite(ratio) && ratio >= 0 && ratio <= 1 && count(valid) && count(total) && total > 0 && valid <= total
      && Math.abs(ratio - valid / total) <= 1e-6 && strings(visibility.required_joint_ids).length > 0;
    const measuredNames = new Set<string>();
    const measurements = window.measurements.flatMap(value => {
      const item = record(value), feature = text(item.feature_name, 160), label = text(item.label_zh, 200);
      if (!feature || !label || measuredNames.has(feature)) return [];
      measuredNames.add(feature);
      const details = sourceMeasurementDetailsOf(item);
      const model = windowModel || hasModelMeasurementContext(item);
      const proof = details.source_window;
      const modelFrames = proof?.source_frames as number[] | undefined;
      const transition = ["landing_proxy_to_next_fs02_ms", "stable_control_proxy_to_next_fs10_or_fs02_ms"].includes(feature);
      const validModel = validModelWindow && !!proof && !!details.model_geometry_coverage?.passes
        && sameModelSamples(details, windowProof!)
        && ["video_sha256", "person_track_id", "selection_epoch", "camera_reference_epoch"].every(key => proof[key] === windowProof!.source_window[key])
        && Array.isArray(modelFrames) && modelFrames.length === details.source_frames?.length
        && modelFrames.every((frame, index) => frame === details.source_frames![index])
        && (transition || Number(proof.start_ms) >= Number(window.start_ms) && Number(proof.end_ms) <= Number(window.end_ms));
      const measured = item.status === "measured" && finite(item.value) && !!text(item.unit, 80) && (!model || validModel);
      const measurementWindow = record(item.measurement_window);
      const sameFrameTransition = measured && item.value === 0 && ["landing_proxy_to_next_fs02_ms", "stable_control_proxy_to_next_fs10_or_fs02_ms"].includes(feature);
      const validMeasurementWindow = finite(measurementWindow.start_ms) && finite(measurementWindow.end_ms)
        && measurementWindow.start_ms >= 0 && (measurementWindow.end_ms > measurementWindow.start_ms || sameFrameTransition && measurementWindow.end_ms === measurementWindow.start_ms)
        && (duration === null || measurementWindow.end_ms <= duration)
        && (!model || validModel && measurementWindow.start_ms === proof!.start_ms && measurementWindow.end_ms === proof!.end_ms);
      return [{ feature_name: feature, label_zh: label, value: measured && (!model || validMeasurementWindow) ? item.value as number : null, unit: text(item.unit, 80),
        status: measured && (!model || validMeasurementWindow) ? "measured" as const : "unavailable" as const, reason_zh: text(item.reason_zh), source_requirement_zh: text(item.source_requirement_zh),
        measurement_window: validMeasurementWindow ? { start_ms: measurementWindow.start_ms as number, end_ms: measurementWindow.end_ms as number } : null,
        ...details }];
    });
    return [{ start_ms: window.start_ms, end_ms: window.end_ms, event_id: window.event_id as string,
      person_track_id: count(window.person_track_id) ? window.person_track_id : null, measurements,
      ...(validModelWindow ? windowProof : {}),
      visibility: { status: validVisibility ? text(visibility.status, 80) : "unavailable", valid_frame_ratio: validVisibility ? ratio : null,
        valid_frame_count: validVisibility ? valid : null, total_frame_count: validVisibility ? total : null,
        required_joint_ids: strings(visibility.required_joint_ids), reason_zh: windowModel ? "SAM 单目模型几何可计算不等于真实可见；原文 70% 可见性未知，未判定技术等级。" : validVisibility ? text(visibility.reason_zh) : "未提供本指标在该时段的可核验关键点可见性。",
        ...(windowModel ? { visibility_verified: false as const, source_visibility_70_percent_verified: null } : {}) },
      limitations_zh: strings(window.limitations_zh) }];
  }).sort((left, right) => left.start_ms - right.start_ms);
}

export function sourceReviewCoverage(result: DemoResultResponse | null, indicatorId: string) {
  const assessment = record(result?.source_aligned_assessment);
  const rows = Array.isArray(assessment.indicators) ? assessment.indicators.map(record).filter(item => item.indicator_id === indicatorId) : [];
  const row = rows.length === 1 ? rows[0] : {};
  const windows = sourceReviewWindows(result, indicatorId), returned = windows.length;
  const completeCounts = count(row.window_count) && count(row.measured_window_count) && count(row.unavailable_window_count)
    && row.window_count >= returned && row.measured_window_count + row.unavailable_window_count === row.window_count;
  const measured = completeCounts ? row.measured_window_count as number : windows.filter(window => window.measurements.some(item => item.status === "measured")).length;
  const unavailable = completeCounts ? row.unavailable_window_count as number : returned - measured;
  return { returned, measured, unavailable, countsScope: completeCounts ? "all" : "returned", total: count(row.window_count) && row.window_count >= returned ? row.window_count : returned,
    truncated: row.is_truncated === true && count(row.window_count) && row.window_count > returned };
}

export function sourceWindowDisplay(windows: SourceReviewWindow[], mode: "measured" | "unavailable" | "all") {
  const measured = windows.filter(window => window.measurements.some(item => item.status === "measured")).sort((a, b) => a.start_ms - b.start_ms);
  const unavailable = windows.filter(window => !window.measurements.some(item => item.status === "measured")).sort((a, b) => a.start_ms - b.start_ms);
  return mode === "all" ? [...measured, ...unavailable] : mode === "unavailable" ? unavailable : measured;
}

/** Match Python len/str.strip for opaque source IDs; never normalize the value. */
export function isOpaqueSourceIdentity(value: unknown): value is string {
  if (typeof value !== "string") return false;
  const characters = Array.from(value);
  return characters.length > 0 && characters.length <= 256
    // eslint-disable-next-line no-control-regex -- Deliberately mirrors Python str.strip, including its control whitespace.
    && characters.some(character => !/^[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]$/.test(character));
}
const hipIdentity = isOpaqueSourceIdentity;
const hipNeutral = { score: null, grade: null, formal_event_verified: false, scoring_allowed: false,
  assessment_status: "measurement_only", coordinate_system: "stable_camera_reference_image_pixels", sampling_protocol: "original_source_frames" } as const;
const hipAdapter = { version: "native-sam-primary-epochs-v1.0.0",
  provenance: "unique_same_frame_selected_track_SAM_request_and_pose_binding_and_camera", source_files_changed: false } as const;
const hipIdentityKeys = ["person_track_id", "selection_epoch", "camera_reference_epoch"] as const;
const hipBindingKeys = ["video_sha256", "frame_index", "processed_index", "pts", "time_base_num", "time_base_den", "timestamp_ms", ...hipIdentityKeys];
const near = (left: number, right: number) => Number.isFinite(left) && Number.isFinite(right)
  && Math.abs(left - right) <= Math.max(1e-12, 1e-9 * Math.max(Math.abs(left), Math.abs(right)));
// Match the service's fixed pre-helper/helper failure contracts, not arbitrary reason text.
const hipPreHelperReasons = new Set([
  "contextual_measurement_failed", "contextual_source_window_incomplete", "contextual_raw_source_identity_mismatch",
  "contextual_native_adapter_rejected", "contextual_native_adapter_version_mismatch", "contextual_source_loader_rejected",
  "contextual_source_binding_unavailable", "contextual_event_source_binding_mismatch",
]);
const hipHelperReasons = new Set([
  "invalid_external_window", "original_source_sampling_not_declared", "invalid_video_time_array",
  "window_not_complete_consecutive_source_frames", "source_time_binding_unavailable", "source_time_binding_mismatch",
  "source_video_clock_origin_changed", "video_identity_changed", "subject_identity_changed", "camera_reference_changed",
  "source_timebase_changed", "nonadjacent_processed_indexes", "source_pts_not_increasing", "video_time_not_increasing",
  "primary_timeline_window_mismatch", "primary_identity_unverified", "primary_timeline_binding_mismatch",
  "primary_timeline_clock_mismatch", "coordinate_contract_unverified", "modified_sampling_or_geometry",
  "camera_reference_unverified", "camera_application_evidence_invalid", "frame_dimensions_unavailable",
  "frame_dimensions_invalid_or_changed", "model_source_binding_unavailable", "model_source_binding_mismatch",
  "hip_geometry_unavailable", "hip_geometry_shape_invalid", "hip_model_geometry_unavailable",
  "model_confidence_must_remain_unknown", "hip_confidence_invalid", "nonfinite_reference_geometry",
  "hip_outside_original_frame", "nonfinite_path_geometry", "zero_net_displacement", "nonfinite_ratio",
]);

/** Complete decoder windows only: no subset, nearest-time match, or inferred identity. */
function hipWindowOf(value: unknown): Record<string, unknown> | null {
  const row = record(value), frames = row.source_frames, video = row.video_timestamps_ms, times = row.source_timestamps_ms, bindings = row.source_bindings;
  const videoSha = typeof row.video_sha256 === "string" && /^[a-f0-9]{64}$/i.test(row.video_sha256) ? row.video_sha256.toLowerCase() : null;
  if (!count(row.start_ms) || !count(row.end_ms) || row.end_ms <= row.start_ms || !videoSha
    || hipIdentityKeys.some(key => !count(row[key])) || !Array.isArray(frames) || frames.length < 2 || frames.length > 20000
    || !Array.isArray(video) || video.length !== frames.length || !Array.isArray(times) || times.length !== frames.length
    || !Array.isArray(bindings) || bindings.length !== frames.length || video[0] !== row.start_ms || video.at(-1) !== row.end_ms) return null;
  let originLow = -Infinity, originHigh = Infinity;
  const cleanBindings: Record<string, unknown>[] = [];
  for (let i = 0; i < frames.length; i++) {
    const binding = record(bindings[i]), previous = record(bindings[i - 1]);
    if (!count(frames[i]) || !count(video[i]) || !finite(times[i]) || times[i] < 0
      || i > 0 && (frames[i] !== frames[i - 1] + 1 || video[i] <= video[i - 1] || times[i] <= times[i - 1])
      || !sameSourceVideoSha256(binding.video_sha256, videoSha) || binding.frame_index !== frames[i]
      || hipIdentityKeys.some(key => binding[key] !== row[key])
      || !count(binding.processed_index) || !count(binding.pts) || !count(binding.time_base_num) || !binding.time_base_num
      || !count(binding.time_base_den) || !binding.time_base_den || !finite(binding.timestamp_ms)
      || i > 0 && (binding.processed_index !== Number(previous.processed_index) + 1
        || binding.time_base_num !== previous.time_base_num || binding.time_base_den !== previous.time_base_den)
      || "video_timestamp_ms" in binding && binding.video_timestamp_ms !== video[i]) return null;
    const exactMs = binding.pts * binding.time_base_num / binding.time_base_den * 1000;
    if (!Number.isFinite(exactMs) || Math.abs(exactMs - times[i]) > 1e-6 || Math.abs(exactMs - binding.timestamp_ms) > .51) return null;
    originLow = Math.max(originLow, exactMs - video[i] - .51); originHigh = Math.min(originHigh, exactMs - video[i] + .51);
    if (originLow > originHigh) return null;
    cleanBindings.push({ ...Object.fromEntries([...hipBindingKeys, ...("video_timestamp_ms" in binding ? ["video_timestamp_ms"] : [])].map(key => [key, binding[key]])), video_sha256: videoSha });
  }
  return { start_ms: row.start_ms, end_ms: row.end_ms, source_frames: [...frames], video_timestamps_ms: [...video], source_timestamps_ms: [...times],
    source_bindings: cleanBindings, video_sha256: videoSha, ...Object.fromEntries(hipIdentityKeys.map(key => [key, row[key]])) };
}

function hipMeasurementOf(value: unknown, assessment: Record<string, unknown>): HipContextualMeasurement | null {
  const row = record(value), parent = record(row.parent_window_ms), adapter = record(row.adapter);
  if (row.type !== "hip_path_ratio_2d" || row.version !== "hip-contextual-measurement-v1.0.0" || row.helper_version !== "bounded-hip-path-ratio-v1.0.0"
    || !hipIdentity(row.event_id) || typeof row.event_code !== "string" || !["FS01", "FS02", "FS09"].includes(row.event_code) || !count(row.person_track_id)
    || !hipIdentity(row.video_id) || row.video_id !== assessment.video_id || !hipIdentity(row.source_id) || row.source_id !== assessment.source_id
    || !sha256(row.video_sha256) || row.video_sha256 !== assessment.video_sha256
    || Object.entries(hipNeutral).some(([key, expected]) => row[key] !== expected)
    || Object.entries(hipAdapter).some(([key, expected]) => adapter[key] !== expected)
    || !count(parent.start_ms) || !count(parent.end_ms) || parent.end_ms <= parent.start_ms
    || row.status !== "measured" && row.status !== "unavailable" || typeof row.reason !== "string") return null;
  const inputs = record(row.input_sha256), bounded = record(row.window_input_sha256);
  if (["frames", "primary_timeline", "events"].some(key => !sha256(inputs[key])) || !sha256(bounded.event)
    || ["records", "original_primary", "adapted_primary"].some(key => bounded[key] !== null && !sha256(bounded[key]))) return null;
  const window = row.source_window === null ? null : hipWindowOf(row.source_window), bounds = record(row.source_frame_bounds), measurement = record(row.measurement_window_video_ms);
  if (row.source_window !== null && !window || !Array.isArray(row.source_bindings)) return null;
  if (window ? window.start_ms !== parent.start_ms || window.end_ms !== parent.end_ms || window.video_sha256 !== row.video_sha256
    || measurement.start_ms !== parent.start_ms || measurement.end_ms !== parent.end_ms
    || bounds.start_source_frame !== (window.source_frames as number[])[0] || bounds.end_source_frame !== (window.source_frames as number[]).at(-1)
    : row.source_frame_bounds !== null || row.measurement_window_video_ms !== null || row.source_bindings.length !== 0 || row.source_reference !== null) return null;
  const optionalFailure = row.status === "unavailable" && row.reason === "contextual_measurement_failed";
  if (!window ? row.status !== "unavailable" || !["contextual_event_source_window_unavailable", "contextual_measurement_failed"].includes(row.reason)
    || ["records", "original_primary", "adapted_primary"].some(key => bounded[key] !== null)
    : !optionalFailure && ["records", "original_primary"].some(key => !sha256(bounded[key]))) return null;
  const bindings: Record<string, unknown>[] = [];
  if (row.source_bindings.length) {
    if (!window || row.source_bindings.length !== (window.source_frames as number[]).length) return null;
    for (let i = 0; i < row.source_bindings.length; i++) {
      const binding = record(row.source_bindings[i]), source = (window.source_bindings as Record<string, unknown>[])[i];
      if (binding.timestamp_source !== "decoder_pts" || binding.primary_player_id !== row.person_track_id
        || binding.source_timestamp_ms !== source.timestamp_ms || binding.video_timestamp_ms !== (window.video_timestamps_ms as number[])[i]
        || hipBindingKeys.filter(key => key !== "timestamp_ms").some(key => binding[key] !== source[key])) return null;
      bindings.push(Object.fromEntries([...hipBindingKeys.filter(key => key !== "timestamp_ms"), "timestamp_source", "primary_player_id", "source_timestamp_ms", "video_timestamp_ms"].map(key => [key, binding[key]])));
    }
  }
  const reference = record(row.source_reference);
  if (row.source_reference !== null && (!window || reference.video_sha256 !== row.video_sha256 || reference.primary_player_id !== row.person_track_id
    || hipIdentityKeys.some(key => reference[key] !== window[key]))) return null;
  const length = row.hip_path_length_px, net = row.hip_net_displacement_px, ratio = row.hip_path_over_net_ratio;
  const geometric = row.status === "measured" || row.reason === "zero_net_displacement";
  if (geometric) {
    if (!window || !bindings.length || row.source_reference === null || !finite(length) || length < 0 || !finite(net) || net < 0
      || ["records", "original_primary", "adapted_primary"].some(key => !sha256(bounded[key]))) return null;
    if (row.status === "measured" ? row.reason !== "bounded_image_plane_geometry_only" || net <= 0 || !finite(ratio) || ratio <= 0
      || length < net && !near(length, net) || !near(ratio, length / net)
      : row.status !== "unavailable" || net !== 0 || ratio !== null) return null;
  } else if (row.status !== "unavailable" || [length, net, ratio].some(value => value !== null)
    || window && !hipPreHelperReasons.has(row.reason) && !hipHelperReasons.has(row.reason)) return null;
  if (row.status === "unavailable" && hipPreHelperReasons.has(row.reason) && (bindings.length || row.source_reference !== null)) return null;
  if (hipHelperReasons.has(row.reason) && (!bindings.length || !sha256(bounded.adapted_primary))) return null;
  if (row.reason === "contextual_measurement_failed" && (bindings.length || row.source_reference !== null
    || ["records", "original_primary", "adapted_primary"].some(key => bounded[key] !== null))) return null;
  return { type: "hip_path_ratio_2d", version: "hip-contextual-measurement-v1.0.0", helper_version: "bounded-hip-path-ratio-v1.0.0",
    event_id: row.event_id, event_code: row.event_code as HipContextualMeasurement["event_code"], video_id: row.video_id, source_id: row.source_id,
    video_sha256: row.video_sha256 as string, person_track_id: row.person_track_id, status: row.status as HipContextualMeasurement["status"], reason: row.reason,
    hip_path_length_px: length as number | null, hip_net_displacement_px: net as number | null, hip_path_over_net_ratio: ratio as number | null,
    parent_window_ms: { start_ms: parent.start_ms, end_ms: parent.end_ms }, measurement_window_video_ms: window ? { start_ms: parent.start_ms, end_ms: parent.end_ms } : null,
    source_frame_bounds: window ? { start_source_frame: bounds.start_source_frame as number, end_source_frame: bounds.end_source_frame as number } : null,
    source_window: window, source_bindings: bindings,
    source_reference: row.source_reference === null ? null : { video_sha256: row.video_sha256 as string, primary_player_id: row.person_track_id,
      person_track_id: reference.person_track_id as number, selection_epoch: reference.selection_epoch as number, camera_reference_epoch: reference.camera_reference_epoch as number },
    adapter: { ...hipAdapter }, input_sha256: { frames: inputs.frames as string, primary_timeline: inputs.primary_timeline as string, events: inputs.events as string },
    window_input_sha256: { records: bounded.records as string | null, original_primary: bounded.original_primary as string | null, adapted_primary: bounded.adapted_primary as string | null, event: bounded.event as string }, ...hipNeutral };
}

/** This additive list never enters the existing source-card states or counts. */
export function contextualHipMeasurements(result: DemoResultResponse | null): HipContextualMeasurement[] {
  const assessment = record(result?.source_aligned_assessment);
  if (assessment.version !== SOURCE_ASSESSMENT_VERSION || assessment.status !== "available" && assessment.status !== "unavailable"
    || assessment.score_semantics !== "source_aligned_measurement_not_technical_grade" || assessment.technical_grade !== null
    || "technical_score_0_to_100" in assessment && assessment.technical_score_0_to_100 !== null
    || !Array.isArray(assessment.contextual_measurements)) return [];
  const rows = assessment.contextual_measurements.map(row => hipMeasurementOf(row, assessment)).filter((row): row is HipContextualMeasurement => row !== null);
  const counts = new Map<string, number>(); rows.forEach(row => counts.set(row.event_id, (counts.get(row.event_id) ?? 0) + 1));
  return rows.filter(row => counts.get(row.event_id) === 1);
}

export function contextualHipForEpisode(rows: HipContextualMeasurement[], episode: FootworkReview["episodes"][number]): HipContextualMeasurement | null {
  if (!hipIdentity(episode.source_id)) return null;
  const matching = rows.filter(row => row.event_id === episode.event_id && row.event_code === episode.event_code
    && row.video_id === episode.video_id && row.source_id === episode.source_id && row.person_track_id === episode.person_track_id
    && row.parent_window_ms.start_ms === episode.start_ms && row.parent_window_ms.end_ms === episode.end_ms);
  if (matching.length !== 1) return null;
  const row = matching[0], source = record(episode), window = hipWindowOf(source.source_window);
  if (row.source_window ? !window || JSON.stringify(row.source_window) !== JSON.stringify(window)
    : window !== null || !sameSourceVideoSha256(record(source.source_window).video_sha256, row.video_sha256)) return null;
  return row;
}

export function contextualHipReason(reason: string): string {
  const labels: Record<string, string> = {
    bounded_image_plane_geometry_only: "已计算完整候选窗口的二维髋中心路径；不确认动作类型或技术等级。",
    zero_net_displacement: "起终点净位移为 0，路径/净位移比不可计算；不记作 0 分。",
    contextual_measurement_failed: "本窗口的独立测量计算未完成，未生成数值。",
    contextual_event_source_window_unavailable: "未取得完整且可核验的原帧窗口。",
    hip_outside_original_frame: "窗口内有髋部点位超出原画面，整窗测量不可用。",
    hip_geometry_unavailable: "完整窗口内缺少可用髋部几何数据。",
    hip_model_geometry_unavailable: "完整窗口内缺少可用髋部模型几何数据。",
    contextual_source_window_incomplete: "原始帧未覆盖完整候选窗口。",
    contextual_source_binding_unavailable: "原帧来源绑定不可核验。",
    contextual_native_adapter_rejected: "原帧与主体连续性绑定未通过核验。",
  };
  return labels[reason] ?? "本窗口未通过完整测量条件，未生成数值。";
}

/** Re-export only measurements that pass the same source and time checks as the UI. */
export function sourceAssessmentForExport(result: DemoResultResponse | null) {
  const assessment = record(result?.source_aligned_assessment);
  const contextual = contextualHipMeasurements(result);
  if (assessment.version !== SOURCE_ASSESSMENT_VERSION || (assessment.status !== "available" && !(assessment.status === "unavailable" && contextual.length))
    || assessment.score_semantics !== "source_aligned_measurement_not_technical_grade" || assessment.technical_grade !== null
    || !Array.isArray(assessment.indicators)) return undefined;
  const ids = [...new Set(assessment.indicators.map(record).map(row => text(row.indicator_id, 128)).filter(Boolean))];
  return { version: SOURCE_ASSESSMENT_VERSION, status: assessment.status, score_semantics: "source_aligned_measurement_not_technical_grade", technical_grade: null,
    ...(sha256(assessment.video_sha256) ? { video_sha256: assessment.video_sha256 } : {}),
    ...(hipIdentity(assessment.video_id) ? { video_id: assessment.video_id } : {}),
    ...(hipIdentity(assessment.source_id) ? { source_id: assessment.source_id } : {}),
    contextual_measurements: contextual,
    indicators: ids.flatMap(indicator_id => {
      const windows = sourceReviewWindows(result, indicator_id), coverage = sourceReviewCoverage(result, indicator_id);
      return windows.length ? [{ indicator_id, window_count: coverage.total, returned_window_count: windows.length, is_truncated: coverage.truncated,
        ...(coverage.countsScope === "all" ? { measured_window_count: coverage.measured, unavailable_window_count: coverage.unavailable } : {}),
        windows: windows.map(window => ({ ...window, visibility: { ...window.visibility, threshold_ratio: .7, scope: "indicator_window" } })) }] : [];
    }) };
}

/** User-entered seconds must stay exact to milliseconds; blank is not zero. */
export function reviewInterval(start: string, end: string, durationMs: number | null): { start_ms: number; end_ms: number } | null {
  const parse = (value: string) => /^\d+(?:\.\d{1,3})?$/.test(value.trim()) ? Number(value.trim()) * 1000 : NaN;
  const startMs = parse(start), endMs = parse(end);
  if (!finite(startMs) || !finite(endMs) || startMs < 0 || endMs <= startMs
    || durationMs !== null && endMs > durationMs) return null;
  return { start_ms: Math.round(startMs), end_ms: Math.round(endMs) };
}
