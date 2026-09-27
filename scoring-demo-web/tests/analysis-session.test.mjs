import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import ts from "typescript";

async function moduleFrom(path) {
  const source = fs.readFileSync(new URL(path, import.meta.url), "utf8");
  const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
  return import(`data:text/javascript;base64,${Buffer.from(js).toString("base64")}`);
}
const { watchAnalysis, reconnectDelay } = await moduleFrom("../app/lib/analysis-session.ts");
const { proxyAnalysis } = await moduleFrom("../worker/gateway.ts");
const id = "test-job-12345678";
const client = (overrides = {}) => ({
  getJob: async () => ({ id, status: "succeeded" }),
  getDemoResult: async () => ({ job_id: id, status: "ready", training_evaluation: { score_0_to_100: 77 } }),
  getTrajectory: async () => ({}), getTechniqueAssessment: async () => ({}), ...overrides,
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
