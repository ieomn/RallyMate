import type { CardInstance } from "./lib/card-execution";
import { objectOf, validRacketSide } from "./lib/manual-card-evidence";
import FsStepMotionEvidence from "./FsStepMotionEvidence";
import MicroAdjustmentCyclesEvidence from "./MicroAdjustmentCyclesEvidence";
import DirectionalMicroEvidence from "./DirectionalMicroEvidence";

const list = (value: unknown): unknown[] => Array.isArray(value) ? value : [];
const show = (value: unknown) => typeof value === "number" && Number.isFinite(value) ? value.toLocaleString("zh-CN", { maximumFractionDigits: 3 }) : typeof value === "string" ? value : "未核验";
const wrist = (value: unknown) => value === "left" ? "左腕" : value === "right" ? "右腕" : "侧别未确认";

export default function ManualCardEvidence({ instance }: { instance: CardInstance }) {
  const proof = instance.racket_side_evidence, a = instance.source_window_annotation, evidence = instance.source_window_evidence;
  const observation = proof?.observation, validSide = proof && validRacketSide(proof, instance, instance.source_stage);
  const binding = objectOf(a?.binding);
  return <>
    {instance.measurement_provenance && <div className="card-model-measurement-provenance">
      <p>模型测量来源随结果保留，应用评分政策与原始测量来源分别记录。</p>
      <details><summary>完整模型测量来源记录</summary><pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{JSON.stringify({ model_scoring_policy_version: instance.model_scoring_policy_version, measurement_provenance: instance.measurement_provenance }, null, 2)}</pre></details>
    </div>}
    {observation && <div className="card-racket-side-evidence">
      <p>测量腕：{validSide ? wrist(observation.measurement_wrist_side) : "来源未核验，侧别不用于评分"}；采样画面的持拍手观察：{validSide ? observation.observed_racket_hands === "both" ? "双手" : wrist(observation.observed_racket_hands) : "未确认"}。</p>
      <p>这是人工观察到的持拍腕，不是优势手判定；采样帧不证明全程持续持拍，也不是专家确认的技术真值。边界不确定性 ±{show(observation.uncertainty_ms)} 毫秒。</p>
      <p>{validSide && observation.stage_continuity_verified === true ? "后端已核验阶段内的选人、相机分段及原帧库存；测量腕绑定可用于此阶段。" : "此阶段的连续绑定尚未确认，不能据此计算指定腕的速度。"}</p>
      <details><summary>测量腕人工来源与原帧证明</summary>
        <p>{show(observation.source)} · {show(observation.version)}</p><p>{show(observation.source_description)}</p>
        <div style={{ maxWidth: "100%", overflowX: "auto" }}><table><thead><tr><th>原帧</th><th>原始 PTS / 时基</th><th>原时钟毫秒</th><th>球员 / 选人段 / 相机段</th></tr></thead><tbody>
          {list(observation.source_frame_bindings).map((value, index) => { const row = objectOf(value); return <tr key={index}><td>{show(row.frame_index)}</td><td>{show(row.pts)} × {show(row.time_base_num)}/{show(row.time_base_den)}</td><td>{show(row.timestamp_ms)}</td><td>{show(row.person_track_id)} / {show(row.selection_epoch)} / {show(row.camera_reference_epoch)}</td></tr>; })}
        </tbody></table></div>
        <details><summary>完整来源记录（包含原始绑定）</summary><pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{JSON.stringify(proof, null, 2)}</pre></details>
      </details>
    </div>}
    {a && <div className="card-manual-fs-evidence">
      <p>步伐卡窗口来源：人工逐卡候选窗口，边界不确定性 ±{show(binding.uncertainty_ms)} 毫秒。不是专家动作、触地或受力真值，也不是自动步伐阶段定位。</p>
      {!evidence?.step_motion && !instance.directional_micro_validation && !instance.micro_cycle_validation && <p>完整步周期未由此窗口确认。需要完整脚序的条件保留缺测，不把人工窗口当作完整周期。</p>}
      <FsStepMotionEvidence instance={instance} />
      <DirectionalMicroEvidence instance={instance} />
      <MicroAdjustmentCyclesEvidence instance={instance} />
      <details><summary>逐卡窗口原帧与来源依据</summary>
        <p>原帧 {show(a.start_source_frame)}–{show(a.end_source_frame)}；{show(binding.source)} · {show(binding.version)}</p>
        <p>保留原始 PTS 和时基，原时钟不按播放器零点改写。选人段与相机段是工程分段，不等于人工身份真值。</p>
        <div style={{ maxWidth: "100%", overflowX: "auto" }}><table><thead><tr><th>原帧</th><th>原始 PTS / 时基</th><th>原时钟毫秒</th><th>球员 / 选人段 / 相机段</th></tr></thead><tbody>
          {list(evidence?.source_frame_bindings).map((value, index) => { const row = objectOf(value); return <tr key={index}><td>{show(row.frame_index)}</td><td>{show(row.pts)} × {show(row.time_base_num)}/{show(row.time_base_den)}</td><td>{show(row.timestamp_ms)}</td><td>{show(row.selected_person_track_id)} / {show(row.selection_epoch)} / {show(row.camera_reference_epoch)}</td></tr>; })}
        </tbody></table></div>
        <details><summary>完整窗口、源质量与绑定记录</summary><pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{JSON.stringify({ annotation: a, evidence, window: instance.source_window_context }, null, 2)}</pre></details>
      </details>
    </div>}
  </>;
}
