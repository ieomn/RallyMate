"use client";

/* eslint-disable @next/next/no-img-element -- This is a local, transient video-frame data URL used for interactive corner selection. */

import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import type { TrajectoryPreviewResponse } from "./lib/api-types";
import { calibrationWindowAt, courtHomography, courtPlaybackFrame, courtProjectionOf, courtReferenceRegions, type Corner, type CourtReferenceRegion } from "./lib/court-simulation";
import { courtCalibrationKey, parseCourtCalibration, type SavedCourtCalibration } from "./lib/court-calibration";
import { waitForPresentedVideoFrame } from "./lib/video-frame-capture";
import { timestampForVideoTime } from "./lib/trajectory-viewer";

const cornerNames = ["远端左角", "远端右角", "近端右角", "近端左角"];
const clock = (milliseconds: number) => `${Math.floor(Math.max(0, milliseconds) / 60000)}:${String(Math.floor(Math.max(0, milliseconds) / 1000) % 60).padStart(2, "0")}`;
const subscribeStorage = (callback: () => void) => { window.addEventListener("storage", callback); return () => window.removeEventListener("storage", callback); };
const readStorage = (key: string | null) => { try { return key ? localStorage.getItem(key) : null; } catch { return null; } };

type CourtSimulationProps = { trajectory: TrajectoryPreviewResponse | null; pending: boolean };

export default function CourtSimulationPanel(props: CourtSimulationProps) {
  // Reset playback, draft corners and in-flight capture when the upload/source changes.
  return <CourtSimulationContent key={courtCalibrationKey(props.trajectory) ?? props.trajectory?.job_id ?? "unloaded"} {...props} />;
}

function CourtSimulationContent({ trajectory, pending }: CourtSimulationProps) {
  const captureController = useRef<AbortController | null>(null);
  const [saved, setSaved] = useState<{ sourceKey: string; value: SavedCourtCalibration | null } | null>(null);
  const [snapshot, setSnapshot] = useState<{ url: string; timestampMs: number } | null>(null);
  const [region, setRegion] = useState<CourtReferenceRegion>("doubles");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [corners, setCorners] = useState<Corner[]>([]);
  const [fixedCamera, setFixedCamera] = useState(false);
  const [notice, setNotice] = useState("");
  const [capturing, setCapturing] = useState(false);
  const [time, setTime] = useState<number | null>(null);
  const [playing, setPlaying] = useState(false);
  const sourceKey = courtCalibrationKey(trajectory);
  const persisted = useSyncExternalStore(subscribeStorage, () => readStorage(sourceKey), () => null);
  const restored = useMemo(() => sourceKey ? parseCourtCalibration(persisted, sourceKey) : null, [persisted, sourceKey]);
  const activeSaved = saved?.sourceKey === sourceKey ? saved.value : restored;
  const manualMatrix = useMemo(() => activeSaved ? courtHomography(activeSaved.corners, activeSaved.region) : null, [activeSaved]);
  const savedWindow = activeSaved ? calibrationWindowAt(trajectory, activeSaved.timestampMs) : null;
  const snapshotWindow = snapshot ? calibrationWindowAt(trajectory, snapshot.timestampMs) : null;
  const projection = useMemo(() => courtProjectionOf(trajectory, manualMatrix, savedWindow), [trajectory, manualMatrix, savedWindow]);
  const { start, end, current, sliderTime, outsideRange, paths, ball } = useMemo(() => courtPlaybackFrame(projection.points, time, savedWindow), [projection.points, time, savedWindow]);
  const plot = (point: Corner) => ({ x: 84 + point.x * 212, y: 52 + point.y * 460 });
  const ballPosition = ball ? plot(ball) : null;
  const fitted = useMemo(() => courtHomography(corners, region), [corners, region]);
  useEffect(() => () => captureController.current?.abort(), []);

  function applyCalibration() {
    if (!fitted || !fixedCamera || !snapshot || !sourceKey || !snapshotWindow) return;
    const value: SavedCourtCalibration = { version: 1, sourceKey, region, corners, timestampMs: snapshot.timestampMs, fixedCamera: true };
    setSaved({ sourceKey, value }); setPlaying(false); setTime(null); setSnapshot(null);
    try { localStorage.setItem(sourceKey, JSON.stringify(value)); setNotice("标定已保存在本浏览器，刷新后仍可回看；不改变评分。"); }
    catch { setNotice("标定已应用，但浏览器禁止保存；关闭页面后需重新标定。"); }
  }

  function clearCalibration() {
    try { if (sourceKey) localStorage.removeItem(sourceKey); } catch { /* Storage may be disabled. */ }
    setSaved(sourceKey ? { sourceKey, value: null } : null); setPlaying(false); setTime(null); setNotice("已清除本视频的展示标定。");
  }

  useEffect(() => {
    if (!playing || end <= start) return;
    let frame = 0, previous = performance.now();
    const tick = (now: number) => {
      const elapsed = Math.min(now - previous, 250); previous = now;
      setTime(value => { const next = Math.max(start, value ?? start) + elapsed; return next >= end ? start : next; });
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [playing, start, end]);

  useEffect(() => {
    const reveal = (event: Event) => {
      const detail = (event as CustomEvent<{ jobId?: string; timestampMs?: number }>).detail;
      if (!Number.isFinite(detail?.timestampMs) || detail.timestampMs! < 0 || detail?.jobId && detail.jobId !== trajectory?.job_id) return;
      setPlaying(false); setTime(detail.timestampMs!);
    };
    window.addEventListener("rallymate:seek-video", reveal);
    return () => window.removeEventListener("rallymate:seek-video", reveal);
  }, [trajectory?.job_id]);

  useEffect(() => {
    const syncOriginalSeek = (event: Event) => {
      const replay = document.getElementById("evidence");
      if (!trajectory?.job_id || replay?.dataset.jobId !== trajectory.job_id) return;
      const video = replay.querySelector<HTMLVideoElement>("#replay-view-original video[data-replay-role='original']");
      // Ignore other players, detached old elements and events while a replacement source is loading.
      if (!video || event.target !== video || !video.currentSrc || video.currentSrc !== video.src) return;
      const rawOrigin = video.dataset.timeOriginMs;
      const origin = rawOrigin?.trim() ? Number(rawOrigin) : NaN;
      if (!Number.isFinite(origin) || !Number.isFinite(video.currentTime) || video.currentTime < 0) return;
      const timestamp = timestampForVideoTime(video.currentTime, origin);
      if (!Number.isFinite(timestamp) || timestamp < 0) return;
      setPlaying(false); setTime(timestamp);
    };
    // Native media seek events do not bubble. Capture also handles videos mounted after this panel.
    document.addEventListener("seeking", syncOriginalSeek, true);
    document.addEventListener("seeked", syncOriginalSeek, true);
    return () => {
      document.removeEventListener("seeking", syncOriginalSeek, true);
      document.removeEventListener("seeked", syncOriginalSeek, true);
    };
  }, [trajectory?.job_id, sourceKey]);

  async function captureFrame() {
    setNotice(""); setSettingsOpen(true);
    const replay = document.getElementById("evidence");
    const video = (!trajectory?.job_id || replay?.dataset.jobId === trajectory.job_id) ? replay?.querySelector<HTMLVideoElement>("#replay-view-original video[data-replay-role='original']") : null;
    if (!replay || !video) { setNotice("取图仅支持原视频。请在上方训练回放切换到“原视频”并加载画面后重试；标注回放不能用于取图。"); return; }
    captureController.current?.abort();
    const controller = new AbortController(); captureController.current = controller;
    const target = { time: video.currentTime, source: video.src, currentSource: video.currentSrc, origin: video.dataset.timeOriginMs, jobId: replay.dataset.jobId };
    const origin = target.origin?.trim() ? Number(target.origin) : NaN;
    setCapturing(true);
    try {
      if (!Number.isFinite(origin)) throw new Error("time origin unavailable");
      const presented = await waitForPresentedVideoFrame(video, { signal: controller.signal });
      if (controller.signal.aborted) return;
      if (!presented) { setNotice("当前画面尚未显示，未保存截图。请在上方原视频中播放到目标画面后暂停，再点击取图。"); return; }
      // Other microtasks can replace or seek media after readiness resolves.
      // Validate the selected frame again in the same turn as drawImage.
      const currentReplay = document.getElementById("evidence");
      if (currentReplay !== replay || currentReplay.dataset.jobId !== target.jobId
        || currentReplay.querySelector("#replay-view-original video[data-replay-role='original']") !== video
        || video.src !== target.source || (target.currentSource && video.currentSrc !== target.currentSource)
        || (video.src && video.currentSrc && video.src !== video.currentSrc)
        || !Number.isFinite(video.currentTime) || Math.abs(video.currentTime - target.time) >= .001
        || video.dataset.timeOriginMs !== target.origin || video.seeking || !(video.readyState >= 2)
        || !(video.videoWidth > 0 && video.videoHeight > 0)) {
        setNotice("取图期间视频画面已变化，未保存截图。请暂停在目标画面后重新取图。"); return;
      }
      const canvas = document.createElement("canvas");
      canvas.width = Math.min(video.videoWidth, 1280); canvas.height = Math.round(canvas.width * video.videoHeight / video.videoWidth);
      const context = canvas.getContext("2d");
      if (!context) throw new Error("canvas");
      context.drawImage(video, 0, 0, canvas.width, canvas.height);
      setSnapshot({ url: canvas.toDataURL("image/jpeg", .9), timestampMs: timestampForVideoTime(target.time, origin) }); setCorners([]); setFixedCamera(false);
    } catch { if (!controller.signal.aborted) setNotice("当前视频无法取图，请打开本次上传的视频后重试。"); }
    finally { if (!controller.signal.aborted) setCapturing(false); }
  }

  return <section className="court-simulation-panel" id="court" aria-labelledby="court-heading">
    <div className="report-section-heading"><div><span className="process-eyebrow">01 · 球路回看</span><h2 id="court-heading">场地俯视图模拟</h2></div><span className="court-status">{pending ? "分析中" : projection.status === "calibrated" ? manualMatrix ? "手工标定" : "已标定" : "待定位球场"}</span></div>
    <div className="court-stage">
      <svg className="court-simulation" viewBox="0 0 380 565" role="img" aria-label="网球场俯视模拟；球路为地面投影，不表示真实落点">
        <rect className="court-runoff" x="15" y="18" width="350" height="530" rx="18" />
        <rect className="court-surface" x="84" y="52" width="212" height="460" />
        <g className="court-markings"><rect x="84" y="52" width="212" height="460" /><path d="M110.5 52V512M269.5 52V512M110.5 158H269.5M110.5 406H269.5M190 158V406M184 52H196M184 512H196" /></g>
        <path className="court-net" d="M72 282H308" />
        {paths.filter(path => path.length > 1).map((path, index) => <polyline key={`${path[0].segment_id}-${index}`} className={`court-ball-path${path.some(point => point.source === "interpolated") ? " is-interpolated" : ""}`} points={path.map(point => { const p = plot(point); return `${p.x},${p.y}`; }).join(" ")} />)}
        {ballPosition && <g className="court-ball"><circle cx={ballPosition.x} cy={ballPosition.y} r="10" /><circle cx={ballPosition.x} cy={ballPosition.y} r="4" /></g>}
        <text className="court-end-label" x="190" y="37" textAnchor="middle">远端</text><text className="court-end-label" x="190" y="538" textAnchor="middle">近端</text>
      </svg>
      {!projection.points.length && <div className="court-empty"><strong>{pending ? "球路正在生成" : projection.status === "calibrated" ? "此段尚无可投影球路" : "先定位，再回看球路"}</strong><p>{pending ? "完成后可播放场地中的球路模拟。" : projection.status === "calibrated" ? "标定已保留。请检查机位片段及球的可见性。" : "在原视频画面上标记场地线交点，即可投影已有球路。"}</p><button type="button" className="product-primary" disabled={capturing} onClick={() => void captureFrame()}>标定球场</button></div>}
    </div>
    <div className="court-playback"><button type="button" disabled={end <= start} aria-label={playing ? "暂停球路模拟" : "播放球路模拟"} onClick={() => { if (!playing && (outsideRange || current >= end)) setTime(start); setPlaying(value => !value); }}>{playing ? "暂停" : "播放"}</button><input type="range" aria-label="球路模拟时间" aria-valuetext={outsideRange ? `当前原视频时间 ${clock(current)}；超出球路片段，拖动可回看片段` : clock(current)} min={start} max={Math.max(start + 1, end)} step="10" value={sliderTime} disabled={end <= start} onChange={event => { setPlaying(false); setTime(Number(event.target.value)); }} /><time title="原视频时间坐标">{clock(current)}</time></div>
    {projection.points.length > 0 && <p className="court-caption">球路片段 {(start / 1000).toFixed(1)}–{(end / 1000).toFixed(1)} 秒。{outsideRange ? "当前时间超出可投影片段，已隐藏球与轨迹；拖动滑块或点击播放可回看此片段。" : !ball ? "当前时刻缺少连续球路，已隐藏球与轨迹。" : ""}</p>}
    <p className="court-caption">{projection.status === "calibrated" ? "球路为地面投影示意，空中球的位置不是真实落点。" : "场地示意 · 尚未绘制未经标定的球路。"}</p>
    {savedWindow && <p className="court-caption">已投影 {projection.points.length} 个球路点 · 适用片段 {(savedWindow.start_timestamp_ms / 1000).toFixed(1)}–{(savedWindow.end_timestamp_ms / 1000).toFixed(1)} 秒。移动或缩放后的片段需重新取图标定。</p>}
    <details className="court-settings" open={settingsOpen} onToggle={event => setSettingsOpen(event.currentTarget.open)}><summary>球场设置与投影说明</summary><p>{projection.reason_zh}</p><button type="button" className="product-secondary" disabled={capturing} onClick={() => void captureFrame()}>{capturing ? "正在等待当前画面…" : "从原视频当前画面取图"}</button>{notice && <p role="status">{notice}</p>}
      {snapshot && <div className="court-calibration">
        <label className="court-reference-choice">选择画面中能完整看清的区域<select value={region} onChange={event => { setRegion(event.target.value as CourtReferenceRegion); setCorners([]); }}>{Object.entries(courtReferenceRegions).map(([key, value]) => <option key={key} value={key}>{value.label}</option>)}</select></label>
        <div className="court-calibration-guide"><svg viewBox="0 0 120 170" role="img" aria-label="标点顺序示意：远左1、远右2、近右3、近左4"><rect x="20" y="10" width="80" height="150" /><path d="M30 10V160M90 10V160M30 44.6H90M30 125.4H90M60 44.6V125.4M15 85H105" />{courtReferenceRegions[region].targets.map((point, index) => <g key={index}><circle cx={20 + point.x * 80} cy={10 + point.y * 150} r="8" /><text x={20 + point.x * 80} y={13 + point.y * 150} textAnchor="middle">{index + 1}</text></g>)}</svg><p>{courtReferenceRegions[region].hint} 左右以当前画面为准。看不清时先在原视频换一帧，不要猜测画面外的位置。</p></div>
        <p aria-live="polite">{corners.length < 4 ? `第 ${corners.length + 1} 步：点选所选区域的${cornerNames[corners.length]}。` : fitted ? "四个交点已选好。确认本片段机位固定后应用。" : "选点顺序、间距或区域面积不合理，请撤销或重新选择。"}</p>
        <button type="button" className="court-calibration-image" aria-label={corners.length < 4 ? `在画面上点选${cornerNames[corners.length]}` : "已选择四个场地线交点"} disabled={corners.length >= 4} onClick={event => { if (event.detail === 0) return; const rect = event.currentTarget.getBoundingClientRect(); const point = { x: (event.clientX - rect.left) / rect.width, y: (event.clientY - rect.top) / rect.height }; if (point.x >= 0 && point.x <= 1 && point.y >= 0 && point.y <= 1) setCorners(value => value.length < 4 ? [...value, point] : value); }}><img src={snapshot.url} alt="当前视频帧，用于点选场地线交点" /><svg viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">{corners.length > 1 && <polyline points={[...corners, ...(corners.length === 4 ? [corners[0]] : [])].map(point => `${point.x * 100},${point.y * 100}`).join(" ")} />}</svg>{corners.map((point, index) => <span className="court-point-number" key={index} style={{ left: `${point.x * 100}%`, top: `${point.y * 100}%` }}>{index + 1}</span>)}</button>
        <p>取图时间 {(snapshot.timestampMs / 1000).toFixed(2)} 秒。{snapshotWindow ? `可用于 ${(snapshotWindow.start_timestamp_ms / 1000).toFixed(1)}–${(snapshotWindow.end_timestamp_ms / 1000).toFixed(1)} 秒的稳定机位片段。` : "该时刻缺少稳定机位证据，请换到机位稳定的片段取图。"}</p>
        <label className="court-fixed-camera"><input type="checkbox" checked={fixedCamera} onChange={event => setFixedCamera(event.target.checked)} />我确认上述片段的机位固定，没有移动或缩放。</label>
        <div className="court-calibration-actions"><button type="button" className="product-secondary" disabled={!corners.length} onClick={() => setCorners(value => value.slice(0, -1))}>撤销上一点</button><button type="button" className="product-secondary" disabled={!corners.length} onClick={() => setCorners([])}>重新选点</button><button type="button" className="product-primary" disabled={!fitted || !fixedCamera || !snapshotWindow || !sourceKey} onClick={applyCalibration}>应用展示标定</button></div>
      </div>}
      {manualMatrix && <button type="button" className="product-secondary" onClick={clearCalibration}>清除手工标定</button>}
    </details>
  </section>;
}
