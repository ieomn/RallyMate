import assert from "node:assert/strict";
import test from "node:test";

const workerUrl = new URL("../dist/server/index.js", import.meta.url);

async function worker() {
  const url = new URL(workerUrl);
  url.searchParams.set("test", `${process.pid}-${Date.now()}-${Math.random()}`);
  return (await import(url.href)).default;
}

const env = {
  ASSETS: { fetch: async () => new Response("Not found", { status: 404 }) },
};
const ctx = { waitUntil() {}, passThroughOnException() {} };

test("renders the complete RallyMate scoring demo", async () => {
  const app = await worker();
  const response = await app.fetch(new Request("http://localhost/", { headers: { accept: "text/html" } }), env, ctx);
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);
  const html = await response.text();
  assert.match(html, /RallyMate Score Lab/);
  assert.match(html, /让每一分/);
  assert.match(html, /298 条规则/);
  assert.match(html, /完整验收演示/);
  assert.doesNotMatch(html, /Your site is taking shape|SkeletonPreview/);
});

test("scorecard API returns all 298 traceable results", async () => {
  const app = await worker();
  const request = new Request("http://localhost/api/scorecard", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ scenarioId: "demo-advanced" }),
  });
  const response = await app.fetch(request, env, ctx);
  assert.equal(response.status, 200);
  const report = await response.json();
  assert.equal(report.resultCount, 298);
  assert.equal(report.acceptanceStatus, "complete-demo");
  assert.equal(report.results.filter((item) => item.status === "scored").length, 298);
  assert.ok(report.overallScore >= 80 && report.overallScore <= 100);
  assert.equal(report.domains.GS.total, 248);
  assert.equal(report.domains.FS.total, 50);
});

test("real Stage 1 scenario never fabricates a technical grade", async () => {
  const app = await worker();
  const request = new Request("http://localhost/api/scorecard", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ scenarioId: "real-c235227f" }),
  });
  const response = await app.fetch(request, env, ctx);
  const report = await response.json();
  assert.equal(report.overallScore, null);
  assert.equal(report.overallGrade, null);
  assert.equal(report.acceptanceStatus, "evidence-audit");
  assert.equal(report.results.some((item) => item.score !== null), false);
});
