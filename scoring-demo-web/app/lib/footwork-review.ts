import type { FootworkReview, IndependentFootworkMeasurement } from "./api-types";
import { hasModelMeasurementContext, modelMeasurementEvidenceOf, modelEvidenceWithin, modelGeometryCoverage, type ModelMeasurementEvidence } from "./model-measurement";
import { freezeReportTransport, isImmutableReportTransport } from "./report-transport-state";
const episodeCache = new WeakMap<object, FootworkReview["episodes"]>();

const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const strings = (value: unknown): string[] => Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
export function independentFootworkMeasurements(value: unknown, startMs: number, endMs: number, parent?: ModelMeasurementEvidence | null, modelEpisode = false): IndependentFootworkMeasurement[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const names = new Set<string>();
  return value.filter(item => item && typeof item.feature_name === "string" && !names.has(item.feature_name) && (names.add(item.feature_name), true)).map(item => {
    const window = item.window;
    const model = modelEpisode || hasModelMeasurementContext(item);
    const evidence = model ? modelMeasurementEvidenceOf(item) : null;
    const required = strings(item.required_joints);
    const sourceFrames = Array.isArray(item.source_frames) && item.source_frames.every((frame: unknown) => Number.isSafeInteger(frame) && Number(frame) >= 0)
      ? [...new Set(item.source_frames as number[])].sort((a, b) => a-b) : [];
    const measured = item.status === "measured" && finite(item.value)
      && (model ? item.confidence === null && evidence !== null && parent != null && modelEvidenceWithin(evidence, parent) && modelGeometryCoverage(evidence, required) >= .5
        && sourceFrames.length > 0 && sourceFrames.every(frame => (evidence.source_window.source_frames as number[]).includes(frame))
        : finite(item.confidence) && item.confidence >= 0 && item.confidence <= 1)
      && typeof item.unit === "string" && ["image_plane_proxy", "independent_target_context"].includes(item.view_semantics)
      && window?.scope === "event_interval" && window.start_ms === startMs && window.end_ms === endMs;
    return { feature_name: item.feature_name, name_zh: typeof item.name_zh === "string" ? item.name_zh : item.feature_name,
      status: measured ? "measured" : "unavailable", value: measured ? item.value : null, unit: typeof item.unit === "string" ? item.unit : null, confidence: measured ? item.confidence : null,
      reason_codes: strings(item.reason_codes), reason_zh: typeof item.reason_zh === "string" ? item.reason_zh : "测量缺失或窗口不可核验。", review_hint_zh: typeof item.review_hint_zh === "string" ? item.review_hint_zh : "",
      window: { start_ms: startMs, end_ms: endMs, scope: "event_interval" }, feature_version: typeof item.feature_version === "string" ? item.feature_version : null, required_joints: strings(item.required_joints), view_semantics: item.view_semantics === "independent_target_context" ? "independent_target_context" : "image_plane_proxy",
      ...(measured && evidence ? evidence : {}),
      ...(sourceFrames.length ? { source_frames: sourceFrames } : {}),
    };
  });
}

/** Old reports may lack event boundaries; never infer them from frame counts. */
export function footworkEpisodes(value?: unknown): FootworkReview["episodes"] {
  const cacheable = isImmutableReportTransport(value);
  if (cacheable) { const cached = episodeCache.get(value); if (cached) return cached; }
  const review = value as FootworkReview | undefined;
  if (!["1.0.0", "1.1.0"].includes(review?.schema_version ?? "") || review?.status !== "available" || !Array.isArray(review.episodes)) return [];
  const ids = new Set<string>();
  const result = review.episodes.filter(episode => {
    if (!episode || typeof episode.event_id !== "string" || !episode.event_id || ids.has(episode.event_id) || typeof episode.name_zh !== "string" || !["FS01", "FS02", "FS09"].includes(episode.event_code)) return false;
    if (!Number.isFinite(episode.start_ms) || !Number.isFinite(episode.end_ms) || episode.start_ms < 0 || episode.end_ms <= episode.start_ms || !Array.isArray(episode.indicators)) return false;
    ids.add(episode.event_id);
    return true;
  }).map(episode => {
    const raw = episode as unknown as Record<string, unknown>;
    const model = hasModelMeasurementContext(raw);
    const diagnostics = raw.track_diagnostics as Record<string, unknown> | undefined;
    const tracks = diagnostics?.source_track_ids;
    const parent = model && Array.isArray(tracks) && tracks.length === 1 &&
      (!diagnostics || !("primary_player_id" in diagnostics) || diagnostics.primary_player_id === episode.person_track_id)
      ? modelMeasurementEvidenceOf({ ...raw, source_track_id: tracks[0] }) : null;
    return { ...episode, indicators: episode.indicators.filter(indicator => indicator && typeof indicator.indicator_id === "string" && indicator.indicator_id.startsWith(`${episode.event_code}-M`) && typeof indicator.name_zh === "string").map(indicator => ({
    ...indicator,
    measurements: independentFootworkMeasurements(indicator.measurements, episode.start_ms, episode.end_ms, parent, model),
    scoring_status: model ? "unavailable" : ["scored", "calibration_required", "unavailable"].includes(indicator.scoring_status) ? indicator.scoring_status : "unavailable",
    features: indicator.feature_status === "measured" && Array.isArray(indicator.features) ? indicator.features.flatMap(feature => {
      if (!feature || typeof feature.feature_name !== "string" || !finite(feature.value) || typeof feature.unit !== "string") return [];
      const item = feature as unknown as Record<string, unknown>;
      const modelFeature = model || hasModelMeasurementContext(item);
      const evidence = modelFeature ? modelMeasurementEvidenceOf(item) : null;
      if (modelFeature ? feature.confidence !== null || evidence === null || parent === null || !modelEvidenceWithin(evidence, parent)
        || modelGeometryCoverage(evidence, strings(item.required_joints)) < .5
        : !finite(feature.confidence) || feature.confidence < 0 || feature.confidence > 1) return [];
      return [{ feature_name: feature.feature_name, name_zh: feature.name_zh, value: feature.value, unit: feature.unit,
        confidence: modelFeature ? null : feature.confidence, ...(evidence ? { ...evidence, required_joints: strings(item.required_joints) } : {}) }];
    }) : [],
  })) };
  }).sort((a, b) => a.start_ms - b.start_ms);
  if (cacheable) { freezeReportTransport(result); episodeCache.set(value, result); }
  return result;
}

export function footworkUnitText(unit?: string | null) {
  const units: Record<string, string> = { body: "身体尺度", "body/s": "身体尺度/秒", "body/s2": "身体尺度/秒²", "frame_long_edge/s": "画面长边/秒", deg: "°", "deg/s": "°/秒", ms: "毫秒", ratio: "比例", px: "像素", torso: "固定躯干尺度" };
  return units[unit ?? ""] ?? unit ?? "";
}

export function footworkFeatureText(value: unknown, unit?: string | null) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  return `${Number(value.toFixed(3))} ${footworkUnitText(unit)}`.trim();
}

export function footworkScoringStatus(status: string) {
  return status === "calibration_required" ? "尚缺可执行计分规则" : status === "scored" ? "已按版本规则计算，见逐项记录" : "评分证据不足";
}
