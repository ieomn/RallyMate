"use client";

import type { DemoResultResponse, MotionAnalysis, ProcessEvaluation } from "./lib/api-types";
import { evidenceReferenceScore, measurementCounts } from "./lib/measurement-evidence";
import { analysisReportOf, replayMoment } from "./lib/training-report";
import ProcessScorePanel, { RelativeScoreSummary } from "./ProcessScorePanel";
import { processEvaluationOf } from "./lib/process-evaluation";
import type { ReactNode } from "react";
import { focusSelection, trainingGuidance, type TrainingFocusSelection } from "./lib/training-guidance";
import type { FootCrossingPanelContext } from "./FootCrossingCandidatesPanel";

export default function TrainingReportOverview({ result, motion, pending, title, description, children, preparedReport, showSummary = true }: { result: DemoResultResponse | null; motion: MotionAnalysis | null; pending: boolean; title: string; description: string; children?: ReactNode; candidateContext?: FootCrossingPanelContext; preparedReport?: ProcessEvaluation | null; showSummary?: boolean }) {
  const score = evidenceReferenceScore(result?.training_evaluation);
  const counts = measurementCounts(result);
  const independent = analysisReportOf(result?.analysis_report)?.measurement_summary;
  // Validate once per render and share it; do not cache mutable input by identity.
  const process = preparedReport === undefined ? processEvaluationOf(result?.process_evaluation) : preparedReport;
  return <aside className="training-overview" id="scoreboard" aria-label="训练报告概览">
    {showSummary && <RelativeScoreSummary result={result} report={process} pending={pending} />}
    <ProcessScorePanel result={result} motion={motion} pending={pending} title={title} description={description} preparedReport={process} showSummary={false} />
    <details className="report-score report-process-auxiliary">
      <summary>测量情况 · 辅助查看</summary>
      <div className="report-score-heading"><h2 id="report-overview-title">本次测量概览</h2><span className="report-state">{pending ? result ? "部分结果可用" : "整理中" : result ? "已读取结果" : "暂无结果"}</span></div>
      {result && !pending && <div className="report-measurement-summary">{independent ? <><div><strong>{independent.footwork_measured_feature_instances ?? "—"}</strong><span>步伐单项观测</span></div><div><strong>{independent.rotation_local_window_count ?? "—"}</strong><span>转体局部窗口</span></div></> : <div><strong>{counts.measured}<small> / {counts.total}</small></strong><span>项可复核测量</span></div>}</div>}
      {independent?.is_truncated && <p className="report-summary-scope">仅统计已返回片段</p>}
      <p className="report-summary-note">二维过程测量 · 完整评分依据见各项说明</p>
      {result && !pending && <div className="report-reference-score"><p>测量证据参考分：{score === null ? "—（暂不可计算）" : `${score} / 100`}。</p><p>{score === null ? "当前未提供可用的测量证据参考分；已保存的逐项测量仍可查看。" : `${counts.measured} / ${counts.total} 项有测量；高分不代表动作正确。`} 此分仅用于了解测量情况，不参与过程评分。</p></div>}
      {(!result || pending) && <p className="report-status-copy"><strong>{title}</strong>{description}</p>}
    </details>
    {children}
  </aside>;
}

export function TrainingRecommendations({ result, motion, pending, status, onSaveFocus, replayAvailable = true }: { result: DemoResultResponse | null; motion: MotionAnalysis | null; pending: boolean; status?: string; onSaveFocus?: (focus: TrainingFocusSelection) => void; replayAvailable?: boolean }) {
  const focus = trainingGuidance(result, motion).sort((a, b) => Number(a.evidenceState === "unavailable") - Number(b.evidenceState === "unavailable")).slice(0, 2);
  const available = focus.filter(item => item.evidenceState !== "unavailable");
  const emptyMessage = pending ? "分析完成后会显示本次观察和对应练习。"
    : status === "failed" || status === "error" ? "本次分析未完成，暂时无法形成训练建议。"
    : status === "cancelled" ? "本次分析已取消，暂时没有训练建议。"
    : result || status === "succeeded" || status === "completed" ? "本次没有足够的动作测量。下次保留准备、挥拍和回位的完整画面，再比较同一项练习。"
    : "上传一段训练视频，获得动作观察、练习步骤和复测方法。";
  const clock = (value: number) => `${(value / 1000).toFixed(2)} 秒`;
  return <section className="report-focus report-recommendations report-first-look" id="recommendations" aria-labelledby="report-focus-title">
    <div className="report-section-heading"><div><span className="process-eyebrow">下一次怎么练</span><h2 id="report-focus-title">{focus.length ? focus.length === 1 ? "先练这一项" : "从这两项中选一项开始" : "本次训练建议"}</h2></div><span className="report-evidence-status">{pending ? result ? "部分结果可用" : "等待分析结果" : available.length ? "结合本次测量" : "基础练习"}</span></div>
    {!!focus.length && <p className="report-first-look-intro">每次只练一个目标，先用慢球完成，再用相同任务记录进步。</p>}
    <div className="report-recommendation-grid">{focus.length ? focus.map((item, index) => <article key={item.id} className={`report-focus-item focus-${item.evidenceState}`}>
      <span className="focus-index" aria-hidden="true">0{index + 1}</span><div className="report-focus-body">
        <span className="focus-evidence-label">{item.label}</span>
        <h3>{item.title}</h3>
        <p className="focus-observation"><strong>本次观察：</strong>{item.observation}</p>
        <dl className="focus-review-steps"><div><dt>练习目标</dt><dd>{item.goal}</dd></div><div><dt>怎么练</dt><dd>{item.drill}</dd></div><div><dt>复测标准</dt><dd>{item.retest}</dd></div>{item.moment && <div><dt>对应片段</dt><dd>{clock(item.moment.startMs)} – {clock(item.moment.endMs)}</dd></div>}</dl>
        {!replayAvailable && item.moment && <p className="focus-media-unavailable">此报告未附可用原片，可按上方时间在自己的视频中查看。</p>}
        <div className="focus-actions">{item.moment && replayAvailable && <button type="button" className="focus-replay" onClick={() => replayMoment(item.moment!, result?.job_id)}>回看这段原片 <span aria-hidden="true">↗</span></button>}{onSaveFocus && <button type="button" className="focus-save" onClick={() => onSaveFocus(focusSelection(item))}>保存练习目标</button>}</div>
        <details className="focus-evidence-detail"><summary>测量说明与练习参考</summary><p>{item.detail}</p><p>{item.limitation}</p>{item.source_links?.map(source => <p key={source.url}><a href={source.url} target="_blank" rel="noreferrer">{source.title} ↗</a></p>)}</details>
      </div></article>) : <p className="report-status-copy">{emptyMessage}</p>}</div>
    {!!focus.length && <p className="report-judgment-boundary">建议从舒适、低强度的节奏开始；出现不适时停止。</p>}
  </section>;
}
