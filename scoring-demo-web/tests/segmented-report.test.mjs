import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import ts from "typescript";
const modules = new Map();
function compiledUrl(file) {
  if (file.pathname.endsWith(".json")) return `data:text/javascript;base64,${Buffer.from(`export default ${fs.readFileSync(file, "utf8")}`).toString("base64")}`;
  if (modules.has(file.href)) return modules.get(file.href);
  let js = ts.transpileModule(fs.readFileSync(file, "utf8"), { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
  js = js.replace(/from "([^"]+)"/g, (_match, name) => `from ${JSON.stringify(name.startsWith(".") ? compiledUrl(new URL(name.endsWith(".json") ? name : `${name}.ts`, file)) : import.meta.resolve(name))}`);
  const url = `data:text/javascript;base64,${Buffer.from(js).toString("base64")}`; modules.set(file.href, url); return url;
}
const from = path => import(compiledUrl(new URL(path, import.meta.url)));
const { parseReportOverview, parseReportMotion, resultFromReportSummary, parseReportSummary, parseReportActions, parseReportActionDetail, SUMMARY_BYTES } = await from("../app/lib/segmented-report.ts");
const { packReportBackup } = await from("../app/lib/report-backup-codec.ts");
const { createApiClient } = await from("../app/lib/api-client.ts");
const { watchSummaryAnalysis, AnalysisCancelledError } = await from("../app/lib/analysis-session.ts");
const { analysisProgress } = await from("../app/lib/analysis-progress.ts");
const { proxyAnalysis } = await from("../worker/gateway.ts");
const job = "segmented-job-1234";
const fixture = JSON.parse(fs.readFileSync(new URL("./fixtures/sam-source-observations.json", import.meta.url), "utf8")).indicator_assessment.observations[0];
const proof = () => structuredClone(Object.fromEntries(["measurement_provenance", "source_window", "geometry_mask", "source_track_id"].map(key => [key, fixture[key]])));
const source = { source_job_id: job, video_id: job, video_sha256: fixture.source_window.video_sha256, frames_sha256: "b".repeat(64), evidence_revision: "d".repeat(64) };
const action = { action_id: "event-1", event_id: "event-1", event_code: "FS01", label: "动作", start_ms: fixture.source_window.start_ms, end_ms: fixture.source_window.end_ms, person_track_id: 1, source_track_id: fixture.source_track_id, indicator_count: 1, evidence_status: "available" };
const envelope = { schema_version: "segmented-report-v1", job_id: job, source };
const summary = () => ({ ...envelope, status: "ready", video: { width: 960, height: 448, fps: 30, frame_count: 100, duration_ms: 3333 }, processing: { processed_frames: 100 }, scoring: { status: "calibration_required", message: "需校准", event_counts: { FS01: 1 }, score_status_counts: { calibration_required: 1 } }, delivery: { summary: "complete", evidence: "on_demand" } });
const detail = () => ({ ...envelope, status: "complete", action, event: { ...proof(), event_id: action.event_id, event_code: action.event_code, person_track_id: action.person_track_id, video_id: job }, records: [{ ...proof(), event_id: action.event_id, event_code: action.event_code, person_track_id: action.person_track_id, video_id: job, indicator_id: "FS01-M01" }], pagination: { offset: 0, limit: 4, total: 1, next_offset: null }, evidence_validation: "validated", scope: "event_indicator_records" });
const packed = value => packReportBackup(value, "api");

test("summary has its own complete contract and cannot become an old full report", () => {
  assert.equal(parseReportSummary(summary(), job).scoring.status, "calibration_required");
  for (const change of [value => value.job_id = "other", value => value.delivery.summary = "partial", value => value.source.video_sha256 = "bad", value => delete value.source.evidence_revision, value => value.source.evidence_revision = "bad", value => value.video.duration_ms = -1]) {
    const value = structuredClone(summary()); change(value); assert.throws(() => parseReportSummary(value, job));
  }
});
test("action pages bind source, offset, count and completeness", () => {
  const value = { ...envelope, status: "partial", actions: [action], pagination: { offset: 0, limit: 20, total: 2, next_offset: 1 } };
  assert.equal(parseReportActions(value, job, source, 0).status, "partial");
  for (const change of [value => value.source.frames_sha256 = "c".repeat(64), value => value.source.evidence_revision = "e".repeat(64), value => value.status = "complete", value => value.pagination.next_offset = 0, value => value.actions.push(action)]) {
    const bad = structuredClone(value); change(bad); assert.throws(() => parseReportActions(bad, job, source, 0));
  }
});
test("details preserve strict pooled and native proofs and separate logical versus native tracks", () => {
  const input = detail(); input.action = { ...action, person_track_id: action.source_track_id + 99 }; input.records[0].person_track_id = input.action.person_track_id; input.event.person_track_id = input.action.person_track_id;
  for (const value of [input, packed(input)]) assert.equal(parseReportActionDetail(value, job, source, input.action, 0).records[0].source_window.person_track_id, action.source_track_id);
  for (const change of [value => value.records[0].event_id = "other", value => value.records[0].person_track_id = 99, value => value.records[0].video_id = "other", value => value.records[0].source_window.source_bindings[0].pts++, value => value.pagination.total = 2]) {
    const bad = detail(); change(bad); assert.throws(() => parseReportActionDetail(bad, job, source, action, 0));
  }
  const foreign = structuredClone(detail()); foreign.source.video_sha256 = "c".repeat(64);
  assert.throws(() => parseReportActionDetail(foreign, job, { ...source, video_sha256: "c".repeat(64) }, action, 0));
  const revised = structuredClone(detail()); revised.source.evidence_revision = "e".repeat(64);
  assert.throws(() => parseReportActionDetail(packed(revised), job, source, action, 0), /重新读取摘要/);
});
test("summary transport enforces its small byte cap before decoding and cancels oversized streams", async () => {
  let cancelled = false;
  const response = new Response(new ReadableStream({ cancel() { cancelled = true; } }), { headers: { "Content-Length": String(SUMMARY_BYTES + 1) } });
  await assert.rejects(createApiClient({}, async () => response).getReportSummary(job), /读取上限/);
  assert.equal(cancelled, true);
});
test("new API reads request only the selected page and never the full report", async () => {
  const requests = [];
  const client = createApiClient({}, async url => { requests.push(url); return Response.json(url.includes("report-summary") ? summary() : packed(detail())); });
  await client.getReportSummary(job); await client.getReportActionDetail(job, source, action);
  assert.equal(requests[0], `/v1/jobs/${job}/report-summary`);
  assert.equal(requests[1], `/v1/jobs/${job}/report-actions/event-1?offset=0&limit=4&report_delivery=deferred-v1`);
});
test("a bounded detail page retries with one whole record after an explicit size rejection", async () => {
  const urls = [];
  const client = createApiClient({}, async url => {
    urls.push(url);
    if (new URL(url, "https://local.invalid").searchParams.get("limit") === "4") return Response.json({ detail: { code: "report_segment_limit" } }, { status: 413 });
    const value = detail(); value.pagination.limit = 1; return Response.json(packed(value));
  });
  const result = await client.getReportActionDetail(job, source, action);
  assert.equal(result.records.length, 1); assert.equal(result.pagination.limit, 1);
  assert.equal(urls.length, 2); assert.equal(new URL(urls[1], "https://local.invalid").searchParams.get("limit"), "1");
});
test("summary session stops at the summary and ignores late completion after navigation", async () => {
  let reads = 0;
  const client = { getJobStatus: async () => ({ id: job, status: "succeeded" }), getReportSummary: async () => { reads++; return summary(); }, getDemoResult: () => assert.fail("full report must not load") };
  assert.equal((await watchSummaryAnalysis(client, job, new AbortController().signal, () => {}, 0)).summary.status, "ready"); assert.equal(reads, 1);
  const controller = new AbortController();
  await assert.rejects(watchSummaryAnalysis({ ...client, getReportSummary: async () => { controller.abort(); return summary(); } }, job, controller.signal, () => {}, 0), error => error.name === "AbortError");
  await assert.rejects(watchSummaryAnalysis({ ...client, getJobStatus: async () => ({ id: job, status: "cancelled" }) }, job, new AbortController().signal, () => {}, 0), AnalysisCancelledError);
  for (const status of [{ id: "other-job", status: "succeeded" }, { id: job, status: "unknown" }]) {
    await assert.rejects(watchSummaryAnalysis({ ...client, getJobStatus: async () => status, getReportSummary: () => assert.fail("invalid job status must not load a summary") }, job, new AbortController().signal, () => {}, 0), /任务响应不完整/);
  }
});

test("successful polling of an unclaimed job preserves queue state and waits through executor recovery", async () => {
  const created_at = "2026-11-07T09:26:00Z", updates = [];
  let polls = 0, summaries = 0;
  const client = {
    getJobStatus: async id => {
      assert.equal(id, job); polls++;
      return polls <= 35 ? { id, status: "queued", created_at, started_at: null, updated_at: created_at, queue_position: 1,
        executor: { status: polls <= 30 ? "unavailable" : "restarting", available: false, heartbeat_at: null } }
        : { id, status: polls === 36 ? "running" : "succeeded", started_at: "2026-11-07T10:00:00Z", completed_at: polls === 37 ? "2026-11-07T10:01:00Z" : null };
    },
    getReportSummary: async () => { summaries++; assert.equal(polls, 37); return summary(); },
    submitVideo: () => assert.fail("must not resubmit a queued job"),
    cancelJob: () => assert.fail("must not cancel an unclaimed job"),
  };
  const result = await watchSummaryAnalysis(client, job, new AbortController().signal, state => updates.push(state), 0);
  assert.equal(result.job.status, "succeeded"); assert.equal(summaries, 1);
  assert.equal(updates.filter(state => state.status === "queued").length, 35);
  for (const state of updates.slice(0, 35)) {
    const progress = analysisProgress(state, Date.parse("2026-11-07T09:59:00Z"));
    assert.match(progress.elapsed, /已排队 33 分/); assert.doesNotMatch(progress.elapsed, /已处理/);
  }
});

test("status read failures are distinct from executor unavailability and clear after reconnect", async () => {
  let polls = 0;
  const updates = [], controller = new AbortController();
  const client = { getJobStatus: async () => {
    polls++;
    if (polls === 2) throw new TypeError("network failed");
    return { id: job, status: "queued", executor: { status: "unavailable", available: false, heartbeat_at: null } };
  }, getReportSummary: () => assert.fail("queued job has no report") };
  await assert.rejects(watchSummaryAnalysis(client, job, controller.signal, state => {
    updates.push(state); if (updates.length === 3) controller.abort();
  }, 0), error => error.name === "AbortError");
  assert.deepEqual(updates.map(state => analysisProgress(state).title), ["分析执行服务暂不可用", "正在重新连接分析服务", "分析执行服务暂不可用"]);
  assert.equal(updates[2].client_connection, undefined);
});
test("gateway permits bounded report routes with internal auth and rejects arbitrary children", async t => {
  for (const suffix of ["status", "report-summary", "report-actions?offset=20&limit=20", "report-actions/event-1?offset=4&limit=4"]) {
    let auth;
    t.mock.method(globalThis, "fetch", async (_url, options) => { auth = new Headers(options.headers).get("authorization"); return Response.json({ ok: true }); });
    const response = await proxyAnalysis(new Request(`http://localhost:8000/v1/jobs/${job}/${suffix}`), { RALLYMATE_API_ORIGIN: "http://127.0.0.1:8001", RALLYMATE_API_KEY: "internal" });
    assert.equal(response.status, 200); assert.equal(auth, "Bearer internal");
  }
  assert.equal((await proxyAnalysis(new Request(`http://localhost:8000/v1/jobs/${job}/report-actions/event-1/arbitrary`), {}, () => assert.fail())).status, 404);
});

test("overview retains original timing and binds the video source before restoring complete UI", () => {
  const input = summary(); input.original_video = { sha256: source.video_sha256, pose_time_binding: { exact: true } };
  const result = resultFromReportSummary(input);
  assert.equal(result.original_video, input.original_video);
  const payload = { ...envelope, status: "complete", result, assessment: null };
  assert.equal(parseReportOverview(payload, job, source).result.original_video, input.original_video);
  payload.result.input.video.sha256 = "e".repeat(64);
  assert.throws(() => parseReportOverview(payload, job, source));
});

test("motion details retain process evidence and reject a different episode or revision", () => {
  const episode = { episode_id: "motion-one", family: "baseline", person_track_id: 1, start_ms: 100, peak_ms: 200, end_ms: 500, contact_confirmed: false, classification: { status: "unclassified", label_zh: "挥拍" }, phases: [], metrics: { peak_wrist_speed_torso_per_s: 2 }, evidence: {} };
  const payload = { ...envelope, status: "complete", episode, process_evaluation: { status: "available", dimensions: [] } };
  const detail = parseReportMotion(payload, job, source, episode);
  assert.equal(detail.episode.metrics.peak_wrist_speed_torso_per_s, 2);
  assert.equal(detail.processEvaluation, payload.process_evaluation);
  assert.throws(() => parseReportMotion(payload, job, source, { ...episode, episode_id: "other" }));
  assert.throws(() => parseReportMotion({ ...payload, source: { ...source, evidence_revision: "e".repeat(64) } }, job, source, episode));
});

test("only the bounded overview read gets its 70 second preparation window", async t => {
  const budgets = [];
  t.mock.method(AbortSignal, "timeout", ms => { budgets.push(ms); return new AbortController().signal; });
  const client = createApiClient({}, async url => Response.json(url.includes("/report-overview")
    ? { ...envelope, status: "complete", result: resultFromReportSummary(summary()), assessment: null } : summary()));
  await client.getReportOverview(job, source);
  assert.ok(budgets.includes(70000));
  budgets.length = 0;
  await client.getReportSummary(job);
  assert.ok(budgets.includes(20000));
  assert.ok(!budgets.includes(70000));
});

const pendingSegment = (id = job) => ({ schema_version: "segmented-report-v1", job_id: id, status: "preparing", retry_after_ms: 1000, delivery: { state: "preparing" } });
const readyOverview = () => ({ ...envelope, status: "complete", result: resultFromReportSummary(summary()), assessment: null });
function segmentClock() {
  let now = 0; const waits = [];
  return { waits, now: () => now, wait: async (ms, signal) => { signal?.throwIfAborted(); waits.push(ms); now += ms; } };
}
test("segmented preparation and explicit saturation wait without restarting analysis", async () => {
  const timing = segmentClock(), calls = [];
  const responses = [Response.json(pendingSegment(), { status: 202 }),
    Response.json({ detail: { code: "report_segments_busy" } }, { status: 429, headers: { "Retry-After": "2" } }),
    Response.json({ detail: { code: "report_segments_busy" } }, { status: 503, headers: { "Retry-After": "30" } }), Response.json(readyOverview())];
  const client = createApiClient({}, async (url, options) => { calls.push([url, options.method]); return responses.shift(); }, timing);
  assert.equal((await client.getReportOverview(job, source)).job_id, job);
  assert.deepEqual(timing.waits, [1000, 2000, 5000]);
  assert.ok(calls.every(([url, method]) => url === `/v1/jobs/${job}/report-overview?report_delivery=deferred-v1` && method === "GET"));
});
test("segmented wait rejects foreign preparation and real resource or evidence failures", async () => {
  const cases = [[202, pendingSegment("foreign-job")], [202, { ...pendingSegment(), delivery: {} }],
    [202, { ...pendingSegment(), retry_after_ms: "1000" }], [200, pendingSegment()],
    [503, { detail: { code: "report_segment_resource_limit" } }], [429, { detail: { code: "other_busy" } }],
    [409, { detail: { code: "report_source_changed" } }]];
  for (const [status, body] of cases) {
    let calls = 0; const timing = segmentClock();
    const client = createApiClient({}, async () => { calls++; return Response.json(body, { status }); }, timing);
    await assert.rejects(client.getReportOverview(job, source));
    assert.equal(calls, 1); assert.deepEqual(timing.waits, []);
  }
});
test("segmented preparation has a total budget and cancellation stops only the caller", async () => {
  const timing = segmentClock(); let calls = 0;
  const client = createApiClient({}, async () => { calls++; return Response.json(pendingSegment(), { status: 202 }); }, timing);
  await assert.rejects(client.getReportOverview(job, source), error => error.status === 425);
  assert.equal(timing.waits.reduce((a, b) => a + b, 0), 210000); assert.equal(calls, 210);
  const controller = new AbortController(); calls = 0;
  const cancelled = createApiClient({}, async () => { calls++; return Response.json(pendingSegment(), { status: 202 }); },
    { now: () => 0, wait: async (_ms, signal) => { controller.abort(); signal.throwIfAborted(); } });
  await assert.rejects(cancelled.getReportOverview(job, source, controller.signal), error => error.name === "AbortError");
  assert.equal(calls, 1);
});
test("same-source overview readers share a request and cancelling one preserves the other", async () => {
  let release, calls = 0, transportSignal;
  const client = createApiClient({}, async (_url, options) => {
    calls++; transportSignal = options.signal; await new Promise(resolve => release = resolve); return Response.json(readyOverview());
  });
  const cancelled = new AbortController();
  const first = client.getReportOverview(job, source, cancelled.signal), second = client.getReportOverview(job, source);
  const rejection = assert.rejects(first, error => error.name === "AbortError");
  cancelled.abort(); await rejection;
  assert.equal(calls, 1); assert.equal(transportSignal.aborted, false);
  release(); assert.equal((await second).job_id, job);
});
test("score, sidebar and trajectory wait for shared overview and automatically continue in order", async () => {
  let release; const calls = [];
  const shot = { schema_version: "1.0.0", job_id: job, status: "unreviewed", revision: 0,
    source: { video_sha256: source.video_sha256, frame_count: 100, start_ms: 0, end_ms: 3333, duration_ms: 3333, coordinate_space: "normalized_frame_0_1" },
    summary: { confirmed_hit_count: 0, confirmed_bounce_count: 0, mapped_bounce_count: 0, candidate_hit_count: 0,
      candidate_bounce_count: 0, dismissed_count: 0, count_semantics: "review_confirmed_events_not_total_video" }, events: [], limitations_zh: [] };
  const client = createApiClient({}, async url => {
    calls.push(new URL(url, "https://local.invalid").pathname.split("/").at(-1));
    if (url.includes("/report-overview")) { await new Promise(resolve => release = resolve); return Response.json(readyOverview()); }
    return Response.json(url.endsWith("/shot-statistics") ? shot : { job_id: job });
  });
  // Child/assessment effects may run before the overview effect in the same render.
  const score = client.getTennisAssessment(job), overview = client.getReportOverview(job, source);
  const sidebar = client.getShotStatistics(job), trajectory = client.getTrajectory(job);
  await Promise.resolve(); assert.deepEqual(calls, ["report-overview"]);
  release(); await Promise.all([overview, score, sidebar, trajectory]);
  assert.deepEqual(calls, ["report-overview", "tennis-assessment", "shot-statistics", "trajectory"]);
});
test("queued auxiliary cancellation makes no request and a transport timeout retries at most once", async () => {
  let release; const calls = [], controller = new AbortController();
  const client = createApiClient({}, async url => {
    calls.push(url); await new Promise(resolve => release = resolve); return Response.json(readyOverview());
  });
  const overview = client.getReportOverview(job, source), score = client.getTennisAssessment(job, controller.signal);
  const rejected = assert.rejects(score, error => error.name === "AbortError"); controller.abort(); await rejected;
  release(); await overview; assert.equal(calls.length, 1);
  for (const permanent of [false, true]) {
    let count = 0; const timing = segmentClock();
    const retry = createApiClient({}, async () => { count++; if (count === 1 || permanent) throw new DOMException("late", "TimeoutError"); return Response.json({ status: "ready" }); }, timing);
    if (permanent) await assert.rejects(retry.getTennisAssessment(job), error => error.name === "TimeoutError");
    else assert.equal((await retry.getTennisAssessment(job)).status, "ready");
    assert.equal(count, 2); assert.deepEqual(timing.waits, [2000]);
  }
});
