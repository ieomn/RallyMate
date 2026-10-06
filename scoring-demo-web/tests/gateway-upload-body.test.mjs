import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import http from "node:http";
import { once } from "node:events";
import { createHash } from "node:crypto";
import ts from "typescript";

const source = fs.readFileSync(new URL("../worker/gateway.ts", import.meta.url), "utf8");
const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const { proxyAnalysis } = await import(`data:text/javascript;base64,${Buffer.from(js).toString("base64")}`);
const CHUNK_BYTES = 4 * 1024 * 1024;
const path = "/v1/uploads/buffered-upload-123/chunks/0";
const env = { RALLYMATE_API_ORIGIN: "http://127.0.0.1:8001", RALLYMATE_API_KEY: "internal-test-key" };
const request = (body, headers = {}, signal) => new Request(`http://localhost${path}`, {
  method: "PUT", body, duplex: "half", signal,
  headers: { origin: "http://localhost", "content-type": "application/octet-stream", ...headers },
});
const tick = () => new Promise(resolve => setTimeout(resolve, 5));
const sha = bytes => createHash("sha256").update(bytes).digest("hex");

test("fragmented slow upload reaches real upstream only when complete, with exact length and checksum", async t => {
  const expected = Buffer.from("first fragment|second fragment|last fragment");
  const parts = [expected.subarray(0, 15), expected.subarray(15, 31), expected.subarray(31)];
  let calls = 0;
  const upstream = http.createServer(async (req, res) => {
    calls++;
    const chunks = [];
    for await (const part of req) chunks.push(part);
    const received = Buffer.concat(chunks);
    res.setHeader("content-type", "application/json");
    res.end(JSON.stringify({ bytes: received.length, digest: sha(received), declared: req.headers["content-length"], transferEncoding: req.headers["transfer-encoding"] ?? null, checksum: req.headers["x-chunk-sha256"], authorization: req.headers.authorization }));
  });
  upstream.listen(0, "127.0.0.1");
  await once(upstream, "listening");
  t.after(() => { upstream.closeAllConnections(); upstream.close(); });
  let controller;
  const body = new ReadableStream({ start(value) { controller = value; } });
  const output = proxyAnalysis(request(body, { "content-length": "1", "x-chunk-sha256": sha(expected), authorization: "Bearer browser-must-not-forward" }), { ...env, RALLYMATE_API_ORIGIN: `http://127.0.0.1:${upstream.address().port}` });
  for (const part of parts) { controller.enqueue(part); await tick(); assert.equal(calls, 0); }
  controller.close();
  const response = await output;
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { bytes: expected.length, digest: sha(expected), declared: String(expected.length), transferEncoding: null, checksum: sha(expected), authorization: "Bearer internal-test-key" });
  assert.equal(calls, 1);
});

test("exact 4 MiB chunk is forwarded once as a byte body without duplex streaming", async t => {
  const expected = Buffer.alloc(CHUNK_BYTES, 91);
  let count = 0;
  t.mock.method(globalThis, "fetch", async (_url, init) => {
    count++;
    assert.ok(init.body instanceof ArrayBuffer);
    assert.equal(init.headers.get("content-length"), String(CHUNK_BYTES));
    assert.equal(init.duplex, undefined);
    assert.equal(sha(Buffer.from(init.body)), sha(expected));
    return Response.json({ received_chunks: [0] });
  });
  assert.equal((await proxyAnalysis(request(expected), env)).status, 200);
  assert.equal(count, 1);
});

test("oversized actual bytes are rejected even with absent or forged smaller length", async t => {
  t.mock.method(globalThis, "fetch", () => assert.fail("oversized body reached upstream"));
  for (const headers of [{}, { "content-length": "1" }]) {
    let cancelled = false;
    const body = new ReadableStream({
      start(controller) { controller.enqueue(new Uint8Array(CHUNK_BYTES)); controller.enqueue(new Uint8Array(1)); },
      cancel() { cancelled = true; },
    });
    const response = await proxyAnalysis(request(body, headers), env);
    assert.equal(response.status, 413);
    assert.equal(cancelled, true);
  }
});

test("declared oversize, null body and empty stream are rejected before upstream", async t => {
  t.mock.method(globalThis, "fetch", () => assert.fail("invalid body reached upstream"));
  let pulled = false, cancelled = false;
  const body = new ReadableStream({ pull() { pulled = true; }, cancel() { cancelled = true; } }, { highWaterMark: 0 });
  assert.equal((await proxyAnalysis(request(body, { "content-length": String(CHUNK_BYTES + 1) }), env)).status, 413);
  assert.equal(pulled, false);
  assert.equal(cancelled, true);
  for (const empty of [null, new Uint8Array(0)]) {
    assert.equal((await proxyAnalysis(request(empty), env)).status, 400);
  }
});

test("interrupted chunk is retryable and never forwards partial bytes", async t => {
  t.mock.method(globalThis, "fetch", () => assert.fail("partial body reached upstream"));
  let controller;
  const body = new ReadableStream({ start(value) { controller = value; } });
  const result = proxyAnalysis(request(body), env);
  controller.enqueue(Buffer.from("partial"));
  await tick();
  controller.error(new Error("connection lost"));
  const response = await result;
  assert.equal(response.status, 408);
  assert.deepEqual(await response.json(), { error: "upload_chunk_interrupted" });
});

test("80-second whole-attempt deadline cancels stalled reads without waiting for source cancellation", async t => {
  t.mock.method(globalThis, "fetch", () => assert.fail("timed out body reached upstream"));
  const deadline = new AbortController();
  t.mock.method(AbortSignal, "timeout", ms => { assert.equal(ms, 80000); return deadline.signal; });
  let cancelled = false;
  const body = new ReadableStream({ cancel() { cancelled = true; return new Promise(() => {}); } });
  const result = proxyAnalysis(request(body), env);
  deadline.abort(new Error("deadline"));
  assert.equal((await result).status, 408);
  assert.equal(cancelled, true);
});

test("caller abort during upload cancels receipt and does not reach upstream", async t => {
  t.mock.method(globalThis, "fetch", () => assert.fail("aborted body reached upstream"));
  const controller = new AbortController();
  let cancelled = false;
  const body = new ReadableStream({ cancel() { cancelled = true; } });
  const result = proxyAnalysis(request(body, {}, controller.signal), env);
  controller.abort();
  assert.equal((await result).status, 408);
  assert.equal(cancelled, true);
});

test("the same chunk deadline also bounds the upstream request", async t => {
  const deadline = new AbortController();
  t.mock.method(AbortSignal, "timeout", ms => { assert.equal(ms, 80000); return deadline.signal; });
  t.mock.method(globalThis, "fetch", async (_url, init) => {
    deadline.abort(new Error("upstream timeout"));
    init.signal.throwIfAborted();
  });
  assert.equal((await proxyAnalysis(request(Buffer.from("complete")), env)).status, 408);
});

test("cross-origin upload is rejected before consuming or forwarding any body", async t => {
  t.mock.method(globalThis, "fetch", () => assert.fail("cross-origin body reached upstream"));
  let pulled = false;
  const body = new ReadableStream({ pull() { pulled = true; } }, { highWaterMark: 0 });
  const response = await proxyAnalysis(request(body, { origin: "https://attacker.example" }), env);
  assert.equal(response.status, 403);
  assert.equal(pulled, false);
});
