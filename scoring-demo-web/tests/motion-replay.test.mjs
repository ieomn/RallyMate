import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import ts from "typescript";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

const modules = new Map();
function compiledUrl(file) {
  if (modules.has(file.href)) return modules.get(file.href);
  let js = ts.transpileModule(fs.readFileSync(file, "utf8"), { fileName: file.pathname,
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  js = js.replace(/from "([^"]+)"/g, (_match, name) => {
    const local = new URL(`${name}.ts`, file);
    return `from ${JSON.stringify(name.startsWith(".") ? compiledUrl(fs.existsSync(local) ? local : new URL(`${name}.tsx`, file)) : import.meta.resolve(name))}`;
  });
  const url = `data:text/javascript;base64,${Buffer.from(js).toString("base64")}`;
  modules.set(file.href, url); return url;
}
const { parseMotionReplay, parseMotionReplayList, motionReplayIsActive, motionReplayMediaPath, motionReplayPollDelay } = await import(compiledUrl(new URL("../app/lib/motion-replay.ts", import.meta.url)));
const { createApiClient } = await import(compiledUrl(new URL("../app/lib/api-client.ts", import.meta.url)));
const { MotionReplayCard, default: Panel } = await import(compiledUrl(new URL("../app/MotionReplayPanel.tsx", import.meta.url)));
const { proxyAnalysis } = await import(compiledUrl(new URL("../worker/gateway.ts", import.meta.url)));
const jobId = "job-replay-12345678", id = "replay-12345678";
const media = artifact => `/v1/motion-replays/${id}/media/${artifact}`;
const fixture = () => ({ id, job_id: jobId, generation_status: "provider_processing", quality_status: "pending_review",
  model: "MiniMax-Hailuo-2.3", resolution: "768P", duration_seconds: 6, source_window: { start_ms: 1400, duration_ms: 6000 },
  created_at: "2026-10-11T03:00:00Z", updated_at: "2026-10-11T03:01:00Z", attempts: 1, next_attempt_at: null,
  error: null, retry_allowed: false, media: { reference_video: media("reference.mp4"), first_frame: media("first.png"), middle_frame: media("middle.png"), last_frame: media("last.png"), generated_video: null, trajectory: media("trajectory.json"), manifest: media("manifest.json") } });
const configured = { baseUrl: "", jobsPath: "/v1/jobs", scorecardPath: "/api/scorecard", techniquesPath: "/v1/techniques" };

test("persisted task contracts preserve ready white replay while generation is pending", () => {
  const source = fixture(); source.media.generated_video = media("generated.mp4");
  const parsed = parseMotionReplay(source, jobId, id);
  assert.equal(parsed.media.reference_video, media("reference.mp4"));
  assert.equal(parsed.media.generated_video, null);
  assert.equal(motionReplayIsActive(parsed), true);
  assert.equal(parseMotionReplayList({ items: [source] }, jobId).items.length, 1);
  assert.throws(() => parseMotionReplay(source, "other-job"), /不匹配/);
  assert.throws(() => parseMotionReplay(source, jobId, "other-task"), /不匹配/);
  assert.throws(() => parseMotionReplayList({ items: [source] }, "other-job"), /不匹配/);
  assert.throws(() => parseMotionReplay({ ...source, generation_status: "invented_status" }, jobId), /不匹配/);
});

test("empty reports wait for delayed automatic enqueue for at most one minute", () => {
  assert.equal(motionReplayPollDelay([], 0), 5000);
  assert.equal(motionReplayPollDelay([], 55000), 5000);
  assert.equal(motionReplayPollDelay([], 59500), 500);
  assert.equal(motionReplayPollDelay([], 60000), null);
  assert.equal(motionReplayPollDelay([], 90000, 3), null);
  const active = parseMotionReplay(fixture());
  assert.equal(motionReplayPollDelay([active], 120000), 5000);
  assert.equal(motionReplayPollDelay([active], 120000, 3), 30000);
  assert.equal(motionReplayPollDelay([{ ...active, generation_status: "completed" }], 5000), null);
  assert.equal(motionReplayPollDelay([{ ...active, generation_status: "submit_unknown" }], 5000), null);
});

test("report identity and enabled state own separate component sessions", () => {
  const first = Panel({ jobId, enabled: true });
  const other = Panel({ jobId: "another-job-12345678", enabled: true });
  assert.equal(first.key, jobId);
  assert.equal(other.key, "another-job-12345678");
  assert.notEqual(first.key, other.key);
  assert.equal(Panel({ jobId, enabled: false }), null);
  assert.equal(Panel({ enabled: true }), null);
  // The disabled branch removes the state-owning child, so re-enabling mounts fresh state.
  assert.equal(Panel({ jobId, enabled: true }).key, jobId);
});

test("quality approval does not invent the identity of a reviewer", () => {
  const task = parseMotionReplay({ ...fixture(), quality_status: "approved" });
  const html = renderToStaticMarkup(createElement(MotionReplayCard, { task }));
  assert.match(html, /AI 生成 · 已核验/);
  assert.doesNotMatch(html, /人工核验|教练核验/);
});

test("media paths are task-bound and provider origins cannot become video URLs", () => {
  assert.equal(motionReplayMediaPath(`http://127.0.0.1:8001${media("reference.mp4")}`, id), media("reference.mp4"));
  for (const path of ["https://provider.example/generated.mp4", "/v1/motion-replays/other-task/media/generated.mp4", media("secret.json"), media("reference.mp4") + "?token=secret", "javascript:alert(1)", media("../reference.mp4")]) {
    assert.equal(motionReplayMediaPath(path, id), null, path);
  }
});

test("unknown submission blocks retry even if an inconsistent server flag permits it", () => {
  const task = parseMotionReplay({ ...fixture(), generation_status: "submit_unknown", retry_allowed: true });
  assert.equal(task.retry_allowed, false);
  assert.equal(motionReplayIsActive(task), false);
  const html = renderToStaticMarkup(createElement(MotionReplayCard, { task, onRetry() {} }));
  assert.match(html, /提交结果待确认/);
  assert.match(html, /当前不能直接重试/);
  assert.doesNotMatch(html, /重试生成/);
  assert.match(html, /reference\.mp4/);
});

test("provider failure keeps white video playable and only exposes permitted retry", () => {
  const task = parseMotionReplay({ ...fixture(), generation_status: "failed", retry_allowed: true, error: { code: "provider_failed", message: "生成服务暂不可用", retryable: true } });
  const html = renderToStaticMarkup(createElement(MotionReplayCard, { task, onRetry() {}, mediaBase: "https://gateway.example" }));
  assert.match(html, /src="https:\/\/gateway\.example\/v1\/motion-replays\/replay-12345678\/media\/reference\.mp4"/);
  assert.match(html, /生成服务暂不可用/);
  assert.match(html, /重试生成/);
  assert.equal((html.match(/<video/g) ?? []).length, 1);
  assert.doesNotMatch(html, /已人工核验/);
});

test("completed generated video is visibly pending review and does not replace the white replay", () => {
  const source = fixture(); source.generation_status = "completed"; source.media.generated_video = media("generated.mp4");
  const html = renderToStaticMarkup(createElement(MotionReplayCard, { task: parseMotionReplay(source) }));
  assert.equal((html.match(/<video/g) ?? []).length, 2);
  assert.match(html, /AI 生成 · 动作待核验/);
  assert.match(html, /不将生成画面作为测量或技术评分依据/);
  assert.match(html, /原视频 1\.4–7\.4 秒/);
  assert.doesNotMatch(html, /已人工核验|精准复现/);
  assert.equal(renderToStaticMarkup(createElement(Panel, { jobId, enabled: false })), "");
});

test("client separates persistence reads from explicit create and retry writes", async () => {
  const requests = [];
  const client = createApiClient(configured, async (url, init) => {
    requests.push({ url: String(url), init });
    return Response.json(String(url).endsWith("/motion-replays") && init.method === "GET" ? { items: [fixture()] } : fixture());
  });
  await client.listMotionReplays(jobId);
  await client.getMotionReplay(id);
  await client.createMotionReplay(jobId, { start_ms: 1400, duration_ms: 6000 });
  await client.retryMotionReplay(id);
  assert.deepEqual(requests.map(({ url, init }) => [url, init.method]), [
    [`/v1/jobs/${jobId}/motion-replays`, "GET"], [`/v1/motion-replays/${id}`, "GET"],
    [`/v1/jobs/${jobId}/motion-replays`, "POST"], [`/v1/motion-replays/${id}/retry`, "POST"],
  ]);
  assert.equal(requests[0].init.cache, "no-store");
  assert.ok(requests[2].init.headers["Idempotency-Key"]);
  assert.deepEqual(JSON.parse(requests[2].init.body), { start_ms: 1400, duration_ms: 6000 });
  assert.equal(requests[3].init.body, "{}");
});

test("gateway allows persisted replay endpoints and media ranges without opening arbitrary artifacts", async t => {
  const forwarded = [];
  t.mock.method(globalThis, "fetch", async (url, init) => {
    forwarded.push({ url: String(url), init });
    return new Response("video", { status: 206, headers: { "content-type": "video/mp4", "content-range": "bytes 0-4/5", "accept-ranges": "bytes" } });
  });
  const env = { RALLYMATE_API_ORIGIN: "http://127.0.0.1:8001" };
  for (const [method, path] of [["GET", `/v1/jobs/${jobId}/motion-replays`], ["POST", `/v1/jobs/${jobId}/motion-replays`], ["GET", `/v1/motion-replays/${id}`], ["POST", `/v1/motion-replays/${id}/retry`], ...["reference.mp4", "first.png", "middle.png", "last.png", "generated.mp4", "trajectory.json", "manifest.json"].map(name => ["GET", media(name)])]) {
    const response = await proxyAnalysis(new Request(`http://localhost${path}`, { method, headers: { range: "bytes=0-4", "Idempotency-Key": "creation-123" }, ...(method === "POST" ? { body: "{}" } : {}) }), env);
    assert.equal(response.status, 206, path);
    assert.equal(response.headers.get("content-range"), "bytes 0-4/5");
    assert.equal(forwarded.at(-1).init.headers.get("range"), "bytes=0-4");
    assert.equal(forwarded.at(-1).init.headers.get("idempotency-key"), "creation-123");
  }
  const before = forwarded.length;
  for (const [method, path] of [["GET", media("secret.json")], ["POST", media("reference.mp4")], ["DELETE", `/v1/motion-replays/${id}`], ["GET", `/v1/motion-replays/${id}/retry`]]) {
    assert.equal((await proxyAnalysis(new Request(`http://localhost${path}`, { method }), env)).status, 404);
  }
  assert.equal((await proxyAnalysis(new Request(`http://localhost/v1/jobs/${jobId}/motion-replays`, { method: "POST", headers: { origin: "https://outside.example" }, body: "{}" }), env)).status, 403);
  assert.equal(forwarded.length, before);
});
