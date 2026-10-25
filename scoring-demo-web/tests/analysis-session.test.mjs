import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import ts from "typescript";

async function moduleFrom(path) {
  const source = fs.readFileSync(new URL(path, import.meta.url), "utf8");
  const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
  return import(`data:text/javascript;base64,${Buffer.from(js).toString("base64")}`);
}
const { watchAnalysis, reconnectDelay, AnalysisCancelledError, mergeAnalysisProgress } = await moduleFrom("../app/lib/analysis-session.ts");
const { analysisProgress } = await moduleFrom("../app/lib/analysis-progress.ts");
const { proxyAnalysis } = await moduleFrom("../worker/gateway.ts");
const id = "test-job-12345678";
const client = (overrides = {}) => ({
  getJob: async () => ({ id, status: "succeeded" }),
  getDemoResult: async () => ({ job_id: id, status: "ready", training_evaluation: { score_0_to_100: 77 } }),
  getTrajectory: async () => ({}), getTechniqueAssessment: async () => ({}), ...overrides,
});

test("requested cancellation remains running until a server terminal response, without reading a false report", async () => {
  const updates = []; let polls = 0;
  await assert.rejects(watchAnalysis(client({
    getJob: async () => ({ id, status: ++polls < 3 ? "running" : "cancelled", cancel_requested: true }),
    getDemoResult: async () => assert.fail("cancelled job cannot produce a completed report"),
  }), id, new AbortController().signal, job => updates.push(job), 0), AnalysisCancelledError);
  assert.deepEqual(updates.map(job => job.status), ["running", "running", "cancelled"]);
  assert.equal(analysisProgress(updates[0]).title, "正在取消分析");
  assert.match(analysisProgress(updates[0]).detail, /等待服务器确认停止/);
  assert.equal(analysisProgress(updates[2]).title, "分析已取消");
});

test("completion wins a cancellation race and the completed report is read", async () => {
  let polls = 0;
  const result = await watchAnalysis(client({ getJob: async () => ({ id, status: ++polls === 1 ? "running" : "succeeded", cancel_requested: true }) }), id, new AbortController().signal, () => {}, 0);
  assert.equal(result.result.training_evaluation.score_0_to_100, 77);
  assert.equal(analysisProgress(result.job).title, "正在读取训练报告");
});

test("late poll/cancel replies cannot regress terminal reports or undo accepted cancellation", () => {
  const done = { id, status: "succeeded", summary: { preserved: true } };
  for (const status of ["running", "queued", "cancelled"]) assert.equal(mergeAnalysisProgress(done, { id, status }), done);
  const requested = { id, status: "running", cancel_requested: true, cancel_requested_at: "2026-10-18T10:00:00Z" };
  const stale = mergeAnalysisProgress(requested, { id, status: "running", cancel_requested: false, progress: 50 });
  assert.equal(stale.cancel_requested, true); assert.equal(stale.progress, 50);
  assert.equal(stale.cancel_requested_at, requested.cancel_requested_at);
  assert.equal(mergeAnalysisProgress(requested, { id, status: "succeeded" }).status, "succeeded");
  const other = { id: "other-job", status: "queued" };
  assert.equal(mergeAnalysisProgress(requested, other), other);
});

test("cancel gateway permits only same-origin POST and uses server authentication", async t => {
  const calls = [];
  t.mock.method(globalThis, "fetch", async (url, init) => { calls.push({ url: String(url), init }); return Response.json({ id, status: "cancelled" }); });
  const env = { RALLYMATE_API_ORIGIN: "https://analyzer.example", RALLYMATE_API_KEY: "server-test-key" };
  const response = await proxyAnalysis(new Request(`https://app.example/v1/jobs/${id}/cancel`, { method: "POST", headers: { origin: "https://app.example", authorization: "Bearer browser", cookie: "private=1" } }), env);
  assert.equal(response.status, 200); assert.equal((await response.json()).status, "cancelled");
  assert.equal(calls[0].url, `https://analyzer.example/v1/jobs/${id}/cancel`);
  assert.equal(calls[0].init.method, "POST"); assert.equal(calls[0].init.headers.get("authorization"), "Bearer server-test-key"); assert.equal(calls[0].init.headers.get("cookie"), null);
  for (const headers of [{ origin: "https://other.example" }, { "sec-fetch-site": "cross-site" }]) assert.equal((await proxyAnalysis(new Request(`https://app.example/v1/jobs/${id}/cancel`, { method: "POST", headers }), env)).status, 403);
  for (const method of ["GET", "PUT", "DELETE"]) assert.equal((await proxyAnalysis(new Request(`https://app.example/v1/jobs/${id}/cancel`, { method }), env)).status, 404);
  assert.equal(calls.length, 1);
});

test("long jobs continue beyond the old 180-poll limit", async () => {
  let polls = 0;
  const result = await watchAnalysis(client({ getJob: async () => ({ id, status: ++polls > 185 ? "succeeded" : "running" }) }), id, new AbortController().signal, () => {}, 0);
  assert.equal(polls, 186); assert.equal(result.result.training_evaluation.score_0_to_100, 77);
});
test("a transient disconnect reconnects to the same job", async () => {
  let calls = 0;
  const result = await watchAnalysis(client({ getJob: async () => { if (++calls === 1) throw new Error("network"); return { id, status: "succeeded" }; } }), id, new AbortController().signal, () => {}, 0);
  assert.equal(result.job.id, id); assert.equal(calls, 2);
});

test("repeated temporary failures reconnect without replacing the server job status", async () => {
  let calls = 0;
  const updates = [];
  const result = await watchAnalysis(client({ getJob: async () => {
    calls += 1;
    if (calls === 1) return { id, status: "running", progress: { percent: 27 } };
    if (calls <= 9) throw Object.assign(new Error("unavailable"), { status: 503 });
    return { id, status: "succeeded" };
  } }), id, new AbortController().signal, job => updates.push(job), 0);
  assert.equal(result.job.id, id);
  assert.equal(calls, 10);
  const reconnects = updates.filter(job => job.message?.includes("自动重连"));
  assert.equal(reconnects.length, 8);
  assert.ok(reconnects.every(job => job.id === id && job.status === "running" && job.progress.percent === 27));
  assert.deepEqual([1, 2, 3, 4, 25].map(attempt => reconnectDelay(attempt)), [2000, 4000, 8000, 15000, 15000]);
});

test("unauthorized, forbidden and missing jobs stop immediately", async () => {
  for (const status of [401, 403, 404]) {
    let calls = 0;
    await assert.rejects(watchAnalysis(client({ getJob: async () => { calls += 1; throw Object.assign(new Error("terminal"), { status }); } }), id, new AbortController().signal, () => {}, 0), /terminal/);
    assert.equal(calls, 1);
  }
});

test("unrecovered disconnection stays resumable after the bounded retry budget", async () => {
  let calls = 0;
  await assert.rejects(watchAnalysis(client({ getJob: async () => { calls += 1; throw new Error("network"); } }), id, new AbortController().signal, () => {}, 0), /当前任务，无需重新上传/);
  assert.equal(calls, 30);
});

test("required completed result is retried without resubmitting the video", async () => {
  let reads = 0;
  const result = await watchAnalysis(client({ getDemoResult: async () => {
    if (++reads < 3) throw Object.assign(new Error("unavailable"), { status: 502 });
    return { job_id: id, status: "ready", training_evaluation: { score_0_to_100: 77 } };
  } }), id, new AbortController().signal, () => {}, 0);
  assert.equal(reads, 3);
  assert.equal(result.result.training_evaluation.score_0_to_100, 77);
});
test("failed results are resumable and never reported as complete", async () => {
  await assert.rejects(watchAnalysis(client({ getDemoResult: async () => { throw new Error("503"); } }), id, new AbortController().signal, () => {}, 0), /无需重新上传/);
  await assert.rejects(watchAnalysis(client({ getDemoResult: async () => ({ job_id: "other", status: "ready" }) }), id, new AbortController().signal, () => {}, 0), /不匹配/);
});
test("optional overlays can fail without discarding a valid score", async () => {
  const result = await watchAnalysis(client({ getTrajectory: async () => { throw new Error("unavailable"); } }), id, new AbortController().signal, () => {}, 0);
  assert.equal(result.result.training_evaluation.score_0_to_100, 77); assert.ok(result.warning);
});
test("stopping observation cancels polling", async () => {
  const controller = new AbortController(); controller.abort();
  await assert.rejects(watchAnalysis(client(), id, controller.signal, () => {}, 0), { name: "AbortError" });
});

test("running trajectory renders before completion and final trajectory is refreshed", async () => {
  let polls = 0;
  let reads = 0;
  const received = [];
  const result = await watchAnalysis(client({
    getJob: async () => ({ id, status: ++polls <= 2 ? "running" : "succeeded", progress: { processed_frames: polls * 50 } }),
    getTrajectory: async (_id, options) => {
      assert.equal(options.predictionHorizonMs, 0);
      return { job_id: id, source: { is_partial: ++reads === 1 } };
    },
  }), id, new AbortController().signal, () => {}, 0, preview => {
    assert.equal(polls, 1);
    received.push(preview);
  });
  assert.equal(received.length, 1);
  assert.equal(received[0].source.is_partial, true);
  assert.equal(reads, 2, "rapid polling should not repeatedly parse the artifact");
  assert.equal(result.trajectory.source.is_partial, false);
});

test("an unavailable running preview never interrupts score delivery", async () => {
  let polls = 0;
  let reads = 0;
  const result = await watchAnalysis(client({
    getJob: async () => ({ id, status: ++polls === 1 ? "running" : "succeeded", progress: 10 }),
    getTrajectory: async () => { if (++reads === 1) throw new Error("409 waiting for frames"); return { job_id: id }; },
  }), id, new AbortController().signal, () => {}, 0, () => assert.fail("preview was unavailable"));
  assert.equal(result.result.training_evaluation.score_0_to_100, 77);
  assert.equal(result.trajectory.job_id, id);
});

test("a partial preview is preserved if the final optional fetch fails", async () => {
  let polls = 0;
  let reads = 0;
  const result = await watchAnalysis(client({
    getJob: async () => ({ id, status: ++polls === 1 ? "running" : "succeeded" }),
    getTrajectory: async () => { if (++reads > 1) throw new Error("503"); return { job_id: id, source: { is_partial: true } }; },
  }), id, new AbortController().signal, () => {}, 0, () => {});
  assert.equal(result.trajectory.source.is_partial, true);
  assert.ok(result.warning);
});

test("trajectory responses from another video are ignored", async () => {
  let polls = 0;
  const result = await watchAnalysis(client({
    getJob: async () => ({ id, status: ++polls === 1 ? "running" : "succeeded" }),
    getTrajectory: async () => ({ job_id: "other-video" }),
  }), id, new AbortController().signal, () => {}, 0, () => assert.fail("wrong video"));
  assert.equal(result.trajectory, null);
  assert.ok(result.warning);
});

test("aborting an in-flight preview never invokes the renderer", async () => {
  const controller = new AbortController();
  await assert.rejects(watchAnalysis(client({
    getJob: async () => ({ id, status: "running" }),
    getTrajectory: async () => { controller.abort(); return { job_id: id }; },
  }), id, controller.signal, () => {}, 0, () => assert.fail("render after abort")), { name: "AbortError" });
});

test("a slow preview never blocks progress and cannot overwrite the final result", async () => {
  let polls = 0;
  let reads = 0;
  let releasePreview;
  let previewSignal;
  const observed = [];
  const result = await watchAnalysis(client({
    getJob: async () => ({ id, status: ++polls < 3 ? "running" : "succeeded", progress: { processed_frames: polls * 50 } }),
    getTrajectory: async (_id, options) => {
      if (++reads === 1) {
        assert.equal(options.sampleLimit, 128);
        previewSignal = options.signal;
        return new Promise(resolve => { releasePreview = resolve; });
      }
      assert.equal(options.sampleLimit, 384);
      return { job_id: id, source: { is_partial: false } };
    },
  }), id, new AbortController().signal, () => {}, 0, preview => observed.push(preview));
  assert.equal(polls, 3);
  assert.equal(reads, 2);
  assert.equal(previewSignal.aborted, true);
  assert.equal(result.trajectory.source.is_partial, false);
  releasePreview({ job_id: id, source: { is_partial: true } });
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(observed.length, 0);
});
test("proxy rejects unknown paths and cross-site uploads", async () => {
  assert.equal((await proxyAnalysis(new Request("http://localhost/v1/admin"), {})).status, 404);
  assert.equal((await proxyAnalysis(new Request("http://localhost/v1/jobs", { method: "POST", headers: { origin: "https://other.example" } }), {})).status, 403);
});
test("local tunnel permits same-host HTTPS uploads to a loopback API only when enabled", async () => {
  const original = globalThis.fetch;
  const request = () => new Request("http://private.example/v1/jobs", { method: "POST", headers: { origin: "https://private.example" } });
  const env = { RALLYMATE_API_ORIGIN: "http://127.0.0.1:8001" };
  try {
    globalThis.fetch = async (url) => {
      assert.equal(String(url), "http://127.0.0.1:8001/v1/jobs");
      return Response.json({ id }, { status: 202 });
    };
    assert.equal((await proxyAnalysis(request(), env)).status, 403);
    assert.equal((await proxyAnalysis(request(), { ...env, RALLYMATE_LOCAL_TUNNEL: "1" })).status, 202);
    assert.equal((await proxyAnalysis(new Request("http://private.example/v1/jobs", { method: "POST", headers: { origin: "https://other.example" } }), { ...env, RALLYMATE_LOCAL_TUNNEL: "1" })).status, 403);
  } finally { globalThis.fetch = original; }
});
test("proxy strips browser credentials and preserves video range", async () => {
  const original = globalThis.fetch;
  try {
    globalThis.fetch = async (url, init) => {
      assert.equal(String(url), `https://analyzer.example/v1/jobs/${id}/artifacts/annotated.mp4`);
      assert.equal(init.headers.get("authorization"), "Bearer backend-test");
      assert.equal(init.headers.get("cookie"), null); assert.equal(init.headers.get("range"), "bytes=0-9"); assert.equal(init.redirect, "manual");
      return new Response("0123456789", { status: 206, headers: { "content-range": "bytes 0-9/100", "content-type": "video/mp4" } });
    };
    const response = await proxyAnalysis(new Request(`http://localhost/v1/jobs/${id}/artifacts/annotated.mp4`, { headers: { cookie: "private=1", authorization: "Basic browser", range: "bytes=0-9" } }), { RALLYMATE_API_ORIGIN: "https://analyzer.example", RALLYMATE_API_KEY: "backend-test" });
    assert.equal(response.status, 206); assert.equal(response.headers.get("content-range"), "bytes 0-9/100");
  } finally { globalThis.fetch = original; }
});


test("required result failure preserves its original cause without altering the bounded same-job retries", async () => {
  const original = Object.assign(new Error("private cause text"), { status: 503, payload: { private: true } });
  let calls = 0;
  await assert.rejects(watchAnalysis(client({ getDemoResult: async () => { calls++; throw original; } }), id, new AbortController().signal, () => {}, 0), error => {
    assert.equal(error.cause, original);
    assert.ok(error.message.includes("无需重新上传"));
    assert.ok(!error.message.includes("private"));
    return true;
  });
  assert.equal(calls, 3);
});

test("an optional timeout retries once with a fresh deadline after primary delivery", async t => {
  const deadlines = [];
  t.mock.method(AbortSignal, "timeout", ms => { const controller = new AbortController(); deadlines.push({ ms, controller }); return controller.signal; });
  let reportReady = false, trajectoryReads = 0, assessmentReads = 0, initialSignal;
  const timeout = new DOMException("cold artifact read timed out", "TimeoutError");
  const result = await watchAnalysis(client({
    getDemoResult: async () => ({ job_id: id, status: "ready" }),
    getTrajectory: async (jobId, options) => {
      assert.equal(reportReady, true);
      assert.equal(jobId, id); assert.equal(options.sampleLimit, 384); assert.equal(options.predictionHorizonMs, 0);
      if (++trajectoryReads === 1) {
        initialSignal = options.signal;
        queueMicrotask(() => deadlines[0].controller.abort(timeout));
        return new Promise((_resolve, reject) => options.signal.addEventListener("abort", () => reject(options.signal.reason), { once: true }));
      }
      assert.equal(reportReady, true);
      assert.notEqual(options.signal, initialSignal); assert.equal(initialSignal.aborted, true); assert.equal(options.signal.aborted, false);
      return { job_id: id, ball: { observed: [{ timestamp_ms: 0, x: .3, y: .4 }] } };
    },
    getTechniqueAssessment: async () => { assessmentReads++; return { job_id: id }; },
  }), id, new AbortController().signal, () => {}, 0, undefined, () => { reportReady = true; });
  assert.equal(trajectoryReads, 2); assert.equal(assessmentReads, 1);
  assert.deepEqual(deadlines.map(row => row.ms), [20000, 20000]);
  assert.equal(result.trajectory.ball.observed.length, 1); assert.equal(result.warning, null);
});

test("both failed optional reads get one independent retry and permanent failures remain bounded", async t => {
  const deadlines = [];
  t.mock.method(AbortSignal, "timeout", ms => { const controller = new AbortController(); deadlines.push({ ms, controller }); return controller.signal; });
  let trajectories = 0, assessments = 0;
  const signals = [];
  const result = await watchAnalysis(client({
    getTrajectory: async (_id, options) => { signals.push(options.signal); trajectories++; throw Object.assign(new Error("busy"), { status: 503 }); },
    getTechniqueAssessment: async (_id, options) => { signals.push(options.signal); assessments++; throw Object.assign(new Error("busy"), { status: 503 }); },
  }), id, new AbortController().signal, () => {}, 0);
  assert.equal(trajectories, 2); assert.equal(assessments, 2); assert.notEqual(signals[2], signals[3]);
  assert.deepEqual(deadlines.map(row => row.ms), [20000, 20000, 20000]);
  assert.equal(result.trajectory, null); assert.equal(result.assessment, null); assert.ok(result.warning);
  let permanentReads = 0;
  await watchAnalysis(client({ getTrajectory: async () => { permanentReads++; throw Object.assign(new Error("invalid artifact"), { status: 422 }); } }), id, new AbortController().signal, () => {}, 0);
  assert.equal(permanentReads, 1);
});

test("late optional responses after cancellation or a retry deadline never enter a completed report", async t => {
  const deadlines = [];
  t.mock.method(AbortSignal, "timeout", ms => { const controller = new AbortController(); deadlines.push({ ms, controller }); return controller.signal; });
  for (const callerAbort of [false, true]) {
    const controller = new AbortController(); let reads = 0;
    const work = watchAnalysis(client({ getTrajectory: async () => {
      if (++reads === 1) throw new Error("network");
      if (callerAbort) controller.abort(new Error("job switched"));
      else deadlines.at(-1).controller.abort(new DOMException("expired", "TimeoutError"));
      return { job_id: id, ball: { observed: [{}] } };
    } }), id, controller.signal, () => {}, 0);
    if (callerAbort) await assert.rejects(work, /job switched/);
    else { const result = await work; assert.equal(result.trajectory, null); assert.ok(result.warning); }
    assert.equal(reads, 2);
  }
});

test("optional retry keeps job identity checks and never runs for a rejected primary identity", async () => {
  let trajectoryReads = 0, assessmentReads = 0;
  const result = await watchAnalysis(client({
    getTrajectory: async () => { if (++trajectoryReads === 1) throw new Error("network"); return { job_id: "other-job" }; },
    getTechniqueAssessment: async () => { if (++assessmentReads === 1) throw new Error("network"); return { job_id: "other-job" }; },
  }), id, new AbortController().signal, () => {}, 0);
  assert.equal(trajectoryReads, 2); assert.equal(assessmentReads, 2);
  assert.equal(result.trajectory, null); assert.equal(result.assessment, null); assert.ok(result.warning);
  let reads = 0;
  await assert.rejects(watchAnalysis(client({
    getDemoResult: async () => ({ job_id: "other-job", status: "ready" }),
    getTrajectory: async () => { reads++; throw new Error("network"); },
  }), id, new AbortController().signal, () => {}, 0), /不匹配/);
  assert.equal(reads, 0);
});

test("empty optional responses never discard a valid primary report or trigger another read", async () => {
  for (const value of [null, undefined]) {
    let reads = 0;
    const result = await watchAnalysis(client({
      getTrajectory: async () => { reads++; return value; },
      getTechniqueAssessment: async () => { reads++; return value; },
    }), id, new AbortController().signal, () => {}, 0);
    assert.equal(result.result.training_evaluation.score_0_to_100, 77);
    assert.equal(result.trajectory, null); assert.equal(result.assessment, null); assert.ok(result.warning);
    assert.equal(reads, 2);
  }
});

test("validated primary report becomes readable while optional evidence is still pending", async () => {
  let release, delivered = null, done = false;
  const optional = new Promise(resolve => { release = resolve; });
  const work = watchAnalysis(client({ getTrajectory: () => optional }), id, new AbortController().signal, () => {}, 0, undefined, result => { delivered = result; }).then(result => { done = true; return result; });
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(delivered.job_id, id); assert.equal(done, false);
  release({ job_id: id });
  assert.equal((await work).result, delivered);
});

test("supplementary requests and their fresh deadlines start only after the primary callback", async t => {
  const deadlines = [], events = [];
  t.mock.method(AbortSignal, "timeout", ms => { deadlines.push(ms); return new AbortController().signal; });
  let releasePrimary, releaseOptional, delivered;
  const primary = { job_id: id, status: "ready", training_evaluation: { score_0_to_100: 0 } };
  const pendingPrimary = new Promise(resolve => { releasePrimary = resolve; });
  const pendingOptional = new Promise(resolve => { releaseOptional = resolve; });
  const work = watchAnalysis(client({
    getDemoResult: () => { events.push("primary"); return pendingPrimary; },
    getTrajectory: () => { events.push("trajectory"); assert.equal(delivered, primary); return pendingOptional; },
    getTechniqueAssessment: async () => { events.push("assessment"); assert.equal(delivered, primary); return { job_id: id }; },
  }), id, new AbortController().signal, () => {}, 0, undefined, result => { events.push("published"); delivered = result; });
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.deepEqual(events, ["primary"]); assert.deepEqual(deadlines, []);
  releasePrimary(primary);
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.deepEqual(events, ["primary", "published", "trajectory", "assessment"]);
  assert.deepEqual(deadlines, [20000]);
  assert.equal(delivered.training_evaluation.score_0_to_100, 0);
  releaseOptional({ job_id: id });
  assert.equal((await work).result, primary);
});

test("navigation during primary delivery prevents every supplementary request", async () => {
  const controller = new AbortController(), reason = new Error("navigated away");
  let optionalReads = 0;
  await assert.rejects(watchAnalysis(client({
    getTrajectory: async () => { optionalReads++; return { job_id: id }; },
    getTechniqueAssessment: async () => { optionalReads++; return { job_id: id }; },
  }), id, controller.signal, () => {}, 0, undefined, () => controller.abort(reason)), error => error === reason);
  assert.equal(optionalReads, 0);
});

test("primary callback cannot expose a wrong task or late cancelled response", async () => {
  let calls = 0;
  await assert.rejects(watchAnalysis(client({ getDemoResult: async () => ({ job_id: "another-job", status: "ready" }) }), id, new AbortController().signal, () => {}, 0, undefined, () => calls++), /不匹配/);
  const controller = new AbortController();
  await assert.rejects(watchAnalysis(client({ getDemoResult: async () => { controller.abort(new Error("switched task")); return { job_id: id, status: "ready" }; } }), id, controller.signal, () => {}, 0, undefined, () => calls++), /switched task/);
  assert.equal(calls, 0);
});

test("a rejected or mismatched required report never starts supplementary requests", async () => {
  for (const mismatch of [false, true]) {
    let optionalReads = 0;
    await assert.rejects(watchAnalysis(client({
      getDemoResult: async () => { if (mismatch) return { job_id: "wrong-job", status: "ready" }; throw Object.assign(new Error("missing report"), { status: 404 }); },
      getTrajectory: async () => { optionalReads++; return { job_id: id }; },
      getTechniqueAssessment: async () => { optionalReads++; return { job_id: id }; },
    }), id, new AbortController().signal, () => {}, 0), mismatch ? /不匹配/ : /继续读取结果/);
    assert.equal(optionalReads, 0);
  }
});

test("a UI delivery exception never repeats a successful report request", async () => {
  let reads = 0, optionalReads = 0;
  await assert.rejects(watchAnalysis(client({
    getDemoResult: async () => { reads++; return { job_id: id, status: "ready" }; },
    getTrajectory: async () => { optionalReads++; return { job_id: id }; },
    getTechniqueAssessment: async () => { optionalReads++; return { job_id: id }; },
  }), id, new AbortController().signal, () => {}, 0, undefined, () => { throw new Error("render callback failed"); }), /render callback failed/);
  assert.equal(reads, 1); assert.equal(optionalReads, 0);
});

test("an explicit server preparation deadline is terminal and does not auto-retry HTTP504", async () => {
  let reads = 0;
  const original = Object.assign(new Error("builder deadline"), { status: 504, payload: { detail: { code: "report_preparation_timeout" } } });
  await assert.rejects(watchAnalysis(client({ getDemoResult: async () => { reads++; throw original; } }),
    id, new AbortController().signal, () => {}, 0), error => error.cause === original && /整理报告已超时/.test(error.message));
  assert.equal(reads, 1);
});

test("automatic report retries share one 120 second budget instead of restarting it", async t => {
  let now = 0; const budgets = [];
  t.mock.method(performance, "now", () => now);
  await assert.rejects(watchAnalysis(client({ getDemoResult: async (_id, options) => {
    budgets.push(options.maxWaitMs);
    now += budgets.length === 1 ? 80000 : 40001;
    throw Object.assign(new Error("temporary transfer failure"), { status: 503 });
  } }), id, new AbortController().signal, () => {}, 0), error => error.cause.status === 425);
  assert.deepEqual(budgets, [120000, 40000]);
});
