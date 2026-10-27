import type { DemoResultResponse } from "./api-types";

export const TENNIS_DIMENSIONS = [
  { id: "movement", label: "移动支撑" }, { id: "preparation", label: "准备引拍" },
  { id: "coordination", label: "动作协调" }, { id: "striking", label: "击球姿态" },
  { id: "follow_through", label: "随挥恢复" },
] as const;
export type TennisDimensionId = typeof TENNIS_DIMENSIONS[number]["id"];
export type TennisMetric = { id: string; label: string; value: number | null; unit: string; score: number | null; weight: number; observation_count: number; target: string };
export type TennisDimension = { id: TennisDimensionId; label: string; score: number | null; observation_count: number; metrics: TennisMetric[] };
export type TennisImprovement = { dimension_id: TennisDimensionId; title: string; observation: string; action: string; drill: string; retest: string; start_ms: number | null; end_ms: number | null };
export type TennisPatternRequirement = { id: string; label: string; status: "measured_proxy" | "not_measured"; metric_ids: string[]; reason: string | null };
export type TennisMotionPattern = {
  version: "tennis-motion-patterns-v1.0.0" | "tennis-motion-patterns-v1.1.0" | "tennis-motion-patterns-v1.2.0"; profile_id: string; classification_status: "verified" | "technique_unverified";
  requirements: TennisPatternRequirement[]; coverage: { measured_requirements: number; applicable_requirements: number; fraction: number; full_source_technique_covered: false };
  classification_provenance: { method: string; reviewer?: string | null };
};
export type TennisSubject = { subject_id: string; identity_basis: "target_lock" | "unlinked_source_track"; start_ms: number; end_ms: number;
  observed_frames: number; source_track_ids: number[]; measured_episode_count: number; included_in_report: boolean };
export type TennisSubjectScope = { version: "subject-scoped-assessment-v1"; selected_subject_id: string | null;
  selection_basis: "first_locked_target" | "first_unlinked_source_track" | "no_verified_subject"; cross_subject_aggregation: false; subjects: TennisSubject[] };
export type TennisEpisodeValidity = { episode_id: string; status: "scored" | "other_subject" | "rejected"; reason_codes: string[];
  subject_id?: string; person_track_id?: number; start_ms?: number; end_ms?: number };
export type TennisPatternCoverage = { measured_requirements: number; applicable_requirements: number; fraction: number | null;
  verified_technique_episodes: number; generic_episodes: number; full_source_technique_covered: false; profiles: string[] };
export type TennisReturnContext = { opponent_serve_id: string; server_track_id: number; ball_track_id: number; ball_observations: number;
  incoming_start_ms: number; incoming_end_ms: number; receiver_peak_ms: number; verification_method: "source_bound_opponent_serve_and_incoming_ball" };
type HandSide = "left" | "right";
export type TennisHandRoles = {
  version: "tennis-hand-roles-v1"; motion_wrist: HandSide; measurement_hand: HandSide; primary_racket_hand: HandSide | null;
  primary_hand_provenance: { method: "unverified" | "human_visual_review" | "agent_visual_review" | "source_bound_racket_association"; reviewer?: string };
  relationship: "motion_only" | "same_side" | "verified_two_hand";
  paired_wrist_evidence: Record<string, unknown> | null;
};
export type TennisEpisode = { episode_id: string; family: "baseline" | "serve" | "return"; start_ms: number; peak_ms: number; end_ms: number;
  subject_id?: string; person_track_id?: number; source_selection_epoch?: number; camera_reference_epoch?: number; motion_pattern?: TennisMotionPattern; metrics?: Record<string, { value: number; score: number }>; return_context?: TennisReturnContext; quality_reason_codes?: string[]; hand_roles?: TennisHandRoles };
export type TennisAssessment = {
  version: "tennis-action-assessment-v1.1.0" | "tennis-action-assessment-v1.2.0" | "tennis-action-assessment-v1.3.0" | "tennis-action-assessment-v1.4.0"; status: "scored" | "partial" | "unavailable";
  source_run_id: string; source_video_sha256: string; source_timestamp_semantics: "original_decoder_pts";
  overall_score: number | null; dimensions: TennisDimension[];
  reason_zh?: string;
  episodes?: TennisEpisode[];
  subject_scope?: TennisSubjectScope; episode_validity?: TennisEpisodeValidity[]; pattern_coverage?: TennisPatternCoverage;
  strengths: { title: string; observation: string }[]; improvements: TennisImprovement[];
  rubric: { version: "tennis-action-rubric-v1.1.0" | "tennis-action-rubric-v1.2.0" | "tennis-action-rubric-v1.3.0"; semantics: "engineering_action_quality"; coach_calibrated: false;
    identity_labels_used: false; confidence_used_in_score: false; aggregation: "mean_episode_dimension_scores_then_equal_dimension_mean" };
};
export type TennisAssessmentContext = { jobId: string; videoSha256: string; durationMs?: number | null };
const record = (value: unknown): Record<string, unknown> | null => value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;
const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const score = (value: unknown): value is number | null => value === null || finite(value) && value >= 0 && value <= 100;
const count = (value: unknown): value is number => Number.isSafeInteger(value) && Number(value) >= 0;
const text = (value: unknown): value is string => typeof value === "string" && value.length <= 4000;
const hash = (value: unknown): value is string => typeof value === "string" && /^[a-f0-9]{64}$/i.test(value);
const identifier = (value: unknown): value is string => typeof value === "string" && /^[A-Za-z0-9_:.-]{1,160}$/.test(value);
const ratio = (value: unknown): value is number => finite(value) && value >= 0 && value <= 1;
const handSide = (value: unknown): value is HandSide => value === "left" || value === "right";
export const TENNIS_PATTERN_NAMES: Record<string, string> = {
  generic: "通用挥拍", baseline_forehand: "底线正手", baseline_two_hand_backhand: "底线双手反手", baseline_one_hand_backhand: "底线单手反手",
  forehand_slice: "正手切削", backhand_slice: "反手切削", serve: "发球", return_forehand: "正手接发", return_two_hand_backhand: "双手反手接发",
  return_one_hand_backhand: "单手反手接发", return_forehand_slice: "正手切削接发", return_backhand_slice: "反手切削接发",
};

function motionPattern(value: unknown, metrics: Record<string, { value: number; score: number }>, expectedVersion: TennisMotionPattern["version"]): TennisMotionPattern | null {
  const item = record(value), coverage = record(item?.coverage), provenance = record(item?.classification_provenance);
  if (!item || item.version !== expectedVersion || typeof item.profile_id !== "string"
    || !Object.hasOwn(TENNIS_PATTERN_NAMES, item.profile_id) || !Array.isArray(item.requirements)
    || item.classification_status !== (item.profile_id === "generic" ? "technique_unverified" : "verified")
    || !coverage || !count(coverage.measured_requirements) || !count(coverage.applicable_requirements)
    || !ratio(coverage.fraction) || coverage.full_source_technique_covered !== false
    || !provenance || !identifier(provenance.method) || provenance.reviewer !== undefined && provenance.reviewer !== null && !text(provenance.reviewer)) return null;
  const requirements: TennisPatternRequirement[] = [], ids = new Set<string>();
  for (const raw of item.requirements) {
    const req = record(raw);
    if (!req || !identifier(req.id) || ids.has(req.id) || !text(req.label)
      || !["measured_proxy", "not_measured"].includes(String(req.status)) || !Array.isArray(req.metric_ids)
      || !req.metric_ids.every(identifier) || new Set(req.metric_ids).size !== req.metric_ids.length
      || req.status === "measured_proxy" && (req.reason !== null || !req.metric_ids.length || req.metric_ids.some(id => !Object.hasOwn(metrics, id)))
      || req.status === "not_measured" && (!text(req.reason) || !req.reason || req.metric_ids.some(id => Object.hasOwn(metrics, id)))) return null;
    ids.add(req.id);
    requirements.push({ id: req.id, label: req.label, status: req.status as TennisPatternRequirement["status"], metric_ids: [...req.metric_ids], reason: req.reason as string | null });
  }
  const measured = requirements.filter(req => req.status === "measured_proxy").length;
  if (!requirements.length || coverage.applicable_requirements !== requirements.length || coverage.measured_requirements !== measured
    || Math.abs(coverage.fraction - measured / requirements.length) > .000001) return null;
  return { version: expectedVersion, profile_id: item.profile_id, classification_status: item.classification_status as TennisMotionPattern["classification_status"], requirements,
    coverage: { measured_requirements: measured, applicable_requirements: requirements.length, fraction: coverage.fraction, full_source_technique_covered: false },
    classification_provenance: { method: provenance.method, ...(provenance.reviewer === undefined ? {} : { reviewer: provenance.reviewer as string | null }) } };
}

function handRoles(value: unknown, episode: Record<string, unknown>, pattern: TennisMotionPattern, sourceHash: string): TennisHandRoles | null {
  const item = record(value), provenance = record(item?.primary_hand_provenance);
  if (!item || item.version !== "tennis-hand-roles-v1" || !handSide(item.motion_wrist) || !handSide(item.measurement_hand)
    || !count(episode.source_selection_epoch) || !count(episode.camera_reference_epoch)
    || !(item.primary_racket_hand === null || handSide(item.primary_racket_hand)) || !provenance
    || !["unverified", "human_visual_review", "agent_visual_review", "source_bound_racket_association"].includes(String(provenance.method))
    || ["human_visual_review", "agent_visual_review"].includes(String(provenance.method)) && (!text(provenance.reviewer) || !provenance.reviewer)
    || item.primary_racket_hand === null !== (provenance.method === "unverified")) return null;
  const primary = item.primary_racket_hand, motion = item.motion_wrist, measured = item.measurement_hand;
  let evidence: Record<string, unknown> | null = null;
  if (item.relationship === "motion_only") {
    if (primary !== null || measured !== motion || item.paired_wrist_evidence !== null) return null;
  } else if (item.relationship === "same_side") {
    if (primary !== motion || measured !== motion || item.paired_wrist_evidence !== null) return null;
  } else if (item.relationship === "verified_two_hand") {
    evidence = record(item.paired_wrist_evidence);
    const source = record(evidence?.source), stats = record(evidence?.stats);
    if (primary === null || primary === motion || measured !== primary
      || !["human_visual_review", "agent_visual_review"].includes(String(provenance.method))
      || !["baseline_two_hand_backhand", "return_two_hand_backhand"].includes(pattern.profile_id)
      || !evidence || evidence.version !== "paired-wrist-phase-v1" || evidence.status !== "supported"
      || evidence.verification_method !== "recomputed_from_source_bound_native_image_observations"
      || evidence.motion_wrist !== motion || evidence.paired_wrist !== primary
      || !finite(evidence.start_ms) || !finite(evidence.end_ms) || !finite(evidence.declared_phase_start_ms) || !finite(evidence.declared_phase_end_ms)
      || evidence.start_ms < Number(episode.start_ms) || evidence.end_ms > Number(episode.end_ms) || evidence.start_ms >= evidence.end_ms
      || Math.abs(evidence.declared_phase_start_ms - evidence.start_ms) > 1.01 || Math.abs(evidence.declared_phase_end_ms - evidence.end_ms) > 1.01
      || Math.abs(evidence.end_ms - Number(episode.peak_ms)) > 1.01
      || !source || !hash(source.video_sha256) || source.video_sha256.toLowerCase() !== sourceHash.toLowerCase()
      || source.person_track_id !== episode.person_track_id || source.source_selection_epoch !== episode.source_selection_epoch
      || source.camera_reference_epoch !== episode.camera_reference_epoch || !count(source.selection_epoch)
      || !count(source.source_selection_epoch) || !count(source.camera_reference_epoch)
      || source.pose_source !== "sam3d" || source.observation_semantics !== "monocular_model_estimate"
      || !["full_torso", "left_shoulder_left_torso", "right_shoulder_right_torso"].includes(String(source.normalization_basis))
      || !stats || !ratio(stats.coupled_fraction) || !ratio(stats.displacement_ratio)
      || !finite(stats.net_displacement_cosine) || Math.abs(stats.net_displacement_cosine) > 1.000001
      || ![stats.max_wrist_gap, stats.left_peak_speed_torso_per_s, stats.right_peak_speed_torso_per_s,
        stats.left_displacement_torso, stats.right_displacement_torso].every(v => finite(v) && v >= 0)
      || !Array.isArray(evidence.sample_binding) || evidence.sample_binding.length < 3) return null;
    let lastFrame = -1, lastTime = -Infinity, sourceOffset: number | null = null;
    for (const raw of evidence.sample_binding) {
      const sample = record(raw), binding = record(sample?.source_binding);
      if (!sample || !count(sample.frame_index) || sample.frame_index <= lastFrame || !finite(sample.timestamp_ms)
        || sample.timestamp_ms <= lastTime || lastTime > -Infinity && sample.timestamp_ms - lastTime > 160
        || !binding || binding.frame_index !== sample.frame_index || binding.person_track_id !== source.person_track_id
        || binding.selection_epoch !== source.source_selection_epoch || binding.camera_reference_epoch !== source.camera_reference_epoch
        || !hash(binding.video_sha256) || binding.video_sha256.toLowerCase() !== sourceHash.toLowerCase()
        || !count(binding.pts) || !count(binding.time_base_num) || binding.time_base_num === 0 || !count(binding.time_base_den) || binding.time_base_den === 0
        || !finite(binding.timestamp_ms) || Math.abs(binding.timestamp_ms - binding.pts * binding.time_base_num / binding.time_base_den * 1000) > 1.01) return null;
      const offset = sample.timestamp_ms - binding.timestamp_ms;
      if (sourceOffset === null) sourceOffset = offset;
      else if (Math.abs(offset - sourceOffset) > 1.01) return null;
      lastFrame = sample.frame_index; lastTime = sample.timestamp_ms;
    }
    if (Math.abs(Number(record(evidence.sample_binding[0])?.timestamp_ms) - evidence.start_ms) > 1.01
      || Math.abs(lastTime - evidence.end_ms) > 1.01) return null;
  } else return null;
  return { version: "tennis-hand-roles-v1", motion_wrist: motion, measurement_hand: measured, primary_racket_hand: primary,
    primary_hand_provenance: { method: provenance.method as TennisHandRoles["primary_hand_provenance"]["method"],
      ...(typeof provenance.reviewer === "string" ? { reviewer: provenance.reviewer } : {}) },
    relationship: item.relationship, paired_wrist_evidence: evidence };
}

function scopedAssessment(report: Record<string, unknown>, duration: number | null | undefined): {
  subject_scope: TennisSubjectScope; episode_validity: TennisEpisodeValidity[]; pattern_coverage: TennisPatternCoverage; episodes: TennisEpisode[];
} | null {
  const scope = record(report.subject_scope), coverage = record(report.pattern_coverage);
  if (!scope || scope.version !== "subject-scoped-assessment-v1" || scope.cross_subject_aggregation !== false
    || !(scope.selected_subject_id === null || identifier(scope.selected_subject_id)) || !Array.isArray(scope.subjects)
    || !["first_locked_target", "first_unlinked_source_track", "no_verified_subject"].includes(String(scope.selection_basis))
    || !Array.isArray(report.episodes) || !Array.isArray(report.episode_validity) || !coverage) return null;
  if (report.episodes.length > 0 && !finite(duration)) return null;
  const subjects: TennisSubject[] = [], subjectIds = new Set<string>();
  for (const raw of scope.subjects) {
    const item = record(raw);
    if (!item || !identifier(item.subject_id) || subjectIds.has(item.subject_id) || !["target_lock", "unlinked_source_track"].includes(String(item.identity_basis))
      || !finite(item.start_ms) || !finite(item.end_ms) || item.start_ms < 0 || item.end_ms < item.start_ms || duration != null && item.end_ms > duration
      || !count(item.observed_frames) || item.observed_frames === 0 || !Array.isArray(item.source_track_ids) || !item.source_track_ids.length
      || !item.source_track_ids.every(count) || new Set(item.source_track_ids).size !== item.source_track_ids.length
      || !count(item.measured_episode_count) || typeof item.included_in_report !== "boolean"
      || item.included_in_report !== (item.subject_id === scope.selected_subject_id)) return null;
    subjectIds.add(item.subject_id);
    subjects.push({ subject_id: item.subject_id, identity_basis: item.identity_basis as TennisSubject["identity_basis"], start_ms: item.start_ms, end_ms: item.end_ms,
      observed_frames: item.observed_frames, source_track_ids: [...item.source_track_ids], measured_episode_count: item.measured_episode_count, included_in_report: item.included_in_report });
  }
  const selected = subjects.find(subject => subject.subject_id === scope.selected_subject_id);
  if (scope.selected_subject_id === null ? subjects.length > 0 || report.episodes.length > 0 : !selected) return null;
  if (selected && (scope.selection_basis === "first_locked_target" ? selected.identity_basis !== "target_lock"
    : scope.selection_basis === "first_unlinked_source_track" ? selected.identity_basis !== "unlinked_source_track" : true)) return null;
  const episodes: TennisEpisode[] = [], episodeIds = new Set<string>();
  for (const raw of report.episodes) {
    const item = record(raw), rawMetrics = record(item?.metrics);
    if (!item || !selected || !identifier(item.episode_id) || episodeIds.has(item.episode_id)
      || item.subject_id !== selected.subject_id || !count(item.person_track_id) || !selected.source_track_ids.includes(item.person_track_id)
      || !["baseline", "serve", "return"].includes(String(item.family)) || !finite(item.start_ms) || !finite(item.peak_ms) || !finite(item.end_ms)
      || item.start_ms < 0 || item.start_ms >= item.peak_ms || item.peak_ms >= item.end_ms || duration != null && item.end_ms > duration
      || item.start_ms < selected.start_ms - 1.01 || item.end_ms > selected.end_ms + 1.01 || !rawMetrics) return null;
    const metrics: Record<string, { value: number; score: number }> = Object.create(null);
    for (const [key, rawMetric] of Object.entries(rawMetrics)) {
      const metric = record(rawMetric);
      if (!identifier(key) || !metric || !finite(metric.value) || !finite(metric.score) || !score(metric.score)) return null;
      metrics[key] = { value: metric.value, score: metric.score };
    }
    const current = report.version === "tennis-action-assessment-v1.3.0" || report.version === "tennis-action-assessment-v1.4.0";
    const pattern = motionPattern(item.motion_pattern, metrics, report.version === "tennis-action-assessment-v1.4.0"
      ? "tennis-motion-patterns-v1.2.0" : current ? "tennis-motion-patterns-v1.1.0" : "tennis-motion-patterns-v1.0.0");
    if (!pattern) return null;
    const roles = report.version === "tennis-action-assessment-v1.4.0" ? handRoles(item.hand_roles, item, pattern, String(report.source_video_sha256)) : null;
    if (report.version === "tennis-action-assessment-v1.4.0" && !roles) return null;
    let returnContext: TennisReturnContext | undefined;
    if (current) {
      if (episodes.some(previous => Math.min(previous.end_ms, item.end_ms as number) - Math.max(previous.start_ms, item.start_ms as number) > 1.01)) return null;
      if (pattern.profile_id !== "generic" && (pattern.profile_id === "serve" ? item.family !== "serve"
        : pattern.profile_id.startsWith("return_") ? item.family !== "return" : item.family !== "baseline")) return null;
      const provenance = pattern.classification_provenance;
      if (!["unverified", "source_bound_rule_inferred", "source_bound_serve_phases", "human_visual_review", "agent_visual_review"].includes(provenance.method)
        || pattern.profile_id === "generic" && provenance.method !== "unverified"
        || pattern.profile_id !== "generic" && provenance.method === "unverified"
        || ["human_visual_review", "agent_visual_review"].includes(provenance.method) && !provenance.reviewer
        || provenance.method === "source_bound_serve_phases" && pattern.profile_id !== "serve") return null;
      if (item.family === "return") {
        const evidence = record(item.return_context);
        if (!evidence || evidence.verification_method !== "source_bound_opponent_serve_and_incoming_ball"
          || !identifier(evidence.opponent_serve_id) || !count(evidence.server_track_id) || evidence.server_track_id === item.person_track_id
          || !count(evidence.ball_track_id) || !count(evidence.ball_observations) || evidence.ball_observations < 3
          || !finite(evidence.incoming_start_ms) || !finite(evidence.incoming_end_ms) || !finite(evidence.receiver_peak_ms)
          || evidence.incoming_start_ms < 0 || evidence.incoming_start_ms >= evidence.incoming_end_ms
          || evidence.receiver_peak_ms < item.start_ms || evidence.receiver_peak_ms > item.end_ms
          || evidence.incoming_end_ms < evidence.receiver_peak_ms - 350 || evidence.incoming_end_ms > evidence.receiver_peak_ms + 100) return null;
        returnContext = { opponent_serve_id: evidence.opponent_serve_id, server_track_id: evidence.server_track_id,
          ball_track_id: evidence.ball_track_id, ball_observations: evidence.ball_observations, incoming_start_ms: evidence.incoming_start_ms,
          incoming_end_ms: evidence.incoming_end_ms, receiver_peak_ms: evidence.receiver_peak_ms, verification_method: evidence.verification_method };
      } else if (item.return_context !== undefined) return null;
    }
    if (item.quality_reason_codes !== undefined && (!Array.isArray(item.quality_reason_codes) || !item.quality_reason_codes.every(identifier))) return null;
    episodeIds.add(item.episode_id);
    episodes.push({ episode_id: item.episode_id, family: item.family as TennisEpisode["family"], start_ms: item.start_ms, peak_ms: item.peak_ms, end_ms: item.end_ms,
      subject_id: selected.subject_id, person_track_id: item.person_track_id, motion_pattern: pattern, metrics,
      ...(roles ? { hand_roles: roles, source_selection_epoch: item.source_selection_epoch as number, camera_reference_epoch: item.camera_reference_epoch as number } : {}),
      ...(Array.isArray(item.quality_reason_codes) ? { quality_reason_codes: [...item.quality_reason_codes] } : {}),
      ...(returnContext ? { return_context: returnContext } : {}) });
  }
  if (selected && selected.measured_episode_count !== episodes.length || report.status !== "unavailable" && !episodes.length) return null;
  const validity: TennisEpisodeValidity[] = [], scored = new Set<string>();
  for (const raw of report.episode_validity) {
    const item = record(raw);
    if (!item || !identifier(item.episode_id) || !["scored", "other_subject", "rejected"].includes(String(item.status))
      || !Array.isArray(item.reason_codes) || !item.reason_codes.every(identifier)
      || item.subject_id !== undefined && item.subject_id !== null && !identifier(item.subject_id)
      || item.person_track_id !== undefined && item.person_track_id !== null && !count(item.person_track_id)) return null;
    if (item.status === "scored") {
      if (item.subject_id !== scope.selected_subject_id || !episodeIds.has(item.episode_id) || scored.has(item.episode_id)) return null;
      scored.add(item.episode_id);
    } else if (item.status === "other_subject" && (!identifier(item.subject_id) || !subjectIds.has(item.subject_id) || item.subject_id === scope.selected_subject_id)) return null;
    validity.push({ episode_id: item.episode_id, status: item.status as TennisEpisodeValidity["status"], reason_codes: [...item.reason_codes],
      ...(identifier(item.subject_id) ? { subject_id: item.subject_id } : {}), ...(count(item.person_track_id) ? { person_track_id: item.person_track_id } : {}),
      ...(finite(item.start_ms) && finite(item.end_ms) && item.start_ms >= 0 && item.end_ms > item.start_ms && (duration == null || item.end_ms <= duration)
        ? { start_ms: item.start_ms, end_ms: item.end_ms } : {}) });
  }
  if (scored.size !== episodes.length) return null;
  const measured = episodes.reduce((sum, e) => sum + e.motion_pattern!.coverage.measured_requirements, 0);
  const applicable = episodes.reduce((sum, e) => sum + e.motion_pattern!.coverage.applicable_requirements, 0);
  const verified = episodes.filter(e => e.motion_pattern!.classification_status === "verified").length;
  const profiles = [...new Set(episodes.map(e => e.motion_pattern!.profile_id))].sort();
  if (coverage.measured_requirements !== measured || coverage.applicable_requirements !== applicable
    || coverage.verified_technique_episodes !== verified || coverage.generic_episodes !== episodes.length - verified
    || coverage.full_source_technique_covered !== false || !Array.isArray(coverage.profiles) || coverage.profiles.length !== profiles.length
    || !coverage.profiles.every(value => typeof value === "string" && profiles.includes(value)) || new Set(coverage.profiles).size !== profiles.length
    || (applicable === 0 ? coverage.fraction !== null : !ratio(coverage.fraction) || Math.abs(coverage.fraction - measured / applicable) > .000001)) return null;
  return { subject_scope: { version: "subject-scoped-assessment-v1", selected_subject_id: scope.selected_subject_id,
    selection_basis: scope.selection_basis as TennisSubjectScope["selection_basis"], cross_subject_aggregation: false, subjects }, episode_validity: validity,
    pattern_coverage: { measured_requirements: measured, applicable_requirements: applicable, fraction: applicable ? measured / applicable : null,
      verified_technique_episodes: verified, generic_episodes: episodes.length - verified, full_source_technique_covered: false, profiles },
    episodes: episodes.sort((a, b) => a.start_ms - b.start_ms || a.end_ms - b.end_ms) };
}

function navigationEpisodes(value: unknown, durationMs: number | null | undefined): TennisEpisode[] {
  if (!Array.isArray(value) || !finite(durationMs) || durationMs <= 0) return [];
  const ids = new Map<unknown, number>();
  for (const raw of value) { const id = record(raw)?.episode_id; ids.set(id, (ids.get(id) ?? 0) + 1); }
  const seen = new Set<string>(), episodes: TennisEpisode[] = [];
  for (const raw of value) {
    const item = record(raw);
    if (!item || typeof item.episode_id !== "string" || !/^[A-Za-z0-9_:.-]{1,160}$/.test(item.episode_id) || ids.get(item.episode_id) !== 1
      || !["baseline", "serve", "return"].includes(String(item.family)) || !finite(item.start_ms) || !finite(item.peak_ms) || !finite(item.end_ms)
      || item.start_ms < 0 || item.start_ms >= item.peak_ms || item.peak_ms >= item.end_ms || item.end_ms > durationMs) continue;
    const key = `${item.start_ms}:${item.peak_ms}:${item.end_ms}`;
    if (seen.has(key)) continue;
    seen.add(key);
    episodes.push({ episode_id: item.episode_id, family: item.family as TennisEpisode["family"], start_ms: item.start_ms, peak_ms: item.peak_ms, end_ms: item.end_ms });
  }
  return episodes.sort((a, b) => a.start_ms - b.start_ms || a.end_ms - b.end_ms || a.episode_id.localeCompare(b.episode_id));
}

/** A payload never establishes its own identity: bind it to independent video metadata. */
export function tennisAssessmentContext(result: DemoResultResponse | null): TennisAssessmentContext | null {
  if (!result?.job_id) return null;
  const input = record(record(result.input)?.video);
  const originalHash = result.original_video?.sha256, inputHash = input?.sha256;
  if (originalHash !== undefined && !hash(originalHash) || inputHash !== undefined && !hash(inputHash)) return null;
  if (hash(originalHash) && hash(inputHash) && originalHash.toLowerCase() !== inputHash.toLowerCase()) return null;
  const videoSha256 = originalHash ?? inputHash;
  if (!hash(videoSha256)) return null;
  const duration = input?.duration_ms;
  return { jobId: result.job_id, videoSha256, ...(finite(duration) && duration > 0 ? { durationMs: duration } : {}) };
}

/** Validate before display, persistence, or export; missing measurements never become zero. */
export function validatedTennisAssessment(value: unknown, context: TennisAssessmentContext): TennisAssessment | null {
  const report = record(value), rubric = record(report?.rubric);
  const current = report?.version === "tennis-action-assessment-v1.3.0" || report?.version === "tennis-action-assessment-v1.4.0";
  const scoped = current || report?.version === "tennis-action-assessment-v1.2.0";
  if (!report || !context.jobId || !hash(context.videoSha256) || !["tennis-action-assessment-v1.1.0", "tennis-action-assessment-v1.2.0", "tennis-action-assessment-v1.3.0", "tennis-action-assessment-v1.4.0"].includes(String(report.version))
    || report.source_run_id !== context.jobId || !hash(report.source_video_sha256) || report.source_video_sha256.toLowerCase() !== context.videoSha256.toLowerCase()
    || report.source_timestamp_semantics !== "original_decoder_pts" || !["scored", "partial", "unavailable"].includes(String(report.status))
    || !score(report.overall_score) || !Array.isArray(report.dimensions) || report.dimensions.length !== 5
    || report.reason_zh !== undefined && !text(report.reason_zh)
    || !rubric || rubric.version !== (current ? "tennis-action-rubric-v1.3.0" : scoped ? "tennis-action-rubric-v1.2.0" : "tennis-action-rubric-v1.1.0") || rubric.semantics !== "engineering_action_quality"
    || rubric.coach_calibrated !== false || rubric.identity_labels_used !== false || rubric.confidence_used_in_score !== false
    || rubric.aggregation !== "mean_episode_dimension_scores_then_equal_dimension_mean"
    || context.durationMs !== undefined && context.durationMs !== null && (!finite(context.durationMs) || context.durationMs <= 0)) return null;
  const scopedFields = scoped ? scopedAssessment(report, context.durationMs) : null;
  if (scoped && !scopedFields) return null;
  const dimensions: TennisDimension[] = [];
  for (const spec of TENNIS_DIMENSIONS) {
    const matches = report.dimensions.map(record).filter(item => item?.id === spec.id);
    const dimension = matches[0];
    if (matches.length !== 1 || !dimension || !score(dimension.score) || !count(dimension.observation_count) || !Array.isArray(dimension.metrics)) return null;
    const metrics: TennisMetric[] = [], ids = new Set<string>();
    for (const raw of dimension.metrics) {
      const metric = record(raw);
      if (!metric || !text(metric.id) || !metric.id || ids.has(metric.id) || !text(metric.label) || !text(metric.unit) || !text(metric.target)
        || !(metric.value === null || finite(metric.value)) || !score(metric.score) || !finite(metric.weight) || metric.weight <= 0
        || !count(metric.observation_count) || metric.score !== null && (metric.value === null || metric.observation_count === 0)) return null;
      ids.add(metric.id);
      metrics.push({ id: metric.id, label: metric.label, value: metric.value, unit: metric.unit, score: metric.score,
        weight: metric.weight, observation_count: metric.observation_count, target: metric.target });
    }
    const scoredMetrics = metrics.filter(item => item.score !== null);
    const scoredWeight = scoredMetrics.reduce((sum, item) => sum + item.weight, 0);
    if (dimension.score !== null && (dimension.observation_count === 0 || !scoredMetrics.length || !Number.isFinite(scoredWeight)
      || Math.abs(scoredWeight - 1) > .00001
      || Math.abs(dimension.score - scoredMetrics.reduce((sum, item) => sum + item.score! * item.weight, 0) / scoredWeight) > .15)
      || dimension.score === null && scoredMetrics.length > 0) return null;
    if (scopedFields) {
      const available = scopedFields.episodes.map(episode => metrics.filter(metric => Object.hasOwn(episode.metrics!, metric.id)).length);
      const episodeCount = available.filter(n => n > 0).length;
      if (dimension.observation_count !== episodeCount) return null;
      for (const metric of metrics) {
        const observations = scopedFields.episodes.flatMap((episode, index) => {
          const value = episode.metrics![metric.id];
          return value ? [{ ...value, weight: 1 / available[index] }] : [];
        });
        if (metric.observation_count !== observations.length) return null;
        if (!observations.length) { if (metric.score !== null || metric.value !== null) return null; continue; }
        const mass = observations.reduce((sum, item) => sum + item.weight, 0);
        if (metric.score === null || metric.value === null || Math.abs(metric.weight - mass / episodeCount) > .000001
          || Math.abs(metric.score - observations.reduce((sum, item) => sum + item.score * item.weight, 0) / mass) > .02
          || Math.abs(metric.value - observations.reduce((sum, item) => sum + item.value * item.weight, 0) / mass) > .00002) return null;
      }
    }
    dimensions.push({ id: spec.id, label: spec.label, score: dimension.score, observation_count: dimension.observation_count, metrics });
  }
  const measured = dimensions.filter(item => item.score !== null);
  if (scopedFields && scopedFields.episodes.some(episode => episode.motion_pattern!.requirements.some(requirement => requirement.status === "measured_proxy"
    && requirement.metric_ids.some(id => !dimensions.some(dimension => dimension.metrics.some(metric => metric.id === id && metric.score !== null)))))) return null;
  if (report.status === "scored" && measured.length !== 5 || report.status === "unavailable" && measured.length !== 0
    || report.status === "partial" && (measured.length === 0 || measured.length === 5)) return null;
  if (report.overall_score !== null && (!measured.length || Math.abs(report.overall_score - measured.reduce((sum, item) => sum + item.score!, 0) / measured.length) > .15)) return null;
  if (!measured.length && report.overall_score !== null) return null;
  if (measured.length && report.overall_score === null) return null;
  if (!Array.isArray(report.strengths) || !Array.isArray(report.improvements) || report.improvements.length > 2) return null;
  const strengths: TennisAssessment["strengths"] = [];
  for (const raw of report.strengths) {
    const item = record(raw);
    if (!item || !text(item.title) || !text(item.observation)) return null;
    strengths.push({ title: item.title, observation: item.observation });
  }
  const improvements: TennisImprovement[] = [];
  for (const raw of report.improvements) {
    const item = record(raw), dimension = dimensions.find(d => d.id === item?.dimension_id);
    if (!item || !dimension || ![item.title, item.observation, item.action, item.drill, item.retest].every(text)) return null;
    const start = item.start_ms, end = item.end_ms;
    const hasTime = finite(start) && finite(end) && start >= 0 && end > start && (context.durationMs == null || end <= context.durationMs);
    if (!(start === null && end === null) && !hasTime) return null;
    improvements.push({ dimension_id: dimension.id, title: item.title as string, observation: item.observation as string, action: item.action as string,
      drill: item.drill as string, retest: item.retest as string, start_ms: hasTime ? start as number : null, end_ms: hasTime ? end as number : null });
  }
  return { version: report.version as TennisAssessment["version"], status: report.status as TennisAssessment["status"], source_run_id: context.jobId,
    source_video_sha256: report.source_video_sha256, source_timestamp_semantics: "original_decoder_pts", overall_score: report.overall_score,
    dimensions, strengths, improvements, ...(typeof report.reason_zh === "string" ? { reason_zh: report.reason_zh } : {}),
    ...(scopedFields ?? (report.episodes === undefined ? {} : { episodes: navigationEpisodes(report.episodes, context.durationMs) })),
    rubric: { version: rubric.version as TennisAssessment["rubric"]["version"], semantics: "engineering_action_quality", coach_calibrated: false,
      identity_labels_used: false, confidence_used_in_score: false, aggregation: "mean_episode_dimension_scores_then_equal_dimension_mean" } };
}

export function tennisAssessmentOf(result: DemoResultResponse | null): TennisAssessment | null {
  const context = tennisAssessmentContext(result);
  return context ? validatedTennisAssessment(result?.tennis_assessment, context) : null;
}

/** Late supplemental reads must not erase a score already loaded for this same video. */
export function retainTennisAssessment(incoming: DemoResultResponse, previous: DemoResultResponse | null): DemoResultResponse {
  if (incoming.tennis_assessment !== undefined) return incoming;
  const context = tennisAssessmentContext(incoming), existing = tennisAssessmentOf(previous);
  const retained = context && existing ? validatedTennisAssessment(existing, context) : null;
  return retained ? { ...incoming, tennis_assessment: retained } : incoming;
}
