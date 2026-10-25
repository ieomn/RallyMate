/** Transport proof for model geometry, never a visibility or grading assertion. */
import { isImmutableReportTransport } from "./report-transport-state";
export type ModelMeasurementEvidence = {
  measurement_provenance: Record<string, unknown>; source_window: Record<string, unknown>;
  geometry_mask: Record<string, boolean[]>; source_track_id: number;
};
const object = (value: unknown): Record<string, unknown> => value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const integer = (value: unknown): value is number => Number.isSafeInteger(value) && Number(value) >= 0;
const provenanceFields = { pose_source: "sam3d", observation_semantics: "monocular_model_estimate", assessment_status: "measurement_only",
  scoring_allowed: false, confidence_available: false, visibility_verified: false };
const bindingFields = ["video_sha256", "frame_index", "processed_index", "pts", "time_base_num", "time_base_den",
  "timestamp_ms", "person_track_id", "selection_epoch", "camera_reference_epoch"];
const windowFields = ["start_ms", "end_ms", "source_frames", "video_timestamps_ms", "source_timestamps_ms",
  "person_track_id", "selection_epoch", "camera_reference_epoch", "video_sha256"];
const transportEvidence = new WeakMap<object, WeakMap<object, WeakMap<object, Map<number, ModelMeasurementEvidence>>>>();

/** The codec registers only its validated, deeply frozen tuple. All four keys matter. */
export function registerModelTransportEvidence(raw: Record<string, unknown>): void {
  const track = raw.source_track_id ?? object(raw.source_window).person_track_id;
  if (![raw.source_window, raw.geometry_mask, raw.measurement_provenance].every(isImmutableReportTransport)) return;
  const checked = modelMeasurementEvidenceOf({ ...raw, source_track_id: track });
  if (!checked) return;
  const window = raw.source_window as object, masks = raw.geometry_mask as object, provenance = raw.measurement_provenance as object;
  let byMask = transportEvidence.get(window); if (!byMask) { byMask = new WeakMap(); transportEvidence.set(window, byMask); }
  let byProvenance = byMask.get(masks); if (!byProvenance) { byProvenance = new WeakMap(); byMask.set(masks, byProvenance); }
  let byTrack = byProvenance.get(provenance); if (!byTrack) { byTrack = new Map(); byProvenance.set(provenance, byTrack); }
  byTrack.set(checked.source_track_id, Object.freeze({ measurement_provenance: raw.measurement_provenance as Record<string, unknown>,
    source_window: raw.source_window as Record<string, unknown>, geometry_mask: raw.geometry_mask as Record<string, boolean[]>, source_track_id: checked.source_track_id }));
}

export function hasModelMeasurementContext(raw: Record<string, unknown>): boolean {
  const provenance = object(raw.measurement_provenance);
  return provenance.pose_source === "sam3d" || ["monocular_model_estimate", "model_estimate"].includes(String(provenance.observation_semantics))
    || "source_window" in raw || "geometry_mask" in raw;
}

export function modelMeasurementEvidenceOf(raw: Record<string, unknown>): ModelMeasurementEvidence | null {
  if (isImmutableReportTransport(raw.source_window) && isImmutableReportTransport(raw.geometry_mask) && isImmutableReportTransport(raw.measurement_provenance) && integer(raw.source_track_id)) {
    const cached = transportEvidence.get(raw.source_window)?.get(raw.geometry_mask)?.get(raw.measurement_provenance)?.get(raw.source_track_id);
    if (cached) return cached;
  }
  const provenance = object(raw.measurement_provenance), window = object(raw.source_window);
  if (Object.entries(provenanceFields).some(([key, expected]) => provenance[key] !== expected)) return null;
  const frames = window.source_frames, local = window.video_timestamps_ms, source = window.source_timestamps_ms, bindings = window.source_bindings;
  const identity = ["person_track_id", "selection_epoch", "camera_reference_epoch"];
  if (!integer(raw.source_track_id) || window.person_track_id !== raw.source_track_id
    || identity.some(key => !integer(window[key])) || !integer(window.start_ms) || !integer(window.end_ms) || window.end_ms <= window.start_ms
    || typeof window.video_sha256 !== "string" || !/^[a-f\d]{64}$/i.test(window.video_sha256)
    || !Array.isArray(frames) || frames.length < 2 || frames.length > 20000 || !frames.every(integer)
    || !Array.isArray(local) || local.length !== frames.length || !local.every(integer)
    || !Array.isArray(source) || source.length !== frames.length || !source.every(value => finite(value) && value >= 0)
    || !Array.isArray(bindings) || bindings.length !== frames.length
    || local[0] !== window.start_ms || local.at(-1) !== window.end_ms
    || frames.some((frame, i) => i > 0 && frame !== frames[i - 1] + 1)
    || local.some((time, i) => i > 0 && (time <= local[i - 1] || time-local[i - 1] > 160))
    || source.some((time, i) => i > 0 && (time <= source[i - 1] || time-source[i - 1] > 161.01))
    || source.some((time, i) => Math.abs(time-source[0]-(local[i]-Number(window.start_ms))) > 1.01)) return null;
  let previousProcessed = -1;
  for (let i = 0; i < frames.length; i++) {
    const binding = object(bindings[i]);
    if (binding.video_sha256 !== window.video_sha256 || identity.some(key => !integer(binding[key]) || binding[key] !== window[key])
      || !integer(binding.frame_index) || binding.frame_index !== frames[i] || !integer(binding.processed_index) || i > 0 && binding.processed_index !== previousProcessed + 1
      || i > 0 && ["time_base_num", "time_base_den"].some(key => binding[key] !== object(bindings[0])[key])
      || !integer(binding.pts) || !integer(binding.time_base_num) || binding.time_base_num === 0 || !integer(binding.time_base_den) || binding.time_base_den === 0
      || !finite(binding.timestamp_ms) || Math.abs(binding.timestamp_ms-source[i]) > .51
      || Math.abs(binding.pts*binding.time_base_num/binding.time_base_den*1000-source[i]) > .000001
      || "video_timestamp_ms" in binding && binding.video_timestamp_ms !== local[i]) return null;
    previousProcessed = binding.processed_index;
  }
  const masks = object(raw.geometry_mask), keys = Object.keys(masks);
  if (!keys.length || keys.length > 200 || keys.some(key => !/^[a-z][a-z0-9_]{0,63}$/.test(key)
    || !Array.isArray(masks[key]) || masks[key].length !== frames.length || masks[key].some(value => typeof value !== "boolean"))) return null;
  const cleanWindow = Object.fromEntries(windowFields.map(key => [key, structuredClone(window[key])]));
  cleanWindow.source_bindings = bindings.map(value => { const binding = object(value); return Object.fromEntries(
    [...bindingFields, ...("video_timestamp_ms" in binding ? ["video_timestamp_ms"] : [])].map(key => [key, binding[key]])); });
  return { measurement_provenance: { ...provenanceFields }, source_window: cleanWindow,
    geometry_mask: structuredClone(masks) as Record<string, boolean[]>, source_track_id: raw.source_track_id };
}

export function modelGeometryCoverage(evidence: ModelMeasurementEvidence, required: string[]): number {
  if (!required.length || required.some(name => !Array.isArray(evidence.geometry_mask[name]))) return 0;
  const count = (evidence.source_window.source_frames as number[]).length;
  return Array.from({ length: count }, (_, i) => required.every(name => evidence.geometry_mask[name][i])).filter(Boolean).length / count;
}

export function modelEvidenceWithin(child: ModelMeasurementEvidence, parent: ModelMeasurementEvidence): boolean {
  const a = child.source_window, b = parent.source_window;
  if (["person_track_id", "selection_epoch", "camera_reference_epoch", "video_sha256"].some(key => a[key] !== b[key])
    || Number(a.start_ms) < Number(b.start_ms) || Number(a.end_ms) > Number(b.end_ms)) return false;
  const positions = new Map((b.source_frames as number[]).map((frame, i) => [frame, i]));
  return (a.source_frames as number[]).every((frame, i) => {
    const j = positions.get(frame);
    if (j === undefined || ["video_timestamps_ms", "source_timestamps_ms"].some(key => (a[key] as number[])[i] !== (b[key] as number[])[j])) return false;
    const first = (a.source_bindings as Record<string, unknown>[])[i], second = (b.source_bindings as Record<string, unknown>[])[j];
    return bindingFields.every(key => first[key] === second[key])
      && Object.entries(child.geometry_mask).every(([name, mask]) => !mask[i] || parent.geometry_mask[name]?.[j] === true);
  });
}
