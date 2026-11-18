"use client";

import { useContext, useEffect, useMemo, useRef, useState, type RefObject } from "react";
import { ReportClipSelection } from "./report-clip-selection";
import type { DemoResultResponse, MotionAnalysis } from "./lib/api-types";
import { createReplayClipController, emptyClipPlayback, replayClipsOf, replayRangeForMedia } from "./lib/replay-clips";
import { tennisAssessmentOf } from "./lib/tennis-assessment";
import { primaryTennisPractice } from "./lib/tennis-practice";
import { trainingGuidance } from "./lib/training-guidance";

const clock = (milliseconds: number) => `${(milliseconds / 1000).toFixed(2)} 秒`;

export default function ReplayClipNavigator({ containerRef, result, motion, mediaKey, jobId }: {
  containerRef: RefObject<HTMLDivElement | null>; result: DemoResultResponse | null; motion: MotionAnalysis | null;
  mediaKey: string; jobId?: string;
}) {
  const assessment = useMemo(() => tennisAssessmentOf(result), [result]);
  const clips = useMemo(() => replayClipsOf(result, motion, assessment), [result, motion, assessment]);
  const priorityWindow = useMemo(() => {
    if (assessment) {
      const practice = primaryTennisPractice(assessment);
      return practice?.start_ms != null && practice.end_ms != null ? { startMs: practice.start_ms, endMs: practice.end_ms } : null;
    }
    return trainingGuidance(result, motion).find(item => item.evidenceState !== "unavailable")?.moment ?? null;
  }, [assessment, result, motion]);
  const [playback, setPlayback] = useState(emptyClipPlayback);
  const [page, setPage] = useState<number | null>(null);
  const interacted = useRef(false);
  const automaticClip = useRef<string | null>(null);
  const notifySelection = useContext(ReportClipSelection)?.onSelect;
  // ScoreLab keys this component by the report session, so a new job cannot
  // inherit the previous video's source clock or selected window.
  const controller = useMemo(() => createReplayClipController(setPlayback), []);
  const available = useMemo(() => playback.ready ? clips.filter(clip => replayRangeForMedia(clip, playback.originMs, playback.durationSeconds)) : clips, [clips, playback.ready, playback.originMs, playback.durationSeconds]);
  const selected = available.find(clip => clip.id === playback.selectedId) ?? null;
  const pageCount = Math.max(1, Math.ceil(available.length / 5));
  const selectedIndex = selected ? available.indexOf(selected) : 0;
  const offset = Math.min(page ?? Math.floor(selectedIndex / 5), pageCount - 1) * 5;
  const visible = available.slice(offset, offset + 5);
  const activeIndex = visible.findIndex(clip => clip.id === selected?.id);
  useEffect(() => { notifySelection?.(selected); }, [notifySelection, selected]);
  useEffect(() => {
    if (playback.selectedId && !clips.some(clip => clip.id === playback.selectedId)) controller.select(null);
  }, [clips, controller, playback.selectedId]);
  useEffect(() => {
    if (interacted.current || !available.length) return;
    const covering = priorityWindow ? available.filter(clip => clip.startMs <= priorityWindow.startMs && clip.endMs >= priorityWindow.endMs) : [];
    const exact = priorityWindow && covering.find(clip => clip.startMs === priorityWindow.startMs && clip.endMs === priorityWindow.endMs);
    const first = exact || (covering.length === 1 ? covering[0] : available[0]);
    const windowKey = `${first.id}:${first.startMs}:${first.endMs}`;
    if (automaticClip.current === windowKey) return;
    automaticClip.current = windowKey;
    controller.select(first, "action", false);
  }, [available, controller, priorityWindow]);
  useEffect(() => {
    const container = containerRef.current;
    if (!container || !selected || !playback.ready) return;
    const durationMs = playback.durationSeconds * 1000;
    container.style.setProperty("--clip-start", `${(selected.startMs - playback.originMs) / durationMs * 100}%`);
    container.style.setProperty("--clip-length", `${(selected.endMs - selected.startMs) / durationMs * 100}%`);
    container.dataset.clipWindow = "true";
    return () => {
      delete container.dataset.clipWindow;
      container.style.removeProperty("--clip-start");
      container.style.removeProperty("--clip-length");
    };
  }, [containerRef, selected, playback.ready, playback.durationSeconds, playback.originMs]);
  useEffect(() => () => controller.dispose(), [controller]);
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
    const takeControl = () => { interacted.current = true; };
    container.addEventListener("pointerdown", takeControl, true);
    container.addEventListener("keydown", takeControl, true);
    return () => {
      observer.disconnect();
      container.removeEventListener("pointerdown", takeControl, true);
      container.removeEventListener("keydown", takeControl, true);
    };
  }, [containerRef, controller, mediaKey]);
  useEffect(() => {
    const onSeek = (event: Event) => {
      const detail = (event as CustomEvent<{ timestampMs?: number; jobId?: string; preferOriginal?: boolean }>).detail;
      if (!detail || detail.jobId && detail.jobId !== jobId || !Number.isFinite(detail.timestampMs)) return;
      interacted.current = true;
      setPage(null);
      const at = detail.timestampMs!;
      const matches = clips.filter(clip => clip.startMs <= at && at <= clip.endMs);
      const matching = matches.find(clip => clip.startMs === at) ?? (matches.length === 1 ? matches[0] : undefined);
      controller.select(matching ?? null, "preserve");
      controller.seek(detail.timestampMs!, false, detail.preferOriginal === true);
    };
    window.addEventListener("rallymate:seek-video", onSeek);
    return () => window.removeEventListener("rallymate:seek-video", onSeek);
  }, [clips, controller, jobId]);
  function chooseClip(clip: typeof clips[number]) {
    interacted.current = true;
    const video = containerRef.current?.querySelector<HTMLVideoElement>("video[data-time-origin-ms]");
    controller.select(clip, "action", video?.paused === false);
  }
  return <section className="replay-clips" aria-label="动作片段与关键时间">
    <div className="replay-clips-heading"><h3>片段时间轴</h3>{available.length > 0 && <span>{available.length} 段</span>}</div>
    {available.length > 0 ? <>
      <div className="review-timeline" role="group" aria-label="选择动作片段"><div className="review-timeline-track" style={{ gridTemplateColumns: `repeat(${visible.length}, minmax(78px, 1fr))` }}>
        <span aria-hidden="true" className="review-timeline-cursor" style={{ width: `${100 / visible.length}%`, opacity: activeIndex < 0 ? 0 : 1, transform: `translateX(${100 * Math.max(0, activeIndex)}%)` }} />
        {visible.map((clip, index) => <button type="button" key={clip.id} aria-pressed={selected?.id === clip.id} title={`${clip.label} · ${clock(clip.startMs)} – ${clock(clip.endMs)}`}
          onKeyDown={event => {
            let destination;
            if (event.key === "Home") destination = 0;
            else if (event.key === "End") destination = available.length - 1;
            else if (event.key === "ArrowLeft") destination = Math.max(0, offset + index - 1);
            else if (event.key === "ArrowRight") destination = Math.min(available.length - 1, offset + index + 1);
            else return;
            event.preventDefault();
            setPage(Math.floor(destination / 5));
            const container = event.currentTarget.parentElement;
            chooseClip(available[destination]);
            requestAnimationFrame(() => {
              const button = container?.querySelectorAll("button")[destination % 5];
              button?.focus({ preventScroll: true });
              const scroller = container?.parentElement;
              if (!button || !scroller) return;
              const { left, right } = button.getBoundingClientRect(), viewport = scroller.getBoundingClientRect();
              if (left < viewport.left || right > viewport.right) scroller.scrollBy({
                left: left < viewport.left ? left - viewport.left : right - viewport.right,
                behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth",
              });
            });
          }} onClick={() => chooseClip(clip)}>
          <span>{clip.label} {offset + index + 1}</span><time>{clock(clip.startMs)}</time>
        </button>)}</div></div>
      <div className="review-timeline-tools">
        {pageCount > 1 && <div className="review-timeline-pages">
          <button type="button" disabled={offset === 0} onClick={() => setPage(offset / 5 - 1)}>上一组</button>
          <span>{offset / 5 + 1} / {pageCount}</span>
          <button type="button" disabled={offset + 5 >= available.length} onClick={() => setPage(offset / 5 + 1)}>下一组</button></div>}
        {selected && <><button type="button" onClick={() => { interacted.current = true; controller.select(null); }}>返回全片</button>
          <button type="button" disabled={!playback.ready} onClick={() => controller.restart()}>重播本段</button>
          <button type="button" disabled={!playback.ready} aria-pressed={playback.looping} onClick={() => controller.setLoop(!playback.looping)}>循环本段</button></>}
      </div>
      {selected && playback.ready && selected.anchors.length > 0 && <details className="replay-anchor-detail"><summary>关键时间点</summary><div className="replay-clips-anchors" role="group" aria-label="本段关键时间">{selected.anchors.map(anchor => <button type="button" key={anchor.id} onClick={() => controller.seek(anchor.sourceMs)}><span>{anchor.label}</span><time>{clock(anchor.sourceMs)}</time></button>)}</div></details>}
    </> : <p className="replay-clips-note">{clips.length && playback.ready ? "此视频未包含已定位的完整片段。" : "本次没有可定位的完整片段。"}</p>}
    {playback.notice && <p className="replay-clips-notice" role="status">{playback.notice}</p>}
  </section>;
}
