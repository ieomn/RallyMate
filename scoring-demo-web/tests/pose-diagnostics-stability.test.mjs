import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import ts from "typescript";

const hostKey = Symbol.for("rallymate.pose-diagnostics-test-host");
const dataUrl = source => `data:text/javascript;base64,${Buffer.from(source).toString("base64")}`;
const hooksUrl = dataUrl(["useState", "useRef", "useEffect", "useMemo", "useCallback"].map(name => `export const ${name} = (...args) => globalThis[Symbol.for("rallymate.pose-diagnostics-test-host")].${name}(...args);`).join("\n"));
const apiUrl = dataUrl('export class RallyMateApiError extends Error {}; export const createApiClient = (...args) => globalThis[Symbol.for("rallymate.pose-diagnostics-test-host")].createApi(...args);');
const childUrl = dataUrl("export default function View() { return null; }");
const shellUrl = dataUrl("export const ProductShell = () => null, TrainingHome = () => null, SessionLibrary = () => null, CaptureGuide = () => null, ProductIcon = () => null;");
// Persistence has separate coverage. This stable journal preserves the parent
// memoization contract while this harness exercises actual pose read effects.
const journalUrl = dataUrl(`export const journalRecordId=(source,id,sourceId)=>source+':'+(id||sourceId||'offline'); const journal={records:[],ready:true,error:'',notice:'',visitReport:visit=>journalRecordId(visit.source,visit.jobId,visit.sourceId),saveFocus:()=>true,updateRecord:()=>true,selectFollowUp:()=>true,retrySave:()=>{}}; export const useTrainingJournal=()=>journal; export const TrainingJournalPanel=()=>null,TrainingJournalLibrary=()=>null,TrainingFollowUpPicker=()=>null;`);
const modules = new Map();
function compiledUrl(file) {
  // Keep source JSON imports shared with production, without a generated copy.
  if (file.pathname.endsWith(".json")) return `data:text/javascript;base64,${Buffer.from(`export default ${fs.readFileSync(file, "utf8")}`).toString("base64")}`;
  if (modules.has(file.href)) return modules.get(file.href);
  let js = ts.transpileModule(fs.readFileSync(file, "utf8"), { fileName: file.pathname, compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  js = js.replace(/from "([^"]+)"/g, (_match, name) => {
    const local = new URL(name.endsWith(".json") ? name : `${name}.ts`, file);
    return `from ${JSON.stringify(name === "react" ? hooksUrl : name.startsWith(".") ? compiledUrl(fs.existsSync(local) ? local : new URL(`${name}.tsx`, file)) : import.meta.resolve(name))}`;
  });
  const url = dataUrl(js); modules.set(file.href, url); return url;
}
const sessionUrl = compiledUrl(new URL("../app/lib/analysis-session.ts", import.meta.url));
const sessionMockUrl = dataUrl(`export { AnalysisCancelledError, mergeAnalysisProgress } from ${JSON.stringify(sessionUrl)}; export const watchAnalysis = (...args) => globalThis[Symbol.for("rallymate.pose-diagnostics-test-host")].watch(...args);`);
let componentJs = ts.transpileModule(fs.readFileSync(new URL("../app/ScoreLab.tsx", import.meta.url), "utf8"), { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText;
componentJs = componentJs.replace(/from "([^"]+)"/g, (_match, name) => `from ${JSON.stringify(name === "react" ? hooksUrl : name === "./lib/api-client" ? apiUrl : name === "./lib/analysis-session" ? sessionMockUrl : name === "./ProductWorkspace" ? shellUrl : name === "./TrainingJournal" ? journalUrl : name === "./BallTrajectoryViewer" ? compiledUrl(new URL("../app/BallTrajectoryViewer.tsx", import.meta.url)) : name.startsWith("./lib/") ? compiledUrl(new URL(`../app/${name}.ts`, import.meta.url)) : name.startsWith(".") ? childUrl : import.meta.resolve(name))}`);
const { default: ScoreLab } = await import(dataUrl(componentJs));
const { createApiClient: realCreateApiClient } = await import(compiledUrl(new URL("../app/lib/api-client.ts", import.meta.url)));
const jobId = "pose-diagnostic-job-123";
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const nodes = node => Array.isArray(node) ? node.flatMap(nodes) : node?.props ? [node, ...nodes(node.props.children)] : [];
const text = node => Array.isArray(node) ? node.map(text).join("") : node?.props ? text(node.props.children) : typeof node === "string" || typeof node === "number" ? String(node) : "";
const result = id => ({ job_id: id, artifact_urls: { "annotated.mp4": "available" }, runtime: { annotated_video_compatibility: { timing_preserved: true } } });
function preview(request, x = .4) {
  const start = request.options.startMs;
  return { schema_version: "1.0.0", result_kind: "observed_pose_playback", job_id: request.jobId, coordinate_space: "normalized_frame_0_1", joint_schema: "halpe26_named", min_keypoint_confidence: .35, skeleton_edges: [],
    source: { requested_start_ms: start, requested_end_ms: start + 10000, width: 1280, height: 720, returned_frames: 1, is_sampled: false, is_partial: false, timestamp_semantics: "source_frame_timestamp_ms" },
    frames: [{ frame_index: start / 1000, timestamp_ms: start, valid_until_ms: start + 100, person_track_id: 1, selection_epoch: 0, selection_status: "selected", width: 1280, height: 720, keypoints: [{ name: "left_wrist", x, y: .5, confidence: .9 }] }] };
}

// Real ScoreLab -> ReplayPanel -> BallTrajectoryViewer -> usePosePlayback, with
// separate React-like hook scopes. Only transport, peripheral views and the DOM
// scheduler are substituted. Production effect bodies/cleanup execute with
// Object.is dependency scheduling, rather than unconditional mocked useMemo.
function mount(t, { realTransport = false } = {}) {
  const scopes = new Map(), requests = [], watches = [], clients = [], storage = new Map();
  let current, dirty = false, tree, replayProps, viewerProps, viewerTree, replayKey, viewerKey;
  const cleanupScope = name => { for (const slot of scopes.get(name)?.slots ?? []) slot?.cleanup?.(); scopes.delete(name); };
  const run = (name, callback) => {
    current = scopes.get(name) ?? { slots: [], index: 0, effects: [] }; scopes.set(name, current);
    current.index = 0; current.effects = [];
    const output = callback(), effects = current.effects; current = null;
    effects.forEach(effect => effect()); return output;
  };
  const globals = {
    window: Object.assign(new EventTarget(), { location: { pathname: "/", search: `?view=analysis&job=${jobId}`, hash: "" }, scrollTo() {}, setInterval: () => 1, clearInterval() {}, history: { pushState: (_a, _b, url) => { window.location.search = new URL(url, "https://local.test").search; }, replaceState: (_a, _b, url) => { window.location.search = new URL(url, "https://local.test").search; } } }),
    document: new EventTarget(), requestAnimationFrame: () => 1, cancelAnimationFrame() {},
    localStorage: { getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key) },
  };
  const restore = Object.entries(globals).map(([key, value]) => { const before = Object.getOwnPropertyDescriptor(globalThis, key); Object.defineProperty(globalThis, key, { configurable: true, writable: true, value }); return () => { if (before) Object.defineProperty(globalThis, key, before); else delete globalThis[key]; }; });
  const oldHost = globalThis[hostKey];
  const changed = (previous, dependencies) => !previous || !dependencies || previous.dependencies.length !== dependencies.length || dependencies.some((value, index) => !Object.is(value, previous.dependencies[index]));
  const memo = (compute, dependencies) => { const slot = current.index++, previous = current.slots[slot]; if (changed(previous, dependencies)) current.slots[slot] = { dependencies, value: compute() }; return current.slots[slot].value; };
  globalThis[hostKey] = {
    useState(initial) { const scope = current, slot = scope.index++; if (!(slot in scope.slots)) scope.slots[slot] = typeof initial === "function" ? initial() : initial; return [scope.slots[slot], value => { const next = typeof value === "function" ? value(scope.slots[slot]) : value; if (!Object.is(scope.slots[slot], next)) { scope.slots[slot] = next; dirty = true; } }]; },
    useRef(initial) { const slot = current.index++; return current.slots[slot] ??= { current: initial }; },
    useMemo: memo, useCallback: (callback, dependencies) => memo(() => callback, dependencies),
    useEffect(effect, dependencies) { const scope = current, slot = scope.index++, previous = scope.slots[slot]; if (changed(previous, dependencies)) scope.effects.push(() => { previous?.cleanup?.(); scope.slots[slot] = { dependencies, cleanup: effect() }; }); },
    createApi(_config, _fetch, _decode, observer) {
      if (realTransport) {
        const api = realCreateApiClient({ baseUrl: "" }, async (url, init) => {
          const parsed = new URL(url, "https://local.test");
          assert.match(parsed.pathname, /\/pose-preview$/);
          assert.equal(init.method, "GET");
          const body = deferred();
          const request = { jobId: parsed.pathname.split("/").at(-2), options: { startMs: Number(parsed.searchParams.get("start_ms")), signal: init.signal },
            resolve: value => body.resolve(JSON.stringify(value)), reject: body.reject };
          requests.push(request);
          const response = new Response(null, { status: 200 });
          response.text = () => body.promise;
          return response;
        }, undefined, observer);
        clients.push(api); return api;
      }
      const api = { getPosePreview(id, options) {
        const request = { jobId: id, options, ...deferred(), diagnostic(stage, extra = {}) { observer?.({ operation: "pose_preview", stage, outcome: "success", category: null, requestId: "11111111-1111-4111-8111-111111111111", httpStatus: 200, startedAt: "2026-10-19T00:00:00.000Z", elapsedMs: 10, bodyComplete: stage !== "headers", ...extra }); } };
        requests.push(request); request.diagnostic("headers"); return request.promise;
      } };
      clients.push(api); return api;
    },
    watch(api, id, signal, onProgress, _interval, onTrajectory) {
      const watch = { api, jobId: id, signal, onProgress, onTrajectory, ...deferred() }; watches.push(watch);
      signal.addEventListener("abort", () => watch.reject(signal.reason), { once: true }); return watch.promise;
    },
  };
  t.after(() => { for (const name of [...scopes.keys()]) cleanupScope(name); if (oldHost) globalThis[hostKey] = oldHost; else delete globalThis[hostKey]; restore.forEach(callback => callback()); });
  function render() {
    let count = 0;
    do {
      dirty = false; tree = run("score", () => ScoreLab());
      const report = nodes(tree).find(node => node.props.replay && "result" in node.props);
      const replay = report ? report.props.replay.props.children : nodes(tree).find(node => "replaySignal" in node.props && "evidence" in node.props);
      if (replay) {
        const nextReplayKey = report?.props.replay.key ?? replay.props.evidence.jobId;
        if (nextReplayKey !== replayKey) { cleanupScope("replay"); cleanupScope("viewer"); replayKey = nextReplayKey; viewerKey = undefined; }
        replayProps = replay.props;
        const replayTree = run("replay", () => replay.type(replay.props));
        const viewer = nodes(replayTree).find(node => "loadPoseWindow" in node.props);
        if (viewer.key !== viewerKey) { cleanupScope("viewer"); viewerKey = viewer.key; }
        viewerProps = viewer.props; viewerTree = run("viewer", () => viewer.type(viewer.props));
      } else { cleanupScope("replay"); cleanupScope("viewer"); viewerTree = null; replayProps = undefined; viewerProps = undefined; replayKey = undefined; viewerKey = undefined; }
      assert.ok(++count < 16, "diagnostic updates must settle without recreating the pose loader/effect");
    } while (dirty);
  }
  const app = {
    requests, watches, clients, storage, render,
    get loader() { return viewerProps.loadPoseWindow; },
    get pending() { return replayProps?.pending ?? true; },
    get focus() { return nodes(viewerTree).find(node => "focus" in node.props)?.props.focus ?? null; },
    get diagnostics() { return nodes(tree).find(node => "diagnostics" in node.props)?.props.diagnostics ?? []; },
    async flush() { for (let n = 0; n < 8; n++) await Promise.resolve(); render(); },
    async complete() { const watch = watches.at(-1); watch.onProgress({ id: watch.jobId, status: "succeeded" }); watch.resolve({ job: { id: watch.jobId, status: "succeeded" }, result: result(watch.jobId), trajectory: null, assessment: null, warning: null }); await app.flush(); },
    seek(timestampMs) { const event = new Event("rallymate:seek-video"); event.detail = { timestampMs, jobId: replayProps.evidence.jobId }; window.dispatchEvent(event); render(); },
    togglePose(enabled) { const label = nodes(viewerTree).find(node => node.type === "label" && text(node) === "动作标记"); const input = nodes(label).find(node => node.type === "input"); input.props.onChange({ target: { checked: enabled } }); render(); },
    changeJob(id) { window.location.search = `?view=analysis&job=${id}`; window.dispatchEvent(new Event("popstate")); render(); },
    async importSameJob() { const input = nodes(tree).find(node => node.type === "input" && node.props.accept === "application/json,.json"); const content = JSON.stringify({ schemaVersion: "rallymate-practice-report/1", jobId, result: result(jobId) }); await input.props.onChange({ target: { files: [new File([content], "same-job.json", { type: "application/json" })], value: "" } }); await app.flush(); },
  };
  render(); return app;
}

test("actual parent diagnostics preserve one observed API, stable pose loader and in-flight effect", async t => {
  const app = mount(t);
  assert.equal(app.requests.length, 0, "pending analysis must not fetch playback windows");
  await app.complete();
  assert.equal(app.clients.length, 1, "replay must share the parent's observed client");
  assert.equal(app.watches[0].api, app.clients[0]);
  const request = app.requests[0], loader = app.loader;
  assert.equal(app.requests.length, 1);
  assert.deepEqual([request.jobId, request.options.startMs, request.options.durationMs, request.options.sampleLimit], [jobId, 0, 10000, 600]);
  for (const stage of ["body", "json", "decode"]) {
    request.diagnostic(stage); app.render();
    assert.equal(app.loader, loader); assert.equal(request.options.signal.aborted, false);
    assert.equal(app.requests.length, 1); assert.equal(app.watches.length, 1);
    assert.equal(app.diagnostics.at(-1).stage, stage); assert.equal(app.diagnostics.at(-1).operation, "pose_preview");
  }
  request.resolve(preview(request)); await app.flush();
  assert.equal(app.focus.markers[0].x, .4);
  assert.deepEqual(app.requests.map(row => row.options.startMs), [0, 10000]);
  const prefetch = app.requests[1]; prefetch.diagnostic("body"); app.render();
  assert.equal(app.loader, loader); assert.equal(prefetch.options.signal.aborted, false);
  assert.equal(app.requests.length, 2); assert.equal(app.clients.length, 1);
});

test("a window change aborts its pending read and a late response cannot publish or prefetch", async t => {
  const app = mount(t); await app.complete();
  const old = app.requests[0]; app.seek(20050);
  assert.equal(old.options.signal.aborted, true); assert.equal(app.requests.length, 2);
  const current = app.requests[1]; assert.equal(current.options.startMs, 20000);
  old.resolve(preview(old, .2)); await app.flush();
  assert.equal(app.focus, null); assert.equal(app.requests.length, 2);
  current.resolve(preview(current, .8)); await app.flush();
  assert.equal(app.focus.markers[0].x, .8);
  assert.deepEqual(app.requests.map(row => row.options.startMs), [0, 20000, 30000]);
});

test("switching jobs aborts the old pose window and cannot publish another job's late data", async t => {
  const app = mount(t); await app.complete(); const old = app.requests[0];
  app.changeJob("pose-other-job-456");
  assert.equal(old.options.signal.aborted, true); assert.equal(app.pending, true);
  assert.equal(app.requests.length, 1);
  old.resolve(preview(old, .2)); await app.flush(); assert.equal(app.focus, null);
  await app.complete(); const current = app.requests[1];
  assert.equal(current.jobId, "pose-other-job-456"); assert.equal(current.options.startMs, 0);
  current.resolve(preview(current, .7)); await app.flush();
  assert.equal(app.focus.markers[0].x, .7);
  assert.deepEqual(app.requests.map(row => row.jobId), [jobId, "pose-other-job-456", "pose-other-job-456"]);
});

test("disabling pose aborts a pending window and ignores its late completion", async t => {
  const app = mount(t); await app.complete(); const old = app.requests[0];
  app.togglePose(false); assert.equal(old.options.signal.aborted, true);
  old.resolve(preview(old, .2)); await app.flush();
  assert.equal(app.focus, null); assert.equal(app.requests.length, 1);
  app.togglePose(true); assert.equal(app.requests.length, 2, "an aborted response must not seed the cache");
  const current = app.requests[1]; current.resolve(preview(current, .6)); await app.flush();
  assert.equal(app.focus.markers[0].x, .6);
});

test("same-job offline import clears the active job and cannot fetch online pose windows", async t => {
  const app = mount(t); await app.complete(); const old = app.requests[0];
  app.togglePose(false); await app.importSameJob();
  assert.equal(app.pending, false); assert.equal(app.storage.has("rallymate.activeJob"), false);
  assert.equal(window.location.search.includes("job="), false); assert.equal(app.watches.length, 1);
  assert.equal(app.watches[0].signal.aborted, true); assert.equal(old.options.signal.aborted, true);
  app.togglePose(true);
  assert.equal(app.loader, undefined, "an imported job ID cannot authorize an online pose loader");
  assert.equal(app.requests.length, 1);
  old.resolve(preview(old, .6)); await app.flush();
  assert.equal(app.focus, null); assert.equal(app.clients.length, 1);
  assert.equal(app.requests.length, 1, "a late online response cannot populate or prefetch offline evidence");
});

test("old pose body diagnostics cannot enter the next job before passive effect cleanup", async t => {
  const app = mount(t, { realTransport: true }); await app.complete(); await app.flush();
  const old = app.requests[0], oldRequestId = app.diagnostics.at(-1).requestId;
  assert.equal(app.diagnostics.at(-1).stage, "headers");
  // The source-switch handler must invalidate requests before passive cleanup.
  // Force a transport to finish anyway; it must not repopulate cleared rows.
  window.location.search = "?view=analysis&job=pose-other-job-456";
  window.dispatchEvent(new Event("popstate"));
  assert.equal(app.watches.length, 2);
  assert.equal(old.options.signal.aborted, true, "source invalidation cannot wait for the pose effect cleanup");
  old.resolve(preview(old));
  for (let n = 0; n < 8; n++) await Promise.resolve();
  app.render();
  assert.equal(old.options.signal.aborted, true);
  assert.equal(app.focus, null);
  assert.deepEqual(app.diagnostics.filter(row => row.requestId === oldRequestId), [], "a response from the invalidated source must not repopulate its diagnostics");
});

test("direct same-job import invalidates pose reads and reopening online starts a fresh stable scope", async t => {
  const app = mount(t, { realTransport: true }); await app.complete(); await app.flush();
  const old = app.requests[0], oldLoader = app.loader;
  assert.equal(app.requests.length, 1);
  await app.importSameJob();
  assert.equal(app.pending, false); assert.equal(app.watches.length, 1);
  assert.equal(app.storage.has("rallymate.activeJob"), false);
  assert.equal(old.options.signal.aborted, true);
  assert.equal(app.loader, undefined, "offline evidence must not inherit the previous online loader");
  old.resolve(preview(old, .2)); await app.flush();
  assert.equal(app.requests.length, 1); assert.equal(app.focus, null);
  // Explicitly reopening the service job grants a new source scope. The same
  // job/time window must fetch anew, while later diagnostics keep it stable.
  app.changeJob(jobId); await app.complete(); await app.flush();
  assert.equal(app.watches.length, 2);
  assert.equal(app.requests.length, 2, "a live reopen must not reuse the invalidated read or cache");
  assert.notEqual(app.loader, oldLoader);
  const current = app.requests[1], currentLoader = app.loader;
  assert.deepEqual([current.jobId, current.options.startMs], [jobId, 0]);
  assert.equal(current.options.signal.aborted, false);
  await app.flush();
  assert.equal(app.loader, currentLoader); assert.equal(app.requests.length, 2);
  current.resolve(preview(current, .7)); await app.flush();
  assert.equal(app.focus.markers[0].x, .7);
  assert.equal(app.clients.length, 1);
  assert.deepEqual(app.requests.map(row => row.options.startMs), [0, 0, 10000]);
});
