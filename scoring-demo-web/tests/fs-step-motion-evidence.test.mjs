import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import ts from "typescript";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

const cache = new Map();
function compiledUrl(fileUrl) {
  if (cache.has(fileUrl.href)) return cache.get(fileUrl.href);
  if (fileUrl.pathname.endsWith(".json")) return `data:text/javascript;base64,${Buffer.from(`export default ${fs.readFileSync(fileUrl, "utf8")}`).toString("base64")}`;
  let js = ts.transpileModule(fs.readFileSync(fileUrl, "utf8"), { fileName: fileUrl.pathname, compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  js = js.replace(/from "([^"]+)"/g, (_match, name) => { const local = new URL(name.endsWith(".json") ? name : `${name}.ts`, fileUrl); return `from ${JSON.stringify(name.startsWith(".") ? compiledUrl(fs.existsSync(local) ? local : new URL(`${name}.tsx`, fileUrl)) : import.meta.resolve(name))}`; });
  const url = `data:text/javascript;base64,${Buffer.from(js).toString("base64")}`; cache.set(fileUrl.href, url); return url;
}
const { cardExecutionOf } = await import(compiledUrl(new URL("../app/lib/card-execution.ts", import.meta.url)));
const { default: Panel } = await import(compiledUrl(new URL("../app/CardExecutionPanel.tsx", import.meta.url)));
const { default: contract } = await import(compiledUrl(new URL("../app/lib/fs-step-motion-display-contract.ts", import.meta.url)));
const fixture = JSON.parse(fs.readFileSync(new URL("./fixtures/fs-step-motion-synthetic-contract.json", import.meta.url), "utf8"));
const catalogue = JSON.parse(fs.readFileSync(new URL("../../src/rallymate_scoring/data/card_rules_v1.json", import.meta.url), "utf8"));
const original = (variant = "complete", card = "FS08-M02") => structuredClone(fixture.cases.find(c => c.variant === variant && c.indicator_id === card).report);
const instance = report => report.cards[0].instances[0];
function asProxy(i) { i.status = "proxy_scored"; i.proxy_grade = "A"; i.score = 100; i.reason_codes = [];
  i.semantics = "engineering_visual_condition_completion_not_original_technical_grade"; i.conditions.forEach(c => { c.passed = true; c.value = c.operator === "lte" ? 0 : c.threshold; }); }
function rejected(report, label) { const parsed = cardExecutionOf(report), i = instance(parsed);
  assert.equal(i.score, null, label); assert.equal(i.proxy_grade, null, label); assert.notEqual(i.status, "proxy_scored", label);
  assert.deepEqual(cardExecutionOf(parsed), parsed, label); return i; }

test("four generated motion policies match catalogue formulas without any real-step claim", () => {
  assert.equal(fixture.real_step_motion_instances, 0); assert.match(fixture.evidence_kind, /synthetic.*not_real_steps/);
  assert.deepEqual(Object.keys(contract.cards).sort(), ["FS02-M04", "FS07-M02", "FS08-M02", "FS08-M03"]);
  for (const [id, spec] of Object.entries(contract.cards)) {
    const source = catalogue.cards.find(c => c.indicator_id === id);
    assert.deepEqual(spec.conditions, source.evaluation.conditions.map(c => [c.metric, c.operator, c.value ?? c.bounds, c.unit]));
  }
});

test("four synthetic raw-source complete episodes independently retain their proxy scores and all raw geometry", () => {
  for (const { variant, report } of fixture.cases.filter(c => c.variant === "complete")) {
    const parsed = cardExecutionOf(report), i = instance(parsed), raw = instance(report);
    assert.equal(i.status, "proxy_scored", JSON.stringify(i)); assert.equal(i.score, 100, variant); assert.equal(i.original_grade, null);
    assert.equal(i.step_motion_validation.status, "measured_visual_motion"); assert.equal(i.step_motion_validation.condition_values_match, true);
    assert.deepEqual(i.source_window_evidence, raw.source_window_evidence); assert.deepEqual(cardExecutionOf(parsed), parsed);
    assert.equal(i.step_motion_validation.total_frame_count, 18); assert.equal(i.step_motion_validation.valid_frame_count, 18);
  }
});

test("a fully measured small displacement may be E, while missing or rejected motion never gains E or zero", () => {
  const small = instance(cardExecutionOf(original("small_displacement")));
  assert.equal(small.status, "proxy_scored"); assert.equal(small.proxy_grade, "E"); assert.equal(small.score, 20);
  for (const variant of ["wrong_declared_side", "joint_gap", "missing_motion_observation"]) {
    const raw = original(variant), i = instance(cardExecutionOf(raw));
    assert.equal(i.status, "unavailable"); assert.equal(i.score, null); assert.equal(i.proxy_grade, null);
    asProxy(instance(raw)); rejected(raw, variant);
  }
});

test("source quality remains a separate score gate after all motion values are independently measured", () => {
  const raw = original("original_quality"), initial = instance(cardExecutionOf(raw));
  assert.equal(initial.status, "review_required"); assert.equal(initial.score, null);
  assert.equal(initial.step_motion_validation.status, "measured_visual_motion");
  assert.deepEqual(initial.source_window_evidence.original_event_quality, instance(raw).source_window_evidence.original_event_quality);
  asProxy(instance(raw)); instance(raw).conditions[0].value = 1;
  rejected(raw, "saved review cleared");
});

test("saved completeness, phases, end medians, scale, distances and path lengths cannot substitute for recomputation", () => {
  const mutations = [
    p => { p.complete_visual_motion_cycle = false; }, p => { p.contact_verified = true; }, p => { p.target_direction_verified = true; },
    p => { p.technical_step_verified = true; }, p => { p.policy_version = "future"; }, p => { p.version = "future"; },
    p => { p.endpoint_count = 3; }, p => { p.fixed_torso_scale *= 2; }, p => { p.source_torso_scales[0] += 1; },
    p => { p.feet.right.net_displacement_torso += .5; }, p => { p.feet.left.path_length_torso = .5; },
    p => { p.feet.right.phases[0].duration_ms = 80; }, p => { p.feet.right.phases[0].source_frames.pop(); },
    p => { p.feet.right.intervals[4].moving = false; }, p => { p.feet.right.intervals[4].speed_torso_s = 0; },
    p => { p.feet.right.endpoint_medians.start[0] += 1; }, p => { p.feet.right.endpoint_low_cloud_radii_torso[0] += 1; },
    p => { p.minimum_phase_duration_ms = 1; }, p => { p.minimum_phase_frame_count = 1; }, p => { p.low_speed_threshold_torso_s = 0; },
  ];
  mutations.forEach((mutate, n) => { const raw = original(); mutate(instance(raw).source_window_evidence.step_motion); rejected(raw, String(n)); });
});

test("complete original frame, decoder clock and selected-person proof remains mandatory", () => {
  const mutations = [
    e => { e.source_frames.shift(); }, e => { e.source_pts.pop(); }, e => { e.source_frame_bindings.splice(7, 1); },
    e => { e.step_motion.source_geometry_series.pop(); }, e => { e.step_motion.reference_series.shift(); },
    e => { e.step_motion.source_pts[5].pts++; }, e => { e.source_frame_bindings[5].selection_epoch++; },
    e => { e.source_frame_bindings[5].camera_reference_epoch++; }, e => { e.source_frame_bindings[5].selected_person_track_id = 9; },
    e => { e.source_pts[5].time_base_den *= 2; }, e => { e.source_frame_bindings[5].video_sha256 = "f".repeat(64); },
  ];
  mutations.forEach((mutate, n) => { const raw = original(); mutate(instance(raw).source_window_evidence); rejected(raw, String(n)); });
});

test("the six-joint intersection is rebuilt from normalized geometry and confidence, never stored valid counts", () => {
  for (const variant of ["low_confidence", "missing_point", "unknown_model", "forged_count", "nan_point"]) {
    const raw = original(), proof = instance(raw).source_window_evidence.step_motion;
    contract.required_joint_names.forEach((joint, n) => {
      const row = proof.source_geometry_series[n];
      if (variant === "low_confidence") row.point_confidences[joint] = .1;
      if (variant === "missing_point") row.normalized_points[joint] = null;
      if (variant === "unknown_model") { row.point_confidences[joint] = null; row.observation_semantics = "unverified"; }
      if (variant === "nan_point") row.normalized_points[joint][0] = Infinity;
    });
    if (variant === "forged_count") instance(raw).quality.valid_frame_count = 17;
    rejected(raw, variant);
  }
});

test("FS08-M03 tilt is recomputed in the full-torso normalized camera-reference plane", () => {
  const raw = original("complete", "FS08-M03"), proof = instance(raw).source_window_evidence.step_motion;
  for (const name of ["left_shoulder", "right_shoulder"]) proof.source_geometry_series[5].normalized_points[name][0] += .5;
  rejected(raw, "tilt differs despite unchanged reference motion and saved pass");
});

test("the two geometry series must agree under the production full-torso normalization formula", () => {
  for (const variant of ["frozen", "swapped_ankles", "reference_pixels", "wrong_basis"]) {
    const raw = original("complete", "FS08-M03"), proof = instance(raw).source_window_evidence.step_motion;
    const first = structuredClone(proof.source_geometry_series[0].normalized_points);
    proof.source_geometry_series.forEach((row, index) => {
      if (variant === "frozen") row.normalized_points = structuredClone(first);
      if (variant === "swapped_ankles") [row.normalized_points.left_ankle, row.normalized_points.right_ankle] = [row.normalized_points.right_ankle, row.normalized_points.left_ankle];
      if (variant === "reference_pixels") row.normalized_points = structuredClone(proof.reference_series[index].points);
      if (variant === "wrong_basis") row.normalization_basis = "left_shoulder_left_torso";
    });
    const result = rejected(raw, variant);
    assert.equal(result.step_motion_validation.status, "invalid", variant);
    assert.ok(result.step_motion_validation.reason_codes.includes("step_motion_normalized_reference_mismatch"), variant);
  }
});

test("self-claimed model semantics cannot authorize missing confidences without exact per-frame model provenance", () => {
  const variants = ["no_binding", "no_model_source", "wrong_video", "wrong_frame", "wrong_person", "wrong_pts", "wrong_selection", "wrong_camera", "wrong_timestamp", "missing_processed_index"];
  for (const variant of variants) {
    const raw = original("bound_synthetic_model"), proof = instance(raw).source_window_evidence.step_motion;
    proof.source_geometry_series.forEach(row => {
      for (const joint of contract.required_joint_names) row.point_confidences[joint] = null;
      row.observation_semantics = "monocular_model_estimate"; row.pose_source = "sam3d";
      if (variant === "no_binding") delete row.source_binding;
      if (variant === "no_model_source") row.pose_source = null;
      if (variant === "wrong_video") row.source_binding.video_sha256 = "f".repeat(64);
      if (variant === "wrong_frame") row.source_binding.frame_index++;
      if (variant === "wrong_person") row.source_binding.person_track_id++;
      if (variant === "wrong_pts") row.source_binding.pts++;
      if (variant === "wrong_selection") row.source_binding.selection_epoch++;
      if (variant === "wrong_camera") row.source_binding.camera_reference_epoch++;
      if (variant === "wrong_timestamp") row.source_binding.timestamp_ms += 2;
      if (variant === "missing_processed_index") delete row.source_binding.processed_index;
    });
    const result = rejected(raw, variant);
    assert.equal(result.step_motion_validation.status, "invalid", variant);
    assert.ok(result.step_motion_validation.reason_codes.includes("step_motion_model_source_binding_invalid"), variant);
  }
});

test("a formal source-bound synthetic SAM record retains missing-confidence arithmetic without establishing visibility", () => {
  const raw = original("bound_synthetic_model"), parsed = instance(cardExecutionOf(raw));
  assert.equal(parsed.status, "proxy_scored"); assert.equal(parsed.score, 100);
  assert.equal(parsed.quality.visibility_verified, false);
  assert.equal(parsed.step_motion_validation.valid_frame_count, 18);
  for (const row of parsed.source_window_evidence.step_motion.source_geometry_series) {
    assert.equal(row.pose_source, "sam3d"); assert.equal(row.observation_semantics, "monocular_model_estimate");
    assert.equal(row.normalization_basis, "full_torso");
    assert.ok(Object.values(row.point_confidences).every(c => c === null));
    assert.equal(row.source_binding.frame_index, row.frame_index);
  }
});

test("backend-produced counterexamples cannot be promoted by replacing completeness and grade fields", () => {
  for (const variant of ["two_motions", "short_middle", "endpoint_overlap", "out_and_back", "other_net_dominates", "competing_cycles", "phase_79ms"]) {
    const raw = original(variant), initial = instance(cardExecutionOf(raw));
    assert.equal(initial.status, "unavailable", variant); assert.equal(initial.step_motion_validation.status, "unavailable", variant);
    const i = instance(raw), proof = i.source_window_evidence.step_motion;
    asProxy(i); proof.status = "measured_visual_motion"; proof.complete_visual_motion_cycle = true; proof.reason_codes = [];
    i.source_window_evidence.step_cycle_bound = true;
    for (const foot of Object.values(proof.feet)) { foot.complete_visual_motion_cycle = true; foot.reason_codes = [];
      for (const phase of foot.phases) { phase.enough_frames = true; phase.enough_duration = true; } }
    rejected(raw, variant);
  }
  const at80 = instance(cardExecutionOf(original("phase_80ms")));
  assert.equal(at80.score, 100); assert.equal(at80.step_motion_validation.feet.right.phases[0].duration_ms, 80);
});

test("camera-compensated source replay preserves arithmetic across both coordinate series", () => {
  const raw = original("camera_rotation_compensated", "FS08-M03"), i = instance(cardExecutionOf(raw));
  assert.equal(i.score, instance(raw).score); assert.equal(i.step_motion_validation.status, "measured_visual_motion");
  assert.ok(i.step_motion_validation.values["trunk_tilt_deg.range"] < 1e-8);
});

test("changing the declared foot or injecting authority fields cannot select the fastest ankle automatically", () => {
  for (const target of ["side", "pts", "score"]) {
    const raw = original(), i = instance(raw), observations = [i.source_window_annotation.step_motion_observation,
      i.window.step_motion_observation, i.source_window_evidence.step_motion.moving_foot_observation];
    for (const observation of observations) {
      if (target === "side") observation.moving_foot = "left";
      if (target === "pts") observation.source_frames[0].pts++;
      if (target === "score") observation.score = 100;
    }
    rejected(raw, target);
  }
});

test("the panel distinguishes observed moving foot, original clock, source review and technical uncertainty", () => {
  const html = renderToStaticMarkup(createElement(Panel, { value: original() }));
  assert.match(html, /迈步 \/ 跨步的局部二维运动/); assert.match(html, /人工观察的移动脚：右脚/);
  assert.match(html, /18\/18 原帧六关节共同有效/); assert.match(html, /至少 80 毫秒和 3 个原帧/); assert.match(html, /原 PTS/);
  assert.match(html, /至下一原帧速度/);
  assert.match(html, /不是前脚、触地、目标方向或技术步法确认/); assert.match(html, /补偿参考平面逐帧归一化/);
  const review = renderToStaticMarkup(createElement(Panel, { value: original("original_quality") }));
  assert.match(review, /此卡没有分数/); assert.doesNotMatch(review, /A · 100/);
});
