"use client";

// Native links avoid the current vinext client-router error when returning to the homepage.

import { useEffect, useId, useMemo, useRef, useState, type ReactNode } from "react";
import { recentReports, type RecentReport } from "./lib/training-report";
import { AnimatedDisclosure } from "./AnimatedDisclosure";
import { courtArtworkPath, courtArtworkPoint, COURT_LENGTH, DOUBLES_WIDTH, NET_DISTANCE, SERVICE_DISTANCE, SINGLES_WIDTH } from "./lib/court-artwork";
import type { PersonalJobRecord } from "./lib/api-client";
import "./workspace-redesign.css";

export type WorkspaceView = "overview" | "sessions" | "analysis" | "guide";
type IconName = "home" | "sessions" | "analysis" | "guide" | "plus" | "arrow-left" | "arrow-right" | "arrow-up-right" | "search" | "chevron-right" | "chevron-down" | "clock" | "play" | "check" | "camera" | "frame" | "upload" | "movement" | "close" | "menu" | "tennis";

export function ProductIcon({ name, size = 20, className }: { name: IconName; size?: number; className?: string }) {
  const paths: Record<IconName, ReactNode> = {
    home: <><path d="m3 10 9-7 9 7v10a1 1 0 0 1-1 1h-5v-7H9v7H4a1 1 0 0 1-1-1Z" /></>,
    sessions: <><rect x="3" y="5" width="18" height="16" rx="3" /><path d="M7 5V3m10 2V3M3 10h18m-13 5h3m3 0h2" /></>,
    analysis: <><rect x="3" y="3" width="18" height="18" rx="3" /><path d="M7 16v-4m5 4V7m5 9v-6" /></>,
    guide: <><path d="M12 5c-3-2-6-2-9-1v15c3-1 6-1 9 1 3-2 6-2 9-1V4c-3-1-6-1-9 1Zm0 0v15" /></>,
    plus: <path d="M12 5v14M5 12h14" />,
    "arrow-left": <path d="M20 12H5m6-6-6 6 6 6" />,
    "arrow-right": <path d="M4 12h15m-6-6 6 6-6 6" />,
    "arrow-up-right": <path d="M6 18 18 6M6 6h12v12" />,
    search: <><circle cx="10.5" cy="10.5" r="6.5" /><path d="m16 16 4 4" /></>,
    "chevron-right": <path d="m9 5 7 7-7 7" />,
    "chevron-down": <path d="m6 9 6 6 6-6" />,
    clock: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>,
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

const navigation: Array<{ view: WorkspaceView; label: string; icon: IconName }> = [
  { view: "overview", label: "训练总览", icon: "home" },
  { view: "sessions", label: "训练记录", icon: "sessions" },
  { view: "analysis", label: "视频分析", icon: "analysis" },
  { view: "guide", label: "拍摄指南", icon: "guide" },
];

function WorkspaceSurface({ view, children }: { view: WorkspaceView; children: ReactNode }) {
  const contentRef = useRef<HTMLElement>(null);
  useEffect(() => {
    const content = contentRef.current;
    const motion = window.matchMedia("(prefers-reduced-motion: reduce)");
    if (!content?.animate || motion.matches) return;
    // Animate the existing surface; never remount the player or upload inputs.
    const entry = content.animate([
      { opacity: 0.55, transform: "translateY(10px)" },
      { opacity: 1, transform: "translateY(0)" },
    ], { duration: 320, easing: "cubic-bezier(.22, 1, .36, 1)" });
    const stop = () => { if (motion.matches) entry.cancel(); };
    motion.addEventListener("change", stop);
    return () => { entry.cancel(); motion.removeEventListener("change", stop); };
  }, [view]);
  return <main ref={contentRef} id="workspace-content" className="pw-content" tabIndex={-1}>{children}</main>;
}

export function ProductShell({ view, onNavigate, onNewAnalysis, children, currentName, busy = false }: {
  view: WorkspaceView; onNavigate: (view: WorkspaceView) => void; onNewAnalysis: () => void;
  children: ReactNode; currentName?: string; busy?: boolean;
}) {
  const title = navigation.find(item => item.view === view)?.label ?? "训练总览";
  return <div className="product-app pw-studio">
    <a className="pw-skip-link" href="#workspace-content">跳到主要内容</a>
    <aside className="pw-sidebar">
      <a className="pw-brand" href="/welcome" aria-label="RallyMate 首页"><span className="pw-brand-symbol"><ProductIcon name="tennis" size={27} /></span><span>RallyMate</span></a>
      <nav className="pw-desktop-nav" aria-label="主导航">{navigation.map(item => <button type="button" key={item.view} className={view === item.view ? "is-active" : undefined} aria-current={view === item.view ? "page" : undefined} onClick={() => onNavigate(item.view)}><ProductIcon name={item.icon} /><span>{item.label}</span>{item.view === "analysis" && busy && <i className="pw-busy-dot" aria-label="分析进行中" />}</button>)}</nav>
    </aside>
    <div className="pw-workspace">
      <header className="pw-topbar"><div className="pw-breadcrumb"><a className="pw-home-link" href="/welcome"><ProductIcon name="arrow-left" size={17} /><span>返回首页</span></a><ProductIcon name="chevron-right" size={14} /><span>{title}</span>{view === "analysis" && currentName && <><ProductIcon name="chevron-right" size={14} /><span className="pw-current-name" title={currentName}>{currentName}</span></>}</div><div className="pw-topbar-actions"><button type="button" className="pw-button pw-button-accent pw-topbar-action" onClick={onNewAnalysis} disabled={busy}><ProductIcon name="plus" size={17} /><span>{busy ? "分析进行中" : "新建分析"}</span></button></div></header>
      <WorkspaceSurface view={view}>{children}</WorkspaceSurface>
    </div>
    <nav className="pw-mobile-nav" aria-label="移动主导航">{navigation.map(item => <button type="button" key={item.view} className={view === item.view ? "is-active" : undefined} aria-current={view === item.view ? "page" : undefined} onClick={() => onNavigate(item.view)}><ProductIcon name={item.icon} size={21} /><span>{item.label}</span></button>)}</nav>
  </div>;
}

function CourtArtwork() {
  const halfWidth = DOUBLES_WIDTH / 2, singles = SINGLES_WIDTH / 2;
  const serviceFar = NET_DISTANCE - SERVICE_DISTANCE, serviceNear = NET_DISTANCE + SERVICE_DISTANCE;
  const [netLeft, netY] = courtArtworkPoint(-halfWidth - .45, NET_DISTANCE);
  const [netRight] = courtArtworkPoint(halfWidth + .45, NET_DISTANCE);
  const [netCenter] = courtArtworkPoint(0, NET_DISTANCE);
  const netHeight = 15;
  return <svg className="pw-court-art pw-court-art-compact" viewBox="0 0 640 460" fill="none" aria-hidden="true">
    <g transform="translate(70 28) rotate(-13 250 200)">
      <path d="M148 24h268l111 344H5Z" fill="#394238" stroke="#8fa387" strokeOpacity=".18" />
      <path d={courtArtworkPath([[-halfWidth, 0], [halfWidth, 0], [halfWidth, COURT_LENGTH], [-halfWidth, COURT_LENGTH]], true)} stroke="#d5e6c5" strokeOpacity=".7" strokeWidth="1.4" />
      <g stroke="#d5e6c5" strokeOpacity=".58" strokeWidth="1.2">
        {[-singles, singles].map(x => <path key={x} d={courtArtworkPath([[x, 0], [x, COURT_LENGTH]])} />)}
        {[serviceFar, serviceNear].map(distance => <path key={distance} d={courtArtworkPath([[-singles, distance], [singles, distance]])} />)}
        <path d={courtArtworkPath([[0, serviceFar], [0, serviceNear]])} />
        <path d={courtArtworkPath([[0, 0], [0, .28]]) + courtArtworkPath([[0, COURT_LENGTH - .28], [0, COURT_LENGTH]])} />
      </g>
      <g data-court-net="midcourt">
        <path d={`M${netLeft} ${netY}H${netRight}`} stroke="#101b14" strokeWidth="3" strokeOpacity=".35" />
        <path d={`M${netLeft} ${netY - netHeight}Q${netCenter} ${netY - netHeight + 4} ${netRight} ${netY - netHeight}V${netY}H${netLeft}Z`} fill="#101b14" fillOpacity=".35" stroke="#d5e6c5" strokeOpacity=".25" />
        {[.25, .5, .75].map(row => <path key={row} d={`M${netLeft} ${netY - netHeight * row}H${netRight}`} stroke="#d5e6c5" strokeOpacity=".18" />)}
        {Array.from({ length: 31 }, (_, index) => netLeft + (netRight - netLeft) * (index + 1) / 32).map(x => <path key={x} d={`M${x} ${netY - netHeight + 2}V${netY}`} stroke="#d5e6c5" strokeOpacity=".14" />)}
        <path d={`M${netLeft} ${netY - netHeight}Q${netCenter} ${netY - netHeight + 4} ${netRight} ${netY - netHeight}`} stroke="#eff8e7" strokeWidth="2" />
        {[netLeft, netRight].map(x => <path key={x} d={`M${x} ${netY - netHeight - 3}V${netY + 2}`} stroke="#d5e6c5" strokeWidth="2.6" />)}
      </g>
    </g>
  </svg>;
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

function ReportPreview({ id }: { id: string }) {
  const [failed, setFailed] = useState(false);
  return <div className={`pw-report-preview${failed ? " is-fallback" : ""}`}>
    <div className="pw-preview-fallback"><CourtArtwork /></div>
    {/* eslint-disable-next-line @next/next/no-img-element -- Same-origin authenticated artifact must use its gateway URL without an image optimizer. */}
    {!failed && <img key={id} src={`/v1/jobs/${encodeURIComponent(id)}/artifacts/preview.jpg`} alt="" loading="lazy" onError={() => setFailed(true)} />}
    <span className="pw-preview-play"><ProductIcon name="play" size={19} /></span>
  </div>;
}

function ReportCard({ report, onOpen, dateLabel = "最近查看" }: { report: RecentReport; onOpen: (id: string) => void; dateLabel?: string }) {
  return <button type="button" className="pw-report-card" onClick={() => onOpen(report.id)} aria-label={`打开训练报告：${report.name || "未命名训练"}`}><ReportPreview key={report.id} id={report.id} /><div className="pw-report-card-body"><h3 title={report.name}>{report.name || "未命名训练"}</h3><p><ProductIcon name="clock" size={13} /><span>{dateLabel} · {dateText(report.viewedAt)}</span></p></div></button>;
}

function EmptyTraining({ onNewAnalysis, busy = false }: { onNewAnalysis: () => void; busy?: boolean }) {
  return <div className="pw-empty-training"><span className="pw-empty-icon"><ProductIcon name="sessions" size={30} /></span><div><h3>第一份训练记录，从这里开始</h3><p>上传一段训练视频，把值得回看的动作留在这里。</p></div><button type="button" className="pw-button pw-button-secondary" onClick={onNewAnalysis} disabled={busy}>{busy ? "等待当前分析完成" : "上传训练视频"}<ProductIcon name="arrow-right" size={17} /></button></div>;
}

export type HomeReport = RecentReport & { source?: "online" | "imported"; status?: PersonalJobRecord["status"]; dateLabel?: "最近查看" | "提交时间" };
const recordStates: Record<PersonalJobRecord["status"], string> = {
  queued: "排队中", running: "分析中", succeeded: "分析完成", failed: "分析失败", cancelled: "已取消",
};

export function TrainingHome({ reports, onOpenReport, onNewAnalysis, onNavigate, currentName, loadedImportId, busy = false, dateLabel = "最近查看", loading = false, error = "", onRetry }: {
  reports: HomeReport[]; onOpenReport: (id: string) => void; onNewAnalysis: () => void;
  onNavigate: (view: WorkspaceView) => void; currentName?: string; loadedImportId?: string; busy?: boolean; dateLabel?: string;
  loading?: boolean; error?: string; onRetry?: () => void;
}) {
  const recent = useMemo(() => orderedReports(reports), [reports]);
  const featured = recent.find(report => report.status === "running" || report.status === "queued") ?? recent[0];
  const history = busy ? recent : recent.filter(report => report.id !== featured?.id);
  const stage = featured?.status;
  const active = busy || stage === "running" || stage === "queued";
  const hasSelection = busy || Boolean(featured);
  const mainLabel = busy ? "当前分析进度" : active ? "进行中的训练" : featured || loading ? "最近一次训练" : "新建训练";
  const mainAction = active ? "查看进度" : !featured ? "上传训练视频" : featured.source === "imported" && featured.id !== loadedImportId ? "选择报告备份" : stage === "failed" || stage === "cancelled" ? "查看任务" : "打开训练报告";
  const openFeatured = () => {
    if (busy) onNavigate("analysis");
    else if (featured) onOpenReport(featured.id);
    else onNewAnalysis();
  };
  return <div className="pw-home">
    <header className="studio-title"><h1>训练总览</h1></header>
    <div className="studio-overview">
      <section className="studio-feature" aria-label={mainLabel}>
        <header className="studio-section-rail"><h2>{mainLabel}</h2>{(busy || stage) && <span className="studio-state">{busy ? "处理中" : stage && recordStates[stage]}</span>}</header>
        {!hasSelection && loading ? <p className="studio-empty-message" role="status">正在读取最近训练…</p> : <button type="button" className="studio-feature-link" onClick={openFeatured}>
          {featured && featured.source !== "imported" && !busy && (!stage || stage === "succeeded") ? <ReportPreview key={featured.id} id={featured.id} /> :
            hasSelection ? <div className="studio-court-slate" aria-hidden="true"><CourtArtwork /></div> :
            <span className="studio-empty-photo" aria-hidden="true" />}
          <span className="studio-feature-body">
            <span className="studio-feature-name">{busy ? currentName || "当前视频" : featured?.name || "选择一段训练原片"}</span>
            {featured && !busy && <time dateTime={featured.viewedAt}>{featured.dateLabel ?? dateLabel} · {dateText(featured.viewedAt)}{featured.source === "imported" && " · 本地导入"}</time>}
            {!hasSelection && <span className="studio-filetypes">MP4 / MOV / M4V / AVI / MKV</span>}
            <span className="studio-feature-cta">{mainAction}<ProductIcon name="arrow-up-right" size={19} /></span>
          </span>
        </button>}
      </section>
      <aside className="studio-recent" aria-labelledby="pw-recent-heading">
        <header className="studio-section-rail"><h2 id="pw-recent-heading">近期记录</h2><button type="button" className="studio-link" onClick={() => onNavigate("sessions")}>查看全部<ProductIcon name="arrow-right" size={16} /></button></header>
        {history.length > 0 ? <ol className="studio-history-list">{history.slice(0, 6).map(report => <li key={report.id}>
          <button type="button" disabled={busy && report.source === "imported"} onClick={() => onOpenReport(report.id)}><span><strong>{report.name || "未命名训练"}</strong><time dateTime={report.viewedAt}>{report.dateLabel ?? dateLabel} · {dateText(report.viewedAt, true)}{report.source === "imported" && " · 本地导入"}</time></span><ProductIcon name="arrow-up-right" size={16} /></button>
        </li>)}</ol> : <p className="studio-empty-message" role="status">{loading ? "正在读取训练记录…" : error ? "训练记录读取失败。" : hasSelection ? "暂无其他训练记录" : "训练记录将在上传后出现在这里。"}</p>}
        {error && <div className="studio-load-error" role="alert"><p>{error}</p>{onRetry && <button className="studio-link" type="button" onClick={onRetry}>重新读取</button>}</div>}
        <button className="studio-guide-link" type="button" onClick={() => onNavigate("guide")}><ProductIcon name="camera" size={22} /><span><strong>拍摄指南</strong><small>机位、取景与开拍检查</small></span><ProductIcon name="arrow-right" size={17} /></button>
      </aside>
    </div>
  </div>;
}

export function SessionLibrary({ reports, onOpenReport, onNewAnalysis }: { reports: RecentReport[]; onOpenReport: (id: string) => void; onNewAnalysis: () => void }) {
  const [search, setSearch] = useState("");
  const [order, setOrder] = useState<"desc" | "asc">("desc");
  const cleanReports = useMemo(() => orderedReports(reports, order), [reports, order]);
  const matching = useMemo(() => cleanReports.filter(report => report.name.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase())), [cleanReports, search]);
  const searchId = useId(), orderId = useId();
  return <div className="pw-library"><div className="pw-page-heading"><div><h1>训练记录</h1><p>这里保存此浏览器最近查看过的训练报告。</p></div><button type="button" className="pw-button pw-button-secondary" onClick={onNewAnalysis}><ProductIcon name="plus" size={18} />新建分析</button></div><div className="pw-library-toolbar"><div className="pw-search"><ProductIcon name="search" size={19} /><label className="pw-sr-only" htmlFor={searchId}>按训练视频名称搜索</label><input id={searchId} type="search" value={search} onChange={event => setSearch(event.target.value)} placeholder="搜索训练视频名称" autoComplete="off" /></div><div className="pw-sort"><label htmlFor={orderId}>最近查看</label><select id={orderId} value={order} onChange={event => setOrder(event.target.value === "asc" ? "asc" : "desc")}><option value="desc">由新到旧</option><option value="asc">由旧到新</option></select><ProductIcon name="chevron-down" size={15} /></div></div><div className="pw-library-meta" aria-live="polite"><span>{search.trim() ? `找到 ${matching.length} 份记录` : `共 ${cleanReports.length} 份最近记录`}</span>{search.trim() && <button type="button" className="pw-text-button" onClick={() => setSearch("")}>清除搜索<ProductIcon name="close" size={14} /></button>}</div>{!cleanReports.length ? <EmptyTraining onNewAnalysis={onNewAnalysis} /> : matching.length ? <div className="pw-report-grid pw-library-grid">{matching.map(report => <ReportCard key={report.id} report={report} onOpen={onOpenReport} />)}</div> : <div className="pw-search-empty"><span className="pw-empty-icon"><ProductIcon name="search" size={28} /></span><h2>暂时没有找到这份训练</h2><p>试试视频名称中的其他字词，或查看全部记录。</p><button type="button" className="pw-button pw-button-secondary" onClick={() => setSearch("")}>查看全部记录</button></div>}<p className="pw-library-note"><ProductIcon name="clock" size={14} />按最近查看时间记录；这里的时间不是视频拍摄时间。</p></div>;
}

function CaptureIllustrations() {
  return <section className="pw-capture-visuals" aria-label="机位与构图示意">
    <figure><svg viewBox="0 0 320 200" role="img" aria-label="机位示意：固定手机放在底线后斜侧方，朝向球员"><path d="M90 20h140v142H90zM90 91h140M107 20v142M213 20v142M90 56h140M90 126h140M160 56v70" fill="none" stroke="#99ad98" strokeWidth="1.5" /><circle cx="160" cy="146" r="10" fill="#d8fc75" /><path d="m265 177-95-44 69 5Z" fill="#d8fc75" opacity=".15" /><path d="m261 176-94-31" stroke="#d8fc75" strokeDasharray="4 5" /><rect x="253" y="164" width="25" height="17" rx="3" fill="#d8fc75" /><path d="m264 181-8 13m8-13 8 13" stroke="#d8fc75" strokeWidth="2" /><text x="28" y="148" fill="#c6d1c0" fontSize="13">球员</text><path d="M60 144h80" stroke="#64775e" /></svg><figcaption><strong>先试斜后方或侧面机位</strong><span>手机固定在场外安全位置，先试挥和横移，再调整距离。角度随观察主题调整。</span></figcaption></figure>
    <figure className="is-good"><svg viewBox="0 0 320 200" role="img" aria-label="合格构图示意：头部、球拍与双脚都在画面中，周围留有活动余量"><rect x="22" y="16" width="276" height="168" rx="8" fill="#19251d" stroke="#7f9b66" /><path d="M22 154h276" stroke="#475f44" /><circle cx="155" cy="57" r="12" fill="none" stroke="#c0d1b6" strokeWidth="4" /><path d="M155 70v49m0-37-34 27m34-25 40-14m-40 49-22 39m22-39 28 38" fill="none" stroke="#c0d1b6" strokeWidth="5" strokeLinecap="round" /><path d="m196 70 13-17" stroke="#d8fc75" strokeWidth="3" /><ellipse cx="217" cy="42" rx="11" ry="16" transform="rotate(35 217 42)" stroke="#d8fc75" strokeWidth="2" fill="none" /><path d="m41 38 7 7 13-15" stroke="#d8fc75" strokeWidth="4" fill="none" /></svg><figcaption><strong>完整入镜，四周留余量</strong><span>移动时也能看见头、手、球拍和双脚；身体足够大，细节清楚。</span></figcaption></figure>
    <figure className="is-bad"><svg viewBox="0 0 320 200" role="img" aria-label="不合格构图示意：镜头过近，双脚和球拍被画面边缘裁切"><defs><clipPath id="capture-crop"><rect x="22" y="16" width="276" height="168" rx="8" /></clipPath></defs><rect x="22" y="16" width="276" height="168" rx="8" fill="#222521" stroke="#848a7f" /><g clipPath="url(#capture-crop)" fill="none" stroke="#babdb4" strokeWidth="7" strokeLinecap="round"><circle cx="157" cy="48" r="20" /><path d="M157 69v87m0-64-62 46m62-43 70-47m-70 108-41 68m41-68 46 68" /><path d="m228 48 26-45" stroke="#b5bbae" /></g><path d="m40 32 16 16m0-16L40 48M100 183h111" stroke="#e5e8dd" strokeWidth="3" /></svg><figcaption><strong>镜头过近，脚或球拍出画</strong><span>退远一些再拍。遮挡、强逆光和手持跟拍，也会让部分动作无法判断。</span></figcaption></figure>
  </section>;
}

const guideSections: Array<{ icon: IconName; title: string; details: ReactNode }> = [
  { icon: "camera", title: "固定机位，留出完整空间", details: <><p>用支架或稳固的位置固定手机。横拍或竖拍都可以，重点是运动中头部、持拍手和双脚始终留在画面里。</p><p>开拍前试挥一拍并左右移动，确认不会出画。避免手持跟拍、频繁缩放和镜头切换。</p></> },
  { icon: "frame", title: "让需要观察的部位看得清", details: <><p>优先选择光线均匀的位置，避开强逆光。球员不能小到看不清肩、髋和手脚，也不要为了放大画面裁掉脚部。</p><p>侧面或斜后方机位可以作为起点，再根据教练想观察的动作调整。没有一个角度能同时看清所有细节；被身体或球拍遮住的部分需要另外拍摄。</p></> },
  { icon: "movement", title: "保留动作前后", details: <><p>第一次可以选 10–30 秒的原片，围绕同一项练习。</p><p>从准备动作开始录制，拍到击球后恢复站位。</p></> },
  { icon: "upload", title: "使用原视频，保留原来的节奏", details: <><p>上传相机或手机直接保存的视频，优先保留原清晰度、速度和画幅。不要添加美颜、骨架、贴纸或遮挡动作的字幕。</p><p>慢动作和变速导出可能改变视频时间关系。如需慢放，请在回放时调整；初次分析优先使用正常速度的原片。</p></> },
];

export function CaptureGuide({ onNewAnalysis }: { onNewAnalysis: () => void }) {
  const [checked, setChecked] = useState<Set<number>>(() => new Set());
  const checklist = ["机位稳定，全身与双脚都在画面里", "光线清楚，肩髋和持拍手尽量没有遮挡", "保留动作前的准备和动作后的恢复", "准备好未经变速和拼接的原视频"];
  return <div className="pw-guide"><header className="studio-title"><h1>拍摄指南</h1></header>
    <div className="studio-guide-layout"><div className="studio-guide-reading"><CaptureIllustrations />
    <section className="pw-guide-articles" aria-label="拍摄建议">{guideSections.map((section, index) => <AnimatedDisclosure className="pw-guide-detail" bodyClassName="pw-guide-detail-body" key={section.title} defaultOpen={index === 0} summary={<><span className="pw-guide-icon"><ProductIcon name={section.icon} size={22} /></span><span><strong>{section.title}</strong></span><ProductIcon name="plus" size={18} /></>}>{section.details}</AnimatedDisclosure>)}</section></div>
    <aside className="pw-checklist"><h2>开拍检查</h2><p className="pw-checklist-intro">可选自查，勾选不会影响视频能否上传。</p><div className="pw-checklist-items">{checklist.map((item, index) => <label key={item} className={checked.has(index) ? "is-checked" : undefined}><input type="checkbox" checked={checked.has(index)} onChange={() => setChecked(previous => { const next = new Set(previous); if (next.has(index)) next.delete(index); else next.add(index); return next; })} /><span className="pw-custom-check"><ProductIcon name="check" size={13} /></span><span>{item}</span></label>)}</div><p className="pw-checklist-count" aria-live="polite">{checked.size === checklist.length ? "准备就绪，带上你的训练视频。" : `已检查 ${checked.size} / ${checklist.length} 项`}</p><button type="button" className="pw-button pw-button-accent" onClick={onNewAnalysis}>开始视频分析<ProductIcon name="arrow-right" size={18} /></button></aside></div></div>;
}
