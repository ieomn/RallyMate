"use client";

import { useId } from "react";
import type { TennisAssessment } from "./lib/tennis-assessment";
import type { TrainingFocusSelection } from "./lib/training-guidance";
import { productText } from "./lib/presentation-text";
import { tennisImprovementFocus } from "./lib/tennis-practice";
export { tennisImprovementFocus } from "./lib/tennis-practice";
import "./tennis-assessment.css";

const scoreText = (value: number) => value.toFixed(1);
const seconds = (value: number) => (value / 1000).toFixed(1);
function TennisRadar({ report }: { report: TennisAssessment }) {
  const titleId = useId();
  const point = (index: number, value: number) => {
    const angle = -Math.PI / 2 + index * Math.PI * 2 / 5, radius = 83 * value / 100;
    return [155 + Math.cos(angle) * radius, 112 + Math.sin(angle) * radius];
  };
  const complete = report.dimensions.every(item => item.score !== null);
  return <svg className="tennis-radar" viewBox="0 0 310 236" role="img" aria-labelledby={titleId}>
    <title id={titleId}>五维动作评分图</title>
    {[25, 50, 75, 100].map(level => <polygon key={level} className="tennis-radar-ring" points={report.dimensions.map((_, index) => point(index, level).join(",")).join(" ")} />)}
    {report.dimensions.map((item, index) => { const [x, y] = point(index, 100); return <line key={item.id} className="tennis-radar-axis" x1={155} y1={112} x2={x} y2={y} />; })}
    {complete && <polygon className="tennis-radar-shape" points={report.dimensions.map((item, index) => point(index, item.score!).join(",")).join(" ")} />}
    {report.dimensions.map((item, index) => {
      const [x, y] = point(index, 127), marker = item.score === null ? null : point(index, item.score);
      return <g key={item.id}>{marker && <circle className="tennis-radar-point" cx={marker[0]} cy={marker[1]} r={3.5} />}
        <text className="tennis-radar-label" x={x} y={y} textAnchor="middle">{item.label}</text>
        <text className="tennis-radar-value" x={x} y={y + 17} textAnchor="middle">{item.score === null ? "—" : scoreText(item.score)}</text>
      </g>;
    })}
  </svg>;
}

export default function TennisAssessmentSummary({ report, loading, error, message, offline = false, onRetry, onSaveFocus, replayAvailable = true }: {
  report: TennisAssessment | null; loading?: boolean; error?: boolean; message?: string; offline?: boolean; onRetry?: () => void;
  onSaveFocus?: (focus: TrainingFocusSelection) => void; replayAvailable?: boolean;
}) {
  if (!report) return <section className="tennis-assessment-wait" id="assessment" role="status"><span className={loading ? "tennis-loading-dot" : ""} />
    <p>{message || (error ? "动作评分暂未读取成功。" : offline ? "这份备份还没有动作评分，请打开在线报告更新。" : "正在生成动作评分")}</p>
    {(error || message) && onRetry && <button className="product-secondary" type="button" onClick={onRetry}>重新读取</button>}
  </section>;
  if (report.status === "unavailable") return <section className="tennis-assessment-wait" id="assessment"><div>
    <p>{productText(report.reason_zh) || "本次没有形成可评分的完整动作。"}</p>
    <p>{productText(report.improvements[0]?.action) || "请在回放中确认准备、挥拍和随挥完整；重新拍摄时，让全身与双脚持续入镜。"}</p>
  </div></section>;
  return <div className="tennis-training-result">
    <section className="tennis-score-card" id="assessment" aria-labelledby="tennis-score-title">
      <div className="tennis-score-copy"><span className="tennis-eyebrow">本次表现</span><h2 id="tennis-score-title">动作评分</h2>
        <div className="tennis-score-number"><strong>{report.overall_score === null ? "—" : scoreText(report.overall_score)}</strong><span>/ 100</span></div>
        <p>{report.episodes?.length ? `本次分析了 ${report.episodes.length} 段挥拍，结合五个环节定位练习重点。` : "结合五个动作环节，找到下次训练的重点。"}</p>
        <small>可见动作的试验评分，不代表竞技等级；请用同类动作与相近机位比较。</small>
        {report.status === "partial" && <small>本次已测 {report.dimensions.filter(item => item.score !== null).length} 个动作环节</small>}
      </div><TennisRadar report={report} />
    </section>
    {!!report.strengths.length && <section className="tennis-strengths" aria-labelledby="tennis-strengths-title">
      <h2 id="tennis-strengths-title">继续保持</h2>
      <ul>{report.strengths.slice(0, 2).map((item, index) => <li key={`${item.title}-${index}`}><strong>{productText(item.title)}</strong><p>{productText(item.observation)}</p></li>)}</ul>
    </section>}
    {!!report.improvements.length && <section className="tennis-practice" id="recommendations" aria-labelledby="tennis-practice-title">
      <div className="tennis-section-title"><h2 id="tennis-practice-title">下次这样练</h2><span>选一项开始 · 先慢后快</span></div>
      <div className="tennis-practice-grid">{report.improvements.map((item, index) => <article className="tennis-drill" key={item.dimension_id}>
        <div className="tennis-drill-heading"><span aria-hidden="true">0{index + 1}</span><h3>{productText(item.title)}</h3></div>
        {item.start_ms !== null && item.end_ms !== null && <span className="tennis-drill-source">对应片段 {seconds(item.start_ms)}–{seconds(item.end_ms)} 秒</span>}
        <p className="tennis-observation">{productText(item.observation)}</p>
        <p className="tennis-action">{productText(item.action)}</p>
        <dl><div><dt>怎么练</dt><dd>{productText(item.drill)}</dd></div><div><dt>复测目标</dt><dd>{productText(item.retest)}</dd></div></dl>
        <div className="tennis-drill-actions">{item.start_ms !== null && replayAvailable && <button type="button" onClick={() => window.dispatchEvent(new CustomEvent("rallymate:seek-video", { detail: { jobId: report.source_run_id, timestampMs: item.start_ms } }))}>回看这段动作 ↗</button>}
          {onSaveFocus && <button type="button" onClick={() => onSaveFocus(tennisImprovementFocus(item))}>保存练习</button>}</div>
      </article>)}</div>
    </section>}
  </div>;
}

export function TennisAssessmentDetails({ report }: { report: TennisAssessment | null }) {
  if (!report) return null;
  return <section className="tennis-measurements" aria-label="动作评分测量依据"><h2>评分依据</h2>
    <p>动作评分结合本次人体姿态估计。击球姿态描述身体姿态，不代表触球质量、拍面、落点或实际竞技能力。评分规则尚未用教练标注校准。</p>
    <p>各完整动作先汇总该维度可测项目，再对动作等权平均；总分由已测维度等权平均。下列权重是该项目在维度中的实际贡献，缺测项目不填分。</p>
    <p>屈膝准备、准备站距、准备后半段转体和挥拍手臂空间按实际持续时间计分，短暂达到要求不等于整段完成。平均测量值用于解释动作，不能单独代入重算整段得分；发球只有屈膝准备使用这一时间积分。</p>
    {report.dimensions.map(dimension => <div key={dimension.id}><h3>{dimension.label} · {dimension.score === null ? "—" : scoreText(dimension.score)}</h3>
      <ul>{dimension.metrics.map(metric => <li key={metric.id}><strong>{productText(metric.label)}</strong><span>{metric.score === null ? "缺少测量" : `${scoreText(metric.score)} 分 · 权重 ${(metric.weight * 100).toFixed(1)}%`}</span><span>{metric.value === null ? "—" : `${Number(metric.value.toFixed(2))} ${metric.unit}`}</span><span>{productText(metric.target)}</span></li>)}</ul></div>)}
  </section>;
}
