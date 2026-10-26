import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import ts from "typescript";

const js = ts.transpileModule(fs.readFileSync(new URL("../app/lib/video-frame-capture.ts", import.meta.url), "utf8"), { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const { waitForPresentedVideoFrame } = await import(`data:text/javascript;base64,${Buffer.from(js).toString("base64")}`);

function fixture({ readyState = 4, seeking = false } = {}) {
  let currentTime = 4.25;
  const assignedTimes = [], listeners = new Map();
  const video = new EventTarget();
  const add = video.addEventListener.bind(video), remove = video.removeEventListener.bind(video);
  Object.assign(video, { readyState, videoWidth: 960, videoHeight: 448, seeking, preload: "metadata", src: "https://local.test/video-a.mp4", currentSrc: "https://local.test/video-a.mp4", pauses: 0, plays: 0,
    pause() { this.pauses++; }, play() { this.plays++; assert.fail("capture must never play"); }, load() { assert.fail("capture must not reset the source"); },
    requestVideoFrameCallback() { assert.fail("decoded canvas capture must not depend on compositor callbacks"); },
    addEventListener(name, callback, options) { listeners.set(name, callback); add(name, callback, options); },
    removeEventListener(name, callback) { listeners.delete(name); remove(name, callback); },
  });
  Object.defineProperty(video, "currentTime", { get: () => currentTime, set: value => { assignedTimes.push(value); currentTime = value; video.seeking = true; video.dispatchEvent(new Event("seeking")); } });
  return { video, assignedTimes, listeners, options: { timeoutMs: 200 },
    seeked() { video.seeking = false; video.dispatchEvent(new Event("seeked")); },
    decoded() { video.readyState = 2; video.dispatchEvent(new Event("loadeddata")); },
  };
}

function clean(f) {
  assert.equal(f.listeners.size, 0);
  assert.equal(f.video.plays, 0);
}

test("decoded paused frames permit canvas without rAF, video callbacks or a same-time seek", async t => {
  const previous = Object.getOwnPropertyDescriptor(globalThis, "requestAnimationFrame");
  Object.defineProperty(globalThis, "requestAnimationFrame", { configurable: true, value: () => assert.fail("paint scheduling is not a canvas prerequisite") });
  t.after(() => { if (previous) Object.defineProperty(globalThis, "requestAnimationFrame", previous); else delete globalThis.requestAnimationFrame; });
  for (const readyState of [2, 3, 4]) {
    const f = fixture({ readyState });
    assert.equal(await waitForPresentedVideoFrame(f.video, f.options), true);
    assert.equal(f.video.pauses, 1); assert.equal(f.video.currentTime, 4.25);
    assert.deepEqual(f.assignedTimes, []); clean(f);
  }
});

test("a pending seek waits for completion even when decoded data is available", async () => {
  const f = fixture({ seeking: true }); let settled = false;
  const result = waitForPresentedVideoFrame(f.video, f.options).then(value => { settled = true; return value; });
  f.decoded(); f.video.dispatchEvent(new Event("canplay")); await Promise.resolve();
  assert.equal(settled, false); assert.deepEqual(f.assignedTimes, []);
  f.seeked(); assert.equal(await result, true); clean(f);
});

test("seek completion alone cannot permit an undecoded frame", async () => {
  const f = fixture({ readyState: 1, seeking: true }); let settled = false;
  const result = waitForPresentedVideoFrame(f.video, f.options).then(value => { settled = true; return value; });
  f.seeked(); await Promise.resolve(); assert.equal(settled, false);
  f.decoded(); assert.equal(await result, true);
  assert.equal(f.video.preload, "metadata"); assert.deepEqual(f.assignedTimes, []); clean(f);
});

test("a pending seek times out without capturing and releases listeners", async () => {
  const f = fixture({ seeking: true });
  assert.equal(await waitForPresentedVideoFrame(f.video, { ...f.options, timeoutMs: 15 }), false);
  assert.deepEqual(f.assignedTimes, []); clean(f);
});

test("metadata-only media decodes through loadeddata without paint and restores preload", async () => {
  const f = fixture({ readyState: 1 }); let settled = false;
  const result = waitForPresentedVideoFrame(f.video, f.options).then(value => { settled = true; return value; });
  assert.equal(f.video.preload, "auto"); await Promise.resolve(); assert.equal(settled, false);
  f.decoded(); assert.equal(await result, true);
  assert.equal(f.video.preload, "metadata"); assert.equal(f.video.currentTime, 4.25);
  assert.deepEqual(f.assignedTimes, []); clean(f);
});

test("unloaded metadata and zero dimensions cannot permit canvas", async () => {
  const f = fixture({ readyState: 0 }); f.video.videoWidth = 0; f.video.videoHeight = 0; let settled = false;
  const result = waitForPresentedVideoFrame(f.video, f.options).then(value => { settled = true; return value; });
  f.video.readyState = 1; f.video.dispatchEvent(new Event("loadedmetadata")); await Promise.resolve(); assert.equal(settled, false);
  f.decoded(); await Promise.resolve(); assert.equal(settled, false);
  Object.assign(f.video, { videoWidth: 960, videoHeight: 448 }); f.video.dispatchEvent(new Event("canplay"));
  assert.equal(await result, true); assert.deepEqual(f.assignedTimes, []); clean(f);
});

test("source or time replacement before the initial microtask rejects the old frame", async () => {
  for (const change of [f => { f.video.src = "replacement"; }, f => { f.video.currentSrc = "replacement"; }, f => { f.video.currentTime = 7; }]) {
    const f = fixture(), result = waitForPresentedVideoFrame(f.video, f.options);
    change(f); assert.equal(await result, false); clean(f);
  }
});

test("an old decoded currentSrc cannot satisfy a replacement src", async () => {
  const f = fixture(); f.video.currentSrc = "https://local.test/previous.mp4";
  assert.equal(await waitForPresentedVideoFrame(f.video, f.options), false);
  assert.equal(f.video.pauses, 0); clean(f);
});

test("an initially empty currentSrc may load only the selected source", async () => {
  for (const currentSource of ["https://local.test/video-a.mp4", "https://local.test/previous.mp4"]) {
    const f = fixture({ readyState: 1 }); f.video.currentSrc = "";
    const result = waitForPresentedVideoFrame(f.video, f.options);
    f.video.currentSrc = currentSource; f.decoded();
    assert.equal(await result, currentSource === f.video.src); clean(f);
  }
});

test("source replacement or a new seek during loading rejects the old frame", async () => {
  for (const change of [f => { f.video.src = "replacement"; f.decoded(); }, f => { f.video.currentSrc = "replacement"; f.decoded(); }, f => { f.video.currentTime = 7; }]) {
    const f = fixture({ readyState: 1 }), result = waitForPresentedVideoFrame(f.video, f.options);
    await Promise.resolve(); change(f); assert.equal(await result, false); clean(f);
  }
});

test("a same-time seek started while loading still requires seeked", async () => {
  const f = fixture({ readyState: 1 }); let settled = false;
  const result = waitForPresentedVideoFrame(f.video, f.options).then(value => { settled = true; return value; });
  f.video.currentTime = 4.25; f.decoded(); await Promise.resolve(); assert.equal(settled, false);
  f.seeked(); assert.equal(await result, true); clean(f);
});

test("errors, emptied, aborts and timeouts retain failure and preload cleanup", async () => {
  for (const action of ["error", "emptied", "abort", "timeout"]) {
    const f = fixture({ readyState: 1 }), controller = new AbortController();
    const result = waitForPresentedVideoFrame(f.video, { ...f.options, timeoutMs: 15, signal: controller.signal });
    if (action === "error" || action === "emptied") f.video.dispatchEvent(new Event(action));
    else if (action === "abort") controller.abort();
    assert.equal(await result, false); assert.equal(f.video.preload, "metadata"); clean(f);
  }
});

test("abort before the initial decoded-frame check prevents capture", async () => {
  const f = fixture(), controller = new AbortController();
  const result = waitForPresentedVideoFrame(f.video, { ...f.options, signal: controller.signal });
  controller.abort(); assert.equal(await result, false); clean(f);
});

test("invalid initial times and pre-aborted requests do not touch media", async () => {
  for (const time of [NaN, Infinity, -1]) {
    const f = fixture(); f.video.currentTime = time;
    assert.equal(await waitForPresentedVideoFrame(f.video, f.options), false);
    assert.equal(f.video.pauses, 0); clean(f);
  }
  const f = fixture(), controller = new AbortController(); controller.abort();
  assert.equal(await waitForPresentedVideoFrame(f.video, { ...f.options, signal: controller.signal }), false);
  assert.equal(f.video.pauses, 0); clean(f);
});

test("preload cleanup does not overwrite a replacement source or a caller preference", async () => {
  for (const change of [f => { f.video.src = "replacement"; }, f => { f.video.preload = "none"; }]) {
    const f = fixture({ readyState: 1 }), controller = new AbortController();
    const result = waitForPresentedVideoFrame(f.video, { ...f.options, signal: controller.signal });
    change(f); const expected = f.video.preload; controller.abort();
    assert.equal(await result, false); assert.equal(f.video.preload, expected); clean(f);
  }
});
