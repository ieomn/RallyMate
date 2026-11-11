import { DOMAINS, GATES, type LevelAssessment } from "../scoring/level-grading";
import currentPolicy from "../../../src/rallymate_scoring/data/level_grading_v1.json";
import legacyPolicy from "../../../src/rallymate_scoring/data/level_grading_legacy_v1.json";

const object = (value: unknown): Record<string, unknown> => value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
const bounded = (v: unknown, max = 1): v is number => typeof v === "number" && Number.isFinite(v) && v >= 0 && v <= max;
const strings = (v: unknown): v is string[] => Array.isArray(v) && v.every(s => typeof s === "string") && new Set(v).size === v.length;
const close = (a: number, b: number) => Math.abs(a - b) <= 0.0002;

/** Validate the contract and recompute totals before displaying API values. */
export function levelAssessmentOf(value: unknown): LevelAssessment | null {
  const row = object(value);
  // Historical exports keep their original grades and policy. New scoring uses
  // currentPolicy only; this read path never upgrades or rewrites saved scores.
  const policy = [currentPolicy, legacyPolicy].find(policy => policy.version === row.policy_version);
  if (row.version !== "level-assessment-v1.0.0" || !policy ||
      !Number.isInteger(row.target_level) || !bounded(row.target_level, 5) || row.target_level < 1 ||
      !["one_handed", "two_handed"].includes(String(row.backhand_style))) return null;
  const domains = object(row.domains);
  const level = policy.levels[String(row.target_level) as keyof typeof policy.levels];
  const major = level.module_weights, configured = level.technique_weights;
  if (row.weight_source !== policy.internal_weight_source && row.weight_source !== "caller_override"
      || row.major_weight_source !== policy.major_weight_source
      || Object.keys(domains).length !== DOMAINS.length) return null;
  let total = 0, mass = 0, coverage = 0;
  for (const domain of DOMAINS) {
    const d = object(domains[domain]);
    if (d.score !== null && !bounded(d.score, 100) || d.family_score !== null && !bounded(d.family_score, 100) ||
        d.module_weight !== major[domain] || !bounded(d.coverage) || !strings(d.measured_techniques) || !strings(d.missing_techniques) || !strings(d.excluded_techniques)) return null;
    if (!["measured_techniques", "observed_family", "unobserved"].includes(String(d.score_source))) return null;
    if (d.active_weights === null || typeof d.active_weights !== "object" || Array.isArray(d.active_weights)
      || d.observed_weights === null || typeof d.observed_weights !== "object" || Array.isArray(d.observed_weights)) return null;
    const active = object(d.active_weights), observed = object(d.observed_weights);
    const activeKeys = Object.keys(active), observedKeys = Object.keys(observed);
    const forbidden = row.backhand_style === "two_handed" ? ["GS03", "RS03"] : ["GS02", "RS02"];
    const known = configured[domain];
    if (d.excluded_techniques.some(code => !Object.hasOwn(known, code) || !forbidden.includes(code))) return null;
    const expectedActive = Object.entries(known).filter(([code, weight]) => !forbidden.includes(code) && weight > 0);
    const expectedMass = expectedActive.reduce((sum, [, weight]) => sum + weight, 0);
    if (activeKeys.some(code => !Object.hasOwn(known, code) || forbidden.includes(code) || !bounded(active[code]) || active[code] === 0) ||
        !close(Object.values(active).reduce<number>((sum, w) => sum + Number(w), 0), activeKeys.length ? 1 : 0) ||
        row.weight_source !== "caller_override" && (activeKeys.length !== expectedActive.length
          || expectedActive.some(([code, weight]) => !Object.hasOwn(active, code) || !close(Number(active[code]), weight / expectedMass))) ||
        row.weight_source === "caller_override" && !activeKeys.length && expectedActive.length > 0 ||
        observedKeys.some(code => !(code in active) || !bounded(observed[code]) || observed[code] === 0) ||
        d.measured_techniques.length !== observedKeys.length || d.measured_techniques.some(code => !(code in observed))) return null;
    const observedMass = observedKeys.reduce((sum, code) => sum + Number(active[code]), 0);
    if (!close(d.coverage, observedMass) || observedKeys.some(code => !close(Number(observed[code]), Number(active[code]) / observedMass))) return null;
    if (d.missing_techniques.length !== activeKeys.length - observedKeys.length || d.missing_techniques.some(code => !(code in active) || code in observed)) return null;
    if (observedKeys.length ? d.score_source !== "measured_techniques" || d.score === null :
        d.family_score !== null ? d.score_source !== "observed_family" || d.score === null || !close(Number(d.score), Number(d.family_score)) : d.score_source !== "unobserved" || d.score !== null) return null;
    if (d.score !== null && major[domain] > 0) { total += Number(d.score) * major[domain]; mass += major[domain]; }
    coverage += d.coverage * major[domain];
  }
  if (!bounded(row.weighted_coverage) || !bounded(row.observed_module_weight) || !close(row.weighted_coverage, coverage) || !close(row.observed_module_weight, mass)) return null;
  if (mass ? !bounded(row.overall_score, 100) || !close(row.overall_score, total / mass) || row.status !== "scored" : row.overall_score !== null || row.status !== "no_measured_active_techniques") return null;
  const gates = object(row.gatekeepers), states: Record<string, string> = {};
  for (const [name, keys] of Object.entries(GATES)) {
    const g = object(gates[name]), checks = object(g.checks);
    if (Object.keys(checks).length !== keys.length || keys.some(key => checks[key] !== null && typeof checks[key] !== "boolean") || !strings(g.missing_evidence) || !strings(g.failed_checks)) return null;
    const status = keys.every(key => checks[key] === true) ? "passed" : keys.some(key => checks[key] === false) ? "failed" : "pending";
    if (g.status !== status || JSON.stringify([...g.missing_evidence].sort()) !== JSON.stringify(keys.filter(key => checks[key] === null).sort()) || JSON.stringify([...g.failed_checks].sort()) !== JSON.stringify(keys.filter(key => checks[key] === false).sort())) return null;
    states[name] = status;
  }
  const eligible = Math.min(row.target_level, states.intermediate !== "passed" ? 2 : states.advanced !== "passed" ? 3 : 5);
  if (row.eligible_level !== (mass ? eligible : null)) return null;
  return row as unknown as LevelAssessment;
}

export function levelAssessmentsOf(value: unknown): LevelAssessment[] {
  const source = Array.isArray(value) ? value : Object.values(object(value));
  const levels = source.map(levelAssessmentOf).filter((row): row is LevelAssessment => row !== null);
  if (new Set(levels.map(row => row.target_level)).size !== levels.length) return [];
  return levels.sort((a, b) => a.target_level - b.target_level);
}
