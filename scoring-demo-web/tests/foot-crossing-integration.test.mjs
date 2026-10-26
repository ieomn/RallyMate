import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import test from "node:test";
import ts from "typescript";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

const scoreLab = fs.readFileSync(new URL("../app/ScoreLab.tsx", import.meta.url), "utf8");
const ast = ts.createSourceFile("ScoreLab.tsx", scoreLab, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
function actualFunction(name) {
  let found;
  function visit(node) {
    if (ts.isFunctionDeclaration(node) && node.name?.text === name) found = node;
    ts.forEachChild(node, visit);
  }
  visit(ast);
  assert.ok(found, `actual implementation ${name} exists`);
  return found.getText(ast).replace(/^export\s+/, "");
}
const guardCode = ts.transpileModule(actualFunction("candidateOnlineJobId"), {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None },
}).outputText;
const candidateOnlineJobId = vm.runInNewContext(`${guardCode}; candidateOnlineJobId`);

test("candidate discovery is limited to a completed matching online report", () => {
  const result = { job_id: "job-live" };
  for (const evidence of [
    { mode: "live", jobId: "job-live", result },
    { mode: "live", candidateSource: "imported", jobId: "job-live", result },
    { candidateSource: "online", jobId: "job-live", result: { job_id: "job-other" } },
    { candidateSource: "online", jobId: "job-live", result: null },
  ]) assert.equal(candidateOnlineJobId(evidence, false), null);
  const online = { candidateSource: "online", jobId: "job-live", result };
  assert.equal(candidateOnlineJobId(online, true), null);
  assert.equal(candidateOnlineJobId(online, false), "job-live");
});

// Execute the actual nested import function with UI setters replaced by a
// recorder. Fixtures below are synthetic imports, not online service evidence.
async function importActual(data) {
  let evidence, importState, monitored = 0, invalidated = 0;
  const scope = { busy: false, analysisRunRef: { current: 0 }, EMPTY_EVIDENCE: { mode: "demo" },
    navigate() {}, invalidateAnalysisRun() { invalidated++; scope.analysisRunRef.current++; },
    readReportJson: async () => data, setVideoFile() {}, setUploadError() {}, setImportedSummary() {},
    setEvidence(value) { evidence = value; }, setImportState(value) { importState = value; },
    setJob() {}, setUploadState() {}, setScoreContext() {}, monitorJob() { monitored++; },
    setCurrentJournalRecordId() {}, visitReport: visit => `${visit.source}:${visit.jobId}`,
    measurementResultFromSummary: summary => ({ job_id: summary.job_id }),
  };
  const functions = ["isStage1Summary", "isTechniqueAssessment", "extractTechniqueAssessment", "assertImportedJobBinding", "importSummary"];
  const code = ts.transpileModule(functions.map(actualFunction).join("\n"), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None },
  }).outputText;
  const run = vm.runInNewContext(`${code}; importSummary`, scope);
  await run({ target: { files: [{ name: "synthetic-import.json" }], value: "chosen" } });
  assert.equal(importState?.status, "success", importState?.message);
  assert.equal(monitored, 0); assert.equal(invalidated, 1);
  return evidence;
}

test("all self-contained import branches refuse a forged online marker and reuse of a job ID", async () => {
  const assessment = { techniques: [], assessment_version: "test", registry_version: "test", job_id: "same-job-123" };
  const imports = [
    { schemaVersion: "rallymate-practice-report/1", jobId: "same-job-123", candidateSource: "online",
      result: { job_id: "same-job-123", candidateSource: "online" } },
    { job_id: "same-job-123", candidateSource: "online", technique_assessment: assessment,
      result: { job_id: "same-job-123", candidateSource: "online" } },
    { job_id: "same-job-123", candidateSource: "online", processing: {} },
  ];
  for (const data of imports) {
    const evidence = await importActual(data);
    assert.equal(evidence.jobId, "same-job-123");
    assert.equal(evidence.candidateSource, undefined);
    assert.equal(candidateOnlineJobId(evidence, false), null);
  }
});

const modules = new Map();
function compiledUrl(file) {
  // Keep source JSON imports shared with production, without a generated copy.
  if (file.pathname.endsWith(".json")) return `data:text/javascript;base64,${Buffer.from(`export default ${fs.readFileSync(file, "utf8")}`).toString("base64")}`;
  if (modules.has(file.href)) return modules.get(file.href);
  let js = ts.transpileModule(fs.readFileSync(file, "utf8"), {
    fileName: file.pathname,
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  js = js.replace(/from "([^"]+)"/g, (_match, name) => {
    const local = new URL(name.endsWith(".json") ? name : `${name}.ts`, file);
    return `from ${JSON.stringify(name.startsWith(".") ? compiledUrl(fs.existsSync(local) ? local : new URL(`${name}.tsx`, file)) : import.meta.resolve(name))}`;
  });
  const url = `data:text/javascript;base64,${Buffer.from(js).toString("base64")}`;
  modules.set(file.href, url); return url;
}
const { default: IndicatorAssessmentPanel, IndicatorCandidateEvidence } = await import(compiledUrl(new URL("../app/IndicatorAssessmentPanel.tsx", import.meta.url)));
const { default: TrainingReplayPanel } = await import(compiledUrl(new URL("../app/TrainingReplayPanel.tsx", import.meta.url)));
const syntheticAssessment = indicatorId => ({ version: "word-indicator-assessment-v1.0.0",
  source_binding: { reference_version: "synthetic-test", source_documents: [] }, observations: [],
  indicators: [{ indicator_id: indicatorId, name_zh: "合成测试指标", event_code: "FS03", stage_code: "FS03",
    status: "source_boundaries_missing", explanation_zh: "尚未完成正式阶段认定", calculation_zh: "", observation_ids: [], blockers: [],
    source: { document_id: "CARD-FS", sha256: "a".repeat(64), table: null, rubric_sha256: "b".repeat(64) },
    grade: { status: "calibration_required", label_zh: "暂不生成等级", reason_zh: "完整行为未验证" } }],
});

test("only FS03-M03 gains a collapsed independent entry without changing grade or observation data", () => {
  let requests = 0;
  const context = { jobId: "job-live", sessionKey: "job-live:1", signal: new AbortController().signal,
    load: async () => { requests++; throw new Error("collapsed must not request"); }, replay: null };
  for (const id of ["FS03-M02", "FS03-M03", "FS03-M04"]) {
    const value = syntheticAssessment(id), original = structuredClone(value);
    const html = renderToStaticMarkup(createElement(IndicatorAssessmentPanel, { value, candidateContext: context }));
    assert.equal(html.includes('aria-label="独立图像换序线索"'), false, "outer details starts closed");
    const open = renderToStaticMarkup(createElement(IndicatorCandidateEvidence, { indicatorId: id, expanded: true, context }));
    assert.equal(open.includes('aria-label="独立图像换序线索"'), id === "FS03-M03");
    if (id === "FS03-M03") assert.match(open, /aria-expanded="false"/);
    assert.equal(renderToStaticMarkup(createElement(IndicatorCandidateEvidence, { indicatorId: id, expanded: false, context })), "");
    assert.match(html, /0 项有观测/); assert.match(html, /技术等级尚未判定/);
    assert.deepEqual(value, original);
  }
  assert.equal(requests, 0);
});

test("the replay container labels the selected media while retaining old callers", () => {
  const annotated = renderToStaticMarkup(createElement(TrainingReplayPanel, { jobId: "job-live", mediaKind: "annotated" }, "player"));
  assert.match(annotated, /标注回放/); assert.doesNotMatch(annotated, /原视频|原片/);
  const unknown = renderToStaticMarkup(createElement(TrainingReplayPanel, { mediaKind: null }, "player"));
  assert.match(unknown, /训练回放/); assert.doesNotMatch(unknown, /原视频|原片/);
  const legacy = renderToStaticMarkup(createElement(TrainingReplayPanel, null, "player"));
  assert.match(legacy, /原视频 · 动作识别/);
});
