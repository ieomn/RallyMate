"use client";

import { useState, type CSSProperties } from "react";
import type { DemoResultResponse, TechniqueAssessmentResponse, TechniqueCatalogResponse, TrajectoryPreviewResponse } from "./lib/api-types";
import { summarizeLiveStats } from "./lib/live-stats";

const FAMILIES = { baseline: "底线", serve: "发球", return: "接发", net_attack: "网前", footwork: "步伐" };
const scoreText = (value: number | null | undefined) => typeof value === "number" && Number.isFinite(value) ? value.toFixed(1) : "—";
const statusLabel = (status: string, observed: boolean) => {
  if (status === "ready") return "证据就绪";
  if (status === "partial") return "部分就绪";
  if (status === "measured") return "已观测";
  if (status === "proxy") return "代理证据";
  if (status === "unavailable") return "证据不足";
  if (status === "not_observed" || !observed) return "未观测";
  return "待确认";
};
const readinessText = (score: number | null | undefined, status: string, observed: boolean) => {
  if (!observed || status === "not_observed" || status === "unavailable") return "—";
  return typeof score === "number" && Number.isFinite(score) ? `${scoreText(score)}%` : "待确认";
};
const FIELD_LABELS: Record<string, string> = {
  pose: "人体姿态", tracking: "连续跟踪", ball: "球轨迹", racket: "球拍观测", court: "场地标定",
  event_boundary: "事件边界", contact: "击球接触", phase: "动作阶段", left_right: "左右侧别",
};
const fieldLabel = (field: string) => FIELD_LABELS[field] ?? field.replaceAll("_", " ");

function LiveStats({ result, trajectory, summary }: { result: DemoResultResponse | null; trajectory: TrajectoryPreviewResponse | null; summary?: Record<string, unknown> | null }) {
  const stats = summarizeLiveStats(result, trajectory, summary);
  const metric = (value: number | null) => value === null ? "—" : value.toLocaleString("zh-CN");
  return <section className="live-stats" aria-labelledby="live-stats-title">
    <div className="live-stats-head"><div><span className="card-kicker">OBSERVATION COUNTS</span><h2 id="live-stats-title">动作与观测统计</h2></div><span className="stats-disclosure">只显示服务端返回的计数</span></div>
    <div className="live-stats-grid">
      <article><span>动作片段</span><strong>{metric(stats.actionSegments)}</strong><small>{stats.actionKinds} 类已识别动作</small></article>
      <article><span>球观测点</span><strong>{metric(trajectory?.ball.observed_count ?? null)}</strong><small>轨迹采样点 · 非击球次数</small></article>
      <article><span>视频帧</span><strong>{metric(stats.processedFrames)}</strong><small>{stats.ballDetectionFrames === null ? "球检测帧：—" : `球检测帧 ${metric(stats.ballDetectionFrames)} · 检测 ${metric(stats.ballDetections)}`}</small></article>
      <article><span>球拍观测</span><strong>{metric(trajectory?.racket.observed_count ?? stats.racketDetectionFrames)}</strong><small>{stats.racketDetections === null ? "服务端未返回检测总数" : `检测总数 ${metric(stats.racketDetections)}`}</small></article>
      <article className="stats-wide"><span>触球次数</span><strong>{stats.contactCount !== null ? metric(stats.contactCount) : stats.shotCount !== null ? metric(stats.shotCount) : "未返回"}</strong><small>{stats.shotCountSource === "server" ? "服务端显式字段" : "当前任务没有触球字段，不作推断"}</small></article>
    </div>
  </section>;
}

export default function LiveResults({ result, assessment, catalog, pending, trajectory, summary }: { result: DemoResultResponse | null; assessment: TechniqueAssessmentResponse | null; catalog: TechniqueCatalogResponse | null; pending: boolean; trajectory: TrajectoryPreviewResponse | null; summary?: Record<string, unknown> | null }) {
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
    <LiveStats result={result} trajectory={trajectory} summary={summary} />
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
        <div className="table-head"><span>技术定义</span><span>状态</span><span>就绪度</span></div><div className="result-rows">{techniques.map(item => <button key={item.technique_id} onClick={() => setSelected(item.technique_id)} className={`result-row ${selected === item.technique_id ? "active" : ""}`}><span className="result-name"><strong>{item.name_zh}</strong></span><span className={`status-pill status-${item.status}`}>{statusLabel(item.status, item.observed)}</span><span className="result-score">{readinessText(item.evidence_score_0_to_100, item.status, item.observed)}</span></button>)}{!techniques.length && <div className="empty-state"><strong>{pending ? "等待动作识别结果" : "没有匹配的动作"}</strong><span>可以切换类别或清除搜索词。</span></div>}</div>
      </div><aside className="inspector" aria-label="当前视频评分详情">
        {family === "footwork" && selectedIndicator && !techniques.some(item => item.technique_id === selected) ? <><span className="inspector-kicker">可测动作 · 练习参考</span><h3>{selectedIndicator.name_zh}</h3><p className="definition">{selectedIndicator.definition_zh}</p><div className="inspector-section"><div className="section-label"><span>参考分</span><strong>{scoreText(selectedIndicator.score_0_to_100)} / 100</strong></div><p>{selectedIndicator.summary_zh}</p></div><div className="inspector-section"><div className="section-label">本次观察</div><p>{selectedIndicator.observation_zh || "暂无补充观察"}</p></div><div className="feedback-box"><span>下一步练习</span><p>{selectedIndicator.suggestion_zh || "先保持低强度，关注一次完整动作。"}</p></div>{selectedIndicator.limitations_zh?.map(item => <p className="muted-small" key={item}>{item}</p>)}</> : selectedTechnique ? <><span className="inspector-kicker">最新动作定义 · {statusLabel(selectedTechnique.status, selectedTechnique.observed)}</span><h3>{selectedTechnique.name_zh}</h3><div className="inspector-section"><div className="section-label">视觉识别依据</div>{(selectedTechnique.core_visual_features ?? []).map(item => <p className="definition" key={item}>{item}</p>)}</div>{selectedTechnique.key_field_analysis && <div className="inspector-section key-field-analysis"><div className="section-label">关键字段证据底线</div>{[...selectedTechnique.key_field_analysis.required_fields, ...selectedTechnique.key_field_analysis.enhanced_fields].map(item => <div className="field-row" key={`${item.required ? "required" : "enhanced"}-${item.field}`}><span>{fieldLabel(item.field)}{item.required ? "" : "（增强）"}</span><small className={`field-status field-${item.status}`}>{item.status === "ready" || item.status === "available" ? "已观测" : item.status === "partial" ? "部分证据" : item.status === "optional_missing" ? "未提供" : "缺失"} · {item.coverage_percent}%</small></div>)}{selectedTechnique.key_field_analysis.missing_required_fields.length > 0 && <p className="muted-small">缺少必需字段：{selectedTechnique.key_field_analysis.missing_required_fields.map(fieldLabel).join("、")}</p>}</div>}<div className="phase-list">{(selectedTechnique.phase_statuses ?? []).map(item => <div key={item.phase}><span>{item.phase}</span><small>{statusLabel(item.status, item.status === "ready")}</small></div>)}</div>{(selectedTechnique.limitations_zh ?? []).map(item => <p className="muted-small" key={item}>{item}</p>)}</> : <div className="empty-state">动作识别完成后，这里会展示判断依据与练习提示。</div>}
      </aside></div>
    </section>
  </div>;
}
