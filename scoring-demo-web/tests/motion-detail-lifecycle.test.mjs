import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import ts from "typescript";
const tick = () => new Promise(resolve => setTimeout(resolve, 0));
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
function harness() {
  const slots = []; let cursor = 0, effects = [], tree;
  const hooks = {
    useState(initial) { const i = cursor++; if (!(i in slots)) slots[i] = typeof initial === "function" ? initial() : initial; return [slots[i], value => slots[i] = typeof value === "function" ? value(slots[i]) : value]; },
    useEffectEvent(callback) { const i = cursor++; if (!(i in slots)) slots[i] = { call: (...args) => slots[i].callback(...args) }; slots[i].callback = callback; return slots[i].call; },
    useEffect(effect, deps) { const i = cursor++, old = slots[i]; if (!old || deps.some((d, j) => !Object.is(d, old.deps[j]))) { slots[i] = { deps, cleanup: old?.cleanup }; effects.push(() => { slots[i].cleanup?.(); slots[i].cleanup = effect(); }); } },
  };
  const requests = [], load = (episode, signal) => { const request = { ...deferred(), episode, signal }; requests.push(request); return request.promise; };
  let loader = load, jobId = "job";
  const episode = id => ({ episode_id: id, family: "baseline", start_ms: 100, peak_ms: 200, end_ms: 300, classification: { label_zh: id }, phases: [], metrics: {}, limitations_zh: [] });
  let data = { episodes: [episode("one"), episode("two")] };
  const modules = { react: hooks, "react/jsx-runtime": { jsx: (type, props) => ({ type, props }), jsxs: (type, props) => ({ type, props }), Fragment: "fragment" }, "./RotationAnalysisPanel": { default: "RotationAnalysisPanel" }, "./lib/motion-analysis": { MOTION_METRICS: [{ key: "peak_wrist_speed_torso_per_s", label: "手腕速度", unit: "test" }], motionMetricText: value => value == null ? "—" : String(value) } };
  const code = ts.transpileModule(fs.readFileSync(new URL("../app/MotionAnalysisPanel.tsx", import.meta.url), "utf8"), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  const exported = {}; new Function("require", "exports", code)(name => modules[name], exported);
  const render = () => { cursor = 0; effects = []; tree = exported.default({ familyLabel: "底线", data, jobId, pending: false, loadEpisode: loader }); effects.forEach(effect => effect()); return tree; };
  const nodes = value => !value || typeof value !== "object" ? [] : Array.isArray(value) ? value.flatMap(nodes) : [value, ...nodes(value.props?.children)];
  const text = value => value == null || typeof value === "boolean" ? "" : typeof value !== "object" ? String(value) : Array.isArray(value) ? value.map(text).join("") : text(value.props?.children);
  return { render, requests, text: () => text(tree), button: label => nodes(tree).find(node => node.type === "button" && text(node) === label), cloneOverview: () => { data = structuredClone(data); }, changeSource: () => { loader = (...args) => load(...args); }, changeJob: () => { jobId = "another-job"; }, finish: (request, number) => request.resolve({ ...request.episode, metrics: { peak_wrist_speed_torso_per_s: number } }), unmount: () => slots.forEach(value => value?.cleanup?.()) };
}

test("motion selection aborts old measurements and ignores late responses", async () => {
  const ui = harness(); ui.render();
  ui.button("下一片段 →").props.onClick(); ui.render();
  assert.equal(ui.requests[0].signal.aborted, true); assert.equal(ui.requests[1].episode.episode_id, "two");
  ui.finish(ui.requests[0], 999); await tick(); ui.render(); assert.doesNotMatch(ui.text(), /999/);
  ui.finish(ui.requests[1], 47); await tick(); ui.render(); assert.match(ui.text(), /47/);
  ui.unmount(); assert.equal(ui.requests[1].signal.aborted, true);
});

test("fresh overview objects do not reload the same motion, while failed reads can retry", async () => {
  const ui = harness(); ui.render(); ui.cloneOverview(); ui.render(); assert.equal(ui.requests.length, 1);
  ui.requests[0].reject(new Error("片段暂不可用")); await tick(); ui.render(); assert.match(ui.text(), /片段暂不可用/);
  ui.button("重试当前运动片段").props.onClick(); ui.render(); assert.equal(ui.requests.length, 2);
  assert.doesNotMatch(ui.text(), /片段暂不可用/); assert.match(ui.text(), /正在按需读取/);
  ui.finish(ui.requests[1], 23); await tick(); ui.render(); assert.match(ui.text(), /23/);
  ui.unmount();
});

test("same episode id cannot retain a completed detail across source or job changes", async () => {
  const ui = harness(); ui.render(); ui.finish(ui.requests[0], 987); await tick(); ui.render(); assert.match(ui.text(), /987/);
  ui.changeSource(); ui.render(); assert.equal(ui.requests[0].signal.aborted, true); assert.equal(ui.requests.length, 2);
  assert.doesNotMatch(ui.text(), /987/); assert.match(ui.text(), /正在按需读取/);
  ui.finish(ui.requests[1], 654); await tick(); ui.render(); assert.match(ui.text(), /654/);
  ui.changeJob(); ui.render(); assert.equal(ui.requests[1].signal.aborted, true); assert.equal(ui.requests.length, 3);
  assert.doesNotMatch(ui.text(), /654/); ui.unmount();
});
