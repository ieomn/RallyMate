import type { RallyMateApiClient } from "./api-client";
import type { DemoResultResponse, JobProgress } from "./api-types";

const JOB_ID = /^[a-zA-Z0-9_-]{8,80}$/;
const validId = (value: unknown): value is string => typeof value === "string" && JOB_ID.test(value);

export function annotationHref(jobId?: string | null): string {
  return validId(jobId) ? `/annotate?job=${encodeURIComponent(jobId)}` : "/annotate";
}

/** Only task IDs and report links on this gateway can select an annotation job. */
export function annotationJobId(value: string, origin: string): string | null {
  const input = value.trim();
  if (validId(input)) return input;
  if (!input || !/^(?:https?:\/\/|\/|\?)/i.test(input)) return null;
  try {
    const url = new URL(input, `${origin}/`);
    if (!["http:", "https:"].includes(url.protocol) || url.origin !== new URL(origin).origin || url.username || url.password
      || !["/", "/annotate", "/annotate/"].includes(url.pathname) || url.searchParams.getAll("job").length !== 1) return null;
    const jobId = url.searchParams.get("job");
    return validId(jobId) ? jobId : null;
  } catch { return null; }
}

export type AnnotationReport = {
  job: JobProgress;
  result: DemoResultResponse;
  videoTimeOriginMs: number | null;
};

/** Read an existing completed job; never submit, restart or rewrite an analysis. */
export async function loadAnnotationReport(
  client: Pick<RallyMateApiClient, "getJob" | "getDemoResult" | "getTrajectory">,
  jobId: string,
  signal: AbortSignal,
): Promise<AnnotationReport> {
  if (!validId(jobId)) throw new Error("请输入有效的任务编号，或粘贴本站的报告链接。");
  signal.throwIfAborted();
  const job = await client.getJob(jobId, signal);
  signal.throwIfAborted();
  if (job.id !== jobId) throw new Error("任务响应与所选视频不一致，请重新读取。");
  if (["failed", "cancelled"].includes(job.status)) throw new Error("这个任务未完成分析，暂时不能标注。请返回训练空间查看任务状态。");
  if (!["completed", "succeeded"].includes(job.status)) throw new Error("这个任务尚未分析完成，请稍后重新读取。");
  const [result, trajectory] = await Promise.allSettled([
    client.getDemoResult(jobId, { signal }),
    client.getTrajectory(jobId, { signal: AbortSignal.any([signal, AbortSignal.timeout(8000)]), sampleLimit: 16, predictionHorizonMs: 0 }),
  ]);
  signal.throwIfAborted();
  if (result.status === "rejected") throw result.reason;
  if (result.value.job_id !== jobId || result.value.status !== "ready") throw new Error("报告与所选视频不一致，或结果尚未就绪，请重新读取。");
  const origin = trajectory.status === "fulfilled" && trajectory.value.job_id === jobId ? trajectory.value.source?.start_timestamp_ms : null;
  return {
    job,
    result: result.value,
    videoTimeOriginMs: typeof origin === "number" && Number.isFinite(origin) && origin >= 0 ? origin : null,
  };
}

/** A source timestamp is only safe when the served video preserved source PTS. */
export function annotationSeekSeconds(report: AnnotationReport, jobId: unknown, timestampMs: unknown): number | null {
  if (jobId !== report.job.id || typeof timestampMs !== "number" || !Number.isFinite(timestampMs)
    || report.videoTimeOriginMs === null || timestampMs < report.videoTimeOriginMs
    || report.result.runtime?.annotated_video_compatibility?.timing_preserved !== true) return null;
  return (timestampMs - report.videoTimeOriginMs) / 1000;
}
