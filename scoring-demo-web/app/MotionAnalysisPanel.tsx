"use client";

import { useState } from "react";
import type { MotionFamily } from "./lib/api-types";
import { MOTION_METRICS, motionMetricText } from "./lib/motion-analysis";
import RotationAnalysisPanel from "./RotationAnalysisPanel";

const seconds = (value: number) => (value / 1000).toFixed(2);

export default function MotionAnalysisPanel({ familyLabel, data, jobId, pending }: { familyLabel: string; data?: MotionFamily; jobId?: string; pending: boolean }) {
  const [selectedId, setSelectedId] = useState("");
  const episodes = data?.episodes ?? [];
  const selectedIndex = Math.max(0, episodes.findIndex(episode => episode.episode_id === selectedId));
  const episode = episodes[selectedIndex];
  const seek = (timestampMs: number) => window.dispatchEvent(new CustomEvent("rallymate:seek-video", { detail: { timestampMs, jobId } }));

  return <section className="motion-analysis-panel" aria-label={`${familyLabel}运动分析`}>
    <div className="motion-analysis-heading"><h3>{familyLabel} · 当前视频</h3><span>{episode ? "运动测量 · 规则参考" : pending ? "分析中" : "证据说明"}</span></div>
    {episode ? <>
      <div className="motion-episode-heading"><div><strong>{episode.classification.label_zh}</strong><p>{seconds(episode.start_ms)}–{seconds(episode.end_ms)} 秒 · 触球未确认</p></div><button type="button" onClick={() => seek(episode.start_ms)}>定位回放 ↗</button></div>
      <p className="motion-analysis-reason">{episode.classification.reason_zh}</p>
      <p className="motion-phase-disclosure">{episode.analysis_status === "partial" ? "阶段证据不完整 · " : ""}阶段边界按二维运动变化估计，不代表触球时刻。</p>
      <div className="motion-phases" aria-label="估计动作阶段">{episode.phases.map(phase => {
        const start = phase.start_ms, end = phase.end_ms;
        const available = phase.status !== "unavailable" && start !== null && end !== null && Number.isFinite(start) && Number.isFinite(end) && end > start;
        return <button type="button" key={phase.phase} disabled={!available} onClick={() => { if (available && start !== null) seek(start); }}><span>{phase.label_zh}{available ? "" : "证据不足"}</span><small>{available && start !== null && end !== null ? `${seconds(start)}–${seconds(end)}s · 估计` : "未定位阶段"}</small></button>;
      })}</div>
      <div className="motion-metrics">{MOTION_METRICS.filter(metric => !episode.rotation_analysis || metric.key !== "shoulder_line_change_deg").map(metric => <article key={metric.key}><span>{metric.label}</span><strong>{motionMetricText(episode.metrics[metric.key])}</strong><small>{metric.unit}</small></article>)}</div>
      <RotationAnalysisPanel episode={episode} seek={seek} />
      <div className="motion-episode-nav"><button type="button" disabled={selectedIndex === 0} onClick={() => setSelectedId(episodes[selectedIndex - 1].episode_id)}>← 上一片段</button><span>逐段查看</span><button type="button" disabled={selectedIndex === episodes.length - 1} onClick={() => setSelectedId(episodes[selectedIndex + 1].episode_id)}>下一片段 →</button></div>
      <p className="motion-analysis-note">以上是图像中的二维运动测量。正反手等类型属于规则推断；运动片段不等于已确认击球，不作为技术评分。</p>
      <details className="motion-evidence"><summary>查看测量依据与限制</summary><p>有效姿态采样 {episode.evidence?.pose_samples ?? "—"} 帧 · 关联球拍 {episode.evidence?.racket_associated_frames ?? "—"} 帧</p>{[...new Set([...(episode.metric_notes_zh ?? []), ...episode.limitations_zh])].map(note => <p key={note}>{note}</p>)}</details>
    </> : <div className="motion-empty"><strong>{pending ? "正在分析动作阶段与运动指标" : `本次${familyLabel}分析证据不足`}</strong><p>{data?.reason_zh || (pending ? "完成后会显示可复核的动作区间与测量结果。" : "当前结果未提供运动分析，请重新读取任务结果。")}</p></div>}
  </section>;
}
