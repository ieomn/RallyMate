"use client";

import { useEffect, useRef, type ReactNode } from "react";

export function originalReplayHash(hash: string): "#evidence" | null {
  return ["#evidence", "#motion-replay", "#skeleton-replay"].includes(hash) ? "#evidence" : null;
}

export default function TrainingReplayPanel({ jobId, children, mediaKind }: { jobId?: string; children: ReactNode; mediaKind?: "annotated" | "original" | null }) {
  const root = useRef<HTMLElement>(null);

  useEffect(() => {
    let scrollFrame = 0;
    const reveal = () => {
      cancelAnimationFrame(scrollFrame);
      scrollFrame = requestAnimationFrame(() => root.current?.scrollIntoView({ block: "start", behavior: "instant" }));
    };
    const onHash = () => {
      const originalHash = originalReplayHash(window.location.hash);
      if (!originalHash) return;
      if (window.location.hash !== originalHash) window.history.replaceState(null, "", originalHash);
      reveal();
    };
    const onSeek = (event: Event) => {
      const detail = (event as CustomEvent<{ timestampMs?: number; jobId?: string }>).detail;
      if (!Number.isFinite(detail?.timestampMs) || detail?.jobId && jobId && detail.jobId !== jobId) return;
      reveal();
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
    <div className="report-section-heading"><div><h2 id="evidence-title">{mediaKind === "annotated" ? "标注回放" : mediaKind === null ? "训练回放" : "原视频"} · 动作识别</h2><p className="training-replay-intro">{mediaKind === "annotated" ? "当前为服务器生成的标注回放，支持慢放查看。" : mediaKind === null ? "选择可用的视频回放查看动作。" : "结合动作标记复看原片，支持慢放与动作片段定位。"}</p></div></div>
    <div id="replay-view-original">{children}</div>
  </section>;
}
