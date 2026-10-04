import type { DemoResultResponse, TechniqueAssessmentResponse, TrajectoryPreviewResponse } from "./api-types";

export type ActionCandidateView = { id: string; family: "baseline" | "serve"; label: string; startMs: number; peakMs: number; endMs: number };

/** Candidate intervals remain distinct from measured actions and contacts. */
export function summarizeActionCandidates(result: DemoResultResponse | null, assessment?: TechniqueAssessmentResponse | null) {
  const raw = result?.action_recognition ?? (assessment as unknown as Record<string, unknown> | null)?.action_recognition;
  const recognition = raw && typeof raw === "object" ? raw as Record<string, unknown> : {};
  const candidates: ActionCandidateView[] = [];
  const ids = new Set<string>();
  for (const rawCandidate of Array.isArray(recognition.candidates) ? recognition.candidates : []) {
    if (!rawCandidate || typeof rawCandidate !== "object") continue;
    const item = rawCandidate as Record<string, unknown>;
    if (item.status !== "candidate" || item.contact_confirmed !== false || !["baseline", "serve"].includes(String(item.family))) continue;
    const start = finiteNumber(item.start_ms), peak = finiteNumber(item.peak_ms), end = finiteNumber(item.end_ms);
    if (start === null || peak === null || end === null || start < 0 || peak < start || end <= start || peak > end || typeof item.candidate_id !== "string" || ids.has(item.candidate_id)) continue;
    ids.add(item.candidate_id);
    const family = item.family as "baseline" | "serve";
    candidates.push({ id: item.candidate_id, family, label: family === "serve" ? "发球动作候选" : "底线挥拍候选", startMs: start, peakMs: peak, endMs: end });
  }
  candidates.sort((a, b) => a.startMs - b.startMs);
  return { candidates, status: typeof recognition.status === "string" ? recognition.status : "not_run" };
}

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
  actionSegments: number | null;
  actionKinds: number | null;
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

function validCount(value: unknown): number | null {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0 ? value : null;
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
  const actionSegments = result?.actions ? actions.reduce((sum, action) => sum + (validCount(action.detected_segments) ?? 0), 0) : null;
  const hitStatistics = result?.hit_statistics;
  // Generic GS segments and recursively discovered "hit_count" fields are
  // not confirmed ball/racket contacts. Only the canonical contract qualifies.
  const contactCount = hitStatistics?.status === "observed" && hitStatistics.count_semantics === "confirmed_contact_events" ? validCount(hitStatistics.total_count) : null;
  const shotCount = contactCount;
  return {
    actionSegments,
    actionKinds: result?.actions ? actions.filter((action) => (validCount(action.detected_segments) ?? 0) > 0).length : null,
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

export function analysisPresentation({ result, pending, status, error }: { result: DemoResultResponse | null; pending: boolean; status?: string; error?: string | null }) {
  const state = status ?? result?.status;
  if (state === "failed" || (!result && error && !pending)) return { state: "error", title: "本次分析未完成", description: error || "分析服务未能完成本次任务，可重新读取任务状态或重试上传。", emptyTitle: "暂无动作结果", emptyDescription: "本次分析失败，不会继续等待识别结果。" };
  if (state === "cancelled") return { state: "cancelled", title: "本次分析已取消", description: "任务已停止，可重新上传视频开始分析。", emptyTitle: "分析已取消", emptyDescription: "本次任务没有完整的动作结果。" };
  if (state === "uploading") return { state: "pending", title: "正在上传视频", description: "上传完成后将进入分析队列。", emptyTitle: "视频正在上传", emptyDescription: "上传完成后开始分析动作。" };
  if (state === "queued") return { state: "pending", title: "视频已进入分析队列", description: "视频已接收，计算资源就绪后开始分析。", emptyTitle: "任务排队中", emptyDescription: "尚未开始识别动作。" };
  if (pending && !result && ["completed", "succeeded"].includes(state ?? "")) return { state: "pending", title: "正在读取训练报告", description: "视频分析已完成，正在整理回放与测量结果。", emptyTitle: "正在读取动作结果", emptyDescription: "任务已经完成，当前正在读取报告内容。" };
  if ((pending || state === "running" || state === "processing") && !["completed", "succeeded", "unsupported"].includes(state ?? "")) return { state: "pending", title: "正在分析这一段视频", description: "可先回放视频。结果会随任务进度更新，刷新页面后可继续读取。", emptyTitle: "动作分析进行中", emptyDescription: "已有视频可先回放；动作结果仍在计算。" };
  if (state === "unsupported") return { state: "unsupported", title: "本次未提供动作评分", description: "当前输入或分析配置不支持动作评分，已有视频和观测结果仍可查看。", emptyTitle: "暂不支持此动作分析", emptyDescription: "本次不会生成该项动作评分。" };
  if (result) return { state: "complete", title: "分析已完成 · 技术评分待标定", description: "查看证据参考分与动作测量；参考分描述证据完整程度，不能用于比较技术水平。", emptyTitle: "本次未提供该类动作证据", emptyDescription: "分析已完成，可切换动作类别；缺少证据不代表动作未发生。" };
  if (["completed", "succeeded"].includes(state ?? "")) return { state: "unavailable", title: "分析已完成，结果暂不可用", description: "任务已结束，但动作结果尚未成功读取。请重新读取当前任务结果。", emptyTitle: "动作结果暂不可用", emptyDescription: "任务已结束，无需继续等待识别。" };
  return { state: "idle", title: "上传视频后查看动作分析", description: "选择一段练习视频，分析完成后查看当前视频的动作证据。", emptyTitle: "尚未开始分析", emptyDescription: "上传视频或导入已有报告后查看动作结果。" };
}

export function buildTrajectoryDisplayModel(trajectory: TrajectoryPreviewResponse | null): TrajectoryDisplayModel {
  const ball = trajectory?.ball;
  const status = ball?.prediction_status ?? "not_available";
  const predictedCount = status === "heuristic_preview" ? (ball?.predicted.length ?? 0) : 0;
  return {
    observedCount: ball?.reconstruction?.summary.observed_count ?? ball?.observed_count ?? ball?.observed.length ?? 0,
    predictedCount,
    predictionStatus: status,
    predictionLabel: status === "heuristic_preview" && predictedCount > 0 ? "启发式外推" : "未提供外推",
    predictionNote: status === "heuristic_preview" && predictedCount > 0
      ? "短时可视化参考，不代表真实落点"
      : "只展示真实观测点",
    observedCoverage: finiteNumber(ball?.reconstruction?.summary.coverage_fraction) ?? finiteNumber(ball?.coverage_fraction),
    observedConfidence: finiteNumber(ball?.reconstruction?.summary.confidence?.mean) ?? finiteNumber(ball?.confidence.mean),
    direction: ball?.velocity && (ball.velocity.segment_count ?? 0) > 0 && finiteNumber(ball.velocity.direction_image_deg) !== null
      ? String(ball.velocity.direction_image_deg)
      : null,
    directionSegments: ball?.velocity?.segment_count ?? 0,
  };
}
