"use client";

import { useId, useState } from "react";
import { TENNIS_PATTERN_NAMES, formatTennisScore as scoreText, type TennisAssessment, type TennisDimension, type TennisImprovement } from "./lib/tennis-assessment";
import type { TrainingFocusSelection } from "./lib/training-guidance";
import { productText } from "./lib/presentation-text";
import { tennisEvidenceReason } from "./lib/tennis-evidence-status";
import { primaryTennisPractice, tennisImprovementFocus } from "./lib/tennis-practice";
import { footworkUnitText as metricUnit } from "./lib/footwork-review";
import type { ReplayClip } from "./lib/replay-clips";
import LevelAssessmentPanel from "./LevelAssessmentPanel";
import JointKinematicsDetails from "./JointKinematicsDetails";
export { tennisImprovementFocus } from "./lib/tennis-practice";
import "./tennis-assessment.css";

const seconds = (value: number) => (value / 1000).toFixed(2);
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
  </section>;
}

export function TennisScoreOverview({ report }: { report: TennisAssessment }) {
  const [selected, setSelected] = useState<TennisDimension["id"] | null>(null);
  const panelId = useId();
  if (report.status === "unavailable" || report.overall_score === null) return null;
  const measured = report.dimensions.filter(item => item.score !== null).length;
  const dimension = report.dimensions.find(item => item.id === selected);
  return <section className="review-score-overview" id="assessment" aria-label="动作评分">
    <div className="review-total">
      <div><h2>动作评分</h2><span>已测 {measured} / {report.dimensions.length} 项</span></div>
      <div className="tennis-score-number"><strong>{scoreText(report.overall_score)}</strong><span>/ 100</span></div>
    </div>
    <div className="review-dimension-list" role="group" aria-label="查看分项评分依据">
      {report.dimensions.map(item => <button type="button" key={item.id} aria-controls={panelId} aria-expanded={selected === item.id}
        onClick={() => setSelected(selected === item.id ? null : item.id)}>
        <span>{item.label}</span><strong>{item.score === null ? "未测" : scoreText(item.score)}</strong>
        <span className="review-score-track" aria-hidden="true">{item.score !== null && <i style={{ width: `${item.score}%` }} />}</span>
      </button>)}
    </div>
    <div id={panelId} hidden={!dimension} className="review-dimension-detail">
      {dimension && <DimensionMeasurements key={dimension.id} dimension={dimension} />}
    </div>
  </section>;
}

export function DimensionMeasurements({ dimension }: { dimension: TennisDimension }) {
  return <section aria-label={`${dimension.label}评分依据`}>
    <h3>{dimension.label}的评分依据</h3>
    {dimension.metrics.length > 0 ? <ul>{dimension.metrics.map(metric =>
      <li key={metric.id}>
        <div><strong>{productText(metric.label)}</strong><span>{metric.score === null ? "未计分" : `${scoreText(metric.score)} 分`}</span></div>
        {metric.value !== null && <p className="review-metric-data">实测 {Number(metric.value.toFixed(2))} {metricUnit(metric.unit)} · {metric.observation_count} 次观测{metric.score !== null && ` · 权重 ${Number((metric.weight * 100).toFixed(1))}%`}</p>}
        {metric.target && <p>动作要求 · {productText(metric.target)}</p>}
      </li>)}</ul> : <p>这段视频没有可用于该项评分的测量。</p>}
  </section>;
}

type PracticeActions = { onSaveFocus?: (focus: TrainingFocusSelection) => boolean; replayAvailable?: boolean };

export function SavePractice({ focus, onSaveFocus }: PracticeActions & { focus: TrainingFocusSelection }) {
  const [saved, setSaved] = useState<boolean | null>(null);
  if (!onSaveFocus) return null;
  return <><button type="button" disabled={saved === true} onClick={() => setSaved(onSaveFocus(focus))}>
    {saved ? "已保存练习" : "保存练习"}
  </button><span role="status" className="practice-save-status">{saved === false ? "保存失败，请检查浏览器存储权限后重试。" : saved ? "已记入此浏览器的训练记录。" : ""}</span></>;
}

function Practice({ item, report, priority, replayAvailable, onSaveFocus }: PracticeActions & {
  item: TennisImprovement; report: TennisAssessment; priority: boolean;
}) {
  const detail = observationParts(item.observation);
  return <article className="tennis-drill">
    {priority && <span className="review-label">这次先改什么</span>}
    <h3>{productText(item.title)}</h3>
    {detail.observation && <p className="tennis-observation">{detail.observation}</p>}
    <p className="review-next"><span className="review-label">下一次怎么练</span>{productText(item.drill)}</p>
    {!priority && <PracticeEvidence item={item} />}
    <div className="tennis-drill-actions">
      {item.start_ms !== null && item.end_ms !== null && (replayAvailable
        ? <button type="button" onClick={() => window.dispatchEvent(new CustomEvent("rallymate:seek-video", { detail: { jobId: report.source_run_id, timestampMs: item.start_ms, preferOriginal: true } }))}>回看片段 <time>{seconds(item.start_ms)}–{seconds(item.end_ms)} 秒</time></button>
        : <time>对应片段 {seconds(item.start_ms)}–{seconds(item.end_ms)} 秒</time>)}
      <SavePractice key={`${item.dimension_id}:${item.start_ms}:${item.title}`} focus={tennisImprovementFocus(item)} onSaveFocus={onSaveFocus} />
    </div>
  </article>;
}

function PracticeEvidence({ item }: { item: TennisImprovement }) {
  const { measurement } = observationParts(item.observation);
  return <details className="review-drill-steps"><summary>动作提示与复测目标</summary>
    <dl><div><dt>动作提示</dt><dd>{productText(item.action)}</dd></div><div><dt>复测目标</dt><dd>{productText(item.retest)}</dd></div></dl>
    {measurement && <p className="tennis-observation-measurement"><span>测量依据</span>{measurement}</p>}
  </details>;
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
  const priority = primaryTennisPractice(report);
  if (report && priority) return <div className="tennis-training-result">
    <Practice item={priority} report={report} priority onSaveFocus={onSaveFocus} replayAvailable={replayAvailable} />
  </div>;
  const waiting = !report && loading && !offline && !error && !message;
  return <section className="tennis-assessment-wait" role="status">
    <h2>{waiting ? "正在整理训练重点" : report?.status === "unavailable" ? "本次动作未评分" : "本次训练重点"}</h2>
    <p>{message ? productText(message) : error ? "暂时无法读取动作观察。" : waiting ? "动作测量完成后，这里会显示练习建议。" :
      report?.reason_zh ? productText(report.reason_zh) : report?.status === "unavailable" ? "本次没有可计分的动作测量。" : !report && offline ? "这份备份没有可显示的动作评分。" : "这次没有具体的改进建议。"}</p>
    {!waiting && !error && !message && <p className="review-next"><span className="review-label">下一次怎么练</span>选一个熟悉的动作，按平时的节奏练一组。每次从准备做到挥拍后的回位。</p>}
    {!waiting && !offline && onRetry && (!report || report.status === "unavailable") && <button className="product-secondary" type="button" onClick={onRetry}>重新读取</button>}
  </section>;
}

export function TennisScoreDetails({ report, onSaveFocus, replayAvailable = true }: PracticeActions & { report: TennisAssessment }) {
  const generalPractice = report.improvements.filter(item =>
    report.dimensions.some(dimension => dimension.id === item.dimension_id && dimension.score === null));
  const reference = generalPractice.length > 0 && <details className="review-more"><summary>基础练习参考</summary>
    {generalPractice.map(item => <section key={item.dimension_id}><p>{productText(item.action)}</p><p>{productText(item.drill)}</p><p>{productText(item.retest)}</p></section>)}
  </details>;
  const levels = report.level_assessments?.length ? <details className="review-more"><summary>分级技术评分</summary>
    <LevelAssessmentPanel key={report.source_run_id} assessments={report.level_assessments} />
  </details> : null;
  if (report.status === "unavailable") return <>{reference}{levels}</>;
  const priority = primaryTennisPractice(report);
  const otherDrills = report.improvements.filter(item => item !== priority && !generalPractice.includes(item));
  return <div className="tennis-training-result review-score-details">
    {priority && <PracticeEvidence item={priority} />}
    {reference}
    {levels}
    {(otherDrills.length > 0 || report.strengths.length > 0) && <details className="review-more"><summary>其他练习与优势</summary>
      {otherDrills.map(item => <Practice key={item.dimension_id} item={item} report={report} priority={false} onSaveFocus={onSaveFocus} replayAvailable={replayAvailable} />)}
      {!!report.strengths.length && <section className="review-strengths"><h3>继续保持</h3><ul>{report.strengths.map((item, index) => <li key={index}><strong>{productText(item.title)}</strong><p>{productText(item.observation)}</p></li>)}</ul></section>}
    </details>}
  </div>;
}

export function TennisAssessmentDetails({ report }: { report: TennisAssessment | null }) {
  if (!report || report.status === "unavailable") return null;
  return <section className="tennis-measurements" aria-label="计分方法与片段依据">
    <p>先按权重汇总每段动作的可测项目，再对各段取平均，得到分项成绩。总分取已测分项的平均值，未测项不参与计算。</p>
    {report.dimensions.some(item => item.metrics.some(metric => metric.score !== null && ["knee_load", "support_control", "coil", "arm_space"].includes(metric.id))) &&
      <p>屈膝、站距、准备后半段转体和挥拍手臂空间按持续时间计分。分项中的实测值显示平均表现。</p>}
    <p>分数采用项目试验规则，根据画面中的人体姿态计算，未经教练标定。可用来比较相同动作、相近机位下的训练表现。</p>
    {report.subject_scope && report.pattern_coverage ? <SubjectAndPatternScope report={report} />
      : !!report.episodes?.length && <p>本次分析了 {report.episodes.length} 段挥拍。</p>}
    {report.multimodal_assessment && <details><summary>球、球拍与场地观测</summary>
      <p>共处理 {report.multimodal_assessment.frame_count} 帧：球有观测 {report.multimodal_assessment.summary.ball_observed_frames} 帧，
        球拍有观测 {report.multimodal_assessment.summary.racket_observed_frames} 帧，场地标定有效 {report.multimodal_assessment.summary.court_calibrated_frames} 帧。</p>
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
  </section>;
}
