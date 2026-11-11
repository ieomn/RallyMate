import { DOMAINS, gradeLevel, moduleWeightsFor, type BackhandStyle, type GateKey, type Level, type LevelAssessment, type ScoringDomain } from "./level-grading";
export type Domain = ScoringDomain;
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
  /** Kept for old saved scenario compatibility; never used to generate a score. */
  baseScore: number | null;
  cardScores?: Record<string, number | null>;
  cardWeights?: Record<string, number>;
  techniqueScores?: Record<string, number | null>;
  targetLevel?: Level;
  backhandStyle?: BackhandStyle;
  gates?: Partial<Record<GateKey, boolean | null>>;
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
  levelAssessment: LevelAssessment;
  acceptanceStatus: "complete-demo" | "provisional" | "evidence-audit";
  domains: Record<Domain, DomainResult>;
  results: CardResult[];
  formula: {
    dimensionWeights: Partial<Record<keyof DimensionScores, number>>;
    moduleWeights: Record<Domain, number>;
    gradeThresholds: Record<Grade, string>;
    aggregation: string;
  };
}

const round1 = (value: number) => Math.round(value * 10) / 10;
const finiteScore = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 100;

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
    (dependency) => scenario.dependencyCoverage[dependency] < 0.70,
  );
  return { evidence, mandatory, optional, missing };
}

function evaluateCard(card: MetricCard, scenario: Scenario): CardResult {
  const { evidence, mandatory, optional, missing } = evidenceFor(card, scenario);
  const confidence = round1(scenario.eventConfidence * evidence * 100) / 100;
  const measuredScore = scenario.cardScores?.[card.id];
  // Explicit upstream measurements are independent of scenario identity and
  // global coverage. Missing unrelated dependencies cannot erase a valid score.
  const score = finiteScore(measuredScore) ? measuredScore : null;
  const grade = score === null ? null : gradeFor(score);
  const status: EvaluationStatus = score !== null ? "scored" : missing.length === 0 && evidence >= 0.70 ? "ready" : evidence >= 0.4 ? "partial" : "blocked";
  const verdict = grade ? card.grades[grade] || `${grade} 级：按已提供的实测规则评分。` : "尚未收到本项的实测评分；可用测量保留在报告中。";
  return { card, status, score, grade, evidence: round1(evidence * 100) / 100, confidence, dimensions: null,
    mandatoryDependencies: mandatory, optionalDependencies: optional, missingDependencies: missing,
    verdict, feedback: grade === "A" || grade === "B" ? card.positiveFeedback || "保持当前动作节奏。" : grade ? card.improvementFeedback || "优先练习本项动作。" : verdict };
}

function aggregate(
  code: string,
  name: string,
  results: CardResult[],
  cardWeights: Record<string, number> = {},
): GroupResult {
  const weightFor = (id: string) => cardWeights[id] ?? 1;
  for (const result of results) if (!Number.isFinite(weightFor(result.card.id)) || weightFor(result.card.id) < 0) throw new Error("Card weights must be finite and nonnegative");
  const scoredResults = results.filter((result) => result.score !== null && weightFor(result.card.id) > 0);
  const score = scoredResults.length
    ? round1(
        scoredResults.reduce((sum, result) => sum + result.score! * weightFor(result.card.id), 0) /
          scoredResults.reduce((sum, result) => sum + weightFor(result.card.id), 0),
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

const domainNames: Record<Domain, string> = { GS: "底线基础", FS: "步伐移动", SS: "发球", RS: "接发", NS: "网前", DS: "防守", OS: "高压", TS: "战术" };

export function buildScoreReport(cards: MetricCard[], scenario: Scenario): ScoreReport {
  const results = cards.map((card) => evaluateCard(card, scenario));
  const techniqueScores: Record<string, number | null> = {};
  const grouped = new Map<string, GroupResult>();
  for (const eventCode of new Set(cards.map(card => card.eventCode))) {
    const eventResults = results.filter(result => result.card.eventCode === eventCode);
    const group = aggregate(eventCode, eventResults[0]?.card.stageName || eventCode, eventResults, scenario.cardWeights);
    grouped.set(eventCode, group);
    techniqueScores[eventCode] = group.score;
  }
  Object.assign(techniqueScores, scenario.techniqueScores ?? {});
  const levelAssessment = gradeLevel(techniqueScores, { targetLevel: scenario.targetLevel, backhandStyle: scenario.backhandStyle, gates: scenario.gates });
  const domains = Object.fromEntries(DOMAINS.map(domain => {
    const domainResults = results.filter(result => result.card.domain === domain);
    const groups = [...new Set(domainResults.map(result => result.card.eventCode))].map(code => grouped.get(code)!);
    return [domain, { ...aggregate(domain, domainNames[domain], domainResults, scenario.cardWeights), domain, score: levelAssessment.domains[domain].score, groups }];
  })) as Record<Domain, DomainResult>;
  const moduleWeights = moduleWeightsFor(scenario.targetLevel ?? 1);
  const overallEvidence = round1(DOMAINS.reduce((sum, domain) => sum + domains[domain].evidence * moduleWeights[domain], 0));
  const overallScore = levelAssessment.overall_score;
  return { reportVersion: "1.0.0", generatedAt: new Date().toISOString(), scenario, overallScore,
    overallGrade: overallScore === null ? null : gradeFor(overallScore), overallEvidence, levelAssessment,
    acceptanceStatus: overallScore === null ? "evidence-audit" : "provisional", domains, results,
    formula: { dimensionWeights: {}, moduleWeights, gradeThresholds: { A: "90–100", B: "80–89.9", C: "70–79.9", D: "60–69.9", E: "0–59.9" },
      aggregation: "实测指标按配置汇总为子技术；当前等级的子技术权重和系统权重分别归一化。单反与双反二选一；缺测影响覆盖率，不作零分。" } };
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
