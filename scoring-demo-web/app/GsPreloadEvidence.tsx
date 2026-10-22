import type { CardInstance } from "./lib/card-execution";
import { objectOf } from "./lib/manual-card-evidence";

const array = (value: unknown): unknown[] => Array.isArray(value) ? value : [];
const display = (value: unknown) => typeof value === "number" && Number.isFinite(value) ? value.toLocaleString("zh-CN", { maximumFractionDigits: 4 }) : "—";
const text = (value: unknown) => typeof value === "string" ? value : "来源未核验";
const labels: Record<string, string> = { hip_downward_displacement_torso: "局部髋中点下降", "left_knee_flexion_deg.delta": "左膝投影屈曲变化", "right_knee_flexion_deg.delta": "右膝投影屈曲变化" };
const reasons: Record<string, string> = {
  preload_contract_or_grade_claim_invalid: "辅助协议不匹配或输入包含不支持的评分声明",
  preload_source_binding_invalid: "辅助来源与父阶段、动作或人物绑定不一致",
  preload_stage_containment_invalid: "局部原帧窗口未完整包含于准备阶段",
  preload_original_denominator_invalid: "完整原帧库存或八关节共同分母不一致",
  preload_source_pts_invalid: "逐帧原始 PTS、时基或时钟绑定不一致",
  preload_joint_union_invalid: "八关节共同有效帧统计不一致",
  preload_series_invalid: "逐帧几何序列不完整或数值无效",
  preload_joint_timing_invalid: "相邻髋下降与双膝屈曲的联合时序证据不一致",
  preload_observation_contract_invalid: "辅助参考值被修改或输入试图授权评分",
  preload_continuous_measurement_invalid: "父阶段、连续帧或身份与相机条件不足，不能展示为有效测量",
  preload_arithmetic_mismatch: "辅助汇总值与逐帧序列重新计算结果不一致",
  preload_unavailable_must_not_expose_measurements: "缺测状态包含了未经支持的汇总数值",
  local_preload_requires_seven_continuous_source_frames: "局部连续原帧不足七帧",
  local_preload_fixed_camera_reference_required: "缺少固定相机参考",
  local_preload_source_identity_or_reference_changed: "局部窗口跨越选人或相机参考分段",
  local_preload_joint_gap_in_continuous_window: "八个必要关节未在每个原帧共同有效",
  local_preload_joint_union_below_70_percent: "八关节共同有效帧不足 70%",
  local_preload_decoder_pts_invalid: "原始解码时钟无效",
  parent_gs_stage_unavailable: "所属准备阶段不可用，局部观测不能替代完整阶段",
  window_too_short: "局部连续观测不足 80 毫秒",
  continuous_endpoint_window_missing: "缺少完整连续的端点测量窗口",
  partial_window_cannot_establish_change: "部分窗口不足以确认端点变化",
  source_decoder_window_inventory_incomplete: "父阶段的原始解码帧库存不完整",
};

export default function GsPreloadEvidence({ instance }: { instance: CardInstance }) {
  const raw = instance.auxiliary_evidence, validation = instance.auxiliary_validation;
  if (!raw || !validation) return null;
  const source = objectOf(raw.source_annotation), observation = objectOf(source.observation), series = objectOf(raw.series);
  const bindings = array(raw.source_frame_bindings), frames = array(series.source_frames);
  return <section className="card-preload-evidence" aria-label="局部下降辅助观测">
    <h4>局部下降辅助观测 · 不评分</h4>
    <p>人工圈定的局部下降也可能是站姿调整，不能确认垫步、触地、发力或真实重心。下列工程参考值不判定通过，不产生原卡等级或视觉代理分。</p>
    <p>{validation.status === "measured_auxiliary" ? "连续辅助几何已计算；父卡仍需人工复核。" : validation.status === "unavailable" ? "连续辅助观测缺测；父卡保持原有复核或不可用状态。" : "辅助输入未通过独立校验，数值不显示为有效测量。"}</p>
    {validation.total_frame_count !== null && <p>八关节共同有效：{validation.valid_frame_count} / {validation.total_frame_count} 个原帧。肩、髋、膝、踝两侧必须同时有效；连续测量要求每帧完整，70% 仅为诊断参考。</p>}
    {validation.status !== "invalid" && <>
      <p>人工局部窗口来源：{text(observation.source)}；边界不确定性 ±{display(observation.uncertainty_ms)} 毫秒。不是专家真值，也不代表自动定位准备阶段。</p>
      <dl>{validation.observations.map(row => <div key={row.metric}><dt>{labels[row.metric] ?? row.metric}</dt><dd>辅助值 {display(row.value)} {row.unit === "deg" ? "度" : "躯干单位"}；工程参考 ≥{display(row.engineering_reference)}，仅供复核，不判通过。</dd></div>)}</dl>
      <p>相邻髋下降与双膝屈曲共同发生的时间比：{validation.joint_descent_fraction === null ? "— 缺测" : `${display(validation.joint_descent_fraction * 100)}%`}。按实际 PTS 时长计算，不是区间数量占比或技术得分。</p>
      <details><summary>原始时钟与完整逐帧辅助序列（{frames.length} 帧）</summary>
        <p>髋 y 使用固定相机参考像素；屈曲角使用度。保留原始 PTS 原点，不换算为播放器零点。首尾中位数各使用 {display(series.endpoint_frame_count)} 帧，固定躯干尺度 {display(series.fixed_torso_scale)} 像素。</p>
        <div style={{ maxWidth: "100%", overflowX: "auto" }}><table><thead><tr><th>原帧</th><th>原始 PTS / 时基</th><th>原时钟毫秒</th><th>髋 y（像素）</th><th>左膝 / 右膝（度）</th><th>躯干尺度（像素）</th><th>与下一原帧的三项联合变化</th></tr></thead><tbody>
          {frames.map((frame, index) => { const b = objectOf(bindings[index]), simultaneous = array(series.simultaneous_descent_intervals)[index]; return <tr key={index}>
            <td>{display(frame)}</td><td>{display(b.pts)} × {display(b.time_base_num)}/{display(b.time_base_den)}</td><td>{display(array(series.source_timestamps_ms)[index])}</td>
            <td>{display(array(series.hip_y_camera_reference)[index])}</td><td>{display(array(series.left_knee_flexion_deg)[index])} / {display(array(series.right_knee_flexion_deg)[index])}</td><td>{display(array(series.torso_scale_by_frame)[index])}</td><td>{simultaneous === true ? "同区间增加" : simultaneous === false ? "未同区间增加" : "—"}</td>
          </tr>; })}
        </tbody></table></div>
        <p>标注版本：{text(observation.version)}；文档摘要：{text(source.document_sha256)}。</p>
      </details>
    </>}
    {validation.reason_codes.map(reason => <p key={reason}>{reasons[reason] ?? reason}</p>)}
    <details><summary>{validation.status === "invalid" ? "未经验证的输入记录（仅供诊断）" : "完整原始辅助记录与身份绑定"}</summary>
      {validation.status === "invalid" && <p>以下原始字段未获验证，任何保存的评分或通过标志均不采纳。</p>}
      <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{JSON.stringify(raw, null, 2)}</pre>
    </details>
  </section>;
}
