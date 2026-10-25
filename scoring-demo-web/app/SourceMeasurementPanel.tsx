"use client";

import { useMemo, useState } from "react";
import { productText } from "./lib/presentation-text";
import type { DemoResultResponse } from "./lib/api-types";
import type { RuleReviewCatalog } from "./lib/rule-review";
import { footworkFeatureText } from "./lib/footwork-review";
import { replayMoment } from "./lib/training-report";
import { sourceReviewCoverage, sourceReviewWindows, sourceWindowDisplay } from "./lib/technical-review";
import { sourceMeasurementDetailLines } from "./lib/source-measurement-details";
import "./source-measurements.css";

const formatTime = (ms: number) => `${Number((ms / 1000).toFixed(3))} 秒`;
type WindowMode = "measured" | "unavailable" | "all";

export default function SourceMeasurementPanel({ result, catalog, initialIndicatorId }: {
  result: DemoResultResponse; catalog: RuleReviewCatalog; initialIndicatorId?: string;
}) {
  const available = useMemo(() => catalog.rules.flatMap(rule => {
    const windows = sourceReviewWindows(result, rule.id);
    return windows.length ? [{ rule, windows }] : [];
  }), [catalog, result]);
  const [selectedId, setSelectedId] = useState(initialIndicatorId ?? "");
  const [windowMode, setWindowMode] = useState<WindowMode>("measured");
  const [showAll, setShowAll] = useState(false);
  const selected = available.find(item => item.rule.id === selectedId)
    ?? available.find(item => item.windows.some(window => window.measurements.some(measurement => measurement.status === "measured")))
    ?? available[0];
  if (!selected) return null;

  const { rule, windows } = selected;
  const coverage = sourceReviewCoverage(result, rule.id);
  const displayed = sourceWindowDisplay(windows, windowMode);
  const chooseMode = (mode: WindowMode) => { setWindowMode(mode); setShowAll(false); };

  return <section id="source-measurements" className="source-measurements" aria-labelledby="source-measurements-title">
    <header className="sm-heading">
      <div><h2 id="source-measurements-title">逐项动作测量</h2><p>查看逐段二维测量、计算时段和关键点可见性。</p></div>
      <span>{available.length} 项指标有片段记录</span>
    </header>
    <label className="sm-indicator-select"><span>选择测量指标</span><select value={rule.id} onChange={event => {
      setSelectedId(event.target.value); setWindowMode("measured"); setShowAll(false);
    }}>{available.map(item => <option key={item.rule.id} value={item.rule.id}>{item.rule.event_name} · {item.rule.name}</option>)}</select></label>
    <div className="sm-indicator-heading"><p>{rule.event_name} · {rule.id}</p><h3>{productText(rule.name)}</h3></div>
    <p className="sm-window-count"><strong>{coverage.measured} / {coverage.countsScope === "all" ? coverage.total : coverage.returned}</strong> 个{coverage.countsScope === "all" ? "候选" : "已返回"}片段有测量，{coverage.unavailable} 个未测得。</p>
    <div className="sm-window-filters" role="group" aria-label="选择测量片段范围">
      <button type="button" aria-pressed={windowMode === "measured"} onClick={() => chooseMode("measured")}>有测量</button>
      <button type="button" aria-pressed={windowMode === "unavailable"} onClick={() => chooseMode("unavailable")}>未测得片段</button>
      <button type="button" aria-pressed={windowMode === "all"} onClick={() => chooseMode("all")}>全部已返回 · {coverage.returned}</button>
    </div>
    {!displayed.length && <p className="sm-note">{windowMode === "measured" ? "已返回片段中没有可用测量，可切换到未测得片段查看原因。" : "已返回片段中没有这个范围的记录。"}</p>}
    <div className="sm-windows">{(showAll ? displayed : displayed.slice(0, 3)).map(window => <article key={`${window.event_id}-${window.start_ms}-${window.end_ms}`}>
      <header><strong>{formatTime(window.start_ms)}–{formatTime(window.end_ms)}</strong><button type="button" onClick={() => replayMoment({ startMs: window.start_ms }, result.job_id)}>回看这段</button></header>
      {!window.measurements.some(measurement => measurement.status === "measured") && <p className="sm-unavailable">这段未测得</p>}
      <div className="sm-measurements">{window.measurements.map(measurement => <div className="sm-measurement" key={measurement.feature_name}>
        <div className="sm-measurement-value"><span>{productText(measurement.label_zh)}</span><strong>{measurement.status === "measured" ? footworkFeatureText(measurement.value, measurement.unit) : "未测得"}</strong></div>
        {measurement.reason_zh && <p className="sm-note">{productText(measurement.reason_zh)}</p>}
        {measurement.status === "measured" && (measurement.measurement_window ? <div className="sm-measurement-window">
          <span>{measurement.measurement_window.start_ms === measurement.measurement_window.end_ms ? "同帧衔接" : measurement.measurement_window.start_ms < window.start_ms || measurement.measurement_window.end_ms > window.end_ms ? "跨事件测量区间" : "实际测量区间"}：{formatTime(measurement.measurement_window.start_ms)}–{formatTime(measurement.measurement_window.end_ms)}</span>
          <button type="button" onClick={() => replayMoment({ startMs: measurement.measurement_window!.start_ms }, result.job_id)}>回看此测量</button>
        </div> : <p className="sm-note">未提供可核验的具体计算时段。</p>)}
        {sourceMeasurementDetailLines(measurement).length > 0 && <details><summary>计算说明</summary>{sourceMeasurementDetailLines(measurement).map((line, index) => <p key={index}>{line}</p>)}</details>}
      </div>)}</div>
      <details className="sm-visibility"><summary>关键点可见性与测量限制</summary>
        <strong>{window.visibility.valid_frame_ratio !== null ? `本项所需关键点：${Math.round(window.visibility.valid_frame_ratio * 100)}% 有效帧` : "本时段的关键点可见性待核实"}</strong>
        {window.visibility.valid_frame_ratio !== null && <p>{window.visibility.valid_frame_count} / {window.visibility.total_frame_count} 帧；有效帧门槛为 70%。{window.visibility.valid_frame_ratio < .7 ? "低于门槛，不能据这些测量判级。" : "达到门槛仍需核对其他观测条件和动作要求。"}</p>}
        <p>{productText(window.visibility.reason_zh)}</p>
        {window.limitations_zh.map((note, index) => <p key={`${index}-${productText(note)}`}>{productText(note)}</p>)}
      </details>
    </article>)}</div>
    {displayed.length > 3 && <button type="button" className="sm-expand" aria-expanded={showAll} onClick={() => setShowAll(value => !value)}>{showAll ? "收起片段" : `展开这组 ${displayed.length} 个片段`}</button>}
    {coverage.truncated && <p className="sm-truncation">本报告返回了 {coverage.returned} / {coverage.total} 个运动片段；未返回的片段不能视作未发生。</p>}
    <details className="sm-source"><summary>查看计算与观测要求</summary>
      <p>{productText(rule.definition)}</p>
      {rule.calculation && <p><strong>计算方式</strong>{productText(rule.calculation)}</p>}
      {rule.required_points && <p><strong>所需观测</strong>{productText(rule.required_points)}</p>}
      {rule.unevaluable && <p><strong>不可评价条件</strong>{productText(rule.unevaluable)}</p>}
      {rule.source_issues?.map((issue, index) => <p key={`${index}-${productText(issue)}`}>{productText(issue)}</p>)}
      <p>{productText(rule.implementation.note)}</p>
    </details>
  </section>;
}
