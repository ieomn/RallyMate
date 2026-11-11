"use client";

import { useEffect, useEffectEvent, useState } from "react";
import type { MotionFamily, MotionEpisode } from "./lib/api-types";
import { MOTION_METRICS, motionMetricText } from "./lib/motion-analysis";
import RotationAnalysisPanel from "./RotationAnalysisPanel";

const seconds = (value: number) => (value / 1000).toFixed(2);

export default function MotionAnalysisPanel({ familyLabel, data, jobId, pending, loadEpisode }: { loadEpisode?: (episode: MotionEpisode, signal: AbortSignal) => Promise<MotionEpisode>; familyLabel: string; data?: MotionFamily; jobId?: string; pending: boolean }) {
  const [selectedId, setSelectedId] = useState("");
  const episodes = data?.episodes ?? [];
  const selectedIndex = Math.max(0, episodes.findIndex(episode => episode.episode_id === selectedId));
  const selected = episodes[selectedIndex];
  const [read, setRead] = useState<{ id: string; jobId?: string; loader: typeof loadEpisode; attempt: number; episode?: MotionEpisode; error?: string } | null>(null);
  const [attempt, setAttempt] = useState(0);
  const episodeId = selected?.episode_id;
  // Overview refreshes create new objects for the same source-bound episode.
  // Read the latest payload when its identity changes without restarting for
  // an otherwise identical overview object.
  const readSelectedEpisode = useEffectEvent((signal: AbortSignal) => selected && loadEpisode ? loadEpisode(selected, signal) : null);
  useEffect(() => {
    if (!episodeId || !loadEpisode) return;
    const controller = new AbortController();
    const request = readSelectedEpisode(controller.signal);
    if (!request) return () => controller.abort();
    const identity = { id: episodeId, jobId, loader: loadEpisode, attempt };
    void request.then(episode => {
      if (!controller.signal.aborted) setRead({ ...identity, episode });
    }).catch(error => { if (!controller.signal.aborted) setRead({ ...identity, error: error instanceof Error ? error.message : "当前片段测量暂未读取成功。" }); });
    return () => controller.abort();
  }, [episodeId, jobId, loadEpisode, attempt]);
  const current = read && read.id === episodeId && read.jobId === jobId && read.loader === loadEpisode && read.attempt === attempt ? read : null;
  const episode = loadEpisode ? current?.episode ?? selected : selected;
  const loading = Boolean(loadEpisode && selected && !current?.episode && !current?.error);
  const seek = (timestampMs: number) => window.dispatchEvent(new CustomEvent("rallymate:seek-video", { detail: { timestampMs, jobId, preferOriginal: true } }));

  return <section className="motion-analysis-panel" aria-label={`${familyLabel}运动分析`}>
    <div className="motion-analysis-heading"><h3>{familyLabel} · 当前视频</h3><span>{episode ? "运动测量 · 规则参考" : pending ? "分析中" : "—"}</span></div>
    {episode ? <>
      <div className="motion-episode-heading"><div><strong>{episode.classification.label_zh}</strong><p>{seconds(episode.start_ms)}–{seconds(episode.end_ms)} 秒</p></div><button type="button" onClick={() => seek(episode.start_ms)}>定位回放 ↗</button></div>
      {loading && <p role="status">正在按需读取当前片段的测量与转体依据…</p>}
      {current?.error && <div role="alert"><p>{current.error}</p><button type="button" onClick={() => setAttempt(value => value + 1)}>重试当前运动片段</button></div>}
      <p className="motion-phase-disclosure">动作阶段按二维运动变化估计。</p>
      <div className="motion-phases" aria-label="估计动作阶段">{episode.phases.map(phase => {
        const start = phase.start_ms, end = phase.end_ms;
        const available = phase.status !== "unavailable" && start !== null && end !== null && Number.isFinite(start) && Number.isFinite(end) && end > start;
        return <button type="button" key={phase.phase} disabled={!available} onClick={() => { if (available && start !== null) seek(start); }}><span>{phase.label_zh}</span><small>{available && start !== null && end !== null ? `${seconds(start)}–${seconds(end)}s · 估计` : "—"}</small></button>;
      })}</div>
      <div className="motion-metrics">{MOTION_METRICS.filter(metric => !episode.rotation_analysis || metric.key !== "shoulder_line_change_deg").map(metric => <article key={metric.key}><span>{metric.label}</span><strong>{motionMetricText(episode.metrics[metric.key])}</strong><small>{metric.unit}</small></article>)}</div>
      <RotationAnalysisPanel episode={episode} seek={seek} />
      <div className="motion-episode-nav"><button type="button" disabled={selectedIndex === 0} onClick={() => setSelectedId(episodes[selectedIndex - 1].episode_id)}>← 上一片段</button><span>逐段查看</span><button type="button" disabled={selectedIndex === episodes.length - 1} onClick={() => setSelectedId(episodes[selectedIndex + 1].episode_id)}>下一片段 →</button></div>
      <details className="motion-evidence"><summary>查看测量依据</summary><p>有效姿态采样 {episode.evidence?.pose_samples ?? "—"} 帧 · 关联球拍 {episode.evidence?.racket_associated_frames ?? "—"} 帧</p></details>
    </> : <div className="motion-empty"><strong>{pending ? "正在分析动作阶段与运动指标" : "—"}</strong>{pending && <p>完成后会显示动作区间与测量结果。</p>}</div>}
  </section>;
}
