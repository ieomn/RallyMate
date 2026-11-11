import policy from "../../../src/rallymate_scoring/data/level_grading_v1.json";

export const DOMAINS = ["GS", "FS", "SS", "RS", "NS", "DS", "OS", "TS"] as const;
export type ScoringDomain = typeof DOMAINS[number];
export type Level = 1 | 2 | 3 | 4 | 5;
export type BackhandStyle = "two_handed" | "one_handed";
export const GATES = {
  intermediate: ["non_dominant_hand_ok", "stroke_continuity_ok", "body_coordination_ok"],
  advanced: ["contact_point_reasonable", "serve_fluency_ok", "ball_control_consistency_ok"],
} as const;
export type GateKey = typeof GATES[keyof typeof GATES][number];
type Gate = { status: "passed" | "failed" | "pending"; checks: Partial<Record<GateKey, boolean | null>>; missing_evidence: string[]; failed_checks: string[] };
export type LevelDomain = { score: number | null; family_score: number | null; score_source: string; module_weight: number; coverage: number; active_weights: Record<string, number>; observed_weights: Record<string, number>; measured_techniques: string[]; missing_techniques: string[]; excluded_techniques: string[] };
export type LevelAssessment = {
  version: "level-assessment-v1.0.0"; policy_version: string;
  status: "scored" | "no_measured_active_techniques";
  target_level: Level; eligible_level: Level | null; backhand_style: BackhandStyle;
  overall_score: number | null; weighted_coverage: number; observed_module_weight: number;
  score_semantics: string; level_semantics: string; weight_source: string; major_weight_source: string;
  domains: Record<ScoringDomain, LevelDomain>; gatekeepers: { intermediate: Gate; advanced: Gate };
};
type Weights = Record<ScoringDomain, Record<string, number>>;
const finite = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
const round = (v: number, places = 4) => Math.round(v * 10 ** places) / 10 ** places;
const normalizeCode = (code: string) => {
  if (!/^(GS|FS|SS|RS|NS|DS|OS|TS)-?\d{2}$/.test(code)) throw new Error(`Unknown technique code: ${code}`);
  return code.replace("-", "");
};
export function moduleWeightsFor(level: Level): Record<ScoringDomain, number> {
  if (!Number.isInteger(level) || level < 1 || level > 5) throw new Error("targetLevel must be 1 to 5");
  return { ...policy.levels[String(level) as keyof typeof policy.levels].module_weights };
}

export function gradeLevel(techniqueScores: Record<string, number | null>, options: {
  targetLevel?: Level; backhandStyle?: BackhandStyle; gates?: Partial<Record<GateKey, boolean | null>>;
  techniqueWeights?: Partial<Weights>;
  domainScores?: Partial<Record<ScoringDomain, number | null>>;
} = {}): LevelAssessment {
  const target = options.targetLevel ?? 1, style = options.backhandStyle ?? "two_handed";
  const major = moduleWeightsFor(target);
  if (style !== "two_handed" && style !== "one_handed") throw new Error("Invalid backhand style");
  const configured = policy.levels[String(target) as keyof typeof policy.levels].technique_weights;
  const known = new Set(Object.values(configured).flatMap(group => Object.keys(group)));
  const scores: Record<string, number | null> = {};
  for (const [domain, score] of Object.entries(options.domainScores ?? {})) {
    if (!DOMAINS.includes(domain as ScoringDomain) || score !== null && (!finite(score) || score < 0 || score > 100)) throw new Error("Invalid domain score");
  }
  for (const [raw, score] of Object.entries(techniqueScores)) {
    const code = normalizeCode(raw);
    if (!known.has(code) || code in scores || score !== null && (!finite(score) || score < 0 || score > 100)) throw new Error("Invalid or duplicate technique score");
    scores[code] = score;
  }
  const excluded = new Set(style === "two_handed" ? ["GS03", "RS03"] : ["GS02", "RS02"]);
  const weights = {} as Weights;
  for (const domain of DOMAINS) weights[domain] = { ...configured[domain] };
  for (const [domain, group] of Object.entries(options.techniqueWeights ?? {})) {
    if (!DOMAINS.includes(domain as ScoringDomain) || !group || !Object.keys(group).length) throw new Error("Invalid technique weight override");
    const override: Record<string, number> = {};
    for (const [raw, weight] of Object.entries(group)) {
      const code = normalizeCode(raw);
      if (!known.has(code) || !code.startsWith(domain) || code in override || !finite(weight) || weight < 0) throw new Error("Invalid technique weight");
      override[code] = weight;
    }
    if (!Object.entries(override).some(([code, weight]) => weight > 0 && !excluded.has(code))) throw new Error("No active positive technique weight");
    weights[domain as ScoringDomain] = override;
  }
  const domains = {} as Record<ScoringDomain, LevelDomain>;
  let total = 0, observedWeight = 0, coverage = 0;
  for (const domain of DOMAINS) {
    const active = Object.entries(weights[domain]).filter(([code, weight]) => !excluded.has(code) && weight > 0);
    const denominator = active.reduce((sum, [, w]) => sum + w, 0);
    const normalized = Object.fromEntries(active.map(([code, w]) => [code, w / denominator]));
    const observed = Object.entries(normalized).filter(([code]) => scores[code] !== undefined && scores[code] !== null);
    const mass = observed.reduce((sum, [, w]) => sum + w, 0);
    const family = options.domainScores?.[domain] ?? null;
    const score = mass ? observed.reduce((sum, [code, w]) => sum + scores[code]! * w, 0) / mass : family;
    if (score !== null && major[domain] > 0) { total += score * major[domain]; observedWeight += major[domain]; }
    coverage += mass * major[domain];
    domains[domain] = { score: score === null ? null : round(score), family_score: family, score_source: mass ? "measured_techniques" : score !== null ? "observed_family" : "unobserved", module_weight: major[domain], coverage: round(mass, 6), active_weights: normalized,
      observed_weights: Object.fromEntries(observed.map(([code, w]) => [code, w / mass])),
      measured_techniques: observed.map(([code]) => code).sort(), missing_techniques: active.map(([code]) => code).filter(code => scores[code] == null).sort(),
      excluded_techniques: Object.keys(weights[domain]).filter(code => excluded.has(code)).sort() };
  }
  const values = options.gates ?? {}, allowed = new Set<string>(Object.values(GATES).flat());
  for (const [key, value] of Object.entries(values)) if (!allowed.has(key) || value !== null && typeof value !== "boolean") throw new Error("Invalid promotion gate");
  const gatekeepers = {} as LevelAssessment["gatekeepers"];
  for (const [name, keys] of Object.entries(GATES)) {
    const checks = Object.fromEntries(keys.map(key => [key, values[key] ?? null]));
    const status = Object.values(checks).every(v => v === true) ? "passed" : Object.values(checks).some(v => v === false) ? "failed" : "pending";
    gatekeepers[name as keyof typeof GATES] = { status, checks, missing_evidence: keys.filter(key => checks[key] === null), failed_checks: keys.filter(key => checks[key] === false) };
  }
  const eligible = Math.min(target, gatekeepers.intermediate.status !== "passed" ? 2 : gatekeepers.advanced.status !== "passed" ? 3 : 5) as Level;
  return { version: "level-assessment-v1.0.0", policy_version: policy.version, status: observedWeight ? "scored" : "no_measured_active_techniques",
    target_level: target, eligible_level: observedWeight ? eligible : null, backhand_style: style, overall_score: observedWeight ? round(total / observedWeight) : null,
    weighted_coverage: round(coverage, 6), observed_module_weight: round(observedWeight, 6), domains, gatekeepers,
    score_semantics: "measured_active_techniques_with_available_module_weight_normalization", weight_source: options.techniqueWeights && Object.keys(options.techniqueWeights).length ? "caller_override" : policy.internal_weight_source,
    major_weight_source: policy.major_weight_source, level_semantics: "requested_tier_with_promotion_gates_not_skill_certification" };
}
