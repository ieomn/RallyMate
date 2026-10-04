import type { PosePreviewFrame, PosePreviewResponse } from "./api-types";

export const POSE_WINDOW_MS = 10000;
export const POSE_MAX_HOLD_MS = 100;
export const POSE_MIN_CONFIDENCE = .35;
const JOINTS = new Set(["nose", "left_eye", "right_eye", "left_ear", "right_ear", "left_shoulder", "right_shoulder", "left_elbow", "right_elbow", "left_wrist", "right_wrist", "left_hip", "right_hip", "left_knee", "right_knee", "left_ankle", "right_ankle", "head", "neck", "hip", "left_big_toe", "right_big_toe", "left_small_toe", "right_small_toe", "left_heel", "right_heel"]);
const object = (value: unknown): Record<string, unknown> => value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const positive = (value: unknown): number | null => finite(value) && value > 0 ? value : null;
export const poseWindowStart = (timestampMs: number) => Math.floor(Math.max(0, Number.isFinite(timestampMs) ? timestampMs : 0) / POSE_WINDOW_MS) * POSE_WINDOW_MS;

/** Source PTS can only drive the original video or an explicitly preserved replay. */
export function poseReplayTimingAllowed(isLocalOriginal: boolean, result: unknown): boolean {
  return isLocalOriginal || object(object(object(result).runtime).annotated_video_compatibility).timing_preserved === true;
}

/** Observations only: malformed/missing frames remain empty so previous poses expire. */
export function normalizePosePreview(value: unknown, jobId?: string): PosePreviewResponse | null {
  const raw = object(value), source = object(raw.source);
  if (raw.schema_version !== "1.0.0" || raw.result_kind !== "observed_pose_playback" || raw.coordinate_space !== "normalized_frame_0_1" || raw.joint_schema !== "halpe26_named" || typeof raw.job_id !== "string" || jobId && raw.job_id !== jobId || source.timestamp_semantics !== "source_frame_timestamp_ms" || !Array.isArray(raw.frames)) return null;
  if (!finite(source.requested_start_ms) || !finite(source.requested_end_ms) || source.requested_start_ms < 0 || source.requested_end_ms <= source.requested_start_ms || source.requested_end_ms - source.requested_start_ms > POSE_WINDOW_MS) return null;
  const minConfidence = finite(raw.min_keypoint_confidence) && raw.min_keypoint_confidence <= 1 ? Math.max(POSE_MIN_CONFIDENCE, raw.min_keypoint_confidence) : POSE_MIN_CONFIDENCE;
  const frames: PosePreviewFrame[] = [];
  for (const value of raw.frames.slice(0, 600)) {
    const row = object(value);
    if (!finite(row.timestamp_ms) || row.timestamp_ms < Math.max(0, source.requested_start_ms - POSE_MAX_HOLD_MS) || row.timestamp_ms >= source.requested_end_ms) continue;
    const validUntil = finite(row.valid_until_ms) && row.valid_until_ms > row.timestamp_ms ? Math.min(row.valid_until_ms, row.timestamp_ms + POSE_MAX_HOLD_MS) : row.timestamp_ms;
    const track = Number.isSafeInteger(row.person_track_id) && Number(row.person_track_id) >= 0 ? Number(row.person_track_id) : null;
    const epoch = Number.isSafeInteger(row.selection_epoch) && Number(row.selection_epoch) >= 0 ? Number(row.selection_epoch) : -1;
    const selected = epoch >= 0 && (row.selection_status === "selected" && track !== null || row.selection_status === "single_visible_person");
    const points = new Map<string, PosePreviewFrame["keypoints"][number]>();
    const duplicates = new Set<string>();
    for (const value of selected && Array.isArray(row.keypoints) ? row.keypoints.slice(0, 26) : []) {
      const point = object(value);
      if (typeof point.name !== "string" || !JOINTS.has(point.name)) continue;
      if (points.has(point.name)) { duplicates.add(point.name); continue; }
      if (!finite(point.x) || point.x < 0 || point.x > 1 || !finite(point.y) || point.y < 0 || point.y > 1 || !finite(point.confidence) || point.confidence < minConfidence || point.confidence > 1) continue;
      points.set(point.name, { name: point.name, x: point.x, y: point.y, confidence: point.confidence });
    }
    for (const name of duplicates) points.delete(name);
    frames.push({ frame_index: Number.isSafeInteger(row.frame_index) ? Number(row.frame_index) : -1, timestamp_ms: row.timestamp_ms, valid_until_ms: validUntil, person_track_id: track, selection_epoch: epoch, selection_status: selected ? row.selection_status as "selected" | "single_visible_person" : "unavailable", width: positive(row.width), height: positive(row.height), keypoints: [...points.values()] });
  }
  frames.sort((a, b) => a.timestamp_ms - b.timestamp_ms);
  for (let index = 0; index < frames.length; index++) {
    const next = frames[index + 1];
    if (next) frames[index].valid_until_ms = Math.min(frames[index].valid_until_ms, next.timestamp_ms);
    if (next?.timestamp_ms === frames[index].timestamp_ms || frames[index - 1]?.timestamp_ms === frames[index].timestamp_ms) frames[index].keypoints = [];
  }
  const edges = new Map<string, [string, string]>();
  for (const pair of Array.isArray(raw.skeleton_edges) ? raw.skeleton_edges.slice(0, 60) : []) {
    if (!Array.isArray(pair) || pair.length !== 2 || !JOINTS.has(pair[0]) || !JOINTS.has(pair[1]) || pair[0] === pair[1]) continue;
    edges.set([...pair].sort().join("/"), [pair[0], pair[1]]);
  }
  return { schema_version: "1.0.0", result_kind: "observed_pose_playback", job_id: raw.job_id, coordinate_space: "normalized_frame_0_1", joint_schema: "halpe26_named", min_keypoint_confidence: minConfidence, status: typeof raw.status === "string" ? raw.status : undefined, reason_zh: typeof raw.reason_zh === "string" ? raw.reason_zh : undefined, skeleton_edges: [...edges.values()], source: { requested_start_ms: source.requested_start_ms, requested_end_ms: source.requested_end_ms, width: positive(source.width), height: positive(source.height), returned_frames: frames.length, is_sampled: source.is_sampled === true, is_partial: source.is_partial === true, timestamp_semantics: "source_frame_timestamp_ms" }, frames };
}

/** Binary search never borrows the next pose or joins observations in time. */
export function poseFrameAt(preview: PosePreviewResponse | null, timestampMs: number): PosePreviewFrame | null {
  if (!preview || !Number.isFinite(timestampMs)) return null;
  const frames = preview.frames;
  let low = 0, high = frames.length - 1, found = -1;
  while (low <= high) { const middle = (low + high) >>> 1; if (frames[middle].timestamp_ms <= timestampMs) { found = middle; low = middle + 1; } else high = middle - 1; }
  const frame = frames[found];
  return frame && timestampMs < frame.valid_until_ms && frame.selection_status !== "unavailable" && frame.keypoints.length ? frame : null;
}

export function poseEdges(frame: PosePreviewFrame, edges: Array<[string, string]>) {
  const points = new Map(frame.keypoints.map(point => [point.name, point]));
  return edges.flatMap(([from, to]) => { const start = points.get(from), end = points.get(to); return start && end ? [{ from, to, start, end }] : []; });
}
