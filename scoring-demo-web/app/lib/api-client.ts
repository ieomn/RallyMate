import type {
  DemoResultResponse,
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
} from "./api-types";
import { RallyMateApiError } from "./api-types";

export { RallyMateApiError } from "./api-types";
export type { JobProgress } from "./api-types";

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
  readonly config: RallyMateApiConfig;
  getCapabilities(signal?: AbortSignal): Promise<ScorecardCapabilities>;
  getMeta(signal?: AbortSignal): Promise<RallyMateMetaResponse>;
  getTechniques(signal?: AbortSignal): Promise<TechniqueCatalogResponse>;
  scorecard(request: ScorecardRequest, signal?: AbortSignal): Promise<ScorecardResponse>;
  submitVideo(file: File | Blob, options?: SubmitVideoOptions): Promise<JobSubmission>;
  getJob(jobId: string, signal?: AbortSignal): Promise<JobProgress>;
  getDemoResult(jobId: string, signal?: AbortSignal): Promise<DemoResultResponse>;
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
): RallyMateApiClient {
  const resolvedConfig = { ...getApiConfig(), ...config };
  const urlFor = (path: string) => {
    if (/^https?:\/\//i.test(path)) return path;
    return `${resolvedConfig.baseUrl}${ensurePath(path, "/")}`;
  };

  async function parseResponse<T>(response: Response): Promise<T> {
    const text = await response.text();
    let payload: unknown = undefined;
    if (text) {
      try {
        payload = JSON.parse(text);
      } catch {
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
      throw new RallyMateApiError(message, response.status, payload);
    }
    return payload as T;
  }

  const get = <T>(path: string, signal?: AbortSignal) =>
    fetchImpl(urlFor(path), { method: "GET", headers: { "X-Request-ID": requestId() }, signal }).then((response) => parseResponse<T>(response));

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
    submitVideo: async (file, options = {}) => {
      const form = new FormData();
      const fileName = typeof File !== "undefined" && file instanceof File ? file.name : "rallymate-video.mp4";
      form.set("video", file, fileName);
      form.set("court_mode", options.courtMode ?? "auto");
      form.set("write_annotated_video", String(options.writeAnnotatedVideo ?? false));
      Object.entries(options.metadata ?? {}).forEach(([key, value]) => form.set(key, value));
      return parseResponse<JobSubmission>(
        await fetchImpl(urlFor(resolvedConfig.jobsPath), {
          method: "POST",
          headers: { "X-Request-ID": requestId() },
          body: form,
          signal: options.signal,
        }),
      );
    },
    getJob: (jobId, signal) => get<JobProgress>(`${resolvedConfig.jobsPath}/${encodeURIComponent(jobId)}`, signal),
    getDemoResult: (jobId, signal) =>
      get<DemoResultResponse>(`${resolvedConfig.jobsPath}/${encodeURIComponent(jobId)}/demo-result`, signal),
    getTrajectory: (jobId, options = {}) => {
      const endpoint = options.endpoint || `${resolvedConfig.jobsPath}/${encodeURIComponent(jobId)}/trajectory`;
      const query = new URLSearchParams();
      if (options.sampleLimit !== undefined) query.set("sample_limit", String(options.sampleLimit));
      if (options.predictionHorizonMs !== undefined) query.set("prediction_horizon_ms", String(options.predictionHorizonMs));
      const suffix = query.toString();
      return get<TrajectoryPreviewResponse>(suffix ? `${endpoint}${endpoint.includes("?") ? "&" : "?"}${suffix}` : endpoint, options.signal);
    },
    getTechniqueAssessment: (jobId, options = {}) =>
      get<TechniqueAssessmentResponse>(options.endpoint || `${resolvedConfig.jobsPath}/${encodeURIComponent(jobId)}/technique-assessment`, options.signal),
  };
}

export const apiClient = createApiClient();
