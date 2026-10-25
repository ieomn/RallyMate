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
    <details className="report-insight-details"><summary><span>五维测量与参考图</span><small>查看适用范围、缺失依据与已有参考分</small></summary><ProcessScorePanel result={result} motion={motion} pending={pending} title={title} description={description} preparedReport={process} showSummary={false} /></details>
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
  const emptyMessage = pending ? "完成后会在这里显示有依据的训练重点。"
    : status === "failed" || status === "error" ? "本次分析未完成，暂时无法形成训练建议。"
    : status === "cancelled" ? "本次分析已取消，暂时没有训练建议。"
    : result || status === "succeeded" || status === "completed" ? "本次分析已完成，但现有证据不足以形成训练建议。可先回看原视频中的动作片段。"
    : "上传一段训练视频，从步伐与转体开始复盘。";
  const clock = (value: number) => `${(value / 1000).toFixed(2)} 秒`;
  return <section className="report-focus report-recommendations report-first-look" id="recommendations" aria-labelledby="report-focus-title">
    <div className="report-section-heading"><div><span className="process-eyebrow">这次先关注</span><h2 id="report-focus-title">{available.length ? focus.length === 1 ? "先看这一项" : "先看这两项" : result ? "先保留一个下次能验证的问题" : "本次复盘重点"}</h2></div><span className="report-evidence-status">{pending ? result ? "部分结果可用" : "等待分析结果" : available.length ? "依据当前可用证据" : "暂不能判断动作"}</span></div>
    {!!focus.length && <p className="report-first-look-intro">根据动作观察选择一个练习目标，再用相同任务复测。通用练习不代表已诊断出你的动作错误。</p>}
    <div className="report-recommendation-grid">{focus.length ? focus.map((item, index) => <article key={item.id} className={`report-focus-item focus-${item.evidenceState}`}>
      <span className="focus-index" aria-hidden="true">0{index + 1}</span><div className="report-focus-body">
        <span className="focus-evidence-label">{item.evidenceState === "observation" ? "已有观察" : item.evidenceState === "review" ? "动作观察" : "无法判断"} · {item.label}</span>
        <h3>{item.title}</h3>
        <p className="focus-observation">{item.observation}</p>
        <dl className="focus-review-steps"><div><dt>看哪段</dt><dd>{item.moment ? `${clock(item.moment.startMs)} – ${clock(item.moment.endMs)} · ${item.moment.kind === "footwork" ? "步伐候选区间" : "挥拍运动区间"}` : "暂无可靠时间定位，可先手动回看原片。"}</dd></div><div><dt>观察什么</dt><dd>{item.question}</dd></div><div><dt>下一步</dt><dd>{item.nextStep}</dd></div></dl>
        {!replayAvailable && item.moment && <p className="focus-media-unavailable">此报告未附可用原片，请保留时间并在原片中核对。</p>}
        <div className="focus-actions">{item.moment && replayAvailable && <button type="button" className="focus-replay" onClick={() => replayMoment(item.moment!, result?.job_id)}>回看这段原片 <span aria-hidden="true">↗</span></button>}{onSaveFocus && <button type="button" className="focus-save" onClick={() => onSaveFocus(focusSelection(item))}>保存为关注点</button>}</div>
        <details className="focus-evidence-detail"><summary>依据与判断边界</summary><p>{item.detail}</p><p>{item.limitation}</p>{item.source_links?.map(source => <p key={source.url}><a href={source.url} target="_blank" rel="noreferrer">{source.title} ↗</a></p>)}</details>
      </div></article>) : <p className="report-status-copy">{emptyMessage}</p>}</div>
    {available.length > 0 && <p className="report-judgment-boundary"><strong>暂时无法判断</strong>这些观察不能单独说明技术好坏或真实发力效率；缺少测量的项目不会按低分处理。</p>}
  </section>;
}
