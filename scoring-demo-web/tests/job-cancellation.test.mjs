import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import ts from "typescript";

const hostKey = Symbol.for("rallymate.cancel-test-host");
const dataUrl = source => `data:text/javascript;base64,${Buffer.from(source).toString("base64")}`;
const modules = new Map();
function compiledUrl(file) {
  // Keep source JSON imports shared with production, without a generated copy.
  if (file.pathname.endsWith(".json")) return `data:text/javascript;base64,${Buffer.from(`export default ${fs.readFileSync(file, "utf8")}`).toString("base64")}`;
  if (modules.has(file.href)) return modules.get(file.href);
  let js = ts.transpileModule(fs.readFileSync(file, "utf8"), { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  js = js.replace(/from "([^"]+)"/g, (_match, name) => `from ${JSON.stringify(name.startsWith(".") ? compiledUrl(new URL(name.endsWith(".json") ? name : `${name}.ts`, file)) : import.meta.resolve(name))}`);
  const url = dataUrl(js); modules.set(file.href, url); return url;
}
const sessionUrl = compiledUrl(new URL("../app/lib/analysis-session.ts", import.meta.url));
const { AnalysisCancelledError } = await import(sessionUrl);
const hookUrl = dataUrl(["useState", "useRef", "useEffect", "useMemo", "useCallback"].map(name => `export const ${name} = (...args) => globalThis[Symbol.for("rallymate.cancel-test-host")].${name}(...args);`).join("\n"));
const apiUrl = dataUrl('export class RallyMateApiError extends Error {}; export const createApiClient = () => globalThis[Symbol.for("rallymate.cancel-test-host")].api;');
const sessionMockUrl = dataUrl(`export { AnalysisCancelledError, mergeAnalysisProgress } from ${JSON.stringify(sessionUrl)}; export const watchAnalysis = (...args) => globalThis[Symbol.for("rallymate.cancel-test-host")].watch(...args);`);
const shellUrl = dataUrl('export const ProductShell = () => null, TrainingHome = () => null, SessionLibrary = () => null, CaptureGuide = () => null, ProductIcon = () => null;');
const childUrl = dataUrl("export default function View() { return null; }");
// Journal persistence is covered independently; keep its callbacks stable so
// cancellation tests still exercise ScoreLab's real observation lifetimes.
const journalUrl = dataUrl(`export const journalRecordId=(source,id,sourceId)=>source+':'+(id||sourceId||'offline'); const journal={records:[],ready:true,error:'',notice:'',visitReport:visit=>journalRecordId(visit.source,visit.jobId,visit.sourceId),saveFocus:()=>true,updateRecord:()=>true,selectFollowUp:()=>true,retrySave:()=>{}}; export const useTrainingJournal=()=>journal; export const TrainingJournalPanel=()=>null,TrainingJournalLibrary=()=>null,TrainingFollowUpPicker=()=>null;`);
let componentJs = ts.transpileModule(fs.readFileSync(new URL("../app/ScoreLab.tsx", import.meta.url), "utf8"), { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText;
componentJs = componentJs.replace(/from "([^"]+)"/g, (_match, name) => `from ${JSON.stringify(name === "react" ? hookUrl : name === "./lib/api-client" ? apiUrl : name === "./lib/analysis-session" ? sessionMockUrl : name === "./ProductWorkspace" ? shellUrl : name === "./TrainingJournal" ? journalUrl : name.startsWith("./lib/") ? compiledUrl(new URL(`../app/${name}.ts`, import.meta.url)) : name.startsWith(".") ? childUrl : import.meta.resolve(name))}`);
const { default: ScoreLab } = await import(dataUrl(componentJs));
const id = "cancel-job-123456";
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };

// Drive the actual ScoreLab handlers. Only transport, child views and React scheduling are mocked.
function mount(t, jobId = id) {
  const slots = [], watches = [], requests = [], storage = new Map(), upload = deferred();
  let index = 0, effects = [], dirty = false, tree;
  const globals = {
    window: Object.assign(new EventTarget(), { location: { pathname: "/", search: jobId ? `?view=analysis&job=${jobId}` : "?view=analysis", hash: "" }, scrollTo() {}, setInterval: () => 1, clearInterval() {}, history: { pushState: (_a, _b, url) => { window.location.search = new URL(url, "https://local.test").search; }, replaceState: (_a, _b, url) => { window.location.search = new URL(url, "https://local.test").search; } } }),
    localStorage: { getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key) },
  };
  const restore = Object.entries(globals).map(([key, value]) => { const previous = Object.getOwnPropertyDescriptor(globalThis, key); Object.defineProperty(globalThis, key, { configurable: true, writable: true, value }); return () => { if (previous) Object.defineProperty(globalThis, key, previous); else delete globalThis[key]; }; });
  const oldHost = globalThis[hostKey];
  const memo = (compute, dependencies) => { const slot = index++, previous = slots[slot]; if (!previous || dependencies.some((value, offset) => !Object.is(value, previous.dependencies[offset]))) slots[slot] = { dependencies, value: compute() }; return slots[slot].value; };
  globalThis[hostKey] = {
    useState(initial) { const slot = index++; if (!(slot in slots)) slots[slot] = typeof initial === "function" ? initial() : initial; return [slots[slot], value => { const next = typeof value === "function" ? value(slots[slot]) : value; if (!Object.is(slots[slot], next)) { slots[slot] = next; dirty = true; } }]; },
    useRef(initial) { const slot = index++; return slots[slot] ??= { current: initial }; },
    useMemo: memo, useCallback: (callback, dependencies) => memo(() => callback, dependencies),
    useEffect(effect, dependencies) { const slot = index++, previous = slots[slot]; if (!previous || dependencies.some((value, offset) => !Object.is(value, previous.dependencies[offset]))) effects.push(() => { previous?.cleanup?.(); slots[slot] = { dependencies, cleanup: effect() }; }); },
    api: { cancelJob(jobId) { const request = { jobId, ...deferred() }; requests.push(request); return request.promise; }, submitVideo: () => upload.promise },
    watch(_client, jobId, signal, onProgress, _interval, onTrajectory) { const run = { jobId, signal, onProgress, onTrajectory, ...deferred() }; watches.push(run); signal.addEventListener("abort", () => run.reject(signal.reason), { once: true }); return run.promise; },
  };
  t.after(() => { for (const slot of slots) slot?.cleanup?.(); if (oldHost) globalThis[hostKey] = oldHost; else delete globalThis[hostKey]; restore.forEach(callback => callback()); });
  function render() { let runs = 0; do { dirty = false; index = 0; effects = []; tree = ScoreLab(); effects.forEach(callback => callback()); assert.ok(++runs < 12, "component did not settle"); } while (dirty); }
  function nodes(node) { return Array.isArray(node) ? node.flatMap(item => nodes(item)) : node?.props ? [node, ...nodes(node.props.children)] : []; }
  function text(node) { return Array.isArray(node) ? node.map(text).join("") : node?.props ? text(node.props.children) : typeof node === "string" || typeof node === "number" ? String(node) : ""; }
  render();
  return { render, requests, watches, storage, upload, text: () => text(tree), find: predicate => nodes(tree).find(predicate), button: label => nodes(tree).find(node => node.type === "button" && text(node) === label),
    async flush() { await Promise.resolve(); await Promise.resolve(); render(); },
    progress(job) { watches.at(-1).onProgress(job); render(); },
    changeJob(jobId) { window.location.search = `?view=analysis&job=${jobId}`; window.dispatchEvent(new Event("popstate")); render(); },
  };
}

test("queued cancellation deduplicates clicks and stops observation only after server confirmation", async t => {
  const app = mount(t), run = app.watches[0], button = app.button("取消分析");
  const preview = { job_id: id, source: { is_partial: true } }; run.onTrajectory(preview); app.render();
  button.props.onClick(); button.props.onClick(); app.render();
  assert.equal(app.requests.length, 1); assert.equal(app.requests[0].jobId, id);
  assert.equal(run.signal.aborted, false); assert.equal(app.button("正在发送…").props.disabled, true);
  assert.doesNotMatch(app.text(), /本次分析已取消/);
  app.requests[0].resolve({ id, status: "cancelled", cancel_requested: true }); await app.flush();
  assert.equal(run.signal.aborted, true); assert.match(app.text(), /本次分析已取消/);
  assert.equal(app.button("取消分析"), undefined);
  assert.equal(app.find(node => node.props.evidence?.trajectory === preview).props.evidence.trajectory, preview);
  assert.equal(app.find(node => node.props.replay && "result" in node.props), undefined, "cancelled preview must not masquerade as a completed empty report");
  assert.equal(app.storage.get("rallymate.activeJob"), id);
});

test("running acknowledgement stays pending through stale polls until the worker confirms cancelled", async t => {
  const app = mount(t); app.progress({ id, status: "running", progress: 25 });
  app.button("取消分析").props.onClick(); app.requests[0].resolve({ id, status: "running", cancel_requested: true }); await app.flush();
  assert.equal(app.watches[0].signal.aborted, false); assert.equal(app.button("正在取消…").props.disabled, true);
  assert.match(app.text(), /等待服务器确认停止/); assert.doesNotMatch(app.text(), /本次分析已取消/);
  app.progress({ id, status: "running", cancel_requested: false, progress: 30 });
  assert.equal(app.button("正在取消…").props.disabled, true);
  app.progress({ id, status: "cancelled", cancel_requested: true });
  app.watches[0].reject(new AnalysisCancelledError()); await app.flush();
  assert.match(app.text(), /本次分析已取消/); assert.doesNotMatch(app.text(), /这次分析暂时无法完成/);
});

test("lost cancellation acknowledgement leaves the job resumable and a retry uses the same job", async t => {
  const app = mount(t); app.progress({ id, status: "running" });
  app.button("取消分析").props.onClick(); app.requests[0].reject(new Error("network lost")); await app.flush();
  assert.equal(app.watches[0].signal.aborted, false); assert.match(app.text(), /暂未确认取消请求/);
  assert.equal(app.button("取消分析").props.disabled, false);
  app.watches[0].reject(new Error("连接中断，稍后可继续读取结果")); await app.flush();
  assert.ok(app.button("继续读取结果"));
  app.button("取消分析").props.onClick(); app.render();
  assert.deepEqual(app.requests.map(request => request.jobId), [id, id]);
  app.button("继续读取结果").props.onClick(); app.render();
  assert.equal(app.watches.length, 2);
  app.requests[1].resolve({ id, status: "cancelled" }); await app.flush();
  assert.equal(app.watches[1].signal.aborted, false); assert.ok(app.button("取消分析"), "stale reply must not affect a new observation run");
});

test("a completed report survives a late running cancellation response", async t => {
  const app = mount(t); app.progress({ id, status: "running" }); app.button("取消分析").props.onClick();
  const job = { id, status: "succeeded", original_filename: "training.mp4" }, result = { job_id: id, status: "ready", measurement: "preserve" };
  app.progress(job); app.watches[0].resolve({ job, result, trajectory: null, assessment: null, warning: null }); await app.flush();
  app.requests[0].resolve({ id, status: "running", cancel_requested: true }); await app.flush();
  const report = app.find(node => node.props.replay && "result" in node.props);
  assert.equal(report.props.result, result); assert.equal(report.props.status, "succeeded"); assert.equal(report.props.pending, false);
  assert.equal(app.button("取消分析"), undefined); assert.doesNotMatch(app.text(), /本次分析已取消/);
  assert.equal(JSON.parse(app.storage.get("rallymate.recentReports"))[0].id, id);
});

test("poll confirmation supersedes an earlier lost cancellation acknowledgement", async t => {
  const app = mount(t); app.progress({ id, status: "running" });
  app.button("取消分析").props.onClick(); app.requests[0].reject(new Error("response lost")); await app.flush();
  assert.match(app.text(), /暂未确认取消请求/);
  app.progress({ id, status: "running", cancel_requested: true });
  assert.doesNotMatch(app.text(), /暂未确认取消请求/);
  assert.equal(app.button("正在取消…").props.disabled, true);
});

test("late cancellation replies from a previous job cannot cancel the newly opened video", async t => {
  const app = mount(t); app.button("取消分析").props.onClick(); app.changeJob("different-job-123");
  app.requests[0].resolve({ id, status: "cancelled" }); await app.flush();
  assert.equal(app.watches[1].signal.aborted, false); assert.ok(app.button("取消分析"));
  app.button("取消分析").props.onClick();
  assert.equal(app.requests[1].jobId, "different-job-123");
  app.requests[1].resolve({ id: "different-job-123", status: "cancelled" }); await app.flush();
});

test("no cancellation button exists during upload before a server job has been created", async t => {
  const app = mount(t, null);
  const videoInput = app.find(node => node.type === "input" && node.props.accept?.startsWith("video/"));
  videoInput.props.onChange({ target: { files: [new File(["video"], "practice.mp4", { type: "video/mp4" })], value: "" } }); app.render();
  app.button("开始分析").props.onClick(); app.render();
  assert.equal(app.button("取消分析"), undefined); assert.equal(app.requests.length, 0);
  app.upload.resolve({ id, status: "queued" }); await app.flush();
  assert.ok(app.button("取消分析")); assert.equal(app.watches[0].jobId, id);
});
