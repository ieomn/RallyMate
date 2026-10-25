"use client";

import { useEffect, useState, useSyncExternalStore } from "react";
import type { CandidateReplayHandle } from "./BallTrajectoryViewer";
import { footCrossingIdentityKey, type FootCrossingCandidate, type FootCrossingResponse } from "./lib/foot-crossing-candidates";

export type FootCrossingPanelContext = {
  jobId: string;
  sessionKey: string;
  signal: AbortSignal;
  load: (signal: AbortSignal) => Promise<FootCrossingResponse>;
  replay: CandidateReplayHandle | null;
};

type PanelState = {
  open: boolean;
  phase: "closed" | "offline" | "loading" | "ready" | "error";
  response: FootCrossingResponse | null;
  selected: number;
  message: string;
};
const closedState = (): PanelState => ({ open: false, phase: "closed", response: null, selected: 0, message: "" });
const sameSession = (a?: FootCrossingPanelContext, b?: FootCrossingPanelContext) => a?.jobId === b?.jobId
  && a?.sessionKey === b?.sessionKey && a?.signal === b?.signal;

export function footCrossingCandidateKey(candidate: FootCrossingCandidate): string {
  return JSON.stringify([candidate.source_frames, candidate.processed_indexes,
    candidate.source_bindings.map(binding => [binding.pts, binding.time_base_num, binding.time_base_den])]);
}

export function footCrossingParentSourceNotes(candidate: FootCrossingCandidate): string[] {
  const counts = candidate.parent_source_association_counts;
  const notes: string[] = [];
  if (counts.weak_source_track_association) notes.push(`${counts.weak_source_track_association} 个父事件仅弱来源关联；局部疑点供复核，未完成双帧来源验证。`);
  if (counts.mismatch) notes.push(`${counts.mismatch} 个父事件来源不一致，已排除其否决和疑点判定作用；当前候选仍保留。`);
  if (counts.unverified) notes.push(`${counts.unverified} 个父事件缺少可用来源关联，未用于否决或疑点判定。`);
  if (counts.pair_source_bindings_verified) notes.push(`${counts.pair_source_bindings_verified} 个父事件的双帧来源绑定核对一致；这不证明实际身份或动作正确。`);
  return notes;
}

export function canReplayFootCrossing(context: FootCrossingPanelContext | undefined,
  response: FootCrossingResponse | null, candidate: FootCrossingCandidate | undefined): boolean {
  if (!context || context.signal.aborted || !context.replay || response?.status !== "available" || !candidate) return false;
  const binding = context.replay.binding;
  return response.job_id === context.jobId && binding.jobId === context.jobId
    && binding.sessionKey === context.sessionKey && binding.mappingVerified === true
    && binding.originMs !== null && Number.isFinite(binding.originMs)
    && binding.videoSha256 === response.identity.video_sha256
    && candidate.source_bindings.every(source => source.video_sha256 === response.identity.video_sha256)
    && Number.isFinite(candidate.source_bindings[0].video_timestamp_ms);
}

/** The UI and state tests share this request lifecycle; no cross-session cache. */
export function createFootCrossingPanelController(initialContext?: FootCrossingPanelContext) {
  let context: FootCrossingPanelContext | undefined;
  let state = closedState();
  let generation = 0;
  let request: AbortController | null = null;
  let seekRequest: AbortController | null = null;
  const listeners = new Set<() => void>();
  const publish = (next: PanelState) => { state = next; listeners.forEach(listener => listener()); };
  const cancelSeek = () => { seekRequest?.abort(); seekRequest = null; };
  const cancelRead = () => { generation += 1; request?.abort(); request = null; cancelSeek(); };
  const close = () => { cancelRead(); publish(closedState()); };
  const sessionAborted = () => close();
  const setContext = (next?: FootCrossingPanelContext) => {
    if (!sameSession(context, next)) {
      context?.signal.removeEventListener("abort", sessionAborted);
      close();
      context = next;
      context?.signal.addEventListener("abort", sessionAborted, { once: true });
    } else {
      if (context?.replay !== next?.replay) cancelSeek();
      context = next;
    }
  };
  const open = async () => {
    if (state.open) return;
    cancelRead();
    if (!context) { publish({ ...closedState(), open: true, phase: "offline" }); return; }
    if (context.signal.aborted) {
      publish({ ...closedState(), open: true, phase: "error", message: "报告已切换，请展开当前在线报告的依据。" });
      return;
    }
    const started = context;
    const currentGeneration = generation;
    const controller = new AbortController();
    request = controller;
    publish({ ...closedState(), open: true, phase: "loading" });
    const current = () => generation === currentGeneration && !controller.signal.aborted
      && !started.signal.aborted && sameSession(context, started) && state.open;
    try {
      const response = await started.load(controller.signal);
      if (!current()) return; // A transport may resolve even after abort.
      if (response.job_id !== started.jobId) throw new Error("response_job_mismatch");
      cancelSeek();
      publish({ open: true, phase: "ready", response, selected: 0, message: "" });
    } catch {
      if (current()) publish({ ...closedState(), open: true, phase: "error", message: "暂时无法读取这份计算。关闭后可重新展开读取。" });
    } finally {
      if (request === controller) request = null;
    }
  };
  const select = (index: number) => {
    cancelSeek();
    if (state.response?.status !== "available" || !Number.isInteger(index)
        || index < 0 || index >= state.response.candidates.length) return;
    publish({ ...state, selected: index, message: "" });
  };
  const seek = () => {
    cancelSeek();
    const response = state.response;
    const candidate = response?.status === "available" ? response.candidates[state.selected] : undefined;
    if (!state.open || !canReplayFootCrossing(context, response, candidate)
        || !context?.replay || response?.status !== "available" || !candidate) return;
    const controller = new AbortController();
    seekRequest = controller;
    try {
      const result = context.replay.seek({ jobId: context.jobId, sessionKey: context.sessionKey,
        identityKey: footCrossingIdentityKey(response.identity), candidateKey: footCrossingCandidateKey(candidate),
        videoSha256: response.identity.video_sha256, videoTimestampMs: candidate.source_bindings[0].video_timestamp_ms }, controller.signal);
      publish({ ...state, message: result.status === "rejected" ? "当前媒体无法可靠定位到此原帧。"
        : result.status === "pending" ? "等待当前回放就绪；切换候选或收起依据会取消定位。"
          : "已定位到该区间起点。" });
    } catch {
      cancelSeek();
      publish({ ...state, message: "当前媒体无法可靠定位到此原帧。" });
    }
  };
  const dispose = () => { context?.signal.removeEventListener("abort", sessionAborted); context = undefined; close(); };
  setContext(initialContext);
  return { getSnapshot: () => state, subscribe: (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener); }; },
    setContext, open, close, select, seek, dispose };
}

const unavailableText: Record<string, string> = {
  not_computed: "这份任务未保存此独立计算，不能据此判断有无交叉步。",
  artifact_unavailable: "已登记的候选文件暂时不可读取。",
  binding_conflict: "候选来源与当前报告不一致，已停止展示。",
  unsupported_version: "这份候选的版本暂不受支持。",
  invalid_artifact: "候选文件未通过完整性核对，已停止展示。",
  limit_exceeded: "候选文件超出按需读取上限，暂不展示。",
};
/** Display the saved coverage only; excluded pairs never imply a negative finding. */
export function FootCrossingCoverageSummary({ response }: {
  response: Extract<FootCrossingResponse, { status: "available" }>;
}) {
  const counts = response.counts;
  const assessablePairs = counts.candidates + counts.no_exchange;
  const excludedReasons = Object.entries(counts.reasons).filter(([reason, count]) => count > 0
    && reason !== "strict_projected_order_exchange" && reason !== "same_projected_order");
  return <>
    <p>帧数 {counts.frames} · 相邻帧对 {counts.adjacent_sequence_pairs} 对 · 可判定帧对 {assessablePairs} 对
      （候选 {counts.candidates} 对 + 未换序 {counts.no_exchange} 对）· 排除 {counts.excluded} 对。</p>
    <p>已保存 {response.candidates.length} 条 / 原始候选 {counts.candidates} 条
      {response.output_limits.candidates_truncated ? "（按原帧顺序截断）" : ""}。计数不是准确率或实际步数。</p>
    {assessablePairs === 0
      ? <p role="status">本次无法判断图像换序：{counts.adjacent_sequence_pairs === 0 ? "没有相邻帧对。" : "所有相邻帧对均被排除。"}不能据此认定没有实际交叉步。</p>
      : counts.candidates === 0
        ? <p>可判定部分未发现换序候选；不能据此认定没有实际交叉步。</p>
        : response.candidates.length === 0 && <p>此工件未保存可展开的候选明细。</p>}
    {counts.excluded > 0 && <>
      <p>排除部分未参与图像换序判断。</p>
      <p>排除明细保存上限为 {response.output_limits.exclusion_limit} 条，
        {response.output_limits.exclusions_truncated ? "已按原帧顺序截断" : "未截断"}。
        排除总数和下方原因汇总涵盖全部排除帧对，不是已保存明细条数。</p>
      <ul aria-label="排除原因">{excludedReasons.map(([reason, count]) => <li key={reason}>
        {reason === "source_time_binding_unavailable" ? "原帧与视频时间绑定不可用" : "其他排除原因"}
        （{reason}）：{count} 对。
      </li>)}</ul>
    </>}
  </>;
}

const decimal = (value: number, digits = 3) => Number(value.toFixed(digits)).toString();
const jointNames: Record<string, string> = { left_hip: "左髋", right_hip: "右髋", left_ankle: "左踝", right_ankle: "右踝" };

export default function FootCrossingCandidatesPanel({ context }: { context?: FootCrossingPanelContext }) {
  const [controller] = useState(() => createFootCrossingPanelController());
  const state = useSyncExternalStore(controller.subscribe, controller.getSnapshot, controller.getSnapshot);
  useEffect(() => { controller.setContext(context); }, [controller, context]);
  useEffect(() => () => controller.dispose(), [controller]);
  const response = state.response;
  const available = response?.status === "available" ? response : null;
  const candidate = available?.candidates[state.selected];
  const jumps = candidate ? Object.entries(candidate.local_joint_diagnostics.keypoint_jump_candidate_frames_by_joint) : [];
  const swaps = candidate?.local_joint_diagnostics.left_right_swap_candidate_joint_pairs ?? [];
  const toggle = () => { controller.setContext(context); if (controller.getSnapshot().open) controller.close(); else void controller.open(); };

  return <section className="motion-analysis-panel" aria-label="独立图像换序线索">
    <button type="button" aria-expanded={state.open} onClick={toggle}>{state.open ? "收起" : "查看"}独立图像换序线索</button>
    {!context && <p className="motion-analysis-note">查看此依据需要打开对应的在线报告。</p>}
    {state.open && <div>
      <p className="motion-analysis-note">两帧内同一图像轴上的脚踝顺序变化；不确认正式交叉步，不产生等级或成绩。</p>
      {state.phase === "loading" && <p role="status">正在按需读取候选依据…</p>}
      {state.phase === "offline" && <p>离线报告没有这份独立候选文件。</p>}
      {state.phase === "error" && <p role="status">{state.message}</p>}
      {response && response.status !== "available" && <p role="status">{unavailableText[response.status] ?? "此独立计算暂不可用。"}</p>}
      {available && <>
        <p className="motion-analysis-note" style={{ overflowWrap: "anywhere" }}>当前任务 {available.job_id} · 来源任务 {available.identity.source_job_id}<br />
          工件 {available.identity.artifact_sha256.slice(0, 12)} · {available.identity.algorithm_version}</p>
        <FootCrossingCoverageSummary response={available} />
        {candidate && <>
          <label>查看区间 <select value={state.selected} onChange={event => controller.select(Number(event.target.value))}>
            {available.candidates.map((row, index) => <option key={footCrossingCandidateKey(row)} value={index}>原帧 {row.source_frames[0]} → {row.source_frames[1]}</option>)}
          </select></label>
          <p>原帧 {candidate.source_frames.join(" → ")} · 源时间 {candidate.source_interval_ms.map(time => decimal(time.approx_ms, 6)).join(" → ")} 毫秒<br />
            原生 PTS {candidate.source_bindings.map(binding => binding.pts).join(" → ")}，时间基 {candidate.source_bindings[0].time_base_num}/{candidate.source_bindings[0].time_base_den} 秒。</p>
          <p><strong>同轴前后：{candidate.signed_ankle_order_px.map(value => decimal(value)).join(" → ")} 像素</strong><br />
            轴取这两帧髋中点在同一相机参考图像中的位移，全程固定；方向 ({candidate.axis.unit_vector?.map(value => decimal(value, 5)).join(", ") ?? "不可用"})，位移 {decimal(candidate.axis.hip_displacement_length_px)} 像素。方向可靠性仍未知。</p>
          <p>{candidate.candidate_quality_status === "suspect" ? "有局部疑点，保留候选供核对。" : "局部风险未知；未提供异常标记不代表没有异常。"}
            {footCrossingParentSourceNotes(candidate).map(note => <span key={note}> {note}</span>)}
            {candidate.local_joint_diagnostics.assessment_status === "sam_confidence_diagnostics_not_assessed"
              && " SAM 置信度、可见性及这些局部诊断未评估。"}
            {jumps.map(([joint, frames]) => <span key={joint}> {jointNames[joint] ?? joint}跳变疑点：原帧 {frames.join("、")}。</span>)}
            {swaps.map((swap, index) => <span key={index}> {jointNames[swap.left_joint] ?? swap.left_joint}/{jointNames[swap.right_joint] ?? swap.right_joint}互换疑点：原帧 {swap.frames.join("、")}。</span>)}
            侧视遮挡、解剖左右和连续轨迹交叉均未独立确认。</p>
          <button type="button" disabled={!canReplayFootCrossing(context, available, candidate)} onClick={controller.seek}>
            {context?.replay?.binding.kind === "annotated" ? "定位标注回放" : "定位原视频"}
          </button>
          {!canReplayFootCrossing(context, available, candidate) && <p className="motion-analysis-note">当前媒体的时间映射尚未验证，或来源与本报告不一致，暂不可定位。</p>}
          {state.message && <p role="status">{state.message}</p>}
        </>}
      </>}
    </div>}
  </section>;
}
