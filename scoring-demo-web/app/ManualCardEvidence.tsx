import { handRacketAuxiliarySummary, type CardInstance } from "./lib/card-execution";
import { objectOf, validRacketSide } from "./lib/manual-card-evidence";
import FsStepMotionEvidence from "./FsStepMotionEvidence";
import MicroAdjustmentCyclesEvidence from "./MicroAdjustmentCyclesEvidence";
import DirectionalMicroEvidence from "./DirectionalMicroEvidence";
import { isRecoveryCard, gsRecoverySummary, validateGsRecovery } from "./lib/gs-recovery-evidence";

const list = (value: unknown): unknown[] => Array.isArray(value) ? value : [];
const show = (value: unknown) => typeof value === "number" && Number.isFinite(value) ? value.toLocaleString("zh-CN", { maximumFractionDigits: 3 }) : typeof value === "string" ? value : "未核验";
const wrist = (value: unknown) => value === "left" ? "左腕" : value === "right" ? "右腕" : "侧别未确认";

export default function ManualCardEvidence({ instance }: { instance: CardInstance }) {
  const proof = instance.racket_side_evidence, a = instance.source_window_annotation, evidence = instance.source_window_evidence;
  const observation = proof?.observation, validSide = proof && validRacketSide(proof, instance, instance.source_stage);
  const binding = objectOf(a?.binding);
  const recovery = validateGsRecovery(instance.recovery_evidence, instance.recovery_parent_context, instance);
  const recoveryRaw = objectOf(instance.recovery_evidence), referenceRaw = objectOf(recoveryRaw.reference), referenceBinding = objectOf(referenceRaw.binding);
  return <>
    {instance.scoring_revision_id === "gs01-m09-hand-racket-observability-v1.0.0" && <div className="card-hand-racket-evidence">
      <p>{handRacketAuxiliarySummary(instance)}</p>
      <details><summary>双腕辅助观测与规则修订记录</summary><pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{JSON.stringify({
        scoring_revision_id: instance.scoring_revision_id, missing_required_observables: instance.missing_required_observables,
        auxiliary_observations: instance.auxiliary_observations, superseded_conditions: instance.superseded_conditions,
        superseded_condition_evidence: instance.superseded_condition_evidence,
      }, null, 2)}</pre></details>
    </div>}
    {isRecoveryCard(instance.indicator_id) && <div className="card-gs-recovery-evidence">
      <p>{gsRecoverySummary(recovery, instance.indicator_id)}</p>
      {recovery?.status === "measured_recovery" && recovery.stage && <>
        <p>{recovery.stage.source_frames.length}/{recovery.stage.source_frames.length} 原帧所需关节共同有效；完整窗口固定躯干尺度 {show(recovery.stage.fixed_torso_scale)} 参考图像单位；起末端各 {show(recovery.endpoint_count)} 帧（20%，至少3帧）。</p>
        {recovery.reference && <p>独立准备参照：动作开始前的原帧 {show(recovery.reference.source_frames[0])}–{show(recovery.reference.source_frames.at(-1))}；人工观察不确定性 ±{show(referenceBinding.uncertainty_ms)} 毫秒；{show(referenceBinding.source)}。参照至恢复末帧的连续原来源账本同时核验。</p>}
        <details><summary>身体恢复原帧、原始时钟与独立复算</summary>
          <p>肩髋角是补偿参考平面的 180° 周期轴角，不是三维解剖旋转；参考坐标不再重复应用相机变换。速度对应本帧至下一原帧，末帧无速度。</p>
          <div style={{ maxWidth: "100%", overflowX: "auto" }}><table><thead><tr><th>原帧</th><th>原始 PTS / 时基</th><th>原时钟毫秒</th><th>肩轴 / 髋轴（度）</th><th>髋至下一帧速度（躯干/秒）</th><th>躯干倾角（度）</th></tr></thead><tbody>
            {recovery.stage.source_frames.map((frame, index) => { const b = objectOf(list(objectOf(recoveryRaw.stage).source_frame_bindings)[index]); return <tr key={frame}>
              <td>{frame}</td><td>{show(b.pts)} × {show(b.time_base_num)}/{show(b.time_base_den)}</td><td>{show(recovery.stage!.source_times_ms[index])}</td>
              <td>{show(recovery.stage!.axis_degrees.shoulder[index])} / {show(recovery.stage!.axis_degrees.hip[index])}</td>
              <td>{index < recovery.stage!.hip_speeds_torso_s.length ? show(recovery.stage!.hip_speeds_torso_s[index]) : "—"}</td><td>{show(recovery.stage!.trunk_tilt_degrees[index])}</td></tr>; })}
          </tbody></table></div>
        </details>
      </>}
      <details><summary>完整恢复来源、辅助观测与历史规则记录（未经验证的输入记录）</summary>
        <p>下列原始记录用于追溯。腕距、辅助标记和历史条件不授权新成绩；有效数值仅由当前规则独立复算后展示。</p>
        <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{JSON.stringify({ scoring_revision_id: instance.scoring_revision_id,
          missing_required_observables: instance.missing_required_observables, recovery_evidence: instance.recovery_evidence,
          recovery_parent_context: instance.recovery_parent_context, validation_reason_codes: recovery?.reason_codes ?? [],
          auxiliary_observations: instance.auxiliary_observations, superseded_conditions: instance.superseded_conditions,
          superseded_condition_evidence: instance.superseded_condition_evidence }, null, 2)}</pre>
      </details>
    </div>}
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
