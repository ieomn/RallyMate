import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import { gunzipSync } from "node:zlib";
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
const { cardExecutionOf, handRacketAuxiliarySummary } = await import(compiledUrl(new URL("../app/lib/card-execution.ts", import.meta.url)));
const { default: Evidence } = await import(compiledUrl(new URL("../app/ManualCardEvidence.tsx", import.meta.url)));
const { default: CardExecutionPanel } = await import(compiledUrl(new URL("../app/CardExecutionPanel.tsx", import.meta.url)));
const { buildReportBackup, buildPracticeReport } = await import(compiledUrl(new URL("../app/lib/report-export.ts", import.meta.url)));
const historicalBytes = fs.readFileSync(new URL("../../tests/fixtures/gs-follow-through-results-20261024.json", import.meta.url));
const historical = JSON.parse(historicalBytes), catalogue = JSON.parse(fs.readFileSync(new URL("../../src/rallymate_scoring/data/card_rules_v1.json", import.meta.url), "utf8"));
const ids = ["GS01-M09-04", "GS01-M09-05"], revision = "gs01-m09-hand-racket-observability-v1.0.0";
const card = (r, id) => r.cards.find(c => c.indicator_id === id);
function original() {
  return { version: "card-execution-v1.0.0", rule_version: catalogue.version, source_run_id: historical.source_run_id,
    scoring_policy_version: "automatic-formal-card-scoring-v1.0.0", cards: catalogue.cards.filter(c => c.stage_code === "GS01-M09").map(c => ({
      indicator_id: c.indicator_id, event_code: c.event_code, name_zh: c.effective.name_zh, rule_version: c.rule_version,
      rule_kind: ids.includes(c.indicator_id) ? "visual_proxy" : c.evaluation.kind,
      runtime_engine: "gs-card-execution-v1.0.0", run_status: "scored", reason_codes: [],
      instances: structuredClone(historical.instances.filter(i => i.indicator_id === c.indicator_id)),
    })) };
}
// A transformation of an existing real row exercises the reader contract. It
// is not a new media replay; the separately bound backend replay covers that.
function currentExample() {
  const r = original();
  for (const id of ids) {
    const c = card(r, id), spec = catalogue.cards.find(c => c.indicator_id === id).evaluation;
    c.rule_kind = "manual_review"; c.run_status = "unavailable";
    for (const i of c.instances) {
      const measure = structuredClone(i.conditions[0]?.evidence ?? {});
      Object.assign(i, { scoring_revision_id: revision, missing_required_observables: spec.required_observables,
        status: "unavailable", grade: null, proxy_grade: null, score: null, conditions: [], reason_codes: [spec.score_block_reason_code],
        auxiliary_observations: { version: "gs-hand-racket-auxiliary-v1.0.0", semantics: "bilateral_wrist_geometry_only_not_hand_racket_approach_or_control",
          measurement_version: "card-kinematics-v1.0.0", scoring_eligible: false,
          status: Number.isFinite(measure.statistics?.delta) ? "measured_auxiliary" : "unavailable", measurements: { wrist_separation_torso: measure }, reason_codes: [] },
      });
    }
  }
  return r;
}

test("retired duplicate wrist predicates lose two grades without dropping source cards or modifying historical bytes", () => {
  const raw = original(), snapshot = JSON.stringify(raw), p = cardExecutionOf(raw);
  assert.equal(p.cards.length, 5); assert.equal(p.summary.rejected_cards, 0);
  assert.equal(p.summary.formal_scored_instances, 3); assert.equal(p.summary.unavailable_instances, 17);
  assert.equal(p.summary.formal_scored_cards, 1); assert.equal(p.overall_score, 100);
  for (const id of ids) for (const i of card(p, id).instances) {
    assert.equal(i.status, "unavailable"); assert.equal(i.score, null); assert.equal(i.grade, null); assert.equal(i.proxy_grade, null);
    assert.equal(i.scoring_revision_id, revision); assert.equal(i.conditions.length, 0);
    assert.ok(i.superseded_conditions.every(c => c.passed === null));
  }
  assert.equal(card(p, ids[0]).instances[0].superseded_conditions[0].value, -.0846946610547554);
  assert.deepEqual(card(p, ids[0]).instances[0].superseded_condition_evidence[0].evidence, card(raw, ids[0]).instances[0].conditions[0].evidence);
  assert.match(handRacketAuxiliarySummary(card(p, ids[0]).instances[0]), /旧规则双腕距离变化记录 -0.084695/);
  assert.equal(JSON.stringify(raw), snapshot); assert.equal(JSON.stringify(JSON.parse(historicalBytes)), JSON.stringify(historical));
  assert.deepEqual(cardExecutionOf(p), p);
  const input = { mode: "live", uploadState: "complete", job: { id: raw.source_run_id, status: "completed", original_filename: "mit-forehand.mp4" },
    evidence: { jobId: raw.source_run_id, error: null, result: { job_id: raw.source_run_id, card_execution: raw } }, summary: null };
  const backup = buildReportBackup(input, "2026-10-24T09:00:00Z");
  assert.deepEqual(cardExecutionOf(backup.result.card_execution), p);
});

test("all 298 old catalogue rows and native 114 formal instances remain readable", () => {
  const raw = JSON.parse(gunzipSync(fs.readFileSync(new URL("./fixtures/micro-cycle-native-e420-unified.json.gz", import.meta.url))));
  const p = cardExecutionOf(raw);
  assert.equal(p.cards.length, 298); assert.equal(p.summary.rejected_cards, 0); assert.equal(p.summary.formal_scored_instances, 114);
  assert.ok(Math.abs(p.overall_score - 57.11467678858983) < 1e-12);
  assert.deepEqual(cardExecutionOf(p), p);
});

test("revision, auxiliary values, saved pass bits, source-grade claims or changed row kind never re-enable either score", () => {
  const mutations = [
    i => Object.assign(i, { score: 100, grade: "A", proxy_grade: "A", status: "scored", original_grade: "A", source_grade_supported: true }),
    i => { delete i.scoring_revision_id; },
    i => { i.scoring_revision_id = "invented-future-version"; },
    i => { i.auxiliary_observations.scoring_eligible = true; i.auxiliary_observations.score = 100; },
    i => { i.missing_required_observables = []; i.hand_racket_control_verified = true; },
    i => { i.conditions = [{ metric: "wrist_separation_torso.delta", value: -999, passed: true, threshold: -.15, operator: "lte", unit: "torso" }]; },
  ];
  for (const mutate of mutations) for (const id of ids) {
    const r = currentExample(), i = card(r, id).instances[0]; mutate(i);
    for (const kind of ["manual_review", "visual_proxy"]) {
      card(r, id).rule_kind = kind; const actual = card(cardExecutionOf(r), id).instances[0];
      assert.equal(actual.score, null, String(mutate)); assert.equal(actual.grade, null); assert.equal(actual.original_grade, null);
      assert.equal(actual.status, "unavailable"); assert.equal(actual.conditions.length, 0);
    }
  }
});

test("the kind exception is restricted to the two revised cards", () => {
  const r = original(); card(r, "GS01-M09-01").rule_kind = "manual_review";
  const p = cardExecutionOf(r); assert.equal(p.summary.rejected_cards, 1); assert.equal(card(p, "GS01-M09-01"), undefined);
});

test("one missing instance does not describe the already-scored instances as missing evidence", () => {
  const r = original(); card(r, "GS01-M09-01").reason_codes = ["required_condition_evidence_missing"];
  const html = renderToStaticMarkup(createElement(CardExecutionPanel, { value: r }));
  assert.match(html, /部分实例缺少完整计算依据，已评分实例保持成绩/);
  assert.match(html, /100/); assert.doesNotMatch(html, /缺少本卡所需的完整计算依据/);
  r.cards = r.cards.filter(c => c.indicator_id === ids[1]);
  card(r, ids[1]).reason_codes = ["hand_racket_control_sequence_required"];
  const missing = renderToStaticMarkup(createElement(CardExecutionPanel, { value: r }));
  assert.match(missing, /缺少本卡所需的完整计算依据/); assert.doesNotMatch(missing, /已评分实例保持成绩/);
});

test("auxiliary source records and revision survive complete JSON backup, while readable output contains no review prompt", () => {
  const raw = currentExample(), p = cardExecutionOf(raw);
  const input = { mode: "live", uploadState: "complete", job: { id: raw.source_run_id, status: "completed", original_filename: "mit-forehand.mp4" },
    evidence: { jobId: raw.source_run_id, error: null, result: { job_id: raw.source_run_id, card_execution: raw } }, summary: null };
  const backup = buildReportBackup(input, "2026-10-24T09:00:00Z"), reread = cardExecutionOf(backup.result.card_execution);
  assert.deepEqual(reread, p);
  for (const id of ids) {
    const i = card(p, id).instances[0]; assert.deepEqual(i.auxiliary_observations, card(raw, id).instances[0].auxiliary_observations);
    const html = renderToStaticMarkup(createElement(Evidence, { instance: i }));
    assert.match(html, /双腕/); assert.match(html, /未评分/); assert.doesNotMatch(html, /需要复核|人工复核/);
    assert.match(handRacketAuxiliarySummary(i), /-0.084695/);
  }
  const readable = JSON.stringify(buildPracticeReport(input)); assert.match(readable, /双腕距离变化记录/); assert.doesNotMatch(readable, /待复核|需要复核/);
});
