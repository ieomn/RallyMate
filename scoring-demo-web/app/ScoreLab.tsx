"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ChangeEvent } from "react";
import { measurementResultFromSummary } from "./lib/measurement-evidence";
import { createApiClient, RallyMateApiError, type JobProgress } from "./lib/api-client";
import { watchAnalysis } from "./lib/analysis-session";
import { recentReports, type RecentReport } from "./lib/training-report";
import { readWorkspaceLocation, workspaceHref } from "./lib/workspace-navigation";
import { poseReplayTimingAllowed } from "./lib/pose-playback";
import { adviceContextKey, adviceForContext, adviceNotice as describeAdvice, type AdviceResponse, type ScopedAdvice } from "./lib/advice-display";
import type { UploadProgress } from "./lib/resumable-upload";
import type { TechniqueAssessmentResponse, TechniqueCatalogResponse, DemoResultResponse, TrajectoryPreviewResponse } from "./lib/api-types";
import { ProductShell, TrainingHome, SessionLibrary, CaptureGuide, ProductIcon, type WorkspaceView } from "./ProductWorkspace";
import LiveResults from "./LiveResults";
import ReportExportActions from "./ReportExportActions";
import BallTrajectoryViewer from "./BallTrajectoryViewer";
import BallTrajectorySummary from "./BallTrajectorySummary";
import AdviceResult from "./AdviceResult";

const SUPPORTED_VIDEO_EXTENSION = /\.(mp4|mov|m4v|avi|mkv)$/i;
type Source = { domain: string; fileName: string; indicatorCount: number; stageCount: number };
type ImportState = { status: "idle" } | { status: "reading"; fileName: string } | { status: "success"; fileName: string } | { status: "error"; message: string };
type LiveEvidence = { mode: "demo" | "live"; jobId?: string; trajectory: TrajectoryPreviewResponse | null; assessment: TechniqueAssessmentResponse | null; result?: DemoResultResponse | null; error: string | null };
const EMPTY_EVIDENCE: LiveEvidence = { mode: "demo", trajectory: null, assessment: null, error: null };

function formatBytes(bytes: number) {
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function jobPercent(job: JobProgress | null, fallback: number) {
  const value = typeof job?.progress === "object" ? job.progress.percent : job?.progress;
  const percent = Number(value ?? fallback);
  return Math.max(0, Math.min(100, Number.isFinite(percent) ? percent : fallback));
}

function isSupportedVideo(file: File) {
  // The API derives the persisted suffix from the filename before probing the
  // bytes, so keep the browser contract aligned with ALLOWED_EXTENSIONS rather
  // than trusting a caller-controlled MIME type.
  return SUPPORTED_VIDEO_EXTENSION.test(file.name);
}

function isStage1Summary(value: unknown): value is Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return false;
  const summary = value as Record<string, unknown>;
  const jobId = summary.job_id;
  const coverage = summary.coverage;
  const processing = summary.processing;
  const hasJobId = typeof jobId === "string" && jobId.trim().length > 0;
  const hasCoverage = coverage !== null && typeof coverage === "object" && !Array.isArray(coverage);
  const hasProcessing = processing !== null && typeof processing === "object" && !Array.isArray(processing);
  return hasJobId && (hasCoverage || hasProcessing);
}

/** A model export may contain only the technique assessment (without the
 * pipeline's Stage 1 summary). Keep this shape importable so users can review
 * and re-export the evidence produced by inference jobs. */
function isTechniqueAssessment(value: unknown): value is TechniqueAssessmentResponse {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const item = value as Record<string, unknown>;
  return Array.isArray(item.techniques)
    && (typeof item.assessment_version === "string" || typeof item.schema_version === "string")
    && typeof item.registry_version === "string";
}

function extractTechniqueAssessment(value: unknown): TechniqueAssessmentResponse | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const root = value as Record<string, unknown>;
  const candidates = [
    root.assessment,
    root.technique_assessment,
    (root.result && typeof root.result === "object" ? (root.result as Record<string, unknown>).technique_assessment : null),
  ];
  return candidates.find(isTechniqueAssessment) ?? (isTechniqueAssessment(value) ? value : null);
}

function ReplayPanel({ evidence, localVideoSrc, pending = false, error }: { evidence: LiveEvidence; localVideoSrc?: string | null; pending?: boolean; error?: string | null }) {
  const replayApi = useMemo(() => createApiClient(), []);
  const loadPoseWindow = useCallback((startMs: number, signal: AbortSignal) => replayApi.getPosePreview(evidence.jobId!, { startMs, durationMs: 10000, sampleLimit: 600, signal }), [replayApi, evidence.jobId]);
  const trajectory = evidence.trajectory;
  const mediaBase = evidence.jobId ? `/v1/jobs/${encodeURIComponent(evidence.jobId)}/artifacts` : "";
  const usingLocalVideo = !evidence.result?.artifact_urls?.["annotated.mp4"] && Boolean(localVideoSrc);
  const selectedVideoSrc = usingLocalVideo ? localVideoSrc : evidence.result?.artifact_urls?.["annotated.mp4"] ? `${mediaBase}/annotated.mp4` : null;
  const [videoFailed, setVideoFailed] = useState<string | null>(null);
  const embeddedPoints = evidence.result?.features?.ball?.trajectory?.points ?? [];
  return <section className="report-replay" id="evidence" aria-labelledby="evidence-title">
    <div className="report-section-heading"><div><span className="card-kicker">SESSION REPLAY</span><h2 id="evidence-title">训练回放</h2></div><span>{selectedVideoSrc ? "原视频 · 同步回放" : "此报告未附视频"}</span></div>
    <BallTrajectoryViewer key={selectedVideoSrc ?? "coordinates"} trajectory={trajectory} pending={pending} error={error}
      jobId={evidence.jobId} loadPoseWindow={evidence.jobId && !pending ? loadPoseWindow : undefined}
      poseTimingPreserved={poseReplayTimingAllowed(usingLocalVideo, evidence.result)}
      videoSrc={videoFailed !== selectedVideoSrc ? selectedVideoSrc : null}
      poster={evidence.result?.artifact_urls?.["preview.jpg"] ? `${mediaBase}/preview.jpg` : null}
      embeddedPoints={embeddedPoints.map((point, index) => ({ ...point, timestamp_ms: point.timestamp_ms ?? index }))}
      videoTimeOriginMs={usingLocalVideo ? 0 : (trajectory?.source.start_timestamp_ms ?? 0)}
      onVideoError={() => setVideoFailed(selectedVideoSrc ?? "unknown")} />
    <p className="replay-caption">{selectedVideoSrc ? "选择下方动作片段快速回看，慢速播放帮助你看清动作细节。" : "测量结果仍可查看。需要视频回放时，请打开对应的原任务报告。"}</p>
    <details className="report-replay-details"><summary>回放说明</summary><BallTrajectorySummary trajectory={trajectory} pending={pending} error={error} />
      {evidence.error && <p className="upload-error" role="status">部分增强证据暂不可用：{evidence.error}</p>}
    </details>
  </section>;
}

export default function ScoreLab({ registryVersion, sources }: { registryVersion: string; sources: Source[] }) {
  const [view, setView] = useState<WorkspaceView>("overview");
  const [history, setHistory] = useState<RecentReport[]>([]);
  const [importState, setImportState] = useState<ImportState>({ status: "idle" });
  const [importedSummary, setImportedSummary] = useState<Record<string, unknown> | null>(null);
  const [scoreContext, setScoreContext] = useState<"demo" | "live-pending" | "live">("demo");
  const [videoFile, updateVideoFile] = useState<File | null>(null);
  const [localVideoSrc, setLocalVideoSrc] = useState<string | null>(null);
  const [localVideoJobId, setLocalVideoJobId] = useState<string | null>(null);
  const [uploadProgress, setUploadProgress] = useState<UploadProgress | null>(null);
  const [uploadState, setUploadState] = useState<"idle" | "ready" | "uploading" | "processing" | "complete" | "error">("idle");
  const [job, setJob] = useState<JobProgress | null>(null);
  const [uploadError, setUploadError] = useState("");
  const [evidence, setEvidence] = useState<LiveEvidence>(EMPTY_EVIDENCE);
  const [techniqueCatalog, setTechniqueCatalog] = useState<TechniqueCatalogResponse | null>(null);
  const [catalogError, setCatalogError] = useState("");
  const [savedAdvice, setSavedAdvice] = useState<ScopedAdvice | null>(null);
  const [adviceLoading, setAdviceLoading] = useState(false);
  const [adviceNotice, setAdviceNotice] = useState("");
  const [adviceNotes, setAdviceNotes] = useState("");
  const [coachTechnique, setCoachTechnique] = useState("");
  const [dragging, setDragging] = useState(false);
  const [reportSection, setReportSection] = useState("evidence");
  const fileInput = useRef<HTMLInputElement>(null);
  const videoInput = useRef<HTMLInputElement>(null);
  const localVideoRef = useRef<string | null>(null);
  const analysisRunRef = useRef(0);
  const analysisAbort = useRef<AbortController | null>(null);
  const activeServiceJob = useRef<string | null>(null);
  const apiClient = useMemo(() => createApiClient(), []);
  const busy = uploadState === "uploading" || uploadState === "processing";
  const hasReport = scoreContext !== "demo";
  const adviceTechnique = coachTechnique || (evidence.assessment?.family_summary.footwork?.observed_count ? "步伐" : "底线击球");
  const currentAdviceKey = adviceContextKey(evidence.jobId, adviceTechnique);
  const currentAdvice = adviceForContext(savedAdvice, currentAdviceKey);
  const currentName = videoFile?.name || (importState.status === "success" ? importState.fileName : history.find(item => item.id === evidence.jobId)?.name) || "训练视频";

  const navigate = useCallback((next: WorkspaceView, jobId?: string) => {
    setView(next);
    const href = workspaceHref(next, jobId);
    if (window.location.pathname + window.location.search + window.location.hash !== href) window.history.pushState(null, "", href);
    window.scrollTo({ top: 0, behavior: "instant" });
  }, []);

  function setVideoFile(file: File | null) {
    if (localVideoRef.current) URL.revokeObjectURL(localVideoRef.current);
    localVideoRef.current = file ? URL.createObjectURL(file) : null;
    setLocalVideoSrc(localVideoRef.current); setLocalVideoJobId(null); updateVideoFile(file);
  }
  useEffect(() => () => { if (localVideoRef.current) URL.revokeObjectURL(localVideoRef.current); }, []);
  function invalidateAnalysisRun() {
    analysisRunRef.current += 1; analysisAbort.current?.abort(); activeServiceJob.current = null; setImportedSummary(null);
    try { localStorage.removeItem("rallymate.activeJob"); } catch { /* Optional storage. */ }
  }

  useEffect(() => {
    const controller = new AbortController();
    apiClient.getTechniques(controller.signal).then(catalog => { if (!controller.signal.aborted) { setTechniqueCatalog(catalog); setCatalogError(""); } }).catch(error => { if (!controller.signal.aborted) setCatalogError(error instanceof Error ? error.message : "动作目录暂时无法读取"); });
    return () => controller.abort();
  }, [apiClient]);

  const monitorJob = useCallback(async (id: string) => {
    analysisRunRef.current += 1;
    activeServiceJob.current = id;
    analysisAbort.current?.abort();
    const controller = new AbortController(); analysisAbort.current = controller;
    setImportedSummary(null); setUploadState("processing"); setUploadError(""); setScoreContext("live-pending"); setJob({ id, status: "queued" });
    try { localStorage.setItem("rallymate.activeJob", id); } catch { /* Optional storage. */ }
    setEvidence({ mode: "live", jobId: id, trajectory: null, assessment: null, error: null });
    try {
      const completed = await watchAnalysis(apiClient, id, controller.signal, setJob, 2000, preview => {
        if (!controller.signal.aborted) setEvidence(previous => previous.jobId === id ? { ...previous, trajectory: preview } : previous);
      });
      if (controller.signal.aborted) return;
      setEvidence({ mode: "live", jobId: id, trajectory: completed.trajectory, assessment: completed.assessment ?? completed.result.technique_assessment ?? null, result: completed.result, error: completed.warning });
      setScoreContext("live"); setUploadState("complete");
      try {
        const existing = recentReports(JSON.parse(localStorage.getItem("rallymate.recentReports") || "[]"));
        const next = recentReports([{ id, name: existing.find(item => item.id === id)?.name || "训练视频", viewedAt: new Date().toISOString() }, ...existing.filter(item => item.id !== id)]);
        localStorage.setItem("rallymate.recentReports", JSON.stringify(next)); setHistory(next);
      } catch { /* History remains optional in private browsing. */ }
    } catch (error) {
      if (controller.signal.aborted) return;
      setUploadState("error"); setUploadError(error instanceof Error ? error.message : "连接中断，稍后可继续读取结果，无需重新上传。");
    }
  }, [apiClient]);

  useEffect(() => {
    try {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setHistory(recentReports(JSON.parse(localStorage.getItem("rallymate.recentReports") || "[]")));
    } catch { /* Deep links also work when browser storage is unavailable. */ }
    const restore = () => {
      const location = readWorkspaceLocation(window.location.search);
      setView(location.view);
      if (location.view === "analysis" && location.jobId && (location.jobId !== activeServiceJob.current || analysisAbort.current?.signal.aborted)) {
        setVideoFile(null); setImportState({ status: "idle" }); void monitorJob(location.jobId);
      }
    };
    restore(); window.addEventListener("popstate", restore);
    return () => { window.removeEventListener("popstate", restore); analysisAbort.current?.abort(); };
  }, [monitorJob]);

  function openReport(id: string) { if (id === activeServiceJob.current) { navigate("analysis", id); return; } if (uploadState === "uploading") { navigate("analysis", activeServiceJob.current ?? undefined); return; } setVideoFile(null); setImportState({ status: "idle" }); navigate("analysis", id); void monitorJob(id); }
  function newAnalysis() {
    if (busy) { navigate("analysis", evidence.jobId); return; }
    invalidateAnalysisRun(); setVideoFile(null); setJob(null); setUploadState("idle"); setUploadError(""); setEvidence(EMPTY_EVIDENCE); setScoreContext("demo"); setImportState({ status: "idle" }); navigate("analysis");
  }
  function acceptVideoFile(file: File | undefined) {
    if (!file || busy) return;
    invalidateAnalysisRun(); setUploadError(""); setJob(null); setEvidence(EMPTY_EVIDENCE); setScoreContext("demo"); setImportState({ status: "idle" }); navigate("analysis");
    if (!isSupportedVideo(file)) { setVideoFile(null); setUploadState("error"); setUploadError("请选择 MP4、MOV、M4V、AVI 或 MKV 视频文件。"); return; }
    setVideoFile(file); setUploadState("ready");
  }
  function chooseVideo(event: ChangeEvent<HTMLInputElement>) { acceptVideoFile(event.target.files?.[0]); event.target.value = ""; }

  async function submitVideo() {
    if (!videoFile || busy) return;
    invalidateAnalysisRun(); const runId = analysisRunRef.current;
    const controller = new AbortController(); analysisAbort.current = controller;
    setUploadState("uploading"); setUploadProgress(null); setUploadError(""); setJob(null); setScoreContext("live-pending"); setEvidence({ mode: "live", trajectory: null, assessment: null, error: null });
    try {
      const submitted = await apiClient.submitVideo(videoFile, { courtMode: "auto", writeAnnotatedVideo: true, signal: controller.signal, onUploadProgress: progress => { if (!controller.signal.aborted) setUploadProgress(progress); } });
      if (analysisRunRef.current !== runId) return;
      if (typeof submitted.id !== "string" || !/^[a-zA-Z0-9_-]{8,80}$/.test(submitted.id)) throw new Error("服务没有返回有效任务，请稍后重试。");
      setJob(submitted); setLocalVideoJobId(submitted.id);
      try {
        const existing = recentReports(JSON.parse(localStorage.getItem("rallymate.recentReports") || "[]"));
        const next = recentReports([{ id: submitted.id, name: videoFile.name, viewedAt: new Date().toISOString() }, ...existing.filter(item => item.id !== submitted.id)]);
        localStorage.setItem("rallymate.recentReports", JSON.stringify(next)); setHistory(next);
      } catch { /* Optional history. */ }
      if (readWorkspaceLocation(window.location.search).view === "analysis") window.history.replaceState(null, "", workspaceHref("analysis", submitted.id));
      await monitorJob(submitted.id);
    } catch (error) {
      if (analysisRunRef.current !== runId || controller.signal.aborted) return;
      setUploadState("error"); setUploadError(error instanceof RallyMateApiError ? `${error.message}（${error.status}）` : error instanceof Error ? error.message : "上传失败，请稍后重试。");
    }
  }

  async function askCoach() {
    setAdviceLoading(true); setAdviceNotice("");
    const contextKey = currentAdviceKey;
    setSavedAdvice({ contextKey, response: {} });
    try {
      const response = await fetch("/api/advice", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ technique: adviceTechnique, skillLevel: "业余进阶", sessionGoal: "稳定性", observations: adviceNotes, ...(evidence.jobId ? { jobId: evidence.jobId } : {}) }) });
      const payload = await response.json() as AdviceResponse;
      if (!response.ok) throw new Error(response.status === 429 ? "提问较频繁，请稍后再试。" : response.status === 400 ? "请用 600 字以内描述网球练习问题，避免填写私人信息。" : "建议服务暂时不可用，请稍后重试。");
      if (!payload.advice) throw new Error("建议返回不完整，当前没有可展示的建议；请稍后重试。");
      setSavedAdvice({ contextKey, response: payload });
      setAdviceNotice(describeAdvice(payload));
    } catch (error) { setAdviceNotice(error instanceof Error ? error.message : "建议暂时不可用"); }
    finally { setAdviceLoading(false); }
  }

  async function importSummary(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file || busy) return;
    navigate("analysis");
    invalidateAnalysisRun();
    const importRun = analysisRunRef.current;
    setVideoFile(null);
    setImportState({ status: "reading", fileName: file.name });
    setUploadError("");
    try {
      if (file.size > 5 * 1024 * 1024) throw new Error("报告超过 5 MB，请选择任务摘要或本站导出的报告。");
      const contents = await file.text();
      if (importRun !== analysisRunRef.current) return;
      const data = JSON.parse(contents);
      if (data?.schemaVersion === "rallymate-practice-report/1" && typeof data.jobId === "string" && /^[a-zA-Z0-9_-]{8,80}$/.test(data.jobId)) {
        // Prefer the self-contained result embedded by our exporter. This
        // keeps downloaded reports reviewable when the API is offline; only
        // fall back to polling the job when the export contains no result.
        const embeddedResult = data.result && typeof data.result === "object" ? data.result as DemoResultResponse : null;
        const embeddedAssessment = extractTechniqueAssessment(data) ?? embeddedResult?.technique_assessment ?? null;
        if (embeddedResult || embeddedAssessment) {
          const summary = data.summary && typeof data.summary === "object" ? data.summary as Record<string, unknown> : null;
          if (summary && isStage1Summary(summary)) {
            setImportedSummary(summary);
          }
          const fallbackResult: DemoResultResponse = { job_id: data.jobId, ...(embeddedAssessment ? { technique_assessment: embeddedAssessment } : {}) };
          setEvidence({ mode: "live", jobId: data.jobId, trajectory: data.trajectory ?? null, assessment: embeddedAssessment, result: embeddedResult ?? fallbackResult, error: null });
          setJob({ id: data.jobId, status: "completed" });
          setUploadState("complete"); setScoreContext("live");
          setImportState({ status: "success", fileName: file.name });
          return;
        }
        try { localStorage.setItem("rallymate.activeJob", data.jobId); } catch { /* Optional storage. */ }
        setImportState({ status: "success", fileName: file.name });
        navigate("analysis", data.jobId);
        await monitorJob(data.jobId);
        return;
      }
      const importedAssessment = extractTechniqueAssessment(data);
      if (importedAssessment) {
        const rawResult = data.result && typeof data.result === "object" ? data.result as DemoResultResponse : null;
        const jobId = typeof data.job_id === "string" && /^[a-zA-Z0-9_-]{8,80}$/.test(data.job_id)
          ? data.job_id
          : typeof importedAssessment.job_id === "string" ? importedAssessment.job_id : undefined;
        const summary = data.summary && typeof data.summary === "object" && isStage1Summary(data.summary)
          ? data.summary as Record<string, unknown> : null;
        if (summary) {
          setImportedSummary(summary);
        } else {
          setImportedSummary(null);
        }
        setJob(jobId ? { id: jobId, status: "completed" } : null);
        setUploadState("complete");
        setEvidence({ mode: "live", jobId, trajectory: data.trajectory ?? null, assessment: importedAssessment, result: rawResult ?? { technique_assessment: importedAssessment, ...(jobId ? { job_id: jobId } : {}) }, error: null });
        setScoreContext("live");
        setImportState({ status: "success", fileName: file.name });
        return;
      }
      const summary = data.summary ?? data;
      if (!isStage1Summary(summary)) {
        throw new Error("该 JSON 不是可识别的分析文件：请提供 Stage 1 summary、模型技术评价（techniques）或本站导出的报告。");
      }
      const importedResult = measurementResultFromSummary(summary);
      setImportedSummary(summary);
      setJob(importedResult.job_id ? { id: importedResult.job_id, status: "completed", summary } : null);
      setUploadState("complete");
      setVideoFile(null);
      setEvidence({ mode: "live", jobId: importedResult.job_id, result: importedResult, trajectory: null, assessment: null, error: null });
      setScoreContext("live");
      setImportState({ status: "success", fileName: file.name });
    } catch (error) {
      if (importRun !== analysisRunRef.current) return;
      setJob(null);
      setVideoFile(null);
      setUploadState("idle");
      setEvidence(EMPTY_EVIDENCE);
      setScoreContext("demo");
      setImportState({
        status: "error",
        message: error instanceof Error
          ? error.message
          : "无法读取该 JSON。请确认它是模型技术评价、本站导出报告或一期 pipeline 生成的 summary.json。",
      });
    } finally {
      event.target.value = "";
    }
  }

  const exportInput = { mode: scoreContext, uploadState, job, evidence, summary: importedSummary ?? (job?.summary as Record<string, unknown> | undefined), videoName: currentName };
  const progress = uploadState === "uploading" ? uploadProgress?.percent ?? 0 : jobPercent(job, uploadState === "complete" ? 100 : 0);
  const stageLabel = uploadState === "uploading" ? uploadProgress?.phase === "merging" ? "正在校验视频" : uploadProgress?.resumed ? "正在继续上传" : "正在上传视频" : job?.status === "queued" ? "视频已接收，等待分析" : "正在分析你的动作";
  const openSection = (id: string) => { setReportSection(id); const section = document.getElementById(id); if (section instanceof HTMLDetailsElement) section.open = true; section?.scrollIntoView({ behavior: "smooth", block: "start" }); };
  return <ProductShell view={view} onNavigate={next => navigate(next, next === "analysis" ? activeServiceJob.current ?? undefined : undefined)} onNewAnalysis={newAnalysis} currentName={hasReport || videoFile ? currentName : undefined} busy={busy}>
    <input ref={videoInput} type="file" accept="video/mp4,video/quicktime,video/x-msvideo,video/x-matroska,.mp4,.mov,.m4v,.avi,.mkv" hidden onChange={chooseVideo} />
    <input ref={fileInput} type="file" accept="application/json,.json" hidden onChange={importSummary} />
    {view === "overview" && <TrainingHome reports={history} onOpenReport={openReport} onNewAnalysis={newAnalysis} onNavigate={next => navigate(next, next === "analysis" ? activeServiceJob.current ?? undefined : undefined)} currentName={hasReport ? currentName : undefined} busy={busy} />}
    {view === "sessions" && <><SessionLibrary reports={history} onOpenReport={openReport} onNewAnalysis={newAnalysis} /><div className="product-library-import"><p>已有分析报告？导入备份文件继续查看。</p><button type="button" className="product-secondary" disabled={busy} onClick={() => fileInput.current?.click()}><ProductIcon name="upload" size={16} />导入报告</button></div></>}
    {view === "guide" && <CaptureGuide onNewAnalysis={newAnalysis} />}
    {view === "analysis" && <div className={`product-analysis ${hasReport ? "has-report" : ""}`} aria-busy={importState.status === "reading"}>
      <div className="product-page-heading"><div><span className="product-eyebrow">{hasReport ? "SESSION REVIEW" : "NEW SESSION"}</span><h1>{hasReport ? "这一次，练得怎么样？" : "开始一次新的训练分析"}</h1><p>{hasReport ? currentName : "上传训练视频，把值得回看的动作留下来。"}</p></div><button type="button" className="product-secondary" disabled={busy || importState.status === "reading"} onClick={() => fileInput.current?.click()}><ProductIcon name="upload" size={16} />导入报告</button></div>
      {importState.status !== "idle" && <div className={`product-notice import-${importState.status}`} role="status">{importState.status === "reading" ? `正在读取 ${importState.fileName}…` : importState.status === "success" ? `已载入 ${importState.fileName}` : importState.message}</div>}
      {!hasReport && <div className="product-upload-layout"><section className="product-upload-panel" aria-labelledby="upload-title"><div className="product-panel-title"><span className="product-step-number">01</span><div><h2 id="upload-title">选择训练视频</h2><p>一次完整练习，一份清晰复盘。</p></div></div>
        <button type="button" className={`product-dropzone ${dragging ? "is-dragging" : ""} ${videoFile ? "has-file" : ""}`} onClick={() => videoInput.current?.click()} onDragOver={event => { event.preventDefault(); setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={event => { event.preventDefault(); setDragging(false); acceptVideoFile(event.dataTransfer.files?.[0]); }} aria-label="选择或拖入视频文件"><span className="product-upload-symbol"><ProductIcon name={videoFile ? "check" : "upload"} size={28} /></span><strong>{videoFile ? videoFile.name : "把视频拖到这里"}</strong><span>{videoFile ? `${formatBytes(videoFile.size)} · 已准备好` : "或点击选择本地视频"}</span><small>MP4、MOV、M4V、AVI、MKV · 建议 200 MB 以内</small></button>
        {videoFile && <div className="product-file-actions"><span><ProductIcon name="check" size={15} />视频已选择</span><button type="button" onClick={() => { setVideoFile(null); setUploadState("idle"); setUploadError(""); }}>移除视频</button></div>}
        {uploadError && <p className="product-notice is-error" role="alert">{uploadError}</p>}
        <button type="button" className="product-primary product-upload-start" disabled={!videoFile} onClick={() => void submitVideo()}>开始分析<ProductIcon name="arrow-right" size={18} /></button><p className="product-upload-footnote">上传后会显示处理进度。网络中断后可继续读取，无需重复上传。</p>
      </section><aside className="product-capture-checklist"><span className="product-eyebrow">BEFORE YOU START</span><h2>拍清楚，<br />才看得更明白。</h2><div><ProductIcon name="frame" /><span><strong>全身和双脚入镜</strong><p>保留挥拍空间，尽量减少遮挡。</p></span></div><div><ProductIcon name="camera" /><span><strong>固定相机</strong><p>稳定机位，避免跟随人物频繁移动。</p></span></div><div><ProductIcon name="movement" /><span><strong>保留完整动作</strong><p>从准备到随挥，再到恢复。</p></span></div><button type="button" onClick={() => navigate("guide")}>查看完整拍摄指南<ProductIcon name="arrow-up-right" size={16} /></button></aside></div>}
      {busy && <section className="product-processing" aria-live="polite"><div className="product-processing-heading"><span className="product-processing-icon"><ProductIcon name="movement" size={22} /></span><div><h2>{stageLabel}</h2><p>{uploadState === "uploading" ? "保留当前页面，上传完成后将自动开始分析。" : "你可以浏览训练记录，完成后在视频分析中查看结果。"}</p></div><strong>{Math.floor(progress)}<small>%</small></strong></div><div className="product-progress-track" role="progressbar" aria-label="视频分析进度" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.floor(progress)}><span style={{ width: `${progress}%` }} /></div><ol className="product-processing-steps"><li className={uploadState === "uploading" ? "current" : "done"}>上传视频</li><li className={uploadState === "processing" ? "current" : ""}>分析动作</li><li>生成报告</li></ol>{uploadProgress && uploadState === "uploading" && <small>已上传 {(uploadProgress.uploadedBytes / 1048576).toFixed(1)} / {(uploadProgress.totalBytes / 1048576).toFixed(1)} MB</small>}</section>}
      {hasReport && uploadError && <section className="product-recovery" role="alert"><div><h2>这次分析暂时无法完成</h2><p>{uploadError}</p></div><button type="button" className="product-secondary" onClick={() => { if (evidence.jobId && !["failed", "cancelled"].includes(job?.status ?? "")) void monitorJob(evidence.jobId); else if (videoFile) void submitVideo(); else newAnalysis(); }}>{evidence.jobId && !["failed", "cancelled"].includes(job?.status ?? "") ? "继续读取结果" : videoFile ? "重试分析" : "选择新视频"}</button></section>}
      {hasReport && !busy && <nav className="product-report-nav" aria-label="报告内容">{[["evidence", "回放与重点"], ["timeline", "动作片段"], ["rules", "测量详情"], ["coach", "训练问答"], ["export", "导出报告"]].map(([id, label]) => <button key={id} type="button" aria-current={reportSection === id ? "location" : undefined} onClick={() => openSection(id)}>{label}</button>)}</nav>}
      {hasReport && <LiveResults key={evidence.jobId ?? "pending"} status={job?.status} error={uploadError || evidence.error} result={evidence.result ?? null} assessment={evidence.assessment} catalog={techniqueCatalog} pending={busy} trajectory={evidence.trajectory} summary={importedSummary ?? (job?.summary && typeof job.summary === "object" ? job.summary as Record<string, unknown> : null)} replay={<ReplayPanel key={evidence.jobId ?? evidence.mode} evidence={evidence} pending={busy} error={uploadError || evidence.error} localVideoSrc={localVideoJobId === evidence.jobId ? localVideoSrc : null} />} />}
      {hasReport && !busy && <><details className="report-disclosure report-coach" id="coach"><summary>训练问答<span>围绕这次视频，进一步了解你的动作</span></summary><section className="coach-section"><div className="logic-heading"><div><span className="card-kicker">A CLOSER LOOK</span><h2>把问题，带回这次练习。</h2><p>结合当前视频中的可用证据解释动作；不能判断的部分会明确说明。</p></div></div><div className="coach-layout"><form className="coach-form" onSubmit={event => { event.preventDefault(); void askCoach(); }}><label>练习主题<select aria-label="AI 练习主题" value={adviceTechnique} onChange={event => setCoachTechnique(event.target.value)}>{["底线击球", "发球", "接发", "网前进攻", "步伐"].map(name => <option key={name}>{name}</option>)}</select></label><label>你的问题<span>{adviceNotes.length}/600</span><textarea value={adviceNotes} maxLength={600} onChange={event => setAdviceNotes(event.target.value)} placeholder="例如：我应该重点回看哪一段脚步？" /></label><button className="primary-button" disabled={adviceLoading}>{adviceLoading ? "正在整理…" : "查看解释与建议"}<ProductIcon name="arrow-right" size={18} /></button>{adviceNotice && savedAdvice?.contextKey === currentAdviceKey && <p className="coach-status" role="status">{adviceNotice}</p>}</form><AdviceResult technique={adviceTechnique} response={currentAdvice} /></div></section></details>
      <div id="export"><ReportExportActions input={exportInput} /></div><details className="report-disclosure report-system" id="system"><summary>关于这份报告<span>测量范围与评分说明</span></summary><div className="report-system-copy"><h3>参考分如何理解</h3><p>测量证据参考分反映视频证据的完整程度，不代表技术水平。正式技术评分需要教练标定。</p><h3>动作与测量</h3><p>动作类型和阶段目前由规则推断。脚步候选不等于实际步数，挥拍区间不等于已确认触球；二维肩髋变化不代表真实三维转体角度。缺失证据不会补成测量结果。</p><details><summary>报告版本信息</summary><p>指标目录：{registryVersion} · 技术目录：{techniqueCatalog?.registry_version ?? "暂未连接"} · {sources.reduce((count, item) => count + item.indicatorCount, 0)} 项指标定义</p>{catalogError && <p>目录读取说明：{catalogError}</p>}</details></div></details></>}
    </div>}
  </ProductShell>;
}
