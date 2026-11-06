"use client";

import { useId, useState, type KeyboardEvent, type MouseEvent } from "react";
import { formatShotTime, shotCourtDisplay, shotCourtPoint, shotSeekDetail, shotStatisticsCount,
  type ShotEvent, type ShotReviewOperation, type ShotStatisticsResponse } from "./lib/shot-statistics";
import "./shot-statistics.css";

type Marking = { timestampMs: number; eventId?: string; point: { x: number; y: number } | null };
export type ShotStatisticsPanelProps = {
  data: ShotStatisticsResponse | null; jobId?: string; pending?: boolean; error?: string | null;
  saving?: boolean; currentTimeMs?: number | null; onReview?: (operation: ShotReviewOperation) => void | Promise<unknown>;
  getCurrentTimeMs?: () => number | null; onRetry?: () => void; onSeek?: (timestampMs: number) => void;
};

const label = (event: Pick<ShotEvent, "kind">) => event.kind === "hit" ? "击球" : "落地";

export default function ShotStatisticsPanel(props: ShotStatisticsPanelProps) {
  // A new job must never inherit a draft point or selected event from the old video.
  return <ShotStatisticsContent key={props.jobId ?? "no-job"} {...props} />;
}

export function ShotStatisticsContent({ data, jobId, pending = false, error, saving = false,
  currentTimeMs = null, getCurrentTimeMs, onReview, onRetry, onSeek }: ShotStatisticsPanelProps) {
  const headingId = useId();
  const [filter, setFilter] = useState<"active" | "candidate" | "dismissed">("active");
  const [selected, setSelected] = useState<string | null>(null);
  const [marking, setMarking] = useState<Marking | null>(null);
  const [saveNotice, setSaveNotice] = useState<string | null>(null);
  const scoped = data?.job_id === jobId ? data : null;
  const writable = !!scoped && !!onReview && !pending && !saving && scoped.status !== "unavailable";
  const timeValid = typeof currentTimeMs === "number" && Number.isFinite(currentTimeMs) && !!scoped
    && currentTimeMs >= scoped.source.start_ms && currentTimeMs <= scoped.source.end_ms;
  const candidates = scoped ? scoped.summary.candidate_hit_count + scoped.summary.candidate_bounce_count : 0;
  const confirmedBounces = scoped?.events.filter(event => event.kind === "bounce" && event.status === "confirmed" && event.position_court) ?? [];
  const events = scoped?.events.filter(event => filter === "active" ? event.status !== "dismissed" : event.status === filter) ?? [];
  const unmapped = scoped ? scoped.summary.confirmed_bounce_count - scoped.summary.mapped_bounce_count : 0;

  function seek(event: Pick<ShotEvent, "id" | "timestamp_ms">) {
    if (!jobId) return;
    setSelected(event.id);
    if (onSeek) onSeek(event.timestamp_ms);
    else window.dispatchEvent(new CustomEvent("rallymate:seek-video", { detail: shotSeekDetail(event, jobId) }));
  }

  async function review(operation: ShotReviewOperation) {
    if (!writable || !onReview) return;
    setSaveNotice(null);
    try {
      await onReview(operation);
      setMarking(null);
    } catch {
      setSaveNotice("本次修改未能保存，请重试。确认数量仍以已保存结果为准。");
    }
  }

  function startMarking(event?: ShotEvent) {
    const timestampMs = event ? event.timestamp_ms : getCurrentTimeMs ? getCurrentTimeMs() : currentTimeMs;
    if (!writable || timestampMs === null || !scoped || !Number.isFinite(timestampMs)
      || timestampMs < scoped.source.start_ms || timestampMs > scoped.source.end_ms) return;
    setMarking({ timestampMs, eventId: event?.id, point: event?.position_court ? { x: event.position_court.x, y: event.position_court.y } : null });
    setSaveNotice(null);
    if (event) seek(event);
    else if (onSeek) onSeek(timestampMs);
  }

  function recordHit() {
    const timestampMs = getCurrentTimeMs ? getCurrentTimeMs() : currentTimeMs;
    if (!writable || timestampMs === null || !scoped || !Number.isFinite(timestampMs)
      || timestampMs < scoped.source.start_ms || timestampMs > scoped.source.end_ms) return;
    void review({ operation: "add", kind: "hit", timestamp_ms: timestampMs });
  }

  function choosePoint(event: MouseEvent<HTMLButtonElement>) {
    const point = shotCourtPoint(event.clientX, event.clientY, event.currentTarget.getBoundingClientRect());
    if (point) setMarking(value => value ? { ...value, point } : value);
  }

  function movePoint(event: KeyboardEvent<HTMLButtonElement>) {
    if (!["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Enter", " "].includes(event.key)) return;
    event.preventDefault();
    const dx = event.key === "ArrowLeft" ? -.025 : event.key === "ArrowRight" ? .025 : 0;
    const dy = event.key === "ArrowUp" ? -.025 : event.key === "ArrowDown" ? .025 : 0;
    setMarking(value => value ? { ...value, point: { x: Math.max(-.5, Math.min(1.5, (value.point?.x ?? .5) + dx)), y: Math.max(-.25, Math.min(1.25, (value.point?.y ?? .5) + dy)) } } : value);
  }

  return <aside className="shot-statistics" aria-labelledby={headingId} aria-busy={pending || saving}>
    <div className="shot-statistics-overview">
      <div className="shot-statistics-heading"><h3 id={headingId}>击球与落点</h3><span>{saving ? "正在保存…" : scoped?.status === "partial" ? "分析未结束" : scoped?.status === "reviewed" ? "已复核" : "视频复核"}</span></div>
      <div className="shot-statistics-counts">
        <div><span>已确认击球</span><strong>{shotStatisticsCount(scoped, "hit") ?? "—"}</strong></div>
        <div><span>已确认落地</span><strong>{shotStatisticsCount(scoped, "bounce") ?? "—"}</strong></div>
      </div>
      <p className="shot-statistics-note">{scoped?.status === "unreviewed" ? "尚未复核。候选不计入次数，空白不代表没有击球。" : "只统计已确认事件，不等同于全片总次数。"}</p>
      {pending && !scoped && <p className="shot-statistics-state" role="status">正在读取击球与落点候选…</p>}
      {!pending && !scoped && !error && <p className="shot-statistics-state">当前任务暂未提供可复核的击球与落点记录。</p>}
      {(error || saveNotice) && <div className="shot-statistics-error" role="alert"><p>{error || saveNotice}</p>{onRetry && <button type="button" className="shot-statistics-secondary" disabled={pending || saving} onClick={onRetry}>重新读取</button>}</div>}
      <div className="shot-statistics-quick-actions">
        <button type="button" disabled={!writable || !timeValid} onClick={recordHit}>记一次击球</button>
        <button type="button" disabled={!writable || !timeValid} onClick={() => startMarking()}>标记落点</button>
      </div>
      <p className="shot-statistics-note">{timeValid ? `当前画面 ${formatShotTime(currentTimeMs!)} · 看清触球或落地后再记录。` : "播放原视频并停在清晰触球或落地画面，即可补记。"}</p>
    </div>

    <div className="shot-statistics-map">
      <div className="shot-statistics-court-heading"><h4>落点分布</h4><span>{scoped ? `${scoped.summary.mapped_bounce_count} 个已定位` : "等待定位"}</span></div>
      <div className={`shot-statistics-court${marking ? " is-marking" : ""}`}>
        <svg viewBox="0 0 200 150" preserveAspectRatio="none" role="img" aria-label="网球场俯视图，上方为远端，下方为近端。仅显示已确认落地的位置。">
          <rect x="50" y="25" width="100" height="100" fill="#24483b" stroke="#b5c5b1" strokeWidth=".7" />
          <path d="M62.49 25V125 M137.51 25V125 M62.49 48.07H137.51 M62.49 101.93H137.51 M100 48.07V101.93" fill="none" stroke="#adbfaa" strokeWidth=".6" />
          <path d="M47 75H153" stroke="#d0d7cb" strokeWidth="1" strokeDasharray="1 1" />
          <path d="M100 25V27 M100 123V125" stroke="#adbfaa" strokeWidth=".6" />
        </svg>
        <span className="shot-statistics-court-label far">远端</span><span className="shot-statistics-court-label near">近端</span>
        {confirmedBounces.map((event, index) => <button type="button" key={event.id} className={`shot-statistics-dot${event.position_court?.source === "calibrated_ground_projection" ? " is-calibrated" : ""}${selected === event.id ? " is-selected" : ""}`}
          style={shotCourtDisplay(event.position_court!)} disabled={!!marking}
          aria-label={`落点 ${index + 1}，${formatShotTime(event.timestamp_ms)}，${event.position_court?.source === "manual" ? "人工标记" : "标定映射"}，点击回看`}
          onClick={() => seek(event)}>{index + 1}</button>)}
        {!confirmedBounces.length && !marking && <p className="shot-statistics-map-empty">{unmapped > 0 ? "落地已确认，位置尚未标记" : "确认落地后在球场上标记位置"}</p>}
        {marking?.point && <span className="shot-statistics-dot is-draft" style={shotCourtDisplay(marking.point)} aria-hidden="true">+</span>}
        {marking && <button type="button" className="shot-statistics-court-capture" aria-label="选择落点：点击球场或使用方向键移动，按回车选中中心" disabled={saving} onClick={choosePoint} onKeyDown={movePoint} />}
      </div>
      <div className="shot-statistics-map-legend"><span>人工标记</span><span>标定映射</span></div>
      {unmapped > 0 && <p className="shot-statistics-note">还有 {unmapped} 次已确认落地未定位，可在列表中补标。</p>}
      {marking && <div className="shot-statistics-marking" role="status"><p>正在标记 {formatShotTime(marking.timestampMs)} 的落点。点击球场选择位置，可标记界外位置。</p><div>
        <button type="button" disabled={!marking.point || saving} onClick={() => marking.point && void review(marking.eventId ? { operation: "confirm", event_id: marking.eventId, position_court: marking.point } : { operation: "add", kind: "bounce", timestamp_ms: marking.timestampMs, position_court: marking.point })}>保存落点</button>
        <button type="button" disabled={saving} onClick={() => setMarking(null)}>取消</button>
      </div></div>}
    </div>

    <div className="shot-statistics-event-section">
      <div className="shot-statistics-filter" role="group" aria-label="筛选击球与落地记录">
        <button type="button" aria-pressed={filter === "active"} onClick={() => setFilter("active")}>全部记录</button>
        <button type="button" aria-pressed={filter === "candidate"} onClick={() => setFilter("candidate")}>待确认 {candidates || ""}</button>
        {!!scoped?.summary.dismissed_count && <button type="button" aria-pressed={filter === "dismissed"} onClick={() => setFilter("dismissed")}>已排除</button>}
      </div>
      <ol className="shot-statistics-events" aria-label="按视频时间排列的击球与落地记录">
        {events.map(event => <li key={event.id} data-shot-event-id={event.id} data-shot-status={event.status}>
          <div className="shot-statistics-event-main"><button type="button" className="shot-statistics-event-seek" onClick={() => seek(event)} aria-label={`回看 ${formatShotTime(event.timestamp_ms)} 的${label(event)}`}><time>{formatShotTime(event.timestamp_ms)}</time><span>{label(event)}</span></button><span className={`shot-statistics-status is-${event.status}`}>{event.status === "confirmed" ? "已确认" : event.status === "dismissed" ? "已排除" : "待确认"}</span></div>
          {event.kind === "bounce" && event.status === "confirmed" && <span className="shot-statistics-event-location">{event.position_court ? event.position_court.source === "manual" ? "人工标记位置" : "由球场标定定位" : "位置尚未标记"}</span>}
          <div className="shot-statistics-event-actions">
            {event.status === "candidate" && <><button type="button" disabled={!writable} onClick={() => void review({ operation: "confirm", event_id: event.id })}>确认{label(event)}</button>{event.kind === "bounce" && <button type="button" disabled={!writable} onClick={() => startMarking(event)}>确认并标点</button>}<button type="button" disabled={!writable} onClick={() => void review({ operation: "dismiss", event_id: event.id })}>排除</button></>}
            {event.status === "confirmed" && <>{event.kind === "bounce" && <button type="button" disabled={!writable} onClick={() => startMarking(event)}>{event.position_court ? "调整落点" : "补标落点"}</button>}<button type="button" disabled={!writable} onClick={() => void review({ operation: event.source === "manual" ? "remove" : "restore", event_id: event.id })}>{event.source === "manual" ? "删除记录" : "撤回确认"}</button></>}
            {event.status === "dismissed" && <button type="button" disabled={!writable} onClick={() => void review({ operation: "restore", event_id: event.id })}>恢复待确认</button>}
          </div>
        </li>)}
      </ol>
      {!events.length && <p className="shot-statistics-state">{pending ? "候选正在整理。" : filter === "candidate" ? "暂无待确认候选，可在清晰画面补记。" : filter === "dismissed" ? "暂无已排除记录。" : "暂无记录。先回看原视频，再补记清晰击球或落地。"}</p>}
    </div>
    <details><summary>统计说明</summary><p>自动候选用于定位复核，不直接计数。清晰看到球拍触球或球接触地面后再确认；挥拍动作、轨迹转折和球检测点不直接等同于击球或落地。</p><p>人工落点是你在俯视球场上的标记；标定映射来自已确认落地时的球场坐标，不用于毫米级压线判定。</p>{scoped?.limitations_zh.map((text, index) => <p key={`${index}:${text}`}>{text}</p>)}</details>
  </aside>;
}
