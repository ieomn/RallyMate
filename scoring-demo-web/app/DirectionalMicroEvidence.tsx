import type { CardInstance } from "./lib/card-execution";
import { directionalMicroSummary } from "./lib/directional-micro-adjustment-evidence";
import { objectOf } from "./lib/manual-card-evidence";

const show = (v: unknown) => typeof v === "number" && Number.isFinite(v) ? v.toLocaleString("zh-CN", { maximumFractionDigits: 3 }) : "—";
export default function DirectionalMicroEvidence({ instance }: { instance: CardInstance }) {
  const v = instance.directional_micro_validation;
  if (!v) return null;
  const ref = objectOf(instance.source_window_annotation?.direction_reference), frame = objectOf(ref.source_frame);
  return <div className="card-directional-micro-evidence">
    <p>{directionalMicroSummary(v)}</p>
    <p>接近目标、小幅运动、轴向一致是三个独立条件。接近值为负表示沿所标轴远离此帧可见球；其余条件成立不改变这个结果。纵向和横向各自使用原图标记的轴，不把图像垂线当作球场横向。</p>
    <p>图像参考原帧 {show(frame.frame_index)}；PTS {show(frame.pts)} × {show(frame.time_base_num)}/{show(frame.time_base_den)}；标点不确定性 ±{show(ref.uncertainty_px)} 像素。应用规则：{instance.application_rule_version}。</p>
    {v.feet && <details><summary>双脚完整路径与动静区间</summary>{(["left", "right"] as const).map(side => <div key={side}>
      <p>{side === "left" ? "左脚" : "右脚"}：路径 {show(v.feet![side].path_length_torso)} 躯干单位；{v.feet![side].complete_visual_motion_cycle ? "完整低速—移动—低速" : "本脚没有完整低速—移动—低速"}{v.moving_foot === side ? "；人工观察脚" : ""}。</p>
      <ol>{v.feet![side].phases.map((phase, index) => <li key={index}>原帧 {phase.source_frames[0]}–{phase.source_frames.at(-1)}：{phase.moving ? "移动" : "低速"} {show(phase.duration_ms)} 毫秒。</li>)}</ol>
    </div>)}</details>}
    <p>{v.status === "measured_directional_motion" ? "六个必要关节均由原帧连续提供；局部完整躯干归一化与原模型绑定独立保留，没有补齐缺失关节。" : "尚无通过校验的完整六关节连续测量；原始输入仅作为来源记录保留。"}</p>
  </div>;
}
