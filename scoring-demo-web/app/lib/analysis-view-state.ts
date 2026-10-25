export type AnalysisViewInput = {
  uploadState: "idle" | "ready" | "uploading" | "processing" | "complete" | "cancelled" | "error";
  jobStatus?: string;
  hasResult: boolean;
  hasReplay: boolean;
  missingJob?: boolean;
  warning?: string | null;
};

/** UI delivery state is separate from the analyzer's terminal status. */
export function analysisViewState(input: AnalysisViewInput) {
  const { uploadState, jobStatus, hasResult, hasReplay, missingJob, warning } = input;
  const reading = ["succeeded", "completed"].includes(jobStatus ?? "");
  const phase = missingJob ? "missing"
    : uploadState === "cancelled" || jobStatus === "cancelled" ? "cancelled"
    : uploadState === "error" ? reading ? "read-error" : "failed"
    : uploadState === "uploading" ? "uploading"
    : uploadState === "processing" ? hasResult ? "partial" : reading ? "reading" : "analyzing"
    : uploadState === "complete" ? warning ? "partial" : "complete"
    : "idle";
  return {
    phase,
    showReport: hasResult && !missingJob,
    showReplayOnly: !hasResult && hasReplay && !missingJob,
    showNavigation: hasResult && !missingJob,
    recoveryTitle: missingJob ? "当前服务找不到这份记录"
      : phase === "read-error" ? "分析已完成，报告读取中断"
      : "这次分析暂时无法完成",
    recoveryDetail: missingJob ? "记录和关注点仍保存在此浏览器。它可能来自另一个分析服务；可导入报告备份，或返回训练记录。" : "",
    canResumeRead: !missingJob && !["failed", "cancelled"].includes(jobStatus ?? ""),
  };
}
