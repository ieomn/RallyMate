import { safeRequestDiagnostic, type RequestDiagnostic } from "./lib/request-diagnostics";

const operations: Record<RequestDiagnostic["operation"], string> = {
  job: "任务状态", report: "训练报告", trajectory: "球路", technique_assessment: "动作依据",
  pose_preview: "动作标记", technical_review: "技术评审", meta: "服务信息", other_read: "其他读取",
};
const stages: Record<RequestDiagnostic["stage"], string> = { headers: "收到响应", body: "接收内容", json: "解析数据", decode: "恢复报告" };
const categories: Record<NonNullable<RequestDiagnostic["category"]>, string> = {
  http: "服务返回错误", timeout: "读取超时", network: "连接未完成", body_read: "内容读取中断",
  json_parse: "数据格式无法解析", response_contract: "响应格式不符合要求", decode: "报告恢复失败", preparation_timeout: "报告整理等待超时",
  pose_normalization: "动作标记格式或任务不匹配", pose_window_mismatch: "动作标记时间窗不匹配",
};

export default function RequestReadDetails({ diagnostics }: { diagnostics: readonly RequestDiagnostic[] }) {
  const rows = diagnostics.slice(-48).map(safeRequestDiagnostic).filter((row): row is RequestDiagnostic => row !== null);
  if (!rows.length) return null;
  return <details className="report-replay-details" aria-label="结果读取详情">
    <summary>结果读取详情</summary>
    <p>读取未完成时，可用下列请求编号定位原因。</p>
    <ol>{rows.map((row, index) => <li key={`${row.requestId}-${row.stage}-${index}`}>
      <strong>{operations[row.operation]} · {row.operation === "pose_preview" && row.stage === "decode" ? "核对动作标记" : stages[row.stage]}：{row.outcome === "success" ? "完成" : "未完成"}</strong>
      <p>{row.category ? `${categories[row.category]}；` : ""}HTTP {row.httpStatus ?? "未收到"}；正文{row.bodyComplete ? "已收完" : "未收完"}；耗时 {Math.round(row.elapsedMs)} ms</p>
      <p>请求编号：<code>{row.requestId ?? "未取得"}</code> · 开始时间：<time dateTime={row.startedAt}>{row.startedAt}</time></p>
    </li>)}</ol>
  </details>;
}
