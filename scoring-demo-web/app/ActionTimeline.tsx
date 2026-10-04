"use client";

import { useState } from "react";
import type { DemoResultResponse, MotionAnalysis } from "./lib/api-types";
import { reportMoments, replayMoment } from "./lib/training-report";

export default function ActionTimeline({ result, motion }: { result: DemoResultResponse | null; motion: MotionAnalysis | null }) {
  const [kind, setKind] = useState("all");
  const [page, setPage] = useState(0);
  const [active, setActive] = useState("");
  const allMoments = reportMoments(result, motion);
  const moments = allMoments.filter(item => kind === "all" || item.kind === kind);
  const lastPage = Math.max(0, Math.ceil(moments.length / 6) - 1);
  const currentPage = Math.min(page, lastPage);
  const visible = moments.slice(currentPage * 6, currentPage * 6 + 6);
  return <section className="report-timeline" id="timeline" aria-labelledby="timeline-title">
    <div className="report-section-heading"><div><span className="card-kicker">SESSION MOMENTS</span><h2 id="timeline-title">训练时间线</h2></div><div className="timeline-filter" aria-label="筛选回放片段">{[["all", "全部"], ["footwork", "步伐"], ["rotation", "挥拍与转体"]].map(([value, label]) => <button key={value} type="button" aria-pressed={kind === value} onClick={() => { setKind(value); setPage(0); }}>{label}</button>)}</div></div>
    <p className="timeline-disclosure">选择片段即可定位回放 · 运动区间与规则候选，触球未确认</p>
    <div className="timeline-moments">{visible.map(item => <button type="button" key={item.id} className={`timeline-moment ${item.kind}`} aria-label={`回看${item.label}，${(item.startMs / 1000).toFixed(2)} 至 ${(item.endMs / 1000).toFixed(2)} 秒`} aria-pressed={active === item.id} onClick={() => { setActive(item.id); replayMoment(item, result?.job_id); }}><small>{(item.startMs / 1000).toFixed(2)}–{(item.endMs / 1000).toFixed(2)}s</small><strong>{item.label}</strong><span>{item.note}</span><b aria-hidden="true">↗</b></button>)}</div>
    {!visible.length && <div className="timeline-empty"><strong>{allMoments.length ? "这一类别暂无可定位片段" : "暂无可定位片段"}</strong><p>已提供的测量仍可在测量详情中查看。</p></div>}
    {lastPage > 0 && <div className="timeline-pagination"><button type="button" disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}>← 上一组</button><span>分组回看</span><button type="button" disabled={currentPage === lastPage} onClick={() => setPage(currentPage + 1)}>下一组 →</button></div>}
  </section>;
}
