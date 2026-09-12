import type {
  DemoResultResponse,
  JobProgress,
  JobSubmission,
  RallyMateApiConfig,
  ScorecardCapabilities,
  ScorecardRequest,
  ScorecardResponse,
  SubmitVideoOptions,
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
  return {
    baseUrl:
      processEnv?.NEXT_PUBLIC_RALLYMATE_API_URL ??
      processEnv?.NEXT_PUBLIC_API_BASE_URL ??
      processEnv?.NEXT_PUBLIC_API_URL ??
      viteEnv?.VITE_RALLYMATE_API_URL ??
      viteEnv?.VITE_API_BASE_URL ??
      viteEnv?.VITE_API_URL,
    scorecardPath:
      processEnv?.NEXT_PUBLIC_RALLYMATE_SCORECARD_PATH ?? viteEnv?.VITE_RALLYMATE_SCORECARD_PATH,
    jobsPath:
      processEnv?.NEXT_PUBLIC_RALLYMATE_JOBS_PATH ?? viteEnv?.VITE_RALLYMATE_JOBS_PATH,
  };
};

const trimSlashes = (value: string) => value.replace(/\/+$/, "");
const ensurePath = (value: string, fallback: string) => {
  const path = value.trim() || fallback;
  return path.startsWith("/") ? path : `/${path}`;
};

export function getApiConfig(): RallyMateApiConfig {
  const env = readPublicEnv();
  return {
    baseUrl: trimSlashes(env.baseUrl ?? ""),
    scorecardPath: ensurePath(env.scorecardPath ?? "", "/api/scorecard"),
    jobsPath: ensurePath(env.jobsPath ?? "", "/v1/jobs"),
  };
}

export interface RallyMateApiClient {
  readonly config: RallyMateApiConfig;
  getCapabilities(signal?: AbortSignal): Promise<ScorecardCapabilities>;
  scorecard(request: ScorecardRequest, signal?: AbortSignal): Promise<ScorecardResponse>;
  submitVideo(file: File | Blob, options?: SubmitVideoOptions): Promise<JobSubmission>;
  getJob(jobId: string, signal?: AbortSignal): Promise<JobProgress>;
  getDemoResult(jobId: string, signal?: AbortSignal): Promise<DemoResultResponse>;
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
          : `RallyMate API request failed (${response.status})`;
      throw new RallyMateApiError(message, response.status, payload);
    }
    return payload as T;
  }

  const get = <T>(path: string, signal?: AbortSignal) =>
    fetchImpl(urlFor(path), { method: "GET", signal }).then((response) => parseResponse<T>(response));

  return {
    config: resolvedConfig,
    getCapabilities: (signal) => get<ScorecardCapabilities>(resolvedConfig.scorecardPath, signal),
    scorecard: (request, signal) =>
      fetchImpl(urlFor(resolvedConfig.scorecardPath), {
        method: "POST",
        headers: { "content-type": "application/json" },
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
          body: form,
          signal: options.signal,
        }),
      );
    },
    getJob: (jobId, signal) => get<JobProgress>(`${resolvedConfig.jobsPath}/${encodeURIComponent(jobId)}`, signal),
    getDemoResult: (jobId, signal) =>
      get<DemoResultResponse>(`${resolvedConfig.jobsPath}/${encodeURIComponent(jobId)}/demo-result`, signal),
  };
}

export const apiClient = createApiClient();

