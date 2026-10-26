import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import ts from "typescript";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { tennisFixture, tennisHash, tennisResult } from "./fixtures/tennis-assessment.mjs";

const cache = new Map();
function compiledUrl(url) {
  if (url.pathname.endsWith(".json")) return `data:text/javascript;base64,${Buffer.from(`export default ${fs.readFileSync(url, "utf8")}`).toString("base64")}`;
  if (cache.has(url.href)) return cache.get(url.href);
  let js = ts.transpileModule(fs.readFileSync(url, "utf8"), { fileName: url.pathname, compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  js = js.replace(/^import\s+["'][^"']+\.css["'];?\s*$/gm, "");
  js = js.replace(/from "([^"]+)"/g, (_match, name) => {
    const local = new URL(name.endsWith(".json") ? name : `${name}.ts`, url);
    return `from ${JSON.stringify(name.startsWith(".") ? compiledUrl(fs.existsSync(local) ? local : new URL(`${name}.tsx`, url)) : import.meta.resolve(name))}`;
  });
  const output = `data:text/javascript;base64,${Buffer.from(js).toString("base64")}`;
  cache.set(url.href, output); return output;
}
const { validatedTennisAssessment, tennisAssessmentOf, retainTennisAssessment } = await import(compiledUrl(new URL("../app/lib/tennis-assessment.ts", import.meta.url)));
const { default: Summary, tennisImprovementFocus } = await import(compiledUrl(new URL("../app/TennisAssessmentSummary.tsx", import.meta.url)));
const { default: LiveResults } = await import(compiledUrl(new URL("../app/LiveResults.tsx", import.meta.url)));
const context = { jobId: "tennis-test", videoSha256: tennisHash, durationMs: 9000 };
const parse = report => validatedTennisAssessment(report, context);
const render = (report, extra = {}) => renderToStaticMarkup(createElement(Summary, { report, ...extra }));

test("new action scores bind to the current job, video hash and original source time", () => {
  const fixture = tennisFixture(), original = structuredClone(fixture);
  assert.equal(parse(fixture).overall_score, 70); assert.deepEqual(fixture, original);
  for (const patch of [{ source_run_id: "foreign-job" }, { source_video_sha256: "b".repeat(64) }, { source_timestamp_semantics: "frames" }, { version: "unknown" }]) assert.equal(parse({ ...fixture, ...patch }), null);
  assert.equal(tennisAssessmentOf(tennisResult()).overall_score, 70);
  const result = tennisResult(); delete result.input.video.sha256;
  assert.equal(tennisAssessmentOf(result), null, "a payload cannot vouch for its own video identity");
  result.original_video = { sha256: tennisHash }; assert.equal(tennisAssessmentOf(result).overall_score, 70);
  result.input.video.sha256 = "b".repeat(64); assert.equal(tennisAssessmentOf(result), null);
});

test("unknown rubrics, duplicate dimensions and ungrounded scores cannot enter the main card", () => {
  for (const patch of [{ identity_labels_used: true }, { confidence_used_in_score: true }, { coach_calibrated: true }, { aggregation: "weighted_confidence" }]) {
    const report = tennisFixture(); Object.assign(report.rubric, patch); assert.equal(parse(report), null);
  }
  for (const mutate of [r => r.dimensions.pop(), r => r.dimensions[1].id = "movement", r => r.overall_score = 95,
    r => r.dimensions[0].score = 95, r => r.dimensions[0].metrics[0].score = null, r => r.dimensions[0].metrics[0].value = null,
    r => r.dimensions[0].observation_count = 0, r => r.dimensions[0].metrics[0].weight = 2,
    r => r.dimensions[0].score = NaN, r => r.dimensions[0].metrics[0].value = Infinity]) {
    const report = tennisFixture(); mutate(report); assert.equal(parse(report), null);
  }
});

test("the old saturated score scale cannot survive validation or late-result retention", () => {
  const old = tennisFixture(); old.version = "tennis-action-assessment-v1.0.0"; old.rubric.version = "tennis-action-rubric-v1.0.0";
  assert.equal(parse(old), null);
  const incoming = tennisResult(); delete incoming.tennis_assessment;
  assert.equal(retainTennisAssessment(incoming, { ...tennisResult(), tennis_assessment: old }).tennis_assessment, undefined);
  const mixed = tennisFixture(); mixed.rubric.version = "tennis-action-rubric-v1.0.0";
  assert.equal(parse(mixed), null);
});

test("mixed actions use their measured contribution weights instead of averaging unlike metric IDs equally", () => {
  const report = tennisFixture(), dimension = report.dimensions[0], metric = dimension.metrics[0];
  dimension.metrics = [{ ...metric, score: 80, weight: 100 / 101 }, { ...metric, id: "serve_support", score: 20, weight: 1 / 101 }];
  dimension.score = (80 * 100 + 20) / 101;
  report.overall_score = (dimension.score + 60 + 70 + 80 + 90) / 5;
  assert.ok(parse(report));
  const wrongAverage = structuredClone(report); wrongAverage.dimensions[0].score = 50; wrongAverage.overall_score = 70;
  assert.equal(parse(wrongAverage), null, "one serve cannot receive the same action weight as 100 baseline swings");
  for (const invalid of [0, -1, NaN, Infinity]) {
    const bad = structuredClone(report); bad.dimensions[0].metrics[0].weight = invalid; assert.equal(parse(bad), null);
  }
  dimension.metrics.push({ ...metric, id: "unmeasured", score: null, value: null, observation_count: 0, weight: 1 });
  assert.ok(parse(report), "unavailable metrics do not enter the weight denominator");
  report.rubric.aggregation = "mean_episode_metric_scores_then_equal_dimension_mean";
  assert.equal(parse(report), null, "the earlier aggregation is not the current score scale");
});

test("partial radar keeps missing dimensions empty while genuine zero remains zero", () => {
  const report = tennisFixture(); report.status = "partial";
  report.dimensions[0].score = null; report.dimensions[0].metrics[0].score = null; report.dimensions[0].metrics[0].value = null;
  report.overall_score = 75;
  const parsed = parse(report); assert.equal(parsed.dimensions[0].score, null);
  const html = render(parsed); assert.match(html, />—<\/text>/); assert.doesNotMatch(html, /class="tennis-radar-shape"/);
  assert.match(html, /本次已测 4 个动作环节/);
  report.status = "scored"; report.dimensions[0].score = 0; report.dimensions[0].metrics[0].score = 0; report.dimensions[0].metrics[0].value = 0; report.overall_score = 60;
  assert.equal(parse(report).dimensions[0].score, 0); assert.match(render(parse(report)), />0\.0<\/text>/);
});

test("practice timestamps must fit the source video and an untimed drill never invents a seek", () => {
  for (const patch of [{ start_ms: -1 }, { start_ms: 2200 }, { start_ms: NaN }, { end_ms: 10000 }, { end_ms: null }]) {
    const report = tennisFixture(); Object.assign(report.improvements[0], patch); assert.equal(parse(report), null);
  }
  const report = tennisFixture(); report.improvements.forEach(item => { item.start_ms = null; item.end_ms = null; });
  const parsed = parse(report); assert.doesNotMatch(render(parsed), /回看这段动作/);
  assert.equal(tennisImprovementFocus(parsed.improvements[0]).timestampMs, undefined);
  const focus = tennisImprovementFocus(tennisFixture().improvements[0]); assert.equal(focus.timestampMs, 1000); assert.match(focus.question, /复测/);
});

test("default summary shows one score, five dimensions and two actionable drills without technical copy", () => {
  const html = render(parse(tennisFixture()), { onSaveFocus() {} });
  assert.match(html, /<strong>70\.0<\/strong>/); assert.match(html, /五维动作评分图/);
  assert.equal((html.match(/class="tennis-radar-value"/g) ?? []).length, 5);
  assert.equal((html.match(/class="tennis-drill"/g) ?? []).length, 2);
  assert.equal((html.match(/怎么练/g) ?? []).length, 2); assert.equal((html.match(/复测目标/g) ?? []).length, 2);
  assert.match(html, /本次分析了 2 段挥拍/);
  assert.match(html, /试验评分，不代表竞技等级/);
  assert.match(html, /继续保持/); assert.match(html, /随挥后能恢复准备姿势/);
  assert.match(html, /对应片段 1\.0–2\.2 秒/);
  assert.doesNotMatch(html, /置信|算法|规则未实现|P0080|Word|technical|coach_calibrated|触球质量/);
});

test("legacy report has one loading message and all older measurement panels start folded after playback", () => {
  const result = tennisResult(); delete result.tennis_assessment; result.training_evaluation = { score_0_to_100: 99 };
  const html = renderToStaticMarkup(createElement(LiveResults, { result, assessment: null, pending: false, trajectory: null,
    journal: createElement("div", { id: "journal-test" }, "关注点"), details: createElement("div", { id: "tools-test" }, "工具"), replay: createElement("video", { id: "video-test" }) }));
  const details = html.indexOf('id="details"'), primary = html.slice(0, details);
  assert.equal((primary.match(/正在生成动作评分/g) ?? []).length, 1);
  assert.doesNotMatch(primary, /process-overall-reference|>99|待观察|0 \/ 5/);
  assert.ok(html.indexOf('id="video-test"') < details); assert.ok(html.indexOf('id="journal-test"') > details); assert.ok(html.indexOf('id="tools-test"') > details);
  assert.match(html, /<details class="report-all-details" id="details">/); assert.doesNotMatch(html, /<details[^>]*\bopen(?:=|>)/);
});

test("unavailable and offline states use one clear action instead of a page of empty scores", () => {
  assert.match(render(null, { offline: true }), /打开在线报告更新/);
  assert.match(render(null, { error: true, onRetry() {} }), /重新读取/);
  const missingSource = render(null, { message: "原视频来源未读取完整，请重新读取报告。", onRetry() {} });
  assert.match(missingSource, /原视频来源未读取完整/); assert.match(missingSource, /重新读取<\/button>/);
  assert.doesNotMatch(missingSource, /正在生成动作评分|tennis-loading-dot/);
  const report = tennisFixture(); report.status = "unavailable"; report.overall_score = null;
  report.dimensions.forEach(d => { d.score = null; d.observation_count = 0; d.metrics.forEach(m => { m.score = null; m.value = null; m.observation_count = 0; }); });
  const html = render(parse(report)); assert.match(html, /先完成移动/); assert.doesNotMatch(html, /tennis-radar|tennis-score-number|正在生成/);
  report.reason_zh = "动作姿态记录缺失或来源校验未通过。";
  assert.match(render(parse(report)), /来源校验未通过/);
  assert.doesNotMatch(render(parse(report)), /未完整入镜/);
  report.reason_zh = { unsafe: true }; assert.equal(parse(report), null);
});

test("late supplemental results retain a loaded score only for the same job and independently bound video", () => {
  const previous = tennisResult(), incoming = tennisResult(); delete incoming.tennis_assessment;
  assert.equal(tennisAssessmentOf(retainTennisAssessment(incoming, previous)).overall_score, 70);
  for (const mutate of [r => { r.job_id = "foreign-job"; }, r => { r.input.video.sha256 = "b".repeat(64); }, r => { r.input.video.duration_ms = 500; }]) {
    const next = structuredClone(incoming); mutate(next); assert.equal(retainTennisAssessment(next, previous).tennis_assessment, undefined);
  }
  const explicit = { ...incoming, tennis_assessment: { version: "unknown" } };
  assert.equal(retainTennisAssessment(explicit, previous), explicit, "do not hide a new explicit invalid server result with a previous score");
});

test("complete episode navigation is source-bound, strictly timed and reduced to necessary fields", () => {
  const report = tennisFixture(); report.episodes[0].metrics = { private_debug: 123 };
  const parsed = parse(report);
  assert.equal(parsed.episodes.length, 2); assert.equal(parsed.episodes[0].metrics, undefined);
  assert.deepEqual(Object.keys(parsed.episodes[0]).sort(), ["end_ms", "episode_id", "family", "peak_ms", "start_ms"]);
  for (const patch of [{ episode_id: "" }, { episode_id: "../foreign" }, { family: "unknown" }, { start_ms: -1 }, { start_ms: 1500 },
    { peak_ms: 2200 }, { peak_ms: Infinity }, { end_ms: 9001 }, { end_ms: null }]) {
    const value = tennisFixture(); Object.assign(value.episodes[0], patch); assert.equal(parse(value).episodes.length, 1, JSON.stringify(patch));
  }
  const duplicate = tennisFixture(); duplicate.episodes.push({ ...duplicate.episodes[0], start_ms: 5000, peak_ms: 5500, end_ms: 6000 });
  assert.deepEqual(parse(duplicate).episodes.map(e => e.episode_id), ["complete-swing-2"], "ambiguous episode IDs never select a different occurrence");
  assert.deepEqual(validatedTennisAssessment(report, { ...context, durationMs: undefined }).episodes, [], "no duration means no verified navigation range");
  const legacy = tennisFixture(); delete legacy.episodes; assert.equal(parse(legacy).episodes, undefined);
  assert.deepEqual(parse({ ...report, episodes: "untrusted" }).episodes, []);
  assert.deepEqual(tennisAssessmentOf({ ...tennisResult(), tennis_assessment: parsed }).episodes, parsed.episodes, "the lightweight response retains canonical navigation when merged");
});
