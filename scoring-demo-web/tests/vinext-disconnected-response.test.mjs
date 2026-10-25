import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import http from "node:http";
import { once } from "node:events";
import { gunzipSync } from "node:zlib";
import { patchInstalledVinext, patchedVinextSource, VINEXT_VERSION } from "../scripts/patch-vinext-disconnected-response.mjs";

patchInstalledVinext();
const { sendWebResponse } = await import("vinext/server/prod-server");

async function exchange(t, headers = {}) {
  let receive;
  const received = new Promise(resolve => { receive = resolve; });
  const server = http.createServer((req, res) => receive({ req, res }));
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  const request = http.get({ hostname: "127.0.0.1", port: server.address().port, headers });
  const errors = [];
  request.on("error", error => errors.push(error));
  t.after(async () => {
    request.destroy();
    server.closeAllConnections();
    await new Promise(resolve => server.close(resolve));
    assert.ok(errors.every(error => error.code === "ECONNRESET"), "unexpected client transport error");
  });
  return { request, ...await received };
}

function cancellableResponse() {
  let cancellations = 0;
  return {
    response: new Response(new ReadableStream({ cancel() { cancellations++; } })),
    count: () => cancellations,
  };
}

test("the pinned patch is repeatable and rejects upstream version or content drift", () => {
  const patched = fs.readFileSync(new URL(import.meta.resolve("vinext/server/prod-server")), "utf8");
  assert.equal(patchedVinextSource(patched, VINEXT_VERSION), patched);
  assert.throws(() => patchedVinextSource(patched, "1.0.0-beta.3"), /requires/);
  assert.throws(() => patchedVinextSource(patched + "\n", VINEXT_VERSION), /differs/);
  assert.equal(patchInstalledVinext(), false);
});

test("a real client disconnect before send cancels the response body without piping", async t => {
  const { request, req, res } = await exchange(t);
  const closed = once(res, "close");
  request.destroy();
  await closed;
  assert.equal(res.destroyed, true);
  const body = cancellableResponse();
  await assert.doesNotReject(sendWebResponse(body.response, req, res, true));
  assert.equal(body.count(), 1);
  assert.equal(res.headersSent, false);
});

test("an already ended Node response cancels a late upstream body", async t => {
  const { request, req, res } = await exchange(t);
  request.on("response", response => response.resume());
  res.end("already complete");
  assert.equal(res.writableEnded, true);
  const body = cancellableResponse();
  await sendWebResponse(body.response, req, res, false);
  assert.equal(body.count(), 1);
});

for (const compressed of [false, true]) {
  test(`a connected request retains streamed status, headers and ${compressed ? "gzip" : "plain"} body`, async t => {
    const { request, req, res } = await exchange(t, compressed ? { "accept-encoding": "gzip" } : {});
    const incoming = once(request, "response");
    const body = new ReadableStream({ start(controller) {
      controller.enqueue(new TextEncoder().encode("first chunk · "));
      controller.enqueue(new TextEncoder().encode("第二段"));
      controller.close();
    } });
    await sendWebResponse(new Response(body, { status: 206, headers: { "content-type": "text/plain", "x-test-stream": "preserved" } }), req, res, compressed);
    const [response] = await incoming;
    const chunks = [];
    for await (const chunk of response) chunks.push(chunk);
    const buffer = Buffer.concat(chunks);
    assert.equal(response.statusCode, 206);
    assert.equal(response.headers["x-test-stream"], "preserved");
    assert.equal(compressed ? gunzipSync(buffer).toString() : buffer.toString(), "first chunk · 第二段");
    assert.equal(response.headers["content-encoding"], compressed ? "gzip" : undefined);
  });
}

test("a genuine connected-response error still rejects instead of being swallowed", async t => {
  const { req, res } = await exchange(t);
  const body = cancellableResponse();
  await assert.rejects(sendWebResponse({ status: 9999, statusText: "invalid", headers: new Headers(), body: body.response.body }, req, res, false), /status code/i);
  assert.equal(body.count(), 0);
  await body.response.body.cancel();
});
