export type GatewayEnv = Record<string, unknown> & { RALLYMATE_API_ORIGIN?: string; RALLYMATE_INTERNAL_API_ORIGIN?: string; MIMO_API_KEY?: string; RALLYMATE_LOCAL_TUNNEL?: string; RALLYMATE_POSE_REQUEST_DIAGNOSTICS?: string };
export const isLoopback = (host: string) => ["localhost", "127.0.0.1", "[::1]"].includes(host);
const reply = (error: string, status: number) => Response.json({ error }, { status, headers: { "cache-control": "private, no-store" } });
const UPLOAD_CHUNK_BYTES = 4 * 1024 * 1024;

export function internalAnalysisOrigin(value: unknown): string | undefined {
  if (value === undefined || value === "") return;
  // The private Compose service is an explicit deployment setting, never a request target.
  if (value !== "http://api:8000" && value !== "http://api:8000/") throw new Error("invalid_internal_analysis_origin");
  return "http://api:8000";
}

export function isAnalysisRoute(pathname: string, method: string): boolean {
  const id = "[a-zA-Z0-9_-]{8,80}";
  if (method === "GET" && ["/health/live", "/health/ready", "/v1/meta", "/v1/techniques", "/v1/model-capabilities", "/v1/jobs", "/api/records", "/api/auth/me", "/api/admin/users", "/api/admin/stats"].includes(pathname)) return true;
  if (method === "POST" && ["/v1/jobs", "/v1/uploads", "/api/auth/register", "/api/auth/login", "/api/auth/logout", "/api/auth/change-password"].includes(pathname)) return true;
  if (method === "GET" && new RegExp(`^/v1/(?:uploads/${id}|jobs/${id}(?:/(?:demo-result|status|report-summary|report-overview|report-motion/[a-zA-Z0-9][a-zA-Z0-9_.-]{0,159}|report-actions(?:/[a-zA-Z0-9][a-zA-Z0-9_.-]{0,159})?|trajectory|pose-preview|mesh-preview|mesh-geometry|swing-kinematics|tennis-assessment|original-video|foot-crossing-candidates|technique-assessment|technical-review(?:/export)?|artifacts/(?:summary\\.json|frames\\.jsonl|indicator-features\\.jsonl|annotated\\.mp4|preview\\.jpg|sam3d-mesh(?:-side)?\\.mp4|sam3d-mesh-preview\\.jpg|sam3d-mesh-manifest\\.json)))?)$`).test(pathname)) return true;
  if (["GET", "HEAD"].includes(method) && new RegExp(`^/v1/jobs/${id}/artifacts/(?:analysis-report\\.html|scoring-loop-report\\.html)$`).test(pathname)) return true;
  if (method === "POST" && new RegExp(`^/(?:v1/jobs/${id}/(?:cancel|technical-review)|v1/uploads/${id}/complete|api/admin/users/${id}/reset-password)$`).test(pathname)) return true;
  if (method === "PUT" && new RegExp(`^/v1/uploads/${id}/chunks/[0-9]{1,6}$`).test(pathname)) return true;
  if (method === "PATCH" && new RegExp(`^/api/admin/users/${id}/status$`).test(pathname)) return true;
  return method === "DELETE" && new RegExp(`^/(?:v1/jobs|v1/uploads|api/admin/users)/${id}$`).test(pathname);
}

export function analysisRequestRejection(method: string, url: URL, headers: Headers, env: GatewayEnv = {}): Response | undefined {
  if (!isAnalysisRoute(url.pathname, method)) return reply("route_not_allowed", 404);
  const origin = headers.get("origin");
  const tunnelOrigin = env.RALLYMATE_LOCAL_TUNNEL === "1" ? `https://${url.host}` : null;
  const sessionCookie = /(?:^|;\s*)rallymate_session=[^;\s]+/.test(headers.get("cookie") ?? "");
  if (["POST", "PUT", "PATCH", "DELETE"].includes(method)
      && (headers.get("sec-fetch-site") === "cross-site" || (sessionCookie && !origin)
          || (origin && origin !== url.origin && origin !== tunnelOrigin))) return reply("origin_not_allowed", 403);
  // Liveness is the only anonymous probe. Readiness exposes deployment
  // diagnostics, so the caller must present the operator key itself; the
  // gateway never lends its own server credential to a public request.
  const publicRoute = ["/health/live", "/api/auth/register", "/api/auth/login"].includes(url.pathname);
  if (url.pathname === "/health/ready" && !headers.get("authorization")) return reply("authentication_required", 401);
  if (!publicRoute && !sessionCookie && !headers.get("authorization")) return reply("authentication_required", 401);
}

/** Separate origin response/read deadlines from a slow downstream report reader. */
function reportReadDeadline(caller: AbortSignal, totalMs = 120000) {
  const started = performance.now();
  const deadline = new AbortController();
  const signal = AbortSignal.any([caller, deadline.signal]);
  const expire = () => deadline.abort(new DOMException("Report transfer timed out", "TimeoutError"));
  const timer = (ms: number) => {
    const handle = setTimeout(expire, ms);
    // Node request timers should not keep a stopped server/test process alive.
    // Workers return numeric handles and do not expose unref.
    if (typeof handle === "object" && "unref" in handle) handle.unref();
    return handle;
  };
  let headersTimer: ReturnType<typeof setTimeout> | undefined = timer(30000);
  let idleTimer: ReturnType<typeof setTimeout> | undefined;
  const totalTimer = timer(totalMs);
  const stopRead = () => { clearTimeout(idleTimer); idleTimer = undefined; };
  const headersReceived = () => { clearTimeout(headersTimer); headersTimer = undefined; };
  const close = () => {
    headersReceived(); stopRead(); clearTimeout(totalTimer);
    signal.removeEventListener("abort", close);
  };
  signal.addEventListener("abort", close, { once: true });
  if (signal.aborted) close();
  return { signal, deadline: deadline.signal, headersReceived, close, stopRead,
    elapsedMs: () => Math.round((performance.now() - started) * 1000) / 1000,
    startRead: () => { stopRead(); idleTimer = timer(30000); },
    abort: (reason: unknown) => deadline.abort(reason) };
}

class ReportBodyLengthError extends Error {
  constructor() { super("Report response length mismatch"); this.name = "ReportBodyLengthError"; }
}

function trustedReportLength(headers: Headers): number | undefined {
  const encoding = headers.get("content-encoding");
  const declaredLength = headers.get("content-length");
  // RFC 9112 section 6.3: without Transfer-Encoding, valid Content-Length
  // defines the body boundary. Fetch may auto-decode an encoded response, so
  // encoded/unknown framing must still wait for the upstream done signal.
  return !headers.has("transfer-encoding") && (encoding === null || encoding.trim().toLowerCase() === "identity")
    && declaredLength !== null && /^(?:0|[1-9][0-9]*)$/.test(declaredLength)
    && Number.isSafeInteger(Number(declaredLength)) ? Number(declaredLength) : undefined;
}

function reportResponseBody(response: Response, timing: ReturnType<typeof reportReadDeadline>, request: Request) {
  const reader = response.body!.getReader();
  const expected = trustedReportLength(response.headers);
  let received = 0, failureLogged = false;
  const failure = (error: unknown, override?: "downstream_cancelled") => {
    if (failureLogged) return;
    failureLogged = true;
    try {
      const incoming = request.headers.get("x-request-id");
      const requestId = incoming && incoming.length <= 64 &&
        /^(?:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|web-[a-z0-9]{1,16}-[a-z0-9]{1,16})$/i.test(incoming) ? incoming : null;
      const cause = error instanceof Error ? error.cause as { code?: unknown } | undefined : undefined;
      const category = override ?? (error instanceof ReportBodyLengthError ? "length_mismatch"
        : request.signal.aborted ? "caller_abort" : timing.deadline.aborted ? "deadline"
        : cause?.code === "UND_ERR_SOCKET" ? "upstream_socket" : "upstream_error");
      console.warn(JSON.stringify({ event: "report_body_error", request_id: requestId,
        elapsed_ms: timing.elapsedMs(), bytes: received, expected_bytes: expected ?? null, error_category: category }));
    } catch { /* Diagnostics must never alter report forwarding. */ }
  };
  let onAbort: () => void;
  const close = () => { timing.close(); timing.signal.removeEventListener("abort", onAbort); };
  return new ReadableStream<Uint8Array>({
    start(controller) {
      onAbort = () => {
        failure(timing.signal.reason);
        controller.error(timing.signal.reason);
        void reader.cancel(timing.signal.reason).catch(() => {});
        close();
      };
      timing.signal.addEventListener("abort", onAbort, { once: true });
      if (timing.signal.aborted) onAbort();
      else if (expected === 0) {
        close(); controller.close(); void reader.cancel().catch(() => {});
      }
    },
    async pull(controller) {
      // Only an outstanding upstream read counts as idle. Time waiting for
      // downstream demand/backpressure is covered by the separate total cap.
      timing.startRead();
      try {
        const part = await reader.read();
        timing.stopRead();
        timing.signal.throwIfAborted();
        if (part.done) {
          if (expected !== undefined && received !== expected) throw new ReportBodyLengthError();
          close(); controller.close();
        } else {
          received += part.value.byteLength;
          if (expected !== undefined && received > expected) throw new ReportBodyLengthError();
          controller.enqueue(part.value);
          if (expected !== undefined && received === expected) {
            // Do not perform an unnecessary read after the HTTP body boundary.
            // A late socket-close error must not destroy queued gzip tail bytes.
            close(); controller.close(); void reader.cancel().catch(() => {});
          }
        }
      } catch (error) {
        failure(error); close(); controller.error(error);
        void reader.cancel(error).catch(() => {});
      }
    },
    async cancel(reason) {
      failure(reason, "downstream_cancelled");
      close(); timing.abort(reason);
      await reader.cancel(reason).catch(() => {});
    },
  });
}

type PoseDiagnosticPhase = "gateway_ingress" | "upstream_start" | "upstream_headers" | "upstream_timeout" | "caller_abort" | "upstream_aborted" | "upstream_error" | "gateway_error" | "gateway_return";
type PoseDiagnosticError = "none" | "http_error" | "timeout" | "caller_abort" | "abort_source_ambiguous" | "upstream_fetch_failed" | "gateway_response_failed" | "upstream_redirect" | "gateway_rejected";

/** Logs only observations up to returning the unchanged stream, never delivery or body completion. */
function poseRequestDiagnostics(request: Request, env: GatewayEnv, pathname: string) {
  try {
    if (request.method !== "GET" || !/^\/v1\/jobs\/[a-zA-Z0-9_-]{8,80}\/pose-preview$/.test(pathname)) return;
    // Node's gateway entry copies a fixed binding allowlist. Read only this flag
    // as a fallback; explicit bindings (including "0") always take precedence.
    const configured = env.RALLYMATE_POSE_REQUEST_DIAGNOSTICS;
    const enabled = configured !== undefined ? configured
      : typeof process !== "undefined" ? process.env.RALLYMATE_POSE_REQUEST_DIAGNOSTICS : undefined;
    if (enabled !== "1") return;
    const incomingId = request.headers.get("x-request-id");
    const requestId = incomingId && incomingId.length <= 64 &&
      /^(?:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|web-[a-z0-9]{1,16}-[a-z0-9]{1,16})$/i.test(incomingId) ? incomingId : null;
    const started = performance.now();
    let previous = started, sequence = 0;
    return (phase: PoseDiagnosticPhase, status: number | null, error: PoseDiagnosticError,
      signal?: AbortSignal, deadline?: AbortSignal) => {
      try {
        // Bound observations even if a later code path adds another phase.
        if (sequence >= 8) return;
        const now = performance.now();
        if (!Number.isFinite(started) || !Number.isFinite(now)) return;
        const elapsedMs = Math.round(Math.max(0, now - started) * 1000) / 1000;
        const phaseMs = Math.round(Math.max(0, now - previous) * 1000) / 1000;
        if (!Number.isFinite(elapsedMs) || !Number.isFinite(phaseMs)) return;
        previous = now;
        sequence++;
        console.info(JSON.stringify({
          event: "pose_request_diagnostic", layer: "gateway", route: "pose_preview", phase,
          request_id: requestId, utc: new Date().toISOString(), sequence,
          elapsed_ms: elapsedMs, since_previous_phase_ms: phaseMs,
          http_status: status !== null && Number.isInteger(status) && status >= 100 && status <= 599 ? status : null,
          caller_aborted: request.signal.aborted, upstream_aborted: signal?.aborted === true,
          deadline_expired: deadline?.aborted === true, error_category: error,
        }));
      } catch { /* Diagnostics must never change request forwarding or the returned response. */ }
    };
  } catch { return; }
}

async function readUploadChunk(request: Request, signal: AbortSignal): Promise<ArrayBuffer | Response> {
  const declaredLength = request.headers.get("content-length");
  if (declaredLength && /^\d+$/.test(declaredLength) && Number(declaredLength) > UPLOAD_CHUNK_BYTES) {
    void request.body?.cancel().catch(() => {});
    return reply("upload_chunk_too_large", 413);
  }
  if (!request.body) return reply("upload_chunk_empty", 400);
  if (signal.aborted) return reply("upload_chunk_interrupted", 408);
  const reader = request.body.getReader();
  // Bounded growth avoids allocating 4 MiB for every 128 KiB request while
  // retaining a hard cap even for forged lengths and tiny stream fragments.
  const hintedLength = declaredLength && /^\d+$/.test(declaredLength) ? Number(declaredLength) : 0;
  let bytes = new Uint8Array(hintedLength > 0 && hintedLength <= UPLOAD_CHUNK_BYTES ? hintedLength : 64 * 1024);
  let length = 0;
  const cancel = () => { void reader.cancel().catch(() => {}); };
  signal.addEventListener("abort", cancel, { once: true });
  try {
    while (true) {
      const part = await reader.read();
      if (signal.aborted) return reply("upload_chunk_interrupted", 408);
      if (part.done) break;
      if (length + part.value.byteLength > UPLOAD_CHUNK_BYTES) {
        cancel();
        return reply("upload_chunk_too_large", 413);
      }
      if (length + part.value.byteLength > bytes.byteLength) {
        const grown = new Uint8Array(Math.min(UPLOAD_CHUNK_BYTES, Math.max(length + part.value.byteLength, bytes.byteLength * 2)));
        grown.set(bytes.subarray(0, length));
        bytes = grown;
      }
      bytes.set(part.value, length);
      length += part.value.byteLength;
    }
    if (!length) return reply("upload_chunk_empty", 400);
    return length === bytes.byteLength ? bytes.buffer : bytes.buffer.slice(0, length);
  } catch {
    return reply("upload_chunk_interrupted", 408);
  } finally {
    signal.removeEventListener("abort", cancel);
    reader.releaseLock();
  }
}

export async function proxyAnalysis(request: Request, env: GatewayEnv): Promise<Response> {
  const url = new URL(request.url);
  const id = "[a-zA-Z0-9_-]{8,80}";
  const reportPath = new RegExp(`^/v1/jobs/${id}/artifacts/(?:analysis-report\\.html|scoring-loop-report\\.html)$`).test(url.pathname);
  const reportHead = reportPath && request.method === "HEAD";
  const demoResultRead = request.method === "GET" && new RegExp(`^/v1/jobs/${id}/demo-result$`).test(url.pathname);
  const diagnostic = poseRequestDiagnostics(request, env, url.pathname);
  let getDeadline: AbortSignal | undefined;
  let upstreamSignal: AbortSignal | undefined = undefined;
  let receivedHeaders = false;
  let reportTiming: ReturnType<typeof reportReadDeadline> | undefined;
  diagnostic?.("gateway_ingress", null, "none");
  const finish = (response: Response, error: PoseDiagnosticError = "none") => {
    diagnostic?.("gateway_return", response.status, error, upstreamSignal, getDeadline);
    if (reportHead && response.body) {
      void response.body.cancel().catch(() => {});
      return new Response(null, { status: response.status, headers: response.headers });
    }
    return response;
  };
  const chunkWrite = request.method === "PUT" && new RegExp(`^/v1/uploads/${id}/chunks/[0-9]{1,6}$`).test(url.pathname);
  const rejection = analysisRequestRejection(request.method, url, request.headers, env);
  if (rejection) return finish(rejection, "gateway_rejected");
  let internal: string | undefined;
  try { internal = internalAnalysisOrigin(env.RALLYMATE_INTERNAL_API_ORIGIN); }
  catch { return finish(reply("invalid_internal_analysis_origin", 503), "gateway_rejected"); }
  const origin = internal ?? env.RALLYMATE_API_ORIGIN;
  if (!origin) return finish(reply("analysis_service_not_configured", 503), "gateway_rejected");
  let base: URL;
  try {
    base = new URL(origin);
    if (base.username || base.password || base.search || base.hash || !["", "/"].includes(base.pathname) || !["https:", "http:"].includes(base.protocol)) throw new Error();
    const localTunnel = env.RALLYMATE_LOCAL_TUNNEL === "1" && base.protocol === "http:" && isLoopback(base.hostname);
    if (!internal && !isLoopback(url.hostname) && !localTunnel && (base.protocol !== "https:" || isLoopback(base.hostname))) throw new Error();
  } catch { return finish(reply("invalid_analysis_origin", 503), "gateway_rejected"); }
  const headers = new Headers();
  for (const name of ["content-type", "accept", "range", "if-range", "x-request-id", "x-chunk-sha256", "x-upload-acknowledgement", "idempotency-key", "cookie", "authorization", "origin", "x-csrf-token", "sec-fetch-site"]) {
    if (reportHead && ["range", "if-range"].includes(name)) continue;
    const value = request.headers.get(name); if (value) headers.set(name, value);
  }
  const compactPoseRead = request.method === "GET" && url.pathname.endsWith("/pose-preview") && url.searchParams.get("pose_transport") === "compact-v1";
  if (demoResultRead || compactPoseRead) headers.set("accept-encoding", "identity");
  if (demoResultRead || compactPoseRead) {
    // The pose client uses 20s header/idle and 90s total budgets. Let it own
    // those limits without a hidden 30s gateway cap on a progressing body.
    reportTiming = reportReadDeadline(request.signal, compactPoseRead ? 100000 : 120000);
    getDeadline = reportTiming.deadline;
  } else if (request.method === "GET" || reportHead) {
    // The bounded overview can validate a long video's saved records for up to
    // the API's 60-second budget. Let it return its own result/error while the
    // independent summary, score and video remain available. Cold action-score
    // recomputation has its own bounded 120s budget; ordinary GETs stay at 30s.
    getDeadline = AbortSignal.timeout(url.pathname.endsWith("/tennis-assessment") ? 120000
      : url.pathname.endsWith("/report-overview") ? 65000 : 30000);
  }
  // The chunk deadline covers both receiving and forwarding, and finishes before
  // the browser's 90-second attempt deadline so its existing retry can continue.
  // Non-chunk mutations retain an independent 100-second deadline so an in-flight
  // upload creation/completion or cancel is not aborted mid-commit on tab close.
  const signal = chunkWrite
    ? AbortSignal.any([request.signal, AbortSignal.timeout(80000)])
    // Keep caller cancellation active while fetch streams the response body too.
    : reportTiming ? reportTiming.signal : getDeadline
      ? AbortSignal.any([request.signal, getDeadline])
      : AbortSignal.timeout(["POST", "PUT", "PATCH", "DELETE"].includes(request.method) ? 100000 : 30000);
  upstreamSignal = signal;
  try {
    // The artifact API is GET-only. Report HEAD reuses its authenticated headers
    // without forwarding Range, consuming HTML, or returning a response body.
    const init: RequestInit & { duplex?: string } = { method: reportHead ? "GET" : request.method, headers, redirect: "manual", signal };
    if (chunkWrite) {
      const body = await readUploadChunk(request, signal);
      if (body instanceof Response) return finish(body);
      headers.set("content-length", String(body.byteLength));
      init.body = body;
    } else if (["POST", "PUT", "PATCH", "DELETE"].includes(request.method)) { init.body = request.body; init.duplex = "half"; }
    diagnostic?.("upstream_start", null, "none", signal, getDeadline);
    const response = await fetch(new URL(url.pathname + url.search, base), init);
    reportTiming?.headersReceived();
    if (reportHead) void response.body?.cancel().catch(() => {});
    receivedHeaders = true;
    diagnostic?.("upstream_headers", response.status, response.status >= 400 ? "http_error" : "none", signal, getDeadline);
    if (response.status >= 300 && response.status < 400) {
      reportTiming?.close();
      void response.body?.cancel().catch(() => {});
      return finish(reply("unexpected_upstream_redirect", 502), "upstream_redirect");
    }
    const output = new Headers({ "cache-control": "private, no-store", "x-content-type-options": "nosniff" });
    for (const name of ["content-type", "content-length", "content-range", "accept-ranges", "etag", "last-modified", "content-disposition", "retry-after"]) { const value = response.headers.get(name); if (value) output.set(name, value); }
    if (url.pathname.startsWith("/api/auth/")) {
      const cookieHeaders = response.headers as Headers & { getSetCookie?: () => string[]; getAll?: (name: string) => string[] };
      const cookies = cookieHeaders.getSetCookie?.() ?? cookieHeaders.getAll?.("Set-Cookie") ?? [];
      for (const cookie of cookies) output.append("set-cookie", cookie);
    }
    // Fetch can return decoded bytes with the origin's encoded length. Never
    // declare that length downstream when report framing was not trusted.
    if (reportTiming && trustedReportLength(response.headers) === undefined) output.delete("content-length");
    const responseRequestId = response.headers.get("x-request-id");
    if (responseRequestId && responseRequestId === request.headers.get("x-request-id") && responseRequestId.length <= 64 &&
        !/[^A-Za-z0-9-]/.test(responseRequestId) && /^(?:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|web-[a-z0-9]{1,16}-[a-z0-9]{1,16})$/i.test(responseRequestId)) output.set("x-request-id", responseRequestId);
    const body = reportTiming && response.body ? reportResponseBody(response, reportTiming, request) : response.body;
    if (!body) reportTiming?.close();
    return finish(new Response(reportHead ? null : body, { status: response.status, headers: output }), response.status >= 400 ? "http_error" : "none");
  } catch {
    reportTiming?.close();
    // Signal state is observable; exception messages/reasons and body state are not logged.
    const error: PoseDiagnosticError = receivedHeaders ? "gateway_response_failed"
      : request.signal.aborted && getDeadline?.aborted ? "abort_source_ambiguous"
      : request.signal.aborted ? "caller_abort" : getDeadline?.aborted ? "timeout" : "upstream_fetch_failed";
    const phase: PoseDiagnosticPhase = receivedHeaders ? "gateway_error" : error === "caller_abort" ? "caller_abort" : error === "timeout" ? "upstream_timeout"
      : error === "abort_source_ambiguous" ? "upstream_aborted" : "upstream_error";
    diagnostic?.(phase, null, error, signal, getDeadline);
    return finish(chunkWrite && signal.aborted
      ? reply("upload_chunk_interrupted", 408)
      : reply("analysis_service_unavailable", 502), error);
  }
}
