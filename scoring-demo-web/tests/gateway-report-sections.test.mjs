import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import ts from "typescript";

const source = fs.readFileSync(new URL("../worker/gateway.ts", import.meta.url), "utf8");
const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const { proxyAnalysis } = await import(`data:text/javascript;base64,${Buffer.from(js).toString("base64")}`);
const env = { RALLYMATE_API_ORIGIN: "http://127.0.0.1:8001", RALLYMATE_API_KEY: "test-report-key", RALLYMATE_POSE_REQUEST_DIAGNOSTICS: "0" };
const job = "/v1/jobs/66883aeb-e0c3-4065-80fd-f67aa108b208";

for (const suffix of ["report-overview", "report-motion/motion-baseline-0001"]) {
  test(`bounded section ${suffix} reaches the authenticated upstream`, async t => {
    let calls = 0;
    const deadline = new AbortController();
    t.mock.method(AbortSignal, "timeout", ms => {
      assert.equal(ms, suffix === "report-overview" ? 65000 : 30000);
      return deadline.signal;
    });
    t.mock.method(globalThis, "fetch", async (url, init) => {
      calls++;
      assert.equal(String(url), `http://127.0.0.1:8001${job}/${suffix}`);
      assert.equal(init.headers.get("authorization"), "Bearer test-report-key");
      return Response.json({ status: "complete" });
    });
    const response = await proxyAnalysis(new Request(`http://localhost${job}/${suffix}`), env);
    assert.equal(response.status, 200);
    assert.equal(calls, 1);
    assert.equal(response.headers.get("cache-control"), "private, no-store");
    assert.deepEqual(await response.json(), { status: "complete" });
  });
}

test("motion section allowlist does not expose writes or arbitrary nested paths", async t => {
  t.mock.method(globalThis, "fetch", () => { throw new Error("must not dispatch"); });
  for (const [path, method] of [
    [`${job}/report-motion/a`, "POST"], [`${job}/report-motion/a`, "DELETE"],
    [`${job}/report-motion/a/extra`, "GET"], [`${job}/report-motion/`, "GET"],
    [`${job}/report-motion/${"a".repeat(161)}`, "GET"],
  ]) {
    const response = await proxyAnalysis(new Request(`http://localhost${path}`, { method }), env);
    assert.equal(response.status, 404, `${method} ${path}`);
    assert.deepEqual(await response.json(), { error: "route_not_allowed" });
  }
});

test("public gateway cannot invoke local executor maintenance", async t => {
  t.mock.method(globalThis, "fetch", () => { throw new Error("maintenance must never be proxied"); });
  for (const method of ["GET", "POST", "DELETE"]) {
    const response = await proxyAnalysis(new Request("https://public.example/internal/maintenance/stop-executor", { method }), env);
    assert.equal(response.status, 404);
    assert.deepEqual(await response.json(), { error: "route_not_allowed" });
  }
});
