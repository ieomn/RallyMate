"use client";

import { useContext, useEffect, useRef, type ReactNode } from "react";
import { ReportClipSelection } from "./report-clip-selection";

export function originalReplayHash(hash: string): "#evidence" | null {
  return ["#evidence", "#motion-replay", "#skeleton-replay"].includes(hash) ? "#evidence" : null;
}

export default function TrainingReplayPanel({ jobId, children, mediaKind }: { jobId?: string; children: ReactNode; mediaKind?: "original" | null }) {
  const root = useRef<HTMLElement>(null);
  const clip = useContext(ReportClipSelection)?.clip;

  useEffect(() => {
    let scrollFrame = 0;
    const reveal = (behavior: ScrollBehavior) => {
      cancelAnimationFrame(scrollFrame);
      scrollFrame = requestAnimationFrame(() => root.current?.scrollIntoView({ block: "start", behavior }));
    };
    const onHash = () => {
      const originalHash = originalReplayHash(window.location.hash);
      if (!originalHash) return;
      if (window.location.hash !== originalHash) window.history.replaceState(null, "", originalHash);
      reveal("instant");
    };
    const onSeek = (event: Event) => {
      const detail = (event as CustomEvent<{ timestampMs?: number; jobId?: string }>).detail;
      if (!Number.isFinite(detail?.timestampMs) || detail?.jobId && jobId && detail.jobId !== jobId) return;
      const stage = root.current?.querySelector("video")?.getBoundingClientRect();
      if (!stage || stage.top < 0 || stage.bottom > window.innerHeight) {
        reveal(window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth");
      }
      window.history.replaceState(null, "", "#evidence");
    };
    onHash();
    window.addEventListener("hashchange", onHash);
    window.addEventListener("popstate", onHash);
    window.addEventListener("rallymate:seek-video", onSeek);
    return () => {
      cancelAnimationFrame(scrollFrame);
      window.removeEventListener("hashchange", onHash);
      window.removeEventListener("popstate", onHash);
      window.removeEventListener("rallymate:seek-video", onSeek);
    };
  }, [jobId]);

  return <section ref={root} className="report-replay" id="evidence" aria-labelledby="evidence-title" data-job-id={jobId}>
    <div className="report-section-heading"><h2 id="evidence-title">{mediaKind === null ? "训练回放" : "关键动作片段"}</h2>
      {clip && <span className="review-window-time" key={clip.id}>{(clip.startMs / 1000).toFixed(2)}–{(clip.endMs / 1000).toFixed(2)} 秒</span>}
    </div>
    <div id="replay-view-original">{children}</div>
  </section>;
}
