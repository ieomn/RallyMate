import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import ts from "typescript";

const source = fs.readFileSync(new URL("../worker/gateway.ts", import.meta.url), "utf8");
const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const { proxyAnalysis } = await import(`data:text/javascript;base64,${Buffer.from(js).toString("base64")}`);
const env = {
  RALLYMATE_API_ORIGIN: "http://127.0.0.1:8001",
  RALLYMATE_API_KEY: "internal-test-key",
  RALLYMATE_POSE_REQUEST_DIAGNOSTICS: "0",
};
const jobPath = "/v1/jobs/66883aeb-e0c3-4065-80fd-f67aa108b208";
const candidatePath = `${jobPath}/foot-crossing-candidates`;

test("candidate GET preserves its expected artifact SHA query and existing streaming protections", async t => {
  const checksum = "abcdef0123456789".repeat(4);
  const path = `${candidatePath}?expected_artifact_sha256=${checksum}`;
  const deadline = new AbortController();
  t.mock.method(AbortSignal, "timeout", ms => { assert.equal(ms, 30000); return deadline.signal; });
  const upstream = Response.json({ status: "available", artifact_sha256: checksum });
  let calls = 0;
  t.mock.method(globalThis, "fetch", async (url, init) => {
    calls++;
    assert.equal(String(url), `http://127.0.0.1:8001${path}`);
    assert.equal(init.method, "GET");
    assert.equal(init.redirect, "manual");
    assert.equal(init.signal.aborted, false);
    assert.equal(init.headers.get("authorization"), "Bearer internal-test-key");
    return upstream;
  });
  const response = await proxyAnalysis(new Request(`http://localhost${path}`), env);
  assert.equal(calls, 1);
  assert.equal(response.status, 200);
  assert.equal(response.body, upstream.body);
  assert.equal(response.headers.get("cache-control"), "private, no-store");
  assert.equal((await response.json()).artifact_sha256, checksum);
});

for (const method of ["POST", "PUT", "PATCH", "DELETE", "HEAD"]) {
  test(`candidate ${method} is rejected before upstream dispatch`, async t => {
    let calls = 0;
    t.mock.method(globalThis, "fetch", async () => { calls++; throw new Error("unexpected upstream call"); });
    const response = await proxyAnalysis(new Request(`http://localhost${candidatePath}`, { method }), env);
    assert.equal(response.status, 404);
    assert.deepEqual(await response.json(), { error: "route_not_allowed" });
    assert.equal(calls, 0);
  });
}

for (const path of [
  `${candidatePath}/`,
  `${candidatePath}/extra`,
  `${candidatePath}.json`,
  `${jobPath}/artifacts/foot-crossing-candidates.json`,
  `${jobPath}/artifacts/other.json`,
  `${jobPath}/foot-crossing-candidates%2f..%2fsummary.json`,
  "/v1/jobs/target%2f..%2f66883aeb-e0c3-4065-80fd-f67aa108b208/foot-crossing-candidates",
]) {
  test(`unapproved candidate path is rejected: ${path}`, async t => {
    let calls = 0;
    t.mock.method(globalThis, "fetch", async () => { calls++; throw new Error("unexpected upstream call"); });
    const response = await proxyAnalysis(new Request(`http://localhost${path}`), env);
    assert.equal(response.status, 404);
    assert.deepEqual(await response.json(), { error: "route_not_allowed" });
    assert.equal(calls, 0);
  });
}
