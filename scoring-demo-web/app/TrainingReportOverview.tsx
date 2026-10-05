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
    <section className="report-score" aria-labelledby="report-overview-title">
      <div className="report-score-heading"><h2 id="report-overview-title">本次训练概览</h2><span className="report-state">{pending ? "整理中" : result ? "已完成" : "暂无结果"}</span></div>
      {result && !pending && <div className="report-measurement-summary">{independent ? <><div><strong>{independent.footwork_measured_feature_instances ?? "—"}</strong><span>步伐单项观测</span></div><div><strong>{independent.rotation_local_window_count ?? "—"}</strong><span>转体局部窗口</span></div></> : <div><strong>{counts.measured}<small> / {counts.total}</small></strong><span>项可复核测量</span></div>}</div>}
      {independent?.is_truncated && <p className="report-summary-scope">仅统计已返回片段</p>}
      <p className="report-summary-note">二维画面测量 · 技术评分待教练标定</p>
      {result && !pending && <div className="report-reference-highlight"><span>测量证据参考分</span><strong>{score === null ? "—" : score}<small>{score === null ? "暂不可计算" : "/ 100"}</small></strong><p>{score === null ? "未取得足够的可靠测量。" : `${counts.measured} / ${counts.total} 项有测量；高分不代表动作正确。`}</p><button type="button" onClick={() => { const details = document.getElementById("evidence-reference-details"); if (details instanceof HTMLDetailsElement) details.open = true; document.getElementById("score-explanation")?.scrollIntoView({ behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth", block: "start" }); }}>查看分数构成 <span aria-hidden="true">↓</span></button></div>}
      {(!result || pending) && <p className="report-status-copy"><strong>{title}</strong>{description}</p>}
    </section>
    <section className="report-focus" aria-labelledby="report-focus-title">
      <div className="report-section-heading"><h2 id="report-focus-title">这次先关注</h2></div>
      {focus.length ? focus.map((item, index) => <article key={item.id} className="report-focus-item"><span className="focus-index" aria-hidden="true">0{index + 1}</span><div><small>{item.label}</small><h3>{item.title}</h3><p>{item.detail}</p>{item.moment && <button type="button" onClick={() => replayMoment(item.moment!, result?.job_id)}>回看片段 <span aria-hidden="true">↗</span></button>}</div></article>) : <p className="report-status-copy">{pending ? "完成后会在这里显示有依据的复核重点与补录建议。" : "上传一段训练视频，从步伐与转体开始复盘。"}</p>}
    </section>
  </aside>;
}
