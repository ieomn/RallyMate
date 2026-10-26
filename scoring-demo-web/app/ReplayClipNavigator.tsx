"use client";

import { useEffect, useMemo, useState, type RefObject } from "react";
import type { DemoResultResponse, MotionAnalysis } from "./lib/api-types";
import { createReplayClipController, emptyClipPlayback, replayClipsOf, replayRangeForMedia } from "./lib/replay-clips";

const clock = (milliseconds: number) => `${(milliseconds / 1000).toFixed(2)} 秒`;

export default function ReplayClipNavigator({ containerRef, result, motion, mediaKey, jobId }: {
  containerRef: RefObject<HTMLDivElement | null>; result: DemoResultResponse | null; motion: MotionAnalysis | null;
  mediaKey: string; jobId?: string;
}) {
  const clips = useMemo(() => replayClipsOf(result, motion), [result, motion]);
  const [playback, setPlayback] = useState(emptyClipPlayback);
  // ScoreLab keys this component by the report session, so a new job cannot
  // inherit the previous video's source clock or selected window.
  const controller = useMemo(() => createReplayClipController(setPlayback), []);
  const available = playback.ready ? clips.filter(clip => replayRangeForMedia(clip, playback.originMs, playback.durationSeconds)) : clips;
  const selected = available.find(clip => clip.id === playback.selectedId) ?? null;
  useEffect(() => () => controller.dispose(), [controller]);
  useEffect(() => {
    // A lightweight assessment can replace the legacy list after playback has
    // started. Drop its old loop without moving the current source clock.
    if (playback.selectedId && !clips.some(clip => clip.id === playback.selectedId)) controller.select(null);
  }, [clips, controller, playback.selectedId]);
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const connect = () => {
      const video = container.querySelector<HTMLVideoElement>("video[data-time-origin-ms]");
      const origin = video?.dataset.timeOriginMs;
      controller.attach(video, origin !== undefined && origin.trim() ? Number(origin) : Number.NaN, video?.getAttribute("src") ?? mediaKey);
    };
    connect();
    const observer = new MutationObserver(connect);
    observer.observe(container, { childList: true, subtree: true, attributes: true, attributeFilter: ["src", "data-time-origin-ms"] });
    return () => observer.disconnect();
  }, [containerRef, controller, mediaKey]);
  useEffect(() => {
    const onSeek = (event: Event) => {
      const detail = (event as CustomEvent<{ timestampMs?: number; jobId?: string; preferOriginal?: boolean }>).detail;
      if (!detail || detail.jobId && detail.jobId !== jobId || !Number.isFinite(detail.timestampMs)) return;
      controller.select(null);
      controller.seek(detail.timestampMs!, false, detail.preferOriginal === true);
    };
    window.addEventListener("rallymate:seek-video", onSeek);
    return () => window.removeEventListener("rallymate:seek-video", onSeek);
  }, [controller, jobId]);
  return <section className="replay-clips" aria-label="动作片段与关键时间">
    <div className="replay-clips-heading"><div><span>逐段回看</span><h3>动作片段</h3></div><span>{available.length} 段可定位</span></div>
    <div className="replay-clips-controls">
      <label><span>选择回放范围</span><select aria-label="选择动作片段" value={selected?.id ?? "all"} disabled={!playback.ready} onChange={event => controller.select(available.find(clip => clip.id === event.target.value) ?? null)}>
        <option value="all">全部视频</option>
        {available.map((clip, index) => <option key={clip.id} value={clip.id}>{index + 1}. {clip.label} · {clock(clip.startMs)} – {clock(clip.endMs)}</option>)}
      </select></label>
      <button type="button" disabled={!playback.ready} onClick={() => controller.restart()}>{selected ? "从本段开头播放" : "从头播放"}</button>
      <button type="button" disabled={!selected || !playback.ready} aria-pressed={playback.looping} onClick={() => controller.setLoop(!playback.looping)}>循环本段{playback.looping ? " · 已开启" : ""}</button>
    </div>
    {selected ? <>
      <p className="replay-clips-scope">动作时间 {clock(selected.startMs)} – {clock(selected.endMs)}。回放保留前后各约 0.35 秒，方便观察准备与恢复。</p>
      <div className="replay-clips-anchors" role="group" aria-label="本段关键时间">{selected.anchors.map(anchor => <button type="button" key={anchor.id} onClick={() => controller.seek(anchor.sourceMs)}><span>{anchor.label}</span><time>{clock(anchor.sourceMs)}</time></button>)}</div>
      <p className="replay-clips-note">时间点来自本次动作测量；运动峰值不等同于触球时刻。</p>
    </> : <p className="replay-clips-note">{available.length ? "选择一段动作，可从关键时间点定位，或循环慢放。" : clips.length && playback.ready ? "当前视频未包含已定位的完整片段，可切换原视频查看。" : "本次暂无可定位的完整动作片段，仍可播放整个视频。"}</p>}
    {playback.notice && <p className="replay-clips-notice" role="status">{playback.notice}</p>}
  </section>;
}
