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

/** Shape used by the production /v1/jobs endpoint. Unknown additions are kept. */
export interface JobSubmission {
  id: string;
  status: JobStatus | string;
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

export interface DemoResultResponse {
  footwork_review?: FootworkReview;
  action_recognition?: { motion_analysis?: MotionAnalysis; [key: string]: unknown };
  training_evaluation?: TrainingEvaluation;
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
  status: "measured_2d" | "partial" | "unavailable";
  is_3d_rotation: false;
  is_formal_coach_score: false;
  score: null;
  score_status: "calibration_required" | "insufficient_evidence";
  metric_evidence: Record<string, { status: string; coverage_fraction: number; valid_samples?: number; total_samples?: number; start_ms?: number | null; end_ms?: number | null; reason_zh?: string }>;
  limitations_zh?: string[];
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
    event_id: string; event_code: string; name_zh: string; person_track_id: number;
    start_ms: number; end_ms: number;
    indicators: Array<{ indicator_id: string; name_zh: string; feature_status: string; scoring_status: string;
      features: Array<{ feature_name: string; name_zh?: string; value: number | null; unit: string | null; confidence: number | null }>;
    }>;
  }>;
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

  constructor(message: string, status: number, payload?: unknown) {
    super(message);
    this.name = "RallyMateApiError";
    this.status = status;
    this.payload = payload;
  }
}
