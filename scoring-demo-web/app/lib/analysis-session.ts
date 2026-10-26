import type { RallyMateApiClient } from "./api-client";
import type { DemoResultResponse, JobProgress, TechniqueAssessmentResponse, TrajectoryPreviewResponse } from "./api-types";

export type AnalysisResult = { job: JobProgress; result: DemoResultResponse; trajectory: TrajectoryPreviewResponse | null; assessment: TechniqueAssessmentResponse | null; warning: string | null };

export class AnalysisCancelledError extends Error {
  constructor() { super("本次分析已取消，服务器已停止处理。"); this.name = "AnalysisCancelledError"; }
}

/** Poll and cancel responses can arrive out of order. Terminal reports must not regress. */
export function mergeAnalysisProgress(previous: JobProgress | null, incoming: JobProgress): JobProgress {
  if (!previous || previous.id !== incoming.id) return incoming;
  if (["succeeded", "completed", "failed", "cancelled"].includes(previous.status)) return previous;
  return previous.cancel_requested && ["queued", "running", "processing"].includes(incoming.status)
    ? { ...incoming, cancel_requested: true, cancel_requested_at: incoming.cancel_requested_at ?? previous.cancel_requested_at }
    : incoming;
}

function pause(ms: number, signal: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    signal.throwIfAborted();
    const abort = () => { clearTimeout(timer); reject(signal.reason); };
    const timer = setTimeout(() => { signal.removeEventListener("abort", abort); resolve(); }, ms);
    signal.addEventListener("abort", abort, { once: true });
  });
}

function preparationTimedOut(error: unknown) {
  if (!error || typeof error !== "object" || !("status" in error) || error.status !== 504 || !("payload" in error)) return false;
  const payload = error.payload;
  const detail = payload && typeof payload === "object" && "detail" in payload ? payload.detail : null;
  return !!detail && typeof detail === "object" && "code" in detail && detail.code === "report_preparation_timeout";
}

function retryable(error: unknown) {
  // An explicit builder deadline is terminal for this read, not a transient
  // network 504. The user can choose a later fresh read.
  if (preparationTimedOut(error)) return false;
  const status = error && typeof error === "object" && "status" in error ? Number(error.status) : 0;
  return !status || status === 408 || status === 429 || status >= 500;
}

export function reconnectDelay(failures: number, interval = 2000) {
  return Math.min(15000, Math.max(0, interval) * 2 ** Math.min(Math.max(0, failures - 1), 5));
}

/** Jobs keep running on the analyzer when a tab closes. Only stop watching. */
export async function watchAnalysis(client: RallyMateApiClient, jobId: string, signal: AbortSignal, onProgress: (job: JobProgress) => void, interval = 2000, onTrajectory?: (trajectory: TrajectoryPreviewResponse) => void, onResult?: (result: DemoResultResponse) => void): Promise<AnalysisResult> {
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
      if (job.status === "cancelled") throw new AnalysisCancelledError();
      if (job.status === "failed") throw new Error(job.error || "视频处理失败，请更换片段后重试");
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
  const reportDeadline = performance.now() + 120000;
  const readResult = async () => {
    for (let attempt = 1; ; attempt += 1) {
      try {
        const maxWaitMs = reportDeadline - performance.now();
        if (maxWaitMs <= 0) throw Object.assign(new Error("报告读取已达到本次等待时限，请继续读取结果，无需重新上传。"), { status: 425 });
        const result = await client.getDemoResult(jobId, { signal, maxWaitMs });
        signal.throwIfAborted();
        return result;
      }
      catch (error) {
        signal.throwIfAborted();
        if (!retryable(error) || attempt >= 3) throw error;
        onProgress({ ...job, message: "推理已完成，正在重试读取动作结果。无需重新上传。" });
        await pause(Math.min(reconnectDelay(attempt, interval), Math.max(0, reportDeadline - performance.now())), signal);
        if (performance.now() >= reportDeadline) throw Object.assign(new Error(
          "报告读取已达到本次等待时限，请继续读取结果，无需重新上传。", { cause: error }), { status: 425 });
      }
    }
  };
  const optionalController = new AbortController();
  // Scores are required for the results screen, including an explicit null
  // score for a clip with insufficient observations. Optional overlays can fail.
  const primaryRead = readResult();
  try {
    let primary: DemoResultResponse;
    try { primary = await primaryRead; }
    catch (cause) {
      signal.throwIfAborted();
      throw new Error(preparationTimedOut(cause)
        ? "服务器整理报告已超时。可稍后点击“继续读取结果”；本次分析无需重新上传。"
        : "推理已完成，结果暂未读取成功。点击“继续读取结果”，无需重新上传。", { cause });
    }
    signal.throwIfAborted();
    if (primary.job_id !== jobId || primary.status !== "ready") throw new Error("结果与当前视频任务不匹配，请重新读取");
    // Publish the required report, or its error, independently of optional reads.
    // Rendering callbacks are outside the transport retry loop: a UI failure
    // must not issue the same report request again.
    onResult?.(primary);
    signal.throwIfAborted();
    // Publish the validated report before supplementary reads compete for the
    // same connection. Their fresh deadline starts after primary delivery.
    const optionalSignal = AbortSignal.any([signal, optionalController.signal, AbortSignal.timeout(20000)]);
    const optionalReads = Promise.allSettled([
      client.getTrajectory(jobId, { signal: optionalSignal, predictionHorizonMs: 0, sampleLimit: 384 }),
      client.getTechniqueAssessment(jobId, { signal: optionalSignal }),
    ]);
    const [trajectory, assessment] = await optionalReads;
    // Retry each transient failure once with its own fresh deadline.
    // Successful reads and invalid artifacts are retained.
    const retryOptional = async <T,>(previous: PromiseSettledResult<T>, read: (retrySignal: AbortSignal) => Promise<T>): Promise<PromiseSettledResult<T>> => {
      if (previous.status === "fulfilled" || !retryable(previous.reason)) return previous;
      signal.throwIfAborted();
      const retrySignal = AbortSignal.any([signal, optionalController.signal, AbortSignal.timeout(20000)]);
      try {
        const value = await read(retrySignal);
        retrySignal.throwIfAborted();
        return { status: "fulfilled", value };
      } catch (reason) { return { status: "rejected", reason }; }
    };
    const [retriedTrajectory, retriedAssessment] = await Promise.all([
      retryOptional(trajectory, retrySignal => client.getTrajectory(jobId, { signal: retrySignal, predictionHorizonMs: 0, sampleLimit: 384 })),
      retryOptional(assessment, retrySignal => client.getTechniqueAssessment(jobId, { signal: retrySignal })),
    ]);
    signal.throwIfAborted();
    const finalTrajectory = retriedTrajectory.status === "fulfilled" && retriedTrajectory.value?.job_id === jobId ? retriedTrajectory.value : null;
    const finalAssessment = retriedAssessment.status === "fulfilled" && retriedAssessment.value && typeof retriedAssessment.value === "object" && !Array.isArray(retriedAssessment.value)
      && (retriedAssessment.value.job_id === undefined || retriedAssessment.value.job_id === jobId) ? retriedAssessment.value : null;
    return {
      job, result: primary,
      trajectory: finalTrajectory ?? liveTrajectory,
      assessment: finalAssessment,
      warning: !finalTrajectory || !finalAssessment ? "部分画面证据暂不可用，动作结果已保留。" : null,
    };
  } finally {
    optionalController.abort();
  }
}
