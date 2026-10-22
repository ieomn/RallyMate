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
const { default: contract } = await import(compiledUrl(new URL("../app/lib/footwork-cycle-display-contract.ts", import.meta.url)));
const { buildPracticeReport, buildReportBackup } = await import(compiledUrl(new URL("../app/lib/report-export.ts", import.meta.url)));
const real = JSON.parse(fs.readFileSync(new URL("./fixtures/footwork-cycle-real.json", import.meta.url), "utf8"));
const artifactReal = JSON.parse(fs.readFileSync(new URL("./fixtures/footwork-cycle-artifact-real.json", import.meta.url), "utf8"));
const synthetic = JSON.parse(fs.readFileSync(new URL("./fixtures/footwork-cycle-synthetic-unit.json", import.meta.url), "utf8"));
const catalogue = JSON.parse(fs.readFileSync(new URL("../../src/rallymate_scoring/data/card_rules_v1.json", import.meta.url), "utf8"));
const instances = report => report.cards.flatMap(card => card.instances);
function oneSynthetic(id = "FS04-M04", label = "FS04") {
  const report = structuredClone(synthetic.reports.find(row => row.label === label).report);
  report.cards = report.cards.filter(card => card.indicator_id === id);
  return report;
}

test("ten generated cycle contracts preserve catalogue source anchors, exact conditions and common ankle joints", () => {
  const common = ["left_shoulder", "right_shoulder", "left_hip", "right_hip", "left_ankle", "right_ankle"];
  const expected = Object.fromEntries(catalogue.cards.filter(card => ["FS03", "FS04"].includes(card.event_code)).map(card => [card.indicator_id, {
    rule_kind: card.evaluation.kind, limitation: card.evaluation.limitation, required_joint_names: [...new Set([...common, ...card.required_joint_names])].sort(),
    conditions: card.evaluation.conditions.map(condition => [condition.metric, condition.operator, condition.bounds ?? condition.value, condition.unit]),
    source: Object.fromEntries(["document_id", "document_sha256", "table", "rubric_sha256"].map(key => [key, card.source[key]])),
  }]));
  assert.deepEqual(contract.cards, expected);
  assert.equal(Object.keys(contract.cards).length, 10);
  assert.equal(contract.cards["FS04-M04"].conditions[0][1], "eq");
});

test("real mixed USTA observations preserve ten reviews and never become pure step positives", () => {
  assert.match(real.provenance.warning, /not_pure_FS03_or_FS04/);
  const report = cardExecutionOf(real.report);
  assert.equal(report.summary.proxy_scored_instances, 0);
  assert.equal(report.summary.review_instances, 10);
  assert.equal(report.summary.original_scored_cards, 0);
  for (const row of instances(report)) {
    const raw = instances(real.report).find(item => item.indicator_id === row.indicator_id && item.event_id === row.event_id);
    assert.equal(row.score, null); assert.equal(row.proxy_grade, null); assert.equal(row.original_grade, null);
    assert.deepEqual(row.foot_cycle.source_frames, raw.evidence.source_frames);
    assert.deepEqual(row.foot_cycle.source_frame_bindings, raw.evidence.source_frame_bindings);
    assert.deepEqual(row.foot_cycle.series, raw.evidence.series);
    assert.ok(row.conditions.every(condition => condition.passed === null));
  }
  assert.deepEqual(cardExecutionOf(report), report);
});

test("actual video artifact excerpt preserves five reviews with verified manual annotation provenance", () => {
  assert.equal(artifactReal.provenance.kind, "actual_original_video_artifact_service_output");
  const raw = artifactReal.report;
  assert.equal(raw.input.artifact_files_verified, true);
  assert.equal(raw.input.decoder_verified_from_original_video, true);
  const report = cardExecutionOf(raw);
  assert.equal(report.summary.computed_cards, 5);
  assert.equal(report.summary.review_instances, 5);
  assert.equal(report.summary.proxy_scored_instances, 0);
  for (const row of instances(report)) {
    assert.equal(row.source_cycle_annotation.binding.uncertainty_ms, 100);
    assert.equal(row.source_cycle_annotation.binding.source, row.foot_cycle.step_window_binding.evidence_reference);
    assert.equal(row.source_cycle_annotation.start_source_frame, row.foot_cycle.source_frames[0]);
    assert.equal(row.score, null);
  }
  assert.deepEqual(cardExecutionOf(report), report);
  const html = renderToStaticMarkup(createElement(CardExecutionPanel, { value: raw }));
  assert.match(html, /±100 毫秒/); assert.match(html, /不是专家步型真值/);
});

test("controlled trajectories exercise math only and retain four calculated proxies per fixture", () => {
  assert.equal(synthetic.semantics, "synthetic_trajectory_unit_tests_only_not_real_video_validation");
  for (const { label, report: raw } of synthetic.reports) {
    assert.equal(cardExecutionOf({ ...raw, version: contract.runtime_engine }), null, "standalone service report is not a production unified envelope");
    const report = cardExecutionOf(raw);
    assert.equal(report.summary.proxy_scored_instances, 4, label);
    assert.equal(report.summary.review_instances, 1, label);
    for (const row of instances(report)) assert.equal(row.score, instances(raw).find(item => item.indicator_id === row.indicator_id).score, label);
    assert.deepEqual(cardExecutionOf(report), report);
  }
});

test("the formal manual annotation remains distinct from GS stages and invalid bindings cancel a proxy", () => {
  const raw = oneSynthetic(), row = raw.cards[0].instances[0], binding = row.evidence.step_window_binding;
  row.source_annotation = { document_sha256: "1".repeat(64), document_version: "footwork-cycle-annotations-v1.0.0",
    event_id: row.event_id, event_code: "FS04", start_source_frame: binding.start_source_frame, end_source_frame: binding.end_source_frame,
    requested_window_ms: { start_ms: 0, end_ms: 840 }, binding: { method: "manual_anchor", version: binding.version, source: binding.evidence_reference,
      uncertainty_ms: 80, semantics: "manual_visual_candidate_window_not_expert_technique_or_contact_truth" } };
  const parsed = cardExecutionOf(raw);
  assert.equal(instances(parsed)[0].source_cycle_annotation.binding.uncertainty_ms, 80);
  assert.equal(instances(parsed)[0].source_stage, null);
  assert.deepEqual(cardExecutionOf(parsed), parsed);
  const html = renderToStaticMarkup(createElement(CardExecutionPanel, { value: raw }));
  assert.match(html, /±80 毫秒/);
  for (const change of [item => item.source_annotation.event_code = "FS03", item => item.source_annotation.end_source_frame++,
    item => item.source_annotation.binding.source = "other", item => item.source_annotation.binding.uncertainty_ms = -1,
    item => item.source_annotation.document_sha256 = "unknown", item => item.source_annotation.binding.semantics = "expert_truth"]) {
    const changed = structuredClone(raw); change(changed.cards[0].instances[0]);
    assert.equal(instances(cardExecutionOf(changed))[0].score, null);
  }
});

test("coherently altered saved condition arithmetic still disagrees with retained source series", () => {
  const raw = oneSynthetic(), row = raw.cards[0].instances[0];
  row.conditions[0].value = 2; row.conditions[0].passed = false; row.measurements.ankle_order_exchange_count = 2;
  row.proxy_grade = "C"; row.score = 60;
  assert.equal(instances(cardExecutionOf(raw))[0].score, null);
});

test("tampered clocks, identity, camera, full frame denominator and foot sequence cannot retain a grade", () => {
  const mutations = [
    row => delete row.evidence, row => row.evidence.step_window_binding.method = "versioned_detector",
    row => row.evidence.step_window_binding.video_sha256 = "b".repeat(64), row => row.evidence.step_window_binding.event_id = "other",
    row => row.evidence.step_window_binding.primary_player_id = 2,
    row => row.evidence.source_frame_bindings[5].pts += 1,
    row => row.evidence.source_frame_bindings[5].time_base_den = 0,
    row => row.evidence.source_frame_bindings[5].selection_epoch += 1,
    row => row.evidence.source_frame_bindings[5].camera_reference_epoch += 1,
    row => row.evidence.source_frame_bindings[5].person_track_id += 1,
    row => row.evidence.source_frame_bindings.shift(), row => row.evidence.source_frames.pop(),
    row => row.evidence.interpolation_used = true, row => row.evidence.observations_are_ground_contact = true,
    row => row.evidence.series.complete_visual_motion_cycle_count = 99,
    row => row.evidence.series.sequential_follow_pairs = [],
    row => row.evidence.series.motion_step_candidates.right[0].preceding_low_motion_duration_ms = 999,
    row => row.evidence.series.ankle_speed_torso_s.right[6] = null,
    row => row.evidence.event_quality.review_flags.push("left_right_swap_candidates_present"),
    row => row.conditions[0].operator = "lte", row => row.conditions[0].value = 1,
    row => row.source.rubric_sha256 = "0".repeat(64), row => row.quality.required_joint_names.pop(),
  ];
  for (const mutate of mutations) {
    const raw = oneSynthetic(); mutate(raw.cards[0].instances[0]);
    const row = instances(cardExecutionOf(raw))[0];
    assert.equal(row.status, "unavailable", String(mutate));
    assert.equal(row.score, null); assert.equal(row.proxy_grade, null);
  }
});

test("crossing evidence needs actual intermediate near-zero frames and linked motion windows", () => {
  for (const mutate of [row => row.evidence.series.ankle_order_exchanges[0].near_zero_source_frames = [],
    row => row.evidence.series.ankle_order_exchanges[0].complete_motion_source_windows = [],
    row => row.evidence.series.ankle_order_exchanges[0].after_source_frame = 999]) {
    const raw = oneSynthetic("FS03-M03", "FS03"); mutate(raw.cards[0].instances[0]);
    assert.equal(instances(cardExecutionOf(raw))[0].score, null);
  }
});

test("cleared unresolved flags cannot hide unstable order boundaries, brief reversals or unexplained deadband", () => {
  const changes = [
    ["first frame is unresolved", signs => signs[0] = 0],
    ["last frame is unresolved", signs => signs[signs.length - 1] = 0],
    ["first stable sign lasts only 40 ms", signs => signs[2] = 0],
    ["last stable sign lasts only 40 ms", signs => signs[signs.length - 3] = 0],
    ["brief opposite sign cannot be discarded", signs => signs[6] = -signs[6]],
    ["deadband returns to the same stable order", signs => signs[6] = 0],
  ];
  for (const [id, label] of [["FS03-M03", "FS03"], ["FS04-M03", "FS04"], ["FS04-M04", "FS04"]]) {
    for (const [name, change] of changes) {
      const raw = oneSynthetic(id, label), row = raw.cards[0].instances[0], series = row.evidence.series;
      assert.equal(instances(cardExecutionOf(raw))[0].status, "proxy_scored", id);
      change(series.ankle_order_sign);
      // Keep saved signs and gaps consistent, remove every warning, and retain
      // valid-looking grade arithmetic. The timeline itself must still fail.
      series.ankle_signed_horizontal_gap_torso = series.ankle_order_sign.map(sign => sign * .2);
      series.unresolved_order_exchanges = [];
      row.status = "proxy_scored";
      const parsed = instances(cardExecutionOf(raw))[0];
      assert.equal(parsed.status, "unavailable", `${id}: ${name}`);
      assert.equal(parsed.score, null); assert.equal(parsed.proxy_grade, null);
      assert.ok(parsed.conditions.every(condition => condition.passed === null));
    }
  }
});

test("shuffle follow pairs require same-direction foot travel and the leading position at onset", () => {
  const changes = [
    ["lead foot travels against the hip direction", series => series.motion_step_candidates.right[0].delta_xy_torso[0] *= -1],
    ["following foot travels against the hip direction", series => series.motion_step_candidates.left[0].delta_xy_torso[0] *= -1],
    ["both feet travel against the hip direction", series => { for (const side of ["left", "right"]) series.motion_step_candidates[side][0].delta_xy_torso[0] *= -1; }],
    ["the first moving foot is behind at onset", series => {
      const i = series.sequential_follow_pairs[0].lead_start_source_frame;
      [series.left_ankle_xy_reference[i], series.right_ankle_xy_reference[i]] = [series.right_ankle_xy_reference[i], series.left_ankle_xy_reference[i]];
    }],
  ];
  for (const [name, change] of changes) {
    const raw = oneSynthetic(), row = raw.cards[0].instances[0];
    change(row.evidence.series); row.evidence.series.unresolved_order_exchanges = []; row.status = "proxy_scored";
    const parsed = instances(cardExecutionOf(raw))[0];
    assert.equal(parsed.status, "unavailable", name); assert.equal(parsed.score, null); assert.equal(parsed.proxy_grade, null);
  }
});

test("review and unavailable states keep measurements and null grades even when saved scores are supplied", () => {
  for (const status of ["review_required", "unavailable"]) {
    const raw = structuredClone(real.report);
    for (const row of instances(raw)) { row.status = status; row.score = 100; row.proxy_grade = "A"; row.conditions.forEach(condition => condition.passed = true); }
    const report = cardExecutionOf(raw);
    assert.equal(report.summary.proxy_scored_instances, 0);
    assert.ok(instances(report).every(row => row.score === null && row.proxy_grade === null && row.conditions.every(c => c.passed === null)));
  }
});

test("manual foot windows, raw clock evidence and foot order survive UI and portable backup", () => {
  const raw = oneSynthetic("FS04-M04", "FS04-clock-offset");
  const html = renderToStaticMarkup(createElement(CardExecutionPanel, { value: raw, jobId: raw.source_run_id, durationMs: 1000 }));
  assert.match(html, /人工候选窗口/); assert.match(html, /不是专家步型真值/); assert.match(html, /先后脚观测/);
  assert.match(html, /0\.160 秒/); assert.doesNotMatch(html, /9\.160 秒/);
  assert.match(html, /等于 0 次/); assert.match(html, /逐帧踝部观测与原始时钟/);
  assert.match(html, /必须完整先后脚周期/);
  assert.match(html, /双踝间距（躯干单位）/); assert.match(html, /按原图长边归一化/);
  assert.match(html, /0 表示落在噪声带、顺序不可分辨，不是换序次数为零/);
  const input = { mode: "live", uploadState: "complete", job: { id: raw.source_run_id, status: "succeeded" }, evidence: { jobId: raw.source_run_id, result: { job_id: raw.source_run_id, card_execution: raw } } };
  const backup = JSON.parse(JSON.stringify(buildReportBackup(input)));
  assert.deepEqual(cardExecutionOf(backup.result.card_execution), cardExecutionOf(raw));
  const section = buildPracticeReport(input).sections.find(row => row.title === "逐卡视频实例与条件");
  assert.ok(section.rows.every(row => row.at(-1).includes("人工步周期候选窗口")));
  assert.ok(section.rows.every(row => !row.at(-1).includes("由动作检测器")));
});
