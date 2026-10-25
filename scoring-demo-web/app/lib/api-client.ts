import { normalizeMeasurementResult } from "./measurement-evidence";
import { MODEL_PROOF_CODEC, unpackApiReport } from "./report-backup-codec";
import { MAX_REPORT_WIRE_BYTES } from "./report-limits";
import { uploadResumable } from "./resumable-upload";
import { parseTechnicalReview, type TechnicalReviewInput, type TechnicalReviewLedger } from "./technical-review";
import type {
  DemoResultResponse,
  Sam3dMeshPreview,
  JobProgress,
  JobSubmission,
  RallyMateApiConfig,
  RallyMateMetaResponse,
  ScorecardCapabilities,
  ScorecardRequest,
  ScorecardResponse,
  SubmitVideoOptions,
  TechniqueAssessmentResponse,
  TechniqueCatalogResponse,
  TrajectoryPreviewResponse,
  PosePreviewResponse,
} from "./api-types";
import { RallyMateApiError } from "./api-types";
import { attachRequestDiagnostics, safeRequestDiagnostic, type RequestDiagnostic, type RequestDiagnosticObserver } from "./request-diagnostics";
import { normalizePosePreview } from "./pose-playback";
import { FOOT_CROSSING_RESPONSE_BYTES, parseFootCrossingResponse, type FootCrossingResponse } from "./foot-crossing-candidates";

export { RallyMateApiError } from "./api-types";
export type { JobProgress } from "./api-types";

export type ReportPreparationTiming = { now(): number; wait(ms: number, signal?: AbortSignal): Promise<void> };
function waitForReport(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    signal?.throwIfAborted();
    const abort = () => { clearTimeout(timer); reject(signal?.reason); };
    const timer = setTimeout(() => { signal?.removeEventListener("abort", abort); resolve(); }, ms);
    signal?.addEventListener("abort", abort, { once: true });
  });
}

const readPublicEnv = (): Record<string, string | undefined> => {
  // Keep this compatible with both vinext/Next (NEXT_PUBLIC_*) and Vite hosts
  // (VITE_*). Values are intentionally public: never put credentials here.
  const processEnv =
    typeof process !== "undefined" && process.env ? process.env : undefined;
  const viteEnv =
    typeof import.meta !== "undefined"
      ? (import.meta as ImportMeta & { env?: Record<string, string | undefined> }).env
      : undefined;
  // Treat an injected empty string like an unset value.  This matters when a
  // shared .env template defines NEXT_PUBLIC_* as blank while a Vite build
  // supplies the actual VITE_* value (or vice versa).
  const firstNonEmpty = (...values: Array<string | undefined>) =>
    values.find((value) => typeof value === "string" && value.trim().length > 0)?.trim();
  return {
    baseUrl: firstNonEmpty(
      processEnv?.NEXT_PUBLIC_RALLYMATE_API_URL,
      processEnv?.NEXT_PUBLIC_API_BASE_URL,
      processEnv?.NEXT_PUBLIC_API_URL,
      viteEnv?.VITE_RALLYMATE_API_URL,
      viteEnv?.VITE_API_BASE_URL,
      viteEnv?.VITE_API_URL,
    ),
    scorecardPath: firstNonEmpty(
      processEnv?.NEXT_PUBLIC_RALLYMATE_SCORECARD_PATH,
      viteEnv?.VITE_RALLYMATE_SCORECARD_PATH,
    ),
    jobsPath: firstNonEmpty(
      processEnv?.NEXT_PUBLIC_RALLYMATE_JOBS_PATH,
      viteEnv?.VITE_RALLYMATE_JOBS_PATH,
    ),
    techniquesPath: firstNonEmpty(
      processEnv?.NEXT_PUBLIC_RALLYMATE_TECHNIQUES_PATH,
      viteEnv?.VITE_RALLYMATE_TECHNIQUES_PATH,
    ),
  };
};

const trimSlashes = (value: string) => value.replace(/\/+$/, "");
const ensurePath = (value: string, fallback: string) => {
  const path = value.trim() || fallback;
  return path.startsWith("/") ? path : `/${path}`;
};

function requestId() {
  if (typeof globalThis.crypto?.randomUUID === "function") return globalThis.crypto.randomUUID();
  return `web-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

export function getApiConfig(): RallyMateApiConfig {
  const env = readPublicEnv();
  return {
    baseUrl: trimSlashes(env.baseUrl ?? ""),
    scorecardPath: ensurePath(env.scorecardPath ?? "", "/api/scorecard"),
    jobsPath: ensurePath(env.jobsPath ?? "", "/v1/jobs"),
    techniquesPath: ensurePath(env.techniquesPath ?? "", "/v1/techniques"),
  };
}

export interface RallyMateApiClient {
  getMeshPreview(jobId: string, signal?: AbortSignal): Promise<{ job_id: string; status: "ready" | "pending" | "failed" | "unavailable"; sam3d_mesh: Sam3dMeshPreview | null }>;
  readonly config: RallyMateApiConfig;
  getCapabilities(signal?: AbortSignal): Promise<ScorecardCapabilities>;
  getMeta(signal?: AbortSignal): Promise<RallyMateMetaResponse>;
  getTechniques(signal?: AbortSignal): Promise<TechniqueCatalogResponse>;
  scorecard(request: ScorecardRequest, signal?: AbortSignal): Promise<ScorecardResponse>;
  submitVideo(file: File | Blob, options?: SubmitVideoOptions): Promise<JobSubmission>;
  getJob(jobId: string, signal?: AbortSignal): Promise<JobProgress>;
  cancelJob(jobId: string, signal?: AbortSignal): Promise<JobProgress>;
  getTechnicalReview(jobId: string, signal?: AbortSignal): Promise<TechnicalReviewLedger>;
  saveTechnicalReview(jobId: string, review: TechnicalReviewInput, signal?: AbortSignal): Promise<TechnicalReviewLedger>;
  exportTechnicalReview(jobId: string, signal?: AbortSignal): Promise<Record<string, unknown>>;
  getDemoResult(jobId: string, options?: { endpoint?: string | null; signal?: AbortSignal; onPrepare?: () => void; maxWaitMs?: number }): Promise<DemoResultResponse>;
  getPosePreview(jobId: string, options?: { startMs?: number; durationMs?: number; sampleLimit?: number; signal?: AbortSignal }): Promise<PosePreviewResponse>;
  getFootCrossingCandidates(jobId: string, options?: { signal?: AbortSignal }): Promise<FootCrossingResponse>;
  getTrajectory(
    jobId: string,
    options?: {
      endpoint?: string | null;
      sampleLimit?: number;
      predictionHorizonMs?: number;
      signal?: AbortSignal;
    },
  ): Promise<TrajectoryPreviewResponse>;
  getTechniqueAssessment(
    jobId: string,
    options?: { endpoint?: string | null; signal?: AbortSignal },
  ): Promise<TechniqueAssessmentResponse>;
}

export function createApiClient(
  config: Partial<RallyMateApiConfig> = {},
  fetchImpl: typeof fetch = fetch,
  reportTiming: ReportPreparationTiming = { now: () => performance.now(), wait: waitForReport },
  onDiagnostic?: RequestDiagnosticObserver,
): RallyMateApiClient {
  const resolvedConfig = { ...getApiConfig(), ...config };
  type ReadContext = {
    operation: RequestDiagnostic["operation"]; requestId: string; httpStatus: number | null;
    startedAt: string; startedMs: number; bodyComplete: boolean;
    caller?: AbortSignal; timeout: AbortSignal; rows: RequestDiagnostic[];
    bodyIdleController?: AbortController;
  };
  const responseContexts = new WeakMap<Response, ReadContext>();
  const operationFor = (path: string): RequestDiagnostic["operation"] => {
    const pathname = path.split(/[?#]/, 1)[0];
    if (/\/demo-result$/.test(pathname)) return "report";
    if (/\/technique-assessment$/.test(pathname)) return "technique_assessment";
    if (/\/trajectory$/.test(pathname)) return "trajectory";
    if (/\/pose-preview$/.test(pathname)) return "pose_preview";
    if (/\/technical-review(?:\/export)?$/.test(pathname)) return "technical_review";
    if (/\/meta$/.test(pathname)) return "meta";
    return /\/jobs\/[^/]+$/.test(pathname) ? "job" : "other_read";
  };
  const record = (context: ReadContext | undefined, stage: RequestDiagnostic["stage"], outcome: RequestDiagnostic["outcome"], category: RequestDiagnostic["category"] = null, error?: unknown) => {
    if (!context || context.caller?.aborted) return;
    const row = safeRequestDiagnostic({ operation: context.operation, stage, outcome, category,
      requestId: context.requestId, httpStatus: context.httpStatus, startedAt: context.startedAt,
      elapsedMs: Math.max(0, performance.now() - context.startedMs), bodyComplete: context.bodyComplete });
    if (!row) return;
    context.rows.push(row);
    if (error) attachRequestDiagnostics(error, context.rows);
    try { onDiagnostic?.(row); } catch { /* Observability must not alter report delivery. */ }
  };
  const readFailure = (context: ReadContext | undefined, stage: "headers" | "body", error: unknown) => {
    record(context, stage, "failure", context?.timeout.aborted ? "timeout" : stage === "headers" ? "network" : "body_read", error);
  };
  const urlFor = (path: string) => {
    // Result links can contain the analyzer's private address. Always request
    // their API path through the configured browser gateway.
    if (/^https?:\/\//i.test(path)) {
      const parsed = new URL(path);
      if (!parsed.pathname.startsWith("/v1/")) throw new RallyMateApiError("服务返回了无效的结果地址", 502);
      path = parsed.pathname + parsed.search;
    }
    return `${resolvedConfig.baseUrl}${ensurePath(path, "/")}`;
  };

  async function boundedResponseText(response: Response, limit: number, signal?: AbortSignal, idle?: AbortController): Promise<string> {
    signal?.throwIfAborted();
    const length = response.headers.get("content-length");
    if (length !== null && (!/^\d+$/.test(length) || Number(length) > limit)) {
      void response.body?.cancel().catch(() => {});
      throw new RallyMateApiError("响应超出读取上限。", 422);
    }
    if (!response.body) return "";
    const reader = response.body.getReader(), decoder = new TextDecoder("utf-8", { fatal: true });
    const cancel = () => { void reader.cancel().catch(() => {}); };
    signal?.addEventListener("abort", cancel, { once: true });
    let bytes = 0, text = "", complete = false;
    let idleTimer: ReturnType<typeof setTimeout> | undefined;
    const progress = () => {
      if (!idle) return;
      clearTimeout(idleTimer);
      idleTimer = setTimeout(() => idle.abort(new DOMException("报告数据连续 20 秒没有下载进展，请重试读取。", "TimeoutError")), 20000);
    };
    progress();
    try {
      for (;;) {
        signal?.throwIfAborted();
        const { done, value } = await reader.read();
        signal?.throwIfAborted();
        if (done) { complete = true; return text + decoder.decode(); }
        if (value.byteLength) progress();
        bytes += value.byteLength;
        if (bytes > limit) throw new RallyMateApiError("响应超出读取上限。", 422);
        text += decoder.decode(value, { stream: true });
      }
    } finally {
      clearTimeout(idleTimer);
      signal?.removeEventListener("abort", cancel);
      if (!complete) cancel();
      reader.releaseLock();
    }
  }

  async function parseResponse<T>(response: Response, bounds?: { maxBytes: number; signal?: AbortSignal }): Promise<T> {
    const context = responseContexts.get(response);
    let text: string;
    const signals = [bounds?.signal, bounds ? context?.timeout : undefined].filter((value): value is AbortSignal => !!value);
    const readSignal = signals.length ? AbortSignal.any(signals) : undefined;
    try { text = bounds ? await boundedResponseText(response, bounds.maxBytes, readSignal, context?.bodyIdleController) : await response.text(); }
    catch (error) { readFailure(context, "body", error); throw error; }
    if (context) context.bodyComplete = true;
    record(context, "body", "success");
    let payload: unknown = undefined;
    let parseError: unknown;
    if (text) {
      try {
        payload = JSON.parse(text);
      } catch (error) {
        parseError = error;
        payload = text;
      }
    }
    if (!response.ok) {
      const message =
        payload && typeof payload === "object" && "error" in payload
          ? String((payload as { error?: unknown }).error)
          : payload && typeof payload === "object" && "detail" in payload
            ? (() => {
                const detail = (payload as { detail?: unknown }).detail;
                if (typeof detail === "string") return detail;
                if (detail && typeof detail === "object") {
                  const record = detail as { message?: unknown; message_zh?: unknown; code?: unknown };
                  return String(record.message_zh ?? record.message ?? record.code ?? "请求被服务端拒绝");
                }
                return `RallyMate API request failed (${response.status})`;
              })()
          : `RallyMate API request failed (${response.status})`;
      const error = new RallyMateApiError(message, response.status, payload, parseError ? { cause: parseError } : undefined);
      const detail = payload && typeof payload === "object" && "detail" in payload ? payload.detail : null;
      const preparationTimeout = response.status === 504 && detail && typeof detail === "object" && "code" in detail && detail.code === "report_preparation_timeout";
      record(context, "json", "failure", preparationTimeout ? "preparation_timeout" : "http", error);
      throw error;
    }
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
      const error = new RallyMateApiError("服务未返回有效 JSON，请检查 API 代理配置", 502, undefined, parseError ? { cause: parseError } : undefined);
      record(context, "json", "failure", parseError ? "json_parse" : "response_contract", error);
      throw error;
    }
    record(context, "json", "success");
    return payload as T;
  }

  const reportPath = (jobId: string, suffix = "") => {
    const boundary = resolvedConfig.jobsPath.search(/[?#]/);
    const path = boundary < 0 ? resolvedConfig.jobsPath : resolvedConfig.jobsPath.slice(0, boundary);
    const tail = boundary < 0 ? "" : resolvedConfig.jobsPath.slice(boundary);
    return `${trimSlashes(path)}/${encodeURIComponent(jobId)}${suffix}${tail}`;
  };

  const requestGet = async (path: string, signal?: AbortSignal, reportContract?: "current", reportTransport?: typeof MODEL_PROOF_CODEC,
    reportDelivery?: "deferred-v1", timeoutMs = 20000, reportBudget?: AbortSignal) => {
    signal?.throwIfAborted();
    // Validate and rewrite analyzer URLs before adding the representation query.
    // Other reads keep their existing contract and query string unchanged.
    let url = urlFor(path);
    if (reportContract) {
      const fragmentIndex = url.indexOf("#");
      const fragment = fragmentIndex < 0 ? "" : url.slice(fragmentIndex);
      const endpoint = fragmentIndex < 0 ? url : url.slice(0, fragmentIndex);
      const queryIndex = endpoint.indexOf("?");
      const query = new URLSearchParams(queryIndex < 0 ? "" : endpoint.slice(queryIndex + 1));
      query.set("report_contract", reportContract);
      if (reportTransport) query.set("report_transport", reportTransport);
      if (reportDelivery) query.set("report_delivery", reportDelivery);
      url = `${queryIndex < 0 ? endpoint : endpoint.slice(0, queryIndex)}?${query}${fragment}`;
    }
    const bodyIdleController = reportBudget ? new AbortController() : undefined;
    const timeout = bodyIdleController ? AbortSignal.any([bodyIdleController.signal, reportBudget!])
      : AbortSignal.timeout(Math.max(1, Math.min(20000, Math.ceil(timeoutMs))));
    // Only reports have separate header and progress-sensitive body budgets.
    // Clearing this timer after headers prevents it from aborting a progressing body.
    const headerTimer = bodyIdleController ? setTimeout(() => bodyIdleController.abort(new DOMException(
      "报告服务 20 秒内没有返回响应，请重试读取。", "TimeoutError")), Math.max(1, Math.min(20000, Math.ceil(timeoutMs)))) : undefined;
    const context: ReadContext = { operation: operationFor(path), requestId: requestId(), httpStatus: null,
      startedAt: new Date().toISOString(), startedMs: performance.now(), bodyComplete: false, caller: signal, timeout, bodyIdleController, rows: [] };
    try {
      const response = await fetchImpl(url, { method: "GET", cache: "no-store", headers: { "X-Request-ID": context.requestId }, signal: signal ? AbortSignal.any([signal, timeout]) : timeout });
      clearTimeout(headerTimer);
      signal?.throwIfAborted(); timeout.throwIfAborted();
      context.httpStatus = response.status;
      responseContexts.set(response, context);
      record(context, "headers", "success");
      return response;
    } catch (error) { readFailure(context, "headers", error); throw error; }
    finally { clearTimeout(headerTimer); }
  };
  const get = <T,>(path: string, signal?: AbortSignal, reportContract?: "current", reportTransport?: typeof MODEL_PROOF_CODEC) =>
    requestGet(path, signal, reportContract, reportTransport).then(response => parseResponse<T>(response));

  async function demoResult(jobId: string, options: { endpoint?: string | null; signal?: AbortSignal; onPrepare?: () => void; maxWaitMs?: number } = {}): Promise<DemoResultResponse> {
    const budgetMs = typeof options.maxWaitMs === "number" && Number.isFinite(options.maxWaitMs)
      ? Math.max(1, Math.min(120000, options.maxWaitMs)) : 120000;
    const deadline = reportTiming.now() + budgetMs;
    const budget = new AbortController();
    const reportSignal = options.signal ? AbortSignal.any([options.signal, budget.signal]) : budget.signal;
    let lastContext: ReadContext | undefined;
    const timeout = (cause?: unknown) => {
      const error = new RallyMateApiError("报告读取已达到本次等待时限。请稍后继续读取，无需重新上传。", 425, undefined, cause !== undefined ? { cause } : undefined);
      // An in-flight failure already owns its request diagnostics. Do not attach
      // the preceding 202 response's request ID to that newer failure.
      if (cause === undefined) record(lastContext, "json", "failure", "preparation_timeout", error);
      return error;
    };
    const totalTimer = setTimeout(() => budget.abort(new DOMException("报告读取达到总等待时限。", "TimeoutError")), budgetMs);
    try {
    let preparing = false;
    for (;;) {
      options.signal?.throwIfAborted();
      const remaining = deadline - reportTiming.now();
      if (remaining <= 0) throw timeout();
      let response: Response;
      try { response = await requestGet(options.endpoint || reportPath(jobId, "/demo-result"), options.signal, "current", MODEL_PROOF_CODEC, "deferred-v1", remaining, budget.signal); }
      catch (error) { options.signal?.throwIfAborted(); if (reportTiming.now() >= deadline) throw timeout(error); throw error; }
      lastContext = responseContexts.get(response);
      const payload = await parseResponse<Record<string, unknown>>(response, { maxBytes: MAX_REPORT_WIRE_BYTES, signal: reportSignal });
      options.signal?.throwIfAborted();
      if (reportTiming.now() >= deadline) throw timeout();
      if (response.status === 202) {
        if (payload.job_id !== jobId || payload.status !== "preparing" || payload.report_delivery !== "deferred-v1"
          || !Number.isSafeInteger(payload.retry_after_ms) || Number(payload.retry_after_ms) < 0) {
          const error = new RallyMateApiError("报告准备响应与当前任务不匹配，请重新读取。", 422);
          record(lastContext, "json", "failure", "response_contract", error);
          throw error;
        }
        preparing = true; options.onPrepare?.();
        await reportTiming.wait(Math.min(deadline - reportTiming.now(), Math.max(1000, Math.min(3000, Number(payload.retry_after_ms)))), reportSignal);
        continue;
      }
      if (response.status !== 200 || payload.status === "preparing" || "report_delivery" in payload
        || preparing && (payload.job_id !== jobId || payload.status !== "ready")) {
        const error = new RallyMateApiError("服务未返回当前任务的完整报告，请重新读取。", 422);
        record(lastContext, "json", "failure", "response_contract", error);
        throw error;
      }
      try {
        // Allow the loading stage and stop-waiting action to paint before the
        // synchronous proof decoder. Preserve all validation and normalization.
        await waitForReport(0, reportSignal);
        reportSignal.throwIfAborted();
        const result = normalizeMeasurementResult(unpackApiReport(payload) as DemoResultResponse);
        if (reportTiming.now() >= deadline) throw timeout();
        record(lastContext, "decode", "success");
        return result;
      } catch (error) { record(lastContext, "decode", "failure", "decode", error); throw error; }
    }
    } catch (error) {
      options.signal?.throwIfAborted();
      if (budget.signal.aborted) throw timeout(error);
      throw error;
    } finally { clearTimeout(totalTimer); }
  }

  return {
    config: resolvedConfig,
    getCapabilities: (signal) => get<ScorecardCapabilities>(resolvedConfig.scorecardPath, signal),
    getMeta: (signal) => get<RallyMateMetaResponse>("/v1/meta", signal),
    getTechniques: (signal) => get<TechniqueCatalogResponse>(resolvedConfig.techniquesPath, signal),
    scorecard: (request, signal) =>
      fetchImpl(urlFor(resolvedConfig.scorecardPath), {
        method: "POST",
        headers: { "content-type": "application/json", "X-Request-ID": requestId() },
        body: JSON.stringify(request),
        signal,
      }).then((response) => parseResponse<ScorecardResponse>(response)),
    submitVideo: (file, options = {}) => uploadResumable(file, options, urlFor, fetchImpl, parseResponse),
    getJob: (jobId, signal) => get<JobProgress>(reportPath(jobId), signal, "current"),
    getMeshPreview: (jobId, signal) => get(reportPath(jobId, "/mesh-preview"), signal),
    cancelJob: (jobId, signal) => fetchImpl(urlFor(reportPath(jobId, "/cancel")), {
      method: "POST", cache: "no-store", headers: { "X-Request-ID": requestId() },
      signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(20000)]) : AbortSignal.timeout(20000),
    }).then(parseResponse<JobProgress>).then(job => {
      if (job.id !== jobId || !["queued", "running", "processing", "succeeded", "completed", "failed", "cancelled"].includes(job.status) ||
        ["queued", "running", "processing"].includes(job.status) && job.cancel_requested !== true) throw new RallyMateApiError("取消响应未确认当前任务，请继续读取任务状态", 502);
      return job;
    }),
    getTechnicalReview: (jobId, signal) => get<unknown>(`${resolvedConfig.jobsPath}/${encodeURIComponent(jobId)}/technical-review`, signal).then(value => parseTechnicalReview(value, jobId)),
    saveTechnicalReview: (jobId, review, signal) => fetchImpl(urlFor(`${resolvedConfig.jobsPath}/${encodeURIComponent(jobId)}/technical-review`), {
      method: "POST", headers: { "content-type": "application/json", "X-Request-ID": requestId() }, body: JSON.stringify(review),
      signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(20000)]) : AbortSignal.timeout(20000),
    }).then(parseResponse<unknown>).then(value => parseTechnicalReview(value, jobId)),
    exportTechnicalReview: (jobId, signal) => get<Record<string, unknown>>(`${resolvedConfig.jobsPath}/${encodeURIComponent(jobId)}/technical-review/export`, signal),
    getDemoResult: demoResult,
    getFootCrossingCandidates: async (jobId, options = {}) => {
      options.signal?.throwIfAborted();
      const response = await requestGet(reportPath(jobId, "/foot-crossing-candidates"), options.signal);
      const raw = await parseResponse<unknown>(response, { maxBytes: FOOT_CROSSING_RESPONSE_BYTES, signal: options.signal });
      options.signal?.throwIfAborted();
      const context = responseContexts.get(response);
      context?.timeout.throwIfAborted();
      try {
        if (response.status !== 200 || response.redirected) throw new Error("Unexpected candidate response.");
        const parsed = parseFootCrossingResponse(raw, jobId);
        options.signal?.throwIfAborted();
        context?.timeout.throwIfAborted();
        record(context, "decode", "success");
        return parsed;
      } catch (cause) {
        options.signal?.throwIfAborted();
        context?.timeout.throwIfAborted();
        const error = new RallyMateApiError("独立换序线索与当前任务或支持的数据契约不匹配。", 422, undefined, { cause });
        record(context, "decode", "failure", "response_contract", error);
        throw error;
      }
    },
    getPosePreview: async (jobId, options = {}) => {
      const bounded = (value: number | undefined, fallback: number, max: number) => typeof value === "number" && Number.isFinite(value) ? Math.min(max, Math.max(0, Math.floor(value))) : fallback;
      const startMs = bounded(options.startMs, 0, Number.MAX_SAFE_INTEGER);
      const query = new URLSearchParams({ start_ms: String(startMs), duration_ms: String(Math.max(1, bounded(options.durationMs, 10000, 10000))), sample_limit: String(Math.max(1, bounded(options.sampleLimit, 600, 600))) });
      const response = await requestGet(`${resolvedConfig.jobsPath}/${encodeURIComponent(jobId)}/pose-preview?${query}`, options.signal);
      const raw = await parseResponse<PosePreviewResponse>(response);
      options.signal?.throwIfAborted();
      const context = responseContexts.get(response);
      let parsed: PosePreviewResponse | null;
      try { parsed = normalizePosePreview(raw, jobId); }
      catch (cause) {
        options.signal?.throwIfAborted();
        const error = new RallyMateApiError("动作标记数据无法解析。", 422, undefined, { cause });
        record(context, "decode", "failure", "pose_normalization", error);
        throw error;
      }
      options.signal?.throwIfAborted();
      if (!parsed || parsed.source.requested_start_ms !== startMs) {
        const error = new RallyMateApiError("人体姿态数据与当前回放不匹配。", 422);
        record(context, "decode", "failure", parsed ? "pose_window_mismatch" : "pose_normalization", error);
        throw error;
      }
      record(context, "decode", "success");
      // Keep the loader contract and the hook's independent identity/time guards.
      return raw;
    },
    getTrajectory: (jobId, options = {}) => {
      const endpoint = options.endpoint || `${resolvedConfig.jobsPath}/${encodeURIComponent(jobId)}/trajectory`;
      const query = new URLSearchParams();
      if (options.sampleLimit !== undefined) query.set("sample_limit", String(options.sampleLimit));
      if (options.predictionHorizonMs !== undefined) query.set("prediction_horizon_ms", String(options.predictionHorizonMs));
      const suffix = query.toString();
      return get<TrajectoryPreviewResponse>(suffix ? `${endpoint}${endpoint.includes("?") ? "&" : "?"}${suffix}` : endpoint, options.signal);
    },
    getTechniqueAssessment: (jobId, options = {}) =>
      get<TechniqueAssessmentResponse>(options.endpoint || reportPath(jobId, "/technique-assessment"), options.signal, "current"),
  };
}

export const apiClient = createApiClient();
