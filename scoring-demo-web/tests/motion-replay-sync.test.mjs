import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import ts from "typescript";

const file = new URL("../app/lib/motion-replay-sync.ts", import.meta.url);
const js = ts.transpileModule(fs.readFileSync(file, "utf8"), { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const { MotionReplaySynchronizer, replayVideoTime } = await import(`data:text/javascript;base64,${Buffer.from(js).toString("base64")}`);
function media(duration = 6) {
  return { duration, currentTime: 0, readyState: 4, seeking: false, paused: true, playbackRate: 1, play() { this.paused = false; return Promise.resolve(); }, pause() { this.paused = true; }, load() { this.readyState = 4; } };
}
function fixture() {
  const original = media(), reference = media(), generated = media(6.583);
  const sync = new MotionReplaySynchronizer(Object.entries({ original, reference, generated }).map(([id, video]) => ({ id, video })), 6);
  return { original, reference, generated, sync };
}

test("shared seconds preserve real AI duration and freeze shorter videos at their own end", () => {
  const { original, reference, generated, sync } = fixture();
  sync.seek(6.25);
  const state = sync.tick(0);
  assert.equal(state.duration, 6.583);
  assert.equal(original.currentTime, 6);
  assert.equal(reference.currentTime, 6);
  assert.equal(generated.currentTime, 6.25);
  assert.equal(state.videos.original, "ended");
  assert.equal(state.videos.generated, "ready");
  assert.equal(replayVideoTime(3, 6.583), 3);
  sync.play(); sync.tick(10); const next = sync.tick(110);
  assert.equal(original.paused, true);
  assert.equal(generated.paused, false);
  assert.ok(Math.abs(next.elapsed - 6.35) < 0.00001);
});

test("buffering freezes the shared clock and resumes all sources without losing play intent", () => {
  const { original, reference, generated, sync } = fixture();
  sync.play(); sync.tick(0);
  generated.readyState = 2;
  let state = sync.tick(100);
  assert.equal(state.buffering, true);
  assert.equal(state.playing, true);
  assert.equal(original.paused, true);
  assert.equal(reference.paused, true);
  assert.equal(state.elapsed, 0.1);
  assert.equal(sync.tick(2100).elapsed, 0.1);
  generated.readyState = 4;
  state = sync.tick(2200);
  assert.equal(state.buffering, false);
  assert.equal(original.paused, false);
  assert.equal(generated.paused, false);
  assert.ok(Math.abs(sync.tick(2300).elapsed - 0.2) < 0.00001);
});

test("one failed or stalled media does not permanently block the remaining replays", () => {
  const { original, reference, generated, sync } = fixture();
  generated.readyState = 0;
  sync.play(); sync.tick(0);
  let state = sync.tick(10001);
  assert.equal(state.videos.generated, "unavailable");
  assert.equal(state.buffering, false);
  assert.equal(original.paused, false);
  sync.fail("reference");
  state = sync.tick(10101);
  assert.equal(state.videos.reference, "unavailable");
  assert.equal(state.playing, true);
  assert.equal(original.paused, false);
  assert.equal(reference.paused, true);
  sync.retry("generated");
  state = sync.tick(10201);
  assert.equal(state.videos.generated, "ready");
});

test("manual frame stepping pauses all media at a common second and speed changes do not rescale positions", () => {
  const { original, reference, generated, sync } = fixture();
  sync.play(); sync.tick(0);
  sync.seek(2 + 1 / 24);
  sync.setRate(0.25);
  let state = sync.tick(100);
  assert.equal(state.playing, false);
  for (const video of [original, reference, generated]) {
    assert.equal(video.currentTime, 2 + 1 / 24);
    assert.equal(video.paused, true);
    assert.equal(video.playbackRate, 0.25);
  }
  sync.play(); sync.tick(200); state = sync.tick(1200);
  assert.ok(Math.abs(state.elapsed - (2 + 1 / 24 + 0.25)) < 0.00001);
});
