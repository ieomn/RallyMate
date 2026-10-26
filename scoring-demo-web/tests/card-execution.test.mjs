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
const { cardExecutionOf, TEMPORAL_CARD_CONDITIONS } = await import(compiledUrl(new URL("../app/lib/card-execution.ts", import.meta.url)));
const { default: CardExecutionPanel } = await import(compiledUrl(new URL("../app/CardExecutionPanel.tsx", import.meta.url)));
const real = JSON.parse(fs.readFileSync(new URL("./fixtures/card-execution-real.json", import.meta.url), "utf8"));
const catalogue = JSON.parse(fs.readFileSync(new URL("../../src/rallymate_scoring/data/card_rules_v1.json", import.meta.url), "utf8"));

test("temporal axis patch preserves explicit old and new versions without mixing their engines", () => {
  const updated = structuredClone(real);
  for (const card of updated.cards) {
    if (card.runtime_engine === "temporal-card-evidence-v1.0.0") card.runtime_engine = "temporal-card-evidence-v1.0.1";
    for (const instance of card.instances) if (instance.measurement_version === "temporal-card-evidence-v1.0.0") instance.measurement_version = "temporal-card-evidence-v1.0.1";
  }
  assert.deepEqual(cardExecutionOf(updated).cards.map(c => c.instances.map(i => i.score)), cardExecutionOf(real).cards.map(c => c.instances.map(i => i.score)));
  const scoredCard = updated.cards.find(c => c.instances.some(i => i.status === "proxy_scored"));
  scoredCard.runtime_engine = "temporal-card-evidence-v1.0.0";
  const parsed = cardExecutionOf(updated).cards.find(c => c.indicator_id === scoredCard.indicator_id);
  assert.ok(parsed.instances.every(i => i.score === null));
});

test("three real instances preserve 40/100 proxies and a null fallback without importing full-run counts", () => {
  const report = cardExecutionOf(real);
  assert.equal(report.summary.catalogue_cards, 3);
  assert.equal(report.summary.expected_source_cards, 298);
  assert.equal(report.summary.computed_cards, 3);
  assert.equal(report.summary.proxy_scored_cards, 2);
  assert.equal(report.summary.proxy_scored_instances, 2);
  assert.equal(report.summary.review_instances, 1);
  assert.equal(report.overall_score, null);
  assert.deepEqual(report.cards.map(card => card.instances[0].score), [null, 40, 100]);
  assert.equal(report.cards[0].instances[0].fallback.selected.start_ms, 1467);
  assert.equal(report.cards[1].instances[0].conditions[0].value, -0.5730220154257564);
  assert.equal(report.cards[0].instances[0].conditions[0].passed, null);
  assert.deepEqual(cardExecutionOf(report), report);
});

test("replay controls require the displayed job to match the card report source", () => {
  const matching = renderToStaticMarkup(createElement(CardExecutionPanel, { value: real, jobId: real.source_run_id, durationMs: 97033 }));
  assert.match(matching, /回看计算片段/);
  for (const jobId of [undefined, "another-video-job"]) {
    const mismatched = renderToStaticMarkup(createElement(CardExecutionPanel, { value: real, jobId, durationMs: 97033 }));
    assert.doesNotMatch(mismatched, /回看计算片段/);
  }
});

test("the parser recognizes every source card identity but never counts catalogue presence as execution", () => {
  const raw = structuredClone(real);
  raw.cards = catalogue.cards.map(card => ({ indicator_id: card.indicator_id, event_code: card.event_code, name_zh: card.effective.name_zh,
    rule_version: catalogue.version, rule_kind: card.evaluation.kind, runtime_engine: null, run_status: "not_executed", instances: [] }));
  const report = cardExecutionOf(raw);
  assert.equal(report.summary.catalogue_cards, 298);
  assert.equal(report.summary.computed_cards, 0);
  assert.equal(report.summary.proxy_scored_cards, 0);
  assert.equal(report.summary.original_scored_cards, 0);
});

test("wired frontend threshold contracts exactly match the current backend catalogue", () => {
  for (const [id, expected] of Object.entries(TEMPORAL_CARD_CONDITIONS)) {
    const rule = catalogue.cards.find(card => card.indicator_id === id);
    const actual = rule.evaluation.conditions.map(condition => [condition.metric, condition.operator, condition.bounds ?? condition.value, condition.unit]);
    assert.deepEqual(expected, actual, id);
  }
});

test("forged grades, pass bits, thresholds, units and unsafe counts lose the score", () => {
  const mutations = [
    row => row.score = 100, row => row.original_grade = "A", row => row.source_grade_supported = true,
    row => row.conditions[0].passed = true, row => row.conditions[0].threshold = -999,
    row => row.conditions[0].unit = "pixels", row => row.conditions[0].value = Infinity,
    row => row.conditions.push(structuredClone(row.conditions[0])), row => row.quality.valid_frame_count = 999999,
    row => row.quality.total_frame_count = -1, row => row.measurement_version = "unknown",
    row => row.evidence.requested_window_ms.end_ms = -1,
  ];
  for (const mutate of mutations) {
    const raw = structuredClone(real); mutate(raw.cards[1].instances[0]);
    const instance = cardExecutionOf(raw).cards[1].instances[0];
    assert.equal(instance.score, null);
    assert.equal(instance.status, "unavailable");
    assert.equal(instance.original_grade, null);
  }
});

test("identical duplicate instances collapse and conflicting duplicates cannot boost coverage", () => {
  const raw = structuredClone(real), card = raw.cards[1];
  card.instances.push(structuredClone(card.instances[0]));
  assert.equal(cardExecutionOf(raw).summary.proxy_scored_instances, 2);
  card.instances[1].conditions[0].value = 100;
  const report = cardExecutionOf(raw);
  assert.equal(report.summary.proxy_scored_instances, 1);
  assert.equal(report.summary.conflicting_instances, 1);
  assert.equal(report.cards[1].instances[0].score, null);
  assert.deepEqual(cardExecutionOf(report), report);
});

test("unknown identities and cross-video instances are not accepted as measured cards", () => {
  const raw = structuredClone(real);
  raw.cards[1].instances[0].video_id = "another-video";
  raw.cards.push({ ...structuredClone(raw.cards[2]), indicator_id: "GS99-M99-99", event_code: "GS99" });
  raw.coverage = { source_card_count: 298, original_auto_graded_card_count: 298 };
  raw.summary = { proxy_scored_cards: 298 };
  const report = cardExecutionOf(raw);
  assert.equal(report.summary.catalogue_cards, 3);
  assert.equal(report.summary.proxy_scored_cards, 1);
  assert.equal(report.summary.original_scored_cards, 0);
  assert.equal(report.summary.rejected_cards, 1);
});

test("real fallback renders no zero grade and clearly separates proxy completion from original grade", () => {
  const html = renderToStaticMarkup(createElement(CardExecutionPanel, { value: real, durationMs: 97033 }));
  assert.match(html, /— 未评分/);
  assert.match(html, /工程条件完成度/);
  assert.match(html, /缺测不记零分/);
  assert.match(html, /原卡技术等级尚未自动判定/);
  assert.match(html, /1\.467 秒/);
  assert.match(html, /3.*298/);
  assert.doesNotMatch(html, /0 \/ 100/);
});

test("conflicting card records lose all scores and retain semantics across backup normalization", () => {
  const raw = structuredClone(real);
  const conflict = structuredClone(raw.cards[1]);
  conflict.instances[0].conditions[0].value += .1;
  raw.cards.push(conflict);
  const report = cardExecutionOf(raw);
  assert.equal(report.summary.catalogue_cards, 3);
  assert.equal(report.summary.conflicting_instances, 1);
  assert.equal(report.summary.proxy_scored_instances, 1);
  assert.equal(report.cards[1].run_status, "unavailable");
  assert.deepEqual(cardExecutionOf(report), report);
});

test("empty unavailable reports remain visible with reasons and round-trip without fabricating zero scores", () => {
  const raw = { version: real.version, rule_version: real.rule_version, source_run_id: real.source_run_id,
    status: "unavailable", cards: [], reason_codes: ["card_execution_report_unavailable"], limitations: ["缺少原始逐帧观测，不能计算本次评分卡。"] };
  const report = cardExecutionOf(raw);
  assert.deepEqual(cardExecutionOf(report), report);
  const html = renderToStaticMarkup(createElement(CardExecutionPanel, { value: raw }));
  assert.match(html, /本次评分卡计算暂不可用/);
  assert.match(html, /缺少原始逐帧观测/);
  assert.equal(report.summary.proxy_scored_instances, 0);
  assert.equal(report.overall_score, null);
  const missingIdentity = structuredClone(real); missingIdentity.source_run_id = "";
  assert.equal(cardExecutionOf(missingIdentity).summary.proxy_scored_instances, 0);
});
