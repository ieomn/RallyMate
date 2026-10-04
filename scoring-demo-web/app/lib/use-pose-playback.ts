import { useEffect, useRef, useState } from "react";
import type { PosePreviewResponse } from "./api-types";
import { normalizePosePreview, POSE_WINDOW_MS, poseWindowStart } from "./pose-playback";

export type PoseWindowLoader = (startMs: number, signal: AbortSignal) => Promise<PosePreviewResponse>;

/** Optional, bounded playback windows do not participate in analysis completion. */
export function usePosePlayback({ atMs, jobId, loadWindow, initialPreview, enabled }: { atMs: number; jobId?: string; loadWindow?: PoseWindowLoader; initialPreview?: PosePreviewResponse | null; enabled: boolean }) {
  const start = poseWindowStart(atMs);
  const cache = useRef(new Map<string, PosePreviewResponse>());
  const [state, setState] = useState<{ jobId?: string; start: number; preview: PosePreviewResponse | null; error: string | null } | null>(null);
  useEffect(() => {
    if (!loadWindow || !enabled) return;
    const controller = new AbortController();
    const store = (key: number, preview: PosePreviewResponse) => {
      cache.current.set(`${jobId ?? ""}:${key}`, preview);
      while (cache.current.size > 4) cache.current.delete(cache.current.keys().next().value!);
    };
    const read = async (windowStart: number) => {
      const cached = cache.current.get(`${jobId ?? ""}:${windowStart}`);
      if (cached && !cached.source.is_partial) return cached;
      const raw = await loadWindow(windowStart, controller.signal);
      const parsed = normalizePosePreview(raw, jobId);
      if (!parsed || parsed.source.requested_start_ms !== windowStart) throw new Error("人体姿态数据与当前回放不匹配。");
      if (!controller.signal.aborted) store(windowStart, parsed);
      return parsed;
    };
    void read(start).then(preview => {
      if (controller.signal.aborted) return;
      setState({ jobId, start, preview, error: null });
      // Prefetch just one following window; no full-video pose download.
      void read(start + POSE_WINDOW_MS).catch(() => {});
    }).catch(error => {
      if (!controller.signal.aborted) setState({ jobId, start, preview: null, error: error instanceof Error ? error.message : "人体骨架暂时无法读取。" });
    });
    return () => controller.abort();
  }, [start, jobId, loadWindow, enabled]);
  const supplied = initialPreview ? normalizePosePreview(initialPreview, jobId) : null;
  const current = state?.start === start && state.jobId === jobId ? state : null;
  return { preview: supplied ?? current?.preview ?? null, error: current?.error ?? null, pending: Boolean(enabled && loadWindow && !supplied && !current) };
}
