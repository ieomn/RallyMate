import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import ts from "typescript";

const source = fs.readFileSync(new URL("../worker/gateway.ts", import.meta.url), "utf8");
const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const { proxyAnalysis } = await import(`data:text/javascript;base64,${Buffer.from(js).toString("base64")}`);
const path = "/v1/jobs/66883aeb-e0c3-4065-80fd-f67aa108b208/original-video";
const env = { RALLYMATE_API_ORIGIN: "http://127.0.0.1:8001", RALLYMATE_API_KEY: "test-private-key" };

test("original video GET preserves byte-range requests, streamed partial bodies and validators", async t => {
  const upstream = new Response(new Uint8Array([1, 2, 3]), { status: 206, headers: {
    "content-type": "video/mp4", "content-length": "3", "content-range": "bytes 2-4/10",
    "accept-ranges": "bytes", etag: '"source-hash"', "last-modified": "Mon, 19 Oct 2026 00:00:00 GMT",
  } });
  t.mock.method(globalThis, "fetch", async (url, init) => {
    assert.equal(String(url), `http://127.0.0.1:8001${path}`);
    assert.equal(init.method, "GET"); assert.equal(init.redirect, "manual");
    assert.equal(init.headers.get("range"), "bytes=2-4");
    assert.equal(init.headers.get("if-range"), '"source-hash"');
    assert.equal(init.headers.get("authorization"), "Bearer test-private-key");
    return upstream;
  });
  const response = await proxyAnalysis(new Request(`http://localhost${path}`, { headers: { range: "bytes=2-4", "if-range": '"source-hash"' } }), env);
  assert.equal(response.status, 206); assert.equal(response.body, upstream.body);
  for (const name of ["content-type", "content-length", "content-range", "accept-ranges", "etag", "last-modified"]) assert.equal(response.headers.get(name), upstream.headers.get(name));
  assert.equal(response.headers.get("authorization"), null);
  assert.equal(response.headers.get("cache-control"), "private, no-store");
  assert.deepEqual([...new Uint8Array(await response.arrayBuffer())], [1, 2, 3]);
});

test("unsatisfied original video ranges retain the upstream size", async t => {
  t.mock.method(globalThis, "fetch", async () => new Response(null, { status: 416, headers: { "content-range": "bytes */10" } }));
  const response = await proxyAnalysis(new Request(`http://localhost${path}`, { headers: { range: "bytes=30-" } }), env);
  assert.equal(response.status, 416); assert.equal(response.headers.get("content-range"), "bytes */10");
});

test("original video permits only its exact GET endpoint", async t => {
  let calls = 0;
  t.mock.method(globalThis, "fetch", async () => { calls++; throw new Error("unexpected upstream call"); });
  for (const method of ["HEAD", "POST", "PUT", "PATCH", "DELETE"]) {
    assert.equal((await proxyAnalysis(new Request(`http://localhost${path}`, { method }), env)).status, 404);
  }
  for (const invalid of [`${path}/`, `${path}/extra`, `${path}.mp4`, path.replace("original-video", "artifacts/original.mp4"), path.replace("original-video", "original-video%2f..%2fsecret")]) {
    assert.equal((await proxyAnalysis(new Request(`http://localhost${invalid}`), env)).status, 404);
  }
  assert.equal(calls, 0);
});
