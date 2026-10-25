import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import ts from "typescript";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

const cache = new Map();
function compiledUrl(fileUrl) {
  // Keep source JSON imports shared with production, without a generated copy.
  if (fileUrl.pathname.endsWith(".json")) return `data:text/javascript;base64,${Buffer.from(`export default ${fs.readFileSync(fileUrl, "utf8")}`).toString("base64")}`;
  if (cache.has(fileUrl.href)) return cache.get(fileUrl.href);
  let js = ts.transpileModule(fs.readFileSync(fileUrl, "utf8"), { fileName: fileUrl.pathname, compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  js = js.replace(/from "([^"]+)"/g, (_match, name) => {
    const local = new URL(name.endsWith(".json") ? name : `${name}.ts`, fileUrl);
    return `from ${JSON.stringify(name.startsWith(".") ? compiledUrl(fs.existsSync(local) ? local : new URL(`${name}.tsx`, fileUrl)) : import.meta.resolve(name))}`;
  });
  const url = `data:text/javascript;base64,${Buffer.from(js).toString("base64")}`;
  cache.set(fileUrl.href, url);
  return url;
}
const { processEvaluationOf, processRadarGeometry, processCriterionReplay, processCriterionGroups, processPeakSequence, emptyProcessDimensions, trainingDimensions } = await import(compiledUrl(new URL("../app/lib/process-evaluation.ts", import.meta.url)));
const { default: ProcessScorePanel } = await import(compiledUrl(new URL("../app/ProcessScorePanel.tsx", import.meta.url)));
const { default: TrainingReportOverview, TrainingRecommendations } = await import(compiledUrl(new URL("../app/TrainingReportOverview.tsx", import.meta.url)));
const { processPreparationCriterion } = await import(compiledUrl(new URL("../app/lib/process-preparation.ts", import.meta.url)));
const { relativeReferenceOf } = await import(compiledUrl(new URL("../app/lib/relative-score.ts", import.meta.url)));
const { trainingScoreOf } = await import(compiledUrl(new URL("../app/lib/training-score.ts", import.meta.url)));

const preparationFixture = JSON.parse(fs.readFileSync(new URL("./fixtures/process-preparation-real.json", import.meta.url), "utf8"));

// Synthetic source-clock variant of a real numeric translation window. This is
// a contract regression fixture, not evidence of SAM inference or accuracy.
function samTranslationCriterion() {
  const row = structuredClone(preparationFixture.criteria.find(item => item.id === "preparation_translation_hip_center_dx_torso"));
  row.reference = { ...row.reference, pose_source: "sam3d", observation_semantics: "monocular_model_estimate", video_sha256: "a".repeat(64), source_selection_epoch: 0 };
  row.measurement_provenance = { pose_source: "sam3d", observation_semantics: "monocular_model_estimate", assessment_status: "measurement_only",
    scoring_allowed: false, confidence_available: false, visibility_verified: false, model_estimate_sample_count: row.sample_count };
  row.time_basis = "bound_source_pts";
  row.source_time_origin_ms = 75000;
  row.source_timestamps_ms = row.video_timestamps_ms.map(time => time + 75000);
  for (const [edge, index] of [["start", 0], ["end", row.source_frames.length - 1]]) {
    row[`source_binding_${edge}`] = { video_sha256: row.reference.video_sha256, person_track_id: row.person_track_id,
      selection_epoch: 0, camera_reference_epoch: row.reference.camera_reference_epoch, frame_index: row.source_frames[index],
      processed_index: index, pts: row.source_timestamps_ms[index], time_base_num: 1, time_base_den: 1000,
      timestamp_ms: row.source_timestamps_ms[index], video_timestamp_ms: row.video_timestamps_ms[index] };
  }
  for (const field of ["reference", "measurement_provenance", "time_basis", "source_timestamps_ms", "source_binding_start", "source_binding_end"]) {
    row.original_metric_evidence[field] = structuredClone(row[field]);
  }
  return row;
}

test("SAM translation preserves independent clocks, signed values and repeat parsing", () => {
  const original = samTranslationCriterion();
  const first = processPreparationCriterion(original);
  assert.ok(first);
  assert.deepEqual(processPreparationCriterion(first), first);
  assert.deepEqual(first.source_timestamps_ms, first.video_timestamps_ms.map(time => time + 75000));
  for (const value of [0, -Math.abs(original.value)]) {
    const changed = structuredClone(original);
    changed.value = value; changed.original_metric_evidence.value = value;
    assert.equal(processPreparationCriterion(changed).value, value);
  }
});

test("translation rejects inconsistent model clocks, binding, camera and original evidence", () => {
  const changes = [
    row => { delete row.source_binding_start.processed_index; },
    row => { row.source_binding_start.video_sha256 = "b".repeat(64); },
    row => { row.source_binding_end.timestamp_ms += 1000; },
    row => { row.source_time_origin_ms += 1000; },
    row => { row.time_basis = "decoder_timestamp_ms"; row.original_metric_evidence.time_basis = row.time_basis; },
    row => { row.reference.camera_compensated = false; row.original_metric_evidence.reference.camera_compensated = false; },
    row => { row.video_timestamps_ms[1] += 10; },
    row => { row.original_metric_evidence.source_duration_ms += 1000; },
    row => { row.source_timestamps_ms = row.video_timestamps_ms.map(time => 75000 + row.start_ms + 2*(time-row.start_ms));
      row.source_duration_ms *= 2; row.original_metric_evidence.source_timestamps_ms = [...row.source_timestamps_ms];
      row.original_metric_evidence.source_duration_ms = row.source_duration_ms; },
  ];
  for (const change of changes) {
    const row = samTranslationCriterion(); change(row);
    assert.equal(processPreparationCriterion(row), null);
  }
});

test("actual preparation candidates and local joint windows survive frontend parsing with original evidence", () => {
  const input = preparationFixture.criteria.filter(row => ["preparation_candidate_", "preparation_joint_", "preparation_translation_"].some(prefix => row.id.startsWith(prefix)));
  assert.equal(input.filter(row => row.id.startsWith("preparation_candidate_")).length, preparationFixture.new_measurement_count);
  assert.ok(preparationFixture.new_measurement_count > 0);
  for (const row of input) {
    const parsed = processPreparationCriterion(row);
    assert.ok(parsed, row.id);
    assert.equal(parsed.value, row.value);
    assert.equal(parsed.score, null);
    assert.deepEqual(parsed.source_frames, row.source_frames);
    assert.deepEqual(parsed.reference, row.reference);
    assert.equal(parsed.start_ms, row.start_ms);
    assert.equal(parsed.end_ms, row.end_ms);
    assert.deepEqual(processPreparationCriterion(parsed), parsed);
    if (row.id.startsWith("preparation_candidate_")) {
      assert.equal(parsed.source_card_applicability, "unconfirmed");
      assert.deepEqual(parsed.original_metric_evidence, row.original_metric_evidence);
      assert.deepEqual(parsed.source_timestamps_ms, row.source_timestamps_ms);
    }
  }
});

test("preparation measurements reject foreign reference, fabricated score and detached original evidence", () => {
  const original = preparationFixture.criteria.find(row => row.id.includes("candidate_mean_speed"));
  for (const mutate of [
    row => { row.reference.person_track_id += 1; },
    row => { row.score = 100; },
    row => { row.original_metric_evidence.wrist.mean_speed_torso_per_s += 1; },
    row => { row.source_timestamps_ms[1] = row.source_timestamps_ms[0]; },
    row => { row.source_frames[1] = row.source_frames[0]; },
    row => { row.source_card_applicability = "confirmed"; },
    row => { row.source = "rotation_analysis.metrics.mean_speed_torso_per_s"; },
    row => { row.related_source_cards = ["GS05-M01-03"]; },
  ]) {
    const row = structuredClone(original); mutate(row);
    assert.equal(processPreparationCriterion(row), null);
  }
});

test("empty recommendations distinguish no job, pending, failed, cancelled and completed evidence", () => {
  const render = options => renderToStaticMarkup(createElement(TrainingRecommendations, { result: null, motion: null, pending: false, ...options }));
  assert.match(render({}), /上传一段训练视频/);
  assert.match(render({ pending: true }), /分析完成后会显示本次观察和对应练习/);
  assert.match(render({ status: "failed" }), /本次分析未完成/);
  assert.match(render({ status: "cancelled" }), /本次分析已取消/);
  for (const options of [{ status: "succeeded" }, { status: "completed" }]) {
    const html = render(options);
    assert.match(html, /本次没有足够的动作测量/);
    assert.doesNotMatch(html, /上传一段训练视频/);
  }
  const existingGuidance = render({ status: "succeeded", result: { job_id: "completed-job", indicators: [] } });
  assert.match(existingGuidance, /基础练习/);
  assert.match(existingGuidance, /怎么练/); assert.match(existingGuidance, /复测标准/);
  assert.doesNotMatch(existingGuidance, /上传一段训练视频|本次没有足够的动作测量/);
});
const realProcess = JSON.parse(fs.readFileSync(new URL("./fixtures/process-v1.1-real.json", import.meta.url), "utf8"));
const source = "action_recognition.motion_analysis.episodes.process_motion";
const criterion = overrides => ({ id: "acceleration_trend", label_zh: "挥拍加速趋势参考", status: "measured", value: 80, unit: "%", score: 80, sample_count: 8, reason_zh: "腕速正向变化占 80%。", rule_zh: "100 × 正向变化量 / 全部变化量。", source: `${source}.phases.wrist.speed_increase_fraction`, start_ms: 1000, end_ms: 1600, episode_id: "swing-1", ...overrides });
const dimension = overrides => ({ id: "swing", name_zh: "挥拍", status: "partial", score: 80, summary_zh: "80分", aggregation: "mean_of_independent_samples_for_one_explicit_rule", criteria: [criterion()], ...overrides });
const report = dimensions => ({ version: "process-evaluation-v1.0.0", semantics: "observable_motion_process_reference", is_calibrated: false, overall_score: null, dimensions: dimensions ?? [dimension()] });
const parsedSwing = value => processEvaluationOf(value).dimensions.find(item => item.id === "swing");
const render = (value, extra = {}) => renderToStaticMarkup(createElement(ProcessScorePanel, { result: value === null ? null : { process_evaluation: value }, motion: null, pending: false, title: "分析中", description: "正在整理依据。", ...extra }));
const motion = () => ({ families: { baseline: { episodes: [{ episode_id: "swing-1", start_ms: 900, end_ms: 1700, classification: { label_zh: "动作一" } }, { episode_id: "swing-2", start_ms: 2200, end_ms: 2800, classification: { label_zh: "动作二" } }] } } });

// Transport fields copied from the actual DINOv3 service-projection-v2 phase summary.
const modelProvenance = () => ({ pose_source: "sam3d", observation_semantics: "monocular_model_estimate", assessment_status: "measurement_only",
  scoring_allowed: false, confidence_available: false, visibility_verified: false, model_estimate_sample_count: 20,
  reason_zh: "基于单目模型估计的二维关节几何；模型未提供关键点置信度或可见性，保留测量但不用于技术计分。" });
const modelCriterion = overrides => criterion({ score: null, assessment_status: "measurement_only", measurement_provenance: modelProvenance(), ...overrides });

test("legacy model phase measurements preserve provenance and measured zero without gaining a score", () => {
  const input = report([dimension({ score: null, criteria: [modelCriterion({ value: 0 })] })]);
  const parsed = parsedSwing(input);
  assert.equal(parsed.criteria[0].status, "measured");
  assert.equal(parsed.criteria[0].value, 0);
  assert.equal(parsed.criteria[0].score, null);
  assert.equal(parsed.criteria[0].assessment_status, "measurement_only");
  assert.deepEqual(parsed.criteria[0].measurement_provenance, modelProvenance());
  assert.equal(parsed.score, null);
  assert.equal(parsed.status_reason, "selection_biased_proxy_excluded");
  assert.match(parsed.status_reason_zh, /已有单目模型估计.*置信度和可见性未提供/);
  assert.doesNotMatch(parsed.summary_zh, /尚缺连续运动测量/);
  assert.deepEqual(parsedSwing(processEvaluationOf(input)), parsed);
  assert.match(render(input), /已有单目模型估计/);
  assert.deepEqual(processRadarGeometry(trainingDimensions(processEvaluationOf(input).dimensions)).points, [null, null, null, null, null]);
});

test("imported model phase values cannot be promoted by a forged matching engineering score or aggregate", () => {
  for (const value of [0, 80, 100]) {
    const input = report([dimension({ score: value, criteria: [modelCriterion({ value, score: value })] })]);
    const parsed = parsedSwing(input);
    assert.equal(parsed.criteria[0].value, value);
    assert.equal(parsed.criteria[0].score, null);
    assert.equal(parsed.score, null);
    assert.equal(parsed.aggregation, "none");
    assert.deepEqual(parsedSwing(processEvaluationOf(input)), parsed);
  }
  const mixed = parsedSwing(report([dimension({ criteria: [criterion(), modelCriterion({ episode_id: "model-2", score: 80 })] })]));
  assert.equal(mixed.score, null, "one rejected model score invalidates the supplied aggregate");
  assert.equal(mixed.criteria[0].score, null, "the selection-biased trend is no longer a quality score");
  assert.equal(mixed.criteria[0].legacy_diagnostic.score, 80);
  assert.equal(mixed.criteria[1].score, null);
});

test("incomplete or contradictory phase model provenance fails closed and cannot lose its non-scoring marker", () => {
  const mutations = [
    row => { delete row.assessment_status; },
    row => { delete row.measurement_provenance; },
    row => { row.measurement_provenance.pose_source = "observed"; },
    row => { row.measurement_provenance.observation_semantics = "observed"; },
    row => { row.measurement_provenance.assessment_status = "scored"; },
    row => { row.measurement_provenance.scoring_allowed = true; },
    row => { row.measurement_provenance.confidence_available = true; },
    row => { delete row.measurement_provenance.visibility_verified; },
    row => { row.measurement_provenance.model_estimate_sample_count = 0; },
    row => { row.measurement_provenance.model_estimate_sample_count = 2.5; },
  ];
  for (const mutate of mutations) {
    const row = modelCriterion({ score: 80 }); mutate(row);
    const parsed = parsedSwing(report([dimension({ criteria: [row] })]));
    assert.equal(parsed.score, null);
    assert.equal(parsed.criteria[0].status, "unavailable");
    assert.equal(parsed.criteria[0].value, null);
    assert.equal(parsed.criteria[0].assessment_status, "measurement_only");
    assert.equal(parsed.criteria[0].score, null);
    assert.deepEqual(parsedSwing(report([parsed])), parsed);
  }
});

test("measured directions without a score are not described as missing measurements and raw status prose is not trusted", () => {
  const input = report([dimension({ score: null, status_reason: "model_estimate_measurement_only", status_reason_zh: "伪造模型说明",
    criteria: [criterion({ id: "swing_direction", unit: "deg", value: 0, score: null, source: `${source}.phases.wrist.direction_angle_deg` })] })]);
  const parsed = parsedSwing(input);
  assert.equal(parsed.status_reason, "selection_biased_proxy_excluded");
  assert.match(parsed.summary_zh, /保留可用测量/);
  assert.doesNotMatch(parsed.summary_zh, /尚缺连续运动测量|伪造模型说明/);
});

const actualProjectionRoot = new URL("../../.codex_tmp/autodl-sam-coordination-20261018/qa-evidence/full97s-three-model-scoring/", import.meta.url);
for (const [model, expected] of [["rtmpose", [600, 380, 49, 54]], ["dinov3-body", [488, 337, 73, 51]], ["vith-body", [422, 316, 78, 48]]]) {
  const file = new URL(`${model}/service-projection-v2/process-evaluation.json`, actualProjectionRoot);
  test(`actual ${model} v2 projection retains full counts, source declarations and exact values twice`, { skip: !fs.existsSync(file) }, () => {
    const raw = JSON.parse(fs.readFileSync(file, "utf8")), parsed = processEvaluationOf(raw);
    assert.deepEqual(processEvaluationOf(parsed), parsed);
    const [preparation, swing, follow] = ["preparation", "swing", "follow_through"].map(id => parsed.dimensions.find(row => row.id === id));
    assert.deepEqual([preparation.measured_criterion_count, preparation.returned_measured_criterion_count, swing.measured_criterion_count, follow.measured_criterion_count], expected);
    for (const before of raw.dimensions) {
      const after = parsed.dimensions.find(row => row.id === before.id);
      assert.equal(after.score, ["swing", "follow_through"].includes(before.id) ? null : before.score);
      for (const criterion of before.criteria.filter(row => row.status === "measured")) {
        const saved = after.criteria.find(row => row.id === criterion.id && row.episode_id === criterion.episode_id && row.start_ms === criterion.start_ms && row.end_ms === criterion.end_ms);
        assert.ok(saved, `${model} ${criterion.id}`);
        for (const field of ["status", "value", "unit", "source", "start_ms", "end_ms", "assessment_status"]) assert.deepEqual(saved[field], criterion[field], `${model} ${criterion.id} ${field}`);
        if (Object.keys(criterion.measurement_provenance ?? {}).length) assert.deepEqual(saved.measurement_provenance, criterion.measurement_provenance);
      }
    }
    if (model !== "rtmpose") for (const row of [preparation, swing, follow]) {
      assert.equal(row.status_reason, row.id === "preparation" ? "rule_not_implemented" : "selection_biased_proxy_excluded");
      assert.match(row.status_reason_zh, /已有单目模型估计/);
      assert.doesNotMatch(row.summary_zh, /尚缺连续运动测量/);
      assert.equal(row.score, null);
    }
    else assert.deepEqual([parsed.dimensions[0].measured_criterion_count, parsed.dimensions[0].returned_measured_criterion_count], [1052, 336]);
  });
}

test("real v1.1 report retains all 298 source memberships and full versus sampled measurement counts", () => {
  const parsed = processEvaluationOf(realProcess);
  assert.equal(parsed.version, "process-evaluation-v1.1.0");
  assert.deepEqual(trainingDimensions(parsed.dimensions).map(row => row.source_basis.card_count), [70, 78, 72, 25, 53]);
  assert.equal(new Set(parsed.dimensions.flatMap(row => row.source_basis?.card_ids ?? [])).size, 298);
  const movement = parsed.dimensions[0];
  assert.equal(movement.score, null);
  assert.equal(movement.measured_criterion_count, 1052);
  assert.equal(movement.returned_measured_criterion_count, 336);
  assert.equal(movement.criteria.length, 377);
  assert.equal(movement.sample_count, 116);
  assert.equal(movement.total_rule_count, 29);
  assert.equal(movement.measurement_input_scope.returned_event_count, 123);
  assert.equal(movement.status_reason, "independent_reference_missing");
  assert.equal(parsed.dimensions[3].total_rule_count, 3);
  for (const row of parsed.dimensions) {
    const original = realProcess.dimensions.find(item => item.id === row.id);
    assert.equal(row.score, ["swing", "follow_through"].includes(row.id) ? null : original.score);
    assert.equal(row.criteria.filter(item => item.status === "measured").length, original.criteria.filter(item => item.status === "measured").length);
  }
  assert.deepEqual(processEvaluationOf(JSON.parse(JSON.stringify(parsed))), parsed);
  const html = render(realProcess);
  assert.match(html, /缺少评分依据的维度留空/);
  assert.match(html, /未评分/);
  assert.match(html, /完整技术规则尚未实现/);
  assert.doesNotMatch(html, /原文目录|298|GS\d{2}-M\d{2}|FS\d{2}-M\d{2}/);
  assert.doesNotMatch(html, /击球效果|击球质量|随挥恢复/);
});

test("new footwork observations require the reviewed whitelist and identity/quality provenance", () => {
  const raw = realProcess.dimensions[0].criteria.find(item => item.id.startsWith("footwork_"));
  const measure = overrides => processEvaluationOf(report([dimension({ id: "movement", score: null, criteria: [{ ...raw, ...overrides }] })])).dimensions[0];
  assert.equal(measure({}).criteria[0].status, "measured");
  for (const overrides of [{ score: 99 }, { id: "footwork_FS10-M01_invented" }, { feature_name: "invented" }, { indicator_id: "FS02-M01" }, { source: "unverified" }, { unit: "m" }, { video_id: "" }, { person_track_id: -1 }, { source_frames: [] }, { source_frames: [true] }, { feature_version: "" }, { window_scope: "whole_video" }, { measurement_semantics: "calibrated_grade" }, { quality: { ...raw.quality, confidence: 1.1 } }, { quality: { ...raw.quality, reason_codes: ["camera_motion"] } }, { quality: { ...raw.quality, review_version: "invented" } }]) {
    const parsed = measure(overrides);
    assert.equal(parsed.measured_criterion_count, 0, JSON.stringify(overrides));
    assert.equal(parsed.score, null);
  }
});

test("signed footwork changes and out-of-support projections remain observations, never scores", () => {
  for (const [feature, value] of [["hip_center_relative_to_ankle_support", 1.4], ["post_step_hip_direction_consistency", -.7], ["left_knee_flexion_change_deg", -20], ["braking_ankle_speed_drop_body_s", -1.2]]) {
    const raw = realProcess.dimensions[0].criteria.find(item => item.feature_name === feature);
    assert.ok(raw, feature);
    const movement = processEvaluationOf(report([dimension({ id: "movement", score: null, criteria: [{ ...raw, value }] })])).dimensions[0];
    assert.equal(movement.criteria[0].value, value);
    assert.equal(movement.criteria[0].score, null);
  }
});

test("partial source catalogues and corrupt sample counts cannot masquerade as complete evidence", () => {
  const corrupt = structuredClone(realProcess);
  corrupt.dimensions[0].source_basis.card_ids.pop();
  corrupt.dimensions[0].criteria_selection.returned_count++;
  const parsed = processEvaluationOf(corrupt).dimensions[0];
  assert.equal(parsed.source_basis, undefined);
  assert.equal(parsed.criteria_selection, undefined);
  assert.equal(parsed.measured_criterion_count, 336);
  assert.doesNotMatch(parsed.summary_zh, /1052/);
  const invalidMeasurement = structuredClone(realProcess);
  invalidMeasurement.dimensions[0].criteria.find(row => row.id.startsWith("footwork_")).quality.confidence = 5;
  assert.equal(processEvaluationOf(invalidMeasurement).dimensions[0].criteria_selection, undefined);
});

test("footwork replay cannot cross video identity, tracked player or event boundaries", () => {
  const row = processEvaluationOf(realProcess).dimensions[0].criteria.find(item => item.id.startsWith("footwork_"));
  const episode = { event_id: row.episode_id, video_id: row.video_id, person_track_id: row.person_track_id, event_code: "FS01", name_zh: "准备", start_ms: row.start_ms, end_ms: row.end_ms, indicators: [] };
  const result = { footwork_review: { schema_version: "1.1.0", status: "available", episodes: [episode] } };
  assert.deepEqual(processCriterionReplay(row, result), { startMs: row.start_ms });
  for (const override of [{ video_id: "other-video" }, { person_track_id: 999 }, { start_ms: row.start_ms + 1 }]) assert.equal(processCriterionReplay({ ...row, ...override }, result), null);
});

test("only the explicit process contract is accepted and all six original evidence dimensions remain", () => {
  const input = report(), snapshot = JSON.stringify(input), parsed = processEvaluationOf(input);
  assert.equal(parsed.dimensions.length, 6);
  assert.deepEqual(parsed.dimensions.map(item => item.name_zh), ["移动质量", "准备与引拍", "挥拍协调", "触球控制", "随挥与衔接", "两次之间的衔接"]);
  assert.equal(parsed.overall_score, null); assert.equal(parsedSwing(input).score, null);
  assert.equal(parsedSwing(input).criteria[0].legacy_diagnostic.score, 80);
  assert.equal(JSON.stringify(input), snapshot);
  for (const overrides of [{ version: "next-version" }, { semantics: "measurement_evidence_quality" }, { is_calibrated: true }, { overall_score: 80 }, { dimensions: {} }]) assert.equal(processEvaluationOf({ ...input, ...overrides }), null);
});

test("product chart has the five approved dimensions and never merges transition into a score", () => {
  const dimensions = emptyProcessDimensions();
  Object.assign(dimensions[4], { status: "partial", score: 42 });
  Object.assign(dimensions[5], { status: "partial", score: 98 });
  const product = trainingDimensions(dimensions);
  assert.deepEqual(product.map(item => item.name_zh), ["移动质量", "准备与引拍", "挥拍协调", "触球控制", "随挥与衔接"]);
  assert.equal(product.length, 5); assert.equal(product[4].score, 42); assert.equal(dimensions.length, 6);
  const graph = processRadarGeometry(product);
  assert.equal(graph.points.length, 5); assert.equal(graph.points.filter(Boolean).length, 1);
  assert.equal(graph.points[4].score, 42);
  const html = render(report());
  assert.match(html, /五维动作评分图/); assert.match(html, /<details class="process-explanation"><summary>查看评分依据与动作细节/);
  assert.equal((html.match(/aria-pressed=/g) ?? []).length, 5);
  assert.doesNotMatch(html, /六维|六个维度|<details[^>]*open/);
});

test("unavailable, corrupt or unbound observations never become a score", () => {
  for (const overrides of [{ status: "unavailable" }, { value: NaN }, { value: Infinity }, { value: "80" }, { value: true }, { value: -1 }, { value: 101 }, { score: 110 }, { score: "80" }, { score: 79 }, { unit: "deg" }, { source: "unverified" }, { start_ms: -1 }, { end_ms: 500 }, { episode_id: null }, { sample_count: 2 }, { sample_count: undefined }, { rule_zh: "" }]) {
    const parsed = parsedSwing(report([dimension({ criteria: [criterion(overrides)] })]));
    assert.equal(parsed.score, null, JSON.stringify(overrides));
  }
  const unavailable = parsedSwing(report([dimension({ status: "unavailable" })]));
  assert.equal(unavailable.criteria[0].value, null); assert.equal(unavailable.criteria[0].score, null);
  assert.equal(unavailable.status, "unavailable");
});

test("legacy curve aggregates stay diagnostic and preserve repeated measurements across episodes", () => {
  const input = report([dimension({ score: 60, criteria: [criterion({ value: 20, score: 20 }), criterion({ episode_id: "swing-2", value: 100, score: 100, start_ms: 2000, end_ms: 2600 })] })]);
  const parsed = parsedSwing(input);
  assert.equal(parsed.score, null); assert.equal(parsed.criteria.length, 2);
  assert.equal(parsed.scored_rule_count, 0); assert.equal(parsed.sample_count, 2); assert.equal(parsed.scored_sample_count, 0);
  assert.equal(processEvaluationOf(input).relative_reference.legacy_diagnostic.dimensions[2].rules[0].raw_mean, 60);
  assert.equal(processCriterionGroups(parsed.criteria).length, 2);
  input.dimensions[0].score = 99;
  assert.equal(parsedSwing(input).score, null);
  assert.doesNotMatch(parsedSwing(input).summary_zh, /99/);
  input.dimensions[0].score = 60; input.dimensions[0].aggregation = "unknown";
  assert.equal(parsedSwing(input).score, null);
  input.dimensions[0].criteria.push(criterion({ value: 20, score: 20 }));
  assert.equal(parsedSwing(input).score, null);
  assert.equal(parsedSwing(input).criteria.length, 2);
});

test("missing samples stay in the denominator without being scored as zero", () => {
  const parsed = parsedSwing(report([dimension({ criteria: [criterion(), criterion({ episode_id: "swing-2", status: "unavailable", value: null, score: null })] })]));
  assert.equal(parsed.score, null); assert.equal(parsed.total_sample_count, 2); assert.equal(parsed.scored_sample_count, 0);
  assert.equal(parsed.criteria[0].legacy_diagnostic.score, 80);
  assert.equal(parsed.criteria[1].legacy_diagnostic.score, null);
});

test("directions remain signed observations and scoreless dimensions never acquire a score", () => {
  const c = criterion({ id: "swing_direction", unit: "deg", value: -120, score: null, source: `${source}.phases.wrist.direction_angle_deg` });
  const parsed = parsedSwing(report([dimension({ score: 80, criteria: [c] })]));
  assert.equal(parsed.criteria[0].value, -120); assert.equal(parsed.score, null); assert.equal(parsed.status, "partial");
  for (const overrides of [{ value: -181 }, { score: 80 }, { id: "new_unreviewed_rule" }]) assert.equal(parsedSwing(report([dimension({ criteria: [{ ...c, ...overrides }] })])).score, null);
  const contact = processEvaluationOf(report([dimension({ id: "contact", criteria: [criterion()] })])).dimensions.find(item => item.id === "contact");
  assert.equal(contact.score, null); assert.equal(contact.status, "unavailable");
});

test("duration measurements must agree with their evidence windows", () => {
  const c = criterion({ id: "preparation_duration", value: 600, score: null, unit: "ms", source: `${source}.phases` });
  const get = row => processEvaluationOf(report([dimension({ id: "preparation", score: null, criteria: [row] })])).dimensions[1].criteria[0];
  assert.equal(get(c).value, 600); assert.equal(get({ ...c, value: 800 }).value, null);
});

test("direction continuity and reversals remain unscored observations with strict units and ranges", () => {
  const consistency = criterion({ id: "swing_direction_consistency", label_zh: "腕部方向一致性", value: .82, score: null, unit: "ratio", source: `${source}.direction.direction_consistency_ratio` });
  const reversals = criterion({ id: "swing_direction_reversals", label_zh: "腕部运动反向次数", value: 2, score: null, unit: "count", source: `${source}.direction.reversal_count` });
  const input = report([dimension({ score: null, criteria: [consistency, reversals] })]);
  const parsed = parsedSwing(input);
  assert.equal(parsed.score, null); assert.equal(parsed.criteria[0].value, .82); assert.equal(parsed.criteria[1].value, 2);
  assert.equal(parsed.total_rule_count, 6);
  for (const value of [-.1, 1.1, Infinity]) assert.equal(parsedSwing(report([dimension({ criteria: [{ ...consistency, value }] })])).criteria[0].value, null);
  for (const value of [-1, .5, Number.MAX_SAFE_INTEGER + 1]) assert.equal(parsedSwing(report([dimension({ criteria: [{ ...reversals, value }] })])).criteria[0].value, null);
  const html = render(input);
  assert.match(html, /<strong>82<\/strong><span>%<\/span>/);
  assert.match(html, /<strong>2<\/strong><span>次<\/span>/);
  assert.doesNotMatch(html, /参考 82 分|参考 2 分/);
});

test("raw zero stays real while missing axes remain unscored in the product chart", () => {
  const dims = emptyProcessDimensions();
  for (const index of [0, 1, 3]) Object.assign(dims[index], { status: "partial", score: index === 0 ? 0 : 75 });
  const graph = processRadarGeometry(dims);
  assert.equal(graph.points.filter(Boolean).length, 3); assert.equal(graph.lines.length, 1); assert.equal(graph.complete, false);
  assert.equal(graph.points[0].x, 180); assert.equal(graph.points[0].y, 150); assert.equal(graph.points[2], null);
  const html = render(report());
  assert.equal((html.match(/class="process-radar-point/g) ?? []).length, 0);
  assert.equal((html.match(/class="process-radar-point[^"]*is-neutral/g) ?? []).length, 0);
  assert.match(html, /缺少评分依据的维度留空/);
  for (const tick of [0, 25, 50, 75, 100]) assert.match(html, new RegExp(`class="process-radar-tick"[^>]*>${tick}</text>`));
});

test("actual peak order is only shown when timing resolves every adjacent peak", () => {
  const c = (id, value, suffix) => criterion({ id, value, unit: "ms", score: null, source: `${source}.sequence.${suffix}`, timing_resolution_ms: 20, ordering_resolved: true });
  const rows = [c("hip_to_shoulder_peak_delay", -100, "hip_to_shoulder_ms"), c("shoulder_to_wrist_peak_delay", 250, "shoulder_to_wrist_ms")];
  assert.equal(processPeakSequence(parsedSwing(report([dimension({ score: null, criteria: rows })])).criteria), "肩 → 髋 → 腕");
  for (const overrides of [{ ordering_resolved: false }, { timing_resolution_ms: null }, { timing_resolution_ms: 200 }, { value: 0 }, { episode_id: "another-motion" }]) assert.equal(processPeakSequence([{ ...rows[0], ...overrides }, rows[1]]), null);
});

test("replay checks episode identity and boundaries including both sides of a transition", () => {
  assert.deepEqual(processCriterionReplay(criterion(), null, motion()), { startMs: 1000 });
  assert.equal(processCriterionReplay(criterion({ end_ms: 1800 }), null, motion()), null);
  assert.equal(processCriterionReplay(criterion({ episode_id: "different" }), null, motion()), null);
  const transition = criterion({ id: "between_motion_interval", value: 500, unit: "ms", score: null, episode_id: "swing-2", related_episode_id: "swing-1", start_ms: 1700, end_ms: 2200, source: `${source}.transition_from_previous` });
  const parsed = processEvaluationOf(report([dimension({ id: "transition", score: null, criteria: [transition] })])).dimensions[5].criteria[0];
  assert.equal(parsed.status, "measured"); assert.deepEqual(processCriterionReplay(parsed, null, motion()), { startMs: 1700 });
  assert.equal(processCriterionReplay({ ...parsed, related_episode_id: "missing" }, null, motion()), null);
});

test("long reports render one selected episode instead of hundreds of evidence cards", () => {
  const rows = Array.from({ length: 73 }, (_, index) => criterion({ episode_id: `swing-${index + 1}`, start_ms: 1000 + index * 1000, end_ms: 1600 + index * 1000 }));
  const html = render(report([dimension({ criteria: rows })]));
  assert.equal((html.match(/class="process-criterion"/g) ?? []).length, 1);
  assert.equal((html.match(/<option /g) ?? []).length, 73);
  assert.match(html, /选择过程评价维度/); assert.match(html, /aria-pressed="true"[^>]*><span>挥拍/);
  assert.match(html, /未评分的维度留空|已测到动作数据，但还不足以判断各维表现/); assert.match(html, /本次训练评分/);
});

test("completed empty and historical reports stay unscored while pending preserves evidence", () => {
  for (const value of [{}, report([]), { ...report(), version: "unknown" }]) {
    const html = render(value);
    assert.equal((html.match(/class="process-radar-point/g) ?? []).length, 0);
    assert.match(html, /class="process-overall-reference"><strong>—<\/strong>/);
    assert.doesNotMatch(html, /中性起点|显示 50 中性暂估|补为 50/);
    assert.match(html, /0 \/ 5 维有评分依据/);
    assert.match(html, /当前识别结果还不足以计算动作得分/);
    assert.match(html, /0 \/ 5 维有测量/);
    assert.match(html, /未评分/);
  }
  const pending = render(null, { pending: true });
  assert.doesNotMatch(pending, /class="process-radar-point/); assert.match(pending, /分析中/);
  const partial = render(report(), { pending: true });
  assert.doesNotMatch(partial, /class="process-radar-point/);
  assert.doesNotMatch(partial, /class="process-overall-reference"/);
  assert.doesNotMatch(render(null), /class="process-overall-reference"/);
  const overview = renderToStaticMarkup(createElement(TrainingReportOverview, { result: { process_evaluation: report(), training_evaluation: { score_0_to_100: 91, score_semantics: "measurement_evidence_quality" } }, motion: null, pending: false, title: "", description: "" }));
  assert.match(overview, /<details class="report-score report-process-auxiliary"><summary>测量情况/);
  assert.doesNotMatch(overview, /report-reference-highlight/);
});

test("training score is visible before collapsed details while historical scores stay diagnostic", () => {
  const renderOverview = result => renderToStaticMarkup(createElement(TrainingReportOverview, { result, motion: null, pending: false, title: "", description: "" }));
  const value = { job_id: "any-completed-report", process_evaluation: report(), training_evaluation: { score_0_to_100: 91, score_semantics: "measurement_evidence_quality" } };
  const html = renderOverview(value);
  assert.match(html, /class="process-overall-reference"><strong>—<\/strong>/);
  assert.equal(processEvaluationOf(value.process_evaluation).relative_reference.legacy_diagnostic.overall_score, 51.5);
  assert.equal(processEvaluationOf(value.process_evaluation).relative_reference.legacy_relative_reference.overall_score, 50);
  assert.doesNotMatch(html, /按旧公式重算|中性先验/);
  assert.equal((html.match(/class="process-overall-reference"/g) ?? []).length, 1);
  assert.ok(html.indexOf('class="process-overall-reference"') < html.indexOf('<details'));
  assert.match(html, /0 \/ 5 维有评分依据/);
  assert.match(html, /测量证据参考分：91 \/ 100/);
  assert.match(html, /高分不代表动作正确/);
  value.process_evaluation.dimensions[0].criteria[0].value = 20;
  value.process_evaluation.dimensions[0].criteria[0].score = 20;
  value.process_evaluation.dimensions[0].score = 20;
  assert.match(renderOverview(value), /class="process-overall-reference"><strong>—<\/strong>/);
  assert.equal(processEvaluationOf(value.process_evaluation).relative_reference.legacy_diagnostic.overall_score, 48.5);
  assert.doesNotMatch(renderOverview(value), /按旧公式重算/);
});

test("no report, read failure, cancelled or pending jobs do not manufacture a neutral final score", () => {
  const renderOverview = (result, pending = false) => renderToStaticMarkup(createElement(TrainingReportOverview, { result, motion: null, pending, title: "报告读取失败", description: "重试读取" }));
  assert.doesNotMatch(renderOverview(null), /class="process-overall-reference"/);
  for (const status of ["failed", "error", "cancelled", "queued", "pending", "running", "processing"]) {
    assert.doesNotMatch(renderOverview({ job_id: "unfinished-job", status }), /class="process-overall-reference"/);
  }
  assert.doesNotMatch(renderOverview({ process_evaluation: report() }, true), /class="process-overall-reference"/);
  for (const value of [{ job_id: "old-report" }, { job_id: "empty-new-report", process_evaluation: report([]) }]) {
    assert.match(renderOverview(value), /class="process-overall-reference"><strong>—<\/strong>/);
  }
});

test("null fallback tolerates malformed legacy containers without trusting stored reference numbers", () => {
  for (const input of [null, {}, "old", [null, false, []], [{ id: "swing", criteria: {} }], [{ id: "swing", criteria: [null, criterion({ episode_id: 7 })] }]]) {
    assert.equal(relativeReferenceOf(input).overall_score, null);
  }
  const value = { ...report([]), relative_reference: { overall_score: 99, observed_dimension_count: 5 } };
  assert.match(render(value), /class="process-overall-reference"><strong>—<\/strong>/);
  assert.match(render(report([dimension({ score: null, criteria: [modelCriterion({ value: 80 })] })])), /class="process-overall-reference"><strong>—<\/strong>/);
});

test("current score ignores legacy SAM curve scores while preserving their historical diagnostics", () => {
  assert.equal(relativeReferenceOf().overall_score, null);
  const input = report([dimension({ score: null, criteria: [modelCriterion({ value: 80 })] })]);
  assert.equal(processEvaluationOf({ ...input, relative_reference: { overall_score: 100, is_calibrated: true } }), null);
  const parsed = processEvaluationOf(input);
  assert.equal(parsed.relative_reference.overall_score, null);
  assert.equal(parsed.relative_reference.dimensions[2].score, null);
  assert.equal(parsed.relative_reference.legacy_diagnostic.overall_score, 51.5);
  assert.equal(parsed.relative_reference.legacy_diagnostic.dimensions[2].rules[0].model_estimate_sample_count, 1);
  assert.equal(parsed.overall_score, null);
  assert.deepEqual(processEvaluationOf(parsed).relative_reference, parsed.relative_reference);
  const lower = structuredClone(input); lower.dimensions[0].criteria[0].value = 20;
  assert.equal(processEvaluationOf(lower).relative_reference.overall_score, null);
  assert.equal(processEvaluationOf(lower).relative_reference.legacy_diagnostic.overall_score, 48.5);
});

test("current SAM mathematical reference passes numeric rule validation without confidence thresholds", () => {
  const input = { ...report([dimension({ criteria: [modelCriterion({ score: 80, assessment_status: "mathematical_reference" })] })]), version: "process-evaluation-v1.2.0" };
  const parsed = processEvaluationOf(input);
  assert.equal(parsed.dimensions[2].score, null);
  assert.equal(parsed.dimensions[2].criteria[0].assessment_status, "measurement_only");
  assert.equal(parsed.relative_reference.overall_score, null);
  assert.equal(parsed.relative_reference.legacy_diagnostic.overall_score, 51.5);
  const reparsed = processEvaluationOf(parsed);
  assert.deepEqual(reparsed.relative_reference, parsed.relative_reference);
  assert.deepEqual(reparsed.dimensions[2], parsed.dimensions[2]);
});

test("relative reference excludes conflicting duplicates, nonfinite values and invalid model provenance", () => {
  const high = modelCriterion({ value: 80 });
  const same = report([dimension({ score: null, criteria: [high, structuredClone(high)] })]);
  assert.equal(processEvaluationOf(same).relative_reference.overall_score, null);
  assert.equal(processEvaluationOf(same).relative_reference.legacy_diagnostic.overall_score, 51.5);
  for (const defect of [row => { row.value = 20; }, row => { row.end_ms += 1; }]) {
    const copy = structuredClone(same); defect(copy.dimensions[0].criteria[1]);
    assert.equal(processEvaluationOf(copy).relative_reference.overall_score, null);
    assert.equal(processEvaluationOf(copy).relative_reference.legacy_diagnostic.overall_score, 50);
  }
  for (const mutate of [row => { row.value = NaN; }, row => { row.value = Infinity; }, row => { row.value = 101; }, row => { row.sample_count = 2; }, row => { row.measurement_provenance.pose_source = "invented"; }]) {
    const row = structuredClone(high); mutate(row);
    const reference = processEvaluationOf(report([dimension({ score: null, criteria: [row] })])).relative_reference;
    assert.equal(reference.overall_score, null);
    assert.equal(reference.legacy_diagnostic.overall_score, 50);
    assert.doesNotMatch(JSON.stringify(reference), /NaN|Infinity/);
  }
});

test("historical curve shrinkage remains in legacy diagnostics and never enters current scores", () => {
  const rows = Array.from({ length: 3 }, (_, i) => modelCriterion({ value: 80, episode_id: `action-${i}`, start_ms: i * 1000, end_ms: i * 1000 + 600 }));
  const parsed = processEvaluationOf(report([dimension({ score: null, criteria: rows })]));
  assert.equal(parsed.relative_reference.dimensions[2].score, null);
  assert.equal(parsed.relative_reference.overall_score, null);
  assert.equal(parsed.relative_reference.observed_dimension_count, 0);
  assert.equal(parsed.relative_reference.legacy_diagnostic.dimensions[2].score, 65);
  assert.equal(parsed.relative_reference.legacy_diagnostic.overall_score, 53);
  const raw = structuredClone(parsed.dimensions);
  raw[2].criteria[0].score = 0;
  assert.deepEqual(relativeReferenceOf(raw), parsed.relative_reference);
});

function boundTargetCriterion(angle = 0, episodeId = "swing-1") {
  const local = [1000, 1040, 1080, 1120], sourceTimes = local.map(value => value + 75000), frames = [0, 1, 2, 3];
  const identity = { video_sha256: "a".repeat(64), person_track_id: 7, selection_epoch: 0, camera_reference_epoch: 0 };
  const provenance = modelProvenance(); delete provenance.model_estimate_sample_count;
  const row = criterion({ id: "movement_target_alignment", value: 0, unit: "deg", score: 100,
    source: "indicator_features.FS02-M02.scoring_context.accepted_operator_reference",
    assessment_status: "mathematical_reference", measurement_provenance: provenance, source_track_id: 7,
    geometry_mask: { left_hip: [true, true, true, true], right_hip: [true, true, true, true] },
    source_window: { ...identity, start_ms: 1000, end_ms: 1120, source_frames: frames,
      video_timestamps_ms: local, source_timestamps_ms: sourceTimes,
      source_bindings: frames.map((frame, i) => ({ ...identity, frame_index: frame, processed_index: frame,
        pts: sourceTimes[i], time_base_num: 1, time_base_den: 1000, timestamp_ms: sourceTimes[i], video_timestamp_ms: local[i] })) } });
  row.value = angle; row.score = 100 * (1 - angle / 180); row.episode_id = episodeId;
  return row;
}

test("bound SAM target direction uses full geometry proof instead of rejecting absent phase-summary sample counts", () => {
  const input = report([dimension({ id: "movement", score: 100, criteria: [boundTargetCriterion()] })]);
  const parsed = processEvaluationOf(input);
  assert.equal(parsed.dimensions[0].score, 100);
  assert.equal(parsed.relative_reference.overall_score, 100);
  assert.equal(parsed.relative_reference.legacy_relative_reference.overall_score, 52.5);
  assert.deepEqual(processEvaluationOf(parsed).relative_reference, parsed.relative_reference);
  for (const mutate of [value => { value.source_window.source_bindings[1].pts += 1; },
    value => { value.geometry_mask.left_hip.fill(false); }, value => { value.source_track_id = 8; }]) {
    const broken = structuredClone(input); mutate(broken.dimensions[0].criteria[0]);
    assert.equal(processEvaluationOf(broken).relative_reference.overall_score, null);
  }
});


test("identical bound target angles score equally for one and ten actions while v2 stays historical", () => {
  for (const [angle, score] of [[0, 100], [36, 80], [90, 50], [180, 0]]) {
    const references = [1, 10].map(count => {
      const input = report([dimension({ id: "movement", score, criteria: Array.from({ length: count }, (_, i) => boundTargetCriterion(angle, `target-${i}`)) })]);
      const before = structuredClone(input), parsed = processEvaluationOf(input);
      assert.deepEqual(input, before);
      assert.equal(parsed.relative_reference.overall_score, score);
      assert.equal(parsed.relative_reference.observed_dimension_count, 1);
      assert.deepEqual(parsed.relative_reference.dimensions.map(row => row.score), [score, null, null, null, null]);
      assert.equal(parsed.relative_reference.dimensions[0].rules[0].sample_count, count);
      assert.equal(parsed.relative_reference.dimensions[0].rules[0].measurement_weight, 1);
      assert.equal(parsed.relative_reference.legacy_relative_reference.contributes_to_current_score, false);
      assert.equal(parsed.relative_reference.legacy_diagnostic.contributes_to_current_score, false);
      return parsed.relative_reference;
    });
    if (angle === 0) assert.deepEqual(references.map(row => row.legacy_relative_reference.overall_score), [52.5, 57.7]);
    if (angle === 180) assert.deepEqual(references.map(row => row.legacy_relative_reference.overall_score), [47.5, 42.3]);
  }
});

test("valid zero remains visible and included while missing target measurements never become 50", () => {
  const zero = boundTargetCriterion(180), missing = boundTargetCriterion(0, "missing-target");
  missing.status = "unavailable"; missing.value = null; missing.score = null;
  const input = report([dimension({ id: "movement", score: 0, criteria: [zero, missing] })]);
  const parsed = processEvaluationOf(input), html = render(input);
  assert.equal(parsed.relative_reference.overall_score, 0);
  assert.equal(parsed.relative_reference.dimensions[0].rules[0].sample_count, 1);
  assert.match(html, /class="process-overall-reference"><strong>0<small>\/ 100/);
  assert.equal((html.match(/class="process-radar-point/g) ?? []).length, 1);
  assert.equal((html.match(/class="process-radar-point[^"]*is-neutral/g) ?? []).length, 0);
  const onlyMissing = processEvaluationOf(report([dimension({ id: "movement", score: null, criteria: [missing] })]));
  assert.equal(onlyMissing.relative_reference.overall_score, null);
  assert.equal(onlyMissing.relative_reference.observed_dimension_count, 0);
  assert.equal(onlyMissing.relative_reference.legacy_relative_reference.overall_score, 50);
});

test("one user total selects validated formal evidence then valid process scores including zero", () => {
  const raw = JSON.parse(fs.readFileSync(new URL("./fixtures/temporal-formal-real-0a3-contract.json", import.meta.url), "utf8")).report;
  const process = report([dimension({ id: "movement", score: 0, criteria: [boundTargetCriterion(180)] })]);
  const result = { job_id: raw.source_run_id, card_execution: raw, process_evaluation: process };
  raw.overall_score = 999;
  assert.equal(trainingScoreOf(result).source, "validated_card_execution");
  assert.equal(trainingScoreOf(result).score, 68);
  assert.equal(trainingScoreOf(result).scoreLabel, "步伐动作得分");
  assert.equal(trainingScoreOf(result).onlyFootwork, true);
  assert.ok(trainingScoreOf(result).scoredInstanceCount > 0);
  const foreign = { ...result, job_id: "another-source-job" };
  assert.equal(trainingScoreOf(foreign).source, "observed_process_reference");
  assert.equal(trainingScoreOf(foreign).score, 0);
  assert.equal(trainingScoreOf({ ...foreign, process_evaluation: undefined }).score, null);
  const broken = structuredClone(result);
  broken.card_execution.cards.forEach(card => card.instances.forEach(instance => { instance.score = 999; }));
  assert.equal(trainingScoreOf(broken).source, "observed_process_reference");
  assert.equal(trainingScoreOf(broken).score, 0);
  const legacy = JSON.parse(fs.readFileSync(new URL("./fixtures/card-execution-real.json", import.meta.url), "utf8"));
  assert.equal(trainingScoreOf({ job_id: legacy.source_run_id, card_execution: legacy, process_evaluation: process }).score, 0);
  assert.equal(trainingScoreOf({ job_id: legacy.source_run_id, card_execution: legacy }).score, null);
});

test("overview keeps the five-dimensional chart and dimension controls outside collapsed details", () => {
  const html = renderToStaticMarkup(createElement(TrainingReportOverview, {
    result: { process_evaluation: report() }, motion: null, pending: false, title: "", description: "",
  }));
  const radar = html.indexOf('<svg class="process-radar"');
  const controls = html.indexOf('<div class="process-dimension-options"');
  const explanation = html.indexOf('<details class="process-explanation"');
  assert.ok(radar > 0 && controls > radar && explanation > controls);
  for (const position of [radar, controls]) {
    const ancestors = (html.slice(0, position).match(/<\/?details\b[^>]*>/g) ?? [])
      .reduce((depth, tag) => depth + (tag.startsWith("</") ? -1 : 1), 0);
    assert.equal(ancestors, 0, "the chart and controls must not require expanding a disclosure");
  }
  const visibleControls = html.slice(controls, explanation);
  assert.equal((visibleControls.match(/aria-pressed=/g) ?? []).length, 5);
  assert.equal((visibleControls.match(/aria-expanded="false"/g) ?? []).length, 5);
  assert.doesNotMatch(html.slice(explanation), /<details[^>]*\bopen/);
  assert.equal((html.match(/class="process-radar-point/g) ?? []).length, 0);
});
