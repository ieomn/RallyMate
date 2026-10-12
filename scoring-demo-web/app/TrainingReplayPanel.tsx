"use client";

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import MotionReplayPanel from "./MotionReplayPanel";

type ReplayView = "original" | "comparison";

export default function TrainingReplayPanel({ jobId, allowComparison, children }: { jobId?: string; allowComparison: boolean; children: ReactNode }) {
  const [view, setView] = useState<ReplayView>("original");
  const root = useRef<HTMLElement>(null);
  const comparison = allowComparison && view === "comparison";
  const activate = useCallback((next: ReplayView) => {
    root.current?.querySelectorAll("video").forEach(video => video.pause());
    setView(next);
  }, []);

  useEffect(() => {
    let scrollFrame = 0;
    const reveal = (next: ReplayView) => {
      activate(next);
      cancelAnimationFrame(scrollFrame);
      scrollFrame = requestAnimationFrame(() => root.current?.scrollIntoView({ block: "start", behavior: "instant" }));
    };
    const onHash = () => {
      if (window.location.hash === "#motion-replay" && allowComparison) reveal("comparison");
      else if (window.location.hash === "#evidence") reveal("original");
    };
    const onSeek = (event: Event) => {
      const detail = (event as CustomEvent<{ timestampMs?: number; jobId?: string }>).detail;
      if (!Number.isFinite(detail?.timestampMs) || detail?.jobId && jobId && detail.jobId !== jobId) return;
      reveal("original");
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
  }, [activate, allowComparison, jobId]);

  const select = (next: ReplayView) => {
    activate(next);
    window.history.replaceState(null, "", next === "comparison" ? "#motion-replay" : "#evidence");
  };
  return <section ref={root} className="report-replay" id="evidence" aria-labelledby="evidence-title" data-view={comparison ? "comparison" : "original"}>
    <div className="report-section-heading"><h2 id="evidence-title">训练回放</h2></div>
    {allowComparison && <div className="training-replay-tabs" role="tablist" aria-label="训练回放方式">
      {(["original", "comparison"] as const).map(mode => <button key={mode} id={`replay-tab-${mode}`} role="tab" type="button" aria-selected={comparison === (mode === "comparison")} aria-controls={`replay-view-${mode}`} tabIndex={comparison === (mode === "comparison") ? 0 : -1} onClick={() => select(mode)} onKeyDown={event => {
        if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
        event.preventDefault();
        const next = event.key === "Home" ? "original" : event.key === "End" ? "comparison" : mode === "original" ? "comparison" : "original";
        select(next);
        document.getElementById(`replay-tab-${next}`)?.focus();
      }}>{mode === "original" ? "原视频" : "白模与 AI 对比"}</button>)}
    </div>}
    <div id="replay-view-original" role={allowComparison ? "tabpanel" : undefined} aria-labelledby={allowComparison ? "replay-tab-original" : undefined} hidden={comparison}>{children}</div>
    {allowComparison && <div id="replay-view-comparison" role="tabpanel" aria-labelledby="replay-tab-comparison" hidden={!comparison}><MotionReplayPanel jobId={jobId} enabled active={comparison} /></div>}
  </section>;
}
