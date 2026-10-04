import assert from "node:assert/strict";
import test from "node:test";

const workerUrl = new URL("../dist/server/index.js", import.meta.url);
async function worker() { const url = new URL(workerUrl); url.searchParams.set("test", `${process.pid}-${Date.now()}-${Math.random()}`); return (await import(url.href)).default; }
const env = { ASSETS: { fetch: async () => new Response("Not found", { status: 404 }) } };
const ctx = { waitUntil() {}, passThroughOnException() {} };

test("renders the public training journal with upload first and the offline sample folded", async () => {
  const app = await worker();
  const response = await app.fetch(new Request("https://app.example.com/", { headers: { accept: "text/html" } }), env, ctx);
  assert.equal(response.status, 200); assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);
  assert.equal(response.headers.get("www-authenticate"), null);
  const html = await response.text();
  assert.match(html, /YOUR PRACTICE, IN FOCUS/);
  assert.match(html, /每次练习，都看见一点进步/); assert.match(html, /分析新视频/); assert.match(html, /最近报告/); assert.match(html, /导入报告/);
  assert.match(html, /<details class="report-disclosure offline-demo"><summary>/);
  assert.match(html, /<details class="report-disclosure report-system" id="system"><summary>/);
  assert.doesNotMatch(html, /hero-system-map|保留 COCO17 基线/);
  assert.doesNotMatch(html, /analysis_context_unavailable/);
  assert.doesNotMatch(html, /没有匹配的指标/);
  assert.match(html, /result-row active/);
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
  const app = await worker(); const response = await app.fetch(new Request("http://localhost/api/advice", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ technique: "底线击球", skillLevel: "业余进阶", sessionGoal: "稳定性", observations: "击球后有时站直" }) }), env, ctx);
  assert.equal(response.status, 200); const body = await response.json(); assert.equal(body.source, "evidence_fallback"); assert.equal(body.evidenceStatus, "no_video"); assert.match(body.advice.summary, /无法评价/); assert.ok(Array.isArray(body.advice.nextSteps));
});

test("advice safety gate blocks physical symptom observations", async () => {
  const app = await worker(); const response = await app.fetch(new Request("http://localhost/api/advice", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ technique: "底线击球", skillLevel: "业余进阶", sessionGoal: "稳定性", observations: "出现胸闷，想继续高强度练习" }) }), env, ctx);
  assert.equal(response.status, 200); const body = await response.json(); assert.equal(body.source, "safety_fallback"); assert.match(body.advice.summary, /暂停/); assert.deepEqual(body.advice.drills, []);
});

test("advice job context degrades safely when trusted analysis origin is unavailable", async () => {
  const app = await worker(); const response = await app.fetch(new Request("http://localhost/api/advice", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ technique: "发球", skillLevel: "业余进阶", sessionGoal: "节奏感", observations: "抛球偏后", jobId: "job_12345678" }) }), env, ctx);
  assert.equal(response.status, 200); const body = await response.json(); assert.equal(body.contextStatus, "unavailable");
});

test("public uploads need no browser login and retain server-side API authentication", async () => {
  const app = await worker();
  const original = globalThis.fetch;
  try {
    globalThis.fetch = async (url, init) => {
      assert.equal(String(url), "http://127.0.0.1:8001/v1/jobs");
      assert.equal(init.headers.get("authorization"), "Bearer backend-test");
      return Response.json({ id: "test-job-12345678" }, { status: 202 });
    };
    const response = await app.fetch(new Request("http://app.example.com/v1/jobs", {
      method: "POST", headers: { origin: "https://app.example.com" }, body: "video",
    }), { ...env, RALLYMATE_API_ORIGIN: "http://127.0.0.1:8001", RALLYMATE_API_KEY: "backend-test", RALLYMATE_LOCAL_TUNNEL: "1" }, ctx);
    assert.equal(response.status, 202);
    assert.equal(response.headers.get("www-authenticate"), null);
    assert.deepEqual(await response.json(), { id: "test-job-12345678" });
  } finally { globalThis.fetch = original; }
});

test("Node production entry supports missing Cloudflare bindings", async () => {
  const app = await worker();
  const response = await app.fetch(new Request("http://localhost/", { headers: { accept: "text/html" } }), undefined, ctx);
  assert.equal(response.status, 200);
  const missing = await app.fetch(new Request("http://localhost/v1/meta"), undefined, ctx);
  assert.equal(missing.status, 503);
});
