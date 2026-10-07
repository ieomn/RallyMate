import type { FootworkReview } from "./api-types";

/** Old reports may lack event boundaries; never infer them from frame counts. */
export function footworkEpisodes(value?: unknown): FootworkReview["episodes"] {
  const review = value as FootworkReview | undefined;
  if (review?.schema_version !== "1.0.0" || review.status !== "available" || !Array.isArray(review.episodes)) return [];
  const ids = new Set<string>();
  return review.episodes.filter(episode => {
    if (!episode || typeof episode.event_id !== "string" || !episode.event_id || ids.has(episode.event_id) || typeof episode.name_zh !== "string" || !["FS01", "FS02", "FS09"].includes(episode.event_code)) return false;
    if (!Number.isFinite(episode.start_ms) || !Number.isFinite(episode.end_ms) || episode.start_ms < 0 || episode.end_ms <= episode.start_ms || !Array.isArray(episode.indicators)) return false;
    ids.add(episode.event_id);
    return true;
  }).map(episode => ({ ...episode, indicators: episode.indicators.filter(indicator => indicator && typeof indicator.indicator_id === "string" && indicator.indicator_id.startsWith(`${episode.event_code}-M`) && typeof indicator.name_zh === "string").map(indicator => ({
    ...indicator,
    scoring_status: ["scored", "calibration_required", "unavailable"].includes(indicator.scoring_status) ? indicator.scoring_status : "unavailable",
    features: indicator.feature_status === "measured" && Array.isArray(indicator.features) ? indicator.features.filter(feature => feature && typeof feature.feature_name === "string" && typeof feature.value === "number" && Number.isFinite(feature.value) && typeof feature.confidence === "number" && Number.isFinite(feature.confidence) && feature.confidence >= 0 && feature.confidence <= 1 && typeof feature.unit === "string") : [],
  })) })).sort((a, b) => a.start_ms - b.start_ms);
}

export function footworkFeatureText(value: unknown, unit?: string | null) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  const units: Record<string, string> = { body: "身体尺度", "body/s": "身体尺度/秒", "body/s2": "身体尺度/秒²", deg: "°", "deg/s": "°/秒", ms: "毫秒", ratio: "比例" };
  return `${Number(value.toFixed(3))} ${units[unit ?? ""] ?? unit ?? ""}`.trim();
}

export function footworkScoringStatus(status: string) {
  return status === "calibration_required" ? "技术等级待标定" : status === "scored" ? "另见正式标定记录" : "评分证据不足";
}
