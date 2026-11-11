import { unpackApiReport } from "./report-backup-codec";
import { modelMeasurementEvidenceOf } from "./model-measurement";
import type { DemoResultResponse, MotionEpisode, TechniqueAssessmentResponse } from "./api-types";
import { motionAnalysisOf } from "./motion-analysis";
import { contextualHipMeasurements } from "./technical-review";

export const SUMMARY_BYTES = 256 * 1024, ACTION_PAGE_BYTES = 512 * 1024, ACTION_DETAIL_BYTES = 16 * 1024 * 1024;
type Row = Record<string, unknown>;
export type ReportSource = { source_job_id: string; video_id: string; video_sha256: string; frames_sha256: string; evidence_revision: string };
export type ReportAction = { action_id: string; event_id: string; event_code: string; label: string; start_ms: number; end_ms: number; person_track_id: number; source_track_id: number | null; indicator_count: number; evidence_status: "available" | "failed" };
export type ReportPagination = { offset: number; limit: number; total: number; next_offset: number | null };
export type ReportSummary = {
  schema_version: "segmented-report-v1"; job_id: string; status: "ready"; source: ReportSource;
  video: { width: number; height: number; fps: number; frame_count: number; duration_ms: number };
  processing: { processed_frames: number; elapsed_seconds: number; effective_processed_fps: number };
  scoring: { status: string; measurement_status: string; grade: unknown; message: string; event_counts: Record<string, number>; score_status_counts: Record<string, number> };
  action_candidates: { status: string; count: number; review_count: number; semantics: string };
  original_video: unknown;
};
export type ReportActionPage = { job_id: string; status: "complete" | "partial"; source: ReportSource; actions: ReportAction[]; pagination: ReportPagination };
export type ReportActionDetail = { job_id: string; status: "complete" | "partial"; source: ReportSource; action: ReportAction; event: Row; records: Row[]; pagination: ReportPagination;
  footworkReview?: DemoResultResponse["footwork_review"]; sourceAssessment?: DemoResultResponse["source_aligned_assessment"]; processEvaluation?: DemoResultResponse["process_evaluation"] };
export const OVERVIEW_BYTES = 2 * 1024 * 1024;
export type ReportOverview = { job_id: string; source: ReportSource; result: DemoResultResponse; assessment: TechniqueAssessmentResponse | null };

/** Establish source context immediately so independent scoring need not wait for other sections. */
export function resultFromReportSummary(summary: ReportSummary): DemoResultResponse {
  return { job_id: summary.job_id, video_id: summary.source.video_id, status: "ready",
    input: { video: { ...summary.video, sha256: summary.source.video_sha256 } },
    original_video: summary.original_video as DemoResultResponse["original_video"],
    sam3d_mesh_status: "pending", summary: { job_id: summary.job_id, video: summary.video, processing: summary.processing },
    report_delivery: { mode: "segmented", evidence: "on_demand", overview: "not_loaded" } };
}

export function parseReportOverview(raw: unknown, jobId: string, expected: ReportSource): ReportOverview {
  const value = envelope(raw, jobId, expected);
  if (value.status !== "complete" || !row(value.result) || value.result.job_id !== jobId) return fail();
  const input = row(value.result.input) && row(value.result.input.video) ? value.result.input.video : null;
  if (!input || !sha(input.sha256) || input.sha256.toLowerCase() !== expected.video_sha256.toLowerCase()) return fail();
  if (value.assessment !== null && (!row(value.assessment) || value.assessment.job_id !== jobId || !Array.isArray(value.assessment.techniques))) return fail();
  return { job_id: jobId, source: expected, result: value.result as DemoResultResponse, assessment: value.assessment as TechniqueAssessmentResponse | null };
}
export type ReportMotion = { episode: MotionEpisode; processEvaluation: DemoResultResponse["process_evaluation"] };
export function parseReportMotion(raw: unknown, jobId: string, source: ReportSource, expected: MotionEpisode): ReportMotion {
  const value = envelope(unpackApiReport(raw), jobId, source);
  if (value.status !== "complete" || !row(value.episode)) return fail();
  const episode = value.episode;
  if (["episode_id", "family", "person_track_id", "start_ms", "peak_ms", "end_ms"].some(key => episode[key] !== expected[key as keyof MotionEpisode])) return fail();
  const parsed = motionAnalysisOf({ job_id: jobId, action_recognition: { motion_analysis: {
    schema_version: "1.1.0", contact_confirmed: false, families: { [expected.family]: { episodes: [episode] } },
  } } } as unknown as DemoResultResponse)?.families[expected.family]?.episodes[0];
  if (!parsed) return fail();
  return { episode: parsed, processEvaluation: value.process_evaluation as DemoResultResponse["process_evaluation"] };
}
const row = (value: unknown): value is Row => !!value && typeof value === "object" && !Array.isArray(value);
const integer = (value: unknown): value is number => Number.isSafeInteger(value) && Number(value) >= 0;
const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value) && value >= 0;
const sha = (value: unknown): value is string => typeof value === "string" && /^[a-f0-9]{64}$/i.test(value);
const fail = (): never => { throw new Error("片段证据与当前训练或原视频不匹配，请重新读取摘要。"); };
function sourceOf(raw: unknown): ReportSource {
  if (!row(raw) || typeof raw.source_job_id !== "string" || !raw.source_job_id || typeof raw.video_id !== "string" || !raw.video_id || !sha(raw.video_sha256) || !sha(raw.frames_sha256) || !sha(raw.evidence_revision)) return fail();
  return raw as ReportSource;
}
export function sameReportSource(a: ReportSource, b: ReportSource) {
  return a.source_job_id === b.source_job_id && a.video_id === b.video_id && a.video_sha256.toLowerCase() === b.video_sha256.toLowerCase() && a.frames_sha256.toLowerCase() === b.frames_sha256.toLowerCase()
    && a.evidence_revision.toLowerCase() === b.evidence_revision.toLowerCase();
}
function envelope(raw: unknown, jobId: string, expected?: ReportSource): Row {
  if (!row(raw) || raw.schema_version !== "segmented-report-v1" || raw.job_id !== jobId) return fail();
  const source = sourceOf(raw.source);
  if (expected && !sameReportSource(source, expected)) return fail();
  return raw;
}
function actionOf(raw: unknown): ReportAction {
  if (!row(raw) || typeof raw.action_id !== "string" || !/^[A-Za-z0-9][A-Za-z0-9_.-]{0,159}$/.test(raw.action_id) || raw.action_id !== raw.event_id || typeof raw.event_code !== "string" || typeof raw.label !== "string"
    || !integer(raw.start_ms) || !integer(raw.end_ms) || raw.end_ms < raw.start_ms || !integer(raw.person_track_id) || !integer(raw.indicator_count)
    || !["available", "failed"].includes(String(raw.evidence_status)) || !integer(raw.source_track_id) && !(raw.source_track_id === null && raw.evidence_status === "failed")) return fail();
  return raw as ReportAction;
}
function pageOf(raw: Row, offset: number, size: number, maximum: number): ReportPagination {
  const page = raw.pagination;
  if (!row(page) || page.offset !== offset || !integer(page.limit) || page.limit < 1 || page.limit > maximum || !integer(page.total) || size > page.limit || page.offset + size > page.total
    || page.next_offset !== null && (!integer(page.next_offset) || page.next_offset !== offset + size || page.next_offset >= page.total || size === 0)
    || page.next_offset === null && offset + size !== page.total
    || raw.status !== (page.next_offset === null ? "complete" : "partial")) return fail();
  return page as ReportPagination;
}
export function parseReportSummary(raw: unknown, jobId: string): ReportSummary {
  const value = envelope(raw, jobId);
  if (value.status !== "ready" || !row(value.video) || !["width", "height", "frame_count"].every(key => integer((value.video as Row)[key])) || !finite(value.video.duration_ms) || !finite(value.video.fps)
    || !row(value.processing) || !integer(value.processing.processed_frames) || !row(value.scoring) || value.scoring.message !== null && typeof value.scoring.message !== "string" || !row(value.scoring.event_counts) || !row(value.scoring.score_status_counts)
    || !Object.values(value.scoring.event_counts).every(integer) || !Object.values(value.scoring.score_status_counts).every(integer)
    || !row(value.delivery) || value.delivery.summary !== "complete" || value.delivery.evidence !== "on_demand") return fail();
  return value as unknown as ReportSummary;
}
export function parseReportActions(raw: unknown, jobId: string, source: ReportSource, offset: number): ReportActionPage {
  const value = envelope(raw, jobId, source);
  if (!Array.isArray(value.actions)) return fail();
  const actions = value.actions.map(actionOf);
  if (new Set(actions.map(action => action.action_id)).size !== actions.length) return fail();
  return { job_id: jobId, source, actions, status: value.status as ReportActionPage["status"], pagination: pageOf(value, offset, actions.length, 20) };
}
export function parseReportActionDetail(raw: unknown, jobId: string, source: ReportSource, action: ReportAction, offset: number): ReportActionDetail {
  // Keep the existing full proof, PTS and geometry decoder; pagination never
  // truncates a proof. Source/event binding is an additional boundary check.
  const value = envelope(unpackApiReport(raw), jobId, source), returned = actionOf(value.action);
  if (["action_id", "event_id", "event_code", "start_ms", "end_ms", "person_track_id", "source_track_id", "indicator_count"].some(key => returned[key as keyof ReportAction] !== action[key as keyof ReportAction])
    || action.evidence_status !== "available" || returned.evidence_status !== "available"
    || value.evidence_validation !== "validated" || value.scope !== "event_indicator_records" || !row(value.event) || value.event.event_id !== action.event_id
    || value.event.event_code !== action.event_code || value.event.person_track_id !== action.person_track_id || value.event.video_id !== source.video_id
    || !Array.isArray(value.records) || value.records.some(record => !row(record) || record.event_id !== action.event_id || record.event_code !== action.event_code || record.person_track_id !== action.person_track_id || record.video_id !== source.video_id)) return fail();
  const pagination = pageOf(value, offset, value.records.length, 4);
  if (pagination.total !== action.indicator_count) return fail();
  const parent = modelMeasurementEvidenceOf({ ...value.event, source_track_id: value.event.source_track_id ?? action.source_track_id });
  if (!parent || parent.source_window.start_ms !== action.start_ms || parent.source_window.end_ms !== action.end_ms) return fail();
  const bindings = parent.source_window.source_bindings as Row[];
  const byFrame = new Map(bindings.map(binding => [binding.frame_index, binding]));
  const contextual = row(value.source_aligned_assessment) && Array.isArray(value.source_aligned_assessment.contextual_measurements)
    ? value.source_aligned_assessment.contextual_measurements : [];
  const hipMeasurements = contextualHipMeasurements({ source_aligned_assessment: value.source_aligned_assessment } as DemoResultResponse);
  if (hipMeasurements.length !== contextual.length || hipMeasurements.some(item => item.event_id !== action.event_id || item.event_code !== action.event_code
    || item.video_id !== source.video_id || item.source_id !== source.source_job_id || item.video_sha256.toLowerCase() !== source.video_sha256.toLowerCase()
    || item.input_sha256.frames.toLowerCase() !== source.frames_sha256.toLowerCase() || item.person_track_id !== action.person_track_id
    || item.parent_window_ms.start_ms !== action.start_ms || item.parent_window_ms.end_ms !== action.end_ms)) return fail();
  const independentHipRows = new Set(contextual);
  const checked = new WeakSet<object>();
  const checkProofSources = (item: unknown) => {
    if (item && typeof item === "object") { if (checked.has(item)) return; checked.add(item); }
    if (Array.isArray(item)) { item.forEach(checkProofSources); return; }
    if (!row(item)) return;
    // The independent hip-path contract has its own strict source/PTS validator
    // above. Do not mistake its source_window for a SAM model proof; continue
    // walking unknown nested values so they cannot bypass the proof decoder.
    if ("source_window" in item && !independentHipRows.has(item)) {
      const proof = modelMeasurementEvidenceOf({ ...item, source_track_id: item.source_track_id ?? action.source_track_id });
      if (!proof || String(proof.source_window.video_sha256).toLowerCase() !== source.video_sha256.toLowerCase() || proof.source_track_id !== action.source_track_id) return fail();
      for (const binding of proof.source_window.source_bindings as Row[]) {
        const original = byFrame.get(binding.frame_index);
        if (!original || Object.entries(binding).some(([key, value]) => key !== "video_timestamp_ms" && original[key] !== value)) return fail();
      }
      if (Number(proof.source_window.start_ms) < Number(parent.source_window.start_ms) || Number(proof.source_window.end_ms) > Number(parent.source_window.end_ms)) return fail();
    }
    Object.values(item).forEach(checkProofSources);
  };
  checkProofSources(value.event); checkProofSources(value.records);
  checkProofSources(value.footwork_review);
  checkProofSources(value.source_aligned_assessment);
  checkProofSources(value.process_evaluation);
  return { job_id: jobId, source, action: returned, event: value.event, records: value.records as Row[], pagination, status: value.status as ReportActionDetail["status"],
    footworkReview: value.footwork_review as DemoResultResponse["footwork_review"], sourceAssessment: value.source_aligned_assessment as DemoResultResponse["source_aligned_assessment"], processEvaluation: value.process_evaluation as DemoResultResponse["process_evaluation"] };
}

export function reportTime(ms: number) {
  const seconds = Math.floor(ms / 1000);
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}.${String(Math.floor(ms % 1000)).padStart(3, "0")}`;
}
