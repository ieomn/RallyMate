import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import ts from "typescript";

const modules = new Map();
function compiledUrl(file) {
  if (modules.has(file.href)) return modules.get(file.href);
  let js = file.pathname.endsWith(".json") ? `export default ${fs.readFileSync(file, "utf8")};`
    : ts.transpileModule(fs.readFileSync(file, "utf8"), { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
  js = js.replace(/from "([^"]+)"/g, (_match, name) => `from ${JSON.stringify(name.startsWith(".")
    ? compiledUrl(new URL(name.endsWith(".json") ? name : `${name}.ts`, file)) : import.meta.resolve(name))}`);
  const url = `data:text/javascript;base64,${Buffer.from(js).toString("base64")}`;
  modules.set(file.href, url); return url;
}
const { createApiClient } = await import(compiledUrl(new URL("../app/lib/api-client.ts", import.meta.url)));
const { proxyAnalysis } = await import(compiledUrl(new URL("../worker/gateway.ts", import.meta.url)));
const jobId = "cold-tennis-report-123";
const env = { RALLYMATE_API_ORIGIN: "http://127.0.0.1:8001", RALLYMATE_API_KEY: "test-server-key" };
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
async function settleUntil(condition) { for (let i = 0; i < 40 && !condition(); i++) await Promise.resolve(); assert.ok(condition(), "request did not reach the expected transport state"); }
function deadlines(t) {
  let now = 0; const timers = [];
  t.mock.method(AbortSignal, "timeout", ms => {
    const controller = new AbortController(); timers.push({ ms, at: now + ms, controller }); return controller.signal;
  });
  return { timers, now: () => now, advance(ms) { now += ms; for (const timer of timers) if (timer.at <= now && !timer.controller.signal.aborted) timer.controller.abort(new DOMException("Bounded request expired", "TimeoutError")); } };
}
const gatewayFetch = (url, init) => proxyAnalysis(new Request(new URL(url, "http://localhost"), init), env);

test("a 46-second cold tennis read survives both browser and gateway without changing its score", async t => {
  const clock = deadlines(t), cold = deferred(); let upstream, calls = 0;
  t.mock.method(globalThis, "fetch", (_url, init) => {
    calls++; upstream = init;
    assert.equal(init.headers.get("authorization"), "Bearer test-server-key");
    return cold.promise;
  });
  const client = createApiClient({}, gatewayFetch);
  const result = client.getTennisAssessment(jobId);
  await settleUntil(() => Boolean(upstream));
  assert.deepEqual(clock.timers.map(timer => timer.ms), [125000, 120000]);
  clock.advance(46000); assert.equal(upstream.signal.aborted, false);
  const payload = { job_id: jobId, status: "scored", overall_score: 64.25, episodes: [{ episode_id: "actual-1", score: 64.25 }] };
  cold.resolve(Response.json(payload));
  assert.deepEqual(await result, payload); assert.equal(calls, 1);
});

for (const phase of ["headers", "body"]) test(`caller cancellation remains connected through the tennis ${phase} read`, async t => {
  deadlines(t); const caller = new AbortController(); let upstream, calls = 0;
  t.mock.method(globalThis, "fetch", (_url, init) => {
    calls++; upstream = init;
    if (phase === "headers") return new Promise((_resolve, reject) => init.signal.addEventListener("abort", () => reject(init.signal.reason), { once: true }));
    return Promise.resolve(new Response(new ReadableStream({ start(controller) {
      controller.enqueue(new TextEncoder().encode('{"status":'));
      init.signal.addEventListener("abort", () => controller.error(init.signal.reason), { once: true });
    } }), { headers: { "content-type": "application/json" } }));
  });
  const rows = [], client = createApiClient({}, gatewayFetch, undefined, row => rows.push(row));
  const result = client.getTennisAssessment(jobId, caller.signal);
  await settleUntil(() => phase === "headers" ? Boolean(upstream) : rows.some(row => row.stage === "headers"));
  const reason = new DOMException("Changed source video", "AbortError");
  const rejected = assert.rejects(result, error => error === reason);
  caller.abort(reason); await rejected;
  assert.equal(upstream.signal.reason, reason); assert.equal(calls, 1, "caller cancellation must not retry the old video");
});

test("gateway tennis reads still terminate at their explicit 120-second deadline", async t => {
  const clock = deadlines(t); let upstream;
  t.mock.method(globalThis, "fetch", (_url, init) => { upstream = init; return new Promise((_resolve, reject) => init.signal.addEventListener("abort", () => reject(init.signal.reason), { once: true })); });
  const result = proxyAnalysis(new Request(`http://localhost/v1/jobs/${jobId}/tennis-assessment`), env);
  clock.advance(119999); assert.equal(upstream.signal.aborted, false);
  clock.advance(1); assert.equal(upstream.signal.aborted, true);
  const response = await result; assert.equal(response.status, 502);
  assert.deepEqual(await response.json(), { error: "analysis_service_unavailable" });
});

test("browser tennis timeout retries once and remains bounded when the gateway is bypassed", async t => {
  const clock = deadlines(t); let calls = 0;
  const client = createApiClient({}, (_url, init) => {
    calls++; return new Promise((_resolve, reject) => init.signal.addEventListener("abort", () => reject(init.signal.reason), { once: true }));
  }, { now: clock.now, wait: async (ms, signal) => { signal?.throwIfAborted(); clock.advance(ms); } });
  const result = client.getTennisAssessment(jobId);
  const rejected = assert.rejects(result, error => error.name === "TimeoutError");
  await settleUntil(() => calls === 1); clock.advance(124999); assert.equal(clock.timers[0].controller.signal.aborted, false);
  clock.advance(1); await settleUntil(() => calls === 2);
  clock.advance(125000); await rejected;
  assert.equal(calls, 2); assert.deepEqual(clock.timers.map(timer => timer.ms), [125000, 125000]); assert.equal(clock.now(), 252000);
});
