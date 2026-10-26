import type { TrajectoryPreviewResponse } from "./api-types";

export type CourtPoint = { x: number; y: number; timestamp_ms: number; source: "observed" | "interpolated"; segment_id: string; track_id?: number };
export type CourtProjection = { status: "calibrated" | "unavailable"; coordinate_space: "court_normalized"; semantics: "ground_plane_projection_not_ball_landing"; reason_zh: string; points: CourtPoint[] };
export type Corner = { x: number; y: number };
export type CourtCalibrationWindow = { start_timestamp_ms: number; end_timestamp_ms: number };
// ITF court plan: 10.97 × 23.77 m; singles width 8.23 m; service line 6.40 m from net.
// https://www.itftennis.com/media/7221/2026-rules-of-tennis-english.pdf
const singlesEdge = (10.97 - 8.23) / (2 * 10.97);
const serviceEdge = .5 - 6.4 / 23.77;
export const courtReferenceRegions = {
  doubles: { label: "双打场地外四角", hint: "点选两条底线与双打边线的四个交点。", targets: [{ x: 0, y: 0 }, { x: 1, y: 0 }, { x: 1, y: 1 }, { x: 0, y: 1 }] },
  singles: { label: "单打场地四角", hint: "点选两条底线与单打边线的四个交点。", targets: [{ x: singlesEdge, y: 0 }, { x: 1 - singlesEdge, y: 0 }, { x: 1 - singlesEdge, y: 1 }, { x: singlesEdge, y: 1 }] },
  service: { label: "发球区四角（外角未拍全时）", hint: "点选远、近两条发球线与单打边线的四个交点，围住全部四个发球格。不要点球网顶端或网柱。", targets: [{ x: singlesEdge, y: serviceEdge }, { x: 1 - singlesEdge, y: serviceEdge }, { x: 1 - singlesEdge, y: 1 - serviceEdge }, { x: singlesEdge, y: 1 - serviceEdge }] },
} satisfies Record<string, { label: string; hint: string; targets: Corner[] }>;
export type CourtReferenceRegion = keyof typeof courtReferenceRegions;
const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const record = (value: unknown): Record<string, unknown> => value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};

/** Reject crossed, tiny and nearly collinear selections before fitting a homography. */
export function courtHomography(corners: Corner[], region: CourtReferenceRegion = "doubles"): number[] | null {
  if (!Object.hasOwn(courtReferenceRegions, region)) return null;
  if (corners.length !== 4 || corners.some(point => !finite(point.x) || !finite(point.y) || point.x < 0 || point.x > 1 || point.y < 0 || point.y > 1)) return null;
  const crosses = corners.map((a, index) => { const b = corners[(index + 1) % 4], c = corners[(index + 2) % 4]; return (b.x - a.x) * (c.y - b.y) - (b.y - a.y) * (c.x - b.x); });
  if (crosses.some(value => value <= .001)) return null;
  const area = Math.abs(corners.reduce((sum, a, index) => { const b = corners[(index + 1) % 4]; return sum + a.x * b.y - b.x * a.y; }, 0)) / 2;
  if (area < .01) return null;
  const destinations = courtReferenceRegions[region].targets;
  const rows = corners.flatMap(({ x, y }, index) => { const { x: u, y: v } = destinations[index]; return [[x, y, 1, 0, 0, 0, -u * x, -u * y, u], [0, 0, 0, x, y, 1, -v * x, -v * y, v]]; });
  for (let column = 0; column < 8; column++) {
    let pivot = column;
    for (let row = column + 1; row < 8; row++) if (Math.abs(rows[row][column]) > Math.abs(rows[pivot][column])) pivot = row;
    if (Math.abs(rows[pivot][column]) < 1e-10) return null;
    [rows[pivot], rows[column]] = [rows[column], rows[pivot]];
    const scale = rows[column][column];
    rows[column] = rows[column].map(value => value / scale);
    for (let row = 0; row < 8; row++) if (row !== column) { const factor = rows[row][column]; rows[row] = rows[row].map((value, index) => value - factor * rows[column][index]); }
  }
  const matrix = [...rows.map(row => row[8]), 1];
  return matrix.every(finite) ? matrix : null;
}

export function projectCourtPoint(point: Corner, matrix: number[]): Corner | null {
  if (matrix.length !== 9 || !matrix.every(finite) || !finite(point.x) || !finite(point.y)) return null;
  const denominator = matrix[6] * point.x + matrix[7] * point.y + matrix[8];
  if (Math.abs(denominator) < 1e-8) return null;
  const x = (matrix[0] * point.x + matrix[1] * point.y + matrix[2]) / denominator;
  const y = (matrix[3] * point.x + matrix[4] * point.y + matrix[5]) / denominator;
  return finite(x) && finite(y) && x >= -.5 && x <= 1.5 && y >= -.25 && y <= 1.25 ? { x, y } : null;
}

export function calibrationWindowAt(trajectory: TrajectoryPreviewResponse | null, timestamp: number): CourtCalibrationWindow | null {
  if (!finite(timestamp) || timestamp < 0) return null;
  const windows = trajectory?.source.court_calibration_windows;
  return Array.isArray(windows) ? windows.find(window => finite(window.start_timestamp_ms) && finite(window.end_timestamp_ms) && window.start_timestamp_ms >= 0 && window.end_timestamp_ms >= window.start_timestamp_ms && timestamp >= window.start_timestamp_ms && timestamp <= window.end_timestamp_ms) ?? null : null;
}

export function courtProjectionOf(trajectory: TrajectoryPreviewResponse | null, manualMatrix?: number[] | null, window?: CourtCalibrationWindow | null): CourtProjection {
  const fallback: CourtProjection = { status: "unavailable", coordinate_space: "court_normalized", semantics: "ground_plane_projection_not_ball_landing", reason_zh: "尚未定位球场四角。可展开球场设置，在固定机位画面上标定后查看俯视模拟。", points: [] };
  const manual = manualMatrix?.length === 9 && manualMatrix.every(finite) ? { ...fallback, status: "calibrated" as const, reason_zh: "场地标定已保存，等待球路数据。" } : null;
  if (!trajectory) return manual ?? fallback;
  const raw = record(trajectory.court_projection);
  if (!manualMatrix && raw.status === "calibrated" && raw.coordinate_space === fallback.coordinate_space && raw.semantics === fallback.semantics && Array.isArray(raw.points)) {
    const points = raw.points.map(record).filter(point => finite(point.x) && finite(point.y) && point.x >= -.5 && point.x <= 1.5 && point.y >= -.25 && point.y <= 1.25 && finite(point.timestamp_ms) && point.timestamp_ms >= 0 && ["observed", "interpolated"].includes(String(point.source)) && typeof point.segment_id === "string" && point.segment_id.length > 0).map(point => ({ x: Number(point.x), y: Number(point.y), timestamp_ms: Number(point.timestamp_ms), source: point.source as CourtPoint["source"], segment_id: String(point.segment_id), ...(finite(point.track_id) ? { track_id: point.track_id } : {}) }));
    return { ...fallback, status: "calibrated", reason_zh: typeof raw.reason_zh === "string" ? raw.reason_zh : "由有效球场标定得到地面投影。", points: points.sort((a, b) => a.timestamp_ms - b.timestamp_ms) };
  }
  if (!manualMatrix) return { ...fallback, reason_zh: typeof raw.reason_zh === "string" && raw.reason_zh ? raw.reason_zh : fallback.reason_zh };
  if (!manual) return fallback;
  if (!window || !finite(window.start_timestamp_ms) || !finite(window.end_timestamp_ms) || window.end_timestamp_ms < window.start_timestamp_ms) return { ...manual, reason_zh: "该画面缺少稳定机位证据。请在原视频中换到机位稳定的片段重新取图标定。" };
  if (!["normalized_frame_0_1", "normalized_image", "image_normalized"].includes(trajectory.source.coordinate_space)) return { ...fallback, reason_zh: "当前球路不是已知的归一化画面坐标，无法进行安全的场地投影。" };
  const validPoint = (point: { x: number; y: number; timestamp_ms: number; source?: string }) => finite(point.x) && finite(point.y) && point.x >= 0 && point.x <= 1 && point.y >= 0 && point.y <= 1 && finite(point.timestamp_ms) && point.timestamp_ms >= window.start_timestamp_ms && point.timestamp_ms <= window.end_timestamp_ms && (point.source === undefined || point.source === "observed" || point.source === "interpolated");
  const reconstructed = Array.isArray(trajectory.ball?.reconstruction?.segments) ? trajectory.ball.reconstruction.segments.filter(segment => (typeof segment.segment_id === "string" && segment.segment_id.length > 0 || finite(segment.segment_id)) && Array.isArray(segment.points)).map(segment => ({ id: String(segment.segment_id), points: segment.points.filter(validPoint) })).filter(segment => segment.points.length > 0) : [];
  const segments = reconstructed.length ? reconstructed : [{ id: "observed", points: (Array.isArray(trajectory.ball?.observed) ? trajectory.ball.observed : []).filter(point => point.source !== "interpolated" && validPoint(point)) }];
  const points = segments.flatMap(segment => segment.points.flatMap(point => {
    if (point.timestamp_ms < window.start_timestamp_ms || point.timestamp_ms > window.end_timestamp_ms) return [];
    const projected = projectCourtPoint(point, manualMatrix);
    return projected && finite(point.timestamp_ms) && point.timestamp_ms >= 0 ? [{ ...projected, timestamp_ms: point.timestamp_ms, source: point.source === "interpolated" ? "interpolated" as const : "observed" as const, segment_id: segment.id, ...(finite(point.track_id) ? { track_id: point.track_id } : {}) }] : [];
  })).sort((a, b) => a.timestamp_ms - b.timestamp_ms);
  return { ...fallback, status: "calibrated", reason_zh: "手工标定只用于所选画面所在的稳定机位片段；球路为地面投影，不参与评分。", points };
}

/** Never bridge tracking segments, source gaps, or duplicate timestamps. */
export function courtPaths(points: CourtPoint[], atMs: number, trailMs = 1200): CourtPoint[][] {
  const visible = points.filter(point => point.timestamp_ms <= atMs && point.timestamp_ms >= atMs - trailMs);
  const paths: CourtPoint[][] = [];
  const grouped = new Map<string, CourtPoint[]>();
  for (const point of visible) {
    const key = JSON.stringify([point.segment_id, point.track_id ?? null]);
    grouped.set(key, [...(grouped.get(key) ?? []), point]);
  }
  for (const group of grouped.values()) {
    let path: CourtPoint[] = [];
    for (const point of group) {
      const previous = path.at(-1);
      if (!previous || point.timestamp_ms <= previous.timestamp_ms || point.timestamp_ms - previous.timestamp_ms > 250) { path = [point]; paths.push(path); }
      else path.push(point);
    }
  }
  return paths;
}

/** Keep the requested video time; clamping the slider must never manufacture a ball observation. */
export function courtPlaybackFrame(points: CourtPoint[], atMs: number | null, window?: CourtCalibrationWindow | null) {
  const start = points[0]?.timestamp_ms ?? 0;
  const end = points.at(-1)?.timestamp_ms ?? start;
  const current = finite(atMs) && atMs >= 0 ? atMs : start;
  const sliderTime = Math.max(start, Math.min(current, end));
  const outsideRange = current < start || current > end || !!window && (current < window.start_timestamp_ms || current > window.end_timestamp_ms);
  const paths = outsideRange ? [] : courtPaths(points, current);
  const latest = paths.flatMap(path => path.length ? [path[path.length - 1]] : []).sort((a, b) => b.timestamp_ms - a.timestamp_ms)[0];
  const ball = latest && current - latest.timestamp_ms <= 250 ? latest : null;
  // A trail without a recent observation would imply tracking continued through a detection gap.
  return { start, end, current, sliderTime, outsideRange, ball, paths: ball ? paths : [] };
}
