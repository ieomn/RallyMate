"use client";

import { useId } from "react";
import { TENNIS_PATTERN_NAMES, type TennisAssessment, type TennisImprovement } from "./lib/tennis-assessment";
import type { TrainingFocusSelection } from "./lib/training-guidance";
import { productText } from "./lib/presentation-text";
import { tennisEvidenceReason } from "./lib/tennis-evidence-status";
import { tennisImprovementFocus } from "./lib/tennis-practice";
import { footworkUnitText } from "./lib/footwork-review";
import type { ReplayClip } from "./lib/replay-clips";
import JointKinematicsDetails from "./JointKinematicsDetails";
export { tennisImprovementFocus } from "./lib/tennis-practice";
import "./tennis-assessment.css";

const scoreText = (value: number) => value.toFixed(1);
const metricUnit = (unit: string) => unit === "hip_width" ? "倍髋宽" : footworkUnitText(unit);
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

type PracticeActions = { onSaveFocus?: (focus: TrainingFocusSelection) => void; replayAvailable?: boolean };

function Practice({ item, report, priority, replayAvailable, onSaveFocus }: PracticeActions & {
  item: TennisImprovement; report: TennisAssessment; priority: boolean;
}) {
  const detail = observationParts(item.observation);
  return <article className="tennis-drill">
    {priority && <span className="review-label">本次训练重点</span>}
    <h3>{productText(item.title)}</h3>
    {detail.observation && <p className="tennis-observation">{detail.observation}</p>}
    <p className="review-next"><span className="review-label">下次先练</span>{productText(item.action)}</p>
    <details className="review-drill-steps"><summary>练习步骤与复测</summary>
      <dl><div><dt>怎么练</dt><dd>{productText(item.drill)}</dd></div><div><dt>复测目标</dt><dd>{productText(item.retest)}</dd></div></dl>
      {detail.measurement && <p className="tennis-observation-measurement"><span>测量依据</span>{detail.measurement}</p>}
    </details>
    <div className="tennis-drill-actions">
      {item.start_ms !== null && item.end_ms !== null && (replayAvailable
        ? <button type="button" onClick={() => window.dispatchEvent(new CustomEvent("rallymate:seek-video", { detail: { jobId: report.source_run_id, timestampMs: item.start_ms, preferOriginal: true } }))}>回看片段 <time>{seconds(item.start_ms)}–{seconds(item.end_ms)} 秒</time></button>
        : <time>对应片段 {seconds(item.start_ms)}–{seconds(item.end_ms)} 秒</time>)}
      {onSaveFocus && <button type="button" onClick={() => onSaveFocus(tennisImprovementFocus(item))}>保存练习</button>}
    </div>
  </article>;
}

export function ClipMeasurements({ clip, report }: { clip: ReplayClip; report: TennisAssessment | null }) {
  const episode = report?.episodes?.find(item => `tennis:${item.episode_id}` === clip.id);
  const label = episode?.motion_pattern && TENNIS_PATTERN_NAMES[episode.motion_pattern.profile_id] || clip.label;
  const metrics = report?.dimensions.flatMap(dimension => dimension.metrics).filter(item => episode?.metrics?.[item.id]).slice(0, 3) ?? [];
  const peak = clip.anchors.find(anchor => anchor.id === "peak");
  return <section className="review-clip-readout" aria-label="所选片段测量" aria-live="polite">
    <span className="review-label">所选片段</span><h3>{label}</h3>
    <dl><div><dt>动作区间</dt><dd>{seconds(clip.startMs)}–{seconds(clip.endMs)} 秒</dd></div>
      {peak && <div><dt>运动峰值</dt><dd>{seconds(peak.sourceMs)} 秒</dd></div>}
      {metrics.map(item => <div key={item.id}><dt>{productText(item.label)}</dt><dd>{Number(episode!.metrics![item.id].value.toFixed(2))} {metricUnit(item.unit)}</dd></div>)}
    </dl>
  </section>;
}

export default function TennisAssessmentSummary({ report, loading, error, message, offline = false, onRetry, onSaveFocus, replayAvailable = true }: PracticeActions & {
  report: TennisAssessment | null; loading?: boolean; error?: boolean; message?: string; offline?: boolean; onRetry?: () => void;
}) {
  if (!report) return <section className="tennis-assessment-wait" id="assessment" role="status">
    <h2>动作评分</h2><p>{loading && !error && !message && !offline ? "正在生成动作评分" : "暂无可展示的结果"}</p>
    {message ? <p>{productText(message)}</p> : error ? <p>动作评分读取失败，可重试。</p>
      : offline ? <p>这份导入报告未包含可核验的动作评分。</p> : null}
    {!loading && onRetry && !offline && <button className="product-secondary" type="button" onClick={onRetry}>重新读取</button>}
  </section>;
  if (report.status === "unavailable") return <section className="tennis-assessment-wait" id="assessment">
    <h2>动作评分</h2><p>暂无可展示的结果</p><p>{productText(report.reason_zh) || "当前没有连续可测的动作。"} 未测项目不按零分或动作错误处理。</p>
    <p>{productText(report.improvements[0]?.action) || "下次拍摄时，请把全身、双脚和完整挥拍留在画面内。"}</p>
    {onRetry && !offline && <button className="product-secondary" type="button" onClick={onRetry}>重新读取</button>}
  </section>;
  const [priority, ...otherDrills] = report.improvements;
  const measured = report.dimensions.filter(item => item.score !== null).length;
  return <div className="tennis-training-result">
    <section className="tennis-score-card" id="assessment" aria-labelledby="tennis-score-title">
      <div className="review-score-heading"><h2 id="tennis-score-title">动作评分</h2><span className="review-label">全片 · 已测 {measured} / 5 项</span></div>
      <div className="tennis-score-number"><strong>{report.overall_score === null ? "—" : scoreText(report.overall_score)}</strong><span>/ 100</span></div>
      <small className="review-score-note">可见动作的试验评分 · 未经教练标定</small>
      <details className="tennis-scope-details"><summary>查看五维评分与范围</summary>
        <TennisRadar report={report} />
        {report.episodes?.length ? <p>本次分析了 {report.episodes.length} 段挥拍。</p> : null}
        <p>适合同类动作与相近机位的对照，不能代替原始技术标准的 A–E 等级。</p>
        <SubjectAndPatternScope report={report} />
      </details>
    </section>
    {priority && <section id="recommendations" aria-label="本次训练重点"><Practice item={priority} report={report} priority onSaveFocus={onSaveFocus} replayAvailable={replayAvailable} /></section>}
    {(otherDrills.length > 0 || report.strengths.length > 0) && <details className="review-more"><summary>其他练习与优势</summary>
      {otherDrills.map(item => <Practice key={item.dimension_id} item={item} report={report} priority={false} onSaveFocus={onSaveFocus} replayAvailable={replayAvailable} />)}
      {!!report.strengths.length && <section className="review-strengths"><h3>继续保持</h3><ul>{report.strengths.map((item, index) => <li key={index}><strong>{productText(item.title)}</strong><p>{productText(item.observation)}</p></li>)}</ul></section>}
    </details>}
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
      <ul>{dimension.metrics.map(metric => <li key={metric.id}><strong>{productText(metric.label)}</strong><span>{metric.score === null ? "—" : `${scoreText(metric.score)} 分 · 权重 ${(metric.weight * 100).toFixed(1)}%`}</span><span>{metric.value === null ? "—" : `${Number(metric.value.toFixed(2))} ${metricUnit(metric.unit)}`}</span><span>{productText(metric.target)}</span></li>)}</ul></div>)}
  </section>;
}
