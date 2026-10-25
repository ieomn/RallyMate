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

test("a legacy report waits for the new score while replay stays above folded measurements", () => {
  const html = renderToStaticMarkup(createElement(LiveResults, { result: result(), assessment: null, pending: false, trajectory: null, status: "succeeded", journal: createElement("div", { id: "test-journal" }, "journal"), replay: createElement("div", { id: "test-replay" }, "replay") }));
  const primary = html.slice(0, html.indexOf('id="details"'));
  assert.equal((primary.match(/正在生成动作评分/g) ?? []).length, 1);
  assert.ok(primary.includes('id="test-replay"'));
  assert.doesNotMatch(primary, /process-overall-reference|0 \/ 5|中性先验|298.*评分卡/);
  assert.ok(html.indexOf('id="details"') < html.indexOf('id="test-journal"'));
  for (const status of ["failed", "cancelled"]) {
    const unavailable = renderToStaticMarkup(createElement(LiveResults, { result: null, assessment: null, pending: false, trajectory: null, status }));
    assert.doesNotMatch(unavailable, /正在生成动作评分|process-overall-reference/);
  }
});

test("height and speed observations produce distinct practice goals with visible observation and retest", () => {
  const rows = trainingGuidance(result());
  assert.equal(rows.filter(row => row.evidenceState !== "unavailable").length, 2);
  assert.notEqual(rows[0].question, rows[1].question);
  assert.match(rows[0].goal, /膝髋/); assert.match(rows[1].goal, /来球方向/);
  assert.deepEqual(focusSelection(rows[0]), { title: rows[0].title, question: `${rows[0].goal} 复测：${rows[0].retest}`, timestampMs: 1000 });
  const html = recommendations(result());
  const visible = html.replace(/<details class="focus-evidence-detail">[\s\S]*?<\/details>/g, "");
  assert.match(visible, /本次观察/);
  assert.match(html, /2\.66 身体尺度/); assert.match(html, /5\.94 身体尺度\/秒/);
  assert.match(visible, /练习目标/); assert.match(visible, /怎么练/); assert.match(visible, /复测标准/);
  assert.doesNotMatch(visible, /原文|Word|P0080|候选|待复核|观察什么/);
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
  assert.match(html, /未附可用原片/); assert.match(html, /保存练习目标/);
  assert.doesNotMatch(html, /回看这段原片/);
});

test("valid model estimates are identified once and never become confirmed visible facts", () => {
  const fixture = JSON.parse(fs.readFileSync(new URL("./fixtures/model-motion-null-speed.json", import.meta.url), "utf8"));
  const value = result();
  value.input.video.duration_ms = 100000;
  value.indicator_assessment.indicators = [{ ...indicator("GS01-M01-01", "GS01", fixture.motion.observation_id) }];
  value.indicator_assessment.observations = [fixture.motion];
  const rows = trainingGuidance(value);
  assert.equal(rows[0].evidenceState, "review");
  assert.match(rows[0].observation, /模型估计/);
  assert.match(rows[0].limitation, /单目估计/);
  assert.doesNotMatch(rows[0].observation, /已确认|技术水平/);
});

test("continuous playback stays above the saved training journal and folded technical details", () => {
  const html = renderToStaticMarkup(createElement(LiveResults, { result: result(), assessment: null, pending: false, trajectory: null, journal: createElement("section", { id: "test-journal" }, "训练记录"), replay: createElement("video", { id: "test-original" }) }));
  assert.ok(html.indexOf('id="assessment"') < html.indexOf('id="test-original"'));
  assert.ok(html.indexOf('id="test-original"') < html.indexOf('id="details"'));
  assert.ok(html.indexOf('id="details"') < html.indexOf('id="test-journal"'));
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
