"use client";

import { useState } from "react";
import type { DemoResultResponse, MotionFamily, TechniqueAssessmentItem, TechniqueAssessmentResponse, TechniqueCatalogResponse, TrajectoryPreviewResponse } from "./lib/api-types";
import { analysisPresentation, summarizeActionCandidates, summarizeLiveStats } from "./lib/live-stats";
import { motionAnalysisOf } from "./lib/motion-analysis";
import MotionAnalysisPanel from "./MotionAnalysisPanel";
import FootworkReviewPanel from "./FootworkReviewPanel";
import MeasurementEvidence from "./MeasurementEvidence";
import { evidenceReferenceScore, hasMeasurement, measurementCounts, measurementWarnings, normalizeMeasurementResult } from "./lib/measurement-evidence";

const FAMILIES = { baseline: "底线", serve: "发球", return: "接发", net_attack: "网前", footwork: "步伐" };
const scoreText = (value: number | null) => value === null ? "—" : value.toFixed(1);
const statusLabel = (status: string, observed: boolean) => {
  if (status === "ready") return "证据就绪";
  if (status === "partial") return "部分就绪";
  if (status === "measured") return "已观测";
  if (status === "proxy") return "代理证据";
  if (status === "unavailable") return "证据不足";
  if (status === "not_observed" || !observed) return "未观测";
  return "待确认";
};

const FIELD_LABELS: Record<string, string> = {
  pose: "人体姿态", tracking: "连续跟踪", ball: "球轨迹", racket: "球拍观测", court: "场地标定",
  event_boundary: "事件边界", contact: "击球接触", phase: "动作阶段", left_right: "左右侧别",
};
const fieldLabel = (field: string) => FIELD_LABELS[field] ?? field.replaceAll("_", " ");
const eventFamily = (eventCode: string) => {
  if (eventCode.startsWith("FS")) return "footwork";
  if (eventCode === "GS06" || eventCode.startsWith("SV")) return "serve";
  if (/^GS(07|08|09|10|11)$/.test(eventCode) || eventCode.startsWith("RT")) return "return";
  if (/^GS0[1-5]$/.test(eventCode)) return "baseline";
  return "net_attack";
};

function familyRecognition(family: string, hasCandidates: boolean, summary?: TechniqueAssessmentResponse["family_summary"][string]) {
  const state = summary?.recognition_status ?? (hasCandidates ? family === "baseline" ? "motion_detected_unclassified" : "candidate_only" : "not_observed");
  const observedCount = summary?.observed_count ?? 0;
  const label = state === "motion_analyzed" ? "运动已分析" : state === "motion_unavailable" ? "证据不足" : observedCount > 0 ? `${observedCount} 项已观测` : state === "motion_detected_unclassified" ? "挥拍待分类" : state === "candidate_only" ? "动作待确认" : state === "classifier_unavailable" || state === "analysis_unavailable" ? "分类暂不可用" : "暂无可靠结果";
  const reason = summary?.recognition_reason_zh || (hasCandidates ? family === "baseline" ? "已检测到挥拍运动；正手、反手及切削尚未分类，触球次数暂不可用。" : "检测到发球式挥拍；真实发球与球拍触球尚未确认。" : "");
  return { state, label, reason };
}

function techniqueStatusLabel(item: TechniqueAssessmentItem, familyState: string, detail = false) {
  if (item.observed && item.family === "footwork") return "候选待复核";
  if (item.observed) return statusLabel(item.status, true);
  if (item.recognition_status === "candidate" || item.family === "serve" && familyState === "candidate_only") return detail ? "动作待确认 · 触球未确认" : "动作待确认";
  if (familyState === "motion_detected_unclassified") return "未分类";
  if (familyState === "classifier_unavailable" || familyState === "analysis_unavailable") return "分类暂不可用";
  return statusLabel(item.status, item.observed);
}

export function TechniqueFamilyChip({ family, label, total, summary, motion }: { family: string; label: string; total: number; summary?: TechniqueAssessmentResponse["family_summary"][string]; motion?: MotionFamily }) {
  const recognition = familyRecognition(family, (summary?.candidate_count ?? 0) > 0, summary);
  const awaitingClassification = summary?.observed_count === 0 && ["motion_detected_unclassified", "candidate_only", "classifier_unavailable", "analysis_unavailable"].includes(recognition.state);
  if (motion) return <div className="technique-chip"><span>{label}</span><strong>{motion.episodes.length ? "运动已分析" : "证据不足"}</strong><small>{motion.episodes.length ? "可查看阶段与指标" : "查看具体原因"}</small></div>;
  return <div className="technique-chip"><span>{label}</span><strong>{awaitingClassification ? recognition.label : summary ? `${summary.observed_count} / ${summary.total_count}` : `${total} 项`}</strong><small>{awaitingClassification ? recognition.state === "candidate_only" || recognition.state === "motion_detected_unclassified" ? "触球未确认" : "专项分类结果未提供" : summary ? "查看逐项证据" : "指标契约"}</small></div>;
}

function LiveStats({ result, trajectory, summary }: { result: DemoResultResponse | null; trajectory: TrajectoryPreviewResponse | null; summary?: Record<string, unknown> | null }) {
  const stats = summarizeLiveStats(result, trajectory, summary);
  const metric = (value: number | null) => value === null ? "—" : value.toLocaleString("zh-CN");
  return <section className="live-stats" aria-labelledby="live-stats-title">
    <div className="live-stats-head"><div><span className="card-kicker">VIDEO ANALYSIS</span><h2 id="live-stats-title">本次视频分析</h2></div><span className="stats-disclosure">动作片段与击球次数分别记录</span></div>
    <div className="live-stats-grid">
      <article><span>步伐候选片段</span><strong>{metric(stats.actionSegments)}</strong><small>{stats.actionKinds === null ? "本次尚无动作结果" : `${stats.actionKinds} 类候选 · 非实际步数或击球次数`}</small></article>
      <article><span>已分析视频帧</span><strong>{metric(stats.processedFrames)}</strong><small>{trajectory?.source.is_partial ? "随分析进度更新" : "当前任务的处理记录"}</small></article>
      {stats.contactCount !== null && <article><span>已确认触球</span><strong>{metric(stats.contactCount)}</strong><small>来自已确认的球拍触球事件</small></article>}
    </div>
    {stats.contactCount === null && <p className="stats-disclosure">本次未提供已确认的触球事件，已隐藏击球次数。</p>}
    <details className="live-stats-details"><summary>查看分析观测详情</summary><div className="live-stats-grid">
      <article><span>球检测观测</span><strong>{metric(trajectory?.ball.reconstruction?.summary.observed_count ?? trajectory?.ball.observed_count ?? null)}</strong><small>检测点不会计为击球</small></article>
      <article><span>球检测帧</span><strong>{metric(stats.ballDetectionFrames)}</strong><small>同一球可在多帧重复出现</small></article>
      <article><span>球拍检测观测</span><strong>{metric(trajectory?.racket.observed_count ?? stats.racketDetectionFrames)}</strong><small>检测数不等于挥拍或触球次数</small></article>
    </div></details>
  </section>;
}

export default function LiveResults({ result, assessment, pending, trajectory, summary, status, error }: { result: DemoResultResponse | null; assessment: TechniqueAssessmentResponse | null; catalog: TechniqueCatalogResponse | null; pending: boolean; trajectory: TrajectoryPreviewResponse | null; summary?: Record<string, unknown> | null; status?: string; error?: string | null }) {
  result = result ? normalizeMeasurementResult(result) : null;
  const [chosenFamily, setFamily] = useState("");
  const [selected, setSelected] = useState("");
  const [query, setQuery] = useState("");
  const candidateSummary = summarizeActionCandidates(result, assessment);
  const motionAnalysis = motionAnalysisOf(result, assessment, summary);
  const firstMotionFamily = Object.entries(motionAnalysis?.families ?? {}).find(([, data]) => data.episodes.length)?.[0];
  const family = chosenFamily || firstMotionFamily || candidateSummary.candidates[0]?.family || "footwork";
  const motionFamily = motionAnalysis?.families[family as "baseline" | "serve" | "return"];
  const isMotionFamily = ["baseline", "serve", "return"].includes(family);
  const familyCandidates = candidateSummary.candidates.filter(item => item.family === family);
  const recognition = familyRecognition(family, familyCandidates.length > 0, assessment?.family_summary?.[family]);
  const training = result?.training_evaluation;
  const measured = training?.indicator_evaluations ?? result?.actions?.flatMap(action => action.indicator_evaluations ?? []) ?? [];
  const indicators = [...new Map(measured.map(item => [item.indicator_id, item])).values()];
  const familyIndicators = indicators.filter(item => {
    const eventCode = item.event_code ?? item.indicator_id.split("-")[0] ?? "";
    return eventFamily(eventCode) === family;
  });
  const selectedIndicator = familyIndicators.find(item => item.indicator_id === selected) ?? familyIndicators[0];
  const techniques = assessment?.techniques.filter(item => item.family === family && item.name_zh.includes(query)) ?? [];
  const selectedTechnique = techniques.find(item => item.technique_id === selected) ?? techniques[0];
  const readyCount = assessment?.techniques.filter(item => item.observed).length ?? 0;
  const measurement = measurementCounts(result);
  const referenceScore = evidenceReferenceScore(training);
  const presentation = analysisPresentation({ result, pending, status, error });
  const searching = query.trim().length > 0;
  const emptyTitle = recognition.state === "motion_detected_unclassified" ? "已检测到挥拍运动，动作类型尚未分类" : recognition.state === "candidate_only" ? "已检测到发球式挥拍，尚待确认" : presentation.emptyTitle;
  const emptyDescription = recognition.reason || presentation.emptyDescription;

  return <div className="live-results" aria-live="polite" data-status={presentation.state}>
    {result && measurementWarnings(result).map(warning => <p className="measurement-warning" role="note" key={warning}>{warning}</p>)}
    <LiveStats result={result} trajectory={trajectory} summary={summary} />
    <section className="score-overview" id="scoreboard">
      <div className="score-card score-na">
        <div className="measurement-overview"><span>{presentation.state === "pending" ? "分析中" : referenceScore === null ? "实测指标" : "测量证据参考分（Beta）"}</span><strong>{referenceScore === null ? result ? `${measurement.measured} / ${measurement.total}` : "—" : scoreText(referenceScore)}</strong><small>{referenceScore === null ? "项有可复核测量" : "/ 100 · 非技术评分"}</small><p>技术评分待教练标定</p></div>
        <div className="score-summary"><span className="card-kicker">YOUR ANALYSIS</span><h2>{presentation.title}</h2><p>{presentation.description}</p><div className="fact-row"><div><small>已关联动作证据</small><strong>{assessment ? `${readyCount} / ${assessment.techniques.length}` : "—"}</strong></div><div><small>可查看指标</small><strong>{result ? `${indicators.length} 项` : "—"}</strong></div><div><small>技术评分</small><strong>待标定</strong></div></div><p className="score-disclosure">参考分反映测量证据的完整程度，高分不代表动作正确或技术水平更高。候选步伐需回放复核；技术评分待教练标定。</p></div>
      </div>
      {!!result?.actions?.length && <div className="module-cards live-action-cards">{result.actions.map(action => <button key={action.event_code} className="module-card" onClick={() => { setFamily(action.family || eventFamily(action.event_code)); setSelected(action.indicator_evaluations?.[0]?.indicator_id ?? ""); document.getElementById("rules")?.scrollIntoView({ behavior: "smooth" }); }}><div className="module-head"><span>{action.name_zh}</span><i>{action.detected_segments} 个候选片段</i></div><strong>{evidenceReferenceScore(action.performance_assessment) === null ? `${action.indicator_evaluations?.filter(hasMeasurement).length ?? 0} / ${action.indicator_evaluations?.length ?? 0} 项可测` : `${scoreText(evidenceReferenceScore(action.performance_assessment))} / 100`}</strong><p>{evidenceReferenceScore(action.performance_assessment) === null ? "候选动作待复核 · 技术评分待标定" : "测量证据参考分 · 非技术评分"}</p></button>)}</div>}
    </section>
    <section className="workbench" id="rules"><div className="workbench-head"><div><span className="section-number">02</span><div><span className="card-kicker">MOTION DETAILS</span><h2>动作分析与证据回放</h2></div></div></div>
      <div className="event-rail" aria-label="选择动作类别">{Object.entries(FAMILIES).map(([key, name]) => { const info = familyRecognition(key, candidateSummary.candidates.some(item => item.family === key), assessment?.family_summary?.[key]); const motion = motionAnalysis?.families[key as "baseline" | "serve" | "return"]; return <button key={key} aria-pressed={key === family} className={key === family ? "active" : ""} onClick={() => { setFamily(key); setSelected(""); }}><strong>{name}</strong><small>{motion ? motion.episodes.length ? "运动已分析" : "证据不足" : info.label}</small></button>; })}</div>
      {isMotionFamily && motionAnalysis ? <MotionAnalysisPanel key={`${family}-${result?.job_id ?? ""}`} familyLabel={FAMILIES[family as keyof typeof FAMILIES]} data={motionFamily} jobId={result?.job_id} pending={pending} /> : <>
      <p className="score-disclosure">{recognition.reason || `当前类别已观测 ${assessment?.family_summary?.[family]?.observed_count ?? 0} / ${assessment?.family_summary?.[family]?.total_count ?? techniques.length} 类动作。`}{familyIndicators.length > 0 ? `当前可查看 ${familyIndicators.length} 项可测指标。` : "当前暂无可测指标。"}</p>
      {family === "footwork" && <FootworkReviewPanel key={result?.job_id ?? ""} review={result?.footwork_review} jobId={result?.job_id} />}
      <div className="rules-layout"><div className="rules-list"><div className="rules-list-head"><h3>{FAMILIES[family as keyof typeof FAMILIES]} · 当前视频</h3><label className="search-box"><span>⌕</span><input aria-label="搜索当前视频的指标" placeholder="搜索指标" value={query} onChange={event => setQuery(event.target.value)} /></label></div>
        {familyIndicators.length > 0 && <><div className="table-head"><span>动作指标</span><span>测量</span><span>证据参考分</span></div><div className="result-rows">{familyIndicators.filter(item => item.name_zh.includes(query)).map(item => <button key={item.indicator_id} onClick={() => setSelected(item.indicator_id)} className={`result-row ${selectedIndicator?.indicator_id === item.indicator_id ? "active" : ""}`}><span className="result-name"><i className={`status-mark status-${hasMeasurement(item) ? "measured" : "partial"}`} /><strong>{item.name_zh}</strong></span><span className="status-pill">{item.measured_instance_count ?? 0} 次</span><span className="result-score">{evidenceReferenceScore(item) === null ? hasMeasurement(item) ? "可复核" : "证据不足" : scoreText(evidenceReferenceScore(item))}</span></button>)}</div></>}
        <div className="table-head"><span>技术定义</span><span>状态</span><span>技术评分</span></div><div className="result-rows">{techniques.map(item => <button key={item.technique_id} onClick={() => setSelected(item.technique_id)} className={`result-row ${selected === item.technique_id ? "active" : ""}`}><span className="result-name"><strong>{item.name_zh}</strong></span><span className={`status-pill status-${item.status}`}>{techniqueStatusLabel(item, recognition.state)}</span><span className="result-score">待标定</span></button>)}{!techniques.length && <div className="empty-state"><strong>{searching ? "没有匹配的动作" : emptyTitle}</strong><span>{searching ? "可以切换类别或清除搜索词。" : emptyDescription}</span></div>}</div>
      </div><aside className="inspector" aria-label="当前视频评分详情">
        {familyIndicators.length > 0 && selectedIndicator && !techniques.some(item => item.technique_id === selected) ? <><span className="inspector-kicker">可测动作 · 证据复核</span><h3>{selectedIndicator.name_zh}</h3><p className="definition">{selectedIndicator.definition_zh}</p><div className="inspector-section"><div className="section-label"><span>测量证据参考分</span><strong>{scoreText(evidenceReferenceScore(selectedIndicator))} / 100</strong></div><p>{selectedIndicator.summary_zh}</p></div><div className="inspector-section"><div className="section-label">本次观察</div><p>{selectedIndicator.observation_zh || "暂无补充观察"}</p></div><div className="feedback-box"><span>复核建议</span><p>{selectedIndicator.suggestion_zh || "先保持低强度，关注一次完整动作。"}</p></div><MeasurementEvidence indicator={selectedIndicator} />{selectedIndicator.limitations_zh?.map(item => <p className="muted-small" key={item}>{item}</p>)}</> : selectedTechnique ? <><span className="inspector-kicker">最新动作定义 · {techniqueStatusLabel(selectedTechnique, recognition.state, true)}</span><h3>{selectedTechnique.name_zh}</h3><div className="inspector-section"><div className="section-label">视觉识别依据</div>{(selectedTechnique.core_visual_features ?? []).map(item => <p className="definition" key={item}>{item}</p>)}</div>{selectedTechnique.key_field_analysis && <div className="inspector-section key-field-analysis"><div className="section-label">关键字段证据底线</div>{[...selectedTechnique.key_field_analysis.required_fields, ...selectedTechnique.key_field_analysis.enhanced_fields].map(item => <div className="field-row" key={`${item.required ? "required" : "enhanced"}-${item.field}`}><span>{fieldLabel(item.field)}{item.required ? "" : "（增强）"}</span><small className={`field-status field-${item.status}`}>{item.status === "ready" || item.status === "available" ? "已观测" : item.status === "partial" ? "部分证据" : item.coverage_percent > 0 ? "阶段证据未关联" : item.status === "optional_missing" ? "未提供" : "缺失"} · 全片覆盖 {item.coverage_percent}%</small></div>)}{selectedTechnique.key_field_analysis.missing_required_fields.length > 0 && <p className="muted-small">缺少必需字段：{selectedTechnique.key_field_analysis.missing_required_fields.map(fieldLabel).join("、")}</p>}</div>}<div className="phase-list">{(selectedTechnique.phase_statuses ?? []).map(item => <div key={item.phase}><span>{item.phase}</span><small>{statusLabel(item.status, item.status === "ready")}</small></div>)}</div>{(selectedTechnique.limitations_zh ?? []).map(item => <p className="muted-small" key={item}>{item}</p>)}</> : <div className="empty-state"><strong>{emptyTitle}</strong><span>{emptyDescription}</span></div>}
      </aside></div></>}
    </section>
  </div>;
}
