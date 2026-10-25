import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import ts from "typescript";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

const cache = new Map();
function compiledUrl(file) {
  if (cache.has(file.href)) return cache.get(file.href);
  const source = fs.readFileSync(file, "utf8");
  let js = file.pathname.endsWith(".json") ? `export default ${source}` : ts.transpileModule(source, { fileName: file.pathname, compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  js = js.replace(/from "([^"]+)"/g, (_match, name) => {
    const local = new URL(name.endsWith(".json") ? name : `${name}.ts`, file);
    return `from ${JSON.stringify(name.startsWith(".") ? compiledUrl(fs.existsSync(local) ? local : new URL(`${name}.tsx`, file)) : import.meta.resolve(name))}`;
  });
  const url = `data:text/javascript;base64,${Buffer.from(js).toString("base64")}`;
  cache.set(file.href, url); return url;
}
const { processEvaluationOf, processEvaluationValidationOf } = await import(compiledUrl(new URL("../app/lib/process-evaluation.ts", import.meta.url)));
const { relativeReferenceOf } = await import(compiledUrl(new URL("../app/lib/relative-score.ts", import.meta.url)));
const { default: Panel } = await import(compiledUrl(new URL("../app/ProcessScorePanel.tsx", import.meta.url)));
const { buildPracticeReport, buildReportBackup } = await import(compiledUrl(new URL("../app/lib/report-export.ts", import.meta.url)));
const policy = JSON.parse(fs.readFileSync(new URL("../../src/rallymate_scoring/data/relative_reference_policy.json", import.meta.url)));
const criterion = (id, value, extra = {}) => {
  const rule = [...policy.rules, ...policy.excluded_legacy_rules].find(row => row.id === id);
  return { id, label_zh: "旧标签", status: "measured", value, unit: rule.unit, source: rule.source, score: id === "movement_target_alignment" ? 100 * (1 - value / 180) : value,
    rule_zh: "旧版参考得分", reason_zh: "旧版参考得分 99", start_ms: 100, end_ms: 700, episode_id: "e1", sample_count: 5, ...extra };
};
const process = (id = "swing", rows = [criterion("acceleration_trend", 80)], extra = {}) => ({ version: "process-evaluation-v2.0.0", semantics: "observable_motion_process_reference", is_calibrated: false, overall_score: null,
  dimensions: [...policy.dimensions.map(row => row.id), "transition"].map(key => key === id ? { id, status: "partial", score: rows[0]?.score ?? null, aggregation: "mean_of_independent_samples_for_one_explicit_rule", criteria: rows } : { id: key, status: "unavailable", score: null, aggregation: "none", criteria: [] }), ...extra });
const result = value => ({ status: "ready", job_id: "job-score-v2", process_evaluation: value, training_evaluation: { summary_zh: "已完成过程测量。", score_0_to_100: null, indicator_evaluations: [] }, actions: [] });
const html = value => renderToStaticMarkup(createElement(Panel, { result: result(value), pending: false, motion: null, title: "分析中", description: "等待报告" }));
const input = value => ({ mode: "live", uploadState: "complete", job: { id: "job-score-v2", status: "completed" }, evidence: { jobId: "job-score-v2", result: result(value) } });

test("v3 excludes circular speed trends while preserving raw measurements and historical diagnostics", () => {
  for (const version of ["process-evaluation-v1.0.0", "process-evaluation-v1.1.0", "process-evaluation-v1.2.0", "process-evaluation-v2.0.0"]) {
    const parsed = processEvaluationOf(process("swing", undefined, { version }));
    assert.equal(parsed.relative_reference.version, policy.version);
    assert.equal(parsed.relative_reference.overall_score, null);
    assert.equal(parsed.relative_reference.legacy_diagnostic.overall_score, 51.5);
    assert.equal(parsed.dimensions[2].score, null);
    assert.equal(parsed.dimensions[2].criteria[0].value, 80);
    assert.equal(parsed.dimensions[2].criteria[0].legacy_diagnostic.score, 80);
    assert.equal(parsed.dimensions[2].status_reason, "selection_biased_proxy_excluded");
    assert.doesNotMatch(parsed.dimensions[2].criteria[0].reason_zh, /参考得分 99/);
    assert.deepEqual(processEvaluationOf(parsed), parsed);
  }
});

test("missing dimensions remain unscored without exposing internal formulas or historical grades", () => {
  const rendered = html(process());
  const firstScreen = rendered.slice(0, rendered.indexOf("<details"));
  assert.match(firstScreen, /aria-label="本次训练评分"/);
  assert.equal((firstScreen.match(/<strong>未评分<\/strong>/g) ?? []).length, 5);
  assert.doesNotMatch(firstScreen, /n\/\(n\+3\)|中性暂估|历史 v1|评分卡/);
  assert.equal((rendered.match(/class="process-radar-point/g) ?? []).length, 0);
  assert.match(rendered, /已测/);
});

test("movement is the only active contribution and dimension buttons agree with observed score", () => {
  const raw = process("movement", [criterion("movement_target_alignment", 0)]);
  const parsed = processEvaluationOf(raw), reference = parsed.relative_reference;
  assert.equal(reference.overall_score, 100);
  assert.deepEqual(reference.dimensions.map(row => row.score), [100, null, null, null, null]);
  assert.match(html(raw), /<span>移动质量<small>已评分[^<]*<\/small><\/span><strong>100<\/strong>/);
  assert.equal(processEvaluationOf(process("movement", [criterion("movement_target_alignment", 180)])).relative_reference.overall_score, 0);
});

test("unknown versions and arbitrary bare scores show explicit failed validation", () => {
  for (const raw of [process("swing", undefined, { version: "unknown-v99" }), process("swing", undefined, { overall_score: 98 }), process("swing", undefined, { relative_reference: { version: "future", overall_score: 98 } }), { overall_score: 98 }]) {
    assert.equal(processEvaluationOf(raw), null);
    assert.equal(processEvaluationValidationOf(raw).status, "invalid");
    assert.match(html(raw), /role="alert">数据校验未通过/);
    assert.doesNotMatch(html(raw), /<strong>98/);
  }
  assert.equal(processEvaluationValidationOf(undefined).status, "missing");
});

test("conflicting measurements preserve source-conflict diagnostics across sanitization", () => {
  const raw = process("movement", [criterion("movement_target_alignment", 0), criterion("movement_target_alignment", 180)]);
  const parsed = processEvaluationOf(raw);
  assert.equal(parsed.relative_reference.overall_score, null);
  assert.equal(parsed.relative_reference.dimensions[0].gap.type, "source_conflict");
  assert.equal(parsed.relative_reference.dimensions[0].scoring_audit.excluded_conflicting_episode_count, 1);
  assert.deepEqual(processEvaluationOf(parsed), parsed);
  assert.equal(processEvaluationValidationOf(parsed).status, "invalid");
});

test("rejected speed measurements cannot keep a row-level historical score", () => {
  const conflict = processEvaluationOf(process("swing", [criterion("acceleration_trend", 80), criterion("acceleration_trend", 20)]));
  assert.equal(conflict.dimensions[2].criteria[0].legacy_diagnostic.score, null);
  assert.equal(conflict.relative_reference.legacy_diagnostic.overall_score, 50);
  const raw = process(); raw.dimensions[2].status = "unavailable";
  const rejected = processEvaluationOf(raw);
  assert.equal(rejected.dimensions[2].criteria[0].legacy_diagnostic.score, null);
  assert.equal(rejected.relative_reference.legacy_diagnostic.overall_score, 50);
  const forged = processEvaluationOf(process("swing", [criterion("acceleration_trend", 80, { source_conflict: true })]));
  assert.equal(forged.dimensions[2].criteria[0].status, "unavailable");
  assert.equal(forged.dimensions[2].criteria[0].value, null);
  assert.equal(forged.dimensions[2].criteria[0].legacy_diagnostic.score, null);
  assert.equal(forged.relative_reference.legacy_diagnostic.overall_score, 50);
  assert.deepEqual(processEvaluationOf(forged), forged);
});

test("exports use the same v3 total and retain invalid-contract or rejected-input warnings", () => {
  const raw = process("movement", [criterion("movement_target_alignment", 0)]);
  assert.ok(buildPracticeReport(input(raw)).sections.some(row => row.title === "训练评分：100 / 100"));
  assert.equal(buildReportBackup(input(raw)).result.process_evaluation.relative_reference.overall_score, 100);
  const invalid = { ...raw, version: "unknown-v99" };
  const restored = buildReportBackup(input(invalid)).result.process_evaluation;
  assert.equal(restored.version, "unknown-v99");
  assert.equal(processEvaluationValidationOf(restored).status, "invalid");
  const rejected = process("swing", [{ id: "unknown", label_zh: "未知测量", status: "measured", value: 99 }]);
  const backedUp = buildReportBackup(input(rejected)).result.process_evaluation;
  assert.equal(processEvaluationValidationOf(backedUp).status, "invalid");
});

test("failed, cancelled and pending responses never display final numeric reference charts", () => {
  for (const status of ["failed", "error", "cancelled", "pending", "running"]) {
    const rendered = renderToStaticMarkup(createElement(Panel, { result: { ...result(process()), status }, pending: false, motion: null, title: "等待", description: "等待" }));
    assert.doesNotMatch(rendered, /class="process-overall-reference"|class="process-radar-point/);
  }
  assert.equal(relativeReferenceOf().overall_score, null);
});
