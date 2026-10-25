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
const { createApiClient, RallyMateApiError } = await import(compiledUrl(new URL("../app/lib/api-client.ts", import.meta.url)));
const { getRequestDiagnostics, safeRequestDiagnostic } = await import(compiledUrl(new URL("../app/lib/request-diagnostics.ts", import.meta.url)));
const { normalizePosePreview } = await import(compiledUrl(new URL("../app/lib/pose-playback.ts", import.meta.url)));
const configured = { baseUrl: "", jobsPath: "/v1/jobs", scorecardPath: "/api/scorecard", techniquesPath: "/v1/techniques" };
const job = "pose-job-123";
const secret = "PRIVATE_POSE_PAYLOAD_DO_NOT_DIAGNOSE";
const keys = ["operation", "stage", "outcome", "category", "requestId", "httpStatus", "startedAt", "elapsedMs", "bodyComplete"].sort();

// Same observed-pose contract as pose-playback.test.mjs, with an unselected raw
// field so an API implementation returning its normalized copy cannot pass.
function payload(start = 0) {
  return {
    schema_version: "1.0.0", result_kind: "observed_pose_playback", job_id: job,
    coordinate_space: "normalized_frame_0_1", joint_schema: "halpe26_named", min_keypoint_confidence: .35,
    skeleton_edges: [["left_shoulder", "left_elbow"], ["left_elbow", "left_wrist"]],
    source: { requested_start_ms: start, requested_end_ms: start + 10000, width: 1080, height: 1920,
      returned_frames: 1, is_sampled: false, is_partial: false, timestamp_semantics: "source_frame_timestamp_ms" },
    frames: [{ frame_index: start, timestamp_ms: start, valid_until_ms: start + 33, person_track_id: 8,
      selection_epoch: 1, selection_status: "selected", width: 1080, height: 1920,
      keypoints: [
        { name: "left_shoulder", x: .35, y: .3, confidence: .9 },
        { name: "left_elbow", x: .3, y: .45, confidence: .9 },
        { name: "left_wrist", x: .4, y: .5, confidence: .9 },
      ] }],
    raw_only_marker: secret,
  };
}

function assertSafeRows(rows, requestId, stages) {
  assert.deepEqual(rows.map(row => row.stage), stages);
  for (const row of rows) {
    assert.deepEqual(Object.keys(row).sort(), keys);
    assert.equal(row.operation, "pose_preview");
    assert.equal(row.requestId, requestId);
    assert.ok(Object.isFrozen(row));
    assert.deepEqual(safeRequestDiagnostic(row), row);
    assert.match(row.startedAt, /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/);
    assert.ok(Number.isFinite(row.elapsedMs) && row.elapsedMs >= 0);
  }
  assert.equal(new Set(rows.map(row => row.startedAt)).size, 1);
  assert.ok(!JSON.stringify(rows).includes(secret));
  assert.ok(!JSON.stringify(rows).includes("https://upstream.invalid"));
}

function assertDecodeFailure(error, rows, category) {
  assert.ok(error instanceof RallyMateApiError);
  assert.equal(error.status, 422);
  assert.equal(error.payload, undefined);
  assert.equal(error.cause, undefined);
  assert.ok(error.message.length > 0 && !error.message.includes(secret));
  assert.deepEqual(getRequestDiagnostics(error), rows);
  assert.deepEqual(rows.map(row => row.outcome), ["success", "success", "success", "failure"]);
  assert.equal(rows.at(-1).category, category);
  assert.equal(rows.at(-1).httpStatus, 200);
  assert.equal(rows.at(-1).bodyComplete, true);
}

test("pose success retains raw response and one request ID across headers/body/json/decode", async () => {
  const raw = payload(), rows = []; let calls = 0, requestId;
  assert.ok(normalizePosePreview(raw, job));
  assert.notDeepEqual(normalizePosePreview(raw, job), raw);
  const client = createApiClient(configured, async (url, init) => {
    calls++; requestId = init.headers["X-Request-ID"];
    assert.equal(String(url), `/v1/jobs/${job}/pose-preview?start_ms=0&duration_ms=10000&sample_limit=600`);
    assert.equal(init.method, "GET"); assert.equal(init.cache, "no-store");
    return Response.json(raw, { headers: { "X-Request-ID": "UPSTREAM_PRIVATE_ID", "X-Secret": secret } });
  }, undefined, row => rows.push(row));
  assert.deepEqual(await client.getPosePreview(job), raw);
  assert.equal(calls, 1);
  assertSafeRows(rows, requestId, ["headers", "body", "json", "decode"]);
  assert.deepEqual(rows.map(row => row.outcome), ["success", "success", "success", "success"]);
  assert.deepEqual(rows.map(row => row.bodyComplete), [false, true, true, true]);
  assert.ok(rows.every(row => row.httpStatus === 200 && row.category === null));
  assert.ok(!JSON.stringify(rows).includes("UPSTREAM_PRIVATE_ID"));
});

test("pose validation compares the bounded start sent in the request and preserves old bounds", async () => {
  for (const [requested, expected] of [[12.9, 12], [-3, 0], [NaN, 0], [undefined, 0]]) {
    const raw = payload(expected), rows = []; let calls = 0;
    const client = createApiClient(configured, async url => {
      calls++;
      const query = new URL(url, "https://local.invalid").searchParams;
      assert.equal(query.get("start_ms"), String(expected));
      assert.equal(query.get("duration_ms"), "10000");
      assert.equal(query.get("sample_limit"), "600");
      return Response.json(raw);
    }, undefined, row => rows.push(row));
    assert.deepEqual(await client.getPosePreview(job, { startMs: requested, durationMs: 50000, sampleLimit: 700 }), raw);
    assert.equal(calls, 1); assert.equal(rows.at(-1).stage, "decode"); assert.equal(rows.at(-1).outcome, "success");
  }
});

test("HTTP200 invalid pose and foreign job fail safely at pose_normalization without retry", async () => {
  const invalid = [
    { detail: secret, url: "https://upstream.invalid/private", cause: { message: secret } },
    { ...payload(), coordinate_space: "body_scaled", message: secret },
    { ...payload(), job_id: "foreign-pose-job", message: secret },
  ];
  const messages = [];
  for (const raw of invalid) {
    const rows = []; let calls = 0, requestId;
    assert.equal(normalizePosePreview(raw, job), null);
    const client = createApiClient(configured, async (_url, init) => {
      calls++; requestId = init.headers["X-Request-ID"]; return Response.json(raw);
    }, undefined, row => rows.push(row));
    await assert.rejects(client.getPosePreview(job), error => {
      assertDecodeFailure(error, rows, "pose_normalization"); messages.push(error.message); return true;
    });
    assert.equal(calls, 1);
    assertSafeRows(rows, requestId, ["headers", "body", "json", "decode"]);
  }
  assert.equal(new Set(messages).size, 1, "Normalization error text must not depend on a payload field");
});

test("a valid foreign pose window has its own fixed decode category and no response payload", async () => {
  const raw = payload(10000), rows = []; let calls = 0, requestId;
  assert.ok(normalizePosePreview(raw, job));
  const client = createApiClient(configured, async (_url, init) => {
    calls++; requestId = init.headers["X-Request-ID"]; return Response.json(raw);
  }, undefined, row => rows.push(row));
  await assert.rejects(client.getPosePreview(job, { startMs: 0 }), error => {
    assertDecodeFailure(error, rows, "pose_window_mismatch"); return true;
  });
  assert.equal(calls, 1); assertSafeRows(rows, requestId, ["headers", "body", "json", "decode"]);
});

test("pose HTTP and JSON errors keep their existing status/category and never report decode", async () => {
  for (const [status, body, expectedStatus, category] of [
    [404, JSON.stringify({ detail: secret }), 404, "http"],
    [200, `{broken ${secret}`, 502, "json_parse"],
    [200, "[]", 502, "response_contract"],
  ]) {
    const rows = []; let calls = 0, requestId;
    const client = createApiClient(configured, async (_url, init) => {
      calls++; requestId = init.headers["X-Request-ID"]; return new Response(body, { status });
    }, undefined, row => rows.push(row));
    await assert.rejects(client.getPosePreview(job), error => {
      assert.equal(error.status, expectedStatus);
      if (status === 404) assert.deepEqual(error.payload, { detail: secret });
      if (category === "json_parse") assert.ok(error.cause instanceof SyntaxError);
      assert.equal(getRequestDiagnostics(error).at(-1).category, category); return true;
    });
    assert.equal(calls, 1); assertSafeRows(rows, requestId, ["headers", "body", "json"]);
    assert.equal(rows.at(-1).httpStatus, status); assert.equal(rows.at(-1).bodyComplete, true);
  }
});

test("pose body interruption keeps the exact error and body_read category", async () => {
  const rows = [], reason = new TypeError(secret); let calls = 0, requestId;
  const response = new Response("unused");
  Object.defineProperty(response, "text", { value: async () => { throw reason; } });
  const client = createApiClient(configured, async (_url, init) => {
    calls++; requestId = init.headers["X-Request-ID"]; return response;
  }, undefined, row => rows.push(row));
  await assert.rejects(client.getPosePreview(job), error => error === reason);
  assert.equal(calls, 1); assertSafeRows(rows, requestId, ["headers", "body"]);
  assert.deepEqual([rows.at(-1).category, rows.at(-1).httpStatus, rows.at(-1).bodyComplete], ["body_read", 200, false]);
});

test("pose pre-abort avoids fetch and post-body abort wins over valid or invalid normalization", async () => {
  const stopped = new AbortController(), reason = new DOMException(secret, "AbortError");
  stopped.abort(reason); let preCalls = 0;
  const pre = createApiClient(configured, async () => { preCalls++; return Response.json(payload()); });
  await assert.rejects(pre.getPosePreview(job, { signal: stopped.signal }), error => error === reason);
  assert.equal(preCalls, 0);
  for (const raw of [payload(), { invalid: secret }]) {
    const caller = new AbortController(), rows = []; let calls = 0, requestId;
    const response = new Response("unused");
    Object.defineProperty(response, "text", { value: async () => { caller.abort(reason); return JSON.stringify(raw); } });
    const client = createApiClient(configured, async (_url, init) => {
      calls++; requestId = init.headers["X-Request-ID"]; return response;
    }, undefined, row => rows.push(row));
    await assert.rejects(client.getPosePreview(job, { signal: caller.signal }), error => error === reason);
    assert.equal(calls, 1); assertSafeRows(rows, requestId, ["headers"]);
    assert.deepEqual(getRequestDiagnostics(reason), []);
  }
});

test("caller cancellation after JSON success suppresses the pending pose validation failure", async () => {
  const caller = new AbortController(), reason = new DOMException(secret, "AbortError"), rows = [];
  let requestId, calls = 0;
  const client = createApiClient(configured, async (_url, init) => {
    calls++; requestId = init.headers["X-Request-ID"]; return Response.json({ invalid: secret });
  }, undefined, row => { rows.push(row); if (row.stage === "json") caller.abort(reason); });
  await assert.rejects(client.getPosePreview(job, { signal: caller.signal }), error => error === reason);
  assert.equal(calls, 1); assertSafeRows(rows, requestId, ["headers", "body", "json"]);
  assert.ok(rows.every(row => row.outcome === "success"));
  assert.deepEqual(getRequestDiagnostics(reason), []);
});

test("pose network and local timeout remain distinct original failures with one20s request", async t => {
  for (const timeout of [false, true]) {
    const deadline = new AbortController(), rows = [], reason = new TypeError(secret); let calls = 0, requestId;
    const mock = t.mock.method(AbortSignal, "timeout", ms => { assert.equal(ms, 20000); return deadline.signal; });
    const client = createApiClient(configured, async (_url, init) => {
      calls++; requestId = init.headers["X-Request-ID"];
      if (timeout) deadline.abort(new DOMException("private timeout", "TimeoutError"));
      throw reason;
    }, undefined, row => rows.push(row));
    await assert.rejects(client.getPosePreview(job), error => error === reason);
    assert.equal(calls, 1); assertSafeRows(rows, requestId, ["headers"]);
    assert.deepEqual([rows[0].category, rows[0].httpStatus, rows[0].bodyComplete], [timeout ? "timeout" : "network", null, false]);
    assert.deepEqual(getRequestDiagnostics(reason), rows); mock.mock.restore();
  }
});

test("an observer exception cannot change successful raw pose delivery or a fixed decode failure", async () => {
  const raw = payload(); let successCalls = 0, failedCalls = 0;
  const success = createApiClient(configured, async () => { successCalls++; return Response.json(raw); }, undefined, () => { throw new Error(secret); });
  assert.deepEqual(await success.getPosePreview(job), raw); assert.equal(successCalls, 1);
  const failure = createApiClient(configured, async () => { failedCalls++; return Response.json({ invalid: secret }); }, undefined, () => { throw new Error(secret); });
  await assert.rejects(failure.getPosePreview(job), error => {
    assert.ok(error instanceof RallyMateApiError); assert.equal(error.status, 422);
    assert.equal(error.payload, undefined); assert.equal(error.cause, undefined);
    assert.equal(getRequestDiagnostics(error).at(-1).category, "pose_normalization"); return true;
  });
  assert.equal(failedCalls, 1);
});

test("new pose decode categories still rebuild exactly the safe nine-field diagnostic whitelist", async () => {
  for (const [raw, category] of [[{ invalid: secret }, "pose_normalization"], [payload(10000), "pose_window_mismatch"]]) {
    const rows = [], client = createApiClient(configured, async () => Response.json(raw), undefined, row => rows.push(row));
    await assert.rejects(client.getPosePreview(job), error => error.status === 422);
    const diagnostic = rows.at(-1); assert.equal(diagnostic.category, category);
    const extra = { ...diagnostic, url: "https://upstream.invalid/private", headers: { Authorization: secret }, body: secret, message: secret, stack: secret, cause: { message: secret }, payload: raw };
    assert.deepEqual(safeRequestDiagnostic(extra), diagnostic);
    assert.deepEqual(Object.keys(safeRequestDiagnostic(extra)).sort(), keys);
    assert.ok(!JSON.stringify(safeRequestDiagnostic(extra)).includes(secret));
    assert.equal(safeRequestDiagnostic({ ...diagnostic, category: secret }), null);
    let invoked = false;
    const getter = { ...diagnostic };
    Object.defineProperty(getter, "category", { get() { invoked = true; return category; } });
    assert.equal(safeRequestDiagnostic(getter), null); assert.equal(invoked, false);
  }
});
