import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import ts from "typescript";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

const compiled = new Map();
function compiledUrl(file) {
  if (compiled.has(file.href)) return compiled.get(file.href);
  let js = ts.transpileModule(fs.readFileSync(file, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText.replace(/^import\s+["'][^"']+\.css["'];?\s*$/gm, "");
  js = js.replace(/from "([^"]+)"/g, (_match, name) => {
    const local = new URL(`${name}.ts`, file);
    return `from ${JSON.stringify(name.startsWith(".") ? compiledUrl(fs.existsSync(local) ? local : new URL(`${name}.tsx`, file)) : import.meta.resolve(name))}`;
  });
  const url = `data:text/javascript;base64,${Buffer.from(js).toString("base64")}`;
  compiled.set(file.href, url); return url;
}
const { SCORE_COMPONENTS, scoreExplanationOf, scoreEvidenceWindows, componentImpact } = await import(compiledUrl(new URL("../app/lib/score-explanation.ts", import.meta.url)));
const { default: Panel } = await import(compiledUrl(new URL("../app/ScoreExplanationPanel.tsx", import.meta.url)));
const { normalizeMeasurementResult } = await import(compiledUrl(new URL("../app/lib/measurement-evidence.ts", import.meta.url)));
const { buildPracticeReport, buildReportBackup } = await import(compiledUrl(new URL("../app/lib/report-export.ts", import.meta.url)));
const keys = Object.keys(SCORE_COMPONENTS);
const base = () => ({ version: "evidence-score-explanation-v1.0.0", score_semantics: "measurement_evidence_quality", rounding_method: "nearest_integer_ties_to_even", deduction_semantics: "evidence_reference_points_not_technical_fault_penalties" });
const excluded = key => ({ key, label_zh: SCORE_COMPONENTS[key], included: false, value_percent: null, effective_weight: null, max_points: null, earned_points: null, deduction_points: null, reason_zh: "证据未提供，此分量不计零分。" });
const component = (key, value, weight) => value === null ? excluded(key) : ({ key, label_zh: SCORE_COMPONENTS[key], included: true, value_percent: value, effective_weight: weight, max_points: 100 * weight, earned_points: value * weight, deduction_points: (100 - value) * weight, reason_zh: "根据本次证据与实际权重计算，不是技术动作扣分。" });
function indicator(id, score, values = [80, 70, 60, null, 100], weights = [.4, 4 / 15, .2, null, 2 / 15]) {
  const available = score !== null;
  const parts = available ? keys.map((key, index) => component(key, values[index], weights[index])) : keys.map(excluded);
  const raw = available ? parts.reduce((total, part) => total + (part.earned_points ?? 0), 0) : null;
  return {
    indicator_id: id, event_code: id.slice(0, 4), name_zh: `指标 ${id}`, available, score_0_to_100: score, score_semantics: "measurement_evidence_quality",
    components: Object.fromEntries(keys.map((key, index) => [`${key}_percent`, values[index]])),
    effective_component_weights: available ? Object.fromEntries(keys.flatMap((key, index) => values[index] === null ? [] : [[key, weights[index]]])) : {},
    total_instance_count: 2, measured_instance_count: available ? 2 : 0,
    score_explanation: { ...base(), aggregation: "single_indicator_weighted_components", status: available ? "available" : "unavailable", score_0_to_100: score, raw_score: raw, rounding_adjustment_points: available ? score - raw : null, components: parts,
      total_instance_count: 2, measured_instance_count: available ? 2 : 0, feature_gaps: [], blocker_reasons: [], blocker_count_semantics: "overlapping_validated_record_counts_not_additive_point_deductions" },
  };
}
function resultOf(items, score) {
  const included = items.filter(item => item.available), omitted = items.filter(item => !item.available);
  const available = included.length > 0, n = included.length;
  const parts = keys.map(key => {
    const parts = included.map(item => item.score_explanation.components.find(row => row.key === key));
    const active = parts.filter(part => part.included);
    if (!active.length) return { ...excluded(key), included_indicator_count: 0, total_indicator_count: n, value_percent_aggregation: "earned_points_divided_by_allocated_max_points" };
    const max = parts.reduce((total, part) => total + (part.max_points ?? 0), 0) / n;
    const earned = parts.reduce((total, part) => total + (part.earned_points ?? 0), 0) / n;
    return { ...component(key, earned / max * 100, max / 100), included_indicator_count: active.length, total_indicator_count: n, value_percent_aggregation: "earned_points_divided_by_allocated_max_points" };
  });
  const raw = available ? parts.reduce((total, part) => total + (part.earned_points ?? 0), 0) : null;
  const mean = available ? included.reduce((total, item) => total + item.score_0_to_100, 0) / n : null;
  return { job_id: "job-valid-12345678", input: { video: { duration_ms: 5000 } }, training_evaluation: {
    available, score_semantics: "measurement_evidence_quality", score_0_to_100: score, indicator_evaluations: items,
    score_explanation: { ...base(), aggregation: "unweighted_mean_of_available_indicator_reference_scores", status: available ? "available" : "unavailable", score_0_to_100: score, raw_score: raw, rounding_adjustment_points: available ? score - raw : null, components: parts,
      included_indicator_ids: included.map(item => item.indicator_id), excluded_indicator_ids: omitted.map(item => item.indicator_id), included_indicator_count: n, total_indicator_count: items.length,
      indicator_scores: included.map(item => ({ indicator_id: item.indicator_id, score_0_to_100: item.score_0_to_100 })), rounded_indicator_mean: mean,
      rounding_stages: { mean_indicator_rounding_points: available ? mean - raw : null, aggregate_rounding_points: available ? score - mean : null } },
  } };
}
const valid = () => resultOf([indicator("FS01-M02", 76), indicator("FS02-M01", 100, [100, 100, 100, 100, 100], [.3, .2, .15, .25, .1]), indicator("FS09-M01", null)], 88);
const textOf = result => renderToStaticMarkup(createElement(Panel, { result, onInspectRule() {} })).replace(/<[^>]*>/g, "");

test("readable exports explain the score and backup preserves only verified nested reference scores", () => {
  const result = valid();
  const input = { mode: "live", uploadState: "complete", job: { id: result.job_id, status: "completed" }, evidence: { jobId: result.job_id, result } };
  const sections = buildPracticeReport(input).sections;
  const explanation = sections.find(section => section.title === "分数解释");
  assert.match(explanation.paragraphs.join(" "), /88.*不是技术评分/);
  assert.equal(explanation.rows.length, 5);
  const backup = buildReportBackup(input);
  assert.equal(scoreExplanationOf(backup.result).status, "verified");
  assert.deepEqual(backup.result.training_evaluation.score_explanation.indicator_scores.map(row => row.score_0_to_100), [76, 100]);
  const imported = normalizeMeasurementResult(JSON.parse(JSON.stringify(backup)).result);
  assert.equal(scoreExplanationOf(imported).status, "verified");
  assert.equal(imported.actions, undefined);
  result.training_evaluation.score_explanation.indicator_scores[0].score_0_to_100 = 77;
  const rejected = buildReportBackup(input);
  assert.ok(rejected.result.training_evaluation.score_explanation.indicator_scores.every(row => row.score_0_to_100 === null));
});

test("evidence deductions reconcile weighted indicators and their equal contribution to the total", () => {
  const state = scoreExplanationOf(valid());
  assert.equal(state.status, "verified"); assert.equal(state.score, 88);
  assert.deepEqual(state.explanation.includedIds, ["FS01-M02", "FS02-M01"]);
  assert.deepEqual(state.explanation.excludedIds, ["FS09-M01"]);
  const component = state.explanation.components.find(item => item.key === "median_feature_confidence");
  assert.equal(component.maximum, 17.5); assert.ok(Math.abs(component.earned - 13.5) < 1e-10); assert.ok(Math.abs(component.deduction - 4) < 1e-10);
  assert.equal(componentImpact(state.explanation.indicators[0], "median_feature_confidence", 2), 4);
  assert.equal(scoreExplanationOf(normalizeMeasurementResult(valid())).status, "verified", "normalizing a valid report must preserve its verified explanation");
});

test("missing components are not zero scores and use their actual redistributed weights", () => {
  const input = resultOf([indicator("FS01-M02", 76)], 76);
  const state = scoreExplanationOf(input);
  const repeatability = state.explanation.components.find(item => item.key === "repeatability");
  assert.equal(repeatability.included, false); assert.equal(repeatability.deduction, null);
  assert.equal(state.explanation.components[0].weight, .4);
  assert.equal(componentImpact(state.explanation.indicators[0], "repeatability", 1), null);
  input.training_evaluation.score_explanation.components[3].deduction_points = 0;
  assert.equal(scoreExplanationOf(input).status, "invalid", "even a fabricated zero is invalid for an excluded component");
});

test("Python half-to-even scores remain server values at both rounding stages", () => {
  const evenDown = indicator("FS01-M02", 10, [10.5, null, null, null, null], [1, null, null, null, null]);
  const evenUp = indicator("FS02-M01", 12, [11.5, null, null, null, null], [1, null, null, null, null]);
  const state = scoreExplanationOf(resultOf([evenDown, evenUp], 11));
  assert.equal(state.status, "verified"); assert.equal(state.explanation.indicatorRounding, 0);
  const aggregateTie = resultOf([evenDown, indicator("FS02-M01", 11, [11, null, null, null, null], [1, null, null, null, null])], 10);
  assert.equal(scoreExplanationOf(aggregateTie).status, "verified");
  const wrongHalf = indicator("FS01-M02", 11, [10.5, null, null, null, null], [1, null, null, null, null]);
  assert.equal(scoreExplanationOf(resultOf([wrongHalf], 11)).status, "invalid");
  aggregateTie.training_evaluation.score_0_to_100 = 11;
  aggregateTie.training_evaluation.score_explanation.score_0_to_100 = 11;
  aggregateTie.training_evaluation.score_explanation.rounding_adjustment_points += 1;
  aggregateTie.training_evaluation.score_explanation.rounding_stages.aggregate_rounding_points += 1;
  assert.equal(scoreExplanationOf(aggregateTie).status, "invalid", "closed arithmetic still cannot replace Python rounding with ties-up");
});

test("Python 3.10 weighted totals tolerate arithmetic noise without relaxing input ranges or closure", () => {
  // A measured FS01-M03 record without confidence/repeatability redistributes
  // weights exactly this way in Python 3.10, whose sum can exceed 100 by 1 ULP.
  const item = indicator("FS01-M03", 100, [100, 100, null, null, 100], [.5, .33333333333333337, null, null, .16666666666666669]);
  const input = resultOf([item], 100);
  assert.equal(item.score_explanation.raw_score, 100.00000000000001);
  const state = scoreExplanationOf(input);
  assert.equal(state.status, "verified");
  assert.equal(state.score, 100);
  assert.equal(state.explanation.raw, 100.00000000000001, "do not clamp or recompute the backend raw value");
  assert.equal(state.explanation.rounding, -1.4210854715202004e-14);
  assert.equal(scoreExplanationOf(normalizeMeasurementResult(input)).status, "verified");
  assert.match(textOf(input), /各分量差额/);
  for (const mutate of [
    source => { source.score_explanation.raw_score += .00001; source.score_explanation.rounding_adjustment_points -= .00001; },
    source => { source.score_explanation.raw_score = -1; },
    source => { source.score_explanation.components[0].earned_points -= .01; },
    source => { source.indicator_evaluations[0].components.measured_instance_ratio_percent = 100.00000000000001; },
    source => { source.indicator_evaluations[0].score_explanation.components[0].value_percent = 100.00000000000001; },
    source => { source.score_0_to_100 = 100.00000000000001; },
  ]) {
    const invalid = structuredClone(input); mutate(invalid.training_evaluation);
    assert.equal(scoreExplanationOf(invalid).status, "invalid");
  }
});

test("malformed explanations never become deductions, despite retaining a separately declared reference score", () => {
  const cases = [
    source => { source.score_explanation.components[0].earned_points += 1; },
    source => { source.score_explanation.components[0].deduction_points = -1; },
    source => { source.score_explanation.components[0].value_percent = NaN; },
    source => { source.score_explanation.raw_score = Infinity; },
    source => { source.score_explanation.version = "evidence-score-explanation-v9"; },
    source => { source.score_explanation.score_semantics = "technical_quality"; },
    source => { source.score_explanation.score_0_to_100 = 87; },
    source => { source.score_explanation.components[1].key = source.score_explanation.components[0].key; },
    source => { source.score_explanation.included_indicator_ids[1] = source.score_explanation.included_indicator_ids[0]; },
    source => { source.score_explanation.indicator_scores[1].indicator_id = source.score_explanation.indicator_scores[0].indicator_id; },
    source => { source.score_explanation.excluded_indicator_ids = []; },
    source => { source.score_explanation.indicator_scores[0].score_0_to_100 += 1; },
    source => { source.indicator_evaluations[0].effective_component_weights.measured_instance_ratio = .3; },
    source => { source.indicator_evaluations[0].components.measured_instance_ratio_percent = 70; },
    source => { source.indicator_evaluations[0].score_explanation.components[0].max_points += 1; },
    source => { source.indicator_evaluations[0].score_explanation.blocker_reasons = [{ key: "missing", reason_zh: "证据缺失", affected_instance_count: 3 }]; },
    source => { source.indicator_evaluations[0].score_explanation = null; },
  ];
  for (const mutate of cases) { const input = valid(); mutate(input.training_evaluation); const state = scoreExplanationOf(input); assert.equal(state.status, "invalid"); assert.equal(state.score, 88); }
  const invalid = valid(); invalid.training_evaluation.score_explanation.components[0].earned_points = NaN;
  assert.match(textOf(invalid), /未附可核验的分数明细/);
  assert.doesNotMatch(textOf(invalid), /各分量差额|−NaN/);
});

test("a self-consistent forged total is still rejected when its components disagree with individual contributions", () => {
  const input = valid(), parts = input.training_evaluation.score_explanation.components;
  parts[0].earned_points += 1; parts[0].deduction_points -= 1; parts[0].value_percent = parts[0].earned_points / parts[0].effective_weight;
  parts[1].earned_points -= 1; parts[1].deduction_points += 1; parts[1].value_percent = parts[1].earned_points / parts[1].effective_weight;
  assert.equal(scoreExplanationOf(input).status, "invalid");
});

test("legacy and entirely unavailable reports stay honest instead of acquiring a zero", () => {
  assert.deepEqual(scoreExplanationOf(null), { status: "missing", score: null });
  const legacy = valid(); delete legacy.training_evaluation.score_explanation;
  assert.deepEqual(scoreExplanationOf(legacy), { status: "missing", score: 88 });
  assert.match(textOf(legacy), /历史报告没有保存分数构成/);
  const unavailable = resultOf([indicator("FS01-M02", null)], null);
  assert.equal(scoreExplanationOf(unavailable).status, "verified");
  assert.match(textOf(unavailable), /不会被记为 0 分/);
  assert.match(textOf(unavailable), /1 项未纳入总分/);
  assert.doesNotMatch(textOf(unavailable), /各分量差额|\/ 100/);
  const html = renderToStaticMarkup(createElement(Panel, { result: valid(), onInspectRule() {} }));
  assert.match(html, /id="score-explanation"/); assert.match(html, /核对原文规则/);
  assert.match(html, /正式技术评分仍待教练标定/); assert.match(html, /不按零分参与平均/);
  assert.equal((html.match(/aria-pressed=/g) ?? []).length, 5);
});

function episode(index, overrides = {}) {
  const start = index * 1000, end = start + 500;
  return { event_id: `event-${index}`, event_code: "FS01", name_zh: "分腿准备", person_track_id: 1, start_ms: start, end_ms: end,
    indicators: [{ indicator_id: "FS01-M02", name_zh: "下沉", feature_status: "measured", scoring_status: "calibration_required", features: [], measurements: [{ feature_name: "hip_center_y_body", name_zh: "髋中心", status: "measured", value: .3, confidence: .9, unit: "body", view_semantics: "image_plane_proxy", window: { start_ms: start, end_ms: end, scope: "event_interval" } }] }], ...overrides };
}
test("only actual measurement windows from the same indicator become capped replay examples", () => {
  const result = valid(); result.footwork_review = { schema_version: "1.1.0", status: "available", episodes: [episode(1), episode(2), episode(3), episode(4)] };
  assert.deepEqual(scoreEvidenceWindows(result, "FS01-M02").map(item => item.id), ["event-1", "event-2", "event-3"]);
  assert.deepEqual(scoreEvidenceWindows(result, "FS02-M01"), []);
  assert.match(textOf(result), /不能把整项差额归因于某一段动作/);
  assert.match(textOf(result), /回看 1.00 秒–1.50 秒/);
});

test("out-of-bounds, unavailable, mismatched or invented measurement events cannot offer replay links", () => {
  const wrongWindow = episode(1); wrongWindow.indicators[0].measurements[0].window.end_ms = 1700;
  const unavailable = episode(2); unavailable.indicators[0].measurements[0].status = "unavailable";
  const noMeasurements = episode(3); delete noMeasurements.indicators[0].measurements;
  const invalidValue = episode(4); invalidValue.indicators[0].measurements[0].value = NaN;
  const result = valid(); result.footwork_review = { schema_version: "1.1.0", status: "available", episodes: [wrongWindow, unavailable, noMeasurements, invalidValue, episode(6), episode(7, { start_ms: -1 }), episode(8, { end_ms: 8000 })] };
  assert.deepEqual(scoreEvidenceWindows(result, "FS01-M02"), []);
  assert.doesNotMatch(textOf(result), /回看 \d/);
});
