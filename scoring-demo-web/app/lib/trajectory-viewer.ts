import type { TrajectoryPoint, TrajectoryPreviewResponse } from "./api-types";

export type TrajectoryEdge = { from: TrajectoryPoint; to: TrajectoryPoint; source: "observed" | "interpolated"; pathIndex: number };
export type TrajectoryDisplayMode = "segment" | "tail" | "overview";
export const TRAJECTORY_HOLD_MS = 600;

/** Limit each independent path to the recent past; never draw future detections. */
export function trajectoryWindow(paths: TrajectoryPoint[][], atMs: number, tailMs = 750): TrajectoryPoint[][] {
  return paths.map(points => points.filter(point => point.timestamp_ms <= atMs && point.timestamp_ms >= atMs - tailMs));
}

/** Conservative display filter only; the source inference evidence stays intact. */
export function visibleTrajectoryPaths(paths: TrajectoryPoint[][], atMs: number, options: { showInterpolated?: boolean; minimumConfidence?: number; tailMs?: number; mode?: TrajectoryDisplayMode; trustedSegments?: boolean; holdMs?: number; sampledPathEndMs?: Array<number | undefined> } = {}): TrajectoryPoint[][] {
  const { showInterpolated = true, tailMs = 750, mode = "segment", trustedSegments = false, holdMs = TRAJECTORY_HOLD_MS } = options;
  // The backend validates reconstructed geometry. That does not validate a
  // missing/invalid detector score. Use its existing 0.15 evidence floor for
  // reconstruction; do not apply the legacy 0.6 cutoff to tiny-ball detections.
  const minimumConfidence = options.minimumConfidence ?? (trustedSegments ? 0.15 : 0.6);
  const candidates: Array<{ points: TrajectoryPoint[]; supportEndMs: number }> = [];
  for (const [pathIndex, path] of trajectoryWindow(paths, atMs, mode === "tail" ? tailMs : Infinity).entries()) {
    let run: TrajectoryPoint[] = [];
    const finish = () => {
      if (run.length >= 2 && run.some(point => Math.hypot(point.x - run[0].x, point.y - run[0].y) >= 0.008)) {
        const sampledEnd = options.sampledPathEndMs?.[pathIndex];
        // Full-source segments bridge at most 600 ms. Point downsampling must
        // not make their still-observed interval appear expired between samples.
        const supportEndMs = trustedSegments && mode === "segment" && run.at(-1) === path.at(-1) && typeof sampledEnd === "number" && Number.isFinite(sampledEnd)
          ? Math.min(atMs, sampledEnd) : run[run.length - 1].timestamp_ms;
        candidates.push({ points: run, supportEndMs });
      }
      run = [];
    };
    for (const point of path) {
      const valid = [point.x, point.y, point.timestamp_ms].every(value => typeof value === "number" && Number.isFinite(value))
        && point.x >= 0 && point.x <= 1 && point.y >= 0 && point.y <= 1
        && typeof point.confidence === "number" && Number.isFinite(point.confidence) && point.confidence <= 1
        && point.confidence >= minimumConfidence * (trustedSegments && point.source === "interpolated" ? 0.85 : 1)
        && (point.source !== "interpolated" || showInterpolated);
      if (!valid) { finish(); continue; }
      const previous = run.at(-1);
      if (previous && (point.timestamp_ms <= previous.timestamp_ms || !trustedSegments && (point.timestamp_ms - previous.timestamp_ms > 250 || Math.hypot(point.x - previous.x, point.y - previous.y) > 0.18))) finish();
      run.push(point);
    }
    finish();
  }
  // Briefly hold missed observations, then clear stale history. Simultaneous
  // candidates remain separate: recency alone cannot identify the active ball.
  return candidates
    .filter(({ supportEndMs }) => mode === "overview" || atMs - supportEndMs <= (mode === "tail" ? tailMs : holdMs))
    .sort((a, b) => b.supportEndMs - a.supportEndMs || b.points[b.points.length - 1].timestamp_ms - a.points[a.points.length - 1].timestamp_ms)
    .slice(0, 12).map(candidate => candidate.points);
}

/** A display strength, never a probability of correctness or a technique score. */
export function trajectoryDisplayOpacity(points: TrajectoryPoint[], medianConfidence?: number | null) {
  const scores = points.filter(point => point.source !== "interpolated").map(point => point.confidence).filter((value): value is number => typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 1).sort((a, b) => a - b);
  const confidence = typeof medianConfidence === "number" && Number.isFinite(medianConfidence)
    ? medianConfidence : scores.length ? (scores[Math.floor((scores.length - 1) / 2)] + scores[Math.floor(scores.length / 2)]) / 2 : 0;
  return Math.max(0.2, Math.min(0.9, 0.2 + 0.7 * confidence));
}

export function trajectoryAvailability(trajectory: TrajectoryPreviewResponse | null, pending = false, error?: string | null) {
  const reconstruction = trajectory?.ball.reconstruction;
  if (!trajectory) {
    if (error) return { state: "error", title: "球路数据未能加载", description: "视频仍可正常回放。可重新读取任务结果以恢复球路数据。" };
    if (pending) return { state: "pending", title: "正在分析视频", description: "可靠球路会随分析进度更新，并与视频同步显示。" };
    return { state: "unavailable", title: "暂无球路数据", description: "本次结果未提供可用球路，不影响已有视频和动作结果的查看。" };
  }
  if (trajectory.source.is_partial) return { state: "pending", title: "视频分析中", description: "已返回部分观测。球路随播放展开，短缺口用虚线区分。" };
  if (["unsupported", "not_available"].includes(trajectory.status) || reconstruction?.status === "not_available") return { state: "unsupported", title: "本次未提供球路分析", description: "当前结果不支持球路重建；视频可继续正常回放。" };
  if (reconstruction?.segments.length) return { state: "ready", title: "球路观测已完成", description: "显示最近 0.6 秒仍有观测的独立候选，过时轨迹自动隐藏。虚线为短缺口插值，浅色表示较低检测置信度；尚未确认唯一比赛用球。" };
  if (!reconstruction && trajectory.ball.observed.length) return { state: "legacy", title: "仅有基础球观测", description: "本次结果没有分段重建；回放仅显示通过筛选的短时球路，不叠加人体骨架。" };
  return { state: "not_observed", title: "未获得可靠球路", description: "本次视频未形成可用的球轨迹；检测点数量不代表击球次数。" };
}

/** Paths remain separate across segments and missing-video gaps. */
export function trajectoryEdges(paths: TrajectoryPoint[][], atMs: number | null = null, maximumGapMs = 250, intervalsByPath: Array<Array<{ start_ms: number; end_ms: number }>> = []): TrajectoryEdge[] {
  const edges: TrajectoryEdge[] = [];
  for (const [pathIndex, path] of paths.entries()) {
    for (let index = 1; index < path.length; index += 1) {
      const from = path[index - 1];
      const to = path[index];
      const gap = to.timestamp_ms - from.timestamp_ms;
      if (!Number.isFinite(gap) || gap <= 0 || gap > maximumGapMs || (atMs !== null && to.timestamp_ms > atMs)) continue;
      if (![from.x, from.y, to.x, to.y].every(Number.isFinite)) continue;
      const crossesInterpolation = (intervalsByPath[pathIndex] ?? []).some(interval => from.timestamp_ms < interval.end_ms && to.timestamp_ms > interval.start_ms);
      edges.push({ from, to, pathIndex, source: crossesInterpolation || from.source === "interpolated" || to.source === "interpolated" ? "interpolated" : "observed" });
    }
  }
  return edges;
}

/** One stroke per continuous provenance run keeps dash patterns visible at video-frame spacing. */
export function trajectoryStrokes(edges: TrajectoryEdge[]) {
  const strokes: Array<{ source: TrajectoryEdge["source"]; points: TrajectoryPoint[]; pathIndex: number }> = [];
  for (const edge of edges) {
    const last = strokes.at(-1), point = last?.points.at(-1);
    if (last && last.pathIndex === edge.pathIndex && last.source === edge.source && point && point.timestamp_ms === edge.from.timestamp_ms && point.x === edge.from.x && point.y === edge.from.y) last.points.push(edge.to);
    else strokes.push({ source: edge.source, points: [edge.from, edge.to], pathIndex: edge.pathIndex });
  }
  return strokes;
}

export function timestampForVideoTime(seconds: number, originMs = 0) {
  return originMs + seconds * 1000;
}

export function videoTimeForTimestamp(timestampMs: number, originMs = 0) {
  return Math.max(0, (timestampMs - originMs) / 1000);
}
