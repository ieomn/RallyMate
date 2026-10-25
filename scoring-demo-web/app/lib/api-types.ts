import type { Domain, Grade, ScoreReport } from "../scoring/engine";

/** Public shape returned by /api/scorecard (and compatible remote score services). */
export interface ScorecardResult {
  indicatorId: string;
  indicatorName: string;
  domain: Domain;
  eventCode: string;
  stageCode: string;
  status: "scored" | "ready" | "partial" | "blocked";
  score: number | null;
  grade: Grade | null;
  evidence: number;
  confidence: number;
  dimensions: {
    technique: number;
    timing: number;
    stability: number;
    continuity: number;
  } | null;
  verdict: string;
  feedback: string;
}

export interface ScorecardRequest {
  scenarioId?: string;
  domain?: Domain;
  stage1Summary?: Record<string, unknown>;
}

export interface ScorecardResponse
  extends Pick<
    ScoreReport,
    | "reportVersion"
    | "generatedAt"
    | "scenario"
    | "overallScore"
    | "overallGrade"
    | "overallEvidence"
    | "acceptanceStatus"
    | "domains"
    | "formula"
  > {
  registryVersion: string;
  apiVersion?: string;
  compatibilityVersion?: string;
  surface?: string;
  registryKind?: string;
  resultCount: number;
  results: ScorecardResult[];
}

export interface ScorecardCapabilities {
  service: string;
  apiVersion: string;
  compatibilityVersion?: string;
  surface?: string;
  registryKind?: string;
  registryVersion: string;
  indicatorCount: number;
  scenarios: Array<{
    id: string;
    label: string;
    mode: "demo" | "real";
    description: string;
  }>;
  usage?: string;
  [key: string]: unknown;
}

/** Versioned qualitative catalog served by the Python API. */
export interface TechniqueCatalogItem {
  id: string;
  family: string;
  name_zh: string;
  aliases?: string[];
  phases: string[];
  core_visual_features: string[];
  required_evidence: string[];
  enhanced_evidence: string[];
  proxy_limits?: string[];
  reference_constraints?: Array<Record<string, unknown>>;
  [key: string]: unknown;
}

export interface TechniqueCatalogResponse {
  schema_version: string;
  registry_id: string;
  registry_version: string;
  registry_sha256: string;
  source_documents: Array<Record<string, unknown>>;
  semantics: Record<string, unknown>;
  default_phase_contracts: Record<string, string[]>;
  techniques: TechniqueCatalogItem[];
  [key: string]: unknown;
}

export interface RallyMateMetaResponse {
  service: string;
  api_version: string;
  compatibility_version?: string;
  environment?: string;
  public_base_url?: string | null;
  authentication?: Record<string, unknown>;
  capabilities?: Record<string, unknown>;
  technique_registry?: Record<string, unknown>;
  client_configuration?: Record<string, unknown>;
  [key: string]: unknown;
}

export type JobStatus =
  | "queued"
  | "running"
  | "processing"
  | "completed"
  | "succeeded"
  | "failed"
  | "cancelled";

export interface OriginalPoseTimeBinding {
  version: "original-video-decoder-pts-v1";
  job_id: string; source_job_id: string; video_sha256: string; frames_sha256: string;
  timestamp_semantics: "source_relative_decoder_pts";
  source_pts_origin_ms: number; media_time_origin_ms: 0;
  anchor: { index: number; processed_index: number; pts: number; time_base_num: number;
    time_base_den: number; source_timestamp_ms: number; timestamp_ms: number };
}

/** File identity; source-clock authorization requires the separate PTS binding. */
export interface OriginalVideo {
  url: string;
  sha256: string;
  bytes: number;
  source_job_id: string;
  pose_time_binding?: OriginalPoseTimeBinding;
}

/** Shape used by the production /v1/jobs endpoint. Unknown additions are kept. */
export interface JobSubmission {
  id: string;
  status: JobStatus | string;
  cancel_requested?: boolean;
  cancel_requested_at?: string | null;
  cancelled_at?: string | null;
  error?: string | null;
  progress?: number | {
    phase?: string;
    percent?: number;
    message?: string;
    [key: string]: unknown;
  };
  trajectory_url?: string | null;
  technique_assessment_url?: string | null;
  demo_result_url?: string | null;
  artifact_urls?: Record<string, string>;
  original_video?: OriginalVideo | null;
  createdAt?: string;
  created_at?: string;
  [key: string]: unknown;
}

export interface JobProgress extends JobSubmission {
  progress?: number | {
    phase?: string;
    percent?: number;
    message?: string;
    [key: string]: unknown;
  };
  stage?: string;
  message?: string;
  error?: string | null;
  processedFrames?: number;
  processed_frames?: number;
  totalFrames?: number;
  total_frames?: number;
}

export interface Sam3dMeshPreview {
  status: "ready";
  video_url: string;
  side_video_url: string;
  preview_image_url: string;
  manifest_url: string;
  source_video_sha256: string;
  source_frame_start: number;
  /** Exclusive source frame boundary. */
  source_frame_end: number;
  frame_count: number;
  source_timestamp_origin_ms: number;
  source_relative_origin_ms: number;
  timing_preserved: true;
  renderer: "official-sam3d-pyrender-persistent-context";
}

export interface SwingKinematicsMetric {
  value: number | null;
  unit: "deg" | "deg/s" | "torso/s";
  timestamp_ms: number | null;
  source_frames: number[];
  window_start_ms: number | null;
  window_end_ms: number | null;
  status: "estimated" | "unavailable";
}

export interface SwingKinematicsEpisode {
  episode_id: string;
  start_ms: number;
  end_ms: number;
  person_track_id: number;
  racket_hand: "left" | "right" | null;
  frame_count: number;
  valid_frame_count: number;
  coverage: number;
  reason_zh: string;
  quality_reason_counts: Record<string, number>;
  metrics: Record<"shoulder_hip_separation_deg" | "hip_angular_speed_deg_s" | "shoulder_angular_speed_deg_s" | "wrist_speed_torso_s", SwingKinematicsMetric>;
}

export interface SwingKinematicsReport {
  version: "sam3d-swing-kinematics-v1";
  status: "available" | "partial" | "unavailable";
  source_run_id: string | null;
  source_video_sha256: string | null;
  source_timestamp_semantics: "original_decoder_pts";
  coordinate_system: "sam3d_pred_keypoints_3d";
  coordinate_units: "model_units";
  wrist_reference: "midhip_relative_fixed_median_torso_per_continuous_window";
  camera_reference: "same_camera_epoch_uncalibrated_monocular_estimate";
  camera_rotation_compensated: false;
  cross_video_absolute_speed_comparable: false;
  measurement_semantics: "monocular_3d_model_estimate";
  score: null;
  reason_zh: string;
  episodes: SwingKinematicsEpisode[];
  limitations_zh: string[];
}

export interface DemoResultResponse {
  swing_kinematics?: SwingKinematicsReport;
  original_video?: OriginalVideo | null;
  sam3d_mesh?: Sam3dMeshPreview | null;
  sam3d_mesh_status?: "ready" | "pending" | "failed" | "unavailable";
  runtime?: {
    annotated_video_compatibility?: { timing_preserved?: boolean; timebase?: string; timing_warning?: string; [key: string]: unknown } | null;
    [key: string]: unknown;
  };
  analysis_report?: AnalysisReport;
  footwork_review?: FootworkReview;
  source_aligned_assessment?: { contextual_measurements?: HipContextualMeasurement[]; [key: string]: unknown };
  action_recognition?: { motion_analysis?: MotionAnalysis; [key: string]: unknown };
  training_evaluation?: TrainingEvaluation;
  process_evaluation?: ProcessEvaluation;
  card_execution?: unknown;
  actions?: ActionEvaluation[];
  technique_assessment?: TechniqueAssessmentResponse;
  artifact_urls?: Record<string, string>;
  job_id?: string;
  video_id?: string;
  status?: string;
  summary?: Record<string, unknown>;
  scorecard?: ScorecardResponse;
  artifacts?: { evidence_frames?: EvidenceFrame[]; [key: string]: unknown };
  features?: { ball?: { trajectory?: BallTrajectory }; [key: string]: unknown };
  signals?: { racket?: RacketSignal; grip?: GripSignal; [key: string]: unknown };
  hit_statistics?: {
    status: "observed" | "not_observed" | "unsupported" | string;
    total_count: number | null;
    by_event_code?: Record<string, number>;
    reason_zh?: string;
    count_semantics?: string;
  };
  trajectory_analysis?: {
    status: "available" | "unavailable" | string;
    source_artifact?: string | null;
    endpoint_required?: boolean;
    reason_zh?: string;
    [key: string]: unknown;
  };
  [key: string]: unknown;
}

export type ProcessDimensionId = "movement" | "preparation" | "swing" | "contact" | "follow_through" | "transition";

export interface ProcessCriterion {
  legacy_diagnostic?: { version: "relative-reference-v1.0.0"; score: number | null; semantics: "selection_biased_motion_curve_diagnostic"; is_technical_quality: false; contributes_to_current_score: false; exclusion_reason: "selection_biased_proxy_excluded" };
  id: string;
  label_zh: string;
  status: "measured" | "unavailable";
  source_conflict?: true;
  value: number | null;
  unit: string | null;
  score: number | null;
  reason_zh: string;
  rule_zh: string;
  source: string;
  start_ms: number | null;
  end_ms: number | null;
  episode_id: string | null;
  related_episode_id?: string | null;
  sample_count?: number;
  timing_resolution_ms?: number | null;
  ordering_resolved?: boolean;
  indicator_id?: string;
  feature_name?: string;
  video_id?: string;
  person_track_id?: number;
  source_frames?: number[];
  feature_version?: string;
  window_scope?: "event_interval" | "continuous_window_in_estimated_preparation" | "continuous_local_window_in_estimated_preparation";
  measurement_semantics?: "image_plane_proxy_not_source_card_grade" | "preparation_candidate_2d_not_formal_stage_or_grade" | "joint_specific_2d_measurements_not_technical_grade";
  reference?: Record<string, unknown>;
  source_timestamps_ms?: number[];
  related_source_cards?: string[];
  source_refs?: string[];
  source_card_applicability?: "unconfirmed";
  assessment_status?: "measurement_only" | "mathematical_reference";
  full_source_rule_satisfied?: false;
  original_metric_evidence?: Record<string, unknown>;
  measurement_provenance?: Record<string, unknown>;
  source_window?: Record<string, unknown>;
  geometry_mask?: Record<string, boolean[]>;
  source_track_id?: number;
  phase?: "preparation";
  phase_window?: { start_ms: number; end_ms: number };
  statistics?: Record<string, unknown>;
  applicability_missing?: string[];
  source_binding_start?: Record<string, unknown>;
  source_binding_end?: Record<string, unknown>;
  source_time_origin_ms?: number;
  video_timestamps_ms?: number[];
  time_basis?: "decoder_timestamp_ms" | "bound_source_pts";
  fixed_scale_px?: number;
  source_duration_ms?: number;
  full_phase_coverage?: boolean;
  quality?: { measurement_status: "measured"; confidence: number | null; reason_codes: string[]; aggregate_feature_status: "measured" | "unavailable"; required_joints: string[]; review_version: string };
}

export interface ProcessDimension {
  id: ProcessDimensionId;
  name_zh: string;
  score: number | null;
  status: "measured" | "partial" | "unavailable";
  summary_zh: string;
  criteria: ProcessCriterion[];
  scoring_audit?: RelativeScoringAudit;
  gap?: RelativeScoringGap;
  scored_rule_count?: number;
  total_rule_count?: number;
  sample_count?: number;
  measured_criterion_count?: number;
  returned_measured_criterion_count?: number;
  scored_sample_count?: number;
  total_sample_count?: number;
  aggregation?: "mean_of_independent_samples_for_one_explicit_rule" | "none";
  score_scope?: "engineering_subcriterion_reference" | "observation_only";
  score_scope_zh?: string;
  score_covers_all_source_cards?: false;
  status_reason?: string;
  status_reason_zh?: string;
  criteria_selection?: { full_count: number; returned_count: number; is_truncated: boolean; policy: "all_scoring_criteria_and_uniform_time_ordered_unscored_footwork_per_feature" | "all_scoring_criteria_and_uniform_time_ordered_unscored_measurements_per_feature"; max_footwork_observations_per_feature: 12; count_scope: "all_validated_input_episodes_before_display_sampling" };
  measurement_input_scope?: { source: "footwork_review"; available_event_count: number; returned_event_count: number; is_truncated: boolean; reason_zh: string };
  source_basis?: {
    version: string;
    card_ids: string[];
    card_count: number;
    grouping_origin: "proposed_source_stage_grouping";
    proposed_not_original: true;
    grouping_rule_zh: string;
    source_reference_version: string;
    source_reference_sha256: string;
    source_anchors: unknown[];
    source_blockers: unknown[];
    source_documents: unknown[];
  };
}

export interface RelativeReferenceRule {
  id: string; name_zh: string; raw_mean: number | null; score: number | null; sample_count: number;
  model_estimate_sample_count: number; measurement_weight: number; formula_zh: string; source: string;
  excluded_conflicting_episode_count: number;
}
export interface RelativeReferenceDimension {
  id: ProcessDimensionId; name_zh: string; score: number | null; basis: "observed_reference" | "unavailable" | "neutral_prior";
  observed_rule_count: number; implemented_rule_count: number; sample_count: number; rules: RelativeReferenceRule[];
  scoring_audit: RelativeScoringAudit; gap: RelativeScoringGap;
}
export interface RelativeScoringAudit {
  measured_criterion_count: number; measured_episode_count: number; scored_rule_count: number;
  scored_episode_count: number; implemented_rule_count: number; excluded_legacy_rule_ids: string[];
  excluded_conflicting_episode_count: number;
  count_scope: "validated_returned_criteria";
}
export interface RelativeScoringGap {
  type: "none" | "independent_reference_missing" | "valid_measurement_missing" | "rule_not_implemented" | "selection_biased_proxy_excluded" | "source_conflict";
  reason_zh: string;
  rule_version: "relative-reference-v3.0.0"; measurement_status: "measured" | "unavailable";
}
export interface LegacyMotionDiagnostic {
  version: "relative-reference-v1.0.0"; semantics: "selection_biased_motion_curve_diagnostic";
  is_technical_quality: false; contributes_to_current_score: false; overall_score: number;
  dimensions: Omit<RelativeReferenceDimension, "scoring_audit" | "gap">[]; observed_dimension_count: number; reason_zh: string;
}
export interface RelativeReference {
  version: "relative-reference-v3.0.0"; semantics: "observed_measurement_reference";
  overall_score: number | null; is_calibrated: false;
  aggregation: "independent_episode_mean_then_rule_mean_then_observed_dimension_mean";
  observed_dimension_count: number; total_dimension_count: 5; dimensions: RelativeReferenceDimension[];
  summary_zh: string; formula_zh: string; limitations_zh: string[];
  legacy_diagnostic: LegacyMotionDiagnostic;
  legacy_relative_reference: { version: "relative-reference-v2.0.0"; semantics: "neutral_prior_relative_reference"; is_technical_quality: false; contributes_to_current_score: false; neutral_score: 50; prior_sample_weight: 3; overall_score: number; dimensions: Omit<RelativeReferenceDimension, "scoring_audit" | "gap">[]; observed_dimension_count: number; aggregation: "independent_episode_mean_then_rule_mean_then_five_equal_dimensions"; formula_zh: string; reason_zh: string };
  recalculation: { from_version: "relative-reference-v2.0.0"; to_version: "relative-reference-v3.0.0"; source: "validated_process_measurements"; cached_scores_used: false; reason_zh: string };
}
export interface ProcessEvaluation {
  version: "process-evaluation-v1.0.0" | "process-evaluation-v1.1.0" | "process-evaluation-v1.2.0" | "process-evaluation-v2.0.0";
  semantics: "observable_motion_process_reference";
  is_calibrated: false;
  overall_score: null;
  relative_reference?: RelativeReference;
  dimensions: ProcessDimension[];
  display_dimension_ids?: ProcessDimensionId[];
  validation_issues?: string[];
}

export interface AnalysisReport {
  version: "training-report-v1.0.0";
  headline_zh: string;
  score_semantics: "measurement_evidence_quality";
  reference_score_0_to_100: number | null;
  technical_score_0_to_100: null;
  recommendation_semantics: "evidence_linked_review_and_capture_not_technical_error_diagnosis";
  focus_areas: Array<{ id: string; title_zh: string; summary_zh: string; basis_zh: string; kind: "review" | "capture"; status: "available" | "partial" | "unavailable"; start_ms: number | null; end_ms: number | null; metric_ids: string[]; is_technical_error_diagnosis: false }>;
  layers: Array<{ id: string; label_zh: string; status: string; reason_zh: string; source: string }>;
  measurement_summary: { measured_indicator_count: number; footwork_measured_feature_instances: number; footwork_missing_feature_instances: number; rotation_measured_metric_instances: number; rotation_local_window_count: number; scope: "returned_replay_episodes"; is_truncated: boolean };
}

export interface IndependentFootworkMeasurement {
  measurement_provenance?: Record<string, unknown>; source_window?: Record<string, unknown>;
  geometry_mask?: Record<string, boolean[]>; source_track_id?: number;
  source_frames?: number[];
  feature_name: string; name_zh: string; value: number | null; unit: string | null; confidence: number | null;
  status: "measured" | "unavailable"; reason_codes: string[]; reason_zh: string; review_hint_zh: string;
  window: { start_ms: number; end_ms: number; scope: "event_interval" };
  feature_version: string | null; required_joints: string[]; view_semantics: "image_plane_proxy" | "independent_target_context";
}

export interface MotionEpisode {
  episode_id: string;
  family: "baseline" | "serve" | "return";
  person_track_id?: number;
  start_ms: number;
  peak_ms: number;
  end_ms: number;
  classification: { label: string; label_zh: string; status: "rule_inferred" | "unclassified"; reason_zh: string };
  method: string;
  contact_confirmed: false;
  analysis_status?: "complete" | "partial";
  phase_timing_status?: "estimated_from_2d_motion" | string;
  candidate_peak_ms?: number;
  phases: Array<{ phase: string; label_zh: string; start_ms: number | null; end_ms: number | null; status: string }>;
  metrics: { duration_ms?: number | null; peak_wrist_speed_torso_per_s?: number | null; wrist_path_torso?: number | null; elbow_extension_deg?: number | null; shoulder_line_change_deg?: number | null; hip_line_change_deg?: number | null; shoulder_hip_separation_max_deg?: number | null; shoulder_hip_separation_change_deg?: number | null; peak_shoulder_angular_speed_deg_s?: number | null; peak_hip_angular_speed_deg_s?: number | null };
  rotation_analysis?: RotationAnalysis;
  evidence?: { pose_samples?: number; racket_associated_frames?: number; [key: string]: unknown };
  limitations_zh: string[];
  metric_notes_zh?: string[];
}

export interface RotationAnalysis {
  local_windows?: RotationWindow[];
  phase_measurements?: RotationWindow[];
  status: "measured_2d" | "partial" | "unavailable";
  is_3d_rotation: false;
  is_formal_coach_score: false;
  score: null;
  score_status: "calibration_required" | "insufficient_evidence";
  metric_evidence: Record<string, { status: string; coverage_fraction: number; valid_samples?: number; total_samples?: number; start_ms?: number | null; end_ms?: number | null; reason_zh?: string }>;
  limitations_zh?: string[];
}

export interface RotationWindow {
  window_id?: string; axis?: string; phase?: string; label_zh?: string; status?: string;
  start_ms: number; end_ms: number; metrics: MotionEpisode["metrics"]; metric_evidence: RotationAnalysis["metric_evidence"];
}

export interface FootworkReview {
  schema_version: string;
  status: "available" | "unavailable";
  reason_zh?: string;
  limitations_zh?: string[];
  episode_count?: number;
  returned_episode_count?: number;
  is_truncated?: boolean;
  episodes: Array<{
    event_id: string; event_code: string; name_zh: string; person_track_id: number; video_id?: string;
    source_id?: string;
    start_ms: number; end_ms: number;
    indicators: Array<{ indicator_id: string; name_zh: string; feature_status: string; scoring_status: string;
      measurements?: IndependentFootworkMeasurement[]; measurement_status?: "measured" | "partial" | "unavailable";
      features: Array<{ feature_name: string; name_zh?: string; value: number | null; unit: string | null; confidence: number | null;
        measurement_provenance?: Record<string, unknown>; source_window?: Record<string, unknown>; geometry_mask?: Record<string, boolean[]>; source_track_id?: number }>;
    }>;
  }>;
}

/** Independent whole-window image geometry, never an event classification or grade. */
export interface HipContextualMeasurement {
  type: "hip_path_ratio_2d";
  version: "hip-contextual-measurement-v1.0.0";
  helper_version: "bounded-hip-path-ratio-v1.0.0";
  event_id: string; event_code: "FS01" | "FS02" | "FS09";
  video_id: string; source_id: string; video_sha256: string;
  person_track_id: number;
  status: "measured" | "unavailable"; reason: string;
  hip_path_length_px: number | null; hip_net_displacement_px: number | null; hip_path_over_net_ratio: number | null;
  parent_window_ms: { start_ms: number; end_ms: number };
  measurement_window_video_ms: { start_ms: number; end_ms: number } | null;
  source_frame_bounds: { start_source_frame: number; end_source_frame: number } | null;
  source_window: Record<string, unknown> | null;
  source_bindings: Array<Record<string, unknown>>;
  source_reference: { video_sha256: string; primary_player_id: number; person_track_id: number; selection_epoch: number; camera_reference_epoch: number } | null;
  adapter: { version: "native-sam-primary-epochs-v1.0.0"; provenance: "unique_same_frame_selected_track_SAM_request_and_pose_binding_and_camera"; source_files_changed: false };
  input_sha256: { frames: string; primary_timeline: string; events: string };
  window_input_sha256: { records: string | null; original_primary: string | null; adapted_primary: string | null; event: string };
  score: null; grade: null; formal_event_verified: false; scoring_allowed: false;
  assessment_status: "measurement_only";
  coordinate_system: "stable_camera_reference_image_pixels";
  sampling_protocol: "original_source_frames";
}

export interface MotionFamily {
  status: "analyzed" | "insufficient_evidence";
  reason_zh: string;
  episodes: MotionEpisode[];
  summary?: { analyzed_count: number; classifications: Record<string, number> };
}

export interface MotionAnalysis {
  schema_version: string;
  analysis_version: string;
  method: string;
  status: "available" | "insufficient_evidence" | "unavailable";
  contact_confirmed: false;
  families: Partial<Record<"baseline" | "serve" | "return", MotionFamily>>;
  limitations_zh: string[];
}

export interface IndicatorEvaluation {
  score_explanation?: unknown;
  technical_grade?: null;
  formal_grade?: null;
  available?: boolean;
  technical_score_0_to_100?: number | null;
  component_weights?: Record<string, number>;
  effective_component_weights?: Record<string, number>;
  indicator_id: string;
  event_code?: string;
  name_zh: string;
  definition_zh?: string;
  score_0_to_100: number | null;
  level_zh?: string;
  summary_zh?: string;
  observation_zh?: string;
  suggestion_zh?: string;
  measured_instance_count?: number;
  total_instance_count?: number;
  limitations_zh?: string[];
  score_semantics?: string;
  technical_score_status?: string;
  components?: Record<string, number | null>;
  repeatability_status?: string;
  representative_measurements?: Array<{ feature_name: string; label_zh: string; median_value: number; unit_zh: string; sample_count: number; typical_range?: [number, number] }>;
}
export interface ActionEvaluation {
  family?: string;
  event_code: string;
  name_zh: string;
  detected_segments: number;
  summary_zh?: string;
  indicator_evaluations?: IndicatorEvaluation[];
  performance_assessment?: { score_0_to_100: number | null; level_zh?: string; score_semantics?: string; technical_score_0_to_100?: null; technical_grade?: null; formal_grade?: null };
}
export interface TrainingEvaluation {
  score_explanation?: unknown;
  technical_grade?: null;
  formal_grade?: null;
  available?: boolean;
  evaluated_indicator_count?: number;
  total_indicator_count?: number;
  technical_score_0_to_100?: number | null;
  component_weights?: Record<string, number>;
  action_evaluations?: unknown;
  label_zh?: string;
  meaning_zh?: string;
  score_semantics?: string;
  technical_score_status?: string;
  score_0_to_100: number | null;
  level_zh: string;
  summary_zh: string;
  strengths_zh?: string[];
  priorities_zh?: string[];
  indicator_evaluations?: IndicatorEvaluation[];
}

export interface TrajectoryPoint {
  frame_index?: number | null;
  processed_index?: number | null;
  timestamp_ms: number;
  x: number;
  y: number;
  confidence?: number;
  track_id?: number | null;
  bbox?: [number, number, number, number];
  /** Whether this point comes from detector evidence or short-gap interpolation. */
  source?: "observed" | "interpolated" | string;
}

export interface PoseDisplayAnchors {
  before_timestamp_ms: number; after_timestamp_ms: number;
  before_frame_index: number; after_frame_index: number;
  before_processed_index: number; after_processed_index: number;
  person_track_id: number; selection_epoch: number;
  before_selection_epoch?: number; after_selection_epoch?: number;
}

export interface PoseIdentityRepair {
  version: "short-target-dropout-v1.0.0";
  method: "same_target_detection_dropout";
  original_track_id: number | null; original_selection_epoch: number; target_track_id: number;
  dropout_kind?: "pose_unavailable";
  before_frame_index: number; after_frame_index: number;
  before_timestamp_ms: number; after_timestamp_ms: number;
  before_selection_epoch: number; after_selection_epoch: number;
  primary_player_id: number;
}

export interface PoseModelSourceBinding {
  video_sha256: string; frame_index: number; processed_index: number;
  pts: number; time_base_num: number; time_base_den: number; timestamp_ms: number;
  person_track_id: number; selection_epoch: number; camera_reference_epoch: number;
}

export interface PosePreviewFrame {
  frame_index: number;
  processed_index?: number;
  source_timestamp_ms?: number;
  timestamp_ms: number;
  valid_until_ms: number;
  person_track_id: number | null;
  selection_epoch: number;
  selection_status: "selected" | "single_visible_person" | "unavailable" | "display_interpolated";
  width?: number | null;
  height?: number | null;
  pose_source?: "sam3d";
  source_keypoint_format?: "mhr70";
  observation_semantics?: "monocular_model_estimate";
  source_binding?: PoseModelSourceBinding;
  keypoints: Array<{ name: string; x: number; y: number; confidence: number | null; source?: "observed" | "interpolated" | "model_estimate"; anchors?: PoseDisplayAnchors;
    score_semantics?: "rtmpose_simcc_response_clipped_for_display"; raw_confidence?: number;
    confidence_semantics?: "not_provided_by_sam3d"; observation_semantics?: "model_estimate";
    in_frame?: boolean; raw_x_px?: number; raw_y_px?: number; native_index?: number;
  }>;
  observed_person_track_id?: number | null;
  observed_selection_epoch?: number;
  identity_repair?: PoseIdentityRepair;
}

export interface PosePreviewResponse {
  schema_version: "1.0.0";
  result_kind: "observed_pose_playback";
  job_id: string;
  status?: string;
  reason_zh?: string;
  coordinate_space: "normalized_frame_0_1";
  joint_schema: "halpe26_named" | "mhr70_named";
  min_keypoint_confidence?: number;
  skeleton_edges: Array<[string, string]>;
  source: {
    requested_start_ms: number;
    requested_end_ms: number;
    width?: number | null;
    height?: number | null;
    returned_frames: number;
    is_sampled: boolean;
    is_partial: boolean;
    timestamp_semantics: "source_frame_timestamp_ms";
    timestamp_rounding_tolerance_ms?: number;
    selection_source?: string;
  };
  frames: PosePreviewFrame[];
  display_frames?: PosePreviewFrame[];
  display_continuity?: {
    version: "short-joint-gap-v1.0.0"; max_gap_ms: number;
    interpolated_keypoints: number; affected_frames: number;
    confidence_semantics: "minimum_anchor_confidence_not_model_estimate";
    used_for_scoring: false;
    response_score_recovered_keypoints?: number;
    identity_repair_version?: "short-target-dropout-v1.0.0";
    repaired_identity_frames?: number;
  };
}

export interface BallReconstructionAnalysis {
  duration_ms: number;
  displacement_normalized: number;
  path_length_normalized: number;
  mean_speed_normalized_per_s: number | null;
  direction_image_deg?: number | null;
  motion_status?: "moving" | "stationary" | "insufficient";
  [key: string]: unknown;
}

export interface BallReconstructionSegment {
  segment_id: string | number;
  track_ids: number[];
  start_ms: number;
  end_ms: number;
  observed_count: number;
  interpolated_count: number;
  points: TrajectoryPoint[];
  analysis?: BallReconstructionAnalysis;
  interpolation_intervals?: Array<{ start_ms: number; end_ms: number; method: string }>;
  interpolation_intervals_complete?: boolean;
  sampling?: { method: string; is_sampled: boolean; original_point_count: number; returned_point_count: number };
  display_quality?: {
    semantics: "display_support_not_accuracy_or_technical_score";
    detector_confidence: { mean: number | null; median: number | null; min: number | null; max: number | null };
    observed_point_fraction: number;
    tracked_observation_fraction: number;
    active_ball_identity: "unconfirmed";
  };
}

export interface BallTrajectoryReconstruction {
  version: string;
  status: "ready" | "partial" | "not_available" | "not_observed" | string;
  coordinate_space: "normalized_frame_0_1" | string;
  segments: BallReconstructionSegment[];
  summary: {
    segment_count: number;
    observed_count: number;
    observed_frame_count?: number;
    returned_segment_count?: number;
    returned_point_count?: number;
    confidence?: { mean: number | null; median: number | null; min: number | null; max: number | null };
    interpolated_count: number;
    coverage_fraction: number;
    rejected_observation_count?: number;
    [key: string]: unknown;
  };
  limitations_zh: string[];
}

export interface TrajectoryTrack {
  track_id: number | null;
  track_id_status: string;
  track_count: number;
  observed_count: number;
  coverage_fraction: number;
  confidence: {
    mean: number | null;
    median: number | null;
    min: number | null;
    max: number | null;
  };
  observed: TrajectoryPoint[];
  [key: string]: unknown;
}

export interface TrajectoryPreviewResponse {
  court_projection?: {
    status: "calibrated" | "unavailable";
    coordinate_space: "court_normalized";
    semantics: "ground_plane_projection_not_ball_landing";
    reason_zh: string;
    points: Array<{ x: number; y: number; timestamp_ms: number; source: "observed" | "interpolated"; segment_id: string; track_id?: number }>;
  };
  schema_version: string;
  result_kind: string;
  job_id?: string;
  status: "ready" | "not_observed" | string;
  source: {
    frames_path: string;
    frame_count: number;
    width: number | null;
    height: number | null;
    coordinate_space: string;
    timebase: string;
    court_calibration_windows?: Array<{ start_timestamp_ms: number; end_timestamp_ms: number }>;
    is_partial?: boolean;
    start_timestamp_ms?: number;
    end_timestamp_ms?: number;
    [key: string]: unknown;
  };
  ball: TrajectoryTrack & {
    prediction_status: "heuristic_preview" | "not_available" | "not_requested" | string;
    prediction_reason: string | null;
    prediction_horizon_ms: number;
    predicted_covered_horizon_ms: number;
    predicted: TrajectoryPoint[];
    velocity: {
      direction_image_deg?: number;
      segment_count?: number;
      [key: string]: unknown;
    } | null;
    semantics: string;
    reconstruction?: BallTrajectoryReconstruction | null;
  };
  racket: TrajectoryTrack & {
    geometry_status: "bbox_only" | string;
    association_status: "unassociated";
    association_reason: string;
    recognition_status: "generic_bbox_detection" | "not_observed" | string;
    keypoint_status: string;
    prediction_status: string;
    prediction_reason: string;
    semantics: string;
  };
  limitations: string[];
}

export interface TechniqueAssessmentItem {
  technique_id: string;
  family: string;
  family_name_zh: string;
  name_zh: string;
  status: "not_observed" | "unavailable" | "partial" | "ready" | string;
  observed: boolean;
  recognition_status?: "observed" | "candidate" | "not_observed";
  evidence_score_0_to_100: number;
  score_0_to_100: number | null;
  formal_grade: string | null;
  phase_statuses: Array<{ phase: string; status: string; evidence_source?: string | null }>;
  evidence: {
    required_coverage: Record<string, number>;
    enhanced_coverage: Record<string, number>;
    contact_status: string;
    contact_policy_version: string;
    event_codes: string[];
  };
  /** Per-field evidence emitted by the inference analyzer. */
  key_field_analysis?: {
    required_fields: Array<{ field: string; field_label_zh?: string; required: boolean; coverage_percent: number; status: string }>;
    enhanced_fields: Array<{ field: string; field_label_zh?: string; required: boolean; coverage_percent: number; status: string }>;
    missing_required_fields: string[];
  };
  core_visual_features: string[];
  reference_constraints: Array<Record<string, unknown>>;
  limitations_zh: string[];
  semantics: string;
}

export interface TechniqueAssessmentResponse {
  assessment_version: string;
  registry_version: string;
  job_id?: string;
  overall_evidence_score_0_to_100: number | null;
  formal_score_available: boolean;
  formal_score_message_zh: string;
  coverage: Record<string, number>;
  coverage_detail: Record<string, {
    fraction: number;
    percent: number;
    source: string;
    field: string | null;
    scope: string;
    source_kind?: string;
    fallback_used: boolean;
  }>;
  coverage_source: Record<string, string>;
  family_summary: Record<string, {
    name_zh: string;
    observed_count: number;
    candidate_count?: number;
    recognition_status?: "observed" | "motion_analyzed" | "motion_unavailable" | "motion_detected_unclassified" | "candidate_only" | "analysis_unavailable" | "classifier_unavailable" | "not_observed";
    recognition_reason_zh?: string;
    total_count: number;
    evidence_score_0_to_100: number | null;
  }>;
  techniques: TechniqueAssessmentItem[];
  registry_snapshot: {
    status: "verified" | "legacy_unpinned";
    expected: Record<string, unknown> | null;
    actual: Record<string, unknown>;
  };
  policy: Record<string, unknown>;
  safety: Record<string, boolean>;
  [key: string]: unknown;
}

export interface EvidenceFrame {
  frame_url?: string;
  timestamp_ms: number;
  labels?: string[];
  source?: string;
  is_demo?: boolean;
}

export interface BallTrajectory {
  points: Array<{ x: number; y: number; z?: number; timestamp_ms?: number }>;
  predicted_direction?: string;
  confidence?: number;
  source?: string;
  is_demo?: boolean;
}

export interface RacketSignal {
  status: "detected" | "partial" | "unavailable" | string;
  keypoints?: Array<{ name: string; x: number; y: number; confidence?: number }>;
  confidence?: number;
  source?: string;
  is_demo?: boolean;
}

export interface GripSignal {
  status: "confirmed" | "candidate" | "unavailable" | string;
  candidate?: string;
  confidence?: number;
  source?: string;
  is_demo?: boolean;
}

export interface SubmitVideoOptions {
  courtMode?: "auto" | "manual" | "disabled";
  onUploadProgress?: (progress: import("./resumable-upload").UploadProgress) => void;
  writeAnnotatedVideo?: boolean;
  signal?: AbortSignal;
  metadata?: Record<string, string>;
}

export interface RallyMateApiConfig {
  /** API origin, for example https://autodl.example.com. Empty means same origin. */
  baseUrl: string;
  scorecardPath: string;
  jobsPath: string;
  techniquesPath: string;
}

export class RallyMateApiError extends Error {
  readonly status: number;
  readonly payload: unknown;

  constructor(message: string, status: number, payload?: unknown, options?: ErrorOptions) {
    super(message, options);
    this.name = "RallyMateApiError";
    this.status = status;
    this.payload = payload;
  }
}
