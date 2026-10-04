import type { DemoResultResponse, IndicatorEvaluation } from "./api-types";
import { evidenceReferenceScore } from "./measurement-evidence";
import { footworkEpisodes } from "./footwork-review";
import { reportVideoDurationMs } from "./workspace-navigation";

export const SCORE_COMPONENTS = {
  measured_instance_ratio: "可测片段比例",
  required_feature_coverage: "必需特征覆盖",
  median_feature_confidence: "特征置信度",
  repeatability: "跨片段重复性",
  scoring_evidence_ratio: "评分证据覆盖",
} as const;
export type ScoreComponentKey = keyof typeof SCORE_COMPONENTS;
export type ScoreComponent = {
  key: ScoreComponentKey; label: string; included: boolean; value: number | null;
  weight: number | null; maximum: number | null; earned: number | null; deduction: number | null; reason: string;
};
export type EvidenceGap = { label: string; unavailable: number; total: number };
export type EvidenceBlocker = { reason: string; affected: number };
export type IndicatorExplanation = {
  id: string; name: string; included: boolean; score: number | null; raw: number | null; rounding: number | null;
  components: ScoreComponent[]; gaps: EvidenceGap[]; blockers: EvidenceBlocker[];
};
export type VerifiedScoreExplanation = {
  status: "available" | "unavailable"; score: number | null; raw: number | null; rounding: number | null;
  indicatorRounding: number | null; aggregateRounding: number | null; components: ScoreComponent[];
  indicators: IndicatorExplanation[]; includedIds: string[]; excludedIds: string[];
};
export type ScoreExplanationState = {
  status: "missing" | "invalid"; score: number | null;
} | { status: "verified"; score: number | null; explanation: VerifiedScoreExplanation };
export type ScoreEvidenceWindow = { id: string; startMs: number; endMs: number };

const KEYS = Object.keys(SCORE_COMPONENTS) as ScoreComponentKey[];
const record = (value: unknown): Record<string, unknown> | null => value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;
const finite = (value: unknown, min = 0, max = 100): value is number => typeof value === "number" && Number.isFinite(value) && value >= min && value <= max;
const count = (value: unknown): value is number => finite(value, 0, Number.MAX_SAFE_INTEGER) && Number.isSafeInteger(value);
const ARITHMETIC_TOLERANCE = 1e-7;
const close = (left: number, right: number) => Math.abs(left - right) <= ARITHMETIC_TOLERANCE;
// Python 3.10 may sum valid weighted 100% components to 100.00000000000001.
// Permit only arithmetic noise in the derived total, preserving the exact value
// and every closure check. Input percentages and displayed scores stay strict.
const finiteRawTotal = (value: unknown): value is number => finite(value, -ARITHMETIC_TOLERANCE, 100 + ARITHMETIC_TOLERANCE);
const label = (value: unknown): value is string => typeof value === "string" && value.trim().length > 0 && value.length <= 2000;
const id = (value: unknown): value is string => typeof value === "string" && /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}$/.test(value);
const ids = (value: unknown): value is string[] => Array.isArray(value) && value.every(id) && new Set(value).size === value.length;
const sum = (values: number[]) => values.reduce((total, value) => total + value, 0);
const declared = (value: unknown) => record(value)?.score_explanation;

/** Check the supplied rounded value; never replace Python's ties-to-even result with Math.round. */
function validPythonRounding(raw: number, displayed: unknown): displayed is number {
  if (!finite(displayed) || !Number.isInteger(displayed) || Math.abs(displayed - raw) > .5) return false;
  const fraction = raw - Math.floor(raw);
  return fraction !== .5 || displayed % 2 === 0;
}

function readComponents(value: unknown): ScoreComponent[] | null {
  if (!Array.isArray(value) || value.length !== KEYS.length) return null;
  const found = new Map<ScoreComponentKey, ScoreComponent>();
  for (const raw of value) {
    const row = record(raw);
    if (!row || typeof row.key !== "string" || !KEYS.includes(row.key as ScoreComponentKey) || found.has(row.key as ScoreComponentKey)
      || !label(row.label_zh) || !label(row.reason_zh) || typeof row.included !== "boolean") return null;
    const { value_percent: percent, effective_weight: weight, max_points: maximum, earned_points: earned, deduction_points: deduction } = row;
    if (row.included) {
      if (!finite(percent) || !finite(weight, Number.MIN_VALUE, 1) || !finite(maximum) || !finite(earned) || !finite(deduction)
        || !close(maximum, weight * 100) || !close(earned, percent * weight) || !close(deduction, maximum - earned)) return null;
    } else if ([percent, weight, maximum, earned, deduction].some(number => number !== null)) return null;
    const key = row.key as ScoreComponentKey;
    found.set(key, { key, label: SCORE_COMPONENTS[key], included: row.included, value: percent as number | null,
      weight: weight as number | null, maximum: maximum as number | null, earned: earned as number | null, deduction: deduction as number | null, reason: row.reason_zh });
  }
  return KEYS.map(key => found.get(key)!);
}

function readBase(value: unknown, parentScore: number | null, aggregation: string) {
  const raw = record(value);
  if (!raw || raw.version !== "evidence-score-explanation-v1.0.0" || raw.score_semantics !== "measurement_evidence_quality"
    || raw.aggregation !== aggregation || raw.rounding_method !== "nearest_integer_ties_to_even"
    || raw.deduction_semantics !== "evidence_reference_points_not_technical_fault_penalties"
    || !["available", "unavailable"].includes(String(raw.status))) return null;
  const components = readComponents(raw.components);
  if (!components || raw.score_0_to_100 !== parentScore) return null;
  if (raw.status === "unavailable") {
    if (parentScore !== null || raw.raw_score !== null || raw.rounding_adjustment_points !== null || components.some(item => item.included)) return null;
    return { raw, components, included: false, score: null, rawScore: null, rounding: null };
  }
  if (!finite(parentScore) || !Number.isInteger(parentScore) || !finiteRawTotal(raw.raw_score) || !finite(raw.rounding_adjustment_points, -1, 1)
    || !close(sum(components.map(item => item.weight ?? 0)), 1)
    || !close(sum(components.map(item => item.earned ?? 0)), raw.raw_score)
    || !close(raw.raw_score + raw.rounding_adjustment_points, parentScore)
    || !close(100 - sum(components.map(item => item.deduction ?? 0)) + raw.rounding_adjustment_points, parentScore)) return null;
  if (aggregation === "single_indicator_weighted_components" && !validPythonRounding(raw.raw_score, parentScore)) return null;
  return { raw, components, included: true, score: parentScore, rawScore: raw.raw_score, rounding: raw.rounding_adjustment_points };
}

function readIndicator(value: unknown): IndicatorExplanation | null {
  const source = record(value);
  if (!source || !id(source.indicator_id) || !label(source.name_zh) || source.score_semantics !== "measurement_evidence_quality") return null;
  const parent = evidenceReferenceScore(source);
  // Invalid or unavailable parent scores must not silently turn into a valid no-score explanation.
  if (source.score_0_to_100 !== parent) return null;
  const base = readBase(source.score_explanation, parent, "single_indicator_weighted_components");
  if (!base || base.included !== (source.available === true)) return null;
  const raw = base.raw;
  if (!count(raw.total_instance_count) || !count(raw.measured_instance_count) || raw.measured_instance_count > raw.total_instance_count
    || raw.total_instance_count !== source.total_instance_count || raw.measured_instance_count !== source.measured_instance_count
    || !Array.isArray(raw.feature_gaps) || !Array.isArray(raw.blocker_reasons)
    || raw.blocker_count_semantics !== "overlapping_validated_record_counts_not_additive_point_deductions") return null;
  const gaps: EvidenceGap[] = [], blockers: EvidenceBlocker[] = [];
  const featureIds = new Set<string>(), blockerIds = new Set<string>();
  for (const value of raw.feature_gaps) {
    const gap = record(value);
    if (!gap || !id(gap.feature_name) || featureIds.has(gap.feature_name) || !label(gap.label_zh)
      || ![gap.required_instance_count, gap.missing_instance_count, gap.excluded_by_measurement_gate_count, gap.unavailable_instance_count, gap.available_instance_count].every(count)
      || gap.required_instance_count !== raw.total_instance_count
      || Number(gap.missing_instance_count) + Number(gap.excluded_by_measurement_gate_count) !== gap.unavailable_instance_count
      || Number(gap.available_instance_count) + Number(gap.unavailable_instance_count) !== gap.required_instance_count) return null;
    featureIds.add(gap.feature_name);
    gaps.push({ label: gap.label_zh, unavailable: Number(gap.unavailable_instance_count), total: Number(gap.required_instance_count) });
  }
  for (const value of raw.blocker_reasons) {
    const blocker = record(value);
    if (!blocker || !id(blocker.key) || blockerIds.has(blocker.key) || !label(blocker.reason_zh)
      || !count(blocker.affected_instance_count) || blocker.affected_instance_count > raw.total_instance_count) return null;
    blockerIds.add(blocker.key);
    blockers.push({ reason: blocker.reason_zh, affected: blocker.affected_instance_count });
  }
  // Source percentages and weights provide a second, independent consistency check.
  const percentages = record(source.components), weights = record(source.effective_component_weights);
  if (!percentages || !weights || Object.keys(weights).some(key => !KEYS.includes(key as ScoreComponentKey))) return null;
  for (const component of base.components) {
    if (component.included && (!finite(percentages[`${component.key}_percent`]) || !finite(weights[component.key], 0, 1)
      || !close(component.value!, Number(percentages[`${component.key}_percent`])) || !close(component.weight!, Number(weights[component.key])))) return null;
    if (!component.included && Object.hasOwn(weights, component.key)) return null;
  }
  return { id: source.indicator_id, name: source.name_zh, included: base.included, score: base.score, raw: base.rawScore, rounding: base.rounding, components: base.components, gaps, blockers };
}

/** All displayed deductions must reconcile against every included indicator and the declared parent score. */
export function scoreExplanationOf(result: DemoResultResponse | null): ScoreExplanationState {
  const training = record(result?.training_evaluation);
  const score = evidenceReferenceScore(training);
  if (!training || declared(training) === undefined || declared(training) === null) return { status: "missing", score };
  const invalid: ScoreExplanationState = { status: "invalid", score };
  if (training.score_semantics !== "measurement_evidence_quality" || training.score_0_to_100 !== score || !Array.isArray(training.indicator_evaluations)) return invalid;
  const overall = readBase(declared(training), score, "unweighted_mean_of_available_indicator_reference_scores");
  if (!overall || overall.included !== (training.available === true)) return invalid;
  const values = training.indicator_evaluations.map(readIndicator);
  if (values.some(value => !value)) return invalid;
  const indicators = values as IndicatorExplanation[];
  if (new Set(indicators.map(item => item.id)).size !== indicators.length) return invalid;
  const included = indicators.filter(item => item.included), excluded = indicators.filter(item => !item.included);
  const raw = overall.raw;
  if (!ids(raw.included_indicator_ids) || !ids(raw.excluded_indicator_ids)
    || raw.included_indicator_ids.length !== included.length || raw.excluded_indicator_ids.length !== excluded.length
    || raw.included_indicator_ids.some(key => !included.some(item => item.id === key)) || raw.excluded_indicator_ids.some(key => !excluded.some(item => item.id === key))
    || raw.included_indicator_count !== included.length || raw.total_indicator_count !== indicators.length
    || !Array.isArray(raw.indicator_scores) || raw.indicator_scores.length !== included.length) return invalid;
  const scoreIds = new Set<string>();
  for (const value of raw.indicator_scores) {
    const row = record(value), match = included.find(item => item.id === row?.indicator_id);
    if (!row || !id(row.indicator_id) || scoreIds.has(row.indicator_id) || !match || row.score_0_to_100 !== match.score) return invalid;
    scoreIds.add(row.indicator_id);
  }
  const stages = record(raw.rounding_stages);
  if (!stages) return invalid;
  if (!overall.included) {
    if (included.length || stages.mean_indicator_rounding_points !== null || stages.aggregate_rounding_points !== null) return invalid;
    return { status: "verified", score: null, explanation: { status: "unavailable", score: null, raw: null, rounding: null, indicatorRounding: null, aggregateRounding: null, components: overall.components, indicators, includedIds: [], excludedIds: raw.excluded_indicator_ids } };
  }
  if (!included.length || !finite(stages.mean_indicator_rounding_points, -.50000001, .50000001) || !finite(stages.aggregate_rounding_points, -.50000001, .50000001)) return invalid;
  const mean = sum(included.map(item => item.score!)) / included.length;
  if (!finite(raw.rounded_indicator_mean) || !close(raw.rounded_indicator_mean, mean) || !validPythonRounding(raw.rounded_indicator_mean, score)
    || !close(stages.mean_indicator_rounding_points, sum(included.map(item => item.rounding!)) / included.length)
    || !close(stages.aggregate_rounding_points, score! - mean)
    || !close(stages.mean_indicator_rounding_points + stages.aggregate_rounding_points, overall.rounding!)) return invalid;
  for (const component of overall.components) {
    const sources = included.map(item => item.components.find(part => part.key === component.key)!);
    const active = sources.filter(item => item.included);
    const rawComponent = (raw.components as unknown[]).map(record).find(item => item?.key === component.key)!;
    if (component.included !== Boolean(active.length) || rawComponent.included_indicator_count !== active.length || rawComponent.total_indicator_count !== included.length
      || rawComponent.value_percent_aggregation !== "earned_points_divided_by_allocated_max_points") return invalid;
    if (component.included && (!close(component.maximum!, sum(sources.map(item => item.maximum ?? 0)) / included.length)
      || !close(component.earned!, sum(sources.map(item => item.earned ?? 0)) / included.length)
      || !close(component.deduction!, sum(sources.map(item => item.deduction ?? 0)) / included.length))) return invalid;
  }
  return { status: "verified", score, explanation: { status: "available", score, raw: overall.rawScore, rounding: overall.rounding,
    indicatorRounding: stages.mean_indicator_rounding_points, aggregateRounding: stages.aggregate_rounding_points,
    components: overall.components, indicators, includedIds: raw.included_indicator_ids, excludedIds: raw.excluded_indicator_ids } };
}

/** A representative measurement is not a timestamp. Only actual measured event windows become replay links. */
export function scoreEvidenceWindows(result: DemoResultResponse | null, indicatorId: string): ScoreEvidenceWindow[] {
  if (!result || !id(indicatorId)) return [];
  const duration = reportVideoDurationMs(result);
  return footworkEpisodes(result.footwork_review).filter(episode => (duration === null || episode.end_ms <= duration)
    && episode.indicators.some(indicator => indicator.indicator_id === indicatorId && indicator.measurements?.some(measurement => measurement.status === "measured")))
    .slice(0, 3).map(episode => ({ id: episode.event_id, startMs: episode.start_ms, endMs: episode.end_ms }));
}

export function componentImpact(indicator: IndicatorExplanation, key: ScoreComponentKey, includedCount: number): number | null {
  const component = indicator.components.find(item => item.key === key);
  return indicator.included && component?.included && Number.isSafeInteger(includedCount) && includedCount > 0 ? component.deduction! / includedCount : null;
}

export type IndicatorWithExplanation = IndicatorEvaluation & { score_explanation?: unknown };
