import type { ActionEvaluation, DemoResultResponse, TrajectoryPreviewResponse } from "./api-types";

export type TrajectoryDisplayModel = {
  observedCount: number;
  predictedCount: number;
  predictionStatus: string;
  predictionLabel: string;
  predictionNote: string;
  observedCoverage: number | null;
  observedConfidence: number | null;
  direction: string | null;
  directionSegments: number;
};

export type LiveStatsModel = {
  actionSegments: number;
  actionKinds: number;
  ballDetections: number | null;
  ballDetectionFrames: number | null;
  racketDetections: number | null;
  racketDetectionFrames: number | null;
  processedFrames: number | null;
  shotCount: number | null;
  contactCount: number | null;
  shotCountSource: "server" | "unavailable";
};

function finiteNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function explicitCount(actions: ActionEvaluation[] | undefined, keys: string[]): number | null {
  for (const action of actions ?? []) {
    const record = action as unknown as Record<string, unknown>;
    for (const key of keys) {
      const value = finiteNumber(record[key]);
      if (value !== null) return value;
    }
  }
  return null;
}

/** Build a display model from server evidence. It never turns detections into hits. */
export function summarizeLiveStats(
  result: DemoResultResponse | null,
  trajectory: TrajectoryPreviewResponse | null,
  summary?: Record<string, unknown> | null,
): LiveStatsModel {
  const actions = result?.actions ?? [];
  const counts = summary?.counts as Record<string, unknown> | undefined;
  const detections = counts?.detections as Record<string, unknown> | undefined;
  const framesWithClass = counts?.frames_with_class as Record<string, unknown> | undefined;
  const processing = summary?.processing as Record<string, unknown> | undefined;
  const actionSegments = actions.reduce((sum, action) => sum + Math.max(0, finiteNumber(action.detected_segments) ?? 0), 0);
  const shotCount = explicitCount(actions, ["shot_count", "shots_detected", "hit_count"]);
  const contactCount = explicitCount(actions, ["contact_count", "contacts_detected"]);
  return {
    actionSegments,
    actionKinds: actions.filter((action) => (finiteNumber(action.detected_segments) ?? 0) > 0).length,
    ballDetections: finiteNumber(detections?.ball) ?? (trajectory ? finiteNumber((trajectory.ball as unknown as Record<string, unknown>).detection_count) : null),
    ballDetectionFrames: finiteNumber(framesWithClass?.ball),
    racketDetections: finiteNumber(detections?.racket),
    racketDetectionFrames: finiteNumber(framesWithClass?.racket),
    processedFrames: finiteNumber(processing?.processed_frames) ?? finiteNumber(trajectory?.source.frame_count),
    shotCount,
    contactCount,
    shotCountSource: shotCount !== null || contactCount !== null ? "server" : "unavailable",
  };
}

export function buildTrajectoryDisplayModel(trajectory: TrajectoryPreviewResponse | null): TrajectoryDisplayModel {
  const ball = trajectory?.ball;
  const status = ball?.prediction_status ?? "not_available";
  const predictedCount = status === "heuristic_preview" ? (ball?.predicted.length ?? 0) : 0;
  return {
    observedCount: ball?.observed_count ?? ball?.observed.length ?? 0,
    predictedCount,
    predictionStatus: status,
    predictionLabel: status === "heuristic_preview" && predictedCount > 0 ? "启发式外推" : "未提供外推",
    predictionNote: status === "heuristic_preview" && predictedCount > 0
      ? "短时可视化参考，不代表真实落点"
      : "只展示真实观测点",
    observedCoverage: finiteNumber(ball?.coverage_fraction),
    observedConfidence: finiteNumber(ball?.confidence.mean),
    direction: ball?.velocity && (ball.velocity.segment_count ?? 0) > 0 && finiteNumber(ball.velocity.direction_image_deg) !== null
      ? String(ball.velocity.direction_image_deg)
      : null,
    directionSegments: ball?.velocity?.segment_count ?? 0,
  };
}
