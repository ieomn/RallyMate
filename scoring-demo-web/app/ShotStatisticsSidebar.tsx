"use client";

import { useEffect, useRef, useState, type RefObject } from "react";
import type { RallyMateApiClient } from "./lib/api-client";
import { parseShotStatistics, type ShotReviewOperation, type ShotStatisticsResponse } from "./lib/shot-statistics";
import ShotStatisticsPanel from "./ShotStatisticsPanel";

type Api = Pick<RallyMateApiClient, "getShotStatistics" | "saveShotReview">;
type ReadState = { scope: string; data: ShotStatisticsResponse | null; pending: boolean; error: string | null };

export function shotVideoTimeMs(video: HTMLVideoElement | null): number | null {
  const raw = video?.dataset.timeOriginMs;
  const origin = raw?.trim() ? Number(raw) : NaN;
  return video && video.readyState >= 1 && video.currentSrc && video.currentSrc === video.src
    && Number.isFinite(video.currentTime) && video.currentTime >= 0 && Number.isFinite(origin)
    ? video.currentTime * 1000 + origin : null;
}

export default function ShotStatisticsSidebar({ api, jobId, sourceSha, enabled, scope, signal, containerRef }: {
  api: Api; jobId?: string; sourceSha?: string | null; enabled: boolean; scope: string;
  signal: AbortSignal; containerRef: RefObject<HTMLDivElement | null>;
}) {
  const [state, setState] = useState<ReadState | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [saving, setSaving] = useState(false);
  const savingRef = useRef(false);
  const [clock, setClock] = useState<{ scope: string; time: number | null } | null>(null);
  const readScope = `${scope}:${sourceSha ?? "none"}:${attempt}`;
  const current = state?.scope === readScope ? state : null;
  const data = current?.data ?? null;
  useEffect(() => {
    if (!enabled || !jobId || !sourceSha) return;
    const controller = new AbortController();
    const requestSignal = AbortSignal.any([signal, controller.signal]);
    void api.getShotStatistics(jobId, requestSignal).then(result => {
      if (requestSignal.aborted) return;
      const parsed = parseShotStatistics(result, jobId, sourceSha);
      if (!parsed) throw new Error("击球统计与当前原视频不匹配。");
      setState({ scope: readScope, data: parsed, pending: false, error: null });
    }).catch(error => {
      if (!requestSignal.aborted) setState({ scope: readScope, data: null, pending: false,
        error: error instanceof Error ? error.message : "击球统计读取失败，请重试。" });
    });
    return () => controller.abort();
  }, [api, jobId, enabled, sourceSha, readScope, signal]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    let attached: HTMLVideoElement | null = null;
    const sample = () => {
      setClock({ scope, time: shotVideoTimeMs(attached) });
    };
    const detach = () => {
      for (const event of ["timeupdate", "loadedmetadata", "seeked", "emptied"]) attached?.removeEventListener(event, sample);
    };
    const connect = () => {
      const video = container.querySelector<HTMLVideoElement>("video[data-time-origin-ms]");
      if (video !== attached) {
        detach(); attached = video;
        for (const event of ["timeupdate", "loadedmetadata", "seeked", "emptied"]) attached?.addEventListener(event, sample);
      }
      sample();
    };
    connect();
    const observer = new MutationObserver(connect);
    observer.observe(container, { subtree: true, childList: true, attributes: true, attributeFilter: ["src", "data-time-origin-ms"] });
    return () => { observer.disconnect(); detach(); };
  }, [containerRef, scope]);

  const seek = (timestampMs: number) => {
    containerRef.current?.querySelector<HTMLVideoElement>("video")?.pause();
    window.dispatchEvent(new CustomEvent("rallymate:seek-video", { detail: { timestampMs, jobId, preferOriginal: true } }));
  };
  const review = async (operation: ShotReviewOperation) => {
    if (!data || !jobId || !enabled || savingRef.current || signal.aborted) throw new Error("当前视频暂不能保存标记。");
    containerRef.current?.querySelector<HTMLVideoElement>("video")?.pause();
    const existing = operation.operation !== "add" ? data.events.find(event => event.id === operation.event_id) : null;
    if (operation.operation !== "add" && !existing) throw new Error("事件已改变，请重新读取。");
    const status = ["dismiss", "remove"].includes(operation.operation) ? "dismissed" : operation.operation === "restore" ? "candidate" : "confirmed";
    const position = "position_court" in operation && operation.position_court ? { ...operation.position_court, source: "manual" } : null;
    const event: Record<string, unknown> = operation.operation === "add"
      ? { id: `manual-${crypto.randomUUID()}`, kind: operation.kind, status, timestamp_ms: operation.timestamp_ms, frame_index: null, position_court: position }
      : { id: existing!.id, kind: existing!.kind, status, timestamp_ms: existing!.timestamp_ms, frame_index: existing!.frame_index,
          ...(position ? { position_court: position } : {}) };
    savingRef.current = true; setSaving(true);
    try {
      const result = await api.saveShotReview(jobId, { mutation_id: crypto.randomUUID(), expected_revision: data.revision,
        source_video_sha256: data.source.video_sha256, event }, signal);
      if (!signal.aborted) setState({ scope: readScope, data: result, pending: false, error: null });
    } catch (error) {
      if (!signal.aborted) setState({ scope: readScope, data, pending: false,
        error: error instanceof Error ? error.message : "标记未保存，请重新读取后重试。" });
      throw error;
    } finally { savingRef.current = false; setSaving(false); }
  };
  return <ShotStatisticsPanel data={data} jobId={jobId} pending={current?.pending ?? enabled}
    error={current?.error} saving={saving} currentTimeMs={clock?.scope === scope ? clock.time : null}
    getCurrentTimeMs={() => shotVideoTimeMs(containerRef.current?.querySelector<HTMLVideoElement>("video[data-time-origin-ms]") ?? null)}
    onReview={enabled && data ? review : undefined} onRetry={enabled ? () => setAttempt(value => value + 1) : undefined} onSeek={seek} />;
}
