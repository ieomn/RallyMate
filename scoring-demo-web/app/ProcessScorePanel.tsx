"use client";

import { useId, useState } from "react";
import type { DemoResultResponse, MotionAnalysis, ProcessCriterion, ProcessDimension, ProcessDimensionId, ProcessEvaluation, RelativeReference } from "./lib/api-types";
import { emptyProcessDimensions, processCriterionGroups, processCriterionReplay, processDimensionState, processEvaluationOf, processEvaluationValidationOf, processPeakSequence, processRadarGeometry, processScoreText, trainingDimensions } from "./lib/process-evaluation";
import { replayMoment } from "./lib/training-report";
import { relativeReferenceOf } from "./lib/relative-score";
import { trainingScoreOf } from "./lib/training-score";

const displayUnit = (unit: string) => ({ deg: "°", "deg/s": "°/秒", ms: "毫秒", ratio: "比例", fraction: "比例", count: "次", torso: "躯干长度", "torso/s": "躯干长度/秒", body: "身体尺度", "body/s": "身体尺度/秒", "body/s2": "身体尺度/秒²" })[unit] ?? unit;

export function ProcessRadar({ dimensions, selected, reference }: { dimensions: ProcessDimension[]; selected: ProcessDimensionId; reference?: RelativeReference | null }) {
  const id = useId();
  const plotted = dimensions.map(item => {
    const relative = reference?.dimensions.find(row => row.id === item.id);
    return relative ? { ...item, score: relative.score, status: "partial" as const } : item;
  });
  const { points, lines, complete, pointAt } = processRadarGeometry(plotted);
  const labels = dimensions.map((_, index) => { const angle = index * 2 * Math.PI / dimensions.length - Math.PI / 2; return { x: 180 + Math.cos(angle) * 130, y: 142 + Math.sin(angle) * 124 }; });
  return <svg className="process-radar" viewBox="0 0 360 310" role="img" aria-labelledby={`${id}-title ${id}-description`}>
    <title id={`${id}-title`}>五维动作评分图</title>
    <desc id={`${id}-description`}>{plotted.map((item, index) => `${item.name_zh}：${item.score === null ? processDimensionState(dimensions[index]) : `${processScoreText(item.score)} 动作得分`}`).join("；")}。显示已有过程观测对应的分项得分。刻度为 0、25、50、75、100；缺少评分依据的维度留空。</desc>
    <g aria-hidden="true">
      {[25, 50, 75, 100].map(value => <polygon key={value} className="process-radar-grid" points={dimensions.map((_, index) => { const point = pointAt(index, value); return `${point.x},${point.y}`; }).join(" ")} />)}
      {dimensions.map((item, index) => { const point = pointAt(index, 100); return <line key={item.id} className="process-radar-axis" x1="180" y1="150" x2={point.x} y2={point.y} />; })}
      {[0, 25, 50, 75, 100].map(value => <text key={value} className="process-radar-tick" x="186" y={153 - value * .9}>{value}</text>)}
      {complete && <polygon className="process-radar-area" points={points.map(point => `${point!.x},${point!.y}`).join(" ")} />}
      {lines.map(line => <line className="process-radar-line" key={`${line.from.id}-${line.to.id}`} x1={line.from.x} y1={line.from.y} x2={line.to.x} y2={line.to.y} />)}
      {points.map(point => point && <circle className={`process-radar-point${point.id === selected ? " is-selected" : ""}${reference?.dimensions.find(row => row.id === point.id)?.score === null ? " is-neutral" : ""}`} key={point.id} cx={point.x} cy={point.y} r={point.id === selected ? 5 : 4} />)}
      {dimensions.map((item, index) => <g key={item.id} className={item.id === selected ? "process-radar-label is-selected" : "process-radar-label"}>
        <text x={labels[index].x} y={labels[index].y} textAnchor="middle">{item.name_zh}</text>
        <text className="process-radar-label-score" x={labels[index].x} y={labels[index].y + 18} textAnchor="middle">{plotted[index].score !== null ? `${processScoreText(plotted[index].score)} ${"分"}` : "未评分"}</text>
      </g>)}
    </g>
  </svg>;
}

function Criterion({ criterion, result, motion }: { criterion: ProcessCriterion; result: DemoResultResponse | null; motion: MotionAnalysis | null }) {
  const replay = processCriterionReplay(criterion, result, motion);
  const value = criterion.value === null ? null : criterion.id === "swing_direction_consistency" ? criterion.value * 100 : criterion.value;
  const unit = criterion.id === "swing_direction_consistency" ? "%" : criterion.unit ? displayUnit(criterion.unit) : "";
  return <article className="process-criterion">
    <div className="process-criterion-heading"><h4>{criterion.label_zh}</h4><span className={criterion.status === "measured" ? "is-measured" : ""}>{criterion.status === "measured" ? "已测" : "缺测"}</span></div>
    <div className="process-criterion-value"><strong>{value === null ? "—" : Number(value.toFixed(2)).toLocaleString("zh-CN")}</strong>{value !== null && unit && <span>{unit}</span>}{criterion.score !== null && <small>本项得分 {processScoreText(criterion.score)} / 100</small>}</div>
    <p>{criterion.reason_zh || "尚无足够依据，暂不评价。"}</p>
    {criterion.unit?.startsWith("body") && <p>身体尺度用于消除画面大小差异，不是米制距离。</p>}
    {replay && <button type="button" className="process-replay" onClick={() => replayMoment(replay, result?.job_id)}>回看依据片段 <span aria-hidden="true">↗</span></button>}
  </article>;
}

export function RelativeScoreSummary({ result, report, pending }: { result: DemoResultResponse | null; report: ProcessEvaluation | null; pending: boolean }) {
  if (!result || pending || ["failed", "error", "cancelled", "queued", "pending", "running", "processing"].includes(result.status ?? "")) return null;
  const reference = report?.relative_reference ?? relativeReferenceOf();
  const validation = processEvaluationValidationOf(result.process_evaluation, report);
  const observed = reference.observed_dimension_count;
  const current = trainingScoreOf(result, report);
  const formal = current.source === "validated_card_execution";
  return <section className="process-score-panel process-relative-summary" aria-label="本次训练评分">
    <div className="process-score-heading"><div><span className="process-eyebrow">本次训练结果</span><h2>动作评分</h2></div><span className="process-trial">{formal ? `${current.measuredIndicatorCount} 项动作指标已评分` : `${observed} / 5 维有评分依据`}</span></div>
    <div className="process-overall-reference"><strong>{processScoreText(current.score)}{current.score !== null && <small>/ 100</small>}</strong>
      <p>{current.score !== null ? "根据本次已测得的动作项目计算，未测得的项目不计入平均分。" : "当前识别结果还不足以计算动作得分，已测得的关节运动仍可在下方查看。"}</p>
    </div>
    {!formal && validation.status === "invalid" && <p role="alert">{validation.reason_zh}</p>}
    {!formal && <div className="process-reference-method" aria-label="各维度评分">{reference.dimensions.map(item => <section key={item.id}>
      <h3>{item.name_zh} <strong>{item.score === null ? "未评分" : `${processScoreText(item.score)} / 100`}</strong></h3>
    </section>)}</div>}
  </section>;
}

export default function ProcessScorePanel({ result, motion, pending, title, description, preparedReport, showSummary = true }: { result: DemoResultResponse | null; motion: MotionAnalysis | null; pending: boolean; title: string; description: string; preparedReport?: ProcessEvaluation | null; showSummary?: boolean }) {
  const [selectedId, setSelectedId] = useState<ProcessDimensionId>("swing");
  const [selectedEpisodeId, setSelectedEpisodeId] = useState("");
  const id = useId();
  const report = preparedReport === undefined ? processEvaluationOf(result?.process_evaluation) : preparedReport;
  const completed = !!result && !pending && !["failed", "error", "cancelled", "queued", "pending", "running", "processing"].includes(result.status ?? "");
  const reference = report?.relative_reference ?? relativeReferenceOf();
  const dimensions = trainingDimensions(report?.dimensions ?? emptyProcessDimensions());
  const selected = dimensions.find(item => item.id === selectedId)!;
  const selectedReference = reference.dimensions.find(item => item.id === selectedId)!;
  const transition = selected.id === "follow_through" ? report?.dimensions.find(item => item.id === "transition") : null;
  const measuredCount = dimensions.filter(item => item.criteria.some(criterion => criterion.status === "measured")).length;
  const groups = processCriterionGroups(selected.criteria);
  const group = groups.find(item => item.id === selectedEpisodeId) ?? groups[0];
  const criteria = [...(group?.criteria ?? [])].sort((a, b) => Number(b.status === "measured") - Number(a.status === "measured"));
  const sequence = processPeakSequence(criteria);
  return <>{showSummary && <RelativeScoreSummary result={result} report={report} pending={pending} />}<section className="process-score-panel" aria-labelledby={`${id}-heading`} aria-busy={pending}>
    <div className="process-score-heading"><div><span className="process-eyebrow">深入查看 · 测量与适用范围</span><h2 id={`${id}-heading`}>五维过程测量</h2></div><span className="process-trial">{measuredCount} / 5 维有测量</span></div>
    {completed ? <><p className="process-score-contract">图表展示已有过程观测对应的分项得分，缺测维度留空。</p><ProcessRadar dimensions={dimensions} selected={selectedId} reference={reference} /><p className="process-radar-caption"><i aria-hidden="true" />{reference.observed_dimension_count} / 5 维有计分方向 · {measuredCount} / 5 维有过程测量<span>分数反映本次可测的动作项目。</span></p></> : <div className="process-empty" role="status"><strong>{pending ? title : "本次暂无有效动作测量"}</strong><p>{pending ? description : "尚未读取有效报告。"}</p></div>}
    <details className="process-explanation">
    <summary>查看评分依据与动作细节</summary>
    <div className="process-dimension-options" role="group" aria-label="选择过程评价维度">
      {dimensions.map(item => { const score = reference.dimensions.find(row => row.id === item.id)!; return <button key={item.id} type="button" aria-pressed={item.id === selectedId} aria-controls={`${id}-detail`} onClick={() => setSelectedId(item.id)}><span>{item.name_zh}<small>{completed ? score.score === null ? "未评分" : "已评分" : "报告尚未就绪"} · {processDimensionState(item)}</small></span><strong>{completed ? processScoreText(score.score) : "—"}</strong></button>; })}
    </div>
    <section id={`${id}-detail`} className="process-dimension-detail" aria-labelledby={`${id}-detail-heading`}>
      <div className="process-detail-heading"><h3 id={`${id}-detail-heading`}>{selected.name_zh}<span>{completed ? selectedReference.score === null ? "未评分" : "已评分" : "报告尚未就绪"}</span></h3><strong>{completed ? processScoreText(selectedReference.score) : "—"}<small>{completed ? "/ 100" : "待完成"}</small></strong></div>
      <p>{selectedReference.gap.reason_zh}</p>
      <p className="process-dimension-summary">{selected.summary_zh}</p>
      {selected.criteria_selection?.is_truncated && <p className="process-source-scope">展示部分测量，统计基于已读取事件；每类动作测量按时间保留最多 12 条，所有计分子项完整保留。</p>}
      {selected.measurement_input_scope && <p className="process-source-scope">已读取 {selected.measurement_input_scope.returned_event_count} / {selected.measurement_input_scope.available_event_count} 个候选步伐事件，{selected.sample_count ?? 0} 个事件有有效测量。{selected.measurement_input_scope.is_truncated ? "尚有事件未读入，此统计不代表全片。" : ""}</p>}
      {selected.id === "swing" && <p className="process-proxy-note">重点查看肩、髋、腕的二维运动时序与动作方向。画面中的速度峰值先后不能代表真实受力或三维动力链。</p>}
      {groups.length > 1 && <label className="process-episode-picker"><span>查看动作片段 <small>{groups.filter(item => item.id !== "unassigned").length} 段</small></span><select value={group.id} onChange={event => setSelectedEpisodeId(event.target.value)}>{groups.map((item, index) => <option key={item.id} value={item.id}>{item.id === "unassigned" ? "尚缺依据的项目" : `片段 ${index + 1}${item.startMs === null ? "" : ` · ${(item.startMs / 1000).toFixed(2)} 秒`}`}</option>)}</select></label>}
      {sequence && <div className="process-peak-sequence"><span>本片段 · 二维运动峰值先后</span><strong>{sequence}</strong><small>这是画面中的运动顺序，不是真实发力顺序。</small></div>}
      {criteria.map(criterion => <Criterion key={`${criterion.episode_id}:${criterion.id}:${criterion.start_ms}:${criterion.end_ms}`} criterion={criterion} result={result} motion={motion} />)}
      {!criteria.length && <p className="process-no-criteria">{pending ? "分析完成后显示逐项测量与判定依据。" : "本维度暂无有效测量，未计入得分。"}</p>}
      {transition && <details className="process-transition"><summary>两次动作之间的恢复与衔接</summary><p className="process-no-criteria">衔接测量单独保留，不与随挥参考分混合。</p>{transition.criteria.map(criterion => <Criterion key={`${criterion.episode_id}:${criterion.id}`} criterion={criterion} result={result} motion={motion} />)}{!transition.criteria.length && <p className="process-no-criteria">{transition.summary_zh}</p>}</details>}
    </section>
    <p className="process-score-footer">评分基于本次可用的动作测量，原始关节数据与计算过程保留在分析记录中。</p>
    </details>
  </section></>;
}
