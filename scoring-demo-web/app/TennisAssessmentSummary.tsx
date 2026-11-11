"use client";

import { useId } from "react";
import { TENNIS_PATTERN_NAMES, type TennisAssessment } from "./lib/tennis-assessment";
import type { TrainingFocusSelection } from "./lib/training-guidance";
import { productText } from "./lib/presentation-text";
import { tennisEvidenceReason } from "./lib/tennis-evidence-status";
import { tennisImprovementFocus } from "./lib/tennis-practice";
import JointKinematicsDetails from "./JointKinematicsDetails";
export { tennisImprovementFocus } from "./lib/tennis-practice";
import "./tennis-assessment.css";

const scoreText = (value: number) => value.toFixed(1);
const seconds = (value: number) => (value / 1000).toFixed(1);
const observationParts = (value: string) => {
  const text = productText(value);
  const marker = text.search(/(?:姿态估计|测量汇总)：/);
  return marker < 0 ? { observation: text, measurement: "" }
    : { observation: text.slice(0, marker).trim(), measurement: text.slice(marker).trim() };
};
function SubjectAndPatternScope({ report }: { report: TennisAssessment }) {
  const scope = report.subject_scope, coverage = report.pattern_coverage;
  if (!scope || !coverage) return null;
  const selected = scope.subjects.find(subject => subject.subject_id === scope.selected_subject_id);
  if (!selected) return null;
  return <section className="tennis-evidence-scope" aria-label="评分对象与动作覆盖">
    <p><strong>评分对象</strong> {selected.identity_basis === "target_lock" ? "本次锁定目标" : "首个独立轨迹"}，汇总 {selected.measured_episode_count} 段动作。</p>
    <p><strong>动作要求覆盖</strong> {coverage.measured_requirements} / {coverage.applicable_requirements} 项次有可测依据，
      {coverage.verified_technique_episodes} 段已确认具体技术，{coverage.generic_episodes} 段使用通用指标。</p>
    <p>以上覆盖率仅统计本次适用的动作测量要求；五个环节都有分，也不表示原始技术标准的全部评分点已经完成。</p>
  </section>;
}
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
    <p>{loading && !error && !message && !offline ? "正在生成动作评分" : "暂无可展示的结果"}</p>
    {message ? <p>{productText(message)}</p> : error ? <p>动作评分暂未读取成功，可重新读取，无需重新上传视频。</p>
      : offline ? <p>这份导入报告未包含可核验的动作评分。可从原任务重新读取并导出报告。</p> : null}
    {!loading && onRetry && <button className="product-secondary" type="button" onClick={onRetry}>重新读取</button>}
  </section>;
  if (report.status === "unavailable") return <section className="tennis-assessment-wait" id="assessment"><div>
    <p>暂无可展示的结果</p>
    <p>{productText(report.reason_zh) || "本次缺少可用于评分的连续动作证据。"} 未测项目不按零分或动作错误处理。</p>
    <p>{productText(report.improvements[0]?.action) || "请在回放中确认准备、挥拍和随挥完整；重新拍摄时，让全身与双脚持续入镜。"}</p>
    {onRetry && <button className="product-secondary" type="button" onClick={onRetry}>重新读取</button>}
  </div></section>;
  const priority = report.improvements[0];
  return <div className="tennis-training-result">
    {priority && <section className="tennis-priority" aria-labelledby="tennis-priority-title">
      <span className="tennis-eyebrow">本次优先练什么</span>
      <h2 id="tennis-priority-title">{productText(priority.title)}</h2>
      <p>{observationParts(priority.observation).observation}</p>
      <div className="tennis-priority-next"><span>先做这一步</span><strong>{productText(priority.action)}</strong></div>
      <a href="#recommendations">查看对应片段与练习方法 <span aria-hidden="true">↓</span></a>
      <small>以下重点来自本次可见动作。先回看对应片段，再选一项练习。</small>
    </section>}
    <section className="tennis-score-card" id="assessment" aria-labelledby="tennis-score-title">
      <div className="tennis-score-copy"><span className="tennis-eyebrow">本次表现</span><h2 id="tennis-score-title">动作评分</h2>
        <div className="tennis-score-number"><strong>{report.overall_score === null ? "—" : scoreText(report.overall_score)}</strong><span>/ 100</span></div>
        <p>{report.episodes?.length ? `本次分析了 ${report.episodes.length} 段挥拍，结合五个环节定位练习重点。` : "结合五个动作环节，找到下次训练的重点。"}</p>
        <small>可见动作的试验评分，尚未经教练标定，不能代替原始技术标准的 A–E 等级；请用同类动作与相近机位比较。</small>
        {report.status === "partial" && <small>本次已测 {report.dimensions.filter(item => item.score !== null).length} 个动作环节</small>}
      </div><TennisRadar report={report} />
    </section>
    {report.subject_scope && report.pattern_coverage && <details className="tennis-scope-details"><summary>本次评估了谁、哪些动作</summary><SubjectAndPatternScope report={report} /></details>}
    {!!report.strengths.length && <section className="tennis-strengths" aria-labelledby="tennis-strengths-title">
      <h2 id="tennis-strengths-title">继续保持</h2>
      <ul>{report.strengths.slice(0, 2).map((item, index) => <li key={`${item.title}-${index}`}><strong>{productText(item.title)}</strong><p>{productText(item.observation)}</p></li>)}</ul>
    </section>}
    {!!report.improvements.length && <section className="tennis-practice" id="recommendations" aria-labelledby="tennis-practice-title">
      <div className="tennis-section-title"><h2 id="tennis-practice-title">具体表现与练习方法</h2><span>选一项开始 · 先慢后快</span></div>
      <div className="tennis-practice-grid">{report.improvements.map((item, index) => { const detail = observationParts(item.observation); return <article className="tennis-drill" key={item.dimension_id}>
        <div className="tennis-drill-heading"><span aria-hidden="true">0{index + 1}</span><h3>{productText(item.title)}</h3></div>
        {item.start_ms !== null && item.end_ms !== null && <span className="tennis-drill-source">对应片段 {seconds(item.start_ms)}–{seconds(item.end_ms)} 秒</span>}
        <div className="tennis-observation-block"><h4>这段动作的表现</h4><p className="tennis-observation">{detail.observation}</p>
          {detail.measurement && <p className="tennis-observation-measurement"><span>测量依据</span>{detail.measurement}</p>}</div>
        <dl><div className="tennis-action-row"><dt>调整方法</dt><dd>{productText(item.action)}</dd></div><div><dt>怎么练</dt><dd>{productText(item.drill)}</dd></div><div><dt>复测目标</dt><dd>{productText(item.retest)}</dd></div></dl>
        <div className="tennis-drill-actions">{item.start_ms !== null && replayAvailable && <button type="button" onClick={() => window.dispatchEvent(new CustomEvent("rallymate:seek-video", { detail: { jobId: report.source_run_id, timestampMs: item.start_ms } }))}>回看这段动作 ↗</button>}
          {onSaveFocus && <button type="button" onClick={() => onSaveFocus(tennisImprovementFocus(item))}>保存练习</button>}</div>
      </article>; })}</div>
    </section>}
  </div>;
}

export function TennisAssessmentDetails({ report }: { report: TennisAssessment | null }) {
  if (!report) return null;
  return <section className="tennis-measurements" aria-label="动作评分测量依据"><h2>评分依据</h2>
    <p>动作评分依据本次人体姿态估计，采用可见动作的试验评分。</p>
    <p>百分制阈值与权重来自项目试验规则，并非原始技术文档规定的分数换算；未测项目不计入分数，也不代表动作不合格。</p>
    <p>各完整动作先汇总该维度可测项目，再对动作等权平均；总分由已测维度等权平均。下列权重是该项目在维度中的实际贡献。</p>
    <p>屈膝准备、准备站距、准备后半段转体和挥拍手臂空间按实际持续时间计分。平均测量值用于解释动作；发球的屈膝准备使用时间积分。</p>
    {report.multimodal_assessment && <details><summary>球、球拍与场地观测</summary>
      <p>共处理 {report.multimodal_assessment.frame_count} 帧：球有观测 {report.multimodal_assessment.summary.ball_observed_frames} 帧，
        球拍有观测 {report.multimodal_assessment.summary.racket_observed_frames} 帧，场地标定有效 {report.multimodal_assessment.summary.court_calibrated_frames} 帧。</p>
      <p>球的图像运动与人体动作分别计算。物理球速、转速、过网高度需要相应标定和可见数据；缺少这些数据时，已测动作评分仍然保留。</p>
    </details>}
    {report.pattern_coverage && <div className="tennis-pattern-evidence"><h3>各段动作与测量项目</h3>
      <p>髋肩腕的先后与人体中心移动采用经过核验的固定画面投影；随挥按连续运动记录。</p>
      {(report.episodes ?? []).map(episode => episode.motion_pattern && <details key={episode.episode_id}>
        <summary>{seconds(episode.start_ms)}–{seconds(episode.end_ms)} 秒 · {TENNIS_PATTERN_NAMES[episode.motion_pattern.profile_id]} · 已测 {episode.motion_pattern.coverage.measured_requirements}/{episode.motion_pattern.coverage.applicable_requirements} 项</summary>
        {episode.motion_pattern.classification_provenance.method === "agent_visual_review" || episode.motion_pattern.classification_provenance.method === "agent_review"
          ? <p>技术类别来自辅助画面复核。</p> : null}
        {episode.hand_roles && <p>阶段参考：{episode.hand_roles.motion_wrist === "left" ? "左腕" : "右腕"}；
          {episode.hand_roles.primary_racket_hand === null ? ""
            : `${["agent_visual_review", "human_visual_review"].includes(episode.hand_roles.primary_hand_provenance.method) ? "画面复核" : "球拍关联"}：${episode.hand_roles.primary_racket_hand === "left" ? "左手" : "右手"}持拍。`}
          {episode.hand_roles.relationship === "verified_two_hand" && `原始画面投影支持这两腕在同一加速阶段共同运动；手臂与轨迹项目使用${episode.hand_roles.measurement_hand === "left" ? "左手" : "右手"}测量。`}
        </p>}
        <ul>{episode.motion_pattern.requirements.map(requirement => <li key={requirement.id}>
          <strong>{productText(requirement.label)}</strong><span>{requirement.status === "measured_proxy" ? "已计入可见动作评分" : `未计分：${tennisEvidenceReason(requirement.reason)}`}</span>
        </li>)}</ul>
        <JointKinematicsDetails episode={episode} jobId={report.source_run_id} />
      </details>)}
    </div>}
    {report.dimensions.map(dimension => <div key={dimension.id}><h3>{dimension.label} · {dimension.score === null ? "—" : scoreText(dimension.score)}</h3>
      <ul>{dimension.metrics.map(metric => <li key={metric.id}><strong>{productText(metric.label)}</strong><span>{metric.score === null ? "—" : `${scoreText(metric.score)} 分 · 权重 ${(metric.weight * 100).toFixed(1)}%`}</span><span>{metric.value === null ? "—" : `${Number(metric.value.toFixed(2))} ${metric.unit}`}</span><span>{productText(metric.target)}</span></li>)}</ul></div>)}
  </section>;
}
