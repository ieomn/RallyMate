import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import ts from "typescript";

const modules = new Map();
function compiledUrl(file) {
  // Keep source JSON imports shared with production, without a generated copy.
  if (file.pathname.endsWith(".json")) return `data:text/javascript;base64,${Buffer.from(`export default ${fs.readFileSync(file, "utf8")}`).toString("base64")}`;
  if (modules.has(file.href)) return modules.get(file.href);
  let js = ts.transpileModule(fs.readFileSync(file, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  js = js.replace(/from "([^"]+)"/g, (_match, name) => `from ${JSON.stringify(
    name.startsWith(".") ? compiledUrl(new URL(name.endsWith(".json") ? name : `${name}.ts`, file)) : import.meta.resolve(name),
  )}`);
  const url = `data:text/javascript;base64,${Buffer.from(js).toString("base64")}`;
  modules.set(file.href, url);
  return url;
}
const { createApiClient } = await import(compiledUrl(new URL("../app/lib/api-client.ts", import.meta.url)));
const { getRequestDiagnostics } = await import(compiledUrl(new URL("../app/lib/request-diagnostics.ts", import.meta.url)));
const { MAX_REPORT_WIRE_BYTES } = await import(compiledUrl(new URL("../app/lib/report-limits.ts", import.meta.url)));

test("report Content-Length is rejected before reading an oversized response", async () => {
  let cancelled = false;
  const body = new ReadableStream({ cancel() { cancelled = true; } });
  const response = new Response(body, { headers: { "Content-Length": String(MAX_REPORT_WIRE_BYTES + 1) } });
  const client = createApiClient({}, async () => response);
  await assert.rejects(client.getDemoResult("job-12345678"), error => error.status === 422 && /读取上限/.test(error.message));
  assert.equal(cancelled, true);
});

test("report streamed bytes cannot bypass the bound with a missing or forged length", async () => {
  // Inject a small wire budget into this isolated test module to exercise the
  // same streaming reader without allocating a 128 MiB synthetic response.
  const local = new Map();
  function limitedUrl(file) {
    if (file.pathname.endsWith(".json")) return `data:text/javascript;base64,${Buffer.from(`export default ${fs.readFileSync(file, "utf8")}`).toString("base64")}`;
    if (local.has(file.href)) return local.get(file.href);
    let source = fs.readFileSync(file, "utf8");
    if (file.pathname.endsWith("/report-limits.ts")) source = source.replace("128 * 1024 * 1024", "32");
    let js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
    js = js.replace(/from "([^"]+)"/g, (_match, name) => `from ${JSON.stringify(name.startsWith(".") ? limitedUrl(new URL(name.endsWith(".json") ? name : `${name}.ts`, file)) : import.meta.resolve(name))}`);
    const url = `data:text/javascript;base64,${Buffer.from(js).toString("base64")}`;
    local.set(file.href, url); return url;
  }
  const limited = await import(limitedUrl(new URL("../app/lib/api-client.ts", import.meta.url)));
  for (const headers of [{}, { "Content-Length": "1" }]) {
    let cancelled = false;
    const body = new ReadableStream({ start(controller) { controller.enqueue(new Uint8Array(33).fill(32)); }, cancel() { cancelled = true; } });
    const client = limited.createApiClient({}, async () => new Response(body, { headers }));
    await assert.rejects(client.getDemoResult("job-12345678"), error => error.status === 422 && /读取上限/.test(error.message));
    assert.equal(cancelled, true);
  }
});
const configured = { baseUrl: "", jobsPath: "/v1/jobs", scorecardPath: "/api/scorecard", techniquesPath: "/v1/techniques" };
const preparing = (job_id = "job-12345678", retry_after_ms = 1000) => ({ job_id, status: "preparing", report_delivery: "deferred-v1", retry_after_ms });
function fakeReportClock() {
  let now = 0; const waits = [];
  return { waits, now: () => now, wait: async (ms, signal) => { signal?.throwIfAborted(); waits.push(ms); now += ms; } };
}

test("only explicit matching HTTP 202 report preparation polls, clamps retries and preserves query negotiation", async () => {
  const timing = fakeReportClock(), requests = []; let notices = 0;
  const client = createApiClient(configured, async (url, init) => {
    requests.push({ url, signal: init.signal });
    return requests.length <= 2 ? Response.json(preparing("job-12345678", requests.length === 1 ? 0 : 9000), { status: 202 }) : Response.json(nativeReport());
  }, timing);
  const result = await client.getDemoResult("job-12345678", { onPrepare: () => notices++ });
  assert.equal(result.status, "ready"); assert.equal(notices, 2); assert.deepEqual(timing.waits, [1000, 3000]);
  assert.equal(requests.length, 3);
  for (const { url, signal } of requests) {
    const query = new URL(url, "https://local.invalid").searchParams;
    assert.deepEqual(query.getAll("report_delivery"), ["deferred-v1"]);
    assert.deepEqual(query.getAll("report_transport"), ["model-proof-pool-v1"]);
    assert.deepEqual(query.getAll("report_contract"), ["current"]);
    assert.ok(signal instanceof AbortSignal);
  }
});

test("preparation cannot be forged by a wrong job, missing marker, wrong HTTP status or invalid retry", async () => {
  const cases = [
    [202, { ...preparing(), job_id: "other" }], [202, { ...preparing(), report_delivery: undefined }],
    [202, { ...preparing(), status: "ready" }], [202, { ...preparing(), status: "unknown" }],
    [202, { ...preparing(), retry_after_ms: "1000" }], [202, { ...preparing(), retry_after_ms: -1 }],
    [200, preparing()], [201, nativeReport()],
  ];
  for (const [status, body] of cases) {
    const clock = fakeReportClock(); let calls = 0;
    const client = createApiClient(configured, async () => { calls++; return Response.json(body, { status }); }, clock);
    await assert.rejects(client.getDemoResult("job-12345678"), error => error.status === 422);
    assert.equal(calls, 1); assert.deepEqual(clock.waits, []);
  }
  for (const final of [{ ...nativeReport(), job_id: "other" }, { ...nativeReport(), status: "cancelled" }]) {
    let calls = 0;
    const client = createApiClient(configured, async () => ++calls === 1 ? Response.json(preparing(), { status: 202 }) : Response.json(final), fakeReportClock());
    await assert.rejects(client.getDemoResult("job-12345678"), error => error.status === 422);
    assert.equal(calls, 2);
  }
});

test("report preparation has one 120-second overall deadline without a final extra request", async () => {
  const clock = fakeReportClock(); let calls = 0;
  const client = createApiClient(configured, async () => { calls++; return Response.json(preparing(), { status: 202 }); }, clock);
  await assert.rejects(client.getDemoResult("job-12345678"), error => error.status === 425 && /等待时限/.test(error.message));
  assert.equal(clock.now(), 120000); assert.equal(calls, 120); assert.equal(clock.waits.length, 120);
});

test("aborting report preparation clears the real wait timer and prevents all later fetches", async t => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const controller = new AbortController(); let calls = 0, notify;
  const noticed = new Promise(resolve => { notify = resolve; });
  const client = createApiClient(configured, async () => { calls++; return Response.json(preparing(), { status: 202 }); });
  const pending = client.getDemoResult("job-12345678", { signal: controller.signal, onPrepare: notify });
  await noticed;
  controller.abort(new DOMException("User stopped", "AbortError"));
  await assert.rejects(pending, error => error.name === "AbortError");
  t.mock.timers.tick(120000); await Promise.resolve();
  assert.equal(calls, 1);
});

test("pre-abort and abort during a report request never schedule preparation polling", async () => {
  const stopped = new AbortController(); stopped.abort(); let calls = 0;
  await assert.rejects(createApiClient(configured, async () => { calls++; return Response.json(nativeReport()); }).getDemoResult("job-12345678", { signal: stopped.signal }), error => error.name === "AbortError");
  assert.equal(calls, 0);
  const active = new AbortController(); let started;
  const begun = new Promise(resolve => { started = resolve; });
  const client = createApiClient(configured, async (_url, init) => {
    calls++; started(); return new Promise((_resolve, reject) => init.signal.addEventListener("abort", () => reject(init.signal.reason), { once: true }));
  });
  const pending = client.getDemoResult("job-12345678", { signal: active.signal });
  await begun; active.abort(); await assert.rejects(pending, error => error.name === "AbortError"); assert.equal(calls, 1);
});

test("ordinary GETs do not acquire report preparation polling or delivery negotiation", async () => {
  const urls = [], timing = fakeReportClock();
  const client = createApiClient(configured, async url => { urls.push(String(url)); return Response.json(preparing(), { status: 202 }); }, timing);
  await client.getJob("job-12345678"); await client.getMeta();
  assert.equal(urls.length, 2); assert.deepEqual(timing.waits, []);
  assert.ok(urls.every(url => !url.includes("report_delivery")));
});

test("cancel uses an explicit POST and accepts only a matching confirmed job response", async () => {
  const id = "cancel-job-123", requests = [];
  for (const job of [{ id, status: "running", cancel_requested: true }, { id, status: "cancelled", cancelled_at: "2026-10-18T10:00:00Z" }, { id, status: "succeeded", summary: { preserved: true } }]) {
    const client = createApiClient(configured, async (url, init) => { requests.push({ url, init }); return Response.json(job); });
    assert.deepEqual(await client.cancelJob(id), job);
  }
  assert.ok(requests.every(({ url, init }) => url === `/v1/jobs/${id}/cancel` && init.method === "POST" && init.cache === "no-store" && init.headers["X-Request-ID"] && init.signal && !init.body));
  for (const job of [{ id: "other-job", status: "cancelled" }, { id, status: "running", cancel_requested: false }, { id, status: "unknown", cancel_requested: true }]) {
    await assert.rejects(createApiClient(configured, async () => Response.json(job)).cancelJob(id), /取消响应未确认当前任务/);
  }
  await assert.rejects(createApiClient(configured, async () => Response.json({ detail: "Job not found" }, { status: 404 })).cancelJob(id), error => error.status === 404);
});

function nativeReport() {
  const motion = { schema_version: "1.1.0", analysis_version: "stroke-motion-analysis-v2.0.0",
    temporal_backend: { kind: "deterministic_rules" }, contact_confirmed: false,
    families: { baseline: { episodes: [{ episode_id: "swing-one", temporal_recognition: { anchor_is_contact: false },
      rotation_analysis: { schema_version: "1.1.0", windows: [{ start_ms: 200, end_ms: 500 }] }, metrics: { elbow_extension_deg: null } }] } } };
  const footwork = { schema_version: "1.1.0", review_version: "footwork-independent-measurements-v1.1.0",
    measurement_summary: { partial_indicator_count: 1 }, episodes: [{ event_id: "step-one", indicators: [{
      indicator_id: "FS01-M02", feature_status: "unavailable", features: [], measurement_status: "partial",
      measurements: [{ feature_name: "stance_width_body", status: "measured", value: .7 },
        { feature_name: "left_knee_flexion_deg", status: "unavailable", value: null }],
    }] }] };
  return { job_id: "job-12345678", status: "ready", footwork_review: footwork,
    action_recognition: { motion_analysis: motion }, summary: { action_recognition: { motion_analysis: motion } },
    analysis_report: { version: "training-report-v1.0.0", evidence: { technical_grade: null } },
    source_aligned_assessment: { technical_grade: null, status: "partial" },
    measurement_contract: { contract_version: "isotropic-frame-long-edge-v1.1.0" } };
}

test("only the three report reads opt into current and preserve complete 1.1 evidence", async () => {
  const requests = [], source = nativeReport();
  const client = createApiClient(configured, async (url, init) => {
    requests.push({ url: String(url), init });
    assert.deepEqual(new URL(url, "https://browser.test").searchParams.getAll("report_contract"), ["current"]);
    return Response.json(source);
  });
  const job = await client.getJob(source.job_id);
  const demo = await client.getDemoResult(source.job_id);
  const assessment = await client.getTechniqueAssessment(source.job_id);
  assert.deepEqual(requests.map(item => item.url), [
    "/v1/jobs/job-12345678?report_contract=current",
    "/v1/jobs/job-12345678/demo-result?report_contract=current&report_transport=model-proof-pool-v1&report_delivery=deferred-v1",
    "/v1/jobs/job-12345678/technique-assessment?report_contract=current",
  ]);
  assert.ok(requests.every(({ init }) => init.method === "GET" && init.cache === "no-store" && init.headers["X-Request-ID"]));
  assert.deepEqual(job.summary, source.summary);
  for (const result of [demo, assessment]) {
    assert.deepEqual(result.action_recognition, source.action_recognition);
    assert.deepEqual(result.footwork_review, source.footwork_review);
    assert.deepEqual(result.source_aligned_assessment, source.source_aligned_assessment);
    assert.deepEqual(result.analysis_report, source.analysis_report);
  }
});

test("private absolute result links stay on the configured gateway and retain existing queries", async () => {
  const requests = [];
  const client = createApiClient({ ...configured, baseUrl: "https://preview.example/gateway" }, async url => {
    requests.push(String(url)); return Response.json(nativeReport());
  });
  for (const [method, suffix] of [["getDemoResult", "demo-result"], ["getTechniqueAssessment", "technique-assessment"]]) {
    await client[method]("job-12345678", { endpoint: `http://127.0.0.1:8001/v1/jobs/job-12345678/${suffix}?locale=zh%20CN&tag=one&tag=two&report_contract=legacy-v1&report_contract=current` });
    const url = new URL(requests.at(-1));
    assert.equal(url.origin, "https://preview.example");
    assert.equal(url.pathname, `/gateway/v1/jobs/job-12345678/${suffix}`);
    assert.equal(url.searchParams.get("locale"), "zh CN");
    assert.deepEqual(url.searchParams.getAll("tag"), ["one", "two"]);
    assert.deepEqual(url.searchParams.getAll("report_contract"), ["current"]);
  }
});

test("relative custom endpoints and configured job paths keep queries before fragments", async () => {
  const requests = [];
  const client = createApiClient({ ...configured, baseUrl: "/analysis", jobsPath: "/v1/custom-jobs/?tenant=trial&report_contract=legacy-v1" }, async url => {
    requests.push(String(url)); return Response.json(nativeReport());
  });
  await client.getJob("job with/slash");
  await client.getDemoResult("job with/slash");
  await client.getTechniqueAssessment("job with/slash");
  assert.deepEqual(requests, ["", "/demo-result", "/technique-assessment"].map(suffix =>
    `/analysis/v1/custom-jobs/job%20with%2Fslash${suffix}?tenant=trial&report_contract=current${suffix === "/demo-result" ? "&report_transport=model-proof-pool-v1&report_delivery=deferred-v1" : ""}`));
  const endpoint = "/v1/jobs/custom/demo-result?encoded=a%2Bb&report_contract=current&report_contract=legacy-v1#details";
  for (let repeat = 0; repeat < 2; repeat++) {
    await client.getDemoResult("ignored", { endpoint });
    const url = new URL(requests.at(-1), "https://preview.example");
    assert.equal(url.pathname, "/analysis/v1/jobs/custom/demo-result");
    assert.equal(url.searchParams.get("encoded"), "a+b");
    assert.deepEqual(url.searchParams.getAll("report_contract"), ["current"]);
    assert.equal(url.hash, "#details");
  }
});

test("invalid analyzer endpoints are rejected before fetch even with a current query", async () => {
  let calls = 0;
  const client = createApiClient({ ...configured, baseUrl: "https://preview.example" }, async () => {
    calls++; return Response.json(nativeReport());
  });
  for (const endpoint of ["http://127.0.0.1:8001/private?report_contract=current", "https://private.example/v1/../secret", "https://private.example/v10/jobs/example"]) {
    for (const method of ["getDemoResult", "getTechniqueAssessment"]) {
      await assert.rejects(async () => client[method]("job-12345678", { endpoint }), error => error.status === 502 && /无效/.test(error.message));
    }
  }
  assert.equal(calls, 0);
});

test("metadata, catalogs, pose, trajectory and technical review export do not acquire a report contract", async () => {
  const requests = [];
  const client = createApiClient(configured, async url => {
    requests.push(String(url));
    return Response.json(String(url).includes("/pose-preview?") ? {
      schema_version: "1.0.0", result_kind: "observed_pose_playback", job_id: "job-12345678",
      coordinate_space: "normalized_frame_0_1", joint_schema: "halpe26_named", frames: [],
      source: { requested_start_ms: 10, requested_end_ms: 10010, timestamp_semantics: "source_frame_timestamp_ms" },
    } : {});
  });
  await client.getMeta();
  await client.getTechniques();
  await client.getCapabilities();
  await client.getPosePreview("job-12345678", { startMs: 10 });
  await client.getTrajectory("job-12345678", { endpoint: "/v1/jobs/job-12345678/trajectory?existing=yes", sampleLimit: 20 });
  await client.exportTechnicalReview("job-12345678");
  assert.ok(requests.every(url => !new URL(url, "https://browser.test").searchParams.has("report_contract")));
  assert.equal(requests.at(-2), "/v1/jobs/job-12345678/trajectory?existing=yes&sample_limit=20");
});


test("read diagnostics retain four success stages without URL, response data or upstream IDs", async () => {
  const rows = []; let sentId;
  const client = createApiClient(configured, async (_url, init) => {
    sentId = init.headers["X-Request-ID"];
    return Response.json({ ...nativeReport(), private_fixture: "BODY_SECRET" }, { headers: { "X-Request-ID": "UPSTREAM_SECRET" } });
  }, undefined, row => rows.push(row));
  const result = await client.getDemoResult("job-12345678", { endpoint: "/v1/jobs/job-12345678/demo-result?token=QUERY_SECRET" });
  assert.equal(result.status, "ready");
  assert.deepEqual(rows.map(row => row.stage), ["headers", "body", "json", "decode"]);
  assert.deepEqual(rows.map(row => row.bodyComplete), [false, true, true, true]);
  assert.ok(rows.every(row => row.operation === "report" && row.outcome === "success" && row.category === null && row.requestId === sentId && row.httpStatus === 200 && Object.isFrozen(row)));
  assert.ok(rows.every(row => Number.isFinite(Date.parse(row.startedAt)) && Number.isFinite(row.elapsedMs) && row.elapsedMs >= 0));
  const encoded = JSON.stringify(rows);
  for (const forbidden of ["QUERY_SECRET", "UPSTREAM_SECRET", "BODY_SECRET", '"endpoint":', '"headers":', '"payload":', '"stack":', '"cause":']) assert.ok(!encoded.includes(forbidden));
});

test("HTTP status and parse failure diagnostics remain distinct without changing retry status", async () => {
  for (const [status, body, category, retryStatus] of [[503, '{"detail":"BODY_SECRET"}', "http", 503], [200, '{bad SECRET', "json_parse", 502], [200, '[]', "response_contract", 502]]) {
    const rows = [];
    const client = createApiClient(configured, async () => new Response(body, { status }), undefined, row => rows.push(row));
    await assert.rejects(client.getDemoResult("job-12345678"), error => {
      assert.equal(error.status, retryStatus);
      const diagnostic = getRequestDiagnostics(error).at(-1);
      assert.equal(diagnostic.stage, "json"); assert.equal(diagnostic.category, category);
      assert.equal(diagnostic.httpStatus, status); assert.equal(diagnostic.bodyComplete, true);
      if (category === "json_parse") assert.equal(error.cause.name, "SyntaxError");
      assert.ok(!JSON.stringify(getRequestDiagnostics(error)).includes("SECRET"));
      return true;
    });
    assert.equal(rows.at(-1).outcome, "failure");
  }
});

test("known request timeout before headers retains the original exception and original 20-second budget", async t => {
  const deadline = new AbortController(), rows = [];
  t.mock.method(AbortSignal, "timeout", ms => { assert.equal(ms, 20000); return deadline.signal; });
  const reason = new DOMException("PRIVATE_TIMEOUT_TEXT", "TimeoutError");
  const client = createApiClient(configured, async (_url, init) => {
    deadline.abort(reason); init.signal.throwIfAborted();
  }, undefined, row => rows.push(row));
  await assert.rejects(client.getJob("job-12345678"), error => error === reason);
  assert.equal(rows.length, 1);
  assert.deepEqual([rows[0].operation, rows[0].stage, rows[0].category, rows[0].httpStatus, rows[0].bodyComplete], ["job", "headers", "timeout", null, false]);
  assert.ok(!JSON.stringify(getRequestDiagnostics(reason)).includes("PRIVATE_TIMEOUT_TEXT"));
});

test("body interruption preserves the stream error without guessing its network cause", async () => {
  const rows = [], reason = new TypeError("PRIVATE_STREAM_TEXT");
  const client = createApiClient(configured, async () => new Response(new ReadableStream({
    start(controller) { controller.enqueue(new TextEncoder().encode('{"unfinished":')); },
    pull(controller) { controller.error(reason); },
  }), { status: 200 }), undefined, row => rows.push(row));
  await assert.rejects(client.getDemoResult("job-12345678"), error => error === reason);
  const row = rows.at(-1);
  assert.deepEqual([row.stage, row.httpStatus, row.bodyComplete, row.category], ["body", 200, false, "body_read"]);
  assert.ok(!JSON.stringify(getRequestDiagnostics(reason)).includes("PRIVATE_STREAM_TEXT"));
});

const flushReads = async () => { for (let i = 0; i < 16; i++) await Promise.resolve(); };
function slowReport(t, options = {}) {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  let now = 0, stream, fetchSignal, cancelled = false;
  const rows = [];
  const response = new Response(new ReadableStream({ start(controller) { stream = controller; }, cancel() { cancelled = true; } }));
  const client = createApiClient(configured, async (_url, init) => { fetchSignal = init.signal; return response; }, { now: () => now, wait: async () => assert.fail("ready stream must not prepare-poll") }, row => rows.push(row));
  const work = client.getDemoResult("job-12345678", options).then(value => ({ value }), error => ({ error }));
  return { work, rows, stream, signal: () => fetchSignal, cancelled: () => cancelled,
    advance: async ms => { now += ms; t.mock.timers.tick(ms); await flushReads(); } };
}

test("a progressing report body can take more than 20 seconds after headers", async t => {
  const read = slowReport(t); await flushReads();
  const bytes = new TextEncoder().encode(JSON.stringify(nativeReport())), step = Math.ceil(bytes.length / 5);
  for (let offset = 0; offset < bytes.length; offset += step) {
    await read.advance(9000); assert.equal(read.signal().aborted, false);
    read.stream.enqueue(bytes.slice(offset, offset + step)); await flushReads();
  }
  read.stream.close(); await flushReads(); await read.advance(0);
  const result = await read.work;
  assert.equal(result.error, undefined); assert.equal(result.value.status, "ready");
  assert.equal(result.value.job_id, "job-12345678"); assert.equal(read.cancelled(), false);
  assert.equal(read.rows.at(-1).stage, "decode");
  await read.advance(120000); assert.equal(read.signal().aborted, false, "all completed report timers must be cleared");
});

test("a report body stops after 20 seconds without byte progress and clears its reader", async t => {
  const read = slowReport(t); await flushReads();
  read.stream.enqueue(new TextEncoder().encode('{"job_id":')); await flushReads();
  await read.advance(15000); read.stream.enqueue(new Uint8Array(0)); await flushReads();
  await read.advance(5000);
  const result = await read.work;
  assert.equal(result.error.name, "TimeoutError"); assert.equal(read.cancelled(), true); assert.equal(read.signal().aborted, true);
  const diagnostic = getRequestDiagnostics(result.error).at(-1);
  assert.deepEqual([diagnostic.stage, diagnostic.category, diagnostic.bodyComplete], ["body", "timeout", false]);
});

test("continuous slow progress cannot extend a report beyond the overall 120 second budget", async t => {
  const read = slowReport(t); await flushReads();
  for (let i = 0; i < 7; i++) { await read.advance(15000); read.stream.enqueue(new TextEncoder().encode(" ")); await flushReads(); }
  await read.advance(15000);
  const result = await read.work;
  assert.equal(result.error.status, 425); assert.equal(result.error.cause.name, "TimeoutError");
  assert.equal(read.cancelled(), true); assert.equal(read.signal().aborted, true);
  assert.equal(getRequestDiagnostics(result.error).at(-1).stage, "body");
});

test("navigation abort cancels a progressing report body without publishing late data", async t => {
  const caller = new AbortController(), reason = new DOMException("navigation", "AbortError");
  const read = slowReport(t, { signal: caller.signal }); await flushReads();
  await read.advance(9000); read.stream.enqueue(new TextEncoder().encode(" ")); await flushReads();
  const count = read.rows.length;
  caller.abort(reason); await flushReads(); const result = await read.work;
  assert.equal(result.error, reason); assert.equal(read.cancelled(), true); assert.equal(read.rows.length, count);
  await read.advance(120000); assert.equal(read.rows.length, count);
});

test("report connection still times out after 20 seconds before response headers", async t => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const rows = [];
  const client = createApiClient(configured, async (_url, init) => new Promise((_resolve, reject) => {
    init.signal.addEventListener("abort", () => reject(init.signal.reason), { once: true });
  }), undefined, row => rows.push(row));
  const work = client.getDemoResult("job-12345678").then(() => assert.fail("missing headers"), error => error);
  await flushReads(); t.mock.timers.tick(20000); await flushReads();
  const error = await work; assert.equal(error.name, "TimeoutError");
  assert.deepEqual([rows.at(-1).stage, rows.at(-1).category], ["headers", "timeout"]);
});

test("caller abort suppresses diagnostics after cancellation and preserves the exact reason", async () => {
  const caller = new AbortController(), rows = [];
  const reason = new DOMException("PRIVATE_CALLER_TEXT", "AbortError");
  const client = createApiClient(configured, async (_url, init) => {
    caller.abort(reason); init.signal.throwIfAborted();
  }, undefined, row => rows.push(row));
  await assert.rejects(client.getDemoResult("job-12345678", { signal: caller.signal }), error => error === reason);
  assert.deepEqual(rows, []); assert.deepEqual(getRequestDiagnostics(reason), []);
});

test("proof decode failure is separate from JSON and diagnostics cannot alter delivery", async () => {
  const rows = [];
  const invalid = createApiClient(configured, async () => Response.json({ ...nativeReport(), nested: { model_evidence_ref: "bad" } }), undefined, row => rows.push(row));
  await assert.rejects(invalid.getDemoResult("job-12345678"), error => {
    assert.deepEqual(getRequestDiagnostics(error).map(row => row.stage), ["headers", "body", "json", "decode"]);
    assert.equal(getRequestDiagnostics(error).at(-1).category, "decode");
    assert.equal(getRequestDiagnostics(error).at(-1).httpStatus, 200);
    return true;
  });
  const valid = createApiClient(configured, async () => Response.json(nativeReport()), undefined, () => { throw new Error("observer failed"); });
  assert.equal((await valid.getDemoResult("job-12345678")).status, "ready");
});

test("report decoding yields so a queued stop action can cancel before proof work", async () => {
  const caller = new AbortController(), rows = [];
  const reason = new DOMException("stop waiting", "AbortError");
  const client = createApiClient(configured, async () => Response.json(nativeReport()), undefined, row => {
    rows.push(row);
    if (row.stage === "json" && row.outcome === "success") setTimeout(() => caller.abort(reason), 0);
  });
  await assert.rejects(client.getDemoResult("job-12345678", { signal: caller.signal }), error => error === reason);
  assert.equal(rows.some(row => row.stage === "decode"), false);
});

test("server preparation deadline is retained as a specific diagnostic rather than a network timeout", async () => {
  const client = createApiClient(configured, async () => Response.json({ detail: { code: "report_preparation_timeout", message: "Preparation exceeded its deadline" } }, { status: 504 }));
  await assert.rejects(client.getDemoResult("job-12345678"), error => {
    assert.equal(error.status, 504); assert.equal(error.payload.detail.code, "report_preparation_timeout");
    assert.equal(getRequestDiagnostics(error).at(-1).category, "preparation_timeout"); return true;
  });
});

test("preparation deadline diagnostic records actual 202 rather than synthetic retry status425", async () => {
  const rows = [], timing = fakeReportClock(); let requests = 0;
  const client = createApiClient(configured, async () => { requests++; return Response.json(preparing(), { status: 202 }); }, timing, row => rows.push(row));
  await assert.rejects(client.getDemoResult("job-12345678"), error => {
    assert.equal(error.status, 425);
    assert.equal(getRequestDiagnostics(error).at(-1).category, "preparation_timeout");
    assert.equal(getRequestDiagnostics(error).at(-1).httpStatus, 202);
    return true;
  });
  assert.equal(timing.now(), 120000); assert.equal(requests, 120);
});


test("network rejection retains its own request diagnostics without inventing an HTTP status", async () => {
  const original = new TypeError("SECRET_NETWORK_DETAIL"), rows = [];
  const client = createApiClient(configured, async () => { throw original; }, undefined, row => rows.push(row));
  await assert.rejects(client.getJob("job-12345678"), error => error === original);
  assert.equal(rows.length, 1);
  assert.deepEqual([rows[0].stage, rows[0].category, rows[0].httpStatus], ["headers", "network", null]);
  assert.ok(!JSON.stringify(getRequestDiagnostics(original)).includes("SECRET_NETWORK_DETAIL"));
});

test("a final in-flight failure never inherits the preceding preparation response ID or HTTP202", async () => {
  let now = 0, calls = 0; const requestIds = [];
  const timing = { now: () => now, wait: async ms => { now += ms; } };
  const original = new TypeError("controlled failure");
  const client = createApiClient(configured, async (_url, init) => {
    requestIds.push(init.headers["X-Request-ID"]);
    if (++calls === 1) return Response.json(preparing(), { status: 202 });
    now = 120000; throw original;
  }, timing);
  await assert.rejects(client.getDemoResult("job-12345678"), error => {
    assert.equal(error.status, 425); assert.equal(error.cause, original);
    const row = getRequestDiagnostics(error).at(-1);
    assert.equal(row.requestId, requestIds[1]); assert.notEqual(row.requestId, requestIds[0]);
    assert.equal(row.httpStatus, null); assert.equal(row.stage, "headers");
    return true;
  });
  assert.equal(calls, 2);
});
