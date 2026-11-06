import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

async function load(relative) {
  const source = fs.readFileSync(new URL(relative, import.meta.url), "utf8");
  const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText
    .replace(/^import\s+["'][^"']+\.css["'];?\s*$/gm, "")
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
  assert.doesNotMatch(html, /未识别不等于动作错误/);
  assert.doesNotMatch(html, /通用练习提示|分钟|你的动作|训练安排/);
});

test("unrecognized action keeps neutral empty state and capture steps without diagnostic wording", () => {
  const explanation = "当前未有效识别接发，无法判断动作是否正确。";
  const html = render({
    source: "evidence_fallback", evidenceStatus: "insufficient_evidence",
    evidence: { status: "insufficient_evidence", explanation, availableFacts: [], limitations: ["未识别不代表动作有问题。"], nextSteps: ["补充来球与挥拍的连续画面。"] },
    advice: { summary: explanation, strengths: [], nextSteps: ["补充来球与挥拍的连续画面。"], drills: [], safetyNotes: [], confidence: "low" },
  });
  assert.match(html, /识别证据说明/);
  assert.match(html, /暂无可展示的结果/);
  assert.doesNotMatch(html, /尚不能判断的部分/);
  assert.match(html, /如何补充证据/);
  assert.doesNotMatch(html, /未识别不代表动作有问题/);
  assert.equal(html.split(explanation).length - 1, 0);
  assert.doesNotMatch(html, /通用练习提示|本次可确认的内容|分钟/);
});

test("partial motion keeps measured facts and hides missing-phase and unconfirmed-contact diagnostics", () => {
  const html = render({
    source: "fallback", providerStatus: "disabled",
    evidence: { status: "motion_only", explanation: "只有二维运动，阶段不完整。", availableFacts: ["已测到加速挥拍。"], limitations: ["缺少准备和随挥证据。", "球拍触球未确认。"], partialMotion: true },
    advice: { summary: "语言模型未启用。", strengths: [], nextSteps: ["回放核对已有运动。"], drills: [], safetyNotes: [], confidence: "low" },
  });
  for (const text of ["本次可确认的内容", "已测到加速挥拍"]) assert.ok(html.includes(text));
  assert.doesNotMatch(html, /尚不能判断的部分|缺少准备和随挥证据|球拍触球未确认|阶段不完整/);
  assert.doesNotMatch(html, /已确认击球|完整动作已识别/);
});

test("provider unavailability and insufficient evidence have distinct source notices", () => {
  assert.match(adviceNotice({ source: "evidence_fallback" }), /未调用语言模型/);
  assert.match(adviceNotice({ source: "fallback", providerStatus: "disabled" }), /尚未启用/);
  assert.match(adviceNotice({ source: "fallback", providerStatus: "billing_configuration_required" }), /配置尚未就绪/);
  assert.match(adviceNotice({ source: "fallback", providerStatus: "provider_http_503" }), /未将服务故障解释为动作问题/);
  assert.match(adviceNotice({ source: "safety_fallback" }), /不是视频动作评价/);
});

test("measured coaching keeps source, practice and retest distinct without inventing a diagnosis", () => {
  const html = render({
    source: "fallback", providerStatus: "disabled",
    evidence: { status: "observed", explanation: "已测到相关动作。", availableFacts: ["旧摘要"], limitations: ["无法从这段视频判断球的落点。"] },
    advice: { summary: "本次练习说明", strengths: [], nextSteps: ["重复提示"], drills: [], safetyNotes: [], confidence: "low",
      coaching: { status: "measured_focus", focus: "让准备接上挥出", evidence: ["这段准备到挥出之间衔接较快。"], whyItMatters: "练习目标是把准备与挥出连接起来。",
        practice: ["先完成一次舒适准备，再连续挥出。"], checkNextTime: "用相同机位比较下一次动作。", startMs: 1200, endMs: 2500,
        reviewQuestion: "准备结束后，挥出是否连续？", assessmentScope: "可见动作的试验测量，需要回放复核。" } },
  });
  for (const text of ["让准备接上挥出", "具体依据", "为什么练这一项", "下一次怎么练", "怎样看出进步", "对应片段 1.2–2.5 秒", "回放时，重点核对"]) assert.ok(html.includes(text), text);
  assert.doesNotMatch(html, /MiMo 辅助整理|重复提示|旧摘要|动作错误|确诊|无法从这段视频判断球的落点|可见动作的试验测量，需要回放复核/);
  assert.equal(html.split("这段准备到挥出之间衔接较快。").length - 1, 1);
});

test("insufficient coaching stays a request for evidence and never a measured finding", () => {
  const html = render({ source: "evidence_fallback", advice: { summary: "暂无依据", strengths: [], nextSteps: [], drills: [], safetyNotes: [], confidence: "low",
    coaching: { status: "insufficient_evidence", focus: "还不能确定本次动作的问题", evidence: [], whyItMatters: "先确认完整动作是否入镜。", practice: ["补充完整动作画面。"], checkNextTime: "确认准备到随挥均可见。" } } });
  assert.match(html, /如何补充证据/);
  assert.match(html, /暂无可展示的结果/);
  assert.doesNotMatch(html, /还不能确定本次动作的问题/);
  assert.doesNotMatch(html, /基于动作测量的练习重点|具体依据|为什么练这一项/);
});
