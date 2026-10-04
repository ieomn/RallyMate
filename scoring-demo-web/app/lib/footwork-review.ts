import type { FootworkReview, IndependentFootworkMeasurement } from "./api-types";

const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const strings = (value: unknown): string[] => Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
export function independentFootworkMeasurements(value: unknown, startMs: number, endMs: number): IndependentFootworkMeasurement[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const names = new Set<string>();
  return value.filter(item => item && typeof item.feature_name === "string" && !names.has(item.feature_name) && (names.add(item.feature_name), true)).map(item => {
    const window = item.window;
    const measured = item.status === "measured" && finite(item.value) && finite(item.confidence) && item.confidence >= 0 && item.confidence <= 1
      && typeof item.unit === "string" && ["image_plane_proxy", "independent_target_context"].includes(item.view_semantics)
      && window?.scope === "event_interval" && window.start_ms === startMs && window.end_ms === endMs;
    return { feature_name: item.feature_name, name_zh: typeof item.name_zh === "string" ? item.name_zh : item.feature_name,
      status: measured ? "measured" : "unavailable", value: measured ? item.value : null, unit: typeof item.unit === "string" ? item.unit : null, confidence: measured ? item.confidence : null,
      reason_codes: strings(item.reason_codes), reason_zh: typeof item.reason_zh === "string" ? item.reason_zh : "测量缺失或窗口不可核验。", review_hint_zh: typeof item.review_hint_zh === "string" ? item.review_hint_zh : "",
      window: { start_ms: startMs, end_ms: endMs, scope: "event_interval" }, feature_version: typeof item.feature_version === "string" ? item.feature_version : null, required_joints: strings(item.required_joints), view_semantics: item.view_semantics === "independent_target_context" ? "independent_target_context" : "image_plane_proxy",
    };
  });
}

/** Old reports may lack event boundaries; never infer them from frame counts. */
export function footworkEpisodes(value?: unknown): FootworkReview["episodes"] {
  const review = value as FootworkReview | undefined;
  if (!["1.0.0", "1.1.0"].includes(review?.schema_version ?? "") || review?.status !== "available" || !Array.isArray(review.episodes)) return [];
  const ids = new Set<string>();
  return review.episodes.filter(episode => {
    if (!episode || typeof episode.event_id !== "string" || !episode.event_id || ids.has(episode.event_id) || typeof episode.name_zh !== "string" || !["FS01", "FS02", "FS09"].includes(episode.event_code)) return false;
    if (!Number.isFinite(episode.start_ms) || !Number.isFinite(episode.end_ms) || episode.start_ms < 0 || episode.end_ms <= episode.start_ms || !Array.isArray(episode.indicators)) return false;
    ids.add(episode.event_id);
    return true;
  }).map(episode => ({ ...episode, indicators: episode.indicators.filter(indicator => indicator && typeof indicator.indicator_id === "string" && indicator.indicator_id.startsWith(`${episode.event_code}-M`) && typeof indicator.name_zh === "string").map(indicator => ({
    ...indicator,
    measurements: independentFootworkMeasurements(indicator.measurements, episode.start_ms, episode.end_ms),
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
