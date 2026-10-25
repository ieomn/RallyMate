import type { DemoResultResponse, TrajectoryPreviewResponse } from "./api-types";

export const TRAINING_JOURNAL_KEY = "rallymate.trainingJournal.v1";
export const LEGACY_REPORTS_KEY = "rallymate.recentReports";
export type TrainingFocus = { id: string; title: string; question: string; timestampMs?: number };
export type TrainingReplay = { kind: "annotated"; originMs: number; endMs: number; timingPreserved: true };
export type TrainingFollowUp = { recordId: string; focus: TrainingFocus };
export type TrainingRecord = {
  id: string; jobId?: string; source: "online" | "imported"; name: string; originalName: string;
  theme: string; notes: string; createdAt: string; viewedAt: string; focuses: TrainingFocus[];
  followUp?: TrainingFollowUp; replay?: TrainingReplay;
};
export type TrainingJournal = { version: 1; records: TrainingRecord[]; pendingFocus?: TrainingFollowUp };
export type TrainingVisit = {
  jobId?: string; name: string; source: "online" | "imported"; sourceId?: string; linkPending?: boolean;
  result?: DemoResultResponse | null; trajectory?: TrajectoryPreviewResponse | null;
};
export type TrainingRecordPatch = Pick<TrainingRecord, "name" | "theme" | "notes">;
export type JournalStorage = Pick<Storage, "getItem" | "setItem">;
const row = (value: unknown): Record<string, unknown> => value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
const text = (value: unknown, limit: number) => typeof value === "string" ? value.trim().slice(0, limit) : "";
const jobIdOf = (value: unknown) => typeof value === "string" && /^[a-zA-Z0-9_-]{8,80}$/.test(value) ? value : undefined;
const time = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value) && value >= 0;
const date = (value: unknown) => typeof value === "string" && Number.isFinite(Date.parse(value)) ? value : "";
const basename = (name: string) => name.split(/[\\/]/).pop() || "训练视频";
function defaultName(originalName: string, viewedAt: string) {
  const stem = originalName.replace(/\.[a-z0-9]{2,5}$/i, "");
  if (/^[a-f\d-]{16,}$/i.test(stem)) {
    const parts = new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", month: "numeric", day: "numeric" }).formatToParts(new Date(viewedAt));
    return `${parts.find(part => part.type === "month")?.value}月${parts.find(part => part.type === "day")?.value}日训练复盘`;
  }
  return stem || "未命名训练";
}

export function journalRecordId(source: "online" | "imported", jobId?: string, sourceId?: string): string {
  if (source === "online") return jobIdOf(jobId) ? `online:${jobId}` : "";
  return `imported:${encodeURIComponent(`${jobIdOf(jobId) || "offline"}:${sourceId || "report"}`)}`;
}
export function trainingFocusOf(value: unknown): TrainingFocus | null {
  const item = row(value), id = text(item.id, 200), title = text(item.title, 160), question = text(item.question, 1600);
  if (!id || !title || !question) return null;
  return { id, title, question, ...(time(item.timestampMs) ? { timestampMs: item.timestampMs } : {}) };
}
function followUpOf(value: unknown): TrainingFollowUp | undefined {
  const item = row(value), focus = trainingFocusOf(item.focus);
  return typeof item.recordId === "string" && /^(online|imported):/.test(item.recordId) && focus ? { recordId: item.recordId, focus } : undefined;
}
function replayOf(value: unknown): TrainingReplay | undefined {
  const item = row(value);
  return item.kind === "annotated" && item.timingPreserved === true && time(item.originMs) && time(item.endMs) && item.endMs > item.originMs
    ? { kind: "annotated", originMs: item.originMs, endMs: item.endMs, timingPreserved: true } : undefined;
}

/** Persist only a live, job-bound replay whose source-clock offset was supplied. */
export function trainingReplayOf(visit: TrainingVisit): TrainingReplay | undefined {
  const runtime = row(visit.result?.runtime), compatibility = row(runtime.annotated_video_compatibility);
  const source = visit.trajectory?.source;
  if (visit.source !== "online" || !jobIdOf(visit.jobId) || visit.result?.job_id !== visit.jobId || compatibility.timing_preserved !== true || !visit.result?.artifact_urls?.["annotated.mp4"] || !source) return undefined;
  return replayOf({ kind: "annotated", timingPreserved: true, originMs: source.start_timestamp_ms, endMs: source.end_timestamp_ms });
}

export function trainingJournalOf(value: unknown): TrainingJournal {
  const input = row(value);
  if (input.version !== 1 || !Array.isArray(input.records)) throw new Error("训练记录格式无法识别。现有内容已保留，请先导出浏览器记录或恢复备份。");
  const records: TrainingRecord[] = [], ids = new Set<string>();
  for (const raw of input.records) {
    const item = row(raw), jobId = jobIdOf(item.jobId);
    if (item.source !== "online" && item.source !== "imported" || typeof item.id !== "string" || ids.has(item.id) || !date(item.createdAt) || !date(item.viewedAt)) continue;
    if (item.source === "online" ? !jobId || item.id !== journalRecordId("online", jobId) : !item.id.startsWith("imported:")) continue;
    const focuses = (Array.isArray(item.focuses) ? item.focuses : []).map(trainingFocusOf).filter((focus): focus is TrainingFocus => Boolean(focus));
    records.push({ id: item.id, source: item.source, ...(jobId ? { jobId } : {}), name: text(item.name, 160) || "未命名训练", originalName: text(item.originalName, 160), theme: text(item.theme, 120), notes: text(item.notes, 2000), createdAt: date(item.createdAt), viewedAt: date(item.viewedAt), focuses: [...new Map(focuses.map(focus => [focus.id, focus])).values()], followUp: followUpOf(item.followUp), replay: item.source === "online" ? replayOf(item.replay) : undefined });
    ids.add(item.id);
  }
  return { version: 1, records, pendingFocus: followUpOf(input.pendingFocus) };
}

/** Legacy history is copied, never removed or truncated. Existing edits win. */
export function migrateTrainingHistory(journal: TrainingJournal, legacy: unknown): TrainingJournal {
  const records = [...journal.records], ids = new Set(records.map(item => item.id));
  for (const raw of Array.isArray(legacy) ? legacy : []) {
    const item = row(raw), jobId = jobIdOf(item.id), viewedAt = date(item.viewedAt);
    if (!jobId || !viewedAt || typeof item.name !== "string") continue;
    const id = journalRecordId("online", jobId);
    if (ids.has(id)) continue;
    const originalName = basename(text(item.name, 160));
    records.push({ id, jobId, source: "online", name: defaultName(originalName, viewedAt), originalName, theme: "", notes: "", createdAt: viewedAt, viewedAt, focuses: [] }); ids.add(id);
  }
  return { ...journal, records };
}

export function readTrainingJournal(storage: JournalStorage): TrainingJournal {
  const saved = storage.getItem(TRAINING_JOURNAL_KEY);
  const journal = saved ? trainingJournalOf(JSON.parse(saved)) : { version: 1 as const, records: [] };
  // A broken legacy list must not hide the newer journal or be overwritten.
  let legacy: unknown = [];
  try { legacy = JSON.parse(storage.getItem(LEGACY_REPORTS_KEY) || "[]"); } catch { /* Preserve legacy bytes; the valid newer journal remains usable. */ }
  return migrateTrainingHistory(journal, legacy);
}
export function writeTrainingJournal(storage: JournalStorage, journal: TrainingJournal): void {
  storage.setItem(TRAINING_JOURNAL_KEY, JSON.stringify(journal));
}

export function visitTrainingReport(journal: TrainingJournal, visit: TrainingVisit, now = new Date().toISOString()): TrainingJournal {
  const id = journalRecordId(visit.source, visit.jobId, visit.sourceId);
  if (!id) return journal;
  const existing = journal.records.find(item => item.id === id), originalName = basename(visit.name).slice(0, 160);
  const linked = visit.linkPending && journal.pendingFocus?.recordId !== id ? journal.pendingFocus : undefined;
  const replay = trainingReplayOf(visit);
  const record: TrainingRecord = existing
    ? { ...existing, viewedAt: now, ...(replay ? { replay } : {}), ...(linked && !existing.followUp ? { followUp: linked } : {}) }
    : { id, source: visit.source, ...(jobIdOf(visit.jobId) ? { jobId: visit.jobId } : {}), name: defaultName(originalName, now), originalName, theme: "", notes: "", createdAt: now, viewedAt: now, focuses: [], ...(linked ? { followUp: linked } : {}), ...(replay ? { replay } : {}) };
  return { ...journal, records: [record, ...journal.records.filter(item => item.id !== id)], ...(visit.linkPending ? { pendingFocus: undefined } : {}) };
}
export function updateTrainingRecord(journal: TrainingJournal, id: string, patch: TrainingRecordPatch): TrainingJournal {
  return { ...journal, records: journal.records.map(item => item.id === id ? { ...item, name: text(patch.name, 160) || "未命名训练", theme: text(patch.theme, 120), notes: text(patch.notes, 2000) } : item) };
}
export function saveTrainingFocus(journal: TrainingJournal, id: string, value: TrainingFocus): TrainingJournal {
  const focus = trainingFocusOf(value);
  if (!focus) return journal;
  return { ...journal, records: journal.records.map(item => item.id === id ? { ...item, focuses: [...item.focuses.filter(saved => saved.id !== focus.id), focus] } : item) };
}
export function selectTrainingFollowUp(journal: TrainingJournal, recordId?: string, focusId?: string): TrainingJournal {
  const record = journal.records.find(item => item.id === recordId), focus = record?.focuses.find(item => item.id === focusId);
  return { ...journal, pendingFocus: record && focus ? { recordId: record.id, focus: { ...focus } } : undefined };
}
export function replayTarget(record: TrainingRecord | undefined, focus: TrainingFocus | undefined): { src: string; seconds: number } | null {
  if (record?.source !== "online" || !jobIdOf(record.jobId) || !record.replay || !focus || !time(focus.timestampMs) || focus.timestampMs < record.replay.originMs || focus.timestampMs > record.replay.endMs) return null;
  return { src: `/v1/jobs/${encodeURIComponent(record.jobId!)}/artifacts/annotated.mp4`, seconds: (focus.timestampMs - record.replay.originMs) / 1000 };
}
