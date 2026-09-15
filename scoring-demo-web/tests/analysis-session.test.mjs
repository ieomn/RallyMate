import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import ts from "typescript";

async function moduleFrom(path) {
  const source = fs.readFileSync(new URL(path, import.meta.url), "utf8");
  const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
  return import(`data:text/javascript;base64,${Buffer.from(js).toString("base64")}`);
}
const { watchAnalysis } = await moduleFrom("../app/lib/analysis-session.ts");
const { proxyAnalysis, requireAccess } = await moduleFrom("../worker/gateway.ts");
const id = "test-job-12345678";
const client = (overrides = {}) => ({
  getJob: async () => ({ id, status: "succeeded" }),
  getDemoResult: async () => ({ job_id: id, status: "ready", training_evaluation: { score_0_to_100: 77 } }),
  getTrajectory: async () => ({}), getTechniqueAssessment: async () => ({}), ...overrides,
});

test("long jobs continue beyond the old 180-poll limit", async () => {
  let polls = 0;
  const result = await watchAnalysis(client({ getJob: async () => ({ id, status: ++polls > 185 ? "succeeded" : "running" }) }), id, new AbortController().signal, () => {}, 0);
  assert.equal(polls, 186); assert.equal(result.result.training_evaluation.score_0_to_100, 77);
});
test("a transient disconnect reconnects to the same job", async () => {
  let calls = 0;
  const result = await watchAnalysis(client({ getJob: async () => { if (++calls === 1) throw new Error("network"); return { id, status: "succeeded" }; } }), id, new AbortController().signal, () => {}, 0);
  assert.equal(result.job.id, id); assert.equal(calls, 2);
});
test("failed results are resumable and never reported as complete", async () => {
  await assert.rejects(watchAnalysis(client({ getDemoResult: async () => { throw new Error("503"); } }), id, new AbortController().signal, () => {}, 0), /无需重新上传/);
  await assert.rejects(watchAnalysis(client({ getDemoResult: async () => ({ job_id: "other", status: "ready" }) }), id, new AbortController().signal, () => {}, 0), /不匹配/);
});
test("optional overlays can fail without discarding a valid score", async () => {
  const result = await watchAnalysis(client({ getTrajectory: async () => { throw new Error("unavailable"); } }), id, new AbortController().signal, () => {}, 0);
  assert.equal(result.result.training_evaluation.score_0_to_100, 77); assert.ok(result.warning);
});
test("stopping observation cancels polling", async () => {
  const controller = new AbortController(); controller.abort();
  await assert.rejects(watchAnalysis(client(), id, controller.signal, () => {}, 0), { name: "AbortError" });
});
test("public gateway fails closed without an account", async () => {
  const response = await requireAccess(new Request("https://app.example.com/v1/jobs"), { RALLYMATE_API_KEY: "backend-test" });
  assert.equal(response.status, 503);
  assert.equal(await requireAccess(new Request("http://localhost/v1/jobs"), {}), null);
});
test("private gateway requires the configured web account", async () => {
  const env = { RALLYMATE_WEB_USER: "tester", RALLYMATE_WEB_PASSWORD: "test-password-12345678" };
  assert.equal((await requireAccess(new Request("https://app.example.com/"), env)).status, 401);
  assert.equal(await requireAccess(new Request("https://app.example.com/", { headers: { authorization: "Basic " + btoa("tester:test-password-12345678") } }), env), null);
});
test("proxy rejects unknown paths and cross-site uploads", async () => {
  assert.equal((await proxyAnalysis(new Request("http://localhost/v1/admin"), {})).status, 404);
  assert.equal((await proxyAnalysis(new Request("http://localhost/v1/jobs", { method: "POST", headers: { origin: "https://other.example" } }), {})).status, 403);
});
test("proxy strips browser credentials and preserves video range", async () => {
  const original = globalThis.fetch;
  try {
    globalThis.fetch = async (url, init) => {
      assert.equal(String(url), `https://analyzer.example/v1/jobs/${id}/artifacts/annotated.mp4`);
      assert.equal(init.headers.get("authorization"), "Bearer backend-test");
      assert.equal(init.headers.get("cookie"), null); assert.equal(init.headers.get("range"), "bytes=0-9"); assert.equal(init.redirect, "manual");
      return new Response("0123456789", { status: 206, headers: { "content-range": "bytes 0-9/100", "content-type": "video/mp4" } });
    };
    const response = await proxyAnalysis(new Request(`http://localhost/v1/jobs/${id}/artifacts/annotated.mp4`, { headers: { cookie: "private=1", authorization: "Basic browser", range: "bytes=0-9" } }), { RALLYMATE_API_ORIGIN: "https://analyzer.example", RALLYMATE_API_KEY: "backend-test" });
    assert.equal(response.status, 206); assert.equal(response.headers.get("content-range"), "bytes 0-9/100");
  } finally { globalThis.fetch = original; }
});
