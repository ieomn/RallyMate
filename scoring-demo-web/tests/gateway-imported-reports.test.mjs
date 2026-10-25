import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import ts from "typescript";

const source = fs.readFileSync(new URL("../worker/gateway.ts", import.meta.url), "utf8");
const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const { proxyAnalysis } = await import(`data:text/javascript;base64,${Buffer.from(js).toString("base64")}`);
const job = "/v1/jobs/ceaf2f9e-dbe7-40cb-bf7d-08b3851da9bb";
const reports = ["analysis-report.html", "scoring-loop-report.html"];
const env = { RALLYMATE_API_ORIGIN: "http://127.0.0.1:8001", RALLYMATE_API_KEY: "test-internal-report-key" };
const html = "<!doctype html><title>Preserved report fixture</title>";
const htmlHeaders = {
  "content-type": "text/html; charset=utf-8", "content-length": String(Buffer.byteLength(html)),
  "content-disposition": 'inline; filename="saved-report.html"', etag: '"fixture-etag"',
  "last-modified": "Mon, 19 Oct 2026 00:00:00 GMT", "accept-ranges": "bytes",
};

for (const name of reports) {
  test(`${name} GET streams only the exact report through internal authentication`, async t => {
    const path = `${job}/artifacts/${name}?report_contract=current`;
    const upstream = new Response(html, { headers: {
      ...htmlHeaders, authorization: "Bearer must-not-leak", "set-cookie": "private=must-not-leak",
    } });
    t.mock.method(globalThis, "fetch", async (url, init) => {
      assert.equal(String(url), `http://127.0.0.1:8001${path}`);
      assert.equal(init.method, "GET"); assert.equal(init.redirect, "manual");
      assert.equal(init.headers.get("authorization"), "Bearer test-internal-report-key");
      assert.equal(init.headers.get("cookie"), null);
      return upstream;
    });
    const response = await proxyAnalysis(new Request(`http://localhost:8000${path}`, {
      headers: { authorization: "Bearer browser-value", cookie: "browser-cookie=value" },
    }), env);
    assert.equal(response.status, 200); assert.equal(response.body, upstream.body);
    for (const [key, value] of Object.entries(htmlHeaders)) assert.equal(response.headers.get(key), value);
    assert.equal(response.headers.get("cache-control"), "private, no-store");
    assert.equal(response.headers.get("x-content-type-options"), "nosniff");
    assert.equal(response.headers.get("authorization"), null); assert.equal(response.headers.get("set-cookie"), null);
    assert.equal(await response.text(), html);
  });

  test(`${name} HEAD uses upstream GET, cancels the body and retains full representation headers`, async t => {
    const path = `${job}/artifacts/${name}`;
    let cancelled = false;
    const body = new ReadableStream({ cancel() { cancelled = true; } });
    t.mock.method(globalThis, "fetch", async (url, init) => {
      assert.equal(String(url), `http://127.0.0.1:8001${path}`);
      assert.equal(init.method, "GET");
      assert.equal(init.headers.get("range"), null); assert.equal(init.headers.get("if-range"), null);
      assert.equal(init.headers.get("authorization"), "Bearer test-internal-report-key");
      return new Response(body, { headers: htmlHeaders });
    });
    const response = await proxyAnalysis(new Request(`http://localhost:8000${path}`, {
      method: "HEAD", headers: { range: "bytes=0-1", "if-range": '"fixture-etag"' },
    }), env);
    assert.equal(response.status, 200); assert.equal(response.body, null);
    assert.equal(await response.text(), ""); assert.equal(cancelled, true);
    for (const [key, value] of Object.entries(htmlHeaders)) assert.equal(response.headers.get(key), value);
    assert.equal(response.headers.get("cache-control"), "private, no-store");
  });
}

test("report HEAD preserves upstream errors and returns no body for rejected redirects or fetch failures", async t => {
  const path = `${job}/artifacts/analysis-report.html`;
  let cancelled = 0;
  const outcomes = [404, 302, "throw"];
  t.mock.method(globalThis, "fetch", async () => {
    const outcome = outcomes.shift();
    if (outcome === "throw") throw new Error("fixture failure");
    return new Response(new ReadableStream({ cancel() { cancelled++; } }), { status: outcome });
  });
  for (const status of [404, 502, 502]) {
    const response = await proxyAnalysis(new Request(`http://localhost:8000${path}`, { method: "HEAD" }), env);
    assert.equal(response.status, status); assert.equal(response.body, null);
  }
  assert.ok(cancelled >= 1);
  const notConfigured = await proxyAnalysis(new Request(`http://localhost:8000${path}`, { method: "HEAD" }), {});
  assert.equal(notConfigured.status, 503); assert.equal(notConfigured.body, null);
});

test("report HEAD retains the existing read deadline and caller cancellation", async t => {
  const caller = new AbortController();
  const deadline = new AbortController();
  t.mock.method(AbortSignal, "timeout", milliseconds => {
    assert.equal(milliseconds, 30000); return deadline.signal;
  });
  t.mock.method(globalThis, "fetch", async (_url, init) => {
    assert.equal(init.method, "GET"); assert.equal(init.signal.aborted, false);
    caller.abort(); assert.equal(init.signal.aborted, true);
    throw new Error("fixture caller cancellation");
  });
  const response = await proxyAnalysis(new Request(`http://localhost:8000${job}/artifacts/scoring-loop-report.html`, {
    method: "HEAD", signal: caller.signal,
  }), env);
  assert.equal(response.status, 502); assert.equal(response.body, null);
});

test("report allowlist rejects other artifact names, path suffixes, invalid job IDs and all writes", async t => {
  let calls = 0;
  t.mock.method(globalThis, "fetch", async () => { calls++; throw new Error("unexpected upstream call"); });
  for (const name of reports) {
    const path = `${job}/artifacts/${name}`;
    for (const method of ["POST", "PUT", "PATCH", "DELETE", "OPTIONS"]) {
      assert.equal((await proxyAnalysis(new Request(`http://localhost:8000${path}`, { method }), env)).status, 404);
    }
    for (const invalid of [`${path}/`, `${path}.bak`, `${path}/secret`, `${path}%2fsecret`, path.replace(name, name.toUpperCase())]) {
      for (const method of ["GET", "HEAD"]) assert.equal((await proxyAnalysis(new Request(`http://localhost:8000${invalid}`, { method }), env)).status, 404);
    }
  }
  for (const invalid of [
    `${job}/artifacts/custom.html`, `${job}/artifacts/ssh.json`, `${job}/artifacts/.env`,
    `${job}/artifacts/%2e%2e%2fanalysis-report.html`, "/v1/jobs/short/artifacts/analysis-report.html",
    `/v1/jobs/${"a".repeat(81)}/artifacts/scoring-loop-report.html`,
  ]) {
    for (const method of ["GET", "HEAD"]) assert.equal((await proxyAnalysis(new Request(`http://localhost:8000${invalid}`, { method }), env)).status, 404);
  }
  for (const path of [`${job}/original-video`, `${job}/demo-result`, `${job}/artifacts/summary.json`, "/health/live"]) {
    assert.equal((await proxyAnalysis(new Request(`http://localhost:8000${path}`, { method: "HEAD" }), env)).status, 404);
  }
  assert.equal(calls, 0);
});

test("existing GET read paths remain available and report origin validation is unchanged", async t => {
  let calls = 0;
  t.mock.method(globalThis, "fetch", async () => { calls++; return new Response("existing read fixture"); });
  const existing = [`${job}/artifacts/summary.json`, `${job}/artifacts/frames.jsonl`, `${job}/artifacts/indicator-features.jsonl`, `${job}/artifacts/annotated.mp4`, `${job}/artifacts/preview.jpg`, `${job}/demo-result`, "/health/live"];
  for (const path of existing) assert.equal((await proxyAnalysis(new Request(`http://localhost:8000${path}`), env)).status, 200);
  for (const origin of ["http://user:password@127.0.0.1:8001", "http://127.0.0.1:8001/private", "http://127.0.0.1:8001?credential=bad"]) {
    const response = await proxyAnalysis(new Request(`http://localhost:8000${job}/artifacts/analysis-report.html`), { ...env, RALLYMATE_API_ORIGIN: origin });
    assert.equal(response.status, 503);
  }
  assert.equal(calls, existing.length);
});
