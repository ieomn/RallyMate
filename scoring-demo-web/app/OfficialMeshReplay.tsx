"use client";

import { useEffect, useRef, useState } from "react";
import type { OfficialMeshMedia } from "./lib/official-mesh-media";
import { useMediaReadiness } from "./lib/use-media-readiness";

export default function OfficialMeshReplay({ media, jobId }: { media: OfficialMeshMedia; jobId: string }) {
  const video = useRef<HTMLVideoElement>(null);
  const [side, setSide] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [failed, setFailed] = useState(false);
  const resume = useRef({ time: 0, paused: true });
  const source = side ? media.side_video_url : media.video_url;
  const readiness = useMediaReadiness(video, `${jobId}:${source}`);
  const pendingSeek = useRef<number | null>(null);
  useEffect(() => {
    const onSeek = (event: Event) => {
      const detail = (event as CustomEvent<{ timestampMs?: number; jobId?: string }>).detail;
      if (!video.current || detail?.jobId && detail.jobId !== jobId
        || typeof detail?.timestampMs !== "number" || !Number.isFinite(detail.timestampMs)) return;
      const time = (detail.timestampMs - media.source_relative_origin_ms) / 1000;
      if (time < 0) return;
      if (!Number.isFinite(video.current.duration)) { pendingSeek.current = time; return; }
      if (time <= video.current.duration) { video.current.currentTime = time; pendingSeek.current = null; }
    };
    window.addEventListener("rallymate:seek-video", onSeek);
    return () => window.removeEventListener("rallymate:seek-video", onSeek);
  }, [jobId, media.source_relative_origin_ms]);
  const choose = (next: boolean) => {
    if (next === side) return;
    resume.current = { time: video.current?.currentTime ?? 0, paused: video.current?.paused ?? true };
    setFailed(false);
    setSide(next);
  };
  return <div className="official-mesh-replay">
    <div className="replay-overlay-switches" role="group" aria-label="人体表面视角">
      <button type="button" className="product-secondary" aria-pressed={!side} onClick={() => choose(false)}>原片叠加</button>
      <button type="button" className="product-secondary" aria-pressed={side} onClick={() => choose(true)}>侧面 90°</button>
      <label>播放速度 <select value={speed} onChange={event => {
        const value = Number(event.target.value); setSpeed(value); if (video.current) video.current.playbackRate = value;
      }}>{[0.25, 0.5, 1, 1.5, 2].map(value => <option key={value} value={value}>{value}×</option>)}</select></label>
    </div>
    <video ref={video} key={readiness.key} controls muted playsInline preload="metadata"
      style={{ width: "100%", maxHeight: "72vh", background: side ? "#fff" : "#101820", borderRadius: 12 }}
      src={source} poster={!side ? media.preview_image_url : undefined}
      aria-label={side ? "SAM 3D Body 官方人体网格侧面回放" : "SAM 3D Body 官方人体网格原片叠加回放"}
      onLoadedMetadata={() => {
        if (!video.current) return;
        setFailed(false);
        video.current.playbackRate = speed;
        const target = pendingSeek.current ?? resume.current.time;
        if (Number.isFinite(video.current.duration) && target <= video.current.duration) video.current.currentTime = target;
        pendingSeek.current = null;
        if (!resume.current.paused) void video.current.play().catch(() => {});
      }} onError={() => setFailed(true)} />
    {readiness.state === "loading" && <p role="status">正在读取官方人体表面视频…</p>}
    {(failed || readiness.state === "failed" || readiness.state === "timeout") && <div role="status"><p>{readiness.state === "timeout" ? "人体表面视频读取超时。" : "人体表面视频暂时无法播放。"}报告和评分仍可查看。</p><button type="button" className="product-secondary" onClick={() => {
      resume.current = { time: video.current?.currentTime ?? 0, paused: video.current?.paused ?? true };
      setFailed(false); readiness.retry();
    }}>重新读取官方回放</button></div>}
    <p className="replay-caption">SAM 3D Body 输出的真实人体网格，采用官方淡蓝材质与光照。{side ? "当前为模型估计人体的 90° 侧视。" : "当前叠加在同一帧原视频上。"} 单目估计；遮挡处外形由模型推断。</p>
  </div>;
}
