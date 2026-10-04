"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { PosePreviewResponse, TrajectoryPoint, TrajectoryPreviewResponse } from "./lib/api-types";
import { timestampForVideoTime, trajectoryAvailability, trajectoryDisplayOpacity, trajectoryEdges, trajectoryStrokes, videoTimeForTimestamp, visibleTrajectoryPaths, type TrajectoryDisplayMode } from "./lib/trajectory-viewer";
import { poseFrameAt } from "./lib/pose-playback";
import { usePosePlayback, type PoseWindowLoader } from "./lib/use-pose-playback";
import PoseSkeletonOverlay from "./PoseSkeletonOverlay";

type BallTrajectoryViewerProps = {
  trajectory: TrajectoryPreviewResponse | null;
  /** A clean replay URL or a local object URL owned by the caller. */
  videoSrc?: string | null;
  poster?: string | null;
  embeddedPoints?: TrajectoryPoint[];
  isLive?: boolean;
  onVideoError?: () => void;
  /** Timestamp represented by video time 0. Use 0 for the original uploaded file. */
  videoTimeOriginMs?: number;
  pending?: boolean;
  error?: string | null;
  jobId?: string;
  posePreview?: PosePreviewResponse | null;
  loadPoseWindow?: PoseWindowLoader;
  /** Original source video, or generated replay with timing_preserved === true. */
  poseTimingPreserved?: boolean;
  /** Explicit viewer preferences. Reports start with an unobstructed replay. */
  initialOverlays?: { ball?: boolean; pose?: boolean };
};

function clamp(value: number, minimum = 0, maximum = 1) {
  return Math.max(minimum, Math.min(maximum, Number.isFinite(value) ? value : minimum));
}

function pointSource(point: TrajectoryPoint) {
  return point.source === "interpolated" ? "interpolated" : "observed";
}

const motionLabel = (value?: string) => value === "moving" ? "移动" : value === "stationary" ? "静止 / 微动" : "零散观测";
const EMPTY_POINTS: TrajectoryPoint[] = [];
const PLAYBACK_RATES = [.5, .75, 1, 1.5, 2] as const;

export function formatReplayTime(milliseconds: number) {
  const seconds = Math.floor(Math.max(0, Number.isFinite(milliseconds) ? milliseconds : 0) / 1000);
  const minutes = Math.floor(seconds / 60);
  return `${minutes >= 60 ? `${Math.floor(minutes / 60)}:` : ""}${String(minutes % 60).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
}

export function setReplayRate(video: Pick<HTMLVideoElement, "playbackRate"> | null, rate: number) {
  if (!video || !PLAYBACK_RATES.some(value => value === rate)) return false;
  video.playbackRate = rate;
  return true;
}

export async function toggleReplayFullscreen(element: Pick<HTMLElement, "requestFullscreen"> | null, owner: Pick<Document, "fullscreenElement" | "fullscreenEnabled" | "exitFullscreen">) {
  if (!element || !owner.fullscreenEnabled || typeof element.requestFullscreen !== "function") throw new Error("此浏览器暂不支持全屏回放。");
  if (owner.fullscreenElement === element) await owner.exitFullscreen();
  else await element.requestFullscreen();
}

function PlaybackIcon({ playing }: { playing: boolean }) {
  return <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">{playing ? <path d="M7 5h3v14H7zm7 0h3v14h-3z" /> : <path d="m8 5 11 7-11 7z" />}</svg>;
}

export default function BallTrajectoryViewer({
  trajectory,
  videoSrc,
  poster,
  embeddedPoints = EMPTY_POINTS,
  isLive = true,
  onVideoError,
  videoTimeOriginMs = 0,
  pending = false,
  error,
  jobId,
  posePreview,
  loadPoseWindow,
  poseTimingPreserved = false,
  initialOverlays,
}: BallTrajectoryViewerProps) {
  const viewerRef = useRef<HTMLDivElement>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const animationRef = useRef<number | null>(null);
  const pendingSeekRef = useRef<number | null>(null);
  const [selectedSegment, setSelectedSegment] = useState<number | null>(null);
  const [currentMs, setCurrentMs] = useState<number | null>(null);
  const [durationMs, setDurationMs] = useState(0);
  const [videoAspectRatio, setVideoAspectRatio] = useState(16 / 9);
  const [playing, setPlaying] = useState(false);
  const [seeking, setSeeking] = useState(false);
  const [showOverlay, setShowOverlay] = useState(initialOverlays?.ball ?? false);
  const [showPose, setShowPose] = useState(initialOverlays?.pose ?? false);
  const [playbackRate, setPlaybackRate] = useState(1);
  const [playbackError, setPlaybackError] = useState("");
  const [fullscreenAvailable, setFullscreenAvailable] = useState(false);
  const [fullscreen, setFullscreen] = useState(false);
  const [fullscreenError, setFullscreenError] = useState("");
  const [showInterpolated, setShowInterpolated] = useState(true);
  const [displayMode, setDisplayMode] = useState<TrajectoryDisplayMode>("segment");
  const [showPoints, setShowPoints] = useState(false);
  const [movingOnly, setMovingOnly] = useState(true);
  const [followLatest, setFollowLatest] = useState(false);

  const reconstruction = trajectory?.ball.reconstruction ?? null;
  const segments = useMemo(() => reconstruction?.segments ?? [], [reconstruction?.segments]);
  const pointSegments = useMemo(() => new Map(segments.flatMap(item => item.points.map(point => [point, item] as const))), [segments]);
  const safeSelectedSegment = selectedSegment === null ? null : Math.min(selectedSegment, Math.max(segments.length - 1, 0));
  const segment = safeSelectedSegment === null ? null : (segments[safeSelectedSegment] ?? null);
  const fallbackObserved = trajectory?.ball.observed ?? embeddedPoints;
  const availability = trajectoryAvailability(trajectory, pending, error);

  const range = useMemo(() => {
    let first = Infinity;
    let last = videoTimeOriginMs;
    for (const path of [...segments.map(item => item.points), fallbackObserved]) for (const point of path) {
      if (Number.isFinite(point.timestamp_ms)) { first = Math.min(first, point.timestamp_ms); last = Math.max(last, point.timestamp_ms); }
    }
    return { start: videoSrc ? videoTimeOriginMs : trajectory?.source.start_timestamp_ms ?? (Number.isFinite(first) ? first : videoTimeOriginMs), end: Math.max(last, trajectory?.source.end_timestamp_ms ?? 0, videoTimeOriginMs + durationMs) };
  }, [segments, fallbackObserved, durationMs, videoSrc, videoTimeOriginMs, trajectory?.source.start_timestamp_ms, trajectory?.source.end_timestamp_ms]);

  const following = Boolean(trajectory?.source.is_partial && followLatest);
  const latestMs = trajectory?.source.end_timestamp_ms ?? range.end;
  const atMs = following ? latestMs : currentMs ?? (videoSrc ? videoTimeOriginMs : range.end);
  const pose = usePosePlayback({ atMs, jobId: jobId ?? trajectory?.job_id, loadWindow: loadPoseWindow, initialPreview: posePreview, enabled: showPose && poseTimingPreserved });
  const poseFrame = poseFrameAt(pose.preview, atMs);
  const aspectRatio = trajectory?.source.width && trajectory?.source.height ? trajectory.source.width / trajectory.source.height : videoAspectRatio;
  const replayJobId = jobId ?? trajectory?.job_id ?? pose.preview?.job_id;

  useEffect(() => {
    const syncFullscreen = () => {
      setFullscreenAvailable(Boolean(document.fullscreenEnabled && viewerRef.current?.requestFullscreen));
      setFullscreen(document.fullscreenElement === viewerRef.current);
    };
    const frame = requestAnimationFrame(syncFullscreen);
    document.addEventListener("fullscreenchange", syncFullscreen);
    return () => { cancelAnimationFrame(frame); document.removeEventListener("fullscreenchange", syncFullscreen); };
  }, []);

  useEffect(() => {
    const video = videoRef.current;
    if (video && following && video.readyState >= 1) video.currentTime = videoTimeForTimestamp(latestMs, videoTimeOriginMs);
  }, [following, latestMs, videoTimeOriginMs]);

  useEffect(() => {
    if (!playing) {
      if (animationRef.current !== null) cancelAnimationFrame(animationRef.current);
      animationRef.current = null;
      return;
    }
    const tick = () => {
      const video = videoRef.current;
      if (video) setCurrentMs(timestampForVideoTime(video.currentTime, videoTimeOriginMs));
      animationRef.current = requestAnimationFrame(tick);
    };
    animationRef.current = requestAnimationFrame(tick);
    return () => { if (animationRef.current !== null) cancelAnimationFrame(animationRef.current); };
  }, [playing, videoTimeOriginMs]);

  useEffect(() => {
    const onSeek = (event: Event) => {
      const { timestampMs, jobId: seekJobId } = (event as CustomEvent<{ timestampMs: number; jobId?: string }>).detail ?? {};
      if (!Number.isFinite(timestampMs) || seekJobId && replayJobId && seekJobId !== replayJobId) return;
      const video = videoRef.current;
      setFollowLatest(false);
      setSelectedSegment(null);
      setCurrentMs(timestampMs);
      if (video) {
        video.pause();
        if (video.readyState >= 1) video.currentTime = videoTimeForTimestamp(timestampMs, videoTimeOriginMs);
        else pendingSeekRef.current = timestampMs;
        video.scrollIntoView({ behavior: "smooth", block: "center" });
      }
    };
    window.addEventListener("rallymate:seek-video", onSeek);
    return () => window.removeEventListener("rallymate:seek-video", onSeek);
  }, [replayJobId, videoTimeOriginMs]);

  const canPlayVideo = Boolean(videoSrc);
  const paths = useMemo(() => {
    const available = movingOnly ? segments.filter(item => !item.analysis?.motion_status || item.analysis.motion_status === "moving") : segments;
    return reconstruction ? (segment ? [segment] : available).map(item => item.points) : [fallbackObserved];
  }, [reconstruction, segments, segment, movingOnly, fallbackObserved]);
  const trustedSegments = Boolean(reconstruction && /^1\.[1-9]\d*\./.test(reconstruction.version));
  const sampledPathEndMs = paths.map(path => {
    const item = pointSegments.get(path[0]);
    // Only source-wide evidence can certify freshness between sampled points.
    return item?.sampling?.is_sampled && (item.display_quality?.detector_confidence.min ?? 0) >= .15 ? item.end_ms : undefined;
  });
  const renderedPoints = showOverlay ? visibleTrajectoryPaths(paths, atMs, { showInterpolated, mode: displayMode, trustedSegments, sampledPathEndMs }) : [];
  const observed = renderedPoints.flat().filter((point) => pointSource(point) === "observed");
  const interpolated = renderedPoints.flat().filter((point) => pointSource(point) === "interpolated");
  const intervalsByPath = renderedPoints.map(path => {
    const item = pointSegments.get(path[0]);
    return item?.interpolation_intervals_complete === false ? [{ start_ms: item.start_ms, end_ms: item.end_ms }] : item?.interpolation_intervals ?? [];
  });
  const edges = trajectoryEdges(renderedPoints, atMs, trustedSegments ? Infinity : 250, intervalsByPath).filter(edge => showInterpolated || edge.source !== "interpolated");
  const strokes = trajectoryStrokes(edges);
  const lastObservedMs = renderedPoints.reduce((latest, path) => {
    const item = pointSegments.get(path[0]);
    const sampleSupported = item?.sampling?.is_sampled && (item.display_quality?.detector_confidence.min ?? 0) >= .15;
    return Math.max(latest, sampleSupported ? Math.min(atMs, item.end_ms) : path[path.length - 1].timestamp_ms);
  }, -Infinity);
  const holdingCompletedFlight = showOverlay && displayMode === "segment" && edges.length > 0 && atMs - lastObservedMs > 250;
  const activeRange = showOverlay && segment ? { start: segment.start_ms, end: segment.end_ms } : range;
  const progress = activeRange.end <= activeRange.start ? 0 : clamp((atMs - activeRange.start) / (activeRange.end - activeRange.start));
  const hasData = segments.length > 0 || fallbackObserved.length > 0;

  function seek(value: number) {
    const timestamp = activeRange.start + (activeRange.end - activeRange.start) * value;
    setFollowLatest(false);
    setCurrentMs(timestamp);
    if (videoRef.current) videoRef.current.currentTime = videoTimeForTimestamp(timestamp, videoTimeOriginMs);
  }

  async function togglePlayback() {
    const video = videoRef.current;
    if (!video) return;
    setFollowLatest(false);
    setPlaybackError("");
    if (video.paused) { try { await video.play(); } catch { setPlaying(false); setPlaybackError("视频暂时无法播放，请重试播放。"); } } else video.pause();
  }

  async function toggleFullscreen() {
    setFullscreenError("");
    try { await toggleReplayFullscreen(viewerRef.current, document); }
    catch { setFullscreenError("未能进入全屏，仍可在当前窗口回放。"); }
  }

  const currentTimeLabel = formatReplayTime(Math.max(0, atMs - videoTimeOriginMs));
  const durationLabel = durationMs > 0 ? formatReplayTime(durationMs) : "—:—";
  const hasPoseSource = Boolean(loadPoseWindow || posePreview);

  return (
    <div ref={viewerRef} className="ball-trajectory-viewer" data-status={availability.state} data-overlay={showOverlay ? "on" : "off"} data-pose={poseTimingPreserved ? showPose ? "on" : "off" : "paused"}>
      <div className="ball-trajectory-stage" style={{ aspectRatio }}>
        {canPlayVideo ? <video ref={videoRef} className="ball-trajectory-video" src={videoSrc ?? undefined} poster={poster ?? undefined} playsInline muted preload="metadata" onError={onVideoError} aria-label="当前视频回放"
          onLoadedMetadata={event => { const video = event.currentTarget; setReplayRate(video, playbackRate); setDurationMs(Number.isFinite(video.duration) ? video.duration * 1000 : 0); if (video.videoWidth > 0 && video.videoHeight > 0) setVideoAspectRatio(video.videoWidth / video.videoHeight); if (pendingSeekRef.current !== null) { video.currentTime = videoTimeForTimestamp(pendingSeekRef.current, videoTimeOriginMs); pendingSeekRef.current = null; } else if (following) video.currentTime = videoTimeForTimestamp(latestMs, videoTimeOriginMs); setCurrentMs(timestampForVideoTime(video.currentTime, videoTimeOriginMs)); }}
          onTimeUpdate={event => setCurrentMs(timestampForVideoTime(event.currentTarget.currentTime, videoTimeOriginMs))}
          onSeeking={() => setSeeking(true)} onSeeked={event => { setSeeking(false); setCurrentMs(timestampForVideoTime(event.currentTarget.currentTime, videoTimeOriginMs)); }}
          onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)} onEnded={() => setPlaying(false)}
        /> : <div className="ball-trajectory-empty"><strong>{error ? "回放暂不可用" : pending ? "视频分析中" : "当前结果没有回放视频"}</strong><span>{hasData ? "拖动时间轴可查看短时球路观测" : availability.description}</span></div>}
        {poseTimingPreserved && showPose && canPlayVideo && !seeking && poseFrame && pose.preview && <PoseSkeletonOverlay frame={poseFrame} preview={pose.preview} aspectRatio={aspectRatio} />}
        {showOverlay && <svg className="ball-trajectory-overlay" viewBox="0 0 100 100" preserveAspectRatio="none" role="img" aria-label={displayMode === "tail" ? "球路短尾迹" : "已播放的完整球路"}>
          {strokes.map((stroke, index) => <polyline key={`stroke-${stroke.points[0].timestamp_ms}-${index}`} points={stroke.points.map(point => `${point.x * 100},${point.y * 100}`).join(" ")} fill="none" stroke={stroke.source === "interpolated" ? "#93b4d5" : "#d8ff66"} strokeOpacity={trajectoryDisplayOpacity(renderedPoints[stroke.pathIndex], pointSegments.get(stroke.points[0])?.display_quality?.detector_confidence.median) * (holdingCompletedFlight ? .75 : 1)} strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" strokeDasharray={stroke.source === "interpolated" ? "4 4" : undefined} vectorEffect="non-scaling-stroke" />)}
          {showPoints && observed.map((point, index) => <circle key={`o-${point.timestamp_ms}-${index}`} cx={point.x * 100} cy={point.y * 100} r="0.25" fill="#b6d0a9" />)}
          {showPoints && interpolated.map((point, index) => <circle key={`i-${point.timestamp_ms}-${index}`} cx={point.x * 100} cy={point.y * 100} r="0.2" fill="#93b4d5" />)}
        </svg>}
        {holdingCompletedFlight && <span className="trajectory-held-label">最近球路观测 · 最多保留 0.6 秒</span>}
        {canPlayVideo && !playing && <button type="button" className="replay-stage-play" aria-label="播放视频" onClick={togglePlayback}><PlaybackIcon playing={false} /></button>}
      </div>
      <div className="ball-trajectory-controls">
        <input className="replay-scrubber" type="range" min="0" max="1" step="0.001" value={progress} disabled={!canPlayVideo && !hasData} onChange={(event) => seek(Number(event.target.value))} aria-label="视频回放时间轴" aria-valuetext={`${currentTimeLabel}，总时长 ${durationLabel}`} style={{ backgroundSize: `${progress * 100}% 100%` }} />
        <div className="replay-control-row">
          <button className="replay-play-toggle" type="button" onClick={togglePlayback} disabled={!canPlayVideo} aria-label={playing ? "暂停视频" : "播放视频"} title={playing ? "暂停视频" : "播放视频"}><PlaybackIcon playing={playing} /></button>
          <span className="replay-clock"><time>{currentTimeLabel}</time><span aria-hidden="true">/</span><time>{durationLabel}</time></span>
          <label className="replay-speed"><span>倍速</span><select aria-label="回放倍速" value={playbackRate} disabled={!canPlayVideo} onChange={event => { const rate = Number(event.target.value); if (setReplayRate(videoRef.current, rate)) setPlaybackRate(rate); }}>{PLAYBACK_RATES.map(rate => <option key={rate} value={rate}>{rate}×</option>)}</select></label>
          <button className="replay-fullscreen" type="button" onClick={toggleFullscreen} disabled={!canPlayVideo || !fullscreenAvailable} aria-label={fullscreen ? "退出全屏" : "全屏回放"} title={fullscreenAvailable ? fullscreen ? "退出全屏" : "全屏回放" : "此浏览器暂不支持全屏回放"}><svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d={fullscreen ? "M9 3v6H3m18 0h-6V3M3 15h6v6m6 0v-6h6" : "M9 3H3v6m12-6h6v6M3 15v6h6m6 0h6v-6"} fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" /></svg></button>
        </div>
      </div>
      {(playbackError || fullscreenError) && <p className="replay-control-feedback" role="status">{playbackError || fullscreenError}</p>}
      <details className="ball-trajectory-options">
        <summary><span>分析工具</span><span>{showPose && poseTimingPreserved || showOverlay ? "叠层已开启" : "按需开启画面叠层"}</span></summary>
        <div className="replay-overlay-switches" aria-label="回放显示设置"><label><input type="checkbox" checked={showPose && poseTimingPreserved} disabled={!poseTimingPreserved || !canPlayVideo || !hasPoseSource} onChange={event => setShowPose(event.target.checked)} />人体骨架</label><label><input type="checkbox" checked={showOverlay} disabled={!hasData} onChange={event => setShowOverlay(event.target.checked)} />球路</label><span role="status">{!poseTimingPreserved ? "回放时间对应尚未确认，人体骨架已暂停。" : !hasPoseSource ? "此报告未提供人体骨架，视频仍可回放。" : !showPose ? "人体骨架已隐藏" : seeking ? "正在定位画面…" : pose.pending ? "正在读取人体骨架…" : pose.error ? "人体骨架暂时无法读取，视频仍可回放。" : poseFrame ? "骨架随回放同步" : pending ? "人体姿态识别中" : "当前时刻暂无可靠人体姿态"}</span></div>
        {showOverlay && <div className="trajectory-view-modes" aria-label="球路显示方式">{([["segment", "当前候选球路"], ["tail", "短尾迹"], ["overview", "球路概览"]] as const).map(([mode, label]) => <button type="button" key={mode} aria-pressed={displayMode === mode} onClick={() => { setDisplayMode(mode); setSelectedSegment(null); }}>{label}</button>)}</div>}
        {showOverlay && renderedPoints.length > 1 && displayMode !== "overview" && <p className="ball-trajectory-analysis">当前显示 {renderedPoints.length} 条候选，尚未确认唯一比赛用球；片段之间不连线。</p>}
        <div className="ball-trajectory-options-body">
          <label><input type="checkbox" checked={showPoints} disabled={!showOverlay} onChange={event => setShowPoints(event.target.checked)} />显示识别点</label>
          <label><input type="checkbox" checked={showInterpolated} disabled={!showOverlay} onChange={event => setShowInterpolated(event.target.checked)} />显示短缺口插值</label>
        </div>
      {showOverlay && segments.length > 0 && <div className="ball-trajectory-segments">
        <label>球路片段 <select aria-label="球路片段" value={safeSelectedSegment ?? ""} onChange={event => {
          const index = event.target.value === "" ? null : Number(event.target.value); setSelectedSegment(index);
          if (index !== null) { const item = segments[index]; setFollowLatest(false); setCurrentMs(item.start_ms); if (videoRef.current) videoRef.current.currentTime = videoTimeForTimestamp(item.start_ms, videoTimeOriginMs); }
        }}><option value="">随播放自动选择</option>{segments.map((item, index) => (!movingOnly || item.analysis?.motion_status !== "stationary" && item.analysis?.motion_status !== "insufficient") && <option key={item.segment_id} value={index}>片段 {index + 1} · {(item.start_ms / 1000).toFixed(2)}–{(item.end_ms / 1000).toFixed(2)}s · {motionLabel(item.analysis?.motion_status)}</option>)}</select></label>
        {segment && <button type="button" onClick={() => { videoRef.current?.pause(); seek(1); }}>查看这一段完整轨迹</button>}
        <label><input type="checkbox" checked={movingOnly} onChange={event => { setMovingOnly(event.target.checked); setSelectedSegment(null); }} />仅移动片段</label>
        {trajectory?.source.is_partial && <button type="button" onClick={() => { setFollowLatest(true); setSelectedSegment(null); }}>{following ? "跟随最新观测中" : "跟随最新观测"}</button>}
      </div>}
      {showOverlay && segment && <p className="ball-trajectory-analysis">片段时长 {((segment.end_ms - segment.start_ms) / 1000).toFixed(2)}s · 画面内平均速度 {segment.analysis?.mean_speed_normalized_per_s?.toFixed(3) ?? "—"} 坐标/秒（非实际球速）</p>}
      {showOverlay && <div className="ball-trajectory-legend"><span><i className="observed" />球路观测 · 浅色为较低检测置信度</span>{showInterpolated && <span><i className="interpolated" />短缺口插值</span>}<span>{displayMode === "tail" ? "当前帧前 0.75 秒" : displayMode === "overview" ? "已播放的最近 12 段 · 片段间不连线" : "缺少新观测 0.6 秒后自动隐藏 · 不显示未来球路"}</span></div>}
      {showOverlay && edges.length === 0 && <p className="ball-trajectory-analysis">当前时刻没有通过筛选的连续球路。</p>}
      <p className="ball-trajectory-limitations">{isLive ? availability.description : "演示路径不代表模型识别结果。"}</p>
      </details>
    </div>
  );
}
