import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import ts from "typescript";

const modules = new Map();
function compiledUrl(file) {
  // Keep source JSON imports shared with production, without a generated copy.
  if (file.pathname.endsWith(".json")) return `data:text/javascript;base64,${Buffer.from(`export default ${fs.readFileSync(file, "utf8")}`).toString("base64")}`;
  if (modules.has(file.href)) return modules.get(file.href);
  let js = ts.transpileModule(fs.readFileSync(file, "utf8"), { fileName: file.pathname,
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  js = js.replace(/from "([^"]+)"/g, (_match, name) => {
    const local = new URL(name.endsWith(".json") ? name : `${name}.ts`, file);
    return `from ${JSON.stringify(name.startsWith(".") ? compiledUrl(fs.existsSync(local) ? local : new URL(`${name}.tsx`, file)) : import.meta.resolve(name))}`;
  });
  const url = `data:text/javascript;base64,${Buffer.from(js).toString("base64")}`;
  modules.set(file.href, url);
  return url;
}
const { candidateReplayTarget, createCandidateReplayController } = await import(compiledUrl(new URL("../app/BallTrajectoryViewer.tsx", import.meta.url)));
const SHA = "a".repeat(64);
const ranges = values => ({ length: values.length, start: i => values[i][0], end: i => values[i][1] });

function fixture(bindingOverrides = {}) {
  const binding = { jobId: "job-a", sessionKey: "session-1", mediaKey: "annotated-a", kind: "annotated",
    videoSha256: SHA, originMs: 2000, mappingVerified: true, ...bindingOverrides };
  let currentTime = 0;
  const writes = [], applied = [];
  const video = { src: "https://local.test/media-a.mp4", currentSrc: "https://local.test/media-a.mp4", readyState: 4,
    duration: 10, seekable: ranges([[0, 10]]), pauses: 0, pause() { this.pauses++; },
    get currentTime() { return currentTime; }, set currentTime(value) { writes.push(value); currentTime = value; } };
  const context = { binding, jobId: binding.jobId, videoSrc: "/media-a.mp4", videoTimeOriginMs: 2000, video, baseUrl: "https://local.test/" };
  const controller = createCandidateReplayController({ getContext: () => context, onApplied: value => applied.push(value) });
  const request = { jobId: "job-a", sessionKey: "session-1", identityKey: "manifest-sha:artifact-sha", candidateKey: "170:171",
    videoSha256: SHA, videoTimestampMs: 5667 };
  return { binding, video, context, controller, request, writes, applied, signal: new AbortController() };
}

test("candidate source PTS never replaces its bound video clock with a verified nonzero media origin", () => {
  const f = fixture();
  // The evidence may retain PTS=45400,tb=1/600 (75666.666...ms); seek gets only its bound video clock.
  assert.deepEqual(f.controller.seek(f.request, f.signal.signal), { status: "applied" });
  assert.deepEqual(f.writes, [3.667]);
  assert.deepEqual(f.applied, [5667]);
  assert.equal(f.video.pauses, 1);
});

test("candidate time gate rejects negative and terminal/outside duration without clamping or publishing", () => {
  for (const timestamp of [1000, 12000, 12001, NaN, Infinity]) {
    const f = fixture();
    assert.equal(f.controller.seek({ ...f.request, videoTimestampMs: timestamp }, f.signal.signal).status, "rejected");
    assert.deepEqual(f.writes, []);
    assert.deepEqual(f.applied, []);
  }
});

test("seekable gaps reject even inside duration; unrelated ball ranges play no part", () => {
  const f = fixture();
  f.video.seekable = ranges([[0, 2], [4, 10]]);
  assert.equal(f.controller.seek(f.request, f.signal.signal).reason, "outside_media_seekable");
  assert.deepEqual(f.writes, []);
  f.video.seekable = ranges([[0, 10]]);
  assert.equal(f.controller.seek(f.request, f.signal.signal).status, "applied");
  assert.deepEqual(f.writes, [3.667]);
});

test("unknown mapping, source SHA and missing candidate identity cannot establish eligibility", () => {
  for (const change of [
    { mappingVerified: false },
    { originMs: null },
    { originMs: NaN },
    { videoSha256: null },
  ]) {
    const f = fixture(change);
    assert.equal(f.controller.seek(f.request, f.signal.signal).status, "rejected");
    assert.deepEqual(f.writes, []);
  }
  for (const change of [{ videoSha256: "b".repeat(64) }, { jobId: "job-b" }, { sessionKey: "session-2" }, { identityKey: "" }, { candidateKey: "" }]) {
    const f = fixture();
    assert.equal(f.controller.seek({ ...f.request, ...change }, f.signal.signal).status, "rejected");
    assert.deepEqual(f.writes, []);
  }
});

test("metadata-delayed request applies once after the same source obtains a seekable range", () => {
  const f = fixture();
  f.video.readyState = 0; f.video.currentSrc = ""; f.video.duration = NaN; f.video.seekable = ranges([]);
  assert.equal(f.controller.seek(f.request, f.signal.signal).reason, "media_metadata_pending");
  f.video.readyState = 1; f.video.currentSrc = f.video.src; f.video.duration = 10;
  assert.equal(f.controller.resumePending().reason, "media_seekable_pending");
  assert.deepEqual(f.writes, []);
  f.video.seekable = ranges([[0, 10]]);
  assert.equal(f.controller.resumePending().status, "applied");
  assert.equal(f.controller.resumePending(), null);
  assert.deepEqual(f.writes, [3.667]);
});

test("pending revalidates newly known duration and rejects outside it", () => {
  const f = fixture(); f.video.readyState = 0;
  assert.equal(f.controller.seek(f.request, f.signal.signal).status, "pending");
  f.video.readyState = 1; f.video.duration = 2; f.video.seekable = ranges([[0, 2]]);
  assert.equal(f.controller.resumePending().reason, "outside_media_duration");
  assert.equal(f.controller.resumePending(), null);
  assert.deepEqual(f.writes, []);
});

test("pending is invalidated by job/session/media/node/origin changes including same-job replacement", () => {
  const changes = {
    job: f => { f.context.jobId = "job-b"; },
    session: f => { f.context.binding = { ...f.binding, sessionKey: "session-2" }; },
    mediaKey: f => { f.context.binding = { ...f.binding, mediaKey: "media-b" }; },
    srcProp: f => { f.context.videoSrc = "/media-b.mp4"; },
    srcNode: f => { f.video.src = "https://local.test/media-b.mp4"; },
    currentSrc: f => { f.video.currentSrc = "https://local.test/media-b.mp4"; },
    node: f => { f.context.video = { ...f.video }; },
    origin: f => { f.context.binding = { ...f.binding, originMs: 3000 }; },
    actualViewerOrigin: f => { f.context.videoTimeOriginMs = 3000; },
    proof: f => { f.context.binding = { ...f.binding, mappingVerified: false }; },
  };
  for (const [name, change] of Object.entries(changes)) {
    const f = fixture(); f.video.readyState = 0;
    assert.equal(f.controller.seek(f.request, f.signal.signal).status, "pending", name);
    change(f); f.video.readyState = 1;
    assert.equal(f.controller.resumePending().status, "rejected", name);
    assert.deepEqual(f.writes, [], name);
    assert.equal(f.controller.resumePending(), null, name);
  }
});

test("abort clears a pending selection and cannot publish after metadata", () => {
  const f = fixture(); f.video.readyState = 0;
  assert.equal(f.controller.seek(f.request, f.signal.signal).status, "pending");
  f.signal.abort(); f.video.readyState = 1;
  assert.equal(f.controller.resumePending(), null);
  assert.deepEqual(f.writes, []);
  assert.equal(f.controller.seek(f.request, f.signal.signal).reason, "request_aborted");
});

test("new candidate replaces prior pending and snapshots request against caller mutation", () => {
  const f = fixture(); f.video.readyState = 0;
  assert.equal(f.controller.seek(f.request, f.signal.signal).status, "pending");
  const nextSignal = new AbortController();
  const next = { ...f.request, identityKey: "manifest-b", candidateKey: "180:181", videoTimestampMs: 6000 };
  assert.equal(f.controller.seek(next, nextSignal.signal).status, "pending");
  next.videoTimestampMs = 9000;
  f.signal.abort(); // Old abort must not cancel the replacement.
  f.video.readyState = 1;
  assert.equal(f.controller.resumePending().status, "applied");
  assert.deepEqual(f.writes, [4]);
});

test("cancel and dispose clear pending; disposed handle remains unusable", () => {
  for (const method of ["cancel", "dispose"]) {
    const f = fixture(); f.video.readyState = 0;
    f.controller.seek(f.request, f.signal.signal); f.controller[method](); f.video.readyState = 1;
    assert.equal(f.controller.resumePending(), null);
    assert.deepEqual(f.writes, []);
    assert.equal(f.controller.seek(f.request, f.signal.signal).status, method === "dispose" ? "rejected" : "applied");
  }
});

test("pause callback cannot change scope and still move the old media", () => {
  const f = fixture();
  f.video.pause = () => { f.context.binding = { ...f.binding, sessionKey: "session-2" }; };
  assert.equal(f.controller.seek(f.request, f.signal.signal).status, "rejected");
  assert.deepEqual(f.writes, []);
  assert.deepEqual(f.applied, []);
});

test("media seek errors do not publish a successful position", () => {
  const f = fixture();
  Object.defineProperty(f.video, "currentTime", { set() { throw new Error("not seekable"); } });
  assert.equal(f.controller.seek(f.request, f.signal.signal).reason, "media_seek_failed");
  assert.deepEqual(f.applied, []);
});

test("pure target gate distinguishes unknown range from invalid duration and verified origin zero", () => {
  const video = { readyState: 1, duration: 10, seekable: ranges([[0, 10]]) };
  assert.deepEqual(candidateReplayTarget(0, 0, video), { status: "ready", seconds: 0 });
  assert.equal(candidateReplayTarget(2000, null, video).reason, "time_mapping_unavailable");
  assert.equal(candidateReplayTarget(2000, 0, { ...video, duration: Infinity }).reason, "media_duration_unavailable");
  assert.equal(candidateReplayTarget(2000, 0, { ...video, seekable: ranges([]) }).status, "pending");
});

test("a verified candidate origin must equal the current viewer playback origin", () => {
  const f = fixture();
  f.context.videoTimeOriginMs = 0;
  assert.equal(f.controller.seek(f.request, f.signal.signal).reason, "replay_time_origin_mismatch");
  assert.deepEqual(f.writes, []);
  assert.deepEqual(f.applied, []);
});
