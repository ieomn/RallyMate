"use client";

import { useContext, useEffect, useMemo, useState, type RefObject } from "react";
import { ReportClipSelection } from "./report-clip-selection";
import type { DemoResultResponse, MotionAnalysis } from "./lib/api-types";
import { createReplayClipController, emptyClipPlayback, replayClipsOf, replayRangeForMedia } from "./lib/replay-clips";

const clock = (milliseconds: number) => `${(milliseconds / 1000).toFixed(2)} 秒`;

export default function ReplayClipNavigator({ containerRef, result, motion, mediaKey, jobId }: {
  containerRef: RefObject<HTMLDivElement | null>; result: DemoResultResponse | null; motion: MotionAnalysis | null;
  mediaKey: string; jobId?: string;
}) {
  const clips = useMemo(() => replayClipsOf(result, motion), [result, motion]);
  const [playback, setPlayback] = useState(emptyClipPlayback);
  const [page, setPage] = useState(0);
  const notifySelection = useContext(ReportClipSelection);
  // ScoreLab keys this component by the report session, so a new job cannot
  // inherit the previous video's source clock or selected window.
  const controller = useMemo(() => createReplayClipController(setPlayback), []);
  const available = playback.ready ? clips.filter(clip => replayRangeForMedia(clip, playback.originMs, playback.durationSeconds)) : clips;
  const selected = available.find(clip => clip.id === playback.selectedId) ?? null;
  const pageCount = Math.max(1, Math.ceil(available.length / 5));
  const offset = Math.min(page, pageCount - 1) * 5;
  const visible = available.slice(offset, offset + 5);
  const activeIndex = visible.findIndex(clip => clip.id === selected?.id);
  useEffect(() => { notifySelection?.(selected); }, [notifySelection, selected]);
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
      const at = detail.timestampMs!;
      const matches = clips.filter(clip => clip.startMs <= at && at <= clip.endMs);
      const matching = matches.find(clip => clip.startMs === at) ?? (matches.length === 1 ? matches[0] : undefined);
      controller.select(matching ?? null, "preserve");
      controller.seek(detail.timestampMs!, false, detail.preferOriginal === true);
    };
    window.addEventListener("rallymate:seek-video", onSeek);
    return () => window.removeEventListener("rallymate:seek-video", onSeek);
  }, [clips, controller, jobId]);
  return <section className="replay-clips" aria-label="动作片段与关键时间">
    <div className="replay-clips-heading"><h3>动作时间轴</h3><span>{available.length} 段可定位</span></div>
    {available.length > 0 ? <>
      <div className="review-timeline" role="group" aria-label="选择动作片段"><div className="review-timeline-track" style={{ gridTemplateColumns: `repeat(${visible.length}, minmax(78px, 1fr))` }}>
        <span aria-hidden="true" className="review-timeline-cursor" style={{ width: `${100 / visible.length}%`, opacity: activeIndex < 0 ? 0 : 1, transform: `translateX(${100 * Math.max(0, activeIndex)}%)` }} />
        {visible.map((clip, index) => <button type="button" key={clip.id} aria-pressed={selected?.id === clip.id} title={`${clip.label} · ${clock(clip.startMs)} – ${clock(clip.endMs)}`}
          onKeyDown={event => {
            if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
            event.preventDefault();
            const direction = event.key === "ArrowLeft" ? -1 : 1;
            const destination = Math.max(0, Math.min(available.length - 1, offset + index + direction));
            setPage(Math.floor(destination / 5));
            const container = event.currentTarget.parentElement;
            controller.select(available[destination], "action");
            requestAnimationFrame(() => container?.querySelectorAll("button")[destination % 5]?.focus());
          }} onClick={() => controller.select(clip, "action")}>
          <span>{clip.label} {offset + index + 1}</span><time>{clock(clip.startMs)}</time>
        </button>)}</div></div>
      <div className="review-timeline-tools">
        {pageCount > 1 && <div className="review-timeline-pages">
          <button type="button" disabled={offset === 0} onClick={() => setPage(offset / 5 - 1)}>上一组</button>
          <span>{offset / 5 + 1} / {pageCount}</span>
          <button type="button" disabled={offset + 5 >= available.length} onClick={() => setPage(offset / 5 + 1)}>下一组</button></div>}
        {selected && <><button type="button" onClick={() => controller.select(null)}>返回全片</button>
          <button type="button" disabled={!playback.ready} onClick={() => controller.restart()}>重播本段</button>
          <button type="button" disabled={!playback.ready} aria-pressed={playback.looping} onClick={() => controller.setLoop(!playback.looping)}>循环本段</button></>}
      </div>
      {selected && playback.ready && <div className="replay-clips-anchors" role="group" aria-label="本段关键时间">{selected.anchors.map(anchor => <button type="button" key={anchor.id} onClick={() => controller.seek(anchor.sourceMs)}><span>{anchor.label}</span><time>{clock(anchor.sourceMs)}</time></button>)}</div>}
    </> : <p className="replay-clips-note">{clips.length && playback.ready ? "此视频未包含已定位的完整片段。" : "本次没有可定位的完整片段。"}</p>}
    {playback.notice && <p className="replay-clips-notice" role="status">{playback.notice}</p>}
  </section>;
}
