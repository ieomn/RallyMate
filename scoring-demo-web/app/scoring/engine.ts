export type Domain = "GS" | "FS";
export type Dependency = "pose" | "ball" | "racket" | "court" | "tracking";
export type Grade = "A" | "B" | "C" | "D" | "E";
export type EvaluationStatus = "scored" | "ready" | "partial" | "blocked";

export interface MetricCard {
  id: string;
  domain: Domain;
  eventCode: string;
  stageCode: string;
  stageName: string;
  stageOrder: number;
  startAction: string;
  endAction: string;
  name: string;
  definition: string;
  sourceKey: string;
  reuseSource: string;
  currentPoints: string;
  requiredPoints: string;
  sourceStatus: string;
  calculation: string;
  dependencies: Dependency[];
  grades: Record<Grade, string>;
  unavailable: string;
  positiveFeedback: string;
  improvementFeedback: string;
}

export interface Scenario {
  id: string;
  label: string;
  shortLabel: string;
  mode: "demo" | "real";
  description: string;
  source: string;
  baseScore: number | null;
  eventConfidence: number;
  dependencyCoverage: Record<Dependency, number>;
  globalCoverage?: Partial<Record<Dependency, number>>;
  /** Provenance for each dependency value; UI must not present proxies as measurements. */
  coverageSource?: Partial<Record<Dependency, string>>;
  facts: Array<{ label: string; value: string }>;
  caveat: string;
}

export interface DimensionScores {
  technique: number;
  timing: number;
  stability: number;
  continuity: number;
}

export interface CardResult {
  card: MetricCard;
  status: EvaluationStatus;
  score: number | null;
  grade: Grade | null;
  evidence: number;
  confidence: number;
  dimensions: DimensionScores | null;
  mandatoryDependencies: Dependency[];
  optionalDependencies: Dependency[];
  missingDependencies: Dependency[];
  verdict: string;
  feedback: string;
}

export interface GroupResult {
  code: string;
  name: string;
  score: number | null;
  evidence: number;
  total: number;
  scored: number;
  ready: number;
  partial: number;
  blocked: number;
}

export interface DomainResult extends GroupResult {
  domain: Domain;
  groups: GroupResult[];
}

export interface ScoreReport {
  reportVersion: "1.0.0";
  generatedAt: string;
  scenario: Scenario;
  overallScore: number | null;
  overallGrade: Grade | null;
  overallEvidence: number;
  acceptanceStatus: "complete-demo" | "provisional" | "evidence-audit";
  domains: Record<Domain, DomainResult>;
  results: CardResult[];
  formula: {
    dimensionWeights: Record<keyof DimensionScores, number>;
    moduleWeights: Record<Domain, number>;
    gradeThresholds: Record<Grade, string>;
    aggregation: string;
  };
}

export const DIMENSION_WEIGHTS: Record<keyof DimensionScores, number> = {
  technique: 0.4,
  timing: 0.25,
  stability: 0.2,
  continuity: 0.15,
};

export const MODULE_WEIGHTS: Record<Domain, number> = { GS: 0.7, FS: 0.3 };

const clamp = (value: number, min = 0, max = 100) =>
  Math.max(min, Math.min(max, value));

const round1 = (value: number) => Math.round(value * 10) / 10;

function hash01(value: string): number {
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0) / 4294967295;
}

export function gradeFor(score: number): Grade {
  if (score >= 90) return "A";
  if (score >= 80) return "B";
  if (score >= 70) return "C";
  if (score >= 60) return "D";
  return "E";
}

function isOptional(card: MetricCard, dependency: Dependency): boolean {
  const text = card.requiredPoints;
  if (dependency === "ball") {
    return /可选[^；。]*BALL|可选对手与球轨迹/i.test(text) && !/必须[^；。]*BALL/i.test(text);
  }
  if (dependency === "court") {
    return /场地(?:信息|标定|单应性)?(?:可选|建议)|单应性建议/.test(text);
  }
  return false;
}

function evidenceFor(
  card: MetricCard,
  scenario: Scenario,
): {
  evidence: number;
  mandatory: Dependency[];
  optional: Dependency[];
  missing: Dependency[];
} {
  const optional = card.dependencies.filter((dep) => isOptional(card, dep));
  const mandatory = card.dependencies.filter((dep) => !optional.includes(dep));
  const mandatoryCoverage = mandatory.length
    ? Math.min(...mandatory.map((dep) => scenario.dependencyCoverage[dep]))
    : 1;
  const optionalCoverage = optional.length
    ? optional.reduce((sum, dep) => sum + scenario.dependencyCoverage[dep], 0) /
      optional.length
    : mandatoryCoverage;
  const evidence = mandatoryCoverage * 0.85 + optionalCoverage * 0.15;
  const missing = mandatory.filter(
    (dependency) => scenario.dependencyCoverage[dependency] < 0.65,
  );
  return { evidence, mandatory, optional, missing };
}

function dimensionScores(card: MetricCard, scenario: Scenario): DimensionScores {
  const base = scenario.baseScore ?? 0;
  const emphasisText = `${card.name} ${card.definition} ${card.calculation}`;
  const emphasis = {
    timing: /时机|同步|时间|节奏|早|晚/.test(emphasisText) ? 2.5 : 0,
    stability: /稳定|平衡|波动|对称|重心/.test(emphasisText) ? 2 : 0,
    continuity: /连续|流畅|轨迹|恢复|衔接/.test(emphasisText) ? 2 : 0,
  };
  const jitter = (key: string, span: number) =>
    (hash01(`${scenario.id}:${card.id}:${key}`) - 0.5) * span;
  return {
    technique: round1(clamp(base + jitter("technique", 15))),
    timing: round1(clamp(base + emphasis.timing + jitter("timing", 18))),
    stability: round1(clamp(base + emphasis.stability + jitter("stability", 16))),
    continuity: round1(clamp(base + emphasis.continuity + jitter("continuity", 17))),
  };
}

function evaluateCard(card: MetricCard, scenario: Scenario): CardResult {
  const { evidence, mandatory, optional, missing } = evidenceFor(card, scenario);
  const confidence = round1(scenario.eventConfidence * evidence * 100) / 100;

  if (scenario.mode === "real") {
    const status: EvaluationStatus =
      missing.length === 0 && evidence >= 0.72
        ? "ready"
        : evidence >= 0.4
          ? "partial"
          : "blocked";
    const verdict =
      status === "ready"
        ? "一期证据达到事件层接入门槛；完成事件切分与特征标定后才可出技术等级。"
        : status === "partial"
          ? "部分证据可用，但连续性或专项目标不足，仅能做预检。"
          : "关键证据不足，本项不可评价。";
    return {
      card,
      status,
      score: null,
      grade: null,
      evidence: round1(evidence * 100) / 100,
      confidence,
      dimensions: null,
      mandatoryDependencies: mandatory,
      optionalDependencies: optional,
      missingDependencies: missing,
      verdict,
      feedback: status === "ready" ? "进入事件识别与特征计算队列。" : card.unavailable || verdict,
    };
  }

  const dimensions = dimensionScores(card, scenario);
  const score = round1(
    Object.entries(DIMENSION_WEIGHTS).reduce(
      (sum, [key, weight]) => sum + dimensions[key as keyof DimensionScores] * weight,
      0,
    ),
  );
  const grade = gradeFor(score);
  return {
    card,
    status: "scored",
    score,
    grade,
    evidence: round1(evidence * 100) / 100,
    confidence,
    dimensions,
    mandatoryDependencies: mandatory,
    optionalDependencies: optional,
    missingDependencies: [],
    verdict: card.grades[grade] || `${grade} 级：已按统一阈值完成评价。`,
    feedback:
      grade === "A" || grade === "B"
        ? card.positiveFeedback || "动作表现稳定，保持当前节奏。"
        : card.improvementFeedback || "优先修正本项最低分维度。",
  };
}

function aggregate(
  code: string,
  name: string,
  results: CardResult[],
): GroupResult {
  const scoredResults = results.filter((result) => result.score !== null);
  const score = scoredResults.length
    ? round1(
        scoredResults.reduce((sum, result) => sum + (result.score ?? 0), 0) /
          scoredResults.length,
      )
    : null;
  return {
    code,
    name,
    score,
    evidence: round1(
      (results.reduce((sum, result) => sum + result.evidence, 0) /
        Math.max(results.length, 1)) *
        100,
    ),
    total: results.length,
    scored: results.filter((result) => result.status === "scored").length,
    ready: results.filter((result) => result.status === "ready").length,
    partial: results.filter((result) => result.status === "partial").length,
    blocked: results.filter((result) => result.status === "blocked").length,
  };
}

function aggregateDomain(domain: Domain, results: CardResult[]): DomainResult {
  const eventCodes = [...new Set(results.map((result) => result.card.eventCode))];
  const groups = eventCodes.map((eventCode) => {
    const eventResults = results.filter((result) => result.card.eventCode === eventCode);
    const name =
      domain === "GS"
        ? eventResults[0]?.card.stageName.split(" ")[0] || eventCode
        : eventResults[0]?.card.stageName || eventCode;
    return aggregate(eventCode, name, eventResults);
  });
  const scoredGroups = groups.filter((group) => group.score !== null);
  const base = aggregate(domain, domain === "GS" ? "底线击球" : "步伐事件", results);
  return {
    ...base,
    domain,
    score: scoredGroups.length
      ? round1(
          scoredGroups.reduce((sum, group) => sum + (group.score ?? 0), 0) /
            scoredGroups.length,
        )
      : null,
    groups,
  };
}

export function buildScoreReport(cards: MetricCard[], scenario: Scenario): ScoreReport {
  const results = cards.map((card) => evaluateCard(card, scenario));
  const gs = aggregateDomain(
    "GS",
    results.filter((result) => result.card.domain === "GS"),
  );
  const fs = aggregateDomain(
    "FS",
    results.filter((result) => result.card.domain === "FS"),
  );
  const overallScore =
    gs.score !== null && fs.score !== null
      ? round1(gs.score * MODULE_WEIGHTS.GS + fs.score * MODULE_WEIGHTS.FS)
      : null;
  const overallEvidence = round1(
    gs.evidence * MODULE_WEIGHTS.GS + fs.evidence * MODULE_WEIGHTS.FS,
  );
  return {
    reportVersion: "1.0.0",
    generatedAt: new Date().toISOString(),
    scenario,
    overallScore,
    overallGrade: overallScore === null ? null : gradeFor(overallScore),
    overallEvidence,
    acceptanceStatus:
      scenario.mode === "real"
        ? "evidence-audit"
        : overallEvidence >= 90
          ? "complete-demo"
          : "provisional",
    domains: { GS: gs, FS: fs },
    results,
    formula: {
      dimensionWeights: DIMENSION_WEIGHTS,
      moduleWeights: MODULE_WEIGHTS,
      gradeThresholds: {
        A: "90–100",
        B: "80–89.9",
        C: "70–79.9",
        D: "60–69.9",
        E: "0–59.9",
      },
      aggregation: "指标→事件等权平均；GS/FS 模块按 70%/30% 合成。技术得分与证据覆盖率分开呈现。",
    },
  };
}

export function scenarioFromStage1Summary(summary: Record<string, unknown>): Scenario {
  const asRecord = (value: unknown): Record<string, unknown> =>
    value !== null && typeof value === "object" ? (value as Record<string, unknown>) : {};
  const coverage = asRecord(summary.coverage);
  const primaryContainer = asRecord(summary.primary_player);
  const primary = asRecord(primaryContainer.diagnostics ?? primaryContainer);
  const input = asRecord(summary.input);
  const video = asRecord(input.video);
  const processing = asRecord(summary.processing);
  const jobId = String(summary.job_id ?? `imported-${Date.now()}`);
  const finiteFraction = (value: unknown, fallback = 0): number => {
    const numeric = typeof value === "number" && Number.isFinite(value) ? value : fallback;
    return Math.max(0, Math.min(1, numeric));
  };
  const hasNumber = (value: unknown): value is number =>
    typeof value === "number" && Number.isFinite(value);
  const globalPose = finiteFraction(coverage.pose_frame_fraction);
  const globalTracking = finiteFraction(coverage.player_frame_fraction);
  const primaryPose = hasNumber(primary.pose_coverage_fraction)
    ? finiteFraction(primary.pose_coverage_fraction)
    : globalPose;
  const primaryTracking = hasNumber(primary.track_coverage_fraction)
    ? finiteFraction(primary.track_coverage_fraction)
    : globalTracking;
  const hasCalibratedCourt = hasNumber(coverage.court_calibrated_fraction);
  const court = hasCalibratedCourt
    ? finiteFraction(coverage.court_calibrated_fraction)
    : finiteFraction(coverage.court_detected_fraction);
  return {
    id: jobId,
    label: `导入一期结果 · ${jobId.replace(/^full-test-/, "").slice(0, 8)}`,
    shortLabel: "导入结果",
    mode: "real",
    description: "由用户导入的 Stage 1 summary.json 生成证据可用性审计。",
    source: "Stage 1 summary.json（用户导入）",
    baseScore: null,
    eventConfidence: 0,
    dependencyCoverage: {
      pose: primaryPose,
      ball: finiteFraction(coverage.ball_frame_fraction),
      racket: finiteFraction(coverage.racket_frame_fraction),
      court,
      tracking: primaryTracking,
    },
    globalCoverage: {
      pose: globalPose,
      ball: finiteFraction(coverage.ball_frame_fraction),
      racket: finiteFraction(coverage.racket_frame_fraction),
      court: court,
      tracking: globalTracking,
    },
    coverageSource: {
      pose: hasNumber(primary.pose_coverage_fraction)
        ? "summary.primary_player.pose_coverage_fraction"
        : "summary.coverage.pose_frame_fraction (global upper-bound proxy)",
      tracking: hasNumber(primary.track_coverage_fraction)
        ? "summary.primary_player.track_coverage_fraction"
        : "summary.coverage.player_frame_fraction (global upper-bound proxy)",
      ball: "summary.coverage.ball_frame_fraction",
      racket: "summary.coverage.racket_frame_fraction",
      court: hasCalibratedCourt
        ? "summary.coverage.court_calibrated_fraction"
        : "summary.coverage.court_detected_fraction (region hint; not calibration)",
    },
    facts: [
      { label: "分辨率", value: `${video.width ?? "?"} × ${video.height ?? "?"}` },
      { label: "帧率", value: `${video.fps ?? "?"} fps` },
      { label: "处理帧", value: String(processing.processed_frames ?? "?") },
    ],
    caveat: hasNumber(primary.pose_coverage_fraction) || hasNumber(primary.track_coverage_fraction)
      ? "主球员覆盖率优先取 primary_player 诊断；场地只在存在 calibrated_fraction 时用于场地依赖指标。新技术目录仍只输出证据就绪度，不输出技术等级。"
      : "summary.json 没有主球员轨迹诊断，本审计把全局 pose/track 覆盖率标记为上限代理；场地检测区域不等同标定，因此不会输出技术等级。",
  };
}
