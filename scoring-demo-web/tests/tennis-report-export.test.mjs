import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import ts from "typescript";
import { tennisScopedFixture } from "./fixtures/tennis-assessment.mjs";

const cache = new Map();
function compiledUrl(file) {
  if (cache.has(file.href)) return cache.get(file.href);
  if (file.pathname.endsWith(".json")) return `data:text/javascript;base64,${Buffer.from(`export default ${fs.readFileSync(file, "utf8")}`).toString("base64")}`;
  let js = ts.transpileModule(fs.readFileSync(file, "utf8"), { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
  js = js.replace(/from "([^"]+)"/g, (_match, name) => `from ${JSON.stringify(name.startsWith(".") ? compiledUrl(new URL(name.endsWith(".json") ? name : `${name}.ts`, file)) : import.meta.resolve(name))}`);
  const url = `data:text/javascript;base64,${Buffer.from(js).toString("base64")}`;
  cache.set(file.href, url);
  return url;
}
const moduleAt = name => import(compiledUrl(new URL(`../app/lib/${name}.ts`, import.meta.url)));
const { buildPracticeReport, buildReportBackup, renderReportMarkdown } = await moduleAt("report-export");
const { readReportJson } = await moduleAt("report-file");
const { tennisAssessmentOf } = await moduleAt("tennis-assessment");
const { trainingScoreOf } = await moduleAt("training-score");
const { reportFocus } = await moduleAt("training-report");

function input() {
  const job = "tennis-report-123", sha = "a".repeat(64);
  const dimensions = ["movement", "preparation", "coordination", "striking", "follow_through"].map((id, i) => ({
    id, label: id, score: 50 + i * 10, observation_count: 2,
    metrics: [{ id: `${id}_metric`, label: "动作测量", value: i + 1, unit: "deg", score: 50 + i * 10, weight: 1, observation_count: 2, target: "连续动作" }],
  }));
  return { mode: "live", uploadState: "complete", job: { id: job, status: "succeeded" },
    evidence: { jobId: job, result: { job_id: job, input: { video: { sha256: sha, duration_ms: 10000 } },
      tennis_assessment: { version: "tennis-action-assessment-v1.1.0", status: "scored", source_run_id: job, source_video_sha256: sha,
        source_timestamp_semantics: "original_decoder_pts", overall_score: 70, dimensions,
        strengths: [{ title: "随挥连续", observation: "随挥动作连续完成" }],
        improvements: [{ dimension_id: "movement", title: "保持弹性支撑", observation: "准备时膝部屈曲较少", action: "保持弹性屈膝后再挥拍", drill: "慢球练习两组，每组十次", retest: "用相同喂球复测十次", start_ms: 1000, end_ms: 2000 }],
        rubric: { version: "tennis-action-rubric-v1.1.0", semantics: "engineering_action_quality", coach_calibrated: false,
          identity_labels_used: false, confidence_used_in_score: false, aggregation: "mean_episode_dimension_scores_then_equal_dimension_mean" },
      },
    } },
  };
}

test("new scores and actionable focus survive export, reload and report rendering", async () => {
  const source = input(), expected = tennisAssessmentOf(source.evidence.result);
  assert.ok(expected);
  assert.equal(trainingScoreOf(source.evidence.result).score, 70);
  assert.equal(reportFocus(source.evidence.result)[0].detail, "保持弹性屈膝后再挥拍");
  const report = buildPracticeReport(source);
  assert.equal(report.sections[0].title, "动作评分：70 / 100");
  assert.equal(report.sections[0].rows.length, 5);
  const text = renderReportMarkdown(report);
  assert.match(text, /怎么练：慢球练习两组/);
  assert.doesNotMatch(text, /待观测|待复核|原文|规则尚未实现|过程依据|测量证据参考分/);
  const encoded = JSON.stringify(buildReportBackup(source));
  const restored = await readReportJson({ size: Buffer.byteLength(encoded), text: async () => encoded });
  assert.deepEqual(tennisAssessmentOf(restored.result), expected);
  assert.equal(trainingScoreOf(restored.result).score, 70);
  assert.equal(buildPracticeReport({ ...source, evidence: { ...source.evidence, result: restored.result } }).sections[0].title, report.sections[0].title);
});

test("complete action navigation survives a portable backup without raw episode debug measurements", async () => {
  const source = input();
  source.evidence.result.tennis_assessment.episodes = [{ episode_id: "nav-episode", family: "baseline", start_ms: 1000, peak_ms: 1500, end_ms: 2000, metrics: { private_debug: 100 } }];
  const encoded = JSON.stringify(buildReportBackup(source));
  const saved = await readReportJson({ size: Buffer.byteLength(encoded), text: async () => encoded });
  assert.deepEqual(tennisAssessmentOf(saved.result).episodes, [{ episode_id: "nav-episode", family: "baseline", start_ms: 1000, peak_ms: 1500, end_ms: 2000 }]);
  assert.equal(saved.result.tennis_assessment.episodes[0].metrics, undefined);
});

test("a foreign assessment never replaces this video's score or authorizes an empty backup", () => {
  const source = input();
  source.evidence.result.tennis_assessment.source_run_id = "another-report";
  assert.equal(trainingScoreOf(source.evidence.result).score, null);
  assert.equal(tennisAssessmentOf(source.evidence.result), null);
  assert.throws(() => buildReportBackup(source), /不能导出空报告/);
});

test("an unavailable report preserves the actual reason instead of inventing a camera problem", async () => {
  const source = input(), assessment = source.evidence.result.tennis_assessment;
  assessment.status = "unavailable"; assessment.overall_score = null;
  assessment.reason_zh = "动作姿态记录缺失或来源校验未通过。";
  assessment.dimensions.forEach(d => { d.score = null; d.observation_count = 0; d.metrics = []; });
  assessment.strengths = []; assessment.improvements = [];
  const report = buildPracticeReport(source), text = renderReportMarkdown(report);
  assert.match(text, /本次动作暂未评分/); assert.match(text, /来源校验未通过/); assert.doesNotMatch(text, /未完整入镜/);
  const encoded = JSON.stringify(buildReportBackup(source));
  const restored = await readReportJson({ size: Buffer.byteLength(encoded), text: async () => encoded });
  assert.equal(tennisAssessmentOf(restored.result).reason_zh, assessment.reason_zh);
});

test("a valid assessment cannot be exported under another wrapper task", () => {
  const source = input();
  source.evidence.jobId = "different-wrapper";
  assert.throws(() => buildPracticeReport(source), /任务与结果编号不一致/);
  assert.throws(() => buildReportBackup(source), /任务与结果编号不一致/);
});

test("a measured zero remains zero and a missing dimension stays null after roundtrip", async () => {
  const source = input(), assessment = source.evidence.result.tennis_assessment;
  assessment.dimensions[0].score = 0;
  assessment.dimensions[0].metrics[0].score = 0;
  assessment.dimensions[4].score = null;
  assessment.dimensions[4].observation_count = 0;
  assessment.dimensions[4].metrics = [];
  assessment.overall_score = 52.5;
  assessment.status = "partial";
  const encoded = JSON.stringify(buildReportBackup(source));
  const saved = await readReportJson({ size: Buffer.byteLength(encoded), text: async () => encoded });
  const restored = tennisAssessmentOf(saved.result);
  assert.equal(restored.dimensions[0].score, 0);
  assert.equal(restored.dimensions[4].score, null);
  assert.equal(restored.overall_score, 52.5);
});

test("v1.2 subject boundaries, motion coverage and measured contributions survive export and revalidation", async () => {
  const source = input(), scoped = tennisScopedFixture();
  source.job.id = source.evidence.jobId = source.evidence.result.job_id = scoped.source_run_id;
  source.evidence.result.tennis_assessment = scoped;
  const expected = tennisAssessmentOf(source.evidence.result);
  assert.ok(expected);
  const markdown = renderReportMarkdown(buildPracticeReport(source));
  assert.match(markdown, /只汇总锁定目标的 2 段动作/);
  assert.match(markdown, /其他轨迹的 1 段没有混入评分/);
  assert.match(markdown, /已测 2\/4 项次动作要求/);
  assert.match(markdown, /尚未测量：具体技术类型未确认/);
  assert.doesNotMatch(markdown, /VIS-GS|P0097|legacy-track-2-epoch/);
  const encoded = JSON.stringify(buildReportBackup(source));
  const restored = await readReportJson({ size: Buffer.byteLength(encoded), text: async () => encoded });
  assert.deepEqual(tennisAssessmentOf(restored.result), expected);
  assert.equal(trainingScoreOf(restored.result).score, 70);
});
