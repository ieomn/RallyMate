import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import ts from "typescript";

const source = fs.readFileSync(new URL("../worker/gateway.ts", import.meta.url), "utf8");
const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const { proxyAnalysis } = await import(`data:text/javascript;base64,${Buffer.from(js).toString("base64")}`);
const env = { RALLYMATE_API_ORIGIN: "http://127.0.0.1:8001", RALLYMATE_API_KEY: "internal-test-key" };
const path = "/v1/jobs/cancelled-read-123/pose-preview?start_ms=80000&duration_ms=10000";
const request = signal => new Request(`http://localhost${path}`, { signal });

test("GET already cancelled before dispatch reaches fetch with the same abort reason", async t => {
  const caller = new AbortController();
  const reason = new DOMException("Playback window changed", "AbortError");
  caller.abort(reason);
  let calls = 0;
  t.mock.method(globalThis, "fetch", async (_url, init) => {
    calls++;
    assert.equal(init.signal.aborted, true);
    assert.equal(init.signal.reason, reason);
    init.signal.throwIfAborted();
  });
  const response = await proxyAnalysis(request(caller.signal), env);
  assert.equal(response.status, 502);
  assert.deepEqual(await response.json(), { error: "analysis_service_unavailable" });
  assert.equal(calls, 1);
});

test("GET caller cancellation aborts an upstream request still waiting for headers without retry", async t => {
  const caller = new AbortController();
  const reason = new DOMException("Playback window changed", "AbortError");
  let upstreamSignal, calls = 0;
  t.mock.method(globalThis, "fetch", (_url, init) => {
    calls++;
    upstreamSignal = init.signal;
    return new Promise((_resolve, reject) => init.signal.addEventListener("abort", () => reject(init.signal.reason), { once: true }));
  });
  const pending = proxyAnalysis(request(caller.signal), env);
  assert.equal(upstreamSignal.aborted, false);
  caller.abort(reason);
  assert.equal(upstreamSignal.aborted, true);
  assert.equal(upstreamSignal.reason, reason);
  assert.equal((await pending).status, 502);
  assert.equal(calls, 1);
});

for (const trigger of ["caller", "deadline"]) {
  test(`GET ${trigger} cancellation remains connected after response headers and a first body chunk`, async t => {
    const caller = new AbortController(), deadline = new AbortController();
    const reason = new DOMException("Read stopped", trigger === "caller" ? "AbortError" : "TimeoutError");
    t.mock.method(AbortSignal, "timeout", ms => { assert.equal(ms, 30000); return deadline.signal; });
    let upstreamSignal, calls = 0;
    t.mock.method(globalThis, "fetch", async (_url, init) => {
      calls++;
      upstreamSignal = init.signal;
      // Model fetch's live connection between its signal and its response body.
      // The gateway must return the stream without detaching that connection.
      return new Response(new ReadableStream({
        start(controller) {
          controller.enqueue(new TextEncoder().encode('{"frames":['));
          init.signal.addEventListener("abort", () => controller.error(init.signal.reason), { once: true });
        },
      }), { status: 200, headers: { "content-type": "application/json" } });
    });
    const response = await proxyAnalysis(request(caller.signal), env);
    assert.equal(response.status, 200);
    const reader = response.body.getReader();
    assert.equal(new TextDecoder().decode((await reader.read()).value), '{"frames":[');
    const waitingForBody = reader.read();
    const rejected = assert.rejects(waitingForBody, error => error === reason);
    (trigger === "caller" ? caller : deadline).abort(reason);
    await rejected;
    assert.equal(upstreamSignal.aborted, true);
    assert.equal(upstreamSignal.reason, reason);
    assert.equal(calls, 1);
    reader.releaseLock();
  });
}

test("normal GET preserves its 30-second deadline, query, server authentication and streamed response", async t => {
  const deadline = new AbortController();
  t.mock.method(AbortSignal, "timeout", ms => { assert.equal(ms, 30000); return deadline.signal; });
  let calls = 0;
  t.mock.method(globalThis, "fetch", async (url, init) => {
    calls++;
    assert.equal(String(url), `http://127.0.0.1:8001${path}`);
    assert.equal(init.method, "GET");
    assert.equal(init.redirect, "manual");
    assert.equal(init.signal.aborted, false);
    assert.equal(init.headers.get("authorization"), "Bearer internal-test-key");
    assert.equal(init.headers.get("x-request-id"), "bounded-read-test");
    return Response.json({ frames: [0, 1] });
  });
  const response = await proxyAnalysis(new Request(`http://localhost${path}`, {
    headers: { authorization: "Bearer browser-must-not-forward", "x-request-id": "bounded-read-test" },
  }), env);
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("cache-control"), "private, no-store");
  assert.deepEqual(await response.json(), { frames: [0, 1] });
  assert.equal(calls, 1);
});

test("non-chunk POST retains its existing independent 100-second deadline", async t => {
  const caller = new AbortController(), deadline = new AbortController();
  t.mock.method(AbortSignal, "timeout", ms => { assert.equal(ms, 100000); return deadline.signal; });
  let calls = 0;
  t.mock.method(globalThis, "fetch", async (_url, init) => {
    calls++;
    caller.abort();
    assert.equal(init.method, "POST");
    assert.equal(init.signal, deadline.signal);
    assert.equal(init.signal.aborted, false);
    return Response.json({ accepted: true });
  });
  const response = await proxyAnalysis(new Request("http://localhost/v1/uploads", {
    method: "POST", body: "{}", signal: caller.signal,
    headers: { origin: "http://localhost", "content-type": "application/json" },
  }), env);
  assert.equal(response.status, 200);
  assert.equal(calls, 1);
});


test("gateway only returns an upstream request ID matching a generated-format incoming ID", async t => {
  const valid = "f67b39de-cf4d-4298-b175-fbc5a0c094f1";
  for (const [incoming, returned, expected] of [[valid, valid, valid], ["web-mgj31234-ab123456", "web-mgj31234-ab123456", "web-mgj31234-ab123456"], [valid, "UPSTREAM_SECRET", null], [valid, "a67b39de-cf4d-4298-b175-fbc5a0c094f1", null], ["unsafe_token", "unsafe_token", null]]) {
    const mock = t.mock.method(globalThis, "fetch", async () => Response.json({ ok: true }, { headers: { "X-Request-ID": returned } }));
    const response = await proxyAnalysis(new Request(`http://localhost${path}`, { headers: { "X-Request-ID": incoming } }), env);
    assert.equal(response.headers.get("x-request-id"), expected);
    assert.equal(response.status, 200);
    mock.mock.restore();
  }
});
