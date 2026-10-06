import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { createHash } from "node:crypto";
import ts from "typescript";

function moduleUrl(path, dependencies = {}) {
  const source = fs.readFileSync(new URL(path, import.meta.url), "utf8");
  let js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
  for (const [specifier, url] of Object.entries(dependencies)) js = js.replaceAll(`from "${specifier}"`, `from "${url}"`);
  return `data:text/javascript;base64,${Buffer.from(js).toString("base64")}`;
}
const typesUrl = moduleUrl("../app/lib/api-types.ts");
const { RallyMateApiError } = await import(typesUrl);
const { uploadResumable } = await import(moduleUrl("../app/lib/resumable-upload.ts", { "./api-types": typesUrl }));
const { proxyAnalysis } = await import(moduleUrl("../worker/gateway.ts"));
const CHUNK = 4 * 1024 * 1024;
const sha = bytes => createHash("sha256").update(bytes).digest("hex");
const urlFor = path => `https://rallymate.example${path}`;
const parse = async response => {
  const body = await response.json();
  if (!response.ok) throw new RallyMateApiError(body.detail || "request failed", response.status, body);
  return body;
};

function browserStorage(t) {
  const previous = Object.getOwnPropertyDescriptor(globalThis, "localStorage");
  const entries = new Map();
  Object.defineProperty(globalThis, "localStorage", { configurable: true, value: {
    getItem: key => entries.get(key) ?? null,
    setItem: (key, value) => entries.set(key, String(value)),
    removeItem: key => entries.delete(key),
  } });
  t.after(() => {
    if (previous) Object.defineProperty(globalThis, "localStorage", previous);
    else delete globalThis.localStorage;
  });
  // Exercise the actual retry loop without spending seconds on its backoff.
  const originalTimer = globalThis.setTimeout;
  t.mock.method(globalThis, "setTimeout", (callback, delay, ...args) =>
    originalTimer(callback, delay >= 700 && delay <= 8000 ? 0 : delay, ...args));
  return entries;
}

function protocol(hooks = {}) {
  const sessions = new Map();
  const requests = [];
  let activePuts = 0, maxActivePuts = 0, createdJobs = 0;
  const snapshot = session => ({
    upload_id: session.upload_id, size: session.size, chunk_size: session.chunkSize,
    chunk_count: Math.ceil(session.size / session.chunkSize),
    received_chunks: [...session.chunks.keys()].sort((a, b) => a - b),
    chunk_sha256: Object.fromEntries([...session.chunks].map(([i, bytes]) => [i, sha(bytes)])),
    received_bytes: [...session.chunks.values()].reduce((sum, bytes) => sum + bytes.length, 0),
    job_id: session.job?.id ?? null,
  });
  const fetch = async (url, init) => {
    const path = new URL(url).pathname;
    const request = { path, method: init.method, headers: new Headers(init.headers) };
    requests.push(request);
    assert.ok(init.signal instanceof AbortSignal);
    assert.ok(request.headers.get("x-request-id"));
    if (path === "/v1/uploads" && init.method === "POST") {
      const payload = JSON.parse(init.body);
      request.uploadId = payload.upload_id;
      const intercepted = await hooks.beforeCreate?.(payload, sessions);
      if (intercepted) return intercepted;
      if (!sessions.has(payload.upload_id)) sessions.set(payload.upload_id, { ...payload, chunkSize: hooks.chunkSize ?? CHUNK, chunks: new Map(), job: null });
      const session = sessions.get(payload.upload_id);
      assert.equal(session.size, payload.size);
      assert.equal(session.filename, payload.filename);
      return Response.json(snapshot(session), { status: 201 });
    }
    const match = path.match(/^\/v1\/uploads\/([^/]+)(?:\/(?:chunks\/(\d+)|(complete)))?$/);
    assert.ok(match, `unexpected route ${path}`);
    const session = sessions.get(match[1]);
    assert.ok(session, "session must be created first");
    request.uploadId = match[1];
    if (init.method === "GET") return Response.json(snapshot(session));
    if (match[2] !== undefined) {
      assert.equal(init.method, "PUT");
      request.index = Number(match[2]);
      activePuts++;
      maxActivePuts = Math.max(activePuts, maxActivePuts);
      try {
        await hooks.beforePut?.(request, session);
        const bytes = Buffer.from(await init.body.arrayBuffer());
        assert.equal(bytes.length, Math.min(session.chunkSize, session.size - request.index * session.chunkSize));
        assert.equal(request.headers.get("x-chunk-sha256"), sha(bytes));
        const old = session.chunks.get(request.index);
        if (old) assert.deepEqual(bytes, old, "idempotent chunk retries must preserve bytes");
        session.chunks.set(request.index, bytes);
        const replacement = await hooks.afterPut?.(request, session, snapshot(session));
        return Response.json(replacement ?? snapshot(session));
      } finally { activePuts--; }
    }
    assert.equal(init.method, "POST");
    await hooks.beforeComplete?.(request, session);
    assert.equal(session.chunks.size, Math.ceil(session.size / session.chunkSize));
    if (!session.job) { session.job = { id: session.upload_id, status: "queued" }; createdJobs++; }
    await hooks.afterComplete?.(request, session);
    return Response.json(session.job, { status: 202 });
  };
  return { sessions, requests, fetch, get maxActivePuts() { return maxActivePuts; }, get createdJobs() { return createdJobs; } };
}

function video(bytes = Buffer.alloc(CHUNK * 2 + 8192, 7)) {
  return new File([bytes], "tennis.mp4", { type: "video/mp4", lastModified: 1000 });
}

test("uploads real 4 MiB chunks with exactly two in flight and acknowledged byte progress", { timeout: 5000 }, async t => {
  const storage = browserStorage(t);
  let firstArrivals = 0, release;
  const firstPair = new Promise(resolve => { release = resolve; });
  const server = protocol({ beforePut: async request => {
    if (request.index < 2) {
      if (++firstArrivals === 2) release();
      await firstPair;
    }
  } });
  const file = video(), progress = [];
  const job = await uploadResumable(file, { onUploadProgress: p => progress.push(p) }, urlFor, server.fetch, parse);
  assert.equal(server.maxActivePuts, 2);
  assert.equal(server.createdJobs, 1);
  assert.equal(job.status, "queued");
  assert.deepEqual(server.requests.filter(r => r.method === "PUT").map(r => r.index).sort(), [0, 1, 2]);
  assert.equal(progress[0].uploadedBytes, 0);
  assert.equal(progress[0].percent, 0);
  assert.equal(progress.at(-1).phase, "merging");
  assert.equal(progress.at(-1).uploadedBytes, file.size);
  assert.equal(progress.at(-1).percent, 100);
  assert.ok(progress.some(p => p.percent > 0 && p.percent < 100));
  for (let i = 0; i < progress.length; i++) {
    const p = progress[i];
    assert.equal(p.totalBytes, file.size);
    assert.equal(p.percent, 100 * p.uploadedBytes / file.size);
    assert.ok(Number.isFinite(p.bytesPerSecond) && p.bytesPerSecond >= 0);
    if (i) assert.ok(p.uploadedBytes >= progress[i - 1].uploadedBytes);
    assert.equal(p.resumed, false);
  }
  assert.equal(storage.size, 0);
});

test("after a disconnect resume verifies saved chunks and sends only the missing chunk", async t => {
  const storage = browserStorage(t);
  let offline = true;
  const server = protocol({ beforePut: request => { if (offline && request.index === 1) throw new TypeError("network disconnected"); } });
  const file = video();
  await assert.rejects(uploadResumable(file, {}, urlFor, server.fetch, parse), /已上传的分块会保留/);
  assert.equal(server.createdJobs, 0);
  assert.equal(storage.size, 1);
  const oldId = [...server.sessions.keys()][0];
  const oldSession = server.sessions.get(oldId);
  assert.deepEqual([...oldSession.chunks.keys()].sort(), [0, 2]);
  const initialBytes = [...oldSession.chunks.values()].reduce((sum, bytes) => sum + bytes.length, 0);
  const cut = server.requests.length, progress = [];
  offline = false;
  const job = await uploadResumable(file, { onUploadProgress: p => progress.push(p) }, urlFor, server.fetch, parse);
  assert.equal(job.id, oldId);
  assert.equal(server.sessions.size, 1);
  assert.deepEqual(server.requests.slice(cut).filter(r => r.method === "PUT").map(r => r.index), [1]);
  assert.equal(progress.find(p => p.phase === "uploading").uploadedBytes, initialBytes);
  assert.equal(progress.find(p => p.phase === "uploading").resumed, true);
  assert.equal(progress.at(-1).uploadedBytes, file.size);
  assert.equal(storage.size, 0);
});

test("same name size and endpoint bytes cannot reuse uploaded chunks with different middle bytes", async t => {
  browserStorage(t);
  let offline = true;
  const server = protocol({ beforeComplete: () => { if (offline) throw new TypeError("offline before merge"); } });
  const original = Buffer.alloc(CHUNK * 3, 3);
  await assert.rejects(uploadResumable(video(original), {}, urlFor, server.fetch, parse), /已上传的分块会保留/);
  const oldId = [...server.sessions.keys()][0];
  assert.equal(server.sessions.get(oldId).chunks.size, 3);
  const changed = Buffer.from(original);
  changed[CHUNK + 125] = 9;
  assert.deepEqual(changed.subarray(0, 65536), original.subarray(0, 65536));
  assert.deepEqual(changed.subarray(-65536), original.subarray(-65536));
  offline = false;
  const cut = server.requests.length;
  const job = await uploadResumable(video(changed), {}, urlFor, server.fetch, parse);
  assert.notEqual(job.id, oldId);
  assert.equal(server.sessions.size, 2);
  const calls = server.requests.slice(cut);
  assert.deepEqual(calls.filter(r => r.path === "/v1/uploads").map(r => r.uploadId), [oldId, job.id]);
  assert.deepEqual(calls.filter(r => r.method === "PUT").map(r => r.index).sort(), [0, 1, 2]);
  assert.ok(calls.filter(r => r.method === "PUT").every(r => r.uploadId === job.id));
  const assembled = Buffer.concat([...server.sessions.get(job.id).chunks].sort(([a], [b]) => a - b).map(([, bytes]) => bytes));
  assert.deepEqual(assembled, changed);
  assert.equal(server.sessions.get(oldId).chunks.get(1)[125], 3, "old session must remain unchanged");
});

test("lost complete response retries the same id and creates only one inference job", async t => {
  const storage = browserStorage(t);
  let drop = true;
  const server = protocol({ afterComplete: () => { if (drop) { drop = false; throw new TypeError("complete response lost"); } } });
  const job = await uploadResumable(video(Buffer.alloc(4096, 2)), {}, urlFor, server.fetch, parse);
  const completions = server.requests.filter(r => r.path.endsWith("/complete"));
  assert.equal(completions.length, 2);
  assert.deepEqual(completions.map(r => r.uploadId), [job.id, job.id]);
  assert.equal(server.createdJobs, 1);
  assert.equal(server.requests.filter(r => r.method === "PUT").length, 1);
  assert.equal(storage.size, 0);
});

test("128 KiB server chunks produce confirmed incremental progress and preserve full bytes", async t => {
  browserStorage(t);
  const chunkSize = 128 * 1024;
  const server = protocol({ chunkSize });
  const file = video(Buffer.alloc(chunkSize * 5 + 123, 5));
  const progress = [];
  const job = await uploadResumable(file, { onUploadProgress: p => progress.push(p) }, urlFor, server.fetch, parse);
  const session = server.sessions.get(job.id);
  const combined = Buffer.concat([...session.chunks].sort(([a], [b]) => a - b).map(([, value]) => value));
  assert.deepEqual(combined, Buffer.from(await file.arrayBuffer()));
  assert.ok(server.maxActivePuts >= 1 && server.maxActivePuts <= 2);
  assert.equal(session.chunks.size, 6);
  assert.ok(progress.some(p => p.uploadedBytes === chunkSize && p.percent > 0 && p.percent < 25));
  assert.equal(progress.at(-1).uploadedBytes, file.size);
  assert.ok(progress.every(p => p.percent === 100 * p.uploadedBytes / file.size));
});

test("410 replaces an empty legacy session with a fresh server-sized upload without rewriting the old id", async t => {
  browserStorage(t);
  let oldId;
  const server = protocol({ chunkSize: 128 * 1024, beforeCreate(payload, sessions) {
    if (!oldId) {
      oldId = payload.upload_id;
      sessions.set(oldId, { ...payload, chunkSize: CHUNK, chunks: new Map(), job: null });
      return Response.json({ detail: "上传分块方式已更新" }, { status: 410 });
    }
  } });
  const file = video(Buffer.alloc(128 * 1024 * 3 + 17));
  const progress = [];
  const job = await uploadResumable(file, { onUploadProgress: p => progress.push(p) }, urlFor, server.fetch, parse);
  assert.notEqual(job.id, oldId);
  assert.equal(server.sessions.get(oldId).chunks.size, 0);
  assert.equal(server.sessions.get(oldId).chunkSize, CHUNK);
  assert.equal(server.sessions.get(job.id).chunkSize, 128 * 1024);
  assert.ok(server.requests.filter(r => r.method === "PUT").every(r => r.uploadId === job.id));
  assert.equal(server.sessions.get(job.id).chunks.size, 4);
  assert.equal(progress.at(-1).uploadedBytes, file.size);
});

test("lost PUT acknowledgement refreshes durable status instead of sending confirmed bytes twice", async t => {
  browserStorage(t);
  let drop = true;
  const server = protocol({ chunkSize: 128 * 1024, afterPut: () => {
    if (drop) { drop = false; throw new TypeError("PUT acknowledgement lost"); }
  } });
  const file = video(Buffer.alloc(128 * 1024 + 10));
  const progress = [];
  await uploadResumable(file, { onUploadProgress: p => progress.push(p) }, urlFor, server.fetch, parse);
  assert.deepEqual(server.requests.filter(r => r.method === "PUT").map(r => r.index).sort(), [0, 1]);
  assert.ok(server.requests.some(r => r.method === "GET" && r.path.startsWith("/v1/uploads/")));
  assert.ok(progress.some(p => p.phase === "retrying" && p.message.includes("自动重试")));
  assert.equal(progress.at(-1).uploadedBytes, file.size);
  assert.equal(server.createdJobs, 1);
});

test("concurrent stale confirmations never decrease acknowledged progress", async t => {
  browserStorage(t);
  let releaseFirst;
  const firstCanReturn = new Promise(resolve => { releaseFirst = resolve; });
  const server = protocol({ chunkSize: 128 * 1024, afterPut: async (request, _session, snapshot) => {
    if (request.index === 0) await firstCanReturn;
    else { releaseFirst(); }
    return snapshot;
  } });
  const file = video(Buffer.alloc(128 * 1024 * 2));
  const progress = [];
  await uploadResumable(file, { onUploadProgress: p => progress.push(p) }, urlFor, server.fetch, parse);
  for (let i = 1; i < progress.length; i++) assert.ok(progress[i].uploadedBytes >= progress[i - 1].uploadedBytes);
  assert.equal(progress.at(-1).uploadedBytes, file.size);
});

test("a waiting or retrying upload reports its state without inventing received bytes", async t => {
  browserStorage(t);
  const timer = globalThis.setTimeout;
  t.mock.method(globalThis, "setTimeout", (callback, ms, ...args) => timer(callback, ms === 10000 ? 1 : ms, ...args));
  let attempts = 0;
  const server = protocol({ beforePut: async () => {
    await new Promise(resolve => setTimeout(resolve, 10));
    if (!attempts++) throw new TypeError("temporary network interruption");
  } });
  const file = video(Buffer.alloc(1024));
  const progress = [];
  await uploadResumable(file, { onUploadProgress: p => progress.push(p) }, urlFor, server.fetch, parse);
  assert.ok(progress.some(p => p.phase === "preparing" && p.message.includes("准备")));
  assert.ok(progress.some(p => p.phase === "waiting" && p.message.includes("等待服务器确认")));
  assert.ok(progress.some(p => p.phase === "retrying" && p.retryAttempt === 1));
  assert.ok(progress.filter(p => p.phase === "waiting" || p.phase === "retrying").every(p => p.uploadedBytes === 0 && p.percent === 0));
  assert.equal(progress.at(-1).percent, 100);
});

test("gateway forwards PUT bytes and checksum to the loopback API without client credentials", async t => {
  const bytes = Buffer.from("transport bytes, not a separately decoded video");
  const checksum = sha(bytes), id = "12345678-1234-4234-9234-123456789abc";
  let upstreamCalls = 0;
  t.mock.method(globalThis, "fetch", async (url, init) => {
    upstreamCalls++;
    assert.equal(String(url), `http://127.0.0.1:8001/v1/uploads/${id}/chunks/0`);
    assert.equal(init.method, "PUT");
    assert.equal(init.headers.get("x-chunk-sha256"), checksum);
    assert.equal(init.headers.get("content-type"), "application/octet-stream");
    assert.equal(init.headers.get("authorization"), "Bearer server-test-key");
    assert.deepEqual(Buffer.from(await new Response(init.body).arrayBuffer()), bytes);
    return Response.json({ received_chunks: [0] });
  });
  const response = await proxyAnalysis(new Request(`http://test.trycloudflare.com/v1/uploads/${id}/chunks/0`, {
    method: "PUT", body: bytes,
    headers: { origin: "https://test.trycloudflare.com", "sec-fetch-site": "same-origin",
      "content-type": "application/octet-stream", "x-chunk-sha256": checksum, authorization: "Bearer client-must-not-forward" },
  }), { RALLYMATE_API_ORIGIN: "http://127.0.0.1:8001", RALLYMATE_LOCAL_TUNNEL: "1", RALLYMATE_API_KEY: "server-test-key" });
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { received_chunks: [0] });
  assert.equal(upstreamCalls, 1);
});

test("gateway rejects cross-site chunk writes and merge requests before reaching the API", async t => {
  t.mock.method(globalThis, "fetch", () => assert.fail("cross-site request reached the API"));
  const id = "12345678-1234-4234-9234-123456789abc";
  const env = { RALLYMATE_API_ORIGIN: "http://127.0.0.1:8001", RALLYMATE_LOCAL_TUNNEL: "1" };
  for (const [path, method] of [[`/v1/uploads/${id}/chunks/0`, "PUT"], [`/v1/uploads/${id}/complete`, "POST"], ["/v1/uploads", "POST"]]) {
    for (const headers of [{ origin: "https://attacker.example" }, { origin: "https://test.trycloudflare.com", "sec-fetch-site": "cross-site" }]) {
      const response = await proxyAnalysis(new Request(`http://test.trycloudflare.com${path}`, { method, headers }), env);
      assert.equal(response.status, 403);
      assert.deepEqual(await response.json(), { error: "origin_not_allowed" });
    }
  }
});
