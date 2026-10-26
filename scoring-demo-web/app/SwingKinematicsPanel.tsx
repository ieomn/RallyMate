"use client";

import { useEffect, useMemo, useState } from "react";
import type { SwingKinematicsReport } from "./lib/api-types";
import { SWING_METRICS, swingKinematicsOf, swingMetricText, swingSeekDetail, type SwingContext, type SwingEpisode, type SwingView } from "./lib/swing-kinematics";
import "./swing-kinematics.css";

const clock = (ms: number) => `${(ms / 1000).toFixed(2)} 秒`;
type ReadState = { scope: string; value?: unknown; failed?: boolean } | null;

export function SwingKinematicsView({ view, jobId, selectedId, onSelect, loading = false, failed = false, onRetry }: {
  view: SwingView; jobId: string; selectedId: string; onSelect: (id: string) => void; loading?: boolean; failed?: boolean; onRetry?: () => void;
}) {
  const selected = view.episodes.find(episode => episode.episode_id === selectedId) ?? view.episodes[0];
  const seek = (episode: SwingEpisode, timestamp: number | null) => {
    const detail = swingSeekDetail(jobId, episode, timestamp);
    if (detail) window.dispatchEvent(new CustomEvent("rallymate:seek-video", { detail }));
  };
  return <section className="swing-kinematics" aria-label="转体与挥拍" aria-busy={loading}>
    <div className="swing-heading"><div><span>动作测量</span><h3>转体与挥拍</h3></div>{selected && <span>{view.episodes.length} 段动作</span>}</div>
    <p className="swing-description">复用本次已推理的姿态，查看肩、髋和手腕如何衔接；点击峰值时间回看动作。</p>
    {loading ? <p className="swing-status" role="status">正在读取转体与挥拍测量…</p>
      : failed ? <div className="swing-status" role="status">本次测量暂未读取成功。{onRetry && <button type="button" onClick={onRetry}>重新读取</button>}</div>
      : !selected ? <p className="swing-status">{view.status === "rejected" ? "测量与当前视频或动作时段未能对应，暂不展示数值。" : "本次尚无连续的三维动作测量，可先使用上方片段回放。"}</p>
      : <>
        <div className="swing-selection"><label><span>选择测量片段</span><select aria-label="选择转体与挥拍片段" value={selected.episode_id} onChange={event => onSelect(event.target.value)}>
          {view.episodes.map((episode, index) => <option key={episode.episode_id} value={episode.episode_id}>{index + 1}. {episode.label} · {clock(episode.start_ms)} – {clock(episode.end_ms)}</option>)}
        </select></label><button type="button" onClick={() => seek(selected, selected.start_ms)}>定位本段开始 ↗</button></div>
        {selected.phases.length > 0 && <div className="swing-phases" role="group" aria-label="准备与挥拍阶段">{selected.phases.map(phase =>
          <button type="button" key={phase.id} onClick={() => seek(selected, phase.startMs)}><span>{phase.label}</span><time>{clock(phase.startMs)} – {clock(phase.endMs)}</time></button>)}</div>}
        <div className="swing-metrics">{SWING_METRICS.map(spec => {
          const metric = selected.metrics[spec.key], available = metric.status === "estimated";
          return <article key={spec.key}><span className="swing-metric-label">{spec.label}</span><div className="swing-value"><strong>{swingMetricText(metric)}</strong>{available && <span>{spec.displayUnit}</span>}</div>
            {available && metric.timestamp_ms !== null ? <button type="button" aria-label={`定位${spec.label} ${clock(metric.timestamp_ms)}`} onClick={() => seek(selected, metric.timestamp_ms)}><time>{clock(metric.timestamp_ms)}</time> 回看峰值 ↗</button> : <span className="swing-missing">本段暂无有效测量</span>}
          </article>;
        })}</div>
        {Object.values(selected.metrics).every(metric => metric.status === "unavailable") && <p className="swing-status">{selected.reason_zh || "本段没有连续且主体一致的三维测量。"}</p>}
        <p className="swing-scope">{selected.racket_hand ? `${selected.racket_hand === "left" ? "左" : "右"}腕相对髋中心的运动速度，以本段固定躯干长度归一化。` : "本段未确认用于腕速测量的持拍侧。"} 回看准备到挥拍的连续变化，数值不以越大越好判断。</p>
      </>}
    <p className="swing-note">单目三维模型估计；相机旋转未校正，不用于跨视频绝对速度排名，也不表示真实受力、触球时刻或技术评分。</p>
  </section>;
}

export default function SwingKinematicsPanel({ context, initialData, load, signal }: {
  context: SwingContext; initialData?: SwingKinematicsReport; load: (jobId: string, signal?: AbortSignal) => Promise<SwingKinematicsReport>; signal: AbortSignal;
}) {
  const scope = `${context.jobId}:${context.videoSha256}`;
  const [read, setRead] = useState<ReadState>(null), [attempt, setAttempt] = useState(0), [selectedId, setSelectedId] = useState("");
  const current = read?.scope === scope ? read : null;
  const raw = current?.value ?? initialData;
  const view = useMemo(() => swingKinematicsOf(raw, context), [raw, context]);
  const failed = current?.failed === true;
  const loading = raw === undefined && !failed;
  useEffect(() => {
    if (initialData !== undefined && attempt === 0) return;
    const controller = new AbortController(), readSignal = AbortSignal.any([signal, controller.signal]);
    void load(context.jobId, readSignal).then(value => { if (!readSignal.aborted) setRead({ scope, value }); })
      .catch(() => { if (!readSignal.aborted) setRead({ scope, failed: true }); });
    return () => controller.abort();
  }, [attempt, context.jobId, initialData, load, scope, signal]);
  useEffect(() => {
    const onSeek = (event: Event) => {
      const detail = (event as CustomEvent<{ jobId?: string; timestampMs?: number }>).detail;
      if (detail?.jobId !== context.jobId || !Number.isFinite(detail.timestampMs)) return;
      // Overlapping windows are common: retain the current selection if it fits.
      const selected = view.episodes.find(episode => episode.episode_id === selectedId);
      if (selected && swingSeekDetail(context.jobId, selected, detail.timestampMs)) return;
      const match = view.episodes.find(episode => swingSeekDetail(context.jobId, episode, detail.timestampMs));
      if (match) setSelectedId(match.episode_id);
    };
    window.addEventListener("rallymate:seek-video", onSeek);
    return () => window.removeEventListener("rallymate:seek-video", onSeek);
  }, [context.jobId, selectedId, view.episodes]);
  return <SwingKinematicsView view={view} jobId={context.jobId} selectedId={selectedId} onSelect={setSelectedId} loading={loading} failed={failed}
    onRetry={() => { setRead(null); setAttempt(value => value + 1); }} />;
}
