"use client";

import { useMemo, useState } from "react";
import { INDICATOR_STATUSES, indicatorAssessmentOf } from "./lib/indicator-assessment";
import { replayMoment } from "./lib/training-report";
import { sourceMeasurementDetailLines } from "./lib/source-measurement-details";
import { footworkUnitText } from "./lib/footwork-review";
import { productText } from "./lib/presentation-text";
import FootCrossingCandidatesPanel, { type FootCrossingPanelContext } from "./FootCrossingCandidatesPanel";

export function IndicatorCandidateEvidence({ indicatorId, expanded, context }: { indicatorId: string; expanded: boolean; context?: FootCrossingPanelContext }) {
  // Unmount on outer collapse so its pending read and seek are cancelled too.
  return expanded && indicatorId === "FS03-M03" ? <FootCrossingCandidatesPanel context={context} /> : null;
}

export default function IndicatorAssessmentPanel({ value, jobId, durationMs, candidateContext }: { value: unknown; jobId?: string; durationMs?: number | null; candidateContext?: FootCrossingPanelContext }) {
  const assessment = useMemo(() => indicatorAssessmentOf(value), [value]);
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("");
  const [selectedId, setSelectedId] = useState("");
  const [observationId, setObservationId] = useState("");
  const [selectedWindow, setSelectedWindow] = useState(0);
  const [expanded, setExpanded] = useState(false);
  if (!assessment) return null;
  const indicators = assessment.indicators.filter(item => (!status || status === item.status) && `${item.indicator_id} ${productText(item.name_zh)}`.toLowerCase().includes(query.toLowerCase().trim()));
  const selected = indicators.find(item => item.indicator_id === selectedId) ?? indicators[0];
  const observations = selected ? assessment.observations.filter(item => selected.observation_ids.includes(item.observation_id)) : [];
  const observation = observations.find(item => item.observation_id === observationId) ?? observations[0];
  const window = observation?.status === "partial" ? observation.windows[selectedWindow] ?? observation.windows[0] : null;
  const displayValue = window ? window.statistics[observation.feature_name.endsWith("fraction") ? "mean" : "median"] : observation?.value;
  const statistics = window?.statistics ?? observation?.statistics ?? {};
  const details = observation ? sourceMeasurementDetailLines(observation) : [];
  const replay = window ?? observation;
  const replayAllowed = replay && replay.start_ms !== null && replay.end_ms !== null && replay.end_ms > replay.start_ms && typeof durationMs === "number" && Number.isFinite(durationMs) && replay.end_ms <= durationMs;
  const measured = assessment.indicators.filter(item => item.observation_ids.length > 0).length;
  const format = (value: number) => Number(value.toFixed(3)).toLocaleString("zh-CN");
  return <details className="indicator-assessment" onToggle={event => { if (event.target === event.currentTarget) setExpanded(event.currentTarget.open); }}><summary>逐项指标判定 <span>{assessment.indicators.length} 项均有说明 · {measured} 项有观测</span></summary>
    <p className="indicator-assessment-intro">{productText(assessment.summary_zh) || "逐项查看动作测量、计算方法与适用范围。"}</p>
    {assessment.is_truncated && <p className="indicator-assessment-intro">展示 {assessment.returned_observation_count} / {assessment.full_observation_count} 条代表观测。{productText(assessment.observation_selection.reason_zh)}</p>}
    <div className="indicator-filters"><label>搜索指标<input type="search" value={query} onChange={event => setQuery(event.target.value)} placeholder="名称或编号" /></label><label>判定状态<select value={status} onChange={event => setStatus(event.target.value)}><option value="">全部状态</option>{Object.entries(INDICATOR_STATUSES).map(([key, label]) => <option key={key} value={key}>{productText(label)}（{assessment.indicators.filter(item => item.status === key).length}）</option>)}</select></label></div>
    <label className="indicator-picker">选择指标 <span>共 {indicators.length} 项</span><select value={selected?.indicator_id ?? ""} onChange={event => { setSelectedId(event.target.value); setObservationId(""); }}>{indicators.map(item => <option value={item.indicator_id} key={item.indicator_id}>{item.indicator_id} · {productText(item.name_zh)}</option>)}</select></label>
    {selected ? <article className="indicator-detail"><span className="indicator-state">{productText(selected.status_label_zh)}</span><h3>{productText(selected.name_zh)}</h3><p>{productText(selected.explanation_zh)}</p>{selected.calculation_zh && <details><summary>如何计算</summary><p>{productText(selected.calculation_zh)}</p></details>}
      {observations.length > 1 && <label className="indicator-picker">选择观测 <span>{selected.observations_truncated ? `${observations.length} / ${selected.observation_count} 条代表观测` : `${observations.length} 条`}</span><select value={observation?.observation_id ?? ""} onChange={event => { setObservationId(event.target.value); setSelectedWindow(0); }}>{observations.map((item, index) => <option value={item.observation_id} key={item.observation_id}>{index + 1}. {productText(item.label_zh)}{item.start_ms === null ? "" : ` · ${(item.start_ms / 1000).toFixed(2)} 秒`}</option>)}</select></label>}
      {observation && <div className="indicator-observation"><span>{productText(observation.label_zh)}{observation.phase_label_zh ? ` · ${productText(observation.phase_label_zh)}` : ""}{observation.status === "partial" ? " · 分段测量" : ""}</span>
        {observation.status === "partial" && <p>以下连续片段仅代表局部画面，不作为整个阶段的起止变化。</p>}
        {observation.status === "partial" && observation.windows.length > 0 && <label className="indicator-picker">选择连续测量片段<select value={Math.min(selectedWindow, observation.windows.length - 1)} onChange={event => setSelectedWindow(Number(event.target.value))}>{observation.windows.map((part, index) => <option key={`${part.start_ms}-${part.end_ms}`} value={index}>{index + 1}. {(part.start_ms / 1000).toFixed(2)}–{(part.end_ms / 1000).toFixed(2)} 秒</option>)}</select></label>}
        <strong>{displayValue === null || displayValue === undefined ? "分段测量" : format(displayValue)} <small>{displayValue === null || displayValue === undefined ? "" : footworkUnitText(observation.unit)}{window ? " · 局部片段" : ""}</small></strong><p>{productText(observation.definition_zh)}</p>{!window && observation.condition && <p>{productText(observation.condition.label_zh)}（仅描述测量变化）</p>}
        {Object.keys(statistics).length > 0 && <dl>{Object.entries(statistics).map(([key, value]) => <div key={key}><dt>{({ start: "起始", end: "结束", delta: "变化", min: "最小", max: "最大", median: "中位数", mean: "均值", range: "范围" } as Record<string, string>)[key]}</dt><dd>{format(value)}</dd></div>)}</dl>}
        {replayAllowed && <button type="button" className="product-secondary" onClick={() => replayMoment({ startMs: replay.start_ms! }, jobId)}>回看依据 · {(replay.start_ms! / 1000).toFixed(2)}–{(replay.end_ms! / 1000).toFixed(2)} 秒</button>}
        {details.length > 0 && <details><summary>计算说明</summary>{details.map((line, index) => <p key={index}>{productText(line)}</p>)}</details>}
      </div>}
      {selected.blockers.length > 0 && <details><summary>尚缺哪些依据</summary><ul>{selected.blockers.map((blocker, index) => <li key={`${blocker.code}-${index}`}>{productText(blocker.message_zh)}</li>)}</ul></details>}
      <p className="indicator-grade-note">{productText(selected.grade.label_zh)}。{productText(selected.grade.reason_zh)}</p>
      {selected.grade.status === "proxy_scored" && selected.grade.grade_counts && <p className="indicator-grade-note">代理规则事件等级：{Object.entries(selected.grade.grade_counts).filter(([, count]) => count > 0).map(([grade, count]) => `${grade} 级 ${count} 次`).join(" · ")}。仅描述本次可计算项目。</p>}
      <IndicatorCandidateEvidence indicatorId={selected.indicator_id} expanded={expanded} context={candidateContext} />

    </article> : <p className="indicator-assessment-intro">没有匹配的指标，可清除搜索或切换状态。</p>}
  </details>;
}
