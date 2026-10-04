import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

function compiledUrl(fileUrl) {
  const source = fs.readFileSync(fileUrl, "utf8");
  let js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  js = js.replace(/from "([^"]+)"/g, (_match, name) => {
    const local = new URL(`${name}.ts`, fileUrl);
    const url = name.startsWith(".") ? compiledUrl(fs.existsSync(local) ? local : new URL(`${name}.tsx`, fileUrl)) : import.meta.resolve(name);
    return `from ${JSON.stringify(url)}`;
  });
  return `data:text/javascript;base64,${Buffer.from(js).toString("base64")}`;
}

const { default: Viewer } = await import(compiledUrl(new URL("../app/BallTrajectoryViewer.tsx", import.meta.url)));
const { default: LiveResults, TechniqueFamilyChip } = await import(compiledUrl(new URL("../app/LiveResults.tsx", import.meta.url)));
const { default: Summary } = await import(compiledUrl(new URL("../app/BallTrajectorySummary.tsx", import.meta.url)));
const { default: MotionPanel } = await import(compiledUrl(new URL("../app/MotionAnalysisPanel.tsx", import.meta.url)));
const { default: RotationPanel } = await import(compiledUrl(new URL("../app/RotationAnalysisPanel.tsx", import.meta.url)));
const { motionAnalysisOf, ROTATION_METRICS } = await import(compiledUrl(new URL("../app/lib/motion-analysis.ts", import.meta.url)));
const catalog = JSON.parse(fs.readFileSync(new URL("../app/data/technique-catalog.json", import.meta.url), "utf8"));
const assessmentRows = family => catalog.techniques.filter(item => item.family === family).map(item => ({
  technique_id: item.id, family, name_zh: item.name_zh, status: "not_observed", observed: false,
  recognition_status: family === "serve" ? "candidate" : "not_observed", evidence_score_0_to_100: 0,
  core_visual_features: item.core_visual_features, limitations_zh: item.proxy_limits,
  phase_statuses: item.phases.map(phase => ({ phase, status: "not_observed" })),
}));

test("video replay enables the reliable ball tail without points or skeleton overlays", () => {
  const trajectory = { status: "ready", source: { start_timestamp_ms: 0, end_timestamp_ms: 1000 }, ball: { observed: [{ timestamp_ms: 800, x: .1, y: .4, confidence: .9 }, { timestamp_ms: 840, x: .2, y: .4, confidence: .9 }] } };
  const html = renderToStaticMarkup(createElement(Viewer, { trajectory, videoSrc: "/video.mp4" }));
  assert.match(html, /<video/);
  assert.match(html, /data-overlay="on"/);
  assert.match(html, /<svg/);
  assert.doesNotMatch(html, /<circle|<polyline|player-skeleton|pose-overlay/);
  assert.match(html, /轨迹显示设置/);
  assert.match(html, /显示识别点/);
  assert.match(html, /<input type="checkbox"\s*\/>显示识别点/);
  assert.match(html, /<input type="checkbox" checked=""\s*\/>显示短缺口插值/);
  assert.match(html, /aria-pressed="true">当前候选球路/);
});

test("default replay draws only the current reliable ball path", () => {
  const trajectory = { status: "ready", source: { start_timestamp_ms: 0, end_timestamp_ms: 1000 }, ball: { observed: [{ timestamp_ms: 800, x: .1, y: .4, confidence: .9 }, { timestamp_ms: 840, x: .2, y: .4, confidence: .9 }, { timestamp_ms: 900, x: .3, y: .4, confidence: .9 }] } };
  const html = renderToStaticMarkup(createElement(Viewer, { trajectory, videoSrc: "/video.mp4", videoTimeOriginMs: 840 }));
  assert.equal((html.match(/<polyline /g) ?? []).length, 1);
  assert.doesNotMatch(html, /<circle|30,40|player-skeleton|pose-overlay/);
});

test("replay keeps multiple current candidates separate and clears old paths", () => {
  const segments = [.1, .6].map((x, index) => ({
    segment_id: index + 1, track_ids: [index + 1], start_ms: 800, end_ms: 900,
    observed_count: 2, interpolated_count: 0, analysis: { motion_status: "moving" },
    points: [{ timestamp_ms: 800, x, y: .4, confidence: .3, source: "observed" }, { timestamp_ms: 900, x: x + .1, y: .4, confidence: .3, source: "observed" }],
  }));
  const trajectory = { status: "ready", source: { start_timestamp_ms: 0, end_timestamp_ms: 2000 }, ball: { observed: [], reconstruction: { version: "1.1.0", segments } } };
  const active = renderToStaticMarkup(createElement(Viewer, { trajectory, videoSrc: "/video.mp4", videoTimeOriginMs: 1000 }));
  assert.equal((active.match(/<polyline /g) ?? []).length, 2);
  assert.match(active, /尚未确认唯一比赛用球/);
  assert.match(active, /浅色为较低检测置信度/);
  const expired = renderToStaticMarkup(createElement(Viewer, { trajectory, videoSrc: "/video.mp4", videoTimeOriginMs: 1501 }));
  assert.doesNotMatch(expired, /<polyline /);
});

test("completed unsupported hit analysis has no invented hit count card or waiting message", () => {
  const html = renderToStaticMarkup(createElement(LiveResults, {
    result: { status: "ready", hit_statistics: { status: "unsupported", total_count: 981, reason_zh: "unsupported" } },
    assessment: null, catalog: null, pending: false, trajectory: null, status: "succeeded",
  }));
  assert.match(html, /已隐藏击球次数/);
  assert.doesNotMatch(html, /981|击球 \/ 触球次数|等待动作识别|动作识别完成后/);
  assert.match(html, /分析已完成/);
});

test("failed analysis and unavailable trajectory show terminal explanations", () => {
  const html = renderToStaticMarkup(createElement(LiveResults, { result: null, assessment: null, catalog: null, pending: false, trajectory: null, status: "failed", error: "视频解码失败" }));
  assert.match(html, /视频解码失败/);
  assert.doesNotMatch(html, /等待动作识别|动作识别完成后/);
  const summary = renderToStaticMarkup(createElement(Summary, { trajectory: null, pending: false }));
  assert.match(summary, /暂无球路数据/);
  assert.doesNotMatch(summary, /等待|后端待更新/);
});

test("serve motion keeps its uncertainty without the candidate card list", () => {
  const html = renderToStaticMarkup(createElement(LiveResults, {
    result: { status: "ready", action_recognition: { status: "candidates_detected", candidates: [{ candidate_id: "serve-1", family: "serve", status: "candidate", contact_confirmed: false, start_ms: 5066, peak_ms: 5632, end_ms: 6266 }] } },
    assessment: { techniques: assessmentRows("serve"), family_summary: { serve: { observed_count: 0, total_count: 1, candidate_count: 1, recognition_status: "candidate_only" } } }, catalog: null, pending: false, trajectory: null, status: "succeeded",
  }));
  assert.match(html, /发球式挥拍/);
  assert.match(html, /球拍触球尚未确认/);
  assert.match(html, /status-pill status-not_observed">动作待确认<\/span>/);
  assert.match(html, /最新动作定义 · 动作待确认 · 触球未确认/);
  assert.doesNotMatch(html, /action-candidates|action-candidate-list|已定位|5\.07.*6\.27|<span>已确认触球<\/span>/);
});

test("many baseline candidates are summarized without counts or unconfirmed hit totals", () => {
  const candidates = Array.from({ length: 57 }, (_, index) => ({ candidate_id: `baseline-${index}`, family: "baseline", status: "candidate", contact_confirmed: false, start_ms: index * 2000, peak_ms: index * 2000 + 600, end_ms: index * 2000 + 1000 }));
  const html = renderToStaticMarkup(createElement(LiveResults, {
    result: { status: "ready", action_recognition: { status: "candidates_detected", candidates } },
    assessment: { techniques: assessmentRows("baseline"), family_summary: { baseline: { observed_count: 0, total_count: 5, recognition_status: "motion_detected_unclassified", recognition_reason_zh: "已检测到挥拍运动；正手、反手及切削尚未分类，触球次数暂不可用。" } } }, catalog: null, pending: false, trajectory: null, status: "succeeded",
  }));
  assert.match(html, /挥拍待分类/);
  assert.match(html, /正手、反手及切削尚未分类/);
  assert.equal((html.match(/status-pill status-not_observed">未分类<\/span>/g) ?? []).length, 5);
  assert.match(html, /最新动作定义 · 未分类/);
  for (const item of assessmentRows("baseline")) assert.ok(html.includes(item.name_zh));
  assert.doesNotMatch(html, /57|action-candidates|action-candidate-list|已定位|<span>已确认触球<\/span>/);
});

test("unavailable classification keeps all five baseline rows explicit", () => {
  const html = renderToStaticMarkup(createElement(LiveResults, {
    result: { status: "ready", action_recognition: { candidates: [{ candidate_id: "old-motion", family: "baseline", status: "candidate", contact_confirmed: false, start_ms: 0, peak_ms: 500, end_ms: 1000 }] } },
    assessment: { techniques: assessmentRows("baseline"), family_summary: { baseline: { observed_count: 0, total_count: 5, recognition_status: "classifier_unavailable", recognition_reason_zh: "专项分类器未部署。" } } }, catalog: null, pending: false, trajectory: null, status: "succeeded",
  }));
  assert.equal((html.match(/status-pill status-not_observed">分类暂不可用<\/span>/g) ?? []).length, 5);
  assert.match(html, /最新动作定义 · 分类暂不可用/);
});

test("a truly observed technique keeps its ready status even when other types are unclassified", () => {
  const rows = assessmentRows("baseline");
  rows[0] = { ...rows[0], observed: true, recognition_status: "observed", status: "ready", evidence_score_0_to_100: 85 };
  const html = renderToStaticMarkup(createElement(LiveResults, {
    result: { status: "ready", action_recognition: { candidates: [{ candidate_id: "other-motion", family: "baseline", status: "candidate", contact_confirmed: false, start_ms: 0, peak_ms: 500, end_ms: 1000 }] } },
    assessment: { techniques: rows, family_summary: { baseline: { observed_count: 1, total_count: 5, recognition_status: "motion_detected_unclassified" } } }, catalog: null, pending: false, trajectory: null, status: "succeeded",
  }));
  assert.match(html, /status-pill status-ready">证据就绪<\/span>/);
  assert.match(html, /最新动作定义 · 证据就绪/);
  assert.doesNotMatch(html, /85\.0%/);
  assert.match(html, /待标定/);
  assert.equal((html.match(/status-pill status-not_observed">未分类<\/span>/g) ?? []).length, 4);
});

test("preview family chips show recognition limits without candidate counts", () => {
  for (const [family, state, label] of [["baseline", "motion_detected_unclassified", "挥拍待分类"], ["serve", "candidate_only", "动作待确认"], ["baseline", "classifier_unavailable", "分类暂不可用"]]) {
    const html = renderToStaticMarkup(createElement(TechniqueFamilyChip, { family, label: family, total: 5, summary: { observed_count: 0, total_count: 5, candidate_count: 57, recognition_status: state } }));
    assert.ok(html.includes(label));
    assert.doesNotMatch(html, /57|段候选|就绪度|0 \/ 5/);
  }
  const measured = renderToStaticMarkup(createElement(TechniqueFamilyChip, { family: "baseline", label: "底线", total: 5, summary: { observed_count: 2, total_count: 5, recognition_status: "observed", evidence_score_0_to_100: 80 } }));
  assert.match(measured, /2 \/ 5/);
  assert.doesNotMatch(measured, /就绪度 80/);
  assert.match(measured, /查看逐项证据/);
});

const motionEpisode = { episode_id: "motion-1", family: "baseline", start_ms: 1000, peak_ms: 1800, end_ms: 2400, contact_confirmed: false, classification: { label: "forehand", label_zh: "正手挥拍", status: "rule_inferred", reason_zh: "持拍手明确，手腕由持拍侧向身体另一侧运动。" }, phases: [{ phase: "preparation", label_zh: "准备", start_ms: 1000, end_ms: 1500 }, { phase: "acceleration", label_zh: "加速", start_ms: 1500, end_ms: 1800 }, { phase: "follow_through", label_zh: "随挥", start_ms: 1800, end_ms: 2400 }], metrics: { peak_wrist_speed_torso_per_s: 3.25, wrist_path_torso: 2.3, elbow_extension_deg: 54.26, shoulder_line_change_deg: null }, limitations_zh: ["尚未经过专项标注集准确率验证。"] };

test("motion results show one measured episode and phases without restoring the candidate list", () => {
  const episodes = Array.from({ length: 57 }, (_, index) => ({ ...motionEpisode, episode_id: `motion-${index}` }));
  const motion_analysis = { schema_version: "1.0.0", contact_confirmed: false, families: { baseline: { status: "analyzed", reason_zh: "已提供二维运动测量。", episodes } } };
  const html = renderToStaticMarkup(createElement(LiveResults, { result: { status: "ready", action_recognition: { motion_analysis } }, assessment: null, catalog: null, pending: false, trajectory: null, status: "succeeded" }));
  assert.match(html, /底线 · 当前视频/);
  assert.match(html, /正手挥拍/);
  assert.match(html, /手腕峰值速度/);
  assert.match(html, /3\.25/);
  assert.match(html, /准备.*加速.*随挥/s);
  assert.match(html, /触球未确认/);
  assert.match(html, /下一片段/);
  assert.equal((html.match(/motion-episode-heading/g) ?? []).length, 1);
  assert.doesNotMatch(html, /57|已定位|status-pill status-not_observed/);
});

test("return analysis explains missing context and never fills it with a zero score", () => {
  const html = renderToStaticMarkup(createElement(MotionPanel, { familyLabel: "接发", data: { status: "insufficient_evidence", reason_zh: "没有对手发球与来球顺序，不能区分接发与普通底线挥拍。", episodes: [] }, pending: false }));
  assert.match(html, /接发 · 当前视频/);
  assert.match(html, /没有对手发球与来球顺序/);
  assert.doesNotMatch(html, /motion-metrics|等待|0 \/ 100/);
});

test("partial motion disables missing follow-through and labels available stages as estimates", () => {
  const partial = { ...motionEpisode, analysis_status: "partial", phase_timing_status: "estimated_from_2d_motion", phases: [...motionEpisode.phases.slice(0, 2), { phase: "follow_through", label_zh: "随挥", status: "unavailable", start_ms: null, end_ms: null }], metric_notes_zh: ["肩线投影缩短，未输出肩线变化。"] };
  const html = renderToStaticMarkup(createElement(MotionPanel, { familyLabel: "底线", data: { episodes: [partial] }, pending: false }));
  assert.match(html, /阶段证据不完整/);
  assert.match(html, /二维运动变化估计，不代表触球时刻/);
  assert.match(html, /<button type="button" disabled=""><span>随挥证据不足<\/span><small>未定位阶段<\/small>/);
  assert.match(html, /1\.00–1\.50s · 估计/);
  assert.match(html, /肩线投影缩短/);
  assert.doesNotMatch(html, /0\.00–0\.00s/);
});

function rotationEpisode(status = "measured_2d") {
  const row = structuredClone(motionEpisode);
  row.metrics = { ...row.metrics, ...Object.fromEntries(ROTATION_METRICS.map(({ key }, i) => [key, i * 12])) };
  row.rotation_analysis = {
    status, is_3d_rotation: false, is_formal_coach_score: false, score: null,
    score_status: status === "unavailable" ? "insufficient_evidence" : "calibration_required",
    metric_evidence: Object.fromEntries(ROTATION_METRICS.map(({ key }) => [key, {
      status: "measured", coverage_fraction: 1, time_coverage_fraction: .86,
      valid_samples: 24, total_samples: 24, continuous_samples: 24,
      start_ms: 1100, end_ms: 2300, reason_zh: "肩髋线连续观测可复核。",
    }])),
  };
  return row;
}

function parsedRotationEpisode(row) {
  return motionAnalysisOf({ action_recognition: { motion_analysis: {
    schema_version: "1.0.0", contact_confirmed: false, families: { baseline: { episodes: [row] } },
  } } }).families.baseline.episodes[0];
}

test("rotation cards show measured angles and speeds once, with no technical grade or contact claim", () => {
  const row = parsedRotationEpisode(rotationEpisode());
  const html = renderToStaticMarkup(createElement(MotionPanel, { familyLabel: "底线", data: { episodes: [row] }, pending: false }));
  assert.match(html, /二维转体观察/);
  assert.match(html, /连续测量可用/);
  assert.match(html, /技术评分：待教练标定/);
  assert.match(html, /不能据此判断真实三维转体幅度或动力链先后/);
  assert.match(html, /画面内肩线变化<\/span><strong>0\.00<\/strong>/);
  assert.match(html, /髋线峰值角速度<\/span><strong>60\.00<\/strong>/);
  assert.match(html, /°\/秒/);
  assert.equal((html.match(/画面内肩线变化/g) ?? []).length, 1);
  assert.equal((html.match(/复核测量区间/g) ?? []).length, 6);
  assert.doesNotMatch(html, /0 \/ 100|转体合格|骨盆领先/);
});

test("partial rotation cards suppress weak axes and only offer replay for observed windows", () => {
  const row = rotationEpisode("partial");
  for (const key of ["hip_line_change_deg", "shoulder_hip_separation_max_deg", "peak_hip_angular_speed_deg_s"]) {
    row.metrics[key] = 9876;
    row.rotation_analysis.metric_evidence[key] = { status: "unavailable", coverage_fraction: .25, start_ms: null, end_ms: null, reason_zh: "轴投影缩短，连续证据不足。" };
  }
  const parsed = parsedRotationEpisode(row);
  const html = renderToStaticMarkup(createElement(RotationPanel, { episode: parsed, seek() {} }));
  assert.match(html, /部分指标可测/);
  assert.match(html, /轴投影缩短，连续证据不足/);
  assert.match(html, /有效采样 25%/);
  assert.equal((html.match(/复核测量区间/g) ?? []).length, 3);
  assert.equal((html.match(/<strong>—<\/strong>/g) ?? []).length, 3);
  assert.doesNotMatch(html, /9876|0 \/ 100/);
});

test("unavailable rotation cards keep missing values and offer no fabricated replay target", () => {
  const row = parsedRotationEpisode(rotationEpisode("unavailable"));
  const html = renderToStaticMarkup(createElement(RotationPanel, { episode: row, seek() {} }));
  assert.match(html, /测量证据不足/);
  assert.match(html, /技术评分：证据不足/);
  assert.equal((html.match(/<strong>—<\/strong>/g) ?? []).length, 6);
  assert.doesNotMatch(html, /<button|0\.00|待教练标定|\/ 100/);
});

test("rotation review buttons seek to their validated measurement window start", () => {
  const row = parsedRotationEpisode(rotationEpisode());
  const sought = [];
  const rendered = RotationPanel({ episode: row, seek: timestamp => sought.push(timestamp) });
  const buttons = [];
  const visit = node => {
    if (Array.isArray(node)) { node.forEach(visit); return; }
    if (!node || typeof node !== "object") return;
    if (node.type === "button") buttons.push(node);
    visit(node.props?.children);
  };
  visit(rendered);
  assert.equal(buttons.length, 6);
  for (const button of buttons) button.props.onClick();
  assert.deepEqual(sought, [1100, 1100, 1100, 1100, 1100, 1100]);
  assert.equal(RotationPanel({ episode: structuredClone(motionEpisode), seek() {} }), null);
});
