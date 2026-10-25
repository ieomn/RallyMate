"use client";

import { useMemo, useState } from "react";
import type { DemoResultResponse, FootworkReview } from "./lib/api-types";
import { footworkEpisodes, footworkFeatureText, footworkScoringStatus } from "./lib/footwork-review";
import { contextualHipForEpisode, contextualHipMeasurements, contextualHipReason } from "./lib/technical-review";

export default function FootworkReviewPanel({ review, jobId, sourceAssessment }: {
  review?: FootworkReview; jobId?: string; sourceAssessment?: DemoResultResponse["source_aligned_assessment"];
}) {
  const [selectedId, select] = useState("");
  const contextual = useMemo(() => contextualHipMeasurements({ source_aligned_assessment: sourceAssessment }), [sourceAssessment]);
  const episodes = footworkEpisodes(review);
  const index = Math.max(0, episodes.findIndex(episode => episode.event_id === selectedId));
  const episode = episodes[index];
  if (!episode) return <p className="score-disclosure">{review?.reason_zh || "本次结果没有可复核的步伐时间区间；重新读取任务可检查是否已有逐段证据。"}</p>;
  const hip = contextualHipForEpisode(contextual, episode);
  const hipFrames = hip?.source_window?.source_frames as number[] | undefined;
  const firstBinding = hip?.source_bindings[0], lastBinding = hip?.source_bindings.at(-1);
  const seek = () => window.dispatchEvent(new CustomEvent("rallymate:seek-video", { detail: { timestampMs: episode.start_ms, jobId } }));
  return <section className="motion-analysis-panel footwork-review" aria-label="步伐逐段复核">
    <div className="motion-analysis-heading"><h3>步伐逐段复核</h3><span>规则候选 · 非实际步数</span></div>
    <div className="motion-episode-heading"><div><strong>{episode.name_zh} · 候选待复核</strong><p>{(episode.start_ms / 1000).toFixed(2)}–{(episode.end_ms / 1000).toFixed(2)} 秒</p></div><button type="button" onClick={seek}>定位回放 ↗</button></div>
    <div className="footwork-evidence-list">{episode.indicators.map(indicator => <article key={indicator.indicator_id}>
      <div><strong>{indicator.name_zh}</strong><span>{footworkScoringStatus(indicator.scoring_status)}</span></div>
      {indicator.measurements ? <div className="independent-measurements">{indicator.measurements.map(item => <div key={item.feature_name}><span>{item.name_zh}</span><strong>{item.status === "measured" ? footworkFeatureText(item.value, item.unit) : "—"}</strong>{item.status !== "measured" && <small>{item.reason_zh}</small>}<details><summary>测量依据与建议</summary><p>{item.reason_zh} {item.review_hint_zh}</p><small>{item.view_semantics === "independent_target_context" ? "独立目标方向" : "二维画面代理"} · {(item.window.start_ms / 1000).toFixed(2)}–{(item.window.end_ms / 1000).toFixed(2)} 秒</small></details></div>)}</div> : <p>{indicator.features.length ? indicator.features.map(feature => `${feature.name_zh || feature.feature_name}：${footworkFeatureText(feature.value, feature.unit)}`).join("；") : "本段没有通过质量门槛的测量值。"}</p>}
    </article>)}</div>
    {hip && <details className="report-disclosure"><summary>髋中心路径/净位移 <span>二维量测 · 非技术等级</span></summary>
      <p>路径长度：{hip.hip_path_length_px === null ? "不可用" : footworkFeatureText(hip.hip_path_length_px, "px")}；
        起终点净位移：{hip.hip_net_displacement_px === null ? "不可用" : footworkFeatureText(hip.hip_net_displacement_px, "px")}；
        路径/净位移比：{hip.hip_path_over_net_ratio === null ? "不可用" : footworkFeatureText(hip.hip_path_over_net_ratio)}。</p>
      <p>{contextualHipReason(hip.reason)} {hip.status === "unavailable" && <code>{hip.reason}</code>}</p>
      <p>原事件 {hip.event_code} · 候选窗口 {(hip.parent_window_ms.start_ms / 1000).toFixed(3)}–{(hip.parent_window_ms.end_ms / 1000).toFixed(3)} 秒。
        {hip.measurement_window_video_ms ? "计算使用该完整窗口，未裁切或插值。" : "完整计算窗口不可核验，未生成测量值。"}
        {hip.source_frame_bounds && <> 原帧 {hip.source_frame_bounds.start_source_frame}–{hip.source_frame_bounds.end_source_frame}，共 {hipFrames?.length} 帧。</>}</p>
      {firstBinding && lastBinding && <p>原生 PTS {String(firstBinding.pts)}–{String(lastBinding.pts)}；时间基 {String(firstBinding.time_base_num)}/{String(firstBinding.time_base_den)} 秒。</p>}
      <p>计算定义来源：步伐卡 FS05-M02（T023 / P0827）。本窗口仍属于 {hip.event_code} 候选，不确认正式动作类型。数值是稳定相机参考画面中的像素量，不是米数、实际步数或技术成绩。</p>
      <p className="motion-analysis-note" style={{ overflowWrap: "anywhere" }}>来源任务 {hip.source_id} · 视频 {hip.video_id}<br />视频 SHA-256：{hip.video_sha256}<br />方法版本：{hip.helper_version}；来源绑定：{hip.adapter.version}</p>
    </details>}
    <div className="motion-episode-nav"><button type="button" disabled={index === 0} onClick={() => select(episodes[index - 1].event_id)}>← 上一片段</button><span>{index + 1} / {episodes.length} 段</span><button type="button" disabled={index === episodes.length - 1} onClick={() => select(episodes[index + 1].event_id)}>下一片段 →</button></div>
    <p className="motion-analysis-note">分腿、启动和制动由二维姿态规则定位；离地、落地、双支撑等是运动代理。缺少教练标定时，测量幅度不换算成技术等级。</p>
    {review?.is_truncated && <p className="motion-analysis-note">本次展示前 {episodes.length} 段，共 {review.episode_count ?? "—"} 段；其余区间保存在当前任务的事件记录中。</p>}
  </section>;
}
