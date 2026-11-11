import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import ts from "typescript";

const tick = () => new Promise(resolve => setTimeout(resolve, 0));
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
function harness(t, { manualPages = false } = {}) {
  const slots = []; let cursor = 0, pendingEffects = [];
  const hooks = {
    useState(initial) { const i = cursor++; if (!(i in slots)) slots[i] = typeof initial === "function" ? initial() : initial;
      return [slots[i], value => slots[i] = typeof value === "function" ? value(slots[i]) : value]; },
    useRef(value) { const i = cursor++; return slots[i] ??= { current: value }; },
    useCallback(callback, deps) { const i = cursor++, old = slots[i]; if (!old || deps.some((dep, n) => !Object.is(dep, old.deps[n]))) slots[i] = { callback, deps }; return slots[i].callback; },
    useEffect(effect, deps) { const i = cursor++, old = slots[i];
      if (!old || deps.some((dep, n) => !Object.is(dep, old.deps[n]))) {
        slots[i] = { deps, cleanup: old?.cleanup };
        pendingEffects.push(() => { slots[i].cleanup?.(); slots[i].cleanup = effect(); });
      }
    },
  };
  const action = n => ({ action_id: `action-${n}`, event_id: `action-${n}`, label: `动作${n}`, start_ms: n * 1000, end_ms: n * 1000 + 500, person_track_id: 1, source_track_id: 2, indicator_count: 1, evidence_status: "available" });
  const actions = [action(1), action(2)];
  let summary = { job_id: "component-job-123", source: { video_sha256: "a".repeat(64), frames_sha256: "b".repeat(64), evidence_revision: "c".repeat(64) },
    video: { duration_ms: 3000 }, processing: { processed_frames: 90 }, scoring: { status: "calibration_required", event_counts: { FS01: 2 }, message: "需校准" } };
  const requests = [], pages = [], controller = new AbortController();
  const api = {
    getReportActions: (jobId, source, offset, signal) => {
      if (!manualPages) return Promise.resolve({ status: "complete", actions, pagination: { total: 2, offset: 0, limit: 20, next_offset: null } });
      const request = { ...deferred(), jobId, source, offset, signal }; pages.push(request); return request.promise;
    },
    getReportActionDetail: (jobId, source, action, offset, signal) => { const request = { ...deferred(), jobId, source, action, offset, signal }; requests.push(request); return request.promise; },
  };
  const modules = { react: hooks, "react/jsx-runtime": { jsx: (type, props) => typeof type === "function" ? type(props) : ({ type, props }), jsxs: (type, props) => typeof type === "function" ? type(props) : ({ type, props }), Fragment: "fragment" },
    "./lib/segmented-report": { reportTime: ms => String(ms) }, "./lib/official-mesh-media": { watchMeshPreview: async () => {} },
    "./FootworkReviewPanel": { default: "FootworkReviewPanel" }, "./ProcessScorePanel": { default: "ProcessScorePanel" },
    "./lib/original-video-timing": { originalPoseTimeBinding: () => null }, "./OfficialMeshReplay": { default: "OfficialMeshReplay" }, "./segmented-report.css": {} };
  const code = ts.transpileModule(fs.readFileSync(new URL("../app/SegmentedReport.tsx", import.meta.url), "utf8"), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  const exported = {}; new Function("require", "exports", code)(name => modules[name], exported);
  let tree;
  const priorWindow = globalThis.window; globalThis.window = { addEventListener() {}, removeEventListener() {} }; t.after(() => { slots.forEach(value => value?.cleanup?.()); globalThis.window = priorWindow; });
  const render = () => { cursor = 0; pendingEffects = []; tree = exported.default({ summary, api, signal: controller.signal }); pendingEffects.forEach(run => run()); return tree; };
  const nodes = value => !value || typeof value !== "object" ? [] : Array.isArray(value) ? value.flatMap(nodes) : [value, ...nodes(value.props?.children)];
  const text = value => value == null || typeof value === "boolean" ? "" : typeof value !== "object" ? String(value) : Array.isArray(value) ? value.map(text).join("") : text(value.props?.children);
  const choose = n => nodes(tree).find(node => node.type === "button" && node.props.children?.[0]?.props?.children === `动作${n}`).props.onClick();
  const resolve = (request, label) => request.resolve({ status: "complete", pagination: { total: 1, offset: 0, limit: 4, next_offset: null }, records: [{ indicator_id: label, person_track_id: 1, features: [] }] });
  return { render, choose, requests, resolve, controller, pages,
    resolvePage: (request, ids = [1, 2], next = null) => request.resolve({ status: "complete", actions: ids.map(action), pagination: { total: 40, offset: request.offset, limit: 20, next_offset: next } }),
    changeSource: () => { summary = { ...summary, source: { ...summary.source, evidence_revision: "d".repeat(64) } }; },
    button: label => nodes(tree).find(node => node.type === "button" && text(node) === label), text: () => text(tree) };
}

test("switching actions cancels the first request immediately and ignores its late response", async t => {
  const ui = harness(t); ui.render(); await tick(); ui.render();
  ui.choose(1); ui.render(); assert.equal(ui.requests.length, 1);
  ui.choose(2); assert.equal(ui.requests[0].signal.aborted, true); ui.render();
  assert.equal(ui.requests.length, 2); assert.equal(ui.requests[1].action.action_id, "action-2");
  ui.resolve(ui.requests[0], "旧动作证据"); await tick(); ui.render(); assert.doesNotMatch(ui.text(), /旧动作证据/);
  ui.resolve(ui.requests[1], "新动作证据"); await tick(); ui.render(); assert.match(ui.text(), /新动作证据/);
});

test("clicking the same selected action restarts its request and navigation suppresses late results", async t => {
  const ui = harness(t); ui.render(); await tick(); ui.render();
  ui.choose(1); ui.render(); ui.choose(1); ui.render();
  assert.equal(ui.requests.length, 2); assert.equal(ui.requests[0].signal.aborted, true);
  ui.controller.abort(); assert.equal(ui.requests[1].signal.aborted, true);
  ui.resolve(ui.requests[1], "导航后的旧证据"); await tick(); ui.render(); assert.doesNotMatch(ui.text(), /导航后的旧证据/);
});

test("stopping detail and retrying preserve cancellation while immediately replacing status", async t => {
  const ui = harness(t); ui.render(); await tick(); ui.render();
  ui.choose(1); ui.render(); assert.match(ui.text(), /正在读取此动作的完整证据页/);
  ui.button("停止读取片段").props.onClick(); assert.equal(ui.requests[0].signal.aborted, true); ui.render();
  assert.match(ui.text(), /已停止本次读取/); assert.doesNotMatch(ui.text(), /正在读取此动作的完整证据页/);
  ui.button("重试片段证据").props.onClick(); ui.render(); assert.equal(ui.requests.length, 2);
  assert.doesNotMatch(ui.text(), /已停止本次读取/); assert.match(ui.text(), /正在读取此动作的完整证据页/);
  ui.resolve(ui.requests[0], "已停止的旧证据"); await tick(); ui.render(); assert.doesNotMatch(ui.text(), /已停止的旧证据/);
  ui.requests[1].reject(new Error("当前片段网络错误")); await tick(); ui.render(); assert.match(ui.text(), /当前片段网络错误/);
  ui.button("重试片段证据").props.onClick(); ui.render(); assert.doesNotMatch(ui.text(), /当前片段网络错误/);
  ui.resolve(ui.requests[2], "重试后的证据"); await tick(); ui.render(); assert.match(ui.text(), /重试后的证据/);
});

test("list pagination retains the current page while pending and retry clears the failed read", async t => {
  const ui = harness(t, { manualPages: true }); ui.render(); assert.match(ui.text(), /正在读取动作列表/);
  ui.resolvePage(ui.pages[0], [1, 2], 20); await tick(); ui.render();
  ui.button("下一页动作").props.onClick(); ui.render();
  assert.equal(ui.pages[1].offset, 20); assert.equal(ui.pages[0].signal.aborted, true);
  assert.match(ui.text(), /动作1/); assert.match(ui.text(), /正在读取动作列表/); assert.equal(ui.button("下一页动作").props.disabled, true);
  ui.pages[1].reject(new Error("动作页读取失败")); await tick(); ui.render(); assert.match(ui.text(), /动作页读取失败/);
  ui.button("重试动作列表").props.onClick(); ui.render(); assert.doesNotMatch(ui.text(), /动作页读取失败/); assert.match(ui.text(), /正在读取动作列表/);
  ui.resolvePage(ui.pages[2], [3, 4]); await tick(); ui.render();
  assert.doesNotMatch(ui.text(), /动作1|正在读取动作列表/); assert.match(ui.text(), /动作3/);
});

test("a new report source discards the previous list and selection before any new response", async t => {
  const ui = harness(t, { manualPages: true }); ui.render(); ui.resolvePage(ui.pages[0]); await tick(); ui.render();
  ui.choose(1); ui.render(); ui.changeSource(); ui.render();
  assert.equal(ui.requests[0].signal.aborted, true); assert.equal(ui.requests.length, 1, "old action identity cannot request evidence from the new source");
  assert.doesNotMatch(ui.text(), /动作1|片段依据/); assert.match(ui.text(), /正在读取动作列表/);
  ui.resolve(ui.requests[0], "旧来源的证据"); await tick(); ui.render(); assert.doesNotMatch(ui.text(), /旧来源的证据/);
  ui.resolvePage(ui.pages[1], [3, 4]); await tick(); ui.render(); ui.choose(3); ui.render();
  assert.equal(ui.requests[1].source.evidence_revision, "d".repeat(64));
});
