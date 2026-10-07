import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import ts from "typescript";

const modules = new Map();
function compiledUrl(file) {
  if (modules.has(file.href)) return modules.get(file.href);
  let js = ts.transpileModule(fs.readFileSync(file, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  js = js.replace(/from "([^"]+)"/g, (_match, name) => `from ${JSON.stringify(
    name.startsWith(".") ? compiledUrl(new URL(`${name}.ts`, file)) : import.meta.resolve(name),
  )}`);
  const url = `data:text/javascript;base64,${Buffer.from(js).toString("base64")}`;
  modules.set(file.href, url);
  return url;
}
const { createApiClient } = await import(compiledUrl(new URL("../app/lib/api-client.ts", import.meta.url)));
const configured = { baseUrl: "", jobsPath: "/v1/jobs", scorecardPath: "/api/scorecard", techniquesPath: "/v1/techniques" };

function nativeReport() {
  const motion = { schema_version: "1.1.0", analysis_version: "stroke-motion-analysis-v2.0.0",
    temporal_backend: { kind: "deterministic_rules" }, contact_confirmed: false,
    families: { baseline: { episodes: [{ episode_id: "swing-one", temporal_recognition: { anchor_is_contact: false },
      rotation_analysis: { schema_version: "1.1.0", windows: [{ start_ms: 200, end_ms: 500 }] }, metrics: { elbow_extension_deg: null } }] } } };
  const footwork = { schema_version: "1.1.0", review_version: "footwork-independent-measurements-v1.1.0",
    measurement_summary: { partial_indicator_count: 1 }, episodes: [{ event_id: "step-one", indicators: [{
      indicator_id: "FS01-M02", feature_status: "unavailable", features: [], measurement_status: "partial",
      measurements: [{ feature_name: "stance_width_body", status: "measured", value: .7 },
        { feature_name: "left_knee_flexion_deg", status: "unavailable", value: null }],
    }] }] };
  return { job_id: "job-12345678", status: "ready", footwork_review: footwork,
    action_recognition: { motion_analysis: motion }, summary: { action_recognition: { motion_analysis: motion } },
    analysis_report: { version: "training-report-v1.0.0", evidence: { technical_grade: null } },
    source_aligned_assessment: { technical_grade: null, status: "partial" },
    measurement_contract: { contract_version: "isotropic-frame-long-edge-v1.1.0" } };
}

test("only the three report reads opt into current and preserve complete 1.1 evidence", async () => {
  const requests = [], source = nativeReport();
  const client = createApiClient(configured, async (url, init) => {
    requests.push({ url: String(url), init });
    assert.deepEqual(new URL(url, "https://browser.test").searchParams.getAll("report_contract"), ["current"]);
    return Response.json(source);
  });
  const job = await client.getJob(source.job_id);
  const demo = await client.getDemoResult(source.job_id);
  const assessment = await client.getTechniqueAssessment(source.job_id);
  assert.deepEqual(requests.map(item => item.url), [
    "/v1/jobs/job-12345678?report_contract=current",
    "/v1/jobs/job-12345678/demo-result?report_contract=current",
    "/v1/jobs/job-12345678/technique-assessment?report_contract=current",
  ]);
  assert.ok(requests.every(({ init }) => init.method === "GET" && init.cache === "no-store" && init.headers["X-Request-ID"]));
  assert.deepEqual(job.summary, source.summary);
  for (const result of [demo, assessment]) {
    assert.deepEqual(result.action_recognition, source.action_recognition);
    assert.deepEqual(result.footwork_review, source.footwork_review);
    assert.deepEqual(result.source_aligned_assessment, source.source_aligned_assessment);
    assert.deepEqual(result.analysis_report, source.analysis_report);
  }
});

test("private absolute result links stay on the configured gateway and retain existing queries", async () => {
  const requests = [];
  const client = createApiClient({ ...configured, baseUrl: "https://preview.example/gateway" }, async url => {
    requests.push(String(url)); return Response.json(nativeReport());
  });
  for (const [method, suffix] of [["getDemoResult", "demo-result"], ["getTechniqueAssessment", "technique-assessment"]]) {
    await client[method]("job-12345678", { endpoint: `http://127.0.0.1:8001/v1/jobs/job-12345678/${suffix}?locale=zh%20CN&tag=one&tag=two&report_contract=legacy-v1&report_contract=current` });
    const url = new URL(requests.at(-1));
    assert.equal(url.origin, "https://preview.example");
    assert.equal(url.pathname, `/gateway/v1/jobs/job-12345678/${suffix}`);
    assert.equal(url.searchParams.get("locale"), "zh CN");
    assert.deepEqual(url.searchParams.getAll("tag"), ["one", "two"]);
    assert.deepEqual(url.searchParams.getAll("report_contract"), ["current"]);
  }
});

test("relative custom endpoints and configured job paths keep queries before fragments", async () => {
  const requests = [];
  const client = createApiClient({ ...configured, baseUrl: "/analysis", jobsPath: "/v1/custom-jobs/?tenant=trial&report_contract=legacy-v1" }, async url => {
    requests.push(String(url)); return Response.json(nativeReport());
  });
  await client.getJob("job with/slash");
  await client.getDemoResult("job with/slash");
  await client.getTechniqueAssessment("job with/slash");
  assert.deepEqual(requests, ["", "/demo-result", "/technique-assessment"].map(suffix =>
    `/analysis/v1/custom-jobs/job%20with%2Fslash${suffix}?tenant=trial&report_contract=current`));
  const endpoint = "/v1/jobs/custom/demo-result?encoded=a%2Bb&report_contract=current&report_contract=legacy-v1#details";
  for (let repeat = 0; repeat < 2; repeat++) {
    await client.getDemoResult("ignored", { endpoint });
    const url = new URL(requests.at(-1), "https://preview.example");
    assert.equal(url.pathname, "/analysis/v1/jobs/custom/demo-result");
    assert.equal(url.searchParams.get("encoded"), "a+b");
    assert.deepEqual(url.searchParams.getAll("report_contract"), ["current"]);
    assert.equal(url.hash, "#details");
  }
});

test("invalid analyzer endpoints are rejected before fetch even with a current query", async () => {
  let calls = 0;
  const client = createApiClient({ ...configured, baseUrl: "https://preview.example" }, async () => {
    calls++; return Response.json(nativeReport());
  });
  for (const endpoint of ["http://127.0.0.1:8001/private?report_contract=current", "https://private.example/v1/../secret", "https://private.example/v10/jobs/example"]) {
    for (const method of ["getDemoResult", "getTechniqueAssessment"]) {
      await assert.rejects(async () => client[method]("job-12345678", { endpoint }), error => error.status === 502 && /无效/.test(error.message));
    }
  }
  assert.equal(calls, 0);
});

test("metadata, catalogs, pose, trajectory and technical review export do not acquire a report contract", async () => {
  const requests = [];
  const client = createApiClient(configured, async url => { requests.push(String(url)); return Response.json({}); });
  await client.getMeta();
  await client.getTechniques();
  await client.getCapabilities();
  await client.getPosePreview("job-12345678", { startMs: 10 });
  await client.getTrajectory("job-12345678", { endpoint: "/v1/jobs/job-12345678/trajectory?existing=yes", sampleLimit: 20 });
  await client.exportTechnicalReview("job-12345678");
  assert.ok(requests.every(url => !new URL(url, "https://browser.test").searchParams.has("report_contract")));
  assert.equal(requests.at(-2), "/v1/jobs/job-12345678/trajectory?existing=yes&sample_limit=20");
});
