import type { DemoResultResponse } from "./api-types";
import { footworkEpisodes } from "./footwork-review";
import { measurementIndicators } from "./measurement-evidence";
import { reportVideoDurationMs } from "./workspace-navigation";

export type RuleSource = { document_id: string; location: string; cell?: string };
export type ReviewRule = {
  id: string; event_code: string; event_name: string; stage: string; name: string;
  definition: string; calculation: string; required_points: string; unevaluable: string;
  grades: Array<{ grade: string; definition: string }>;
  sources: RuleSource[];
  source_issues?: string[];
  implementation: { kind: "related_2d_measurement" | "not_implemented"; features: string[]; note: string };
};
export type RuleReviewCatalog = {
  version: string;
  source_documents: Array<{ id: string; name: string; sha256: string }>;
  rules: ReviewRule[];
  findings: Array<{ title: string; detail: string }>;
  visual_techniques?: Array<{ id: string; name: string; phases: Array<{ id: string; name: string; optional: boolean; clauses: Array<{ text: string; source: RuleSource }> }> }>;
};

export function ruleReviewContext(result: DemoResultResponse | null) {
  const indicators = new Map(measurementIndicators(result).map(item => [item.indicator_id, item]));
  const duration = reportVideoDurationMs(result);
  const windows = footworkEpisodes(result?.footwork_review).filter(episode => duration === null || episode.end_ms <= duration).flatMap(episode => episode.indicators.map(item => {
    const measurements = item.measurements ?? [];
    return { indicatorId: item.indicator_id, eventId: episode.event_id, startMs: episode.start_ms, endMs: episode.end_ms, measurements,
      measuredCount: measurements.filter(value => value.status === "measured").length };
  }));
  return { indicators, windows };
}

/** An indicator ID link is a source association, never proof of a passed rubric. */
export function reviewRuleResult(rule: ReviewRule, result: DemoResultResponse | null, context = ruleReviewContext(result)) {
  const indicator = context.indicators.get(rule.id);
  const windows = context.windows.filter(window => window.indicatorId === rule.id);
  const hasEvidence = windows.some(window => window.measuredCount > 0)
    || !!indicator?.representative_measurements?.some(value => Number.isFinite(value.median_value));
  const implemented = rule.implementation.kind === "related_2d_measurement";
  const conflict = !!rule.source_issues?.length;
  return {
    indicator, windows,
    status: conflict ? "source_needs_review" : !implemented ? "not_implemented" : hasEvidence ? "related_measurements" : "unavailable",
    statusLabel: conflict ? "原文待核实" : !implemented ? "尚未接入逐项测量" : hasEvidence ? "有相关二维测量" : "本次证据不足",
    reason: conflict ? "该项原文存在待澄清内容，保留原文供核对；在确认前不能据此给出技术评级。" : !implemented ? "当前分析没有这项原文指标的独立计算与判级结果，不能判为未完成或 E 级。"
      : hasEvidence ? "已保存相关数值，但这些二维测量尚不能完整判定原文中的幅度、时序和技术合理性。"
      : "本次没有取得这项指标所需的可靠测量。缺少证据不会记作 0 分或 E 级。",
  };
}
