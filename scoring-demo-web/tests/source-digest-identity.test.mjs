import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import ts from "typescript";

const cache = new Map();
function compiledUrl(file) {
  // Keep source JSON imports shared with production, without a generated copy.
  if (file.pathname.endsWith(".json")) return `data:text/javascript;base64,${Buffer.from(`export default ${fs.readFileSync(file, "utf8")}`).toString("base64")}`;
  if (cache.has(file.href)) return cache.get(file.href);
  let js = ts.transpileModule(fs.readFileSync(file, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  js = js.replace(/from "([^"]+)"/g, (_match, name) => `from ${JSON.stringify(name.startsWith(".")
    ? compiledUrl(new URL(name.endsWith(".json") ? name : `${name}.ts`, file)) : import.meta.resolve(name))}`);
  const url = `data:text/javascript;base64,${Buffer.from(js).toString("base64")}`;
  cache.set(file.href, url);
  return url;
}
const { sourceReviewWindows, sourceAssessmentForExport } = await import(compiledUrl(new URL("../app/lib/technical-review.ts", import.meta.url)));
const fixture = JSON.parse(fs.readFileSync(new URL("./fixtures/sam-source-observations.json", import.meta.url), "utf8"));
const ids = fixture.source_aligned_assessment.indicators.map(row => row.indicator_id);
const windowsOf = result => ids.flatMap(id => sourceReviewWindows(result, id));
const measurementsOf = result => windowsOf(result).flatMap(window => window.measurements);

function mixedCaseFixture() {
  const result = structuredClone(fixture);
  function uppercaseProof(value) {
    if (!value || typeof value !== "object") return;
    if (value.source_window) {
      value.source_window.video_sha256 = value.source_window.video_sha256.toUpperCase();
      for (const binding of value.source_window.source_bindings) binding.video_sha256 = binding.video_sha256.toUpperCase();
    }
    for (const child of Object.values(value)) uppercaseProof(child);
  }
  uppercaseProof(result.source_aligned_assessment);
  return result;
}

test("real source measurements keep values and exact proof across artifact/proof SHA casing", () => {
  const result = mixedCaseFixture(), before = structuredClone(result);
  const expected = measurementsOf(fixture), actual = measurementsOf(result);
  assert.ok(expected.some(row => row.status === "measured"));
  assert.deepEqual(actual.map(row => [row.feature_name, row.value, row.status]), expected.map(row => [row.feature_name, row.value, row.status]));
  for (const window of windowsOf(result)) {
    const original = result.source_aligned_assessment.indicators.flatMap(card => card.windows).find(row => row.event_id === window.event_id);
    assert.deepEqual(window.source_window, original.source_window);
    for (const row of window.measurements.filter(row => row.status === "measured")) {
      const raw = original.measurements.find(item => item.feature_name === row.feature_name);
      for (const key of ["source_window", "geometry_mask", "measurement_provenance", "source_frames"]) assert.deepEqual(row[key], raw[key]);
      assert.equal(row.measurement_provenance.scoring_allowed, false);
      assert.equal(row.model_geometry_coverage.source_visibility_70_percent_verified, null);
    }
  }
  assert.deepEqual(result, before);
});

test("mixed-case actual source remains stable through export and repeated parsing", () => {
  const result = mixedCaseFixture();
  const first = windowsOf(result);
  const restored = { ...result, source_aligned_assessment: sourceAssessmentForExport(result) };
  assert.deepEqual(windowsOf(restored), first);
  assert.deepEqual(windowsOf(restored), windowsOf(restored));
});

test("malformed or different artifact hashes do not authorize measured model rows", () => {
  const digest = fixture.source_aligned_assessment.video_sha256;
  for (const invalid of [null, true, 1, {}, [], digest.slice(1), digest + "0", " " + digest,
    digest + "\n", digest + "\r\n", "g" + digest.slice(1), "Ａ".repeat(64), "0".repeat(64)]) {
    const result = mixedCaseFixture();
    result.source_aligned_assessment.video_sha256 = invalid;
    assert.ok(measurementsOf(result).every(row => row.status !== "measured" && row.value === null), JSON.stringify(invalid));
  }
});

test("accepting digest casing leaves row bindings, clocks, geometry and provenance strict", () => {
  for (const defect of ["digest", "binding_case", "pts", "track", "mask", "joint", "provenance", "window"]) {
    const result = mixedCaseFixture();
    const parent = result.source_aligned_assessment.indicators.flatMap(card => card.windows)
      .find(window => window.measurements.some(row => row.status === "measured"));
    const row = parent.measurements.find(row => row.status === "measured");
    const joint = row.model_geometry_coverage.required_joint_names[0];
    if (defect === "digest") row.source_window.video_sha256 = "0".repeat(64);
    else if (defect === "binding_case") row.source_window.source_bindings[0].video_sha256 = row.source_window.video_sha256.toLowerCase();
    else if (defect === "pts") row.source_window.source_bindings[0].pts++;
    else if (defect === "track") row.source_window.person_track_id++;
    else if (defect === "mask") row.geometry_mask[joint] = row.geometry_mask[joint].map(() => false);
    else if (defect === "joint") delete row.geometry_mask[joint];
    else if (defect === "provenance") row.measurement_provenance.scoring_allowed = true;
    else if (defect === "window") row.measurement_window.end_ms++;
    const parsed = windowsOf(result).find(window => window.event_id === parent.event_id).measurements.find(item => item.feature_name === row.feature_name);
    assert.equal(parsed.value, null, defect);
    assert.equal(parsed.status, "unavailable", defect);
  }
});
