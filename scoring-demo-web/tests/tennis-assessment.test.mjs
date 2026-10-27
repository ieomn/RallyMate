import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import ts from "typescript";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { tennisFixture, tennisScopedFixture, tennisHash, tennisResult } from "./fixtures/tennis-assessment.mjs";

const cache = new Map();
function compiledUrl(url) {
  if (url.pathname.endsWith(".json")) return `data:text/javascript;base64,${Buffer.from(`export default ${fs.readFileSync(url, "utf8")}`).toString("base64")}`;
  if (cache.has(url.href)) return cache.get(url.href);
  let js = ts.transpileModule(fs.readFileSync(url, "utf8"), { fileName: url.pathname, compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  js = js.replace(/^import\s+["'][^"']+\.css["'];?\s*$/gm, "");
  js = js.replace(/from "([^"]+)"/g, (_match, name) => {
    const local = new URL(name.endsWith(".json") ? name : `${name}.ts`, url);
    return `from ${JSON.stringify(name.startsWith(".") ? compiledUrl(fs.existsSync(local) ? local : new URL(`${name}.tsx`, url)) : import.meta.resolve(name))}`;
  });
  const output = `data:text/javascript;base64,${Buffer.from(js).toString("base64")}`;
  cache.set(url.href, output); return output;
}
const { validatedTennisAssessment, tennisAssessmentOf, retainTennisAssessment } = await import(compiledUrl(new URL("../app/lib/tennis-assessment.ts", import.meta.url)));
const { default: Summary, TennisAssessmentDetails, tennisImprovementFocus } = await import(compiledUrl(new URL("../app/TennisAssessmentSummary.tsx", import.meta.url)));
const { default: LiveResults } = await import(compiledUrl(new URL("../app/LiveResults.tsx", import.meta.url)));
const context = { jobId: "tennis-test", videoSha256: tennisHash, durationMs: 9000 };
const parse = report => validatedTennisAssessment(report, context);
const render = (report, extra = {}) => renderToStaticMarkup(createElement(Summary, { report, ...extra }));
function currentFixture() {
  const report = tennisScopedFixture();
  report.version = "tennis-action-assessment-v1.3.0";
  report.rubric.version = "tennis-action-rubric-v1.3.0";
  report.episodes.forEach(e => { e.motion_pattern.version = "tennis-motion-patterns-v1.1.0"; });
  return report;
}

test("new action scores bind to the current job, video hash and original source time", () => {
  const fixture = tennisFixture(), original = structuredClone(fixture);
  assert.equal(parse(fixture).overall_score, 70); assert.deepEqual(fixture, original);
  for (const patch of [{ source_run_id: "foreign-job" }, { source_video_sha256: "b".repeat(64) }, { source_timestamp_semantics: "frames" }, { version: "unknown" }]) assert.equal(parse({ ...fixture, ...patch }), null);
  assert.equal(tennisAssessmentOf(tennisResult()).overall_score, 70);
  const result = tennisResult(); delete result.input.video.sha256;
  assert.equal(tennisAssessmentOf(result), null, "a payload cannot vouch for its own video identity");
  result.original_video = { sha256: tennisHash }; assert.equal(tennisAssessmentOf(result).overall_score, 70);
  result.input.video.sha256 = "b".repeat(64); assert.equal(tennisAssessmentOf(result), null);
});

test("unknown rubrics, duplicate dimensions and ungrounded scores cannot enter the main card", () => {
  for (const patch of [{ identity_labels_used: true }, { confidence_used_in_score: true }, { coach_calibrated: true }, { aggregation: "weighted_confidence" }]) {
    const report = tennisFixture(); Object.assign(report.rubric, patch); assert.equal(parse(report), null);
  }
  for (const mutate of [r => r.dimensions.pop(), r => r.dimensions[1].id = "movement", r => r.overall_score = 95,
    r => r.dimensions[0].score = 95, r => r.dimensions[0].metrics[0].score = null, r => r.dimensions[0].metrics[0].value = null,
    r => r.dimensions[0].observation_count = 0, r => r.dimensions[0].metrics[0].weight = 2,
    r => r.dimensions[0].score = NaN, r => r.dimensions[0].metrics[0].value = Infinity]) {
    const report = tennisFixture(); mutate(report); assert.equal(parse(report), null);
  }
});

test("the old saturated score scale cannot survive validation or late-result retention", () => {
  const old = tennisFixture(); old.version = "tennis-action-assessment-v1.0.0"; old.rubric.version = "tennis-action-rubric-v1.0.0";
  assert.equal(parse(old), null);
  const incoming = tennisResult(); delete incoming.tennis_assessment;
  assert.equal(retainTennisAssessment(incoming, { ...tennisResult(), tennis_assessment: old }).tennis_assessment, undefined);
  const mixed = tennisFixture(); mixed.rubric.version = "tennis-action-rubric-v1.0.0";
  assert.equal(parse(mixed), null);
});

test("mixed actions use their measured contribution weights instead of averaging unlike metric IDs equally", () => {
  const report = tennisFixture(), dimension = report.dimensions[0], metric = dimension.metrics[0];
  dimension.metrics = [{ ...metric, score: 80, weight: 100 / 101 }, { ...metric, id: "serve_support", score: 20, weight: 1 / 101 }];
  dimension.score = (80 * 100 + 20) / 101;
  report.overall_score = (dimension.score + 60 + 70 + 80 + 90) / 5;
  assert.ok(parse(report));
  const wrongAverage = structuredClone(report); wrongAverage.dimensions[0].score = 50; wrongAverage.overall_score = 70;
  assert.equal(parse(wrongAverage), null, "one serve cannot receive the same action weight as 100 baseline swings");
  for (const invalid of [0, -1, NaN, Infinity]) {
    const bad = structuredClone(report); bad.dimensions[0].metrics[0].weight = invalid; assert.equal(parse(bad), null);
  }
  dimension.metrics.push({ ...metric, id: "unmeasured", score: null, value: null, observation_count: 0, weight: 1 });
  assert.ok(parse(report), "unavailable metrics do not enter the weight denominator");
  report.rubric.aggregation = "mean_episode_metric_scores_then_equal_dimension_mean";
  assert.equal(parse(report), null, "the earlier aggregation is not the current score scale");
});

test("partial radar keeps missing dimensions empty while genuine zero remains zero", () => {
  const report = tennisFixture(); report.status = "partial";
  report.dimensions[0].score = null; report.dimensions[0].metrics[0].score = null; report.dimensions[0].metrics[0].value = null;
  report.overall_score = 75;
  const parsed = parse(report); assert.equal(parsed.dimensions[0].score, null);
  const html = render(parsed); assert.match(html, />—<\/text>/); assert.doesNotMatch(html, /class="tennis-radar-shape"/);
  assert.match(html, /本次已测 4 个动作环节/);
  report.status = "scored"; report.dimensions[0].score = 0; report.dimensions[0].metrics[0].score = 0; report.dimensions[0].metrics[0].value = 0; report.overall_score = 60;
  assert.equal(parse(report).dimensions[0].score, 0); assert.match(render(parse(report)), />0\.0<\/text>/);
});

test("practice timestamps must fit the source video and an untimed drill never invents a seek", () => {
  for (const patch of [{ start_ms: -1 }, { start_ms: 2200 }, { start_ms: NaN }, { end_ms: 10000 }, { end_ms: null }]) {
    const report = tennisFixture(); Object.assign(report.improvements[0], patch); assert.equal(parse(report), null);
  }
  const report = tennisFixture(); report.improvements.forEach(item => { item.start_ms = null; item.end_ms = null; });
  const parsed = parse(report); assert.doesNotMatch(render(parsed), /回看这段动作/);
  assert.equal(tennisImprovementFocus(parsed.improvements[0]).timestampMs, undefined);
  const focus = tennisImprovementFocus(tennisFixture().improvements[0]); assert.equal(focus.timestampMs, 1000); assert.match(focus.question, /复测/);
});

test("default summary shows one score, five dimensions and two actionable drills without technical copy", () => {
  const html = render(parse(tennisFixture()), { onSaveFocus() {} });
  assert.match(html, /<strong>70\.0<\/strong>/); assert.match(html, /五维动作评分图/);
  assert.equal((html.match(/class="tennis-radar-value"/g) ?? []).length, 5);
  assert.equal((html.match(/class="tennis-drill"/g) ?? []).length, 2);
  assert.equal((html.match(/怎么练/g) ?? []).length, 2); assert.equal((html.match(/复测目标/g) ?? []).length, 2);
  assert.match(html, /本次分析了 2 段挥拍/);
  assert.match(html, /试验评分，不代表竞技等级/);
  assert.match(html, /继续保持/); assert.match(html, /随挥后能恢复准备姿势/);
  assert.match(html, /对应片段 1\.0–2\.2 秒/);
  assert.doesNotMatch(html, /置信|算法|规则未实现|P0080|Word|technical|coach_calibrated|触球质量/);
});

test("legacy report has one loading message and all older measurement panels start folded after playback", () => {
  const result = tennisResult(); delete result.tennis_assessment; result.training_evaluation = { score_0_to_100: 99 };
  const html = renderToStaticMarkup(createElement(LiveResults, { result, assessment: null, pending: false, trajectory: null,
    journal: createElement("div", { id: "journal-test" }, "关注点"), details: createElement("div", { id: "tools-test" }, "工具"), replay: createElement("video", { id: "video-test" }) }));
  const details = html.indexOf('id="details"'), primary = html.slice(0, details);
  assert.equal((primary.match(/正在生成动作评分/g) ?? []).length, 1);
  assert.doesNotMatch(primary, /process-overall-reference|>99|待观察|0 \/ 5/);
  assert.ok(html.indexOf('id="video-test"') < details); assert.ok(html.indexOf('id="journal-test"') > details); assert.ok(html.indexOf('id="tools-test"') > details);
  assert.match(html, /<details class="report-all-details" id="details">/); assert.doesNotMatch(html, /<details[^>]*\bopen(?:=|>)/);
});

test("unavailable and offline states use one clear action instead of a page of empty scores", () => {
  assert.match(render(null, { offline: true }), /打开在线报告更新/);
  assert.match(render(null, { error: true, onRetry() {} }), /重新读取/);
  const missingSource = render(null, { message: "原视频来源未读取完整，请重新读取报告。", onRetry() {} });
  assert.match(missingSource, /原视频来源未读取完整/); assert.match(missingSource, /重新读取<\/button>/);
  assert.doesNotMatch(missingSource, /正在生成动作评分|tennis-loading-dot/);
  const report = tennisFixture(); report.status = "unavailable"; report.overall_score = null;
  report.dimensions.forEach(d => { d.score = null; d.observation_count = 0; d.metrics.forEach(m => { m.score = null; m.value = null; m.observation_count = 0; }); });
  const html = render(parse(report)); assert.match(html, /先完成移动/); assert.doesNotMatch(html, /tennis-radar|tennis-score-number|正在生成/);
  report.reason_zh = "动作姿态记录缺失或来源校验未通过。";
  assert.match(render(parse(report)), /来源校验未通过/);
  assert.doesNotMatch(render(parse(report)), /未完整入镜/);
  report.reason_zh = { unsafe: true }; assert.equal(parse(report), null);
});

test("late supplemental results retain a loaded score only for the same job and independently bound video", () => {
  const previous = tennisResult(), incoming = tennisResult(); delete incoming.tennis_assessment;
  assert.equal(tennisAssessmentOf(retainTennisAssessment(incoming, previous)).overall_score, 70);
  for (const mutate of [r => { r.job_id = "foreign-job"; }, r => { r.input.video.sha256 = "b".repeat(64); }, r => { r.input.video.duration_ms = 500; }]) {
    const next = structuredClone(incoming); mutate(next); assert.equal(retainTennisAssessment(next, previous).tennis_assessment, undefined);
  }
  const explicit = { ...incoming, tennis_assessment: { version: "unknown" } };
  assert.equal(retainTennisAssessment(explicit, previous), explicit, "do not hide a new explicit invalid server result with a previous score");
});

test("complete episode navigation is source-bound, strictly timed and reduced to necessary fields", () => {
  const report = tennisFixture(); report.episodes[0].metrics = { private_debug: 123 };
  const parsed = parse(report);
  assert.equal(parsed.episodes.length, 2); assert.equal(parsed.episodes[0].metrics, undefined);
  assert.deepEqual(Object.keys(parsed.episodes[0]).sort(), ["end_ms", "episode_id", "family", "peak_ms", "start_ms"]);
  for (const patch of [{ episode_id: "" }, { episode_id: "../foreign" }, { family: "unknown" }, { start_ms: -1 }, { start_ms: 1500 },
    { peak_ms: 2200 }, { peak_ms: Infinity }, { end_ms: 9001 }, { end_ms: null }]) {
    const value = tennisFixture(); Object.assign(value.episodes[0], patch); assert.equal(parse(value).episodes.length, 1, JSON.stringify(patch));
  }
  const duplicate = tennisFixture(); duplicate.episodes.push({ ...duplicate.episodes[0], start_ms: 5000, peak_ms: 5500, end_ms: 6000 });
  assert.deepEqual(parse(duplicate).episodes.map(e => e.episode_id), ["complete-swing-2"], "ambiguous episode IDs never select a different occurrence");
  assert.deepEqual(validatedTennisAssessment(report, { ...context, durationMs: undefined }).episodes, [], "no duration means no verified navigation range");
  const legacy = tennisFixture(); delete legacy.episodes; assert.equal(parse(legacy).episodes, undefined);
  assert.deepEqual(parse({ ...report, episodes: "untrusted" }).episodes, []);
  assert.deepEqual(tennisAssessmentOf({ ...tennisResult(), tennis_assessment: parsed }).episodes, parsed.episodes, "the lightweight response retains canonical navigation when merged");
});

test("v1.2 validates one subject and recomputes contributions while v1.1 remains readable as history", () => {
  const report = tennisScopedFixture(), parsed = parse(report);
  assert.ok(parsed); assert.equal(parsed.version, "tennis-action-assessment-v1.2.0");
  assert.equal(parsed.episodes[0].subject_id, "subject-1");
  assert.equal(parsed.subject_scope.cross_subject_aggregation, false);
  assert.deepEqual(parse(parsed), parsed, "canonical data survives retention and backups");
  assert.ok(parse(tennisFixture()));
  assert.equal(validatedTennisAssessment(report, { ...context, durationMs: undefined }), null);
  for (const mutate of [r => { delete r.subject_scope; }, r => { r.subject_scope.cross_subject_aggregation = true; },
    r => { r.episodes[1].subject_id = "legacy-track-2-epoch-0"; }, r => { r.episodes[1].person_track_id = 2; },
    r => { r.subject_scope.subjects[1].included_in_report = true; }, r => { r.subject_scope.selected_subject_id = null; },
    r => { r.episode_validity[1].subject_id = "legacy-track-2-epoch-0"; }, r => { r.episode_validity.pop(); r.episode_validity.pop(); },
    r => { r.subject_scope.subjects[0].measured_episode_count = 4; }, r => { r.dimensions[0].observation_count = 4; },
    r => { r.dimensions[0].metrics[0].observation_count = 4; }, r => { r.episodes[0].metrics.movement_metric.score = 100; },
    r => { r.rubric.version = "tennis-action-rubric-v1.1.0"; }]) {
    const invalid = tennisScopedFixture(); mutate(invalid); assert.equal(parse(invalid), null);
  }
});

test("v1.2 coverage is checked against measured episode requirements and actual scoring contributions", () => {
  for (const mutate of [r => { r.pattern_coverage.measured_requirements = 4; }, r => { r.pattern_coverage.fraction = 1; },
    r => { r.pattern_coverage.verified_technique_episodes = 2; }, r => { r.pattern_coverage.profiles = ["serve"]; },
    r => { r.episodes[0].motion_pattern.coverage.measured_requirements = 2; }, r => { r.episodes[0].motion_pattern.profile_id = "serve"; },
    r => { r.episodes[0].motion_pattern.requirements[0].metric_ids = ["unmeasured_feature"]; },
    r => { r.dimensions[4].metrics[0].id = "unrelated"; }]) {
    const invalid = tennisScopedFixture(); mutate(invalid); assert.equal(parse(invalid), null);
  }
});

test("v1.2 main view names the scoped target and measurement gaps without claiming complete force analysis", () => {
  const report = parse(tennisScopedFixture());
  const html = render(report);
  assert.match(html, /本次锁定目标，汇总 2 段动作/);
  assert.match(html, /另有 1 段属于其他轨迹，未混入评分/);
  assert.match(html, /2 \/ 4 项次有可测依据/);
  assert.match(html, /不是动作合格率/);
  assert.match(html, /不表示整套发力模式已被验证/);
  const detail = renderToStaticMarkup(createElement(TennisAssessmentDetails, { report }));
  assert.match(detail, /仅使用通用动作指标/); assert.match(detail, /不要求击后立即减速/);
  assert.doesNotMatch(html + detail, /VIS-GS|P0097|source_binding|legacy-track-2/);
  assert.match(render(parse(tennisFixture())), /历史版本：没有逐目标归属记录/);
});

test("v1.2 empty measurement retains a subject scope without manufacturing episodes or coverage", () => {
  const report = tennisScopedFixture(); report.status = "unavailable"; report.overall_score = null;
  report.episodes = []; report.episode_validity = []; report.strengths = []; report.improvements = [];
  report.dimensions.forEach(d => { d.score = null; d.observation_count = 0; d.metrics = []; });
  report.subject_scope.subjects.forEach(s => { s.measured_episode_count = 0; });
  report.pattern_coverage = { measured_requirements: 0, applicable_requirements: 0, fraction: null, verified_technique_episodes: 0, generic_episodes: 0, full_source_technique_covered: false, profiles: [] };
  assert.ok(parse(report)); assert.match(render(parse(report)), /汇总 0 段动作/);
  assert.doesNotMatch(render(parse(report)), /tennis-radar|tennis-score-number/);
});

test("v1.3 requires its matching observation contract and preserves v1.1/v1.2 history", () => {
  const report = currentFixture(), parsed = parse(report);
  assert.ok(parsed); assert.deepEqual(parse(parsed), parsed);
  assert.ok(parse(tennisScopedFixture())); assert.ok(parse(tennisFixture()));
  for (const mutate of [r => { r.rubric.version = "tennis-action-rubric-v1.2.0"; },
    r => { r.episodes[0].motion_pattern.version = "tennis-motion-patterns-v1.0.0"; },
    r => { r.episodes[1].start_ms = 1800; }, r => { r.dimensions[0].observation_count = 3; },
    r => { r.pattern_coverage.measured_requirements += 1; },
    r => { r.episodes[0].motion_pattern.classification_provenance.method = "agent_visual_review"; }]) {
    const invalid = currentFixture(); mutate(invalid); assert.equal(parse(invalid), null);
  }
});

test("pose discontinuity is explained separately from subject identity without exposing internal codes", () => {
  const raw = currentFixture();
  raw.episode_validity.push({ episode_id: "active-gap", status: "rejected", reason_codes: ["active_wrist_discontinuity_in_motion"] });
  raw.episode_validity.push({ episode_id: "hand-ambiguous", status: "rejected", reason_codes: ["unresolved_racket_hand_with_competing_arm_sequence"] });
  raw.episode_validity.push({ episode_id: "outside-peak", status: "rejected", reason_codes: ["external_peak_motion_geometry_unverified"] });
  raw.episodes[0].quality_reason_codes = ["non_active_wrist_discontinuity_unmeasured"];
  const parsed = parse(raw); assert.ok(parsed);
  assert.deepEqual(parse(parsed), parsed);
  const html = render(parsed) + renderToStaticMarkup(createElement(TennisAssessmentDetails, { report: parsed }));
  assert.match(html, /持拍手姿态不连续，暂无法评分/);
  assert.match(html, /持拍手尚未可靠确认，两侧手腕先后运动/);
  assert.match(html, /运动峰落在候选挥拍区间外，选中片段自身缺少完整前挥证据/);
  assert.match(html, /另一只手的姿态不连续，依赖该手的项目按实际缺测处理/);
  assert.doesNotMatch(html, /active_wrist|non_active_wrist|归属失败/);
  raw.episodes[0].quality_reason_codes = ["<script>untrusted</script>"];
  assert.equal(parse(raw), null);
});

test("v1.3 return type requires incoming-ball context and cannot duplicate its baseline alias", () => {
  function returned() {
    const report = currentFixture(), episode = report.episodes[0];
    episode.family = "return";
    episode.return_context = { opponent_serve_id: "opponent-serve", server_track_id: 9, ball_track_id: 20,
      ball_observations: 8, incoming_start_ms: 800, incoming_end_ms: 1400, receiver_peak_ms: 1500,
      verification_method: "source_bound_opponent_serve_and_incoming_ball" };
    episode.motion_pattern.profile_id = "return_forehand"; episode.motion_pattern.classification_status = "verified";
    episode.motion_pattern.classification_provenance = { method: "agent_visual_review", reviewer: "source-frame-review" };
    report.pattern_coverage.verified_technique_episodes = 1; report.pattern_coverage.generic_episodes = 1;
    report.pattern_coverage.profiles = ["generic", "return_forehand"];
    return report;
  }
  const report = returned(), parsed = parse(report);
  assert.ok(parsed); assert.deepEqual(parsed.episodes[0].return_context, report.episodes[0].return_context);
  assert.deepEqual(parse(parsed), parsed);
  for (const mutate of [r => { delete r.episodes[0].return_context; },
    r => { r.episodes[0].return_context.ball_observations = 2; },
    r => { r.episodes[0].return_context.server_track_id = 1; },
    r => { r.episodes[0].return_context.incoming_end_ms = 5000; },
    r => { r.episodes[0].family = "baseline"; },
    r => { r.episodes[1].start_ms = 1000; r.episodes[1].peak_ms = 1500; r.episodes[1].end_ms = 2200; },
    r => { r.episodes[0].motion_pattern.classification_provenance.reviewer = ""; }]) {
    const invalid = returned(); mutate(invalid); assert.equal(parse(invalid), null);
  }
});
