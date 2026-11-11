import { DOMAINS, GATES, moduleWeightsFor, type Level, type LevelAssessment } from "../scoring/level-grading";

const object = (value: unknown): Record<string, unknown> => value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
const bounded = (v: unknown, max = 1): v is number => typeof v === "number" && Number.isFinite(v) && v >= 0 && v <= max;
const strings = (v: unknown): v is string[] => Array.isArray(v) && v.every(s => typeof s === "string") && new Set(v).size === v.length;
const close = (a: number, b: number) => Math.abs(a - b) <= 0.0002;

/** Validate the contract and recompute totals before displaying API values. */
export function levelAssessmentOf(value: unknown): LevelAssessment | null {
  const row = object(value);
  if (row.version !== "level-assessment-v1.0.0" || row.policy_version !== "level-grading-policy-v1.0.0" ||
      !Number.isInteger(row.target_level) || !bounded(row.target_level, 5) || row.target_level < 1 ||
      !["one_handed", "two_handed"].includes(String(row.backhand_style))) return null;
  const domains = object(row.domains), major = moduleWeightsFor(row.target_level as Level);
  let total = 0, mass = 0, coverage = 0;
  for (const domain of DOMAINS) {
    const d = object(domains[domain]);
    if (d.score !== null && !bounded(d.score, 100) || d.family_score !== null && !bounded(d.family_score, 100) ||
        d.module_weight !== major[domain] || !bounded(d.coverage) || !strings(d.measured_techniques) || !strings(d.missing_techniques) || !strings(d.excluded_techniques)) return null;
    if (!["measured_techniques", "observed_family", "unobserved"].includes(String(d.score_source))) return null;
    const active = object(d.active_weights), observed = object(d.observed_weights);
    const activeKeys = Object.keys(active), observedKeys = Object.keys(observed);
    const forbidden = row.backhand_style === "two_handed" ? ["GS03", "RS03"] : ["GS02", "RS02"];
    if (!activeKeys.length || activeKeys.some(code => !new RegExp(`^${domain}\\d{2}$`).test(code) || forbidden.includes(code) || !bounded(active[code]) || active[code] === 0) ||
        !close(Object.values(active).reduce<number>((sum, w) => sum + Number(w), 0), 1) ||
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
