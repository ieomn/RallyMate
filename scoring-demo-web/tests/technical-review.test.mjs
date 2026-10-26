import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import ts from "typescript";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

const cache = new Map();
function compiledUrl(file) {
  // Keep source JSON imports shared with production, without a generated copy.
  if (file.pathname.endsWith(".json")) return `data:text/javascript;base64,${Buffer.from(`export default ${fs.readFileSync(file, "utf8")}`).toString("base64")}`;
  if (cache.has(file.href)) return cache.get(file.href);
  let js = ts.transpileModule(fs.readFileSync(file, "utf8"), { fileName: file.pathname, compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  js = js.replace(/^import\s+["'][^"']+\.css["'];?\s*$/gm, "");
  js = js.replace(/from "([^"]+)"/g, (_match, name) => {
    const local = new URL(name.endsWith(".json") ? name : `${name}.ts`, file);
    return `from ${JSON.stringify(name.startsWith(".") ? compiledUrl(fs.existsSync(local) ? local : new URL(`${name}.tsx`, file)) : import.meta.resolve(name))}`;
  });
  const url = `data:text/javascript;base64,${Buffer.from(js).toString("base64")}`;
  cache.set(file.href, url); return url;
}
const { parseTechnicalReview, technicalReviewError, sourceReviewWindows, sourceReviewCoverage, sourceWindowDisplay, reviewInterval } = await import(compiledUrl(new URL("../app/lib/technical-review.ts", import.meta.url)));
const { createApiClient } = await import(compiledUrl(new URL("../app/lib/api-client.ts", import.meta.url)));
const { proxyAnalysis } = await import(compiledUrl(new URL("../worker/gateway.ts", import.meta.url)));
const { buildReportBackup, buildPracticeReport } = await import(compiledUrl(new URL("../app/lib/report-export.ts", import.meta.url)));
const { readReportJson } = await import(compiledUrl(new URL("../app/lib/report-file.ts", import.meta.url)));
const { default: Panel } = await import(compiledUrl(new URL("../app/SourceMeasurementPanel.tsx", import.meta.url)));
const catalog = JSON.parse(fs.readFileSync(new URL("../app/data/scoring-reference.json", import.meta.url), "utf8"));
const actualSource = JSON.parse(fs.readFileSync(new URL("./fixtures/fs01-fs02-source-observations.json", import.meta.url), "utf8"));
const samSource = JSON.parse(fs.readFileSync(new URL("./fixtures/sam-source-observations.json", import.meta.url), "utf8"));
const preloadDetails = JSON.parse(fs.readFileSync(new URL("./fixtures/preload-reference-evidence.json", import.meta.url), "utf8"));
const jobId = "job-human-review-123";
const sha = "a".repeat(64), contextSha = "b".repeat(64), referenceSha = "c".repeat(64);
const mutation = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", reviewId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const ledgerOf = () => ({ schema_version: "technical-review-v1.0.0", job_id: jobId, revision: 0,
  source_sha256: sha, artifact_context_sha256: contextSha, source_reference_version: "scoring-reference-20261004", source_reference_sha256: referenceSha,
  video: { duration_ms: 10000, review_start_ms: 1000, review_end_ms: 8000 }, players: [{ player_id: 1, start_ms: 1000, end_ms: 8000 }],
  events: [{ event_id: "event-FS01", event_code: "FS01", player_id: 1, start_ms: 1500, end_ms: 3000 }],
  rules: [{ indicator_id: "FS01-M02", event_code: "FS01", manual_grading_allowed: true, blockers: [] }, { indicator_id: "GS01-M10-04", event_code: "GS01", manual_grading_allowed: false, blockers: [{ code: "source_rule_conflict", message: "原文待澄清" }] }],
  visual_rules: [{ visual_rule_id: "VIS-NET-T003-KP01-C01", technique_ids: ["forehand_volley"], stage_id: "VIS-NET-T003", phase_id: "stability", optional_in_source: true, optional_positive_observation: false,
    source_document_id: "VIS-NET", source_document_sha256: sha, source_locator: "VIS-NET:word/document.xml:P0030", source_text: "保持短暂支撑", rubric_sha256: referenceSha }],
  entries: [], semantics: "human_source_rule_review", formal_grade: null, calibration_eligible: false });
const inputOf = () => ({ mutation_id: mutation, expected_revision: 0, source_sha256: sha, artifact_context_sha256: contextSha, source_reference_version: "scoring-reference-20261004", source_reference_sha256: referenceSha,
  target_kind: "indicator", indicator_id: "FS01-M02", visual_rule_id: null, player_id: 1, event_id: "event-FS01", event_source: "system_event", start_ms: 1600, end_ms: 2900,
  reviewer_id: "coach-one", reviewer_name: "评审甲", reviewer_role: "coach", observability: "observable", status: "graded", grade: "B", reason_zh: "下降动作可见，但准备节奏略慢。", next_step_zh: "先练习连续的轻微预加载。" });
const entryOf = () => ({ ...inputOf(), review_id: reviewId, entry_revision: 1, created_at: "2026-10-05T03:00:00Z", source_binding_current: true });

test("human review ledger keeps its job and source bindings and never becomes a machine grade", () => {
  const ledger = ledgerOf(); ledger.entries = [entryOf()]; ledger.revision = 1;
  assert.equal(parseTechnicalReview(ledger, jobId).entries[0].grade, "B");
  for (const mutate of [
    value => { value.job_id = "other-job"; }, value => { value.formal_grade = "B"; }, value => { value.calibration_eligible = true; },
    value => { value.artifact_context_sha256 = "missing"; }, value => { value.entries[0].observability = "partial"; },
    value => { value.entries[0].source_sha256 = "d".repeat(64); }, value => { value.entries[0].end_ms = 9500; },
    value => { value.entries[0].event_id = "wrong-event"; }, value => { value.entries[0].grade = "98"; },
  ]) { const malformed = structuredClone(ledger); mutate(malformed); assert.throws(() => parseTechnicalReview(malformed, jobId)); }
});

test("stale source reviews remain history but cannot be revised against a changed source", () => {
  const ledger = ledgerOf(), entry = entryOf(); entry.source_binding_current = false; entry.source_sha256 = "d".repeat(64); entry.end_ms = 12000; entry.player_id = 99;
  ledger.entries = [entry]; ledger.revision = 1;
  assert.equal(parseTechnicalReview(ledger, jobId).entries[0].source_binding_current, false);
  assert.match(technicalReviewError({ ...inputOf(), expected_revision: 1, review_id: reviewId }, ledger), /来源/);
});

test("save validation rejects wrong person, event, revision, source and immutable revision binding", () => {
  const ledger = ledgerOf(); assert.equal(technicalReviewError(inputOf(), ledger), null);
  for (const patch of [{ player_id: 2 }, { start_ms: 0 }, { end_ms: 4000 }, { expected_revision: 1 }, { source_sha256: "d".repeat(64) },
    { artifact_context_sha256: "d".repeat(64) }, { event_id: "other" }, { reviewer_name: " " }, { reason_zh: " " }, { reason_zh: "字".repeat(4001) },
    { observability: "partial" }, { event_source: "manual_interval" }]) assert.ok(technicalReviewError({ ...inputOf(), ...patch }, ledger));
  const entry = entryOf(); ledger.entries = [entry]; ledger.revision = 1;
  const edit = { ...inputOf(), review_id: reviewId, expected_revision: 1, grade: "C", reason_zh: "复核后发现准备不连续。" };
  assert.equal(technicalReviewError(edit, ledger), null);
  assert.match(technicalReviewError({ ...edit, reviewer_name: "另一个人" }, ledger), /修订/);
});

test("source conflicts allow an explanation of inability but no invented A-E", () => {
  const ledger = ledgerOf();
  const blocked = { ...inputOf(), indicator_id: "GS01-M10-04", event_source: "manual_interval", event_id: null };
  assert.match(technicalReviewError(blocked, ledger), /动作条件不足/);
  assert.equal(technicalReviewError({ ...blocked, status: "unassessable", grade: null, reason_zh: "原文混入下一节标题，待确认。" }, ledger), null);
  assert.match(technicalReviewError({ ...blocked, status: "unassessable", grade: "E" }, ledger), /不能附带/);
});

test("visual requirements only accept observed states, preserving optional non-penalty semantics", () => {
  const ledger = ledgerOf();
  const visual = { ...inputOf(), target_kind: "visual_rule", indicator_id: null, visual_rule_id: "VIS-NET-T003-KP01-C01", event_source: "manual_interval", event_id: null, status: "not_observed", grade: null };
  assert.equal(technicalReviewError(visual, ledger), null);
  assert.match(technicalReviewError({ ...visual, grade: "E" }, ledger), /不产生技术等级/);
  assert.match(technicalReviewError({ ...visual, observability: "partial" }, ledger), /清楚判断/);
  assert.equal(technicalReviewError({ ...visual, status: "unassessable", observability: "partial" }, ledger), null);
  ledger.entries = [{ ...visual, review_id: reviewId, entry_revision: 1, created_at: "2026-10-05", source_binding_current: true }];
  assert.equal(parseTechnicalReview(ledger, jobId).visual_rules[0].optional_in_source, true);
});

test("interval input never converts blank, negative or out-of-video fields into valid timestamps", () => {
  assert.deepEqual(reviewInterval("1.125", "2.001", 3000), { start_ms: 1125, end_ms: 2001 });
  for (const [start, end] of [["", "2"], ["-1", "2"], ["1", "1"], ["1e3", "2000"], ["1.0001", "2"], ["0", "3.001"]]) assert.equal(reviewInterval(start, end, 3000), null);
});

function sourceResult() {
  return { job_id: jobId, input: { video: { duration_ms: 8000 } }, source_aligned_assessment: { version: "source-aligned-assessment-v1.0.0", status: "available", score_semantics: "source_aligned_measurement_not_technical_grade", technical_grade: null,
    indicators: [{ indicator_id: "FS01-M02", windows: [{ event_id: "event-1", person_track_id: 1, start_ms: 1500, end_ms: 3000,
      measurements: [{ feature_name: "hip_drop", label_zh: "髋部下降幅度", value: 0, unit: "body", status: "measured", reason_zh: "有实际测量", source_requirement_zh: "原文下降量", measurement_window: { start_ms: 1500, end_ms: 4000 } }],
      visibility: { status: "sufficient", valid_frame_ratio: .7, valid_frame_count: 7, total_frame_count: 10, required_joint_ids: ["left_hip", "right_hip"], threshold_ratio: .7, scope: "indicator_window", reason_zh: "同一窗口所需点同时有效" }, limitations_zh: ["仍是二维测量。"] }] }] } };
}
test("source-aligned measured zero and cross-event computation windows remain explicit", () => {
  const windows = sourceReviewWindows(sourceResult(), "FS01-M02");
  assert.equal(windows[0].measurements[0].value, 0);
  assert.equal(windows[0].visibility.valid_frame_ratio, .7);
  assert.equal(windows[0].measurements[0].measurement_window.end_ms, 4000);
  assert.equal(sourceReviewWindows({ job_id: jobId }, "FS01-M02").length, 0);
  const mismatched = sourceResult(); mismatched.source_aligned_assessment.indicators[0].windows[0].visibility.valid_frame_count = 6;
  assert.equal(sourceReviewWindows(mismatched, "FS01-M02")[0].visibility.valid_frame_ratio, null);
  const global = sourceResult(); global.source_aligned_assessment.indicators[0].windows[0].visibility.scope = "whole_video";
  assert.equal(sourceReviewWindows(global, "FS01-M02")[0].visibility.valid_frame_ratio, null);
  const unavailable = sourceResult(); unavailable.source_aligned_assessment.indicators[0].windows[0].measurements[0].status = "unavailable";
  assert.equal(sourceReviewWindows(unavailable, "FS01-M02")[0].measurements[0].value, null);
  const wrongSource = sourceResult(); wrongSource.source_aligned_assessment.score_semantics = "technical_quality";
  assert.deepEqual(sourceReviewWindows(wrongSource, "FS01-M02"), []);
  const outOfBounds = sourceResult(); outOfBounds.source_aligned_assessment.indicators[0].windows[0].measurements[0].measurement_window.end_ms = 8001;
  assert.equal(sourceReviewWindows(outOfBounds, "FS01-M02")[0].measurements[0].measurement_window, null);
  const noDuration = sourceResult(); delete noDuration.input;
  assert.deepEqual(sourceReviewWindows(noDuration, "FS01-M02"), []);
});

test("candidate reference displacement source consumer and two backups retain negative, zero and exact calculation evidence", async () => {
  const result = sourceResult(), source = result.source_aligned_assessment.indicators[0].windows[0];
  result.actions = [{ event_code: "FS01", name_zh: "分腿垫步", detected_segments: 1 }];
  source.measurements = [["preload_hip_reference_dy_px", "px", -40], ["preload_hip_reference_dy_torso", "torso", 0]].map(([feature_name, unit, value]) => ({
    ...source.measurements[0], ...structuredClone(preloadDetails), feature_name, unit, value,
    definition: "候选窗内稳定参考髋纵向位移，向下为正；不确认正式阶段。",
  }));
  const before = sourceReviewWindows(result, "FS01-M02");
  for (const [index, row] of before[0].measurements.entries()) {
    for (const key of ["value", "definition", "source_ref", "source_frames", "evidence"]) assert.deepEqual(row[key], source.measurements[index][key], key);
    assert.equal(row.measurement_provenance, undefined); assert.equal(row.source_window, undefined);
  }
  let restored = result;
  for (let pass = 0; pass < 2; pass++) {
    const text = JSON.stringify(buildReportBackup({ mode: "live", uploadState: "complete", evidence: { result: restored, jobId } }));
    restored = (await readReportJson({ size: Buffer.byteLength(text), text: async () => text })).result;
    assert.deepEqual(sourceReviewWindows(restored, "FS01-M02"), before);
  }
  const html = renderToStaticMarkup(createElement(Panel, { result: restored, catalog }));
  assert.match(html, /-40 像素/); assert.match(html, /0 固定躯干尺度/); assert.match(html, /向下为正/); assert.match(html, /不是技术等级阈值/);
});

test("only measured zero-duration transition quantities can carry a same-frame computation window", () => {
  const result = sourceResult(), measurement = result.source_aligned_assessment.indicators[0].windows[0].measurements[0];
  measurement.measurement_window = { start_ms: 3000, end_ms: 3000 };
  assert.equal(sourceReviewWindows(result, "FS01-M02")[0].measurements[0].measurement_window, null);
  for (const name of ["landing_proxy_to_next_fs02_ms", "stable_control_proxy_to_next_fs10_or_fs02_ms"]) {
    measurement.feature_name = name; measurement.unit = "ms"; measurement.value = 0;
    assert.equal(sourceReviewWindows(result, "FS01-M02")[0].measurements[0].measurement_window.start_ms, 3000);
    const html = renderToStaticMarkup(createElement(Panel, { result, catalog })); assert.match(html, /同帧衔接/);
    measurement.value = 1; assert.equal(sourceReviewWindows(result, "FS01-M02")[0].measurements[0].measurement_window, null);
  }
});

test("default evidence prioritizes measured windows while preserving complete and missing counts", () => {
  const result = sourceResult(), indicator = result.source_aligned_assessment.indicators[0], measured = indicator.windows[0];
  const earlyMissing = structuredClone(measured); earlyMissing.event_id = "early-missing"; earlyMissing.start_ms = 0; earlyMissing.end_ms = 1000;
  earlyMissing.measurements[0].status = "unavailable"; earlyMissing.measurements[0].reason_zh = "早段证据缺失";
  indicator.windows.unshift(earlyMissing); Object.assign(indicator, { window_count: 212, measured_window_count: 49, unavailable_window_count: 163, returned_window_count: 2, is_truncated: true });
  const windows = sourceReviewWindows(result, "FS01-M02"), coverage = sourceReviewCoverage(result, "FS01-M02");
  assert.equal(windows[0].event_id, "early-missing");
  assert.equal(sourceWindowDisplay(windows, "measured")[0].event_id, "event-1");
  assert.equal(sourceWindowDisplay(windows, "unavailable")[0].event_id, "early-missing");
  assert.deepEqual(sourceWindowDisplay(windows, "all").map(row => row.event_id), ["event-1", "early-missing"]);
  assert.deepEqual([coverage.measured, coverage.unavailable, coverage.total, coverage.returned, coverage.truncated], [49, 163, 212, 2, true]);
  const html = renderToStaticMarkup(createElement(Panel, { result, catalog }));
  assert.match(html, /49 \/ 212/); assert.match(html, /163 个未测得/); assert.match(html, /未测得片段/); assert.match(html, /全部已返回/);
  assert.doesNotMatch(html, /早段证据缺失/); assert.match(html, /跨事件测量区间/);
  indicator.measured_window_count = 300;
  const malformed = sourceReviewCoverage(result, "FS01-M02"); assert.equal(malformed.countsScope, "returned"); assert.equal(malformed.measured, 1);
});

test("API normalization and portable backup keep validated source measurements, never promote a forged grade", async () => {
  const raw = sourceResult(); raw.actions = [{ event_code: "FS01", name_zh: "分腿垫步", detected_segments: 1 }];
  const client = createApiClient({}, async () => Response.json(raw));
  const normalized = await client.getDemoResult(jobId);
  assert.equal(sourceReviewWindows(normalized, "FS01-M02")[0].measurements[0].value, 0);
  const input = { mode: "live", uploadState: "complete", evidence: { result: normalized, jobId } };
  const restored = JSON.parse(JSON.stringify(buildReportBackup(input))).result;
  assert.equal(sourceReviewWindows(restored, "FS01-M02")[0].measurements[0].measurement_window.end_ms, 4000);
  assert.equal(sourceReviewWindows(restored, "FS01-M02")[0].visibility.valid_frame_ratio, .7);
  const readable = buildPracticeReport(input);
  assert.ok(readable.sections.find(section => section.title === "测量与对应视频时段").rows.length);
  assert.ok(readable.sections.every(section => !/原文|标注|评审/.test(section.title)));
  normalized.source_aligned_assessment.technical_grade = "A";
  assert.equal(buildReportBackup(input).result.source_aligned_assessment, undefined);
});

test("source measurements retain evidence and replay without manual grading controls", () => {
  const html = renderToStaticMarkup(createElement(Panel, { result: sourceResult(), catalog }));
  assert.match(html, /逐项动作测量/);
  assert.match(html, /髋部下降幅度/);
  assert.match(html, /跨事件测量区间/);
  assert.match(html, /回看/);
  assert.match(html, /70%/);
  assert.doesNotMatch(html, /按原文评审|选择人工技术等级|身份（自行声明）|保存这条评审|导出评审|录入技术评审|人工等级|<textarea/);
});

test("actual head and launch source evidence remains inspectable and survives repeated portable backup", () => {
  const before = new Map();
  for (const id of ["FS01-M01", "FS02-M01"]) {
    const windows = sourceReviewWindows(actualSource, id);
    before.set(id, windows);
    for (const window of windows) for (const measurement of window.measurements) {
      const original = actualSource.source_aligned_assessment.indicators.find(row => row.indicator_id === id).windows.find(row => row.event_id === window.event_id).measurements.find(row => row.feature_name === measurement.feature_name);
      for (const key of ["definition", "source_ref", "source_frames", "coverage", "evidence"]) assert.deepEqual(measurement[key], original[key], `${id}/${measurement.feature_name}/${key}`);
      assert.equal(measurement.value, original.value);
    }
  }
  const input = result => ({ mode: "live", uploadState: "complete", evidence: { result, jobId: actualSource.job_id } });
  const once = JSON.parse(JSON.stringify(buildReportBackup(input(actualSource)))).result;
  const twice = JSON.parse(JSON.stringify(buildReportBackup(input(once)))).result;
  for (const id of ["FS01-M01", "FS02-M01"]) {
    assert.deepEqual(sourceReviewWindows(once, id), before.get(id));
    assert.deepEqual(sourceReviewWindows(twice, id), before.get(id));
    assert.deepEqual(sourceReviewCoverage(twice, id), sourceReviewCoverage(actualSource, id));
  }
  const html = renderToStaticMarkup(createElement(Panel, { result: actualSource, catalog, initialIndicatorId: "FS01-M01" }));
  assert.match(html, /计算说明/); assert.doesNotMatch(html, /P0080|原文|word\/document\.xml/i); assert.match(html, /180°/); assert.match(html, /不跨缺测补帧/);
  assert.match(html, /29 \/ 41/); assert.match(html, /12 个未测得/); assert.doesNotMatch(html, /<details[^>]*open|"used_source_frames"/);
});

test("real SAM source windows and metric proof survive API normalization and two backup round trips", async () => {
  const client = createApiClient({}, async () => Response.json(samSource));
  const normalized = await client.getDemoResult(samSource.job_id);
  const backup = async value => { const text = JSON.stringify(buildReportBackup({ mode: "live", uploadState: "complete", evidence: { result: value, jobId: value.job_id } }));
    return (await readReportJson({ size: Buffer.byteLength(text), text: async () => text })).result; };
  const restored = await backup(await backup(normalized));
  for (const id of ["FS01-M01", "FS02-M01"]) {
    const before = sourceReviewWindows(samSource, id), after = sourceReviewWindows(restored, id);
    assert.equal(before.length, 1); assert.deepEqual(after, before);
    assert.equal(after[0].visibility.valid_frame_ratio, null); assert.equal(after[0].visibility.visibility_verified, false);
    for (const metric of after[0].measurements) {
      assert.equal(metric.status, "measured"); assert.equal(metric.coverage, undefined);
      assert.equal(metric.measurement_provenance.scoring_allowed, false);
      assert.equal(metric.model_geometry_coverage.source_visibility_70_percent_verified, null);
      assert.ok(metric.source_window.source_bindings.length > 2);
    }
  }
  const html = renderToStaticMarkup(createElement(Panel, { result: restored, catalog, initialIndicatorId: "FS01-M01" }));
  assert.match(html, /SAM 单目模型估计/); assert.match(html, /真实可见性未知/); assert.match(html, /模型几何覆盖/);
  assert.doesNotMatch(html, /% 有效帧|达到门槛仍需/);
});

test("SAM source mutations cannot survive by dropping provenance on the next import", async () => {
  for (const defect of ["parent_clock", "parent_track", "visibility", "mask", "bounds", "hash", "local_coverage"]) {
    const result = structuredClone(samSource), row = result.source_aligned_assessment.indicators.find(row => row.indicator_id === "FS01-M01");
    const window = row.windows[0], metric = window.measurements[0];
    if (defect === "parent_clock") window.source_window.source_bindings[0].pts += 1;
    if (defect === "parent_track") window.source_track_id += 1;
    if (defect === "visibility") metric.model_geometry_coverage.source_visibility_70_percent_verified = true;
    if (defect === "mask") metric.geometry_mask.nose[0] = !metric.geometry_mask.nose[0];
    if (defect === "bounds") metric.measurement_window.end_ms += 1;
    if (defect === "hash") result.source_aligned_assessment.video_sha256 = "b".repeat(64);
    if (defect === "local_coverage") metric.model_geometry_coverage.fraction = .123;
    const check = value => sourceReviewWindows(value, "FS01-M01")[0].measurements[0];
    assert.equal(check(result).status, "unavailable", defect);
    const text = JSON.stringify(buildReportBackup({ mode: "live", uploadState: "complete", evidence: { result, jobId: result.job_id } }));
    const restored = (await readReportJson({ size: Buffer.byteLength(text), text: async () => text })).result;
    assert.equal(check(restored).status, "unavailable", defect);
  }
});

test("API client binds read, idempotent write and export to the same job", async () => {
  const calls = [], ledger = ledgerOf();
  const client = createApiClient({ baseUrl: "https://example.test", jobsPath: "/v1/jobs" }, async (url, init) => {
    calls.push({ url, init }); return Response.json(ledger);
  });
  await client.getTechnicalReview(jobId);
  await client.saveTechnicalReview(jobId, inputOf());
  await client.exportTechnicalReview(jobId);
  assert.deepEqual(calls.map(call => new URL(call.url).pathname), [`/v1/jobs/${jobId}/technical-review`, `/v1/jobs/${jobId}/technical-review`, `/v1/jobs/${jobId}/technical-review/export`]);
  assert.equal(calls[1].init.method, "POST");
  assert.equal(JSON.parse(calls[1].init.body).artifact_context_sha256, contextSha);
  assert.equal(JSON.parse(calls[1].init.body).mutation_id, mutation);
  assert.ok(calls[1].init.signal);
});

test("gateway scopes review writes and rejects cross-site review mutations", async t => {
  let calls = 0;
  t.mock.method(globalThis, "fetch", async (url, init) => {
    calls++; assert.equal(init.headers.get("authorization"), "Bearer server-key");
    assert.equal(String(url), `http://127.0.0.1:8001/v1/jobs/${jobId}/technical-review`);
    assert.deepEqual(JSON.parse(await new Response(init.body).text()), inputOf());
    return Response.json(ledgerOf());
  });
  const env = { RALLYMATE_API_ORIGIN: "http://127.0.0.1:8001", RALLYMATE_API_KEY: "server-key" };
  const url = `http://127.0.0.1:8003/v1/jobs/${jobId}/technical-review`;
  const response = await proxyAnalysis(new Request(url, { method: "POST", headers: { origin: "http://127.0.0.1:8003", "content-type": "application/json", authorization: "Bearer client-secret" }, body: JSON.stringify(inputOf()) }), env);
  assert.equal(response.status, 200); assert.equal(calls, 1);
  for (const [target, method, headers] of [[url, "POST", { origin: "https://other.test" }], [url, "POST", { "sec-fetch-site": "cross-site" }], [`${url}/export`, "POST", {}], [url, "DELETE", {}], [url, "PUT", {}]]) {
    const rejected = await proxyAnalysis(new Request(target, { method, headers }), env);
    assert.ok([403, 404].includes(rejected.status));
  }
  assert.equal(calls, 1);
});
