import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import ts from "typescript";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

const cache = new Map();
function compiledUrl(fileUrl) {
  if (fileUrl.pathname.endsWith(".json")) return `data:text/javascript;base64,${Buffer.from(`export default ${fs.readFileSync(fileUrl, "utf8")}`).toString("base64")}`;
  if (cache.has(fileUrl.href)) return cache.get(fileUrl.href);
  let js = ts.transpileModule(fs.readFileSync(fileUrl, "utf8"), { fileName: fileUrl.pathname, compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  js = js.replace(/from "([^"]+)"/g, (_match, name) => { const local = new URL(name.endsWith(".json") ? name : `${name}.ts`, fileUrl); return `from ${JSON.stringify(name.startsWith(".") ? compiledUrl(fs.existsSync(local) ? local : new URL(`${name}.tsx`, fileUrl)) : import.meta.resolve(name))}`; });
  const url = `data:text/javascript;base64,${Buffer.from(js).toString("base64")}`; cache.set(fileUrl.href, url); return url;
}
const { cardExecutionOf } = await import(compiledUrl(new URL("../app/lib/card-execution.ts", import.meta.url)));
const { default: CardExecutionPanel } = await import(compiledUrl(new URL("../app/CardExecutionPanel.tsx", import.meta.url)));
const { default: contract } = await import(compiledUrl(new URL("../app/lib/gs-preload-display-contract.ts", import.meta.url)));
const fixture = JSON.parse(fs.readFileSync(new URL("./fixtures/gs-preload-synthetic-contract.json", import.meta.url), "utf8"));
const catalogue = JSON.parse(fs.readFileSync(new URL("../../src/rallymate_scoring/data/card_rules_v1.json", import.meta.url), "utf8"));
const instance = report => report.cards[0].instances[0];
const fresh = () => structuredClone(fixture.report);

test("five generated preload contracts remain manual with no executable grade conditions", () => {
  assert.equal(Object.keys(contract.cards).length, 5);
  for (const [id, spec] of Object.entries(contract.cards)) {
    const source = catalogue.cards.find(c => c.indicator_id === id);
    assert.equal(spec.rule_kind, "manual_review"); assert.deepEqual(spec.conditions, []);
    assert.equal(spec.stage_code, source.stage_code);
    assert.deepEqual(contract.references, source.evaluation.auxiliary_observations.map(row => [row.metric, row.unit, row.value]));
  }
});

test("synthetic example survives normalization with recomputed geometry and no score or real-video claim", () => {
  assert.match(fixture.evidence_kind, /synthetic.*not_real_tennis_evidence/); assert.equal(fixture.real_m01_instance_count, 0);
  const parsed = cardExecutionOf(fixture.report), i = instance(parsed);
  assert.equal(i.auxiliary_validation.status, "measured_auxiliary");
  assert.deepEqual(i.auxiliary_evidence, instance(fixture.report).auxiliary_evidence);
  assert.equal(i.status, "review_required"); assert.equal(i.score, null); assert.equal(i.proxy_grade, null); assert.equal(i.original_grade, null);
  assert.equal(parsed.summary.proxy_scored_instances, 0); assert.equal(parsed.summary.original_scored_cards, 0);
  assert.equal(i.auxiliary_validation.total_frame_count, 12); assert.equal(i.auxiliary_validation.valid_frame_count, 12);
  assert.ok(Math.abs(i.auxiliary_validation.observations[0].value - 9 / 55.5) < 1e-10);
  assert.equal(i.auxiliary_validation.joint_descent_fraction, 1);
  assert.deepEqual(cardExecutionOf(parsed), parsed);
});

test("external auxiliary grade, pass, eligibility and saved reader-validation flags never authorize anything", () => {
  const mutations = [a => { a.score = 100; }, a => { a.original_grade = "A"; }, a => { a.proxy_grade = "A"; }, a => { a.source_grade_supported = true; },
    a => { a.auxiliary_observations[0].passed = true; }, a => { a.auxiliary_observations[0].scoring_eligible = true; },
    a => { a.split_step_confirmed = true; }, a => { a.version = "future"; }, a => { a.auxiliary_observations[0].value = Infinity; },
    a => { a.scoring_eligible = true; }, a => { a.source_annotation.observation.score = 100; }, a => { a.source_annotation.observation.start_frame.passed = true; }];
  for (const mutate of mutations) {
    const report = fresh(); mutate(instance(report).auxiliary_evidence);
    instance(report).auxiliary_validation = { status: "measured_auxiliary", observations: [{ value: 100 }] };
    const parsed = cardExecutionOf(report), i = instance(parsed);
    assert.equal(i.auxiliary_validation.status, "invalid"); assert.equal(i.auxiliary_validation.observations.length, 0); assert.equal(i.score, null);
    assert.deepEqual(cardExecutionOf(parsed), parsed);
  }
});

test("wrong M01, action, document, video, person, parent bounds and per-frame clock invalidate auxiliary values", () => {
  const mutations = [
    i => { i.auxiliary_evidence.source_annotation.observation.indicator_id = "GS02-M01-04"; },
    i => { i.auxiliary_evidence.source_annotation.stage_code = "GS01-M03"; },
    i => { i.auxiliary_evidence.source_annotation.document_sha256 = "c".repeat(64); },
    i => { i.auxiliary_evidence.source_annotation.observation.action_id = "another"; },
    i => { i.auxiliary_evidence.window.video_sha256 = "b".repeat(64); },
    i => { i.auxiliary_evidence.source_annotation.observation.person_track_id = 2; },
    i => { i.auxiliary_evidence.source_annotation.stage_window_ms.end_ms = 400; },
    i => { i.auxiliary_evidence.source_frame_bindings[4].pts++; },
    i => { i.auxiliary_evidence.source_frame_bindings[4].time_base_den = 2000; },
    i => { i.auxiliary_evidence.source_frame_bindings[4].selected_person_track_id = 2; },
    i => { i.auxiliary_evidence.source_frame_bindings[4].selection_epoch++; },
    i => { i.auxiliary_evidence.source_frame_bindings[4].camera_reference_epoch++; },
    i => { i.status = "unavailable"; },
  ];
  mutations.forEach((mutate, index) => { const report = fresh(); mutate(instance(report)); const parsed = cardExecutionOf(report);
    assert.equal(instance(parsed).auxiliary_validation.status, "invalid", String(index)); assert.equal(instance(parsed).score, null); });
});

test("joint union, frame denominator, finite series and endpoint arithmetic are recomputed", () => {
  const mutations = [
    a => { a.source_frame_bindings.shift(); }, a => { a.source_frame_bindings.pop(); },
    a => { a.quality.joint_valid_frames.left_ankle.splice(5, 1); },
    a => { a.quality.valid_frame_count = 11; a.quality.fraction = 11 / 12; },
    a => { a.series.hip_y_camera_reference[5] = null; },
    a => { a.series.left_knee_flexion_deg[5] = 181; },
    a => { a.series.fixed_torso_scale += 1; },
    a => { a.series.endpoint_frame_count = 2; },
    a => { a.auxiliary_observations[0].value += 1; },
    a => { a.auxiliary_observations[0].engineering_reference.value = .01; },
    a => { a.joint_descent_fraction = .5; },
    a => { a.series.simultaneous_descent_intervals[5] = false; },
    a => { a.series.pair_source_frames[0] = [0, 2]; },
  ];
  mutations.forEach((mutate, index) => { const report = fresh(); mutate(instance(report).auxiliary_evidence); const i = instance(cardExecutionOf(report));
    assert.equal(i.auxiliary_validation.status, "invalid", String(index)); assert.equal(i.auxiliary_validation.joint_descent_fraction, null); });
});

test("local camera proof cannot override an uncompensated parent M01 window", () => {
  for (const normalized of [false, true]) {
    const report = normalized ? cardExecutionOf(fresh()) : fresh(), i = instance(report);
    (normalized ? i.auxiliary_parent_context.window : i.window).camera_compensated = false;
    const parsed = cardExecutionOf(report), actual = instance(parsed);
    assert.equal(actual.auxiliary_validation.status, "invalid");
    assert.deepEqual(actual.auxiliary_validation.observations, []);
    assert.equal(actual.score, null); assert.equal(parsed.summary.proxy_scored_instances, 0);
    assert.deepEqual(actual.auxiliary_evidence, i.auxiliary_evidence);
    assert.deepEqual(cardExecutionOf(parsed), parsed);
  }
});

test("auxiliary declared M01 bounds must match its parent, not just contain the local observation", () => {
  for (const mutate of [stage => { stage.end_ms += 1000; }, stage => { stage.start_ms -= 1; }]) {
    const report = fresh(), i = instance(report);
    mutate(i.auxiliary_evidence.source_annotation.stage_window_ms);
    const actual = instance(cardExecutionOf(report));
    assert.equal(actual.auxiliary_validation.status, "invalid");
    assert.deepEqual(actual.auxiliary_validation.observations, []); assert.equal(actual.score, null);
  }
});

test("auxiliary manual source fields obey the producer's 512 character limit", () => {
  for (const key of ["source", "source_description"]) {
    const report = fresh(), observation = instance(report).auxiliary_evidence.source_annotation.observation;
    observation[key] = "观".repeat(512);
    assert.equal(instance(cardExecutionOf(report)).auxiliary_validation.status, "measured_auxiliary");
    observation[key] += "观";
    assert.equal(instance(cardExecutionOf(report)).auxiliary_validation.status, "invalid");
  }
});

test("legitimate manual boundaries between decoded frames retain distinct requested and actual parent bounds", () => {
  const raw = fixture.additional_synthetic_cases.find(row => row.label === "between_frame_parent_bounds").report;
  const original = instance(raw), parsed = cardExecutionOf(raw), actual = instance(parsed);
  assert.deepEqual(original.source_annotation.requested_window_ms, { start_ms: 15, end_ms: 545 });
  assert.deepEqual(actual.window, { start_ms: 40, end_ms: 520 });
  assert.deepEqual(actual.auxiliary_parent_context.requested_window_ms, { start_ms: 15, end_ms: 545 });
  assert.equal(actual.auxiliary_validation.status, "measured_auxiliary");
  assert.equal(actual.score, null); assert.deepEqual(cardExecutionOf(parsed), parsed);
  for (const normalized of [false, true]) {
    for (const change of ["start", "end", "missing", "malformed"]) {
      const changed = normalized ? structuredClone(parsed) : structuredClone(raw), i = instance(changed);
      const parent = normalized ? i.auxiliary_parent_context : i.source_annotation;
      if (change === "start") parent.requested_window_ms.start_ms += 1;
      if (change === "end") parent.requested_window_ms.end_ms += 1;
      if (change === "missing") delete parent.requested_window_ms;
      if (change === "malformed") parent.requested_window_ms = null;
      const invalid = instance(cardExecutionOf(changed));
      assert.equal(invalid.auxiliary_validation.status, "invalid", `${normalized}/${change}`);
      assert.deepEqual(invalid.auxiliary_validation.observations, []); assert.equal(invalid.score, null);
    }
  }
});

test("UI states auxiliary reference values never constitute pass, proxy grade, or confirmed split step", () => {
  const html = renderToStaticMarkup(createElement(CardExecutionPanel, { value: fresh() }));
  assert.match(html, /局部下降辅助观测 · 不评分/); assert.match(html, /12 \/ 12 个原帧/); assert.match(html, /±80 毫秒/);
  assert.match(html, /仅供复核，不判通过/); assert.match(html, /时间比：100%/); assert.match(html, /原始 PTS/); assert.match(html, /不能确认垫步/);
  const invalid = fresh(); instance(invalid).auxiliary_evidence.score = 100;
  const rejected = renderToStaticMarkup(createElement(CardExecutionPanel, { value: invalid }));
  assert.match(rejected, /未经验证的输入记录/); assert.doesNotMatch(rejected, /连续辅助几何已计算/); assert.doesNotMatch(rejected, /A · 100/);
});

test("all five manual cards and producer-unavailable gaps round-trip with original series and no score", () => {
  for (const { label, report: raw } of fixture.additional_synthetic_cases) {
    const parsed = cardExecutionOf(raw), i = instance(parsed), original = instance(raw);
    assert.equal(i.auxiliary_validation.status, original.auxiliary_evidence.status, label);
    assert.deepEqual(i.auxiliary_evidence, original.auxiliary_evidence, label);
    assert.equal(i.score, null); assert.equal(i.original_grade, null); assert.deepEqual(cardExecutionOf(parsed), parsed);
    if (["six_frames", "joint_gap", "parent_stage_gap"].includes(label)) {
      assert.equal(i.auxiliary_validation.status, "unavailable"); assert.ok(i.auxiliary_validation.observations.every(row => row.value === null));
      const forged = structuredClone(raw), a = instance(forged).auxiliary_evidence;
      a.status = "measured_auxiliary"; a.reason_codes = []; a.auxiliary_observations.forEach(row => { row.value = row.engineering_reference.value; }); a.joint_descent_fraction = 1;
      assert.equal(instance(cardExecutionOf(forged)).auxiliary_validation.status, "invalid", label);
    }
  }
});

test("joint descent uses actual nonzero-origin PTS duration, not interval count", () => {
  const raw = fixture.additional_synthetic_cases.find(row => row.label === "nonzero_pts_duration").report;
  const parsed = cardExecutionOf(raw), i = instance(parsed);
  assert.equal(i.auxiliary_evidence.series.source_timestamps_ms[0], 9000);
  assert.ok(Math.abs(i.auxiliary_validation.joint_descent_fraction - 100 / 340) < 1e-12);
  const changed = structuredClone(raw); instance(changed).auxiliary_evidence.joint_descent_fraction = 5 / 11;
  assert.equal(instance(cardExecutionOf(changed)).auxiliary_validation.status, "invalid");
});
