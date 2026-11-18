import assert from "node:assert/strict";
import test from "node:test";

const workerUrl = new URL("../dist/server/index.js", import.meta.url);
async function worker() { const url = new URL(workerUrl); url.searchParams.set("test", `${process.pid}-${Date.now()}-${Math.random()}`); return (await import(url.href)).default; }
const env = { ASSETS: { fetch: async () => new Response("Not found", { status: 404 }) } };
const ctx = { waitUntil() {}, passThroughOnException() {} };
const accountHeaders = { "content-type": "application/json", cookie: "rallymate_session=test-session", origin: "http://localhost", "x-csrf-token": "test-csrf" };
const accountEnv = { ...env, RALLYMATE_API_ORIGIN: "http://127.0.0.1:8017", RALLYMATE_LOCAL_TUNNEL: "1", MIMO_ADVICE_ENABLED: "false" };
async function withApprovedAccount(callback, contextStatus = 503) {
  const original = globalThis.fetch;
  try {
    globalThis.fetch = async (input, init) => {
      assert.equal(new Headers(init?.headers).get("cookie"), "rallymate_session=test-session");
      if (new URL(String(input)).pathname === "/api/auth/me") return Response.json({
        user: { id: "test-user", username: "Test player", role: "USER", status: "APPROVED", must_change_password: false },
        csrf_token: "test-csrf", expires_at: "2099-01-01T00:00:00Z",
      });
      return Response.json({ detail: "fixture context unavailable" }, { status: contextStatus });
    };
    return await callback();
  } finally { globalThis.fetch = original; }
}

test("server rendering keeps the workspace private until the account guard resolves", async () => {
  const app = await worker();
  const response = await app.fetch(new Request("https://app.example.com/?view=overview", { headers: { accept: "text/html" } }), env, ctx);
  assert.equal(response.status, 200); assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);
  assert.equal(response.headers.get("www-authenticate"), null);
  const html = await response.text();
  assert.match(html, /登录|账[号户]/);
  assert.doesNotMatch(html, /<main[^>]+id="workspace-content"/);
  assert.doesNotMatch(html, /<input[^>]+type="file"/);
  assert.doesNotMatch(html, /offline-demo|result-row active|score-ring|综合分|技术评分\s*[:：]?\s*\d/);
  assert.doesNotMatch(html, /analysis_context_unavailable/);
  assert.doesNotMatch(html, /没有匹配的指标/);
});

test("retired capture-guide links redirect to the canonical overview before loading a job", async () => {
  const app = await worker();
  for (const path of ["/?view=guide", "/?view=guide&job=retired-job-1234"]) {
    const response = await app.fetch(new Request(`https://app.example.com${path}`, { headers: { accept: "text/html" } }), env, ctx);
    assert.equal(response.status, 307);
    assert.equal(response.headers.get("location"), "/?view=overview");
  }
  const landing = await app.fetch(new Request("https://app.example.com/welcome", { headers: { accept: "text/html" } }), env, ctx);
  assert.equal(landing.status, 200);
  assert.doesNotMatch(await landing.text(), /href="[^"]*view=guide|查看(?:完整)?拍摄指南/);
});

test("catalog API exposes the five practice categories", async () => {
  const app = await worker(); const response = await app.fetch(new Request("http://localhost/api/scorecard"), env, ctx);
  assert.equal(response.status, 200); const catalog = await response.json();
  assert.equal(catalog.surface, "practice_catalog"); assert.equal(catalog.techniqueCount, 24); assert.equal(catalog.categories.length, 5);
  assert.equal(catalog.semantics.numericalGrades, false); assert.equal(catalog.semantics.competitiveRanking, false);
  assert.doesNotMatch(JSON.stringify(catalog), /GS|FS/);
});

test("retired scorecard endpoint points to advice service", async () => {
  const app = await worker(); const response = await app.fetch(new Request("http://localhost/api/scorecard", { method: "POST", headers: { "content-type": "application/json" }, body: "{}" }), env, ctx);
  assert.equal(response.status, 410); const body = await response.json(); assert.equal(body.adviceEndpoint, "/api/advice");
});

test("advice endpoint returns a safe fallback without a provider secret", async () => {
  const app = await worker(); const response = await withApprovedAccount(() => app.fetch(new Request("http://localhost/api/advice", { method: "POST", headers: accountHeaders, body: JSON.stringify({ technique: "底线击球", skillLevel: "业余进阶", sessionGoal: "稳定性", observations: "击球后有时站直" }) }), accountEnv, ctx));
  assert.equal(response.status, 200); const body = await response.json(); assert.equal(body.source, "evidence_fallback"); assert.equal(body.evidenceStatus, "no_video"); assert.match(body.advice.summary, /无法评价/); assert.ok(Array.isArray(body.advice.nextSteps));
});

test("advice safety gate blocks physical symptom observations", async () => {
  const app = await worker(); const response = await withApprovedAccount(() => app.fetch(new Request("http://localhost/api/advice", { method: "POST", headers: accountHeaders, body: JSON.stringify({ technique: "底线击球", skillLevel: "业余进阶", sessionGoal: "稳定性", observations: "出现胸闷，想继续高强度练习" }) }), accountEnv, ctx));
  assert.equal(response.status, 200); const body = await response.json(); assert.equal(body.source, "safety_fallback"); assert.match(body.advice.summary, /暂停/); assert.deepEqual(body.advice.drills, []);
});

test("advice job context degrades safely when trusted analysis origin is unavailable", async () => {
  const app = await worker(); const response = await withApprovedAccount(() => app.fetch(new Request("http://localhost/api/advice", { method: "POST", headers: accountHeaders, body: JSON.stringify({ technique: "发球", skillLevel: "业余进阶", sessionGoal: "节奏感", observations: "抛球偏后", jobId: "job_12345678" }) }), accountEnv, ctx));
  assert.equal(response.status, 200); const body = await response.json(); assert.equal(body.contextStatus, "unavailable");
});

test("uploads forward the browser account and never replace it with a shared service key", async () => {
  const app = await worker();
  const original = globalThis.fetch;
  try {
    globalThis.fetch = async (url, init) => {
      assert.equal(String(url), "http://127.0.0.1:8001/v1/jobs");
      assert.equal(init.headers.get("authorization"), "Bearer user-session-jwt");
      return Response.json({ id: "test-job-12345678" }, { status: 202 });
    };
    const response = await app.fetch(new Request("http://app.example.com/v1/jobs", {
      method: "POST", headers: { origin: "https://app.example.com", authorization: "Bearer user-session-jwt" }, body: "video",
    }), { ...env, RALLYMATE_API_ORIGIN: "http://127.0.0.1:8001", RALLYMATE_API_KEY: "backend-test", RALLYMATE_LOCAL_TUNNEL: "1" }, ctx);
    assert.equal(response.status, 202);
    assert.equal(response.headers.get("www-authenticate"), null);
    assert.deepEqual(await response.json(), { id: "test-job-12345678" });
  } finally { globalThis.fetch = original; }
});

test("Node production entry supports missing Cloudflare bindings", async () => {
  const app = await worker();
  const response = await app.fetch(new Request("http://localhost/", { headers: { accept: "text/html" } }), undefined, ctx);
  assert.equal(response.status, 307);
  assert.equal(response.headers.get("location"), "/welcome");
  const landing = await app.fetch(new Request("http://localhost/welcome", { headers: { accept: "text/html" } }), undefined, ctx);
  assert.equal(landing.status, 200);
  const annotation = await app.fetch(new Request("http://localhost/annotate", { headers: { accept: "text/html" } }), undefined, ctx);
  assert.equal(annotation.status, 200);
  const annotationHtml = await annotation.text();
  assert.match(annotationHtml, /登录|账[号户]/);
  assert.doesNotMatch(annotationHtml, /<input[^>]+placeholder="任务编号或本站报告链接"/);
  assert.doesNotMatch(annotationHtml, /id="workspace-content"|选择或拖入视频文件/);
  const missing = await app.fetch(new Request("http://localhost/v1/meta"), undefined, ctx);
  assert.equal(missing.status, 401);
});
