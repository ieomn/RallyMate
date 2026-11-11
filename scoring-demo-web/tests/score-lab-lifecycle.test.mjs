import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import ts from "typescript";

// Exercise the real ScoreLab event handlers with deterministic hook state.
// Child panels stay opaque: their rendering and scoring are tested separately.
function load(relative, require) {
  const file = new URL(relative, import.meta.url);
  if (file.pathname.endsWith(".json")) return JSON.parse(fs.readFileSync(file, "utf8"));
  const code = ts.transpileModule(fs.readFileSync(file, "utf8"), { fileName: file.pathname,
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  const exports = {};
  new Function("require", "exports", code)(require ?? (name => load(new URL(name.endsWith(".json") ? name : `${name}.ts`, file))), exports);
  return exports;
}
const session = load("../app/lib/analysis-session.ts");
const viewState = load("../app/lib/analysis-view-state.ts");
const progress = load("../app/lib/analysis-progress.ts");
const diagnostics = load("../app/lib/request-diagnostics.ts");
const segmentedReport = load("../app/lib/segmented-report.ts");
const tennisAssessment = load("../app/lib/tennis-assessment.ts");
const tick = () => new Promise(resolve => setTimeout(resolve, 0));
const jobId = "lifecycle-job-1234";
const report = { job_id: jobId, status: "ready", training_evaluation: { score_0_to_100: 77 } };
function deferred() { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; }

function harness(t, clientOverrides = {}, { assessmentEffects = false, reportEffects = false } = {}) {
  const slots = []; let cursor = 0, effects = [];
  const hooks = {
    useState(initial) { const index = cursor++; if (!(index in slots)) slots[index] = typeof initial === "function" ? initial() : initial;
      return [slots[index], value => { slots[index] = typeof value === "function" ? value(slots[index]) : value; }]; },
    useRef(value) { const index = cursor++; return slots[index] ??= { current: value }; },
    useMemo(fn, deps) { const index = cursor++, previous = slots[index];
      if (!previous || deps.some((dep, i) => !Object.is(dep, previous.deps[i]))) slots[index] = { value: fn(), deps }; return slots[index].value; },
    useCallback(fn, deps) { return hooks.useMemo(() => fn, deps); },
    useEffect(effect, deps) {
      // Run the real assessment effect in its focused lifecycle regression;
      // the other page effects remain outside this deterministic harness.
      const source = effect.toString();
      if (!(assessmentEffects && source.includes(".getTennisAssessment(")) && !(reportEffects && /\.get(?:ReportOverview|Trajectory)\(/.test(source))) return;
      const index = cursor++, previous = slots[index];
      if (!previous || deps.some((dep, i) => !Object.is(dep, previous.deps[i]))) {
        slots[index] = { deps, cleanup: previous?.cleanup };
        effects.push(() => { slots[index].cleanup?.(); slots[index].cleanup = effect(); });
      }
    },
  };
  const journal = { ready: true, records: [], visitReport: () => `online:${jobId}` };
  const calls = { jobs: 0, cancel: 0 };
  const client = {
    getJob: async id => { calls.jobs++; return { id, status: "succeeded" }; },
    getJobStatus: async id => ({ id, status: "succeeded" }),
    getDemoResult: async () => report,
    getTrajectory: async id => ({ job_id: id }), getTechniqueAssessment: async id => ({ job_id: id }),
    cancelJob: async () => { calls.cancel++; }, ...clientOverrides,
  };
  const modules = {
    react: hooks, "react/jsx-runtime": { jsx: (type, props, key) => ({ type, props, key }), jsxs: (type, props, key) => ({ type, props, key }), Fragment: "fragment" },
    "./lib/analysis-session": session, "./lib/analysis-view-state": viewState, "./lib/analysis-progress": progress,
    "./lib/request-diagnostics": diagnostics,
    "./lib/tennis-assessment": tennisAssessment,
    "./lib/segmented-report": segmentedReport,
    "./lib/swing-kinematics": { swingVideoDurationMs: value => value?.input?.video?.duration_ms },
    "./lib/api-client": { createApiClient: () => client, RallyMateApiError: class extends Error {} },
    "./TrainingJournal": { useTrainingJournal: () => journal, journalRecordId: (source, id) => `${source}:${id}` },
    "./lib/training-report": { recentReports: value => value },
    "./lib/report-file": { readReportJson: async file => JSON.parse(await file.text()) },
    "./lib/workspace-navigation": { workspaceHref: (view, id) => `/?view=${view}${id ? `&job=${id}` : ""}`, readWorkspaceLocation: () => ({ view: "analysis" }) },
    "./lib/advice-display": { adviceContextKey: (id, technique) => `${id}:${technique}`, adviceForContext: (saved, key) => saved?.contextKey === key ? saved.response : null, adviceNotice: () => "建议已返回" },
    "./lib/training-guidance": { trainingGuidance: () => [], focusSelection: value => value }, "./lib/motion-analysis": { motionAnalysisOf: () => null },
  };
  const component = load("../app/ScoreLab.tsx", name => {
    if (name === "react" || name === "react/jsx-runtime") return modules[name];
    return new Proxy(modules[name] ?? {}, { get(target, key) { return key in target ? target[key] : key === "default" ? name.split("/").at(-1) : String(key); } });
  }).default;
  const store = new Map();
  t.mock.method(globalThis, "setInterval", () => 0);
  const oldWindow = globalThis.window, oldStorage = globalThis.localStorage;
  globalThis.window = { location: { pathname: "/", search: "", hash: "" }, history: { pushState() {}, replaceState() {} }, scrollTo() {} };
  globalThis.localStorage = { getItem: key => store.get(key) ?? null, setItem: (key, value) => store.set(key, value), removeItem: key => store.delete(key) };
  t.after(() => { slots.forEach(value => value?.cleanup?.()); globalThis.window = oldWindow; globalThis.localStorage = oldStorage; });
  let tree;
  const render = () => { cursor = 0; effects = []; tree = component(); effects.forEach(effect => effect()); return tree; };
  const nodes = value => !value || typeof value !== "object" ? [] : Array.isArray(value) ? value.flatMap(nodes) : [value, ...nodes(value.props?.children), ...nodes(value.props?.details), ...nodes(value.props?.coaching), ...nodes(value.props?.pagedActions)];
  const find = predicate => nodes(tree).find(predicate);
  const text = value => value == null || typeof value === "boolean" ? "" : typeof value !== "object" ? String(value) : Array.isArray(value) ? value.map(text).join("") : text(value.props?.children) + text(value.props?.details) + text(value.props?.coaching);
  const open = id => { tree.props.onNavigate("overview"); render(); find(node => node.type === "TrainingHome").props.onOpenReport(id); render(); };
  render();
  return { calls, render, find, findAll: predicate => nodes(tree).filter(predicate), text: () => text(tree), open,
    replaceResult: transform => { const i = slots.findIndex(value => value?.candidateSource === "online" && value?.result?.job_id); assert.ok(i >= 0); slots[i] = { ...slots[i], result: transform(slots[i].result) }; },
    button: label => find(node => node.type === "button" && text(node) === label), form: () => find(node => node.props?.className === "coach-form") };
}

test("overview object replacement preserves an active score read while source or duration changes restart it", async t => {
  const reads = [];
  const ui = harness(t, {
    getDemoResult: async () => ({ ...report, input: { video: { sha256: "a".repeat(64), duration_ms: 9000 } } }),
    getTennisAssessment: (id, signal) => { reads.push({ id, signal }); return new Promise(() => {}); },
  }, { assessmentEffects: true });
  ui.open(jobId); await tick(); ui.render();
  assert.equal(reads.length, 1);
  ui.replaceResult(value => ({ ...structuredClone(value), report_delivery: { mode: "segmented", overview: "complete" } })); ui.render();
  assert.equal(reads.length, 1); assert.equal(reads[0].signal.aborted, false);
  ui.replaceResult(value => ({ ...value, input: { video: { ...value.input.video, sha256: "b".repeat(64) } } })); ui.render();
  assert.equal(reads.length, 2); assert.equal(reads[0].signal.aborted, true);
  ui.replaceResult(value => ({ ...value, input: { video: { ...value.input.video, duration_ms: 10000 } } })); ui.render();
  assert.equal(reads.length, 3); assert.equal(reads[1].signal.aborted, true);
  assert.equal(reads[2].signal.aborted, false);
});

test("processing displays counted stage progress rather than the weighted server total", async t => {
  const ui = harness(t, { getJob: async id => ({ id, status: "running", progress: {
    phase: "inference", percent: 44, processed_frames: 50, total_frames: 100,
  } }) });
  ui.open(jobId); await tick(); ui.render();
  const bar = ui.find(node => node.props?.role === "progressbar");
  assert.equal(bar.props["aria-valuenow"], 50);
  assert.equal(bar.props.children.props.style.width, "50%");
  assert.match(ui.text(), /50%当前阶段进度/);
  assert.match(ui.text(), /动作识别完成后还需整理报告/);
  ui.button("停止等待").props.onClick(); await tick();
});

test("an unclaimed task labels the step as waiting and preserves both waiting controls", async t => {
  const created_at = "2026-11-07T09:26:00Z";
  const ui = harness(t, { getJob: async id => ({ id, status: "queued", created_at, updated_at: created_at, started_at: null,
    queue_position: 1, executor: { status: "unavailable", available: false, heartbeat_at: null } }),
    submitVideo: () => assert.fail("opening a waiting task cannot resubmit it"),
  });
  ui.open(jobId); await tick(); ui.render();
  assert.match(ui.text(), /上传视频等待分析整理报告/);
  assert.match(ui.text(), /尚未开始推理/);
  assert.match(ui.text(), /分析执行服务暂不可用/);
  assert.doesNotMatch(ui.text(), /服务器已处理/);
  assert.ok(ui.button("停止等待")); assert.ok(ui.button("取消分析"));
  assert.equal(ui.find(node => node.props?.role === "progressbar").props["aria-valuenow"], undefined);
  ui.button("停止等待").props.onClick(); await tick();
  assert.equal(ui.calls.cancel, 0);
});

test("an uncounted stage stays indeterminate without presenting the weighted total", async t => {
  const ui = harness(t, { getJob: async id => ({ id, status: "running", progress: {
    phase: "preparing_replay", percent: 85, processed_frames: 100, total_frames: 100,
  } }) });
  ui.open(jobId); await tick(); ui.render();
  const bar = ui.find(node => node.props?.role === "progressbar");
  assert.equal(bar.props["aria-valuenow"], undefined);
  assert.equal(bar.props["aria-valuetext"], "正在准备视频回放");
  assert.match(bar.props.className, /is-organizing/);
  assert.equal(ui.find(node => node.props?.className === "processing-percent-scope"), undefined);
  assert.match(ui.text(), /上传视频分析动作整理报告/);
  ui.button("停止等待").props.onClick(); await tick();
});

test("a ready report unlocks report actions while evidence is pending, and switching discards late evidence", async t => {
  const optional = deferred(); let signal;
  const ui = harness(t, { getTrajectory: (_id, options) => { signal = options.signal; return optional.promise; } });
  ui.open(jobId); await tick();
  const tree = ui.render();
  assert.equal(tree.props.busy, false); assert.match(ui.text(), /正在补充画面证据/);
  assert.ok(ui.form()); assert.ok(ui.find(node => node.type === "ReportExportActions"));
  tree.props.onNewAnalysis(); ui.render();
  assert.equal(signal.aborted, true);
  optional.resolve({ job_id: jobId }); await tick(); ui.render();
  assert.match(ui.text(), /开始一次新的训练分析/); assert.equal(ui.find(node => node.type === "LiveResults"), undefined);
});

test("primary report waits alone then unlocks the UI before supplementary requests finish", async t => {
  const primary = deferred(), optional = deferred(); let reads = 0;
  const ui = harness(t, {
    getDemoResult: () => primary.promise,
    getTrajectory: () => { reads++; assert.equal(ui.render().props.busy, false); return optional.promise; },
    getTechniqueAssessment: async () => { reads++; assert.equal(ui.render().props.busy, false); return { job_id: jobId }; },
  });
  ui.open(jobId); await tick();
  assert.equal(ui.render().props.busy, true); assert.equal(reads, 0);
  primary.resolve(report); await tick();
  assert.equal(reads, 2); assert.equal(ui.render().props.busy, false);
  assert.ok(ui.form()); assert.ok(ui.find(node => node.type === "ReportExportActions"));
  assert.match(ui.text(), /正在补充画面证据/);
  optional.resolve({ job_id: jobId }); await tick();
  assert.equal(ui.render().props.busy, false);
});

test("reopening a failed same report reconnects; duplicate clicks while waiting do not duplicate requests", async t => {
  let reads = 0; const pending = deferred();
  const ui = harness(t, { getDemoResult: async () => { if (++reads === 1) throw Object.assign(new Error("unavailable report"), { status: 422 }); return pending.promise; } });
  ui.open(jobId); await tick(); ui.render();
  assert.match(ui.text(), /继续读取结果/);
  ui.open(jobId); await tick(); ui.render(); assert.equal(reads, 2); assert.equal(ui.calls.jobs, 2);
  ui.open(jobId); await tick(); assert.equal(reads, 2); assert.equal(ui.calls.jobs, 2);
  pending.resolve(report); await tick(); ui.render(); assert.equal(ui.render().props.busy, false);
});

test("an expired preparation wait offers the same report again without an interruption warning or upload", async t => {
  const ids = []; let submissions = 0;
  const ui = harness(t, {
    getDemoResult: async id => {
      ids.push(id);
      if (ids.length === 1) throw Object.assign(new Error("still preparing"), { status: 425, payload: { detail: { code: "report_still_preparing" } } });
      return report;
    },
    submitVideo: async () => { submissions++; assert.fail("resuming must not submit the video again"); },
  });
  ui.open(jobId); await tick(); ui.render();
  assert.equal(ui.render().props.busy, false);
  assert.match(ui.text(), /分析已完成，报告仍在整理/); assert.match(ui.text(), /无需重新上传/);
  assert.doesNotMatch(ui.text(), /读取中断|本次分析需要处理/);
  assert.equal(ui.find(node => node.props?.className === "product-recovery").props.role, "status");
  ui.button("继续读取结果").props.onClick(); await tick(); ui.render();
  assert.deepEqual(ids, [jobId, jobId]); assert.equal(ui.calls.jobs, 2); assert.equal(submissions, 0); assert.equal(ui.calls.cancel, 0);
  assert.ok(ui.find(node => node.type === "LiveResults"));
  assert.doesNotMatch(ui.text(), /报告仍在整理/);
});

test("a real server failure keeps the interrupted-read recovery instead of the preparing notice", async t => {
  const original = Object.assign(new Error("builder deadline"), { status: 504, payload: { detail: { code: "report_preparation_timeout" } } });
  const ui = harness(t, { getDemoResult: async () => { throw original; } });
  ui.open(jobId); await tick(); ui.render();
  assert.match(ui.text(), /报告读取中断/); assert.match(ui.text(), /服务器整理报告已超时/);
  assert.doesNotMatch(ui.text(), /报告仍在整理/);
  assert.equal(ui.find(node => node.props?.className === "product-recovery").props.role, "alert");
  assert.ok(ui.button("继续读取结果"));
});

test("stopping report waiting preserves the job and can resume without cancelling the server", async t => {
  let reads = 0, firstSignal;
  const ui = harness(t, { getDemoResult: (_id, options) => {
    if (++reads > 1) return Promise.resolve(report);
    firstSignal = options.signal;
    return new Promise((_resolve, reject) => options.signal.addEventListener("abort", () => reject(options.signal.reason), { once: true }));
  } });
  ui.open(jobId); await tick(); ui.render(); ui.button("停止等待").props.onClick(); await tick();
  assert.equal(firstSignal.aborted, true); assert.equal(ui.calls.cancel, 0); assert.equal(ui.render().props.busy, false);
  assert.match(ui.text(), /服务器报告已保留/);
  ui.button("继续读取结果").props.onClick(); await tick(); ui.render();
  assert.equal(reads, 2); assert.equal(ui.calls.cancel, 0); assert.ok(ui.find(node => node.type === "LiveResults"));
});

test("a stalled initial status read can be stopped without pretending the server job stopped", async t => {
  let signal;
  const ui = harness(t, { getJob: (_id, input) => { signal = input; return new Promise((_resolve, reject) => input.addEventListener("abort", () => reject(input.reason), { once: true })); } });
  ui.open(jobId); await tick(); ui.render(); ui.button("停止等待").props.onClick(); await tick(); ui.render();
  assert.equal(signal.aborted, true); assert.equal(ui.calls.cancel, 0); assert.equal(ui.render().props.busy, false);
  assert.match(ui.text(), /服务器任务继续运行/); assert.ok(ui.button("继续读取结果"));
});

test("advice timeout unlocks retry, and changing the report cancels stale advice", async t => {
  const deadlines = [];
  t.mock.method(AbortSignal, "timeout", ms => { const controller = new AbortController(); deadlines.push({ ms, controller }); return controller.signal; });
  const requests = [];
  t.mock.method(globalThis, "fetch", (_url, options) => {
    const request = deferred(); requests.push({ ...request, signal: options.signal });
    options.signal.addEventListener("abort", () => request.reject(options.signal.reason), { once: true }); return request.promise;
  });
  const ui = harness(t); ui.open(jobId); await tick(); ui.render();
  ui.form().props.onSubmit({ preventDefault() {} }); await tick(); ui.render();
  assert.equal(ui.button("正在整理…").props.disabled, true);
  deadlines.findLast(row => row.ms === 55000).controller.abort(new DOMException("deadline", "TimeoutError"));
  await tick(); ui.render(); assert.match(ui.text(), /建议读取超时/); assert.equal(ui.button("查看解释与建议").props.disabled, false);
  ui.form().props.onSubmit({ preventDefault() {} }); await tick(); ui.render().props.onNewAnalysis(); await tick();
  assert.equal(requests[1].signal.aborted, true); ui.render(); assert.doesNotMatch(ui.text(), /建议读取超时/);
});

test("the primary coaching slot appears once and discards results from a prior job even if fetch ignores abort", async t => {
  const pending = deferred(); let adviceSignal;
  t.mock.method(globalThis, "fetch", (_url, options) => { adviceSignal = options.signal; return pending.promise; });
  const ui = harness(t, { getDemoResult: async id => ({ ...report, job_id: id }) });
  ui.open(jobId); await tick(); ui.render();
  assert.equal(ui.findAll(node => node.props?.id === "coach").length, 1);
  assert.equal(ui.find(node => node.type === "LiveResults").props.coaching.props.id, "coach");
  assert.equal(ui.find(node => node.type === "AdviceResult").props.jobId, jobId);
  ui.form().props.onSubmit({ preventDefault() {} }); await tick();
  ui.open("different-job-4567"); await tick(); ui.render();
  assert.equal(adviceSignal.aborted, true);
  pending.resolve({ ok: true, json: async () => ({ advice: { summary: "旧任务的训练结论" } }) });
  await tick(); ui.render();
  assert.equal(ui.findAll(node => node.props?.id === "coach").length, 1);
  assert.equal(ui.find(node => node.type === "AdviceResult").props.jobId, "different-job-4567");
  assert.equal(ui.find(node => node.type === "AdviceResult").props.response, null);
  assert.doesNotMatch(ui.text(), /旧任务的训练结论|建议已返回/);
});

test("imported report coaching cannot reuse online advice or submit an API request", async t => {
  let requests = 0;
  t.mock.method(globalThis, "fetch", async () => { requests++; return { ok: true, json: async () => ({ advice: { summary: "在线报告的说明" } }) }; });
  const ui = harness(t); ui.open(jobId); await tick(); ui.render();
  ui.form().props.onSubmit({ preventDefault() {} }); await tick(); ui.render();
  assert.equal(requests, 1);
  assert.equal(ui.find(node => node.type === "AdviceResult").props.response.advice.summary, "在线报告的说明");
  const content = JSON.stringify({ schemaVersion: "rallymate-practice-report/1", jobId, result: report });
  await ui.find(node => node.type === "input" && node.props.accept === "application/json,.json").props.onChange({ target: { files: [{ name: "saved-report.json", size: content.length, text: async () => content }] } });
  ui.render();
  assert.match(ui.text(), /离线导入的报告/);
  assert.equal(ui.find(node => node.type === "AdviceResult"), undefined);
  assert.equal(ui.button("查看解释与建议").props.disabled, true);
  await ui.form().props.onSubmit({ preventDefault() {} }); await tick(); ui.render();
  assert.equal(requests, 1, "even direct form submission cannot invoke advice for imported evidence");
});

test("online summary restores full results and replay while fragment evidence stays on demand", async t => {
  const summary = reportSummary(jobId);
  const ui = harness(t, { getReportSummary: async () => summary, getDemoResult: () => assert.fail("full report read"),
    getTrajectory: () => assert.fail("unrequested trajectory"), getTechniqueAssessment: () => assert.fail("full assessment read") });
  ui.open(jobId); await tick(); ui.render();
  assert.equal(ui.render().props.busy, false);
  const results = ui.find(node => node.type === "LiveResults");
  assert.ok(results);
  assert.ok(ui.find(node => node.props?.className === "product-report-nav"));
  assert.equal(results.props.replay.type, "TrainingReplayPanel");
  assert.equal(results.props.replay.props.children.type.name, "ReplayPanel");
  assert.equal(results.props.result.original_video, summary.original_video);
  assert.equal(results.props.pagedActions, undefined);
  assert.ok(ui.find(node => node.type === "ReportExportActions"));
  assert.match(ui.text(), /保存已加载报告/);
  results.props.onOpenDetails(); ui.render();
  assert.equal(ui.find(node => node.type === "SegmentedReport").props.summary, summary);
});

test("switching tasks aborts a pending summary and rejects its late response", async t => {
  const pending = deferred(); let signal;
  const second = "summary-next-5678";
  const ui = harness(t, { getReportSummary: (id, input) => {
    if (id === jobId) { signal = input; return pending.promise; }
    return Promise.resolve(reportSummary(id));
  } });
  ui.open(jobId); await tick(); ui.render();
  ui.open(second); await tick(); ui.render();
  assert.equal(signal.aborted, true);
  pending.resolve(reportSummary(jobId)); await tick(); ui.render();
  assert.equal(ui.find(node => node.type === "LiveResults").props.result.job_id, second);
});

test("refreshing a summary invalidates old fragment reads and adopts the new evidence revision", async t => {
  let reads = 0;
  const ui = harness(t, { getReportSummary: async id => ({ ...reportSummary(id), source: { ...reportSummary(id).source, evidence_revision: String(++reads) } }) });
  ui.open(jobId); await tick(); ui.render();
  ui.find(node => node.type === "LiveResults").props.onOpenDetails(); ui.render();
  const previous = ui.find(node => node.type === "SegmentedReport");
  previous.props.onRefresh(); assert.equal(previous.props.signal.aborted, true);
  await tick(); ui.render();
  ui.find(node => node.type === "LiveResults").props.onOpenDetails(); ui.render();
  const next = ui.find(node => node.type === "SegmentedReport");
  assert.equal(next.props.summary.source.evidence_revision, "2");
  assert.notEqual(previous.key, next.key);
});

test("report reads show pending immediately and retries clear only their matching previous error", async t => {
  const overviews = [], trajectories = [];
  const ui = harness(t, {
    getReportSummary: async () => reportSummary(jobId),
    getReportOverview: (id, source, signal) => { const read = { id, source, signal, ...deferred() }; overviews.push(read); return read.promise; },
    getTrajectory: (id, { signal }) => { const read = { id, signal, ...deferred() }; trajectories.push(read); return read.promise; },
  }, { reportEffects: true });
  ui.open(jobId); await tick(); ui.render();
  assert.equal(overviews.length, 1); assert.equal(trajectories.length, 1);
  assert.match(ui.text(), /正在读取分类与测量概览/);
  assert.equal(ui.find(node => node.type === "BallTrajectorySummary").props.pending, true);
  overviews[0].reject(new Error("概览需要重试")); trajectories[0].reject(new Error("球路需要重试")); await tick(); ui.render();
  assert.match(ui.text(), /概览需要重试/); assert.match(ui.text(), /球路需要重试/);
  assert.equal(ui.find(node => node.type === "BallTrajectorySummary").props.pending, false);
  ui.button("重新读取分析概览").props.onClick(); ui.render();
  assert.equal(overviews.length, 2); assert.equal(overviews[0].signal.aborted, true);
  assert.match(ui.text(), /正在读取分类与测量概览/); assert.doesNotMatch(ui.text(), /概览需要重试/);
  assert.match(ui.text(), /球路需要重试/);
  ui.button("重新读取场地与球路").props.onClick(); ui.render();
  assert.equal(trajectories.length, 2); assert.equal(trajectories[0].signal.aborted, true);
  assert.doesNotMatch(ui.text(), /球路需要重试/); assert.equal(ui.find(node => node.type === "BallTrajectorySummary").props.pending, true);
  overviews[1].resolve({ result: { job_id: jobId }, assessment: null });
  trajectories[1].resolve({ job_id: jobId }); await tick(); ui.render();
  assert.doesNotMatch(ui.text(), /正在读取分类与测量概览/);
  assert.equal(ui.find(node => node.type === "BallTrajectorySummary").props.pending, false);
  assert.equal(overviews.length, 2); assert.equal(trajectories.length, 2);
});

test("refreshed report source ignores late overview and trajectory errors", async t => {
  const overviews = [], trajectories = [];
  let revision = 0;
  const ui = harness(t, {
    getReportSummary: async () => ({ ...reportSummary(jobId), source: { ...reportSummary(jobId).source, evidence_revision: String(++revision) } }),
    getReportOverview: (id, source, signal) => { const read = { id, source, signal, ...deferred() }; overviews.push(read); return read.promise; },
    getTrajectory: (id, { signal }) => { const read = { id, signal, ...deferred() }; trajectories.push(read); return read.promise; },
  }, { reportEffects: true });
  ui.open(jobId); await tick(); ui.render();
  ui.find(node => node.type === "LiveResults").props.onOpenDetails(); ui.render();
  ui.find(node => node.type === "SegmentedReport").props.onRefresh(); await tick(); ui.render();
  assert.equal(overviews.length, 2); assert.equal(trajectories.length, 2);
  assert.equal(overviews[0].signal.aborted, true); assert.equal(trajectories[0].signal.aborted, true);
  overviews[0].reject(new Error("旧概览错误")); trajectories[0].reject(new Error("旧球路错误")); await tick(); ui.render();
  assert.doesNotMatch(ui.text(), /旧概览错误|旧球路错误/); assert.match(ui.text(), /正在读取分类与测量概览/);
  overviews[1].resolve({ result: { job_id: jobId }, assessment: null }); trajectories[1].resolve({ job_id: jobId }); await tick(); ui.render();
  assert.doesNotMatch(ui.text(), /正在读取分类与测量概览/);
  assert.equal(ui.find(node => node.type === "BallTrajectorySummary").props.pending, false);
});

function reportSummary(id) {
  return { job_id: id, status: "ready", source: { source_job_id: id, video_id: id, video_sha256: "a".repeat(64), frames_sha256: "b".repeat(64), evidence_revision: "c".repeat(64) }, video: { duration_ms: 3000, frame_count: 90 }, processing: { processed_frames: 90 }, original_video: { source_job_id: id, sha256: "a".repeat(64), pose_time_binding: { retained: true } } };
}
