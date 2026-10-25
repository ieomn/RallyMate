"use client";

import { useMemo, useState } from "react";
import { productText } from "./lib/presentation-text";
import type { DemoResultResponse } from "./lib/api-types";
import { footworkFeatureText } from "./lib/footwork-review";
import { replayMoment } from "./lib/training-report";
import { reviewRuleResult, ruleReviewContext, type RuleReviewCatalog } from "./lib/rule-review";
import "./rule-review.css";

const PAGE_SIZE = 8;
const time = (ms: number) => `${Math.floor(ms / 60000)}:${String(Math.floor(ms / 1000) % 60).padStart(2, "0")}.${Math.floor(ms / 100) % 10}`;

export default function RuleReviewPanel({ result, catalog, initialIndicatorId }: {
  result: DemoResultResponse | null; catalog: RuleReviewCatalog; initialIndicatorId?: string;
}) {
  const first = catalog.rules.find(rule => rule.id === initialIndicatorId)
    ?? catalog.rules.find(rule => rule.implementation.kind === "related_2d_measurement") ?? catalog.rules[0];
  const [selectedId, setSelectedId] = useState(first?.id ?? "");
  const [scope, setScope] = useState<"connected" | "all">(first?.implementation.kind === "not_implemented" ? "all" : "connected");
  const [query, setQuery] = useState("");
  const [eventCode, setEventCode] = useState("");
  const [page, setPage] = useState(() => Math.floor(Math.max(0, catalog.rules.filter(rule => first?.implementation.kind === "not_implemented" || rule.implementation.kind === "related_2d_measurement").findIndex(rule => rule.id === first?.id)) / PAGE_SIZE));
  const connected = catalog.rules.filter(rule => rule.implementation.kind === "related_2d_measurement");
  const filtered = useMemo(() => catalog.rules.filter(rule => (scope === "all" || rule.implementation.kind === "related_2d_measurement")
    && (!eventCode || rule.event_code === eventCode)
    && `${rule.id} ${productText(rule.name)} ${rule.event_name} ${rule.stage}`.toLowerCase().includes(query.trim().toLowerCase())), [catalog, scope, eventCode, query]);
  const visible = filtered.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);
  const selected = visible.find(rule => rule.id === selectedId) ?? visible[0];
  const context = useMemo(() => ruleReviewContext(result), [result]);
  const review = selected ? reviewRuleResult(selected, result, context) : null;
  const events = [...new Map(catalog.rules.map(rule => [rule.event_code, rule.event_name])).entries()];
  const samples = review?.windows.slice(0, 3) ?? [];
  if (!catalog.rules.length) return null;

  return <section id="rule-review" className="rule-review" aria-labelledby="rule-review-title">
    <div className="rule-review-heading"><div><h2 id="rule-review-title">技术指标与动作要求</h2><p>查看动作要求、本次测量，以及暂不能判级的原因。</p></div><span>{catalog.rules.length} 项技术指标</span></div>
    <div className="rule-review-context"><strong>技术评级尚未成立</strong><p>等级定义给出了 A～E 的定性标准，未给出完整百分制换算。当前 {connected.length} 项有关联测量，仍需核验适用条件与教练标定。</p></div>
    <div className="rule-review-filters">
      <div role="group" aria-label="规则范围"><button type="button" aria-pressed={scope === "connected"} onClick={() => { setScope("connected"); setPage(0); }}>有关联测量 {connected.length}</button><button type="button" aria-pressed={scope === "all"} onClick={() => { setScope("all"); setPage(0); }}>全部技术指标 {catalog.rules.length}</button></div>
      <label><span className="sr-only">筛选动作</span><select aria-label="筛选动作" value={eventCode} onChange={event => { setEventCode(event.target.value); setPage(0); }}><option value="">全部动作</option>{events.map(([code, name]) => <option key={code} value={code}>{code} · {name}</option>)}</select></label>
      <label><span className="sr-only">搜索技术指标</span><input aria-label="搜索技术指标" value={query} onChange={event => { setQuery(event.target.value); setPage(0); }} placeholder="搜索指标名称或编号" /></label>
    </div>
    <div className="rule-review-layout">
      <div className="rule-review-list"><div className="rule-review-list-items">{visible.map(rule => {
        const state = reviewRuleResult(rule, result, context);
        return <button type="button" key={rule.id} className={rule.id === selected.id ? "is-selected" : ""} aria-pressed={rule.id === selected.id} onClick={() => setSelectedId(rule.id)}><span>{rule.event_name}{rule.stage ? ` · ${rule.stage}` : ""}</span><strong>{productText(rule.name)}</strong><span>{state.statusLabel}</span></button>;
      })}{!visible.length && <p className="rule-review-empty">没有匹配的指标。可以更换动作或清除搜索。</p>}</div>
        <div className="rule-review-pagination"><button type="button" disabled={page === 0} onClick={() => setPage(value => value - 1)}>上一页</button><span>{filtered.length ? page + 1 : 0} / {Math.ceil(filtered.length / PAGE_SIZE)}</span><button type="button" disabled={(page + 1) * PAGE_SIZE >= filtered.length} onClick={() => setPage(value => value + 1)}>下一页</button></div>
      </div>
      {selected && review ? <article className="rule-review-detail" aria-label="技术指标核验详情">
        <div className="rule-review-detail-head"><div><p>{selected.id}</p><h3>{productText(selected.name)}</h3></div><span>{review.statusLabel}</span></div>
        <p className="rule-review-conclusion">{review.reason}</p>
        {!!selected.source_issues?.length && <div className="rule-review-condition"><h4>动作要求待明确</h4>{selected.source_issues.map(issue => <p key={productText(issue)}>{productText(issue)}</p>)}</div>}
        <div className="rule-review-comparison"><div><h4>动作要求</h4><p>{productText(selected.definition)}</p><p><strong>计算方式</strong>{productText(selected.calculation) || "尚未提供独立计算方式。"}</p></div><div><h4>本次实际测到什么</h4>{review.indicator?.representative_measurements?.length ? review.indicator.representative_measurements.map(item => <p key={item.feature_name}><span>{item.label_zh}</span><strong>{item.median_value} {item.unit_zh}</strong><span>{item.sample_count} 个样本的{item.feature_name.endsWith("_direction_deg") ? "方向均值" : "典型值"}，不是单帧值</span></p>) : <p>没有可展示的聚合测量值。</p>}<p>{productText(selected.implementation.note)}</p></div></div>
        <div className="rule-review-condition"><h4>什么情况下不能评级</h4><p>{productText(selected.unevaluable) || "尚未提供本项独立的不可评价条件。"}</p><p>这次不自动给出 A～E：相关测量不能替代完整技术判断，当前没有获验证的等级阈值。</p></div>
        {samples.length > 0 && <details className="rule-review-evidence"><summary>核对视频与逐项测量</summary><p>以下为对应指标的实际片段，展示测量依据；不将聚合差额分摊成单段扣分。{result?.footwork_review?.is_truncated ? "片段列表仅包含服务返回的部分区间。" : ""}</p>{samples.map(sample => <article key={sample.eventId}><div><strong>{time(sample.startMs)} – {time(sample.endMs)}</strong><button type="button" onClick={() => replayMoment({ startMs: sample.startMs }, result?.job_id)}>回看这段</button></div>{sample.measurements.map(measurement => <p key={measurement.feature_name}><span>{measurement.name_zh}</span><strong>{measurement.status === "measured" ? footworkFeatureText(measurement.value, measurement.unit) : "未测得"}</strong>{measurement.status === "unavailable" && <span>{productText(measurement.reason_zh)}</span>}</p>)}</article>)}</details>}
        <details className="rule-review-grades"><summary>查看 A～E 等级定义</summary><p>以下是技术等级定义，尚未判定本次视频属于其中任何一级。</p><dl>{selected.grades.map(item => <div key={item.grade}><dt>{item.grade} 级</dt><dd>{productText(item.definition)}</dd></div>)}</dl></details>
        <details className="rule-review-sources"><summary>查看所需观测</summary><p>{productText(selected.required_points) || "尚未提供独立观测点。"}</p></details>
      </article> : <div className="rule-review-detail rule-review-empty"><h3>没有匹配的技术指标</h3><p>清除搜索或切换动作后，选择一项查看。</p></div>}
    </div>
    {!!catalog.visual_techniques?.length && <details className="rule-review-visual"><summary>查看 {catalog.visual_techniques.length} 类动作的视觉定义</summary><p>视觉定义用于核对动作与阶段，不直接换算为技术分。标为可选的阶段，缺少时不自动扣分。</p><div>{catalog.visual_techniques.map(technique => <details key={technique.id}><summary>{productText(technique.name)}</summary>{technique.phases.map(phase => <article key={phase.id}><h3>{productText(phase.name)}{phase.optional ? "（可选阶段）" : ""}</h3>{phase.clauses.map((clause, index) => <p key={`${clause.source.location}-${index}`}>{productText(clause.text)}</p>)}</article>)}</details>)}</div></details>}

  </section>;
}
