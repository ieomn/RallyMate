import type { CardInstance } from "./lib/card-execution";
import { objectOf } from "./lib/manual-card-evidence";
import { stepMotionSummary } from "./lib/fs-step-motion-evidence";

const list = (value: unknown): unknown[] => Array.isArray(value) ? value : [];
const show = (value: unknown) => typeof value === "number" && Number.isFinite(value) ? value.toLocaleString("zh-CN", { maximumFractionDigits: 4 }) : "—";
const point = (value: unknown) => list(value).map(show).join(", ") || "—";

export default function FsStepMotionEvidence({ instance }: { instance: CardInstance }) {
  const value = instance.step_motion_validation;
  if (!value) return null;
  const raw = objectOf(instance.source_window_evidence?.step_motion), geometry = list(raw.source_geometry_series), reference = list(raw.reference_series);
  const pts = list(raw.source_pts), annotation = objectOf(instance.source_window_annotation?.step_motion_observation);
  return <section aria-label="移动脚二维运动证据">
    <h4>迈步 / 跨步的局部二维运动</h4>
    <p>{stepMotionSummary(value)}</p>
    {value.model_estimate_sample_count > 0 && <p>本窗口含 {value.model_estimate_sample_count} 帧单目模型估计。测量与来源逐帧核对，评分按报告所列应用政策计算；模型估计不等于已确认真实可见性。</p>}
    <p>人工声明的移动脚不代表已确认前脚或目标方向。低速—移动—低速只描述图像中的一个运动片段，不确认触地、交叉脚序或技术步法；无低速停顿的弧线和折返仍可能属于同一个速度片段。</p>
    {value.status === "measured_visual_motion" && !["proxy_scored", "scored"].includes(instance.status) && <p>运动几何可复算，但本卡仍缺少完整评分依据；此卡没有分数。</p>}
    {value.status !== "invalid" && <p>固定躯干尺度：{show(value.fixed_torso_scale)} 相机参考像素；首尾各取 {show(value.endpoint_count)} 帧中位数。没有缩短端点窗口来凑足运动证据。</p>}
    {value.status !== "invalid" && value.feet && <>
      <div style={{ maxWidth: "100%", overflowX: "auto" }}><table><thead><tr><th>脚</th><th>片段</th><th>原帧范围</th><th>持续时间</th><th>帧数</th></tr></thead><tbody>
        {(["left", "right"] as const).flatMap(side => value.feet![side].phases.map((phase, i) => <tr key={`${side}-${i}`}><td>{side === "left" ? "左脚" : "右脚"}</td><td>{phase.moving ? "移动" : "低速"}</td><td>{phase.source_frames[0]}–{phase.source_frames.at(-1)}</td><td>{show(phase.duration_ms)} 毫秒</td><td>{phase.source_frames.length}</td></tr>))}
      </tbody></table></div>
      <p>三段各要求至少 80 毫秒和 3 个原帧；相邻片段共享边界帧。速度分界为 0.5 躯干单位/秒，属于未校准的工程观察政策。</p>
      <details><summary>原 PTS、六关节共同分母与完整双踝序列（{value.total_frame_count} 帧）</summary>
        <p>双踝 x,y 为固定相机参考像素；躯干倾角使用同一补偿参考平面逐帧归一化后的坐标。读取时按双肩中心和完整躯干尺度核对两套坐标，不把参考像素当作归一化值。所有原帧都留在分母中，未插值。</p>
        <div style={{ maxWidth: "100%", overflowX: "auto" }}><table><thead><tr><th>原帧</th><th>PTS / 时基</th><th>左踝 x,y</th><th>右踝 x,y</th><th>左 / 右至下一原帧速度（躯干/秒）</th></tr></thead><tbody>
          {geometry.map((item, i) => { const g = objectOf(item), p = objectOf(pts[i]), r = objectOf(objectOf(reference[i]).points); return <tr key={i}><td>{show(g.frame_index)}</td><td>{show(p.pts)} × {show(p.time_base_num)}/{show(p.time_base_den)}</td><td>{point(r.left_ankle)}</td><td>{point(r.right_ankle)}</td><td>{show(value.feet?.left.intervals[i]?.speed_torso_s)} / {show(value.feet?.right.intervals[i]?.speed_torso_s)}</td></tr>; })}
        </tbody></table></div>
        <p>人工移动脚依据：{typeof annotation.source_description === "string" ? annotation.source_description : "未核验"}。该观察不建立触地和技术等级。</p>
      </details>
    </>}
    <details><summary>{value.status === "invalid" ? "未经验证的运动输入记录（仅供诊断）" : "完整原始运动与归一化几何记录"}</summary>
      {value.status === "invalid" && <p>保存的完整周期、评分或通过字段未获授权，不按成绩显示。</p>}
      <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{JSON.stringify(raw, null, 2)}</pre>
    </details>
  </section>;
}
