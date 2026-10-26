import type { ProcessDimension, ProcessDimensionId, RelativeReference, RelativeReferenceDimension, RelativeReferenceRule, RelativeScoringGap } from "./api-types";
import policyInput from "../../../src/rallymate_scoring/data/relative_reference_policy.json";

// Service and browser use this same versioned policy. Input measurements must
// already be validated; saved score fields never become scoring authority.
function checkedPolicy(input: typeof policyInput) {
  if (input.version !== "relative-reference-v3.0.0" || input.legacy_policy.neutral_score !== 50 || input.legacy_policy.prior_sample_weight !== 3) throw new Error("Unsupported relative reference policy");
  return { ...input, version: input.version as "relative-reference-v3.0.0" };
}
const policy = checkedPolicy(policyInput);
export const relativeScoringPolicy = policy;
type Rule = typeof policy.rules[number] | typeof policy.excluded_legacy_rules[number];
const rounded = (value: number) => Math.round(value * 10) / 10;
const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
export function relativeRuleScore(rule: Rule, value: number): number {
  return rule.mapping === "inverse_fraction" ? 100 * (1 - (value - rule.min) / (rule.max - rule.min)) : value;
}

function scoreDimensions(rows: ProcessDimension[], rules: Rule[], historical = false): RelativeReferenceDimension[] {
  return policy.dimensions.map(({ id, name_zh }) => {
    const matches = rows.filter(row => row.id === id);
    const dimension = matches.length === 1 ? matches[0] : null;
    const criteria = Array.isArray(dimension?.criteria) ? dimension.status === "unavailable" ? dimension.criteria.filter(row => row.source_conflict) : dimension.criteria : [];
    const ruleScores: RelativeReferenceRule[] = rules.filter(rule => rule.dimension === id).map(rule => {
      const samples = new Map<string, { score: number; model: boolean; start: number; end: number }>();
      const conflicts = new Set<string>();
      for (const row of criteria) {
        if (row?.id === rule.id && row.source_conflict && typeof row.episode_id === "string" && row.episode_id.trim()) { conflicts.add(row.episode_id); continue; }
        if (!row || typeof row !== "object" || row.id !== rule.id || row.status !== "measured" || row.unit !== rule.unit || row.source !== rule.source
          || !finite(row.value) || row.value < rule.min || row.value > rule.max || !finite(row.start_ms)
          || !finite(row.end_ms) || row.start_ms < 0 || row.start_ms >= row.end_ms || typeof row.episode_id !== "string" || !row.episode_id.trim()
          || rule.minimum_samples > 0 && (!Number.isSafeInteger(row.sample_count) || Number(row.sample_count) < rule.minimum_samples)) continue;
        const sample = { score: relativeRuleScore(rule, row.value),
          model: row.measurement_provenance?.observation_semantics === "monocular_model_estimate", start: row.start_ms, end: row.end_ms };
        const previous = samples.get(row.episode_id);
        if (previous && (previous.score !== sample.score || previous.model !== sample.model || previous.start !== sample.start || previous.end !== sample.end)) conflicts.add(row.episode_id);
        else samples.set(row.episode_id, sample);
      }
      for (const episode of conflicts) samples.delete(episode);
      const values = [...samples.values()];
      const n = values.length, raw = n ? values.reduce((sum, row) => sum + row.score, 0) / n : null;
      const weight = historical ? n / (n + policy.legacy_policy.prior_sample_weight) : n ? 1 : 0;
      const score = historical ? raw === null ? policy.legacy_policy.neutral_score : policy.legacy_policy.neutral_score + (raw - policy.legacy_policy.neutral_score) * weight : raw;
      return { id: rule.id, name_zh: rule.name_zh, raw_mean: raw === null ? null : rounded(raw), score: score === null ? null : rounded(score),
        sample_count: n, model_estimate_sample_count: values.filter(row => row.model).length,
        measurement_weight: Math.round(weight * 1e6) / 1e6, formula_zh: rule.formula_zh, source: rule.source,
        excluded_conflicting_episode_count: conflicts.size };
    });
    const active = ruleScores.filter(row => row.sample_count > 0);
    const measured = criteria.filter(row => row?.status === "measured" && typeof row.id === "string" && row.id.trim() && finite(row.value) && finite(row.start_ms) && finite(row.end_ms)
      && row.start_ms >= 0 && row.start_ms < row.end_ms && typeof row.episode_id === "string" && row.episode_id.trim());
    const excluded = policy.excluded_legacy_rules.filter(rule => rule.dimension === id);
    const conflictCount = ruleScores.reduce((sum, row) => sum + row.excluded_conflicting_episode_count, 0);
    const gapType: RelativeScoringGap["type"] = active.length ? "none" : conflictCount ? "source_conflict" : excluded.length ? "selection_biased_proxy_excluded" : id === "movement" ? "independent_reference_missing" : "rule_not_implemented";
    const gapReason = active.length ? "已按独立目标方向规则计分；只覆盖该子项，不代表整维技术质量。"
      : conflictCount ? "同一动作的独立目标评分输入互相冲突，已排除冲突动作；这不是缺少目标，也不代表动作不合格。"
      : excluded.length ? "腕速加减速趋势与阶段选择使用同一曲线，已退出评分；曲线测量和旧版诊断保留，完整技术规则尚未实现。"
      : id === "movement" ? "尚无通过绑定检查的独立目标方向与启动测量组合；已有步伐测量不因此消失，其他移动技术规则尚未实现。"
      : id === "contact" ? "触球姿态与拍面控制的量化规则尚未实现，且未绑定经验证触球时段。"
      : "已支持过程测量，但尚未实现该维度的技术评分规则；不将几何量或时间长短任意映射为优劣。";
    return { id: id as ProcessDimensionId, name_zh, score: active.length ? rounded(active.reduce((sum, row) => sum + row.score!, 0) / active.length) : historical ? policy.legacy_policy.neutral_score : null,
      basis: active.length ? "observed_reference" : historical ? "neutral_prior" : "unavailable", observed_rule_count: active.length,
      implemented_rule_count: ruleScores.length, sample_count: active.reduce((sum, row) => sum + row.sample_count, 0), rules: ruleScores,
      scoring_audit: { measured_criterion_count: new Set(measured.map(row => JSON.stringify([row.id, row.episode_id, row.start_ms, row.end_ms, row.value]))).size, measured_episode_count: new Set(measured.map(row => row.episode_id)).size,
        scored_rule_count: active.length, scored_episode_count: active.reduce((sum, row) => sum + row.sample_count, 0), implemented_rule_count: ruleScores.length,
        excluded_legacy_rule_ids: excluded.map(rule => rule.id), excluded_conflicting_episode_count: conflictCount, count_scope: "validated_returned_criteria" },
      gap: { type: gapType, reason_zh: gapReason, rule_version: policy.version, measurement_status: measured.length ? "measured" : "unavailable" } };
  });
}

export function relativeReferenceOf(input: unknown = []): RelativeReference {
  const rows = Array.isArray(input) ? input.filter((row): row is ProcessDimension => !!row && typeof row === "object" && !Array.isArray(row)) : [];
  const dimensions = scoreDimensions(rows, policy.rules);
  const historical = scoreDimensions(rows, [...policy.rules, ...policy.excluded_legacy_rules], true);
  const previous = scoreDimensions(rows, policy.rules, true);
  const active = dimensions.filter(row => row.score !== null);
  const observed = dimensions.filter(row => row.basis === "observed_reference").length;
  return { version: policy.version, semantics: "observed_measurement_reference",
    overall_score: active.length ? rounded(active.reduce((sum, row) => sum + row.score!, 0) / active.length) : null,
    is_calibrated: false,
    aggregation: "independent_episode_mean_then_rule_mean_then_observed_dimension_mean",
    observed_dimension_count: observed, total_dimension_count: 5, dimensions,
    summary_zh: `${observed}/5 维有当前有效计分规则与输入；缺测维度保持为空，不进入成绩分母。已有测量与计分缺口分别显示，不代表完整五维技术能力。`,
    formula_zh: policy.formula_zh, limitations_zh: [...policy.limitations_zh],
    legacy_diagnostic: { version: "relative-reference-v1.0.0", semantics: "selection_biased_motion_curve_diagnostic", is_technical_quality: false, contributes_to_current_score: false,
      overall_score: rounded(historical.reduce((sum, row) => sum + row.score!, 0) / policy.dimensions.length),
      dimensions: historical.map(({ scoring_audit, gap, ...row }) => { void scoring_audit; void gap; return row; }),
      observed_dimension_count: historical.filter(row => row.basis === "observed_reference").length,
      reason_zh: "按旧版公式重算的历史曲线诊断；其腕速分段存在选择偏倚，不再贡献当前总分。" },
    legacy_relative_reference: { version: "relative-reference-v2.0.0", semantics: "neutral_prior_relative_reference", is_technical_quality: false, contributes_to_current_score: false, neutral_score: 50, prior_sample_weight: 3,
      overall_score: rounded(previous.reduce((sum, row) => sum + row.score!, 0) / policy.dimensions.length),
      dimensions: previous.map(({ scoring_audit, gap, ...row }) => { void scoring_audit; void gap; return row; }),
      observed_dimension_count: previous.filter(row => row.basis === "observed_reference").length,
      aggregation: "independent_episode_mean_then_rule_mean_then_five_equal_dimensions", formula_zh: policy.legacy_policy.formula_zh,
      reason_zh: "按旧 v2 规则从测量重算的历史参考；缺项填 50 与样本数先验均已退出当前成绩。" },
    recalculation: { from_version: "relative-reference-v2.0.0", to_version: policy.version, source: "validated_process_measurements", cached_scores_used: false,
      reason_zh: "历史报告按当前规则从原始过程测量重算；旧 v1 曲线诊断和旧 v2 参考独立保留，不参与当前成绩，不改写原始测量。" } };
}
