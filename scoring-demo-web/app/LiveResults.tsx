"use client";

import { useState, type CSSProperties } from "react";
import type { DemoResultResponse, TechniqueAssessmentResponse, TechniqueCatalogResponse } from "./lib/api-types";

const FAMILIES = { baseline: "底线", serve: "发球", return: "接发", net_attack: "网前", footwork: "步伐" };
const scoreText = (value: number | null | undefined) => typeof value === "number" && Number.isFinite(value) ? value.toFixed(1) : "—";

export default function LiveResults({ result, assessment, catalog, pending }: { result: DemoResultResponse | null; assessment: TechniqueAssessmentResponse | null; catalog: TechniqueCatalogResponse | null; pending: boolean }) {
  const [family, setFamily] = useState("footwork");
  const [selected, setSelected] = useState("");
  const [query, setQuery] = useState("");
  const training = result?.training_evaluation;
  const measured = training?.indicator_evaluations ?? result?.actions?.flatMap(action => action.indicator_evaluations ?? []) ?? [];
  const indicators = [...new Map(measured.map(item => [item.indicator_id, item])).values()];
  const selectedIndicator = indicators.find(item => item.indicator_id === selected) ?? indicators[0];
  const techniques = assessment?.techniques.filter(item => item.family === family && item.name_zh.includes(query)) ?? [];
  const selectedTechnique = techniques.find(item => item.technique_id === selected) ?? techniques[0];
  const readyCount = assessment?.techniques.filter(item => item.observed).length ?? 0;
  const finiteScore = typeof training?.score_0_to_100 === "number" && Number.isFinite(training.score_0_to_100) ? training.score_0_to_100 : null;

  return <div className="live-results" aria-live="polite">
    <section className="score-overview" id="scoreboard">
      <div className="score-card score-na">
        <div className="score-ring" style={{ "--score": finiteScore ?? 0 } as CSSProperties}><div><span>{pending ? "分析中" : "动作表现"}</span><strong>{scoreText(finiteScore)}</strong><small>练习参考分 / 100</small></div></div>
        <div className="score-summary"><span className="card-kicker">YOUR ANALYSIS</span><h2>{pending ? "正在识别这一段动作" : training?.level_zh || "等待可用的动作证据"}</h2><p>{pending ? "文件已接收。分析在后台继续，刷新页面后会自动恢复进度，也可以先向 AI 提问。" : training?.summary_zh || "评分只依据当前视频中的有效观察。未能测量的动作不补分。"}</p><div className="fact-row"><div><small>已观测动作</small><strong>{readyCount} / {assessment?.techniques.length ?? catalog?.techniques.length ?? 24}</strong></div><div><small>可查看指标</small><strong>{indicators.length} 项</strong></div><div><small>证据就绪度</small><strong>{scoreText(assessment?.overall_evidence_score_0_to_100)}%</strong></div></div><p className="score-disclosure">参考分反映当前可测动作的完成与稳定情况；证据覆盖率单独展示，不代表竞技等级。</p></div>
      </div>
      {!!result?.actions?.length && <div className="module-cards live-action-cards">{result.actions.map(action => <button key={action.event_code} className="module-card" onClick={() => { setFamily(action.family || (action.event_code.startsWith("GS") ? "baseline" : "footwork")); setSelected(action.indicator_evaluations?.[0]?.indicator_id ?? ""); document.getElementById("rules")?.scrollIntoView({ behavior: "smooth" }); }}><div className="module-head"><span>{action.name_zh}</span><i>{action.detected_segments} 段</i></div><strong>{scoreText(action.performance_assessment?.score_0_to_100)}</strong><p>{action.performance_assessment?.level_zh || "未评分"}</p></button>)}</div>}
    </section>
    <section className="workbench" id="rules"><div className="workbench-head"><div><span className="section-number">02</span><div><span className="card-kicker">MOTION DETAILS</span><h2>逐项评分与证据回放</h2></div></div></div>
      <div className="event-rail" aria-label="选择动作类别">{Object.entries(FAMILIES).map(([key, name]) => <button key={key} aria-pressed={key === family} className={key === family ? "active" : ""} onClick={() => { setFamily(key); setSelected(""); }}><strong>{name}</strong><small>{assessment?.family_summary?.[key]?.observed_count ?? 0} 项已观测</small></button>)}</div>
      <div className="rules-layout"><div className="rules-list"><div className="rules-list-head"><h3>{FAMILIES[family as keyof typeof FAMILIES]} · 当前视频</h3><label className="search-box"><span>⌕</span><input aria-label="搜索当前视频的指标" placeholder="搜索指标" value={query} onChange={event => setQuery(event.target.value)} /></label></div>
        {family === "footwork" && indicators.length > 0 && <><div className="table-head"><span>动作指标</span><span>测量</span><span>参考分</span></div><div className="result-rows">{indicators.filter(item => item.name_zh.includes(query)).map(item => <button key={item.indicator_id} onClick={() => setSelected(item.indicator_id)} className={`result-row ${selectedIndicator?.indicator_id === item.indicator_id ? "active" : ""}`}><span className="result-name"><i className={`status-mark status-${item.score_0_to_100 === null ? "partial" : "scored"}`} /><strong>{item.name_zh}</strong></span><span className="status-pill">{item.measured_instance_count ?? 0} 次</span><span className="result-score">{scoreText(item.score_0_to_100)}</span></button>)}</div></>}
        <div className="table-head"><span>技术定义</span><span>状态</span><span>就绪度</span></div><div className="result-rows">{techniques.map(item => <button key={item.technique_id} onClick={() => setSelected(item.technique_id)} className={`result-row ${selected === item.technique_id ? "active" : ""}`}><span className="result-name"><strong>{item.name_zh}</strong></span><span className="status-pill">{item.observed ? "已观测" : "未观测"}</span><span className="result-score">{item.observed ? `${scoreText(item.evidence_score_0_to_100)}%` : "—"}</span></button>)}{!techniques.length && <div className="empty-state"><strong>{pending ? "等待动作识别结果" : "没有匹配的动作"}</strong><span>可以切换类别或清除搜索词。</span></div>}</div>
      </div><aside className="inspector" aria-label="当前视频评分详情">
        {family === "footwork" && selectedIndicator && !techniques.some(item => item.technique_id === selected) ? <><span className="inspector-kicker">可测动作 · 练习参考</span><h3>{selectedIndicator.name_zh}</h3><p className="definition">{selectedIndicator.definition_zh}</p><div className="inspector-section"><div className="section-label"><span>参考分</span><strong>{scoreText(selectedIndicator.score_0_to_100)} / 100</strong></div><p>{selectedIndicator.summary_zh}</p></div><div className="inspector-section"><div className="section-label">本次观察</div><p>{selectedIndicator.observation_zh || "暂无补充观察"}</p></div><div className="feedback-box"><span>下一步练习</span><p>{selectedIndicator.suggestion_zh || "先保持低强度，关注一次完整动作。"}</p></div>{selectedIndicator.limitations_zh?.map(item => <p className="muted-small" key={item}>{item}</p>)}</> : selectedTechnique ? <><span className="inspector-kicker">最新动作定义 · {selectedTechnique.observed ? "已观测" : "未观测"}</span><h3>{selectedTechnique.name_zh}</h3><div className="inspector-section"><div className="section-label">视觉识别依据</div>{(selectedTechnique.core_visual_features ?? []).map(item => <p className="definition" key={item}>{item}</p>)}</div><div className="phase-list">{(selectedTechnique.phase_statuses ?? []).map(item => <div key={item.phase}><span>{item.phase}</span><small>{item.status === "ready" ? "证据就绪" : "待确认"}</small></div>)}</div>{(selectedTechnique.limitations_zh ?? []).map(item => <p className="muted-small" key={item}>{item}</p>)}</> : <div className="empty-state">动作识别完成后，这里会展示判断依据与练习提示。</div>}
      </aside></div>
    </section>
  </div>;
}
