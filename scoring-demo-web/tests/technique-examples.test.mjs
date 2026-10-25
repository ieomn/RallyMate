import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
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
const { techniqueSources, techniqueExamples, techniqueExamplesForCard } = await import(compiledUrl(new URL("../app/lib/technique-examples.ts", import.meta.url)));
const { default: Examples } = await import(compiledUrl(new URL("../app/TechniqueExamples.tsx", import.meta.url)));
const { default: Panel } = await import(compiledUrl(new URL("../app/CardExecutionPanel.tsx", import.meta.url)));
const { cardExecutionOf } = await import(compiledUrl(new URL("../app/lib/card-execution.ts", import.meta.url)));

test("teaching mappings reference actual source cards and only primary-source pages", () => {
  const catalogue = JSON.parse(fs.readFileSync(new URL("../../src/rallymate_scoring/data/metric_cards.json", import.meta.url), "utf8"));
  const cardIds = new Set(catalogue.cards.map(card => card.id));
  assert.equal(cardIds.size, 298);
  assert.equal(new Set(techniqueExamples.map(example => example.id)).size, techniqueExamples.length);
  assert.equal(Object.keys(techniqueSources).length, 7);
  const covered = new Set();
  for (const example of techniqueExamples) {
    assert.ok(techniqueSources[example.sourceId]);
    assert.ok(example.cardIds.length);
    assert.equal(new Set(example.cardIds).size, example.cardIds.length);
    for (const id of example.cardIds) { assert.ok(cardIds.has(id), id); covered.add(id); }
    assert.equal("score" in example, false); assert.equal("grade" in example, false);
  }
  assert.equal(covered.size, 34);
  for (const source of Object.values(techniqueSources)) {
    const url = new URL(source.url);
    assert.equal(url.protocol, "https:"); assert.equal(url.username, ""); assert.equal(url.password, "");
    assert.equal(url.hostname, source.provider === "USTA" ? "www.usta.com" : "www.lta.org.uk");
    assert.match(source.verifiedOn, /^\d{4}-\d{2}-\d{2}$/);
  }
});

test("unmapped cards and unsupported exact-card claims do not receive broad family examples", () => {
  for (const id of ["GS01", "GS01-M03-99", "GS04-M08-04", "FS10-M01", "FS10-M04", "FS01-M02", "javascript:alert(1)"]) {
    assert.deepEqual(techniqueExamplesForCard(id), []);
    assert.equal(renderToStaticMarkup(createElement(Examples, { indicatorId: id })), "");
  }
  const html = renderToStaticMarkup(createElement(Examples, { indicatorId: "GS04-M08-01" }));
  assert.match(html, /相关技术练习/); assert.match(html, /noopener noreferrer/);
  assert.doesNotMatch(html, /iframe|<video|<img|100 \/ 100/);
});

test("the selected card exposes teaching links without changing its formal score or an unavailable result", () => {
  const fixture = JSON.parse(fs.readFileSync(new URL("./fixtures/gs-formal-synthetic-contract.json", import.meta.url), "utf8"));
  for (const unavailable of [false, true]) {
    const raw = structuredClone(fixture.cases[0].report);
    raw.cards = raw.cards.filter(card => card.indicator_id === "GS01-M03-01");
    assert.equal(raw.cards.length, 1);
    if (unavailable) raw.cards[0].instances[0].conditions[0].value = null;
    const snapshot = structuredClone(raw), before = cardExecutionOf(raw);
    const html = renderToStaticMarkup(createElement(Panel, { value: raw }));
    assert.match(html, /官方教学示范/); assert.match(html, /查看 USTA 教学示范/);
    assert.deepEqual(raw, snapshot); assert.deepEqual(cardExecutionOf(raw), before);
    if (unavailable) { assert.equal(before.cards[0].instances[0].score, null); assert.match(html, /未评分/); }
    else assert.equal(before.cards[0].instances[0].status, "scored");
  }
});
