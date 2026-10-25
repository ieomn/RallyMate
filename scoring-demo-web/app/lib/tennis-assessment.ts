import type { DemoResultResponse } from "./api-types";

export const TENNIS_DIMENSIONS = [
  { id: "movement", label: "移动支撑" }, { id: "preparation", label: "准备引拍" },
  { id: "coordination", label: "挥拍协调" }, { id: "striking", label: "击球姿态" },
  { id: "follow_through", label: "随挥恢复" },
] as const;
export type TennisDimensionId = typeof TENNIS_DIMENSIONS[number]["id"];
export type TennisMetric = { id: string; label: string; value: number | null; unit: string; score: number | null; weight: number; observation_count: number; target: string };
export type TennisDimension = { id: TennisDimensionId; label: string; score: number | null; observation_count: number; metrics: TennisMetric[] };
export type TennisImprovement = { dimension_id: TennisDimensionId; title: string; observation: string; action: string; drill: string; retest: string; start_ms: number | null; end_ms: number | null };
export type TennisEpisode = { episode_id: string; family: "baseline" | "serve" | "return"; start_ms: number; peak_ms: number; end_ms: number };
export type TennisAssessment = {
  version: "tennis-action-assessment-v1.0.0"; status: "scored" | "partial" | "unavailable";
  source_run_id: string; source_video_sha256: string; source_timestamp_semantics: "original_decoder_pts";
  overall_score: number | null; dimensions: TennisDimension[];
  episodes?: TennisEpisode[];
  strengths: { title: string; observation: string }[]; improvements: TennisImprovement[];
  rubric: { version: "tennis-action-rubric-v1.0.0"; semantics: "engineering_action_quality"; coach_calibrated: false;
    identity_labels_used: false; confidence_used_in_score: false; aggregation: "mean_episode_dimension_scores_then_equal_dimension_mean" };
};
export type TennisAssessmentContext = { jobId: string; videoSha256: string; durationMs?: number | null };
const record = (value: unknown): Record<string, unknown> | null => value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;
const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const score = (value: unknown): value is number | null => value === null || finite(value) && value >= 0 && value <= 100;
const count = (value: unknown): value is number => Number.isSafeInteger(value) && Number(value) >= 0;
const text = (value: unknown): value is string => typeof value === "string" && value.length <= 4000;
const hash = (value: unknown): value is string => typeof value === "string" && /^[a-f0-9]{64}$/i.test(value);

function navigationEpisodes(value: unknown, durationMs: number | null | undefined): TennisEpisode[] {
  if (!Array.isArray(value) || !finite(durationMs) || durationMs <= 0) return [];
  const ids = new Map<unknown, number>();
  for (const raw of value) { const id = record(raw)?.episode_id; ids.set(id, (ids.get(id) ?? 0) + 1); }
  const seen = new Set<string>(), episodes: TennisEpisode[] = [];
  for (const raw of value) {
    const item = record(raw);
    if (!item || typeof item.episode_id !== "string" || !/^[A-Za-z0-9_:.-]{1,160}$/.test(item.episode_id) || ids.get(item.episode_id) !== 1
      || !["baseline", "serve", "return"].includes(String(item.family)) || !finite(item.start_ms) || !finite(item.peak_ms) || !finite(item.end_ms)
      || item.start_ms < 0 || item.start_ms >= item.peak_ms || item.peak_ms >= item.end_ms || item.end_ms > durationMs) continue;
    const key = `${item.start_ms}:${item.peak_ms}:${item.end_ms}`;
    if (seen.has(key)) continue;
    seen.add(key);
    episodes.push({ episode_id: item.episode_id, family: item.family as TennisEpisode["family"], start_ms: item.start_ms, peak_ms: item.peak_ms, end_ms: item.end_ms });
  }
  return episodes.sort((a, b) => a.start_ms - b.start_ms || a.end_ms - b.end_ms || a.episode_id.localeCompare(b.episode_id));
}

/** A payload never establishes its own identity: bind it to independent video metadata. */
export function tennisAssessmentContext(result: DemoResultResponse | null): TennisAssessmentContext | null {
  if (!result?.job_id) return null;
  const input = record(record(result.input)?.video);
  const originalHash = result.original_video?.sha256, inputHash = input?.sha256;
  if (originalHash !== undefined && !hash(originalHash) || inputHash !== undefined && !hash(inputHash)) return null;
  if (hash(originalHash) && hash(inputHash) && originalHash.toLowerCase() !== inputHash.toLowerCase()) return null;
  const videoSha256 = originalHash ?? inputHash;
  if (!hash(videoSha256)) return null;
  const duration = input?.duration_ms;
  return { jobId: result.job_id, videoSha256, ...(finite(duration) && duration > 0 ? { durationMs: duration } : {}) };
}

/** Validate before display, persistence, or export; missing measurements never become zero. */
export function validatedTennisAssessment(value: unknown, context: TennisAssessmentContext): TennisAssessment | null {
  const report = record(value), rubric = record(report?.rubric);
  if (!report || !context.jobId || !hash(context.videoSha256) || report.version !== "tennis-action-assessment-v1.0.0"
    || report.source_run_id !== context.jobId || !hash(report.source_video_sha256) || report.source_video_sha256.toLowerCase() !== context.videoSha256.toLowerCase()
    || report.source_timestamp_semantics !== "original_decoder_pts" || !["scored", "partial", "unavailable"].includes(String(report.status))
    || !score(report.overall_score) || !Array.isArray(report.dimensions) || report.dimensions.length !== 5
    || !rubric || rubric.version !== "tennis-action-rubric-v1.0.0" || rubric.semantics !== "engineering_action_quality"
    || rubric.coach_calibrated !== false || rubric.identity_labels_used !== false || rubric.confidence_used_in_score !== false
    || rubric.aggregation !== "mean_episode_dimension_scores_then_equal_dimension_mean"
    || context.durationMs !== undefined && context.durationMs !== null && (!finite(context.durationMs) || context.durationMs <= 0)) return null;
  const dimensions: TennisDimension[] = [];
  for (const spec of TENNIS_DIMENSIONS) {
    const matches = report.dimensions.map(record).filter(item => item?.id === spec.id);
    const dimension = matches[0];
    if (matches.length !== 1 || !dimension || !score(dimension.score) || !count(dimension.observation_count) || !Array.isArray(dimension.metrics)) return null;
    const metrics: TennisMetric[] = [], ids = new Set<string>();
    for (const raw of dimension.metrics) {
      const metric = record(raw);
      if (!metric || !text(metric.id) || !metric.id || ids.has(metric.id) || !text(metric.label) || !text(metric.unit) || !text(metric.target)
        || !(metric.value === null || finite(metric.value)) || !score(metric.score) || !finite(metric.weight) || metric.weight <= 0
        || !count(metric.observation_count) || metric.score !== null && (metric.value === null || metric.observation_count === 0)) return null;
      ids.add(metric.id);
      metrics.push({ id: metric.id, label: metric.label, value: metric.value, unit: metric.unit, score: metric.score,
        weight: metric.weight, observation_count: metric.observation_count, target: metric.target });
    }
    const scoredMetrics = metrics.filter(item => item.score !== null);
    const scoredWeight = scoredMetrics.reduce((sum, item) => sum + item.weight, 0);
    if (dimension.score !== null && (dimension.observation_count === 0 || !scoredMetrics.length || !Number.isFinite(scoredWeight)
      || Math.abs(scoredWeight - 1) > .00001
      || Math.abs(dimension.score - scoredMetrics.reduce((sum, item) => sum + item.score! * item.weight, 0) / scoredWeight) > .15)
      || dimension.score === null && scoredMetrics.length > 0) return null;
    dimensions.push({ id: spec.id, label: spec.label, score: dimension.score, observation_count: dimension.observation_count, metrics });
  }
  const measured = dimensions.filter(item => item.score !== null);
  if (report.status === "scored" && measured.length !== 5 || report.status === "unavailable" && measured.length !== 0
    || report.status === "partial" && (measured.length === 0 || measured.length === 5)) return null;
  if (report.overall_score !== null && (!measured.length || Math.abs(report.overall_score - measured.reduce((sum, item) => sum + item.score!, 0) / measured.length) > .15)) return null;
  if (!measured.length && report.overall_score !== null) return null;
  if (!Array.isArray(report.strengths) || !Array.isArray(report.improvements) || report.improvements.length > 2) return null;
  const strengths: TennisAssessment["strengths"] = [];
  for (const raw of report.strengths) {
    const item = record(raw);
    if (!item || !text(item.title) || !text(item.observation)) return null;
    strengths.push({ title: item.title, observation: item.observation });
  }
  const improvements: TennisImprovement[] = [];
  for (const raw of report.improvements) {
    const item = record(raw), dimension = dimensions.find(d => d.id === item?.dimension_id);
    if (!item || !dimension || ![item.title, item.observation, item.action, item.drill, item.retest].every(text)) return null;
    const start = item.start_ms, end = item.end_ms;
    const hasTime = finite(start) && finite(end) && start >= 0 && end > start && (context.durationMs == null || end <= context.durationMs);
    if (!(start === null && end === null) && !hasTime) return null;
    improvements.push({ dimension_id: dimension.id, title: item.title as string, observation: item.observation as string, action: item.action as string,
      drill: item.drill as string, retest: item.retest as string, start_ms: hasTime ? start as number : null, end_ms: hasTime ? end as number : null });
  }
  return { version: "tennis-action-assessment-v1.0.0", status: report.status as TennisAssessment["status"], source_run_id: context.jobId,
    source_video_sha256: report.source_video_sha256, source_timestamp_semantics: "original_decoder_pts", overall_score: report.overall_score,
    dimensions, strengths, improvements, ...(report.episodes === undefined ? {} : { episodes: navigationEpisodes(report.episodes, context.durationMs) }),
    rubric: { version: "tennis-action-rubric-v1.0.0", semantics: "engineering_action_quality", coach_calibrated: false,
      identity_labels_used: false, confidence_used_in_score: false, aggregation: "mean_episode_dimension_scores_then_equal_dimension_mean" } };
}

export function tennisAssessmentOf(result: DemoResultResponse | null): TennisAssessment | null {
  const context = tennisAssessmentContext(result);
  return context ? validatedTennisAssessment(result?.tennis_assessment, context) : null;
}

/** Late supplemental reads must not erase a score already loaded for this same video. */
export function retainTennisAssessment(incoming: DemoResultResponse, previous: DemoResultResponse | null): DemoResultResponse {
  if (incoming.tennis_assessment !== undefined) return incoming;
  const context = tennisAssessmentContext(incoming), existing = tennisAssessmentOf(previous);
  const retained = context && existing ? validatedTennisAssessment(existing, context) : null;
  return retained ? { ...incoming, tennis_assessment: retained } : incoming;
}
