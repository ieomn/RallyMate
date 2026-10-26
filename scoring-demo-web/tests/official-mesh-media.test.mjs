import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import ts from "typescript";

const source = fs.readFileSync(new URL("../app/lib/official-mesh-media.ts", import.meta.url), "utf8");
const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const { verifiedMeshMedia, watchMeshPreview } = await import(`data:text/javascript;base64,${Buffer.from(js).toString("base64")}`);
const jobId = "mesh-job";
const base = `/v1/jobs/${jobId}/artifacts`;
const media = {
  status: "ready", video_url: `${base}/sam3d-mesh.mp4`, side_video_url: `${base}/sam3d-mesh-side.mp4`,
  preview_image_url: `${base}/sam3d-mesh-preview.jpg`, manifest_url: `${base}/sam3d-mesh-manifest.json`,
  timing_preserved: true, source_video_sha256: "a".repeat(64), source_timestamp_origin_ms: 18000,
  source_relative_origin_ms: 18000, frame_count: 90, renderer: "official-sam3d-pyrender-persistent-context",
};

test("online mesh uses only this job's authenticated media paths", () => {
  assert.equal(verifiedMeshMedia(media, jobId, true).video_url, media.video_url);
  assert.equal(verifiedMeshMedia(media, jobId, false), null);
  assert.equal(verifiedMeshMedia(media, "other-job", true), null);
  assert.equal(verifiedMeshMedia({ ...media, video_url: `${media.video_url}?token=untrusted` }, jobId, true), null);
  assert.equal(verifiedMeshMedia({ ...media, timing_preserved: false }, jobId, true), null);
});

test("pending background render becomes visible using only the lightweight mesh endpoint", async () => {
  let calls = 0;
  const states = [];
  await watchMeshPreview(async id => {
    assert.equal(id, jobId);
    return ++calls === 1 ? { job_id: id, status: "pending" } : { job_id: id, status: "ready", sam3d_mesh: media };
  }, jobId, new AbortController().signal, state => states.push(state), 1);
  assert.deepEqual(states.map(state => state.status), ["pending", "ready"]);
  assert.equal(states[1].media.video_url, media.video_url);
});

test("a late ready response after navigation is ignored", async () => {
  const controller = new AbortController();
  const states = [];
  await watchMeshPreview(async () => {
    controller.abort();
    return { job_id: jobId, status: "ready", sam3d_mesh: media };
  }, jobId, controller.signal, state => states.push(state), 1);
  assert.deepEqual(states, []);
});

test("a mismatched job cannot supply a valid-looking mesh descriptor", async () => {
  await assert.rejects(watchMeshPreview(async () => ({ job_id: "other-job", status: "ready", sam3d_mesh: media }),
    jobId, new AbortController().signal, () => assert.fail("wrong job must never update"), 1), /another training session/);
});

test("render failure ends polling without affecting the numerical report", async () => {
  let calls = 0;
  const states = [];
  await watchMeshPreview(async () => { calls++; return { job_id: jobId, status: "failed" }; },
    jobId, new AbortController().signal, state => states.push(state), 1);
  assert.equal(calls, 1);
  assert.deepEqual(states, [{ status: "failed", media: null }]);
});

test("navigation cancels an outstanding polling delay promptly", async () => {
  const controller = new AbortController();
  const promise = watchMeshPreview(async () => ({ job_id: jobId, status: "pending" }),
    jobId, controller.signal, () => setTimeout(() => controller.abort(new Error("navigation")), 0), 10000);
  await assert.rejects(promise, /navigation/);
});

test("consecutive network failures stop after three attempts and a fresh watch can recover", async () => {
  let reads = 0;
  await assert.rejects(watchMeshPreview(async () => { reads++; throw new TypeError("network unavailable"); },
    jobId, new AbortController().signal, () => assert.fail("failed reads cannot certify a render"), 0), /network unavailable/);
  assert.equal(reads, 3);
  const states = [];
  await watchMeshPreview(async () => ({ job_id: jobId, status: "ready", sam3d_mesh: media }), jobId,
    new AbortController().signal, state => states.push(state), 0);
  assert.equal(states[0].status, "ready");
});

test("permanent mesh endpoint errors do not keep retrying", async () => {
  let reads = 0;
  await assert.rejects(watchMeshPreview(async () => { reads++; throw Object.assign(new Error("missing"), { status: 404 }); },
    jobId, new AbortController().signal, () => {}, 0), /missing/);
  assert.equal(reads, 1);
});

test("a continuously pending renderer has a total deadline and late readiness is ignored", async t => {
  const deadline = new AbortController();
  t.mock.method(AbortSignal, "timeout", ms => { assert.equal(ms, 120000); return deadline.signal; });
  const states = [];
  await assert.rejects(watchMeshPreview(async () => {
    deadline.abort(new DOMException("mesh wait expired", "TimeoutError"));
    return { job_id: jobId, status: "ready", sam3d_mesh: media };
  }, jobId, new AbortController().signal, state => states.push(state), 0), { name: "TimeoutError" });
  assert.deepEqual(states, []);
});
