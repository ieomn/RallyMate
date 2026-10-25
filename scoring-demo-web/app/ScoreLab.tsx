"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ChangeEvent } from "react";
import { measurementResultFromSummary } from "./lib/measurement-evidence";
import { createApiClient, RallyMateApiError, type JobProgress, type RallyMateApiClient } from "./lib/api-client";
import { AnalysisCancelledError, mergeAnalysisProgress, watchAnalysis } from "./lib/analysis-session";
import { analysisProgress, formatElapsed } from "./lib/analysis-progress";
import { analysisViewState } from "./lib/analysis-view-state";
import { recentReports, type RecentReport } from "./lib/training-report";
import { readWorkspaceLocation, workspaceHref } from "./lib/workspace-navigation";
import { poseReplayTimingAllowed } from "./lib/pose-playback";
import { originalPoseTimeBinding, poseWindowMatchesOriginal } from "./lib/original-video-timing";
import { adviceContextKey, adviceForContext, adviceNotice as describeAdvice, type AdviceResponse, type ScopedAdvice } from "./lib/advice-display";
import type { UploadProgress } from "./lib/resumable-upload";
import type { TechniqueAssessmentResponse, DemoResultResponse, TrajectoryPreviewResponse } from "./lib/api-types";
import { ProductShell, TrainingHome, CaptureGuide, ProductIcon, type WorkspaceView } from "./ProductWorkspace";
import LiveResults from "./LiveResults";
import ReportExportActions from "./ReportExportActions";
import BallTrajectoryViewer, { type CandidateReplayHandle } from "./BallTrajectoryViewer";
import type { FootCrossingPanelContext } from "./FootCrossingCandidatesPanel";
import BallTrajectorySummary from "./BallTrajectorySummary";
import AdviceResult from "./AdviceResult";
import TrainingReplayPanel from "./TrainingReplayPanel";
import OfficialMeshReplay from "./OfficialMeshReplay";
import ReplayClipNavigator from "./ReplayClipNavigator";
import SwingKinematicsPanel from "./SwingKinematicsPanel";
import { swingVideoDurationMs } from "./lib/swing-kinematics";
import { verifiedMeshMedia, watchMeshPreview, type OfficialMeshMedia } from "./lib/official-mesh-media";
import { readReportJson } from "./lib/report-file";
import { reportReadStage, safeRequestDiagnostic, type RequestDiagnostic } from "./lib/request-diagnostics";
import RequestReadDetails from "./RequestReadDetails";
import UploadVideoPreview from "./UploadVideoPreview";
import { useTrainingJournal, TrainingJournalPanel, TrainingJournalLibrary, TrainingFollowUpPicker, journalRecordId } from "./TrainingJournal";
import { focusSelection, trainingGuidance, type TrainingFocusSelection } from "./lib/training-guidance";
import { motionAnalysisOf } from "./lib/motion-analysis";

const SUPPORTED_VIDEO_EXTENSION = /\.(mp4|mov|m4v|avi|mkv)$/i;
type ImportState = { status: "idle" } | { status: "reading"; fileName: string } | { status: "success"; fileName: string } | { status: "error"; message: string };
type LiveEvidence = { mode: "demo" | "live"; jobId?: string; candidateSource?: "online"; trajectory: TrajectoryPreviewResponse | null; assessment: TechniqueAssessmentResponse | null; result?: DemoResultResponse | null; error: string | null };
const EMPTY_EVIDENCE: LiveEvidence = { mode: "demo", trajectory: null, assessment: null, error: null };

export function candidateOnlineJobId(evidence: Pick<LiveEvidence, "candidateSource" | "jobId" | "result">, pending: boolean): string | null {
  const id = evidence.jobId;
  return !pending && evidence.candidateSource === "online" && id && evidence.result?.job_id === id ? id : null;
}

function formatBytes(bytes: number) {
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
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

function ReplayPanel({ api, replaySignal, evidence, localVideoSrc, pending = false, error, candidateSessionKey, onCandidateReplayHandle }: { api: Pick<RallyMateApiClient, "getPosePreview" | "getMeshPreview" | "getSwingKinematics">; replaySignal: AbortSignal; evidence: LiveEvidence; localVideoSrc?: string | null; pending?: boolean; error?: string | null; candidateSessionKey: string; onCandidateReplayHandle: (handle: CandidateReplayHandle | null) => void }) {
  const replayContainer = useRef<HTMLDivElement>(null);
  const clipMotion = useMemo(() => motionAnalysisOf(evidence.result ?? null, evidence.assessment), [evidence.result, evidence.assessment]);
  const trajectory = evidence.trajectory;
  const mediaBase = evidence.jobId ? `/v1/jobs/${encodeURIComponent(evidence.jobId)}/artifacts` : "";
  const original = evidence.result?.original_video;
  const originalPath = evidence.jobId ? `/v1/jobs/${encodeURIComponent(evidence.jobId)}/original-video` : null;
  const originalUrl = (() => { try { return original?.url ? new URL(original.url, "https://rallymate.invalid") : null; } catch { return null; } })();
  // Only a live response for this job may certify a server original. Offline
  // imports cannot forge this identity or choose an arbitrary media URL.
  const serverOriginal = evidence.candidateSource === "online" && evidence.result?.job_id === evidence.jobId
    && original && originalUrl?.pathname === originalPath && ["http:", "https:"].includes(originalUrl.protocol)
    && !originalUrl.search && !originalUrl.hash && !originalUrl.username && !originalUrl.password && /^[a-f0-9]{64}$/i.test(original.sha256)
    && Number.isSafeInteger(original.bytes) && original.bytes > 0 && typeof original.source_job_id === "string" && original.source_job_id
    ? original : null;
  const originalTimeBinding = serverOriginal ? originalPoseTimeBinding(evidence.result, evidence.jobId, true) : null;
  // public_base_url may be stale after migration; media still travels through
  // this site's authenticated gateway, never the origin supplied in the JSON.
  const originalSrc = localVideoSrc || (serverOriginal ? originalPath : null);
  const onlineEvidence = evidence.candidateSource === "online" && evidence.result?.job_id === evidence.jobId;
  const annotatedSrc = onlineEvidence && evidence.result?.artifact_urls?.["annotated.mp4"] ? `${mediaBase}/annotated.mp4` : null;
  const meshOnline = evidence.candidateSource === "online" && evidence.result?.job_id === evidence.jobId;
  const initialMeshStatus = (evidence.result as unknown as Record<string, unknown> | null)?.sam3d_mesh_status;
  const [meshUpdate, setMeshUpdate] = useState<{ scope: string; status: string; media: OfficialMeshMedia | null } | null>(null);
  const [meshReadAttempt, setMeshReadAttempt] = useState(0);
  const currentMeshUpdate = meshOnline && meshUpdate?.scope === candidateSessionKey ? meshUpdate : null;
  const meshMedia = verifiedMeshMedia(evidence.result?.sam3d_mesh, evidence.jobId, meshOnline) ?? currentMeshUpdate?.media ?? null;
  const meshStatus = meshMedia ? "ready" : currentMeshUpdate?.status ?? initialMeshStatus;
  const meshAvailable = Boolean(meshMedia);
  const [showSurface, setShowSurface] = useState(true);
  useEffect(() => {
    if (!meshOnline || !evidence.jobId || initialMeshStatus !== "pending" && meshReadAttempt === 0) return;
    const controller = new AbortController();
    const signal = AbortSignal.any([replaySignal, controller.signal]);
    void watchMeshPreview(api.getMeshPreview, evidence.jobId, signal,
      state => setMeshUpdate({ ...state, scope: candidateSessionKey }))
      .catch(() => { if (!signal.aborted) setMeshUpdate({ scope: candidateSessionKey, status: "read-error", media: null }); });
    return () => controller.abort();
  }, [api.getMeshPreview, candidateSessionKey, evidence.jobId, initialMeshStatus, meshOnline, replaySignal, meshReadAttempt]);
  const [preferredKind, setPreferredKind] = useState<"original" | "annotated">("original");
  const selectedKind: "original" | "annotated" = (preferredKind === "original" && originalSrc) || !annotatedSrc ? "original" : "annotated";
  const usingOriginal = selectedKind === "original";
  const usingLocalVideo = usingOriginal && Boolean(localVideoSrc);
  const loadPoseWindow = useCallback(async (startMs: number, signal: AbortSignal) => {
    const preview = await api.getPosePreview(evidence.jobId!, { startMs, durationMs: 10000, sampleLimit: 600, signal: AbortSignal.any([signal, replaySignal]) });
    if (usingOriginal && !usingLocalVideo && originalTimeBinding && !poseWindowMatchesOriginal(preview, originalTimeBinding)) {
      throw new Error("原片与动作关键点的时间来源不一致。");
    }
    return preview;
  }, [api, replaySignal, evidence.jobId, usingOriginal, usingLocalVideo, originalTimeBinding]);
  const selectedVideoSrc = usingOriginal ? originalSrc : annotatedSrc;
  const [videoFailed, setVideoFailed] = useState<string | null>(null);
  const [originalSeek, setOriginalSeek] = useState<number | null>(null);
  useEffect(() => {
    const seekOriginal = (event: Event) => {
      const detail = (event as CustomEvent<{ timestampMs?: number; jobId?: string; preferOriginal?: boolean }>).detail;
      if (!detail?.preferOriginal || !originalSrc || !Number.isFinite(detail.timestampMs) || detail.jobId && detail.jobId !== evidence.jobId) return;
      if (showSurface || !usingOriginal) {
        setShowSurface(false); setPreferredKind("original"); setVideoFailed(null); setOriginalSeek(detail.timestampMs!);
      }
    };
    window.addEventListener("rallymate:seek-video", seekOriginal);
    return () => window.removeEventListener("rallymate:seek-video", seekOriginal);
  }, [evidence.jobId, originalSrc, showSurface, usingOriginal]);
  useEffect(() => {
    if (originalSeek === null || showSurface || !usingOriginal) return;
    const frame = requestAnimationFrame(() => {
      window.dispatchEvent(new CustomEvent("rallymate:seek-video", { detail: { timestampMs: originalSeek, jobId: evidence.jobId } }));
      setOriginalSeek(null);
    });
    return () => cancelAnimationFrame(frame);
  }, [originalSeek, showSurface, usingOriginal, evidence.jobId]);
  const embeddedPoints = evidence.result?.features?.ball?.trajectory?.points ?? [];
  const candidateReplayBinding = useMemo(() => ({
    jobId: evidence.jobId ?? "", sessionKey: candidateSessionKey,
    mediaKey: selectedVideoSrc ?? "", kind: selectedKind,
    // An authenticated original SHA establishes file identity, not its mapping
    // to the report/candidate clock. Keep candidate seeking unverified.
    videoSha256: usingOriginal && !usingLocalVideo ? serverOriginal?.sha256 ?? null : null, originMs: null, mappingVerified: false,
  }), [evidence.jobId, candidateSessionKey, selectedVideoSrc, selectedKind, usingOriginal, usingLocalVideo, serverOriginal?.sha256]);
  useEffect(() => {
    if (meshAvailable && showSurface) onCandidateReplayHandle(null);
  }, [meshAvailable, showSurface, onCandidateReplayHandle]);
  return <div className="replay-media-scope" ref={replayContainer}>
    {meshOnline && meshStatus === "pending" && <p className="replay-caption" role="status">人体表面生成中，评分可先查看。完成后会自动显示官方预览。</p>}
    {meshOnline && meshStatus === "failed" && <p className="replay-caption" role="status">人体表面预览暂未生成成功；本次评分和原片回放仍可查看。</p>}
    {meshOnline && meshStatus === "read-error" && <p className="replay-caption" role="status">暂未读取到人体表面预览状态，已停止本次等待。报告和原片回放仍可查看。</p>}
    {meshOnline && ["failed", "read-error"].includes(String(meshStatus)) && <button type="button" className="product-secondary" onClick={() => { setMeshUpdate({ scope: candidateSessionKey, status: "pending", media: null }); setMeshReadAttempt(attempt => attempt + 1); }}>重新检查官方预览</button>}
    {meshMedia && <div className="replay-overlay-switches" role="group" aria-label="动作回放显示方式">
      <button type="button" className="product-secondary" aria-pressed={showSurface} onClick={() => setShowSurface(true)}>人体表面 · 官方渲染</button>
      <button type="button" className="product-secondary" aria-pressed={!showSurface} onClick={() => setShowSurface(false)}>原片与关键点</button>
    </div>}
    {meshMedia && showSurface ? <OfficialMeshReplay media={meshMedia} jobId={evidence.jobId!} /> : <>
    {(originalSrc || annotatedSrc) && <div className="replay-overlay-switches" role="group" aria-label="回放视频选择">
      <button type="button" className="product-secondary" disabled={!originalSrc} aria-pressed={usingOriginal && Boolean(selectedVideoSrc)} onClick={() => { setPreferredKind("original"); setVideoFailed(null); }}>原视频</button>
      <button type="button" className="product-secondary" disabled={!annotatedSrc} aria-pressed={!usingOriginal && Boolean(selectedVideoSrc)} onClick={() => { setPreferredKind("annotated"); setVideoFailed(null); }}>标注回放</button>
      {!originalSrc && <span role="status">本次报告暂未提供可验证的原视频，场地取图不可用。</span>}
    </div>}
    <BallTrajectoryViewer key={selectedVideoSrc ?? "coordinates"} trajectory={trajectory} pending={pending} error={error}
      initialOverlays={{ pose: true }} quickAnalysisTools
      jobId={evidence.jobId} loadPoseWindow={onlineEvidence && evidence.jobId && !pending ? loadPoseWindow : undefined}
      poseTimingPreserved={usingOriginal ? usingLocalVideo || Boolean(originalTimeBinding) : poseReplayTimingAllowed(false, evidence.result)}
      videoKind={selectedVideoSrc ? selectedKind : null}
      videoSrc={videoFailed !== selectedVideoSrc ? selectedVideoSrc : null}
      poster={onlineEvidence && evidence.result?.artifact_urls?.["preview.jpg"] ? `${mediaBase}/preview.jpg` : null}
      embeddedPoints={embeddedPoints.map((point, index) => ({ ...point, timestamp_ms: point.timestamp_ms ?? index }))}
      videoTimeOriginMs={usingOriginal ? 0 : (trajectory?.source.start_timestamp_ms ?? 0)}
      candidateReplayBinding={candidateReplayBinding} onCandidateReplayHandle={onCandidateReplayHandle}
      onVideoError={() => setVideoFailed(selectedVideoSrc ?? "unknown")} />
    {selectedVideoSrc && videoFailed !== selectedVideoSrc && <p className="replay-caption">当前播放：{usingOriginal ? usingLocalVideo ? "本地原始视频" : "已校验来源的原视频" : "服务器标注回放"}。图像换序线索的定位需另行验证该媒体的时间映射。</p>}
    <p className="replay-caption">{selectedVideoSrc && videoFailed !== selectedVideoSrc ? "选择下方动作片段快速回看，慢速播放帮助你看清动作细节。" : "当前结果尚未提供可回放的视频。"}</p>
    <details className="report-replay-details"><summary>回放说明</summary><BallTrajectorySummary trajectory={trajectory} pending={pending} error={error} />
      {evidence.error && <p className="upload-error" role="status">部分增强证据暂不可用：{evidence.error}</p>}
    </details>
    </>}
    <ReplayClipNavigator key={candidateSessionKey} containerRef={replayContainer} result={evidence.result ?? null} motion={clipMotion}
      jobId={evidence.jobId}
      mediaKey={meshMedia && showSurface ? meshMedia.video_url : selectedVideoSrc ?? "no-video"} />
    {onlineEvidence && evidence.jobId && serverOriginal && !pending && <SwingKinematicsPanel key={`swing:${candidateSessionKey}`}
      context={{ jobId: evidence.jobId, videoSha256: serverOriginal.sha256, motion: clipMotion, durationMs: swingVideoDurationMs(evidence.result) }}
      initialData={evidence.result?.swing_kinematics} load={api.getSwingKinematics} signal={replaySignal} />}
  </div>;
}

export default function ScoreLab() {
  const journal = useTrainingJournal();
  const { visitReport } = journal;
  const [currentJournalRecordId, setCurrentJournalRecordId] = useState<string | undefined>(undefined);
  const [view, setView] = useState<WorkspaceView>("overview");
  const [history, setHistory] = useState<RecentReport[]>([]);
  const [importState, setImportState] = useState<ImportState>({ status: "idle" });
  const [importedSummary, setImportedSummary] = useState<Record<string, unknown> | null>(null);
  const [scoreContext, setScoreContext] = useState<"demo" | "live-pending" | "live">("demo");
  const [videoFile, updateVideoFile] = useState<File | null>(null);
  const [localVideoSrc, setLocalVideoSrc] = useState<string | null>(null);
  const [localVideoJobId, setLocalVideoJobId] = useState<string | null>(null);
  const [uploadProgress, setUploadProgress] = useState<UploadProgress | null>(null);
  const [uploadState, setUploadState] = useState<"idle" | "ready" | "uploading" | "processing" | "complete" | "cancelled" | "error">("idle");
  const [job, setJob] = useState<JobProgress | null>(null);
  const [cancelRequest, setCancelRequest] = useState<{ jobId: string; pending: boolean; error?: string } | null>(null);
  const [progressNow, setProgressNow] = useState(Date.now);
  const [uploadError, setUploadError] = useState("");
  const [missingJob, setMissingJob] = useState(false);
  const [openedAt, setOpenedAt] = useState<number | null>(null);
  const [readDiagnostics, setReadDiagnostics] = useState<readonly RequestDiagnostic[]>([]);
  const [supplementingEvidence, setSupplementingEvidence] = useState(false);
  const [reportRetryCount, setReportRetryCount] = useState(0);
  const [replayScope, setReplayScope] = useState(() => new AbortController());
  const [candidateGeneration, setCandidateGeneration] = useState(0);
  const [candidateReplay, setCandidateReplay] = useState<CandidateReplayHandle | null>(null);
  const [evidence, setEvidence] = useState<LiveEvidence>(EMPTY_EVIDENCE);
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
  const adviceAbort = useRef<AbortController | null>(null);
  const replayAbort = useRef(replayScope);
  const activeServiceJob = useRef<string | null>(null);
  const lastProgress = useRef<JobProgress | null>(null);
  const cancelInFlight = useRef<{ jobId: string; runId: number } | null>(null);
  const resetAdvice = useCallback(() => {
    adviceAbort.current?.abort(); adviceAbort.current = null;
    setAdviceLoading(false); setAdviceNotice(""); setSavedAdvice(null);
  }, []);
  const resetReplayReads = useCallback(() => {
    // Invalidate old diagnostics immediately, before child effect cleanup runs.
    replayAbort.current.abort();
    const next = new AbortController(); replayAbort.current = next;
    // A new scope also restarts an unchanged job/window after report import.
    setReplayScope(next);
    setCandidateGeneration(previous => previous + 1);
    setCandidateReplay(null);
  }, []);
  const apiClient = useMemo(() => createApiClient({}, undefined, undefined, diagnostic => {
    const safe = safeRequestDiagnostic(diagnostic);
    if (safe) setReadDiagnostics(previous => [...previous, safe].slice(-48));
  }), []);
  const busy = uploadState === "uploading" || uploadState === "processing";
  const candidateSessionKey = `${evidence.jobId ?? "none"}:${candidateGeneration}`;
  const replayMediaKind = candidateReplay?.binding.jobId === evidence.jobId && candidateReplay?.binding.sessionKey === candidateSessionKey
    ? candidateReplay.binding.kind : null;
  const candidateContext = useMemo<FootCrossingPanelContext | undefined>(() => {
    // Only a completed read from the online service grants discovery. Imported
    // reports deliberately do not acquire this marker from their embedded ID.
    const id = candidateOnlineJobId(evidence, busy);
    if (!id) return undefined;
    return { jobId: id, sessionKey: candidateSessionKey, signal: replayScope.signal,
      load: signal => apiClient.getFootCrossingCandidates(id, { signal: AbortSignal.any([signal, replayScope.signal]) }),
      replay: candidateReplay };
  }, [apiClient, evidence, busy, candidateSessionKey, replayScope, candidateReplay]);
  useEffect(() => {
    if (!busy) return;
    const timer = window.setInterval(() => setProgressNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [busy]);
  const hasReport = scoreContext !== "demo";
  const adviceTechnique = coachTechnique || (evidence.assessment?.family_summary?.footwork?.observed_count ? "步伐" : "底线击球");
  const delivery = analysisViewState({ uploadState, jobStatus: job?.status, hasResult: Boolean(evidence.result || evidence.assessment), hasReplay: Boolean(evidence.trajectory || localVideoSrc && localVideoJobId === evidence.jobId), missingJob, warning: evidence.error });
  const currentAdviceKey = adviceContextKey(evidence.jobId, adviceTechnique);
  const currentAdvice = adviceForContext(savedAdvice, currentAdviceKey);
  const journalId = currentJournalRecordId || (evidence.jobId && importState.status !== "success" ? journalRecordId("online", evidence.jobId) : undefined);
  const currentName = journal.records.find(item => item.id === journalId)?.name || videoFile?.name || (importState.status === "success" ? importState.fileName : job && job.id === evidence.jobId && typeof job.original_filename === "string" ? job.original_filename.split(/[\\/]/).pop() : undefined) || history.find(item => item.id === evidence.jobId)?.name || "训练视频";
  const guidance = useMemo(() => trainingGuidance(evidence.result ?? null, motionAnalysisOf(evidence.result ?? null, evidence.assessment, importedSummary)), [evidence.result, evidence.assessment, importedSummary]);
  const focusId = (focus: TrainingFocusSelection) => `review:${focus.title}:${focus.timestampMs ?? "untimed"}`;
  const focusSuggestions = guidance.slice(0, 2).map(item => { const focus = focusSelection(item); return { ...focus, id: focusId(focus) }; });
  const replayAvailable = Boolean(localVideoSrc && localVideoJobId === evidence.jobId || evidence.candidateSource === "online" && evidence.result?.job_id === evidence.jobId && evidence.result?.original_video);

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
    resetAdvice(); setSupplementingEvidence(false); setReportRetryCount(0);
    resetReplayReads();
    setReadDiagnostics([]);
    lastProgress.current = null; cancelInFlight.current = null; setCancelRequest(null);
    setMissingJob(false); setOpenedAt(null);
    setCurrentJournalRecordId(undefined);
    try { localStorage.removeItem("rallymate.activeJob"); } catch { /* Optional storage. */ }
  }

  const monitorJob = useCallback(async (id: string) => {
    analysisRunRef.current += 1;
    activeServiceJob.current = id;
    analysisAbort.current?.abort();
    resetAdvice(); setSupplementingEvidence(false); setReportRetryCount(0);
    resetReplayReads();
    const controller = new AbortController(); analysisAbort.current = controller;
    setReadDiagnostics([]);
    setCurrentJournalRecordId(journalRecordId("online", id));
    lastProgress.current = { id, status: "queued" }; cancelInFlight.current = null; setCancelRequest(null);
    setImportedSummary(null); setUploadState("processing"); setUploadError(""); setMissingJob(false); setOpenedAt(Date.now()); setScoreContext("live-pending"); setJob({ id, status: "queued" });
    try { localStorage.setItem("rallymate.activeJob", id); } catch { /* Optional storage. */ }
    setEvidence(previous => previous.jobId === id && previous.candidateSource === "online" ? { ...previous, error: null } : { mode: "live", jobId: id, trajectory: null, assessment: null, error: null });
    try {
      const completed = await watchAnalysis(apiClient, id, controller.signal, incoming => {
        if (controller.signal.aborted || activeServiceJob.current !== id) return;
        if (incoming.message === "推理已完成，正在重试读取动作结果。无需重新上传。") setReportRetryCount(previous => previous + 1);
        lastProgress.current = mergeAnalysisProgress(lastProgress.current, incoming);
        setJob(lastProgress.current);
      }, 2000, preview => {
        if (!controller.signal.aborted) setEvidence(previous => previous.jobId === id ? { ...previous, trajectory: preview } : previous);
      }, result => {
        if (!controller.signal.aborted && activeServiceJob.current === id) {
          visitReport({ jobId: id, name: typeof lastProgress.current?.original_filename === "string" ? lastProgress.current.original_filename : "训练视频", source: "online", result });
          setEvidence(previous => previous.jobId === id ? { ...previous, candidateSource: "online", result, assessment: result.technique_assessment ?? null } : previous);
          // Supplemental evidence must not keep unrelated report actions locked.
          setScoreContext("live"); setUploadState("complete"); setSupplementingEvidence(true);
        }
      });
      if (controller.signal.aborted) return;
      setEvidence({ mode: "live", candidateSource: "online", jobId: id, trajectory: completed.trajectory, assessment: completed.assessment ?? completed.result.technique_assessment ?? null, result: completed.result, error: completed.warning });
      setScoreContext("live"); setUploadState("complete");
      setCurrentJournalRecordId(visitReport({ jobId: id, name: typeof completed.job.original_filename === "string" ? completed.job.original_filename : "训练视频", source: "online", result: completed.result, trajectory: completed.trajectory }));
      try {
        const existing = recentReports(JSON.parse(localStorage.getItem("rallymate.recentReports") || "[]"));
        const next = recentReports([{ id, name: (typeof completed.job.original_filename === "string" ? completed.job.original_filename.split(/[\\/]/).pop() : undefined) || existing.find(item => item.id === id)?.name || "训练视频", viewedAt: new Date().toISOString() }, ...existing.filter(item => item.id !== id)]);
        localStorage.setItem("rallymate.recentReports", JSON.stringify(next)); setHistory(next);
      } catch { /* History remains optional in private browsing. */ }
    } catch (error) {
      if (controller.signal.aborted) return;
      if (error instanceof AnalysisCancelledError) {
        setUploadState("cancelled"); setUploadError(""); setScoreContext("live"); return;
      }
      setMissingJob(error instanceof RallyMateApiError && error.status === 404);
      setUploadState("error"); setUploadError(error instanceof Error ? error.message : "连接中断，稍后可继续读取结果，无需重新上传。");
      // Reopening this same record must reconnect after a failed read.
      controller.abort();
    } finally {
      if (analysisAbort.current === controller) setSupplementingEvidence(false);
    }
  }, [apiClient, resetAdvice, resetReplayReads, visitReport]);

  function stopAnalysisRead() {
    if (uploadState !== "processing") return;
    analysisAbort.current?.abort(); setSupplementingEvidence(false);
    setUploadState("error"); setUploadError(["succeeded", "completed"].includes(job?.status ?? "")
      ? "已停止本次等待。服务器报告已保留，可继续读取，也可选择新视频。"
      : "已暂停读取任务状态。服务器任务继续运行，稍后可继续读取，无需重新上传。");
  }

  async function cancelAnalysis() {
    if (!job || activeServiceJob.current !== job.id || !["queued", "running", "processing"].includes(job.status) || job.cancel_requested || cancelInFlight.current) return;
    const request = { jobId: job.id, runId: analysisRunRef.current };
    cancelInFlight.current = request; setCancelRequest({ jobId: job.id, pending: true });
    const current = () => cancelInFlight.current === request && analysisRunRef.current === request.runId && activeServiceJob.current === request.jobId && !analysisAbort.current?.signal.aborted;
    try {
      const response = await apiClient.cancelJob(request.jobId);
      if (!current()) return;
      lastProgress.current = mergeAnalysisProgress(lastProgress.current, response);
      setJob(lastProgress.current); setCancelRequest(null);
      if (lastProgress.current.status === "cancelled") {
        // Stop local polling only after the server has explicitly confirmed cancellation.
        analysisAbort.current?.abort(); setUploadState("cancelled"); setUploadError(""); setScoreContext("live");
      }
    } catch {
      if (current()) setCancelRequest({ jobId: request.jobId, pending: false, error: "暂未确认取消请求。任务可能仍在运行，可重试取消或继续读取结果。" });
    } finally {
      if (cancelInFlight.current === request) cancelInFlight.current = null;
    }
  }

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
    return () => { window.removeEventListener("popstate", restore); analysisAbort.current?.abort(); adviceAbort.current?.abort(); replayAbort.current.abort(); };
  }, [monitorJob]);

  function openReport(id: string) {
    if (id === activeServiceJob.current) {
      navigate("analysis", id);
      if (analysisAbort.current?.signal.aborted && delivery.canResumeRead) void monitorJob(id);
      return;
    }
    if (uploadState === "uploading") { navigate("analysis", activeServiceJob.current ?? undefined); return; }
    setVideoFile(null); setImportState({ status: "idle" }); navigate("analysis", id); void monitorJob(id);
  }
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
      setCurrentJournalRecordId(visitReport({ jobId: submitted.id, name: videoFile.name, source: "online", linkPending: true }));
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
    if (evidence.candidateSource !== "online" || adviceAbort.current) return;
    const controller = new AbortController(); adviceAbort.current = controller;
    const signal = AbortSignal.any([controller.signal, AbortSignal.timeout(20000)]);
    setAdviceLoading(true); setAdviceNotice("");
    const contextKey = currentAdviceKey;
    setSavedAdvice({ contextKey, response: {} });
    try {
      const response = await fetch("/api/advice", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ technique: adviceTechnique, skillLevel: "业余进阶", sessionGoal: "稳定性", observations: adviceNotes, ...(evidence.jobId ? { jobId: evidence.jobId } : {}) }), signal });
      const payload = await response.json() as AdviceResponse;
      signal.throwIfAborted();
      if (!response.ok) throw new Error(response.status === 429 ? "提问较频繁，请稍后再试。" : response.status === 400 ? "请用 600 字以内描述网球练习问题，避免填写私人信息。" : "建议服务暂时不可用，请稍后重试。");
      if (!payload.advice) throw new Error("建议返回不完整，当前没有可展示的建议；请稍后重试。");
      setSavedAdvice({ contextKey, response: payload });
      setAdviceNotice(describeAdvice(payload));
    } catch (error) {
      if (!controller.signal.aborted) setAdviceNotice(signal.aborted ? "建议读取超时，请重试。当前报告仍可查看。" : error instanceof Error ? error.message : "建议暂时不可用");
    } finally {
      if (adviceAbort.current === controller) { adviceAbort.current = null; setAdviceLoading(false); }
    }
  }

  async function importSummary(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file || busy) return;
    navigate("analysis");
    invalidateAnalysisRun();
    const importRun = analysisRunRef.current;
    setVideoFile(null);
    setEvidence(EMPTY_EVIDENCE); setJob(null); setScoreContext("demo"); setUploadState("idle");
    setImportState({ status: "reading", fileName: file.name });
    setUploadError("");
    try {
      const imported = await readReportJson(file);
      if (importRun !== analysisRunRef.current) return;
      const data = imported && typeof imported === "object" ? imported as Record<string, unknown> : {};
      const candidateTrajectory = data.trajectory && typeof data.trajectory === "object" ? data.trajectory as Partial<TrajectoryPreviewResponse> : null;
      const importedTrajectory = candidateTrajectory?.source && candidateTrajectory.ball && Array.isArray(candidateTrajectory.ball.observed) && typeof candidateTrajectory.status === "string" ? candidateTrajectory as TrajectoryPreviewResponse : null;
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
          setEvidence({ mode: "live", jobId: data.jobId, trajectory: importedTrajectory, assessment: embeddedAssessment, result: embeddedResult ?? fallbackResult, error: null });
          setJob({ id: data.jobId, status: "completed" });
          setUploadState("complete"); setScoreContext("live");
          setImportState({ status: "success", fileName: file.name });
          setCurrentJournalRecordId(visitReport({ jobId: data.jobId, name: file.name, source: "imported", sourceId: `${file.name}:${file.size}`, result: embeddedResult ?? fallbackResult }));
          return;
        }
        try { localStorage.setItem("rallymate.activeJob", data.jobId); } catch { /* Optional storage. */ }
        setImportState({ status: "success", fileName: file.name });
        navigate("analysis", data.jobId);
        setImportState({ status: "idle" });
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
        setEvidence({ mode: "live", jobId, trajectory: importedTrajectory, assessment: importedAssessment, result: rawResult ?? { technique_assessment: importedAssessment, ...(jobId ? { job_id: jobId } : {}) }, error: null });
        setScoreContext("live");
        setImportState({ status: "success", fileName: file.name });
        setCurrentJournalRecordId(visitReport({ jobId, name: file.name, source: "imported", sourceId: `${file.name}:${file.size}`, result: rawResult }));
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
      setCurrentJournalRecordId(visitReport({ jobId: importedResult.job_id, name: file.name, source: "imported", sourceId: `${file.name}:${file.size}`, result: importedResult }));
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
  const processing = analysisProgress(job, progressNow);
  const progress = uploadState === "uploading" ? uploadProgress?.percent ?? 0 : processing.percent;
  const organizingReport = uploadState === "processing" && processing.reportStage;
  const reportDiagnostic = readDiagnostics.findLast(diagnostic => diagnostic.operation === "report");
  const recoveryTitle = uploadError.startsWith("已暂停读取任务状态") ? "已暂停读取任务状态" : delivery.recoveryTitle;
  const stageLabel = uploadState === "uploading" ? uploadProgress?.message ?? "正在准备视频" : delivery.phase === "partial" ? "报告已可查看，正在补充画面证据" : processing.title;
  const canCancel = uploadState !== "uploading" && job?.id === evidence.jobId && job && ["queued", "running", "processing"].includes(job.status);
  const cancelPending = cancelRequest?.jobId === job?.id && cancelRequest?.pending;
  const openSection = (id: string) => { setReportSection(id); const section = document.getElementById(id); for (let node: HTMLElement | null = section; node; node = node.parentElement) if (node instanceof HTMLDetailsElement) node.open = true; section?.scrollIntoView({ behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth", block: "start" }); };
  const saveFocus = (focus: TrainingFocusSelection) => { if (journalId && journal.saveFocus(journalId, { ...focus, id: focusId(focus) })) openSection("training-journal"); };
  const journalPanel = <TrainingJournalPanel key={journalId} journal={journal} recordId={journalId} suggestions={focusSuggestions} showSuggestions={false} onSeek={replayAvailable ? timestampMs => window.dispatchEvent(new CustomEvent("rallymate:seek-video", { detail: { timestampMs, jobId: evidence.jobId, preferOriginal: true } })) : undefined} onOpenReport={openReport} />;
  const homeReports = journal.ready ? journal.records.filter(item => item.source === "online" && item.jobId).map(item => ({ id: item.jobId!, name: item.name, viewedAt: item.viewedAt })) : history;
  return <ProductShell view={view} onNavigate={next => navigate(next, next === "analysis" ? activeServiceJob.current ?? undefined : undefined)} onNewAnalysis={newAnalysis} currentName={hasReport || videoFile ? currentName : undefined} annotationJobId={view === "analysis" && importState.status !== "success" ? evidence.jobId : undefined} busy={busy} onImportReport={() => fileInput.current?.click()} importBusy={importState.status === "reading"}>
    <input ref={videoInput} type="file" accept="video/mp4,video/quicktime,video/x-msvideo,video/x-matroska,.mp4,.mov,.m4v,.avi,.mkv" hidden onChange={chooseVideo} />
    <input ref={fileInput} type="file" accept="application/json,.json" hidden onChange={importSummary} />
    {view === "overview" && <TrainingHome reports={homeReports} onOpenReport={openReport} onNewAnalysis={newAnalysis} onNavigate={next => navigate(next, next === "analysis" ? activeServiceJob.current ?? undefined : undefined)} currentName={hasReport ? currentName : undefined} busy={busy} />}
    {view === "sessions" && <><TrainingJournalLibrary journal={journal} onOpenReport={openReport} onNewAnalysis={newAnalysis} /><div className="product-library-import"><p>已有分析报告？导入备份文件继续查看。</p><button type="button" className="product-secondary" disabled={busy} onClick={() => fileInput.current?.click()}><ProductIcon name="upload" size={16} />导入报告</button></div></>}
    {view === "guide" && <CaptureGuide onNewAnalysis={newAnalysis} />}
    {view === "analysis" && <div className={`product-analysis ${hasReport ? "has-report" : ""}`} aria-busy={importState.status === "reading"}>
      <div className="product-page-heading"><div><h1>{delivery.showReport ? "这次训练，先看这些" : hasReport ? missingJob ? "找回这次训练" : uploadState === "cancelled" ? "本次分析已取消" : uploadState === "error" ? "本次分析需要处理" : "正在准备本次复盘" : "开始一次新的训练分析"}</h1><p>{hasReport ? currentName : "上传训练视频，把值得回看的动作留下来。"}</p></div></div>
      {importState.status !== "idle" && <div className={`product-notice import-${importState.status}`} role="status">{importState.status === "reading" ? `正在读取 ${importState.fileName}…` : importState.status === "success" ? `已载入 ${importState.fileName}` : importState.message}</div>}
      {!hasReport && importState.status !== "reading" && <div className="product-upload-layout"><section className="product-upload-panel" aria-labelledby="upload-title"><div className="product-panel-title"><div><h2 id="upload-title">选择训练视频</h2></div></div>
        <button type="button" className={`product-dropzone ${dragging ? "is-dragging" : ""} ${videoFile ? "has-file" : ""}`} onClick={() => videoInput.current?.click()} onDragOver={event => { event.preventDefault(); setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={event => { event.preventDefault(); setDragging(false); acceptVideoFile(event.dataTransfer.files?.[0]); }} aria-label="选择或拖入视频文件"><span className="product-upload-symbol"><ProductIcon name={videoFile ? "check" : "upload"} size={28} /></span><strong>{videoFile ? videoFile.name : "把视频拖到这里"}</strong><span>{videoFile ? `${formatBytes(videoFile.size)} · 已准备好` : "或点击选择本地视频"}</span><small>MP4、MOV、M4V、AVI、MKV · 建议 200 MB 以内</small></button>
        {videoFile && <div className="product-file-actions"><span><ProductIcon name="check" size={15} />视频已选择</span><button type="button" onClick={() => { setVideoFile(null); setUploadState("idle"); setUploadError(""); }}>移除视频</button></div>}
        {videoFile && <UploadVideoPreview file={videoFile} src={localVideoSrc} />}
        <TrainingFollowUpPicker journal={journal} />
        {uploadError && <p className="product-notice is-error" role="alert">{uploadError}</p>}
        <button type="button" className="product-primary product-upload-start" disabled={!videoFile} onClick={() => void submitVideo()}>开始分析<ProductIcon name="arrow-right" size={18} /></button><p className="product-upload-footnote">上传后会显示处理进度。网络中断后可继续读取，无需重复上传。</p>
      </section><aside className="product-capture-checklist"><h2>拍清楚，<br />才看得更明白。</h2><div><ProductIcon name="frame" /><span><strong>全身和双脚入镜</strong><p>保留挥拍空间，尽量减少遮挡。</p></span></div><div><ProductIcon name="camera" /><span><strong>固定相机</strong><p>稳定机位，避免跟随人物频繁移动。</p></span></div><div><ProductIcon name="movement" /><span><strong>保留完整动作</strong><p>从准备到随挥，再到恢复。</p></span></div><button type="button" onClick={() => navigate("guide")}>查看完整拍摄指南<ProductIcon name="arrow-up-right" size={16} /></button></aside></div>}
      {busy && <section className="product-processing" aria-label="当前分析进度">
        <div className="product-processing-heading"><span className="product-processing-icon"><ProductIcon name="movement" size={22} /></span><div><h2 role="status">{stageLabel}</h2><p>{uploadState === "uploading" ? "保留当前页面，上传完成后将自动开始分析。" : delivery.phase === "partial" ? "下方已有结果可先复盘。其余证据到达后会自动补齐。" : processing.detail}</p></div>{!organizingReport && <strong>{Math.floor(progress)}<small>%</small><span className="processing-percent-scope">{uploadState === "uploading" ? "上传进度" : "当前分析阶段"}</span></strong>}</div>
        <div className={`product-progress-track${organizingReport ? " is-organizing" : ""}`} role="progressbar" aria-label={stageLabel} aria-valuemin={0} aria-valuemax={100} aria-valuenow={organizingReport ? undefined : Math.floor(progress)} aria-valuetext={organizingReport ? "报告整理中，尚未全部读取" : undefined}><span style={{ width: organizingReport ? "100%" : `${progress}%` }} /></div>
        <ol className="product-processing-steps"><li className={uploadState === "uploading" ? "current" : "done"}>上传视频</li><li className={uploadState === "processing" ? processing.reportStage ? "done" : "current" : ""}>分析动作</li><li className={uploadState === "processing" && processing.reportStage ? "current" : ""}>整理报告</li></ol>
        {uploadProgress && uploadState === "uploading" && <p className="product-processing-meta">服务器已收到 {(uploadProgress.uploadedBytes / 1048576).toFixed(1)} / {(uploadProgress.totalBytes / 1048576).toFixed(1)} MB</p>}
        {uploadState === "processing" && <><p className="product-processing-meta"><span>{processing.count}</span><span aria-live="off">{openedAt !== null ? `本次打开已等待 ${formatElapsed((progressNow - openedAt) / 1000)}` : ""}</span></p>{processing.elapsed && <p className="product-processing-meta" aria-live="off">{processing.elapsed}（服务器记录）</p>}{processing.notice && <p className="product-processing-notice" role="status">{processing.notice}</p>}</>}
      </section>}
      {uploadState === "processing" && <section className="product-recovery" aria-label="读取控制"><div>{["succeeded", "completed"].includes(job?.status ?? "") ? <><p role="status">{reportReadStage(reportDiagnostic)}{reportRetryCount > 0 ? ` · 已自动重试 ${reportRetryCount}/2 次` : ""}</p><p>分析已完成。停止等待后可重新连接，无需再次上传。</p>{reportDiagnostic && <p>当前请求已等待 {formatElapsed(Math.max(0, progressNow - Date.parse(reportDiagnostic.startedAt)) / 1000)}{reportDiagnostic.requestId ? ` · 请求编号 ${reportDiagnostic.requestId}` : ""}</p>}</> : <p>可暂停本页的进度读取。服务器任务继续运行，稍后可重新连接。</p>}</div><button type="button" className="product-secondary" onClick={stopAnalysisRead}>停止等待</button></section>}
      {canCancel && <section className="product-recovery" aria-label="取消当前分析"><div><p role="status">{job.cancel_requested ? "取消请求已接收，正在等待服务器确认停止。" : cancelPending ? "正在发送取消请求；仍会继续读取任务状态。" : "可请求服务器停止本次分析。"}</p>{!job.cancel_requested && cancelRequest?.jobId === job.id && cancelRequest.error && <p role="alert">{cancelRequest.error}</p>}</div><button type="button" className="product-secondary" disabled={Boolean(cancelPending || job.cancel_requested)} onClick={() => void cancelAnalysis()}>{job.cancel_requested ? "正在取消…" : cancelPending ? "正在发送…" : "取消分析"}</button></section>}
      {job?.status === "cancelled" && <section className="product-recovery" role="status"><div><h2>本次分析已取消</h2><p>服务器已确认停止处理。已保留本页已有的原视频与回放证据。</p></div><button type="button" className="product-secondary" onClick={newAnalysis}>选择新视频</button></section>}
      {hasReport && uploadError && <section className="product-recovery" role="alert"><div><h2>{recoveryTitle}</h2><p>{delivery.recoveryDetail || uploadError}</p>{delivery.showReport && <p>本页已读取的结果仍可查看。</p>}</div>{missingJob ? <div className="recovery-actions"><button type="button" className="product-secondary" onClick={() => fileInput.current?.click()}>导入报告备份</button><button type="button" className="product-secondary" onClick={() => navigate("sessions")}>返回训练记录</button><button type="button" className="product-secondary" onClick={newAnalysis}>选择新视频</button></div> : <button type="button" className="product-secondary" onClick={() => { if (evidence.jobId && delivery.canResumeRead) void monitorJob(evidence.jobId); else if (videoFile) void submitVideo(); else newAnalysis(); }}>{evidence.jobId && delivery.canResumeRead ? "继续读取结果" : videoFile ? "重试分析" : "选择新视频"}</button>}</section>}
      {delivery.showNavigation && <nav className="product-report-nav" aria-label="报告内容">{[["recommendations", "本次观察"], ["evidence", "回看原片"], ["training-journal", "训练关注点"], ["scoreboard", "深入查看"], ["coach", "问问这段动作"]].map(([id, label]) => <button key={id} type="button" aria-current={reportSection === id ? "location" : undefined} onClick={() => openSection(id)}>{label}</button>)}</nav>}
      {uploadState === "complete" && delivery.showReport && <p className="report-delivery-status" role="status">{supplementingEvidence ? "报告已可查看 · 正在补充画面证据，其他操作可正常使用" : evidence.error ? `报告部分可用 · ${evidence.error}` : replayAvailable ? "报告已就绪 · 可查看本次观察、回放并保存关注点" : "报告已就绪 · 可查看本次观察并保存关注点，当前未附可用原片"}</p>}
      {delivery.showReplayOnly && <TrainingReplayPanel jobId={evidence.jobId} mediaKind={replayMediaKind}><p className="replay-caption">{busy ? "回放预览已可用，完整观察仍在整理。" : "本次未形成完整报告，已可用的回放保留在这里。"}</p><ReplayPanel api={apiClient} replaySignal={replayScope.signal} evidence={evidence} pending={busy} error={evidence.error} localVideoSrc={localVideoJobId === evidence.jobId ? localVideoSrc : null} candidateSessionKey={candidateSessionKey} onCandidateReplayHandle={setCandidateReplay} /></TrainingReplayPanel>}
      {delivery.showReport && <LiveResults onSaveFocus={saveFocus} journal={journalPanel} replayAvailable={replayAvailable} key={evidence.jobId ?? "pending"} status={job?.status} error={uploadError || evidence.error} result={evidence.result ?? null} assessment={evidence.assessment} pending={busy} trajectory={evidence.trajectory} summary={importedSummary ?? (job?.summary && typeof job.summary === "object" ? job.summary as Record<string, unknown> : null)} candidateContext={candidateContext} replay={<TrainingReplayPanel key={evidence.jobId ?? evidence.mode} jobId={evidence.jobId} mediaKind={replayMediaKind}><ReplayPanel api={apiClient} replaySignal={replayScope.signal} evidence={evidence} pending={busy} error={evidence.error} localVideoSrc={localVideoJobId === evidence.jobId ? localVideoSrc : null} candidateSessionKey={candidateSessionKey} onCandidateReplayHandle={setCandidateReplay} /></TrainingReplayPanel>} />}
      {delivery.showReport && !busy && <><section className="report-coach coach-section" id="coach" aria-labelledby="coach-heading"><div className="logic-heading"><div><span className="process-eyebrow">结合本次证据</span><h2 id="coach-heading">问问这段动作</h2><p>选择一个复盘问题，或补充你在原片里看到的情况。无法判断的部分会明确说明。</p></div></div><div className="coach-layout"><form className="coach-form" onSubmit={event => { event.preventDefault(); void askCoach(); }}><label>练习主题<select aria-label="练习主题" value={adviceTechnique} onChange={event => setCoachTechnique(event.target.value)}>{["底线击球", "发球", "接发", "网前进攻", "步伐"].map(name => <option key={name}>{name}</option>)}</select></label><div className="coach-question-options" role="group" aria-label="可选复盘问题">{["这份报告里，哪些观察有画面依据？", "哪些片段值得和教练一起复核？", "下次拍摄需要补充哪些画面？"].map(question => <button key={question} type="button" className="product-secondary" onClick={() => setAdviceNotes(question)}>{question}</button>)}</div><label>你的问题<span>{adviceNotes.length}/600</span><textarea value={adviceNotes} maxLength={600} onChange={event => setAdviceNotes(event.target.value)} placeholder="例如：我应该重点回看哪一段脚步？" /></label><button className="primary-button" disabled={adviceLoading || evidence.candidateSource !== "online"}>{adviceLoading ? "正在整理…" : "查看解释与建议"}<ProductIcon name="arrow-right" size={18} /></button>{adviceNotice && savedAdvice?.contextKey === currentAdviceKey && <p className="coach-status" role="status">{adviceNotice}</p>}</form>{evidence.candidateSource === "online" ? <AdviceResult technique={adviceTechnique} response={currentAdvice} /> : <div className="product-notice" role="note">这是离线导入的报告。可根据上方已有观察保存复盘问题；在线问答需要当前服务中的原任务，本页不会把导入的编号当作在线证据。</div>}</div></section>
      <details className="report-disclosure report-export-disclosure" id="export"><summary>保存完整报告<span>含逐项依据、回放时间与原始数据</span></summary><ReportExportActions input={exportInput} /></details></>}
      {hasReport && !delivery.showReport && journalPanel}
      {hasReport && <div className="report-support-tools"><RequestReadDetails diagnostics={readDiagnostics} /></div>}
    </div>}
  </ProductShell>;
}
