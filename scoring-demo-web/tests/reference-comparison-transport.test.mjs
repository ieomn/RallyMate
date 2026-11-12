import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import ts from "typescript";

const modules = new Map();
function compiledUrl(file) {
  if (modules.has(file.href)) return modules.get(file.href);
  let js = ts.transpileModule(fs.readFileSync(file, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  js = js.replace(/from "([^"]+)"/g, (_, name) => `from ${JSON.stringify(name.startsWith(".")
    ? compiledUrl(new URL(`${name}.ts`, file)) : import.meta.resolve(name))}`);
  const url = `data:text/javascript;base64,${Buffer.from(js).toString("base64")}`;
  modules.set(file.href, url);
  return url;
}
const { proxyAnalysis } = await import(compiledUrl(new URL("../worker/gateway.ts", import.meta.url)));
const path = "/v1/jobs/comparison-job-123/reference-comparison";
const env = { RALLYMATE_API_ORIGIN: "http://127.0.0.1:8001", RALLYMATE_API_KEY: "comparison-server-key" };
const request = (query = "", init = {}) => new Request(`http://localhost${path}${query}`, init);

test("comparison GET forwards the exact query and uses only configured server credentials", async t => {
  const query = "?reference_job_id=reference-job-456&episode_id=motion%3A12&reference_episode_id=ref%3A34&subject_id=subject-1&tag=a%2Fb&tag=a+b";
  const payload = { status: "compared", source_run_id: "comparison-job-123", reference_run_id: "reference-job-456", score_delta: 7.25 };
  let calls = 0;
  t.mock.method(globalThis, "fetch", async (url, init) => {
    calls++;
    assert.equal(url.origin, env.RALLYMATE_API_ORIGIN);
    assert.equal(url.pathname, path);
    assert.equal(url.search, query);
    assert.equal(init.method, "GET");
    assert.equal(init.redirect, "manual");
    assert.equal(init.headers.get("authorization"), "Bearer comparison-server-key");
    assert.equal(init.headers.get("cookie"), null);
    assert.equal(init.headers.get("x-api-key"), null);
    return Response.json(payload);
  });
  const response = await proxyAnalysis(request(query, { headers: {
    authorization: "Bearer browser-supplied-key", cookie: "session=browser-cookie", "x-api-key": "browser-key",
  } }), env);
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), payload);
  assert.equal(response.headers.get("cache-control"), "private, no-store");
  assert.equal(calls, 1);
});

test("comparison is GET-only and rejects write methods before contacting the service", async t => {
  let calls = 0;
  t.mock.method(globalThis, "fetch", async () => { calls++; return Response.json({}); });
  for (const method of ["POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]) {
    const response = await proxyAnalysis(request("?reference_job_id=reference-job-456", { method }), env);
    assert.equal(response.status, 404, method);
    assert.deepEqual(await response.json(), { error: "route_not_allowed" });
  }
  assert.equal(calls, 0);
});

test("comparison preserves source error status, detail and retry timing without forwarding response credentials", async t => {
  let expectedStatus;
  t.mock.method(globalThis, "fetch", async () => Response.json({ detail: { code: "comparison_source_invalid", status: expectedStatus } }, {
    status: expectedStatus, headers: { "retry-after": "3", "set-cookie": "upstream=private", authorization: "Bearer upstream-secret" },
  }));
  for (const status of [400, 404, 409, 422, 503]) {
    expectedStatus = status;
    const response = await proxyAnalysis(request("?reference_job_id=reference-job-456"), env);
    assert.equal(response.status, status);
    assert.deepEqual(await response.json(), { detail: { code: "comparison_source_invalid", status } });
    assert.equal(response.headers.get("retry-after"), "3");
    assert.equal(response.headers.get("set-cookie"), null);
    assert.equal(response.headers.get("authorization"), null);
  }
});

test("reference selectors stay query data and cannot change the service URL or authentication", async t => {
  let expectedQuery, calls = 0;
  t.mock.method(globalThis, "fetch", async (url, init) => {
    calls++;
    assert.equal(url.origin, env.RALLYMATE_API_ORIGIN);
    assert.equal(url.pathname, path);
    assert.equal(url.search, expectedQuery);
    assert.equal(url.hash, "");
    assert.equal(init.headers.get("authorization"), "Bearer comparison-server-key");
    return Response.json({ detail: "invalid reference selector" }, { status: 422 });
  });
  for (const value of ["https://untrusted.example/v1/jobs/private", "//untrusted.example/private", "../../artifacts/private?token=value#fragment",
    "ref&reference_job_id=other-job&authorization=bad", "ref\r\nAuthorization: Bearer injected", "%2F%2Funtrusted.example"]) {
    expectedQuery = `?${new URLSearchParams({ reference_job_id: "reference-job-456", reference_episode_id: value, episode_id: value })}`;
    const response = await proxyAnalysis(request(expectedQuery), env);
    assert.equal(response.status, 422);
    assert.deepEqual(await response.json(), { detail: "invalid reference selector" });
  }
  assert.equal(calls, 6);
});

test("comparison redirects cannot forward server credentials to a reference-supplied location", async t => {
  let calls = 0;
  t.mock.method(globalThis, "fetch", async (_url, init) => {
    calls++;
    assert.equal(init.redirect, "manual");
    return new Response(null, { status: 302, headers: { location: "https://untrusted.example/private" } });
  });
  const response = await proxyAnalysis(request(), env);
  assert.equal(response.status, 502);
  assert.deepEqual(await response.json(), { error: "unexpected_upstream_redirect" });
  assert.equal(response.headers.get("location"), null);
  assert.equal(calls, 1);
});

test("comparison uses the bounded 120-second action-read deadline", async t => {
  const deadline = new AbortController();
  let upstreamSignal;
  t.mock.method(AbortSignal, "timeout", ms => { assert.equal(ms, 120000); return deadline.signal; });
  t.mock.method(globalThis, "fetch", (_url, init) => {
    upstreamSignal = init.signal;
    return new Promise((_resolve, reject) => init.signal.addEventListener("abort", () => reject(init.signal.reason), { once: true }));
  });
  const pending = proxyAnalysis(request(), env);
  assert.equal(upstreamSignal.aborted, false);
  deadline.abort(new DOMException("Action read deadline reached", "TimeoutError"));
  const response = await pending;
  assert.equal(upstreamSignal.aborted, true);
  assert.equal(response.status, 502);
  assert.deepEqual(await response.json(), { error: "analysis_service_unavailable" });
});

test("changing the source cancels an outstanding comparison read", async t => {
  const caller = new AbortController();
  let upstreamSignal;
  t.mock.method(globalThis, "fetch", (_url, init) => {
    upstreamSignal = init.signal;
    return new Promise((_resolve, reject) => init.signal.addEventListener("abort", () => reject(init.signal.reason), { once: true }));
  });
  const pending = proxyAnalysis(request("", { signal: caller.signal }), env);
  const reason = new DOMException("Selected another episode", "AbortError");
  caller.abort(reason);
  const response = await pending;
  assert.equal(upstreamSignal.reason, reason);
  assert.equal(response.status, 502);
});
