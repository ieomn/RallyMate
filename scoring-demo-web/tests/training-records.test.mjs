import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import ts from "typescript";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

const compiled = new Map();
function compiledUrl(fileUrl) {
  // Keep source JSON imports shared with production, without a generated copy.
  if (fileUrl.pathname.endsWith(".json")) return `data:text/javascript;base64,${Buffer.from(`export default ${fs.readFileSync(fileUrl, "utf8")}`).toString("base64")}`;
  if (compiled.has(fileUrl.href)) return compiled.get(fileUrl.href);
  let js = ts.transpileModule(fs.readFileSync(fileUrl, "utf8"), { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  js = js.replace(/^import\s+["'][^"']+\.css["'];?\s*$/gm, "");
  js = js.replace(/from "([^"]+)"/g, (_match, name) => {
    const local = new URL(name.endsWith(".json") ? name : `${name}.ts`, fileUrl);
    return `from ${JSON.stringify(name.startsWith(".") ? compiledUrl(fs.existsSync(local) ? local : new URL(`${name}.tsx`, fileUrl)) : import.meta.resolve(name))}`;
  });
  const url = `data:text/javascript;base64,${Buffer.from(js).toString("base64")}`; compiled.set(fileUrl.href, url); return url;
}
const lib = await import(compiledUrl(new URL("../app/lib/training-records.ts", import.meta.url)));
const { TrainingJournalLibrary, TrainingJournalPanel, TrainingFollowUpPicker } = await import(compiledUrl(new URL("../app/TrainingJournal.tsx", import.meta.url)));
const now = "2026-10-21T04:00:00.000Z", nextDay = "2026-10-22T04:00:00.000Z";
const empty = () => ({ version: 1, records: [] });
const visit = (jobId = "training-job-1234", rest = {}) => ({ source: "online", jobId, name: "周三底线.mp4", ...rest });
const focus = { id: "review:feet", title: "启动与回位", question: "开始移动与停稳时，双脚的顺序是什么？", timestampMs: 2500 };
const id = lib.journalRecordId("online", "training-job-1234");
const storage = initial => {
  const values = new Map(Object.entries(initial || {}));
  return { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), values };
};
const controller = data => ({ ...data, ready: true, error: "", notice: "", updateRecord() {}, saveFocus() {}, selectFollowUp() {}, retrySave() {} });

test("migrates all valid legacy history without deleting or truncating old bytes", () => {
  const legacy = Array.from({ length: 25 }, (_, index) => ({ id: `training-job-${index}`, name: `练习${index}.mp4`, viewedAt: now }));
  legacy.push({ id: "../invalid", name: "坏记录", viewedAt: now });
  const oldBytes = JSON.stringify(legacy), disk = storage({ [lib.LEGACY_REPORTS_KEY]: oldBytes });
  const journal = lib.readTrainingJournal(disk);
  assert.equal(journal.records.length, 25);
  assert.equal(journal.records[0].name, "练习0");
  lib.writeTrainingJournal(disk, journal);
  assert.equal(disk.getItem(lib.LEGACY_REPORTS_KEY), oldBytes);
  assert.equal(lib.readTrainingJournal(disk).records.length, 25);
});

test("machine file names receive an editable human label while original names are retained", () => {
  const record = lib.visitTrainingReport(empty(), visit(undefined, { name: "abcdef1234567890.mp4" }), now).records[0];
  assert.equal(record.name, "10月21日训练复盘");
  assert.equal(record.originalName, "abcdef1234567890.mp4");
  const named = lib.visitTrainingReport(empty(), visit(undefined, { name: "正手启动.mp4" }), now).records[0];
  assert.equal(named.name, "正手启动");
});

test("revisit and legacy migration preserve edited names, theme, notes, and saved questions", () => {
  let journal = lib.visitTrainingReport(empty(), visit(), now);
  journal = lib.updateTrainingRecord(journal, id, { name: "下班后启动练习", theme: "回位", notes: "准备下周与教练讨论" });
  journal = lib.saveTrainingFocus(journal, id, focus);
  journal = lib.visitTrainingReport(journal, visit(undefined, { name: "服务器旧文件名.mp4" }), nextDay);
  journal = lib.migrateTrainingHistory(journal, [{ id: "training-job-1234", name: "旧名字.mp4", viewedAt: now }]);
  assert.equal(journal.records.length, 1);
  assert.equal(journal.records[0].name, "下班后启动练习");
  assert.equal(journal.records[0].theme, "回位");
  assert.equal(journal.records[0].notes, "准备下周与教练讨论");
  assert.deepEqual(journal.records[0].focuses, [focus]);
  assert.equal(journal.records[0].viewedAt, nextDay);
});

test("the chosen question persists, links only to a new submission, and keeps its snapshot", () => {
  let journal = lib.saveTrainingFocus(lib.visitTrainingReport(empty(), visit(), now), id, focus);
  journal = lib.selectTrainingFollowUp(journal, id, focus.id);
  journal = lib.trainingJournalOf(JSON.parse(JSON.stringify(journal)));
  assert.equal(journal.pendingFocus.focus.question, focus.question);
  journal = lib.visitTrainingReport(journal, visit("other-job-1234"), nextDay);
  assert.equal(journal.records[0].followUp, undefined);
  assert.ok(journal.pendingFocus);
  journal = lib.visitTrainingReport(journal, visit("newer-job-1234", { linkPending: true }), nextDay);
  assert.equal(journal.pendingFocus, undefined);
  assert.deepEqual(journal.records[0].followUp, { recordId: id, focus });
  journal = lib.saveTrainingFocus(journal, id, { ...focus, question: "后来改写的问题" });
  assert.equal(journal.records[0].followUp.focus.question, focus.question);
});

test("an imported report cannot acquire online media or silently open its embedded job", () => {
  const input = visit(undefined, { source: "imported", sourceId: "report.json:1234", result: { job_id: "training-job-1234", artifact_urls: { "annotated.mp4": "yes" }, runtime: { annotated_video_compatibility: { timing_preserved: true } } }, trajectory: { source: { start_timestamp_ms: 1000, end_timestamp_ms: 10000 } } });
  let journal = lib.visitTrainingReport(empty(), input, now);
  assert.notEqual(journal.records[0].id, id);
  assert.equal(journal.records[0].replay, undefined);
  journal = lib.saveTrainingFocus(journal, journal.records[0].id, focus);
  assert.equal(lib.replayTarget(journal.records[0], focus), null);
  const html = renderToStaticMarkup(createElement(TrainingJournalLibrary, { journal: controller(journal), onOpenReport() {}, onNewAnalysis() {} }));
  assert.match(html, /离线导入/); assert.match(html, /不会自动连接在线任务/);
  assert.doesNotMatch(html, /打开训练报告|<video|\/v1\/jobs\//);
});

test("comparison requires a live job-bound preserved replay plus a known source offset", () => {
  const input = visit(undefined, { result: { job_id: "training-job-1234", artifact_urls: { "annotated.mp4": "yes" }, runtime: { annotated_video_compatibility: { timing_preserved: true } } }, trajectory: { source: { start_timestamp_ms: 1000, end_timestamp_ms: 10000 } } });
  const record = lib.visitTrainingReport(empty(), input, now).records[0];
  assert.deepEqual(lib.replayTarget(record, focus), { src: "/v1/jobs/training-job-1234/artifacts/annotated.mp4", seconds: 1.5 });
  assert.equal(lib.trainingReplayOf({ ...input, trajectory: null }), undefined);
  assert.equal(lib.trainingReplayOf({ ...input, result: { ...input.result, job_id: "other-job-1234" } }), undefined);
  assert.equal(lib.trainingReplayOf({ ...input, result: { ...input.result, runtime: {} } }), undefined);
  assert.equal(lib.replayTarget(record, { ...focus, timestampMs: 500 }), null);
  assert.equal(lib.replayTarget(record, { ...focus, timestampMs: 10001 }), null);
  assert.equal(lib.replayTarget(record, { ...focus, timestampMs: NaN }), null);
});

test("storage failure is surfaced and never changes legacy or last successful saved state", () => {
  const oldBytes = JSON.stringify([{ id: "training-job-1234", name: "原始记录.mp4", viewedAt: now }]);
  const disk = storage({ [lib.LEGACY_REPORTS_KEY]: oldBytes });
  const journal = lib.readTrainingJournal(disk);
  lib.writeTrainingJournal(disk, journal);
  const successful = disk.getItem(lib.TRAINING_JOURNAL_KEY);
  const failing = { getItem: disk.getItem, setItem() { throw new Error("QuotaExceededError"); } };
  const modified = lib.saveTrainingFocus(journal, id, focus);
  assert.throws(() => lib.writeTrainingJournal(failing, modified), /QuotaExceededError/);
  assert.equal(disk.getItem(lib.TRAINING_JOURNAL_KEY), successful);
  assert.equal(disk.getItem(lib.LEGACY_REPORTS_KEY), oldBytes);
  const html = renderToStaticMarkup(createElement(TrainingJournalPanel, { journal: { ...controller(modified), error: "尚未保存：浏览器存储不可用。" }, recordId: id, suggestions: [focus] }));
  assert.match(html, /role="alert"/); assert.match(html, /重试保存/); assert.match(html, /尚未保存/);
  assert.doesNotMatch(html, /已保存在此浏览器/);
});

test("unknown versions and malformed modern storage are not replaced with an empty journal", () => {
  const future = storage({ [lib.TRAINING_JOURNAL_KEY]: '{"version":99,"records":[]}' });
  assert.throws(() => lib.readTrainingJournal(future), /无法识别/);
  assert.equal(future.getItem(lib.TRAINING_JOURNAL_KEY), '{"version":99,"records":[]}');
  assert.throws(() => lib.readTrainingJournal(storage({ [lib.TRAINING_JOURNAL_KEY]: "broken-json" })));
});

test("journal shows saved questions and scope, and offers a new-analysis association", () => {
  let journal = lib.saveTrainingFocus(lib.visitTrainingReport(empty(), visit(), now), id, focus);
  journal = lib.selectTrainingFollowUp(journal, id, focus.id);
  const html = renderToStaticMarkup(createElement(TrainingFollowUpPicker, { journal: controller(journal) }));
  assert.match(html, /周三底线 · 启动与回位/); assert.match(html, /selected=""/);
  assert.match(html, /提交新视频后/); assert.match(html, /不会据此自动判断进步/);
  const panel = renderToStaticMarkup(createElement(TrainingJournalPanel, { journal: controller(journal), recordId: id, suggestions: [focus] }));
  assert.match(panel, /不保存视频/); assert.match(panel, /清除浏览器数据会删除/);
  assert.match(panel, /训练名称/); assert.match(panel, /练习主题/); assert.match(panel, /自己的观察与备注/);
  assert.match(panel, /已经留下的问题/); assert.match(panel, /下次继续关注 · 已选择/);
});

// Exercise the actual persistence hook with a minimal deterministic hook host.
// This avoids a DOM dependency while testing caught failures and retained drafts.
const hookState = { slots: [], cursor: 0, effects: [] };
globalThis.__rallymateJournalHookTest = hookState;
const fakeReact = `const host=globalThis.__rallymateJournalHookTest;
export function useState(initial){const i=host.cursor++;if(!host.slots[i]){const entry={value:typeof initial==='function'?initial():initial};entry.set=next=>{entry.value=typeof next==='function'?next(entry.value):next};host.slots[i]=entry;}const slot=host.slots[i];return [slot.value,slot.set];}
export function useRef(value){const i=host.cursor++;return host.slots[i]??(host.slots[i]={current:value});}
export function useCallback(fn,deps){const i=host.cursor++,old=host.slots[i];if(!old||deps.some((d,n)=>d!==old.deps[n]))host.slots[i]={fn,deps};return host.slots[i].fn;}
export function useEffect(fn,deps){const i=host.cursor++,old=host.slots[i];if(!old||deps.some((d,n)=>d!==old.deps[n])){host.effects.push(fn);host.slots[i]={deps};}}
export function useId(){return 'test-id';} export function useMemo(fn){return fn();}`;
const fakeReactUrl = `data:text/javascript;base64,${Buffer.from(fakeReact).toString("base64")}`;
const hookFile = new URL("../app/TrainingJournal.tsx", import.meta.url);
let hookJs = ts.transpileModule(fs.readFileSync(hookFile, "utf8"), { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText;
hookJs = hookJs.replace(/^import\s+["'][^"']+\.css["'];?\s*$/gm, "").replace(/from "([^"]+)"/g, (_match, name) => `from ${JSON.stringify(name === "react" ? fakeReactUrl : name.startsWith(".") ? compiledUrl(new URL(name.endsWith(".json") ? name : `${name}.ts`, hookFile)) : import.meta.resolve(name))}`);
const { useTrainingJournal } = await import(`data:text/javascript;base64,${Buffer.from(hookJs).toString("base64")}`);
function renderHook() {
  hookState.cursor = 0;
  // eslint-disable-next-line react-hooks/rules-of-hooks -- A deterministic hook host tests persistence callbacks without mounting a DOM.
  const value = useTrainingJournal();
  const effects = hookState.effects.splice(0); effects.forEach(effect => effect());
  return value;
}
function resetHook() { hookState.slots = []; hookState.cursor = 0; hookState.effects = []; }

test("the actual hook retains a failed save and successfully retries without dropping edited data", () => {
  const previousWindow = globalThis.window;
  const disk = storage(); let failWrites = false;
  globalThis.window = { localStorage: { getItem: disk.getItem, setItem(key, value) { if (failWrites) throw new Error("quota"); disk.setItem(key, value); } }, addEventListener() {}, removeEventListener() {} };
  try {
    resetHook(); renderHook(); let journal = renderHook();
    const visitMethod = journal.visitReport;
    journal.visitReport(visit()); journal = renderHook();
    assert.equal(journal.visitReport, visitMethod, "visit callback remains stable after changing records");
    journal.updateRecord(id, { name: "用户编辑的名称", theme: "启动", notes: "保存前的笔记" });
    const savedBytes = disk.getItem(lib.TRAINING_JOURNAL_KEY);
    failWrites = true;
    assert.equal(journal.saveFocus(id, focus), false); journal = renderHook();
    assert.match(journal.error, /尚未保存/); assert.equal(journal.notice, "");
    assert.equal(journal.records[0].focuses[0].question, focus.question);
    assert.equal(disk.getItem(lib.TRAINING_JOURNAL_KEY), savedBytes);
    failWrites = false; journal.retrySave(); journal = renderHook();
    assert.equal(journal.error, ""); assert.match(journal.notice, /已保存在此浏览器/);
    const persisted = lib.readTrainingJournal(disk);
    assert.equal(persisted.records[0].name, "用户编辑的名称");
    assert.equal(persisted.records[0].notes, "保存前的笔记");
    assert.deepEqual(persisted.records[0].focuses, [focus]);
  } finally { globalThis.window = previousWindow; }
});

test("the actual hook blocks mutations when existing journal bytes cannot be read", () => {
  const previousWindow = globalThis.window;
  const disk = storage({ [lib.TRAINING_JOURNAL_KEY]: "unreadable-but-preserved" });
  globalThis.window = { localStorage: disk, addEventListener() {}, removeEventListener() {} };
  try {
    resetHook(); renderHook(); let journal = renderHook();
    assert.match(journal.error, /无法读取/);
    journal.visitReport(visit()); journal = renderHook();
    assert.match(journal.error, /没有写入/);
    assert.equal(disk.getItem(lib.TRAINING_JOURNAL_KEY), "unreadable-but-preserved");
    assert.equal(journal.records.length, 0);
  } finally { globalThis.window = previousWindow; }
});
