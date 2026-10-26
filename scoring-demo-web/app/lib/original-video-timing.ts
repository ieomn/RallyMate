import type { OriginalPoseTimeBinding, PosePreviewResponse } from "./api-types";

const object = (value: unknown): Record<string, unknown> => value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
const digest = (value: unknown): value is string => typeof value === "string" && /^[a-f0-9]{64}$/i.test(value);
const nonnegative = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value) && value >= 0;

/** An authenticated report must bind this job, exact original, and decoder clock. */
export function originalPoseTimeBinding(result: unknown, jobId: string | undefined, online: boolean): OriginalPoseTimeBinding | null {
  const report = object(result), original = object(report.original_video), binding = object(original.pose_time_binding), anchor = object(binding.anchor);
  if (!online || !jobId || report.job_id !== jobId || binding.job_id !== jobId
    || binding.version !== "original-video-decoder-pts-v1" || binding.timestamp_semantics !== "source_relative_decoder_pts"
    || typeof original.source_job_id !== "string" || !original.source_job_id || binding.source_job_id !== original.source_job_id
    || !digest(original.sha256) || !digest(binding.video_sha256) || binding.video_sha256.toLowerCase() !== original.sha256.toLowerCase()
    || !digest(binding.frames_sha256) || !nonnegative(binding.source_pts_origin_ms) || binding.media_time_origin_ms !== 0) return null;
  for (const [key, minimum] of [["index", 0], ["processed_index", 0], ["pts", 0], ["time_base_num", 1], ["time_base_den", 1], ["source_timestamp_ms", 0], ["timestamp_ms", 0]] as const) {
    if (!Number.isSafeInteger(anchor[key]) || Number(anchor[key]) < minimum) return null;
  }
  const ptsMs = Number(anchor.pts) * Number(anchor.time_base_num) / Number(anchor.time_base_den) * 1000;
  if (anchor.processed_index !== 0 || !Number.isFinite(ptsMs)
    || Math.abs(ptsMs - Number(anchor.source_timestamp_ms)) > .51
    || Math.abs(ptsMs - binding.source_pts_origin_ms - Number(anchor.timestamp_ms)) > .51) return null;
  return binding as unknown as OriginalPoseTimeBinding;
}

/** Each returned model window must still use the authorized original clock. */
export function poseWindowMatchesOriginal(preview: PosePreviewResponse, binding: OriginalPoseTimeBinding): boolean {
  if (preview.job_id !== binding.job_id) return false;
  return preview.frames.every(frame => !frame.keypoints.length || (frame.pose_source === "sam3d"
    && frame.source_binding?.video_sha256.toLowerCase() === binding.video_sha256.toLowerCase()
    && Math.abs(frame.source_binding.pts * frame.source_binding.time_base_num / frame.source_binding.time_base_den * 1000
      - binding.source_pts_origin_ms - frame.timestamp_ms) <= .51));
}
