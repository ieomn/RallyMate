import { sourceMeasurementDetailsOf, type SourceMeasurementDetails } from "./source-measurement-details";
import { hasModelMeasurementContext } from "./model-measurement";

export const INDICATOR_STATUSES: Record<string, string> = {
  source_rule_conflict: "动作要求待明确", source_boundaries_missing: "阶段边界不完整",
  measured_partial_rule: "已取得部分动作测量", legacy_proxy_measured: "已有历史代理测量",
  contextual_measurement: "已有相关动作测量", event_type_unresolved: "动作类型待确认",
  evidence_insufficient: "当前证据不足", not_observed: "本次未观测",
};
export type IndicatorObservation = SourceMeasurementDetails & {
  observation_id: string; source: string; status: "measured" | "partial"; feature_name: string; label_zh: string;
  value: number | null; unit: string; episode_id?: string; event_id?: string; start_ms: number | null; end_ms: number | null;
  statistics: Record<string, number>; definition_zh: string; scope: string;
  windows: Array<{ start_ms: number; end_ms: number; statistics: Record<string, number>; reference: Record<string, string | number | boolean> }>;
  person_track_id?: number; phase_label_zh?: string;
  condition?: { status: string; label_zh: string; semantics: "arithmetic_observation_not_technical_grade" };
};
export type AssessedIndicator = {
  indicator_id: string; name_zh: string; event_code: string; stage_code: string; status: string; status_label_zh: string;
  explanation_zh: string; calculation_zh: string; observation_ids: string[]; blockers: Array<{ code: string; message_zh: string }>;
  source: { document_id: string; sha256: string; table: string | number | null; rubric_sha256: string };
  grade: { status: string; label_zh: string; reason_zh: string;
    events?: { event_id: string; grade: string; rule_version: string }[]; grade_counts?: Record<string, number> };
  observation_count: number; returned_observation_count: number; observations_truncated: boolean;
};
export type IndicatorAssessment = {
  version: "word-indicator-assessment-v1.0.0"; summary_zh: string; limitations_zh: string[];
  source_binding: { reference_version: string; source_documents: Array<{ id: string; sha256: string }> };
  indicators: AssessedIndicator[]; observations: IndicatorObservation[];
  full_observation_count: number; returned_observation_count: number; is_truncated: boolean; selection_policy: string;
  observation_selection: { reason_zh: string; count_scope: string; source_input_already_truncated: boolean };
};
const record = (value: unknown): Record<string, unknown> => value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
const text = (value: unknown): string => typeof value === "string" ? value.trim() : "";
const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const list = (value: unknown): unknown[] => Array.isArray(value) ? value : [];

/** Imports use the same explicit contract as live reports; opaque numbers never become grades. */
export function indicatorAssessmentOf(value: unknown): IndicatorAssessment | null {
  const raw = record(value);
  if (raw.version !== "word-indicator-assessment-v1.0.0" || !Array.isArray(raw.indicators)) return null;
  const seenObservations = new Set<string>();
  const observations: IndicatorObservation[] = list(raw.observations).flatMap(value => {
    const item = record(value), id = text(item.observation_id);
    if (!id || seenObservations.has(id) || !["measured", "partial"].includes(text(item.status)) || !["motion_joint_measurement", "source_aligned", "legacy_feature"].includes(text(item.source)) || !text(item.label_zh)) return [];
    const details = sourceMeasurementDetailsOf(item);
    if (["source_aligned", "legacy_feature"].includes(String(item.source)) && hasModelMeasurementContext(item)) {
      const frames = details.source_window?.source_frames as number[] | undefined;
      if (!details.measurement_provenance || !frames || !details.source_frames?.length
        || !details.source_frames.every(frame => frames.includes(frame))
        || item.start_ms !== details.source_window?.start_ms || item.end_ms !== details.source_window?.end_ms
        || item.source === "source_aligned" && (!details.model_geometry_coverage?.passes
          || details.source_frames.length !== frames.length)) return [];
    }
    const windowValid = finite(item.start_ms) && finite(item.end_ms) && item.start_ms >= 0 && item.end_ms >= item.start_ms;
    const statisticKeys = ["start", "end", "delta", "min", "max", "median", "mean", "range"];
    const windows = list(item.windows).map(record).map(window => ({ ...window, ...record(window.statistics) })).filter(window => windowValid && finite(window.start_ms) && finite(window.end_ms) && window.start_ms >= Number(item.start_ms) && window.end_ms <= Number(item.end_ms) && window.end_ms > window.start_ms && statisticKeys.every(key => finite(window[key])) && Math.abs(Number(window.end) - Number(window.start) - Number(window.delta)) <= .0001).map(window => ({ start_ms: Number(window.start_ms), end_ms: Number(window.end_ms), statistics: Object.fromEntries(statisticKeys.map(key => [key, Number(window[key])])), reference: Object.fromEntries(Object.entries(record(window.reference)).filter(([key, value]) => ["person_track_id", "selection_epoch", "camera_reference_epoch", "camera_compensated", "normalization_basis"].includes(key) && (finite(value) || typeof value === "boolean" || typeof value === "string"))) as Record<string, string | number | boolean> }));
    if (!finite(item.value) && !(item.status === "partial" && windows.length > 0)) return [];
    seenObservations.add(id);
    const condition = record(item.condition);
    return [{ observation_id: id, source: text(item.source), status: item.status as "measured" | "partial", feature_name: text(item.feature_name), label_zh: text(item.label_zh), value: finite(item.value) ? item.value : null, unit: text(item.unit),
      ...(text(item.episode_id) ? { episode_id: text(item.episode_id) } : {}), ...(text(item.event_id) ? { event_id: text(item.event_id) } : {}),
      start_ms: windowValid ? Number(item.start_ms) : null, end_ms: windowValid ? Number(item.end_ms) : null,
      statistics: Object.fromEntries(Object.entries(record(item.statistics)).filter(([key, value]) => statisticKeys.includes(key) && finite(value) && !(item.status === "partial" && ["start", "end", "delta"].includes(key)))) as Record<string, number>, windows,
      definition_zh: text(item.definition_zh), scope: text(item.scope),
      ...(Number.isSafeInteger(item.person_track_id) && Number(item.person_track_id) >= 0 ? { person_track_id: Number(item.person_track_id) } : {}),
      ...(text(item.phase_label_zh) ? { phase_label_zh: text(item.phase_label_zh) } : {}),
      ...details,
      ...(condition.semantics === "arithmetic_observation_not_technical_grade" && text(condition.label_zh) ? { condition: { status: text(condition.status), label_zh: text(condition.label_zh), semantics: "arithmetic_observation_not_technical_grade" as const } } : {}),
    }];
  });
  const seenIndicators = new Set<string>();
  const indicators: AssessedIndicator[] = raw.indicators.flatMap(value => {
    const item = record(value), id = text(item.indicator_id), source = record(item.source), grade = record(item.grade);
    if (!/^(?:FS\d{2}-M\d{2}|GS\d{2}-M\d{2}-\d{2})$/.test(id) || seenIndicators.has(id) || !text(item.name_zh) || !INDICATOR_STATUSES[text(item.status)]) return [];
    seenIndicators.add(id);
    const observationIds = list(item.observation_ids).map(text).filter(id => seenObservations.has(id));
    const observationCount = Number.isSafeInteger(item.observation_count) && Number(item.observation_count) >= observationIds.length ? Number(item.observation_count) : observationIds.length;
    const blockers = list(item.blockers).map(record).filter(row => text(row.message_zh)).map(row => ({ code: text(row.code), message_zh: text(row.message_zh) }));
    const unsupportedType = blockers.some(row => row.code === "event_type_recognizer_not_implemented");
    const proxyCandidates = grade.status === "proxy_scored" && grade.semantics === "explicit_numeric_proxy_not_complete_source_grade"
      ? list(grade.events).map(record).filter(row => text(row.event_id) && /^[ABCDE]$/.test(text(row.grade))
        && text(row.rule_version) && record(row.rule).indicator_id === id) : [];
    const proxyByEvent = new Map<string, { event_id: string; grade: string; rule_version: string }>();
    const conflictingEvents = new Set<string>();
    for (const row of proxyCandidates) {
      const event = { event_id: text(row.event_id), grade: text(row.grade), rule_version: text(row.rule_version) };
      const prior = proxyByEvent.get(event.event_id);
      if (prior && (prior.grade !== event.grade || prior.rule_version !== event.rule_version)) conflictingEvents.add(event.event_id);
      proxyByEvent.set(event.event_id, event);
    }
    const proxyEvents = [...proxyByEvent.values()].filter(row => !conflictingEvents.has(row.event_id));
    const proxyCounts = Object.fromEntries(["A", "B", "C", "D", "E"].map(level => [level, proxyEvents.filter(row => row.grade === level).length]));
    const statusLabel = unsupportedType && ["event_type_unresolved", "not_observed"].includes(text(item.status))
      ? "算法尚未支持该类型" : INDICATOR_STATUSES[text(item.status)];
    return [{ indicator_id: id, name_zh: text(item.name_zh), event_code: text(item.event_code), stage_code: text(item.stage_code), status: text(item.status), status_label_zh: statusLabel, explanation_zh: text(item.explanation_zh), calculation_zh: text(item.calculation_zh),
      observation_ids: observationIds, observation_count: observationCount, returned_observation_count: observationIds.length, observations_truncated: observationCount > observationIds.length,
      blockers,
      source: { document_id: text(source.document_id), sha256: text(source.sha256), table: /^T\d+$/.test(text(source.table)) ? text(source.table) : Number.isSafeInteger(source.table) && Number(source.table) >= 0 ? Number(source.table) : null, rubric_sha256: text(source.rubric_sha256) },
      grade: proxyEvents.length > 0 ? { status: "proxy_scored", label_zh: "数值代理等级", reason_zh: text(grade.reason_zh), events: proxyEvents, grade_counts: proxyCounts }
        : { status: ["calibration_required", "source_rule_conflict"].includes(text(grade.status)) ? text(grade.status) : "calibration_required", label_zh: "技术等级尚未判定", reason_zh: text(grade.reason_zh) },
    }];
  });
  const binding = record(raw.source_binding);
  const selection = record(raw.observation_selection), counts = record(raw.counts);
  const total = raw.full_observation_count ?? selection.total_count ?? counts.observations;
  const fullCount = Number.isSafeInteger(total) && Number(total) >= observations.length ? Number(total) : observations.length;
  return { version: "word-indicator-assessment-v1.0.0", summary_zh: text(raw.summary_zh), limitations_zh: list(raw.limitations_zh).map(text).filter(Boolean), source_binding: { reference_version: text(binding.reference_version), source_documents: list(binding.source_documents).map(record).map(row => ({ id: text(row.id), sha256: text(row.sha256) })) }, indicators, observations,
    full_observation_count: fullCount, returned_observation_count: observations.length, is_truncated: fullCount > observations.length, selection_policy: text(raw.selection_policy) || text(selection.strategy),
    observation_selection: { reason_zh: text(selection.reason_zh), count_scope: text(selection.count_scope), source_input_already_truncated: selection.source_input_already_truncated === true },
  };
}
