import type { PosePreviewFrame, PosePreviewResponse } from "./api-types";
import { POSE_MIN_CONFIDENCE, poseFrameAt } from "./pose-playback";

const FOCUS_JOINTS = ["nose", "left_shoulder", "right_shoulder", "left_elbow", "right_elbow",
  "left_wrist", "right_wrist", "left_hip", "right_hip", "left_knee", "right_knee", "left_ankle", "right_ankle"] as const;
const TRAIL_MS = 500;
type Joint = PosePreviewFrame["keypoints"][number];

function modelSourceTimeContinuous(older: PosePreviewFrame, newer: PosePreviewFrame): boolean {
  if (newer.pose_source !== "sam3d") return true;
  const a = older.source_binding, b = newer.source_binding;
  if (!a || !b) return false;
  const sourceDelta = b.pts * b.time_base_num / b.time_base_den - a.pts * a.time_base_num / a.time_base_den;
  // Relative playback and original PTS must advance together. One millisecond
  // allows independent rounding of the two integer timestamp endpoints.
  return sourceDelta > 0 && Math.abs((b.timestamp_ms - a.timestamp_ms) - (newer.timestamp_ms - older.timestamp_ms)) <= 1;
}
export type MotionFocus = {
  width: number;
  height: number;
  markers: Joint[];
  bones: Array<{ from: Joint; to: Joint }>;
  trails: Array<{ name: string; points: Array<Joint & { timestamp_ms: number }> }>;
};

function observedJoint(frame: PosePreviewFrame, name: string): Joint | undefined {
  return frame.keypoints.find(point => point.name === name && point.source !== "interpolated"
    && (point.source === "model_estimate" ? frame.pose_source === "sam3d" && !!frame.source_binding
      && frame.observation_semantics === "monocular_model_estimate" && point.confidence === null && point.in_frame === true
      && point.confidence_semantics === "not_provided_by_sam3d" && point.observation_semantics === "model_estimate"
      : typeof point.confidence === "number" && Number.isFinite(point.confidence) && point.confidence >= POSE_MIN_CONFIDENCE)
    && Number.isFinite(point.x) && Number.isFinite(point.y)
    && point.x >= 0 && point.x <= 1 && point.y >= 0 && point.y <= 1);
}

function sameSequence(a: PosePreviewFrame, b: PosePreviewFrame): boolean {
  return a.person_track_id !== null && a.person_track_id === b.person_track_id
    && a.selection_epoch === b.selection_epoch && a.selection_status === b.selection_status
    && !a.identity_repair && !b.identity_repair && a.pose_source === b.pose_source
    && a.source_binding?.video_sha256 === b.source_binding?.video_sha256
    && a.source_binding?.camera_reference_epoch === b.source_binding?.camera_reference_epoch
    && a.source_binding?.selection_epoch === b.source_binding?.selection_epoch
    && modelSourceTimeContinuous(a, b);
}

/** Suppress an isolated display spike only when both adjacent frames agree.
 * Missing points, identities and source gaps are never interpolated or repaired.
 */
function isolatedSpike(frames: PosePreviewFrame[], index: number, joint: Joint, width: number, height: number): boolean {
  const before = frames[index - 1], current = frames[index], after = frames[index + 1];
  if (!before || !after || !sameSequence(before, current) || !sameSequence(current, after)
    || current.timestamp_ms - before.timestamp_ms <= 0 || current.timestamp_ms - before.timestamp_ms > 80
    || after.timestamp_ms - current.timestamp_ms <= 0 || after.timestamp_ms - current.timestamp_ms > 80
    || before.valid_until_ms < current.timestamp_ms || current.valid_until_ms < after.timestamp_ms
    || before.width !== current.width || after.width !== current.width
    || before.height !== current.height || after.height !== current.height) return false;
  const a = observedJoint(before, joint.name), b = observedJoint(after, joint.name);
  if (!a || !b) return false;
  const distance = (p: Joint, q: Joint) => Math.hypot((p.x - q.x) * width, (p.y - q.y) * height) / Math.hypot(width, height);
  const threshold = joint.name.endsWith("wrist") || joint.name.endsWith("ankle") ? .24 : .12;
  return distance(a, b) < .015 && distance(a, joint) > threshold && distance(joint, b) > threshold;
}

/** Display only: connected body joints and a short, past-only wrist path. */
export function motionFocusAt(preview: PosePreviewResponse | null, timestampMs: number, aspectRatio = 16 / 9): MotionFocus | null {
  if (!preview) return null;
  // The normalized display stream also carries verified observed response scores
  // and identity vetoes. Keep those; exclude interpolation and identity repairs.
  const frames = preview.display_frames ?? preview.frames;
  const current = poseFrameAt(preview, timestampMs);
  if (!current || current.identity_repair) return null;
  const width = current.width || preview.source.width || aspectRatio * 1000;
  const height = current.height || preview.source.height || 1000;
  const currentIndex = frames.indexOf(current);
  // Include every validated native joint (face, feet and fingers as provided),
  // keeping major body joints first. The normalizer owns the schema allowlist.
  const jointNames = [...new Set<string>([...FOCUS_JOINTS, ...current.keypoints.map(point => point.name)])];
  const markers = jointNames.flatMap(name => {
    const joint = observedJoint(current, name);
    return joint && !isolatedSpike(frames, currentIndex, joint, width, height) ? [joint] : [];
  });
  if (!markers.length) return null;
  const trails: MotionFocus["trails"] = [];
  for (const marker of markers.filter(point => point.name.endsWith("wrist"))) {
    // A single visible person without a persistent ID supports a marker, not a path.
    if (current.person_track_id === null) continue;
    const points = [{ ...marker, timestamp_ms: current.timestamp_ms }];
    let newer = current;
    for (let i = currentIndex - 1; i >= 0; i--) {
      const older = frames[i];
      const delta = newer.timestamp_ms - older.timestamp_ms;
      if (timestampMs - older.timestamp_ms > TRAIL_MS || delta <= 0 || delta > 100
        || older.valid_until_ms < newer.timestamp_ms
        || older.person_track_id !== current.person_track_id || older.selection_epoch !== current.selection_epoch
        || older.selection_status !== current.selection_status || older.identity_repair
        || older.pose_source !== current.pose_source
        || older.source_binding?.video_sha256 !== current.source_binding?.video_sha256
        || older.source_binding?.camera_reference_epoch !== current.source_binding?.camera_reference_epoch
        || older.source_binding?.selection_epoch !== current.source_binding?.selection_epoch
        || !modelSourceTimeContinuous(older, newer)
        || (older.width || preview.source.width || aspectRatio * 1000) !== width
        || (older.height || preview.source.height || 1000) !== height) break;
      const joint = observedJoint(older, marker.name);
      if (!joint) break;
      const last = points[points.length - 1];
      const jump = Math.hypot((joint.x - last.x) * width, (joint.y - last.y) * height) / Math.hypot(width, height);
      // Conservative drawing cutoff for abrupt detections, not a biomechanical rule.
      if (jump > .16 || jump / (delta / 1000) > 2.5) break;
      points.push({ ...joint, timestamp_ms: older.timestamp_ms });
      newer = older;
    }
    if (points.length > 1) trails.push({ name: marker.name, points: points.reverse() });
  }
  const byName = new Map(markers.map(point => [point.name, point]));
  const bones = preview.skeleton_edges.flatMap(([a, b]) => {
    const from = byName.get(a), to = byName.get(b);
    return from && to ? [{ from, to }] : [];
  });
  return { width, height, markers, bones, trails };
}
