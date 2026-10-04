import type { AnalysisReport, DemoResultResponse, MotionAnalysis } from "./api-types";
import { footworkEpisodes } from "./footwork-review";
import { hasMeasurement } from "./measurement-evidence";

export type ReportMoment = { id: string; label: string; kind: "footwork" | "rotation"; startMs: number; endMs: number; note: string };
export type ReportFocus = { id: string; title: string; detail: string; label: string; moment?: ReportMoment };

const record = (value: unknown): Record<string, unknown> => value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const string = (value: unknown): string => typeof value === "string" ? value : "";
/** Imports and future server versions must explicitly preserve the non-technical contract. */
export function analysisReportOf(value: unknown): AnalysisReport | null {
  const raw = record(value);
  if (raw.version !== "training-report-v1.0.0" || raw.score_semantics !== "measurement_evidence_quality" || raw.technical_score_0_to_100 !== null || raw.recommendation_semantics !== "evidence_linked_review_and_capture_not_technical_error_diagnosis") return null;
  const ids = new Set<string>();
  const focus_areas: AnalysisReport["focus_areas"] = [];
  for (const row of Array.isArray(raw.focus_areas) ? raw.focus_areas : []) {
    const item = record(row);
    if (!string(item.id) || ids.has(String(item.id)) || !string(item.title_zh) || !string(item.summary_zh) || !["review", "capture"].includes(String(item.kind)) || !["available", "partial", "unavailable"].includes(String(item.status)) || item.is_technical_error_diagnosis !== false) continue;
    ids.add(String(item.id));
    const validWindow = finite(item.start_ms) && finite(item.end_ms) && item.start_ms >= 0 && item.end_ms > item.start_ms;
    focus_areas.push({ id: String(item.id), title_zh: String(item.title_zh), summary_zh: String(item.summary_zh), basis_zh: string(item.basis_zh), kind: item.kind as "review" | "capture", status: item.status as "available" | "partial" | "unavailable", start_ms: validWindow ? Number(item.start_ms) : null, end_ms: validWindow ? Number(item.end_ms) : null, metric_ids: Array.isArray(item.metric_ids) ? item.metric_ids.filter((id): id is string => typeof id === "string") : [], is_technical_error_diagnosis: false });
  }
  const summary = record(raw.measurement_summary);
  const count = (key: string) => Number.isSafeInteger(summary[key]) && Number(summary[key]) >= 0 ? Number(summary[key]) : 0;
  return {
    version: "training-report-v1.0.0", headline_zh: string(raw.headline_zh), score_semantics: "measurement_evidence_quality",
    reference_score_0_to_100: finite(raw.reference_score_0_to_100) && raw.reference_score_0_to_100 >= 0 && raw.reference_score_0_to_100 <= 100 ? raw.reference_score_0_to_100 : null,
    technical_score_0_to_100: null, recommendation_semantics: "evidence_linked_review_and_capture_not_technical_error_diagnosis", focus_areas: focus_areas.slice(0, 3),
    layers: (Array.isArray(raw.layers) ? raw.layers : []).map(record).filter(item => string(item.id) && string(item.label_zh)).map(item => ({ id: String(item.id), label_zh: String(item.label_zh), status: string(item.status), reason_zh: string(item.reason_zh), source: string(item.source) })),
    measurement_summary: { measured_indicator_count: count("measured_indicator_count"), footwork_measured_feature_instances: count("footwork_measured_feature_instances"), footwork_missing_feature_instances: count("footwork_missing_feature_instances"), rotation_measured_metric_instances: count("rotation_measured_metric_instances"), rotation_local_window_count: count("rotation_local_window_count"), scope: "returned_replay_episodes", is_truncated: summary.is_truncated === true },
  };
}

/** Only validated episode boundaries can become replay controls. */
export function reportMoments(result: DemoResultResponse | null, motion?: MotionAnalysis | null): ReportMoment[] {
  const footwork = footworkEpisodes(result?.footwork_review).map(episode => ({
    id: `footwork:${episode.event_id}`, label: episode.name_zh, kind: "footwork" as const,
    startMs: episode.start_ms, endMs: episode.end_ms, note: "步伐规则候选",
  }));
  const rotations = Object.values(motion?.families ?? {}).flatMap(family => family?.episodes ?? []).filter(episode =>
    Number.isFinite(episode.start_ms) && Number.isFinite(episode.end_ms) && episode.start_ms >= 0 && episode.end_ms > episode.start_ms,
  ).map(episode => ({
    id: `rotation:${episode.episode_id}`, label: episode.classification.label_zh, kind: "rotation" as const,
    startMs: episode.start_ms, endMs: episode.end_ms, note: "挥拍运动 · 触球未确认",
  }));
  return [...footwork, ...rotations].sort((a, b) => a.startMs - b.startMs || a.id.localeCompare(b.id));
}

export function reportFocus(result: DemoResultResponse | null, motion?: MotionAnalysis | null): ReportFocus[] {
  if (!result) return [];
  const report = analysisReportOf(result.analysis_report);
  const moments = reportMoments(result, motion);
  if (report?.focus_areas.length) return report.focus_areas.map(item => ({
    id: item.id, title: item.title_zh, detail: item.summary_zh, label: item.kind === "review" ? "本次复核重点" : "下次拍摄建议",
    moment: item.start_ms !== null && item.end_ms !== null && moments.some(moment => item.start_ms! >= moment.startMs && item.end_ms! <= moment.endMs) ? { id: item.id, label: item.title_zh, kind: item.id.startsWith("footwork") ? "footwork" : "rotation", startMs: item.start_ms, endMs: item.end_ms, note: item.basis_zh } : undefined,
  }));
  const indicators = result.training_evaluation?.indicator_evaluations ?? [];
  const measuredFootwork = indicators.filter(item => (item.event_code ?? item.indicator_id).startsWith("FS") && hasMeasurement(item));
  const rotationEpisodes = Object.values(motion?.families ?? {}).flatMap(family => family?.episodes ?? []);
  const measuredRotation = rotationEpisodes.some(episode => Object.values(episode.rotation_analysis?.metric_evidence ?? {}).some(item => item.status === "measured"));
  return [
    { id: "footwork", title: measuredFootwork.length ? "步伐：先回看启动与制动" : "步伐：补足脚部连续画面", label: measuredFootwork.length ? "本次复核重点" : "下次拍摄建议", detail: measuredFootwork.length ? "对照候选片段检查双脚移动和身体停稳的顺序。已有测量可逐项查看，暂不据此判定脚步好坏。" : "让双脚与身体持续留在画面内，保留动作前后的准备和恢复，便于分别测量启动、调整与制动。", moment: moments.find(item => item.kind === "footwork") },
    { id: "rotation", title: measuredRotation ? "转体：对照准备与随挥" : "转体：保持肩髋清晰可见", label: measuredRotation ? "本次复核重点" : "下次拍摄建议", detail: measuredRotation ? "结合动作阶段回看肩线和髋线的变化。二维投影可帮助定位复核区间，不能代表真实三维转体角度。" : "固定相机，尽量减少肩部与髋部遮挡。肩线或髋线不可测时，其余可测指标仍会保留。", moment: moments.find(item => item.kind === "rotation") },
  ];
}

export function replayMoment(moment: Pick<ReportMoment, "startMs">, jobId?: string) {
  window.dispatchEvent(new CustomEvent("rallymate:seek-video", { detail: { timestampMs: moment.startMs, jobId } }));
  document.getElementById("evidence")?.scrollIntoView({ behavior: "smooth", block: "start" });
}

export type RecentReport = { id: string; name: string; viewedAt: string };
export function recentReports(value: unknown): RecentReport[] {
  if (!Array.isArray(value)) return [];
  const ids = new Set<string>();
  return value.filter((item): item is RecentReport => {
    if (!item || typeof item !== "object" || typeof item.id !== "string" || !/^[a-zA-Z0-9_-]{8,80}$/.test(item.id) || ids.has(item.id) || typeof item.name !== "string" || typeof item.viewedAt !== "string" || !Number.isFinite(Date.parse(item.viewedAt))) return false;
    ids.add(item.id); return true;
  }).slice(0, 12).map(item => ({ id: item.id, name: item.name.slice(0, 160), viewedAt: item.viewedAt }));
}
