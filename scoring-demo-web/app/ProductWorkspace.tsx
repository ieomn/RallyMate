"use client";

import { useEffect, useId, useMemo, useRef, useState, type ReactNode } from "react";
import { recentReports, type RecentReport } from "./lib/training-report";
import { AnimatedDisclosure } from "./AnimatedDisclosure";

export type WorkspaceView = "overview" | "sessions" | "analysis" | "guide";
type IconName = "home" | "sessions" | "analysis" | "guide" | "plus" | "arrow-right" | "arrow-up-right" | "search" | "chevron-right" | "chevron-down" | "clock" | "play" | "check" | "camera" | "frame" | "upload" | "movement" | "close" | "menu" | "tennis";

export function ProductIcon({ name, size = 20, className }: { name: IconName; size?: number; className?: string }) {
  const paths: Record<IconName, ReactNode> = {
    home: <><path d="m3 10 9-7 9 7v10a1 1 0 0 1-1 1h-5v-7H9v7H4a1 1 0 0 1-1-1Z" /></>,
    sessions: <><rect x="3" y="5" width="18" height="16" rx="3" /><path d="M7 5V3m10 2V3M3 10h18m-13 5h3m3 0h2" /></>,
    analysis: <><rect x="3" y="3" width="18" height="18" rx="3" /><path d="M7 16v-4m5 4V7m5 9v-6" /></>,
    guide: <><path d="M12 5c-3-2-6-2-9-1v15c3-1 6-1 9 1 3-2 6-2 9-1V4c-3-1-6-1-9 1Zm0 0v15" /></>,
    plus: <path d="M12 5v14M5 12h14" />,
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
  return <div className="product-app">
    <a className="pw-skip-link" href="#workspace-content">跳到主要内容</a>
    <aside className="pw-sidebar">
      <button type="button" className="pw-brand" onClick={() => onNavigate("overview")} aria-label="RallyMate 训练总览"><span className="pw-brand-symbol"><ProductIcon name="tennis" size={27} /></span><span>RallyMate<small>你的网球训练工作台</small></span></button>
      <p className="pw-nav-label">训练空间</p>
      <nav className="pw-desktop-nav" aria-label="主导航">{navigation.map(item => <button type="button" key={item.view} className={view === item.view ? "is-active" : undefined} aria-current={view === item.view ? "page" : undefined} onClick={() => onNavigate(item.view)}><ProductIcon name={item.icon} /><span>{item.label}</span>{item.view === "analysis" && busy && <i className="pw-busy-dot" aria-label="分析进行中" />}</button>)}</nav>
      <div className="pw-sidebar-bottom"><button type="button" className="pw-sidebar-guide" onClick={() => onNavigate("guide")}><ProductIcon name="camera" size={24} /><strong>拍好下一次训练</strong><span>从清晰、完整的画面开始。</span><span className="pw-inline-link">查看拍摄指南 <ProductIcon name="arrow-right" size={16} /></span></button><div className="pw-sidebar-note"><span className="pw-small-mark" />专注每一次进步</div></div>
    </aside>
    <div className="pw-workspace">
      <header className="pw-topbar"><div className="pw-breadcrumb"><button type="button" className="pw-mobile-brand" onClick={() => onNavigate("overview")} aria-label="RallyMate 训练总览"><ProductIcon name="tennis" size={24} /></button><span className="pw-breadcrumb-root">训练空间</span><ProductIcon name="chevron-right" size={14} /><span>{title}</span>{view === "analysis" && currentName && <><ProductIcon name="chevron-right" size={14} /><span className="pw-current-name" title={currentName}>{currentName}</span></>}</div><button type="button" className="pw-button pw-button-accent pw-topbar-action" onClick={onNewAnalysis} disabled={busy}><ProductIcon name="plus" size={17} /><span>{busy ? "分析进行中" : "新建分析"}</span></button></header>
      <WorkspaceSurface view={view}>{children}</WorkspaceSurface>
      <footer className="pw-footer"><span>RallyMate</span><span>看见动作，理解训练。</span></footer>
    </div>
    <nav className="pw-mobile-nav" aria-label="移动主导航">{navigation.map(item => <button type="button" key={item.view} className={view === item.view ? "is-active" : undefined} aria-current={view === item.view ? "page" : undefined} onClick={() => onNavigate(item.view)}><ProductIcon name={item.icon} size={21} /><span>{item.label}</span></button>)}</nav>
  </div>;
}

function CourtArtwork({ compact = false }: { compact?: boolean }) {
  const id = useId().replace(/:/g, "");
  return <svg className={compact ? "pw-court-art pw-court-art-compact" : "pw-court-art"} viewBox="0 0 640 460" fill="none" aria-hidden="true">
    <defs><linearGradient id={`${id}-floor`} x1="120" y1="0" x2="570" y2="440" gradientUnits="userSpaceOnUse"><stop stopColor="#344c34" /><stop offset="1" stopColor="#18221c" /></linearGradient><radialGradient id={`${id}-glow`}><stop stopColor="#c4f469" stopOpacity=".16" /><stop offset="1" stopColor="#c4f469" stopOpacity="0" /></radialGradient></defs>
    <ellipse cx="358" cy="228" rx="295" ry="218" fill={`url(#${id}-glow)`} />
    <g transform="translate(70 28) rotate(-13 250 200)"><path d="M148 24h268l111 344H5Z" fill={`url(#${id}-floor)`} stroke="#8fa387" strokeOpacity=".18" /><path d="M173 53h222l86 280H47Z" stroke="#d5e6c5" strokeOpacity=".7" strokeWidth="1.4" /><path d="M191 53 84 333M377 53l67 280M128 169h302M91 254h365M281 169l-5 85M163 88h242" stroke="#d5e6c5" strokeOpacity=".58" strokeWidth="1.2" /><path d="m106 211 338 0" stroke="#eff8e7" strokeWidth="2" /><path d="M106 205v20m338-20v20" stroke="#d5e6c5" strokeWidth="3" /><path d="M106 214h338m-338 4h338" stroke="#d5e6c5" strokeOpacity=".2" /><path d="m181 288 97-119" stroke="#c4f469" strokeOpacity=".2" strokeWidth="1.2" strokeDasharray="4 8" /><circle cx="181" cy="288" r="25" stroke="#c4f469" strokeOpacity=".12" /><circle cx="181" cy="288" r="13" stroke="#c4f469" strokeOpacity=".2" /><circle cx="181" cy="288" r="5" fill="#c4f469" /><circle cx="327" cy="117" r="4" fill="#b6cab0" fillOpacity=".45" /></g>
    {!compact && <g stroke="#9eb08f" strokeOpacity=".2"><path d="M551 55h24m-12-12v24M79 369H55m12-12v24" /><circle cx="567" cy="361" r="38" /><path d="M567 313v96m-48-48h96" /></g>}
  </svg>;
}

function dateText(value: string, short = false) {
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return "查看时间未记录";
  return new Intl.DateTimeFormat("zh-CN", { year: short ? undefined : "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }).format(date);
}

function orderedReports(reports: RecentReport[], direction: "desc" | "asc" = "desc") {
  return recentReports(reports).sort((a, b) => (Date.parse(a.viewedAt) - Date.parse(b.viewedAt)) * (direction === "desc" ? -1 : 1));
}

function ReportPreview({ id }: { id: string }) {
  const [failed, setFailed] = useState(false);
  return <div className={`pw-report-preview${failed ? " is-fallback" : ""}`}>
    <div className="pw-preview-fallback"><CourtArtwork compact /><span><ProductIcon name="play" size={15} />训练视频</span></div>
    {/* eslint-disable-next-line @next/next/no-img-element -- Same-origin authenticated artifact must use its gateway URL without an image optimizer. */}
    {!failed && <img key={id} src={`/v1/jobs/${encodeURIComponent(id)}/artifacts/preview.jpg`} alt="" loading="lazy" onError={() => setFailed(true)} />}
    <span className="pw-preview-play"><ProductIcon name="play" size={19} /></span>
  </div>;
}

function ReportCard({ report, onOpen }: { report: RecentReport; onOpen: (id: string) => void }) {
  return <button type="button" className="pw-report-card" onClick={() => onOpen(report.id)} aria-label={`打开训练报告：${report.name || "未命名训练"}`}><ReportPreview key={report.id} id={report.id} /><div className="pw-report-card-body"><div className="pw-report-card-kicker"><span>训练记录</span><ProductIcon name="arrow-up-right" size={16} /></div><h3 title={report.name}>{report.name || "未命名训练"}</h3><p><ProductIcon name="clock" size={13} /><span>最近查看 · {dateText(report.viewedAt)}</span></p></div></button>;
}

function EmptyTraining({ onNewAnalysis, busy = false }: { onNewAnalysis: () => void; busy?: boolean }) {
  return <div className="pw-empty-training"><span className="pw-empty-icon"><ProductIcon name="sessions" size={30} /></span><div><h3>第一份训练记录，从这里开始</h3><p>上传一段训练视频，把值得回看的动作留在这里。</p></div><button type="button" className="pw-button pw-button-secondary" onClick={onNewAnalysis} disabled={busy}>{busy ? "等待当前分析完成" : "上传训练视频"}<ProductIcon name="arrow-right" size={17} /></button></div>;
}

export function TrainingHome({ reports, onOpenReport, onNewAnalysis, onNavigate, currentName, busy = false }: {
  reports: RecentReport[]; onOpenReport: (id: string) => void; onNewAnalysis: () => void;
  onNavigate: (view: WorkspaceView) => void; currentName?: string; busy?: boolean;
}) {
  const recent = useMemo(() => orderedReports(reports), [reports]);
  return <div className="pw-home">
    <section className="pw-home-hero"><div className="pw-hero-copy"><p className="pw-eyebrow"><span />YOUR GAME. IN FOCUS.</p><h1>把每一拍，<br />练得更明白<span>。</span></h1><p className="pw-hero-description">从一段训练视频开始。回看脚步与挥拍，<br className="pw-desktop-break" />发现值得和教练一起复盘的细节。</p><div className="pw-hero-actions"><button type="button" className="pw-button pw-button-accent pw-button-large" onClick={onNewAnalysis} disabled={busy}><ProductIcon name="plus" size={20} />{busy ? "视频正在分析中" : "开始新一次分析"}<ProductIcon name="arrow-up-right" size={18} /></button><button type="button" className="pw-text-button" onClick={() => onNavigate("guide")}>怎样拍得更清楚<ProductIcon name="arrow-right" size={17} /></button></div><p className="pw-hero-footnote">真实画面 · 逐段回看 · 看得清的才有依据</p></div><div className="pw-hero-visual"><CourtArtwork /><span className="pw-court-caption">THE COURT IS YOUR STARTING POINT.</span></div></section>
    {busy && <button type="button" className="pw-running-banner" onClick={() => onNavigate("analysis")}><span className="pw-busy-dot" /><span><strong>你的训练视频正在分析</strong><small>{currentName || "可以进入视频分析查看当前进度。"}</small></span><span className="pw-running-link">查看进度<ProductIcon name="arrow-right" size={17} /></span></button>}
    <section className="pw-recent-section" aria-labelledby="pw-recent-heading"><div className="pw-section-heading"><div><p className="pw-eyebrow">RECENT SESSIONS</p><h2 id="pw-recent-heading">最近的训练<span className="pw-count">{recent.length}</span></h2></div><button type="button" className="pw-text-button" onClick={() => onNavigate("sessions")}>全部记录<ProductIcon name="arrow-right" size={17} /></button></div>{recent.length ? <div className="pw-report-grid">{recent.slice(0, 3).map(report => <ReportCard key={report.id} report={report} onOpen={onOpenReport} />)}</div> : <EmptyTraining onNewAnalysis={onNewAnalysis} busy={busy} />}</section>
    <section className="pw-home-method" aria-labelledby="pw-method-heading"><div className="pw-method-intro"><p className="pw-eyebrow">A CLEARER WAY TO TRAIN</p><h2 id="pw-method-heading">让复盘成为<br />训练的一部分。</h2><button type="button" className="pw-text-button" onClick={() => onNavigate("guide")}>从拍摄开始<ProductIcon name="arrow-right" size={17} /></button></div><div className="pw-method-steps">{[{ n: "01", icon: "camera" as const, title: "留下完整动作", description: "固定机位，让全身和双脚始终留在画面里。" }, { n: "02", icon: "play" as const, title: "回到关键片段", description: "结合原始画面，查看可观察的脚步和挥拍过程。" }, { n: "03", icon: "movement" as const, title: "带着问题再练", description: "把具体片段带给教练，让每次讨论都有画面依据。" }].map(step => <div className="pw-method-step" key={step.n}><span className="pw-step-number">{step.n}</span><ProductIcon name={step.icon} size={23} /><h3>{step.title}</h3><p>{step.description}</p></div>)}</div></section>
  </div>;
}

export function SessionLibrary({ reports, onOpenReport, onNewAnalysis }: { reports: RecentReport[]; onOpenReport: (id: string) => void; onNewAnalysis: () => void }) {
  const [search, setSearch] = useState("");
  const [order, setOrder] = useState<"desc" | "asc">("desc");
  const cleanReports = useMemo(() => orderedReports(reports, order), [reports, order]);
  const matching = useMemo(() => cleanReports.filter(report => report.name.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase())), [cleanReports, search]);
  const searchId = useId(), orderId = useId();
  return <div className="pw-library"><div className="pw-page-heading"><div><p className="pw-eyebrow">YOUR TRAINING LIBRARY</p><h1>每一次回看，都有迹可循。</h1><p>这里保存此浏览器最近查看过的训练报告。</p></div><button type="button" className="pw-button pw-button-secondary" onClick={onNewAnalysis}><ProductIcon name="plus" size={18} />新建分析</button></div><div className="pw-library-toolbar"><div className="pw-search"><ProductIcon name="search" size={19} /><label className="pw-sr-only" htmlFor={searchId}>按训练视频名称搜索</label><input id={searchId} type="search" value={search} onChange={event => setSearch(event.target.value)} placeholder="搜索训练视频名称" autoComplete="off" /></div><div className="pw-sort"><label htmlFor={orderId}>最近查看</label><select id={orderId} value={order} onChange={event => setOrder(event.target.value === "asc" ? "asc" : "desc")}><option value="desc">由新到旧</option><option value="asc">由旧到新</option></select><ProductIcon name="chevron-down" size={15} /></div></div><div className="pw-library-meta" aria-live="polite"><span>{search.trim() ? `找到 ${matching.length} 份记录` : `共 ${cleanReports.length} 份最近记录`}</span>{search.trim() && <button type="button" className="pw-text-button" onClick={() => setSearch("")}>清除搜索<ProductIcon name="close" size={14} /></button>}</div>{!cleanReports.length ? <EmptyTraining onNewAnalysis={onNewAnalysis} /> : matching.length ? <div className="pw-report-grid pw-library-grid">{matching.map(report => <ReportCard key={report.id} report={report} onOpen={onOpenReport} />)}</div> : <div className="pw-search-empty"><span className="pw-empty-icon"><ProductIcon name="search" size={28} /></span><h2>暂时没有找到这份训练</h2><p>试试视频名称中的其他字词，或查看全部记录。</p><button type="button" className="pw-button pw-button-secondary" onClick={() => setSearch("")}>查看全部记录</button></div>}<p className="pw-library-note"><ProductIcon name="clock" size={14} />按最近查看时间记录；这里的时间不是视频拍摄时间。</p></div>;
}

const guideSections: Array<{ icon: IconName; title: string; summary: string; details: ReactNode }> = [
  { icon: "camera", title: "固定机位，留出完整空间", summary: "先让画面稳定，再让动作自由。", details: <><p>用支架或稳固的位置固定手机。横拍或竖拍都可以，重点是运动中头部、持拍手和双脚始终留在画面里。</p><p>开拍前试挥一拍并左右移动，确认不会出画。避免手持跟拍、频繁缩放和镜头切换。</p></> },
  { icon: "frame", title: "让需要观察的部位看得清", summary: "身体清晰可见，比拍得很近更重要。", details: <><p>优先选择光线均匀的位置，避开强逆光。球员不能小到看不清肩、髋和手脚，也不要为了放大画面裁掉脚部。</p><p>侧面或斜后方机位可以作为起点，再根据教练想观察的动作调整。没有一个角度能同时看清所有细节；被身体或球拍遮住的部分需要另外拍摄。</p></> },
  { icon: "movement", title: "保留准备、挥拍和恢复", summary: "一拍的前后，和中间同样值得看。", details: <><p>开始录制后先留一点准备时间，动作结束后继续保留恢复过程。连续对拉可以直接保留原片，不必先裁出一个个挥拍。</p><p>想看启动和制动时，也要拍到来球前后的移动。避免只留下触球附近的几帧，或拼接来自不同时间的动作。</p></> },
  { icon: "upload", title: "使用原视频，保留原来的节奏", summary: "让回放中的时间和真实动作对应。", details: <><p>上传相机或手机直接保存的视频，优先保留原清晰度、速度和画幅。不要添加美颜、骨架、贴纸或遮挡动作的字幕。</p><p>慢动作和变速导出可能改变视频时间关系。如需慢放，请在回放时调整；初次分析优先使用正常速度的原片。</p></> },
];

export function CaptureGuide({ onNewAnalysis }: { onNewAnalysis: () => void }) {
  const [checked, setChecked] = useState<Set<number>>(() => new Set());
  const checklist = ["机位稳定，全身与双脚都在画面里", "光线清楚，肩髋和持拍手尽量没有遮挡", "保留动作前的准备和动作后的恢复", "准备好未经变速和拼接的原视频"];
  return <div className="pw-guide"><div className="pw-page-heading"><div><p className="pw-eyebrow">BETTER FOOTAGE. CLEARER DETAILS.</p><h1>拍得清楚，<br className="pw-mobile-break" />才能看得明白。</h1><p>几个简单的准备，让下一次训练更值得回看。</p></div><span className="pw-guide-heading-icon"><ProductIcon name="camera" size={38} /></span></div><div className="pw-guide-layout"><section className="pw-guide-articles" aria-label="拍摄建议">{guideSections.map((section, index) => <AnimatedDisclosure className="pw-guide-detail" bodyClassName="pw-guide-detail-body" key={section.title} defaultOpen={index === 0} summary={<><span className="pw-guide-icon"><ProductIcon name={section.icon} size={22} /></span><span><small>0{index + 1}</small><strong>{section.title}</strong><span>{section.summary}</span></span><ProductIcon name="plus" size={18} /></>}>{section.details}</AnimatedDisclosure>)}<div className="pw-guide-context"><ProductIcon name="guide" size={21} /><p>视频中的二维测量可帮助定位复核片段。动作是否合理、应该怎样改进，仍需结合具体情境与教练判断。</p></div></section><aside className="pw-checklist"><p className="pw-eyebrow">BEFORE YOU PRESS RECORD</p><h2>开拍前，检查一下。</h2><p className="pw-checklist-intro">勾选只是给自己的拍摄提醒，<br />不会影响视频能否上传。</p><div className="pw-checklist-items">{checklist.map((item, index) => <label key={item} className={checked.has(index) ? "is-checked" : undefined}><input type="checkbox" checked={checked.has(index)} onChange={() => setChecked(previous => { const next = new Set(previous); if (next.has(index)) next.delete(index); else next.add(index); return next; })} /><span className="pw-custom-check"><ProductIcon name="check" size={13} /></span><span>{item}</span></label>)}</div><p className="pw-checklist-count" aria-live="polite">{checked.size === checklist.length ? "准备就绪，带上你的训练视频。" : `已检查 ${checked.size} / ${checklist.length} 项`}</p><button type="button" className="pw-button pw-button-accent" onClick={onNewAnalysis}>开始视频分析<ProductIcon name="arrow-right" size={18} /></button></aside></div></div>;
}
