"use client";

import { useState } from "react";
import type { FootworkReview } from "./lib/api-types";
import { footworkEpisodes, footworkFeatureText, footworkScoringStatus } from "./lib/footwork-review";

export default function FootworkReviewPanel({ review, jobId }: { review?: FootworkReview; jobId?: string }) {
  const [selectedId, select] = useState("");
  const episodes = footworkEpisodes(review);
  const index = Math.max(0, episodes.findIndex(episode => episode.event_id === selectedId));
  const episode = episodes[index];
  if (!episode) return <p className="score-disclosure">{review?.reason_zh || "本次结果没有可复核的步伐时间区间；重新读取任务可检查是否已有逐段证据。"}</p>;
  const seek = () => window.dispatchEvent(new CustomEvent("rallymate:seek-video", { detail: { timestampMs: episode.start_ms, jobId } }));
  return <section className="motion-analysis-panel footwork-review" aria-label="步伐逐段复核">
    <div className="motion-analysis-heading"><h3>步伐逐段复核</h3><span>规则候选 · 非实际步数</span></div>
    <div className="motion-episode-heading"><div><strong>{episode.name_zh} · 候选待复核</strong><p>{(episode.start_ms / 1000).toFixed(2)}–{(episode.end_ms / 1000).toFixed(2)} 秒</p></div><button type="button" onClick={seek}>定位回放 ↗</button></div>
    <div className="footwork-evidence-list">{episode.indicators.map(indicator => <article key={indicator.indicator_id}>
      <div><strong>{indicator.name_zh}</strong><span>{footworkScoringStatus(indicator.scoring_status)}</span></div>
      <p>{indicator.features.length ? indicator.features.map(feature => `${feature.name_zh || feature.feature_name}：${footworkFeatureText(feature.value, feature.unit)}`).join("；") : "本段没有通过质量门槛的测量值。"}</p>
    </article>)}</div>
    <div className="motion-episode-nav"><button type="button" disabled={index === 0} onClick={() => select(episodes[index - 1].event_id)}>← 上一片段</button><span>{index + 1} / {episodes.length} 段</span><button type="button" disabled={index === episodes.length - 1} onClick={() => select(episodes[index + 1].event_id)}>下一片段 →</button></div>
    <p className="motion-analysis-note">分腿、启动和制动由二维姿态规则定位；离地、落地、双支撑等是运动代理。缺少教练标定时，测量幅度不换算成技术等级。</p>
    {review?.is_truncated && <p className="motion-analysis-note">本次展示前 {episodes.length} 段，共 {review.episode_count ?? "—"} 段；其余区间保存在当前任务的事件记录中。</p>}
  </section>;
}
