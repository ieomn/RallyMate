import { evidenceReferenceScore, measurementCounts, measurementWarnings, normalizeMeasurementResult } from "./measurement-evidence";
import type { DemoResultResponse, JobProgress, TechniqueAssessmentResponse, TrajectoryPreviewResponse } from "./api-types";
import type { ScoreReport } from "../scoring/engine";
import { footworkEpisodes } from "./footwork-review";
import { analysisReportOf, reportFocus } from "./training-report";
import { motionAnalysisOf, ROTATION_METRICS } from "./motion-analysis";

export type ReportExportInput = {
  mode: "demo" | "live" | "live-pending";
  uploadState: string;
  job?: JobProgress | null;
  evidence: {
    jobId?: string;
    result?: DemoResultResponse | null;
    assessment?: TechniqueAssessmentResponse | null;
    trajectory?: TrajectoryPreviewResponse | null;
    error?: string | null;
  };
  summary?: Record<string, unknown> | null;
  videoName?: string | null;
  demoReport?: ScoreReport | null;
};

export type ReportSection = { title: string; paragraphs: string[]; columns?: string[]; rows?: string[][] };
export type PracticeReport = {
  schemaVersion: "rallymate-readable-report/1";
  title: string;
  exportedAt: string;
  sourceLabel: string;
  isDemo: boolean;
  jobId: string | null;
  sections: ReportSection[];
};

type RecordValue = Record<string, unknown>;
const asRecord = (value: unknown): RecordValue => value && typeof value === "object" && !Array.isArray(value) ? value as RecordValue : {};
const asArray = (value: unknown): unknown[] => Array.isArray(value) ? value : [];
const finite = (value: unknown): number | null => typeof value === "number" && Number.isFinite(value) ? value : null;
const numberText = (value: unknown, suffix = ""): string => finite(value) === null ? "未提供" : `${Number((value as number).toFixed(3))}${suffix}`;
const percent = (value: unknown): string => finite(value) === null ? "未提供" : numberText((value as number) * 100, "%");
const idText = (value: unknown): string | null => typeof value === "string" && /^[A-Za-z0-9_-]{1,80}$/.test(value) ? value : null;
const FAMILY_NAMES: Record<string, string> = { baseline: "底线", serve: "发球", return: "接发", net_attack: "网前进攻", footwork: "步伐" };

/** Export only user-facing text. Links, filesystem locations and credentials are not report evidence. */
function text(value: unknown, fallback = "未提供"): string {
  if (typeof value !== "string" || !value.trim()) return fallback;
  return Array.from(value).filter(character => {
    const code = character.charCodeAt(0);
    return code === 9 || code === 10 || (code > 31 && code !== 127);
  }).join("")
    .replace(/\b(?:Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+/gi, "[凭据已省略]")
    .replace(/\b(?:RALLYMATE_)?(?:API_?KEY|TOKEN|SECRET|PASSWORD|AUTHORIZATION)\s*[:=]\s*["']?[^\s,;"']+/gi, "[凭据已省略]")
    .replace(/\bsk-[A-Za-z0-9_-]{8,}/g, "[凭据已省略]")
    .replace(/(?:https?|file|ftp):\/\/[^\s<>"'）)]+/gi, "[链接已省略]")
    .replace(/\b[A-Za-z]:[\\/][^\s<>"'）)]+/g, "[本地路径已省略]")
    .replace(/\/(?:root|home|mnt|tmp|var|Users|workspace|autodl-tmp)(?:\/[^\s<>"'）)]*)?/g, "[服务器路径已省略]")
    .replace(/\s+/g, " ")
    .trim().slice(0, 4000);
}

const textList = (value: unknown): string[] => asArray(value).filter((item): item is string => typeof item === "string" && Boolean(item.trim())).map(item => text(item));
const familyName = (value: unknown): string => typeof value === "string" ? FAMILY_NAMES[value] ?? text(value) : "未分类";
const statusName = (value: unknown): string => ({ ready: "证据就绪", partial: "部分证据", unavailable: "不可评价", not_observed: "未观测", candidate: "候选（未确认触球）", scored: "练习参考", blocked: "不可评价" }[String(value)] ?? text(value));

function actionFamilyName(action: RecordValue): string {
  if (typeof action.family === "string" && action.family.trim()) return familyName(action.family);
  // The current backend emits FS01 / FS02 / FS09 without a family field.
  // FS01–FS10 are the established footwork event codes; unknown codes stay unclassified.
  return typeof action.event_code === "string" && /^FS(?:0[1-9]|10)$/.test(action.event_code)
    ? FAMILY_NAMES.footwork : "未分类";
}

function assessmentOf(input: ReportExportInput): RecordValue {
  return asRecord(input.evidence.assessment ?? input.evidence.result?.technique_assessment);
}
function summaryOf(input: ReportExportInput): RecordValue {
  return asRecord(input.summary ?? input.evidence.result?.summary ?? input.job?.summary);
}
function recognitionOf(input: ReportExportInput): RecordValue {
  const assessment = assessmentOf(input), summary = summaryOf(input);
  return asRecord(input.evidence.result?.action_recognition ?? assessment.action_recognition ?? summary.action_recognition);
}

function hasAnalysis(input: ReportExportInput): boolean {
  const result = asRecord(input.evidence.result), training = asRecord(result.training_evaluation);
  const assessment = assessmentOf(input);
  return (result.result_kind === "imported_stage1_summary" && (Object.keys(asRecord(asRecord(result.summary).processing)).length > 0 || Object.keys(asRecord(asRecord(result.summary).coverage)).length > 0))
    || asArray(result.actions).some(item => Boolean(asRecord(item).name_zh) && finite(asRecord(item).detected_segments) !== null)
    || asArray(training.indicator_evaluations).length > 0
    || (typeof training.summary_zh === "string" && Boolean(training.summary_zh.trim()))
    || asArray(assessment.techniques).some(item => Boolean(asRecord(item).name_zh) && typeof asRecord(item).observed === "boolean")
    || ["candidates_detected", "no_candidates", "insufficient_pose"].includes(String(recognitionOf(input).status))
    || ["available", "insufficient_evidence"].includes(String(asRecord(recognitionOf(input).motion_analysis).status));
}

export function getReportExportGate(input: ReportExportInput): { allowed: boolean; reason: string } {
  if (["uploading", "processing", "ready"].includes(input.uploadState) || input.mode === "live-pending") {
    return { allowed: false, reason: "视频分析完成后可导出完整报告。" };
  }
  if (input.uploadState === "error" || input.job?.error || ["failed", "cancelled"].includes(input.job?.status ?? "")) {
    return { allowed: false, reason: "当前任务未成功完成，请先恢复分析或重新上传。" };
  }
  if (input.mode === "demo") {
    const report = input.demoReport;
    return report?.scenario.mode === "demo" && report.scenario.id !== "live-pending" && report.results.length > 0
      ? { allowed: true, reason: "离线演示报告：模拟数据，不代表你的视频分析。" }
      : { allowed: false, reason: "当前没有可导出的分析结果。" };
  }
  if (input.uploadState !== "complete" || (input.job && !["completed", "succeeded"].includes(input.job.status))) {
    return { allowed: false, reason: "请等待任务成功完成并读取结果后再导出。" };
  }
  if (!hasAnalysis(input)) return { allowed: false, reason: "分析结果尚未读取，不能导出空报告。" };
  const ids = [input.job?.id, input.evidence.jobId, input.evidence.result?.job_id, assessmentOf(input).job_id, input.evidence.trajectory?.job_id].map(idText).filter(Boolean);
  if (new Set(ids).size > 1) return { allowed: false, reason: "任务与结果编号不一致，请重新读取当前任务。" };
  return { allowed: true, reason: "导出当前已完成分析；缺失证据会在报告中明确说明。" };
}

function requireReady(input: ReportExportInput): void {
  const gate = getReportExportGate(input);
  if (!gate.allowed) throw new Error(gate.reason);
}

const BASE_LIMITATIONS = [
  "动作候选、动作片段、球检测点和球拍检测点均不等于已确认击球或触球次数。",
  "图像中的二维位置与归一化速度不能直接解释为真实三维球速、落点、旋转或场地距离。",
  "测量证据参考分反映证据完整程度，高分不代表动作正确或技术水平更高；技术评分待教练标定。",
  "未观测表示当前视频没有足够可用证据，不能据此判断该动作没有发生。",
];

function interval(row: RecordValue): string {
  const start = finite(row.start_ms), end = finite(row.end_ms);
  return start !== null && end !== null && start >= 0 && end >= start
    ? `${numberText(start / 1000)}–${numberText(end / 1000)} 秒`
    : "未提供有效时间区间";
}

function candidateEvidence(candidate: RecordValue): string {
  const evidence = asRecord(candidate.evidence);
  const labels: Record<string, string> = {
    pose_frame_count: "姿态帧数", observed_pose_frames: "姿态观测帧数", racket_observed_frames: "球拍观测帧数",
    wrist_speed_peak: "手腕速度峰值（归一化）", max_wrist_speed: "手腕速度峰值（归一化）",
    wrist_above_shoulder_fraction: "手腕高于肩部比例", ball_observed_frames: "球观测帧数",
    wrist_motion_fraction: "手腕活动比例", confidence: "候选置信度",
    racket_associated_frames: "关联球拍帧数", pose_samples: "有效姿态采样数",
    overhead_height_torso_units: "过顶高度（躯干长度单位）",
    racket_arm_upward_excursion_torso_units: "持拍臂上抬幅度（躯干长度单位）",
    follow_through_drop_torso_units: "随挥下降幅度（躯干长度单位）",
    horizontal_excursion_torso_units: "水平挥动幅度（躯干长度单位）",
    peak_wrist_speed_torso_units_per_second: "手腕峰值速度（躯干长度/秒）",
  };
  const parts = Object.entries(labels).flatMap(([key, label]) => finite(evidence[key]) === null ? [] : [`${label}：${numberText(evidence[key])}`]);
  return parts.join("；") || "基于人体姿态与可用球拍观测的动作候选，需结合视频复核。";
}

function metricRows(result: RecordValue): string[][] {
  const training = asRecord(result.training_evaluation);
  const metrics = [...asArray(training.indicator_evaluations), ...asArray(result.actions).flatMap(action => asArray(asRecord(action).indicator_evaluations))];
  const seen = new Set<string>();
  return metrics.flatMap(raw => {
    const metric = asRecord(raw);
    if (typeof metric.name_zh !== "string") return [];
    const key = String(metric.indicator_id ?? metric.name_zh);
    if (seen.has(key)) return [];
    seen.add(key);
    const measurements = asArray(metric.representative_measurements).map(raw => { const item = asRecord(raw); return `${text(item.label_zh)} ${numberText(item.median_value)} ${text(item.unit_zh)}（${numberText(item.sample_count)} 个样本）`; }).join("；");
    const components = asRecord(metric.components);
    const evidence = `可测片段 ${numberText(metric.measured_instance_count)}/${numberText(metric.total_instance_count)}；特征覆盖 ${numberText(components.required_feature_coverage_percent, "%")}；置信度 ${numberText(components.median_feature_confidence_percent, "%")}；重复性 ${numberText(components.repeatability_percent, "%")}`;
    return [[text(metric.name_zh), numberText(evidenceReferenceScore(metric), " / 100"), measurements || text(metric.observation_zh ?? metric.summary_zh), evidence, textList(metric.limitations_zh).join("；") || "候选动作需结合视频复核"]];
  });
}

export function buildPracticeReport(input: ReportExportInput, exportedAt = new Date().toISOString()): PracticeReport {
  requireReady(input);
  if (input.evidence.result) input = { ...input, evidence: { ...input.evidence, result: normalizeMeasurementResult(input.evidence.result) } };
  if (input.mode === "demo") {
    const demo = input.demoReport!;
    return {
      schemaVersion: "rallymate-readable-report/1", title: "RallyMate 离线演示报告", exportedAt: text(exportedAt),
      sourceLabel: "DEMO · 模拟数据 · 不是用户视频分析", isDemo: true, jobId: null,
      sections: [
        { title: "演示信息", paragraphs: ["本文件展示产品的报告样式，所有演示分值均不能作为真实训练评价。", text(demo.scenario.description)], columns: ["项目", "内容"], rows: [["演示场景", text(demo.scenario.label)], ["生成时间", text(exportedAt)]] },
        { title: "模拟指标", paragraphs: ["以下均为演示指标。"], columns: ["指标", "模拟参考分", "状态", "说明"], rows: demo.results.map(row => [text(row.card.name), numberText(row.score, " / 100"), statusName(row.status), text(row.verdict)]) },
        { title: "限制", paragraphs: ["DEMO：没有真实上传视频、服务端任务或可核验候选时间区间。", text(demo.scenario.caveat), ...BASE_LIMITATIONS] },
      ],
    };
  }
  const result = asRecord(input.evidence.result), assessment = assessmentOf(input), summary = summaryOf(input);
  const training = asRecord(result.training_evaluation), recognition = recognitionOf(input);
  const sourceInput = asRecord(summary.input), metadata = asRecord(sourceInput.video ?? sourceInput.metadata);
  const processing = asRecord(summary.processing), trajectory = asRecord(input.evidence.trajectory);
  const source = asRecord(trajectory.source), ball = asRecord(trajectory.ball), reconstruction = asRecord(ball.reconstruction), ballSummary = asRecord(reconstruction.summary);
  const jobId = idText(input.job?.id ?? input.evidence.jobId ?? result.job_id ?? assessment.job_id);
  const suppliedName = input.videoName || input.job?.original_filename || sourceInput.file_name || sourceInput.filename || sourceInput.original_filename || asRecord(sourceInput.upstream_metadata).original_filename;
  const videoName = typeof suppliedName === "string" ? text(suppliedName.split(/[\\/]/).pop()) : "未保留原始文件名";
  const width = finite(metadata.width ?? source.width), height = finite(metadata.height ?? source.height);
  const durationMs = finite(metadata.duration_ms);
  const sections: ReportSection[] = [{
    title: "视频与任务", paragraphs: [], columns: ["项目", "内容"], rows: [
      ["视频文件", videoName], ["任务编号", jobId ?? "未提供（导入分析）"],
      ["来源", jobId ? "已完成的视频分析结果" : "导入的分析结果；未关联可核验任务编号"],
      ["导出时间", text(exportedAt)], ["视频时长", durationMs === null ? "未提供" : numberText(durationMs / 1000, " 秒")],
      ["画面尺寸", width !== null && height !== null ? `${width} × ${height}` : "未提供"],
      ["视频帧数", numberText(metadata.frame_count ?? source.frame_count)], ["已处理帧数", numberText(processing.processed_frames)],
      ["处理耗时", numberText(processing.elapsed_seconds, " 秒")],
    ],
  }];
  const parsedMotion = motionAnalysisOf(input.evidence.result ?? null, input.evidence.assessment, summary);
  const focus = reportFocus(input.evidence.result ?? null, parsedMotion);
  if (focus.length) sections.push({ title: "这次先关注", paragraphs: ["以下为证据关联的复核与拍摄建议，不是技术错误诊断。"], columns: ["重点", "建议", "回放区间"], rows: focus.map(item => [text(item.title), text(item.detail), item.moment ? `${numberText(item.moment.startMs / 1000)}–${numberText(item.moment.endMs / 1000)} 秒` : "未提供定位区间"]) });
  const analysisReport = analysisReportOf(result.analysis_report);
  if (analysisReport) sections.push({ title: "识别过程与测量范围", paragraphs: ["各层分别判断可用性。动作未分类不会清空可独立测量的指标。", analysisReport.measurement_summary.is_truncated ? "当前逐段记录经过截取，单项观测数量仅对应已返回片段。" : "单项观测数量对应已返回的回放片段。"], columns: ["分析层", "状态", "说明"], rows: analysisReport.layers.map(layer => [text(layer.label_zh), ({ available: "可用", partial: "部分可用", unavailable: "未提供", unknown: "未确认" } as Record<string, string>)[layer.status] ?? "未确认", text(layer.reason_zh)]) });
  if (input.evidence.result && measurementWarnings(input.evidence.result).length) sections.push({ title: "测量适用范围与历史结果提示", paragraphs: measurementWarnings(input.evidence.result).map(warning => text(warning)) });
  sections.push({ title: "本次动作观察", paragraphs: [text(training.summary_zh, "分析已完成；以下只列出本次返回的观测。"), `实测指标：${measurementCounts(input.evidence.result).measured}/${measurementCounts(input.evidence.result).total} 项。测量证据参考分（Beta）：${numberText(evidenceReferenceScore(training), " / 100")}；高分不代表动作正确，技术评分待教练标定。`], columns: ["动作", "类别", "片段数（非击球次数）", "证据参考分", "观察"], rows: asArray(result.actions).map(raw => {
    const action = asRecord(raw);
    return [text(action.name_zh), actionFamilyName(action), numberText(action.detected_segments), numberText(evidenceReferenceScore(action.performance_assessment), " / 100"), text(action.summary_zh, "参见指标与证据限制")];
  }) });
  const techniques = asArray(assessment.techniques);
  sections.push({ title: "技术证据覆盖", paragraphs: ["逐项展示证据状态；技术评分待教练标定。"], columns: ["技术", "类别", "观察状态", "技术评分", "限制"], rows: techniques.map(raw => {
    const technique = asRecord(raw);
    const observation = technique.recognition_status === "candidate" ? statusName("candidate") : technique.observed === true ? statusName(technique.status) : "未观测";
    return [text(technique.name_zh), familyName(technique.family), observation, "待教练标定", textList(technique.limitations_zh).join("；") || "参见统一限制"];
  }) });
  const motion = asRecord(recognition.motion_analysis);
  const hasMotion = ["1.0.0", "1.1.0"].includes(String(motion.schema_version)) && motion.contact_confirmed === false;
  if (hasMotion) {
    const metricNames = { peak_wrist_speed_torso_per_s: "手腕峰值速度（躯干长度/秒）", wrist_path_torso: "手腕运动距离（躯干长度）", elbow_extension_deg: "肘角变化幅度（度）", shoulder_line_change_deg: "画面内肩线变化（度）", hip_line_change_deg: "画面内髋线变化（度）", shoulder_hip_separation_max_deg: "肩髋线最大夹角（度）", shoulder_hip_separation_change_deg: "肩髋线夹角变化（度）", peak_shoulder_angular_speed_deg_s: "肩线峰值角速度（度/秒）", peak_hip_angular_speed_deg_s: "髋线峰值角速度（度/秒）" };
    for (const family of ["baseline", "serve", "return"]) {
      const familyData = asRecord(asRecord(motion.families)[family]);
      sections.push({ title: `${familyName(family)}运动分析`, paragraphs: [text(familyData.reason_zh, "本次未提供该类运动分析。"), "类型为规则推断参考；阶段边界按二维运动变化估计，不代表触球时刻。未确认触球，不作为技术评分；证据不足的阶段不补全。"], columns: ["运动类型（规则参考）", "时间区间", "估计阶段", "测量指标", "依据与限制"], rows: asArray(familyData.episodes).filter(raw => {
        const episode = asRecord(raw);
        return episode.family === family && episode.contact_confirmed === false && ["rule_inferred", "unclassified"].includes(String(asRecord(episode.classification).status)) && finite(episode.start_ms) !== null && finite(episode.end_ms) !== null && Number(episode.end_ms) > Number(episode.start_ms);
      }).map(raw => {
        const episode = asRecord(raw), classification = asRecord(episode.classification), metrics = asRecord(episode.metrics);
        const phases = asArray(episode.phases).map(rawPhase => {
          const phase = asRecord(rawPhase), start = finite(phase.start_ms), end = finite(phase.end_ms);
          return phase.status === "unavailable" || start === null || end === null || end <= start ? `${text(phase.label_zh)}证据不足` : `${text(phase.label_zh)} ${interval(phase)}（估计）`;
        }).join("；");
        return [text(classification.label_zh), interval(episode), `${episode.analysis_status === "partial" ? "阶段证据不完整；" : ""}${phases}`, Object.entries(metricNames).map(([key, label]) => {
          const rotation = asRecord(episode.rotation_analysis), evidence = asRecord(asRecord(rotation.metric_evidence)[key]);
          const isRotation = !["peak_wrist_speed_torso_per_s", "wrist_path_torso", "elbow_extension_deg"].includes(key);
          const legacy = key === "shoulder_line_change_deg" && episode.rotation_analysis === undefined;
          const measured = !isRotation || legacy || rotation.is_3d_rotation === false && rotation.is_formal_coach_score === false && rotation.score === null && ["measured_2d", "partial"].includes(String(rotation.status)) && evidence.status === "measured" && finite(evidence.coverage_fraction) !== null && Number(evidence.coverage_fraction) >= .8 && Number(evidence.coverage_fraction) <= 1 && finite(evidence.time_coverage_fraction) !== null && Number(evidence.time_coverage_fraction) >= .8 && Number(evidence.time_coverage_fraction) <= 1 && Number.isSafeInteger(evidence.continuous_samples) && Number(evidence.continuous_samples) >= 7 && Number.isSafeInteger(evidence.total_samples) && Number.isSafeInteger(evidence.valid_samples) && Number(evidence.continuous_samples) <= Number(evidence.valid_samples) && Number(evidence.valid_samples) <= Number(evidence.total_samples) && finite(evidence.start_ms) !== null && finite(evidence.end_ms) !== null && Number(evidence.start_ms) >= Number(episode.start_ms) && Number(evidence.end_ms) <= Number(episode.end_ms) && Number(evidence.end_ms) - Number(evidence.start_ms) >= 200;
          return `${label}：${measured && finite(metrics[key]) !== null && Number(metrics[key]) >= 0 ? numberText(metrics[key]) : "未提供"}${isRotation && !legacy ? `（有效采样 ${percent(evidence.coverage_fraction)}）` : ""}`;
        }).join("；"), [text(classification.reason_zh), ...textList(episode.metric_notes_zh), ...textList(episode.limitations_zh)].join("；")];
      }) });
    }
  }
  if (!hasMotion) sections.push({ title: "发球与底线候选证据区间", paragraphs: ["以下仅为候选动作时间区间，不是已确认击球。正反手、触球、落点与正式分级不能由候选直接推出。", ...textList(recognition.limitations_zh)], columns: ["候选动作", "时间区间", "峰值时刻", "证据摘要", "限制"], rows: asArray(recognition.candidates).filter(raw => ["serve", "baseline"].includes(String(asRecord(raw).family))).map(raw => {
    const candidate = asRecord(raw);
    return [candidate.family === "serve" ? "发球动作候选" : "底线挥拍候选", interval(candidate), finite(candidate.peak_ms) === null ? "未提供" : numberText((candidate.peak_ms as number) / 1000, " 秒"), candidateEvidence(candidate), ["未确认触球", ...textList(candidate.limitations_zh)].join("；")];
  }) });
  sections.push({ title: "可测指标与练习提示", paragraphs: ["测量证据参考分（Beta）只描述证据完整程度；技术评分待教练标定，无有效数据的项目保持“未提供”。"], columns: ["指标", "证据参考分", "实际测量", "证据与样本", "指标限制"], rows: metricRows(result) });
  const footwork = asRecord(result.footwork_review);
  const localRows = Object.entries(parsedMotion?.families ?? {}).flatMap(([family, data]) => data.episodes.flatMap(episode => [
    ...(episode.rotation_analysis?.local_windows ?? []).map(window => ({ ...window, windowType: "局部连续窗口" })),
    ...(episode.rotation_analysis?.phase_measurements ?? []).map(window => ({ ...window, windowType: `估计阶段：${window.label_zh || window.phase}` })),
  ].map(window => [familyName(family), text(window.windowType), interval(window), ROTATION_METRICS.filter(metric => window.metric_evidence[metric.key]?.status === "measured").map(metric => `${metric.label}：${numberText(window.metrics[metric.key])} ${metric.unit}`).join("；")])));
  if (localRows.length) sections.push({ title: "转体局部与阶段独立测量", paragraphs: ["仅描述对应连续区间的二维投影；局部值不替代整段覆盖，不作为三维转体或技术好坏的判断。"], columns: ["动作类别", "范围", "时间区间", "测量"], rows: localRows });
  if (["1.0.0", "1.1.0"].includes(String(footwork.schema_version))) sections.push({ title: "步伐逐段复核", paragraphs: ["分腿、启动和制动是规则候选片段，非实际步数或击球次数；技术等级待教练标定。", ...(footwork.reason_zh ? [text(footwork.reason_zh)] : []), ...(footwork.is_truncated ? [`仅展示前 ${numberText(footwork.returned_episode_count)} 段，共 ${numberText(footwork.episode_count)} 段。`] : []), ...textList(footwork.limitations_zh)], columns: ["步伐", "时间区间", "指标", "测量值", "评分状态"], rows: footworkEpisodes(footwork).flatMap(raw => {
    const event = asRecord(raw);
    if (!asArray(event.indicators).length) return [[text(event.name_zh), interval(event), "本段无有效指标", "没有通过质量门槛的测量值", "评分证据不足"]];
    return asArray(event.indicators).map(rawIndicator => {
      const item = asRecord(rawIndicator);
      const measurements = Array.isArray(item.measurements) ? item.measurements : asArray(item.features);
      return [text(event.name_zh), interval(event), text(item.name_zh), measurements.map(rawFeature => { const feature = asRecord(rawFeature); return feature.status === "unavailable" ? `${text(feature.name_zh ?? feature.feature_name)}：未提供（${text(feature.reason_zh)}）` : `${text(feature.name_zh ?? feature.feature_name)}：${numberText(feature.value)} ${text(feature.unit, "")}`; }).join("；") || "没有通过质量门槛的测量值", item.scoring_status === "calibration_required" ? "技术等级待标定" : item.scoring_status === "scored" ? "另见正式标定记录" : "评分证据不足"];
    });
  }) });
  const hitStats = asRecord(result.hit_statistics);
  sections.push({ title: "球轨迹概览", paragraphs: ["轨迹来自图像二维观测；短缺口插值与未来外推不算新的检测证据。", ...textList(reconstruction.limitations_zh), ...textList(trajectory.limitations)], columns: ["项目", "结果"], rows: [
    ["轨迹接口状态", text(trajectory.status, "未取得轨迹数据")], ["球观测点", numberText(ballSummary.observed_count ?? ball.observed_count)],
    ["观测帧覆盖率", percent(ballSummary.coverage_fraction ?? ball.coverage_fraction)], ["重建片段数", numberText(ballSummary.segment_count)],
    ["短缺口插值点", numberText(ballSummary.interpolated_count)], ["检测平均置信度", numberText(asRecord(ballSummary.confidence).mean ?? asRecord(ball.confidence).mean)],
    ["球拍观测点", numberText(asRecord(trajectory.racket).observed_count)],
    ["服务端显式击球计数", numberText(hitStats.total_count)], ["击球计数说明", text(hitStats.reason_zh ?? hitStats.count_semantics, "未提供确认击球字段，不能从轨迹点数推算。")],
    ["外推", ball.prediction_status === "heuristic_preview" ? "启发式预览，不代表真实落点" : "未提供未来轨迹外推"],
  ] });
  const segments = asArray(reconstruction.segments);
  if (segments.length) sections.push({ title: "轨迹片段时间概览", paragraphs: ["列出轨迹接口返回的片段；接口可能对全视频做抽样。"], columns: ["片段", "时间区间", "观测点", "插值点"], rows: segments.map((raw, index) => {
    const segment = asRecord(raw);
    return [String(index + 1), interval(segment), numberText(segment.observed_count), numberText(segment.interpolated_count)];
  }) });
  sections.push({ title: "报告限制", paragraphs: [...new Set([
    ...BASE_LIMITATIONS,
    text(assessment.formal_score_message_zh, "本报告没有推断正式技术等级。"),
    ...textList(motion.limitations_zh),
    ...(!hasMotion && !asArray(recognition.candidates).length ? [recognition.status === "no_candidates" ? "本次没有检出满足当前规则的发球或底线候选，不能据此认定视频里没有击球。" : "本次结果未提供可用的发球/底线候选时间区间，不能补造事件。"] : []),
    ...(input.evidence.error ? [`附加证据读取说明：${text(input.evidence.error)}`] : []),
    ...(!input.evidence.trajectory ? ["轨迹接口未返回数据，本报告中的轨迹部分保持缺失状态。"] : []),
  ])] });
  return { schemaVersion: "rallymate-readable-report/1", title: "RallyMate 网球练习分析报告", exportedAt: text(exportedAt), sourceLabel: jobId ? "已完成视频分析 · 证据与限制同时呈现" : "导入分析证据 · 未关联服务端任务", isDemo: false, jobId, sections };
}

const escapeHtml = (value: string): string => value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
const escapeMarkdown = (value: string): string => escapeHtml(value).replace(/[\\`*_{}[\]()#+.!|~-]/g, "\\$&");

export function renderReportMarkdown(report: PracticeReport): string {
  const lines = [`# ${escapeMarkdown(report.title)}`, "", `> ${escapeMarkdown(report.sourceLabel)}`, "", `导出时间：${escapeMarkdown(report.exportedAt)}`, ""];
  for (const section of report.sections) {
    lines.push(`## ${escapeMarkdown(section.title)}`, "");
    for (const paragraph of section.paragraphs) lines.push(escapeMarkdown(paragraph), "");
    if (section.columns && section.rows?.length) {
      lines.push(`| ${section.columns.map(escapeMarkdown).join(" | ")} |`, `| ${section.columns.map(() => "---").join(" | ")} |`);
      for (const row of section.rows) lines.push(`| ${row.map(escapeMarkdown).join(" | ")} |`);
      lines.push("");
    } else if (section.columns) lines.push("本次没有返回该项可用记录；未补造数据。", "");
  }
  return lines.join("\n");
}

export function renderReportHtml(report: PracticeReport): string {
  const body = report.sections.map(section => `<section><h2>${escapeHtml(section.title)}</h2>${section.paragraphs.map(paragraph => `<p>${escapeHtml(paragraph)}</p>`).join("")}${section.columns && section.rows?.length ? `<div class="table-wrap"><table><thead><tr>${section.columns.map(column => `<th scope="col">${escapeHtml(column)}</th>`).join("")}</tr></thead><tbody>${section.rows.map(row => `<tr>${row.map(cell => `<td>${escapeHtml(cell)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>` : section.columns ? "<p class=muted>本次没有返回该项可用记录；未补造数据。</p>" : ""}</section>`).join("");
  return `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'none'; style-src 'unsafe-inline'; img-src 'none'; connect-src 'none'; base-uri 'none'; form-action 'none'"><title>${escapeHtml(report.title)}</title><style>body{margin:0;background:#edf1f4;color:#17212d;font:15px/1.7 system-ui,-apple-system,"Microsoft YaHei",sans-serif}main{max-width:1080px;margin:32px auto;padding:40px;background:white;border-radius:16px}header{border-bottom:3px solid #176450;padding-bottom:24px}h1{font-size:28px;line-height:1.4;margin:8px 0}h2{font-size:20px;margin:32px 0 12px}p{overflow-wrap:anywhere}.badge{display:inline-block;background:#e5f3ee;color:#14573f;padding:5px 12px;border-radius:6px}.demo{background:#fff0cc;color:#744c00}.muted,footer{color:#647080}.table-wrap{overflow:auto}table{border-collapse:collapse;width:100%;font-size:13px}th,td{text-align:left;vertical-align:top;padding:10px;border:1px solid #dbe3e8;overflow-wrap:anywhere}th{background:#f1f5f7}tbody tr:nth-child(even){background:#fafbfc}footer{margin-top:32px;font-size:12px}@media(max-width:600px){main{margin:0;padding:20px;border-radius:0}h1{font-size:23px}}@media print{@page{size:A4;margin:14mm}body{background:white;font-size:10pt}main{margin:0;padding:0;max-width:none}h1{font-size:20pt}h2{font-size:14pt;break-after:avoid}thead{display:table-header-group}tr{break-inside:avoid}.table-wrap{overflow:visible}table{font-size:9pt}th,td{padding:6px}section{break-inside:auto}.badge{border:1px solid #aac8bd}footer{font-size:8pt}}</style></head><body><main><header><span class="badge${report.isDemo ? " demo" : ""}">${escapeHtml(report.sourceLabel)}</span><h1>${escapeHtml(report.title)}</h1><p class="muted">导出时间：${escapeHtml(report.exportedAt)} · 可使用浏览器“打印”保存为 PDF</p></header>${body}<footer>RallyMate · 此独立报告无需联网，不包含远程脚本、视频文件或服务器访问凭据。</footer></main></body></html>`;
}

/** Keep the legacy import envelope while excluding private paths and transport configuration. */
export function buildReportBackup(input: ReportExportInput, exportedAt = new Date().toISOString()): unknown {
  const report = buildPracticeReport(input, exportedAt);
  if (report.isDemo) return report;
  if (input.evidence.result) input = { ...input, evidence: { ...input.evidence, result: normalizeMeasurementResult(input.evidence.result) } };
  const selected = (root: unknown, fields: string[]): RecordValue => Object.fromEntries(fields.filter(key => Object.hasOwn(asRecord(root), key)).map(key => [key, asRecord(root)[key]]));
  const scrub = (value: unknown, depth = 0): unknown => {
    if (depth > 16) return null;
    if (typeof value === "string") return text(value, "");
    if (typeof value === "number") return finite(value);
    if (value === null || typeof value === "boolean") return value;
    if (Array.isArray(value)) return value.map(item => scrub(item, depth + 1));
    return Object.fromEntries(Object.entries(asRecord(value)).filter(([key]) => key === "wrist_path_torso" || !/(?:path|url|token|secret|password|authorization|cookie|api.?key|__proto__|constructor)/i.test(key)).map(([key, item]) => [key, /^(?:technical_grade|formal_grade)$/.test(key) ? null : /(?:score_0_to_100|value_0_to_100)$/.test(key) ? key === "score_0_to_100" ? evidenceReferenceScore(value) : key === "reference_score_0_to_100" ? evidenceReferenceScore({ ...asRecord(value), score_0_to_100: item }) : null : scrub(item, depth + 1)]));
  };
  return scrub({
    schemaVersion: "rallymate-practice-report/1", exportedAt, jobId: report.jobId,
    result: { ...selected(input.evidence.result, ["job_id", "video_id", "measurement_contract", "measurement_update_required", "measurement_warnings_zh", "training_evaluation", "actions", "hit_statistics", "action_recognition", "footwork_review"]), analysis_report: analysisReportOf(input.evidence.result?.analysis_report) },
    assessment: selected(assessmentOf(input), ["assessment_version", "registry_version", "job_id", "overall_evidence_score_0_to_100", "formal_score_available", "formal_score_message_zh", "coverage", "coverage_detail", "family_summary", "techniques", "action_recognition"]),
    summary: selected(summaryOf(input), ["job_id", "input", "processing", "counts", "coverage", "action_recognition"]),
    trajectory: input.evidence.trajectory ? selected(input.evidence.trajectory, ["schema_version", "result_kind", "job_id", "status", "source", "ball", "racket", "limitations"]) : null,
  });
}

export function reportDownloadName(report: PracticeReport, extension: "md" | "html" | "json"): string {
  return `rallymate-${report.isDemo ? "demo" : "analysis"}-${report.jobId ?? "imported"}.${extension}`;
}
