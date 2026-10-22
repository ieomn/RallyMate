"use client";

import { useMemo, useState } from "react";
import { CARD_RUN_LABELS, cardExecutionOf, cardReasonLabel, type CardCondition } from "./lib/card-execution";
import { replayMoment } from "./lib/training-report";

const metricLabels: Record<string, string> = {
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
};
const unitLabels: Record<string, string> = { torso: "躯干单位", "torso/s": "躯干单位/秒", deg: "度", "deg/s": "度/秒", ms: "毫秒", fraction: "比例", ratio: "比例" };
const format = (value: number) => value.toLocaleString("zh-CN", { maximumFractionDigits: 3 });
const clock = (ms: number) => `${(ms / 1000).toFixed(3)} 秒`;
function thresholdLabel(condition: CardCondition) {
  if (condition.threshold === null) return "阈值缺失";
  const threshold = Array.isArray(condition.threshold) ? `${format(condition.threshold[0])}–${format(condition.threshold[1])}` : format(condition.threshold);
  return `${({ gte: "至少", lte: "至多", between: "范围" } as Record<string, string>)[condition.operator] ?? condition.operator} ${threshold} ${unitLabels[condition.unit] ?? condition.unit}`;
}

export default function CardExecutionPanel({ value, jobId, durationMs }: { value: unknown; jobId?: string; durationMs?: number | null }) {
  const report = useMemo(() => cardExecutionOf(value), [value]);
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("");
  const [selectedId, setSelectedId] = useState("");
  const [selectedEvent, setSelectedEvent] = useState("");
  if (!report) return null;
  const cards = report.cards.filter(card => (!status || card.run_status === status) && `${card.indicator_id} ${card.name_zh}`.toLowerCase().includes(query.trim().toLowerCase()));
  const card = cards.find(row => row.indicator_id === selectedId) ?? cards[0];
  const instanceKey = (row: NonNullable<typeof card>["instances"][number]) => `${row.video_id}:${row.event_id}:${row.person_track_id}`;
  const instance = card?.instances.find(row => instanceKey(row) === selectedEvent) ?? card?.instances[0];
  const summary = report.summary;
  const canReplay = instance?.window && jobId && report.source_run_id === jobId
    && typeof durationMs === "number" && Number.isFinite(durationMs) && instance.window.end_ms <= durationMs;
  return <details className="indicator-assessment card-execution-panel">
    <summary>逐卡计算与复核 <span>{summary.catalogue_cards} / {summary.expected_source_cards} 张规则已返回 · 本次执行 {summary.computed_cards} 张</span></summary>
    <p className="indicator-assessment-intro">规则清单完整度与本次可评分范围分别显示。以下视觉代理分表示工程条件完成度，原卡技术等级尚未自动判定。缺测不记零分，也不记 E。</p>
    {report.status === "unavailable" && <p role="status">本次评分卡计算暂不可用，未生成评分结果。</p>}
    {report.reason_codes.map(reason => <p key={reason}>{cardReasonLabel(reason)}</p>)}
    {report.limitations.length > 0 && <details><summary>本次计算范围与限制</summary>{report.limitations.map((limitation, index) => <p key={index}>{limitation}</p>)}</details>}
    <dl className="report-measurement-summary">
      <div><dt>原卡自动判级</dt><dd>{summary.original_scored_cards} 张</dd></div>
      <div><dt>有视觉代理分的卡</dt><dd>{summary.proxy_scored_cards} 张 · {summary.proxy_scored_instances} 次</dd></div>
      <div><dt>需要人工复核</dt><dd>{summary.review_instances} 次</dd></div>
      <div><dt>证据不足</dt><dd>{summary.unavailable_instances} 次</dd></div>
    </dl>
    {summary.catalogue_cards < summary.expected_source_cards && <p className="indicator-grade-note">本报告只返回部分规则条目，未返回部分不能视作已执行。</p>}
    {(summary.conflicting_instances > 0 || summary.rejected_cards > 0) && <p role="status">存在重复冲突或无效记录，相关分数已排除，需要重新计算。</p>}
    <div className="indicator-filters">
      <label>搜索评分卡<input type="search" value={query} onChange={event => setQuery(event.target.value)} placeholder="名称或编号" /></label>
      <label>本次执行状态<select value={status} onChange={event => setStatus(event.target.value)}><option value="">全部规则</option>{Object.entries(CARD_RUN_LABELS).map(([key, label]) => <option key={key} value={key}>{label}（{summary.run_card_status_counts[key] ?? 0}）</option>)}</select></label>
    </div>
    <label className="indicator-picker">选择评分卡<select value={card?.indicator_id ?? ""} onChange={event => { setSelectedId(event.target.value); setSelectedEvent(""); }}>{cards.map(row => <option key={row.indicator_id} value={row.indicator_id}>{row.indicator_id} · {row.name_zh}</option>)}</select></label>
    {card ? <article className="indicator-detail">
      <span className="indicator-state">{CARD_RUN_LABELS[card.run_status]}</span><h3>{card.name_zh}</h3>
      <p>{card.rule_kind === "visual_proxy" ? "视觉代理规则" : "人工复核规则"} · {card.rule_version}</p>
      {card.reason_codes.map(reason => <p key={reason}>{cardReasonLabel(reason)}</p>)}
      {card.instances.length > 1 && <label className="indicator-picker">动作实例<select value={instance ? instanceKey(instance) : ""} onChange={event => setSelectedEvent(event.target.value)}>{card.instances.map((row, index) => <option key={instanceKey(row)} value={instanceKey(row)}>{index + 1}. {row.window ? `${clock(row.window.start_ms)}–${clock(row.window.end_ms)}` : "未定位完整时间窗"} · {CARD_RUN_LABELS[row.status]}</option>)}</select></label>}
      {instance ? <div className="indicator-observation">
        <strong>{instance.score === null ? "— 未评分" : `${instance.proxy_grade} · ${instance.score} / 100`} <small>{instance.score === null ? CARD_RUN_LABELS[instance.status] : "工程条件完成度"}</small></strong>
        <p>{instance.window ? `计算窗口：${clock(instance.window.start_ms)}–${clock(instance.window.end_ms)}` : "计算窗口不完整，保留缺失原因。"}</p>
        <p>{instance.quality.total_frame_count > 0 ? `所需关节同时有效：${instance.quality.valid_frame_count} / ${instance.quality.total_frame_count} 帧。` : "尚无此卡所需关节的有效帧统计。"}此处仅检查几何可用性，未验证真实可见或物理受力。</p>
        {instance.source_stage && <p>阶段来源：{instance.source_stage.stage_code}，人工估计时间窗{instance.source_stage.binding.uncertainty_ms !== null ? `，边界不确定性 ±${format(instance.source_stage.binding.uncertainty_ms)} 毫秒` : ""}。这不是专家真值，也不代表系统已自动定位完整十阶段。</p>}
        {instance.reason_codes.map(reason => <p key={reason}>{cardReasonLabel(reason)}</p>)}
        {instance.fallback && <p>已触发复核路径：{cardReasonLabel(instance.fallback.trigger)}{instance.fallback.selected ? `；候选片段 ${clock(instance.fallback.selected.start_ms)}–${clock(instance.fallback.selected.end_ms)}` : ""}。复核候选不能当作已确认动作。</p>}
        {instance.conditions.length > 0 && <details open><summary>逐条件计算依据</summary><dl>{instance.conditions.map(condition => <div key={condition.metric}><dt>{metricLabels[condition.metric] ?? condition.metric}</dt><dd>实测 {condition.value === null ? "— 缺测" : `${format(condition.value)} ${unitLabels[condition.unit] ?? condition.unit}`}；{thresholdLabel(condition)}；{condition.passed === null ? "未判定" : condition.passed ? "满足工程条件" : "未满足工程条件"}</dd></div>)}</dl></details>}
        {canReplay && <button type="button" className="product-secondary" onClick={() => replayMoment({ startMs: instance.window!.start_ms }, jobId)}>回看计算片段</button>}
        <details><summary>版本与来源</summary><p>事件：{instance.event_id} · 球员轨迹 {instance.person_track_id}</p><p>计算版本：{instance.measurement_version || "未生成测量"}</p><p>原卡定位：{instance.source.document_id} · {instance.source.table}</p>{instance.source_stage && <p>阶段标注：{instance.source_stage.binding.source} · {instance.source_stage.binding.version}</p>}<p>规则阈值为工程暂定值，尚无专家标定准确率。</p></details>
      </div> : <p>本次没有可用的计算实例，不生成分数。</p>}
      <p className="indicator-grade-note">原卡等级、视觉代理与人工复核分别统计，未合并为总分。</p>
    </article> : <p>没有匹配的评分卡。</p>}
  </details>;
}
