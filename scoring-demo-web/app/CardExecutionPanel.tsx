"use client";

import { useMemo, useState } from "react";
import { CARD_RUN_LABELS, cardExecutionOf, cardReasonLabel, selectedCardInstance, type CardCondition, type CardCycleAnnotation } from "./lib/card-execution";
import { replayMoment } from "./lib/training-report";
import { cycleFrameTime, cycleMotionSteps, type FootworkCycleEvidence } from "./lib/footwork-cycle-evidence";
import ManualCardEvidence from "./ManualCardEvidence";
import GsPreloadEvidence from "./GsPreloadEvidence";
import { isFormalScoringPolicy } from "./lib/formal-card-scoring-policy";
import TechniqueExamples from "./TechniqueExamples";

const metricLabels: Record<string, string> = {
  shoulder_reference_error_reduction_deg: "肩轴至独立准备参照的误差缩小",
  hip_reference_error_reduction_deg: "髋轴至独立准备参照的误差缩小",
  terminal_max_reference_error_deg: "末段肩髋轴至独立参照的最大误差",
  terminal_max_axis_range_deg: "末段肩髋轴最大摆动范围",
  terminal_hip_speed_torso_s: "末段髋中心速度中位数",
  terminal_hip_inside_ankle_support_fraction: "末段髋投影处于双踝区间的比例",
  terminal_trunk_tilt_range_deg: "末段躯干投影倾角范围",
  hip_drop_torso: "髋部下降", left_knee_flexion_change_deg: "左膝屈曲变化", right_knee_flexion_change_deg: "右膝屈曲变化",
  joint_descent_fraction: "髋与双膝同时预加载比例", speed_step_variation_ratio: "速度变化比例", hip_inside_support_fraction: "髋投影位于双踝区间的比例",
  body_speed_std_torso_s: "躯干速度波动", successor_interval_ms: "到后继移动候选的间隔", hip_speed_drop_torso_s: "髋部速度下降",
  hip_speed_drop_fraction: "髋部速度下降比例", trunk_tilt_std_deg: "躯干倾角波动", joint_braking_fraction: "连续制动条件同时满足比例",
  supported_low_motion_duration_ms: "投影支撑内低运动持续时间", shoulder_hip_angular_speed_drop_deg_s: "肩髋角速度下降",
  "hip_axis_deg.range": "髋线投影角度范围", hip_displacement_torso: "髋部投影位移",
  "hip_inside_ankle_support_fraction.mean": "髋投影位于双踝区间的比例", hip_speed_std_torso_s: "髋部速度波动",
  "shoulder_axis_deg.range": "肩线投影角度范围", "shoulder_hip_separation_deg.delta": "肩髋投影夹角变化",
  "shoulder_hip_separation_deg.mean": "肩髋投影平均夹角", "shoulder_hip_separation_deg.range": "肩髋投影夹角范围",
  "trunk_tilt_deg.range": "躯干投影倾角范围", "wrist_separation_torso.delta": "双腕间距变化",
  "wrist_separation_torso.median": "双腕间距中位数", wrist_speed_increase_torso_s: "手腕速度增加",
  "left_knee_flexion_deg.range": "左膝投影屈伸范围", "right_knee_flexion_deg.range": "右膝投影屈伸范围",
  ankle_order_exchange_count: "有中间观测的双踝投影换序次数", ankle_max_displacement_torso: "脚踝起止投影位移",
  ankle_width_contraction_torso: "双踝展开后的间距收窄",
  "ankle_distance_over_hip_width.median": "双踝横向间距与髋宽之比中位数",
  ankle_stability_ratio: "双踝投影运动路程比（较小/较大）",
  head_relative_motion_range_torso: "头部相对肩中点的投影运动范围",
  hip_axis_target_error_reduction_torso: "沿所标轴接近可见球的距离变化",
  complete_visual_adjustment_episode_count: "完整视觉运动片段数",
  both_ankles_max_path_length_torso: "双脚完整运动路程的较大值",
  hip_axis_alignment_fraction: "髋部运动与所标轴的一致比例",
};
const unitLabels: Record<string, string> = { torso: "躯干单位", "torso/s": "躯干单位/秒", deg: "度", "deg/s": "度/秒", ms: "毫秒", fraction: "比例", ratio: "比例", count: "次" };
const format = (value: number) => value.toLocaleString("zh-CN", { maximumFractionDigits: 3 });
const clock = (ms: number) => `${(ms / 1000).toFixed(3)} 秒`;
function thresholdLabel(condition: CardCondition) {
  if (condition.threshold === null) return "阈值缺失";
  const threshold = Array.isArray(condition.threshold) ? `${format(condition.threshold[0])}–${format(condition.threshold[1])}` : format(condition.threshold);
  return `${({ gte: "至少", lte: "至多", between: "范围", eq: "等于" } as Record<string, string>)[condition.operator] ?? condition.operator} ${threshold} ${unitLabels[condition.unit] ?? condition.unit}`;
}

const record = (value: unknown): Record<string, unknown> => value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
const array = (value: unknown): unknown[] => Array.isArray(value) ? value : [];
const scalar = (value: unknown) => typeof value === "number" && Number.isFinite(value) ? format(value) : "—";
const sideName = (value: unknown) => value === "left" ? "左脚" : value === "right" ? "右脚" : "未确认侧别";
function CycleEvidence({ cycle, annotation }: { cycle: FootworkCycleEvidence; annotation: CardCycleAnnotation | null }) {
  const binding = cycle.step_window_binding, series = cycle.series;
  const moment = (frame: unknown) => { const time = cycleFrameTime(cycle, frame); return time === null ? "时钟未核验" : clock(time); };
  return <div className="card-cycle-evidence">
    <p>步周期来源：{binding.method === "manual_anchor" ? "人工候选窗口" : "来源未核验"} · {String(binding.evidence_reference ?? "来源未核验")}。人工窗口不是专家步型真值，也不是自动步周期定位。</p>
    {annotation && <p>人工标注边界不确定性：±{format(annotation.binding.uncertainty_ms)} 毫秒；来源版本 {annotation.binding.version}。</p>}
    <p>连续脚部运动只描述画面投影；运动前后的低速区间不证明触地、支撑或发力。相机补偿和轨迹编号保持也不等于人工确认身份。</p>
    <details><summary>完整脚序与原帧依据</summary>
      <p>候选窗口原帧 {scalar(binding.start_source_frame)}–{scalar(binding.end_source_frame)}；完整视觉运动周期 {scalar(series.complete_visual_motion_cycle_count)} 个。</p>
      {cycleMotionSteps(cycle).length ? <ol>{cycleMotionSteps(cycle).map((step, index) => <li key={index}>
        {sideName(step.side)}：{moment(step.start_source_frame)}–{moment(step.end_source_frame)}，原帧 {scalar(step.start_source_frame)}–{scalar(step.end_source_frame)}；
        运动前低速 {scalar(step.preceding_low_motion_duration_ms)} 毫秒，运动 {scalar(step.motion_duration_ms)} 毫秒，运动后低速 {scalar(step.following_low_motion_duration_ms)} 毫秒；
        {step.complete_visual_motion_cycle === true ? "已观察到完整视觉运动区间" : "运动区间不完整"}。
      </li>)}</ol> : <p>没有完整可核验的脚部运动区间。</p>}
      {array(series.sequential_follow_pairs).map((value, index) => { const pair = record(value); return <p key={`follow-${index}`}>
        先后脚观测：{sideName(pair.lead_side)} {moment(pair.lead_start_source_frame)} 先动，{sideName(pair.follow_side)} {moment(pair.follow_start_source_frame)} 跟随；启动间隔 {scalar(pair.onset_delay_ms)} 毫秒，间距峰值位于原帧 {scalar(pair.width_peak_source_frame)}。
      </p>; })}
      {array(series.ankle_order_exchanges).map((value, index) => { const crossing = record(value); return <p key={`cross-${index}`}>
        投影换序：原帧 {scalar(crossing.before_source_frame)} → 接近重叠的原帧 {array(crossing.near_zero_source_frames).map(scalar).join("、")} → 原帧 {scalar(crossing.after_source_frame)}。端点之间保留实际观测，不以直线补交叉。
      </p>; })}
      <details><summary>逐帧踝部观测与原始时钟（{cycle.source_frames.length} 帧）</summary>
        <p>播放时刻按原帧锚点映射；原始 PTS 的起点可能与播放器不同。x、y 是同一相机参考系中的投影坐标，按原图长边归一化。</p>
        <p>投影顺序图例：1 表示左踝投影在右踝的画面右侧；−1 表示在画面左侧；0 表示落在噪声带、顺序不可分辨，不是换序次数为零。</p>
        <div style={{ maxWidth: "100%", overflowX: "auto" }}><table><thead><tr><th>原帧</th><th>播放时刻</th><th>原始 PTS / 时基</th><th>左踝 x,y</th><th>右踝 x,y</th><th>双踝间距（躯干单位）</th><th>投影顺序</th></tr></thead><tbody>
          {cycle.source_frames.map((frame, index) => { const source = record(cycle.source_frame_bindings[index]); return <tr key={index}>
            <td>{scalar(frame)}</td><td>{moment(frame)}</td><td>{source.status === "verified_decoder_binding" ? `${scalar(source.pts)} × ${scalar(source.time_base_num)}/${scalar(source.time_base_den)}` : "缺少核验"}</td>
            <td>{array(array(series.left_ankle_xy_reference)[index]).map(scalar).join(", ") || "—"}</td>
            <td>{array(array(series.right_ankle_xy_reference)[index]).map(scalar).join(", ") || "—"}</td>
            <td>{scalar(array(series.ankle_width_torso)[index])}</td><td>{scalar(array(series.ankle_order_sign)[index])}</td>
          </tr>; })}
        </tbody></table></div>
      </details>
      <p>窗口来源版本：{String(binding.version ?? "未核验")}；原视频摘要：{String(binding.video_sha256 ?? "未核验")}。</p>
      {annotation && <p>标注文档：{annotation.document_version} · 摘要 {annotation.document_sha256}。</p>}
    </details>
  </div>;
}

export default function CardExecutionPanel({ value, jobId, durationMs }: { value: unknown; jobId?: string; durationMs?: number | null }) {
  const report = useMemo(() => cardExecutionOf(value), [value]);
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("");
  const [selectedId, setSelectedId] = useState("");
  const [selectedEvent, setSelectedEvent] = useState("");
  if (!report) return null;
  const formal = isFormalScoringPolicy(report.scoring_policy_version);
  const cards = report.cards.filter(card => (!status || card.run_status === status) && `${card.indicator_id} ${card.name_zh}`.toLowerCase().includes(query.trim().toLowerCase()));
  const card = cards.find(row => row.indicator_id === selectedId) ?? (formal ? cards.find(row => row.run_status === "scored") : undefined) ?? cards[0];
  const instanceKey = (row: NonNullable<typeof card>["instances"][number]) => `${row.video_id}:${row.event_id}:${row.person_track_id}`;
  const instance = selectedCardInstance(card?.instances ?? [], selectedEvent, formal);
  const summary = report.summary;
  const canReplay = instance?.window && jobId && report.source_run_id === jobId
    && typeof durationMs === "number" && Number.isFinite(durationMs) && instance.window.end_ms <= durationMs;
  return <details className="indicator-assessment card-execution-panel" open={formal}>
    <summary>{formal ? "逐卡正式评分" : "逐卡计算与复核"} <span>{summary.catalogue_cards} / {summary.expected_source_cards} 张规则已返回 · 本次执行 {summary.computed_cards} 张</span></summary>
    <p className="indicator-assessment-intro">{formal ? "可计算条件已自动生成正式评分，总分按各卡实例均值再等权汇总。缺测不记零分，也不记 E。" : "规则清单完整度与本次可评分范围分别显示。以下视觉代理分表示工程条件完成度，原卡技术等级尚未自动判定。缺测不记零分，也不记 E。"}</p>
    {report.status === "unavailable" && <p role="status">本次评分卡计算暂不可用，未生成评分结果。</p>}
    {report.reason_codes.length > 0 && formal ? <p>本次部分评分依据不足，缺失项未计入总分。</p> : report.reason_codes.map(reason => <p key={reason}>{cardReasonLabel(reason)}</p>)}
    {report.limitations.length > 0 && <details><summary>本次计算范围与限制</summary>{report.limitations.map((limitation, index) => <p key={index}>{limitation}</p>)}</details>}
    <dl className="report-measurement-summary">
      {formal ? <><div><dt>正式总分</dt><dd>{report.overall_score === null ? "—" : `${format(report.overall_score)} / 100`}</dd></div><div><dt>已评分</dt><dd>{summary.formal_scored_cards} 张 · {summary.formal_scored_instances} 次</dd></div></>
        : <><div><dt>原卡自动判级</dt><dd>{summary.original_scored_cards} 张</dd></div><div><dt>有视觉代理分的卡</dt><dd>{summary.proxy_scored_cards} 张 · {summary.proxy_scored_instances} 次</dd></div><div><dt>需要人工复核</dt><dd>{summary.review_instances} 次</dd></div></>}
      <div><dt>证据不足</dt><dd>{summary.unavailable_instances} 次</dd></div>
    </dl>
    {summary.catalogue_cards < summary.expected_source_cards && <p className="indicator-grade-note">本报告只返回部分规则条目，未返回部分不能视作已执行。</p>}
    {(summary.conflicting_instances > 0 || summary.rejected_cards > 0) && <p role="status">存在重复冲突或无效记录，相关分数已排除，需要重新计算。</p>}
    <div className="indicator-filters">
      <label>搜索评分卡<input type="search" value={query} onChange={event => setQuery(event.target.value)} placeholder="名称或编号" /></label>
      <label>本次执行状态<select value={status} onChange={event => setStatus(event.target.value)}><option value="">全部规则</option>{Object.entries(CARD_RUN_LABELS).filter(([key]) => !formal || !["review_required", "proxy_scored"].includes(key)).map(([key, label]) => <option key={key} value={key}>{label}（{summary.run_card_status_counts[key] ?? 0}）</option>)}</select></label>
    </div>
    <label className="indicator-picker">选择评分卡<select value={card?.indicator_id ?? ""} onChange={event => { setSelectedId(event.target.value); setSelectedEvent(""); }}>{cards.map(row => <option key={row.indicator_id} value={row.indicator_id}>{row.indicator_id} · {row.name_zh}</option>)}</select></label>
    {card ? <article className="indicator-detail">
      <span className="indicator-state">{CARD_RUN_LABELS[card.run_status]}</span><h3>{card.name_zh}</h3>
      <p>{formal ? "应用评分规则" : card.rule_kind === "visual_proxy" ? "视觉代理规则" : "人工复核规则"} · {card.rule_version}</p>
      {formal ? card.reason_codes.length > 0 && <p>{card.instances.some(row => row.status === "scored") ? "部分实例缺少完整计算依据，已评分实例保持成绩。" : "缺少本卡所需的完整计算依据。"}</p> : card.reason_codes.map(reason => <p key={reason}>{cardReasonLabel(reason)}</p>)}
      {card.instances.length > 1 && <label className="indicator-picker">动作实例<select value={instance ? instanceKey(instance) : ""} onChange={event => setSelectedEvent(event.target.value)}>{card.instances.map((row, index) => <option key={instanceKey(row)} value={instanceKey(row)}>{index + 1}. {row.window ? `${clock(row.window.start_ms)}–${clock(row.window.end_ms)}` : "未定位完整时间窗"} · {CARD_RUN_LABELS[row.status]}</option>)}</select></label>}
      {instance ? <div className="indicator-observation">
        <strong>{instance.score === null ? "— 未评分" : `${formal ? instance.grade : instance.proxy_grade} · ${instance.score} / 100`} <small>{instance.score === null ? CARD_RUN_LABELS[instance.status] : formal ? "正式评分" : "工程条件完成度"}</small></strong>
        <p>{instance.window ? `计算窗口：${clock(instance.window.start_ms)}–${clock(instance.window.end_ms)}` : "计算窗口不完整，保留缺失原因。"}</p>
        <details><summary>测量与来源依据</summary>
        {instance.limitations.map((limitation, index) => <p key={`scope-${index}`}>{limitation}</p>)}
        <p>{instance.quality.total_frame_count > 0 ? `所需关节同时有效：${instance.quality.valid_frame_count} / ${instance.quality.total_frame_count} 帧。` : "尚无此卡所需关节的有效帧统计。"}此处仅检查几何可用性，未验证真实可见或物理受力。</p>
        {instance.source_stage_evidence && <details><summary>完整阶段原帧与分段账本</summary><pre>{JSON.stringify({ window: instance.source_stage_context, evidence: instance.source_stage_evidence }, null, 2)}</pre></details>}
        {instance.source_stage && <p>阶段来源：{instance.source_stage.stage_code}，人工估计时间窗{instance.source_stage.binding.uncertainty_ms !== null ? `，边界不确定性 ±${format(instance.source_stage.binding.uncertainty_ms)} 毫秒` : ""}。这不是专家真值，也不代表系统已自动定位完整十阶段。</p>}
        {instance.foot_cycle && <CycleEvidence cycle={instance.foot_cycle} annotation={instance.source_cycle_annotation} />}
        <ManualCardEvidence instance={instance} />
        <GsPreloadEvidence instance={instance} />
        {instance.reason_codes.map(reason => <p key={reason}>{cardReasonLabel(reason, formal)}</p>)}
        {[...instance.advisory_reason_codes, ...instance.advisory_flags].length > 0 && <p>来源备注：{[...instance.advisory_reason_codes, ...instance.advisory_flags].map(code => cardReasonLabel(code, formal)).join("；")}</p>}
        {instance.local_dip_validation?.status === "measured_local_dip" && <p>已从完整父事件独立重算最早完整局部下降；量测 {instance.local_dip_validation.source_frames.length} 原帧，转折确认 {instance.local_dip_validation.confirmation_source_frames.length} 原帧。未推断起跳或完整技术动作。</p>}
        {instance.temporal_evidence && <details><summary>完整时间序列与来源记录</summary><pre>{JSON.stringify(instance.temporal_evidence, null, 2)}</pre></details>}
        {instance.fallback && <p>{formal ? "局部运动测量来源" : "已触发复核路径"}：{cardReasonLabel(instance.fallback.trigger, formal)}{instance.fallback.selected ? `；候选片段 ${clock(instance.fallback.selected.start_ms)}–${clock(instance.fallback.selected.end_ms)}` : ""}。候选定位不代表已确认完整技术动作。</p>}
        <p>评分政策：{instance.scoring_policy_version ?? "历史视觉代理格式"}；原卡技术等级：未建立。</p>
        </details>
        {instance.conditions.length > 0 && <details open><summary>逐条件计算依据</summary><dl>{instance.conditions.map(condition => <div key={condition.metric}><dt>{metricLabels[condition.metric] ?? condition.metric}</dt><dd>实测 {condition.value === null ? "— 缺测" : `${format(condition.value)} ${unitLabels[condition.unit] ?? condition.unit}`}；{thresholdLabel(condition)}；{condition.passed === null ? "未判定" : condition.passed ? "满足工程条件" : "未满足工程条件"}</dd></div>)}</dl></details>}
        {canReplay && <button type="button" className="product-secondary" onClick={() => replayMoment({ startMs: instance.window!.start_ms }, jobId)}>回看计算片段</button>}
        <details><summary>版本与来源</summary><p>事件：{instance.event_id} · 球员轨迹 {instance.person_track_id}</p><p>计算版本：{instance.measurement_version || "未生成测量"}</p><p>原卡定位：{instance.source.document_id} · {instance.source.table}</p>{instance.source_stage && <p>阶段标注：{instance.source_stage.binding.source} · {instance.source_stage.binding.version}</p>}<p>规则阈值为工程暂定值，尚无专家标定准确率。</p></details>
      </div> : <p>本次没有可用的计算实例，不生成分数。</p>}
      <p className="indicator-grade-note">{formal ? "总分来自有完整计算依据的评分卡，缺失卡保留在覆盖范围中。" : "原卡等级、视觉代理与人工复核分别统计，未合并为总分。"}</p>
      <TechniqueExamples indicatorId={card.indicator_id} />
    </article> : <p>没有匹配的评分卡。</p>}
  </details>;
}
