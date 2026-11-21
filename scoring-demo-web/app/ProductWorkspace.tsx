"use client";

// Native links avoid the current vinext client-router error when returning to the homepage.

import { useMemo, useRef, useState, type ReactNode } from "react";
import { recentReports, type RecentReport } from "./lib/training-report";
import { playPressFeedback } from "./lib/press-feedback";
import { workspaceHref, type WorkspaceView } from "./lib/workspace-navigation";
import type { PersonalJobRecord } from "./lib/api-client";
import "./workspace-redesign.css";
import "./training-home.css";

export type { WorkspaceView } from "./lib/workspace-navigation";
type IconName = "sessions" | "analysis" | "plus" | "arrow-right" | "arrow-up-right" | "play" | "check" | "camera" | "frame" | "upload" | "movement" | "close" | "menu" | "tennis";

export function ProductIcon({ name, size = 20, className }: { name: IconName; size?: number; className?: string }) {
  const paths: Record<IconName, ReactNode> = {
    sessions: <><rect x="3" y="5" width="18" height="16" rx="3" /><path d="M7 5V3m10 2V3M3 10h18m-13 5h3m3 0h2" /></>,
    analysis: <><rect x="3" y="3" width="18" height="18" rx="3" /><path d="M7 16v-4m5 4V7m5 9v-6" /></>,
    plus: <path d="M12 5v14M5 12h14" />,
    "arrow-right": <path d="M4 12h15m-6-6 6 6-6 6" />,
    "arrow-up-right": <path d="M6 18 18 6M6 6h12v12" />,
    play: <path d="m9 5 11 7-11 7Z" />,
    check: <path d="m5 12 4 4L19 6" />,
    camera: <><path d="M4 7h4l2-3h4l2 3h4v13H4Z" /><circle cx="12" cy="13" r="3.5" /></>,
    frame: <><path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5" /><rect x="7" y="7" width="10" height="10" rx="1" /></>,
    upload: <><path d="M12 16V3m-5 5 5-5 5 5M4 16v5h16v-5" /></>,
    movement: <><path d="m3 15 4-7 5 9 4-12 5 10" /><path d="M3 21h18" /></>,
    close: <path d="m6 6 12 12M6 18 18 6" />,
    menu: <path d="M4 6h16M4 12h16M4 18h16" />,
    tennis: <><circle cx="12" cy="12" r="9" /><path d="M4 8c5 0 7-2 8-5M12 21c1-4 4-6 8-6" /></>,
  };
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className={className}>{paths[name]}</svg>;
}

const navigation: ReadonlyArray<{ view: WorkspaceView; label: string }> = [
  { view: "overview", label: "训练总览" },
  { view: "sessions", label: "训练记录" },
  { view: "analysis", label: "视频分析" },
];

export function ProductShell({ view, onNavigate, analysisJobId, children }: {
  view: WorkspaceView;
  onNavigate: (view: WorkspaceView) => void;
  analysisJobId?: string | null;
  children: ReactNode;
}) {
  return <div className="product-app pw-studio">
    <a className="pw-skip-link" href="#workspace-content">跳到主要内容</a>
    <aside className="pw-sidebar">
      <a className="pw-brand" href="/welcome" aria-label="RallyMate 首页">
        <ProductIcon name="tennis" size={25} /><span>RallyMate</span>
      </a>
      <nav className="pw-navigation" aria-label="主导航" data-view={view}>
        <div className="pw-nav-track">
          <span className="pw-nav-cursor" aria-hidden="true" />
          {navigation.map(item => <a key={item.view}
            href={workspaceHref(item.view, analysisJobId)}
            aria-current={view === item.view ? "page" : undefined}
            onClick={event => {
              if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
              event.preventDefault();
              if (view !== item.view) onNavigate(item.view);
            }}>
            <span>{item.label}</span>
          </a>)}
        </div>
      </nav>
    </aside>
    <div className="pw-workspace">
      <main id="workspace-content" className="pw-content" tabIndex={-1}>{children}</main>
    </div>
  </div>;
}

function dateText(value: string, short = false) {
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return "查看时间未记录";
  return new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", year: short ? undefined : "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }).format(date);
}

function orderedReports(reports: HomeReport[], direction: "desc" | "asc" = "desc") {
  const validated = new Map<string, HomeReport>();
  for (const raw of reports) {
    if (raw?.source === "imported") {
      if (typeof raw.id === "string" && raw.id.startsWith("imported:") && raw.id.length > 9 &&
          typeof raw.name === "string" && typeof raw.viewedAt === "string" && Number.isFinite(Date.parse(raw.viewedAt)) && !validated.has(raw.id)) {
        validated.set(raw.id, { id: raw.id, name: raw.name.slice(0, 160), viewedAt: raw.viewedAt, source: "imported", dateLabel: "最近查看" });
      }
      continue;
    }
    const [report] = recentReports([raw]);
    if (report && !validated.has(report.id)) validated.set(report.id, { ...report, status: raw.status, dateLabel: raw.dateLabel });
  }
  return [...validated.values()].sort((a, b) => (Date.parse(a.viewedAt) - Date.parse(b.viewedAt)) * (direction === "desc" ? -1 : 1));
}

export type HomeReport = RecentReport & { source?: "online" | "imported"; status?: PersonalJobRecord["status"]; dateLabel?: "最近查看" | "提交时间" };
const recordStates: Record<PersonalJobRecord["status"], string> = {
  queued: "排队中", running: "分析中", succeeded: "已完成", failed: "分析失败", cancelled: "已取消",
};

function isActiveReport(report: HomeReport) {
  return report.status === "running" || report.status === "queued";
}

export function VideoSelectionHint() {
  return <><span className="file-drag-hint">可将视频拖到这里。</span>先预览，再提交分析。</>;
}

function OverviewRecord({ report, loadedImportId, protectCurrent, dateLabel, onOpen }: {
  report: HomeReport; loadedImportId?: string; protectCurrent: boolean;
  dateLabel: string; onOpen: (id: string) => void;
}) {
  const imported = report.source === "imported";
  const active = isActiveReport(report);
  const action = imported ? report.id === loadedImportId ? "继续复盘" : "选择报告备份"
    : active ? "查看进度" : report.status === "failed" || report.status === "cancelled" ? "查看任务" : "查看报告";
  const name = report.name || "未命名训练";
  const state = imported ? "本地导入" : report.status ? recordStates[report.status] : "";
  const openSeparately = protectCurrent && !imported;
  const content = <>
    <span className="home-entry-name">{name}</span>
    <span className="home-entry-meta">{state && <span>{state}</span>}
      <time dateTime={report.viewedAt}>{report.dateLabel ?? dateLabel} · {dateText(report.viewedAt, true)}</time>
    </span>
    <span className="home-entry-action">{action}
      {openSeparately && <small>新标签页</small>}
      {protectCurrent && imported && <small>当前操作结束后可用</small>}
    </span>
  </>;
  return <li>{openSeparately
    ? <a className="home-entry" href={workspaceHref("analysis", report.id)} target="_blank" rel="noopener noreferrer">{content}</a>
    : <button type="button" className="home-entry" disabled={protectCurrent && imported} onClick={() => onOpen(report.id)}>{content}</button>}
  </li>;
}

export function TrainingHome({ reports, onOpenReport, onNewAnalysis, onNavigate, currentName, loadedImportId, busy = false, dateLabel = "最近查看", loading = false, error = "", onRetry, draftName, activeJobId, stageLabel, onDropVideo }: {
  reports: HomeReport[]; onOpenReport: (id: string) => void; onNewAnalysis: () => void;
  onNavigate: (view: WorkspaceView) => void; currentName?: string; loadedImportId?: string; busy?: boolean; dateLabel?: string;
  loading?: boolean; error?: string; onRetry?: () => void;
  draftName?: string; activeJobId?: string | null; stageLabel?: string; onDropVideo?: (file: File) => void;
}) {
  const recent = useMemo(() => orderedReports(reports), [reports]);
  const remaining = busy && activeJobId ? recent.filter(report => report.id !== activeJobId) : recent;
  const inProgress = remaining.filter(isActiveReport);
  const completed = remaining.filter(report => !isActiveReport(report));
  const [dragging, setDragging] = useState(false);
  const [dropError, setDropError] = useState("");
  const dragDepth = useRef(0);
  const protectCurrent = busy || Boolean(draftName);
  function renderRecord(report: HomeReport) {
    return <OverviewRecord key={report.id} report={report} loadedImportId={loadedImportId}
      protectCurrent={protectCurrent} dateLabel={dateLabel} onOpen={onOpenReport} />;
  }
  return <div className="training-home">
    <h1>训练总览</h1>
    <section className={`home-upload${dragging && !busy ? " is-dragging" : ""}`} aria-labelledby="home-upload-title"
      onDragEnter={event => {
        if (!event.dataTransfer.types.includes("Files")) return;
        event.preventDefault();
        dragDepth.current++;
        setDragging(true);
      }}
      onDragLeave={() => { dragDepth.current = Math.max(0, dragDepth.current - 1); if (!dragDepth.current) setDragging(false); }}
      onDragOver={event => {
        if (event.dataTransfer.types.includes("Files")) { event.preventDefault(); event.dataTransfer.dropEffect = busy ? "none" : "copy"; }
      }}
      onDrop={event => {
        event.preventDefault();
        dragDepth.current = 0; setDragging(false);
        if (busy || !onDropVideo) return;
        const files = event.dataTransfer.files;
        if (files.length > 1) { setDropError("一次可分析一段视频，请选择一个文件。"); return; }
        setDropError(""); if (files[0]) onDropVideo(files[0]);
      }}>
      <div className="home-upload-copy">
        <h2 id="home-upload-title">添加训练视频</h2>
        <p>{busy ? "这段视频分析完，就能添加下一段。" : dragging ? "松开即可预览这段视频。" : <VideoSelectionHint />}</p>
        <button type="button" className="home-pick" onClickCapture={playPressFeedback} disabled={busy} onClick={() => { setDropError(""); onNewAnalysis(); }}>选择视频</button>
        {dropError && <p className="home-drop-error" role="alert">{dropError}</p>}
      </div>
      <div className="home-court-crop" aria-hidden="true" />
    </section>
    {draftName && !busy && <section className="home-queue" aria-labelledby="home-draft-title">
      <h2 id="home-draft-title">待提交</h2>
      <button type="button" className="home-entry home-current" onClick={() => onNavigate("analysis")}>
        <span className="home-entry-name">{draftName}</span>
        <span className="home-entry-meta">已选视频</span>
        <span className="home-entry-action">继续提交</span>
      </button>
    </section>}
    {(busy || inProgress.length > 0) && <section className="home-queue" aria-labelledby="home-queue-title">
      <h2 id="home-queue-title">进行中</h2>
      <ol>{busy && <li><button type="button" className="home-entry home-current" onClick={() => onNavigate("analysis")}>
        <span className="home-entry-name">{currentName || "当前视频"}</span>
        <span className="home-entry-meta">{stageLabel || "处理中"}</span>
        <span className="home-entry-action">查看进度</span>
      </button></li>}{inProgress.map(renderRecord)}</ol>
    </section>}
    <section className="home-history" aria-labelledby="home-history-title">
      <header><h2 id="home-history-title">最近训练</h2>
        <button type="button" className="home-all" onClick={() => onNavigate("sessions")}>全部记录</button>
      </header>
      {completed.length > 0 && <ol>{completed.slice(0, 6).map(renderRecord)}</ol>}
      {error && <div className="home-feedback" role="alert"><p>{error}</p>{onRetry && <button type="button" onClick={onRetry}>重新读取</button>}</div>}
      {loading && !error && <p className="home-feedback" role="status">正在读取训练记录…</p>}
      {!loading && !error && completed.length === 0 && <p className="home-feedback">{protectCurrent || inProgress.length ? "分析完成后，报告会出现在这里。" : "还没有训练记录。"}</p>}
    </section>
  </div>;
}
