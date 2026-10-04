"use client";

import type { DemoResultResponse, MotionAnalysis } from "./lib/api-types";
import { evidenceReferenceScore, measurementCounts } from "./lib/measurement-evidence";
import { analysisReportOf, reportFocus, replayMoment } from "./lib/training-report";

export default function TrainingReportOverview({ result, motion, pending, title, description }: { result: DemoResultResponse | null; motion: MotionAnalysis | null; pending: boolean; title: string; description: string }) {
  const score = evidenceReferenceScore(result?.training_evaluation);
  const counts = measurementCounts(result);
  const focus = reportFocus(result, motion);
  const independent = analysisReportOf(result?.analysis_report)?.measurement_summary;
  return <aside className="training-overview" id="scoreboard" aria-label="训练报告概览">
    <section className="report-score">
      <div className="report-score-heading"><span>本次训练概览</span><span className="report-state">{pending ? "分析中" : result ? "已完成" : "暂无结果"}</span></div>
      <h2>{pending ? "正在整理你的训练报告" : score === null ? "实测指标" : "测量证据参考分（Beta）"}</h2>
      <div className="report-score-number"><strong>{pending ? "—" : score === null ? result ? counts.measured : "—" : score.toFixed(1)}</strong><span>{score === null ? "项有测量" : "/ 100"}</span></div>
      <p className="report-score-meaning">{score === null ? "有证据的项目独立保留；缺测项目不补分。" : "反映测量证据完整程度；高分不代表动作正确。"}</p>
      <div className="report-score-footer"><span>{counts.measured} / {counts.total} 项可复核测量</span><span>技术评分待教练标定</span></div>
      {independent && <div className="report-independent-counts"><span>步伐 {independent.footwork_measured_feature_instances} 次单项观测</span><span>转体 {independent.rotation_local_window_count} 个局部窗口</span>{independent.is_truncated && <small>仅统计已返回片段</small>}</div>}
      {(!result || pending) && <p className="report-status-copy"><strong>{title}</strong>{description}</p>}
    </section>
    <section className="report-focus" aria-labelledby="report-focus-title">
      <div className="report-section-heading"><h2 id="report-focus-title">这次先关注</h2><span>步伐 / 转体</span></div>
      {focus.length ? focus.map((item, index) => <article key={item.id} className="report-focus-item"><span className="focus-index">0{index + 1}</span><div><small>{item.label}</small><h3>{item.title}</h3><p>{item.detail}</p>{item.moment && <button type="button" onClick={() => replayMoment(item.moment!, result?.job_id)}>回看片段 <span>↗</span></button>}</div></article>) : <p className="report-status-copy">{pending ? "完成后会在这里显示有依据的复核重点与补录建议。" : "上传一段训练视频，从步伐与转体开始复盘。"}</p>}
    </section>
  </aside>;
}
