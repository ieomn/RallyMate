import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import ts from "typescript";
const cache = new Map();
function compiledUrl(fileUrl) {
  if (fileUrl.pathname.endsWith(".json")) return `data:text/javascript;base64,${Buffer.from(`export default ${fs.readFileSync(fileUrl, "utf8")}`).toString("base64")}`;
  if (cache.has(fileUrl.href)) return cache.get(fileUrl.href);
  let js = ts.transpileModule(fs.readFileSync(fileUrl, "utf8"), { fileName: fileUrl.pathname, compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  js = js.replace(/from "([^"]+)"/g, (_match, name) => {
    const local = new URL(name.endsWith(".json") ? name : `${name}.ts`, fileUrl);
    return `from ${JSON.stringify(name.startsWith(".") ? compiledUrl(fs.existsSync(local) ? local : new URL(`${name}.tsx`, fileUrl)) : import.meta.resolve(name))}`;
  });
  const url = `data:text/javascript;base64,${Buffer.from(js).toString("base64")}`;
  cache.set(fileUrl.href, url); return url;
}
const { replayClipsOf, replayRangeForMedia, createReplayClipController } = await import(compiledUrl(new URL("../app/lib/replay-clips.ts", import.meta.url)));
const clip = (startMs = 1000, endMs = 2000) => ({ id: "one", label: "挥拍动作", kind: "motion", startMs, endMs, anchors: [] });
class Media extends EventTarget {
  readyState = 1; duration = 10; paused = true; seeking = false; playbackRate = 1;
  position = 0; writes = []; ranges = [[0, 10]];
  get currentTime() { return this.position; }
  set currentTime(value) { this.position = value; this.writes.push(value); }
  get seekable() { return { length: this.ranges.length, start: index => this.ranges[index][0], end: index => this.ranges[index][1] }; }
  pause() { this.paused = true; }
  play() { this.paused = false; return Promise.resolve(); }
  at(seconds, name = "timeupdate") { this.position = seconds; this.dispatchEvent(new Event(name)); }
}
const session = () => { const states = []; return { states, controller: createReplayClipController(state => states.push(state)) }; };

test("clips retain real boundaries and measured phase anchors without inventing contact or duplicate swings", () => {
  const episode = { episode_id: "swing-1", family: "baseline", start_ms: 1000, peak_ms: 1600, end_ms: 2200,
    classification: { label_zh: "挥拍候选" }, phases: [
      { phase: "preparation", status: "measured", start_ms: 1000, end_ms: 1400 },
      { phase: "acceleration", status: "measured", start_ms: 1400, end_ms: 1600 },
      { phase: "follow_through", status: "unavailable", start_ms: 1600, end_ms: 2200 },
      { phase: "recovery", status: "measured", start_ms: 2100, end_ms: 2500 },
    ] };
  const motion = { families: { baseline: { episodes: [episode, { ...episode, episode_id: "duplicate" }, { ...episode, episode_id: "bad", peak_ms: 3000 }] } } };
  const before = structuredClone(motion), clips = replayClipsOf(null, motion);
  assert.equal(clips.length, 1); assert.equal(clips[0].startMs, 1000); assert.equal(clips[0].endMs, 2200);
  assert.deepEqual(clips[0].anchors.map(row => row.sourceMs), [1000, 1400, 1600, 2200]);
  assert.equal(clips[0].anchors.find(row => row.sourceMs === 1600).label, "运动峰值");
  assert.doesNotMatch(JSON.stringify(clips), /触球|候选|恢复开始/);
  assert.deepEqual(motion, before);
  assert.deepEqual(replayClipsOf(null, null), []);
});

test("playback context is clamped to actual media while out-of-range evidence is rejected", () => {
  assert.deepEqual(replayRangeForMedia(clip(100, 900), 0, 1), { startMs: 0, endMs: 1000 });
  assert.deepEqual(replayRangeForMedia(clip(1800, 2200), 1500, 3), { startMs: 1500, endMs: 2550 });
  for (const value of [clip(1000, 2200), clip(1800, 5000), clip(-1, 2000), clip(2000, 2000)]) assert.equal(replayRangeForMedia(value, 1500, 3), null);
  assert.equal(replayRangeForMedia(clip(), 0, Infinity), null);
});

test("seek waits for metadata and seekable data, with no timer or fabricated clamp", async () => {
  const { controller, states } = session(), media = new Media(); media.readyState = 0; media.duration = NaN; media.ranges = [];
  controller.attach(media, 500, "video"); controller.seek(2500);
  assert.deepEqual(media.writes, []);
  media.readyState = 1; media.duration = 10; media.dispatchEvent(new Event("loadedmetadata")); await Promise.resolve();
  assert.deepEqual(media.writes, []);
  media.ranges = [[0, 10]]; media.dispatchEvent(new Event("progress"));
  assert.deepEqual(media.writes, [2]);
  controller.seek(100); assert.deepEqual(media.writes, [2]); assert.match(states.at(-1).notice, /不包含/);
  assert.equal(controller.seek(NaN), false); controller.dispose();
});

test("selected clips stop at their context end and loop only when explicitly enabled", () => {
  const { controller } = session(), media = new Media(); controller.attach(media, 0, "video");
  controller.select(clip()); assert.equal(media.currentTime, .65); assert.equal(media.paused, false);
  media.at(2.4); assert.equal(media.currentTime, 2.35); assert.equal(media.paused, true);
  controller.setLoop(true); controller.restart(); media.at(2.4);
  assert.equal(media.currentTime, .65); assert.equal(media.paused, false);
  controller.select(null); media.at(5); assert.equal(media.currentTime, 5);
  controller.dispose();
});

test("a clip ending at the video end loops on ended even though the media is paused", () => {
  const { controller } = session(), media = new Media(); controller.attach(media, 0, "video");
  controller.select(clip(8000, 10000)); controller.setLoop(true);
  media.paused = true; media.at(10, "ended");
  assert.equal(media.currentTime, 7.65); assert.equal(media.paused, false); controller.dispose();
});

test("original, mesh and side views retain the same source time and detach old listeners", async () => {
  const { controller, states } = session(), original = new Media(), mesh = new Media();
  controller.attach(original, 0, "original"); original.at(4.2); original.playbackRate = .5;
  mesh.readyState = 0; mesh.duration = NaN; mesh.ranges = [];
  controller.attach(mesh, 2000, "mesh");
  // The parent mesh component also restores its own view-local time here.
  mesh.addEventListener("loadedmetadata", () => { mesh.currentTime = 0; });
  mesh.readyState = 1; mesh.duration = 8; mesh.ranges = [[0, 8]];
  mesh.dispatchEvent(new Event("loadedmetadata")); await Promise.resolve();
  mesh.dispatchEvent(new Event("loadeddata")); mesh.dispatchEvent(new Event("canplay"));
  assert.equal(mesh.currentTime, 2.2); assert.equal(mesh.playbackRate, .5); assert.equal(states.at(-1).sourceMs, 4200);
  const side = new Media(); controller.attach(side, 2000, "side"); assert.equal(side.currentTime, 2.2);
  const updates = states.length; original.at(9); mesh.at(7); assert.equal(states.length, updates);
  controller.dispose(); side.at(8); assert.equal(states.length, updates);
});

test("explicit original-video requests survive a current mesh whose source range does not contain the target", () => {
  const { controller } = session(), mesh = new Media(), original = new Media();
  controller.attach(mesh, 2000, "mesh"); mesh.at(3);
  controller.seek(1000, false, true); controller.attach(original, 0, "original");
  assert.equal(original.currentTime, 1); assert.equal(original.paused, true); controller.dispose();
});

test("stale metadata microtasks cannot seek a video after its view was replaced", async () => {
  const { controller } = session(), first = new Media(), second = new Media();
  first.readyState = 0; controller.attach(first, 0, "first"); controller.seek(3000);
  first.readyState = 1; first.dispatchEvent(new Event("loadedmetadata")); controller.attach(second, 0, "second");
  await Promise.resolve(); assert.deepEqual(first.writes, []); assert.equal(second.currentTime, 3); controller.dispose();
});

test("durationchange before metadata cannot consume the source-time transfer across mesh, side and original remounts", async () => {
  const { controller, states } = session(), first = new Media(); first.duration = 30; first.ranges = [[0, 30]];
  controller.attach(first, 0, "mesh-overlay"); first.at(12); first.playbackRate = .5;
  for (const [key, origin] of [["mesh-side", 2000], ["original", 0], ["mesh-overlay", 2000], ["original", 0]]) {
    const next = new Media(); next.readyState = 0; next.duration = NaN; next.ranges = [];
    controller.attach(next, origin, key);
    next.readyState = 1; next.duration = 30; next.ranges = [[0, 30]];
    next.dispatchEvent(new Event("durationchange"));
    assert.deepEqual(next.writes, [], "metadata owns the first safe restoration boundary");
    next.addEventListener("loadedmetadata", () => { next.currentTime = 0; next.playbackRate = 1; });
    next.dispatchEvent(new Event("loadedmetadata"));
    next.dispatchEvent(new Event("timeupdate"));
    await Promise.resolve();
    assert.equal(next.currentTime, (12000 - origin) / 1000, key);
    assert.equal(next.playbackRate, .5, key);
    assert.equal(states.at(-1).sourceMs, 12000, key);
    next.dispatchEvent(new Event("loadeddata")); next.dispatchEvent(new Event("canplay"));
    assert.equal(next.currentTime, (12000 - origin) / 1000, key);
  }
  controller.dispose();
});

test("cached ready media still reapplies the transfer after a later component metadata reset", async () => {
  const { controller } = session(), original = new Media(), cached = new Media();
  controller.attach(original, 0, "original"); original.at(4.5); original.playbackRate = .25;
  controller.attach(cached, 1000, "mesh"); assert.equal(cached.currentTime, 3.5);
  cached.addEventListener("loadedmetadata", () => { cached.currentTime = 0; cached.playbackRate = 1; });
  cached.dispatchEvent(new Event("loadedmetadata")); await Promise.resolve();
  assert.equal(cached.currentTime, 3.5); assert.equal(cached.playbackRate, .25);
  controller.dispose();
});

test("a new seek during metadata dispatch supersedes restoration and waits for a seekable range", async () => {
  const { controller } = session(), original = new Media(), mesh = new Media();
  controller.attach(original, 0, "original"); original.at(4.5);
  mesh.readyState = 0; mesh.ranges = []; controller.attach(mesh, 1000, "mesh");
  mesh.readyState = 1; mesh.dispatchEvent(new Event("loadedmetadata")); controller.seek(7000);
  await Promise.resolve(); assert.deepEqual(mesh.writes, []);
  mesh.ranges = [[0, 10]]; mesh.dispatchEvent(new Event("progress"));
  assert.equal(mesh.currentTime, 6); controller.dispose();
});

test("a stale metadata task cannot overwrite a later attachment of the same element and source", async () => {
  const { controller } = session(), reused = new Media(); reused.readyState = 0;
  controller.attach(reused, 0, "same"); controller.seek(3000);
  reused.readyState = 1; reused.dispatchEvent(new Event("loadedmetadata"));
  controller.attach(null); reused.readyState = 0; controller.attach(reused, 0, "same"); controller.seek(6000);
  await Promise.resolve(); assert.deepEqual(reused.writes, []);
  reused.readyState = 1; reused.dispatchEvent(new Event("loadedmetadata")); await Promise.resolve();
  assert.equal(reused.currentTime, 6); controller.dispose();
});
