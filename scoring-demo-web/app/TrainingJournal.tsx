"use client";

import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import {
  TRAINING_JOURNAL_KEY, journalRecordId, readTrainingJournal, writeTrainingJournal,
  visitTrainingReport, updateTrainingRecord, saveTrainingFocus, selectTrainingFollowUp, replayTarget,
  type TrainingFocus, type TrainingJournal, type TrainingRecord, type TrainingRecordPatch, type TrainingVisit,
} from "./lib/training-records";
import "./training-journal.css";
import { useMediaReadiness } from "./lib/use-media-readiness";
import { accountStorageKey } from "./lib/account-storage";
import { getAccountRequestState } from "./lib/auth-client";
import { workspaceHref } from "./lib/workspace-navigation";

export { journalRecordId } from "./lib/training-records";
export type { TrainingFocus } from "./lib/training-records";
const EMPTY: TrainingJournal = { version: 1, records: [] };
const SCOPE = "名称、备注和关注点仅保存在此浏览器；不保存视频，不会同步到其他设备。清除浏览器数据会删除这些记录。";

export function useTrainingJournal(userId = getAccountRequestState().userId) {
  const [data, setData] = useState<TrainingJournal>(EMPTY);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [ready, setReady] = useState(false);
  const memory = useRef(EMPTY), unsaved = useRef(false), unreadable = useRef(false), initialized = useRef(false);
  const reload = useCallback(() => {
    try {
      const next = readTrainingJournal(window.localStorage, userId);
      memory.current = next; setData(next); unreadable.current = false; initialized.current = true; setError("");
    } catch {
      unreadable.current = true;
      setError("无法读取此浏览器的训练记录。原有内容仍保留；请允许网站存储后重试。当前不会覆盖旧记录。");
    }
    setReady(true);
  }, [userId]);
  useEffect(() => {
    // Reading local storage after hydration avoids sharing browser data in SSR.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    reload();
    const changed = (event: StorageEvent) => {
      if (event.key === accountStorageKey(TRAINING_JOURNAL_KEY, userId) && !unsaved.current) reload();
    };
    window.addEventListener("storage", changed);
    return () => window.removeEventListener("storage", changed);
  }, [reload, userId]);

  const commit = useCallback((change: (previous: TrainingJournal) => TrainingJournal, success = "已保存在此浏览器。") => {
    if (unreadable.current) { setError("旧记录暂时无法读取。为保留原有内容，本次没有写入；请允许网站存储后重试。"); return false; }
    let base = memory.current;
    try {
      // Merge with the latest other-tab version unless retaining an unsaved edit.
      if (!unsaved.current || !initialized.current) base = readTrainingJournal(window.localStorage, userId);
    } catch {
      unreadable.current = true; setError("无法读取旧记录，本次没有覆盖。请检查浏览器存储权限后重试。"); return false;
    }
    const next = change(base); memory.current = next; setData(next); initialized.current = true;
    try {
      writeTrainingJournal(window.localStorage, next, userId);
      unsaved.current = false; setError(""); setNotice(success); return true;
    } catch {
      unsaved.current = true; setNotice("");
      setError("尚未保存：浏览器存储不可用或空间不足。编辑暂留在当前页面，请勿关闭；恢复存储后点击“重试保存”。"); return false;
    }
  }, [userId]);
  const visitReport = useCallback((visit: TrainingVisit) => {
    commit(previous => visitTrainingReport(previous, visit), "");
    return journalRecordId(visit.source, visit.jobId, visit.sourceId);
  }, [commit]);
  const updateRecord = useCallback((id: string, patch: TrainingRecordPatch, seed?: TrainingRecord) => commit(previous => updateTrainingRecord(
    seed && !previous.records.some(record => record.id === id) ? { ...previous, records: [...previous.records, seed] } : previous, id, patch)), [commit]);
  const saveFocus = useCallback((id: string, focus: TrainingFocus) => commit(previous => saveTrainingFocus(previous, id, focus), "关注点已保存在此浏览器，下次分析可以继续关注。"), [commit]);
  const selectFollowUp = useCallback((recordId?: string, focusId?: string) => commit(previous => selectTrainingFollowUp(previous, recordId, focusId), recordId ? "已选为下次分析关注点；提交新视频后自动关联。" : "已取消下次分析的关联。"), [commit]);
  const retrySave = useCallback(() => {
    if (unreadable.current) { reload(); return; }
    commit(previous => previous);
  }, [commit, reload]);
  return { records: data.records, pendingFocus: data.pendingFocus, error, notice, ready, visitReport, updateRecord, saveFocus, selectFollowUp, retrySave };
}
export type TrainingJournalController = ReturnType<typeof useTrainingJournal>;

function JournalStatus({ journal }: { journal: TrainingJournalController }) {
  return <>{journal.error ? <div className="tj-error" role="alert"><p>{journal.error}</p><button type="button" onClick={journal.retrySave}>重试保存</button></div> : journal.notice ? <p className="tj-notice" role="status">{journal.notice}</p> : null}</>;
}
function dateText(value: string) {
  return new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date(value));
}
function RecordEditor({ record, journal }: { record: TrainingRecord; journal: TrainingJournalController }) {
  const [name, setName] = useState(record.name), [theme, setTheme] = useState(record.theme), [notes, setNotes] = useState(record.notes);
  const id = useId();
  return <form className="tj-editor" onSubmit={event => { event.preventDefault(); journal.updateRecord(record.id, { name, theme, notes }, record); }}>
    {record.originalName && <p className="tj-wide tj-subtle">原文件：{record.originalName}</p>}
    <label htmlFor={`${id}-name`}>训练名称<input id={`${id}-name`} value={name} maxLength={160} required onChange={event => setName(event.target.value)} placeholder="例如：周三底线移动复盘" /></label>
    <label htmlFor={`${id}-theme`}>练习主题<input id={`${id}-theme`} value={theme} maxLength={120} onChange={event => setTheme(event.target.value)} placeholder="例如：启动与回位" /></label>
    <label className="tj-wide" htmlFor={`${id}-notes`}>自己的观察与备注<textarea id={`${id}-notes`} value={notes} maxLength={2000} rows={3} onChange={event => setNotes(event.target.value)} placeholder="记录看到了什么、下次想继续确认什么。" /></label>
    <div className="tj-wide"><button type="submit">保存训练记录</button></div>
  </form>;
}

export function TrainingFollowUpPicker({ journal }: { journal: TrainingJournalController }) {
  const id = useId();
  const choices = journal.records.flatMap(record => record.focuses.map(focus => ({ record, focus, value: JSON.stringify([record.id, focus.id]) })));
  const selected = journal.pendingFocus ? JSON.stringify([journal.pendingFocus.recordId, journal.pendingFocus.focus.id]) : "";
  return <section className="tj-next" aria-label="下次分析关注点"><label htmlFor={id}>这次继续关注什么？<span>可选</span></label>
    <select id={id} value={choices.some(item => item.value === selected) ? selected : ""} onChange={event => { if (!event.target.value) journal.selectFollowUp(); else { const [recordId, focusId] = JSON.parse(event.target.value) as [string, string]; journal.selectFollowUp(recordId, focusId); } }}>
      <option value="">从本次视频开始，不关联旧关注点</option>{choices.map(({ record, focus, value }) => <option key={value} value={value}>{record.name} · {focus.title}</option>)}
    </select>
    {journal.pendingFocus ? <p className="tj-pending-question">{journal.pendingFocus.focus.question}<small>提交新视频后会把这个问题带入新记录，供前后回看；不会改变模型推理或评分，也不会据此自动判断进步。</small></p> : <p>在报告中保存一个关注点，下一次上传时就能接着复盘。</p>}
    <JournalStatus journal={journal} />
  </section>;
}

function ComparisonVideo({ label, record, focus }: { label: string; record: TrainingRecord; focus: TrainingFocus }) {
  const target = replayTarget(record, focus), video = useRef<HTMLVideoElement>(null);
  const [state, setState] = useState<"loading" | "ready" | "failed">("loading");
  const readiness = useMediaReadiness(video, `${target?.src ?? ""}:${target?.seconds ?? ""}`);
  if (!target) return null;
  return <div className="tj-comparison-video"><h4>{label} · {record.name}</h4><p>{focus.title}</p>
    {/* eslint-disable-next-line jsx-a11y/media-has-caption -- The verified annotated source replay is exported without audio; the adjacent text supplies the review question. */}
    <video key={readiness.key} ref={video} controls playsInline preload="metadata" aria-label={`${label}训练片段`} src={target.src}
      onLoadedMetadata={() => { const element = video.current; if (!element || !Number.isFinite(element.duration) || target.seconds >= element.duration) { setState("failed"); return; } element.currentTime = target.seconds; setState("ready"); }}
      onError={() => setState("failed")} />
    <button type="button" disabled={state !== "ready"} onClick={() => { if (video.current) { try { video.current.currentTime = target.seconds; } catch { setState("failed"); } } }}>定位这次关注的片段</button>
    {(state === "failed" || readiness.state === "failed" || readiness.state === "timeout") && <div role="status"><p>{readiness.state === "timeout" ? "训练片段读取超时。" : "该片段目前无法读取或超出可回放范围。"}关注点仍已保留。</p><button type="button" onClick={() => { setState("loading"); readiness.retry(); }}>重新读取训练片段</button></div>}
    {state === "loading" && readiness.state === "loading" && <p role="status">正在确认视频是否可回放…</p>}
  </div>;
}
function FollowUpComparison({ record, journal, suggestions, onOpenReport }: { record: TrainingRecord; journal: TrainingJournalController; suggestions: TrainingFocus[]; onOpenReport?: (jobId: string) => void }) {
  const [open, setOpen] = useState(false);
  const previous = journal.records.find(item => item.id === record.followUp?.recordId);
  const previousFocus = record.followUp?.focus;
  const currentFocus = [...record.focuses, ...suggestions].find(focus => replayTarget(record, focus));
  if (!previousFocus) return null;
  const canCompare = previous && replayTarget(previous, previousFocus) && currentFocus;
  return <div className="tj-follow-up"><span className="tj-eyebrow">接着上次的问题看</span><h3>{previousFocus.title}</h3><p>{previousFocus.question}</p><p className="tj-subtle">来自：{previous?.name || "之前保存的训练"}。请先确认两段是同类动作，并考虑机位、练习条件和可见范围；这里不自动评判进步。</p>
    {canCompare ? <><button type="button" aria-expanded={open} onClick={() => setOpen(value => !value)}>{open ? "收起前后片段" : "并排回看前后片段"}</button>{open && <div className="tj-comparison"><ComparisonVideo key={`previous:${previous.id}`} label="上一次" record={previous} focus={previousFocus} /><ComparisonVideo key={`current:${record.id}`} label="这一次" record={record} focus={currentFocus} /></div>}</>
      : <p className="tj-subtle">暂不提供并排定位：需要两次都有已保存的片段位置及可确认时间对应关系的视频。仍可保留问题，分别回看报告。</p>}
    {previous?.source === "online" && previous.jobId && onOpenReport && <button className="tj-quiet" type="button" onClick={() => onOpenReport(previous.jobId!)}>打开上次报告</button>}
    {previous?.source === "imported" && <p className="tj-subtle">上次记录来自离线导入；再次导入原备份即可查看，不会尝试读取同名在线任务。</p>}
  </div>;
}

export function TrainingJournalPanel({ journal, recordId, suggestions = [], showSuggestions = true, onSeek, onOpenReport }: { journal: TrainingJournalController; recordId?: string; suggestions?: TrainingFocus[]; showSuggestions?: boolean; onSeek?: (timestampMs: number) => void; onOpenReport?: (jobId: string) => void }) {
  const record = journal.records.find(item => item.id === recordId);
  const [customTitle, setCustomTitle] = useState(""), [customQuestion, setCustomQuestion] = useState("");
  const id = useId();
  if (!record) return journal.error ? <section className="training-journal"><JournalStatus journal={journal} /></section> : null;
  return <section className="training-journal" id="training-journal" aria-labelledby={`${id}-heading`}>
    <div className="tj-heading"><div><span className="tj-eyebrow">留给下一次训练</span><h2 id={`${id}-heading`}>保存这次关注点</h2></div><span className="tj-local">仅此浏览器</span></div>
    <p className="tj-scope">{SCOPE}</p><JournalStatus journal={journal} />
    <FollowUpComparison key={record.id} record={record} journal={journal} suggestions={suggestions} onOpenReport={onOpenReport} />
    {showSuggestions && suggestions.length > 0 && <div className="tj-suggestions">{suggestions.map(focus => {
      const saved = record.focuses.some(item => item.id === focus.id);
      return <article key={focus.id}><h3>{focus.title}</h3><p>{focus.question}</p><div className="tj-actions">{focus.timestampMs !== undefined && onSeek && <button className="tj-quiet" type="button" onClick={() => onSeek(focus.timestampMs!)}>回看这段</button>}<button type="button" disabled={saved} onClick={() => journal.saveFocus(record.id, focus)}>{saved ? "已加入关注点" : "保存这个关注点"}</button></div></article>;
    })}</div>}
    {record.focuses.length > 0 && <div className="tj-saved"><h3>已经留下的问题</h3>{record.focuses.map(focus => <div className="tj-saved-focus" key={focus.id}><div><strong>{focus.title}</strong><p>{focus.question}</p></div><div className="tj-actions">{focus.timestampMs !== undefined && onSeek && <button className="tj-quiet" type="button" onClick={() => onSeek(focus.timestampMs!)}>回看已存片段</button>}<button type="button" aria-pressed={journal.pendingFocus?.recordId === record.id && journal.pendingFocus.focus.id === focus.id} onClick={() => journal.selectFollowUp(record.id, focus.id)}>{journal.pendingFocus?.recordId === record.id && journal.pendingFocus.focus.id === focus.id ? "下次继续关注 · 已选择" : "下次继续关注"}</button></div></div>)}</div>}
    <details className="tj-details"><summary>写下自己的复盘问题</summary><form className="tj-editor" onSubmit={event => { event.preventDefault(); if (journal.saveFocus(record.id, { id: `personal:${crypto.randomUUID()}`, title: customTitle, question: customQuestion })) { setCustomTitle(""); setCustomQuestion(""); } }}><label htmlFor={`${id}-custom-title`}>关注点名称<input id={`${id}-custom-title`} value={customTitle} maxLength={160} required onChange={event => setCustomTitle(event.target.value)} placeholder="例如：回位过程" /></label><label className="tj-wide" htmlFor={`${id}-custom-question`}>下一次想确认的问题<textarea id={`${id}-custom-question`} value={customQuestion} maxLength={1600} required rows={3} onChange={event => setCustomQuestion(event.target.value)} placeholder="写下希望通过画面确认的事情。" /></label><div><button type="submit">保存自己的问题</button></div></form></details>
    <details className="tj-details"><summary>训练名称、主题与备注</summary><RecordEditor key={record.id} record={record} journal={journal} /></details>
  </section>;
}

export type PersonalHistoryControls = { total: number; offset: number; limit: number; loading: boolean; error: string;
  deleting: string | null; onPage: (offset: number) => void; onRefresh: () => void; onDelete: (id: string) => void };
const JOB_STATUS = { queued: "排队中", running: "分析中", succeeded: "分析完成", failed: "分析失败", cancelled: "已取消" };
export function TrainingJournalLibrary({ journal, onOpenReport, onNewAnalysis, history, activeJobId, protectCurrent = false, loadedImportId }: {
  journal: TrainingJournalController; onOpenReport: (id: string) => void; onNewAnalysis: () => void; history?: PersonalHistoryControls;
  activeJobId?: string | null; protectCurrent?: boolean; loadedImportId?: string;
}) {
  const [search, setSearch] = useState("");
  const id = useId();
  const matching = useMemo(() => journal.records.filter(record => `${record.name} ${record.theme} ${record.notes} ${record.focuses.map(item => `${item.title} ${item.question}`).join(" ")}`.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase())).sort((a, b) => Date.parse(b.viewedAt) - Date.parse(a.viewedAt)), [journal.records, search]);
  return <section className="training-journal tj-library studio-library" aria-labelledby={`${id}-heading`}>
    <header className="studio-title"><h1 id={`${id}-heading`}>训练记录</h1></header>
    <JournalStatus journal={journal} />
    <div className="studio-library-tools">
      <label className="tj-search" htmlFor={`${id}-search`}>{history ? "搜索本页" : "搜索记录"}
        <input id={`${id}-search`} type="search" value={search} onChange={event => setSearch(event.target.value)} placeholder="按名称、主题或关注点查找" />
      </label>
      {history && <div className="tj-history-controls"><p role="status">{history.loading ? "正在读取账号训练记录…" : `共 ${history.total} 条在线任务`}</p><button type="button" onClick={history.onRefresh} disabled={history.loading}>刷新</button></div>}
    </div>
    {history?.error && <p className="tj-error" role="alert">{history.error}</p>}
    {search && <p className="tj-count" aria-live="polite">{`找到 ${matching.length} 份记录`}</p>}
    <div className="studio-records">
      {matching.length > 0 && <div className="studio-record-columns" aria-hidden="true"><span>训练名称</span><span>时间</span><span>状态</span><span>操作</span></div>}
      {matching.map(record => {
        const time = record.completedAt || record.submittedAt || record.viewedAt;
        const label = record.completedAt ? "分析完成" : record.submittedAt ? "提交时间" : "最近查看";
        const inProgress = record.status === "queued" || record.status === "running";
        const actionLabel = inProgress ? "查看分析进度" : record.status === "failed" || record.status === "cancelled" ? "查看任务" : "打开训练报告";
        return <article className="studio-record" key={record.id}>
          <div className="studio-record-line">
            <div className="studio-record-name"><h2>{record.name}</h2>{record.theme && <p>{record.theme}</p>}</div>
            <time dateTime={time}><small>{label}</small>{dateText(time)}</time>
            <span className="studio-record-state">{record.status ? JOB_STATUS[record.status] : record.source === "imported" ? "离线导入" : "在线记录"}</span>
            {record.source === "online" && record.jobId ? (
              protectCurrent && record.jobId !== activeJobId
                ? <a className="studio-record-open" href={workspaceHref("analysis", record.jobId)} target="_blank" rel="noopener noreferrer">在新标签页查看</a>
                : <button type="button" className="studio-record-open" onClick={() => onOpenReport(record.jobId!)}>{actionLabel}</button>
            ) : <button type="button" className="studio-record-open" disabled={protectCurrent} onClick={() => onOpenReport(record.id)}>
              {loadedImportId === record.id ? "继续复盘" : "选择报告备份"}
            </button>}
          </div>
          <details className="studio-record-details"><summary>备注与管理{record.focuses.length > 0 && <span>{record.focuses.length} 个关注点</span>}</summary>
            {record.source === "imported" && <p className="tj-subtle">离线记录保存在此浏览器，不会自动连接在线任务。</p>}
            {record.notes && <p className="tj-record-notes">{record.notes}</p>}
            {record.followUp && <p className="tj-subtle">延续关注 · {record.followUp.focus.title}</p>}
            {record.focuses.length > 0 && <ul className="tj-record-focuses">{record.focuses.map(focus =>
              <li key={focus.id}><strong>{focus.title}</strong><p>{focus.question}</p><button className="tj-quiet" type="button" aria-pressed={journal.pendingFocus?.recordId === record.id && journal.pendingFocus.focus.id === focus.id} onClick={() => journal.selectFollowUp(record.id, focus.id)}>{journal.pendingFocus?.recordId === record.id && journal.pendingFocus.focus.id === focus.id ? "已选为下次关注点" : "下次继续关注"}</button></li>)}</ul>}
            <RecordEditor record={record} journal={journal} />
            {history && record.source === "online" && record.jobId && record.status && !inProgress && <button type="button" className="tj-quiet" disabled={history.deleting !== null} onClick={() => history.onDelete(record.jobId!)}>{history.deleting === record.jobId ? "正在删除…" : "删除在线记录"}</button>}
          </details>
        </article>;
      })}
    </div>
    {journal.ready && !history?.loading && !history?.error && !matching.length && <div className="tj-empty">
      <h2>{search ? "没有找到匹配的训练" : "还没有训练记录"}</h2>
      <p>{search ? "换个关键词，或清除搜索。" : protectCurrent ? "提交的视频会出现在这里。" : "提交一段视频后，在这里看进度和报告。"}</p>
      {search ? <button type="button" onClick={() => setSearch("")}>清除搜索</button> : <button type="button" onClick={onNewAnalysis}>{protectCurrent ? "返回视频分析" : "选择训练视频"}</button>}
    </div>}
    {history && history.total > history.limit && <nav className="tj-history-controls" aria-label="训练记录分页"><button type="button" disabled={history.loading || history.offset === 0} onClick={() => history.onPage(Math.max(0, history.offset - history.limit))}>上一页</button><span>第 {Math.floor(history.offset / history.limit) + 1} 页</span><button type="button" disabled={history.loading || history.offset + history.limit >= history.total} onClick={() => history.onPage(history.offset + history.limit)}>下一页</button></nav>}
    <p className="tj-scope">训练任务随账号同步。自定义名称、备注和关注点存于此浏览器。清除浏览器数据会删除这些内容。</p>
  </section>;
}
