import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import ts from "typescript";

const source = fs.readFileSync(new URL("../app/lib/media-readiness.ts", import.meta.url), "utf8");
const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const { observeMediaReadiness } = await import(`data:text/javascript;base64,${Buffer.from(js).toString("base64")}`);
class Media extends EventTarget { readyState = 0; error = null; paused = false; }

test("metadata never arriving exposes a timeout and repeated stall events cannot hide it", context => {
  context.mock.timers.enable({ apis: ["setTimeout"] });
  const media = new Media(), states = [];
  const stop = observeMediaReadiness(media, value => states.push(value), 100);
  context.mock.timers.tick(99);
  media.dispatchEvent(new Event("stalled"));
  context.mock.timers.tick(1);
  assert.deepEqual(states, ["loading", "timeout"]);
  media.dispatchEvent(new Event("waiting"));
  context.mock.timers.tick(100);
  assert.deepEqual(states, ["loading", "timeout"]);
  stop();
});

test("late successful media remains usable after timeout without replacing the report", context => {
  context.mock.timers.enable({ apis: ["setTimeout"] });
  const media = new Media(), states = [];
  const stop = observeMediaReadiness(media, value => states.push(value), 100);
  context.mock.timers.tick(100);
  media.readyState = 1; media.dispatchEvent(new Event("loadedmetadata"));
  context.mock.timers.tick(1000);
  assert.deepEqual(states, ["loading", "timeout", "ready"]);
  stop();
});

test("playback buffering has a bounded wait, and normal playback clears it", context => {
  context.mock.timers.enable({ apis: ["setTimeout"] });
  const media = new Media(), states = []; media.readyState = 1;
  const stop = observeMediaReadiness(media, value => states.push(value), 100);
  media.dispatchEvent(new Event("waiting"));
  context.mock.timers.tick(50);
  media.readyState = 3; media.dispatchEvent(new Event("canplay"));
  context.mock.timers.tick(1000);
  assert.deepEqual(states, ["ready", "loading", "ready"]);
  stop();
});

test("source change or leaving the report removes both the timer and stale event listeners", context => {
  context.mock.timers.enable({ apis: ["setTimeout"] });
  const media = new Media(), states = [];
  const stop = observeMediaReadiness(media, value => states.push(value), 100);
  stop(); media.readyState = 4; media.dispatchEvent(new Event("canplay")); media.dispatchEvent(new Event("error"));
  context.mock.timers.tick(1000);
  assert.deepEqual(states, ["loading"]);
});

test("paused metadata-only preloading is not mistaken for a stalled playback", context => {
  context.mock.timers.enable({ apis: ["setTimeout"] });
  const media = new Media(), states = []; media.readyState = 1; media.paused = true;
  const stop = observeMediaReadiness(media, value => states.push(value), 100);
  media.dispatchEvent(new Event("stalled"));
  context.mock.timers.tick(1000);
  assert.deepEqual(states, ["ready"]);
  media.paused = false; media.dispatchEvent(new Event("waiting"));
  context.mock.timers.tick(100);
  assert.equal(states.at(-1), "timeout");
  stop();
});

test("failed request supports a new load attempt and clears its old deadline", context => {
  context.mock.timers.enable({ apis: ["setTimeout"] });
  const media = new Media(), states = [];
  const stop = observeMediaReadiness(media, value => states.push(value), 100);
  media.error = {}; media.dispatchEvent(new Event("error"));
  context.mock.timers.tick(100);
  media.error = null; media.dispatchEvent(new Event("loadstart"));
  media.readyState = 1; media.dispatchEvent(new Event("loadedmetadata"));
  context.mock.timers.tick(100);
  assert.deepEqual(states, ["loading", "failed", "loading", "ready"]);
  stop();
});
