import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import ts from "typescript";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

const cache = new Map();
function compiledUrl(file) {
  // Keep source JSON imports shared with production, without a generated copy.
  if (file.pathname.endsWith(".json")) return `data:text/javascript;base64,${Buffer.from(`export default ${fs.readFileSync(file, "utf8")}`).toString("base64")}`;
  if (cache.has(file.href)) return cache.get(file.href);
  let js = ts.transpileModule(fs.readFileSync(file, "utf8"), {
    fileName: file.pathname,
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  js = js.replace(/from "([^"]+)"/g, (_match, name) => `from ${JSON.stringify(name.startsWith(".") ? compiledUrl(new URL(name.endsWith(".json") ? name : `${name}.ts`, file)) : import.meta.resolve(name))}`);
  const url = `data:text/javascript;base64,${Buffer.from(js).toString("base64")}`;
  cache.set(file.href, url); return url;
}
const { default: RequestReadDetails } = await import(compiledUrl(new URL("../app/RequestReadDetails.tsx", import.meta.url)));
const row = overrides => ({ operation: "report", stage: "body", outcome: "failure", category: "body_read", requestId: "af4fd069-5829-46f6-b10e-2c15a5427d3a", httpStatus: 200, startedAt: "2026-10-18T19:00:00.000Z", elapsedMs: 20003, bodyComplete: false, ...overrides });
const render = diagnostics => renderToStaticMarkup(createElement(RequestReadDetails, { diagnostics }));

test("read details show actual HTTP/body stage without exposing arbitrary error data", () => {
  const html = render([row({ url: "SECRET_URL", headers: { authorization: "SECRET_TOKEN" }, body: "SECRET_VIDEO", message: "SECRET_MESSAGE", stack: "SECRET_STACK", cause: new Error("SECRET_CAUSE") })]);
  assert.match(html, /HTTP 200/); assert.match(html, /正文未收完/); assert.match(html, /内容读取中断/);
  assert.match(html, /af4fd069-5829-46f6-b10e-2c15a5427d3a/);
  assert.doesNotMatch(html, /SECRET_/); assert.doesNotMatch(html, /<details[^>]*\sopen/);
});

test("read details reject invalid records and bound retained visible history", () => {
  assert.equal(render([]), "");
  assert.equal(render([row({ category: "SECRET_CATEGORY" }), row({ elapsedMs: Infinity }), row({ requestId: "<script>" })]), "");
  const html = render(Array.from({ length: 60 }, (_, i) => row({ elapsedMs: i, outcome: "success", category: null, bodyComplete: true })));
  assert.equal((html.match(/<li>/g) || []).length, 48);
  assert.doesNotMatch(html, /耗时 0 ms/); assert.match(html, /耗时 59 ms/);
});

test("pose validation labels distinguish data and window rejection without inventing a failed HTTP response", () => {
  for (const [category, label] of [["pose_normalization", "动作标记格式或任务不匹配"], ["pose_window_mismatch", "动作标记时间窗不匹配"]]) {
    const html = render([row({ operation: "pose_preview", stage: "decode", category, bodyComplete: true, message: "SECRET_BODY", cause: "SECRET_CAUSE" })]);
    assert.match(html, /动作标记 · 核对动作标记：未完成/);
    assert.ok(html.includes(label)); assert.match(html, /HTTP 200/); assert.match(html, /正文已收完/);
    assert.doesNotMatch(html, /恢复报告|报告恢复失败|SECRET_/);
  }
  const html = render([row({ operation: "pose_preview", stage: "decode", outcome: "success", category: null, bodyComplete: true })]);
  assert.match(html, /核对动作标记：完成/); assert.doesNotMatch(html, /不匹配/);
});
