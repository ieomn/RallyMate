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
const { buildPracticeReport, buildReportBackup } = await import(compiledUrl(new URL("../app/lib/report-export.ts", import.meta.url)));
const { default: contract } = await import(compiledUrl(new URL("../app/lib/gs-card-display-contract.ts", import.meta.url)));
const catalogue = JSON.parse(fs.readFileSync(new URL("../../src/rallymate_scoring/data/card_rules_v1.json", import.meta.url), "utf8"));
const evidence = JSON.parse(fs.readFileSync(new URL("./fixtures/gs-card-execution-real.json", import.meta.url), "utf8"));
const reports = evidence.reports;

test("service limitations render clear Chinese explanations and survive backup normalization", () => {
  const raw = structuredClone(reports[0]);
  raw.limitations = ["Five temporal FS calculators and explicitly annotated GS stages are connected; no automatic ten-stage GS locator is registered.",
    "manual_stage_annotations_are_estimates_not_expert_ground_truth", "missing_stage_never_inferred_from_three_generic_swing_phases"];
  const parsed = cardExecutionOf(raw);
  assert.ok(parsed.limitations.every(value => /[\u4e00-\u9fff]/.test(value)));
  assert.ok(parsed.limitations.some(value => value.includes("尚非专家")));
  assert.deepEqual(cardExecutionOf(parsed), parsed);
});

test("all 248 GS display contracts retain source kind, thresholds, joint union and source anchors", () => {
  const expected = Object.fromEntries(catalogue.cards.filter(card => card.event_code.startsWith("GS")).map(card => [card.indicator_id, {
    stage_code: card.stage_code, rule_kind: card.evaluation.kind,
    required_joint_names: [...new Set([...card.required_joint_names, ...card.evaluation.conditions.flatMap(condition => [...condition.required_joint_names, ...catalogue.metrics[condition.metric].required_joint_names])])].sort(),
    conditions: card.evaluation.conditions.map(condition => [condition.metric, condition.operator, condition.bounds ?? condition.value, condition.unit]),
    source: Object.fromEntries(["document_id", "document_sha256", "table", "rubric_sha256"].map(key => [key, card.source[key]])),
  }]));
  assert.deepEqual(contract.cards, expected);
  assert.equal(Object.keys(contract.cards).length, 248);
  assert.equal(contract.rule_version, catalogue.version);
});

test("three verified real videos retain 27 proxies, 16 reviews and 17 unavailable instances", () => {
  const totals = { proxy: 0, review: 0, unavailable: 0, cards: 0 };
  for (const raw of reports) {
    const report = cardExecutionOf(raw);
    totals.proxy += report.summary.proxy_scored_instances;
    totals.review += report.summary.review_instances;
    totals.unavailable += report.summary.unavailable_instances;
    totals.cards += report.summary.computed_cards;
    assert.equal(report.summary.original_scored_cards, 0);
    assert.equal(report.overall_score, null);
    assert.deepEqual(cardExecutionOf(report), report);
    for (const instance of report.cards.flatMap(card => card.instances)) {
      assert.equal(instance.source_stage.binding.method, "manual_anchor");
      assert.equal(instance.source_stage.binding.uncertainty_ms, 150);
      assert.equal(instance.original_grade, null);
    }
  }
  assert.deepEqual(totals, { proxy: 27, review: 16, unavailable: 17, cards: 15 });
});

test("missing or mismatched GS source stage evidence, hashes, joints and thresholds lose the grade", () => {
  const mutations = [
    row => delete row.source_annotation,
    row => row.source_annotation.binding.method = "versioned_detector",
    row => row.source_annotation.binding.semantics = "expert_ground_truth",
    row => row.source_annotation.binding.uncertainty_ms = -1,
    row => row.source_annotation.document_sha256 = "invalid",
    row => row.source_annotation.annotation_id = "another-event",
    row => row.window.stage_code = "GS01-M05",
    row => row.source.rubric_sha256 = "0".repeat(64),
    row => row.source.document_sha256 = "f".repeat(64),
    row => row.quality.required_joint_names.pop(),
    row => row.quality.valid_frame_count = 1,
    row => row.conditions[0].threshold = 0,
    row => row.measurement_version = "temporal-card-evidence-v1.0.0",
    row => row.original_grade = "A",
  ];
  for (const mutate of mutations) {
    const raw = structuredClone(reports[0]), card = raw.cards.find(card => card.instances.some(row => row.status === "proxy_scored"));
    const row = card.instances.find(row => row.status === "proxy_scored");
    mutate(row);
    const result = cardExecutionOf(raw).cards.find(candidate => candidate.indicator_id === card.indicator_id).instances.find(candidate => candidate.event_id === row.event_id);
    assert.equal(result.score, null);
    assert.equal(result.status, "unavailable");
  }
});

test("unknown runtime and relabelled manual cards never create an automatic result", () => {
  const raw = structuredClone(reports[0]);
  const card = raw.cards.find(card => card.instances.some(row => row.status === "proxy_scored"));
  card.runtime_engine = "temporal-card-evidence-v1.0.0";
  const manual = raw.cards.find(card => card.rule_kind === "manual_review");
  manual.rule_kind = "visual_proxy";
  const report = cardExecutionOf(raw);
  assert.equal(report.cards.find(row => row.indicator_id === card.indicator_id).instances[0].score, null);
  assert.ok(!report.cards.some(row => row.indicator_id === manual.indicator_id));
  assert.equal(report.summary.rejected_cards, 1);
});

test("GS display and portable exports preserve manual stage uncertainty without a source grade", () => {
  const raw = reports[0], input = { mode: "live", uploadState: "complete", job: { id: raw.source_run_id, status: "succeeded" },
    evidence: { jobId: raw.source_run_id, result: { job_id: raw.source_run_id, card_execution: raw } } };
  const html = renderToStaticMarkup(createElement(CardExecutionPanel, { value: raw, jobId: raw.source_run_id, durationMs: 17000 }));
  assert.match(html, /人工估计时间窗/);
  assert.match(html, /±150 毫秒/);
  assert.match(html, /不是专家真值/);
  assert.match(html, /肩线投影角度范围/);
  assert.match(html, /回看计算片段/);
  const backup = JSON.parse(JSON.stringify(buildReportBackup(input)));
  assert.deepEqual(cardExecutionOf(backup.result.card_execution), cardExecutionOf(raw));
  const section = buildPracticeReport(input).sections.find(row => row.title === "逐卡视频实例与条件");
  assert.ok(section.columns.includes("阶段来源"));
  assert.ok(section.rows.every(row => row.at(-1).includes("人工估计 ±150 毫秒")));
});
