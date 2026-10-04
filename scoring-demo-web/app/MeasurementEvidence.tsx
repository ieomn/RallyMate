import type { IndicatorEvaluation } from "./lib/api-types";

const labels: Record<string, string> = { measured_instance_ratio_percent: "可测片段", required_feature_coverage_percent: "必需特征覆盖", median_feature_confidence_percent: "特征置信度", repeatability_percent: "跨片段重复性", scoring_evidence_ratio_percent: "评分证据覆盖" };

export default function MeasurementEvidence({ indicator }: { indicator: IndicatorEvaluation }) {
  return <details className="inspector-section measurement-evidence">
    <summary className="section-label">查看实际测量与评分依据</summary>
    {indicator.representative_measurements?.filter(item => Number.isFinite(item.median_value)).map(item => <p key={item.feature_name}><strong>{item.label_zh}：{item.median_value} {item.unit_zh}</strong><br /><small>{item.sample_count} 个可测片段{item.typical_range?.every(Number.isFinite) ? ` · 中间 50% 范围 ${item.typical_range[0]}–${item.typical_range[1]} ${item.unit_zh}` : ""}</small></p>)}
    {indicator.components && <dl>{Object.entries(labels).map(([key, label]) => { const value = indicator.components?.[key]; return <div key={key}><dt>{label}</dt><dd>{typeof value === "number" && Number.isFinite(value) ? `${value}%` : "证据不足"}</dd></div>; })}</dl>}
    <p className="muted-small">技术评分：待教练标定。参考分综合测量覆盖、置信度与重复性；重复性高不代表动作正确。</p>
  </details>;
}
