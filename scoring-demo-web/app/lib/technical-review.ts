import type { DemoResultResponse } from "./api-types";
import { reportVideoDurationMs } from "./workspace-navigation";

export const SOURCE_ASSESSMENT_VERSION = "source-aligned-assessment-v1.0.0";
export const REVIEW_SOURCE_ID = "scoring-reference-20261004";
export const TECHNICAL_GRADES = ["A", "B", "C", "D", "E"] as const;
export type TechnicalGrade = typeof TECHNICAL_GRADES[number];
export type SourceMeasurement = { feature_name: string; label_zh: string; value: number | null; unit: string; status: "measured" | "unavailable"; reason_zh: string; source_requirement_zh: string; measurement_window: { start_ms: number; end_ms: number } | null };
export type SourceVisibility = { status: string; valid_frame_ratio: number | null; valid_frame_count: number | null; total_frame_count: number | null; required_joint_ids: string[]; reason_zh: string };
export type SourceReviewWindow = { start_ms: number; end_ms: number; event_id: string; person_track_id: number | null; measurements: SourceMeasurement[]; visibility: SourceVisibility; limitations_zh: string[] };
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
const uuid = (value: unknown) => typeof value === "string" && /^[a-f0-9]{8}-[a-f0-9]{4}-[1-5][a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/i.test(value);

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
    const ratio = visibility.valid_frame_ratio, valid = visibility.valid_frame_count, total = visibility.total_frame_count;
    const validVisibility = visibility.scope === "indicator_window" && visibility.threshold_ratio === .7
      && ["sufficient", "insufficient"].includes(String(visibility.status))
      && finite(ratio) && ratio >= 0 && ratio <= 1 && count(valid) && count(total) && total > 0 && valid <= total
      && Math.abs(ratio - valid / total) <= 1e-6 && strings(visibility.required_joint_ids).length > 0;
    const measuredNames = new Set<string>();
    const measurements = window.measurements.flatMap(value => {
      const item = record(value), feature = text(item.feature_name, 160), label = text(item.label_zh, 200);
      if (!feature || !label || measuredNames.has(feature)) return [];
      measuredNames.add(feature);
      const measured = item.status === "measured" && finite(item.value) && !!text(item.unit, 80);
      const measurementWindow = record(item.measurement_window);
      const sameFrameTransition = measured && item.value === 0 && ["landing_proxy_to_next_fs02_ms", "stable_control_proxy_to_next_fs10_or_fs02_ms"].includes(feature);
      const validMeasurementWindow = finite(measurementWindow.start_ms) && finite(measurementWindow.end_ms)
        && measurementWindow.start_ms >= 0 && (measurementWindow.end_ms > measurementWindow.start_ms || sameFrameTransition && measurementWindow.end_ms === measurementWindow.start_ms)
        && (duration === null || measurementWindow.end_ms <= duration);
      return [{ feature_name: feature, label_zh: label, value: measured ? item.value as number : null, unit: text(item.unit, 80),
        status: measured ? "measured" as const : "unavailable" as const, reason_zh: text(item.reason_zh), source_requirement_zh: text(item.source_requirement_zh),
        measurement_window: validMeasurementWindow ? { start_ms: measurementWindow.start_ms as number, end_ms: measurementWindow.end_ms as number } : null }];
    });
    return [{ start_ms: window.start_ms, end_ms: window.end_ms, event_id: window.event_id as string,
      person_track_id: count(window.person_track_id) ? window.person_track_id : null, measurements,
      visibility: { status: validVisibility ? text(visibility.status, 80) : "unavailable", valid_frame_ratio: validVisibility ? ratio : null,
        valid_frame_count: validVisibility ? valid : null, total_frame_count: validVisibility ? total : null,
        required_joint_ids: strings(visibility.required_joint_ids), reason_zh: validVisibility ? text(visibility.reason_zh) : "未提供本指标在该时段的可核验关键点可见性。" },
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

/** Re-export only measurements that pass the same source and time checks as the UI. */
export function sourceAssessmentForExport(result: DemoResultResponse | null) {
  const assessment = record(result?.source_aligned_assessment);
  if (assessment.version !== SOURCE_ASSESSMENT_VERSION || assessment.status !== "available"
    || assessment.score_semantics !== "source_aligned_measurement_not_technical_grade" || assessment.technical_grade !== null
    || !Array.isArray(assessment.indicators)) return undefined;
  const ids = [...new Set(assessment.indicators.map(record).map(row => text(row.indicator_id, 128)).filter(Boolean))];
  return { version: SOURCE_ASSESSMENT_VERSION, status: "available", score_semantics: "source_aligned_measurement_not_technical_grade", technical_grade: null,
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
