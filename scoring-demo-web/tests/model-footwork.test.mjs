import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import ts from "typescript";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

function compiledUrl(fileUrl) {
  // Keep source JSON imports shared with production, without a generated copy.
  if (fileUrl.pathname.endsWith(".json")) return `data:text/javascript;base64,${Buffer.from(`export default ${fs.readFileSync(fileUrl, "utf8")}`).toString("base64")}`;
  let js = ts.transpileModule(fs.readFileSync(fileUrl, "utf8"), { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  js = js.replace(/from "([^"]+)"/g, (_match, name) => { const local = new URL(name.endsWith(".json") ? name : `${name}.ts`, fileUrl);
    return `from ${JSON.stringify(name.startsWith(".") ? compiledUrl(fs.existsSync(local) ? local : new URL(`${name}.tsx`, fileUrl)) : import.meta.resolve(name))}`; });
  return `data:text/javascript;base64,${Buffer.from(js).toString("base64")}`;
}
const { modelMeasurementEvidenceOf } = await import(compiledUrl(new URL("../app/lib/model-measurement.ts", import.meta.url)));
const { processFootworkCriterion } = await import(compiledUrl(new URL("../app/lib/process-footwork.ts", import.meta.url)));
const { footworkEpisodes } = await import(compiledUrl(new URL("../app/lib/footwork-review.ts", import.meta.url)));
const { processEvaluationOf } = await import(compiledUrl(new URL("../app/lib/process-evaluation.ts", import.meta.url)));
const { ProcessRadar } = await import(compiledUrl(new URL("../app/ProcessScorePanel.tsx", import.meta.url)));
const fixture = JSON.parse(fs.readFileSync(new URL("fixtures/model-footwork.json", import.meta.url), "utf8"));
const criterion = () => structuredClone(fixture.process.dimensions.find(row => row.id === "movement").criteria.find(row => row.status === "measured"));

test("real backend contract preserves null confidence, source clock and identity through repeated parsing", () => {
  const original = criterion();
  const parsed = processFootworkCriterion(original);
  assert.ok(parsed);
  assert.equal(parsed.person_track_id, 1);
  assert.equal(parsed.source_track_id, 17);
  assert.equal(parsed.source_window.source_timestamps_ms[0], 75300);
  assert.equal(parsed.start_ms, 300);
  assert.equal(parsed.quality.confidence, null);
  assert.equal(parsed.score, null);
  assert.deepEqual(processFootworkCriterion(parsed), parsed);
  const episodes = footworkEpisodes(fixture.review);
  const measured = episodes[0].indicators[0].measurements.filter(row => row.status === "measured");
  assert.equal(measured.length, 2);
  assert.equal(episodes[0].indicators[0].features.length, 2);
  assert.ok(measured.every(row => row.confidence === null && row.measurement_provenance.visibility_verified === false));
  assert.deepEqual(footworkEpisodes({ ...fixture.review, episodes }), episodes);
});

test("model evidence strips unknown payload fields from provenance and nested source proof", () => {
  const raw = criterion();
  raw.measurement_provenance.secret = "discard";
  raw.source_window.secret = "discard";
  raw.source_window.source_bindings[0].secret = "discard";
  const parsed = modelMeasurementEvidenceOf(raw);
  assert.ok(parsed);
  assert.doesNotMatch(JSON.stringify(parsed), /secret|discard/);
});

test("model process observations reject forged confidence, coverage, clocks and grading claims", () => {
  const changes = {
    confidence: row => { row.quality.confidence = .99; },
    score: row => { row.score = 99; },
    visibility: row => { row.measurement_provenance.visibility_verified = true; },
    gate: row => { row.measurement_provenance.scoring_allowed = true; },
    identity: row => { row.source_track_id = 1; },
    hash: row => { row.source_window.source_bindings[0].video_sha256 = "b".repeat(64); },
    mask: row => { Object.keys(row.geometry_mask).forEach(key => { row.geometry_mask[key] = [false, false, false, false]; }); },
    lowCoverage: row => { Object.keys(row.geometry_mask).forEach(key => { row.geometry_mask[key] = [true, false, false, false]; }); },
    frameJump: row => { for (const i of [2, 3]) { row.source_window.source_frames[i]++; row.source_window.source_bindings[i].frame_index++; } },
    processedJump: row => { for (const i of [2, 3]) row.source_window.source_bindings[i].processed_index++; },
    timebase: row => { for (const i of [2, 3]) { row.source_window.source_bindings[i].time_base_num = 2; row.source_window.source_bindings[i].time_base_den = 2000; } },
    clock: row => { row.source_window.source_bindings[2].pts += 1000; },
  };
  for (const [name, change] of Object.entries(changes)) {
    const row = criterion(); change(row);
    assert.equal(processFootworkCriterion(row), null, name);
  }
});

test("footwork import cannot promote child geometry or revive numeric confidence on model events", () => {
  for (const defect of ["parent", "confidence", "proof", "identity"]) {
    const review = structuredClone(fixture.review), event = review.episodes[0];
    if (defect === "parent") Object.keys(event.geometry_mask).forEach(key => { event.geometry_mask[key] = [false, false, false, false]; });
    if (defect === "identity") event.track_diagnostics.primary_player_id = 17;
    for (const row of [...event.indicators[0].features, ...event.indicators[0].measurements]) {
      if (defect === "confidence") row.confidence = .9;
      if (defect === "proof") delete row.source_window;
    }
    event.indicators[0].scoring_status = "scored";
    const parsed = footworkEpisodes(review)[0].indicators[0];
    assert.equal(parsed.scoring_status, "unavailable", defect);
    assert.equal(parsed.features.length, 0, defect);
    assert.equal(parsed.measurements.filter(row => row.status === "measured").length, 0, defect);
  }
});

test("full process parser and radar label show model measurement with no numeric score", () => {
  const parsed = processEvaluationOf(fixture.process);
  const movement = parsed.dimensions.find(row => row.id === "movement");
  assert.equal(movement.measured_criterion_count, 2);
  assert.equal(movement.score, null);
  assert.equal(movement.status_reason, "independent_reference_missing");
  assert.match(movement.status_reason_zh, /已有单目模型估计/);
  const html = renderToStaticMarkup(createElement(ProcessRadar, { dimensions: parsed.dimensions, selected: "movement", reference: parsed.relative_reference }));
  assert.match(html, />未评分<\/text>/);
  assert.doesNotMatch(html, /中性暂估|class="process-radar-point/);
  assert.doesNotMatch(html, />需参照<\/text>/);
  assert.deepEqual(processEvaluationOf(parsed), parsed);
});
