import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import ts from "typescript";

// Run the actual component callbacks with persistent hook state. JSX still uses
// React's runtime; only the renderer's hook dispatcher is replaced for this test.
const hooksUrl = `data:text/javascript;base64,${Buffer.from(`
let active;
export function createHooks() { return { values: [], cursor: 0 }; }
export function renderWithHooks(hooks, render) {
  active = hooks; hooks.cursor = 0;
  try { return render(); } finally { active = undefined; }
}
export function useState(initial) {
  const hooks = active, index = hooks.cursor++;
  if (!(index in hooks.values)) hooks.values[index] = typeof initial === "function" ? initial() : initial;
  return [hooks.values[index], value => { hooks.values[index] = typeof value === "function" ? value(hooks.values[index]) : value; }];
}
export function useId() { return "shot-time-test"; }
export function useEffect() {}
export function useRef(value) { return { current: value }; }
`).toString("base64")}`;
const { createHooks, renderWithHooks } = await import(hooksUrl);
const cache = new Map();
function compiledUrl(file) {
  if (cache.has(file.href)) return cache.get(file.href);
  let js = ts.transpileModule(fs.readFileSync(file, "utf8"), { fileName: file.pathname,
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  js = js.replace(/^import\s+["'][^"']+\.css["'];?\s*$/gm, "");
  js = js.replace(/from "([^"]+)"/g, (_match, name) => {
    if (name === "react") return `from ${JSON.stringify(hooksUrl)}`;
    const local = new URL(`${name}.ts`, file);
    return `from ${JSON.stringify(name.startsWith(".") ? compiledUrl(fs.existsSync(local) ? local : new URL(`${name}.tsx`, file)) : import.meta.resolve(name))}`;
  });
  const url = `data:text/javascript;base64,${Buffer.from(js).toString("base64")}`;
  cache.set(file.href, url);
  return url;
}
const { ShotStatisticsContent } = await import(compiledUrl(new URL("../app/ShotStatisticsPanel.tsx", import.meta.url)));
const { shotVideoTimeMs } = await import(compiledUrl(new URL("../app/ShotStatisticsSidebar.tsx", import.meta.url)));

const report = () => ({
  schema_version: "1.0.0", job_id: "current-job", status: "unreviewed", revision: 0,
  source: { video_sha256: "a".repeat(64), frame_count: 301, start_ms: 0, end_ms: 10000, duration_ms: 10000, coordinate_space: "normalized_frame_0_1" },
  summary: { confirmed_hit_count: 0, confirmed_bounce_count: 0, mapped_bounce_count: 0, candidate_hit_count: 0,
    candidate_bounce_count: 0, dismissed_count: 0, count_semantics: "review_confirmed_events_not_total_video" },
  events: [], limitations_zh: [],
});
function textContent(node) {
  if (node == null || typeof node === "boolean") return "";
  if (Array.isArray(node)) return node.map(textContent).join("");
  return typeof node === "object" ? textContent(node.props?.children) : String(node);
}
function findElement(node, matches) {
  if (node == null || typeof node !== "object") return null;
  if (Array.isArray(node)) {
    for (const child of node) { const found = findElement(child, matches); if (found) return found; }
    return null;
  }
  return matches(node) ? node : findElement(node.props?.children, matches);
}
function panel(extra = {}) {
  const hooks = createHooks();
  const operations = [], seeks = [];
  const props = { data: report(), jobId: "current-job", currentTimeMs: 0,
    onReview: operation => operations.push(operation), onSeek: time => seeks.push(time), ...extra };
  let tree;
  const render = (changes = {}) => {
    Object.assign(props, changes);
    tree = renderWithHooks(hooks, () => ShotStatisticsContent(props));
    return tree;
  };
  const find = matches => findElement(tree, matches);
  const button = label => find(node => node.type === "button" && textContent(node) === label);
  const click = label => {
    const element = button(label);
    assert.ok(element, `Button exists: ${label}`);
    assert.equal(element.props.disabled, false, `Button is enabled: ${label}`);
    element.props.onClick();
  };
  render();
  return { operations, seeks, render, find, button, click };
}

test("manual hit samples the video at click time after a seek, while the React clock is still zero", async () => {
  let videoTime = 0;
  const view = panel({ getCurrentTimeMs: () => videoTime });
  videoTime = 6000;
  view.click("记一次击球");
  await Promise.resolve();
  assert.deepEqual(view.operations, [{ operation: "add", kind: "hit", timestamp_ms: 6000 }]);
});

test("marking locks the live six-second timestamp and saves it after playback advances", async () => {
  let videoTime = 0, reads = 0;
  const view = panel({ getCurrentTimeMs: () => { reads++; return videoTime; } });
  videoTime = 6000;
  view.click("标记落点");
  assert.deepEqual(view.seeks, [6000]);
  view.render();
  assert.match(textContent(view.find(node => node.props?.className === "shot-statistics-marking")), /0:06\.0/);
  assert.equal(view.button("保存落点").props.disabled, true);

  videoTime = 9000;
  view.render({ currentTimeMs: 9000 });
  const capture = view.find(node => node.props?.className === "shot-statistics-court-capture");
  capture.props.onClick({ clientX: 100, clientY: 75,
    currentTarget: { getBoundingClientRect: () => ({ left: 0, top: 0, width: 200, height: 150 }) } });
  view.render();
  view.click("保存落点");
  await Promise.resolve();
  assert.deepEqual(view.operations, [{ operation: "add", kind: "bounce", timestamp_ms: 6000, position_court: { x: .5, y: .5 } }]);
  assert.equal(reads, 1, "Saving uses the locked timestamp instead of sampling the later video time");
  view.render();
  assert.equal(view.button("保存落点"), null);
});

test("a null live clock during source switching never falls back to a stale valid display clock", () => {
  const view = panel({ currentTimeMs: 6000, getCurrentTimeMs: () => null });
  view.click("记一次击球");
  view.click("标记落点");
  view.render();
  assert.deepEqual(view.operations, []);
  assert.deepEqual(view.seeks, []);
  assert.equal(view.button("保存落点"), null);
  assert.equal(view.find(node => node.props?.className === "shot-statistics-court-capture"), null);
});

test("live clock bounds and finiteness are checked even when the displayed clock is valid", () => {
  for (const value of [NaN, Infinity, -Infinity, -1, 10001]) {
    const view = panel({ getCurrentTimeMs: () => value });
    view.click("记一次击球");
    view.click("标记落点");
    view.render();
    assert.deepEqual(view.operations, [], `Rejected live time ${value}`);
    assert.deepEqual(view.seeks, []);
    assert.equal(view.button("保存落点"), null);
  }
});

test("confirming an existing bounce retains its event timestamp rather than the live playback time", async () => {
  const data = report();
  data.events = [{ id: "bounce-candidate", kind: "bounce", status: "candidate", timestamp_ms: 4000,
    frame_index: 120, position_image: { x: .4, y: .5 }, position_court: null, source: "trajectory_rule", reason_codes: [] }];
  data.summary.candidate_bounce_count = 1;
  const view = panel({ data, getCurrentTimeMs: () => { throw new Error("Existing events do not need a live clock"); } });
  view.click("确认并标点");
  assert.deepEqual(view.seeks, [4000]);
  view.render();
  const capture = view.find(node => node.props?.className === "shot-statistics-court-capture");
  capture.props.onKeyDown({ key: "Enter", preventDefault() {} });
  view.render();
  view.click("保存落点");
  await Promise.resolve();
  assert.deepEqual(view.operations, [{ operation: "confirm", event_id: "bounce-candidate", position_court: { x: .5, y: .5 } }]);
});

const video = (changes = {}) => ({ readyState: 1, currentSrc: "https://tennis.example/original.mp4",
  src: "https://tennis.example/original.mp4", currentTime: 6, dataset: { timeOriginMs: "0" }, ...changes });

test("video clock reads current playback time with clip origin without rounding frame timing", () => {
  assert.equal(shotVideoTimeMs(video()), 6000);
  assert.equal(shotVideoTimeMs(video({ currentTime: 0 })), 0);
  assert.equal(shotVideoTimeMs(video({ currentTime: 6.125, dataset: { timeOriginMs: " 250 " } })), 6375);
  assert.equal(shotVideoTimeMs(video({ readyState: 4, dataset: { timeOriginMs: "1000" } })), 7000);
});

test("video clock rejects a switching source, missing metadata and invalid playback/origin values", () => {
  const cases = [
    null,
    video({ currentSrc: "https://tennis.example/previous.mp4" }),
    video({ currentSrc: "" }),
    video({ readyState: 0 }),
    ...[NaN, Infinity, -Infinity, -1].map(currentTime => video({ currentTime })),
    ...[undefined, "", " ", "invalid", "Infinity", "NaN"].map(timeOriginMs => video({ dataset: { timeOriginMs } })),
  ];
  for (const value of cases) assert.equal(shotVideoTimeMs(value), null);
});
