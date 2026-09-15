import assert from "node:assert/strict";
import test from "node:test";

const workerUrl = new URL("../dist/server/index.js", import.meta.url);
async function worker() { const url = new URL(workerUrl); url.searchParams.set("test", `${process.pid}-${Date.now()}-${Math.random()}`); return (await import(url.href)).default; }
const env = { ASSETS: { fetch: async () => new Response("Not found", { status: 404 }) } };
const ctx = { waitUntil() {}, passThroughOnException() {} };

test("renders the motion analysis workspace", async () => {
  const app = await worker();
  const response = await app.fetch(new Request("http://localhost/", { headers: { accept: "text/html" } }), env, ctx);
  assert.equal(response.status, 200); assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);
  const html = await response.text();
  assert.match(html, /MOTION ANALYSIS/);
  assert.match(html, /上传一段击球视频/); assert.match(html, /证据回放/); assert.match(html, /逐项评分与证据回放/); assert.match(html, /基于这次练习，问一个下一步/);
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
  assert.equal(response.status, 200); const body = await response.json(); assert.equal(body.source, "fallback"); assert.ok(Array.isArray(body.advice.nextSteps));
});

test("advice safety gate blocks physical symptom observations", async () => {
  const app = await worker(); const response = await app.fetch(new Request("http://localhost/api/advice", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ technique: "底线击球", skillLevel: "业余进阶", sessionGoal: "稳定性", observations: "出现胸闷，想继续高强度练习" }) }), env, ctx);
  assert.equal(response.status, 200); const body = await response.json(); assert.equal(body.source, "safety_fallback"); assert.match(body.advice.summary, /暂停/); assert.deepEqual(body.advice.drills, []);
});

test("advice job context degrades safely when trusted analysis origin is unavailable", async () => {
  const app = await worker(); const response = await app.fetch(new Request("http://localhost/api/advice", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ technique: "发球", skillLevel: "业余进阶", sessionGoal: "节奏感", observations: "抛球偏后", jobId: "job_12345678" }) }), env, ctx);
  assert.equal(response.status, 200); const body = await response.json(); assert.equal(body.contextStatus, "unavailable");
});

test("Node production entry supports missing Cloudflare bindings", async () => {
  const app = await worker();
  const response = await app.fetch(new Request("http://localhost/", { headers: { accept: "text/html" } }), undefined, ctx);
  assert.equal(response.status, 200);
  const missing = await app.fetch(new Request("http://localhost/v1/meta"), undefined, ctx);
  assert.equal(missing.status, 503);
});
