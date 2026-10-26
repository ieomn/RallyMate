import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

const modules = new Map();
function compiledUrl(file) {
  // Keep source JSON imports shared with production, without a generated copy.
  if (file.pathname.endsWith(".json")) return `data:text/javascript;base64,${Buffer.from(`export default ${fs.readFileSync(file, "utf8")}`).toString("base64")}`;
  if (modules.has(file.href)) return modules.get(file.href);
  let js = ts.transpileModule(fs.readFileSync(file, "utf8"), {
    fileName: file.pathname,
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  js = js.replace(/from "([^"]+)"/g, (_match, name) => `from ${JSON.stringify(
    name.startsWith(".") ? compiledUrl(new URL(name.endsWith(".json") ? name : `${name}.ts`, file)) : import.meta.resolve(name),
  )}`);
  const url = `data:text/javascript;base64,${Buffer.from(js).toString("base64")}`;
  modules.set(file.href, url);
  return url;
}

const { default: TrainingReplayPanel, originalReplayHash } = await import(compiledUrl(new URL("../app/TrainingReplayPanel.tsx", import.meta.url)));
const { createApiClient } = await import(compiledUrl(new URL("../app/lib/api-client.ts", import.meta.url)));
const { proxyAnalysis } = await import(compiledUrl(new URL("../worker/gateway.ts", import.meta.url)));
const env = { RALLYMATE_API_ORIGIN: "http://127.0.0.1:8001", RALLYMATE_API_KEY: "test-key" };

test("original replay remains visible with the job-scoped calibration target and no retired controls", () => {
  const html = renderToStaticMarkup(createElement(TrainingReplayPanel, { jobId: "job-original-123" },
    createElement("video", { src: "/source.mp4", "data-replay-role": "original", controls: true })));
  assert.match(html, /id="evidence"[^>]*data-job-id="job-original-123"/);
  assert.match(html, /原视频 · 动作识别/);
  assert.match(html, /<div id="replay-view-original"><video[^>]*data-replay-role="original"/);
  assert.doesNotMatch(html, /hidden|tablist|tabpanel|白模|AI|创建|重试|replay-view-motion/);
});

test("retired replay bookmarks resolve to the original while unrelated sections are untouched", () => {
  for (const hash of ["#evidence", "#motion-replay", "#skeleton-replay"]) assert.equal(originalReplayHash(hash), "#evidence");
  for (const hash of ["", "#court", "#scoreboard", "#recommendations", "#coach", "#motion-replay-extra"]) assert.equal(originalReplayHash(hash), null);
});

test("frontend client no longer exposes replay generation, listing, polling or retrying", () => {
  const client = createApiClient({ baseUrl: "", jobsPath: "/v1/jobs", scorecardPath: "/api/scorecard", techniquesPath: "/v1/techniques" },
    () => assert.fail("constructing the original-only client must not issue requests"));
  for (const key of ["listMotionReplays", "createMotionReplay", "getMotionReplay", "retryMotionReplay"]) assert.equal(key in client, false);
  for (const key of ["getJob", "getDemoResult", "getTrajectory", "getPosePreview"]) assert.equal(typeof client[key], "function");
});

test("retired replay routes reject reads, creation, retries and media before reaching upstream", async t => {
  t.mock.method(globalThis, "fetch", () => assert.fail("retired replay request reached upstream"));
  const routes = [
    ["GET", "/v1/jobs/job-original-123/motion-replays"],
    ["POST", "/v1/jobs/job-original-123/motion-replays"],
    ["GET", "/v1/motion-replays/replay-original-123"],
    ["POST", "/v1/motion-replays/replay-original-123/retry"],
    ["GET", "/v1/motion-replays/replay-original-123/media/reference.mp4"],
    ["GET", "/v1/motion-replays/replay-original-123/media/generated.mp4"],
  ];
  for (const [method, path] of routes) {
    const response = await proxyAnalysis(new Request(`http://localhost${path}`, { method }), env);
    assert.equal(response.status, 404, path);
    assert.deepEqual(await response.json(), { error: "route_not_allowed" });
  }
});

test("original video range reads and pose evidence continue through the gateway", async t => {
  const requests = [];
  t.mock.method(globalThis, "fetch", async (url, init) => {
    requests.push({ url: String(url), range: init.headers.get("range") });
    return new Response("source-bytes", { status: 206, headers: { "content-range": "bytes 0-11/100", "accept-ranges": "bytes" } });
  });
  for (const suffix of ["artifacts/annotated.mp4", "pose-preview?start_ms=0&end_ms=1000", "trajectory"]) {
    const response = await proxyAnalysis(new Request(`http://localhost/v1/jobs/job-original-123/${suffix}`, { headers: { range: "bytes=0-11" } }), env);
    assert.equal(response.status, 206);
    assert.equal(response.headers.get("content-range"), "bytes 0-11/100");
    assert.equal(await response.text(), "source-bytes");
  }
  assert.equal(requests.length, 3);
  assert.ok(requests.every(item => item.url.startsWith("http://127.0.0.1:8001/v1/jobs/job-original-123/") && item.range === "bytes=0-11"));
});
