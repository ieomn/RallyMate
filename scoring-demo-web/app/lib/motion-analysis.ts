import type { DemoResultResponse, MotionAnalysis, MotionEpisode, MotionFamily, RotationAnalysis, TechniqueAssessmentResponse } from "./api-types";

const record = (value: unknown): Record<string, unknown> => value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const strings = (value: unknown): string[] => Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];

/** Render measured motion separately from contact claims and old proposal lists. */
export function motionAnalysisOf(result: DemoResultResponse | null, assessment?: TechniqueAssessmentResponse | null, summary?: Record<string, unknown> | null): MotionAnalysis | null {
  const recognition = record(result?.action_recognition ?? assessment?.action_recognition ?? summary?.action_recognition);
  const raw = record(recognition.motion_analysis);
  if (raw.schema_version !== "1.0.0" || raw.contact_confirmed !== false || !raw.families) return null;
  const families: MotionAnalysis["families"] = {};
  for (const family of ["baseline", "serve", "return"] as const) {
    const data = record(record(raw.families)[family]);
    if (!Object.keys(data).length) continue;
    const ids = new Set<string>();
    const episodes: MotionEpisode[] = [];
    for (const value of Array.isArray(data.episodes) ? data.episodes : []) {
      const episode = record(value), classification = record(episode.classification);
      if (episode.family !== family || episode.contact_confirmed !== false || typeof episode.episode_id !== "string" || ids.has(episode.episode_id)) continue;
      if (![episode.start_ms, episode.peak_ms, episode.end_ms].every(finite) || Number(episode.start_ms) < 0 || Number(episode.start_ms) > Number(episode.peak_ms) || Number(episode.peak_ms) > Number(episode.end_ms) || Number(episode.end_ms) <= Number(episode.start_ms)) continue;
      if (!["rule_inferred", "unclassified"].includes(String(classification.status)) || typeof classification.label_zh !== "string") continue;
      ids.add(episode.episode_id);
      const typed = episode as unknown as MotionEpisode;
      const phases: MotionEpisode["phases"] = [];
      for (const phase of Array.isArray(typed.phases) ? typed.phases : []) {
        if (!phase || typeof phase.phase !== "string" || typeof phase.label_zh !== "string") continue;
        if (phase.status === "unavailable") { phases.push({ ...phase, start_ms: null, end_ms: null }); continue; }
        if (phase.status && phase.status !== "measured") continue;
        if (finite(phase.start_ms) && finite(phase.end_ms) && phase.start_ms >= typed.start_ms && phase.end_ms <= typed.end_ms && phase.end_ms > phase.start_ms) phases.push({ ...phase, status: "measured" });
      }
      const evidence = record(typed.evidence);
      episodes.push({ ...typed, classification: { ...typed.classification, reason_zh: typeof classification.reason_zh === "string" ? classification.reason_zh : "类型由运动规则推断，需结合回放复核。" }, analysis_status: typed.analysis_status === "partial" || phases.some(phase => phase.status === "unavailable") ? "partial" : typed.analysis_status === "complete" ? "complete" : undefined, phases, metrics: { ...record(typed.metrics) }, evidence: { pose_samples: finite(evidence.pose_samples) ? evidence.pose_samples : undefined, racket_associated_frames: finite(evidence.racket_associated_frames) ? evidence.racket_associated_frames : undefined }, limitations_zh: strings(typed.limitations_zh), metric_notes_zh: strings(typed.metric_notes_zh) });
      const parsed = episodes[episodes.length - 1];
      parsed.rotation_analysis = rotationAnalysisOf(episode.rotation_analysis, parsed);
      if (episode.rotation_analysis !== undefined && !parsed.rotation_analysis) for (const { key } of ROTATION_METRICS) parsed.metrics[key] = null;
      if (parsed.rotation_analysis) for (const { key } of ROTATION_METRICS) {
        if (parsed.rotation_analysis.metric_evidence[key].status !== "measured") parsed.metrics[key] = null;
      }
    }
    episodes.sort((a, b) => a.start_ms - b.start_ms);
    families[family] = { status: episodes.length ? "analyzed" : "insufficient_evidence", reason_zh: typeof data.reason_zh === "string" ? data.reason_zh : "当前视频没有满足分析条件的连续运动证据。", episodes, summary: data.summary as MotionFamily["summary"] };
  }
  return { schema_version: "1.0.0", analysis_version: String(raw.analysis_version ?? ""), method: String(raw.method ?? ""), status: Object.values(families).some(family => family.episodes.length) ? "available" : "insufficient_evidence", contact_confirmed: false, families, limitations_zh: strings(raw.limitations_zh) };
}

export const MOTION_METRICS = [
  { key: "peak_wrist_speed_torso_per_s", label: "手腕峰值速度", unit: "躯干长度/秒" },
  { key: "wrist_path_torso", label: "手腕运动距离", unit: "躯干长度" },
  { key: "elbow_extension_deg", label: "肘角变化幅度", unit: "°" },
  { key: "shoulder_line_change_deg", label: "画面内肩线变化", unit: "°" },
] as const;

export const ROTATION_METRICS = [
  { key: "shoulder_line_change_deg", label: "画面内肩线变化", unit: "°" },
  { key: "hip_line_change_deg", label: "画面内髋线变化", unit: "°" },
  { key: "shoulder_hip_separation_max_deg", label: "肩髋线最大夹角", unit: "°" },
  { key: "shoulder_hip_separation_change_deg", label: "肩髋线夹角变化", unit: "°" },
  { key: "peak_shoulder_angular_speed_deg_s", label: "肩线峰值角速度", unit: "°/秒" },
  { key: "peak_hip_angular_speed_deg_s", label: "髋线峰值角速度", unit: "°/秒" },
] as const;

export function rotationAnalysisOf(value: unknown, episode: MotionEpisode): RotationAnalysis | undefined {
  const raw = record(value);
  if (raw.is_3d_rotation !== false || raw.is_formal_coach_score !== false || raw.score !== null || !["measured_2d", "partial", "unavailable"].includes(String(raw.status))) return undefined;
  const metric_evidence: RotationAnalysis["metric_evidence"] = {};
  for (const { key } of ROTATION_METRICS) {
    const item = record(record(raw.metric_evidence)[key]);
    const coverage = finite(item.coverage_fraction) && item.coverage_fraction >= 0 && item.coverage_fraction <= 1 ? item.coverage_fraction : 0;
    const measured = raw.status !== "unavailable" && item.status === "measured" && coverage >= 0.8 && finite(episode.metrics[key]) && Number(episode.metrics[key]) >= 0
      && finite(item.time_coverage_fraction) && item.time_coverage_fraction >= 0.8 && item.time_coverage_fraction <= 1
      && Number.isSafeInteger(item.continuous_samples) && Number(item.continuous_samples) >= 7
      && Number.isSafeInteger(item.total_samples) && Number.isSafeInteger(item.valid_samples)
      && Number(item.continuous_samples) <= Number(item.valid_samples) && Number(item.valid_samples) <= Number(item.total_samples)
      && finite(item.start_ms) && finite(item.end_ms) && item.start_ms >= episode.start_ms && item.end_ms <= episode.end_ms && item.end_ms - item.start_ms >= 200;
    metric_evidence[key] = { status: measured ? "measured" : "unavailable", coverage_fraction: coverage, start_ms: measured ? Number(item.start_ms) : null, end_ms: measured ? Number(item.end_ms) : null, reason_zh: typeof item.reason_zh === "string" ? item.reason_zh : "缺少连续可测证据。" };
  }
  const count = Object.values(metric_evidence).filter(item => item.status === "measured").length;
  return { status: count === ROTATION_METRICS.length ? "measured_2d" : count ? "partial" : "unavailable", is_3d_rotation: false, is_formal_coach_score: false, score: null, score_status: count ? "calibration_required" : "insufficient_evidence", metric_evidence, limitations_zh: strings(raw.limitations_zh) };
}

export function motionMetricText(value: unknown): string {
  return finite(value) && value >= 0 ? value.toFixed(2) : "—";
}
