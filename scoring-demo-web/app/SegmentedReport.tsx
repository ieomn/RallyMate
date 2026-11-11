"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { RallyMateApiClient } from "./lib/api-client";
import { parseReportActions, reportTime, resultFromReportSummary, type ReportAction, type ReportActionDetail, type ReportActionPage, type ReportSummary } from "./lib/segmented-report";
import { originalPoseTimeBinding } from "./lib/original-video-timing";
import FootworkReviewPanel from "./FootworkReviewPanel";
import ProcessScorePanel from "./ProcessScorePanel";
import "./segmented-report.css";

type ReadState<T> = { data: T | null; loading: boolean; error: string | null };
type ReadIdentity = { summary: ReportSummary; api: RallyMateApiClient; signal: AbortSignal; offset: number; attempt: number };
type CompletedRead<T> = ReadIdentity & { data: T | null; error: string | null };
const row = (value: unknown): Record<string, unknown> => value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
const message = (error: unknown) => error instanceof Error ? error.message : "暂未读到片段证据，请重试。";
const featureNames: Record<string, string> = { hip_center_y_body: "髋部中心高度", left_knee_flexion_deg: "左膝屈曲角", right_knee_flexion_deg: "右膝屈曲角", stance_width_body: "站距", hip_center_relative_to_ankle_support: "髋部与脚踝支撑关系", stability_duration_ms: "稳定持续时间", post_slowdown_stance_width_body: "减速后站距", hip_center_lateral_variability_body: "髋部横向变化" };
const statusText = (status: unknown) => ({ calibration_required: "需校准，暂不出正式技术分", measured: "已测量", available: "可查看", unavailable: "证据不足", partial: "部分证据", blocked: "暂不可评分", failed: "读取失败" })[String(status)] ?? "已保存";

function IndicatorEvidence({ record }: { record: Record<string, unknown> }) {
  const features = Array.isArray(record.features) ? record.features.map(row) : [];
  return <article className="segment-indicator">
    <h4>{String(record.indicator_id ?? "动作指标")} <span>{statusText(record.scoring_status)}</span></h4>
    <p>{statusText(record.feature_status)} · 主体 {String(record.person_track_id)}</p>
    {features.length ? <table><thead><tr><th>测量项目</th><th>数值</th><th>来源帧</th></tr></thead><tbody>{features.map((feature, index) => <tr key={`${feature.feature_name}:${index}`}>
      <td>{String(feature.name_zh ?? featureNames[String(feature.feature_name)] ?? feature.feature_name ?? "测量")}</td>
      <td>{feature.valid === true && typeof feature.value === "number" && Number.isFinite(feature.value) ? `${Number(feature.value.toFixed(4))} ${feature.unit === "deg" ? "°" : feature.unit === "ms" ? "毫秒" : feature.unit === "body" ? "身体尺度" : feature.unit === "ratio" ? "比例" : String(feature.unit ?? "")}` : "暂无有效测量"}</td>
      <td>{Array.isArray(feature.source_frames) ? feature.source_frames.length > 8 ? `${feature.source_frames[0]}–${feature.source_frames.at(-1)}（共 ${feature.source_frames.length} 帧）` : feature.source_frames.join("、") : "—"}</td>
    </tr>)}</tbody></table> : <p>此项尚无可显示的测量值。</p>}
  </article>;
}

export default function SegmentedReport({ summary, api, signal, onRefresh }: { summary: ReportSummary; api: RallyMateApiClient; signal: AbortSignal; onRefresh?: () => void }) {
  const [actionRead, setActionRead] = useState<CompletedRead<ReportActionPage> | null>(null);
  const [actionOffset, setActionOffset] = useState(0), [actionAttempt, setActionAttempt] = useState(0);
  const [selection, setSelection] = useState<{ action: ReportAction; summary: ReportSummary; signal: AbortSignal } | null>(null);
  const selected = selection?.summary === summary && selection.signal === signal ? selection.action : null;
  const [detailRead, setDetailRead] = useState<(CompletedRead<ReportActionDetail> & { action: ReportAction }) | null>(null);
  const [detailOffset, setDetailOffset] = useState(0), [detailAttempt, setDetailAttempt] = useState(0);
  const detailController = useRef<AbortController | null>(null);
  const originalTiming = originalPoseTimeBinding(summary, summary.job_id, true);
  const seekAvailable = originalTiming && originalTiming.frames_sha256.toLowerCase() === summary.source.frames_sha256.toLowerCase();
  const sameActionSource = actionRead?.summary === summary && actionRead.api === api && actionRead.signal === signal;
  const currentActionRead = sameActionSource && actionRead.offset === actionOffset && actionRead.attempt === actionAttempt ? actionRead : null;
  // Keep the previous list visible during paging within the same report. A new
  // source cannot reuse it, and detail evidence always belongs to its exact page.
  const actions: ReadState<ReportActionPage> = { data: sameActionSource ? actionRead.data : null, loading: !currentActionRead, error: currentActionRead?.error ?? null };
  const currentDetailRead = detailRead?.summary === summary && detailRead.api === api && detailRead.signal === signal && detailRead.action === selected && detailRead.offset === detailOffset && detailRead.attempt === detailAttempt ? detailRead : null;
  const detail: ReadState<ReportActionDetail> = { data: currentDetailRead?.data ?? null, loading: Boolean(selected && !currentDetailRead), error: currentDetailRead?.error ?? null };
  useEffect(() => {
    const controller = new AbortController(), combined = AbortSignal.any([controller.signal, signal]);
    const identity = { summary, api, signal, offset: actionOffset, attempt: actionAttempt };
    void api.getReportActions(summary.job_id, summary.source, actionOffset, combined).then(data => {
      if (!combined.aborted) setActionRead({ ...identity, data, error: null });
    }).catch(error => { if (!combined.aborted) setActionRead(previous => ({ ...identity,
      data: previous?.summary === summary && previous.api === api && previous.signal === signal ? previous.data : null, error: message(error) })); });
    return () => controller.abort();
  }, [api, summary, signal, actionOffset, actionAttempt]);
  useEffect(() => {
    if (!selected) return;
    const controller = new AbortController(), combined = AbortSignal.any([controller.signal, signal]);
    detailController.current = controller;
    const identity = { summary, api, signal, action: selected, offset: detailOffset, attempt: detailAttempt };
    void api.getReportActionDetail(summary.job_id, summary.source, selected, detailOffset, combined).then(data => {
      if (!combined.aborted) setDetailRead({ ...identity, data, error: null });
    }).catch(error => { if (!combined.aborted) setDetailRead({ ...identity, data: null, error: message(error) }); });
    return () => controller.abort();
  }, [api, summary, signal, selected, detailOffset, detailAttempt]);
  const choose = useCallback((action: ReportAction) => {
    detailController.current?.abort(); setDetailOffset(0); setSelection({ action, summary, signal }); setDetailAttempt(value => value + 1);
    if (seekAvailable) window.dispatchEvent(new CustomEvent("rallymate:seek-video", { detail: { timestampMs: action.start_ms, jobId: summary.job_id, preferOriginal: true } }));
  }, [seekAvailable, summary, signal]);
  useEffect(() => {
    const selectAction = (event: Event) => {
      const request = (event as CustomEvent<{ jobId: string; action: ReportAction }>).detail;
      if (request?.jobId !== summary.job_id || signal.aborted) return;
      try {
        const action = parseReportActions({ schema_version: "segmented-report-v1", job_id: summary.job_id, source: summary.source, status: "complete", actions: [request.action], pagination: { offset: 0, limit: 1, total: 1, next_offset: null } }, summary.job_id, summary.source, 0).actions[0];
        choose(action);
      } catch { /* An invalid navigation request never authorizes an evidence read. */ }
    };
    window.addEventListener("rallymate:select-report-action", selectAction);
    return () => window.removeEventListener("rallymate:select-report-action", selectAction);
  }, [summary, signal, choose]);
  const list = actions.data;
  return <div className="segmented-report">
    <section className="segment-actions" aria-labelledby="segment-actions-title">
      <h2 id="segment-actions-title">按动作查看证据</h2>
      <p className="replay-caption">动作列表按页读取，选择片段后读取该动作的指标依据。点击片段可定位上方原视频。</p>
      {onRefresh && <button type="button" className="product-secondary" onClick={onRefresh}>重新读取摘要</button>}
      {actions.loading && <p role="status">正在读取动作列表…</p>}
      {actions.error && <div role="alert"><p>{actions.error}</p><button type="button" className="product-secondary" onClick={() => setActionAttempt(value => value + 1)}>重试动作列表</button></div>}
      {list && <><p>{list.pagination.total ? `共 ${list.pagination.total} 个动作，当前第 ${list.pagination.offset + 1}–${list.pagination.offset + list.actions.length} 个` : "本次未检出可列出的动作事件。"}</p><div className="segment-action-list">{list.actions.map(action => <button type="button" key={action.action_id} className={`segment-action ${selected?.action_id === action.action_id ? "is-selected" : ""}`} aria-pressed={selected?.action_id === action.action_id} onClick={() => choose(action)}><strong>{action.label}</strong><span>{reportTime(action.start_ms)}–{reportTime(action.end_ms)}</span><small>主体 {action.person_track_id} · {action.indicator_count} 项依据{action.evidence_status === "failed" ? " · 证据校验未通过" : ""}</small></button>)}</div>
        <div className="segment-pager"><button type="button" className="product-secondary" disabled={actions.loading || list.pagination.offset === 0} onClick={() => setActionOffset(Math.max(0, list.pagination.offset - 20))}>上一页动作</button><button type="button" className="product-secondary" disabled={actions.loading || list.pagination.next_offset === null} onClick={() => setActionOffset(list.pagination.next_offset!)}>下一页动作</button></div></>}
      {selected && <section className="segment-detail" aria-labelledby="segment-detail-title"><h3 id="segment-detail-title">{selected.label} · 片段依据</h3><p>{reportTime(selected.start_ms)}–{reportTime(selected.end_ms)} · 主体 {selected.person_track_id}</p>
        {detail.loading && <div role="status"><p>正在读取此动作的完整证据页…</p><button type="button" className="product-secondary" onClick={() => { detailController.current?.abort(); setDetailRead({ summary, api, signal, action: selected, offset: detailOffset, attempt: detailAttempt, data: null, error: "已停止本次读取，可重新读取当前片段。" }); }}>停止读取片段</button></div>}
        {detail.error && <div role="alert"><p>{detail.error}</p><button type="button" className="product-secondary" onClick={() => setDetailAttempt(value => value + 1)}>重试片段证据</button></div>}
        {detail.data && <><p role="status">{detail.data.pagination.total ? `当前显示第 ${detail.data.pagination.offset + 1}–${detail.data.pagination.offset + detail.data.records.length} 项，共 ${detail.data.pagination.total} 项` : "此动作暂无指标记录"}。{detail.data.status === "partial" ? "还有下一页证据。" : detail.data.pagination.offset ? "已到此动作的最后一页。" : "此动作的指标证据已全部读取。"}</p>{detail.data.records.map((record, index) => <IndicatorEvidence key={`${record.indicator_id}:${index}`} record={record} />)}
          <div className="segment-pager"><button type="button" className="product-secondary" disabled={detail.data.pagination.offset === 0} onClick={() => setDetailOffset(Math.max(0, detail.data!.pagination.offset - detail.data!.pagination.limit))}>上一页依据</button><button type="button" className="product-secondary" disabled={detail.data.pagination.next_offset === null} onClick={() => setDetailOffset(detail.data!.pagination.next_offset!)}>下一页依据</button></div>
          {detail.data.footworkReview && <><p className="replay-caption">以下步伐测量对应当前事件与本页指标；其他指标可通过上方翻页查看。</p><FootworkReviewPanel review={detail.data.footworkReview} sourceAssessment={detail.data.sourceAssessment} jobId={summary.job_id} /></>}
          {detail.data.processEvaluation && <ProcessScorePanel result={{ ...resultFromReportSummary(summary), process_evaluation: detail.data.processEvaluation }} motion={null} pending={false} title="当前片段过程测量" description="仅当前事件与本页指标的完整依据" showSummary={false} />}
        </>}
      </section>}
    </section>
  </div>;
}
