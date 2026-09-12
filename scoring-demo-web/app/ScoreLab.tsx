"use client";

import Image from "next/image";
import { useEffect, useMemo, useRef, useState } from "react";
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
import type {
  TechniqueAssessmentResponse,
  TechniqueCatalogResponse,
  TrajectoryPoint,
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
  error: string | null;
};

const EMPTY_EVIDENCE: LiveEvidence = {
  mode: "demo",
  trajectory: null,
  assessment: null,
  error: null,
};

const DEFAULT_TECHNIQUE_FAMILIES = [
  ["baseline", "底线", 5],
  ["serve", "发球", 1],
  ["return", "接发", 5],
  ["net_attack", "网前进攻", 3],
  ["footwork", "步伐", 10],
] as const;

const TECHNIQUE_FAMILY_LABELS: Record<string, string> = {
  baseline: "底线",
  serve: "发球",
  return: "接发",
  net_attack: "网前进攻",
  footwork: "步伐",
};

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

function directionLabel(degrees: number | undefined) {
  if (degrees === undefined || !Number.isFinite(degrees)) return "待观测";
  const normalized = ((degrees % 360) + 360) % 360;
  if (normalized >= 337.5 || normalized < 22.5) return "右方";
  if (normalized < 67.5) return "右下方";
  if (normalized < 112.5) return "下方";
  if (normalized < 157.5) return "左下方";
  if (normalized < 202.5) return "左方";
  if (normalized < 247.5) return "左上方";
  if (normalized < 292.5) return "上方";
  return "右上方";
}

function chartPoints(points: TrajectoryPoint[]) {
  return points
    .map((point) => {
      const x = 26 + Math.max(0, Math.min(1, point.x)) * 460;
      const y = 22 + Math.max(0, Math.min(1, point.y)) * 144;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
}

function confidenceLabel(value: number | null | undefined) {
  return value === null || value === undefined ? "待观测" : value.toFixed(2);
}

function coveragePercent(value: number | null | undefined) {
  return value === null || value === undefined || !Number.isFinite(value)
    ? "待确认"
    : `${Math.round(Math.max(0, Math.min(1, value)) * 100)}%`;
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

function DemoEvidencePanel({
  evidence,
  catalog,
}: {
  evidence: LiveEvidence;
  catalog: TechniqueCatalogResponse | null;
}) {
  const trajectory = evidence.trajectory;
  const assessment = evidence.assessment;
  const observed = trajectory?.ball.observed ?? [];
  const predicted = trajectory?.ball.predicted ?? [];
  const predictedLine = observed.length > 0 ? [observed[observed.length - 1], ...predicted] : predicted;
  // The mode is explicit rather than inferred from nullable payloads.  A
  // failed enhancement request must never make a completed real job fall back
  // to synthetic demo marks or numbers.
  const isLive = evidence.mode === "live";
  const observedTechniques = assessment?.techniques.filter((item) => item.observed) ?? [];
  const racket = trajectory?.racket;
  const racketConfidence = racket?.confidence.mean;
  // `coverage_detail.*.fraction` is the canonical 0–1 display input.  The
  // compact `coverage` map is intentionally a 0–100 compatibility view, so
  // only use it as a fallback after converting its explicit percentage unit.
  const courtCoverage = assessment?.coverage_detail?.court?.fraction
    ?? (assessment?.coverage.court === undefined ? undefined : assessment.coverage.court / 100);
  const catalogFamilyCounts = new Map<string, number>();
  catalog?.techniques.forEach((item) => {
    catalogFamilyCounts.set(item.family, (catalogFamilyCounts.get(item.family) ?? 0) + 1);
  });
  const familyKeys = new Set<string>([
    ...Object.keys(assessment?.family_summary ?? {}),
    ...catalogFamilyCounts.keys(),
  ]);
  const familyRows = familyKeys.size
    ? [...familyKeys].map((key) => [
        key,
        TECHNIQUE_FAMILY_LABELS[key] ?? assessment?.family_summary[key]?.name_zh ?? key,
        assessment?.family_summary[key]?.total_count ?? catalogFamilyCounts.get(key) ?? 0,
      ] as [string, string, number])
    : DEFAULT_TECHNIQUE_FAMILIES;

  return (
    <section className="evidence-preview" id="evidence" aria-labelledby="evidence-title">
      <div className="evidence-heading">
        <div><span className="card-kicker">{isLive ? "EVIDENCE PREVIEW · LIVE API" : "EVIDENCE PREVIEW · DEMO / MOCK"}</span><h2 id="evidence-title">把评分还原到可核验的画面</h2><p>{isLive ? "以下轨迹与证据就绪度来自当前任务的 Stage 1 产物；短时外推仍是可视化启发式，不是物理模型或准确率承诺。" : "以下是离线占位视觉，用于展示真实服务返回后会落位的数据结构。不会冒充模型预测或球员成绩。"}</p></div>
        <span className="demo-badge">{isLive ? "LIVE OBSERVATION" : "DEMO DATA"}</span>
      </div>
      <div className="evidence-grid">
        <article className="frame-card">
          <div className="frame-toolbar"><span>{isLive ? "证据画布 · 当前任务" : "证据帧 · 00:02.480"}</span><span>{isLive ? "no frame asset" : "pose + ball + racket"}</span></div>
          <div className={`court-frame ${isLive ? "live-court-frame" : ""}`} role="img" aria-label={`${isLive ? "当前任务暂无原始帧，仅显示空白场地构图" : "Demo 网球场证据帧插画，包含球员骨架和球拍框线"}`}>
            <div className="court-lines" />
            {!isLive && <><div className="player-skeleton"><i className="sk-head" /><i className="sk-body" /><i className="sk-arm" /><i className="sk-racket" /><i className="sk-leg left" /><i className="sk-leg right" /></div><span className="ball-dot" /><span className="frame-label">MOCK FRAME</span></>}
            {isLive && <div className="live-frame-placeholder"><strong>暂无真实帧资产</strong><span>ILLUSTRATION ONLY</span><small>轨迹与 bbox 数值仍来自 API；此画布不绘制本次任务的虚构人体或球。</small></div>}
          </div>
          <div className="frame-caption"><strong>{isLive ? `已观测技术 ${observedTechniques.length} / ${assessment?.techniques.length ?? catalog?.techniques.length ?? 24}` : "准备阶段 / GS01-M01"}</strong><span>{isLive ? "当前服务未返回可叠加的原始帧；不会把插画当作检测帧。" : "来源：离线演示占位，不代表实际检测结果"}</span></div>
        </article>
        <article className="trajectory-card">
          <div className="frame-toolbar"><span>球轨迹预测</span><span className="confidence-high">{isLive ? `${trajectory?.ball.prediction_status === "heuristic_preview" ? "启发式预览" : "待观测"} · ${confidenceLabel(trajectory?.ball.confidence.mean)}` : "置信度 0.84 · Demo"}</span></div>
          <svg className="trajectory-chart" viewBox="0 0 520 190" role="img" aria-label={`${isLive ? "当前任务" : "Demo"} 球轨迹预测可视化`}>
            <defs><linearGradient id="traj" x1="0" x2="1"><stop offset="0" stopColor="#9ee15a"/><stop offset="1" stopColor="#6cb7ff"/></linearGradient></defs>
            <path d="M26 166 H486 M26 22 V166" stroke="rgba(255,255,255,.14)" />
            {!isLive && <path d="M26 151 C 115 140, 120 42, 218 61 S 348 156, 486 29" fill="none" stroke="url(#traj)" strokeWidth="4" strokeDasharray="8 7" />}
            {isLive && observed.length > 1 && <polyline points={chartPoints(observed)} fill="none" stroke="#c9ff43" strokeWidth="3" />}
            {isLive && predictedLine.length > 1 && <polyline points={chartPoints(predictedLine)} fill="none" stroke="#71a7ff" strokeWidth="3" strokeDasharray="8 7" />}
            {!isLive && <><circle cx="26" cy="151" r="6" fill="#c9ff43"/><circle cx="486" cy="29" r="6" fill="#71a7ff"/></>}
            {isLive && observed.length > 0 && <circle cx={26 + observed[0].x * 460} cy={22 + observed[0].y * 144} r="5" fill="#c9ff43" />}
            {isLive && predicted.length > 0 && <circle cx={26 + predicted[predicted.length - 1].x * 460} cy={22 + predicted[predicted.length - 1].y * 144} r="5" fill="#71a7ff" />}
            {isLive && observed.length === 0 && <text x="175" y="100" fill="rgba(255,255,255,.55)" fontSize="12">暂无可用球观测点</text>}
            <text x="28" y="181" fill="rgba(255,255,255,.5)" fontSize="10">观测</text><text x="445" y="181" fill="rgba(255,255,255,.5)" fontSize="10">外推</text>
          </svg>
          <div className="trajectory-meta"><span><b>方向</b> {isLive ? directionLabel(trajectory?.ball.velocity?.direction_image_deg) : "右前方"}</span><span><b>连续帧</b> {isLive ? `${trajectory?.ball.observed_count ?? 0} / ${trajectory?.source.frame_count ?? 0}` : "18 / 22"}</span><span><b>来源</b> {isLive ? "frames.jsonl · API" : "ball.track · mock"}</span></div>
        </article>
        <article className="signal-card">
          <div className="frame-toolbar"><span>专项观测</span><span>数据来源</span></div>
          <div className="signal-row"><span className="signal-icon">R</span><div><strong>球拍识别</strong><small>{isLive ? `${racket?.geometry_status ?? "bbox_only"} · ${racket?.association_status === "unassociated" ? "未关联主球员" : "无关键点"}` : "racket.keypoint_geometry"}</small></div><b>{isLive ? confidenceLabel(racketConfidence) : "0.79"}</b></div>
          <div className="signal-row"><span className="signal-icon grip">G</span><div><strong>握拍状态</strong><small>仅作候选状态，不下技术结论</small></div><b>待确认</b></div>
          <div className="signal-row"><span className="signal-icon court">C</span><div><strong>{isLive ? "技术证据" : "场地标定"}</strong><small>{isLive ? `${observedTechniques.length} 项动作已观测` : "court.calibration · demo"}</small></div><b>{isLive ? (assessment?.overall_evidence_score_0_to_100 === null || assessment?.overall_evidence_score_0_to_100 === undefined ? "待确认" : `${assessment.overall_evidence_score_0_to_100}`) : "0.91"}</b></div>
          <p className="signal-note">{isLive ? `场地覆盖 ${coveragePercent(courtCoverage)}；球拍当前只承诺通用 bbox 观测，触球与握拍仍需专项模型。` : "接入真实 API 后，这些卡片会由 artifact / feature 字段驱动；缺失字段保持“待确认”。"}</p>
        </article>
      </div>
      <div className="technique-strip" aria-label="最新技术指标目录">
        {familyRows.map(([key, label, total]) => {
          const summary = assessment?.family_summary[key];
          return <div key={key} className="technique-chip"><span>{label}</span><strong>{summary ? `${summary.observed_count} / ${summary.total_count}` : `${total} 项`}</strong><small>{summary ? `就绪度 ${summary.evidence_score_0_to_100 ?? "—"}` : "指标契约"}</small></div>;
        })}
      </div>
      {evidence.error && <p className="upload-error evidence-error" role="status">部分增强证据暂不可用：{evidence.error}</p>}
    </section>
  );
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
          <span className="mono-id">{result.card.id}</span>
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
  const [scenarioId, setScenarioId] = useState(SCENARIOS[0].id);
  const [importedScenario, setImportedScenario] = useState<Scenario | null>(null);
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
  const [videoFile, setVideoFile] = useState<File | null>(null);
  const [uploadState, setUploadState] = useState<"idle" | "ready" | "uploading" | "processing" | "complete" | "error">("idle");
  const [job, setJob] = useState<JobProgress | null>(null);
  const [uploadError, setUploadError] = useState("");
  const [evidence, setEvidence] = useState<LiveEvidence>(EMPTY_EVIDENCE);
  const [techniqueCatalog, setTechniqueCatalog] = useState<TechniqueCatalogResponse | null>(null);
  const [catalogError, setCatalogError] = useState("");
  const apiClient = useMemo(() => createApiClient(), []);
  const analysisRunRef = useRef(0);

  function invalidateAnalysisRun() {
    analysisRunRef.current += 1;
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
    const file = event.target.files?.[0];
    if (!file) return;
    invalidateAnalysisRun();
    setUploadError("");
    if (!isSupportedVideo(file)) {
      setVideoFile(null); setJob(null); setEvidence(EMPTY_EVIDENCE); setScoreContext("demo");
      setImportedScenario(null); setScenarioId(SCENARIOS[0].id);
      setImportState({ status: "idle" }); setUploadState("error");
      setUploadError("请选择 MP4、MOV、M4V、AVI 或 MKV 视频文件。"); return;
    }
    setVideoFile(file); setJob(null); setUploadState("ready"); setEvidence(EMPTY_EVIDENCE); setScoreContext("demo"); setImportedScenario(null); setScenarioId(SCENARIOS[0].id); setImportState({ status: "idle" });
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
    setVideoFile(file); setJob(null); setUploadState("ready"); setEvidence(EMPTY_EVIDENCE); setScoreContext("demo"); setImportedScenario(null); setScenarioId(SCENARIOS[0].id); setImportState({ status: "idle" });
  }

  async function submitVideo() {
    if (!videoFile) return;
    invalidateAnalysisRun();
    const runId = analysisRunRef.current;
    setUploadState("uploading"); setUploadError("");
    setImportedScenario(null);
    setScenarioId(SCENARIOS[0].id);
    setScoreContext("live-pending");
    setEvidence({ mode: "live", trajectory: null, assessment: null, error: null });
    try {
      const submitted = await apiClient.submitVideo(videoFile, { courtMode: "auto", writeAnnotatedVideo: true });
      if (analysisRunRef.current !== runId) return;
      setJob(submitted); setUploadState("processing");
      setEvidence({ mode: "live", jobId: String(submitted.id), trajectory: null, assessment: null, error: null });
      let latest = submitted;
      for (let attempt = 0; attempt < 180 && !["completed", "succeeded", "failed", "cancelled"].includes(String(latest.status).toLowerCase()); attempt += 1) {
        await new Promise((resolve) => window.setTimeout(resolve, Math.min(1500 + attempt * 20, 5000)));
        latest = await apiClient.getJob(String(latest.id));
        if (analysisRunRef.current !== runId) return;
        setJob(latest);
      }
      if (analysisRunRef.current !== runId) return;
      if (["completed", "succeeded"].includes(String(latest.status).toLowerCase())) {
        const [trajectoryResult, assessmentResult, demoResultResult] = await Promise.allSettled([
          apiClient.getTrajectory(String(latest.id), { endpoint: latest.trajectory_url }),
          apiClient.getTechniqueAssessment(String(latest.id), { endpoint: latest.technique_assessment_url }),
          apiClient.getDemoResult(String(latest.id)),
        ]);
        const trajectory = trajectoryResult.status === "fulfilled" ? trajectoryResult.value : null;
        const assessment = assessmentResult.status === "fulfilled" ? assessmentResult.value : null;
        const failedEvidence = [trajectoryResult, assessmentResult]
          .filter((result): result is PromiseRejectedResult => result.status === "rejected")
          .map((result) => result.reason instanceof Error ? result.reason.message : "增强证据请求失败")
          .join("；");
        setEvidence({ mode: "live", jobId: String(latest.id), trajectory, assessment, error: failedEvidence || null });
        const latestSummary = latest.summary;
        const demoSummary = demoResultResult.status === "fulfilled" ? demoResultResult.value.summary : undefined;
        const summaryCandidate = (demoSummary && typeof demoSummary === "object" ? demoSummary : latestSummary);
        if (isStage1Summary(summaryCandidate)) {
          const nextScenario = scenarioFromStage1Summary(summaryCandidate);
          setImportedScenario(nextScenario);
          setScenarioId(nextScenario.id);
          setScoreContext("live");
        }
        setUploadState("complete");
        window.setTimeout(() => document.getElementById("evidence")?.scrollIntoView({ behavior: "smooth", block: "start" }), 0);
      } else if (["failed", "cancelled"].includes(String(latest.status).toLowerCase())) {
        setUploadState("error"); setScoreContext("demo"); setEvidence(EMPTY_EVIDENCE); setUploadError(latest.error || "处理失败，请检查服务端日志。");
      } else {
        // Keep the task visible as a live pending run instead of presenting an
        // error state that invites the user to trust stale demo numbers.
        setUploadState("processing"); setUploadError(`任务仍在后台运行（${String(latest.id)}），可稍后从任务接口继续查询。`);
      }
    } catch (error) {
      if (analysisRunRef.current !== runId) return;
      setUploadState("error"); setScoreContext("demo"); setEvidence(EMPTY_EVIDENCE);
      setUploadError(error instanceof RallyMateApiError ? `${error.message}（${error.status}）` : "无法连接评分服务。可先使用离线 Demo 继续浏览。");
    }
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
    setImportState({ status: "reading", fileName: file.name });
    setUploadError("");
    try {
      const summary = JSON.parse(await file.text());
      if (!isStage1Summary(summary)) {
        throw new Error("该 JSON 不是有效的 Stage 1 summary：需要 job_id，以及 coverage 或 processing 字段。");
      }
      const nextScenario = scenarioFromStage1Summary(summary);
      setImportedScenario(nextScenario);
      setScenarioId(nextScenario.id);
      setJob(null);
      setUploadState("idle");
      setVideoFile(null);
      setEvidence({
        mode: "live",
        trajectory: null,
        assessment: null,
        error: "已导入 summary；该文件不包含轨迹与技术增强接口结果。上传视频后可获取实时观测。",
      });
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
          : "无法读取该 JSON。请确认它是一期 pipeline 生成的 summary.json。",
      });
    } finally {
      event.target.value = "";
    }
  }

  function exportReport() {
    const compactReport = {
      ...report,
      results: report.results.map((result) => ({
        indicatorId: result.card.id,
        indicatorName: result.card.name,
        domain: result.card.domain,
        eventCode: result.card.eventCode,
        stageCode: result.card.stageCode,
        status: result.status,
        score: result.score,
        grade: result.grade,
        evidence: result.evidence,
        confidence: result.confidence,
        dimensions: result.dimensions,
        verdict: result.verdict,
        feedback: result.feedback,
      })),
    };
    const blob = new Blob([JSON.stringify(compactReport, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `rallymate-score-${scenario.id}.json`;
    anchor.click();
    URL.revokeObjectURL(url);
  }

  return (
    <main className="app-shell" aria-busy={importState.status === "reading"}>
      <header className="topbar">
        <a className="brand" href="#top" aria-label="RallyMate Score Lab 首页">
          <span className="brand-mark">RM</span>
          <span><strong>RallyMate</strong><small>SCORE LAB</small></span>
        </a>
        <nav className="topnav" aria-label="页面导航">
          <a href="#upload">上传分析</a>
          <a href="#scoreboard">评分台</a>
          <a href="#evidence">证据回放</a>
          <a href="#rules">规则明细</a>
          <a href="#system">系统逻辑</a>
        </nav>
        <div className="version-pill"><i /> 兼容 {registryVersion} · 技术目录 {techniqueCatalog?.registry_version ?? "加载中"}</div>
      </header>

      <section className="hero" id="top">
        <div className="hero-copy">
          <div className="eyebrow"><span>SCORING SYSTEM DEMO</span><i /></div>
          <h1>让每一分，<br /><em>都能追溯到证据。</em></h1>
          <p>把五份最新技术定义与原有 298 条评分规则，转化为一套能运行、能解释、能守住不可评价边界的动作评分系统。</p>
          <div className="hero-actions">
            <a className="primary-button" href="#upload">上传视频 <span>↘</span></a>
            <button className="ghost-button" onClick={() => fileInput.current?.click()} aria-describedby="import-status" disabled={importState.status === "reading"}>
              {importState.status === "reading" ? "正在读取…" : "导入一期 summary.json"}
            </button>
            <input ref={fileInput} type="file" accept="application/json,.json" hidden onChange={importSummary} />
          </div>
          <div id="import-status" className={`import-status import-${importState.status}`} role="status" aria-live="polite">
            {importState.status === "reading" && <>正在解析 {importState.fileName}，请稍候…</>}
            {importState.status === "success" && <>已载入 {importState.fileName}。当前场景标记为导入数据。</>}
            {importState.status === "error" && <><strong>导入失败：</strong> {importState.message}</>}
          </div>
        </div>
        <div className="hero-system-map hero-visual-card" aria-label="RallyMate 网球姿态与球轨迹分析示意">
          <Image className="hero-visual" src="/og.png" width={1536} height={1024} priority alt="网球运动员姿态骨架、球拍与球轨迹的 RallyMate 分析示意图" />
          <div className="hero-visual-shade" />
          <div className="map-caption">VIDEO → EVIDENCE → FEEDBACK</div>
          <div className="hero-visual-copy">
            <strong>动作不只给结果，<br />还要说明依据。</strong>
            <div><span>24 项技术目录</span><span>球轨迹预览</span><span>球拍 bbox</span></div>
          </div>
          <div className="map-footer"><span>技术分与证据覆盖率分离</span><span>缺失证据不补分</span></div>
        </div>
      </section>

      <section className="upload-section" id="upload" aria-labelledby="upload-title">
        <div className="upload-copy"><span className="card-kicker">01 / VIDEO INTAKE</span><h2 id="upload-title">上传一段击球视频，开始证据链分析。</h2><p>支持本地 API、AutoDL 或部署域名。上传仅提交到你配置的服务端；未连接服务时仍可浏览下方离线 Demo。</p><div className="source-chip"><i /> API 来源：{apiClient.config.baseUrl || "当前站点 /api"}</div></div>
        <div className="upload-card">
          <input ref={videoInput} type="file" accept="video/mp4,video/quicktime,video/x-msvideo,video/x-matroska,.mp4,.mov,.m4v,.avi,.mkv" hidden onChange={chooseVideo} />
          <button className={`dropzone ${uploadState === "error" ? "has-error" : ""}`} onClick={() => videoInput.current?.click()} onDragOver={(event) => event.preventDefault()} onDrop={(event) => { event.preventDefault(); acceptVideoFile(event.dataTransfer.files?.[0]); }} aria-label="选择或拖入视频文件">
            <span className="upload-icon">↑</span><strong>{videoFile ? videoFile.name : "选择或拖入视频文件"}</strong><small>{videoFile ? formatBytes(videoFile.size) : "MP4 / MOV / M4V / AVI / MKV · 建议 200 MB 以内"}</small>
          </button>
          {videoFile && <div className="upload-file-row"><span><b>已选择</b> {formatBytes(videoFile.size)}</span><button onClick={() => { invalidateAnalysisRun(); setVideoFile(null); setJob(null); setUploadState("idle"); setUploadError(""); setImportState({ status: "idle" }); setEvidence(EMPTY_EVIDENCE); setScoreContext("demo"); setImportedScenario(null); setScenarioId(SCENARIOS[0].id); }}>移除</button></div>}
          {uploadState !== "idle" && uploadState !== "ready" && <div className="progress-block" aria-live="polite"><div className="progress-head"><span>{uploadState === "uploading" ? "正在上传" : uploadState === "processing" ? (jobPhase(job) || "正在分析视频") : uploadState === "complete" ? "分析完成" : "处理异常"}</span><strong>{Math.round(jobPercent(job, uploadState === "complete" ? 100 : 18))}%</strong></div><div className="progress-track"><span style={{ width: `${Math.max(4, jobPercent(job, uploadState === "complete" ? 100 : 18))}%` }} /></div></div>}
          {uploadError && <p className="upload-error" role="alert">{uploadError}</p>}
          <button className="primary-button upload-submit" disabled={!videoFile || uploadState === "uploading" || uploadState === "processing"} onClick={submitVideo}>{uploadState === "processing" ? "处理中…" : uploadState === "complete" ? "再次分析" : "开始分析"}<span>→</span></button>
          <p className="upload-footnote">隐私提示：文件由配置的 API 处理。Demo/Mock 视图不会写入真实模型结果。</p>
        </div>
      </section>

      <DemoEvidencePanel evidence={evidence} catalog={techniqueCatalog} />

      <section className="scenario-strip" id="scoreboard">
        <div>
          <span className="strip-label">{scoreContext === "demo" ? "兼容验收场景" : "当前数据上下文"}</span>
          <select value={scoreContext === "live-pending" ? PENDING_SCENARIO.id : scenarioId} disabled={scoreContext === "live-pending"} onChange={(event) => chooseScenario(event.target.value)}>
            {scoreContext === "live-pending" && <option value={PENDING_SCENARIO.id}>{PENDING_SCENARIO.label}</option>}
            {scenarios.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
          </select>
        </div>
        <p>{scenario.description}</p>
        <span className={`mode-chip mode-${scenario.mode}`}>{scenario.mode === "demo" ? "完整演示模式" : "真实数据审慎模式"}</span>
      </section>

      <section className={`data-context-banner context-${scoreContext}`} aria-live="polite">
        <span className="context-dot" />
        {scoreContext === "demo" && <p><strong>兼容演示层：</strong>下方 A—E 与四维分来自旧版 298 条合成验收规则，仅用于验证界面与聚合逻辑，不代表上传视频结果。</p>}
        {scoreContext === "live-pending" && <p><strong>真实任务处理中：</strong>评分台暂不读取演示分；等待服务端 summary 与证据接口返回后再切换为审计视图。</p>}
        {scoreContext === "live" && <p><strong>真实证据审计：</strong>当前场景由任务 summary 驱动。新技术目录只显示证据就绪度，正式技术分与 A—E 仍保持不可用。</p>}
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
                <div className="module-head"><span>{item}</span><i>{item === "GS" ? "70% 权重" : "30% 权重"}</i></div>
                <strong>{scoreText(moduleResult.score ?? moduleResult.evidence)}</strong>
                <p>{item === "GS" ? "底线击球 · 248 项" : "步伐事件 · 50 项"}</p>
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
            <button role="tab" aria-selected={domain === "GS"} tabIndex={domain === "GS" ? 0 : -1} className={domain === "GS" ? "active" : ""} onClick={() => switchDomain("GS")}>GS 底线击球</button>
            <button role="tab" aria-selected={domain === "FS"} tabIndex={domain === "FS" ? 0 : -1} className={domain === "FS" ? "active" : ""} onClick={() => switchDomain("FS")}>FS 步伐事件</button>
          </div>
        </div>

        <div className="event-rail">
          {domainResult.groups.map((group) => (
            <button key={group.code} aria-pressed={eventCode === group.code} className={eventCode === group.code ? "active" : ""} onClick={() => chooseEvent(group.code)}>
              <span>{group.code}</span><strong>{EVENT_NAMES[group.code] ?? group.name}</strong><small>{scoreText(group.score ?? group.evidence)}</small>
            </button>
          ))}
        </div>

        {domain === "GS" && (
          <div className="stage-rail">
            <span>动作阶段</span>
            {stageCodes.map((code) => {
              const name = eventResults.find((item) => item.card.stageCode === code)?.card.stageName ?? code;
              return <button key={code} aria-pressed={stageCode === code} className={stageCode === code ? "active" : ""} onClick={() => { setStageCode(code); const first = eventResults.find((item) => item.card.stageCode === code); if (first) setSelectedId(first.card.id); setVisible(8); }}>{code.split("-")[1]} · {name}</button>;
            })}
          </div>
        )}

        <div className="rules-layout">
          <div className="rules-list">
            <div className="rules-list-head">
              <div><span className="mono-id">{eventCode}{stageCode ? ` / ${stageCode}` : ""}</span><h3>{currentStageName || EVENT_NAMES[eventCode]}</h3></div>
              <label className="search-box"><span>⌕</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索指标" /></label>
            </div>
            <div className="table-head"><span>指标</span><span>状态</span><span>{scenario.mode === "demo" ? "分数" : "证据"}</span></div>
            <div className="result-rows">
              {displayedResults.map((result) => (
                <button key={result.card.id} className={`result-row ${selectedResult?.card.id === result.card.id ? "active" : ""}`} onClick={() => setSelectedId(result.card.id)}>
                  <span className="result-name"><i className={`status-mark status-${result.status}`} /> <span><small>{result.card.id}</small><strong>{result.card.name}</strong></span></span>
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

      <section className="logic-section" id="system">
        <div className="logic-heading"><span className="section-number">03</span><div><span className="card-kicker">SCORING CONTRACT</span><h2>一套能被验收的评分逻辑</h2><p>先判断能不能评，再计算评多少；最后把模块得分合成，但始终保留证据覆盖率。最新 24 项技术目录默认只返回证据就绪度。</p></div></div>
        <div className="formula-grid">
          <article><span>01 / QUALITY GATE</span><h3>证据门禁</h3><p>必需依赖取最低覆盖率；可选依赖只影响 15% 证据置信度。低于 40% 阻断，40–72% 部分可用。</p><div className="formula-line"><b>Pose</b><i>×</i><b>Ball</b><i>×</i><b>Racket</b><i>×</i><b>Court</b></div></article>
          <article><span>02 / FEATURE SCORE</span><h3>四维评分</h3><p>每个指标统一由技术完成度、时机节奏、稳定平衡、连续衔接组成。</p><div className="weight-row"><b>40%</b><b>25%</b><b>20%</b><b>15%</b></div></article>
          <article><span>03 / AGGREGATION</span><h3>逐级聚合</h3><p>指标先聚合到事件；同类事件等权，再按 GS 70% 与 FS 30% 形成综合分。</p><div className="formula-big">总分 = GS × .70 + FS × .30</div></article>
          <article><span>04 / COMPATIBILITY</span><h3>A—E 五级</h3><p>这是既有 298 条 GS/FS 演示规则的兼容验收层；五份最新定义没有提供阈值，因此不会被擅自套用。</p><div className="grade-scale"><b>A 90+</b><b>B 80+</b><b>C 70+</b><b>D 60+</b><b>E &lt;60</b></div></article>
        </div>
      </section>

      <section className="model-decision">
        <div><span className="card-kicker">POSE MODEL DECISION</span><h2>Pose 不推倒重来，分两步升级。</h2></div>
        <div className="decision-steps">
          <article><span>现在</span><h3>保留 COCO17 基线</h3><p>先修复主球员轨迹和遮挡稳定性，落地 117 项 pose-only 核心子集。</p><b>近期必做</b></article>
          <div className="decision-arrow">→</div>
          <article><span>下一版</span><h3>扩到 23–25 个关键点</h3><p>补骨盆中心、脚跟/前掌/脚尖等，覆盖制动、落地、重心与站位细节。</p><b>评分增强</b></article>
          <div className="decision-arrow">→</div>
          <article><span>并行</span><h3>独立球拍关键点模型</h3><p>拍头、拍柄、拍面与甜区不应塞进人体 Pose 模型，单独训练更稳。</p><b>专项模型</b></article>
        </div>
      </section>

      <section className="acceptance-section">
        <div className="acceptance-copy"><span className="card-kicker">{scoreContext === "demo" ? "DEMO ACCEPTANCE" : "LIVE AUDIT STATUS"}</span><h2>{scoreContext === "demo" ? "本 Demo 已覆盖什么？" : "本次任务现在能确认什么？"}</h2><p>{scoreContext === "demo" ? "它验证的是评分系统能否完整运转，也明确暴露一期数据距离真实技术评分还有哪些缺口。" : "真实任务只展示服务端返回的证据与就绪度；兼容层的 PASS 不会被当成本次视频的技术通过。"}</p><button className="primary-button" onClick={exportReport}>导出当前{scoreContext === "demo" ? "完整报告" : "审计报告"} <span>↓</span></button></div>
        {scoreContext === "demo" ? (
          <div className="acceptance-list">
            <div><span>01</span><p><strong>298 条规则全部入库</strong><small>{sources.map((source) => `${source.domain} ${source.indicatorCount}`).join(" · ")}</small></p><b>PASS</b></div>
            <div><span>02</span><p><strong>完整演示可全量评分</strong><small>含等级原文、四维分、证据和反馈</small></p><b>PASS</b></div>
            <div><span>03</span><p><strong>真实一期结果可导入</strong><small>自动生成证据就绪 / 部分 / 阻断清单</small></p><b>PASS</b></div>
            <div><span>04</span><p><strong>不可评价边界已实现</strong><small>无事件特征时不会生成虚假技术分</small></p><b>PASS</b></div>
          </div>
        ) : (
          <div className="acceptance-list">
            <div><span>01</span><p><strong>兼容 298 条规则</strong><small>静态验收层保留；不代表当前视频通过</small></p><b>AUDIT</b></div>
            <div><span>02</span><p><strong>任务证据链</strong><small>{uploadState === "complete" ? "summary / trajectory / assessment 已请求" : "等待或导入 summary 证据"}</small></p><b>{uploadState === "complete" ? "READY" : "PENDING"}</b></div>
            <div><span>03</span><p><strong>球与球拍观测</strong><small>{evidence.trajectory ? "球轨迹与 bbox 字段来自 API" : "当前文件未提供增强观测"}</small></p><b>{evidence.trajectory ? "OBSERVED" : "WAIT"}</b></div>
            <div><span>04</span><p><strong>正式技术等级</strong><small>新 24 项定义仍需教练标定，缺失证据不补分</small></p><b>LOCKED</b></div>
          </div>
        )}
      </section>

      <footer>
        <div className="brand"><span className="brand-mark">RM</span><span><strong>RallyMate</strong><small>SCORE LAB</small></span></div>
        <p>GS × FS compatibility layer · {registryVersion} · technique catalog {techniqueCatalog?.registry_version ?? "loading"}</p>
        <span>Built for evidence-first coaching.</span>
      </footer>
    </main>
  );
}
