"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { createApiClient } from "./lib/api-client";
import MotionReplayComparison from "./MotionReplayComparison";
import { motionReplayInterval, motionReplayIsActive, motionReplayPollDelay, motionReplayStatusText, type MotionReplayTask } from "./lib/motion-replay";

export function MotionReplayCard({ task, mediaBase = "", retrying = false, onRetry }: { task: MotionReplayTask; mediaBase?: string; retrying?: boolean; onRetry?: () => void }) {
  const mediaUrl = (path: string | null) => path ? `${mediaBase.replace(/\/+$/, "")}${path}` : undefined;
  const original = mediaUrl(task.media.original_video);
  const reference = mediaUrl(task.media.reference_video);
  const generated = task.generation_status === "completed" ? mediaUrl(task.media.generated_video) : undefined;
  const active = motionReplayIsActive(task);
  const unknown = task.generation_status === "submit_unknown";
  const qualityLabel = task.quality_status === "approved" ? "AI 生成 · 已核验" : task.quality_status === "rejected" ? "AI 生成 · 未通过核验" : "AI 生成 · 动作待核验";
  return <article className="motion-replay-task" data-status={task.generation_status}>
    <div className="motion-replay-task-meta"><span>原视频 {motionReplayInterval(task)}</span><span className={`motion-replay-status${active ? " is-active" : ""}`} role="status">{motionReplayStatusText(task)}</span></div>
    <MotionReplayComparison duration={task.source_window.duration_ms / 1000} sourceStart={task.source_window.start_ms / 1000} sources={[
      { id: "original", title: "原视频", label: "同一动作片段", src: original, caption: "真实拍摄画面，作为动作与时序的对照依据。", placeholder: "原视频片段暂不可用" },
      { id: "reference", title: "你的轨迹白模", label: "来自原视频关键点", src: reference, poster: mediaUrl(task.media.first_frame), caption: "二维关键点经固定等比缩放，重点对照姿态与发生时间；受遮挡和定位误差影响，不代表真实三维动作。", placeholder: task.generation_status === "queued" ? "等待制作轨迹白模" : task.generation_status === "rendering" ? "正在整理你的动作" : "轨迹白模暂不可用", placeholderDetail: "保留个人动作的二维位置与时间关系。" },
      { id: "generated", title: "MiniMax 动作回放", label: qualityLabel, src: generated, caption: "AI 可能改变动作细节。不将生成画面作为测量或技术评分依据。", placeholder: unknown ? "正在核对提交结果" : task.generation_status === "failed" ? "生成暂未完成" : active ? "个人动作视频正在后台制作" : "生成视频暂不可用", placeholderDetail: unknown ? "为避免重复生成，请先刷新状态；当前不能直接重试。" : active ? "原视频与轨迹白模可先对照，完成后会在这里显示。" : "已有轨迹白模仍可继续回放。" },
    ]} />
    {task.error && <p className="motion-replay-error" role="status">{task.error.message}</p>}
    <div className="motion-replay-task-footer"><p>{active ? "任务已保存，可以离开页面，稍后回来查看。" : unknown ? "任务已保存，待服务确认提交结果。" : "回放记录保存在本次训练中，刷新后仍可查看。"}</p>{task.retry_allowed && !unknown && <button type="button" className="product-secondary" disabled={retrying || !onRetry} onClick={onRetry}>{retrying ? "正在重试…" : "重试生成"}</button>}</div>
  </article>;
}

export default function MotionReplayPanel({ jobId, enabled }: { jobId?: string; enabled: boolean }) {
  // Changing jobs or disabling the feature destroys all request/form state.
  return enabled && jobId ? <MotionReplaySession key={jobId} jobId={jobId} /> : null;
}

function MotionReplaySession({ jobId }: { jobId: string }) {
  const api = useMemo(() => createApiClient(), []);
  const [state, setState] = useState<{ jobId?: string; items: MotionReplayTask[]; loaded: boolean; error: string }>({ items: [], loaded: false, error: "" });
  const [selectedId, setSelectedId] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [mutation, setMutation] = useState<"create" | "retry" | null>(null);
  const [actionError, setActionError] = useState("");
  const [startSeconds, setStartSeconds] = useState("");
  const mutationAbort = useRef<AbortController | null>(null);
  const items = state.jobId === jobId ? state.items : [];
  const loaded = state.jobId === jobId && state.loaded;
  const current = items.find(item => item.id === selectedId) ?? items[0];

  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let failures = 0;
    const startedAt = Date.now();
    let latestItems: MotionReplayTask[] = [];
    const read = async () => {
      try {
        const response = await api.listMotionReplays(jobId, controller.signal);
        if (controller.signal.aborted) return;
        failures = 0;
        latestItems = response.items;
        setState({ jobId, items: response.items, loaded: true, error: "" });
      } catch (error) {
        if (controller.signal.aborted) return;
        failures++;
        setState(previous => ({ jobId, items: previous.jobId === jobId ? previous.items : [], loaded: true, error: error instanceof Error ? error.message : "回放记录暂时无法读取。" }));
      }
      const delay = motionReplayPollDelay(latestItems, Date.now() - startedAt, failures);
      if (delay !== null && !controller.signal.aborted) timer = setTimeout(() => void read(), delay);
    };
    void read();
    return () => { controller.abort(); if (timer) clearTimeout(timer); };
  }, [api, jobId, refresh]);

  useEffect(() => () => mutationAbort.current?.abort(), []);

  async function mutate(kind: "create" | "retry") {
    if (mutation || (kind === "retry" && (!current?.retry_allowed || current.generation_status === "submit_unknown"))) return;
    const start = startSeconds.trim() === "" ? undefined : Number(startSeconds);
    if (kind === "create" && start !== undefined && (!Number.isFinite(start) || start < 0 || !Number.isSafeInteger(Math.round(start * 1000)))) {
      setActionError("请输入有效的片段起点秒数。"); return;
    }
    const controller = new AbortController();
    mutationAbort.current?.abort(); mutationAbort.current = controller;
    setMutation(kind); setActionError("");
    try {
      const task = kind === "retry"
        ? await api.retryMotionReplay(current!.id, controller.signal)
        : await api.createMotionReplay(jobId, { duration_ms: 6000, ...(start === undefined ? {} : { start_ms: Math.round(start * 1000) }) }, controller.signal);
      if (controller.signal.aborted) return;
      if (task.job_id !== jobId) throw new Error("回放任务与当前训练不匹配，请刷新状态。");
      setState(previous => ({ jobId, items: [task, ...(previous.jobId === jobId ? previous.items.filter(item => item.id !== task.id) : [])], loaded: true, error: "" }));
      setSelectedId(task.id);
    } catch (error) {
      if (controller.signal.aborted) return;
      setActionError(error instanceof Error ? error.message : "请求结果暂未确认，请刷新状态查看已保存的任务。");
    } finally {
      if (!controller.signal.aborted) { setMutation(null); setRefresh(value => value + 1); }
    }
  }

  return <section className="motion-replay-panel" id="motion-replay" aria-labelledby="motion-replay-title">
    <div className="motion-replay-heading"><div><span className="motion-replay-eyebrow">PERSONAL MOTION</span><h2 id="motion-replay-title">纯净动作回放</h2><p>把原视频、轨迹白模和 AI 生成画面放在同一时间轴上，直接对照动作差异。</p></div><button type="button" className="product-secondary" onClick={() => setRefresh(value => value + 1)} disabled={Boolean(mutation)}>刷新状态</button></div>
    <form className="motion-replay-create" onSubmit={event => { event.preventDefault(); void mutate("create"); }}>
      <div><strong>选取一个 6 秒片段</strong><p>默认自动选择可用动作；也可以指定原视频起点。</p></div>
      <label>起点（秒，可选）<input type="number" min="0" step="0.1" placeholder="自动选取" value={startSeconds} onChange={event => setStartSeconds(event.target.value)} disabled={Boolean(mutation)} /></label>
      <button type="submit" className="product-primary" disabled={!loaded || Boolean(mutation)}>{mutation === "create" ? "正在创建…" : "创建纯净回放"}</button>
    </form>
    {(actionError || (state.jobId === jobId && state.error)) && <p className="motion-replay-error" role="status">{actionError || state.error}</p>}
    {items.length > 1 && <label className="motion-replay-history">已保存的回放<select value={current?.id ?? ""} onChange={event => setSelectedId(event.target.value)}>{items.map((task, index) => <option value={task.id} key={task.id}>片段 {items.length - index} · {motionReplayInterval(task)} · {motionReplayStatusText(task)}</option>)}</select></label>}
    {current ? <MotionReplayCard key={current.id} task={current} mediaBase={api.config.baseUrl} retrying={Boolean(mutation)} onRetry={() => void mutate("retry")} /> : <div className="motion-replay-empty" role="status"><span aria-hidden="true">◯</span><p>{!loaded ? "正在读取已保存的回放…" : "创建后会在后台持续处理，刷新或离开页面不会丢失任务。"}</p></div>}
  </section>;
}
