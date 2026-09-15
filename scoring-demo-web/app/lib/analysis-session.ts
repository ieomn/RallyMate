import type { RallyMateApiClient } from "./api-client";
import type { DemoResultResponse, JobProgress, TechniqueAssessmentResponse, TrajectoryPreviewResponse } from "./api-types";

export type AnalysisResult = { job: JobProgress; result: DemoResultResponse; trajectory: TrajectoryPreviewResponse | null; assessment: TechniqueAssessmentResponse | null; warning: string | null };

function pause(ms: number, signal: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    signal.throwIfAborted();
    const abort = () => { clearTimeout(timer); reject(signal.reason); };
    const timer = setTimeout(() => { signal.removeEventListener("abort", abort); resolve(); }, ms);
    signal.addEventListener("abort", abort, { once: true });
  });
}

/** Jobs keep running on the analyzer when a tab closes. Only stop watching. */
export async function watchAnalysis(client: RallyMateApiClient, jobId: string, signal: AbortSignal, onProgress: (job: JobProgress) => void, interval = 2000): Promise<AnalysisResult> {
  let failures = 0;
  let job: JobProgress;
  for (;;) {
    signal.throwIfAborted();
    try {
      job = await client.getJob(jobId, signal);
      if (job.id !== jobId || typeof job.status !== "string") throw new Error("任务响应不完整，请重试读取");
      failures = 0;
    } catch (error) {
      signal.throwIfAborted();
      if (++failures >= 5) throw error;
      await pause(interval * failures, signal);
      continue;
    }
    signal.throwIfAborted();
    onProgress(job);
    if (job.status === "succeeded") break;
    if (["failed", "cancelled"].includes(job.status)) throw new Error(job.error || "视频处理失败，请更换片段后重试");
    await pause(interval, signal);
  }
  // Scores are required for the results screen, including an explicit null
  // score for a clip with insufficient observations. Optional overlays can fail.
  const [primary, trajectory, assessment] = await Promise.allSettled([
    client.getDemoResult(jobId, { signal }),
    client.getTrajectory(jobId, { signal }),
    client.getTechniqueAssessment(jobId, { signal }),
  ]);
  signal.throwIfAborted();
  if (primary.status === "rejected") throw new Error("推理已完成，结果暂未读取成功。点击“继续读取结果”，无需重新上传。");
  if (primary.value.job_id !== jobId || primary.value.status !== "ready") throw new Error("结果与当前视频任务不匹配，请重新读取");
  return {
    job, result: primary.value,
    trajectory: trajectory.status === "fulfilled" ? trajectory.value : null,
    assessment: assessment.status === "fulfilled" ? assessment.value : null,
    warning: trajectory.status === "rejected" || assessment.status === "rejected" ? "部分画面证据暂不可用，动作结果已保留。" : null,
  };
}
