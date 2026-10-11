"use client";

import { useEffect, useRef, useState } from "react";
import { MotionReplaySynchronizer, type ReplaySyncSnapshot } from "./lib/motion-replay-sync";

type ComparisonSource = { id: string; title: string; label: string; src?: string; poster?: string; caption: string; placeholder: string; placeholderDetail?: string };
const statusLabels = { loading: "正在加载", ready: "", buffering: "正在缓冲", ended: "片段已结束 · 保留末帧", unavailable: "暂时无法播放，其余视频可继续" };

export default function MotionReplayComparison({ sources, duration, sourceStart }: { sources: ComparisonSource[]; duration: number; sourceStart: number }) {
  const videos = useRef<Record<string, HTMLVideoElement | null>>({});
  const controller = useRef<MotionReplaySynchronizer | null>(null);
  const [state, setState] = useState<ReplaySyncSnapshot>({ elapsed: 0, duration, playing: false, buffering: false, rate: 1, videos: {} });
  const [position, setPosition] = useState("0");
  const sourceKey = sources.map(source => `${source.id}:${source.src ?? ""}`).join("|");

  useEffect(() => {
    const active = Object.entries(videos.current).filter((entry): entry is [string, HTMLVideoElement] => Boolean(entry[1]));
    const sync = new MotionReplaySynchronizer(active.map(([id, video]) => ({ id, video })), duration);
    controller.current = sync;
    let frame = 0, lastUpdate = 0;
    const tick = (now: number) => {
      const snapshot = sync.tick(now);
      if (now - lastUpdate > 60) { setState(snapshot); lastUpdate = now; }
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => { cancelAnimationFrame(frame); sync.pause(); controller.current = null; };
  }, [sourceKey, duration]);

  const hasVideo = sources.some(source => source.src);
  const seek = (seconds: number) => { controller.current?.seek(seconds); };
  return <div className="motion-replay-comparison">
    <div className="motion-replay-grid">
      {sources.map(source => <section className="motion-replay-version" aria-label={source.title} key={source.id}>
        <div className="motion-replay-version-heading"><h3>{source.title}</h3><span className={source.id === "generated" ? "motion-replay-generated-label" : ""}>{source.label}</span></div>
        <div className="motion-replay-screen">
          {source.src ? <video key={source.src} ref={element => { videos.current[source.id] = element; }} className="motion-replay-video" playsInline muted preload="auto" src={source.src} poster={source.poster} aria-label={source.title} onError={() => controller.current?.fail(source.id)} /> : <div className="motion-replay-placeholder"><strong>{source.placeholder}</strong>{source.placeholderDetail && <p>{source.placeholderDetail}</p>}</div>}
          {source.src && state.videos[source.id] && state.videos[source.id] !== "ready" && <div className={`motion-replay-media-state is-${state.videos[source.id]}`} role="status"><span>{statusLabels[state.videos[source.id]]}</span>{state.videos[source.id] === "unavailable" && <button type="button" onClick={() => controller.current?.retry(source.id)}>重新加载</button>}</div>}
        </div>
        <p className="motion-replay-caption">{source.caption}</p>
      </section>)}
    </div>
    <div className="motion-replay-sync-controls" aria-label="同步对比播放控制">
      <div className="motion-replay-playback-row">
        <button type="button" className="product-primary" disabled={!hasVideo} onClick={() => state.playing ? controller.current?.pause() : controller.current?.play()}>{state.playing ? "暂停全部" : "同步播放"}</button>
        <div className="motion-replay-frame-controls"><button type="button" className="product-secondary" disabled={!hasVideo || state.elapsed <= 0} onClick={() => controller.current?.step(-1 / 24)} aria-label="后退一帧">−1 帧</button><button type="button" className="product-secondary" disabled={!hasVideo || state.elapsed >= state.duration} onClick={() => controller.current?.step(1 / 24)} aria-label="前进一帧">+1 帧</button></div>
        <label className="motion-replay-rate">速度<select value={state.rate} onChange={event => controller.current?.setRate(Number(event.target.value))}><option value="0.25">0.25×</option><option value="0.5">0.5×</option><option value="1">1×</option></select></label>
        <output className="motion-replay-clock">{state.elapsed.toFixed(2)} <span>/ {state.duration.toFixed(2)} 秒</span></output>
      </div>
      <input className="motion-replay-timeline" type="range" aria-label="三视频共同时间轴" min="0" max={state.duration} step={1 / 240} value={state.elapsed} disabled={!hasVideo} onChange={event => seek(Number(event.target.value))} />
      <div className="motion-replay-timepoints" aria-label="按秒定位">{Array.from({ length: Math.floor(duration) + 1 }, (_, second) => <button type="button" key={second} onClick={() => seek(second)} disabled={!hasVideo}>{second}s</button>)}</div>
      <form className="motion-replay-position" onSubmit={event => { event.preventDefault(); if (position !== "" && Number.isFinite(Number(position))) seek(Number(position)); }}><label>定位（片段秒数）<input type="number" min="0" max={state.duration} step="any" value={position} onChange={event => setPosition(event.target.value)} disabled={!hasVideo} /></label><button className="product-secondary" type="submit" disabled={!hasVideo || position === ""}>跳转</button></form>
      <p className="motion-replay-sync-note" role="status">{state.buffering ? "正在等待视频就绪，全部画面暂时暂停。" : `片段 ${state.elapsed.toFixed(2)} 秒 · 对应原视频 ${(sourceStart + Math.min(duration, state.elapsed)).toFixed(2)} 秒${state.elapsed > duration ? "（原片段已结束）" : ""}`}<span>按同一秒数对照；不拉伸时长。AI 多出的尾段保留显示。逐帧步长为 1/24 秒。</span></p>
    </div>
  </div>;
}
