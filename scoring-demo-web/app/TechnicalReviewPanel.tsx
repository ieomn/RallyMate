"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { DemoResultResponse } from "./lib/api-types";
import { apiClient, RallyMateApiError } from "./lib/api-client";
import type { ReviewRule, RuleReviewCatalog } from "./lib/rule-review";
import { footworkFeatureText } from "./lib/footwork-review";
import { replayMoment } from "./lib/training-report";
import { reviewInterval, sourceReviewCoverage, sourceReviewWindows, sourceWindowDisplay, technicalReviewError, TECHNICAL_GRADES, type TechnicalGrade, type TechnicalReviewEntry, type TechnicalReviewInput, type TechnicalReviewLedger } from "./lib/technical-review";
import "./technical-review.css";

type Draft = { grade: TechnicalGrade | "observed" | "not_observed" | "unassessable" | ""; observability: "observable" | "partial" | "unobservable" | "";
  start: string; end: string; player: string; event: string; reason: string; nextStep: string; reviewId?: string; reviewer?: TechnicalReviewEntry };
const seconds = (ms: number) => String(Number((ms / 1000).toFixed(3)));
const formatTime = (ms: number) => `${seconds(ms)} 秒`;
const newId = () => crypto.randomUUID();
const emptyDraft = (): Draft => ({ grade: "", observability: "", start: "", end: "", player: "", event: "", reason: "", nextStep: "" });
const reviewLabel = (entry: TechnicalReviewEntry) => entry.status === "graded" ? `${entry.grade} 级` : entry.status === "observed" ? "已观察到" : entry.status === "not_observed" ? "未观察到" : "无法评价";
const draftOf = (entry: TechnicalReviewEntry): Draft => ({ grade: entry.status === "graded" ? entry.grade! : entry.status, observability: entry.observability,
  start: seconds(entry.start_ms), end: seconds(entry.end_ms), player: String(entry.player_id), event: entry.event_id ?? "", reason: entry.reason_zh,
  nextStep: entry.next_step_zh, reviewId: entry.review_id, reviewer: entry });

export default function TechnicalReviewPanel({ result, catalog, initialIndicatorId }: {
  result: DemoResultResponse; catalog: RuleReviewCatalog; initialIndicatorId?: string;
}) {
  const jobId = result.job_id ?? "";
  const jobAvailable = /^[a-zA-Z0-9_-]{8,80}$/.test(jobId);
  const first = catalog.rules.find(rule => rule.id === initialIndicatorId) ?? catalog.rules.find(rule => sourceReviewWindows(result, rule.id).length) ?? catalog.rules.find(rule => rule.implementation.kind === "related_2d_measurement") ?? catalog.rules[0];
  const [selectedId, setSelectedId] = useState(first?.id ?? "");
  const [mode, setMode] = useState<"indicator" | "visual_rule">("indicator");
  const [techniqueId, setTechniqueId] = useState(catalog.visual_techniques?.find(technique => technique.id === "serve")?.id ?? catalog.visual_techniques?.[0]?.id ?? "");
  const [eventCode, setEventCode] = useState(first?.event_code ?? "");
  const [query, setQuery] = useState("");
  const [ledger, setLedger] = useState<TechnicalReviewLedger | null>(null);
  const [loadState, setLoadState] = useState<"loading" | "ready" | "error">(jobAvailable ? "loading" : "error");
  const [reload, setReload] = useState(0);
  const [message, setMessage] = useState("");
  const [saving, setSaving] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [reviewerName, setReviewerName] = useState("");
  const [reviewerRole, setReviewerRole] = useState<"coach" | "reviewer">("reviewer");
  const reviewerId = useRef("");
  const pendingMutation = useRef<{ signature: string; payload: TechnicalReviewInput } | null>(null);
  const [showWindows, setShowWindows] = useState(false);
  const [windowMode, setWindowMode] = useState<"measured" | "unavailable" | "all">("measured");
  useEffect(() => {
    if (!jobAvailable) return;
    const controller = new AbortController();
    apiClient.getTechnicalReview(jobId, controller.signal).then(value => {
      if (!controller.signal.aborted) { setLedger(value); setLoadState("ready"); }
    }).catch(error => {
      if (!controller.signal.aborted) { setLoadState("error"); setMessage(error instanceof Error ? error.message : "暂时无法读取已保存评审。"); }
    });
    return () => controller.abort();
  }, [jobAvailable, jobId, reload]);
  const visualMode = mode === "visual_rule";
  const visualTechnique = catalog.visual_techniques?.find(technique => technique.id === techniqueId);
  const filtered = useMemo(() => {
    const targets: ReviewRule[] = mode === "indicator" ? catalog.rules.filter(rule => !eventCode || rule.event_code === eventCode)
      : (ledger?.visual_rules ?? []).filter(rule => rule.technique_ids.includes(techniqueId)).map(rule => ({
        id: rule.visual_rule_id, name: rule.source_text, definition: rule.source_text, event_code: "", event_name: visualTechnique?.name ?? "技术要点",
        stage: visualTechnique?.phases.find(phase => phase.id === rule.stage_id)?.name ?? "原文阶段", grades: [], sources: [], calculation: "",
        required_points: "", unevaluable: "看不清或无法判断时，保留无法评价，不把缺少画面当成动作未完成。",
        implementation: { kind: "not_implemented", features: [], note: "这项是原文视觉要求，只记录观察结果，不产生 A～E、百分制或扣分。" },
      }));
    return targets.filter(rule => `${rule.id} ${rule.name} ${rule.stage}`.toLowerCase().includes(query.trim().toLowerCase()));
  }, [catalog, eventCode, query, mode, ledger, techniqueId, visualTechnique]);
  const selected = filtered.find(rule => rule.id === selectedId) ?? filtered[0];
  const draft = selected ? drafts[selected.id] ?? emptyDraft() : emptyDraft();
  const ruleGate = ledger?.rules.find(rule => rule.indicator_id === selected?.id);
  const visualRule = ledger?.visual_rules.find(rule => rule.visual_rule_id === selected?.id);
  const sourceWindows = selected && !visualMode ? sourceReviewWindows(result, selected.id) : [];
  const coverage = sourceReviewCoverage(result, selected?.id ?? "");
  const displayWindows = sourceWindowDisplay(sourceWindows, windowMode);
  const interval = reviewInterval(draft.start, draft.end, ledger?.video.duration_ms ?? null);
  const activeWindow = sourceWindows.find(window => window.start_ms === interval?.start_ms && window.end_ms === interval?.end_ms
    && (draft.player === "" || window.person_track_id === Number(draft.player)));
  const saved = ledger?.entries.filter(entry => visualMode ? entry.visual_rule_id === selected?.id : entry.indicator_id === selected?.id) ?? [];
  const currentEntries = ledger?.entries.filter(entry => entry.source_binding_current) ?? [];
  const graded = currentEntries.filter(entry => entry.status === "graded");
  const eventOptions = ledger?.events.filter(event => event.event_code === selected?.event_code && (draft.player === "" || event.player_id === Number(draft.player))) ?? [];
  const events = [...new Map(catalog.rules.map(rule => [rule.event_code, rule.event_name])).entries()];
  const gradeDefinition = selected?.grades.find(grade => grade.grade === draft.grade)?.definition;
  const latest = saved.filter(entry => entry.source_binding_current).at(-1);
  const changeDraft = (values: Partial<Draft>) => {
    if (!selected) return;
    setDrafts(previous => ({ ...previous, [selected.id]: { ...(previous[selected.id] ?? emptyDraft()), ...values } }));
    setMessage("");
  };
  const selectRule = (id: string) => { setSelectedId(id); setShowWindows(false); setWindowMode("measured"); setMessage(""); };

  async function saveReview() {
    if (!selected || !ledger || saving) return;
    if (!interval || !draft.grade || !draft.observability) { setMessage("请先选择时段、可判断程度以及等级或无法评价。"); return; }
    reviewerId.current ||= `reviewer-${newId()}`;
    const previous = draft.reviewer;
    const fields = { expected_revision: ledger.revision, source_sha256: ledger.source_sha256, artifact_context_sha256: ledger.artifact_context_sha256,
      source_reference_version: ledger.source_reference_version, source_reference_sha256: ledger.source_reference_sha256,
      ...(draft.reviewId ? { review_id: draft.reviewId } : {}), target_kind: mode,
      indicator_id: visualMode ? null : selected.id, visual_rule_id: visualMode ? selected.id : null, player_id: Number(draft.player),
      event_id: draft.event || null, event_source: draft.event ? "system_event" as const : "manual_interval" as const, ...interval,
      reviewer_id: previous?.reviewer_id ?? reviewerId.current, reviewer_name: previous?.reviewer_name ?? reviewerName.trim(), reviewer_role: previous?.reviewer_role ?? reviewerRole,
      observability: draft.observability, status: visualMode ? draft.grade as "observed" | "not_observed" | "unassessable" : draft.grade === "unassessable" ? "unassessable" as const : "graded" as const,
      grade: !visualMode && TECHNICAL_GRADES.includes(draft.grade as TechnicalGrade) ? draft.grade as TechnicalGrade : null, reason_zh: draft.reason.trim(), next_step_zh: draft.nextStep.trim() };
    const signature = JSON.stringify(fields);
    const payload = pendingMutation.current?.signature === signature ? pendingMutation.current.payload : { ...fields, mutation_id: newId() };
    const error = technicalReviewError(payload, ledger);
    if (error || draft.player === "") { setMessage(error ?? "请选择视频中的评审人物。"); return; }
    pendingMutation.current = { signature, payload };
    setSaving(true); setMessage("");
    try {
      const next = await apiClient.saveTechnicalReview(jobId, payload);
      setLedger(next); pendingMutation.current = null;
      const savedEntry = next.entries.find(entry => entry.review_id === payload.review_id)
        ?? next.entries.find(entry => !ledger.entries.some(old => old.review_id === entry.review_id) && (entry.indicator_id === selected.id || entry.visual_rule_id === selected.id) && entry.reviewer_id === payload.reviewer_id);
      if (savedEntry) setDrafts(previous => ({ ...previous, [selected.id]: draftOf(savedEntry) }));
      setMessage("已保存。等级、理由和时段已绑定到这段视频，修订记录会一并保留。");
    } catch (error) {
      if (error instanceof RallyMateApiError && error.status === 409) {
        pendingMutation.current = null;
        try { setLedger(await apiClient.getTechnicalReview(jobId)); setMessage("视频、动作记录或评审版本已更新，已重新读取。草稿已保留，请重新核对人物、时段和来源后保存。"); }
        catch { setMessage("评审版本已更新，暂时无法重新读取；草稿已保留，请点重新读取后再保存。"); setLoadState("error"); }
      } else setMessage(`${error instanceof Error ? error.message : "保存没有成功。"} 草稿已保留，可再次保存。`);
    } finally { setSaving(false); }
  }
  async function exportReview() {
    if (!ledger || exporting) return;
    setExporting(true);
    try {
      const data = await apiClient.exportTechnicalReview(jobId);
      if (data.job_id !== jobId || data.source_sha256 !== ledger.source_sha256) throw new Error("导出记录与当前视频不一致。");
      const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
      const anchor = document.createElement("a"); anchor.href = url; anchor.download = `RallyMate-技术评审-${jobId}.json`; anchor.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      setMessage("已导出评审与修订记录，可交给教练复核并用于下一轮标注整理。");
    } catch (error) { setMessage(error instanceof Error ? error.message : "导出失败，请稍后重试。"); }
    finally { setExporting(false); }
  }

  return <section id="technical-review" className="technical-review" aria-labelledby="technical-review-title">
    <div className="tr-heading"><div><h2 id="technical-review-title">按原文评审这次动作</h2><p>看对应片段，给出等级或技术要点观察，并写下判断依据。</p></div><button type="button" onClick={exportReview} disabled={!ledger?.entries.length || exporting}>{exporting ? "正在导出…" : "导出评审"}</button></div>
    <div className="tr-summary"><div><strong>{currentEntries.length}</strong><span>条当前来源评审</span></div><div><strong>{graded.length}</strong><span>条人工等级</span></div><p>等级由评审人逐项录入。原文没有百分制换算，系统不把证据参考分转成技术分。</p></div>
    {loadState !== "ready" && <div className="tr-service" role="status"><p>{!jobAvailable ? "这份结果没有可关联的视频任务。可核对原文；录入评审需要连接实际视频任务。" : loadState === "loading" ? "正在读取这段视频的评审记录…" : "暂未连接到该视频的评审记录，下面的草稿不会自动保存。"}</p>{jobAvailable && loadState === "error" && <button type="button" onClick={() => { setLoadState("loading"); setReload(value => value + 1); }}>重新读取</button>}</div>}
    <div className="tr-mode" role="group" aria-label="评审内容"><button type="button" aria-pressed={!visualMode} onClick={() => { setMode("indicator"); selectRule(""); setQuery(""); }}>A～E 指标 · 298 项</button><button type="button" aria-pressed={visualMode} onClick={() => { setMode("visual_rule"); selectRule(""); setQuery(""); }}>技术要点 · 246 项</button></div>
    <div className="tr-filters"><label><span>动作</span>{visualMode ? <select value={techniqueId} onChange={event => { setTechniqueId(event.target.value); setQuery(""); }}>{catalog.visual_techniques?.map(technique => <option key={technique.id} value={technique.id}>{technique.name}</option>)}</select> : <select value={eventCode} onChange={event => { setEventCode(event.target.value); setQuery(""); }}>{events.map(([id, name]) => <option key={id} value={id}>{name}</option>)}</select>}</label><label><span>{visualMode ? "查找技术要点" : "查找指标"}</span><input value={query} onChange={event => setQuery(event.target.value)} placeholder="名称或原文编号" /></label><label className="tr-indicator-select"><span>{visualMode ? "原文要点" : "原文指标"} · {filtered.length} 项</span><select value={selected?.id ?? ""} onChange={event => selectRule(event.target.value)}>{filtered.length ? filtered.map(rule => <option key={rule.id} value={rule.id}>{rule.name} · {rule.id}</option>) : <option value="">没有匹配的内容</option>}</select></label></div>
    {!selected ? <p className="tr-empty">没有匹配的原文指标。请清除关键词或更换动作。</p> : <div className="tr-layout">
      <div className="tr-evidence"><header><p>{selected.event_name} · {visualMode ? selected.stage : selected.id}</p><h3>{selected.name}</h3></header>
        {!visualMode && ruleGate && !ruleGate.manual_grading_allowed && <div className="tr-optional"><strong>原文待修订，只能记录无法评价</strong>{ruleGate.blockers.map(blocker => <p key={blocker.code}>{blocker.message}</p>)}<p>下方保留原文，供核对；不能把其中的冲突内容当作已确认的要求。</p></div>}
        {!visualMode && <p className="tr-definition">{selected.definition}</p>}
        {visualRule?.optional_in_source && <p className="tr-optional">原文明确这个阶段可以没有；未观察到也不会产生扣分。</p>}
        {latest && <div className="tr-saved-grade"><strong>{reviewLabel(latest)}</strong><div><span>最近一次人工评审 · {latest.reviewer_name}</span><p>{latest.reason_zh}</p><p>{formatTime(latest.start_ms)}–{formatTime(latest.end_ms)}{latest.next_step_zh ? ` · 下一步：${latest.next_step_zh}` : ""}</p></div></div>}
        <h4>{visualMode ? "对照视频核验" : "这项实际测到了什么"}</h4>
        {sourceWindows.length ? <>
          <p className="tr-window-count"><strong>{coverage.measured} / {coverage.countsScope === "all" ? coverage.total : coverage.returned}</strong> 个{coverage.countsScope === "all" ? "候选" : "已返回"}片段有测量，{coverage.unavailable} 个未测得。</p>
          <div className="tr-window-filters" role="group" aria-label="选择测量片段范围">
            <button type="button" aria-pressed={windowMode === "measured"} onClick={() => { setWindowMode("measured"); setShowWindows(false); }}>有测量</button>
            <button type="button" aria-pressed={windowMode === "unavailable"} onClick={() => { setWindowMode("unavailable"); setShowWindows(false); }}>未测得片段</button>
            <button type="button" aria-pressed={windowMode === "all"} onClick={() => { setWindowMode("all"); setShowWindows(false); }}>全部已返回 · {coverage.returned}</button>
          </div>
          {!displayWindows.length && <p className="tr-note">{windowMode === "measured" ? "本报告返回的片段没有这项可用测量，可查看未测得原因后人工复核。" : "已返回片段中没有这个范围的记录。"}</p>}
          <div className="tr-windows">{(showWindows ? displayWindows : displayWindows.slice(0, 3)).map(window => {
            const hasMeasured = window.measurements.some(measurement => measurement.status === "measured");
            const missingReasons = [...new Set(window.measurements.map(measurement => measurement.reason_zh).filter(Boolean))];
            return <article key={`${window.event_id}-${window.start_ms}`}>
              <div><strong>{formatTime(window.start_ms)}–{formatTime(window.end_ms)}</strong><button type="button" disabled={saving || !!draft.reviewId || !ledger} onClick={() => { const event = ledger?.events.find(event => event.event_id === window.event_id && event.player_id === window.person_track_id); changeDraft({ start: seconds(window.start_ms), end: seconds(window.end_ms), player: window.person_track_id === null ? draft.player : String(window.person_track_id), event: event?.event_id ?? "" }); replayMoment({ startMs: window.start_ms }, jobId); }}>回看并使用这段</button></div>
              {!hasMeasured ? <div className="tr-window-unavailable"><strong>这段未测得</strong><p>{window.measurements.map(item => item.label_zh).join("、")}</p>{missingReasons.map(reason => <p key={reason}>{reason}</p>)}</div> : window.measurements.map(measurement => <p key={measurement.feature_name}><span>{measurement.label_zh}</span><strong>{measurement.status === "measured" ? footworkFeatureText(measurement.value, measurement.unit) : "未测得"}</strong>{measurement.status === "unavailable" ? <span>{measurement.reason_zh}</span> : measurement.measurement_window ? <span className="tr-measurement-window">{measurement.measurement_window.start_ms === measurement.measurement_window.end_ms ? "同帧衔接" : measurement.measurement_window.start_ms < window.start_ms || measurement.measurement_window.end_ms > window.end_ms ? "跨事件测量区间" : "实际测量区间"}：{formatTime(measurement.measurement_window.start_ms)}–{formatTime(measurement.measurement_window.end_ms)}<button type="button" onClick={() => replayMoment({ startMs: measurement.measurement_window!.start_ms }, jobId)}>回看此测量</button></span> : <span>未提供可核验的具体计算时段。</span>}</p>)}
            </article>;
          })}</div>
          {displayWindows.length > 3 && <button type="button" className="tr-text-button" onClick={() => setShowWindows(value => !value)}>{showWindows ? "收起片段" : `展开这组 ${displayWindows.length} 个片段`}</button>}
        </> : <p className="tr-note">{visualMode ? "这项需要结合实际视频逐条观察。系统尚未给出这个技术要点的自动结论，不能把未检测到直接当成未做到。" : "本次没有保存这项与原文直接对应的测量。仍可选择视频时段人工评审，旧报告不会自动补出新测量。"}</p>}
        {coverage.truncated && !visualMode && <p className="tr-meta">本报告返回了 {coverage.returned} / {coverage.total} 个候选片段；未返回的片段不能视作未发生。</p>}
        {visualMode ? <p className="tr-note">技术要点只记录已观察到、未观察到或无法评价。没有 A～E 等级，也不换算为分数。{visualRule?.source_locator.replace(/^[^:]+:word\/document.xml:P0*(\d+)$/, "原文正文第 $1 段。")}</p> : <details className="tr-source"><summary>查看计算要求与 A～E 原文</summary><h4>计算与观测</h4><p>{selected.calculation}</p><p>{selected.required_points}</p><h4>不可评价条件</h4><p>{selected.unevaluable}</p><dl>{selected.grades.map(grade => <div key={grade.grade}><dt>{grade.grade} 级</dt><dd>{grade.definition}</dd></div>)}</dl><p>{selected.implementation.note}</p></details>}
        {saved.length > 0 && <details className="tr-history"><summary>查看这项的 {saved.length} 条评审</summary>{saved.map(entry => <article key={entry.review_id}><strong>{reviewLabel(entry)} · {entry.reviewer_name}</strong>{!entry.source_binding_current && <p className="tr-optional">视频或原文来源已变化。这条是历史评审，不计入当前结果，也不能直接修订。</p>}<p>{formatTime(entry.start_ms)}–{formatTime(entry.end_ms)} · {entry.reason_zh}</p>{entry.next_step_zh && <p>下一步：{entry.next_step_zh}</p>}<button type="button" disabled={saving || !entry.source_binding_current} onClick={() => changeDraft(draftOf(entry))}>修订这条评审</button></article>)}</details>}
      </div>
      <form className="tr-editor" onSubmit={event => { event.preventDefault(); void saveReview(); }}>
        <div className="tr-editor-title"><h3>{draft.reviewId ? "修订评审" : "录入技术评审"}</h3>{draft.reviewId && <button type="button" disabled={saving} onClick={() => { changeDraft(emptyDraft()); setDrafts(previous => ({ ...previous, [selected.id]: emptyDraft() })); }}>新增一条</button>}</div>
        <fieldset disabled={saving}><legend className="sr-only">评审内容</legend>
          <div className="tr-two"><label><span>评审人</span><input value={draft.reviewer?.reviewer_name ?? reviewerName} readOnly={!!draft.reviewId} maxLength={100} onChange={event => setReviewerName(event.target.value)} placeholder="填写姓名" /></label><label><span>身份（自行声明）</span><select value={draft.reviewer?.reviewer_role ?? reviewerRole} disabled={!!draft.reviewId} onChange={event => setReviewerRole(event.target.value as "coach" | "reviewer")}><option value="reviewer">评审人</option><option value="coach">教练</option></select></label></div>
          <div className="tr-two"><label><span>视频中的人物</span><select value={draft.player} disabled={!!draft.reviewId} onChange={event => changeDraft({ player: event.target.value, event: "" })}><option value="">选择人物</option>{ledger?.players.map(player => <option key={player.player_id} value={player.player_id}>人物 {player.player_id}</option>)}</select></label><label><span>选择动作片段</span><select value={draft.event} disabled={!!draft.reviewId} onChange={event => { const chosen = eventOptions.find(item => item.event_id === event.target.value); changeDraft(chosen ? { event: chosen.event_id, player: String(chosen.player_id), start: seconds(chosen.start_ms), end: seconds(chosen.end_ms) } : { event: "" }); }}><option value="">手动选择时段</option>{eventOptions.map(event => <option key={event.event_id} value={event.event_id}>{seconds(event.start_ms)}–{seconds(event.end_ms)} 秒</option>)}</select></label></div>
          <div className="tr-two"><label><span>开始（秒）</span><input inputMode="decimal" value={draft.start} readOnly={!!draft.reviewId} onChange={event => changeDraft({ start: event.target.value })} placeholder={ledger ? seconds(ledger.video.review_start_ms) : "0"} /></label><label><span>结束（秒）</span><input inputMode="decimal" value={draft.end} readOnly={!!draft.reviewId} onChange={event => changeDraft({ end: event.target.value })} placeholder={ledger ? seconds(ledger.video.review_end_ms) : "例如 3.5"} /></label></div>
          {ledger && <p className="tr-meta">本次可评审范围 {formatTime(ledger.video.review_start_ms)}–{formatTime(ledger.video.review_end_ms)}。{interval && <button type="button" onClick={() => replayMoment({ startMs: interval.start_ms }, jobId)}>回看所选时段</button>}</p>}
          <div className="tr-visibility"><strong>{activeWindow?.visibility.valid_frame_ratio !== null && activeWindow?.visibility.valid_frame_ratio !== undefined ? `本项所需关键点：${Math.round(activeWindow.visibility.valid_frame_ratio * 100)}% 有效帧` : "所选时段的关键点可见性待核实"}</strong><p>{activeWindow?.visibility.valid_frame_ratio !== null && activeWindow?.visibility.valid_frame_ratio !== undefined ? `${activeWindow.visibility.valid_frame_count} / ${activeWindow.visibility.total_frame_count} 帧；原文门槛为 70%。${activeWindow.visibility.valid_frame_ratio < .7 ? "低于门槛，不能据这些测量判级。" : "达到门槛仍需核对动作、其他观测条件和原文要求。"}` : "未提供同一时段、同一指标所需关键点的有效帧统计，不能用整片覆盖率替代。"}</p>{activeWindow?.visibility.reason_zh && <p>{activeWindow.visibility.reason_zh}</p>}{activeWindow?.limitations_zh.map(note => <p key={note}>{note}</p>)}</div>
          <label><span>本时段是否足以判断这项？</span><select value={draft.observability} onChange={event => changeDraft({ observability: event.target.value as Draft["observability"], grade: "" })}><option value="">根据实际视频选择</option><option value="observable">能够清楚判断</option><option value="partial">只能判断部分内容</option><option value="unobservable">无法判断</option></select></label>
          <p className="tr-meta">这是评审人的观察结论，不代表系统已通过关键点门槛或完成自动技术判级。</p>
          {ruleGate && !ruleGate.manual_grading_allowed && <p className="tr-blockers">原文条件尚不完整，这项只能记录无法评价。</p>}
          {!!ruleGate?.warnings?.length && <details className="tr-source"><summary>评审前需核对的原文说明</summary>{ruleGate.warnings.map(warning => <p key={warning.code}>{warning.message}</p>)}</details>}
          <div className={`tr-grade-buttons ${visualMode ? "tr-visual-buttons" : ""}`} role="group" aria-label={visualMode ? "选择技术要点观察结果" : "选择人工技术等级"}>{visualMode ? [ ["observed", "已观察到"], ["not_observed", "未观察到"] ].map(([value, name]) => <button key={value} type="button" aria-pressed={draft.grade === value} disabled={draft.observability !== "observable"} onClick={() => changeDraft({ grade: value as "observed" | "not_observed" })}>{name}</button>) : TECHNICAL_GRADES.map(grade => <button key={grade} type="button" aria-pressed={draft.grade === grade} disabled={!ruleGate?.manual_grading_allowed || draft.observability !== "observable"} onClick={() => changeDraft({ grade })}>{grade}</button>)}<button type="button" aria-pressed={draft.grade === "unassessable"} onClick={() => changeDraft({ grade: "unassessable" })}>无法评价</button></div>
          {gradeDefinition && <div className="tr-grade-definition"><strong>{draft.grade} 级 · 原文标准</strong><p>{gradeDefinition}</p></div>}
          {draft.grade === "unassessable" && <p className="tr-note">无法评价不是 E 级，也不会换算为零分。请写明看不清、缺少证据或原文待确认的内容。</p>}
          <label><span>{draft.grade === "unassessable" ? "为什么无法评价" : visualMode ? "在这段视频中观察到了什么" : "为什么落在这个等级"}</span><textarea value={draft.reason} onChange={event => changeDraft({ reason: event.target.value })} maxLength={4000} rows={3} placeholder="结合时段描述观察到什么，以及与原文要求的差别。" /></label>
          <label><span>下一步怎么提升（可选）</span><textarea value={draft.nextStep} onChange={event => changeDraft({ nextStep: event.target.value })} maxLength={4000} rows={2} placeholder="记录一项具体的训练建议，或需要补拍的内容。" /></label>
        </fieldset>
        <button className="tr-save" type="submit" disabled={!ledger || loadState !== "ready" || saving}>{saving ? "正在保存…" : draft.reviewId ? "保存修订" : "保存这条评审"}</button>
      </form>
    </div>}
    {message && <p className="tr-feedback" role="status">{message}</p>}
  </section>;
}
