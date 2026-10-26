import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { runInNewContext } from "node:vm";
import ts from "typescript";

const source = fs.readFileSync(new URL("../worker/gateway.ts", import.meta.url), "utf8");
const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const { proxyAnalysis } = await import(`data:text/javascript;base64,${Buffer.from(js).toString("base64")}`);
const env = { RALLYMATE_API_ORIGIN: "http://127.0.0.1:8001", RALLYMATE_API_KEY: "private-upstream-key", RALLYMATE_POSE_REQUEST_DIAGNOSTICS: "1" };
const path = "/v1/jobs/private-job-123/pose-preview?start_ms=0&duration_ms=10000&sample_limit=600&secret=private-query";
const uuid = "d1eb9434-8e3a-49d3-9870-e1c1fc4d1d90";
const webId = "web-mgj31234-ab123456";
const request = (id = uuid, signal) => new Request(`http://localhost${path}`, {
  signal, headers: { "x-request-id": id, authorization: "Bearer private-browser-key", cookie: "private-cookie", "x-private-header": "private-header" },
});
const phases = rows => rows.map(row => row.phase);
function isolatedGateway(processValue, includeEntry = false) {
  const rows = [], forwarded = [];
  const context = { Request, Response, Headers, URL, AbortSignal, performance,
    console: { info: value => rows.push(JSON.parse(value)) },
    fetch: async (url, init) => { forwarded.push({ url, init }); return new Response("unchanged"); },
  };
  if (processValue !== undefined) context.process = processValue;
  let code = js.replace(/^export /gm, "");
  if (includeEntry) {
    const entry = ts.transpileModule(fs.readFileSync(new URL("../worker/index.ts", import.meta.url), "utf8"), {
      compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
    }).outputText;
    // Execute the real entry's env binding and analysis dispatch, with no vinext imports.
    const stripped = entry.replace(/^import .+ from "(?:vinext\/server\/(?:image-optimization|app-router-entry)|\.\/gateway)";\r?$/gm, "")
      .replace("export default worker;", "globalThis.testWorker = worker;");
    assert.doesNotMatch(stripped, /^import /m);
    code += `\n${stripped}`;
  }
  runInNewContext(code, context);
  return Object.assign(context, { rows, forwarded });
}
function capture(t) {
  const rows = [];
  t.mock.method(console, "info", value => { assert.equal(typeof value, "string"); rows.push(JSON.parse(value)); });
  return rows;
}
function safeRows(rows, id = uuid) {
  const keys = ["event", "layer", "route", "phase", "request_id", "utc", "sequence", "elapsed_ms", "since_previous_phase_ms", "http_status", "caller_aborted", "upstream_aborted", "deadline_expired", "error_category"].sort();
  let previous = 0;
  for (const [index, row] of rows.entries()) {
    assert.deepEqual(Object.keys(row).sort(), keys);
    assert.equal(row.event, "pose_request_diagnostic");
    assert.equal(row.layer, "gateway");
    assert.equal(row.route, "pose_preview");
    assert.equal(row.request_id, id);
    assert.match(row.utc, /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/);
    assert.ok(Number.isFinite(Date.parse(row.utc)));
    assert.equal(row.sequence, index + 1);
    assert.ok(row.sequence <= 8);
    assert.ok(Number.isFinite(row.elapsed_ms) && row.elapsed_ms >= previous);
    assert.ok(Number.isFinite(row.since_previous_phase_ms) && row.since_previous_phase_ms >= 0);
    assert.ok(Math.abs(row.since_previous_phase_ms - (row.elapsed_ms - previous)) <= 0.002);
    previous = row.elapsed_ms;
    for (const key of ["caller_aborted", "upstream_aborted", "deadline_expired"]) assert.equal(typeof row[key], "boolean");
    assert.ok(row.http_status === null || Number.isInteger(row.http_status) && row.http_status >= 100 && row.http_status <= 599);
  }
  assert.doesNotMatch(JSON.stringify(rows), /private-|Bearer|cookie|authorization|start_ms|duration_ms|sample_limit|http:\/\/|exception|stack|browser_received|body_complete/i);
}

test("pose diagnostics are off unless the flag is exactly the string 1", async t => {
  const previous = process.env.RALLYMATE_POSE_REQUEST_DIAGNOSTICS;
  delete process.env.RALLYMATE_POSE_REQUEST_DIAGNOSTICS;
  t.after(() => { if (previous === undefined) delete process.env.RALLYMATE_POSE_REQUEST_DIAGNOSTICS; else process.env.RALLYMATE_POSE_REQUEST_DIAGNOSTICS = previous; });
  const rows = capture(t);
  let calls = 0;
  t.mock.method(globalThis, "fetch", async () => { calls++; return new Response("unchanged"); });
  for (const flag of [undefined, "0", "false", "true", true, 1, " 1"]) {
    const response = await proxyAnalysis(request(), { ...env, RALLYMATE_POSE_REQUEST_DIAGNOSTICS: flag });
    assert.equal(response.status, 200);
    assert.equal(await response.text(), "unchanged");
  }
  assert.equal(calls, 7);
  assert.deepEqual(rows, []);
});

test("real Node entry can enable the flag absent from its allowlist, while explicit bindings override", async () => {
  const nodeEnv = { RALLYMATE_API_ORIGIN: env.RALLYMATE_API_ORIGIN, RALLYMATE_API_KEY: "private-node-key", RALLYMATE_POSE_REQUEST_DIAGNOSTICS: "1" };
  const runtime = isolatedGateway({ env: nodeEnv }, true);
  const response = await runtime.testWorker.fetch(request(), {}, {});
  assert.equal(response.status, 200);
  assert.equal(await response.text(), "unchanged");
  assert.equal(runtime.forwarded[0].init.headers.get("authorization"), "Bearer private-node-key");
  assert.equal(runtime.__env.RALLYMATE_POSE_REQUEST_DIAGNOSTICS, undefined, "entry still has its original fixed allowlist");
  assert.deepEqual(phases(runtime.rows), ["gateway_ingress", "upstream_start", "upstream_headers", "gateway_return"]);
  safeRows(runtime.rows);
  for (const flag of ["0", "false", null, false]) {
    runtime.rows.length = 0;
    assert.equal((await runtime.testWorker.fetch(request(), { RALLYMATE_POSE_REQUEST_DIAGNOSTICS: flag }, {})).status, 200);
    assert.deepEqual(runtime.rows, []);
  }
  nodeEnv.RALLYMATE_POSE_REQUEST_DIAGNOSTICS = "0";
  assert.equal((await runtime.testWorker.fetch(request(), { RALLYMATE_POSE_REQUEST_DIAGNOSTICS: "1" }, {})).status, 200);
  assert.equal(runtime.rows.length, 4);
});

test("Worker without process and failed Node flag reads default off; fallback reads only this setting", async () => {
  const noFlag = { RALLYMATE_API_ORIGIN: env.RALLYMATE_API_ORIGIN };
  const worker = isolatedGateway();
  assert.equal((await worker.proxyAnalysis(request(), noFlag)).status, 200);
  assert.deepEqual(worker.rows, []);
  assert.equal((await worker.proxyAnalysis(request(), env)).status, 200);
  assert.equal(worker.rows.length, 4);
  const broken = isolatedGateway({ get env() { throw new Error("private-env-fault"); } });
  assert.equal((await broken.proxyAnalysis(request(), noFlag)).status, 200);
  assert.deepEqual(broken.rows, []);
  const reads = [];
  const single = isolatedGateway({ env: new Proxy({}, { get(_target, key) { reads.push(key); return "1"; }, ownKeys() { assert.fail("environment must not be enumerated"); } }) });
  assert.equal((await single.proxyAnalysis(request(), noFlag)).status, 200);
  assert.deepEqual(reads, ["RALLYMATE_POSE_REQUEST_DIAGNOSTICS"]);
  assert.equal(single.rows.length, 4);
});

test("enabled logging only covers allowed pose GETs, leaving route and origin permissions intact", async t => {
  const rows = capture(t);
  let calls = 0;
  t.mock.method(globalThis, "fetch", async () => { calls++; return new Response("ok"); });
  for (const suffix of ["demo-result", "trajectory", "technical-review", "artifacts/frames.jsonl"]) {
    assert.equal((await proxyAnalysis(new Request(`http://localhost/v1/jobs/private-job-123/${suffix}`), env)).status, 200);
  }
  assert.equal((await proxyAnalysis(new Request("http://localhost/v1/meta"), env)).status, 200);
  assert.equal((await proxyAnalysis(new Request(`http://localhost${path}`, { method: "POST" }), env)).status, 404);
  assert.equal((await proxyAnalysis(new Request("http://localhost/v1/jobs/private-job-123/pose-preview/extra"), env)).status, 404);
  assert.equal((await proxyAnalysis(new Request("http://localhost/v1/jobs", { method: "POST", headers: { origin: "https://cross-site.example" } }), env)).status, 403);
  assert.equal(calls, 5);
  assert.deepEqual(rows, []);
});

test("success logs safe phases and returns exactly the same unconsumed body and existing headers", async t => {
  const rows = capture(t), deadline = new AbortController();
  let pulls = 0, calls = 0;
  const body = new ReadableStream({ pull(controller) { pulls++; controller.enqueue(new TextEncoder().encode("private-body")); controller.close(); } }, { highWaterMark: 0 });
  t.mock.method(AbortSignal, "timeout", ms => { assert.equal(ms, 30000); return deadline.signal; });
  t.mock.method(globalThis, "fetch", async (url, init) => {
    calls++;
    assert.equal(String(url), `http://127.0.0.1:8001${path}`);
    assert.equal(init.method, "GET");
    assert.equal(init.redirect, "manual");
    assert.equal(init.headers.get("authorization"), "Bearer private-upstream-key");
    assert.equal(init.headers.get("cookie"), null);
    assert.equal(init.headers.get("x-private-header"), null);
    assert.equal(init.headers.get("x-request-id"), uuid);
    assert.equal(init.signal.aborted, false);
    return new Response(body, { headers: { "content-type": "application/json", "x-request-id": uuid, "x-private-upstream": "private-response-header" } });
  });
  const response = await proxyAnalysis(request(), env);
  assert.equal(response.status, 200);
  assert.equal(response.body, body);
  assert.equal(response.bodyUsed, false);
  assert.equal(body.locked, false);
  assert.equal(pulls, 0);
  assert.equal(response.headers.get("x-request-id"), uuid);
  assert.equal(response.headers.get("x-private-upstream"), null);
  assert.equal(response.headers.get("cache-control"), "private, no-store");
  assert.equal(response.headers.get("x-content-type-options"), "nosniff");
  assert.deepEqual(phases(rows), ["gateway_ingress", "upstream_start", "upstream_headers", "gateway_return"]);
  assert.deepEqual(rows.map(row => row.http_status), [null, null, 200, 200]);
  assert.ok(rows.every(row => row.error_category === "none"));
  safeRows(rows);
  assert.equal(await response.text(), "private-body");
  deadline.abort(new Error("private-late-timeout"));
  assert.equal(rows.length, 4, "return ends diagnostics, including after the body finishes");
  assert.equal(calls, 1);
});

test("HTTP failures preserve status and body while redirects keep the existing gateway rejection", async t => {
  const rows = capture(t);
  for (const status of [403, 500, 302]) {
    rows.length = 0;
    const body = new ReadableStream({ start(controller) { controller.enqueue(new TextEncoder().encode("private-error-body")); controller.close(); } });
    const mock = t.mock.method(globalThis, "fetch", async () => new Response(body, { status }));
    const response = await proxyAnalysis(request(), env);
    assert.equal(response.status, status === 302 ? 502 : status);
    if (status === 302) assert.deepEqual(await response.json(), { error: "unexpected_upstream_redirect" });
    else { assert.equal(response.body, body); assert.equal(await response.text(), "private-error-body"); }
    assert.deepEqual(phases(rows), ["gateway_ingress", "upstream_start", "upstream_headers", "gateway_return"]);
    assert.equal(rows[2].http_status, status);
    assert.equal(rows[3].error_category, status === 302 ? "upstream_redirect" : "http_error");
    safeRows(rows);
    mock.mock.restore();
  }
});

test("invalid IDs are omitted from logs but their forwarding semantics do not change", async t => {
  const rows = capture(t);
  for (const id of ["private-token", "a".repeat(65), "private_token", uuid.toUpperCase(), webId]) {
    rows.length = 0;
    const mock = t.mock.method(globalThis, "fetch", async (_url, init) => {
      assert.equal(init.headers.get("x-request-id"), id);
      return new Response("ok", { headers: { "x-request-id": id } });
    });
    const response = await proxyAnalysis(request(id), env);
    const expected = [uuid.toUpperCase(), webId].includes(id) ? id : null;
    assert.equal(response.headers.get("x-request-id"), expected);
    safeRows(rows, expected);
    mock.mock.restore();
  }
});

for (const trigger of ["already_cancelled", "caller", "deadline", "both"]) {
  test(`${trigger} before headers is safely classified, propagates cancellation and never retries`, async t => {
    const rows = capture(t), caller = new AbortController(), deadline = new AbortController();
    const reason = new Error("private-reason-message-and-stack");
    let calls = 0, upstreamSignal;
    t.mock.method(AbortSignal, "timeout", ms => { assert.equal(ms, 30000); return deadline.signal; });
    if (trigger === "already_cancelled") caller.abort(reason);
    t.mock.method(globalThis, "fetch", (_url, init) => {
      calls++; upstreamSignal = init.signal;
      if (init.signal.aborted) return Promise.reject(init.signal.reason);
      return new Promise((_resolve, reject) => init.signal.addEventListener("abort", () => reject(init.signal.reason), { once: true }));
    });
    const pending = proxyAnalysis(request(uuid, caller.signal), env);
    if (trigger === "caller" || trigger === "both") caller.abort(reason);
    if (trigger === "deadline" || trigger === "both") deadline.abort(reason);
    const response = await pending;
    assert.equal(response.status, 502);
    assert.deepEqual(await response.json(), { error: "analysis_service_unavailable" });
    assert.equal(upstreamSignal.aborted, true);
    assert.equal(upstreamSignal.reason, reason);
    assert.equal(calls, 1);
    const expectedPhase = trigger === "deadline" ? "upstream_timeout" : trigger === "both" ? "upstream_aborted" : "caller_abort";
    const expectedError = trigger === "deadline" ? "timeout" : trigger === "both" ? "abort_source_ambiguous" : "caller_abort";
    assert.deepEqual(phases(rows), ["gateway_ingress", "upstream_start", expectedPhase, "gateway_return"]);
    assert.equal(rows[2].http_status, null);
    assert.equal(rows[2].error_category, expectedError);
    assert.equal(rows[3].http_status, 502);
    assert.equal(rows[3].caller_aborted, trigger !== "deadline");
    assert.equal(rows[3].deadline_expired, trigger === "deadline" || trigger === "both");
    safeRows(rows);
  });
}

test("fetch failure exposes a fixed category without exception text or reasons", async t => {
  const rows = capture(t);
  let calls = 0;
  t.mock.method(globalThis, "fetch", async () => { calls++; throw new Error("private-network-exception"); });
  const response = await proxyAnalysis(request(), env);
  assert.equal(response.status, 502);
  assert.deepEqual(await response.json(), { error: "analysis_service_unavailable" });
  assert.deepEqual(phases(rows), ["gateway_ingress", "upstream_start", "upstream_error", "gateway_return"]);
  assert.equal(rows[2].error_category, "upstream_fetch_failed");
  assert.equal(rows[2].caller_aborted, false);
  assert.equal(rows[2].deadline_expired, false);
  assert.equal(calls, 1);
  safeRows(rows);
});

test("cancellation after gateway return still reaches the same body without any later diagnostic", async t => {
  const rows = capture(t), caller = new AbortController(), deadline = new AbortController();
  const reason = new DOMException("private-stream-cancel", "AbortError");
  let upstreamSignal;
  t.mock.method(AbortSignal, "timeout", ms => { assert.equal(ms, 30000); return deadline.signal; });
  t.mock.method(globalThis, "fetch", async (_url, init) => {
    upstreamSignal = init.signal;
    return new Response(new ReadableStream({
      start(controller) {
        controller.enqueue(new TextEncoder().encode("first"));
        init.signal.addEventListener("abort", () => controller.error(init.signal.reason), { once: true });
      },
    }));
  });
  const response = await proxyAnalysis(request(uuid, caller.signal), env);
  const reader = response.body.getReader();
  assert.equal(new TextDecoder().decode((await reader.read()).value), "first");
  const waiting = assert.rejects(reader.read(), error => error === reason);
  caller.abort(reason);
  await waiting;
  deadline.abort();
  assert.equal(upstreamSignal.reason, reason);
  assert.deepEqual(phases(rows), ["gateway_ingress", "upstream_start", "upstream_headers", "gateway_return"]);
  assert.ok(rows.every(row => !row.caller_aborted && !row.deadline_expired));
  safeRows(rows);
  reader.releaseLock();
});

test("diagnostic configuration, clock, UTC, formatting and logger errors cannot alter forwarding", async t => {
  for (const fault of ["flag", "clock", "utc", "format", "logger"]) {
    const incoming = request(), upstream = new Response("unchanged-body", { status: 202 });
    let calls = 0;
    const fetchMock = t.mock.method(globalThis, "fetch", async () => { calls++; return upstream; });
    const fail = () => { throw new Error("private-diagnostic-fault"); };
    const faultEnv = { ...env };
    let faultMock;
    if (fault === "flag") Object.defineProperty(faultEnv, "RALLYMATE_POSE_REQUEST_DIAGNOSTICS", { get: fail });
    if (fault === "clock") faultMock = t.mock.method(performance, "now", fail);
    if (fault === "utc") faultMock = t.mock.method(Date.prototype, "toISOString", fail);
    if (fault === "format") faultMock = t.mock.method(JSON, "stringify", fail);
    if (fault === "logger") faultMock = t.mock.method(console, "info", fail);
    const response = await proxyAnalysis(incoming, faultEnv);
    faultMock?.mock.restore();
    fetchMock.mock.restore();
    assert.equal(response.status, 202);
    assert.equal(response.body, upstream.body);
    assert.equal(await response.text(), "unchanged-body");
    assert.equal(calls, 1);
  }
});

test("concurrent requests keep separate IDs, counters and timings despite reversed completion", async t => {
  const rows = capture(t), waiting = new Map();
  let clock = 100;
  t.mock.method(performance, "now", () => { clock += 5; return clock; });
  t.mock.method(globalThis, "fetch", (_url, init) => new Promise(resolve => waiting.set(init.headers.get("x-request-id"), resolve)));
  const first = proxyAnalysis(request(uuid), env), second = proxyAnalysis(request(webId), env);
  waiting.get(webId)(new Response("second"));
  assert.equal(await (await second).text(), "second");
  waiting.get(uuid)(new Response("first"));
  assert.equal(await (await first).text(), "first");
  for (const id of [uuid, webId]) {
    const own = rows.filter(row => row.request_id === id);
    assert.deepEqual(phases(own), ["gateway_ingress", "upstream_start", "upstream_headers", "gateway_return"]);
    safeRows(own, id);
    assert.equal(own[0].elapsed_ms, 5);
  }
  assert.equal(rows.at(-1).request_id, uuid);
});

test("gateway configuration failures retain existing status and do not claim an upstream start", async t => {
  const rows = capture(t);
  t.mock.method(globalThis, "fetch", () => assert.fail("configuration rejection must not fetch"));
  for (const origin of [undefined, "https://user:private-password@api.example", "https://api.example/private-path"]) {
    rows.length = 0;
    const response = await proxyAnalysis(request(), { ...env, RALLYMATE_API_ORIGIN: origin });
    assert.equal(response.status, 503);
    assert.deepEqual(await response.json(), { error: origin ? "invalid_analysis_origin" : "analysis_service_not_configured" });
    assert.deepEqual(phases(rows), ["gateway_ingress", "gateway_return"]);
    assert.equal(rows[1].error_category, "gateway_rejected");
    safeRows(rows);
  }
});
