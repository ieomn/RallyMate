// Allowlisted payloads also protect SDK-added fields and future integrations.
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const HEX = /^[0-9a-f]{16,32}$/i;
const ROUTE_PARTS = new Set(":id :route :private-file api v1 auth me login logout register change-password admin users stats status reset-password jobs uploads records advice annotate welcome account-status analysis history health ready progress events cancel report artifacts video pose diagnostics feedback export csrf metrics".split(" "));
const METHODS = new Set(["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]);

export const PRIVATE_DATA_COLLECTION = Object.freeze({
  userInfo: false, cookies: false, httpHeaders: false, httpBodies: [], urlQueryParams: false,
  graphQL: { document: false, variables: false }, genAI: { inputs: false, outputs: false },
  mcp: { inputs: false, outputs: false }, databaseQueryData: false, queues: false,
  stackFrameVariables: false, frameContextLines: 0,
});

export function monitoringSampleRate(value) {
  if (value === undefined || value === null || value === "") return 0.05;
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed >= 0 && parsed <= 1 ? parsed : 0.05;
}

export function monitoringLabel(value, fallback) {
  return typeof value === "string" && /^[a-zA-Z0-9_.@+-]{1,128}$/.test(value) ? value : fallback;
}

export function monitoringUser(id) {
  return typeof id === "string" && UUID.test(id) ? { id } : null;
}

export function safeUrl(value) {
  if (typeof value !== "string") return undefined;
  const path = value.replace(/^(GET|HEAD|POST|PUT|PATCH|DELETE|OPTIONS)\s+/, "");
  if (!/^(https?:\/\/|\/)/i.test(path)) return "/:route";
  try {
    const url = new URL(path, "https://rallymate.invalid");
    if (!["http:", "https:"].includes(url.protocol)) return "/:route";
    // Uploaded filenames and storage roots never belong in monitoring payloads.
    if (/(?:^|\/)(?:uploads|requests|runs)(?:\/|$)|\.(?:mp4|mov|avi|webm|mkv|json|csv|pdf)(?:\/|$)/i.test(url.pathname)) return "/:private-file";
    return "/" + url.pathname.split("/").filter(Boolean).map((part) => ROUTE_PARTS.has(part) ? part : ":id").join("/");
  } catch { return "/:route"; }
}

function sourceFile(value) {
  if (typeof value !== "string") return undefined;
  let path = value.replaceAll("\\", "/").split(/[?#]/, 1)[0];
  if (/^node:[a-z0-9_./-]+$/i.test(path)) return path;
  if (/(?:^|\/)(?:uploads|requests|runs)(?:\/|$)/i.test(path)) return undefined;
  if (!/\.(?:[cm]?js|jsx|tsx?)(?::\d+)?$/i.test(path)) return undefined;
  const marker = path.match(/(?:^|\/)((?:app|src|assets|dist|node_modules)\/.*)$/);
  path = marker ? marker[1] : path.slice(path.lastIndexOf("/") + 1);
  return /^[a-zA-Z0-9_./@+~$()[\]-]{1,250}$/.test(path) ? path : undefined;
}

function frame(value) {
  const filename = sourceFile(value.filename ?? value.abs_path);
  if (!filename) return null;
  const result = { filename };
  for (const key of ["lineno", "colno"]) if (Number.isInteger(value[key]) && value[key] >= 0) result[key] = value[key];
  if (typeof value.in_app === "boolean") result.in_app = value.in_app;
  if (typeof value.function === "string" && /^[a-zA-Z0-9_.$<> [\]-]{1,100}$/.test(value.function)) result.function = value.function;
  return result;
}

function trace(value = {}) {
  const result = {};
  for (const key of ["trace_id", "span_id", "parent_span_id"]) if (typeof value[key] === "string" && HEX.test(value[key])) result[key] = value[key];
  if (typeof value.op === "string" && /^(?:pageload|navigation|http|ui|resource|browser|function)(?:\.[a-z_]+)*$/.test(value.op)) result.op = value.op;
  if (typeof value.status === "string" && /^(?:ok|unknown_error|internal_error|cancelled|deadline_exceeded|not_found|permission_denied|unauthenticated|unavailable|invalid_argument|resource_exhausted)$/.test(value.status)) result.status = value.status;
  return result;
}

export function scrubBreadcrumb(value) {
  if (!["navigation", "fetch", "xhr", "http"].includes(value.category)) return null;
  const result = { category: value.category };
  if (Number.isFinite(value.timestamp)) result.timestamp = value.timestamp;
  const data = {};
  for (const key of ["url", "from", "to"]) if (typeof value.data?.[key] === "string") data[key] = safeUrl(value.data[key]);
  if (METHODS.has(value.data?.method)) data.method = value.data.method;
  if (Number.isInteger(value.data?.status_code)) data.status_code = value.data.status_code;
  result.data = data;
  return result;
}

export function scrubEvent(event) {
  const result = {};
  if (typeof event.event_id === "string" && /^[0-9a-f]{32}$/i.test(event.event_id)) result.event_id = event.event_id;
  if (event.type === "transaction") result.type = "transaction";
  for (const key of ["timestamp", "start_timestamp"]) if (Number.isFinite(event[key])) result[key] = event[key];
  if (["fatal", "error", "warning", "info", "debug"].includes(event.level)) result.level = event.level;
  if (["javascript", "node"].includes(event.platform)) result.platform = event.platform;
  for (const key of ["release", "environment", "dist"]) {
    const label = monitoringLabel(event[key]);
    if (label) result[key] = label;
  }
  const user = monitoringUser(event.user?.id);
  if (user) result.user = user;
  if (event.request) {
    result.request = { url: safeUrl(event.request.url) };
    if (METHODS.has(event.request.method)) result.request.method = event.request.method;
  }
  if (event.contexts?.trace) result.contexts = { trace: trace(event.contexts.trace) };
  if (Array.isArray(event.exception?.values)) {
    result.exception = { values: event.exception.values.slice(-5).map((value) => {
      const exception = { type: /^(?:[A-Z][a-zA-Z0-9]*(?:Error|Exception)|Error)$/.test(value.type) ? value.type : "Error", value: "Error details removed" };
      if (Array.isArray(value.stacktrace?.frames)) exception.stacktrace = { frames: value.stacktrace.frames.slice(-60).map(frame).filter(Boolean) };
      if (value.mechanism) exception.mechanism = { type: "generic", handled: value.mechanism.handled !== false };
      return exception;
    }) };
  } else if (event.type !== "transaction") result.message = "Frontend error";
  if (Array.isArray(event.breadcrumbs)) result.breadcrumbs = event.breadcrumbs.map(scrubBreadcrumb).filter(Boolean).slice(-30);
  if (event.transaction) result.transaction = safeUrl(event.transaction);
  if (Array.isArray(event.spans)) result.spans = event.spans.map((span) => ({
    ...trace(span), description: safeUrl(span.description),
    ...(Number.isFinite(span.start_timestamp) ? { start_timestamp: span.start_timestamp } : {}),
    ...(Number.isFinite(span.timestamp) ? { timestamp: span.timestamp } : {}),
  }));
  return result;
}

// Last-mile filtering covers envelopes that do not run through beforeSend (e.g. attachments).
export function privateTransport(factory) {
  return (options) => {
    const transport = factory(options);
    return {
      flush: (timeout) => transport.flush(timeout),
      send: (envelope) => {
        const items = envelope[1].filter(([header]) => ["event", "transaction"].includes(header.type))
          .map(([header, payload]) => [{ type: header.type }, scrubEvent(payload)]);
        if (!items.length) return Promise.resolve({ statusCode: 200 });
        const headers = {};
        if (/^[0-9a-f]{32}$/i.test(envelope[0].event_id)) headers.event_id = envelope[0].event_id;
        if (typeof envelope[0].sent_at === "string" && /^\d{4}-\d\d-\d\dT[\d:.]+Z$/.test(envelope[0].sent_at)) headers.sent_at = envelope[0].sent_at;
        return transport.send([headers, items]);
      },
    };
  };
}
