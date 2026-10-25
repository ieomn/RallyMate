import type { MotionAnalysis, MotionEpisode, SwingKinematicsEpisode, SwingKinematicsMetric, SwingKinematicsReport } from "./api-types";
import { productText } from "./presentation-text";

export const SWING_METRICS = [
  { key: "shoulder_hip_separation_deg", label: "肩髋分离角峰值", unit: "deg", displayUnit: "°" },
  { key: "hip_angular_speed_deg_s", label: "髋轴变化峰速", unit: "deg/s", displayUnit: "°/秒" },
  { key: "shoulder_angular_speed_deg_s", label: "肩轴变化峰速", unit: "deg/s", displayUnit: "°/秒" },
  { key: "wrist_speed_torso_s", label: "相对躯干腕速峰值", unit: "torso/s", displayUnit: "躯干长度/秒" },
] as const;
export type SwingMetricKey = typeof SWING_METRICS[number]["key"];
export type SwingContext = { jobId: string; videoSha256: string; motion: MotionAnalysis | null; durationMs?: number | null };
export type SwingPhase = { id: string; label: string; startMs: number; endMs: number };
export type SwingEpisode = Pick<SwingKinematicsEpisode, "episode_id" | "start_ms" | "end_ms" | "person_track_id" | "racket_hand" | "frame_count" | "valid_frame_count" | "coverage" | "reason_zh" | "quality_reason_counts"> & {
  label: string; phases: SwingPhase[]; metrics: Record<SwingMetricKey, SwingKinematicsMetric>;
};
export type SwingView = { status: "available" | "partial" | "unavailable" | "rejected"; episodes: SwingEpisode[] };
const record = (value: unknown): Record<string, unknown> => value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const count = (value: unknown): value is number => Number.isSafeInteger(value) && Number(value) >= 0;
const range = (start: unknown, end: unknown): boolean => finite(start) && finite(end) && start >= 0 && end > start;
const sha = (value: unknown): value is string => typeof value === "string" && /^[a-f\d]{64}$/i.test(value);

export function swingVideoDurationMs(result: unknown): number | null {
  const duration = record(record(record(result).input).video).duration_ms;
  return finite(duration) && duration > 0 ? duration : null;
}

function phasesOf(episode: MotionEpisode): SwingPhase[] {
  const names: Record<string, string> = { preparation: "准备", loading: "蓄势", acceleration: "加速挥拍", swing: "挥拍", follow_through: "随挥", recovery: "恢复" };
  const seen = new Set<string>();
  return (Array.isArray(episode.phases) ? episode.phases : []).flatMap(phase => {
    if (!phase || !names[phase.phase] || seen.has(phase.phase) || phase.status !== "measured" || !range(phase.start_ms, phase.end_ms)
      || phase.start_ms! < episode.start_ms || phase.end_ms! > episode.end_ms) return [];
    seen.add(phase.phase);
    return [{ id: phase.phase, label: names[phase.phase], startMs: phase.start_ms!, endMs: phase.end_ms! }];
  }).sort((a, b) => a.startMs - b.startMs);
}

function metricOf(value: unknown, spec: typeof SWING_METRICS[number], start: number, end: number): SwingKinematicsMetric {
  const item = record(value);
  const unavailable: SwingKinematicsMetric = { value: null, unit: spec.unit, status: "unavailable", timestamp_ms: null, source_frames: [], window_start_ms: null, window_end_ms: null };
  const frames = item.source_frames;
  if (item.status !== "estimated" || item.unit !== spec.unit || !finite(item.value) || item.value < 0
    || spec.unit === "deg" && item.value > 180 || !finite(item.timestamp_ms)
    || !range(item.window_start_ms, item.window_end_ms) || Number(item.window_start_ms) < start || Number(item.window_end_ms) > end
    || item.timestamp_ms < Number(item.window_start_ms) || item.timestamp_ms > Number(item.window_end_ms)
    || !Array.isArray(frames) || frames.length < (spec.unit === "deg" ? 1 : 2)
    || !frames.every((frame, index) => count(frame) && (index === 0 || frame > frames[index - 1]))) return unavailable;
  return { value: item.value, unit: spec.unit, status: "estimated", timestamp_ms: item.timestamp_ms,
    source_frames: [...frames], window_start_ms: Number(item.window_start_ms), window_end_ms: Number(item.window_end_ms) };
}

/** The online report supplies the job/video binding and real episode boundaries;
 * the measurement endpoint cannot enlarge or relabel those windows. */
export function swingKinematicsOf(value: unknown, context: SwingContext): SwingView {
  const raw = record(value), rejected: SwingView = { status: "rejected", episodes: [] };
  if (!context.jobId || raw.source_run_id !== context.jobId || !sha(context.videoSha256) || !sha(raw.source_video_sha256)
    || context.durationMs != null && (!finite(context.durationMs) || context.durationMs <= 0)
    || raw.source_video_sha256.toLowerCase() !== context.videoSha256.toLowerCase()
    || raw.version !== "sam3d-swing-kinematics-v1" || raw.source_timestamp_semantics !== "original_decoder_pts"
    || raw.coordinate_system !== "sam3d_pred_keypoints_3d" || raw.coordinate_units !== "model_units"
    || raw.wrist_reference !== "midhip_relative_fixed_median_torso_per_continuous_window"
    || raw.camera_reference !== "same_camera_epoch_uncalibrated_monocular_estimate"
    || raw.camera_rotation_compensated !== false || raw.cross_video_absolute_speed_comparable !== false
    || raw.measurement_semantics !== "monocular_3d_model_estimate" || raw.score !== null
    || !["available", "partial", "unavailable"].includes(String(raw.status)) || !Array.isArray(raw.episodes)) return rejected;
  if (raw.episodes.length === 0) return { status: "unavailable", episodes: [] };
  if (!context.motion || context.motion.contact_confirmed !== false) return rejected;
  const sourceEpisodes = Object.values(context.motion.families).flatMap(family => family?.episodes ?? []);
  const sourceById = new Map<string, MotionEpisode>();
  const ambiguous = new Set<string>();
  for (const episode of sourceEpisodes) {
    if (sourceById.has(episode.episode_id)) ambiguous.add(episode.episode_id);
    sourceById.set(episode.episode_id, episode);
  }
  const duplicate = new Set<string>(), seen = new Set<string>();
  for (const row of raw.episodes) {
    const id = record(row).episode_id;
    if (typeof id === "string") { if (seen.has(id)) duplicate.add(id); seen.add(id); }
  }
  const episodes: SwingEpisode[] = [];
  for (const row of raw.episodes) {
    const item = record(row), id = item.episode_id;
    if (typeof id !== "string" || !id || duplicate.has(id) || ambiguous.has(id)) continue;
    const source = sourceById.get(id);
    if (!source || !range(item.start_ms, item.end_ms) || item.start_ms !== source.start_ms || item.end_ms !== source.end_ms
      || source.contact_confirmed !== false || !count(item.person_track_id) || item.person_track_id !== source.person_track_id
      || finite(context.durationMs) && Number(item.end_ms) > context.durationMs
      || !count(item.frame_count) || !count(item.valid_frame_count) || item.valid_frame_count > item.frame_count
      || !finite(item.coverage) || item.coverage < 0 || item.coverage > 1
      || Math.abs(item.coverage - (item.frame_count > 0 ? item.valid_frame_count / item.frame_count : 0)) > 1e-6
      || !["left", "right", null].includes(item.racket_hand as string | null)) continue;
    const start = Number(item.start_ms), end = Number(item.end_ms), validFrameCount = item.valid_frame_count;
    const metrics = Object.fromEntries(SWING_METRICS.map(spec => [spec.key,
      metricOf(raw.status !== "unavailable" && validFrameCount > 0 ? record(item.metrics)[spec.key] : null, spec, start, end)])) as SwingEpisode["metrics"];
    if (item.racket_hand === null) metrics.wrist_speed_torso_s = metricOf(null, SWING_METRICS[3], start, end);
    episodes.push({ episode_id: id, start_ms: start, end_ms: end, person_track_id: item.person_track_id,
      racket_hand: item.racket_hand as SwingEpisode["racket_hand"], frame_count: item.frame_count, valid_frame_count: item.valid_frame_count,
      coverage: item.coverage, reason_zh: productText(item.reason_zh),
      quality_reason_counts: Object.fromEntries(Object.entries(record(item.quality_reason_counts)).filter((entry): entry is [string, number] => count(entry[1]))),
      label: source.family === "serve" ? "发球动作" : source.family === "return" ? "接发动作" : "底线挥拍",
      phases: phasesOf(source), metrics });
  }
  episodes.sort((a, b) => a.start_ms - b.start_ms || a.episode_id.localeCompare(b.episode_id));
  const metrics = episodes.flatMap(episode => Object.values(episode.metrics));
  const valid = metrics.filter(metric => metric.status === "estimated").length;
  return { status: !valid ? "unavailable" : valid === metrics.length ? "available" : "partial", episodes };
}

/** A backup may keep this canonical measurement contract, never cached display scores. */
export function validatedSwingKinematicsReport(value: unknown, context: SwingContext): SwingKinematicsReport | null {
  const view = swingKinematicsOf(value, context);
  if (view.status === "rejected") return null;
  return {
    version: "sam3d-swing-kinematics-v1", status: view.status,
    source_run_id: context.jobId, source_video_sha256: context.videoSha256.toLowerCase(),
    source_timestamp_semantics: "original_decoder_pts", coordinate_system: "sam3d_pred_keypoints_3d", coordinate_units: "model_units",
    wrist_reference: "midhip_relative_fixed_median_torso_per_continuous_window", camera_reference: "same_camera_epoch_uncalibrated_monocular_estimate",
    camera_rotation_compensated: false, cross_video_absolute_speed_comparable: false,
    measurement_semantics: "monocular_3d_model_estimate", score: null,
    reason_zh: productText(record(value).reason_zh),
    episodes: view.episodes.map(episode => ({ episode_id: episode.episode_id, start_ms: episode.start_ms, end_ms: episode.end_ms,
      person_track_id: episode.person_track_id, racket_hand: episode.racket_hand, frame_count: episode.frame_count, valid_frame_count: episode.valid_frame_count,
      coverage: episode.coverage, reason_zh: episode.reason_zh, quality_reason_counts: episode.quality_reason_counts, metrics: episode.metrics })),
    limitations_zh: ["单目三维模型估计；相机旋转未校正，不用于跨视频绝对速度排名，也不表示真实受力、触球时刻或技术评分。"],
  };
}

export function swingMetricText(metric: SwingKinematicsMetric): string {
  return metric.status === "estimated" && finite(metric.value) ? Number(metric.value.toFixed(2)).toString() : "—";
}

/** Explicit source time only. Do not request a media switch or reinterpret a peak as contact. */
export function swingSeekDetail(jobId: string, episode: SwingEpisode, timestampMs: unknown): { jobId: string; timestampMs: number } | null {
  return jobId && finite(timestampMs) && timestampMs >= episode.start_ms && timestampMs <= episode.end_ms
    ? { jobId, timestampMs } : null;
}
