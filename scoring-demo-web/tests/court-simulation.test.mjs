import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import ts from "typescript";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

const cache = new Map();
function compiledUrl(fileUrl) {
  // Keep source JSON imports shared with production, without a generated copy.
  if (fileUrl.pathname.endsWith(".json")) return `data:text/javascript;base64,${Buffer.from(`export default ${fs.readFileSync(fileUrl, "utf8")}`).toString("base64")}`;
  if (cache.has(fileUrl.href)) return cache.get(fileUrl.href);
  let js = ts.transpileModule(fs.readFileSync(fileUrl, "utf8"), { fileName: fileUrl.pathname, compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  js = js.replace(/from "([^"]+)"/g, (_match, name) => { const local = new URL(name.endsWith(".json") ? name : `${name}.ts`, fileUrl); return `from ${JSON.stringify(name.startsWith(".") ? compiledUrl(fs.existsSync(local) ? local : new URL(`${name}.tsx`, fileUrl)) : import.meta.resolve(name))}`; });
  const url = `data:text/javascript;base64,${Buffer.from(js).toString("base64")}`; cache.set(fileUrl.href, url); return url;
}
const { courtHomography, projectCourtPoint, courtProjectionOf, courtPaths, courtPlaybackFrame, courtReferenceRegions, calibrationWindowAt } = await import(compiledUrl(new URL("../app/lib/court-simulation.ts", import.meta.url)));
const { courtCalibrationKey, parseCourtCalibration } = await import(compiledUrl(new URL("../app/lib/court-calibration.ts", import.meta.url)));
const { default: CourtSimulationPanel } = await import(compiledUrl(new URL("../app/CourtSimulationPanel.tsx", import.meta.url)));
const corners = [{ x: .3, y: .2 }, { x: .7, y: .2 }, { x: .9, y: .9 }, { x: .1, y: .9 }];
const point = overrides => ({ x: .5, y: .5, timestamp_ms: 1000, source: "observed", segment_id: "one", ...overrides });
const trajectory = overrides => ({ source: { coordinate_space: "normalized_frame_0_1" }, ball: { observed: [point()] }, ...overrides });
const stableWindow = { start_timestamp_ms: 0, end_timestamp_ms: 2000 };

// Exercise the panel's real event handlers and rendered props without a browser DOM.
// The hook host only supplies state/effect scheduling; projection and playback stay real.
const hookHostKey = Symbol.for("rallymate.court-panel-test-hooks");
const hooksUrl = `data:text/javascript;base64,${Buffer.from(["useState", "useRef", "useEffect", "useMemo", "useSyncExternalStore"].map(name => `export const ${name} = (...args) => globalThis[Symbol.for("rallymate.court-panel-test-hooks")].${name}(...args);`).join("\n")).toString("base64")}`;
let interactiveJs = ts.transpileModule(fs.readFileSync(new URL("../app/CourtSimulationPanel.tsx", import.meta.url), "utf8"), { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText;
interactiveJs = interactiveJs.replace(/from "([^"]+)"/g, (_match, name) => `from ${JSON.stringify(name === "react" ? hooksUrl : name.startsWith(".") ? compiledUrl(new URL(`../app/${name}.ts`, import.meta.url)) : import.meta.resolve(name))}`);
const { default: InteractiveCourtPanel } = await import(`data:text/javascript;base64,${Buffer.from(interactiveJs).toString("base64")}`);

function mountPanel(t, initialTrajectory) {
  let slots = [], index = 0, pendingEffects = [], tree, data = initialTrajectory, renderedKey;
  const frames = new Map(), storage = new Map(); let frameId = 0;
  const cleanup = () => { for (const slot of slots) slot?.cleanup?.(); slots = []; };
  const globals = { window: new EventTarget(), document: Object.assign(new EventTarget(), { getElementById: () => null }), localStorage: { getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key) }, requestAnimationFrame: callback => { frames.set(++frameId, callback); return frameId; }, cancelAnimationFrame: id => frames.delete(id) };
  const restoreGlobals = Object.entries(globals).map(([key, value]) => { const before = Object.getOwnPropertyDescriptor(globalThis, key); Object.defineProperty(globalThis, key, { configurable: true, writable: true, value }); return () => { if (before) Object.defineProperty(globalThis, key, before); else delete globalThis[key]; }; });
  const previousHost = globalThis[hookHostKey];
  globalThis[hookHostKey] = {
    useState(initial) { const slot = index++; if (!(slot in slots)) slots[slot] = typeof initial === "function" ? initial() : initial; return [slots[slot], next => { slots[slot] = typeof next === "function" ? next(slots[slot]) : next; }]; },
    useRef(initial) { const slot = index++; return slots[slot] ??= { current: initial }; },
    useMemo: compute => compute(), useSyncExternalStore: (_subscribe, read) => read(),
    useEffect(effect, dependencies) { const slot = index++, previous = slots[slot]; if (!previous || dependencies.some((value, offset) => !Object.is(value, previous.dependencies[offset]))) pendingEffects.push(() => { previous?.cleanup?.(); slots[slot] = { dependencies, cleanup: effect() }; }); },
  };
  t.after(() => { cleanup(); if (previousHost) globalThis[hookHostKey] = previousHost; else delete globalThis[hookHostKey]; restoreGlobals.forEach(restore => restore()); });
  function render(next = data) {
    data = next; index = 0; pendingEffects = [];
    tree = InteractiveCourtPanel({ trajectory: data, pending: false });
    if (typeof tree.type === "function") { if (renderedKey !== tree.key) { cleanup(); renderedKey = tree.key; } tree = tree.type(tree.props); }
    pendingEffects.forEach(run => run());
    return tree;
  }
  function nodes(node) { return Array.isArray(node) ? node.flatMap(item => nodes(item)) : node?.props ? [node, ...nodes(node.props.children)] : []; }
  render();
  return { render, frames, storage, nodes: () => nodes(tree), html: () => renderToStaticMarkup(tree), find: predicate => nodes(tree).find(predicate), reload: () => { cleanup(); render(); },
    seek(timestampMs, jobId = data.job_id) { const event = new Event("rallymate:seek-video"); event.detail = { timestampMs, jobId }; window.dispatchEvent(event); render(); },
  };
}

const replayTrajectory = () => trajectory({ job_id: "court-job", source: { coordinate_space: "normalized_frame_0_1", frames_path: "court-job/frames.jsonl", width: 1280, height: 720 }, court_projection: { status: "calibrated", coordinate_space: "court_normalized", semantics: "ground_plane_projection_not_ball_landing", points: [6900, 7000, 7100, 19500, 19600, 25400, 25500, 25600].map(timestamp_ms => point({ timestamp_ms })) } });

test("original-video seeks outside 6.9–25.6 seconds hide both ball and trail without changing the requested time", t => {
  const panel = mountPanel(t, replayTrajectory());
  for (const [timestamp, slider] of [[1000, 6900], [6899, 6900], [25601, 25600], [30000, 25600]]) {
    panel.seek(timestamp);
    assert.doesNotMatch(panel.html(), /class="court-ball-path|class="court-ball"/, `seek ${timestamp} must not show a boundary point`);
    assert.equal(panel.find(node => node.props["aria-label"] === "球路模拟时间").props.value, slider);
    assert.match(panel.html(), /当前时间超出可投影片段/);
  }
  assert.match(panel.html(), /0:30/);
  panel.seek(19500);
  assert.match(panel.html(), /class="court-ball"/);
  panel.seek(30000, "other-job");
  assert.match(panel.html(), /class="court-ball"/);
  assert.match(panel.html(), /0:19/);
});

test("playback uses exact time bounds and removes stale trails inside sparse detection gaps", () => {
  const points = replayTrajectory().court_projection.points;
  for (const timestamp of [6899, 25601, 30000]) {
    const view = courtPlaybackFrame(points, timestamp);
    assert.equal(view.current, timestamp); assert.equal(view.ball, null); assert.deepEqual(view.paths, []);
  }
  for (const timestamp of [6900, 25600, 7350]) assert.ok(courtPlaybackFrame(points, timestamp).ball);
  for (const timestamp of [7351, 10000, 19499]) {
    const view = courtPlaybackFrame(points, timestamp);
    assert.equal(view.outsideRange, false); assert.equal(view.ball, null); assert.deepEqual(view.paths, []);
  }
  const narrowWindow = { start_timestamp_ms: 19500, end_timestamp_ms: 19600 };
  assert.deepEqual(courtPlaybackFrame(points, 25600, narrowWindow).paths, []);
  const empty = courtPlaybackFrame([], 30000);
  assert.equal(empty.current, 30000); assert.equal(empty.ball, null); assert.deepEqual(empty.paths, []);
});

test("native original-video seeking and seeked update the court only for the current job and selected source", t => {
  const data = replayTrajectory(), panel = mountPanel(t, data);
  const video = { currentTime: 30, src: "https://local.test/source.mp4", currentSrc: "https://local.test/source.mp4", dataset: { timeOriginMs: "0" } };
  let selectedVideo = video;
  const replay = { dataset: { jobId: data.job_id }, querySelector: () => selectedVideo };
  document.getElementById = () => replay;
  const nativeSeek = (kind, target = video) => { const event = new Event(kind); Object.defineProperty(event, "target", { value: target }); document.dispatchEvent(event); panel.render(); };
  nativeSeek("seeking");
  assert.doesNotMatch(panel.html(), /class="court-ball-path|class="court-ball"/);
  assert.match(panel.html(), /0:30/);
  video.currentTime = 19.6; nativeSeek("seeked");
  assert.match(panel.html(), /class="court-ball-path/);
  panel.find(node => node.props["aria-label"] === "播放球路模拟").props.onClick(); panel.render();
  assert.equal(panel.frames.size, 1);
  video.currentTime = 1; nativeSeek("seeking");
  assert.equal(panel.frames.size, 0);
  assert.doesNotMatch(panel.html(), /class="court-ball-path|class="court-ball"/);
  video.currentTime = 18.6; video.dataset.timeOriginMs = "1000"; nativeSeek("seeked");
  assert.equal(panel.find(node => node.props["aria-label"] === "球路模拟时间").props.value, 19600);
  const unchanged = () => assert.equal(panel.find(node => node.props["aria-label"] === "球路模拟时间").props.value, 19600);
  nativeSeek("seeked", { ...video, currentTime: 30 }); unchanged();
  replay.dataset.jobId = "other-job"; video.currentTime = 30; nativeSeek("seeking"); unchanged();
  replay.dataset.jobId = data.job_id;
  video.src = "https://local.test/new-source.mp4"; nativeSeek("seeked"); unchanged();
  video.src = video.currentSrc;
  for (const invalidOrigin of [undefined, "", "bad-time"]) { video.dataset.timeOriginMs = invalidOrigin; nativeSeek("seeked"); unchanged(); }
  video.dataset.timeOriginMs = "0";
  for (const invalidTime of [NaN, -1, Infinity]) { video.currentTime = invalidTime; nativeSeek("seeking"); unchanged(); }
  video.currentTime = 30;
  selectedVideo = { ...video, src: "https://local.test/replacement.mp4", currentSrc: "https://local.test/replacement.mp4", currentTime: 7.1 };
  panel.render({ ...data, source: { ...data.source, frames_path: "replacement/frames.jsonl" } });
  nativeSeek("seeked");
  assert.equal(panel.find(node => node.props["aria-label"] === "球路模拟时间").props.value, 6900);
  nativeSeek("seeked", selectedVideo);
  assert.equal(panel.find(node => node.props["aria-label"] === "球路模拟时间").props.value, 7100);
});

test("slider and play recover from an external out-of-range seek, and later seeks stop playback", t => {
  const panel = mountPanel(t, replayTrajectory());
  panel.seek(30000);
  panel.find(node => node.props["aria-label"] === "球路模拟时间").props.onChange({ target: { value: "19600" } }); panel.render();
  assert.match(panel.html(), /class="court-ball-path/);
  assert.match(panel.html(), /0:19/);
  panel.seek(1000);
  panel.find(node => node.props["aria-label"] === "播放球路模拟").props.onClick(); panel.render();
  assert.match(panel.html(), /class="court-ball"/);
  assert.equal(panel.find(node => node.props["aria-label"] === "球路模拟时间").props.value, 6900);
  assert.equal(panel.frames.size, 1);
  panel.seek(30000);
  assert.equal(panel.frames.size, 0);
  for (const invalid of [-1, NaN, Infinity, "19500"]) panel.seek(invalid);
  assert.match(panel.html(), /0:30/);
  assert.doesNotMatch(panel.html(), /class="court-ball-path|class="court-ball"/);
});

test("switching job or source resets playback and cannot reuse the previous job's seek", t => {
  const data = replayTrajectory(), panel = mountPanel(t, data);
  panel.seek(30000);
  const otherJob = { ...data, job_id: "other-job" };
  panel.render(otherJob);
  assert.match(panel.html(), /class="court-ball"/);
  panel.seek(30000, data.job_id);
  assert.equal(panel.find(node => node.props["aria-label"] === "球路模拟时间").props.value, 6900);
  panel.seek(19600);
  panel.find(node => node.props["aria-label"] === "播放球路模拟").props.onClick(); panel.render();
  assert.equal(panel.frames.size, 1);
  panel.render({ ...otherJob, source: { ...otherJob.source, frames_path: "other-source/frames.jsonl" } });
  assert.equal(panel.frames.size, 0);
  assert.equal(panel.find(node => node.props["aria-label"] === "球路模拟时间").props.value, 6900);
});

test("paused 19.50s capture without paint callbacks supports service corners, undo and persistence", async t => {
  const raw = replayTrajectory();
  const data = { ...raw, court_projection: undefined, source: { ...raw.source, court_calibration_windows: [{ start_timestamp_ms: 6900, end_timestamp_ms: 25600 }] }, ball: { observed: raw.court_projection.points } };
  const panel = mountPanel(t, data);
  const video = Object.assign(new EventTarget(), { currentTime: 19.5, readyState: 4, videoWidth: 1280, videoHeight: 720, seeking: false, src: "/source.mp4", currentSrc: "/source.mp4", dataset: { timeOriginMs: "0" }, pause() {}, play() { assert.fail("capture must stay paused"); }, requestVideoFrameCallback() { assert.fail("canvas must not need a compositor callback"); } });
  globalThis.requestAnimationFrame = () => assert.fail("canvas must not need a paint callback");
  let draws = 0;
  const replay = { dataset: { jobId: data.job_id }, querySelector: () => video };
  document.getElementById = () => replay;
  document.createElement = () => ({ getContext: () => ({ drawImage: input => { assert.equal(input, video); draws++; } }), toDataURL: () => "data:image/jpeg;base64,test" });
  panel.find(node => node.props.children === "从原视频当前画面取图").props.onClick();
  await Promise.resolve(); await Promise.resolve(); panel.render();
  assert.equal(draws, 1); assert.equal(panel.frames.size, 0);
  assert.equal(video.currentTime, 19.5); assert.match(panel.html(), /19\.50/);
  panel.find(node => node.type === "select").props.onChange({ target: { value: "service" } }); panel.render();
  const selectCorner = corner => { panel.find(node => node.props.className === "court-calibration-image").props.onClick({ detail: 1, clientX: corner.x * 100, clientY: corner.y * 100, currentTarget: { getBoundingClientRect: () => ({ left: 0, top: 0, width: 100, height: 100 }) } }); panel.render(); };
  corners.forEach(selectCorner);
  assert.equal(panel.nodes().filter(node => node.props.className === "court-point-number").length, 4);
  panel.find(node => node.props.children === "撤销上一点").props.onClick(); panel.render();
  assert.equal(panel.nodes().filter(node => node.props.className === "court-point-number").length, 3);
  selectCorner(corners[3]);
  panel.find(node => node.props.type === "checkbox").props.onChange({ target: { checked: true } }); panel.render();
  const apply = panel.find(node => node.props.children === "应用展示标定"); assert.equal(apply.props.disabled, false); apply.props.onClick(); panel.render();
  const sourceKey = courtCalibrationKey(data), saved = JSON.parse(panel.storage.get(sourceKey));
  assert.equal(saved.timestampMs, 19500); assert.equal(saved.region, "service"); assert.deepEqual(saved.corners, corners);
  panel.reload(); assert.match(panel.html(), /手工标定/); assert.match(panel.html(), /6\.9–25\.6/);
  panel.seek(30000); assert.doesNotMatch(panel.html(), /class="court-ball-path|class="court-ball"/);
  panel.render({ ...data, job_id: "other-job" }); assert.doesNotMatch(panel.html(), /手工标定|class="court-ball"/);
  panel.render(data); assert.match(panel.html(), /手工标定/);
  panel.find(node => node.props.children === "清除手工标定").props.onClick(); panel.render();
  assert.equal(panel.storage.has(sourceKey), false);
});

test("annotated replay cannot be captured as an original court frame", async t => {
  const data = replayTrajectory(), panel = mountPanel(t, data);
  const video = { dataset: { replayRole: "annotated", timeOriginMs: "0" }, src: "/annotated.mp4", currentTime: 19.5, readyState: 4, videoWidth: 1280, videoHeight: 720 };
  let draws = 0, canvases = 0;
  document.getElementById = () => ({ dataset: { jobId: data.job_id }, querySelector: selector => {
    assert.equal(selector, "#replay-view-original video[data-replay-role='original']");
    return video.dataset.replayRole === "original" ? video : null;
  } });
  document.createElement = () => { canvases++; return { getContext: () => ({ drawImage: () => { draws++; } }), toDataURL: () => "data:image/jpeg;base64,test" }; };
  panel.find(node => node.props.children === "从原视频当前画面取图").props.onClick();
  await Promise.resolve(); await Promise.resolve(); panel.render();
  assert.equal(canvases, 0); assert.equal(draws, 0);
  assert.match(panel.html(), /切换到“原视频”/);
  assert.doesNotMatch(panel.html(), /class="court-calibration-image"/);
});

test("court capture waits for a decoded completed seek before drawing pixels", async t => {
  const data = replayTrajectory(), panel = mountPanel(t, data);
  const video = Object.assign(new EventTarget(), { currentTime: 19.5, readyState: 1, videoWidth: 1280, videoHeight: 720, seeking: true, src: "/source.mp4", currentSrc: "/source.mp4", dataset: { timeOriginMs: "0" }, pause() {} });
  let draws = 0;
  const replay = { dataset: { jobId: data.job_id }, querySelector: () => video };
  document.getElementById = () => replay;
  document.createElement = () => ({ getContext: () => ({ drawImage: () => { draws++; } }), toDataURL: () => "data:image/jpeg;base64,test" });
  panel.find(node => node.props.children === "从原视频当前画面取图").props.onClick();
  video.readyState = 2; video.dispatchEvent(new Event("loadeddata"));
  await Promise.resolve(); await Promise.resolve(); panel.render();
  assert.equal(draws, 0); assert.doesNotMatch(panel.html(), /class="court-calibration-image"/);
  video.seeking = false; video.dispatchEvent(new Event("seeked"));
  await Promise.resolve(); panel.render();
  assert.equal(draws, 1); assert.match(panel.html(), /19\.50/);
});

test("changed source or task and failed canvas serialization cannot save a court screenshot", async t => {
  for (const failure of ["source", "task", "canvas"]) await t.test(failure, async t => {
    const data = replayTrajectory(), panel = mountPanel(t, data);
    const video = Object.assign(new EventTarget(), { currentTime: 19.5, readyState: failure === "canvas" ? 4 : 1, videoWidth: 1280, videoHeight: 720, seeking: false, src: "/source.mp4", currentSrc: "/source.mp4", dataset: { timeOriginMs: "0" }, pause() {} });
    let draws = 0;
    const replay = { dataset: { jobId: data.job_id }, querySelector: () => video };
    document.getElementById = () => replay;
    document.createElement = () => ({ getContext: () => ({ drawImage: () => { draws++; } }), toDataURL: () => { throw new Error("canvas unavailable"); } });
    panel.find(node => node.props.children === "从原视频当前画面取图").props.onClick();
    if (failure === "source") { video.src = "/replacement.mp4"; video.readyState = 2; video.dispatchEvent(new Event("loadeddata")); }
    if (failure === "task") { panel.render({ ...data, job_id: "other-job" }); video.readyState = 2; video.dispatchEvent(new Event("loadeddata")); }
    await Promise.resolve(); await Promise.resolve(); panel.render();
    assert.equal(draws, failure === "canvas" ? 1 : 0);
    assert.doesNotMatch(panel.html(), /class="court-calibration-image"/);
    if (failure === "canvas") assert.match(panel.html(), /当前视频无法取图/);
  });
});

test("court capture revalidates its frame after readiness resolves and before canvas", async t => {
  const changes = {
    time: ({ video }) => { video.currentTime = 30; video.seeking = true; video.dispatchEvent(new Event("seeking")); },
    source: ({ video }) => { video.src = "/replacement.mp4"; video.readyState = 1; video.dispatchEvent(new Event("emptied")); },
    decodedSource: ({ video }) => { video.currentSrc = "/previous.mp4"; },
    seek: ({ video }) => { video.seeking = true; },
    readiness: ({ video }) => { video.readyState = 1; },
    dimensions: ({ video }) => { video.videoWidth = 0; },
    origin: ({ video }) => { video.dataset.timeOriginMs = "1000"; },
    job: ({ replay }) => { replay.dataset.jobId = "other-job"; },
    videoNode: ({ replay, video }) => { replay.querySelector = () => ({ ...video }); },
    replayNode: ({ replay }) => { document.getElementById = () => ({ ...replay }); },
    task: ({ panel, data }) => { panel.render({ ...data, job_id: "other-job" }); },
  };
  for (const [name, change] of Object.entries(changes)) await t.test(name, async t => {
    const data = replayTrajectory(), panel = mountPanel(t, data);
    const video = Object.assign(new EventTarget(), { currentTime: 19.5, readyState: 4, videoWidth: 1280, videoHeight: 720, seeking: false, src: "/source.mp4", currentSrc: "/source.mp4", dataset: { timeOriginMs: "0" }, pause() {} });
    const replay = { dataset: { jobId: data.job_id }, querySelector: () => video };
    let draws = 0;
    document.getElementById = () => replay;
    document.createElement = () => ({ getContext: () => ({ drawImage: () => { draws++; } }), toDataURL: () => "data:image/jpeg;base64,test" });
    panel.find(node => node.props.children === "从原视频当前画面取图").props.onClick();
    queueMicrotask(() => change({ video, replay, panel, data }));
    await Promise.resolve(); await Promise.resolve(); await Promise.resolve(); panel.render();
    assert.equal(draws, 0); assert.doesNotMatch(panel.html(), /class="court-calibration-image"/);
  });
});

test("court capture rejects a mismatched initial source or missing time origin", async t => {
  for (const invalid of ["source", "origin"]) await t.test(invalid, async t => {
    const data = replayTrajectory(), panel = mountPanel(t, data);
    const video = Object.assign(new EventTarget(), { currentTime: 19.5, readyState: 4, videoWidth: 1280, videoHeight: 720, seeking: false, src: "/source.mp4", currentSrc: invalid === "source" ? "/previous.mp4" : "/source.mp4", dataset: { timeOriginMs: invalid === "origin" ? "" : "0" }, pause() {} });
    const replay = { dataset: { jobId: data.job_id }, querySelector: () => video };
    document.getElementById = () => replay;
    document.createElement = () => assert.fail("invalid capture identity must not reach canvas");
    panel.find(node => node.props.children === "从原视频当前画面取图").props.onClick();
    await Promise.resolve(); await Promise.resolve(); panel.render();
    assert.doesNotMatch(panel.html(), /class="court-calibration-image"/);
  });
});

test("court screenshot timestamp uses the recorded target and nonzero origin", async t => {
  const raw = replayTrajectory(), data = { ...raw, source: { ...raw.source, court_calibration_windows: [{ start_timestamp_ms: 6900, end_timestamp_ms: 25600 }] } }, panel = mountPanel(t, data);
  const video = Object.assign(new EventTarget(), { currentTime: 19.5, readyState: 4, videoWidth: 1280, videoHeight: 720, seeking: false, src: "/source.mp4", currentSrc: "/source.mp4", dataset: { timeOriginMs: "1000" }, pause() {} });
  const replay = { dataset: { jobId: data.job_id }, querySelector: () => video };
  document.getElementById = () => replay;
  document.createElement = () => ({ getContext: () => ({ drawImage: () => {} }), toDataURL: () => "data:image/jpeg;base64,test" });
  panel.find(node => node.props.children === "从原视频当前画面取图").props.onClick();
  queueMicrotask(() => { video.currentTime += .0009; });
  await Promise.resolve(); await Promise.resolve(); panel.render();
  assert.match(panel.html(), /取图时间 20\.50 秒/);
  for (const corner of corners) {
    panel.find(node => node.props.className === "court-calibration-image").props.onClick({ detail: 1, clientX: corner.x * 100, clientY: corner.y * 100, currentTarget: { getBoundingClientRect: () => ({ left: 0, top: 0, width: 100, height: 100 }) } }); panel.render();
  }
  panel.find(node => node.props.type === "checkbox").props.onChange({ target: { checked: true } }); panel.render();
  const apply = panel.find(node => node.props.children === "应用展示标定"); assert.equal(apply.props.disabled, false); apply.props.onClick(); panel.render();
  assert.equal(JSON.parse(panel.storage.get(courtCalibrationKey(data))).timestampMs, 20500);
});

test("four corners map precisely to the court without treating pixel locations as world coordinates", () => {
  const matrix = courtHomography(corners);
  const expected = [{ x: 0, y: 0 }, { x: 1, y: 0 }, { x: 1, y: 1 }, { x: 0, y: 1 }];
  for (const [index, corner] of corners.entries()) { const actual = projectCourtPoint(corner, matrix); assert.ok(Math.abs(actual.x - expected[index].x) < 1e-10); assert.ok(Math.abs(actual.y - expected[index].y) < 1e-10); }
  const center = projectCourtPoint({ x: .5, y: .55 }, matrix);
  assert.ok(Math.abs(center.x - .5) < 1e-10); assert.notEqual(center.y, .55);
  assert.equal(courtProjectionOf(trajectory()).status, "unavailable");
  assert.deepEqual(courtProjectionOf(trajectory()).points, []);
});

test("manual calibration rejects missing, self-crossed, reversed, tiny, and degenerate corners", () => {
  for (const invalid of [corners.slice(0, 3), [corners[0], corners[2], corners[1], corners[3]], [...corners].reverse(), corners.map(() => corners[0]), corners.map(point => ({ x: point.x * .01, y: point.y * .01 })), [{ x: NaN, y: 0 }, ...corners.slice(1)], [{ x: -1, y: 0 }, ...corners.slice(1)]]) assert.equal(courtHomography(invalid), null);
  assert.equal(projectCourtPoint({ x: .5, y: .5 }, [1, 0, 0, 0, 1, 0, 0, 0, 0]), null);
  assert.equal(projectCourtPoint({ x: 100, y: 100 }, [1, 0, 0, 0, 1, 0, 0, 0, 1]), null);
});

test("calibrated projection requires known semantics, bounded points and segment provenance", () => {
  const projection = { status: "calibrated", coordinate_space: "court_normalized", semantics: "ground_plane_projection_not_ball_landing", points: [point(), point({ x: 9 }), point({ timestamp_ms: -1 }), point({ segment_id: undefined }), point({ source: "predicted" })] };
  assert.equal(courtProjectionOf(trajectory({ court_projection: projection })).points.length, 1);
  for (const override of [{ status: "unavailable" }, { coordinate_space: "image" }, { semantics: "real_landing" }]) assert.equal(courtProjectionOf(trajectory({ court_projection: { ...projection, ...override } })).points.length, 0);
  const manual = courtProjectionOf(trajectory(), courtHomography(corners), stableWindow);
  assert.equal(manual.points.length, 1); assert.match(manual.reason_zh, /不参与评分/);
  assert.equal(courtProjectionOf(trajectory({ source: { coordinate_space: "pixels" } }), courtHomography(corners), stableWindow).points.length, 0);
});

test("court playback preserves segment gaps and never connects different track identities", () => {
  const points = [point(), point({ timestamp_ms: 1100 }), point({ timestamp_ms: 1200, segment_id: "two" }), point({ timestamp_ms: 1300, segment_id: "two", track_id: 2 }), point({ timestamp_ms: 1700, segment_id: "two", track_id: 2 }), point({ timestamp_ms: 1800, segment_id: "two", track_id: 2 })];
  assert.deepEqual(courtPaths(points, 1800).map(path => path.length), [2, 1, 1, 2]);
  assert.equal(courtPaths(points, 500).length, 0);
  assert.equal(courtPaths(points, 5000).length, 0);
  const interleaved = [point(), point({ timestamp_ms: 1000, track_id: 2 }), point({ timestamp_ms: 1100 }), point({ timestamp_ms: 1100, track_id: 2 })];
  assert.deepEqual(courtPaths(interleaved, 1100).map(path => path.length), [2, 2]);
});

test("unmarked video shows a useful court and collapsed calibration, without fabricated ball paths", () => {
  const html = renderToStaticMarkup(createElement(CourtSimulationPanel, { trajectory: trajectory(), pending: false }));
  assert.match(html, /场地俯视图模拟/); assert.match(html, /球场设置与投影说明/);
  assert.match(html, /<details class="court-settings">/); assert.doesNotMatch(html, /court-source-replay|<video/);
  assert.doesNotMatch(html, /class="court-ball-path|class="court-ball"|<details[^>]*open/);
  assert.match(html, /标定球场/); assert.doesNotMatch(html, /class="court-lines"/);
});

test("visible singles and service intersections map to their real court region, not the outer court", () => {
  for (const region of ["singles", "service"]) {
    const matrix = courtHomography(corners, region);
    for (const [index, corner] of corners.entries()) {
      const actual = projectCourtPoint(corner, matrix), expected = courtReferenceRegions[region].targets[index];
      assert.ok(Math.abs(actual.x - expected.x) < 1e-10);
      assert.ok(Math.abs(actual.y - expected.y) < 1e-10);
    }
  }
  const service = courtReferenceRegions.service.targets;
  assert.ok(Math.abs((service[2].y - service[0].y) * 23.77 - 12.8) < 1e-10);
  assert.ok(Math.abs((service[1].x - service[0].x) * 10.97 - 8.23) < 1e-10);
  assert.equal(courtHomography(corners, "unknown"), null);
});

test("empty or unusable reconstruction falls back to actual observations, preserving gaps and identities", () => {
  const observed = [point(), point({ timestamp_ms: 1100, track_id: 2 }), point({ timestamp_ms: 1600, track_id: 2 }), point({ x: NaN }), point({ source: "predicted" })];
  for (const segments of [[], [{ segment_id: "empty", points: [] }], [{ segment_id: "bad", points: [point({ x: NaN })] }]]) {
    const result = courtProjectionOf(trajectory({ ball: { observed, reconstruction: { segments } } }), courtHomography(corners), stableWindow);
    assert.equal(result.points.length, 3);
    assert.deepEqual(courtPaths(result.points, 1600).map(path => path.length), [1, 1, 1]);
  }
  const reconstructed = courtProjectionOf(trajectory({ ball: { observed, reconstruction: { segments: [{ segment_id: 1, points: [point(), point({ timestamp_ms: 1050, source: "interpolated" })] }] } } }), courtHomography(corners), stableWindow);
  assert.equal(reconstructed.points.length, 2);
  assert.equal(reconstructed.points[1].source, "interpolated");
  assert.equal(reconstructed.points[0].segment_id, "1");
  const outsideWindow = courtProjectionOf(trajectory({ ball: { observed: [point()], reconstruction: { segments: [{ segment_id: 1, points: [point({ timestamp_ms: 2500 })] }] } } }), courtHomography(corners), stableWindow);
  assert.deepEqual(outsideWindow.points.map(point => point.timestamp_ms), [1000]);
});

test("manual projection is restricted to the captured frame's verified stable window", () => {
  const data = trajectory({ source: { coordinate_space: "normalized_frame_0_1", court_calibration_windows: [stableWindow, { start_timestamp_ms: 2100, end_timestamp_ms: 3000 }] }, ball: { observed: [point(), point({ timestamp_ms: 2050 }), point({ timestamp_ms: 2500 })] } });
  assert.equal(calibrationWindowAt(data, 2050), null);
  assert.equal(calibrationWindowAt(data, -1), null);
  assert.equal(calibrationWindowAt(trajectory(), 1000), null);
  assert.equal(courtProjectionOf(data, courtHomography(corners)).points.length, 0);
  assert.deepEqual(courtProjectionOf(data, courtHomography(corners), calibrationWindowAt(data, 1000)).points.map(point => point.timestamp_ms), [1000]);
  assert.deepEqual(courtProjectionOf(data, courtHomography(corners), calibrationWindowAt(data, 2500)).points.map(point => point.timestamp_ms), [2500]);
});

test("saved calibration and missing ball data are distinct states", () => {
  assert.equal(courtProjectionOf(null, courtHomography(corners)).status, "calibrated");
  assert.equal(courtProjectionOf(null, [NaN]).status, "unavailable");
  const result = courtProjectionOf(trajectory({ ball: { observed: [] } }), courtHomography(corners), stableWindow);
  assert.equal(result.status, "calibrated"); assert.deepEqual(result.points, []);
});

test("persisted calibration is bound to the job/source and revalidated rather than trusting a matrix", () => {
  const data = trajectory({ job_id: "one", source: { frames_path: "one/frames.jsonl", width: 1280, height: 720 } });
  const sourceKey = courtCalibrationKey(data);
  const saved = { version: 1, sourceKey, fixedCamera: true, region: "service", corners, timestampMs: 1000 };
  assert.deepEqual(parseCourtCalibration(JSON.stringify(saved), sourceKey), saved);
  const other = courtCalibrationKey({ ...data, job_id: "two" });
  assert.notEqual(sourceKey, other);
  assert.equal(parseCourtCalibration(JSON.stringify(saved), other), null);
  assert.equal(courtCalibrationKey(trajectory()), null);
  for (const override of [{ version: 2 }, { region: "unknown" }, { fixedCamera: false }, { timestampMs: -1 }, { corners: [null, null, null, null] }, { corners: [...corners].reverse() }]) assert.equal(parseCourtCalibration(JSON.stringify({ ...saved, ...override }), sourceKey), null);
  assert.equal(parseCourtCalibration("{broken", sourceKey), null);
});
