"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import TechnicalReviewPanel from "../TechnicalReviewPanel";
import RuleReviewPanel from "../RuleReviewPanel";
import scoringReference from "../data/scoring-reference.json";
import { apiClient } from "../lib/api-client";
import { annotationHref, annotationJobId, annotationSeekSeconds, loadAnnotationReport, type AnnotationReport } from "../lib/annotation-navigation";
import type { RuleReviewCatalog } from "../lib/rule-review";
import { recentReports, type RecentReport } from "../lib/training-report";
import { workspaceHref } from "../lib/workspace-navigation";
import "./annotation-workspace.css";

const catalog = scoringReference as RuleReviewCatalog;

export default function AnnotationWorkspace() {
  const [input, setInput] = useState("");
  const [history, setHistory] = useState<RecentReport[]>([]);
  const [report, setReport] = useState<AnnotationReport | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [videoError, setVideoError] = useState("");
  const [seekMessage, setSeekMessage] = useState("");
  const request = useRef<AbortController | null>(null);
  const generation = useRef(0);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const pendingSeek = useRef<number | null>(null);

  const load = useCallback(async (value: string, updateUrl = true) => {
    request.current?.abort();
    const version = ++generation.current;
    setReport(null); setError(""); setVideoError(""); setSeekMessage(""); pendingSeek.current = null;
    const jobId = annotationJobId(value, window.location.origin);
    if (!jobId) { setLoading(false); setError("请输入有效的任务编号，或粘贴本站的报告链接。"); return; }
    setInput(jobId); setLoading(true);
    if (updateUrl) window.history.pushState(null, "", annotationHref(jobId));
    const controller = new AbortController(); request.current = controller;
    try {
      const loaded = await loadAnnotationReport(apiClient, jobId, controller.signal);
      if (!controller.signal.aborted && version === generation.current) setReport(loaded);
    } catch (cause) {
      if (!controller.signal.aborted && version === generation.current) setError(cause instanceof Error ? cause.message : "暂时无法读取报告，请重试。");
    } finally {
      if (!controller.signal.aborted && version === generation.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    try {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setHistory(recentReports(JSON.parse(localStorage.getItem("rallymate.recentReports") || "[]")));
    } catch { /* The task field remains available when local storage is disabled. */ }
    const restore = () => {
      const params = new URLSearchParams(window.location.search);
      if (params.has("job")) void load(`${window.location.pathname}${window.location.search}`, false);
      else {
        request.current?.abort(); generation.current += 1;
        setReport(null); setInput(""); setError(""); setLoading(false); setVideoError(""); setSeekMessage(""); pendingSeek.current = null;
      }
    };
    restore(); window.addEventListener("popstate", restore);
    return () => { request.current?.abort(); generation.current += 1; window.removeEventListener("popstate", restore); };
  }, [load]);

  useEffect(() => {
    const seek = (event: Event) => {
      if (!report) return;
      const { jobId, timestampMs } = (event as CustomEvent<{ jobId?: string; timestampMs?: number }>).detail ?? {};
      if (jobId !== report.job.id) return;
      const seconds = annotationSeekSeconds(report, jobId, timestampMs);
      const video = videoRef.current;
      if (seconds === null) { setSeekMessage("无法确认此视频与标注时段的时间对应关系，请先核对原视频时段。"); return; }
      if (!video || video.error) { setSeekMessage("视频暂时无法播放，请重新加载视频后回看。"); return; }
      video.pause();
      if (video.readyState < 1) pendingSeek.current = seconds;
      else if (seconds <= video.duration) { video.currentTime = seconds; setSeekMessage(""); }
      else setSeekMessage("所选时段超出当前回放范围，请核对视频。");
    };
    window.addEventListener("rallymate:seek-video", seek);
    return () => window.removeEventListener("rallymate:seek-video", seek);
  }, [report]);

  const taskId = report?.job.id;
  const title = typeof report?.job.original_filename === "string" ? report.job.original_filename.split(/[\\/]/).pop() : history.find(item => item.id === taskId)?.name;
  const seekAvailable = report && annotationSeekSeconds(report, report.job.id, report.videoTimeOriginMs) !== null;
  return <main className="annotation-workspace">
    <header className="annotation-header"><a className="annotation-brand" href="/welcome">RallyMate</a><a className="annotation-return" href={workspaceHref(taskId ? "analysis" : "overview", taskId)}>返回训练空间 ↗</a></header>
    <div className="annotation-intro"><p>独立标注工具</p><h1>标注工作台</h1><span>选择已完成分析的视频，对照回放与原文标准录入人工评审。</span></div>
    <section className="annotation-selector" aria-labelledby="annotation-source-title">
      <h2 id="annotation-source-title">选择视频任务</h2>
      <form onSubmit={event => { event.preventDefault(); void load(input); }}>
        <label htmlFor="annotation-job">任务编号或本站报告链接</label>
        <div className="annotation-input-row"><input id="annotation-job" value={input} onChange={event => setInput(event.target.value)} placeholder="粘贴报告链接，或输入任务编号" autoComplete="off" spellCheck={false} /><button type="submit">{loading ? "重新读取" : "读取任务"}</button></div>
      </form>
      {history.length > 0 && <label className="annotation-recents"><span>从本机最近报告选择</span><select value={taskId ?? ""} onChange={event => { if (event.target.value) void load(event.target.value); }}><option value="">选择一个视频…</option>{history.map(item => <option key={item.id} value={item.id}>{item.name} · {item.id.slice(0, 8)}</option>)}</select></label>}
      {loading && <p className="annotation-message" role="status">正在读取视频和已有报告…</p>}
      {error && <p className="annotation-message annotation-error" role="alert">{error}</p>}
      {!report && !loading && !error && <p className="annotation-hint">先在训练空间完成视频分析，再从报告入口或任务链接打开这里。</p>}
    </section>
    {report && <>
      <section id="evidence" className="annotation-replay" aria-labelledby="annotation-video-title">
        <div className="annotation-video-heading"><div><h2 id="annotation-video-title">{title || "当前视频"}</h2><p>任务 {taskId}</p></div><a href={workspaceHref("analysis", taskId)} target="_blank" rel="noopener noreferrer">查看用户报告 ↗</a></div>
        <video key={taskId} ref={videoRef} controls muted playsInline preload="metadata" aria-label="标注视频回放" src={`/v1/jobs/${encodeURIComponent(taskId!)}/artifacts/annotated.mp4`} onError={() => setVideoError("视频暂时无法播放，请重新加载；标注记录仍可查看。")} onLoadedMetadata={() => {
          setVideoError("");
          const video = videoRef.current, seconds = pendingSeek.current; pendingSeek.current = null;
          if (video && seconds !== null) {
            if (seconds <= video.duration) { video.currentTime = seconds; setSeekMessage(""); }
            else setSeekMessage("所选时段超出当前回放范围，请核对视频。");
          }
        }}>浏览器暂不支持视频播放。</video>
        {videoError && <div className="annotation-message annotation-error" role="status"><p>{videoError}</p><button type="button" onClick={() => { setVideoError(""); videoRef.current?.load(); }}>重新加载视频</button></div>}
        {!seekAvailable && <p className="annotation-hint">此报告未提供可核验的回放时间对应信息。可手动播放；录入时段前请核对原视频时间。</p>}
        {seekMessage && <p className="annotation-message" role="status">{seekMessage}</p>}
      </section>
      <TechnicalReviewPanel key={taskId} result={report.result} catalog={catalog} />
      <details className="annotation-reference"><summary>标注标准与原文核验</summary><RuleReviewPanel key={taskId} result={report.result} catalog={catalog} /></details>
    </>}
  </main>;
}
