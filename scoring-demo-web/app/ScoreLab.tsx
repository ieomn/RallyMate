"use client";
import { measurementResultFromSummary } from "./lib/measurement-evidence";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ChangeEvent, CSSProperties } from "react";
import {
  buildScoreReport,
  scenarioFromStage1Summary,
  type CardResult,
  type Dependency,
  type Domain,
  type MetricCard,
  type Scenario,
} from "./scoring/engine";
import { SCENARIOS } from "./scoring/scenarios";
import { createApiClient, RallyMateApiError, type JobProgress } from "./lib/api-client";
import { watchAnalysis } from "./lib/analysis-session";
import LiveResults from "./LiveResults";
import { recentReports, type RecentReport } from "./lib/training-report";
import { poseReplayTimingAllowed } from "./lib/pose-playback";
import ReportExportActions from "./ReportExportActions";
import type { UploadProgress } from "./lib/resumable-upload";
import BallTrajectoryViewer from "./BallTrajectoryViewer";
import BallTrajectorySummary from "./BallTrajectorySummary";
import AdviceResult from "./AdviceResult";
import { adviceContextKey, adviceForContext, adviceNotice as describeAdvice, type AdviceResponse, type ScopedAdvice } from "./lib/advice-display";
import type {
  TechniqueAssessmentResponse,
  TechniqueCatalogResponse,
  DemoResultResponse,
  TrajectoryPreviewResponse,
} from "./lib/api-types";

const EVENT_NAMES: Record<string, string> = {
  GS01: "正手上旋",
  GS02: "双手反拍",
  GS03: "单手反拍",
  GS04: "反手切削",
  GS05: "正手切削",
  FS01: "分腿垫步",
  FS02: "第一步启动",
  FS03: "交叉步",
  FS04: "并步",
  FS05: "调整步",
  FS06: "开放式站位",
  FS07: "关闭式站位",
  FS08: "跨步",
  FS09: "制动",
  FS10: "回位",
};

const SUPPORTED_VIDEO_EXTENSION = /\.(mp4|mov|m4v|avi|mkv)$/i;

const DEPENDENCY_LABELS: Record<Dependency, string> = {
  pose: "人体姿态",
  ball: "球轨迹",
  racket: "球拍观测",
  court: "场地标定",
  tracking: "连续跟踪",
};

const STATUS_LABELS = {
  scored: "已评分",
  ready: "证据就绪",
  partial: "部分可用",
  blocked: "不可评价",
};

type Source = { domain: string; fileName: string; indicatorCount: number; stageCount: number };

type ImportState =
  | { status: "idle" }
  | { status: "reading"; fileName: string }
  | { status: "success"; fileName: string }
  | { status: "error"; message: string };

type LiveEvidence = {
  mode: "demo" | "live";
  jobId?: string;
  trajectory: TrajectoryPreviewResponse | null;
  assessment: TechniqueAssessmentResponse | null;
  result?: DemoResultResponse | null;
  error: string | null;
};

const EMPTY_EVIDENCE: LiveEvidence = {
  mode: "demo",
  trajectory: null,
  assessment: null,
  error: null,
};

const DOMAIN_TECHNIQUE: Record<Domain, string> = { GS: "底线击球", FS: "步伐" };

const PENDING_SCENARIO: Scenario = {
  id: "live-pending",
  label: "真实任务处理中",
  shortLabel: "处理中",
  mode: "real",
  description: "视频已提交，等待服务端 summary 与增强证据接口返回。",
  source: "RallyMate API · queued / processing",
  baseScore: null,
  eventConfidence: 0,
  dependencyCoverage: { pose: 0, ball: 0, racket: 0, court: 0, tracking: 0 },
  facts: [
    { label: "任务状态", value: "处理中" },
    { label: "技术分", value: "待确认" },
    { label: "证据", value: "等待 API" },
  ],
  caveat: "任务尚未返回可核验的 summary；当前不展示任何演示分数或技术等级。",
};

function pct(value: number) {
  return `${Math.round(value * 100)}%`;
}

function scoreText(value: number | null) {
  return value === null ? "—" : value.toFixed(1);
}

function formatBytes(bytes: number) {
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function jobPercent(job: JobProgress | null, fallback: number) {
  const value = typeof job?.progress === "object" ? job.progress.percent : job?.progress;
  return Math.max(0, Math.min(100, Number(value ?? fallback)));
}

function jobPhase(job: JobProgress | null) {
  return typeof job?.progress === "object" ? job.progress.phase || job.progress.message : job?.stage || job?.message;
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
    <div className="report-section-heading"><div><span className="card-kicker">SESSION REPLAY</span><h2 id="evidence-title">回到这一拍</h2></div><span>训练回放</span></div>
    <BallTrajectoryViewer key={selectedVideoSrc ?? "coordinates"} trajectory={trajectory} pending={pending} error={error}
      jobId={evidence.jobId} loadPoseWindow={evidence.jobId && !pending ? loadPoseWindow : undefined}
      poseTimingPreserved={poseReplayTimingAllowed(usingLocalVideo, evidence.result)}
      videoSrc={videoFailed !== selectedVideoSrc ? selectedVideoSrc : null}
      poster={evidence.result?.artifact_urls?.["preview.jpg"] ? `${mediaBase}/preview.jpg` : null}
      embeddedPoints={embeddedPoints.map((point, index) => ({ ...point, timestamp_ms: point.timestamp_ms ?? index }))}
      videoTimeOriginMs={usingLocalVideo ? 0 : (trajectory?.source.start_timestamp_ms ?? 0)}
      onVideoError={() => setVideoFailed(selectedVideoSrc ?? "unknown")} />
    <p className="replay-caption">通过下方时间线回看片段。人体骨架与球路随画面同步，可分别开启或隐藏。</p>
    <details className="report-replay-details"><summary>球路观测与回放说明</summary><BallTrajectorySummary trajectory={trajectory} pending={pending} error={error} />
      {evidence.error && <p className="upload-error" role="status">部分增强证据暂不可用：{evidence.error}</p>}
    </details>
  </section>;
}

function StatBar({ label, value, tone = "lime" }: { label: string; value: number; tone?: "lime" | "blue" | "orange" }) {
  return (
    <div className="stat-bar">
      <div className="stat-bar-head"><span>{label}</span><strong>{Math.round(value)}%</strong></div>
      <div className="stat-bar-track"><span className={`bar-${tone}`} style={{ width: `${Math.max(2, value)}%` }} /></div>
    </div>
  );
}

function EvidenceTag({ dependency, coverage, optional }: { dependency: Dependency; coverage: number; optional?: boolean }) {
  const state = coverage >= 0.72 ? "good" : coverage >= 0.4 ? "warn" : "bad";
  return (
    <span className={`dependency-tag ${state}`}>
      <i />{DEPENDENCY_LABELS[dependency]} {pct(coverage)}{optional ? " · 可选" : ""}
    </span>
  );
}

function ResultInspector({ result, scenario }: { result: CardResult; scenario: Scenario }) {
  const dependencies = [...result.mandatoryDependencies, ...result.optionalDependencies];
  return (
    <aside className="inspector" aria-label="评分证据详情">
      <div className="inspector-kicker">证据与规则</div>
      <div className="inspector-title-row">
        <div>
          <span className="mono-id">动作指标</span>
          <h3>{result.card.name}</h3>
        </div>
        <div className={`mini-grade grade-${result.grade ?? "na"}`}>{result.grade ?? "N/A"}</div>
      </div>
      <p className="definition">{result.card.definition || "指标卡未提供补充技术定义。"}</p>

      <div className="inspector-section">
        <div className="section-label"><span>证据门禁</span><strong>{Math.round(result.evidence * 100)}%</strong></div>
        <div className="tag-cloud">
          {dependencies.map((dep) => (
            <EvidenceTag
              key={dep}
              dependency={dep}
              coverage={scenario.dependencyCoverage[dep]}
              optional={result.optionalDependencies.includes(dep)}
            />
          ))}
        </div>
        {dependencies.length === 0 && <p className="muted-small">本项由目标事件专用特征提供证据。</p>}
      </div>

      {result.dimensions ? (
        <div className="inspector-section">
          <div className="section-label"><span>四维得分</span><em>40 / 25 / 20 / 15</em></div>
          <StatBar label="技术完成度" value={result.dimensions.technique} />
          <StatBar label="时机与节奏" value={result.dimensions.timing} tone="blue" />
          <StatBar label="稳定与平衡" value={result.dimensions.stability} tone="orange" />
          <StatBar label="连续与衔接" value={result.dimensions.continuity} />
        </div>
      ) : (
        <div className="evidence-only-box">
          <strong>技术分已锁定</strong>
          <p>真实一期数据缺少事件切分和标定后的特征值；系统只报告证据就绪度。</p>
        </div>
      )}

      <div className="inspector-section">
        <div className="section-label"><span>{result.grade ? `${result.grade} 级原文` : "系统判定"}</span></div>
        <p className="verdict">{result.verdict}</p>
      </div>

      <div className="feedback-box">
        <span>AI 教练反馈</span>
        <p>{result.feedback}</p>
      </div>

      <details className="rule-details">
        <summary>展开计算定义与原始字段</summary>
        <dl>
          <div><dt>计算方式</dt><dd>{result.card.calculation || "—"}</dd></div>
          <div><dt>所需点</dt><dd>{result.card.requiredPoints || "—"}</dd></div>
          <div><dt>当前状态</dt><dd>{result.card.sourceStatus || "—"}</dd></div>
          <div><dt>阶段边界</dt><dd>{result.card.startAction || "待补充"} → {result.card.endAction || "待补充"}</dd></div>
        </dl>
      </details>
    </aside>
  );
}

export default function ScoreLab({ cards, registryVersion, sources }: { cards: MetricCard[]; registryVersion: string; sources: Source[] }) {
  const [history, setHistory] = useState<RecentReport[]>([]);
  const [uploadExpanded, setUploadExpanded] = useState(false);
  const [scenarioId, setScenarioId] = useState(SCENARIOS[0].id);
  const [importedScenario, setImportedScenario] = useState<Scenario | null>(null);
  const [importedSummary, setImportedSummary] = useState<Record<string, unknown> | null>(null);
  const [scoreContext, setScoreContext] = useState<"demo" | "live-pending" | "live">("demo");
  const [domain, setDomain] = useState<Domain>("GS");
  const [eventCode, setEventCode] = useState("GS01");
  const [stageCode, setStageCode] = useState<string | null>("GS01-M01");
  const [selectedId, setSelectedId] = useState("GS01-M01-01");
  const [query, setQuery] = useState("");
  const [visible, setVisible] = useState(8);
  const [importState, setImportState] = useState<ImportState>({ status: "idle" });
  const fileInput = useRef<HTMLInputElement>(null);
  const videoInput = useRef<HTMLInputElement>(null);
  const [videoFile, updateVideoFile] = useState<File | null>(null);
  const [localVideoSrc, setLocalVideoSrc] = useState<string | null>(null);
  const localVideoRef = useRef<string | null>(null);
  const [localVideoJobId, setLocalVideoJobId] = useState<string | null>(null);
  const [uploadProgress, setUploadProgress] = useState<UploadProgress | null>(null);
  const [uploadState, setUploadState] = useState<"idle" | "ready" | "uploading" | "processing" | "complete" | "error">("idle");
  const [job, setJob] = useState<JobProgress | null>(null);
  const [uploadError, setUploadError] = useState("");
  const [evidence, setEvidence] = useState<LiveEvidence>(EMPTY_EVIDENCE);
  const [techniqueCatalog, setTechniqueCatalog] = useState<TechniqueCatalogResponse | null>(null);
  const [catalogError, setCatalogError] = useState("");
  const apiClient = useMemo(() => createApiClient(), []);
  const [savedAdvice, setSavedAdvice] = useState<ScopedAdvice | null>(null);
  const [adviceLoading, setAdviceLoading] = useState(false);
  const [adviceNotice, setAdviceNotice] = useState("");
  const [adviceNotes, setAdviceNotes] = useState("");
  const [coachTechnique, setCoachTechnique] = useState("");
  const adviceTechnique = coachTechnique || (evidence.assessment?.family_summary.footwork?.observed_count ? "步伐" : DOMAIN_TECHNIQUE[domain]);
  const currentAdviceKey = adviceContextKey(evidence.jobId, adviceTechnique);
  const currentAdvice = adviceForContext(savedAdvice, currentAdviceKey);
  const analysisRunRef = useRef(0);
  const analysisAbort = useRef<AbortController | null>(null);

  function setVideoFile(file: File | null) {
    if (localVideoRef.current) URL.revokeObjectURL(localVideoRef.current);
    localVideoRef.current = file ? URL.createObjectURL(file) : null;
    setLocalVideoSrc(localVideoRef.current);
    setLocalVideoJobId(null);
    updateVideoFile(file);
  }
  useEffect(() => () => { if (localVideoRef.current) URL.revokeObjectURL(localVideoRef.current); }, []);

  function invalidateAnalysisRun() {
    analysisRunRef.current += 1;
    analysisAbort.current?.abort();
    setImportedSummary(null);
    try { localStorage.removeItem("rallymate.activeJob"); } catch { /* Storage is optional. */ }
    const url = new URL(window.location.href); url.searchParams.delete("job"); window.history.replaceState(window.history.state, "", url);
  }

  useEffect(() => {
    let active = true;
    apiClient.getTechniques()
      .then((catalog) => {
        if (!active) return;
        setTechniqueCatalog(catalog);
        setCatalogError("");
      })
      .catch((error) => {
        if (!active) return;
        setCatalogError(error instanceof Error ? error.message : "技术目录暂不可用，使用内置兼容目录。");
      });
    return () => { active = false; };
  }, [apiClient]);

  const scenarios = importedScenario ? [...SCENARIOS, importedScenario] : SCENARIOS;
  const selectedScenario = scenarios.find((item) => item.id === scenarioId) ?? SCENARIOS[0];
  // A live request must never render the synthetic 298-card scores while its
  // summary is still pending.  If a state transition ever leaves a demo
  // scenario selected, fail closed to the zero-evidence pending view.
  const scenario = scoreContext === "demo"
    ? selectedScenario
    : selectedScenario.mode === "real"
      ? selectedScenario
      : PENDING_SCENARIO;
  const showLegacy = scoreContext === "demo";
  const report = buildScoreReport(cards, scenario);
  const domainResult = report.domains[domain];
  const eventResults = report.results.filter((item) => item.card.eventCode === eventCode);
  const stageCodes = [...new Set(eventResults.map((item) => item.card.stageCode))];
  const filteredResults = eventResults.filter((item) => {
    const stageMatch = domain === "FS" || !stageCode || item.card.stageCode === stageCode;
    const searchMatch = `${item.card.id} ${item.card.name} ${item.card.definition}`.toLowerCase().includes(query.toLowerCase());
    return stageMatch && searchMatch;
  });
  const displayedResults = filteredResults.slice(0, visible);
  const selectedResult = report.results.find((item) => item.card.id === selectedId) ?? displayedResults[0] ?? eventResults[0];
  const scoredCount = report.results.filter((item) => item.status === "scored").length;
  const readyCount = report.results.filter((item) => item.status === "ready").length;
  const partialCount = report.results.filter((item) => item.status === "partial").length;
  const blockedCount = report.results.filter((item) => item.status === "blocked").length;
  const currentStageName = eventResults.find((item) => item.card.stageCode === stageCode)?.card.stageName;

  function chooseVideo(event: ChangeEvent<HTMLInputElement>) {
    acceptVideoFile(event.target.files?.[0]);
    event.target.value = "";
  }

  function acceptVideoFile(file: File | undefined) {
    if (!file) return;
    invalidateAnalysisRun();
    setUploadError("");
    if (!isSupportedVideo(file)) {
      setVideoFile(null); setJob(null); setEvidence(EMPTY_EVIDENCE); setScoreContext("demo");
      setImportedScenario(null); setScenarioId(SCENARIOS[0].id);
      setImportState({ status: "idle" }); setUploadState("error");
      setUploadError("请选择 MP4、MOV、M4V、AVI 或 MKV 视频文件。"); return;
    }
    setUploadExpanded(true);
    setVideoFile(file); setJob(null); setUploadState("ready"); setEvidence(EMPTY_EVIDENCE); setScoreContext("demo"); setImportedScenario(null); setScenarioId(SCENARIOS[0].id); setImportState({ status: "idle" });
  }

  const monitorJob = useCallback(async (id: string) => {
    analysisAbort.current?.abort();
    const controller = new AbortController();
    analysisAbort.current = controller;
    setImportedSummary(null);
    setUploadState("processing"); setUploadError(""); setScoreContext("live-pending");
    setJob({ id, status: "queued" });
    const url = new URL(window.location.href); url.searchParams.set("job", id); window.history.replaceState(window.history.state, "", url);
    try { localStorage.setItem("rallymate.activeJob", id); } catch { /* Optional storage. */ }
    setEvidence({ mode: "live", jobId: id, trajectory: null, assessment: null, error: null });
    try {
      const completed = await watchAnalysis(apiClient, id, controller.signal, setJob, 2000, (preview) => {
        if (!controller.signal.aborted) setEvidence((previous) => previous.jobId === id ? { ...previous, trajectory: preview } : previous);
      });
      if (controller.signal.aborted) return;
      setEvidence({ mode: "live", jobId: id, trajectory: completed.trajectory, assessment: completed.assessment ?? completed.result.technique_assessment ?? null, result: completed.result, error: completed.warning });
      if (isStage1Summary(completed.job.summary)) {
        const next = scenarioFromStage1Summary(completed.job.summary);
        setImportedScenario(next); setScenarioId(next.id);
      }
      setScoreContext("live"); setUploadState("complete");
      setUploadExpanded(false);
      try {
        const existing = recentReports(JSON.parse(localStorage.getItem("rallymate.recentReports") || "[]"));
        const next = recentReports([{ id, name: existing.find(item => item.id === id)?.name || "训练视频", viewedAt: new Date().toISOString() }, ...existing.filter(item => item.id !== id)]);
        localStorage.setItem("rallymate.recentReports", JSON.stringify(next)); setHistory(next);
      } catch { /* History is optional in private browsing. */ }
    } catch (error) {
      if (controller.signal.aborted) return;
      setUploadState("error");
      setUploadError(error instanceof Error ? error.message : "连接中断，稍后可继续读取结果，无需重新上传。");
    }
  }, [apiClient]);

  useEffect(() => {
    try {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setHistory(recentReports(JSON.parse(localStorage.getItem("rallymate.recentReports") || "[]")));
      const id = new URLSearchParams(window.location.search).get("job") || localStorage.getItem("rallymate.activeJob");
      if (id) localStorage.setItem("rallymate.activeJob", id);
      // Restore the external persisted task once after hydration.
      if (id && /^[a-zA-Z0-9_-]{8,80}$/.test(id)) void monitorJob(id);
    } catch { /* Private browsing may disable storage. */ }
    return () => { analysisAbort.current?.abort(); };
  }, [monitorJob]);

  async function submitVideo() {
    if (!videoFile) return;
    invalidateAnalysisRun();
    const runId = analysisRunRef.current;
    const controller = new AbortController(); analysisAbort.current = controller;
    setUploadState("uploading"); setUploadProgress(null); setUploadError(""); setJob(null);
    setImportedScenario(null); setScenarioId(SCENARIOS[0].id); setScoreContext("live-pending");
    setEvidence({ mode: "live", trajectory: null, assessment: null, error: null });
    try {
      const submitted = await apiClient.submitVideo(videoFile, { courtMode: "auto", writeAnnotatedVideo: true, signal: controller.signal, onUploadProgress: (progress) => { if (!controller.signal.aborted) setUploadProgress(progress); } });
      if (analysisRunRef.current !== runId) return;
      if (typeof submitted.id !== "string" || !/^[a-zA-Z0-9_-]{8,80}$/.test(submitted.id)) throw new Error("服务没有返回有效任务，请检查上传接口配置。");
      setJob(submitted);
      setLocalVideoJobId(submitted.id);
      try {
        const existing = recentReports(JSON.parse(localStorage.getItem("rallymate.recentReports") || "[]"));
        localStorage.setItem("rallymate.recentReports", JSON.stringify(recentReports([{ id: submitted.id, name: videoFile.name, viewedAt: new Date().toISOString() }, ...existing.filter(item => item.id !== submitted.id)])));
      } catch { /* Optional local history. */ }
      try { localStorage.setItem("rallymate.activeJob", submitted.id); } catch { /* Storage is optional. */ }
      await monitorJob(submitted.id);
    } catch (error) {
      if (analysisRunRef.current !== runId || controller.signal.aborted) return;
      setUploadState("error");
      setUploadError(error instanceof RallyMateApiError ? `${error.message}（${error.status}）` : error instanceof Error ? error.message : "上传失败，请确认分析服务已启动。");
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

  function switchDomain(next: Domain) {
    const firstEvent = next === "GS" ? "GS01" : "FS01";
    setDomain(next);
    setEventCode(firstEvent);
    setStageCode(next === "GS" ? "GS01-M01" : null);
    const first = report.results.find((item) => item.card.eventCode === firstEvent);
    if (first) setSelectedId(first.card.id);
    setVisible(8);
    setQuery("");
  }

  function chooseEvent(code: string) {
    setEventCode(code);
    const first = report.results.find((item) => item.card.eventCode === code);
    setStageCode(domain === "GS" ? first?.card.stageCode ?? null : null);
    if (first) setSelectedId(first.card.id);
    setVisible(8);
  }

  function chooseScenario(nextId: string) {
    const next = scenarios.find((item) => item.id === nextId);
    setScenarioId(nextId);
    const retainsEvidence =
      next?.mode === "real" &&
      next.id === importedScenario?.id &&
      evidence.jobId === next.id;
    if (!retainsEvidence) {
      invalidateAnalysisRun();
      setJob(null);
      setUploadState("idle");
      setUploadError("");
      setEvidence(next?.mode === "real"
        ? {
            mode: "live",
            trajectory: null,
            assessment: null,
            error: "当前场景只有静态 summary 审计数据；上传视频后可获取轨迹与球拍观测。",
          }
        : EMPTY_EVIDENCE);
    }
    setScoreContext(next?.mode === "real" ? "live" : "demo");
  }

  async function importSummary(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    invalidateAnalysisRun();
    setVideoFile(null);
    setImportState({ status: "reading", fileName: file.name });
    setUploadError("");
    try {
      if (file.size > 5 * 1024 * 1024) throw new Error("报告超过 5 MB，请选择任务摘要或本站导出的报告。");
      const data = JSON.parse(await file.text());
      if (data?.schemaVersion === "rallymate-practice-report/1" && typeof data.jobId === "string" && /^[a-zA-Z0-9_-]{8,80}$/.test(data.jobId)) {
        // Prefer the self-contained result embedded by our exporter. This
        // keeps downloaded reports reviewable when the API is offline; only
        // fall back to polling the job when the export contains no result.
        const embeddedResult = data.result && typeof data.result === "object" ? data.result as DemoResultResponse : null;
        const embeddedAssessment = extractTechniqueAssessment(data) ?? embeddedResult?.technique_assessment ?? null;
        if (embeddedResult || embeddedAssessment) {
          const summary = data.summary && typeof data.summary === "object" ? data.summary as Record<string, unknown> : null;
          if (summary && isStage1Summary(summary)) {
            const nextScenario = scenarioFromStage1Summary(summary);
            setImportedSummary(summary); setImportedScenario(nextScenario); setScenarioId(nextScenario.id);
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
          const nextScenario = scenarioFromStage1Summary(summary);
          setImportedSummary(summary); setImportedScenario(nextScenario); setScenarioId(nextScenario.id);
        } else {
          setImportedSummary(null); setImportedScenario(null); setScenarioId(SCENARIOS[0].id);
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
      const nextScenario = scenarioFromStage1Summary(summary);
      setImportedScenario(nextScenario);
      setScenarioId(nextScenario.id);
      setJob(importedResult.job_id ? { id: importedResult.job_id, status: "completed", summary } : null);
      setUploadState("complete");
      setVideoFile(null);
      setEvidence({ mode: "live", jobId: importedResult.job_id, result: importedResult, trajectory: null, assessment: null, error: null });
      setScoreContext("live");
      setImportState({ status: "success", fileName: file.name });
    } catch (error) {
      setJob(null);
      setVideoFile(null);
      setUploadState("idle");
      setEvidence(EMPTY_EVIDENCE);
      setScoreContext("demo");
      setImportedScenario(null);
      setScenarioId(SCENARIOS[0].id);
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

  const exportInput = { mode: scoreContext, uploadState, job, evidence, summary: importedSummary ?? (job?.summary as Record<string, unknown> | undefined), videoName: videoFile?.name, demoReport: report };

  return (
    <main className={`app-shell practice-app ${!showLegacy ? "has-report" : ""}`} aria-busy={importState.status === "reading"}>
      <header className="topbar">
        <a className="brand" href="#top" aria-label="RallyMate 训练报告首页"><span className="brand-mark">RM</span><span><strong>RallyMate</strong><small>YOUR PRACTICE, IN FOCUS</small></span></a>
        <nav className="topnav" aria-label="页面导航"><a href="#scoreboard">训练报告</a><a href="#evidence">视频回放</a><a href="#rules">动作细节</a></nav>
        <div className="report-top-actions"><details className="report-history"><summary>最近报告 <span>⌄</span></summary><div className="report-history-menu"><strong>本机最近查看</strong>{history.length ? history.map(item => <button type="button" key={item.id} onClick={() => { setVideoFile(null); setUploadExpanded(false); void monitorJob(item.id); }}><span>{item.name}</span><small>{new Date(item.viewedAt).toLocaleDateString("zh-CN", { month: "short", day: "numeric" })}</small></button>) : <p>完成一次分析后，报告会保存在这里。也可导入已有报告。</p>}</div></details><button className="primary-button" onClick={() => { setUploadExpanded(true); videoInput.current?.click(); }}>＋ 新视频</button></div>
      </header>
      <div className="practice-content">
      <section className="report-title" id="top"><div><span className="card-kicker">RALLYMATE / TRAINING JOURNAL</span><h1>{showLegacy ? "每次练习，都看见一点进步。" : "你的训练报告"}</h1><p>{showLegacy ? "从一段视频开始，回看步伐、转体与下一次练习的重点。" : videoFile?.name || history.find(item => item.id === evidence.jobId)?.name || "回看动作，找到下一次练习的重点。"}</p></div><button className="report-import" onClick={() => fileInput.current?.click()} disabled={importState.status === "reading"}>{importState.status === "reading" ? "正在读取…" : "导入报告 ↗"}</button><input ref={fileInput} type="file" accept="application/json,.json" hidden onChange={importSummary} /></section>
      {importState.status !== "idle" && <div id="import-status" className={`import-status import-${importState.status}`} role="status" aria-live="polite">{importState.status === "reading" ? `正在解析 ${importState.fileName}…` : importState.status === "success" ? `已载入 ${importState.fileName}。` : importState.message}</div>}
      <details className="report-upload" id="upload" open={uploadExpanded || showLegacy || uploadState === "processing" || uploadState === "uploading" || uploadState === "error"} onToggle={event => setUploadExpanded(event.currentTarget.open)}><summary>分析新视频 <span>{uploadState === "complete" ? "当前报告已就绪" : "选择文件，开始训练复盘"}</span></summary>
      <section className="upload-section" aria-labelledby="upload-title">
        <div className="upload-copy"><span className="card-kicker">START A NEW SESSION</span><h2 id="upload-title">保留完整动作，<br />看清每个阶段。</h2><p>固定相机，让全身与双脚留在画面内。保留准备、挥拍和恢复过程，更方便复核步伐与转体。</p><div className="capture-tips"><span>01 全身入镜</span><span>02 相机稳定</span><span>03 动作完整</span></div></div>
        <div className="upload-card">
          <input ref={videoInput} type="file" accept="video/mp4,video/quicktime,video/x-msvideo,video/x-matroska,.mp4,.mov,.m4v,.avi,.mkv" hidden onChange={chooseVideo} />
          <button className={`dropzone ${uploadState === "error" ? "has-error" : ""}`} onClick={() => videoInput.current?.click()} onDragOver={(event) => event.preventDefault()} onDrop={(event) => { event.preventDefault(); acceptVideoFile(event.dataTransfer.files?.[0]); }} aria-label="选择或拖入视频文件">
            <span className="upload-icon">↑</span><strong>{videoFile ? videoFile.name : "选择或拖入视频文件"}</strong><small>{videoFile ? formatBytes(videoFile.size) : "MP4 / MOV / M4V / AVI / MKV · 建议 200 MB 以内"}</small>
          </button>
          {videoFile && <div className="upload-file-row"><span><b>已选择</b> {formatBytes(videoFile.size)}</span><button onClick={() => { invalidateAnalysisRun(); setVideoFile(null); setJob(null); setUploadState("idle"); setUploadError(""); setImportState({ status: "idle" }); setEvidence(EMPTY_EVIDENCE); setScoreContext("demo"); setImportedScenario(null); setScenarioId(SCENARIOS[0].id); }}>移除</button></div>}
          {uploadState !== "idle" && uploadState !== "ready" && <div className="progress-block" aria-live="polite">
            <div className="progress-head"><span>{uploadState === "uploading" ? uploadProgress?.phase === "merging" ? "上传完成 · 校验合并中" : uploadProgress?.resumed ? "正在续传" : "正在上传" : uploadState === "processing" ? (job?.status === "queued" ? "排队中 · 视频已接收" : jobPhase(job) || "正在分析视频") : uploadState === "complete" ? "分析完成" : "连接或处理异常"}</span>
              <strong>{uploadState === "uploading" ? `${Math.floor(uploadProgress?.percent ?? 0)}%` : uploadState === "complete" ? "100%" : `${Math.round(jobPercent(job, 0))}%`}</strong></div>
            <div className="progress-track"><span style={{ width: `${uploadState === "uploading" ? uploadProgress?.percent ?? 0 : jobPercent(job, uploadState === "complete" ? 100 : 0)}%` }} /></div>
            {uploadProgress && (uploadState === "uploading" || (uploadState === "error" && !evidence.jobId)) && <p className="upload-privacy">已确认 {(uploadProgress.uploadedBytes / 1048576).toFixed(1)} / {(uploadProgress.totalBytes / 1048576).toFixed(1)} MB{uploadProgress.bytesPerSecond > 0 ? ` · ${(uploadProgress.bytesPerSecond / 1048576).toFixed(2)} MB/s` : ""} · 断线可续传</p>}
          </div>}
          {uploadError && <p className="upload-error" role="alert">{uploadError}</p>}
          <button className="primary-button upload-submit" disabled={(!videoFile && !evidence.jobId) || uploadState === "uploading" || uploadState === "processing"} onClick={() => { if (uploadState === "error" && evidence.jobId && !["failed", "cancelled"].includes(job?.status ?? "")) void monitorJob(evidence.jobId); else if (!videoFile) videoInput.current?.click(); else void submitVideo(); }}>{uploadState === "processing" ? "处理中…" : uploadState === "complete" ? (videoFile ? "再次分析" : "分析新视频") : uploadState === "error" && evidence.jobId && !["failed", "cancelled"].includes(job?.status ?? "") ? "继续读取结果" : uploadState === "error" && videoFile ? "继续上传" : "开始分析"}<span>→</span></button>
          {evidence.jobId && <p className="task-link"><a href={`?job=${encodeURIComponent(evidence.jobId)}#scoreboard`}>重新打开本次任务 ↗</a><span>刷新后自动继续读取</span></p>}
          <p className="upload-footnote">视频交由当前分析服务处理；断线后可继续读取任务。</p>
        </div>
      </section>

      </details>
      {!showLegacy && <LiveResults key={evidence.jobId ?? "pending"} status={job?.status} error={uploadError || evidence.error} result={evidence.result ?? null} assessment={evidence.assessment} catalog={techniqueCatalog} pending={uploadState === "processing" || uploadState === "uploading"} trajectory={evidence.trajectory} summary={importedSummary ?? (job?.summary && typeof job.summary === "object" ? job.summary as Record<string, unknown> : null)} replay={<ReplayPanel key={evidence.jobId ?? evidence.mode} evidence={evidence} pending={uploadState === "processing" || uploadState === "uploading"} error={uploadError || evidence.error} localVideoSrc={localVideoJobId === evidence.jobId ? localVideoSrc : null} />} />}

      {showLegacy && <details className="report-disclosure offline-demo"><summary>浏览离线界面示例 <span>模拟数据，不代表你的训练结果</span></summary>

      <section className="scenario-strip" id="scoreboard">
        <div>
          <span className="strip-label">{scoreContext === "demo" ? "动作分析场景" : "当前数据上下文"}</span>
          <select value={scenarioId} onChange={(event) => chooseScenario(event.target.value)}>
            {scenarios.map((item) => <option key={item.id} value={item.id}>{item.label.replace("完整验收演示", "离线演示")}</option>)}
          </select>
        </div>
        <p>{scenario.description}</p>
        <span className={`mode-chip mode-${scenario.mode}`}>{scenario.mode === "demo" ? "离线演示模式" : "真实数据审慎模式"}</span>
      </section>

      <section className={`data-context-banner context-${scoreContext}`} aria-live="polite">
        <span className="context-dot" />
        {scoreContext === "demo" && <p><strong>离线演示层：</strong>下方示例分数只用于验证界面与聚合逻辑，不代表上传视频结果。</p>}
        {catalogError && <small>技术目录接口暂不可用，当前保留内置目录展示：{catalogError}</small>}
      </section>

      <section className="score-overview">
        <div className={`score-card score-${report.overallGrade ?? "na"}`}>
          <div className="score-ring" style={{ "--score": `${report.overallScore ?? report.overallEvidence}` } as CSSProperties}>
            <div><span>{report.overallGrade ?? "证据"}</span><strong>{scoreText(report.overallScore ?? report.overallEvidence)}</strong><small>{report.overallScore === null ? "就绪度 / 100" : "综合分 / 100"}</small></div>
          </div>
          <div className="score-summary">
            <span className="card-kicker">OVERALL RESULT</span>
            <h2>{report.overallScore === null ? "当前只做证据审计" : `${report.overallGrade} 级 · ${report.overallGrade === "A" ? "表现优秀" : report.overallGrade === "B" ? "动作稳健" : "仍有明确提升空间"}`}</h2>
            <p>{scenario.caveat}</p>
            <div className="fact-row">
              {scenario.facts.map((fact) => <div key={fact.label}><small>{fact.label}</small><strong>{fact.value}</strong></div>)}
            </div>
          </div>
        </div>

        <div className="module-cards">
          {(["GS", "FS"] as Domain[]).map((item) => {
            const moduleResult = report.domains[item];
            return (
              <button key={item} className="module-card" onClick={() => switchDomain(item)}>
                <div className="module-head"><span>{item === "GS" ? "底线击球" : "步伐"}</span><i>动作参考</i></div>
                <strong>{scoreText(moduleResult.score ?? moduleResult.evidence)}</strong>
                <p>{item === "GS" ? "底线击球" : "步伐"}</p>
                <div className="micro-track"><span style={{ width: `${moduleResult.score ?? moduleResult.evidence}%` }} /></div>
                <small>{scenario.mode === "demo" ? `${moduleResult.scored}/${moduleResult.total} 已评分` : `${moduleResult.ready} 就绪 · ${moduleResult.partial} 部分 · ${moduleResult.blocked} 阻断`}</small>
              </button>
            );
          })}
          <div className="coverage-card">
            <span className="card-kicker">EVIDENCE COVERAGE</span>
            <strong>{report.overallEvidence.toFixed(1)}%</strong>
            <p>分数可信度单独计算，不用缺失数据“凑分”。</p>
            <div className="status-dots"><span className="dot-ready">{scoredCount || readyCount} {scoredCount ? "已评分" : "就绪"}</span><span className="dot-partial">{partialCount} 部分</span><span className="dot-blocked">{blockedCount} 阻断</span></div>
          </div>
        </div>
      </section>

      <section className="workbench" id="rules">
        <div className="workbench-head">
          <div>
            <span className="section-number">02</span>
            <div><span className="card-kicker">RULE EXPLORER</span><h2>逐项评分与证据回放</h2></div>
          </div>
          <div className="domain-switch" role="tablist" aria-label="评分域">
            <button role="tab" aria-selected={domain === "GS"} tabIndex={domain === "GS" ? 0 : -1} className={domain === "GS" ? "active" : ""} onClick={() => switchDomain("GS")}>底线击球</button>
            <button role="tab" aria-selected={domain === "FS"} tabIndex={domain === "FS" ? 0 : -1} className={domain === "FS" ? "active" : ""} onClick={() => switchDomain("FS")}>步伐</button>
          </div>
        </div>

        <div className="event-rail">
          {domainResult.groups.map((group) => (
            <button key={group.code} aria-pressed={eventCode === group.code} className={eventCode === group.code ? "active" : ""} onClick={() => chooseEvent(group.code)}>
              <span>动作 {domainResult.groups.indexOf(group) + 1}</span><strong>{EVENT_NAMES[group.code] ?? group.name}</strong><small>{scoreText(group.score ?? group.evidence)}</small>
            </button>
          ))}
        </div>

        {domain === "GS" && (
          <div className="stage-rail">
            <span>动作阶段</span>
            {stageCodes.map((code) => {
              const name = eventResults.find((item) => item.card.stageCode === code)?.card.stageName ?? code;
              return <button key={code} aria-pressed={stageCode === code} className={stageCode === code ? "active" : ""} onClick={() => { setStageCode(code); const first = eventResults.find((item) => item.card.stageCode === code); if (first) setSelectedId(first.card.id); setVisible(8); }}>阶段 {stageCodes.indexOf(code) + 1} · {name}</button>;
            })}
          </div>
        )}

        <div className="rules-layout">
          <div className="rules-list">
            <div className="rules-list-head">
              <div><span className="mono-id">当前动作 · {EVENT_NAMES[eventCode] ?? "动作"}</span><h3>{currentStageName || EVENT_NAMES[eventCode]}</h3></div>
              <label className="search-box"><span>⌕</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索指标" /></label>
            </div>
            <div className="table-head"><span>指标</span><span>状态</span><span>{scenario.mode === "demo" ? "分数" : "证据"}</span></div>
            <div className="result-rows">
              {displayedResults.map((result) => (
                <button key={result.card.id} className={`result-row ${selectedResult?.card.id === result.card.id ? "active" : ""}`} onClick={() => setSelectedId(result.card.id)}>
                  <span className="result-name"><i className={`status-mark status-${result.status}`} /> <span><small>动作指标</small><strong>{result.card.name}</strong></span></span>
                  <span className={`status-pill status-${result.status}`}>{STATUS_LABELS[result.status]}</span>
                  <span className="result-score">{result.score === null ? `${Math.round(result.evidence * 100)}%` : result.score.toFixed(1)}<b>{result.grade ?? ""}</b></span>
                </button>
              ))}
              {displayedResults.length === 0 && <div className="empty-state" role="status"><strong>没有匹配的指标</strong><span>尝试清除搜索词，或切换其他动作阶段。</span></div>}
            </div>
            {visible < filteredResults.length && <button className="load-more" onClick={() => setVisible((value) => value + 8)}>再显示 {Math.min(8, filteredResults.length - visible)} 项</button>}
          </div>
          {selectedResult && <ResultInspector result={selectedResult} scenario={scenario} />}
        </div>
      </section>

      </details>}

      {!showLegacy && <ReportExportActions input={exportInput} />}

      <details className="report-disclosure report-coach" id="coach"><summary>对这次训练继续提问 <span>按需查看解释与建议</span></summary><section className="coach-section">
        <div className="logic-heading"><span className="section-number">03</span><div><span className="card-kicker">COACH NOTE</span><h2>基于这次练习，问一个下一步。</h2><p>只解释所选动作的已核验证据。未有效识别时，会说明不能判断的部分，并给出补录或复核建议。</p></div></div>
        <div className="coach-layout">
          <form className="coach-form" onSubmit={event => { event.preventDefault(); void askCoach(); }}>
            <label>练习主题<select aria-label="AI 练习主题" value={adviceTechnique} onChange={event => setCoachTechnique(event.target.value)}>{["底线击球", "发球", "接发", "网前进攻", "步伐"].map(name => <option key={name}>{name}</option>)}</select></label>
            <label>你的问题 <span>{adviceNotes.length}/600</span><textarea value={adviceNotes} maxLength={600} onChange={event => setAdviceNotes(event.target.value)} placeholder="例如：这次接发是否有足够的识别依据？需要补拍什么？" /></label>
            <button className="primary-button" disabled={adviceLoading}>{adviceLoading ? "整理中…" : "查看解释与建议"}<span>→</span></button>
            {adviceNotice && savedAdvice?.contextKey === currentAdviceKey && <p className="coach-status" role="status">{adviceNotice}</p>}
          </form>
          <AdviceResult technique={adviceTechnique} response={currentAdvice} />
        </div>
      </section>

      </details>
      <details className="report-disclosure report-system" id="system"><summary>测量与评分说明 <span>范围、版本与限制</span></summary><div className="report-system-copy"><h3>测量证据参考分</h3><p>参考分依据可测片段、特征覆盖、置信度、重复性与评分证据合成，只反映测量证据质量。技术评分与正式等级仍需教练标定，高参考分不代表动作正确。</p><h3>步伐与转体</h3><p>动作类型和阶段为规则推断。步伐候选不是实际步数，挥拍区间不是已确认触球；二维肩髋线是画面投影，不能当作真实三维转体角度。每项测量独立判断，缺失值不补分。</p><p>指标目录：{registryVersion} · 技术目录：{techniqueCatalog?.registry_version ?? "暂未连接"} · {sources.reduce((count, item) => count + item.indicatorCount, 0)} 项指标定义</p>{catalogError && <p>目录读取说明：{catalogError}</p>}</div></details>
      </div>

      <footer>
        <div className="brand"><span className="brand-mark">RM</span><span><strong>RallyMate</strong><small>MOTION ANALYSIS</small></span></div>
        <p>回看每一次练习，积累属于你的训练记录。</p>
        <span>RallyMate · 训练报告 Beta</span>
      </footer>
    </main>
  );
}
