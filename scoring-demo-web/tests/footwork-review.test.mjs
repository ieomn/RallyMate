import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

function compiledUrl(fileUrl) {
  let js = ts.transpileModule(fs.readFileSync(fileUrl, "utf8"), { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  js = js.replace(/from "([^"]+)"/g, (_match, name) => {
    const local = new URL(`${name}.ts`, fileUrl);
    return `from ${JSON.stringify(name.startsWith(".") ? compiledUrl(fs.existsSync(local) ? local : new URL(`${name}.tsx`, fileUrl)) : import.meta.resolve(name))}`;
  });
  return `data:text/javascript;base64,${Buffer.from(js).toString("base64")}`;
}
const { footworkEpisodes, footworkFeatureText } = await import(compiledUrl(new URL("../app/lib/footwork-review.ts", import.meta.url)));
const { default: Panel } = await import(compiledUrl(new URL("../app/FootworkReviewPanel.tsx", import.meta.url)));
const { default: Evidence } = await import(compiledUrl(new URL("../app/MeasurementEvidence.tsx", import.meta.url)));
const episode = { event_id: "event-1", event_code: "FS02", name_zh: "第一步启动", person_track_id: 1, start_ms: 1200, end_ms: 1900, indicators: [{ indicator_id: "FS02-M03", name_zh: "启动速度", feature_status: "measured", scoring_status: "calibration_required", features: [{ feature_name: "launch_foot_speed_peak_body_s", name_zh: "启动脚峰值速度", value: 1.25, unit: "body/s", confidence: .9 }] }] };
const review = { schema_version: "1.0.0", status: "available", episodes: [episode] };

test("step review rejects missing, duplicate and invented time intervals", () => {
  assert.deepEqual(footworkEpisodes(), []);
  const parsed = footworkEpisodes({ ...review, episodes: [episode, episode, { ...episode, event_id: "bad", end_ms: 100 }, { ...episode, event_id: "non-step", event_code: "GS01" }, { ...episode, event_id: "nan", start_ms: NaN }] });
  assert.equal(parsed.length, 1);
  assert.equal(footworkFeatureText(null, "ms"), "—");
  assert.equal(footworkFeatureText(0, "ms"), "0 毫秒");
});

test("step panel renders measured values, timestamp replay and calibration limit", () => {
  const html = renderToStaticMarkup(createElement(Panel, { review, jobId: "job" }));
  assert.match(html, /1\.20–1\.90 秒/);
  assert.match(html, /定位回放/);
  assert.match(html, /1\.25 身体尺度\/秒/);
  assert.match(html, /技术等级待标定/);
  assert.doesNotMatch(html, /100 分|表现较稳定/);
});

test("missing step artifact explains unavailability without a fake zero-length episode", () => {
  const html = renderToStaticMarkup(createElement(Panel, { review: { ...review, status: "unavailable", reason_zh: "事件文件缺失。" } }));
  assert.match(html, /事件文件缺失/);
  assert.doesNotMatch(html, /定位回放|0\.00/);
});

test("imported malformed indicators and unavailable feature values cannot crash or become measurements", () => {
  const malformed = { ...review, episodes: [{ ...episode, indicators: [null, { ...episode.indicators[0], features: null }, { ...episode.indicators[0], indicator_id: "FS02-M04", feature_status: "unavailable", features: [{ feature_name: "fake", value: 999, confidence: .9, unit: "ms" }] }] }] };
  const parsed = footworkEpisodes(malformed);
  assert.equal(parsed[0].indicators.length, 2);
  assert.ok(parsed[0].indicators.every(item => item.features.length === 0));
  const html = renderToStaticMarkup(createElement(Panel, { review: malformed }));
  assert.doesNotMatch(html, /999|fake/);
  assert.match(html, /没有通过质量门槛/);
});

test("evidence inspector shows actual medians and keeps unknown repeatability empty", () => {
  const html = renderToStaticMarkup(createElement(Evidence, { indicator: { components: { measured_instance_ratio_percent: 100, repeatability_percent: null }, representative_measurements: [{ feature_name: "stance", label_zh: "支撑宽度", median_value: 45, unit_zh: "% 身体尺度", sample_count: 1 }] } }));
  assert.match(html, /支撑宽度：45/);
  assert.match(html, /跨片段重复性<\/dt><dd>证据不足/);
  assert.match(html, /重复性高不代表动作正确/);
  assert.doesNotMatch(html, /50%/);
});
