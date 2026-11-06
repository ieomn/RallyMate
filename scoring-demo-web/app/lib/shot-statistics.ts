export type ShotKind = "hit" | "bounce";
export type ShotStatus = "candidate" | "confirmed" | "dismissed";
export type ShotCourtPosition = { x: number; y: number; source: "manual" | "calibrated_ground_projection" };
export type ShotEvent = {
  id: string; kind: ShotKind; status: ShotStatus; timestamp_ms: number; frame_index: number | null;
  position_image: { x: number; y: number } | null; position_court: ShotCourtPosition | null;
  source: "trajectory_rule" | "manual"; reason_codes: string[];
};
export type ShotStatisticsResponse = {
  schema_version: "1.0.0"; job_id: string; status: "unreviewed" | "reviewed" | "partial" | "unavailable";
  revision: number;
  source: { video_sha256: string; frame_count: number; start_ms: number; end_ms: number; duration_ms: number; coordinate_space: "normalized_frame_0_1" };
  summary: { confirmed_hit_count: number; confirmed_bounce_count: number; mapped_bounce_count: number;
    candidate_hit_count: number; candidate_bounce_count: number; dismissed_count: number;
    count_semantics: "review_confirmed_events_not_total_video" };
  events: ShotEvent[]; limitations_zh: string[];
};
export type ShotReviewOperation =
  | { operation: "confirm" | "dismiss" | "restore" | "remove"; event_id: string; position_court?: { x: number; y: number } }
  | { operation: "add"; kind: ShotKind; timestamp_ms: number; position_court?: { x: number; y: number } };

const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const count = (value: unknown): value is number => Number.isSafeInteger(value) && Number(value) >= 0;
const object = (value: unknown): Record<string, unknown> | null => value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;

export function validShotCourtPosition(value: unknown): value is { x: number; y: number } {
  const point = object(value);
  return !!point && finite(point.x) && finite(point.y) && point.x >= -.5 && point.x <= 1.5 && point.y >= -.25 && point.y <= 1.25;
}

/** Accept only this job's bounded event ledger, never recursively found counts. */
export function parseShotStatistics(value: unknown, jobId: string, videoSha256?: string | null): ShotStatisticsResponse | null {
  const raw = object(value), source = object(raw?.source), summary = object(raw?.summary);
  if (!raw || raw.schema_version !== "1.0.0" || raw.job_id !== jobId || !jobId || !["unreviewed", "reviewed", "partial", "unavailable"].includes(String(raw.status))
    || !count(raw.revision) || !source || !summary || typeof source.video_sha256 !== "string" || !/^[a-f\d]{64}$/i.test(source.video_sha256)
    || videoSha256 && source.video_sha256.toLowerCase() !== videoSha256.toLowerCase()
    || source.coordinate_space !== "normalized_frame_0_1" || !count(source.frame_count)
    || !finite(source.start_ms) || !finite(source.end_ms) || !finite(source.duration_ms) || source.start_ms < 0 || source.end_ms < source.start_ms || source.duration_ms < 0
    || summary.count_semantics !== "review_confirmed_events_not_total_video"
    || !Array.isArray(raw.events) || raw.events.length > 10000 || !Array.isArray(raw.limitations_zh) || !raw.limitations_zh.every(item => typeof item === "string")) return null;
  const ids = new Set<string>();
  const events: ShotEvent[] = [];
  for (const input of raw.events) {
    const event = object(input);
    if (!event || typeof event.id !== "string" || !event.id || event.id.length > 200 || ids.has(event.id)
      || !["hit", "bounce"].includes(String(event.kind)) || !["candidate", "confirmed", "dismissed"].includes(String(event.status))
      || !["trajectory_rule", "manual"].includes(String(event.source)) || !finite(event.timestamp_ms)
      || event.timestamp_ms < source.start_ms || event.timestamp_ms > source.end_ms
      || event.frame_index !== null && !count(event.frame_index)
      || !Array.isArray(event.reason_codes) || !event.reason_codes.every(reason => typeof reason === "string")) return null;
    if (event.position_image !== null) {
      const point = object(event.position_image);
      if (!point || !finite(point.x) || !finite(point.y) || point.x < 0 || point.x > 1 || point.y < 0 || point.y > 1) return null;
    }
    if (event.position_court !== null) {
      const point = object(event.position_court);
      if (!point || !["manual", "calibrated_ground_projection"].includes(String(point.source)) || !validShotCourtPosition(point)
        || event.kind !== "bounce" || event.status !== "confirmed") return null;
    }
    ids.add(event.id); events.push(event as ShotEvent);
  }
  const expected = {
    confirmed_hit_count: events.filter(event => event.kind === "hit" && event.status === "confirmed").length,
    confirmed_bounce_count: events.filter(event => event.kind === "bounce" && event.status === "confirmed").length,
    mapped_bounce_count: events.filter(event => event.kind === "bounce" && event.status === "confirmed" && event.position_court).length,
    candidate_hit_count: events.filter(event => event.kind === "hit" && event.status === "candidate").length,
    candidate_bounce_count: events.filter(event => event.kind === "bounce" && event.status === "candidate").length,
    dismissed_count: events.filter(event => event.status === "dismissed").length,
  };
  for (const [key, expectedCount] of Object.entries(expected)) if (!count(summary[key]) || summary[key] !== expectedCount) return null;
  return { ...raw, source, summary, events: [...events].sort((a, b) => a.timestamp_ms - b.timestamp_ms || a.id.localeCompare(b.id)) } as ShotStatisticsResponse;
}

export function shotStatisticsCount(data: ShotStatisticsResponse | null, kind: ShotKind): number | null {
  if (!data || data.status === "unreviewed" || data.status === "unavailable") return null;
  if (data.status === "partial" && data.revision === 0 && !data.summary.confirmed_hit_count && !data.summary.confirmed_bounce_count) return null;
  return kind === "hit" ? data.summary.confirmed_hit_count : data.summary.confirmed_bounce_count;
}

export function formatShotTime(milliseconds: number): string {
  if (!finite(milliseconds) || milliseconds < 0) return "—";
  const tenths = Math.floor(milliseconds / 100);
  return `${Math.floor(tenths / 600)}:${String(Math.floor(tenths / 10) % 60).padStart(2, "0")}.${tenths % 10}`;
}

export function shotSeekDetail(event: Pick<ShotEvent, "timestamp_ms">, jobId: string) {
  return { timestampMs: event.timestamp_ms, jobId, preferOriginal: true as const };
}

export function shotCourtPoint(clientX: number, clientY: number, bounds: { left: number; top: number; width: number; height: number }): { x: number; y: number } | null {
  if (![clientX, clientY, bounds.left, bounds.top, bounds.width, bounds.height].every(finite) || bounds.width <= 0 || bounds.height <= 0) return null;
  const point = { x: (clientX - bounds.left) / bounds.width * 2 - .5, y: (clientY - bounds.top) / bounds.height * 1.5 - .25 };
  return validShotCourtPosition(point) ? point : null;
}

export function shotCourtDisplay(point: { x: number; y: number }) {
  return { left: `${(point.x + .5) / 2 * 100}%`, top: `${(point.y + .25) / 1.5 * 100}%` };
}
