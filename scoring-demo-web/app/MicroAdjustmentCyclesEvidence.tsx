import type { CardInstance } from "./lib/card-execution";
import { microCycleSummary } from "./lib/micro-adjustment-cycles-evidence";

const show = (value: unknown) => typeof value === "number" && Number.isFinite(value) ? value.toLocaleString("zh-CN", { maximumFractionDigits: 3 }) : "—";
const reasonLabels: Record<string, string> = {
  preceding_low_motion_censored_at_window_start: "窗口起点没有完整前置低速区间",
  following_low_motion_censored_at_window_end: "窗口终点没有完整后置低速区间",
  step_motion_requires_single_low_move_low: "局部运动没有单一完整低速—移动—低速顺序",
  step_motion_phase_too_short: "局部区间时长或原帧数不足",
  step_motion_endpoint_window_overlaps_movement: "端点统计窗口覆盖了移动区间",
  step_motion_endpoint_positions_not_distinguishable: "起止位置差异未超过端点运动范围",
};
export default function MicroAdjustmentCyclesEvidence({ instance }: { instance: CardInstance }) {
  const v = instance.micro_cycle_validation;
  if (!v) return null;
  return <div className="card-micro-cycle-evidence">
    <p>{microCycleSummary(v)}</p>
    <p>连续性与小幅路径是两个独立条件。左右脚运动时间有正长度重叠时合为一个片段，仅共享一个端点不合并；截断运动参与重叠合并和全窗路径，但自身不能证明完整片段。</p>
    {v.status === "measured_visual_adjustment_episodes" && <>
      <p>完整六关节原帧 {show(v.total_frame_count)} 帧；固定躯干长度 {show(v.fixed_torso_scale)} 像素；窗口 {show(v.duration_s)} 秒。窗口描述频率 {show(v.auxiliary.observed_complete_episode_frequency_hz)} 次/秒，不参与评分。</p>
      <details><summary>全部运动周期与重叠片段</summary>
        {v.feet && (["left", "right"] as const).map(side => <div key={side}>
          <p>{side === "left" ? "左脚" : "右脚"}全窗路径 {show(v.feet![side].path_length_torso)} 躯干单位。</p>
          <ol>{v.feet![side].phases.map((p, index) => <li key={index}>原帧 {p.source_frames[0]}–{p.source_frames.at(-1)}：{p.moving ? "移动" : "低速"} {show(p.duration_ms)} 毫秒。</li>)}</ol>
        </div>)}
        {v.cycles.map(c => <div key={c.cycle_id}>
          <p>{c.moving_ankle === "left" ? "左脚" : "右脚"} {c.cycle_id}；移动原帧 {c.motion_start_source_frame}–{c.motion_end_source_frame}；{c.complete_visual_motion_cycle ? "完整低速—移动—低速" : "截断或不完整运动，保留路径但不单独计完整片段"}。</p>
          {!!c.reason_codes.length && <p>边界与时长记录：{c.reason_codes.map(code => reasonLabels[code] ?? code).join("、")}</p>}
        </div>)}
        <ol>{v.episodes.map(e => <li key={e.episode_id}>{e.episode_id}：原帧 {e.motion_start_source_frame}–{e.motion_end_source_frame}；包含 {e.cycle_ids.join("、")}；{e.complete_visual_adjustment_episode ? "含完整脚部周期，计一个片段" : "仅不完整运动，不计完整片段"}。</li>)}</ol>
      </details>
    </>}
    <p>应用规则：{instance.application_rule_version}。此窗口没有建立专家技术等级、触地、受力或 FS05 阶段真值。</p>
  </div>;
}
