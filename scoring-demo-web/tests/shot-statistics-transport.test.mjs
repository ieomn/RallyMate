import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import ts from "typescript";

const modules = new Map();
function compiledUrl(file) {
  if (file.pathname.endsWith(".json")) return `data:text/javascript;base64,${Buffer.from(`export default ${fs.readFileSync(file, "utf8")}`).toString("base64")}`;
  if (modules.has(file.href)) return modules.get(file.href);
  let js = ts.transpileModule(fs.readFileSync(file, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  js = js.replace(/from "([^"]+)"/g, (_match, name) => `from ${JSON.stringify(
    name.startsWith(".") ? compiledUrl(new URL(name.endsWith(".json") ? name : `${name}.ts`, file)) : import.meta.resolve(name),
  )}`);
  const url = `data:text/javascript;base64,${Buffer.from(js).toString("base64")}`;
  modules.set(file.href, url);
  return url;
}
const { createApiClient } = await import(compiledUrl(new URL("../app/lib/api-client.ts", import.meta.url)));
const { proxyAnalysis } = await import(compiledUrl(new URL("../worker/gateway.ts", import.meta.url)));

const jobId = "66883aeb-e0c3-4065-80fd-f67aa108b208";
const path = `/v1/jobs/${jobId}/shot-statistics`;
const sha = "a".repeat(64);
const configured = { baseUrl: "", jobsPath: "/v1/jobs", scorecardPath: "/api/scorecard", techniquesPath: "/v1/techniques" };
const env = { RALLYMATE_API_ORIGIN: "http://127.0.0.1:8001", RALLYMATE_API_KEY: "internal-test-key" };
const report = (revision = 0) => ({
  schema_version: "1.0.0", job_id: jobId, status: revision ? "reviewed" : "unreviewed", revision,
  source: { video_sha256: sha, frame_count: 301, start_ms: 0, end_ms: 10000, duration_ms: 10000, coordinate_space: "normalized_frame_0_1" },
  summary: { confirmed_hit_count: 0, confirmed_bounce_count: 0, mapped_bounce_count: 0, candidate_hit_count: 0,
    candidate_bounce_count: 0, dismissed_count: 0, count_semantics: "review_confirmed_events_not_total_video" },
  events: [], limitations_zh: ["自动候选需要回看确认。"],
});
const submission = () => ({
  mutation_id: "a6438d47-cfd3-4258-b0ea-1aa9dd40851d", expected_revision: 7, source_video_sha256: sha,
  event: { id: "manual-0e65789f-eefc-4293-ae80-536dcd00ba5d", kind: "bounce", status: "confirmed",
    timestamp_ms: 1250, frame_index: null, position_court: { x: .25, y: .75, source: "manual" } },
});

test("shot GET uses the job endpoint, keeps configured query, and returns source/revision", async () => {
  let calls = 0;
  const expected = report(7);
  const client = createApiClient({ ...configured, jobsPath: "/v1/jobs?view=compact" }, async (url, init) => {
    calls++;
    assert.equal(url, `${path}?view=compact`);
    assert.equal(init.method, "GET");
    assert.equal(init.cache, "no-store");
    assert.ok(new Headers(init.headers).get("x-request-id"));
    assert.ok(init.signal instanceof AbortSignal);
    assert.equal(init.signal.aborted, false);
    return Response.json(expected);
  });
  assert.deepEqual(await client.getShotStatistics(jobId), expected);
  assert.equal(calls, 1);
});

test("shot POST preserves mutation, source, expected revision and the review event exactly", async () => {
  const body = submission(), expected = report(8);
  let calls = 0;
  const client = createApiClient(configured, async (url, init) => {
    calls++;
    assert.equal(url, path);
    assert.equal(init.method, "POST");
    assert.equal(init.cache, "no-store");
    assert.equal(new Headers(init.headers).get("content-type"), "application/json");
    assert.ok(new Headers(init.headers).get("x-request-id"));
    assert.ok(init.signal instanceof AbortSignal);
    assert.deepEqual(JSON.parse(init.body), body);
    return Response.json(expected);
  });
  assert.deepEqual(await client.saveShotReview(jobId, body), expected);
  assert.equal(calls, 1);
});

test("shot client refuses another job on GET and another video on POST", async () => {
  const otherJob = { ...report(), job_id: "another-job" };
  const getter = createApiClient(configured, async () => Response.json(otherJob));
  await assert.rejects(getter.getShotStatistics(jobId), error => error.status === 422 && /当前任务/.test(error.message));
  for (const response of [otherJob, { ...report(8), source: { ...report().source, video_sha256: "b".repeat(64) } }]) {
    const writer = createApiClient(configured, async () => Response.json(response));
    await assert.rejects(writer.saveShotReview(jobId, submission()), error => error.status === 422 && /当前视频/.test(error.message));
  }
});

test("shot client preserves conflict and gateway error details without retrying writes", async () => {
  for (const [status, payload, message] of [
    [409, { detail: { code: "shot_review_revision_conflict", message_zh: "统计已更新，请重新读取。", revision: 8 } }, "统计已更新，请重新读取。"],
    [403, { error: "origin_not_allowed" }, "origin_not_allowed"],
    [404, { detail: "任务不存在" }, "任务不存在"],
  ]) {
    let calls = 0;
    const client = createApiClient(configured, async () => { calls++; return Response.json(payload, { status }); });
    await assert.rejects(client.saveShotReview(jobId, submission()), error => {
      assert.equal(error.status, status);
      assert.equal(error.message, message);
      assert.deepEqual(error.payload, payload);
      return true;
    });
    assert.equal(calls, 1);
  }
});

test("shot GET and POST reject a successful HTML error page as invalid JSON", async () => {
  const client = createApiClient(configured, async () => new Response("<html>Gateway error</html>", { headers: { "content-type": "text/html" } }));
  await assert.rejects(client.getShotStatistics(jobId), error => error.status === 502 && /JSON/.test(error.message));
  await assert.rejects(client.saveShotReview(jobId, submission()), error => error.status === 502 && /JSON/.test(error.message));
});

test("shot GET and POST propagate caller cancellation through their fetch signals", async () => {
  for (const method of ["GET", "POST"]) {
    const caller = new AbortController();
    let calls = 0;
    const client = createApiClient(configured, (_url, init) => {
      calls++;
      assert.equal(init.method, method);
      return new Promise((_resolve, reject) => init.signal.addEventListener("abort", () => reject(init.signal.reason), { once: true }));
    });
    const pending = method === "GET" ? client.getShotStatistics(jobId, caller.signal) : client.saveShotReview(jobId, submission(), caller.signal);
    // GET first joins the report read queue; exercise cancellation after fetch
    // has actually started (cancellation while queued is tested separately).
    await Promise.resolve();
    const reason = new DOMException("Changed video", "AbortError");
    const rejected = assert.rejects(pending, error => error === reason);
    caller.abort(reason);
    await rejected;
    assert.equal(calls, 1);
  }
});

test("shot gateway forwards GET with server authentication and uncached statistics", async t => {
  let calls = 0;
  t.mock.method(globalThis, "fetch", async (url, init) => {
    calls++;
    assert.equal(String(url), `${env.RALLYMATE_API_ORIGIN}${path}?view=compact`);
    assert.equal(init.method, "GET");
    assert.equal(init.redirect, "manual");
    assert.equal(init.headers.get("authorization"), "Bearer internal-test-key");
    assert.equal(init.headers.get("cookie"), null);
    return Response.json(report(7), { headers: { "set-cookie": "upstream=private" } });
  });
  const response = await proxyAnalysis(new Request(`http://localhost${path}?view=compact`, {
    headers: { authorization: "Bearer browser-value", cookie: "browser=private" },
  }), env);
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), report(7));
  assert.equal(response.headers.get("cache-control"), "private, no-store");
  assert.equal(response.headers.get("set-cookie"), null);
  assert.equal(calls, 1);
});

test("shot client and gateway carry a same-origin review to the backend without rewriting its binding", async t => {
  const body = submission();
  let calls = 0;
  t.mock.method(globalThis, "fetch", async (url, init) => {
    calls++;
    assert.equal(String(url), `${env.RALLYMATE_API_ORIGIN}${path}`);
    assert.equal(init.method, "POST");
    assert.equal(init.redirect, "manual");
    assert.equal(init.headers.get("authorization"), "Bearer internal-test-key");
    assert.equal(init.headers.get("content-type"), "application/json");
    assert.ok(init.headers.get("x-request-id"));
    assert.deepEqual(await new Response(init.body).json(), body);
    return Response.json(report(8));
  });
  const client = createApiClient(configured, (url, init) => {
    const headers = new Headers(init.headers);
    headers.set("origin", "http://localhost");
    headers.set("sec-fetch-site", "same-origin");
    return proxyAnalysis(new Request(`http://localhost${url}`, { ...init, headers }), env);
  });
  assert.deepEqual(await client.saveShotReview(jobId, body), report(8));
  assert.equal(calls, 1);
});

test("shot gateway rejects cross-site review writes before contacting the backend", async t => {
  let calls = 0;
  t.mock.method(globalThis, "fetch", async () => { calls++; throw new Error("Unexpected upstream request"); });
  for (const headers of [
    { origin: "https://other.invalid" },
    { origin: "http://localhost", "sec-fetch-site": "cross-site" },
    { "sec-fetch-site": "cross-site" },
  ]) {
    const response = await proxyAnalysis(new Request(`http://localhost${path}`, { method: "POST", headers, body: JSON.stringify(submission()) }), env);
    assert.equal(response.status, 403);
    assert.deepEqual(await response.json(), { error: "origin_not_allowed" });
  }
  assert.equal(calls, 0);
});

test("shot tunnel writes accept only the tunnel browser origin", async t => {
  let calls = 0;
  t.mock.method(globalThis, "fetch", async () => { calls++; return Response.json(report(8)); });
  const tunnelEnv = { ...env, RALLYMATE_LOCAL_TUNNEL: "1" };
  const accepted = await proxyAnalysis(new Request(`http://tennis.example${path}`, {
    method: "POST", headers: { origin: "https://tennis.example", "sec-fetch-site": "same-origin" }, body: JSON.stringify(submission()),
  }), tunnelEnv);
  assert.equal(accepted.status, 200);
  const rejected = await proxyAnalysis(new Request(`http://tennis.example${path}`, {
    method: "POST", headers: { origin: "https://other.example" }, body: JSON.stringify(submission()),
  }), tunnelEnv);
  assert.equal(rejected.status, 403);
  assert.deepEqual(await rejected.json(), { error: "origin_not_allowed" });
  assert.equal(calls, 1);
});

test("shot gateway allowlist permits no other methods or adjacent artifact paths", async t => {
  let calls = 0;
  t.mock.method(globalThis, "fetch", async () => { calls++; throw new Error("Unexpected upstream request"); });
  const cases = [
    ...["PUT", "PATCH", "DELETE", "HEAD"].map(method => [method, path]),
    ...[`${path}/`, `${path}/export`, `${path}.json`, `/v1/jobs/${jobId}/artifacts/shot-statistics.json`,
      "/v1/jobs/short/shot-statistics", "/v1/jobs/job%2fescape/shot-statistics"].flatMap(url => [["GET", url], ["POST", url]]),
  ];
  for (const [method, url] of cases) {
    const response = await proxyAnalysis(new Request(`http://localhost${url}`, { method }), env);
    assert.equal(response.status, 404, `${method} ${url}`);
    assert.deepEqual(await response.json(), { error: "route_not_allowed" });
  }
  assert.equal(calls, 0);
});

test("shot gateway preserves backend conflicts and rejects upstream redirects", async t => {
  const conflict = { detail: { code: "shot_review_revision_conflict", message_zh: "统计已更新，请重新读取。" } };
  const responses = [Response.json(conflict, { status: 409 }), new Response(null, { status: 307, headers: { location: "https://other.invalid" } })];
  let calls = 0;
  t.mock.method(globalThis, "fetch", async () => responses[calls++]);
  const request = () => new Request(`http://localhost${path}`, { method: "POST", headers: { origin: "http://localhost" }, body: JSON.stringify(submission()) });
  const rejected = await proxyAnalysis(request(), env);
  assert.equal(rejected.status, 409);
  assert.equal(rejected.headers.get("cache-control"), "private, no-store");
  assert.deepEqual(await rejected.json(), conflict);
  const redirect = await proxyAnalysis(request(), env);
  assert.equal(redirect.status, 502);
  assert.deepEqual(await redirect.json(), { error: "unexpected_upstream_redirect" });
  assert.equal(calls, 2);
});
