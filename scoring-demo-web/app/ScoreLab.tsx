"use client";

import { ChangeEvent, useRef, useState } from "react";
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

const DEPENDENCY_LABELS: Record<Dependency, string> = {
  pose: "人体姿态",
  ball: "球轨迹",
  racket: "球拍关键点",
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

function DemoEvidencePanel() {
  return (
    <section className="evidence-preview" id="evidence" aria-labelledby="evidence-title">
      <div className="evidence-heading">
        <div><span className="card-kicker">EVIDENCE PREVIEW · DEMO / MOCK</span><h2 id="evidence-title">把评分还原到可核验的画面</h2><p>以下是离线占位视觉，用于展示真实服务返回后会落位的数据结构。不会冒充模型预测或球员成绩。</p></div>
        <span className="demo-badge">DEMO DATA</span>
      </div>
      <div className="evidence-grid">
        <article className="frame-card">
          <div className="frame-toolbar"><span>证据帧 · 00:02.480</span><span>pose + ball + racket</span></div>
          <div className="court-frame" role="img" aria-label="Demo 网球场证据帧占位图，包含球员骨架和球拍框线">
            <div className="court-lines" /><div className="player-skeleton"><i className="sk-head" /><i className="sk-body" /><i className="sk-arm" /><i className="sk-racket" /><i className="sk-leg left" /><i className="sk-leg right" /></div><span className="ball-dot" /><span className="frame-label">MOCK FRAME</span>
          </div>
          <div className="frame-caption"><strong>准备阶段 / GS01-M01</strong><span>来源：离线演示占位，不代表实际检测结果</span></div>
        </article>
        <article className="trajectory-card">
          <div className="frame-toolbar"><span>球轨迹预测</span><span className="confidence-high">置信度 0.84 · Demo</span></div>
          <svg className="trajectory-chart" viewBox="0 0 520 190" role="img" aria-label="Demo 球轨迹预测可视化">
            <defs><linearGradient id="traj" x1="0" x2="1"><stop offset="0" stopColor="#9ee15a"/><stop offset="1" stopColor="#6cb7ff"/></linearGradient></defs>
            <path d="M26 151 C 115 140, 120 42, 218 61 S 348 156, 486 29" fill="none" stroke="url(#traj)" strokeWidth="4" strokeDasharray="8 7" />
            <path d="M26 166 H486 M26 22 V166" stroke="rgba(255,255,255,.14)" /><circle cx="26" cy="151" r="6" fill="#c9ff43"/><circle cx="486" cy="29" r="6" fill="#71a7ff"/>
            <text x="28" y="181" fill="rgba(255,255,255,.5)" fontSize="10">起始帧</text><text x="445" y="181" fill="rgba(255,255,255,.5)" fontSize="10">落点区间</text>
          </svg>
          <div className="trajectory-meta"><span><b>方向</b> 右前方</span><span><b>连续帧</b> 18 / 22</span><span><b>来源</b> ball.track · mock</span></div>
        </article>
        <article className="signal-card">
          <div className="frame-toolbar"><span>专项观测</span><span>数据来源</span></div>
          <div className="signal-row"><span className="signal-icon">R</span><div><strong>球拍识别</strong><small>racket.keypoint_geometry</small></div><b>0.79</b></div>
          <div className="signal-row"><span className="signal-icon grip">G</span><div><strong>握拍状态</strong><small>仅作候选状态，不下技术结论</small></div><b>待确认</b></div>
          <div className="signal-row"><span className="signal-icon court">C</span><div><strong>场地标定</strong><small>court.calibration · demo</small></div><b>0.91</b></div>
          <p className="signal-note">接入真实 API 后，这些卡片会由 artifact / feature 字段驱动；缺失字段保持“待确认”。</p>
        </article>
      </div>
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

  const scenarios = importedScenario ? [...SCENARIOS, importedScenario] : SCENARIOS;
  const scenario = scenarios.find((item) => item.id === scenarioId) ?? SCENARIOS[0];
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
    setUploadError("");
    if (!file.type.startsWith("video/") && !/\.(mp4|mov|webm)$/i.test(file.name)) {
      setVideoFile(null); setUploadState("error"); setUploadError("请选择 MP4、MOV 或 WebM 视频文件。"); return;
    }
    setVideoFile(file); setUploadState("ready");
  }

  function acceptVideoFile(file: File | undefined) {
    if (!file) return;
    setUploadError("");
    if (!file.type.startsWith("video/") && !/\.(mp4|mov|webm)$/i.test(file.name)) {
      setVideoFile(null); setUploadState("error"); setUploadError("请选择 MP4、MOV 或 WebM 视频文件。"); return;
    }
    setVideoFile(file); setUploadState("ready");
  }

  async function submitVideo() {
    if (!videoFile) return;
    setUploadState("uploading"); setUploadError("");
    try {
      const client = createApiClient();
      const submitted = await client.submitVideo(videoFile, { courtMode: "auto" });
      setJob(submitted); setUploadState("processing");
      let latest = submitted;
      for (let attempt = 0; attempt < 30 && !["completed", "succeeded", "failed", "cancelled"].includes(String(latest.status).toLowerCase()); attempt += 1) {
        await new Promise((resolve) => window.setTimeout(resolve, 1200));
        latest = await client.getJob(String(latest.id));
        setJob(latest);
      }
      if (["completed", "succeeded"].includes(String(latest.status).toLowerCase())) setUploadState("complete");
      else if (["failed", "cancelled"].includes(String(latest.status).toLowerCase())) { setUploadState("error"); setUploadError(latest.error || "处理失败，请检查服务端日志。"); }
    } catch (error) {
      setUploadState("error");
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

  async function importSummary(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    setImportState({ status: "reading", fileName: file.name });
    try {
      const summary = JSON.parse(await file.text());
      const nextScenario = scenarioFromStage1Summary(summary);
      setImportedScenario(nextScenario);
      setScenarioId(nextScenario.id);
      setImportState({ status: "success", fileName: file.name });
    } catch {
      setImportState({ status: "error", message: "无法读取该 JSON。请确认它是一期 pipeline 生成的 summary.json。" });
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
        <div className="version-pill"><i /> 规则库 {registryVersion}</div>
      </header>

      <section className="hero" id="top">
        <div className="hero-copy">
          <div className="eyebrow"><span>SCORING SYSTEM DEMO</span><i /></div>
          <h1>让每一分，<br /><em>都能追溯到证据。</em></h1>
          <p>把两份评分指标卡转化为一套能运行、能解释、能守住不可评价边界的动作评分系统。</p>
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
        <div className="hero-system-map" aria-label="评分系统数据流">
          <div className="map-caption">LIVE LOGIC MAP</div>
          <div className="map-node active"><span>01</span><div><strong>事件切分</strong><small>GS 50 阶段 · FS 10 事件</small></div><i /></div>
          <div className="map-link"><b /><b /><b /></div>
          <div className="map-node"><span>02</span><div><strong>证据门禁</strong><small>Pose · Ball · Racket · Court</small></div><i /></div>
          <div className="map-link"><b /><b /><b /></div>
          <div className="map-node"><span>03</span><div><strong>指标评分</strong><small>298 条规则 · A—E 五级</small></div><i /></div>
          <div className="map-footer"><span>✓ 技术分与证据覆盖率分离</span><span>✓ 不可评价不补分</span></div>
        </div>
      </section>

      <section className="upload-section" id="upload" aria-labelledby="upload-title">
        <div className="upload-copy"><span className="card-kicker">01 / VIDEO INTAKE</span><h2 id="upload-title">上传一段击球视频，开始证据链分析。</h2><p>支持本地 API、AutoDL 或部署域名。上传仅提交到你配置的服务端；未连接服务时仍可浏览下方离线 Demo。</p><div className="source-chip"><i /> API 来源：{createApiClient().config.baseUrl || "当前站点 /api"}</div></div>
        <div className="upload-card">
          <input ref={videoInput} type="file" accept="video/mp4,video/quicktime,video/webm,.mp4,.mov,.webm" hidden onChange={chooseVideo} />
          <button className={`dropzone ${uploadState === "error" ? "has-error" : ""}`} onClick={() => videoInput.current?.click()} onDragOver={(event) => event.preventDefault()} onDrop={(event) => { event.preventDefault(); acceptVideoFile(event.dataTransfer.files?.[0]); }} aria-label="选择或拖入视频文件">
            <span className="upload-icon">↑</span><strong>{videoFile ? videoFile.name : "选择或拖入视频文件"}</strong><small>{videoFile ? formatBytes(videoFile.size) : "MP4 / MOV / WebM · 建议 200 MB 以内"}</small>
          </button>
          {videoFile && <div className="upload-file-row"><span><b>已选择</b> {formatBytes(videoFile.size)}</span><button onClick={() => { setVideoFile(null); setUploadState("idle"); }}>移除</button></div>}
          {uploadState !== "idle" && uploadState !== "ready" && <div className="progress-block" aria-live="polite"><div className="progress-head"><span>{uploadState === "uploading" ? "正在上传" : uploadState === "processing" ? (jobPhase(job) || "正在分析视频") : uploadState === "complete" ? "分析完成" : "处理异常"}</span><strong>{Math.round(jobPercent(job, uploadState === "complete" ? 100 : 18))}%</strong></div><div className="progress-track"><span style={{ width: `${Math.max(4, jobPercent(job, uploadState === "complete" ? 100 : 18))}%` }} /></div></div>}
          {uploadError && <p className="upload-error" role="alert">{uploadError}</p>}
          <button className="primary-button upload-submit" disabled={!videoFile || uploadState === "uploading" || uploadState === "processing"} onClick={submitVideo}>{uploadState === "processing" ? "处理中…" : uploadState === "complete" ? "再次分析" : "开始分析"}<span>→</span></button>
          <p className="upload-footnote">隐私提示：文件由配置的 API 处理。Demo/Mock 视图不会写入真实模型结果。</p>
        </div>
      </section>

      <DemoEvidencePanel />

      <section className="scenario-strip" id="scoreboard">
        <div>
          <span className="strip-label">当前验收场景</span>
          <select value={scenarioId} onChange={(event) => setScenarioId(event.target.value)}>
            {scenarios.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
          </select>
        </div>
        <p>{scenario.description}</p>
        <span className={`mode-chip mode-${scenario.mode}`}>{scenario.mode === "demo" ? "完整演示模式" : "真实数据审慎模式"}</span>
      </section>

      <section className="score-overview">
        <div className={`score-card score-${report.overallGrade ?? "na"}`}>
          <div className="score-ring" style={{ "--score": `${report.overallScore ?? report.overallEvidence}` } as React.CSSProperties}>
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
        <div className="logic-heading"><span className="section-number">03</span><div><span className="card-kicker">SCORING CONTRACT</span><h2>一套能被验收的评分逻辑</h2><p>先判断能不能评，再计算评多少；最后把模块得分合成，但始终保留证据覆盖率。</p></div></div>
        <div className="formula-grid">
          <article><span>01 / QUALITY GATE</span><h3>证据门禁</h3><p>必需依赖取最低覆盖率；可选依赖只影响 15% 证据置信度。低于 40% 阻断，40–72% 部分可用。</p><div className="formula-line"><b>Pose</b><i>×</i><b>Ball</b><i>×</i><b>Racket</b><i>×</i><b>Court</b></div></article>
          <article><span>02 / FEATURE SCORE</span><h3>四维评分</h3><p>每个指标统一由技术完成度、时机节奏、稳定平衡、连续衔接组成。</p><div className="weight-row"><b>40%</b><b>25%</b><b>20%</b><b>15%</b></div></article>
          <article><span>03 / AGGREGATION</span><h3>逐级聚合</h3><p>指标先聚合到事件；同类事件等权，再按 GS 70% 与 FS 30% 形成综合分。</p><div className="formula-big">总分 = GS × .70 + FS × .30</div></article>
          <article><span>04 / GRADE</span><h3>A—E 五级</h3><p>等级阈值统一，反馈正文仍取自对应指标卡，避免通用话术覆盖专业定义。</p><div className="grade-scale"><b>A 90+</b><b>B 80+</b><b>C 70+</b><b>D 60+</b><b>E &lt;60</b></div></article>
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
        <div className="acceptance-copy"><span className="card-kicker">DEMO ACCEPTANCE</span><h2>本 Demo 已覆盖什么？</h2><p>它验证的是评分系统能否完整运转，也明确暴露一期数据距离真实技术评分还有哪些缺口。</p><button className="primary-button" onClick={exportReport}>导出当前完整报告 <span>↓</span></button></div>
        <div className="acceptance-list">
          <div><span>01</span><p><strong>298 条规则全部入库</strong><small>{sources.map((source) => `${source.domain} ${source.indicatorCount}`).join(" · ")}</small></p><b>PASS</b></div>
          <div><span>02</span><p><strong>完整演示可全量评分</strong><small>含等级原文、四维分、证据和反馈</small></p><b>PASS</b></div>
          <div><span>03</span><p><strong>真实一期结果可导入</strong><small>自动生成证据就绪 / 部分 / 阻断清单</small></p><b>PASS</b></div>
          <div><span>04</span><p><strong>不可评价边界已实现</strong><small>无事件特征时不会生成虚假技术分</small></p><b>PASS</b></div>
        </div>
      </section>

      <footer>
        <div className="brand"><span className="brand-mark">RM</span><span><strong>RallyMate</strong><small>SCORE LAB</small></span></div>
        <p>GS × FS scoring system demo · {registryVersion}</p>
        <span>Built for evidence-first coaching.</span>
      </footer>
    </main>
  );
}
