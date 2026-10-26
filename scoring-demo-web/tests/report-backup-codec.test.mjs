import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import ts from "typescript";

const cache = new Map();
function compiledUrl(file) {
  // Keep source JSON imports shared with production, without a generated copy.
  if (file.pathname.endsWith(".json")) return `data:text/javascript;base64,${Buffer.from(`export default ${fs.readFileSync(file, "utf8")}`).toString("base64")}`;
  if (cache.has(file.href)) return cache.get(file.href);
  let js = ts.transpileModule(fs.readFileSync(file, "utf8"), { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
  js = js.replace(/from "([^"]+)"/g, (_match, name) => `from ${JSON.stringify(name.startsWith(".") ? compiledUrl(new URL(name.endsWith(".json") ? name : `${name}.ts`, file)) : import.meta.resolve(name))}`);
  const url = `data:text/javascript;base64,${Buffer.from(js).toString("base64")}`;
  cache.set(file.href, url); return url;
}
const { packReportBackup, unpackReportBackup, unpackApiReport, MAX_MODEL_PROOFS, MAX_MODEL_PROOF_REFERENCES, MAX_EXPANDED_BACKUP_BYTES } = await import(compiledUrl(new URL("../app/lib/report-backup-codec.ts", import.meta.url)));
const { readReportJson, MAX_REPORT_FILE_BYTES } = await import(compiledUrl(new URL("../app/lib/report-file.ts", import.meta.url)));
const { serializeReportBackup } = await import(compiledUrl(new URL("../app/lib/report-export.ts", import.meta.url)));
const { indicatorAssessmentOf } = await import(compiledUrl(new URL("../app/lib/indicator-assessment.ts", import.meta.url)));
const { sourceReviewWindows } = await import(compiledUrl(new URL("../app/lib/technical-review.ts", import.meta.url)));
const { modelMeasurementEvidenceOf } = await import(compiledUrl(new URL("../app/lib/model-measurement.ts", import.meta.url)));
const { footworkEpisodes } = await import(compiledUrl(new URL("../app/lib/footwork-review.ts", import.meta.url)));
const { createApiClient } = await import(compiledUrl(new URL("../app/lib/api-client.ts", import.meta.url)));
const fixture = JSON.parse(fs.readFileSync(new URL("./fixtures/sam-source-observations.json", import.meta.url), "utf8"));
const keys = ["measurement_provenance", "source_window", "geometry_mask", "source_track_id"];
const original = fixture.indicator_assessment.observations[0];
const proof = () => structuredClone(Object.fromEntries(keys.map(key => [key, original[key]])));
const envelope = rows => ({ schemaVersion: "rallymate-practice-report/1", result: { rows } });
const packed = () => packReportBackup(envelope([{ ...proof(), value: 0, confidence: null }, { ...proof(), value: null } ]));
const rejects = change => { const value = packed(); change(value); assert.throws(() => unpackReportBackup(value), /来源证据引用无效/); };

test("packer and decoder accept the same exact expanded-byte boundary including reference fields", async () => {
  const file = new URL("../app/lib/report-backup-codec.ts", import.meta.url);
  async function codec(limit) {
    const source = fs.readFileSync(file, "utf8").replace(/export const MAX_EXPANDED_BACKUP_BYTES = [^;]+;/,
      `export const MAX_EXPANDED_BACKUP_BYTES = ${limit};`);
    let js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
    js = js.replace(/from "([^"]+)"/g, (_match, name) => `from ${JSON.stringify(compiledUrl(new URL(name.endsWith(".json") ? name : `${name}.ts`, file)))}`);
    return import(`data:text/javascript;base64,${Buffer.from(js).toString("base64")}`);
  }
  const raw = envelope([{ ...proof(), value: 0, confidence: null }, { ...proof(), value: null }]);
  let low = 1, high = Buffer.byteLength(JSON.stringify(raw)) * 2;
  while (low < high) {
    const middle = Math.floor((low + high) / 2), candidate = await codec(middle);
    try { candidate.packReportBackup(raw); high = middle; }
    catch { low = middle + 1; }
  }
  const boundary = await codec(low), encoded = boundary.packReportBackup(raw);
  assert.deepEqual(boundary.unpackReportBackup(encoded), raw);
  const below = await codec(low - 1);
  assert.throws(() => below.packReportBackup(raw), /来源证据引用无效/);
  assert.throws(() => below.unpackReportBackup(encoded), /来源证据引用无效/);
});

test("complete proof pooling is lossless, immutable, shared after read and idempotent", () => {
  const raw = envelope([{ ...proof(), value: 0, confidence: null }, { ...proof(), value: null }]), before = structuredClone(raw);
  const encoded = packReportBackup(raw);
  assert.deepEqual(raw, before);
  assert.equal(encoded.model_evidence_pool.length, 1);
  assert.equal(encoded.result.rows[0].model_evidence_ref, 0);
  const restored = unpackReportBackup(JSON.parse(JSON.stringify(encoded)));
  assert.deepEqual(restored, raw);
  assert.equal(restored.result.rows[0].source_window, restored.result.rows[1].source_window);
  assert.ok(Object.isFrozen(restored.result.rows[0].source_window.source_bindings[0]));
  assert.throws(() => { restored.result.rows[0].geometry_mask.nose[0] = false; }, TypeError);
  assert.deepEqual(packReportBackup(restored), encoded);
  assert.deepEqual(unpackReportBackup(packReportBackup(restored)), raw);
});

test("legacy unpooled backup shape, zero and null remain compatible", async () => {
  const raw = envelope([{ value: 0, confidence: null, source_frames: [0, 2] }]);
  const text = JSON.stringify(raw);
  assert.deepEqual(packReportBackup(raw), raw);
  assert.deepEqual(await readReportJson({ size: Buffer.byteLength(text), text: async () => text }), raw);
});

test("ref IDs, pool identity and version are exact and cannot be dangling or unused", () => {
  for (const id of [-1, .1, 1, "0", null, Number.MAX_SAFE_INTEGER + 1]) rejects(value => { value.result.rows[0].model_evidence_ref = id; });
  rejects(value => { value.model_evidence_pool[0].id = 1; });
  rejects(value => { value.model_evidence_pool.push({ ...value.model_evidence_pool[0], id: 1 }); });
  rejects(value => { value.model_evidence_codec = "future"; });
  rejects(value => { delete value.model_evidence_codec; });
  rejects(value => { value.model_evidence_pool = []; });
  rejects(value => { value.result.rows = []; });
  assert.throws(() => unpackReportBackup(envelope([{ model_evidence_ref: 0 }])));
});

test("conflicting, nested, unknown or tampered evidence is rejected before restore", () => {
  for (const key of keys) rejects(value => { value.result.rows[0][key] = original[key]; });
  rejects(value => { value.result.rows[0].model_evidence_pool = []; });
  rejects(value => { value.result.rows[0].nested = { model_evidence_codec: "model-proof-pool-v1" }; });
  rejects(value => { value.model_evidence_pool[0].extra = true; });
  rejects(value => { value.model_evidence_pool[0].evidence.extra = true; });
  rejects(value => { value.model_evidence_pool[0].evidence.source_window.source_bindings[0].extra = true; });
  rejects(value => { value.model_evidence_pool[0].evidence.measurement_provenance.extra = true; });
  rejects(value => { value.model_evidence_pool[0].evidence.source_window.extra = true; });
  rejects(value => { value.model_evidence_pool[0].evidence.source_window.source_bindings[0].model_evidence_ref = 0; });
  rejects(value => { value.model_evidence_pool[0].evidence.source_window.source_bindings[0].pts += 1; });
  rejects(value => { delete value.model_evidence_pool[0].evidence.geometry_mask; });
  rejects(value => { value.model_evidence_pool[0].evidence.measurement_provenance.visibility_verified = true; });
  assert.throws(() => unpackReportBackup(JSON.parse('{"result":{"__proto__":{"polluted":true}}}')));
});

test("compressed inputs have independent reference, pool, depth and expansion budgets", () => {
  rejects(value => { value.model_evidence_pool = Array.from({ length: MAX_MODEL_PROOFS + 1 }, (_, id) => ({ ...value.model_evidence_pool[0], id })); });
  rejects(value => { value.result.rows = Array.from({ length: MAX_MODEL_PROOF_REFERENCES + 1 }, () => ({ model_evidence_ref: 0 })); });
  const deep = {}; let cursor = deep;
  for (let i = 0; i < 33; i++) cursor = cursor.child = {};
  assert.throws(() => unpackReportBackup(deep));
  const value = packed(), evidence = value.model_evidence_pool[0].evidence;
  const cost = Buffer.byteLength(JSON.stringify(evidence));
  // A valid source-window proof repeated under 128 MiB must not expand unbounded.
  if (cost * MAX_MODEL_PROOF_REFERENCES <= MAX_EXPANDED_BACKUP_BYTES) {
    const masks = evidence.geometry_mask, length = evidence.source_window.source_frames.length;
    const remaining = 200 - Object.keys(masks).length;
    for (let i = 0; i < remaining; i++) masks[`joint_${String(i).padStart(58, "0")}`] = Array(length).fill(true);
  }
  assert.ok(modelMeasurementEvidenceOf({ ...evidence, source_track_id: evidence.source_track_id ?? evidence.source_window.person_track_id }));
  const bytes = Buffer.byteLength(JSON.stringify(evidence));
  const count = Math.floor(MAX_EXPANDED_BACKUP_BYTES / bytes) + 1;
  assert.ok(count <= MAX_MODEL_PROOF_REFERENCES);
  value.result.rows = Array.from({ length: count }, () => ({ model_evidence_ref: 0 }));
  assert.ok(Buffer.byteLength(JSON.stringify(value)) < MAX_REPORT_FILE_BYTES);
  assert.throws(() => unpackReportBackup(value), /来源证据引用无效/);
});

test("actual SAM source and indicator definitions survive two production serializer-reader cycles", async () => {
  const baseline = structuredClone(fixture), snapshot = value => ({ indicator: indicatorAssessmentOf(value.indicator_assessment),
    source: ["FS01-M01", "FS02-M01"].map(id => sourceReviewWindows(value, id)) });
  const expected = snapshot(baseline); let current = fixture;
  for (let pass = 0; pass < 2; pass++) {
    const content = serializeReportBackup({ mode: "live", uploadState: "complete", evidence: { result: current, jobId: current.job_id } }, "2026-10-18T00:00:00Z");
    assert.equal(content.includes("\n"), false); assert.ok(Buffer.byteLength(content) < MAX_REPORT_FILE_BYTES);
    const restored = await readReportJson({ size: Buffer.byteLength(content), text: async () => content });
    current = restored.result;
    assert.deepEqual(snapshot(current), expected);
    // Parsers may allocate sanitized copies but may not mutate shared frozen proof.
    assert.deepEqual(snapshot(current), expected);
  }
  assert.deepEqual(fixture, baseline);
});

test("actual UTF-8 reader length cannot bypass 128 MiB using a forged size", async () => {
  let read = false;
  await assert.rejects(readReportJson({ size: MAX_REPORT_FILE_BYTES + 1, text: async () => { read = true; return "{}"; } }), /128 MiB/);
  assert.equal(read, false);
  const text = JSON.stringify({ text: "中".repeat(Math.ceil(MAX_REPORT_FILE_BYTES / 3)) });
  await assert.rejects(readReportJson({ size: 1, text: async () => text }), /128 MiB/);
});

test("the public file reader rejects malformed pooled evidence before any report parser", async () => {
  const changes = [
    value => { value.model_evidence_pool.push({ ...value.model_evidence_pool[0] }); },
    value => { value.model_evidence_pool[0].extra = true; },
    value => { value.model_evidence_pool[0].evidence.extra = true; },
    value => { value.result.rows[0].source_window = original.source_window; },
    value => { value.result.rows[0].nested = { model_evidence_pool: [] }; },
    value => { value.model_evidence_pool[0].evidence.source_window.source_bindings[0].model_evidence_ref = 0; },
    value => { delete value.model_evidence_codec; delete value.model_evidence_pool; },
    value => { value.result.rows[0].model_evidence_ref = 50; },
  ];
  for (const change of changes) {
    const raw = packed(); change(raw); const text = JSON.stringify(raw);
    await assert.rejects(readReportJson({ size: Buffer.byteLength(text), text: async () => text }), /来源证据引用无效/);
  }
});

test("demo API explicitly negotiates pooling, rejects malformed transport and retains native fallback", async () => {
  const encoded = packReportBackup(fixture, "api"), urls = [];
  assert.equal(encoded.report_transport, "model-proof-pool-v1");
  assert.deepEqual(unpackReportBackup(JSON.parse(JSON.stringify(encoded))), fixture);
  const call = response => createApiClient({}, async url => { urls.push(String(url)); return Response.json(response); }).getDemoResult(fixture.job_id,
    { endpoint: `/v1/jobs/${fixture.job_id}/demo-result?report_transport=old&report_transport=bad&keep=1` });
  const pooled = await call(encoded), native = await call(fixture);
  assert.deepEqual(pooled, native);
  for (const url of urls) assert.deepEqual(new URL(url, "https://local.invalid").searchParams.getAll("report_transport"), ["model-proof-pool-v1"]);
  for (const mutate of [value => { value.report_transport = "future"; }, value => { delete value.report_transport; },
    value => { value.model_evidence_pool[0].id = -1; }, value => { value.model_evidence_pool[0].evidence.geometry_mask = {}; }]) {
    const bad = structuredClone(encoded); mutate(bad); await assert.rejects(call(bad), /来源证据引用无效/);
  }
});

test("API native fallback rejects stripped envelopes and every nested reserved transport key", async () => {
  const stripped = packReportBackup(fixture, "api");
  for (const key of ["report_transport", "model_evidence_codec", "model_evidence_pool"]) delete stripped[key];
  await assert.rejects(createApiClient({}, async () => Response.json(stripped)).getDemoResult(fixture.job_id), /来源证据引用无效/);
  for (const key of ["report_transport", "model_evidence_codec", "model_evidence_pool", "model_evidence_ref"]) {
    const native = { job_id: "test-native", rows: [{ child: { [key]: 0 } }] };
    await assert.rejects(createApiClient({}, async () => Response.json(native)).getDemoResult(native.job_id), /来源证据引用无效/);
  }
});

test("native-only scanning bounds depth and rejects cycles without applying the pooled two-million-node budget", () => {
  const native = { rows: Array(2_100_000).fill(0) };
  assert.equal(unpackApiReport(native), native);
  const shared = { value: 0 }, aliases = { a: shared, b: shared };
  assert.equal(unpackApiReport(aliases), aliases);
  const cyclic = { child: {} }; cyclic.child.parent = cyclic;
  assert.throws(() => unpackApiReport(cyclic), /来源证据引用无效/);
  const deep = {}; let item = deep;
  for (let i = 0; i < 65; i++) item = item.child = {};
  assert.throws(() => unpackApiReport(deep), /来源证据引用无效/);
});

test("only exact codec-validated frozen proof tuples share a cached model result", () => {
  const restored = unpackReportBackup(packed()), row = restored.result.rows[0];
  const a = modelMeasurementEvidenceOf(row), b = modelMeasurementEvidenceOf({ ...row });
  assert.equal(a, b); assert.equal(a.source_window, row.source_window); assert.equal(a.geometry_mask, row.geometry_mask);
  const changedMask = { ...row, geometry_mask: structuredClone(row.geometry_mask) }; changedMask.geometry_mask.nose[0] = !changedMask.geometry_mask.nose[0];
  const c = modelMeasurementEvidenceOf(changedMask);
  assert.notEqual(c, a); assert.equal(c.geometry_mask.nose[0], changedMask.geometry_mask.nose[0]);
  assert.equal(modelMeasurementEvidenceOf({ ...row, measurement_provenance: { ...row.measurement_provenance, visibility_verified: true } }), null);
  assert.equal(modelMeasurementEvidenceOf({ ...row, source_track_id: row.source_track_id + 1 }), null);
  const mutable = structuredClone(row), first = modelMeasurementEvidenceOf(mutable);
  mutable.geometry_mask.nose[0] = !mutable.geometry_mask.nose[0];
  assert.notEqual(modelMeasurementEvidenceOf(mutable).geometry_mask.nose[0], first.geometry_mask.nose[0]);
});

test("only a decoded immutable review caches episodes; mutable reports revalidate every change", () => {
  const p = proof(), start = p.source_window.start_ms, end = p.source_window.end_ms;
  const review = { schema_version: "1.1.0", status: "available", episodes: [{ ...p, event_id: "one", event_code: "FS01", name_zh: "candidate", person_track_id: 1,
    track_diagnostics: { source_track_ids: [p.source_track_id], primary_player_id: 1 }, start_ms: start, end_ms: end,
    indicators: [{ indicator_id: "FS01-M01", name_zh: "measurement", feature_status: "measured", features: [{ ...p,
      feature_name: "test", name_zh: "test", value: 0, confidence: null, unit: "deg", required_joints: [Object.keys(p.geometry_mask)[0]] }] }] }] };
  review.episodes[0].indicators[0].features.push({ ...review.episodes[0].indicators[0].features[0], feature_name: "unsupported_joint", required_joints: ["missing_joint"] });
  const decoded = unpackReportBackup(packReportBackup(envelope([{ review }]))).result.rows[0].review;
  const first = footworkEpisodes(decoded), second = footworkEpisodes(decoded);
  assert.equal(first, second); assert.ok(Object.isFrozen(first[0].indicators[0].features));
  assert.equal(first[0].indicators[0].features[0].value, 0);
  assert.equal(first[0].indicators[0].features.length, 1); // A shared proof never bypasses this row's joint requirement.
  const mutable = structuredClone(review), before = footworkEpisodes(mutable);
  mutable.episodes[0].indicators[0].features[0].confidence = .9;
  assert.equal(before[0].indicators[0].features.length, 1);
  assert.equal(footworkEpisodes(mutable)[0].indicators[0].features.length, 0);
});
