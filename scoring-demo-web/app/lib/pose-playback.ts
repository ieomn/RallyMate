import type { PoseDisplayAnchors, PoseIdentityRepair, PoseModelSourceBinding, PosePreviewFrame, PosePreviewResponse } from "./api-types";

export const POSE_WINDOW_MS = 10000;
export const POSE_MAX_HOLD_MS = 100;
export const POSE_MIN_CONFIDENCE = .35;
const JOINTS = new Set(["nose", "left_eye", "right_eye", "left_ear", "right_ear", "left_shoulder", "right_shoulder", "left_elbow", "right_elbow", "left_wrist", "right_wrist", "left_hip", "right_hip", "left_knee", "right_knee", "left_ankle", "right_ankle", "head", "neck", "hip", "left_big_toe", "right_big_toe", "left_small_toe", "right_small_toe", "left_heel", "right_heel"]);
const object = (value: unknown): Record<string, unknown> => value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const positive = (value: unknown): number | null => finite(value) && value > 0 ? value : null;
const BODY_JOINTS = new Set([...JOINTS].slice(0, 17));

function modelSourceBinding(row: Record<string, unknown>): PoseModelSourceBinding | null {
  const binding = object(row.source_binding);
  if (row.pose_source !== "sam3d" || row.source_keypoint_format !== "mhr70" || row.observation_semantics !== "monocular_model_estimate"
    || typeof binding.video_sha256 !== "string" || !/^[a-f0-9]{64}$/i.test(binding.video_sha256)
    || !positive(row.width) || !positive(row.height)) return null;
  for (const key of ["frame_index", "processed_index", "pts", "timestamp_ms", "person_track_id", "selection_epoch", "camera_reference_epoch", "time_base_num", "time_base_den"]) {
    if (!Number.isSafeInteger(binding[key]) || Number(binding[key]) < (["time_base_num", "time_base_den", "person_track_id"].includes(key) ? 1 : 0)) return null;
  }
  const sourceTimestamp = Object.hasOwn(row, "source_timestamp_ms") ? row.source_timestamp_ms : row.timestamp_ms;
  if (binding.frame_index !== row.frame_index || binding.processed_index !== row.processed_index || binding.timestamp_ms !== sourceTimestamp || binding.person_track_id !== row.person_track_id
    || Math.abs(Number(binding.pts) * Number(binding.time_base_num) * 1000 / Number(binding.time_base_den) - Number(binding.timestamp_ms)) > .51) return null;
  return binding as unknown as PoseModelSourceBinding;
}
export const poseWindowStart = (timestampMs: number) => Math.floor(Math.max(0, Number.isFinite(timestampMs) ? timestampMs : 0) / POSE_WINDOW_MS) * POSE_WINDOW_MS;

/** Source PTS can only drive the original video or an explicitly preserved replay. */
export function poseReplayTimingAllowed(isLocalOriginal: boolean, result: unknown): boolean {
  return isLocalOriginal || object(object(object(result).runtime).annotated_video_compatibility).timing_preserved === true;
}

/** Raw observations remain intact; only bounded, explicitly marked display points can supplement them. */
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
    const modelBinding = modelSourceBinding(row);
    const isEstimate = row.pose_source === "sam3d" || row.observation_semantics === "monocular_model_estimate";
    const duplicates = new Set<string>();
    for (const value of selected && Array.isArray(row.keypoints) ? row.keypoints.slice(0, 26) : []) {
      const point = object(value);
      if (typeof point.name !== "string" || !JOINTS.has(point.name)) continue;
      if (points.has(point.name)) { duplicates.add(point.name); continue; }
      if (isEstimate) {
        if (!modelBinding || !BODY_JOINTS.has(point.name) || point.source !== "model_estimate" || point.observation_semantics !== "model_estimate"
          || point.confidence !== null || point.confidence_semantics !== "not_provided_by_sam3d" || point.in_frame !== true
          || !finite(point.raw_x_px) || !finite(point.raw_y_px) || point.raw_x_px < 0 || point.raw_x_px >= Number(row.width) || point.raw_y_px < 0 || point.raw_y_px >= Number(row.height)
          || !finite(point.x) || !finite(point.y) || Math.abs(point.x - point.raw_x_px / Number(row.width)) > .000001 || Math.abs(point.y - point.raw_y_px / Number(row.height)) > .000001) continue;
        points.set(point.name, { name: point.name, x: point.x, y: point.y, confidence: null, source: "model_estimate", observation_semantics: "model_estimate",
          confidence_semantics: "not_provided_by_sam3d", in_frame: true, raw_x_px: point.raw_x_px, raw_y_px: point.raw_y_px });
        continue;
      }
      if (point.source !== undefined && point.source !== "observed") continue;
      if (!finite(point.x) || point.x < 0 || point.x > 1 || !finite(point.y) || point.y < 0 || point.y > 1 || !finite(point.confidence) || point.confidence < minConfidence || point.confidence > 1) continue;
      points.set(point.name, { name: point.name, x: point.x, y: point.y, confidence: point.confidence, source: "observed" });
    }
    for (const name of duplicates) points.delete(name);
    frames.push({ frame_index: Number.isSafeInteger(row.frame_index) ? Number(row.frame_index) : -1, timestamp_ms: row.timestamp_ms, valid_until_ms: validUntil, person_track_id: track, selection_epoch: epoch, selection_status: selected ? row.selection_status as "selected" | "single_visible_person" : "unavailable", width: positive(row.width), height: positive(row.height), keypoints: [...points.values()],
      ...(modelBinding ? { processed_index: Number(row.processed_index), pose_source: "sam3d", source_keypoint_format: "mhr70", observation_semantics: "monocular_model_estimate", source_binding: modelBinding,
        ...(finite(row.source_timestamp_ms) ? { source_timestamp_ms: row.source_timestamp_ms } : {}) } as const : {}) });
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
  const display = normalizeDisplayFrames(raw, frames, minConfidence);
  const roundingTolerance = finite(source.timestamp_rounding_tolerance_ms) && source.timestamp_rounding_tolerance_ms >= 0 && source.timestamp_rounding_tolerance_ms <= .5 ? source.timestamp_rounding_tolerance_ms : 0;
  return { schema_version: "1.0.0", result_kind: "observed_pose_playback", job_id: raw.job_id, coordinate_space: "normalized_frame_0_1", joint_schema: "halpe26_named", min_keypoint_confidence: minConfidence, status: typeof raw.status === "string" ? raw.status : undefined, reason_zh: typeof raw.reason_zh === "string" ? raw.reason_zh : undefined, skeleton_edges: [...edges.values()], source: { requested_start_ms: source.requested_start_ms, requested_end_ms: source.requested_end_ms, width: positive(source.width), height: positive(source.height), returned_frames: frames.length, is_sampled: source.is_sampled === true, is_partial: source.is_partial === true, timestamp_semantics: "source_frame_timestamp_ms", timestamp_rounding_tolerance_ms: roundingTolerance }, frames, ...display };
}

function normalizeDisplayFrames(raw: Record<string, unknown>, frames: PosePreviewFrame[], minimumConfidence: number): Pick<PosePreviewResponse, "display_frames" | "display_continuity"> {
  // Legacy interpolation/score recovery cannot rewrite model estimates as observations.
  if (Array.isArray(raw.frames) && raw.frames.some(row => object(row).pose_source === "sam3d" || object(row).observation_semantics === "monocular_model_estimate")) return {};
  const continuity = object(raw.display_continuity);
  const nonnegativeInteger = (value: unknown): value is number => Number.isSafeInteger(value) && Number(value) >= 0;
  if (!Array.isArray(raw.display_frames) || continuity.version !== "short-joint-gap-v1.0.0" || continuity.used_for_scoring !== false
    || continuity.confidence_semantics !== "minimum_anchor_confidence_not_model_estimate"
    || !finite(continuity.max_gap_ms) || continuity.max_gap_ms <= 0 || continuity.max_gap_ms > 150
    || !nonnegativeInteger(continuity.interpolated_keypoints) || !nonnegativeInteger(continuity.affected_frames)) return {};
  const byTime = new Map<number, Record<string, unknown>>();
  const duplicates = new Set<number>();
  for (const value of raw.display_frames.slice(0, 600)) {
    const row = object(value);
    if (!finite(row.timestamp_ms)) continue;
    if (byTime.has(row.timestamp_ms)) duplicates.add(row.timestamp_ms);
    else byTime.set(row.timestamp_ms, row);
  }
  const boundRow = (frame: PosePreviewFrame) => {
    const row = byTime.get(frame.timestamp_ms);
    return row && !duplicates.has(frame.timestamp_ms) && row.frame_index === frame.frame_index && row.selection_epoch === frame.selection_epoch
      && row.width === frame.width && row.height === frame.height && Array.isArray(row.keypoints) ? row : null;
  };
  // Recovery is limited to the documented SimCC response domain, never an
  // arbitrary low-confidence point promoted to an observation by the browser.
  const recovered = frames.map(frame => {
    const row = boundRow(frame);
    if (!row || frame.selection_status === "unavailable" || row.person_track_id !== frame.person_track_id || row.selection_status !== frame.selection_status) return frame;
    const points = new Map(frame.keypoints.map(point => [point.name, point]));
    const candidates = new Map<string, PosePreviewFrame["keypoints"][number]>();
    const repeated = new Set<string>();
    for (const value of (row.keypoints as unknown[]).slice(0, 26)) {
      const point = object(value);
      if (typeof point.name !== "string" || !JOINTS.has(point.name) || points.has(point.name)) continue;
      if (candidates.has(point.name)) { repeated.add(point.name); continue; }
      if (point.source !== "observed" || point.score_semantics !== "rtmpose_simcc_response_clipped_for_display" || point.confidence !== 1
        || !finite(point.raw_confidence) || point.raw_confidence <= 1 || !finite(point.x) || point.x < 0 || point.x > 1 || !finite(point.y) || point.y < 0 || point.y > 1) continue;
      candidates.set(point.name, { name: point.name, x: point.x, y: point.y, confidence: 1, source: "observed", score_semantics: "rtmpose_simcc_response_clipped_for_display", raw_confidence: point.raw_confidence });
    }
    for (const [name, point] of candidates) if (!repeated.has(name)) points.set(name, point);
    return { ...frame, keypoints: [...points.values()] };
  });
  const displayFrames = recovered.map(frame => {
    const row = boundRow(frame);
    if (!row) return frame;
    const original = frames.find(candidate => candidate.timestamp_ms === frame.timestamp_ms)!;
    const repairData = object(row.identity_repair);
    const hasRepair = row.selection_status === "display_interpolated";
    let repair: PoseIdentityRepair | undefined;
    if (hasRepair) {
      const before = repairData.before_timestamp_ms, after = repairData.after_timestamp_ms;
      if (continuity.identity_repair_version !== "short-target-dropout-v1.0.0" || repairData.version !== "short-target-dropout-v1.0.0"
        || repairData.method !== "same_target_detection_dropout" || repairData.original_track_id !== original.person_track_id
        || repairData.original_selection_epoch !== original.selection_epoch || row.observed_person_track_id !== original.person_track_id
        || row.observed_selection_epoch !== original.selection_epoch || !nonnegativeInteger(repairData.target_track_id) || repairData.target_track_id === 0
        || row.person_track_id !== repairData.target_track_id || repairData.target_track_id === original.person_track_id
        || (original.person_track_id === null && (repairData.dropout_kind !== "pose_unavailable" || original.selection_status !== "unavailable" || original.keypoints.length > 0))
        || !finite(before) || !finite(after) || !(before >= 0 && before < frame.timestamp_ms && frame.timestamp_ms < after)
        || after - before > Number(continuity.max_gap_ms) || !nonnegativeInteger(repairData.before_frame_index) || !nonnegativeInteger(repairData.after_frame_index)
        || !(repairData.before_frame_index < frame.frame_index && frame.frame_index < repairData.after_frame_index)
        || !nonnegativeInteger(repairData.before_selection_epoch) || !nonnegativeInteger(repairData.after_selection_epoch)
        || !nonnegativeInteger(repairData.primary_player_id) || repairData.primary_player_id === 0) return frame;
      repair = { version: "short-target-dropout-v1.0.0", method: "same_target_detection_dropout", original_track_id: original.person_track_id,
        ...(repairData.dropout_kind === "pose_unavailable" ? { dropout_kind: "pose_unavailable" as const } : {}),
        original_selection_epoch: original.selection_epoch, target_track_id: repairData.target_track_id,
        before_frame_index: repairData.before_frame_index, after_frame_index: repairData.after_frame_index, before_timestamp_ms: before, after_timestamp_ms: after,
        before_selection_epoch: repairData.before_selection_epoch, after_selection_epoch: repairData.after_selection_epoch, primary_player_id: repairData.primary_player_id };
    } else if (frame.selection_status === "unavailable" || !frame.keypoints.length || row.person_track_id !== frame.person_track_id || row.selection_status !== frame.selection_status) return frame;
    // A repaired target is constructed entirely from its own anchors. None of
    // the transient background person's raw points enter this display frame.
    const points = new Map((repair ? [] : frame.keypoints).map(point => [point.name, point]));
    const repeated = new Set<string>();
    const seen = new Set<string>();
    for (const value of (row.keypoints as unknown[]).slice(0, 26)) {
      const point = object(value), anchors = object(point.anchors);
      if (typeof point.name !== "string" || !JOINTS.has(point.name)) continue;
      if (seen.has(point.name)) repeated.add(point.name);
      seen.add(point.name);
      if (points.has(point.name) || point.source !== "interpolated") continue;
      if (!finite(point.x) || point.x < 0 || point.x > 1 || !finite(point.y) || point.y < 0 || point.y > 1
        || !finite(point.confidence) || point.confidence < minimumConfidence || point.confidence > 1) continue;
      const before = anchors.before_timestamp_ms, after = anchors.after_timestamp_ms;
      if (!finite(before) || !finite(after) || before < 0 || !(before < frame.timestamp_ms && frame.timestamp_ms < after)
        || after - before > Number(continuity.max_gap_ms) || anchors.person_track_id !== (repair?.target_track_id ?? frame.person_track_id) || anchors.selection_epoch !== frame.selection_epoch
        || !nonnegativeInteger(anchors.before_frame_index) || !nonnegativeInteger(anchors.after_frame_index)
        || !(anchors.before_frame_index < frame.frame_index && frame.frame_index < anchors.after_frame_index)
        || !nonnegativeInteger(anchors.before_processed_index) || !nonnegativeInteger(anchors.after_processed_index)
        || anchors.after_processed_index - anchors.before_processed_index !== anchors.after_frame_index - anchors.before_frame_index) continue;
      // A locally available anchor must still belong to this subject and contain
      // this observed joint. At window edges the server provides bounded anchors.
      const anchorFrames = recovered.filter(candidate => candidate.timestamp_ms >= before && candidate.timestamp_ms <= after);
      if (repair) {
        if (before !== repair.before_timestamp_ms || after !== repair.after_timestamp_ms || anchors.before_frame_index !== repair.before_frame_index || anchors.after_frame_index !== repair.after_frame_index
          || anchors.before_selection_epoch !== repair.before_selection_epoch || anchors.after_selection_epoch !== repair.after_selection_epoch) continue;
        const previous = anchorFrames.find(candidate => candidate.timestamp_ms === before), next = anchorFrames.find(candidate => candidate.timestamp_ms === after);
        if (previous && (previous.person_track_id !== repair.target_track_id || previous.selection_epoch !== repair.before_selection_epoch || previous.frame_index !== repair.before_frame_index)
          || next && (next.person_track_id !== repair.target_track_id || next.selection_epoch !== repair.after_selection_epoch || next.frame_index !== repair.after_frame_index)) continue;
        const from = previous?.keypoints.find(endpoint => endpoint.name === point.name), to = next?.keypoints.find(endpoint => endpoint.name === point.name);
        if (previous && !from || next && !to) continue;
        if (from && to) {
          const alpha = (frame.timestamp_ms - before) / (after - before);
          if (Math.abs(point.x - (from.x + (to.x - from.x) * alpha)) > .00002 || Math.abs(point.y - (from.y + (to.y - from.y) * alpha)) > .00002) continue;
        }
      } else if (anchorFrames.some(candidate => candidate.person_track_id !== frame.person_track_id || candidate.selection_epoch !== frame.selection_epoch
        || candidate.selection_status === "unavailable" || !candidate.keypoints.length
        || ((candidate.timestamp_ms === before || candidate.timestamp_ms === after) && !candidate.keypoints.some(endpoint => endpoint.name === point.name)))) continue;
      const parsedAnchors: PoseDisplayAnchors = {
        before_timestamp_ms: before, after_timestamp_ms: after, before_frame_index: anchors.before_frame_index, after_frame_index: anchors.after_frame_index,
        before_processed_index: anchors.before_processed_index, after_processed_index: anchors.after_processed_index,
        person_track_id: Number(anchors.person_track_id), selection_epoch: Number(anchors.selection_epoch),
        ...(repair ? { before_selection_epoch: repair.before_selection_epoch, after_selection_epoch: repair.after_selection_epoch } : {}),
      };
      points.set(point.name, { name: point.name, x: point.x, y: point.y, confidence: point.confidence, source: "interpolated", anchors: parsedAnchors });
    }
    for (const name of repeated) if (points.get(name)?.source === "interpolated") points.delete(name);
    return { ...frame, keypoints: [...points.values()], ...(repair ? { person_track_id: repair.target_track_id, selection_status: "display_interpolated" as const,
      observed_person_track_id: original.person_track_id, observed_selection_epoch: original.selection_epoch, identity_repair: repair } : {}) };
  });
  const interpolatedCount = displayFrames.reduce((count, frame) => count + frame.keypoints.filter(point => point.source === "interpolated").length, 0);
  return { display_frames: displayFrames, display_continuity: {
    version: "short-joint-gap-v1.0.0", max_gap_ms: continuity.max_gap_ms, interpolated_keypoints: interpolatedCount,
    affected_frames: displayFrames.filter(frame => frame.keypoints.some(point => point.source === "interpolated")).length,
    confidence_semantics: "minimum_anchor_confidence_not_model_estimate", used_for_scoring: false,
    response_score_recovered_keypoints: recovered.reduce((count, frame) => count + frame.keypoints.filter(point => point.score_semantics === "rtmpose_simcc_response_clipped_for_display").length, 0),
    ...(continuity.identity_repair_version === "short-target-dropout-v1.0.0" ? { identity_repair_version: "short-target-dropout-v1.0.0" as const, repaired_identity_frames: displayFrames.filter(frame => frame.identity_repair).length } : {}),
  } };
}

/** Only explicitly declared integer-PTS rounding can advance lookup, by at most half a millisecond. */
export function poseFrameAt(preview: PosePreviewResponse | null, timestampMs: number): PosePreviewFrame | null {
  if (!preview || !Number.isFinite(timestampMs) || timestampMs < 0) return null;
  const frames = preview.display_frames ?? preview.frames;
  const declared = preview.source.timestamp_rounding_tolerance_ms;
  const rounding = finite(declared) && declared >= 0 && declared <= .5 ? declared : 0;
  let low = 0, high = frames.length - 1, found = -1;
  while (low <= high) { const middle = (low + high) >>> 1; if (frames[middle].timestamp_ms <= timestampMs + rounding) { found = middle; low = middle + 1; } else high = middle - 1; }
  const frame = frames[found];
  return frame && timestampMs < frame.valid_until_ms && frame.selection_status !== "unavailable" && frame.keypoints.length ? frame : null;
}

export function poseEdges(frame: PosePreviewFrame, edges: Array<[string, string]>) {
  const points = new Map(frame.keypoints.map(point => [point.name, point]));
  return edges.flatMap(([from, to]) => { const start = points.get(from), end = points.get(to); return start && end ? [{ from, to, start, end }] : []; });
}
