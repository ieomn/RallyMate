"use client";

import { useId, useMemo, useState } from "react";
import type { DemoResultResponse } from "./lib/api-types";
import { replayMoment } from "./lib/training-report";
import { componentImpact, scoreEvidenceWindows, scoreExplanationOf, type IndicatorExplanation, type ScoreComponentKey } from "./lib/score-explanation";
import "./score-explanation.css";

const points = (value: number) => value.toLocaleString("zh-CN", { maximumFractionDigits: 2 });
const signedPoints = (value: number) => `${value < 0 ? "−" : "+"}${points(Math.abs(value))}`;
const timestamp = (value: number) => `${(value / 1000).toFixed(2)} 秒`;

function IndicatorDetail({ indicator, componentKey, result, includedCount }: {
  indicator: IndicatorExplanation; componentKey: ScoreComponentKey; result: DemoResultResponse | null; includedCount: number;
}) {
  const part = indicator.components.find(item => item.key === componentKey)!;
  const impact = componentImpact(indicator, componentKey, includedCount);
  const windows = scoreEvidenceWindows(result, indicator.id);
  const canReplay = Boolean(result?.job_id && /^[a-zA-Z0-9_-]{8,80}$/.test(result.job_id));
  return <details className="sx-indicator">
    <summary><span><strong>{indicator.name}</strong><span>对总分的影响</span></span><strong className="sx-impact">{impact === null ? "—" : `${impact > 0 ? "−" : ""}${points(impact)} 分`}</strong><span className="sx-expand" aria-hidden="true">+</span></summary>
    <div className="sx-indicator-body">
      {part.included && <dl className="sx-ledger"><div><dt>本项可得</dt><dd>{points(part.maximum!)} 分</dd></div><div><dt>本项实得</dt><dd>{points(part.earned!)} 分</dd></div><div><dt>本项差额</dt><dd>{points(part.deduction!)} 分</dd></div></dl>}
      {part.included && <p className="sx-reason">该指标与其余 {Math.max(0, includedCount - 1)} 项等权平均，影响总分 {points(impact!)} 分。</p>}
      {part.included && <p className="sx-meta">本分量证据值 {points(part.value!)}% · 实际权重 {points(part.weight! * 100)}%</p>}
      <div className="sx-indicator-score"><span>此指标的证据参考分</span><strong>{points(indicator.score!)} / 100</strong></div>
      <div className="sx-detail-actions">{canReplay && windows.map(window => <button type="button" key={window.id} onClick={() => replayMoment(window, result?.job_id)}>回看 {timestamp(window.startMs)}–{timestamp(window.endMs)} <span aria-hidden="true">↗</span></button>)}</div>
      {canReplay && windows.length > 0 ? <p className="sx-meta">以上是有实际测量的示例片段，不能把整项差额归因于某一段动作。</p> : <p className="sx-meta">本项暂无可验证的视频定位；仍可核对上面的测量依据。</p>}
    </div>
  </details>;
}

export default function ScoreExplanationPanel({ result }: { result: DemoResultResponse | null }) {
  const headingId = useId();
  const state = useMemo(() => scoreExplanationOf(result), [result]);
  const [selected, select] = useState<ScoreComponentKey>(() => state.status === "verified" ? [...state.explanation.components].sort((left, right) => (right.deduction ?? -1) - (left.deduction ?? -1))[0]?.key ?? "measured_instance_ratio" : "measured_instance_ratio");
  const [showAll, setShowAll] = useState(false);
  const model = state.status === "verified" ? state.explanation : null;
  const active = model?.components.find(component => component.key === selected);
  const included = model?.indicators.filter(item => item.included) ?? [];
  const ordered = [...included].sort((left, right) => (componentImpact(right, selected, included.length) ?? -1) - (componentImpact(left, selected, included.length) ?? -1));
  const hasScore = model?.status === "available";
  const deductions = model?.components.reduce((total, part) => total + (part.deduction ?? 0), 0) ?? 0;
  return <section className="score-explanation" id="score-explanation" aria-labelledby={headingId}>
    <div className="sx-heading"><div><h2 id={headingId}>这个分数怎么算？</h2><p>查看测量证据参考分及各分量构成。</p></div><div className="sx-total"><strong>{state.score === null ? "—" : points(state.score)}</strong>{state.score !== null && <span>/ 100</span>}</div></div>
    {hasScore && model && <>
      <div className="sx-equation"><span>100</span><span aria-hidden="true">−</span><span><strong>{points(deductions)}</strong><span>各分量差额</span></span><span><strong>{signedPoints(model.rounding!)}</strong><span>取整调整</span></span><span aria-hidden="true">=</span><strong>{points(model.score!)}</strong></div>
      <p className="sx-calculation-note">{included.length} 项有可核验分数的指标等权平均。单项取整影响 {signedPoints(model.indicatorRounding!)} 分，总分取整 {signedPoints(model.aggregateRounding!)} 分。</p>
      <div className="sx-components" aria-label="选择分数构成">{model.components.map(component => <button type="button" key={component.key} aria-pressed={component.key === selected} onClick={() => { select(component.key); setShowAll(false); }}><span>{component.label}</span><strong>{component.included ? `${component.deduction! > 0 ? "−" : ""}${points(component.deduction!)} 分` : "—"}</strong></button>)}</div>
      {active && <div className="sx-impact-section"><div className="sx-section-heading"><h3>{active.label}</h3>{active.included && <p>按影响排序。展开一项，查看差额来自哪里。</p>}</div><div className="sx-indicators" key={selected}>{(showAll ? ordered : ordered.slice(0, 5)).map(indicator => <IndicatorDetail key={indicator.id} indicator={indicator} componentKey={selected} result={result} includedCount={included.length} />)}</div>{ordered.length > 5 && <button className="sx-show-all" type="button" onClick={() => setShowAll(value => !value)}>{showAll ? "收起为前 5 项" : `查看全部 ${ordered.length} 项`}</button>}</div>}
    </>}
    {state.status === "invalid" && <p className="sx-unavailable" role="alert">分数明细校验失败，请重新读取报告。</p>}
  </section>;
}
