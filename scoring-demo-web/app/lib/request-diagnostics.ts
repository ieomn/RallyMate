/** Deliberately excludes URLs, headers, bodies, messages, stacks and cause text. */
export type RequestDiagnostic = Readonly<{
  operation: "job" | "report" | "trajectory" | "technique_assessment" | "pose_preview" | "technical_review" | "meta" | "other_read";
  stage: "headers" | "body" | "json" | "decode";
  outcome: "success" | "failure";
  category: "http" | "timeout" | "network" | "body_read" | "json_parse" | "response_contract" | "decode" | "preparation_timeout" | "pose_normalization" | "pose_window_mismatch" | null;
  requestId: string | null;
  httpStatus: number | null;
  startedAt: string;
  elapsedMs: number;
  bodyComplete: boolean;
}>;
export type RequestDiagnosticObserver = (diagnostic: RequestDiagnostic) => void;

/** Describe the next pending step using completed read events, without payloads. */
export function reportReadStage(diagnostic?: RequestDiagnostic): string {
  if (!diagnostic || diagnostic.operation !== "report") return "正在连接报告服务";
  if (diagnostic.category === "preparation_timeout") return "报告整理已达到本次等待时限";
  if (diagnostic.outcome === "failure") return "本次报告读取未成功";
  if (diagnostic.httpStatus === 202) return "服务器正在整理报告";
  if (diagnostic.stage === "headers") return "正在接收报告数据";
  if (diagnostic.stage === "body") return "正在解析报告数据";
  if (diagnostic.stage === "json") return "正在校验并解码报告证据";
  return "报告主体已读取";
}

const diagnostics = new WeakMap<object, readonly RequestDiagnostic[]>();
const operations = new Set(["job", "report", "trajectory", "technique_assessment", "pose_preview", "technical_review", "meta", "other_read"]);
const stages = new Set(["headers", "body", "json", "decode"]);
const categories = new Set(["http", "timeout", "network", "body_read", "json_parse", "response_contract", "decode", "preparation_timeout", "pose_normalization", "pose_window_mismatch"]);

export function safeRequestId(value: unknown): string | null {
  if (typeof value !== "string" || value.length > 64 || /[^A-Za-z0-9-]/.test(value)) return null;
  return /^(?:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|web-[a-z0-9]{1,16}-[a-z0-9]{1,16})$/i.test(value) ? value : null;
}

/** Rebuild from a fixed whitelist; never spread or serialize an Error object. */
export function safeRequestDiagnostic(value: unknown): RequestDiagnostic | null {
  if (!value || typeof value !== "object") return null;
  const keys = ["operation", "stage", "outcome", "category", "requestId", "httpStatus", "startedAt", "elapsedMs", "bodyComplete"];
  const row: Record<string, unknown> = {};
  try {
    for (const key of keys) {
      const descriptor = Object.getOwnPropertyDescriptor(value, key);
      if (!descriptor || !("value" in descriptor)) return null;
      row[key] = descriptor.value;
    }
  } catch { return null; }
  if (typeof row.operation !== "string" || !operations.has(row.operation) || typeof row.stage !== "string" || !stages.has(row.stage) ||
      typeof row.outcome !== "string" || !["success", "failure"].includes(row.outcome) || row.category !== null && (typeof row.category !== "string" || !categories.has(row.category)) ||
      row.requestId !== null && safeRequestId(row.requestId) === null ||
      row.httpStatus !== null && !(typeof row.httpStatus === "number" && Number.isInteger(row.httpStatus) && row.httpStatus >= 100 && row.httpStatus <= 599) ||
      typeof row.startedAt !== "string" || row.startedAt.length !== 24 || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/.test(row.startedAt) || !Number.isFinite(Date.parse(row.startedAt)) ||
      typeof row.elapsedMs !== "number" || !Number.isFinite(row.elapsedMs) || row.elapsedMs < 0 ||
      typeof row.bodyComplete !== "boolean") return null;
  return Object.freeze({
    operation: row.operation as RequestDiagnostic["operation"], stage: row.stage as RequestDiagnostic["stage"],
    outcome: row.outcome as RequestDiagnostic["outcome"], category: row.category as RequestDiagnostic["category"],
    requestId: safeRequestId(row.requestId), httpStatus: row.httpStatus as number | null,
    startedAt: row.startedAt, elapsedMs: row.elapsedMs, bodyComplete: row.bodyComplete,
  });
}

export function attachRequestDiagnostics(error: unknown, rows: readonly RequestDiagnostic[]): void {
  if (!error || typeof error !== "object") return;
  const safe = rows.slice(-8).map(safeRequestDiagnostic).filter((row): row is RequestDiagnostic => row !== null);
  diagnostics.set(error, Object.freeze(safe));
}

/** Follow only stored cause values, with cycle/depth bounds; no getters or error text. */
export function getRequestDiagnostics(error: unknown): readonly RequestDiagnostic[] {
  const seen = new Set<object>();
  for (let depth = 0; error && typeof error === "object" && depth < 8 && !seen.has(error); depth++) {
    seen.add(error);
    const rows = diagnostics.get(error);
    if (rows) return rows;
    try { error = Object.getOwnPropertyDescriptor(error, "cause")?.value; } catch { break; }
  }
  return Object.freeze([]);
}
