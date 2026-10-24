/** Preserve manual provenance, but never turn a supplied verification flag into a score. */
import fsContract from "./fs-card-window-display-contract";
import stepContract from "./fs-step-motion-display-contract";
import { validateFsStepMotion } from "./fs-step-motion-evidence";
import { isAdvisoryQualityFlag, isFormalScoringPolicy, validDerivedSelectionLedger } from "./formal-card-scoring-policy";

export type EvidenceObject = Record<string, unknown>;
export interface RacketSideEvidence { observation: EvidenceObject; window_binding: EvidenceObject; window: EvidenceObject; action_id: string }
export const objectOf = (value: unknown): EvidenceObject => value && typeof value === "object" && !Array.isArray(value) ? value as EvidenceObject : {};
const rows = (value: unknown): unknown[] => Array.isArray(value) ? value : [];
const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const integer = (value: unknown): value is number => Number.isSafeInteger(value) && Number(value) >= 0;
const nonempty = (value: unknown): value is string => typeof value === "string" && value.length > 0;
const sha = (value: unknown) => typeof value === "string" && /^[a-f0-9]{64}$/i.test(value);
const close = (a: unknown, b: unknown) => finite(a) && finite(b) && Math.abs(a - b) <= .51;
const canonical = (value: unknown): string => JSON.stringify(Array.isArray(value) ? value.map(v => JSON.parse(canonical(v))) : value && typeof value === "object" ? Object.fromEntries(Object.entries(value).sort(([a], [b]) => a.localeCompare(b)).map(([key, v]) => [key, JSON.parse(canonical(v))])) : value ?? null);
const same = (a: unknown, b: unknown) => canonical(a) === canonical(b);

/** Copies complete bounded JSON evidence, including unknown fields for backups.
 * Oversized/malformed evidence is rejected as a whole, never silently trimmed. */
export function evidenceObject(value: unknown): EvidenceObject | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  let budget = 200000;
  const copy = (entry: unknown, depth: number): unknown => {
    if (--budget < 0 || depth > 14) throw new Error("evidence_limit");
    if (entry === null || typeof entry === "boolean" || finite(entry)) return entry;
    if (typeof entry === "string" && entry.length <= 20000) return entry;
    if (Array.isArray(entry) && entry.length <= 20000) return entry.map(item => copy(item, depth + 1));
    if (entry && typeof entry === "object" && Object.keys(entry).length <= 150)
      return Object.fromEntries(Object.entries(entry).map(([key, item]) => [key, copy(item, depth + 1)]));
    throw new Error("invalid_evidence");
  };
  try { return copy(value, 0) as EvidenceObject; } catch { return null; }
}
export function racketSideOf(row: EvidenceObject): RacketSideEvidence | null {
  const saved = objectOf(row.racket_side_evidence), window = objectOf(row.window), annotation = objectOf(row.source_annotation);
  const observation = saved.observation ?? annotation.racket_side_observation, binding = saved.window_binding ?? window.racket_side_binding;
  if (observation === undefined && binding === undefined) return null;
  return { observation: evidenceObject(observation) ?? {}, window_binding: evidenceObject(binding) ?? {},
    window: evidenceObject(saved.window ?? window) ?? {}, action_id: typeof (saved.action_id ?? row.action_id) === "string" ? String(saved.action_id ?? row.action_id) : "" };
}

type InstanceContext = { indicator_id: string; event_id: string; video_id: string; person_track_id: number;
  window: { start_ms: number; end_ms: number } | null; quality: { total_frame_count: number; required_joint_names: string[] };
  conditions?: { metric: string; value: number | null }[]; scoring_policy_version?: string | null };
type Stage = { document_sha256: string; annotation_id: string; stage_code: string } | null;

function ptsTime(row: EvidenceObject): number | null {
  if (!integer(row.frame_index) || !integer(row.pts) || !integer(row.time_base_num) || !integer(row.time_base_den)
      || row.time_base_num === 0 || row.time_base_den === 0) return null;
  const value = row.pts * row.time_base_num * 1000 / row.time_base_den;
  return Number.isFinite(value) ? value : null;
}
function validWindow(value: EvidenceObject): boolean { return finite(value.start_ms) && finite(value.end_ms) && value.start_ms >= 0 && value.end_ms > value.start_ms; }

/** A valid sampled wrist observation is distinct from an observation applicable
 * to a continuous stage. The latter also requires backend stage continuity. */
export function validRacketSide(proof: RacketSideEvidence | null, context: InstanceContext, stage: Stage, requireApplicable = false): boolean {
  if (!proof) return !requireApplicable;
  const p = proof.observation, w = proof.window, action = objectOf(p.action_window_ms);
  const source = rows(p.source_frames), bindings = rows(p.source_frame_bindings);
  if (!same(p, proof.window_binding) || !stage || !context.window || !validWindow(action)
      || p.version !== "manual-racket-side-observation-v1.0.0" || p.method !== "manual_visual_observation"
      || !nonempty(proof.action_id) || p.action_id !== proof.action_id || p.technique_code !== context.indicator_id.split("-")[0]
      || p.document_version !== "gs-stage-annotations-v1.0.0" || p.document_sha256 !== stage.document_sha256 || !sha(p.document_sha256)
      || p.digest_kind !== "canonical_json_sorted_utf8_v1" || p.verification !== "source_frames_matched_to_decoder_and_selected_person"
      || p.evidence_scope !== "sampled_frames_only" || p.expert_adjudicated !== false || p.dominant_hand_established !== false || p.continuous_racket_hold_established !== false
      || p.semantics !== "visually_observed_racket_holding_wrist_not_dominant_hand" || !nonempty(p.source) || !nonempty(p.source_description)
      || !finite(p.uncertainty_ms) || p.uncertainty_ms < 0 || !["left", "right"].includes(String(p.measurement_wrist_side))
      || !["left", "right", "both"].includes(String(p.observed_racket_hands))
      || p.technique_code === "GS02" && p.observed_racket_hands !== "both"
      || p.observed_racket_hands !== "both" && p.observed_racket_hands !== p.measurement_wrist_side
      || !sha(p.video_sha256) || p.video_sha256 !== w.video_sha256 || p.person_track_id !== context.person_track_id
      || w.person_track_id !== context.person_track_id || w.video_id !== context.video_id || w.event_id !== context.event_id
      || w.event_code !== p.technique_code || w.stage_code !== stage.stage_code || stage.annotation_id !== context.event_id
      || !close(w.start_ms, context.window.start_ms) || !close(w.end_ms, context.window.end_ms)
      || context.window.start_ms < Number(action.start_ms) - .51 || context.window.end_ms > Number(action.end_ms) + .51
      || !source.length || source.length > 2000 || source.length !== bindings.length
      || typeof p.stage_continuity_verified !== "boolean") return false;
  let previous = -1, previousTime = -1, epochsAgree = integer(w.selection_epoch) && integer(w.camera_reference_epoch);
  for (let i = 0; i < source.length; i++) {
    const s = objectOf(source[i]), b = objectOf(bindings[i]), time = ptsTime(s);
    if (time === null || Number(s.frame_index) <= previous || time <= previousTime || time < Number(action.start_ms) - .51 || time > Number(action.end_ms) + .51
        || ["frame_index", "pts", "time_base_num", "time_base_den"].some(key => s[key] !== b[key])
        || b.video_sha256 !== p.video_sha256 || b.person_track_id !== context.person_track_id || !close(b.timestamp_ms, time)
        || !integer(b.selection_epoch) || !integer(b.camera_reference_epoch)) return false;
    previous = Number(s.frame_index); previousTime = time;
    epochsAgree = epochsAgree && b.selection_epoch === w.selection_epoch && b.camera_reference_epoch === w.camera_reference_epoch;
  }
  // False can also mean a missing/interrupted stage inventory not contained in
  // the sampled proof. Do not promote it using matching endpoint epochs.
  if (p.stage_continuity_verified && !epochsAgree) return false;
  if (p.stage_continuity_verified ? w.racket_side !== p.measurement_wrist_side : w.racket_side !== undefined) return false;
  return !requireApplicable || p.stage_continuity_verified === true;
}

export function validFormalGsStageSource(evidence: EvidenceObject | null, w: EvidenceObject | null, context: InstanceContext): boolean {
  if (!evidence || !w || !context.window || !isFormalScoringPolicy(context.scoring_policy_version)) return false;
  const frames = rows(evidence.source_frames), pts = rows(evidence.source_pts).map(objectOf), selected = rows(evidence.source_frame_bindings).map(objectOf);
  if (frames.length < 3 || frames.length !== context.quality.total_frame_count || frames.length !== pts.length || frames.length !== selected.length
      || evidence.interpolation_used !== false || !validDerivedSelectionLedger(selected, true, w.derived_selection_epoch)
      || !sha(w.video_sha256) || w.video_id !== context.video_id || w.event_id !== context.event_id || w.person_track_id !== context.person_track_id
      || w.event_code !== context.indicator_id.split("-")[0] || w.stage_code !== context.indicator_id.split("-").slice(0, 2).join("-")
      || !integer(w.selection_epoch) || !integer(w.camera_reference_epoch) || w.camera_compensated !== true
      || !close(w.start_ms, context.window.start_ms) || !close(w.end_ms, context.window.end_ms)) return false;
  const times: number[] = [], modelEpochs = new Set<number>();
  for (let i = 0; i < frames.length; i++) {
    const p = pts[i], b = selected[i], time = ptsTime(p);
    if (!integer(frames[i]) || i > 0 && Number(frames[i]) !== Number(frames[i - 1]) + 1 || p.frame_index !== frames[i] || time === null
        || ["frame_index", "pts", "time_base_num", "time_base_den"].some(k => p[k] !== b[k]) || b.video_sha256 !== w.video_sha256
        || b.selected_person_track_id !== context.person_track_id || b.selection_epoch !== w.selection_epoch || b.camera_reference_epoch !== w.camera_reference_epoch
        || !close(b.timestamp_ms, time) || i > 0 && (time <= times[i - 1] || p.time_base_num !== pts[0].time_base_num || p.time_base_den !== pts[0].time_base_den)) return false;
    if (b.model_epoch_semantics !== undefined || b.source_model_selection_epoch !== undefined) {
      if (b.model_epoch_semantics !== "detector_track_epoch_primary_selection_applied_downstream" || !integer(b.source_model_selection_epoch)) return false;
      modelEpochs.add(b.source_model_selection_epoch);
    }
    times.push(time);
  }
  return modelEpochs.size <= 1 && close(times[0], context.window.start_ms) && close(times.at(-1), context.window.end_ms);
}

export function validFsWindow(annotation: EvidenceObject | null, evidence: EvidenceObject | null, w: EvidenceObject | null, context: InstanceContext): boolean {
  if (!annotation || !evidence || !w || !context.window) return false;
  const a = annotation, binding = objectOf(a.binding), requested = objectOf(a.requested_window_ms);
  const formal = isFormalScoringPolicy(context.scoring_policy_version);
  const frames = rows(evidence.source_frames), pts = rows(evidence.source_pts), selected = rows(evidence.source_frame_bindings);
  if (!validDerivedSelectionLedger(selected, formal, w.derived_selection_epoch)) return false;
  const motionRequired = context.indicator_id in stepContract.cards;
  const motion = motionRequired ? validateFsStepMotion(annotation, evidence, w, context) : null;
  if (motionRequired && (motion?.status !== "measured_visual_motion" || !motion.condition_values_match)) return false;
  if (a.document_version !== fsContract.annotation_version || !sha(a.document_sha256) || a.digest_kind !== "canonical_json_sorted_utf8_v1"
      || a.annotation_id !== context.event_id || a.indicator_id !== context.indicator_id || a.person_track_id !== context.person_track_id
      || !integer(a.start_source_frame) || !integer(a.end_source_frame) || a.start_source_frame >= a.end_source_frame
      || !sha(a.video_sha256) || w.video_sha256 !== a.video_sha256 || w.video_id !== context.video_id || w.event_id !== context.event_id
      || w.person_track_id !== context.person_track_id || w.event_code !== context.indicator_id.split("-")[0] || w.stage_code !== context.indicator_id
      || !integer(w.selection_epoch) || !integer(w.camera_reference_epoch) || w.camera_compensated !== true
      || !validWindow(requested) || !close(w.start_ms, context.window.start_ms) || !close(w.end_ms, context.window.end_ms)
      || !same(binding, w.stage_binding) || binding.method !== "manual_anchor" || !nonempty(binding.version) || !nonempty(binding.source)
      || binding.semantics !== fsContract.annotation_semantics || !finite(binding.uncertainty_ms) || binding.uncertainty_ms < 0
      || !frames.length || frames.length !== a.end_source_frame - a.start_source_frame + 1 || frames.length !== context.quality.total_frame_count
      || frames.length !== pts.length || frames.length !== selected.length || evidence.interpolation_used !== false
      || evidence.step_cycle_bound !== motionRequired || evidence.source_behavior_or_contact_verified !== false || evidence.quality_observations_present !== true) return false;
  const times: number[] = [];
  for (let i = 0; i < frames.length; i++) {
    const s = objectOf(pts[i]), b = objectOf(selected[i]), time = ptsTime(s);
    if (frames[i] !== a.start_source_frame + i || s.frame_index !== frames[i] || time === null || s.video_sha256 !== a.video_sha256
        || ["frame_index", "pts", "time_base_num", "time_base_den", "video_sha256"].some(key => s[key] !== b[key])
        || b.selected_person_track_id !== context.person_track_id || b.selection_epoch !== w.selection_epoch || b.camera_reference_epoch !== w.camera_reference_epoch
        || !close(b.timestamp_ms, time) || i > 0 && (time <= times[i - 1] || s.time_base_num !== objectOf(pts[0]).time_base_num || s.time_base_den !== objectOf(pts[0]).time_base_den)) return false;
    times.push(time);
  }
  if (!close(times[0], requested.start_ms) || !close(times.at(-1), requested.end_ms)
      || !close(times[0], w.start_ms) || !close(times.at(-1), w.end_ms)) return false;
  const gaps = times.slice(1).map((time, i) => time - times[i]).sort((a, b) => a - b);
  const median = (gaps[Math.floor((gaps.length - 1) / 2)] + gaps[Math.floor(gaps.length / 2)]) / 2;
  if (times.at(-1)! - times[0] < 80 || gaps.some(gap => gap > Math.min(160, median * 1.8))) return false;
  const quality = rows(evidence.original_event_quality).map(objectOf);
  const covered = new Set<number>(), identities = new Set<string>();
  for (const q of quality) {
    const tracks = rows(q.source_track_ids), flags = rows(q.raw_input_quality_flags), diagnostics = objectOf(q.source_track_diagnostics);
    if (!nonempty(q.source_event_id) || identities.has(q.source_event_id) || tracks.length !== 1 || !integer(tracks[0])
        || !finite(q.source_start_ms) || !finite(q.source_end_ms) || q.source_start_ms > q.source_end_ms
        || (formal ? typeof q.global_identity_warning !== "boolean" : q.global_identity_warning !== false || q.primary_identity_ambiguous === true
          || finite(q.source_track_switch_candidate_count) && q.source_track_switch_candidate_count > 0)
        || finite(q.confirmed_id_switch_count) && q.confirmed_id_switch_count > 0
        || q.confirmed_id_switch_count != null && (!integer(q.confirmed_id_switch_count) || q.confirmed_id_switch_count !== 0)
        || !Array.isArray(q.raw_input_quality_flags) || flags.some(flag => typeof flag !== "string"
          || /identity_ambiguous|id_switch|track_switch/.test(flag) && !(formal && isAdvisoryQualityFlag(flag)))) return false;
    if (["source_track_ids", "primary_identity_ambiguous", "source_track_switch_candidate_count", "confirmed_id_switch_count"].some(key => !same(q[key], diagnostics[key]))) return false;
    identities.add(q.source_event_id);
    const applies = tracks[0] === context.person_track_id;
    if (q.applies_to_selected_person !== applies) return false;
    if (!applies) continue;
    if ((formal ? typeof q.primary_identity_ambiguous !== "boolean" || !integer(q.source_track_switch_candidate_count)
          : q.primary_identity_ambiguous !== false || q.source_track_switch_candidate_count !== 0)
        || q.policy_version !== fsContract.quality_policy_version || !Array.isArray(q.review_flags)
        || rows(q.review_flags).some(flag => !formal || !isAdvisoryQualityFlag(flag))) return false;
    if (formal && (q.quality_policy_version !== fsContract.advisory_quality_policy_version
        || !Array.isArray(q.blocking_flags) || q.blocking_flags.length || !Array.isArray(q.advisory_flags)
        || q.advisory_flags.some(flag => !isAdvisoryQualityFlag(flag))
        || !same(q.event_identity_diagnostics, diagnostics))) return false;
    const ignored: string[] = [];
    for (const [flag, field] of [["keypoint_jump_candidates_present", "keypoint_jump_candidate_joints"], ["left_right_swap_candidates_present", "left_right_swap_candidate_joint_pairs"]]) {
      let joints: unknown[] = rows(diagnostics[field]);
      if (field.endsWith("pairs")) joints = joints.flatMap(value => { const pair = objectOf(value); return [pair.left_joint, pair.right_joint]; });
      if (flags.includes(flag) && joints.length && joints.every(joint => typeof joint === "string"
          && (fsContract.known_joint_names as readonly string[]).includes(joint) && !context.quality.required_joint_names.includes(joint))) ignored.push(flag);
    }
    if (!same([...rows(q.irrelevant_joint_flags)].sort(), ignored.sort()) || flags.some(flag => !ignored.includes(String(flag))
        && ((fsContract.quality_review_flags as readonly unknown[]).includes(flag) || /confirmed_id_switch|id_switch_confirmed|censored/.test(String(flag)) || formal && String(flag).startsWith("required_phase_missing:"))
        && !(formal && isAdvisoryQualityFlag(flag)))) return false;
    // The producer's event boundaries use validated source-record milliseconds
    // (which may be rounded), not the exact fractional PTS clock. Each binding
    // timestamp was already checked against its PTS above; use the same clock
    // as the producer so a .3 ms endpoint does not erase legitimate coverage.
    selected.forEach((row, index) => { const time = Number(objectOf(row).timestamp_ms);
      if (Number(q.source_start_ms) <= time && time <= Number(q.source_end_ms)) covered.add(index); });
  }
  return covered.size === times.length;
}

/** Human-readable provenance for portable report tables; full raw bindings
 * stay in the JSON backup through the normalised CardInstance. */
export function manualCardSourceLabel(instance: InstanceContext & { source_window_annotation: EvidenceObject | null;
  racket_side_evidence: RacketSideEvidence | null; source_stage: Stage }): string {
  if (instance.source_window_annotation) {
    const binding = objectOf(instance.source_window_annotation.binding);
    return `人工逐卡窗口；非专家技术、触地或受力真值；边界不确定性 ±${finite(binding.uncertainty_ms) ? binding.uncertainty_ms : "未核验"} 毫秒；${typeof binding.source === "string" ? binding.source : "来源未核验"}；${typeof binding.version === "string" ? binding.version : "版本未核验"}`;
  }
  if (instance.racket_side_evidence) {
    const p = instance.racket_side_evidence.observation, valid = validRacketSide(instance.racket_side_evidence, instance, instance.source_stage);
    return `测量腕 ${valid ? p.measurement_wrist_side === "left" ? "左腕" : "右腕" : "未核验"}；持拍手采样观察 ${valid ? p.observed_racket_hands === "both" ? "双手" : p.observed_racket_hands === "left" ? "左手" : "右手" : "未确认"}；非优势手判定、非持续持拍证明；±${finite(p.uncertainty_ms) ? p.uncertainty_ms : "未核验"} 毫秒；${typeof p.source === "string" ? p.source : "来源未核验"}`;
  }
  return "";
}
