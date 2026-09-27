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

function retryable(error: unknown) {
  const status = error && typeof error === "object" && "status" in error ? Number(error.status) : 0;
  return !status || status === 408 || status === 429 || status >= 500;
}

export function reconnectDelay(failures: number, interval = 2000) {
  return Math.min(15000, Math.max(0, interval) * 2 ** Math.min(Math.max(0, failures - 1), 5));
}

/** Jobs keep running on the analyzer when a tab closes. Only stop watching. */
export async function watchAnalysis(client: RallyMateApiClient, jobId: string, signal: AbortSignal, onProgress: (job: JobProgress) => void, interval = 2000, onTrajectory?: (trajectory: TrajectoryPreviewResponse) => void): Promise<AnalysisResult> {
  let failures = 0;
  let job: JobProgress = { id: jobId, status: "queued" };
  let lastPreviewAt = -Infinity;
  let lastPreviewFrame: number | undefined;
  let liveTrajectory: TrajectoryPreviewResponse | null = null;
  let previewPending = false;
  let watching = true;
  const previewController = new AbortController();
  try {
    for (;;) {
      signal.throwIfAborted();
      try {
        const response = await client.getJob(jobId, signal);
        if (response.id !== jobId || typeof response.status !== "string") throw Object.assign(new Error("任务响应不完整，请重试读取"), { status: 422 });
        job = response;
        failures = 0;
      } catch (error) {
        signal.throwIfAborted();
        if (!retryable(error)) throw error;
        if (++failures >= 30) throw new Error("网络连接暂未恢复。点击“继续读取结果”可重新连接当前任务，无需重新上传。", { cause: error });
        const message = `连接暂时中断，正在自动重连（${failures}/30）。服务器任务继续运行，无需重新上传。`;
        onProgress({ ...job, message, progress: typeof job.progress === "object" ? { ...job.progress, message } : job.progress });
        await pause(reconnectDelay(failures, interval), signal);
        continue;
      }
      signal.throwIfAborted();
      onProgress(job);
      if (["succeeded", "completed"].includes(job.status)) break;
      if (["failed", "cancelled"].includes(job.status)) throw new Error(job.error || "视频处理失败，请更换片段后重试");
      // One optional preview at a time. It cannot hold up job polling and is
      // cancelled when observation ends, so stale previews cannot replace a result.
      const rawProcessed = job.progress && typeof job.progress === "object"
        ? job.progress.processed_frames ?? job.processed_frames : job.processed_frames;
      const processed = typeof rawProcessed === "number" && Number.isFinite(rawProcessed) ? rawProcessed : undefined;
      if (onTrajectory && !previewPending && ["running", "processing"].includes(job.status) && Date.now() - lastPreviewAt >= 15000 &&
          (processed === undefined || processed !== lastPreviewFrame)) {
        lastPreviewAt = Date.now();
        previewPending = true;
        const previewSignal = AbortSignal.any([signal, previewController.signal, AbortSignal.timeout(8000)]);
        void client.getTrajectory(jobId, { signal: previewSignal, predictionHorizonMs: 0, sampleLimit: 128 }).then(preview => {
          if (watching && !previewSignal.aborted && preview.job_id === jobId) {
            liveTrajectory = preview;
            lastPreviewFrame = processed;
            onTrajectory(preview);
          }
        }).catch(() => { /* Optional preview failures never fail the inference job. */ }).finally(() => { previewPending = false; });
      }
      await pause(interval, signal);
    }
  } finally {
    watching = false;
    previewController.abort();
  }
  const readResult = async () => {
    for (let attempt = 1; ; attempt += 1) {
      try { return await client.getDemoResult(jobId, { signal }); }
      catch (error) {
        signal.throwIfAborted();
        if (!retryable(error) || attempt >= 3) throw error;
        onProgress({ ...job, message: "推理已完成，正在重试读取动作结果。无需重新上传。" });
        await pause(reconnectDelay(attempt, interval), signal);
      }
    }
  };
  const optionalSignal = AbortSignal.any([signal, AbortSignal.timeout(8000)]);
  // Scores are required for the results screen, including an explicit null
  // score for a clip with insufficient observations. Optional overlays can fail.
  const [primary, trajectory, assessment] = await Promise.allSettled([
    readResult(),
    client.getTrajectory(jobId, { signal: optionalSignal, predictionHorizonMs: 0, sampleLimit: 384 }),
    client.getTechniqueAssessment(jobId, { signal: optionalSignal }),
  ]);
  signal.throwIfAborted();
  if (primary.status === "rejected") throw new Error("推理已完成，结果暂未读取成功。点击“继续读取结果”，无需重新上传。");
  if (primary.value.job_id !== jobId || primary.value.status !== "ready") throw new Error("结果与当前视频任务不匹配，请重新读取");
  const finalTrajectory = trajectory.status === "fulfilled" && trajectory.value.job_id === jobId ? trajectory.value : null;
  return {
    job, result: primary.value,
    trajectory: finalTrajectory ?? liveTrajectory,
    assessment: assessment.status === "fulfilled" ? assessment.value : null,
    warning: !finalTrajectory || assessment.status === "rejected" ? "部分画面证据暂不可用，动作结果已保留。" : null,
  };
}
