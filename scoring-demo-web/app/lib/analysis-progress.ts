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
const itemCount = (value: unknown): number | null => typeof value === "number" && Number.isSafeInteger(value) && value >= 0 ? value : null;
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

/** Preserve server progress for compatibility; display only counted stage work. */
export function analysisProgress(job: JobProgress | null, now = Date.now()) {
  const details = job?.progress && typeof job.progress === "object" ? job.progress : {};
  const raw = typeof job?.progress === "number" ? job.progress : details.percent;
  const percent = Math.min(100, positiveNumber(raw) ?? 0);
  const phase = typeof details.phase === "string" ? details.phase : job?.stage ?? "";
  const terminal = ["succeeded", "completed"].includes(job?.status ?? "");
  const failed = ["failed", "cancelled"].includes(job?.status ?? "");
  const cancelling = !terminal && !failed && job?.cancel_requested === true;
  const queued = job?.status === "queued";
  const started = timestamp(job?.started_at);
  const requeued = queued && started !== null;
  const queueStatus = requeued ? "任务已重新排队，等待重新执行。" : "任务尚未开始推理。";
  const reconnecting = !terminal && !failed && job?.client_connection === "reconnecting";
  const executorStatus = job?.executor?.status;
  // Only an explicit server assessment can establish executor availability.
  // Old business progress or a legacy response without supervision cannot.
  const unavailable = !terminal && !failed && ["unavailable", "stopped"].includes(executorStatus ?? "");
  const preparing = !terminal && !failed && ["starting", "restarting"].includes(executorStatus ?? "");
  const title = job?.status === "cancelled" ? "分析已取消" : failed ? "分析已停止" : terminal ? "正在读取训练报告" : cancelling ? "正在取消分析" : reconnecting ? "正在重新连接分析服务" : unavailable ? "分析执行服务暂不可用" : preparing ? executorStatus === "restarting" ? "正在恢复分析服务" : "正在启动分析服务" : queued ? "视频已接收，等待分析" : stageTitles[phase] ?? "正在分析你的动作";
  const reportStage = terminal || !queued && ["writing_results", "finalizing", "scoring_readiness"].includes(phase);
  const completed = itemCount(details.completed_items);
  const total = itemCount(details.total_items);
  const frames = itemCount(details.processed_frames ?? job?.processed_frames ?? job?.processedFrames);
  const frameTotal = itemCount(details.total_frames ?? job?.total_frames ?? job?.totalFrames);
  const done = phase === "inference" ? frames : completed;
  const planned = phase === "inference" ? frameTotal : total;
  const hasStageCount = !!phase && done !== null && planned !== null && planned > 0 && done <= planned;
  const active = !terminal && !failed && !cancelling && !queued && !unavailable && !preparing && !reconnecting;
  // The server's weighted total is not a duration estimate or a stage percentage.
  // Unknown work stays indeterminate, including report delivery after job success.
  const stagePercent = active && hasStageCount ? done! / planned! * 100 : null;
  const count = active && hasStageCount
    ? `${phase === "inference" ? "已识别" : "已完成"} ${done!.toLocaleString("zh-CN")} / ${planned!.toLocaleString("zh-CN")} ${phase === "inference" ? "帧" : "项"}`
    : "";
  const created = timestamp(job?.created_at ?? job?.createdAt);
  const updated = timestamp(job?.updated_at);
  const completedAt = timestamp(job?.cancelled_at ?? job?.completed_at);
  const elapsedEnd = terminal || failed ? completedAt ?? updated : now;
  const elapsed = queued
    ? created !== null && now >= created ? `${requeued ? "距提交" : "已排队"} ${formatElapsed((now - created) / 1000)}，${requeued ? "等待重新执行" : "尚未开始推理"}` : ""
    : started !== null && elapsedEnd !== null && elapsedEnd >= started
      ? `${terminal || failed ? "该任务处理耗时" : unavailable || preparing || reconnecting ? "距本次处理开始" : "服务器已处理"} ${formatElapsed((elapsedEnd - started) / 1000)}`
      : failed && job?.started_at === null && created !== null && elapsedEnd !== null && elapsedEnd >= created
        ? `该任务等待耗时 ${formatElapsed((elapsedEnd - created) / 1000)}` : "";
  const stale = !terminal && !failed && updated !== null && now - updated >= 60000;
  const position = itemCount(job?.queue_position);
  const queueDetail = queued && position !== null && position > 0 ? `当前等待队列第 ${position} 位（不含正在处理的任务）。` : "";
  const detail = terminal ? "视频分析已完成，正在整理并读取报告；这一步完成后才会显示完整结果。"
    : cancelling ? "取消请求已接收，正在等待服务器确认停止。"
    : job?.status === "cancelled" ? "服务器已确认停止处理，可选择新视频。"
    : reconnecting ? "暂时无法读取最新任务状态，正在重新连接。已保留当前任务，无需重新上传。"
    : unavailable ? `${queued ? queueStatus : "当前执行状态需要服务器恢复后确认。"}执行服务暂不可用，视频与任务已保留；页面会继续读取状态。`
    : preparing ? `${queued ? queueStatus : "当前任务的执行状态正在恢复。"}执行服务${executorStatus === "restarting" ? "恢复" : "启动"}后会更新状态，无需重新上传。`
    : queued ? `${queueDetail}${queueStatus}可离开此页，稍后回来继续查看，无需重新上传。`
    : typeof job?.message === "string" && job.message.includes("重连") ? job.message
    : phase === "inference" ? "动作识别完成后还需整理报告。可离开此页，稍后回来查看。"
    : "可离开此页，稍后回到视频分析继续查看。处理时间随片段长度、模型与排队情况变化。";
  const notice = !stale || reconnecting || unavailable || preparing ? ""
    : queued ? `${requeued ? queueStatus : "任务仍在排队，尚未开始推理。"}无需重新上传。`
    : `${job?.executor?.available === true ? "执行服务在线，当前阶段暂未产生新进度" : "当前阶段进度暂未更新"}（${Math.floor((now - updated!) / 60000)} 分钟）。无需重新上传，可稍后继续查看。`;
  return {
    title, phase, percent, stagePercent, reportStage, elapsed, count, detail, notice,
    stepLabel: queued ? "等待分析" : unavailable || preparing ? "等待恢复" : "分析动作",
  };
}
