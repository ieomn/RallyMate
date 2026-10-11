export const MOTION_REPLAY_STATES = ["queued", "rendering", "submitting", "provider_processing", "downloading", "completed", "failed", "submit_unknown"] as const;
export type MotionReplayStatus = typeof MOTION_REPLAY_STATES[number];
export type MotionReplayRequest = { episode_id?: string; start_ms?: number; duration_ms?: number };
export type MotionReplayMedia = "reference_video" | "first_frame" | "middle_frame" | "last_frame" | "generated_video" | "trajectory" | "manifest";
export type MotionReplayTask = {
  id: string;
  job_id: string;
  generation_status: MotionReplayStatus;
  quality_status: "pending_review" | "approved" | "rejected";
  model: string;
  resolution: string;
  duration_seconds: number;
  source_window: { start_ms: number; duration_ms: number };
  created_at: string;
  updated_at: string;
  attempts: number;
  next_attempt_at: string | null;
  error: { code: string; message: string; retryable: boolean } | null;
  retry_allowed: boolean;
  media: Record<MotionReplayMedia, string | null>;
};

const record = (value: unknown): Record<string, unknown> => value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const idPattern = /^[a-zA-Z0-9_-]{8,80}$/;
const mediaKeys: MotionReplayMedia[] = ["reference_video", "first_frame", "middle_frame", "last_frame", "generated_video", "trajectory", "manifest"];

/** Media always comes through our persisted replay endpoint, never a provider URL. */
export function motionReplayMediaPath(value: unknown, taskId: string): string | null {
  if (typeof value !== "string" || !idPattern.test(taskId)) return null;
  try {
    const url = new URL(value, "https://rallymate.invalid");
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password || url.search || url.hash) return null;
    const prefix = `/v1/motion-replays/${taskId}/media/`;
    if (!url.pathname.startsWith(prefix)) return null;
    const artifact = url.pathname.slice(prefix.length);
    return ["reference.mp4", "first.png", "middle.png", "last.png", "generated.mp4", "trajectory.json", "manifest.json"].includes(artifact) ? url.pathname : null;
  } catch { return null; }
}

export function parseMotionReplay(value: unknown, expectedJobId?: string, expectedId?: string): MotionReplayTask {
  const data = record(value), source = record(data.source_window), media = record(data.media), error = record(data.error);
  if (typeof data.id !== "string" || !idPattern.test(data.id) || typeof data.job_id !== "string" || !idPattern.test(data.job_id)
    || (expectedJobId && data.job_id !== expectedJobId) || (expectedId && data.id !== expectedId)
    || !MOTION_REPLAY_STATES.includes(data.generation_status as MotionReplayStatus)
    || !["pending_review", "approved", "rejected"].includes(String(data.quality_status))
    || !finite(source.start_ms) || source.start_ms < 0 || !finite(source.duration_ms) || source.duration_ms <= 0) {
    throw new Error("纯净回放数据与当前训练不匹配，请重新读取。");
  }
  const parsedMedia = Object.fromEntries(mediaKeys.map(key => [key, motionReplayMediaPath(media[key], data.id as string)])) as MotionReplayTask["media"];
  // A download may still be incomplete even if a URL was included early.
  if (data.generation_status !== "completed") parsedMedia.generated_video = null;
  return {
    id: data.id, job_id: data.job_id, generation_status: data.generation_status as MotionReplayStatus,
    quality_status: data.quality_status as MotionReplayTask["quality_status"],
    model: typeof data.model === "string" ? data.model : "MiniMax",
    resolution: typeof data.resolution === "string" ? data.resolution : "",
    duration_seconds: finite(data.duration_seconds) && data.duration_seconds > 0 ? data.duration_seconds : source.duration_ms / 1000,
    source_window: { start_ms: source.start_ms, duration_ms: source.duration_ms },
    created_at: typeof data.created_at === "string" ? data.created_at : "",
    updated_at: typeof data.updated_at === "string" ? data.updated_at : "",
    attempts: finite(data.attempts) ? data.attempts : 0,
    next_attempt_at: typeof data.next_attempt_at === "string" ? data.next_attempt_at : null,
    error: typeof error.message === "string" ? { code: String(error.code ?? ""), message: error.message, retryable: error.retryable === true } : null,
    retry_allowed: data.retry_allowed === true && data.generation_status !== "submit_unknown",
    media: parsedMedia,
  };
}

export function parseMotionReplayList(value: unknown, jobId: string): { items: MotionReplayTask[] } {
  const data = record(value);
  if (!Array.isArray(data.items)) throw new Error("纯净回放记录暂时无法读取。");
  return { items: data.items.map(item => parseMotionReplay(item, jobId)) };
}

export function motionReplayIsActive(task: MotionReplayTask): boolean {
  return ["queued", "rendering", "submitting", "provider_processing", "downloading"].includes(task.generation_status);
}

/** Auto-enqueue can follow analysis completion; empty reports get one bounded grace period. */
export function motionReplayPollDelay(items: MotionReplayTask[], elapsedMs: number, failures = 0): number | null {
  const delay = Math.min(30000, 5000 * 2 ** Math.min(failures, 3));
  if (items.some(motionReplayIsActive)) return delay;
  if (items.length === 0 && elapsedMs < 60000) return Math.min(delay, 60000 - Math.max(0, elapsedMs));
  return null;
}

export function motionReplayStatusText(task: MotionReplayTask): string {
  return ({ queued: "已加入后台队列", rendering: "正在制作轨迹白模", submitting: "正在提交 AI 生成", provider_processing: "MiniMax 正在生成", downloading: "正在保存生成视频", completed: "生成完成", failed: "AI 回放暂未完成", submit_unknown: "提交结果待确认" })[task.generation_status];
}

export function motionReplayInterval(task: MotionReplayTask): string {
  const { start_ms: start, duration_ms: duration } = task.source_window;
  return `${(start / 1000).toFixed(1)}–${((start + duration) / 1000).toFixed(1)} 秒`;
}
