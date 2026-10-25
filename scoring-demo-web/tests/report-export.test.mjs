import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import ts from "typescript";

function compiledUrl(fileUrl) {
  // Keep source JSON imports shared with production, without a generated copy.
  if (fileUrl.pathname.endsWith(".json")) return `data:text/javascript;base64,${Buffer.from(`export default ${fs.readFileSync(fileUrl, "utf8")}`).toString("base64")}`;
  const source = fs.readFileSync(fileUrl, "utf8");
  let js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
  js = js.replace(/from "([^"]+)"/g, (_match, name) => {
    const url = name.startsWith(".") ? compiledUrl(new URL(name.endsWith(".json") ? name : `${name}.ts`, fileUrl)) : import.meta.resolve(name);
    return `from ${JSON.stringify(url)}`;
  });
  return `data:text/javascript;base64,${Buffer.from(js).toString("base64")}`;
}
const { getReportExportGate, buildPracticeReport, renderReportMarkdown, renderReportHtml, buildReportBackup, reportDownloadName } = await import(compiledUrl(new URL("../app/lib/report-export.ts", import.meta.url)));
const { reportVideoDurationMs } = await import(compiledUrl(new URL("../app/lib/workspace-navigation.ts", import.meta.url)));
const { readReportJson, MAX_REPORT_FILE_BYTES } = await import(compiledUrl(new URL("../app/lib/report-file.ts", import.meta.url)));
const { processEvaluationOf } = await import(compiledUrl(new URL("../app/lib/process-evaluation.ts", import.meta.url)));
const { cardExecutionOf } = await import(compiledUrl(new URL("../app/lib/card-execution.ts", import.meta.url)));

test("real internal card evidence survives backups but is absent from readable exports", async () => {
  const input = completed();
  const report = JSON.parse(fs.readFileSync(new URL("./fixtures/card-execution-real.json", import.meta.url), "utf8"));
  input.evidence.result.card_execution = report;
  const expected = cardExecutionOf(report);
  const backup = buildReportBackup(input);
  const serialized = JSON.stringify(backup);
  const imported = await readReportJson({ size: Buffer.byteLength(serialized), text: async () => serialized });
  assert.deepEqual(cardExecutionOf(imported.result.card_execution), expected);
  assert.equal(expected.summary.original_scored_cards, 0);
  assert.equal(expected.overall_score, null);
  assert.ok(expected.summary.proxy_scored_instances > 0);
  assert.ok(expected.summary.review_instances > 0);
  const readable = buildPracticeReport(input);
  assert.equal(readable.sections.some(row => row.title.startsWith("逐卡")), false);
  for (const content of [renderReportMarkdown(readable), renderReportHtml(readable)]) {
    assert.doesNotMatch(content, /逐卡计算|逐卡视频实例|GS\d{2}-M\d{2}|FS\d{2}-M\d{2}/);
  }
  assert.deepEqual(imported.result.card_execution.cards, expected.cards);
  report.cards.find(card => card.instances.some(instance => instance.status === "proxy_scored")).instances.find(instance => instance.status === "proxy_scored").score = 99;
  const invalid = buildReportBackup(input).result.card_execution;
  assert.equal(invalid.summary.proxy_scored_instances, expected.summary.proxy_scored_instances - 1);
});

test("missing scores remain null in versioned backups and cached numbers never override recomputation", async () => {
  const input = completed();
  delete input.evidence.result.process_evaluation;
  const backup = buildReportBackup(input);
  assert.equal(backup.result.process_evaluation.relative_reference.version, "relative-reference-v3.0.0");
  assert.equal(backup.result.process_evaluation.relative_reference.overall_score, null);
  backup.result.process_evaluation.relative_reference.overall_score = 99;
  const text = JSON.stringify(backup), imported = await readReportJson({ size: Buffer.byteLength(text), text: async () => text });
  const parsed = processEvaluationOf(imported.result.process_evaluation);
  assert.equal(parsed.relative_reference.overall_score, null);
  assert.equal(parsed.relative_reference.legacy_relative_reference.overall_score, 50);
  assert.equal(parsed.relative_reference.legacy_relative_reference.contributes_to_current_score, false);
  const readable = buildPracticeReport(input);
  assert.ok(readable.sections.some(row => row.title === "训练评分：未评分"));
  assert.doesNotMatch(renderReportMarkdown(readable), /相对参考总分：50|中性先验/);
});

const actualEvidenceRoot = new URL("../../.codex_tmp/autodl-sam-coordination-20261018/qa-evidence/", import.meta.url);
const actualProcessCases = [
  ["d730 preparation", "d730-preparation-with-translation-final-process.json", 598, 380],
  ["RTMPose", "full97s-three-model-scoring/rtmpose/service-projection-v2/process-evaluation.json", 600, 380],
  ["DINOv3", "full97s-three-model-scoring/dinov3-body/service-projection-v2/process-evaluation.json", 488, 337],
  ["ViTH", "full97s-three-model-scoring/vith-body/service-projection-v2/process-evaluation.json", 422, 316],
];
for (const [name, path, fullCount, displayCount] of actualProcessCases) {
  const file = new URL(path, actualEvidenceRoot);
  test(`actual ${name} process evidence survives backup and bounded re-import including zero and model-only provenance`, { skip: !fs.existsSync(file) }, async () => {
    const raw = JSON.parse(fs.readFileSync(file, "utf8"));
    const parsed = processEvaluationOf(raw);
    const actualResult = name === "d730 preparation"
      ? JSON.parse(fs.readFileSync(new URL("../../.codex_tmp/scoring-followup-20261016/demo-result.json", import.meta.url), "utf8"))
      : (() => { const summary = JSON.parse(fs.readFileSync(new URL("../summary.json", new URL(path, actualEvidenceRoot)), "utf8"));
        return { status: "ready", job_id: summary.job_id, summary, action_recognition: summary.action_recognition }; })();
    const input = { mode: "live", uploadState: "complete", evidence: { result: { ...actualResult, process_evaluation: raw } } };
    const text = JSON.stringify(buildReportBackup(input), null, 2), size = Buffer.byteLength(text);
    assert.ok(size < MAX_REPORT_FILE_BYTES);
    const imported = await readReportJson({ size, text: async () => text });
    const restored = processEvaluationOf(imported.result.process_evaluation);
    assert.deepEqual(restored, parsed);
    const preparation = restored.dimensions.find(row => row.id === "preparation");
    assert.deepEqual([preparation.measured_criterion_count, preparation.returned_measured_criterion_count], [fullCount, displayCount]);
    for (const before of raw.dimensions.flatMap(row => row.criteria).filter(row => row.status === "measured" && row.value === 0)) {
      assert.ok(restored.dimensions.some(dimension => dimension.criteria.some(row => row.id === before.id && row.episode_id === before.episode_id && row.start_ms === before.start_ms && row.status === "measured" && row.value === 0 && row.score === before.score)));
    }
    const modelRows = parsed.dimensions.flatMap(row => row.criteria).filter(row => row.measurement_provenance?.pose_source === "sam3d");
    if (["DINOv3", "ViTH"].includes(name)) {
      assert.ok(modelRows.length > 100);
      for (const row of modelRows) assert.equal(row.score, null);
      const readable = buildPracticeReport({ ...input, evidence: { result: imported.result } });
      for (const title of ["挥拍协调 · 过程依据", "随挥与衔接 · 过程依据"]) {
        const section = readable.sections.find(section => section.title === title);
        assert.match(section.paragraphs.join(" "), /已有单目模型估计/);
        assert.doesNotMatch(section.paragraphs.join(" "), /尚缺连续运动测量/);
        assert.ok(section.rows.every(row => row[2] === "未评分" || row[2].includes("历史速度趋势；不参与当前分数")));
        assert.equal(section.columns[1], "测量值");
      }
    }
    if (name === "RTMPose") assert.deepEqual([restored.dimensions[0].measured_criterion_count, restored.dimensions[0].returned_measured_criterion_count], [1052, 336]);
  });
}

test("a fabricated matching score on model-only evidence stays unscored through backup and import", async () => {
  const input = completed();
  input.evidence.result.process_evaluation = {
    version: "process-evaluation-v1.1.0", semantics: "observable_motion_process_reference", is_calibrated: false, overall_score: null,
    dimensions: [{ id: "swing", status: "partial", score: 80, aggregation: "mean_of_independent_samples_for_one_explicit_rule", criteria: [{
      id: "acceleration_trend", label_zh: "腕速变化占比", status: "measured", value: 80, unit: "%", score: 80, sample_count: 8,
      reason_zh: "单目模型估计", rule_zh: "变化占比 × 100", start_ms: 1200, end_ms: 1800, episode_id: "model-only",
      source: "action_recognition.motion_analysis.episodes.process_motion.phases.wrist.speed_increase_fraction", assessment_status: "measurement_only",
      measurement_provenance: { pose_source: "sam3d", observation_semantics: "monocular_model_estimate", assessment_status: "measurement_only", scoring_allowed: false, confidence_available: false, visibility_verified: false, model_estimate_sample_count: 20 },
    }] }],
  };
  const text = JSON.stringify(buildReportBackup(input)), imported = await readReportJson({ size: Buffer.byteLength(text), text: async () => text });
  const row = processEvaluationOf(imported.result.process_evaluation).dimensions.find(row => row.id === "swing");
  assert.equal(row.score, null);
  assert.equal(row.criteria[0].score, null);
  assert.equal(row.criteria[0].value, 80);
  assert.equal(row.criteria[0].assessment_status, "measurement_only");
  const table = buildPracticeReport(input).sections.find(section => section.title === "挥拍协调 · 过程依据");
  assert.equal(table.rows[0][2], "80 历史速度趋势；不参与当前分数");
});

test("real preparation path measurements retain source evidence and counts through a JSON backup round trip", () => {
  const fixture = JSON.parse(fs.readFileSync(new URL("./fixtures/process-preparation-real.json", import.meta.url), "utf8"));
  const input = completed();
  const rawPath = fixture.criteria.find(row => row.id === "preparation_candidate_path_length_torso");
  assert.equal(rawPath.value, .3689);
  assert.deepEqual([rawPath.episode_id, rawPath.start_ms, rawPath.end_ms], ["5189075b8ba9966d741a", 2433, 2700]);
  input.evidence.result.process_evaluation = {
    version: "process-evaluation-v1.1.0", semantics: "observable_motion_process_reference", is_calibrated: false, overall_score: null,
    dimensions: [{ id: "preparation", status: "partial", score: null, aggregation: "none", criteria: fixture.criteria,
      measured_criterion_count: fixture.criteria.length,
      criteria_selection: { full_count: fixture.criteria.length, returned_count: fixture.criteria.length, is_truncated: false,
        policy: "all_scoring_criteria_and_uniform_time_ordered_unscored_measurements_per_feature", max_footwork_observations_per_feature: 12,
        count_scope: "all_validated_input_episodes_before_display_sampling" } }],
  };
  const before = processEvaluationOf(input.evidence.result.process_evaluation).dimensions.find(row => row.id === "preparation");
  const backup = JSON.parse(JSON.stringify(buildReportBackup(input)));
  const restored = processEvaluationOf(backup.result.process_evaluation).dimensions.find(row => row.id === "preparation");
  assert.equal(restored.measured_criterion_count, fixture.criteria.length);
  assert.deepEqual(restored.criteria_selection, before.criteria_selection);
  assert.deepEqual(restored.criteria, before.criteria);
  const path = restored.criteria.find(row => row.id === rawPath.id);
  assert.equal(path.original_metric_evidence.wrist.path_length_torso, .3689);
  assert.deepEqual(path.original_metric_evidence, rawPath.original_metric_evidence);
  assert.deepEqual(path.source_frames, rawPath.source_frames);
  assert.deepEqual(path.source_timestamps_ms, rawPath.source_timestamps_ms);
  assert.deepEqual(path.reference, rawPath.reference);
  assert.deepEqual(path.measurement_provenance ?? {}, rawPath.measurement_provenance);
  assert.equal(path.source, rawPath.source);
  assert.equal(path.video_id, rawPath.video_id);
  assert.equal(path.score, null);
});

test("path metric exemption accepts only finite nonnegative measurements and still scrubs private paths", () => {
  const input = completed();
  const provenance = { pose_source: "sam3d", observation_semantics: "monocular_model_estimate", scoring_allowed: false, confidence_available: false };
  input.evidence.result.action_recognition = { audit_fixture: {
    measurement_provenance: provenance, valid: { path_length_torso: .3689, wrist_path_torso: .5 },
    invalid: ["/root/private/path.json", { nested: "C:\\Users\\Private\\a.json" }, null, -1, Infinity, NaN].map(path_length_torso => ({ path_length_torso })),
    source_path: "/root/private/source.json", path_length_torso_path: "/root/private/metric.json",
    source_url: "https://private.example/evidence", token: "DO_NOT_EXPORT_TOKEN",
  } };
  const backup = JSON.parse(JSON.stringify(buildReportBackup(input)));
  const saved = backup.result.action_recognition.audit_fixture;
  assert.deepEqual(saved.valid, { path_length_torso: .3689, wrist_path_torso: .5 });
  assert.deepEqual(saved.invalid, [{}, {}, {}, {}, {}, {}]);
  assert.deepEqual(saved.measurement_provenance, provenance);
  assert.doesNotMatch(JSON.stringify(saved), /\/root\/|Private|DO_NOT_EXPORT|source_path|source_url|path_length_torso_path|token/);
});

test("full evidence backups above five MiB import while oversized files are rejected before reading", async () => {
  const input = completed();
  input.evidence.result.process_evaluation = JSON.parse(fs.readFileSync(new URL("./fixtures/process-v1.1-real.json", import.meta.url), "utf8"));
  input.evidence.result.action_recognition = { ...input.evidence.result.action_recognition, retained_evidence_note: "a".repeat(6 * 1024 * 1024) };
  const backup = buildReportBackup(input);
  // Repeat independent result metadata to exercise the bounded importer with a real exported envelope.
  backup.result.test_archive_payload = "a".repeat(6 * 1024 * 1024);
  const text = JSON.stringify(backup, null, 2), size = Buffer.byteLength(text);
  assert.ok(size > 5 * 1024 * 1024 && size < MAX_REPORT_FILE_BYTES);
  const restored = await readReportJson({ size, text: async () => text });
  assert.equal(restored.result.process_evaluation.dimensions[0].measured_criterion_count, 1052);
  assert.equal(restored.result.process_evaluation.dimensions[0].returned_measured_criterion_count, 336);
  assert.equal(restored.result.process_evaluation.dimensions.reduce((n, row) => n + (row.source_basis?.card_count ?? 0), 0), 298);
  let read = false;
  await assert.rejects(readReportJson({ size: MAX_REPORT_FILE_BYTES + 1, text: async () => { read = true; return "{}"; } }), /128 MiB/);
  assert.equal(read, false);
});

test("backup removes identical motion copies but preserves different assessment evidence", () => {
  const input = completed();
  const motion = { status: "ready", motion_analysis: { observations: [1, 2] } };
  input.evidence.result.action_recognition = motion;
  input.evidence.assessment = { assessment_version: "v1", registry_version: "v1", techniques: [], action_recognition: structuredClone(motion) };
  input.summary = { job_id: input.evidence.jobId, processing: {}, action_recognition: structuredClone(motion) };
  const backup = buildReportBackup(input);
  assert.deepEqual(backup.result.action_recognition, motion);
  assert.equal(backup.assessment.action_recognition, undefined);
  assert.equal(backup.summary.action_recognition, undefined);
  input.evidence.assessment.action_recognition.motion_analysis.observations.push(3);
  assert.deepEqual(buildReportBackup(input).assessment.action_recognition.motion_analysis.observations, [1, 2, 3]);
});

test("process scores and observed direction survive all exports without filling missing dimensions", () => {
  const input = completed();
  input.evidence.result.process_evaluation = {
    version: "process-evaluation-v1.0.0", semantics: "observable_motion_process_reference", is_calibrated: false, overall_score: null,
    dimensions: [{ id: "swing", name_zh: "挥拍", status: "partial", score: 62.5,
      summary_zh: "只有加速趋势可评分，方向为二维观察。", sample_count: 1, scored_rule_count: 1, total_rule_count: 4,
      scored_sample_count: 1, scoring_rule_count: 1, aggregation: "mean_of_independent_samples_for_one_explicit_rule",
      criteria: [{ id: "acceleration_trend", label_zh: "挥拍加速趋势参考", status: "measured", value: 62.5, unit: "%", score: 62.5,
        reason_zh: "正向变化占62.5%。", rule_zh: "工程试行：正向变化占比乘100。", source: "action_recognition.motion_analysis.episodes.process_motion.phases.wrist.speed_increase_fraction",
        start_ms: 1200, end_ms: 1800, episode_id: "swing-1", sample_count: 8 },
      { id: "swing_direction", label_zh: "加速挥拍腕部运动方向", status: "measured", value: -30, unit: "deg", score: null,
        reason_zh: "画面投影方向。", rule_zh: "不按画面左右判对错。", source: "action_recognition.motion_analysis.episodes.process_motion.phases.wrist.direction_angle_deg",
        start_ms: 1200, end_ms: 1800, episode_id: "swing-1" }],
    }],
  };
  const report = buildPracticeReport(input);
  const overview = report.sections.find(section => section.title === "训练指导五维评价");
  assert.equal(overview.rows.length, 5);
  assert.equal(overview.rows.find(row => row[0] === "挥拍协调")[1], "未提供");
  assert.equal(overview.rows.find(row => row[0] === "触球控制")[1], "未提供");
  assert.match(renderReportHtml(report), /尚未经过教练标定/);
  assert.match(renderReportMarkdown(report), /不按画面左右判对错/);
  const backup = JSON.parse(JSON.stringify(buildReportBackup(input)));
  assert.equal(backup.result.process_evaluation.overall_score, null);
  assert.equal(backup.result.process_evaluation.dimensions.find(row => row.id === "swing").score, null);
  assert.equal(backup.result.process_evaluation.dimensions.find(row => row.id === "swing").criteria[0].legacy_diagnostic.score, 62.5);
  assert.equal(backup.result.process_evaluation.dimensions.find(row => row.id === "contact").score, null);
  input.evidence.result.process_evaluation.version = "unverified-v99";
  const neutral = buildReportBackup(input).result.process_evaluation;
  assert.equal(neutral.version, "unverified-v99");
  assert.equal(processEvaluationOf(neutral), null);
  assert.match(renderReportMarkdown(buildPracticeReport(input)), /数据校验未通过/);
  assert.equal(buildPracticeReport(input).sections.some(section => section.title === "训练指导五维评价"), false);
});

function completed() {
  return {
    mode: "live", uploadState: "complete", job: { id: "job-12345678", status: "completed", original_filename: "发球练习.mp4" },
    evidence: {
      jobId: "job-12345678", error: null,
      result: { job_id: "job-12345678", training_evaluation: { score_0_to_100: null, summary_zh: "有可复核的挥拍候选。", indicator_evaluations: [{ indicator_id: "balance", name_zh: "身体平衡", score_0_to_100: 71, observation_zh: "收拍后出现移动。", suggestion_zh: "低强度复核。", limitations_zh: ["二维姿态代理。"] }] }, actions: [{ family: "footwork", event_code: "FS10", name_zh: "回位", detected_segments: 3 }] },
      assessment: { job_id: "job-12345678", overall_evidence_score_0_to_100: 45, formal_score_available: false, formal_score_message_zh: "没有正式技术等级。", techniques: [{ technique_id: "serve", family: "serve", name_zh: "发球", observed: false, status: "not_observed", evidence_score_0_to_100: 0, limitations_zh: ["缺少触球依据。"] }] },
      trajectory: { job_id: "job-12345678", status: "ready", source: { frames_path: "/root/autodl-tmp/rallymate/service_data/runs/private/frames.jsonl", frame_count: 300, width: 1920, height: 1080 }, ball: { observed_count: 17, coverage_fraction: 0.2, confidence: { mean: 0.7 }, prediction_status: "heuristic_preview", reconstruction: { summary: { observed_count: 17, interpolated_count: 4, segment_count: 2, coverage_fraction: .2 }, segments: [{ segment_id: 1, start_ms: 1100, end_ms: 1800, observed_count: 9, interpolated_count: 2 }], limitations_zh: ["不推断真实落点。"] } }, racket: { observed_count: 20 }, limitations: [] },
    },
    summary: { job_id: "job-12345678", input: { video_path: "/root/private/source.mp4", video: { duration_ms: 12000, width: 1920, height: 1080, frame_count: 300 } }, processing: { processed_frames: 300, elapsed_seconds: 30 }, action_recognition: { status: "candidates_detected", confirmed_contact_count: null, candidates: [{ family: "serve", start_ms: 1200, peak_ms: 1900, end_ms: 2500, contact_confirmed: false, evidence: { racket_associated_frames: 7, pose_samples: 12 }, limitations_zh: ["抛球未确认。"] }, { family: "baseline", start_ms: 4200, peak_ms: 4500, end_ms: 4800, evidence: {} }], limitations_zh: ["仅候选时间区间。"] } },
  };
}

test("backup round trips retain the safe video bounds used to reject out-of-range replay", () => {
  const input = completed();
  input.evidence.result.input = { video: { duration_ms: 5000, width: 1920, height: 1080, frame_count: 125, video_path: "/root/private/video.mp4", token: "private-value" } };
  const backup = JSON.parse(JSON.stringify(buildReportBackup(input)));
  assert.equal(reportVideoDurationMs(backup.result), 5000);
  assert.deepEqual(backup.result.input.video, { duration_ms: 5000, width: 1920, height: 1080, frame_count: 125 });
  delete input.evidence.result.input;
  assert.equal(reportVideoDurationMs(buildReportBackup(input).result), 12000);
});

test("pending, failed, stale-task and empty result exports fail closed even with leftover data", () => {
  for (const overrides of [
    { mode: "live-pending", uploadState: "processing" },
    { uploadState: "error" },
    { uploadState: "ready" },
    { job: { id: "job-12345678", status: "running" } },
    { job: { id: "job-12345678", status: "failed" } },
    { job: { id: "other-12345678", status: "completed" } },
  ]) {
    const input = { ...completed(), ...overrides };
    assert.equal(getReportExportGate(input).allowed, false);
    assert.throws(() => buildPracticeReport(input));
    assert.throws(() => buildReportBackup(input));
  }
  const empty = { ...completed(), summary: null, evidence: { jobId: "job-12345678", result: { job_id: "job-12345678" } } };
  assert.equal(getReportExportGate(empty).allowed, false);
  assert.throws(() => buildPracticeReport(empty), /空报告/);
});

test("a completed task exports readable observations, actual video metadata, candidate intervals and trajectory limits", () => {
  const report = buildPracticeReport(completed(), "2026-09-24T12:00:00Z");
  const md = renderReportMarkdown(report);
  assert.match(md, /发球练习/);
  assert.match(md, /1920 × 1080/);
  assert.match(md, /12 秒/);
  assert.match(md, /发球动作候选/);
  assert.match(md, /底线挥拍候选/);
  assert.match(md, /1\\\.2–2\\\.5 秒/);
  assert.match(md, /关联球拍帧数：7/);
  assert.match(md, /身体平衡/);
  assert.doesNotMatch(md, /71 \/ 100/);
  assert.match(md, /具体评分缺口见各项说明/);
  assert.match(md, /未确认触球/);
  assert.match(md, /不代表真实落点/);
  assert.match(md, /高分不代表动作正确/);
  assert.equal(reportDownloadName(report, "html"), "rallymate-analysis-job-12345678.html");
  assert.doesNotMatch(md, /live-pending|\/root\/|frames\.jsonl/);
});

test("motion report exports measured stages and explicit return limitations without legacy candidate clutter", () => {
  const input = completed();
  input.evidence.result.action_recognition = { motion_analysis: { schema_version: "1.0.0", contact_confirmed: false, families: { baseline: { reason_zh: "已提供运动测量", episodes: [{ episode_id: "x", family: "baseline", contact_confirmed: false, start_ms: 1000, end_ms: 2200, classification: { label_zh: "正手挥拍", status: "rule_inferred", reason_zh: "持拍手明确。" }, phases: [{ label_zh: "加速", start_ms: 1200, end_ms: 1800 }], metrics: { peak_wrist_speed_torso_per_s: 3.2, elbow_extension_deg: 40 }, limitations_zh: ["类型尚未经过专项准确率验证。"] }] }, return: { reason_zh: "缺少对手发球与来球顺序。", episodes: [] } } } };
  const report = buildPracticeReport(input);
  const html = renderReportHtml(report);
  assert.match(html, /底线运动分析/);
  assert.match(html, /正手挥拍/);
  assert.match(html, /手腕峰值速度（躯干长度\/秒）：3\.2/);
  assert.match(html, /缺少对手发球与来球顺序/);
  assert.match(html, /规则推断参考/);
  assert.doesNotMatch(html, /发球与底线候选证据区间/);
  const episode = input.evidence.result.action_recognition.motion_analysis.families.baseline.episodes[0];
  episode.analysis_status = "partial";
  episode.phases.push({ phase: "follow_through", label_zh: "随挥", status: "unavailable", start_ms: null, end_ms: null });
  episode.metric_notes_zh = ["随挥降速证据不足。"];
  const partialHtml = renderReportHtml(buildPracticeReport(input));
  assert.match(partialHtml, /阶段证据不完整/);
  assert.match(partialHtml, /随挥证据不足/);
  assert.match(partialHtml, /随挥降速证据不足/);
  assert.match(partialHtml, /不代表触球时刻/);
  assert.doesNotMatch(partialHtml, /随挥 未提供有效时间区间/);
});

test("a successful result with missing optional evidence stays exportable and discloses the missing evidence", () => {
  const input = completed();
  input.evidence.trajectory = null;
  input.evidence.error = "轨迹接口暂时不可用";
  input.summary.action_recognition = { status: "no_candidates", candidates: [] };
  assert.equal(getReportExportGate(input).allowed, true);
  const md = renderReportMarkdown(buildPracticeReport(input));
  assert.match(md, /轨迹接口未返回数据/);
  assert.match(md, /不能据此认定视频里没有击球/);
  assert.match(md, /轨迹接口暂时不可用/);
});

test("backend footwork event codes supply missing families without guessing unknown action names", () => {
  const input = completed();
  input.evidence.result.actions = [
    { event_code: "FS01", name_zh: "准备与分腿垫步", detected_segments: 2 },
    { event_code: "FS02", name_zh: "第一步启动", detected_segments: 2 },
    { event_code: "FS09", name_zh: "制动与重新稳定", detected_segments: 2 },
    { event_code: "FS99", name_zh: "发球", detected_segments: 1 },
  ];
  const rows = buildPracticeReport(input).sections.find(section => section.title === "本次动作观察").rows;
  assert.deepEqual(rows.slice(0, 3).map(row => row[1]), ["步伐", "步伐", "步伐"]);
  assert.equal(rows[3][1], "未分类");
});

test("a serve recognition candidate takes precedence over legacy not_observed without inventing readiness", () => {
  const input = completed();
  Object.assign(input.evidence.assessment.techniques[0], { status: "not_observed", observed: false, recognition_status: "candidate", candidate_count: 2 });
  const report = buildPracticeReport(input);
  const row = report.sections.find(section => section.title === "技术证据覆盖").rows[0];
  assert.equal(row[0], "发球");
  assert.equal(row[2], "候选（未确认触球）");
  assert.equal(row[3], "动作待确认");
  assert.match(renderReportMarkdown(report), /发球 \| 发球 \| 候选（未确认触球）/);
  assert.match(renderReportHtml(report), /<td>候选（未确认触球）<\/td>/);
});

test("technical coverage export distinguishes unobserved and unimplemented evidence instead of blanket calibration", () => {
  const input = completed();
  input.evidence.assessment.techniques.push({ name_zh: "触球控制", family: "baseline", observed: false, status: "not_observed", status_reason: "contact_control_rule_not_implemented" });
  const report = buildPracticeReport(input);
  const rows = report.sections.find(section => section.title === "技术证据覆盖").rows;
  assert.equal(rows[0][3], "本次未观测");
  assert.equal(rows[1][3], "评分尚未实现");
  assert.ok(rows.every(row => !row.includes("待教练标定")));
  assert.match(renderReportHtml(report), /<td>评分尚未实现<\/td>/);
});

test("HTML is self-contained, printable and treats imported strings as text, never executable markup", () => {
  const input = completed();
  input.evidence.result.training_evaluation.summary_zh = '<script src="https://evil.example/x.js">alert(1)</script><img src=x onerror=alert(1)>';
  input.evidence.result.training_evaluation.indicator_evaluations[0].name_zh = '[偷取](https://evil.example/x) <svg onload=alert(2)> | x';
  const report = buildPracticeReport(input);
  const html = renderReportHtml(report);
  assert.match(html, /<!doctype html>/);
  assert.match(html, /Content-Security-Policy/);
  assert.match(html, /script-src 'none'/);
  assert.match(html, /@media print/);
  assert.match(html, /&lt;svg/);
  assert.doesNotMatch(html, /<script\b|<img\b|<svg\b|<iframe\b|<link\b|https:\/\/evil/i);
  assert.doesNotMatch(renderReportMarkdown(report), /(?<!\\)\[偷取\]\(|<svg\b|<script\b/);
});

test("readable reports and compatible JSON backup omit server paths, credentials and transport configuration", () => {
  const input = completed();
  input.evidence.result.api_key = "DO_NOT_EXPORT_KEY";
  input.evidence.result.artifact_urls = { private: "http://127.0.0.1/private" };
  input.evidence.assessment.secret = "DO_NOT_EXPORT_SECRET";
  input.summary.input.upstream_metadata = { original_filename: "original.mp4", token: "DO_NOT_EXPORT_TOKEN" };
  input.evidence.result.training_evaluation.summary_zh = '文件 /root/autodl-tmp/private/a.mp4 与 C:\\Users\\Private\\a.mp4；RALLYMATE_API_KEY=KEY_VALUE Bearer BEARER_VALUE';
  const report = buildPracticeReport(input);
  const backup = buildReportBackup(input);
  const combined = renderReportHtml(report) + renderReportMarkdown(report) + JSON.stringify(backup);
  assert.doesNotMatch(combined, /DO_NOT_EXPORT|KEY_VALUE|BEARER_VALUE|\/root\/|C:\\\\Users|127\.0\.0\.1|frames_path|video_path|artifact_urls/);
  assert.equal(backup.schemaVersion, "rallymate-practice-report/1");
  assert.equal(backup.jobId, "job-12345678");
  assert.equal(backup.result.training_evaluation.indicator_evaluations[0].score_0_to_100, null);
  assert.equal(backup.assessment.techniques[0].name_zh, "发球");
  assert.equal(backup.summary.action_recognition.candidates[0].contact_confirmed, false);
  assert.equal(backup.trajectory.source.frame_count, 300);
});

test("offline demo export is explicitly labelled and cannot stand in for a live analysis", () => {
  const demo = { scenario: { id: "demo-one", mode: "demo", label: "示例", description: "模拟展示", caveat: "并非视频测量" }, results: [{ card: { name: "平衡" }, score: 80, status: "scored", verdict: "模拟解释" }] };
  const input = { mode: "demo", uploadState: "idle", evidence: {}, demoReport: demo };
  const report = buildPracticeReport(input);
  assert.equal(report.isDemo, true);
  assert.equal(report.jobId, null);
  assert.match(renderReportHtml(report), /DEMO · 模拟数据 · 不是用户视频分析/);
  assert.match(reportDownloadName(report, "md"), /^rallymate-demo-/);
  assert.equal(getReportExportGate({ ...input, mode: "live", uploadState: "complete" }).allowed, false);
  assert.equal(getReportExportGate({ ...input, demoReport: { ...demo, scenario: { ...demo.scenario, id: "live-pending" } } }).allowed, false);
});

const rotationValues = {
  shoulder_line_change_deg: 0, hip_line_change_deg: 18, shoulder_hip_separation_max_deg: 24,
  shoulder_hip_separation_change_deg: 12, peak_shoulder_angular_speed_deg_s: 140, peak_hip_angular_speed_deg_s: 90,
};

function rotationReportInput() {
  const input = completed();
  const episode = {
    episode_id: "rotation-one", family: "baseline", contact_confirmed: false,
    start_ms: 1000, peak_ms: 1600, end_ms: 2200,
    classification: { label_zh: "正手挥拍", status: "rule_inferred", reason_zh: "二维动作规则参考。" },
    phases: [], metrics: { ...rotationValues }, limitations_zh: ["不能由二维画面判定动力链先后。"],
    rotation_analysis: {
      schema_version: "1.0.0", status: "measured_2d", is_3d_rotation: false,
      is_formal_coach_score: false, score: null, score_status: "calibration_required",
      metric_evidence: Object.fromEntries(Object.keys(rotationValues).map(key => [key, {
        status: "measured", coverage_fraction: 1, time_coverage_fraction: 1,
        valid_samples: 25, total_samples: 25, continuous_samples: 25,
        start_ms: 1000, end_ms: 2200, reason_zh: "连续二维轴观测可复核。",
      }])),
    },
  };
  input.evidence.result.action_recognition = { motion_analysis: {
    schema_version: "1.0.0", contact_confirmed: false,
    families: { baseline: { status: "analyzed", reason_zh: "已测得二维肩髋轴。", episodes: [episode] } },
  } };
  return { input, episode };
}

function exportedRotationMetrics(input) {
  return buildPracticeReport(input).sections.find(section => section.title === "底线运动分析").rows[0][3];
}

test("rotation report preserves observed zero, angle units, evidence coverage and a null technical score", () => {
  const { input } = rotationReportInput();
  const report = buildPracticeReport(input);
  const metrics = exportedRotationMetrics(input);
  assert.match(metrics, /画面内肩线变化（度）：0（有效采样 100%）/);
  assert.match(metrics, /画面内髋线变化（度）：18/);
  assert.match(metrics, /肩髋线最大夹角（度）：24/);
  assert.match(metrics, /肩髋线夹角变化（度）：12/);
  assert.match(metrics, /肩线峰值角速度（度\/秒）：140/);
  assert.match(metrics, /髋线峰值角速度（度\/秒）：90/);
  assert.match(renderReportHtml(report), /不作为技术评分/);
  assert.match(renderReportMarkdown(report), /不能由二维画面判定动力链先后/);
  const backup = buildReportBackup(input);
  const saved = backup.result.action_recognition.motion_analysis.families.baseline.episodes[0];
  assert.equal(saved.rotation_analysis.score, null);
  assert.equal(saved.rotation_analysis.is_formal_coach_score, false);
  assert.equal(saved.rotation_analysis.is_3d_rotation, false);
  assert.deepEqual(saved.metrics, rotationValues);
});

test("partial and unavailable rotation exports hide unsupported values without replacing them with zero", () => {
  for (const status of ["partial", "unavailable"]) {
    const { input, episode } = rotationReportInput();
    episode.rotation_analysis.status = status;
    episode.rotation_analysis.metric_evidence.hip_line_change_deg.status = "unavailable";
    const metrics = exportedRotationMetrics(input);
    assert.match(metrics, /画面内髋线变化（度）：未提供/);
    assert.doesNotMatch(metrics, /画面内髋线变化（度）：(?:18|0)/);
    if (status === "partial") assert.match(metrics, /肩线峰值角速度（度\/秒）：140/);
    else {
      assert.match(metrics, /肩线峰值角速度（度\/秒）：未提供/);
      assert.match(metrics, /画面内肩线变化（度）：未提供/);
    }
  }
});

test("rotation export applies the same strict coverage and replay-window gates as the screen", () => {
  const invalid = [
    { coverage_fraction: .79 }, { coverage_fraction: 1.01 }, { coverage_fraction: "0.9" },
    { coverage_fraction: NaN }, { coverage_fraction: Infinity }, { coverage_fraction: null },
    { time_coverage_fraction: .79 }, { time_coverage_fraction: "0.9" },
    { continuous_samples: 6 }, { continuous_samples: 26, total_samples: 25 },
    { start_ms: 999 }, { end_ms: 2201 }, { start_ms: "1000" },
    { start_ms: 1000, end_ms: 1001 }, { end_ms: null },
  ];
  for (const patch of invalid) {
    const { input, episode } = rotationReportInput();
    Object.assign(episode.rotation_analysis.metric_evidence.hip_line_change_deg, patch);
    const metrics = exportedRotationMetrics(input);
    assert.match(metrics, /画面内髋线变化（度）：未提供/, JSON.stringify(patch));
    assert.doesNotMatch(metrics, /画面内髋线变化（度）：18/);
  }
});

test("rotation export rejects numeric scores and 3D or formal claims while preserving legacy shoulder data", () => {
  for (const patch of [{ score: 88 }, { is_formal_coach_score: true }, { is_3d_rotation: true }, { status: "scored" }]) {
    const { input, episode } = rotationReportInput();
    Object.assign(episode.rotation_analysis, patch);
    const metrics = exportedRotationMetrics(input);
    assert.match(metrics, /画面内肩线变化（度）：未提供/);
    assert.match(metrics, /画面内髋线变化（度）：未提供/);
    assert.doesNotMatch(metrics, /：88|：140/);
  }
  for (const invalid of [null, false, "", {}]) {
    const { input, episode } = rotationReportInput();
    episode.rotation_analysis = invalid;
    assert.match(exportedRotationMetrics(input), /画面内肩线变化（度）：未提供/, JSON.stringify(invalid));
  }
  const { input, episode } = rotationReportInput();
  delete episode.rotation_analysis;
  assert.match(exportedRotationMetrics(input), /画面内肩线变化（度）：0/);
  assert.match(exportedRotationMetrics(input), /画面内髋线变化（度）：未提供/);
});
