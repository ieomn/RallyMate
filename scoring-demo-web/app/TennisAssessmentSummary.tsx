"use client";

import { useId } from "react";
import { TENNIS_PATTERN_NAMES, type TennisAssessment } from "./lib/tennis-assessment";
import type { TrainingFocusSelection } from "./lib/training-guidance";
import { productText } from "./lib/presentation-text";
import { tennisImprovementFocus } from "./lib/tennis-practice";
export { tennisImprovementFocus } from "./lib/tennis-practice";
import "./tennis-assessment.css";

const scoreText = (value: number) => value.toFixed(1);
const seconds = (value: number) => (value / 1000).toFixed(1);
const observationReason = (reason: string) => {
  if (reason === "external_peak_motion_geometry_unverified") return "运动峰落在候选挥拍区间外，选中片段自身缺少完整前挥证据，暂不将举拍或移动计为完整挥拍";
  if (reason === "unresolved_racket_hand_with_competing_arm_sequence") return "持拍手尚未可靠确认，两侧手腕先后运动，暂无法确认完整挥拍阶段";
  if (reason === "active_wrist_discontinuity_in_motion") return "测量手腕姿态不连续，暂无法评分";
  if (reason === "non_active_wrist_discontinuity_unmeasured") return "另一只手的姿态不连续，依赖该手的项目按实际缺测处理";
  if (reason === "preparation_start_clipped_by_source_boundary") return "准备阶段起点超出连续观测范围，相关配合项目未测量";
  if (reason === "same_action_represented_by_verified_return" || reason === "candidate_overlap_duplicate") return "与另一候选属于同一动作，只计一次";
  if (reason === "return_context_unverified") return "缺少可核验的对方发球与来球证据，暂不能按接发动作计分";
  if (reason === "candidate_lacks_continuous_complete_motion" || reason === "candidate_source_or_complete_phase_unverified") return "未获得覆盖准备、挥出和随挥的连续有效姿态";
  if (reason === "phase_authority_source_unverified") return "动作阶段与原始姿态的对应关系尚未核验";
  if (reason === "reviewed_hand_conflicts_with_motion_phases") return "复核的持拍手与阶段参考手腕不同，尚无足够依据将它们视为同次挥拍，暂未计分";
  if (reason === "racket_association_conflicts_with_motion_wrist") return "球拍关联的手与阶段参考手腕不同，暂未计分";
  if (reason.startsWith("two_hand_phase_") || reason.startsWith("paired_")) return "双手动作的阶段对应尚未核验，或双腕连续观测不足，暂未计分";
  if (reason === "reviewed_technique_conflicts_with_detected_phases") return "复核的技术与检测阶段不一致，暂未计分";
  if (reason.includes("subject") || reason.includes("identity")) return "动作所属球员尚未可靠确认";
  if (reason.includes("source") || reason.includes("binding")) return "动作与原始视频的对应关系尚未可靠确认";
  return "缺少该动作所需的可靠连续观测";
};
const patternReason = (reason: string | null) => {
  if (!reason) return "";
  if (reason === "slice_joint_directions_ill_conditioned") return "此机位中的向前与向下方向过于接近，无法分别确认前下延展";
  if (reason.includes("camera") || reason.includes("source_identity") || reason.includes("source_video")) return "缺少同一主体、同一相机参考下的连续画面观测";
  if (reason.includes("forward_or_up")) return "向前和向上的方向尚未独立核验";
  if (reason.includes("forward_direction")) return "击球方向尚未独立核验";
  if (reason.includes("up_direction")) return "画面向上方向尚未独立核验";
  if (reason.includes("recovery_boundary")) return "完整恢复阶段的结束尚未核验";
  if (reason.includes("force_ground")) return "当前视频没有真实受力、地面接触和真实重心测量";
  if (reason.includes("racket_ball")) return "缺少球拍、球和握拍的可靠观测";
  if (reason.includes("incoming_ball")) return "缺少来球压力或同类动作参考，不能判断引拍是否足够简短";
  if (reason.includes("ball_release")) return "抛球、触球和落地接触尚未完整测量";
  if (reason.includes("peak_order")) return "运动峰不够清晰，或先后差小于当前时间分辨率";
  if (reason.includes("peak_two_sided")) return "运动峰两侧的观测不足，无法确认先后；未把阶段边界当成峰值";
  if (reason.includes("acceleration_support_axes")) return "加速阶段及其相邻观测中的肩线或髋线短缩、缺测，无法可靠比较时序";
  if (reason.includes("preparation_shoulder_axis")) return "准备阶段肩线短缩或缺测，无法可靠测量转体引拍配合";
  if (reason.includes("measurement_floor")) return "可见运动变化不足以稳定测量这一项";
  if (reason.includes("no_technique_specific")) return "具体技术类型未确认，仅使用通用动作指标";
  if (reason.includes("window_incomplete") || reason.includes("time_discontinuous")) return "动作阶段或时间观测不完整";
  return "缺少这项要求所需的连续观测";
};

function SubjectAndPatternScope({ report }: { report: TennisAssessment }) {
  const scope = report.subject_scope, coverage = report.pattern_coverage;
  if (!scope || !coverage) return <p className="tennis-history-scope">历史版本：没有逐目标归属记录，不能据此确认整片始终为同一人。</p>;
  const selected = scope.subjects.find(subject => subject.subject_id === scope.selected_subject_id);
  const excluded = report.episode_validity?.filter(episode => episode.status === "other_subject").length ?? 0;
  const rejected = report.episode_validity?.filter(episode => episode.status === "rejected").length ?? 0;
  const rejectionReasons = new Map<string, number>();
  for (const episode of report.episode_validity ?? []) if (episode.status === "rejected") {
    for (const label of new Set(episode.reason_codes.map(observationReason))) rejectionReasons.set(label, (rejectionReasons.get(label) ?? 0) + 1);
  }
  return <section className="tennis-evidence-scope" aria-label="评分对象与动作覆盖">
    <p><strong>评分对象</strong> {selected ? `${selected.identity_basis === "target_lock" ? "本次锁定目标" : "首个独立轨迹"}，汇总 ${selected.measured_episode_count} 段动作。` : "尚无可确认的评分对象。"}
      {excluded > 0 && `另有 ${excluded} 段属于其他轨迹，未混入评分。`}{rejected > 0 && ` ${rejected} 个候选因观测不足或来源不符未评分。`}</p>
    {selected?.identity_basis === "unlinked_source_track" && <p>旧姿态记录不能确认不同轨迹属于同一人，因此本次分开处理。</p>}
    {rejectionReasons.size > 0 && <ul aria-label="未评分原因">{Array.from(rejectionReasons, ([label, count]) => <li key={label}>{label}（{count} 段）</li>)}</ul>}
    <p><strong>动作要求覆盖</strong> {coverage.measured_requirements} / {coverage.applicable_requirements} 项次有可测依据，
      {coverage.verified_technique_episodes} 段已确认具体技术，{coverage.generic_episodes} 段使用通用指标。</p>
    <small>覆盖表示测到了哪些要求，不是动作合格率。缺测不填零分，也不表示整套发力模式已被验证。</small>
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
    <p>{message || (error ? "动作评分暂未读取成功。" : offline ? "这份备份还没有动作评分，请打开在线报告更新。" : "正在生成动作评分")}</p>
    {(error || message) && onRetry && <button className="product-secondary" type="button" onClick={onRetry}>重新读取</button>}
  </section>;
  if (report.status === "unavailable") return <section className="tennis-assessment-wait" id="assessment"><div>
    <p>{productText(report.reason_zh) || "本次没有形成可评分的完整动作。"}</p>
    <p>{productText(report.improvements[0]?.action) || "请在回放中确认准备、挥拍和随挥完整；重新拍摄时，让全身与双脚持续入镜。"}</p>
    {report.subject_scope && <SubjectAndPatternScope report={report} />}
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
    <SubjectAndPatternScope report={report} />
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
    {report.pattern_coverage && <div className="tennis-pattern-evidence"><h3>各段动作模式与缺测项目</h3>
      <p>髋肩腕的先后与人体中心移动使用经过核验的固定画面参考；它们是运动投影，不能证明真实受力或世界三维动力链。随挥允许继续加速，不要求击后立即减速。</p>
      {(report.episodes ?? []).map(episode => episode.motion_pattern && <details key={episode.episode_id}>
        <summary>{seconds(episode.start_ms)}–{seconds(episode.end_ms)} 秒 · {TENNIS_PATTERN_NAMES[episode.motion_pattern.profile_id]} · 已测 {episode.motion_pattern.coverage.measured_requirements}/{episode.motion_pattern.coverage.applicable_requirements} 项</summary>
        {episode.motion_pattern.classification_provenance.method === "agent_visual_review" || episode.motion_pattern.classification_provenance.method === "agent_review"
          ? <p>技术类别来自辅助画面复核，尚未经过专家标注确认。</p> : null}
        {episode.hand_roles && <p>阶段参考：{episode.hand_roles.motion_wrist === "left" ? "左腕" : "右腕"}；
          {episode.hand_roles.primary_racket_hand === null ? "持拍手尚未独立确认，手腕运动不代表球拍归属。"
            : `${["agent_visual_review", "human_visual_review"].includes(episode.hand_roles.primary_hand_provenance.method) ? "画面复核" : "球拍关联"}：${episode.hand_roles.primary_racket_hand === "left" ? "左手" : "右手"}持拍。`}
          {episode.hand_roles.relationship === "verified_two_hand" && `原始画面投影支持这两腕在同一加速阶段共同运动；手臂与轨迹项目使用${episode.hand_roles.measurement_hand === "left" ? "左手" : "右手"}测量。双腕协同本身不确认握柄上下次序或真实触球。`}
        </p>}
        {!!episode.quality_reason_codes?.length && <p>{Array.from(new Set(episode.quality_reason_codes.map(observationReason))).join("；")}。</p>}
        <ul>{episode.motion_pattern.requirements.map(requirement => <li key={requirement.id}>
          <strong>{productText(requirement.label)}</strong><span>{requirement.status === "measured_proxy" ? "已计入可见动作评分" : "未测量"}</span>
          {requirement.status === "not_measured" && <span>{patternReason(requirement.reason)}</span>}
        </li>)}</ul>
      </details>)}
    </div>}
    {report.dimensions.map(dimension => <div key={dimension.id}><h3>{dimension.label} · {dimension.score === null ? "—" : scoreText(dimension.score)}</h3>
      <ul>{dimension.metrics.map(metric => <li key={metric.id}><strong>{productText(metric.label)}</strong><span>{metric.score === null ? "缺少测量" : `${scoreText(metric.score)} 分 · 权重 ${(metric.weight * 100).toFixed(1)}%`}</span><span>{metric.value === null ? "—" : `${Number(metric.value.toFixed(2))} ${metric.unit}`}</span><span>{productText(metric.target)}</span></li>)}</ul></div>)}
  </section>;
}
