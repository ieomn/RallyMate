export type WorkspaceView = "overview" | "sessions" | "analysis" | "guide";

export type WorkspaceLocation = {
  view: WorkspaceView;
  jobId: string | null;
};

const VIEWS: readonly WorkspaceView[] = ["overview", "sessions", "analysis", "guide"];
const JOB_ID = /^[a-zA-Z0-9_-]{8,80}$/;

function validJobId(value: unknown): string | null {
  return typeof value === "string" && JOB_ID.test(value) ? value : null;
}

/** An explicit view wins over a job deep link. Browser storage is not navigation. */
export function readWorkspaceLocation(search: string): WorkspaceLocation {
  const params = new URLSearchParams(search);
  const jobId = validJobId(params.get("job"));
  const requestedView = params.get("view");
  const view = VIEWS.includes(requestedView as WorkspaceView)
    ? requestedView as WorkspaceView
    : jobId ? "analysis" : "overview";
  return { view, jobId };
}

/** Keep the selected view explicit so refreshing overview cannot reopen a job. */
export function workspaceHref(view: WorkspaceView, jobId?: string | null): string {
  const params = new URLSearchParams({ view });
  const id = validJobId(jobId);
  if (view === "analysis" && id) params.set("job", id);
  return `/?${params.toString()}`;
}

function record(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown> : {};
}

function timestamp(value: unknown): string | null {
  // An unzoned date does not identify a reliable job instant.
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/i.test(value)) return null;
  const milliseconds = Date.parse(value);
  return Number.isFinite(milliseconds) ? new Date(milliseconds).toISOString() : null;
}

export type ReportDate = { iso: string; label: "分析完成" | "提交时间" };

/** Job timestamps describe analysis/submission, never the unknown recording date. */
export function reportDateOf(job: unknown): ReportDate | null {
  const source = record(job);
  for (const field of ["completed_at", "completedAt"]) {
    const iso = timestamp(source[field]);
    if (iso) return { iso, label: "分析完成" };
  }
  for (const field of ["created_at", "createdAt"]) {
    const iso = timestamp(source[field]);
    if (iso) return { iso, label: "提交时间" };
  }
  return null;
}

export function formatReportDate(value: unknown): string {
  const iso = timestamp(value);
  if (!iso) return "日期未提供";
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit",
  }).format(new Date(iso));
}

/** Use recorded video metadata; processing elapsed time is not video duration. */
export function reportVideoDurationMs(summary: unknown): number | null {
  const input = record(record(summary).input);
  const metadata = record(input.video ?? input.metadata);
  const duration = metadata.duration_ms;
  return typeof duration === "number" && Number.isFinite(duration) && duration >= 0 ? duration : null;
}

export function formatVideoDuration(durationMs: unknown): string {
  if (typeof durationMs !== "number" || !Number.isFinite(durationMs) || durationMs < 0) return "时长未提供";
  const totalSeconds = Math.floor(durationMs / 1000);
  const seconds = String(totalSeconds % 60).padStart(2, "0");
  const minutes = Math.floor(totalSeconds / 60);
  return minutes < 60
    ? `${minutes}:${seconds}`
    : `${Math.floor(minutes / 60)}:${String(minutes % 60).padStart(2, "0")}:${seconds}`;
}
