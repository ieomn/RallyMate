import type { TrajectoryPoint, TrajectoryPreviewResponse } from "./api-types";

export type TrajectoryEdge = { from: TrajectoryPoint; to: TrajectoryPoint; source: "observed" | "interpolated"; pathIndex: number };
export type TrajectoryDisplayMode = "segment" | "tail" | "overview";

/** Limit each independent path to the recent past; never draw future detections. */
export function trajectoryWindow(paths: TrajectoryPoint[][], atMs: number, tailMs = 750): TrajectoryPoint[][] {
  return paths.map(points => points.filter(point => point.timestamp_ms <= atMs && point.timestamp_ms >= atMs - tailMs));
}

/** Conservative display filter only; the source inference evidence stays intact. */
export function visibleTrajectoryPaths(paths: TrajectoryPoint[][], atMs: number, options: { showInterpolated?: boolean; minimumConfidence?: number; tailMs?: number; mode?: TrajectoryDisplayMode; trustedSegments?: boolean } = {}): TrajectoryPoint[][] {
  const { showInterpolated = true, minimumConfidence = 0.6, tailMs = 750, mode = "segment", trustedSegments = false } = options;
  const candidates: TrajectoryPoint[][] = [];
  for (const path of trajectoryWindow(paths, atMs, mode === "tail" ? tailMs : Infinity)) {
    let run: TrajectoryPoint[] = [];
    const finish = () => {
      if (run.length >= 2 && run.some(point => Math.hypot(point.x - run[0].x, point.y - run[0].y) >= 0.008)) candidates.push(run);
      run = [];
    };
    for (const point of path) {
      const valid = [point.x, point.y, point.timestamp_ms].every(value => typeof value === "number" && Number.isFinite(value))
        && point.x >= 0 && point.x <= 1 && point.y >= 0 && point.y <= 1
        && (trustedSegments || (point.confidence ?? 0) >= minimumConfidence)
        && (point.source !== "interpolated" || showInterpolated);
      if (!valid) { finish(); continue; }
      const previous = run.at(-1);
      if (previous && (point.timestamp_ms <= previous.timestamp_ms || !trustedSegments && (point.timestamp_ms - previous.timestamp_ms > 250 || Math.hypot(point.x - previous.x, point.y - previous.y) > 0.18))) finish();
      run.push(point);
    }
    finish();
  }
  // Keep the completed path until the next flight starts. Short tail remains an
  // optional view; a detector miss must not erase the entire flight every 150ms.
  return candidates
    .filter(path => mode !== "tail" || atMs - path[path.length - 1].timestamp_ms <= tailMs)
    .sort((a, b) => b[b.length - 1].timestamp_ms - a[a.length - 1].timestamp_ms || (b[b.length - 1].confidence ?? 0) - (a[a.length - 1].confidence ?? 0))
    .slice(0, mode === "overview" ? 12 : 1);
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
  if (reconstruction?.segments.length) return { state: "ready", title: "球路分析已完成", description: "默认保留当前球路的完整已播放轨迹；虚线表示短缺口插值，长缺口分段显示。不叠加人体骨架。" };
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
