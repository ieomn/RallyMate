import assert from "node:assert/strict";
import fs from "node:fs";
import { createHash } from "node:crypto";
import test from "node:test";
import ts from "typescript";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

const cache = new Map();
function compiledUrl(file) {
  // Keep source JSON imports shared with production, without a generated copy.
  if (file.pathname.endsWith(".json")) return `data:text/javascript;base64,${Buffer.from(`export default ${fs.readFileSync(file, "utf8")}`).toString("base64")}`;
  if (cache.has(file.href)) return cache.get(file.href);
  let js = ts.transpileModule(fs.readFileSync(file, "utf8"), { fileName: file.pathname, compilerOptions: {
    module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX,
  } }).outputText;
  js = js.replace(/from "([^"]+)"/g, (_match, name) => {
    const local = new URL(name.endsWith(".json") ? name : `${name}.ts`, file);
    return `from ${JSON.stringify(name.startsWith(".") ? compiledUrl(fs.existsSync(local) ? local : new URL(`${name}.tsx`, file)) : import.meta.resolve(name))}`;
  });
  const url = `data:text/javascript;base64,${Buffer.from(js).toString("base64")}`;
  cache.set(file.href, url); return url;
}
const { contextualHipMeasurements, contextualHipForEpisode, isOpaqueSourceIdentity, sourceAssessmentForExport } = await import(compiledUrl(new URL("../app/lib/technical-review.ts", import.meta.url)));
const { default: Panel } = await import(compiledUrl(new URL("../app/FootworkReviewPanel.tsx", import.meta.url)));
const { buildReportBackup, buildPracticeReport } = await import(compiledUrl(new URL("../app/lib/report-export.ts", import.meta.url)));
const { readReportJson } = await import(compiledUrl(new URL("../app/lib/report-file.ts", import.meta.url)));

// Synthetic transport fixtures test binding, failure and export behavior only.
// These values are not model output, accuracy evidence, or the actual native41 result.
const sha = "a".repeat(64), inputSha = "b".repeat(64);
function fixture() {
  const frames = [12, 13, 14], times = [1200, 1300, 1400];
  const sourceBindings = frames.map((frame, i) => ({ video_sha256: sha, frame_index: frame, processed_index: frame,
    pts: times[i], time_base_num: 1, time_base_den: 1000, timestamp_ms: times[i], person_track_id: 7, selection_epoch: 2, camera_reference_epoch: 3 }));
  const window = { start_ms: 1200, end_ms: 1400, source_frames: frames, video_timestamps_ms: times, source_timestamps_ms: times,
    video_sha256: sha, person_track_id: 7, selection_epoch: 2, camera_reference_epoch: 3, source_bindings: sourceBindings };
  const row = { type: "hip_path_ratio_2d", version: "hip-contextual-measurement-v1.0.0", helper_version: "bounded-hip-path-ratio-v1.0.0",
    event_id: "candidate-fs02", event_code: "FS02", video_id: "native-video", source_id: "native-source", video_sha256: sha,
    person_track_id: 1, status: "measured", reason: "bounded_image_plane_geometry_only",
    hip_path_length_px: 12, hip_net_displacement_px: 8, hip_path_over_net_ratio: 1.5,
    parent_window_ms: { start_ms: 1200, end_ms: 1400 }, measurement_window_video_ms: { start_ms: 1200, end_ms: 1400 },
    source_frame_bounds: { start_source_frame: 12, end_source_frame: 14 }, source_window: window,
    source_bindings: sourceBindings.map(({ timestamp_ms, ...binding }) => ({ ...binding, timestamp_source: "decoder_pts", primary_player_id: 1,
      source_timestamp_ms: timestamp_ms, video_timestamp_ms: timestamp_ms })),
    source_reference: { video_sha256: sha, primary_player_id: 1, person_track_id: 7, selection_epoch: 2, camera_reference_epoch: 3 },
    adapter: { version: "native-sam-primary-epochs-v1.0.0", provenance: "unique_same_frame_selected_track_SAM_request_and_pose_binding_and_camera", source_files_changed: false },
    input_sha256: { frames: inputSha, primary_timeline: inputSha, events: inputSha },
    window_input_sha256: { records: inputSha, original_primary: inputSha, adapted_primary: inputSha, event: inputSha },
    score: null, grade: null, formal_event_verified: false, scoring_allowed: false, assessment_status: "measurement_only",
    coordinate_system: "stable_camera_reference_image_pixels", sampling_protocol: "original_source_frames" };
  const episode = { event_id: row.event_id, event_code: row.event_code, name_zh: "第一步启动", video_id: row.video_id, source_id: row.source_id,
    person_track_id: 1, start_ms: 1200, end_ms: 1400, source_window: structuredClone(window), indicators: [] };
  const assessment = { version: "source-aligned-assessment-v1.0.0", status: "available", score_semantics: "source_aligned_measurement_not_technical_grade",
    technical_grade: null, technical_score_0_to_100: null, video_id: row.video_id, source_id: row.source_id, video_sha256: sha,
    indicators: [], contextual_measurements: [row] };
  return { job_id: "restored-display-job", video_id: row.video_id, input: { video: { duration_ms: 2000 } }, source_aligned_assessment: assessment,
    action_recognition: { status: "candidates_detected", candidates: [] },
    footwork_review: { schema_version: "1.1.0", status: "available", episode_count: 1, episodes: [episode] } };
}
const getRow = result => result.source_aligned_assessment.contextual_measurements[0];
const getEpisode = result => result.footwork_review.episodes[0];
const panel = result => renderToStaticMarkup(createElement(Panel, { review: result.footwork_review, jobId: result.job_id, sourceAssessment: result.source_aligned_assessment }));
const exportInput = result => ({ mode: "live", uploadState: "complete", evidence: { jobId: result.job_id, result } });
const reload = value => readReportJson(new File([JSON.stringify(value)], "report.json", { type: "application/json" }));
const setSource = (result, identity) => {
  result.source_aligned_assessment.source_id = identity; getRow(result).source_id = identity; getEpisode(result).source_id = identity;
};
function unavailable(row, reason) {
  Object.assign(row, { status: "unavailable", reason, hip_path_length_px: null, hip_net_displacement_px: null, hip_path_over_net_ratio: null });
}
async function rejectsThroughBackups(result, label) {
  for (let round = 0; round < 3; round++) {
    assert.deepEqual(contextualHipMeasurements(result), [], `${label}, round ${round}`);
    assert.equal(contextualHipForEpisode(contextualHipMeasurements(result), getEpisode(result)), null, label);
    if (round < 2) result = (await reload(buildReportBackup(exportInput(result)))).result;
  }
}

test("contextual geometry joins exact event/source while canonical and native subjects remain distinct", () => {
  const result = fixture(), before = structuredClone(result), rows = contextualHipMeasurements(result);
  assert.equal(rows.length, 1);
  assert.equal(rows[0].person_track_id, 1); assert.equal(rows[0].source_reference.person_track_id, 7);
  assert.deepEqual(contextualHipForEpisode(rows, getEpisode(result)), rows[0]);
  const html = panel(result);
  assert.match(html, /路径长度：12 像素/); assert.match(html, /起终点净位移：8 像素/); assert.match(html, /路径\/净位移比：1.5/);
  assert.match(html, /原事件 FS02/); assert.match(html, /原帧 12–14，共 3 帧/);
  assert.deepEqual(result, before, "render and parser must not relabel events or change grade/count/state");
});

test("time overlap and matching names never replace event/video/source identity or whole-window proof", () => {
  const changes = [e => { e.event_id = "different"; }, e => { e.event_code = "FS01"; }, e => { e.source_id = "other-source"; },
    e => { delete e.source_id; }, e => { e.video_id = "other-video"; }, e => { e.person_track_id = 7; }, e => { e.end_ms = 1401; },
    e => { e.source_window.source_bindings[1].pts++; }, e => { e.source_window.camera_reference_epoch++; }, e => { e.source_window.video_sha256 = inputSha; }];
  for (const change of changes) {
    const result = fixture(); change(getEpisode(result));
    assert.equal(contextualHipForEpisode(contextualHipMeasurements(result), getEpisode(result)), null);
    assert.doesNotMatch(panel(result), /路径长度：12/);
  }
});

test("legacy missing-field and missing container identity remain compatible without importing measurement identity", () => {
  for (const change of [r => { delete r.source_aligned_assessment; }, r => { delete r.source_aligned_assessment.contextual_measurements; },
    r => { delete r.source_aligned_assessment.source_id; }, r => { r.source_aligned_assessment.video_id = "other-video"; },
    r => { r.source_aligned_assessment.video_sha256 = inputSha; }]) {
    const result = fixture(); change(result);
    assert.deepEqual(contextualHipMeasurements(result), []);
    assert.match(panel(result), /步伐逐段复核/); assert.doesNotMatch(panel(result), /路径长度：12/);
  }
});

test("source identity is opaque and exact, not trimmed, renamed, or copied from the display job", async () => {
  const result = fixture(), identity = " 原始来源·甲 ";
  result.source_aligned_assessment.source_id = identity; getRow(result).source_id = identity; getEpisode(result).source_id = identity;
  const rows = contextualHipMeasurements(result);
  assert.equal(rows.length, 1); assert.equal(rows[0].source_id, identity);
  assert.ok(contextualHipForEpisode(rows, getEpisode(result)));
  const imported = await reload(buildReportBackup(exportInput(result)));
  assert.equal(getEpisode(imported.result).source_id, identity);
  assert.deepEqual(contextualHipMeasurements(imported.result), rows);
  getEpisode(result).source_id = identity.trim();
  assert.equal(contextualHipForEpisode(rows, getEpisode(result)), null);
  for (const invalid of ["", " ", "x".repeat(257), null, 42]) {
    result.source_aligned_assessment.source_id = invalid; getRow(result).source_id = invalid;
    assert.deepEqual(contextualHipMeasurements(result), []);
  }
});

test("opaque source uses Python codepoint length and whitespace rules through two backup imports", async () => {
  // Python str.isspace/strip set, including C0 separators and NEL absent from JS trim.
  const whitespace = [...Array.from({ length: 5 }, (_, i) => i + 9), ...Array.from({ length: 5 }, (_, i) => i + 0x1c),
    0x85, 0xa0, 0x1680, ...Array.from({ length: 11 }, (_, i) => i + 0x2000), 0x2028, 0x2029, 0x202f, 0x205f, 0x3000]
    .map(code => String.fromCodePoint(code));
  for (const identity of [...whitespace, whitespace.join(""), "🎾".repeat(257)]) {
    assert.equal(isOpaqueSourceIdentity(identity), false, JSON.stringify(identity));
    const result = fixture(); setSource(result, identity);
    assert.deepEqual(contextualHipMeasurements(result), []);
  }
  // FEFF and zero-width space are non-whitespace in Python; neighbors of its
  // whitespace ranges and embedded whitespace must keep their original codepoints.
  for (const identity of ["🎾".repeat(256), "\ufeff", "\u200b", "\u0084", "\u0086", "\u001b", "\u200c",
    `${whitespace.join("")}来源${whitespace.join("")}`]) {
    assert.equal(isOpaqueSourceIdentity(identity), true, JSON.stringify(identity));
    let result = fixture(); setSource(result, identity);
    const original = contextualHipMeasurements(result);
    assert.equal(original.length, 1); assert.ok(contextualHipForEpisode(original, getEpisode(result)));
    for (let round = 0; round < 2; round++) {
      result = (await reload(buildReportBackup(exportInput(result)))).result;
      assert.equal(result.source_aligned_assessment.source_id, identity);
      assert.equal(getEpisode(result).source_id, identity);
      assert.deepEqual(contextualHipMeasurements(result), original);
      assert.ok(contextualHipForEpisode(original, getEpisode(result)));
    }
  }
});

test("runtime event and status enums reject JSON arrays instead of coercing their strings", async () => {
  for (const change of [r => { getRow(r).event_code = ["FS02"]; }, r => { getRow(r).event_code = { value: "FS02" }; },
    r => { getRow(r).status = ["measured"]; }, r => { r.source_aligned_assessment.status = ["available"]; }]) {
    const result = fixture(); change(result);
    await rejectsThroughBackups(JSON.parse(JSON.stringify(result)), "runtime enum type");
  }
});

test("finite inputs with an overflowed quotient cannot become a measured ratio on import", async () => {
  const result = fixture();
  Object.assign(getRow(result), { hip_path_length_px: 1e308, hip_net_displacement_px: 1e-308, hip_path_over_net_ratio: 1 });
  assert.equal(getRow(result).hip_path_length_px / getRow(result).hip_net_displacement_px, Infinity);
  await rejectsThroughBackups(result, "overflowed quotient");
  const valid = fixture();
  Object.assign(getRow(valid), { hip_path_length_px: 60, hip_net_displacement_px: 1e-9, hip_path_over_net_ratio: 60000000000 });
  assert.equal(contextualHipMeasurements(valid).length, 1, "a large but finite ratio is still legitimate");
  const imported = await reload(buildReportBackup(exportInput(valid)));
  assert.deepEqual(contextualHipMeasurements(imported.result), contextualHipMeasurements(valid));
});

test("unavailable reasons enforce their distinct pre-helper and helper evidence requirements", async () => {
  const cases = [
    ["unknown reason", r => unavailable(r, "invented_unrecognized_reason")],
    ["pre-helper forbids bindings", r => { unavailable(r, "contextual_source_window_incomplete"); r.source_reference = null; }],
    ["pre-helper forbids reference", r => { unavailable(r, "contextual_source_window_incomplete"); r.source_bindings = []; }],
    ...["records", "original_primary"].map(key => [`pre-helper requires ${key}`, r => {
      unavailable(r, "contextual_source_window_incomplete"); r.source_bindings = []; r.source_reference = null; r.window_input_sha256[key] = null;
    }]),
    ["helper requires bindings", r => { unavailable(r, "hip_geometry_unavailable"); r.source_bindings = []; r.source_reference = null; }],
    ["helper requires adapted digest", r => { unavailable(r, "hip_geometry_unavailable"); r.window_input_sha256.adapted_primary = null; }],
    ["missing-window reason forbids complete window", r => { unavailable(r, "contextual_event_source_window_unavailable"); r.source_bindings = []; r.source_reference = null; }],
    ["ordinary failure forbids bounded digest", r => { unavailable(r, "contextual_measurement_failed"); r.source_bindings = []; r.source_reference = null; }],
    ["helper reason requires complete window", r => {
      unavailable(r, "hip_geometry_unavailable"); r.source_window = null; r.source_bindings = []; r.source_reference = null;
      r.source_frame_bounds = null; r.measurement_window_video_ms = null;
      Object.assign(r.window_input_sha256, { records: null, original_primary: null, adapted_primary: null });
    }],
    ...["records", "original_primary", "adapted_primary"].map(key => [`absent window forbids ${key}`, r => {
      unavailable(r, "contextual_event_source_window_unavailable"); r.source_window = null; r.source_bindings = []; r.source_reference = null;
      r.source_frame_bounds = null; r.measurement_window_video_ms = null;
      Object.assign(r.window_input_sha256, { records: null, original_primary: null, adapted_primary: null, [key]: inputSha });
    }]),
  ];
  for (const [label, change] of cases) {
    const result = fixture(); change(getRow(result));
    await rejectsThroughBackups(result, label);
  }
  for (const reason of ["contextual_source_window_incomplete", "contextual_native_adapter_rejected", "hip_geometry_unavailable", "nonfinite_ratio"]) {
    let result = fixture(); unavailable(getRow(result), reason);
    const row = getRow(result);
    if (reason.startsWith("contextual_")) {
      row.source_bindings = []; row.source_reference = null; row.window_input_sha256.adapted_primary = null;
    } else row.source_reference = null; // Helper failures may precede a verified reference.
    const expected = contextualHipMeasurements(result);
    assert.equal(expected.length, 1, reason); assert.ok(contextualHipForEpisode(expected, getEpisode(result)));
    for (let round = 0; round < 2; round++) {
      result = (await reload(buildReportBackup(exportInput(result)))).result;
      assert.deepEqual(contextualHipMeasurements(result), expected, reason);
    }
  }
});

test("forged grades, inconsistent arithmetic, incomplete frames and camera/PTS changes fail closed", () => {
  const changes = [r => { r.score = 0; }, r => { r.grade = "A"; }, r => { r.scoring_allowed = true; }, r => { r.formal_event_verified = true; },
    r => { r.status = "scored"; }, r => { r.helper_version = "future"; }, r => { r.event_code = "FS05"; },
    r => { r.hip_path_over_net_ratio = 99; }, r => { r.hip_path_length_px = Infinity; }, r => { r.hip_net_displacement_px = -1; },
    r => { r.source_window.source_frames.splice(1, 1); }, r => { r.source_bindings[1].camera_reference_epoch++; },
    r => { r.source_bindings[1].pts++; }, r => { r.source_reference.primary_player_id = 7; }, r => { r.measurement_window_video_ms.end_ms--; }];
  for (const change of changes) { const result = fixture(); change(getRow(result)); assert.deepEqual(contextualHipMeasurements(result), []); }
});

test("duplicate valid contextual items for one event are all omitted", () => {
  const result = fixture(); result.source_aligned_assessment.contextual_measurements.push(structuredClone(getRow(result)));
  assert.deepEqual(contextualHipMeasurements(result), []);
  assert.deepEqual(sourceAssessmentForExport(result).contextual_measurements, []);
});

test("independent contextual evidence does not promote an unavailable original indicator container", async () => {
  const result = fixture(); result.source_aligned_assessment.status = "unavailable";
  assert.equal(contextualHipMeasurements(result).length, 1);
  assert.match(panel(result), /路径长度：12 像素/);
  const imported = await reload(buildReportBackup(exportInput(result)));
  assert.equal(imported.result.source_aligned_assessment.status, "unavailable");
  assert.deepEqual(imported.result.source_aligned_assessment.indicators, []);
  assert.equal(contextualHipMeasurements(imported.result).length, 1);
  assert.equal(imported.result.source_aligned_assessment.technical_grade, null);
});

test("zero net displacement keeps real zero distances and unavailable ratio through display and backup import", async () => {
  const result = fixture(), row = getRow(result);
  Object.assign(row, { status: "unavailable", reason: "zero_net_displacement", hip_path_length_px: 0, hip_net_displacement_px: 0, hip_path_over_net_ratio: null });
  const html = panel(result);
  assert.match(html, /路径长度：0 像素/); assert.match(html, /起终点净位移：0 像素/); assert.match(html, /路径\/净位移比：不可用/);
  assert.match(html, /净位移为 0/);
  const imported = await reload(buildReportBackup(exportInput(result)));
  assert.deepEqual(contextualHipMeasurements(imported.result), contextualHipMeasurements(result));
  assert.match(panel(imported.result), /路径\/净位移比：不可用/);
});

test("optional ordinary failure keeps the original whole window and explicit reason with no fabricated numeric value", async () => {
  const result = fixture(), row = getRow(result);
  Object.assign(row, { status: "unavailable", reason: "contextual_measurement_failed", hip_path_length_px: null,
    hip_net_displacement_px: null, hip_path_over_net_ratio: null, source_bindings: [], source_reference: null });
  Object.assign(row.window_input_sha256, { records: null, original_primary: null, adapted_primary: null });
  assert.equal(contextualHipMeasurements(result).length, 1);
  assert.match(panel(result), /独立测量计算未完成/); assert.match(panel(result), /路径长度：不可用/);
  const imported = await reload(buildReportBackup(exportInput(result)));
  assert.deepEqual(contextualHipMeasurements(imported.result), contextualHipMeasurements(result));
});

test("missing complete source window can show a bound unavailable reason without inventing frame range", () => {
  const result = fixture(), row = getRow(result);
  Object.assign(row, { status: "unavailable", reason: "contextual_event_source_window_unavailable", hip_path_length_px: null,
    hip_net_displacement_px: null, hip_path_over_net_ratio: null, source_window: null, source_bindings: [], source_reference: null,
    source_frame_bounds: null, measurement_window_video_ms: null });
  Object.assign(row.window_input_sha256, { records: null, original_primary: null, adapted_primary: null });
  getEpisode(result).source_window = { video_sha256: sha };
  const html = panel(result);
  assert.match(html, /未取得完整且可核验的原帧窗口/); assert.doesNotMatch(html, /原帧 12–14/);
  delete getEpisode(result).source_window;
  assert.doesNotMatch(panel(result), /路径长度：不可用/, "missing episode digest cannot be self-certified by the contextual item");
});

test("backup preserves all valid contextual rows beyond the visible event subset, without admitting private paths", async () => {
  const result = fixture(), other = structuredClone(getRow(result)); other.event_id = "not-in-visible-subset";
  result.source_aligned_assessment.contextual_measurements.push(other);
  result.footwork_review.is_truncated = true;
  Object.assign(getRow(result), { filesystem_path: "C:/private/clip.mp4", api_token: "secret-string", unknown_path: 4 });
  Object.assign(getRow(result).source_bindings[0], { nested_path: "C:/private" });
  const backup = buildReportBackup(exportInput(result)), encoded = JSON.stringify(backup);
  assert.doesNotMatch(encoded, /C:\/private|secret-string|filesystem_path|api_token|unknown_path|nested_path/);
  const imported = await reload(backup), rows = contextualHipMeasurements(imported.result);
  assert.equal(rows.length, 2); assert.equal(rows[1].event_id, "not-in-visible-subset");
  assert.equal(rows[0].hip_path_length_px, 12); assert.equal(rows[0].hip_net_displacement_px, 8); assert.equal(rows[0].hip_path_over_net_ratio, 1.5);
  assert.deepEqual(rows[0].source_window, getRow(result).source_window);
  assert.deepEqual(rows[0].input_sha256, getRow(result).input_sha256);
  assert.equal(rows[0].grade, null); assert.equal(rows[0].score, null);
  assert.match(panel(imported.result), /路径长度：12 像素/);
  const report = buildPracticeReport(exportInput(imported.result));
  const section = report.sections.find(row => row.title === "候选事件的髋中心二维测量");
  assert.equal(section.rows.length, 2); assert.match(section.rows[0][0], /^FS02/);
});

test("two backup imports retain 302 synthetic contextual records despite a 300-episode display subset", async () => {
  let result = fixture();
  const template = getRow(result), episode = getEpisode(result);
  result.source_aligned_assessment.contextual_measurements = Array.from({ length: 302 }, (_, i) => ({ ...structuredClone(template), event_id: `transport-only-${i}` }));
  result.footwork_review.episodes = Array.from({ length: 300 }, (_, i) => ({ ...structuredClone(episode), event_id: `transport-only-${i}` }));
  Object.assign(result.footwork_review, { episode_count: 302, is_truncated: true });
  const expected = contextualHipMeasurements(result);
  assert.equal(expected.length, 302); // Repeated synthetic window tests transport, not physical event count.
  for (let round = 0; round < 2; round++) {
    result = (await reload(buildReportBackup(exportInput(result)))).result;
    assert.equal(result.footwork_review.episodes.length, 300);
    assert.deepEqual(contextualHipMeasurements(result), expected);
    assert.equal(contextualHipMeasurements(result).at(-1).event_id, "transport-only-301");
  }
});

test("path-key exceptions admit only exact finite nonnegative geometry numbers or unavailable nulls", async () => {
  const result = fixture();
  result.footwork_review.debug = { hip_path_length_px: "C:/private/secret", hip_path_over_net_ratio: { token: "bad" },
    hip_net_displacement_px: -8, other_path: 12, path_length_torso: "C:/private/secret" };
  const imported = await reload(buildReportBackup(exportInput(result)));
  assert.deepEqual(imported.result.footwork_review.debug, {});
  assert.equal(contextualHipMeasurements(imported.result)[0].hip_path_length_px, 12);
});

test("private locations and credentials cannot escape through an opaque source identity", async () => {
  for (const identity of ["C:/private/clip.mp4", "Bearer private-secret-123"]) {
    const result = fixture(); result.source_aligned_assessment.source_id = identity;
    getRow(result).source_id = identity; getEpisode(result).source_id = identity;
    const backup = buildReportBackup(exportInput(result)), report = buildPracticeReport(exportInput(result));
    assert.doesNotMatch(JSON.stringify([backup, report]), /C:\/private|private-secret-123/);
    const imported = await reload(backup);
    assert.deepEqual(contextualHipMeasurements(imported.result), [], "redacted identity cannot certify a new source");
  }
});

const native41File = new URL("../../.codex_tmp/autodl-sam-coordination-20261018/hip-contextual-episode-source-binding-v1/native41/frontend-source-binding-example.json", import.meta.url);
test("actual local native41 service/footwork projection joins its original FS02 and survives two backup imports", { skip: !fs.existsSync(native41File) }, async () => {
  const bytes = fs.readFileSync(native41File);
  assert.equal(createHash("sha256").update(bytes).digest("hex"), "03adb68fc51ed9e3314c091f7d3dab191ca136d0369dd63e47850b8f4056da6c");
  const example = JSON.parse(bytes), original = structuredClone(example);
  assert.equal(example.example_kind, "saved_native41_local_projection_not_live_API_response");
  // The completion summary is a test envelope for the existing export gate.
  // Neither it nor this composed object is an HTTP response or full-video report.
  let result = { job_id: example.display_job_id, video_id: example.source_job_id,
    training_evaluation: { summary_zh: "本地真实41帧组合样例，仅验证展示和备份，不是线上报告。" },
    source_aligned_assessment: example.source_aligned_assessment, footwork_review: example.footwork_review };
  assert.notEqual(result.job_id, example.source_job_id);
  const [row] = contextualHipMeasurements(result);
  assert.ok(row); assert.equal(row.event_code, "FS02"); assert.equal(row.event_id, "fs02-003-c468002a6cc7");
  assert.equal(row.hip_path_length_px, 194.5261011729798);
  assert.equal(row.hip_net_displacement_px, 178.1290060809591);
  assert.equal(row.hip_path_over_net_ratio, 1.0920517969126726);
  assert.equal(row.source_bindings.length, 41);
  assert.deepEqual(row.source_frame_bounds, { start_source_frame: 159, end_source_frame: 199 });
  assert.deepEqual(contextualHipForEpisode([row], getEpisode(result)), row);
  const html = panel(result);
  assert.match(html, /路径长度：194.526 像素/); assert.match(html, /起终点净位移：178.129 像素/);
  assert.match(html, /路径\/净位移比：1.092/); assert.match(html, /原帧 159–199，共 41 帧/);
  assert.match(html, /原事件 FS02/); assert.match(html, /原生 PTS 3180–3980/);
  const swapped = structuredClone(result); getEpisode(swapped).source_id = result.job_id;
  assert.equal(contextualHipForEpisode([row], getEpisode(swapped)), null, "display job must never replace source identity");
  for (let round = 0; round < 2; round++) {
    const backup = buildReportBackup(exportInput(result)), imported = await reload(backup);
    assert.deepEqual(contextualHipMeasurements(imported.result), [row]);
    assert.deepEqual(contextualHipForEpisode([row], getEpisode(imported.result)), row);
    assert.match(panel(imported.result), /路径长度：194.526 像素/);
    result = imported.result;
  }
  // Reproduce the reviewed malformed imports against the actual bounded parent,
  // not only synthetic identity/window fixtures; no saved source bytes are edited.
  for (const [label, change] of [
    ["native41 unknown failure", r => unavailable(r, "invented_unrecognized_reason")],
    ["native41 pre-helper evidence", r => unavailable(r, "contextual_source_window_incomplete")],
    ["native41 missing helper evidence", r => { unavailable(r, "hip_geometry_unavailable"); r.source_bindings = []; r.source_reference = null; }],
    ["native41 missing pre-helper digest", r => { unavailable(r, "contextual_source_window_incomplete"); r.source_bindings = []; r.source_reference = null; r.window_input_sha256.records = null; }],
    ["native41 overflow", r => Object.assign(r, { hip_path_length_px: 1e308, hip_net_displacement_px: 1e-308, hip_path_over_net_ratio: 1 })],
    ["native41 event array", r => { r.event_code = ["FS02"]; }],
  ]) {
    const malformed = structuredClone(result); change(getRow(malformed));
    await rejectsThroughBackups(malformed, label);
  }
  assert.deepEqual(example, original, "the saved service/footwork structures remain unchanged");
});
