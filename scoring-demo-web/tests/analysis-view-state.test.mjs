import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import ts from "typescript";
const source = fs.readFileSync(new URL("../app/lib/analysis-view-state.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const { analysisViewState } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);
const state = (input) => analysisViewState({ uploadState: "processing", hasResult: false, hasReplay: false, ...input });

test("analyzer completion is still report reading until data is available", () => {
  assert.equal(state({ jobStatus: "succeeded" }).phase, "reading");
  assert.equal(state({ jobStatus: "succeeded" }).showNavigation, false);
  assert.equal(state({ jobStatus: "succeeded", hasResult: true }).phase, "partial");
  assert.equal(state({ jobStatus: "succeeded", uploadState: "complete", hasResult: true }).phase, "complete");
});
test("missing historical job does not render an empty report or retry an absent task", () => {
  const missing = state({ uploadState: "error", missingJob: true });
  assert.equal(missing.phase, "missing"); assert.equal(missing.showNavigation, false); assert.equal(missing.showReport, false);
  assert.equal(missing.canResumeRead, false); assert.match(missing.recoveryDetail, /关注点仍保存在此浏览器/);
});
test("read failure preserves partial evidence and does not call completed inference failed", () => {
  const partial = state({ uploadState: "error", jobStatus: "succeeded", hasResult: true });
  assert.equal(partial.phase, "read-error"); assert.equal(partial.showReport, true); assert.equal(partial.canResumeRead, true);
  assert.match(partial.recoveryTitle, /分析已完成/);
});
test("cancelled and failed jobs keep available replay without report navigation", () => {
  for (const jobStatus of ["cancelled", "failed"]) {
    const stopped = state({ uploadState: jobStatus === "failed" ? "error" : "cancelled", jobStatus, hasReplay: true });
    assert.equal(stopped.showReplayOnly, true); assert.equal(stopped.showNavigation, false); assert.equal(stopped.canResumeRead, false);
  }
});
test("upload, analysis, partial completion and idle are distinct", () => {
  assert.equal(state({ uploadState: "uploading" }).phase, "uploading");
  assert.equal(state({ jobStatus: "running" }).phase, "analyzing");
  assert.equal(state({ uploadState: "complete", hasResult: true, warning: "overlay unavailable" }).phase, "partial");
  assert.equal(state({ uploadState: "ready" }).phase, "idle");
});
