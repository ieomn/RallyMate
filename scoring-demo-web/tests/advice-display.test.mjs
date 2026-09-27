import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

async function load(relative) {
  const source = fs.readFileSync(new URL(relative, import.meta.url), "utf8");
  const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText
    .replace(/from "(react\/jsx-runtime)"/g, (_match, name) => `from ${JSON.stringify(import.meta.resolve(name))}`);
  return import(`data:text/javascript;base64,${Buffer.from(js).toString("base64")}`);
}
const { adviceContextKey, adviceForContext, adviceNotice } = await load("../app/lib/advice-display.ts");
const { default: AdviceResult } = await load("../app/AdviceResult.tsx");
const render = response => renderToStaticMarkup(createElement(AdviceResult, { technique: "接发", response }));

test("advice cannot carry over to another job, family or an unassociated video", () => {
  const response = { source: "mimo", advice: { summary: "当前底线说明" } };
  const saved = { contextKey: adviceContextKey("job-a", "底线击球"), response };
  assert.equal(adviceForContext(saved, adviceContextKey("job-a", "底线击球")), response);
  assert.equal(adviceForContext(saved, adviceContextKey("job-a", "接发")), null);
  assert.equal(adviceForContext(saved, adviceContextKey("job-b", "底线击球")), null);
  assert.equal(adviceForContext(saved, adviceContextKey(undefined, "底线击球")), null);
  assert.equal(adviceForContext(null, saved.contextKey), null);
});

test("empty advice has no fabricated personal assessment or drill plan", () => {
  const html = render(null);
  assert.match(html, /先确认识别依据/);
  assert.match(html, /未识别不等于动作错误/);
  assert.doesNotMatch(html, /通用练习提示|分钟|你的动作|训练安排/);
});

test("unrecognized action renders evidence limits and capture steps without a drill plan", () => {
  const explanation = "当前未有效识别接发，无法判断动作是否正确。";
  const html = render({
    source: "evidence_fallback", evidenceStatus: "insufficient_evidence",
    evidence: { status: "insufficient_evidence", explanation, availableFacts: [], limitations: ["未识别不代表动作有问题。"], nextSteps: ["补充来球与挥拍的连续画面。"] },
    advice: { summary: explanation, strengths: [], nextSteps: ["补充来球与挥拍的连续画面。"], drills: [], safetyNotes: [], confidence: "low" },
  });
  assert.match(html, /识别证据说明/);
  assert.match(html, /尚不能判断的部分/);
  assert.match(html, /如何补充证据/);
  assert.match(html, /未识别不代表动作有问题/);
  assert.equal(html.split(explanation).length - 1, 1);
  assert.doesNotMatch(html, /通用练习提示|本次可确认的内容|分钟/);
});

test("partial motion keeps missing-phase and unconfirmed-contact qualifications visible", () => {
  const html = render({
    source: "fallback", providerStatus: "disabled",
    evidence: { status: "motion_only", explanation: "只有二维运动，阶段不完整。", availableFacts: ["已测到加速挥拍。"], limitations: ["缺少准备和随挥证据。", "球拍触球未确认。"], partialMotion: true },
    advice: { summary: "语言模型未启用。", strengths: [], nextSteps: ["回放核对已有运动。"], drills: [], safetyNotes: [], confidence: "low" },
  });
  for (const text of ["本次可确认的内容", "已测到加速挥拍", "尚不能判断的部分", "缺少准备和随挥证据", "球拍触球未确认", "阶段不完整"]) assert.ok(html.includes(text));
  assert.doesNotMatch(html, /已确认击球|完整动作已识别/);
});

test("provider unavailability and insufficient evidence have distinct source notices", () => {
  assert.match(adviceNotice({ source: "evidence_fallback" }), /未调用语言模型/);
  assert.match(adviceNotice({ source: "fallback", providerStatus: "disabled" }), /尚未启用/);
  assert.match(adviceNotice({ source: "fallback", providerStatus: "billing_configuration_required" }), /配置尚未就绪/);
  assert.match(adviceNotice({ source: "fallback", providerStatus: "provider_http_503" }), /未将服务故障解释为动作问题/);
  assert.match(adviceNotice({ source: "safety_fallback" }), /不是视频动作评价/);
});
