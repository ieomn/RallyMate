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
const { validRacketSide } = await import(compiledUrl(new URL("../app/lib/manual-card-evidence.ts", import.meta.url)));
const { default: CardExecutionPanel } = await import(compiledUrl(new URL("../app/CardExecutionPanel.tsx", import.meta.url)));
const { default: contract } = await import(compiledUrl(new URL("../app/lib/fs-card-window-display-contract.ts", import.meta.url)));
const unit = JSON.parse(fs.readFileSync(new URL("./fixtures/fs-card-window-contract-unit.json", import.meta.url), "utf8"));
const real = JSON.parse(fs.readFileSync(new URL("./fixtures/gs-racket-side-real.json", import.meta.url), "utf8"));
const fsReal = JSON.parse(fs.readFileSync(new URL("./fixtures/fs-card-window-real.json", import.meta.url), "utf8"));
const catalogue = JSON.parse(fs.readFileSync(new URL("../../src/rallymate_scoring/data/card_rules_v1.json", import.meta.url), "utf8"));
const instances = report => report.cards.flatMap(card => card.instances);
function oneFs(id = "FS01-M04") { const report = structuredClone(unit.report); report.cards = report.cards.filter(card => card.indicator_id === id); return report; }
function oneGs(id = "GS01-M07-04") { const source = real.reports.find(r => r.report.cards.some(c => c.indicator_id === id)); const report = structuredClone(source.report); report.cards = report.cards.filter(card => card.indicator_id === id); return report; }
function expectNoScore(report, label) {
  const parsed = cardExecutionOf(report); assert.ok(parsed, label);
  assert.equal(parsed.summary.proxy_scored_instances, 0, label);
  for (const row of instances(parsed)) { assert.equal(row.score, null, label); assert.equal(row.proxy_grade, null, label); assert.equal(row.original_grade, null, label); assert.ok(row.conditions.every(c => c.passed === null), label); }
}
function markProxy(instance) {
  instance.reason_codes = []; instance.status = "proxy_scored"; instance.proxy_grade = "A"; instance.score = 100;
  instance.semantics = "engineering_visual_condition_completion_not_original_technical_grade";
  instance.conditions.forEach(c => { c.value = Array.isArray(c.threshold) ? c.threshold[0] : c.threshold; c.passed = true; });
}

test("35 generated contracts preserve original anchors, conditions and unbound cycle gates", () => {
  const cards = catalogue.cards.filter(c => c.event_code.startsWith("FS") && !["FS03", "FS04"].includes(c.event_code) && !["FS01-M02", "FS01-M05", "FS09-M03", "FS09-M04", "FS09-M05"].includes(c.indicator_id));
  assert.equal(cards.length, 35); assert.equal(Object.keys(contract.cards).length, 35);
  assert.equal(cards.filter(c => c.evaluation.kind === "visual_proxy").length, 20);
  for (const card of cards) {
    const spec = contract.cards[card.indicator_id];
    assert.equal(spec.rule_kind, card.evaluation.kind);
    assert.deepEqual(spec.conditions, card.evaluation.conditions.map(c => [c.metric, c.operator, c.bounds ?? c.value, c.unit]));
    assert.deepEqual(spec.source, Object.fromEntries(["document_id", "document_sha256", "table", "rubric_sha256"].map(key => [key, card.source[key]])));
  }
  assert.deepEqual(Object.entries(contract.cards).filter(([, c]) => c.requires_complete_step_cycle).map(([id]) => id), ["FS02-M04", "FS07-M02", "FS08-M02", "FS08-M03"]);
});

test("controlled repeated poses verify reader plumbing only: 16 proxies, 15 reviews, four missing cycles", () => {
  assert.match(unit.semantics, /synthetic.*not_real_video_validation/);
  assert.equal(unit.report.input.artifact_files_verified, false);
  const report = cardExecutionOf(unit.report);
  assert.equal(report.summary.proxy_scored_instances, 16);
  assert.equal(report.summary.review_instances, 15);
  assert.equal(report.summary.unavailable_instances, 4);
  assert.equal(report.summary.original_scored_cards, 0);
  assert.deepEqual(cardExecutionOf(report), report);
  for (const row of instances(report)) {
    const raw = instances(unit.report).find(i => i.indicator_id === row.indicator_id);
    assert.deepEqual(row.source_window_annotation, raw.source_window_annotation);
    assert.deepEqual(row.source_window_evidence, raw.source_window_evidence);
    assert.deepEqual(row.source_window_context, raw.window);
    assert.equal(row.score, raw.score);
  }
  assert.equal(cardExecutionOf({ ...unit.report, version: contract.runtime_engine }), null);
});

test("FS windows reject wrong source, stage, selection, camera, timebase and forged policy provenance", () => {
  const mutations = [
    i => { delete i.source_window_annotation; }, i => { delete i.source_window_evidence; },
    i => { i.source_window_annotation.indicator_id = "FS10-M04"; },
    i => { i.window.stage_code = "FS01"; }, i => { i.window.video_id = "other"; },
    i => { i.source_window_annotation.video_sha256 = "a".repeat(64); },
    i => { i.source_window_annotation.binding.method = "versioned_detector"; },
    i => { i.source_window_annotation.binding.uncertainty_ms = -1; },
    i => { i.source_window_evidence.source_frame_bindings[5].selected_person_track_id = 2; },
    i => { i.source_window_evidence.source_frame_bindings[5].selection_epoch++; },
    i => { i.source_window_evidence.source_frame_bindings[5].camera_reference_epoch++; },
    i => { i.source_window_evidence.source_pts[5].time_base_den = 1000; },
    i => { i.source_window_evidence.source_frame_bindings[5].timestamp_ms++; },
    i => { i.source_window_evidence.interpolation_used = true; },
    i => { i.source_window_evidence.original_event_quality[0].policy_version = "unversioned"; },
  ];
  mutations.forEach((mutate, index) => { const report = oneFs(); mutate(instances(report)[0]); expectNoScore(report, String(index)); });
});

test("deleting first, middle or last source frame cannot shrink the closed denominator", () => {
  for (const index of [0, 5, 17]) {
    const report = oneFs(), i = instances(report)[0];
    for (const key of ["source_frames", "source_pts", "source_frame_bindings"]) i.source_window_evidence[key].splice(index, 1);
    i.quality.total_frame_count--; i.quality.valid_frame_count--;
    i.window.start_ms = i.source_window_evidence.source_frame_bindings[0].timestamp_ms;
    i.window.end_ms = i.source_window_evidence.source_frame_bindings.at(-1).timestamp_ms;
    expectNoScore(report, `deleted index ${index}`);
  }
});

test("fractional PTS preserve valid quality coverage at rounded source-record boundaries", () => {
  for (const deltaPts of [-27, 27]) {
    const report = oneFs(), i = instances(report)[0];
    // Contract-only uniform clock shift: geometry and durations are unchanged.
    // 27 ticks at 1/90000 is .3 ms; all source-record/event times stay rounded.
    for (const key of ["source_pts", "source_frame_bindings"]) i.source_window_evidence[key].forEach(row => { row.pts += deltaPts; });
    const parsed = cardExecutionOf(report);
    assert.equal(parsed.summary.proxy_scored_instances, 1);
    assert.equal(instances(parsed)[0].score, i.score);
    assert.deepEqual(cardExecutionOf(parsed), parsed);
  }
});

test("FS source quality recomputes raw identity, exact window coverage and blocking flags", () => {
  const mutations = [
    q => { q.source_end_ms = q.source_start_ms + 1; },
    q => { q.source_track_ids = [2]; q.source_track_diagnostics.source_track_ids = [2]; },
    q => { q.primary_identity_ambiguous = true; q.source_track_diagnostics.primary_identity_ambiguous = true; },
    q => { q.source_track_diagnostics.source_track_switch_candidate_count = 1; },
    q => { q.raw_input_quality_flags = ["pose_kinematic_coverage_low"]; q.review_flags = []; },
    q => { q.raw_input_quality_flags = ["boundary_censored"]; q.review_flags = []; },
    q => { q.raw_input_quality_flags = ["keypoint_jump_candidates_present"]; q.irrelevant_joint_flags = ["keypoint_jump_candidates_present"]; q.source_track_diagnostics.keypoint_jump_candidate_joints = ["invented_joint"]; },
    q => { q.raw_input_quality_flags = ["keypoint_jump_candidates_present"]; q.irrelevant_joint_flags = ["keypoint_jump_candidates_present"]; q.source_track_diagnostics.keypoint_jump_candidate_joints = ["left_hip"]; },
  ];
  mutations.forEach((mutate, index) => { const report = oneFs(); mutate(instances(report)[0].source_window_evidence.original_event_quality[0]); expectNoScore(report, String(index)); });
  const report = oneFs(), q = instances(report)[0].source_window_evidence.original_event_quality[0];
  q.raw_input_quality_flags = ["keypoint_jump_candidates_present"]; q.irrelevant_joint_flags = ["keypoint_jump_candidates_present"]; q.source_track_diagnostics.keypoint_jump_candidate_joints = ["left_wrist"];
  assert.equal(cardExecutionOf(report).summary.proxy_scored_instances, 1, "unrequired registered wrist does not block hip/support geometry");
  const other = structuredClone(q); other.source_event_id = "other-person"; other.applies_to_selected_person = false; other.source_track_ids = [2]; other.source_track_diagnostics.source_track_ids = [2];
  instances(report)[0].source_window_evidence.original_event_quality.push(other);
  assert.equal(cardExecutionOf(report).summary.proxy_scored_instances, 1, "unambiguous other raw track is not selected-person quality");
});

test("cycle flags and external scores cannot turn four missing cycles or manual cards into results", () => {
  for (const [id, spec] of Object.entries(contract.cards).filter(([, c]) => c.requires_complete_step_cycle || c.rule_kind === "manual_review")) {
    const report = oneFs(id), i = instances(report)[0]; markProxy(i);
    i.source_window_evidence.step_cycle_bound = true;
    expectNoScore(report, `${id}:${spec.rule_kind}`);
  }
});

test("actual GS artifact outputs preserve 1 proxy, 5 reviews, 9 unavailable and complete sampled wrist proof", () => {
  assert.match(real.provenance.kind, /actual_original_video/); assert.match(real.provenance.envelope, /not_HTTP_validation/);
  const totals = [0, 0, 0];
  for (const { report: raw } of real.reports) {
    const report = cardExecutionOf(raw);
    totals[0] += report.summary.proxy_scored_instances; totals[1] += report.summary.review_instances; totals[2] += report.summary.unavailable_instances;
    assert.deepEqual(cardExecutionOf(report), report);
    for (const i of instances(report)) {
      const source = instances(raw).find(row => row.indicator_id === i.indicator_id);
      assert.deepEqual(i.racket_side_evidence.observation, source.source_annotation.racket_side_observation);
      assert.deepEqual(i.racket_side_evidence.window_binding, source.window.racket_side_binding);
      assert.equal(i.score, source.score); assert.equal(i.original_grade, null);
      if (i.conditions.some(c => c.metric === "wrist_speed_increase_torso_s")) assert.equal(i.score, null);
    }
  }
  assert.deepEqual(totals, [1, 5, 9]);
});

test("GS side proof rejects copied documents, wrong action, source PTS, hand semantics and epoch spoofing", () => {
  const mutations = [p => { p.document_sha256 = "a".repeat(64); }, p => { p.action_id = "other-action"; },
    p => { p.video_sha256 = "b".repeat(64); }, p => { p.person_track_id = 2; }, p => { p.technique_code = "GS03"; },
    p => { p.measurement_wrist_side = "left"; }, p => { p.measurement_wrist_side = "unknown"; },
    p => { p.dominant_hand_established = true; }, p => { p.continuous_racket_hold_established = true; },
    p => { p.source_frame_bindings[0].pts++; }, p => { p.source_frame_bindings[0].selection_epoch++; },
    p => { p.action_window_ms.end_ms = p.action_window_ms.start_ms + 10; },
  ];
  mutations.forEach((mutate, index) => { const report = oneGs(), i = instances(report)[0]; mutate(i.source_annotation.racket_side_observation); i.window.racket_side_binding = structuredClone(i.source_annotation.racket_side_observation); expectNoScore(report, String(index)); });
  const report = oneGs(); instances(report)[0].window.racket_side_binding.source = "different-copy"; expectNoScore(report, "copies differ");
});

test("legacy GS geometric proxies remain compatible; wrist metrics require sampled and stage-applicable provenance", () => {
  const legacy = oneGs(), i = instances(legacy)[0]; delete i.source_annotation.racket_side_observation; delete i.window.racket_side_binding; delete i.window.racket_side;
  assert.equal(cardExecutionOf(legacy).summary.proxy_scored_instances, 1);
  const wrist = oneGs("GS01-M07-01"), w = instances(wrist)[0]; markProxy(w);
  delete w.source_annotation.racket_side_observation; delete w.window.racket_side_binding;
  expectNoScore(wrist, "metric cannot bind side using string only");
  const parsed = instances(cardExecutionOf(oneGs()))[0], p = parsed.racket_side_evidence;
  p.observation.stage_continuity_verified = false; p.window_binding.stage_continuity_verified = false; delete p.window.racket_side;
  assert.equal(validRacketSide(p, parsed, parsed.source_stage), true, "valid sampled provenance can be stage-inapplicable");
  assert.equal(validRacketSide(p, parsed, parsed.source_stage, true), false);
  const two = instances(cardExecutionOf(oneGs("GS02-M07-02")))[0];
  two.racket_side_evidence.observation.observed_racket_hands = "right"; two.racket_side_evidence.window_binding.observed_racket_hands = "right";
  assert.equal(validRacketSide(two.racket_side_evidence, two, two.source_stage), false, "two-handed source requires both observed hands");
});

test("panels show manual uncertainty, complete raw PTS and wrist observation limitations without missing E/0", () => {
  const fsHtml = renderToStaticMarkup(createElement(CardExecutionPanel, { value: oneFs("FS02-M04") }));
  assert.match(fsHtml, /人工逐卡候选窗口/); assert.match(fsHtml, /±100 毫秒/); assert.match(fsHtml, /原始 PTS/); assert.match(fsHtml, /完整步周期未由此窗口确认/); assert.match(fsHtml, /未评分/); assert.doesNotMatch(fsHtml, /E · 0/);
  const gsHtml = renderToStaticMarkup(createElement(CardExecutionPanel, { value: oneGs() }));
  assert.match(gsHtml, /测量腕：右腕/); assert.match(gsHtml, /不是优势手判定/); assert.match(gsHtml, /不证明全程持续持拍/); assert.match(gsHtml, /完整来源记录/);
});

test("three real manually estimated FS windows preserve two reviews and one unavailable without technique claims", () => {
  assert.equal(fsReal.provenance.kind, "actual_original_video_unified_artifact_report_excerpt");
  const report = cardExecutionOf(fsReal.report);
  assert.equal(report.summary.proxy_scored_instances, 0); assert.equal(report.summary.review_instances, 2); assert.equal(report.summary.unavailable_instances, 1);
  assert.deepEqual(cardExecutionOf(report), report);
  for (const row of instances(report)) {
    const raw = instances(fsReal.report).find(i => i.indicator_id === row.indicator_id);
    assert.equal(row.score, null); assert.equal(row.original_grade, null);
    assert.deepEqual(row.source_window_annotation, raw.source_window_annotation);
    assert.deepEqual(row.source_window_evidence, raw.source_window_evidence);
    assert.ok(row.source_window_evidence.original_event_quality.some(q => q.raw_input_quality_flags.length));
    const forged = structuredClone(fsReal.report); forged.cards = forged.cards.filter(c => c.indicator_id === row.indicator_id);
    const i = instances(forged)[0]; markProxy(i); i.source_window_evidence.original_event_quality.forEach(q => { q.review_flags = []; });
    expectNoScore(forged, row.indicator_id);
  }
});
