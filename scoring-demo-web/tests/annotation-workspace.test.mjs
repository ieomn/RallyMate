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
  let js = ts.transpileModule(fs.readFileSync(file, "utf8"), { fileName: file.pathname, compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  js = js.replace(/^import\s+["'][^"']+\.css["'];?\s*$/gm, "");
  js = js.replace(/import (\w+) from "([^"\n]+\.json)";?/g, (_match, binding, name) => `const ${binding} = ${fs.readFileSync(new URL(name, file), "utf8")};`);
  js = js.replace(/from "([^"]+)"/g, (_match, name) => {
    const local = new URL(name.endsWith(".json") ? name : `${name}.ts`, file);
    return `from ${JSON.stringify(name.startsWith(".") ? compiledUrl(fs.existsSync(local) ? local : new URL(`${name}.tsx`, file)) : import.meta.resolve(name))}`;
  });
  const url = `data:text/javascript;base64,${Buffer.from(js).toString("base64")}`;
  cache.set(file.href, url); return url;
}
const { annotationHref, annotationJobId, loadAnnotationReport, annotationSeekSeconds } = await import(compiledUrl(new URL("../app/lib/annotation-navigation.ts", import.meta.url)));
const { createApiClient } = await import(compiledUrl(new URL("../app/lib/api-client.ts", import.meta.url)));
const { default: Workspace } = await import(compiledUrl(new URL("../app/annotate/AnnotationWorkspace.tsx", import.meta.url)));
const { default: Panel } = await import(compiledUrl(new URL("../app/TechnicalReviewPanel.tsx", import.meta.url)));
const catalog = JSON.parse(fs.readFileSync(new URL("../app/data/scoring-reference.json", import.meta.url), "utf8"));
const jobId = "annotation-job-001", origin = "https://same-site.test";
const resultOf = () => ({ job_id: jobId, status: "ready", runtime: { annotated_video_compatibility: { timing_preserved: true } } });
const clientOf = (overrides = {}) => ({ getJob: async () => ({ id: jobId, status: "completed" }), getDemoResult: async () => resultOf(), getTrajectory: async () => ({ job_id: jobId, source: { start_timestamp_ms: 1200 } }), ...overrides });

test("annotation task selection only accepts bounded IDs and unambiguous same-site report links", () => {
  for (const value of [jobId, ` ${jobId} `, `/?view=analysis&job=${jobId}`, `?job=${jobId}`, `${origin}/?job=${jobId}`, `${origin}/annotate?job=${jobId}`]) assert.equal(annotationJobId(value, origin), jobId);
  for (const value of ["", "short", "../untrusted", "a".repeat(81), `https://other-site.test/?job=${jobId}`, `//other-site.test/?job=${jobId}`, `${origin}/v1/jobs/${jobId}`, `${origin}/?job=${jobId}&job=other-job`, `${origin}/?job=bad%2Fpath`, `javascript:alert(1)`, `https://user:password@same-site.test/?job=${jobId}`]) assert.equal(annotationJobId(value, origin), null);
  assert.equal(annotationHref(jobId), `/annotate?job=${jobId}`);
  for (const value of [null, undefined, "../bad", ""]) assert.equal(annotationHref(value), "/annotate");
});

test("annotation loader reads canonical current report endpoints without starting inference or following returned links", async () => {
  const calls = [];
  const client = createApiClient({ baseUrl: origin }, async (url, init) => {
    calls.push({ url: new URL(url), init });
    const path = new URL(url).pathname;
    if (path.endsWith("/demo-result")) return Response.json(resultOf());
    if (path.endsWith("/trajectory")) return Response.json({ job_id: jobId, source: { start_timestamp_ms: 1200 } });
    return Response.json({ id: jobId, status: "succeeded", demo_result_url: "http://private.invalid/v1/jobs/wrong-job/demo-result" });
  });
  const report = await loadAnnotationReport(client, jobId, new AbortController().signal);
  assert.equal(report.result.job_id, jobId);
  assert.equal(report.videoTimeOriginMs, 1200);
  assert.deepEqual(calls.map(call => call.url.pathname).sort(), [`/v1/jobs/${jobId}`, `/v1/jobs/${jobId}/demo-result`, `/v1/jobs/${jobId}/trajectory`].sort());
  for (const call of calls) { assert.equal(call.init.method, "GET"); assert.ok(call.init.signal); }
  for (const call of calls.filter(call => !call.url.pathname.endsWith("/trajectory"))) assert.equal(call.url.searchParams.get("report_contract"), "current");
});

test("unfinished and mismatched jobs never proceed to annotation report reads", async () => {
  for (const job of [{ id: "wrong-job", status: "completed" }, ...["queued", "running", "failed", "cancelled", "unknown"].map(status => ({ id: jobId, status }))]) {
    let reportReads = 0;
    const client = clientOf({ getJob: async () => job, getDemoResult: async () => { reportReads++; return resultOf(); } });
    await assert.rejects(loadAnnotationReport(client, jobId, new AbortController().signal), /任务|视频/);
    assert.equal(reportReads, 0);
  }
  for (const result of [{ ...resultOf(), job_id: "wrong-job" }, { ...resultOf(), status: "processing" }]) {
    await assert.rejects(loadAnnotationReport(clientOf({ getDemoResult: async () => result }), jobId, new AbortController().signal), /报告与所选视频不一致/);
  }
});

test("optional replay failure preserves the report but disables unsafe time jumps", async () => {
  const signal = new AbortController().signal;
  for (const getTrajectory of [async () => { throw new Error("offline"); }, async () => ({ job_id: "wrong-job", source: { start_timestamp_ms: 0 } }), async () => ({ job_id: jobId, source: { start_timestamp_ms: -1 } })]) {
    const report = await loadAnnotationReport(clientOf({ getTrajectory }), jobId, signal);
    assert.equal(report.result.job_id, jobId); assert.equal(report.videoTimeOriginMs, null);
    assert.equal(annotationSeekSeconds(report, jobId, 3000), null);
  }
  const report = await loadAnnotationReport(clientOf(), jobId, signal);
  assert.equal(annotationSeekSeconds(report, jobId, 3200), 2);
  for (const [id, timestamp] of [["wrong-job", 3200], [undefined, 3200], [jobId, -1], [jobId, 1000], [jobId, NaN], [jobId, "3200"]]) assert.equal(annotationSeekSeconds(report, id, timestamp), null);
  report.result.runtime.annotated_video_compatibility.timing_preserved = false;
  assert.equal(annotationSeekSeconds(report, jobId, 3200), null);
});

test("a cancelled old request cannot return a completed annotation report even when a transport ignores abort", async () => {
  const controller = new AbortController();
  let finish;
  const delayed = new Promise(resolve => { finish = resolve; });
  const pending = loadAnnotationReport(clientOf({ getDemoResult: async () => delayed }), jobId, controller.signal);
  await Promise.resolve();
  controller.abort(); finish(resultOf());
  await assert.rejects(pending, { name: "AbortError" });
  const alreadyAborted = new AbortController(); alreadyAborted.abort();
  let requests = 0;
  await assert.rejects(loadAnnotationReport(clientOf({ getJob: async () => { requests++; return {}; } }), jobId, alreadyAborted.signal), { name: "AbortError" });
  assert.equal(requests, 0);
});

test("standalone workspace has its own task loader and guarded manual review form", () => {
  const html = renderToStaticMarkup(createElement(Workspace));
  assert.match(html, /标注工作台/); assert.match(html, /任务编号或本站报告链接/); assert.match(html, /返回训练空间/);
  assert.doesNotMatch(html, /type="file"|开始分析|保存这条评审/);
  const review = renderToStaticMarkup(createElement(Panel, { result: resultOf(), catalog }));
  assert.match(review, /评审人/); assert.match(review, /选择人工技术等级/); assert.match(review, /导出评审/);
  assert.match(review, /type="submit" disabled="">保存这条评审/);
});
