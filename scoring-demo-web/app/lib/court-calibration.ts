import type { TrajectoryPreviewResponse } from "./api-types";
import { courtHomography, type Corner, type CourtReferenceRegion } from "./court-simulation";

export type SavedCourtCalibration = { version: 1; sourceKey: string; region: CourtReferenceRegion; corners: Corner[]; timestampMs: number; fixedCamera: true };

/** A result's immutable job and source identity prevent reuse on a different upload. */
export function courtCalibrationKey(trajectory: TrajectoryPreviewResponse | null): string | null {
  if (!trajectory?.job_id || !trajectory.source.frames_path || !trajectory.source.width || !trajectory.source.height) return null;
  return `rallymate:court:v1:${JSON.stringify([trajectory.job_id, trajectory.source.frames_path, trajectory.source.width, trajectory.source.height])}`;
}

export function parseCourtCalibration(raw: string | null, sourceKey: string): SavedCourtCalibration | null {
  if (!raw) return null;
  try {
    const value = JSON.parse(raw);
    if (value?.version !== 1 || value.sourceKey !== sourceKey || value.fixedCamera !== true || !Number.isFinite(value.timestampMs) || value.timestampMs < 0 || !["doubles", "singles", "service"].includes(value.region) || !Array.isArray(value.corners) || value.corners.some((point: Corner) => !point || typeof point !== "object") || !courtHomography(value.corners, value.region)) return null;
    return { version: 1, sourceKey, fixedCamera: true, timestampMs: value.timestampMs, region: value.region, corners: value.corners.map(({ x, y }: Corner) => ({ x, y })) };
  } catch { return null; }
}
