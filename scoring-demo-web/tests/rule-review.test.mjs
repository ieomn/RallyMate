import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import ts from "typescript";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

const compiled = new Map();
function compiledUrl(file) {
  if (compiled.has(file.href)) return compiled.get(file.href);
  let js = ts.transpileModule(fs.readFileSync(file, "utf8"), { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  js = js.replace(/from "([^"]+)"/g, (_match, name) => {
    const local = new URL(`${name}.ts`, file);
    return `from ${JSON.stringify(name.startsWith(".") ? compiledUrl(fs.existsSync(local) ? local : new URL(`${name}.tsx`, file)) : import.meta.resolve(name))}`;
  });
  const url = `data:text/javascript;base64,${Buffer.from(js).toString("base64")}`;
  compiled.set(file.href, url); return url;
}
const { reviewRuleResult } = await import(compiledUrl(new URL("../app/lib/rule-review.ts", import.meta.url)));
const { default: Panel } = await import(compiledUrl(new URL("../app/RuleReviewPanel.tsx", import.meta.url)));
const catalog = JSON.parse(fs.readFileSync(new URL("../app/data/scoring-reference.json", import.meta.url), "utf8"));

test("source association does not promote proxy measurements to Word grades", () => {
  const rule = catalog.rules.find(item => item.id === "FS01-M04");
  const result = { training_evaluation: { indicator_evaluations: [{ indicator_id: rule.id, representative_measurements: [{ feature_name: "stance_width_body", median_value: .3 }] }] } };
  const review = reviewRuleResult(rule, result);
  assert.equal(review.status, "related_measurements");
  assert.match(review.reason, /尚不能完整判定/);
  assert.match(rule.implementation.note, /髋宽/);
  assert.equal(review.grade, undefined);
});

test("unimplemented and missing evidence remain distinct from zero or E", () => {
  const absent = reviewRuleResult(catalog.rules.find(item => item.id === "FS01-M02"), null);
  assert.equal(absent.status, "unavailable");
  assert.match(absent.reason, /不会记作 0 分或 E 级/);
  const notImplemented = reviewRuleResult(catalog.rules.find(item => item.id === "GS01-M01-01"), null);
  assert.equal(notImplemented.status, "not_implemented");
});

test("known source conflicts are explicit and original A-E remains a reference", () => {
  for (const id of ["GS01-M10-04", "GS02-M01-01", "GS02-M01-02"]) {
    const rule = catalog.rules.find(item => item.id === id);
    assert.equal(reviewRuleResult(rule, null).status, "source_needs_review");
    const html = renderToStaticMarkup(createElement(Panel, { result: null, catalog, initialIndicatorId: id }));
    assert.match(html, /原文待核实/);
    assert.match(html, /尚未判定本次视频属于其中任何一级/);
    assert.match(html, /全部原文指标 298/);
  }
});

test("visual optional stages are disclosed without automatic penalties", () => {
  assert.equal(catalog.visual_techniques.length, 24);
  assert.ok(catalog.visual_techniques.flatMap(item => item.phases).some(phase => phase.optional));
  const html = renderToStaticMarkup(createElement(Panel, { result: null, catalog }));
  assert.match(html, /原文可选/);
  assert.match(html, /缺少时不自动扣分/);
  assert.doesNotMatch(html, /C:\\Users|xwechat_files/);
});

test("a direct request for a later indicator starts on its containing page", () => {
  const html = renderToStaticMarkup(createElement(Panel, { result: null, catalog, initialIndicatorId: "FS09-M05" }));
  assert.match(html, /2 \/ 2/);
  assert.match(html, /<p>FS09-M05<\/p>/);
  assert.match(html, /aria-pressed="true"><span>制动急停/);
});
