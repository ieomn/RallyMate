import type { JobProgress } from "./api-types";

const stageTitles: Record<string, string> = {
  loading_models: "正在准备分析",
  inference: "正在识别视频动作",
  preparing_replay: "正在准备视频回放",
  tracking_player: "正在整理人物轨迹",
  recognizing_strokes: "正在识别挥拍片段",
  detecting_events: "正在划分动作片段",
  extracting_features: "正在计算动作测量",
  scoring: "正在整理测量结果",
  writing_results: "正在保存测量结果",
  finalizing: "正在生成训练报告",
  scoring_readiness: "正在校验报告完整性",
};

const positiveNumber = (value: unknown): number | null => typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
const timestamp = (value: unknown): number | null => {
  if (typeof value !== "string" || !value.includes("T")) return null;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : null;
};

export function formatElapsed(seconds: number) {
  const value = Math.max(0, Math.floor(seconds));
  if (value < 60) return `${value} 秒`;
  const minutes = Math.floor(value / 60);
  return `${minutes} 分 ${String(value % 60).padStart(2, "0")} 秒`;
}

/** Server-reported work only: a high percentage never implies completion. */
export function analysisProgress(job: JobProgress | null, now = Date.now()) {
  const details = job?.progress && typeof job.progress === "object" ? job.progress : {};
  const raw = typeof job?.progress === "number" ? job.progress : details.percent;
  const percent = Math.min(100, positiveNumber(raw) ?? 0);
  const phase = typeof details.phase === "string" ? details.phase : job?.stage ?? "";
  const terminal = ["succeeded", "completed"].includes(job?.status ?? "");
  const failed = ["failed", "cancelled"].includes(job?.status ?? "");
  const title = failed ? "分析已停止" : terminal ? "正在读取训练报告" : job?.status === "queued" ? "视频已接收，等待分析" : stageTitles[phase] ?? "正在分析你的动作";
  const reportStage = terminal || ["writing_results", "finalizing", "scoring_readiness"].includes(phase);
  const completed = positiveNumber(details.completed_items);
  const total = positiveNumber(details.total_items);
  const frames = positiveNumber(details.processed_frames ?? job?.processed_frames ?? job?.processedFrames);
  const frameTotal = positiveNumber(details.total_frames ?? job?.total_frames ?? job?.totalFrames);
  const count = completed !== null && total !== null && total > 0
    ? `已完成 ${Math.min(Math.floor(completed), Math.floor(total)).toLocaleString("zh-CN")} / ${Math.floor(total).toLocaleString("zh-CN")} 项`
    : phase === "inference" && frames !== null && frameTotal !== null && frameTotal > 0
      ? `已识别 ${Math.floor(frames).toLocaleString("zh-CN")} / ${Math.floor(frameTotal).toLocaleString("zh-CN")} 帧`
      : "";
  const started = timestamp(job?.started_at ?? job?.created_at ?? job?.createdAt);
  const updated = timestamp(job?.updated_at);
  const completedAt = timestamp(job?.completed_at);
  const elapsedEnd = terminal || failed ? completedAt ?? updated : now;
  const elapsed = started !== null && elapsedEnd !== null && elapsedEnd >= started ? `已用时 ${formatElapsed((elapsedEnd - started) / 1000)}` : "";
  const stale = !terminal && !failed && updated !== null && now - updated >= 60000;
  return {
    title, phase, percent, reportStage, elapsed, count,
    detail: terminal ? "分析已完成，报告读取完成后会自动显示。" : "可离开此页，稍后回到视频分析继续查看。",
    notice: stale ? `已有 ${Math.floor((now - updated!) / 60000)} 分钟未收到新进度。无需重新上传，可稍后继续查看。` : "",
  };
}
