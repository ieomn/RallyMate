import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import ts from "typescript";

const source = fs.readFileSync(new URL("../worker/gateway.ts", import.meta.url), "utf8");
const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const { proxyAnalysis } = await import(`data:text/javascript;base64,${Buffer.from(js).toString("base64")}`);
const env = { RALLYMATE_API_ORIGIN: "http://127.0.0.1:8001", RALLYMATE_API_KEY: "test-internal" };
const url = "http://localhost/v1/jobs/report-stream-123/demo-result?report_contract=current&report_transport=model-proof-pool-v1&report_delivery=deferred-v1";
const bytes = text => new TextEncoder().encode(text);
const flush = async () => { for (let i = 0; i < 8; i++) await Promise.resolve(); };

function clock(t) {
  let now = 0, next = 0;
  const pending = new Map();
  t.mock.method(globalThis, "setTimeout", (fn, ms) => {
    const id = ++next; pending.set(id, { fn, at: now + ms }); return id;
  });
  t.mock.method(globalThis, "clearTimeout", id => pending.delete(id));
  return {
    advance(ms) {
      const target = now + ms;
      while (true) {
        const due = [...pending].filter(([, item]) => item.at <= target).sort((a, b) => a[1].at - b[1].at)[0];
        if (!due) break;
        now = due[1].at; pending.delete(due[0]); due[1].fn();
      }
      now = target;
    },
    count: () => pending.size,
    deadlines: () => [...pending.values()].map(item => item.at - now).sort((a, b) => a - b),
  };
}

function streamingOrigin(t, first = "{") {
  let controller, signal, cancelReason;
  t.mock.method(globalThis, "fetch", async (_url, init) => {
    signal = init.signal;
    assert.equal(init.headers.get("authorization"), "Bearer test-internal");
    return new Response(new ReadableStream({
      start(value) {
        controller = value;
        if (first !== null) controller.enqueue(bytes(first));
        signal.addEventListener("abort", () => controller.error(signal.reason), { once: true });
      },
      cancel(reason) { cancelReason = reason; },
    }), { headers: { "Content-Type": "application/json", "X-Private": "must-not-forward" } });
  });
  return { push: text => controller.enqueue(bytes(text)), end: () => controller.close(),
    signal: () => signal, cancelReason: () => cancelReason };
}

test("report keeps a 30s response-header deadline and releases all timers on failure", async t => {
  const time = clock(t);
  let signal;
  t.mock.method(globalThis, "fetch", (_url, init) => {
    signal = init.signal;
    return new Promise((_resolve, reject) => signal.addEventListener("abort", () => reject(signal.reason), { once: true }));
  });
  const pending = proxyAnalysis(new Request(url), env);
  assert.deepEqual(time.deadlines(), [30000, 120000]);
  time.advance(30000);
  assert.equal((await pending).status, 502);
  assert.equal(signal.reason.name, "TimeoutError");
  assert.equal(time.count(), 0);
});

test("report body can make progress beyond 30s and preserves every byte", async t => {
  const time = clock(t), origin = streamingOrigin(t, '{"value":');
  const response = await proxyAnalysis(new Request(url), env);
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("cache-control"), "private, no-store");
  assert.equal(response.headers.get("x-private"), null);
  const reader = response.body.getReader(), parts = [];
  parts.push((await reader.read()).value);
  for (const part of ["0", ',"evidence":[', "1.23456789", "]}"]) {
    const reading = reader.read(); await flush();
    time.advance(15000);
    origin.push(part); parts.push((await reading).value);
  }
  origin.end(); assert.equal((await reader.read()).done, true);
  assert.equal(Buffer.concat(parts).toString(), '{"value":0,"evidence":[1.23456789]}');
  assert.equal(origin.signal().aborted, false);
  assert.equal(time.count(), 0);
});

test("downstream backpressure is not charged as upstream no-progress time", async t => {
  const time = clock(t), origin = streamingOrigin(t, "prefix");
  const response = await proxyAnalysis(new Request(url), env);
  await flush(); // one bounded prefetched chunk, then no downstream demand
  assert.deepEqual(time.deadlines(), [120000]);
  time.advance(45000);
  assert.equal(origin.signal().aborted, false);
  origin.push("suffix"); origin.end();
  assert.equal(await response.text(), "prefixsuffix");
  assert.equal(time.count(), 0);
});

test("30s stalled outstanding upstream read rejects the report body", async t => {
  const time = clock(t), origin = streamingOrigin(t, "partial");
  const response = await proxyAnalysis(new Request(url), env);
  const reader = response.body.getReader(); await reader.read();
  const reading = reader.read(); await flush();
  const rejection = assert.rejects(reading, error => error.name === "TimeoutError");
  time.advance(29999); assert.equal(origin.signal().aborted, false);
  time.advance(1); await rejection;
  assert.equal(origin.signal().aborted, true);
  assert.equal(time.count(), 0);
});

test("120s total limit still applies when downstream has stopped consuming", async t => {
  const time = clock(t), origin = streamingOrigin(t, "queued");
  const response = await proxyAnalysis(new Request(url), env);
  await flush();
  time.advance(119999); assert.equal(origin.signal().aborted, false);
  time.advance(1);
  await assert.rejects(response.text(), error => error.name === "TimeoutError");
  assert.equal(time.count(), 0);
});

test("report caller abort remains connected after headers and body read", async t => {
  const time = clock(t), origin = streamingOrigin(t, "first"), caller = new AbortController();
  const response = await proxyAnalysis(new Request(url, { signal: caller.signal }), env);
  const reader = response.body.getReader(); await reader.read();
  const reading = reader.read(); await flush();
  const reason = new DOMException("User changed report", "AbortError");
  const rejection = assert.rejects(reading, error => error === reason);
  caller.abort(reason); await rejection;
  assert.equal(origin.signal().reason, reason);
  assert.equal(time.count(), 0);
});

test("downstream cancellation aborts the upstream and removes report timers", async t => {
  const time = clock(t), origin = streamingOrigin(t, "first");
  const response = await proxyAnalysis(new Request(url), env);
  await response.body.cancel("viewer left");
  assert.equal(origin.signal().aborted, true);
  assert.equal(time.count(), 0);
});

test("empty report and upstream redirect do not leak timers", async t => {
  const time = clock(t);
  for (const status of [204, 302]) {
    const fetchMock = t.mock.method(globalThis, "fetch", async () => new Response(null, { status }));
    const response = await proxyAnalysis(new Request(url), env);
    assert.equal(response.status, status === 302 ? 502 : 204);
    assert.equal(time.count(), 0);
    fetchMock.mock.restore();
  }
});

test("exact identity Content-Length closes before a late upstream socket error", async t => {
  const time = clock(t), payload = bytes('{"score":0,"note":"完整"}');
  let reads = 0, cancelled = false;
  t.mock.method(globalThis, "fetch", async (_url, init) => {
    assert.equal(init.headers.get("accept-encoding"), "identity");
    return new Response(new ReadableStream({
      pull(controller) {
        reads++;
        if (reads === 1) controller.enqueue(payload);
        else throw new TypeError("private socket detail", { cause: { code: "UND_ERR_SOCKET" } });
      },
      cancel() { cancelled = true; },
    }, { highWaterMark: 0 }), { headers: { "Content-Type": "application/json", "Content-Length": String(payload.byteLength) } });
  });
  const response = await proxyAnalysis(new Request(url), env);
  assert.equal(response.headers.get("content-length"), String(payload.byteLength));
  assert.deepEqual(new Uint8Array(await response.arrayBuffer()), payload);
  assert.equal(reads, 1);
  assert.equal(cancelled, true);
  assert.equal(time.count(), 0);
});

test("exact length spans byte chunks and does not wait for a pending cancel promise", async t => {
  const time = clock(t), chunks = [bytes('{"n":'), bytes('0,"note":"é"}')];
  const expected = Buffer.concat(chunks);
  let index = 0;
  t.mock.method(globalThis, "fetch", async () => new Response(new ReadableStream({
    pull(controller) { controller.enqueue(chunks[index++]); },
    cancel() { return new Promise(() => {}); },
  }, { highWaterMark: 0 }), { headers: { "Content-Length": String(expected.byteLength), "Content-Encoding": "identity" } }));
  const response = await proxyAnalysis(new Request(url), env);
  assert.equal(await response.text(), expected.toString());
  assert.equal(index, 2);
  assert.equal(time.count(), 0);
});

for (const [name, declared, actual] of [["short", 8, "short"], ["overrun", 3, "longer"]]) {
  test(`report rejects ${name} length and logs only bounded safe metadata once`, async t => {
    const time = clock(t), rows = [];
    t.mock.method(console, "warn", line => rows.push(JSON.parse(line)));
    t.mock.method(globalThis, "fetch", async () => new Response(actual, { headers: { "Content-Length": String(declared) } }));
    const response = await proxyAnalysis(new Request(url, { headers: { "x-request-id": "web-unit123-abc123", "authorization": "secret-not-to-log" } }), env);
    await assert.rejects(response.text(), error => error.name === "ReportBodyLengthError");
    assert.equal(rows.length, 1);
    assert.deepEqual(Object.keys(rows[0]).sort(), ["event", "request_id", "elapsed_ms", "bytes", "expected_bytes", "error_category"].sort());
    assert.equal(rows[0].error_category, "length_mismatch");
    assert.equal(rows[0].expected_bytes, declared);
    assert.equal(rows[0].bytes, Buffer.byteLength(actual));
    assert.equal(rows[0].request_id, "web-unit123-abc123");
    assert.equal(JSON.stringify(rows).includes("secret"), false);
    assert.equal(time.count(), 0);
  });
}

for (const [name, headers] of [
  ["encoded", { "Content-Length": "1", "Content-Encoding": "gzip" }],
  ["empty-encoding", { "Content-Length": "1", "Content-Encoding": "" }],
  ["chunked", { "Content-Length": "1", "Transfer-Encoding": "chunked" }],
  ["identity-with-transfer-encoding", { "Content-Length": "1", "Content-Encoding": "identity", "Transfer-Encoding": "chunked" }],
  ["unknown", {}],
  ["malformed", { "Content-Length": "invalid" }],
  ["unsafe-integer", { "Content-Length": "9007199254740992" }],
]) {
  test(`${name} framing reads to upstream done without using a decoded byte length`, async t => {
    const time = clock(t);
    let reads = 0, cancelled = false;
    t.mock.method(globalThis, "fetch", async () => new Response(new ReadableStream({
      pull(controller) {
        reads++;
        if (reads === 1) controller.enqueue(bytes("decoded-report"));
        else controller.close();
      }, cancel() { cancelled = true; },
    }, { highWaterMark: 0 }), { headers }));
    const response = await proxyAnalysis(new Request(url, { headers: { "Accept-Encoding": "identity" } }), env);
    assert.equal(response.headers.get("content-length"), null);
    assert.equal(response.headers.get("content-encoding"), null);
    assert.equal(response.headers.get("transfer-encoding"), null);
    assert.equal(await response.text(), "decoded-report");
    assert.equal(reads, 2);
    assert.equal(cancelled, false);
    assert.equal(time.count(), 0);
  });
}

test("unknown-length socket error remains a failure and logs no arbitrary error text", async t => {
  const time = clock(t), rows = [];
  t.mock.method(console, "warn", line => rows.push(JSON.parse(line)));
  t.mock.method(globalThis, "fetch", async () => new Response(new ReadableStream({
    pull() { throw new TypeError("private credentials or URL", { cause: { code: "UND_ERR_SOCKET" } }); },
  }, { highWaterMark: 0 })));
  const response = await proxyAnalysis(new Request(url, { headers: { "x-request-id": "untrusted-token" } }), env);
  await assert.rejects(response.text(), TypeError);
  assert.equal(rows.length, 1);
  assert.equal(rows[0].error_category, "upstream_socket");
  assert.equal(rows[0].expected_bytes, null);
  assert.equal(rows[0].request_id, null);
  assert.equal(JSON.stringify(rows).includes("private"), false);
  assert.equal(time.count(), 0);
});

test("zero length closes without reading body or leaving request timers", async t => {
  const time = clock(t);
  let read = false;
  t.mock.method(globalThis, "fetch", async () => new Response(new ReadableStream({
    pull() { read = true; throw new Error("must not read past HTTP body boundary"); },
  }, { highWaterMark: 0 }), { headers: { "Content-Length": "0" } }));
  const response = await proxyAnalysis(new Request(url), env);
  assert.equal(await response.text(), "");
  assert.equal(read, false);
  assert.equal(time.count(), 0);
});
