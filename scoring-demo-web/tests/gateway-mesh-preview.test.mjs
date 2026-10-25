import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import ts from "typescript";

const source = fs.readFileSync(new URL("../worker/gateway.ts", import.meta.url), "utf8");
const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const { proxyAnalysis } = await import(`data:text/javascript;base64,${Buffer.from(js).toString("base64")}`);
const base = "/v1/jobs/66883aeb-e0c3-4065-80fd-f67aa108b208/artifacts/";
const env = { RALLYMATE_API_ORIGIN: "http://127.0.0.1:8001", RALLYMATE_API_KEY: "fixture-key" };

test("mesh media streams authenticated byte ranges for exact supported artifacts", async t => {
  const names = ["sam3d-mesh.mp4", "sam3d-mesh-side.mp4", "sam3d-mesh-preview.jpg", "sam3d-mesh-manifest.json"];
  t.mock.method(globalThis, "fetch", async (url, init) => {
    assert.ok(names.some(name => String(url) === `http://127.0.0.1:8001${base}${name}`));
    assert.equal(init.headers.get("authorization"), "Bearer fixture-key");
    assert.equal(init.headers.get("range"), "bytes=2-4");
    return new Response(new Uint8Array([2, 3, 4]), { status: 206, headers: { "content-range": "bytes 2-4/10", "content-type": "video/mp4" } });
  });
  for (const name of names) {
    const response = await proxyAnalysis(new Request(`http://localhost${base}${name}`, { headers: { range: "bytes=2-4" } }), env);
    assert.equal(response.status, 206);
    assert.equal(response.headers.get("content-range"), "bytes 2-4/10");
    assert.deepEqual([...new Uint8Array(await response.arrayBuffer())], [2, 3, 4]);
    assert.equal(response.headers.get("authorization"), null);
  }
});

test("internal vertex buffers and write routes remain inaccessible", async t => {
  let calls = 0;
  t.mock.method(globalThis, "fetch", async () => { calls++; throw new Error("unexpected upstream"); });
  for (const name of ["sam3d-mesh.npz", "sam3d-mesh.mp4/extra", "sam3d-mesh-manifest.json.bak"]) {
    assert.equal((await proxyAnalysis(new Request(`http://localhost${base}${name}`), env)).status, 404);
  }
  for (const method of ["POST", "PUT", "DELETE"]) {
    assert.equal((await proxyAnalysis(new Request(`http://localhost${base}sam3d-mesh.mp4`, { method }), env)).status, 404);
  }
  assert.equal(calls, 0);
});

test("background mesh status uses its small authenticated endpoint", async t => {
  t.mock.method(globalThis, "fetch", async (url, init) => {
    assert.equal(String(url), "http://127.0.0.1:8001/v1/jobs/66883aeb-e0c3-4065-80fd-f67aa108b208/mesh-preview");
    assert.equal(init.headers.get("authorization"), "Bearer fixture-key");
    return Response.json({ job_id: "66883aeb-e0c3-4065-80fd-f67aa108b208", status: "pending", sam3d_mesh: null });
  });
  const response = await proxyAnalysis(new Request("http://localhost/v1/jobs/66883aeb-e0c3-4065-80fd-f67aa108b208/mesh-preview"), env);
  assert.equal(response.status, 200);
  assert.equal((await response.json()).status, "pending");
});

test("stored swing observations use an authenticated read-only endpoint", async t => {
  const path = "/v1/jobs/66883aeb-e0c3-4065-80fd-f67aa108b208/swing-kinematics";
  let calls = 0;
  t.mock.method(globalThis, "fetch", async (url, init) => {
    calls++;
    assert.equal(String(url), `http://127.0.0.1:8001${path}`);
    assert.equal(init.headers.get("authorization"), "Bearer fixture-key");
    return Response.json({ version: "sam3d-swing-kinematics-v1", status: "unavailable", score: null, episodes: [] });
  });
  const response = await proxyAnalysis(new Request(`http://localhost${path}`), env);
  assert.equal(response.status, 200);
  assert.equal((await response.json()).score, null);
  for (const method of ["POST", "PUT", "DELETE"]) {
    assert.equal((await proxyAnalysis(new Request(`http://localhost${path}`, { method }), env)).status, 404);
  }
  assert.equal(calls, 1);
});
