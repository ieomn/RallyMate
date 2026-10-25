import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import ts from "typescript";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

const cache = new Map();
function compiledUrl(url) {
  // Keep source JSON imports shared with production, without a generated copy.
  if (url.pathname.endsWith(".json")) return `data:text/javascript;base64,${Buffer.from(`export default ${fs.readFileSync(url, "utf8")}`).toString("base64")}`;
  if (cache.has(url.href)) return cache.get(url.href);
  let js = ts.transpileModule(fs.readFileSync(url, "utf8"), { fileName: url.pathname, compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  js = js.replace(/^import\s+["'][^"']+\.css["'];?\s*$/gm, "");
  js = js.replace(/import (\w+) from "([^"\n]+\.json)";?/g, (_match, binding, name) => `const ${binding} = ${fs.readFileSync(new URL(name, url), "utf8")};`);
  js = js.replace(/from "([^"]+)"/g, (_match, name) => {
    const local = new URL(name.endsWith(".json") ? name : `${name}.ts`, url);
    return `from ${JSON.stringify(name.startsWith(".") ? compiledUrl(fs.existsSync(local) ? local : new URL(`${name}.tsx`, url)) : import.meta.resolve(name))}`;
  });
  const output = `data:text/javascript;base64,${Buffer.from(js).toString("base64")}`;
  cache.set(url.href, output); return output;
}
const { trainingGuidance, focusSelection } = await import(compiledUrl(new URL("../app/lib/training-guidance.ts", import.meta.url)));
const { TrainingRecommendations } = await import(compiledUrl(new URL("../app/TrainingReportOverview.tsx", import.meta.url)));
const { default: LiveResults } = await import(compiledUrl(new URL("../app/LiveResults.tsx", import.meta.url)));
const indicator = (id, event, observationId) => ({ indicator_id: id, name_zh: "动作局部测量", event_code: event, stage_code: id, status: "contextual_measurement", observation_ids: [observationId], blockers: [], source: {}, grade: {} });
const observation = (id, feature, value, start = 1000, end = 1800) => ({ observation_id: id, source: "motion_joint_measurement", status: "measured", feature_name: feature, label_zh: "局部运动测量", value, unit: "body", start_ms: start, end_ms: end, statistics: {}, windows: [], scope: "contextual_motion_phase", definition_zh: "二维局部测量，不代表正式动作阶段。" });
const result = () => ({ job_id: "review-test-job", input: { video: { duration_ms: 3000 } }, indicator_assessment: {
  version: "word-indicator-assessment-v1.0.0", source_binding: {},
  indicators: [indicator("FS01-M01", "FS01", "height"), indicator("FS02-M01", "FS02", "speed")],
  observations: [observation("height", "hip_center_y_body", 2.66), { ...observation("speed", "body_center_speed_body_s", 5.94, 1900, 2600), unit: "body/s" }],
} });
const recommendations = (value, props = {}) => renderToStaticMarkup(createElement(TrainingRecommendations, { result: value, motion: null, pending: false, ...props }));

test("completed report puts one honest score state before recommendations, journal and replay", () => {
  const html = renderToStaticMarkup(createElement(LiveResults, { result: result(), assessment: null, pending: false, trajectory: null, status: "succeeded", journal: createElement("div", { id: "test-journal" }, "journal"), replay: createElement("div", { id: "test-replay" }, "replay") }));
  const score = html.indexOf('class="process-overall-reference"');
  assert.ok(score >= 0);
  assert.equal((html.match(/class="process-overall-reference"/g) ?? []).length, 1);
  for (const later of ['id="recommendations"', 'id="test-journal"', 'id="test-replay"', 'class="report-deep-heading"']) assert.ok(score < html.indexOf(later), later);
  assert.match(html, /class="process-overall-reference"><strong>—<\/strong>/);
  assert.match(html, /0 \/ 5 维有评分依据/);
  assert.doesNotMatch(html, /class="process-overall-reference"><strong>50|中性先验|298.*评分卡/);
  for (const options of [{ result: null, status: "error", error: "报告读取失败" }, { result: result(), pending: true, status: "running" }, { result: null, status: "cancelled" }]) {
    const unavailable = renderToStaticMarkup(createElement(LiveResults, { result: null, assessment: null, pending: false, trajectory: null, ...options }));
    assert.doesNotMatch(unavailable, /class="process-overall-reference"/);
  }
});

test("height and speed observations produce different review questions and keep raw values in collapsed details", () => {
  const rows = trainingGuidance(result());
  assert.equal(rows.filter(row => row.evidenceState !== "unavailable").length, 2);
  assert.notEqual(rows[0].question, rows[1].question);
  assert.match(rows[0].question, /髋部高度/); assert.match(rows[1].question, /移动变慢/);
  assert.deepEqual(focusSelection(rows[0]), { title: rows[0].title, question: rows[0].question, timestampMs: 1000 });
  const html = recommendations(result());
  const visible = html.replace(/<details class="focus-evidence-detail">[\s\S]*?<\/details>/g, "");
  assert.doesNotMatch(visible, /2\.66|5\.94|身体尺度/);
  assert.match(html, /2\.66 身体尺度/); assert.match(html, /5\.94 身体尺度\/秒/);
  assert.match(visible, /看哪段/); assert.match(visible, /观察什么/); assert.match(visible, /下一步/);
  assert.match(visible, /通用练习不代表已诊断出你的动作错误/);
  assert.doesNotMatch(visible, /你的动作错误是|判断为动作错误|技术较差|合格/);
});

test("an unverified or out-of-duration interval never offers a replay or saves a timestamp", () => {
  for (const mutate of [value => { value.input.video.duration_ms = 500; }, value => { delete value.input; }, value => { value.indicator_assessment.observations.forEach(row => { row.start_ms = -1; }); }]) {
    const value = result(); mutate(value);
    const rows = trainingGuidance(value);
    assert.equal(rows[0].moment, undefined);
    assert.equal(focusSelection(rows[0]).timestampMs, undefined);
    assert.doesNotMatch(recommendations(value), /回看这段原片/);
  }
});

test("missing media retains a valid interval and focus action without offering an unusable playback", () => {
  const html = recommendations(result(), { replayAvailable: false, onSaveFocus: () => {} });
  assert.match(html, /1\.00 秒 – 1\.80 秒/);
  assert.match(html, /未附可用原片/); assert.match(html, /保存为关注点/);
  assert.doesNotMatch(html, /回看这段原片/);
});

test("valid model estimates stay review questions and never become confirmed visible facts", () => {
  const fixture = JSON.parse(fs.readFileSync(new URL("./fixtures/model-motion-null-speed.json", import.meta.url), "utf8"));
  const value = result();
  value.input.video.duration_ms = 100000;
  value.indicator_assessment.indicators = [{ ...indicator("GS01-M01-01", "GS01", fixture.motion.observation_id) }];
  value.indicator_assessment.observations = [fixture.motion];
  const rows = trainingGuidance(value);
  assert.equal(rows[0].evidenceState, "review");
  assert.match(rows[0].observation, /模型估计/);
  assert.match(rows[0].limitation, /可见性已验证/);
  assert.doesNotMatch(rows[0].observation, /已确认|技术水平/);
});

test("the report leads with review questions, keeps the journal before playback and starts deep details folded", () => {
  const html = renderToStaticMarkup(createElement(LiveResults, { result: result(), assessment: null, pending: false, trajectory: null, journal: createElement("section", { id: "test-journal" }, "训练记录"), replay: createElement("video", { id: "test-original" }) }));
  assert.ok(html.indexOf('id="recommendations"') < html.indexOf('id="test-journal"'));
  assert.ok(html.indexOf('id="test-journal"') < html.indexOf('id="test-original"'));
  assert.ok(html.indexOf('id="test-original"') < html.indexOf("深入查看"));
  assert.doesNotMatch(html, /<details[^>]*open=/);
});

test("failed and cancelled empty reports do not leave normal report sections under the error", () => {
  for (const status of ["failed", "cancelled"]) {
    const html = renderToStaticMarkup(createElement(LiveResults, { result: null, assessment: null, pending: false, trajectory: null, status }));
    assert.match(html, /当前没有可查看的报告内容/);
    assert.doesNotMatch(html, /id="recommendations"|id="scoreboard"|id="rules"|process-radar/);
  }
});

test("partial results retain the available review while clearly stating that the result is partial", () => {
  const html = recommendations(result(), { pending: true, status: "running" });
  assert.match(html, /部分结果可用/); assert.match(html, /回看这段原片/);
  assert.doesNotMatch(html, /已完成|完成后会在这里/);
});
