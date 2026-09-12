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
  resultCount: number;
  results: ScorecardResult[];
}

export interface ScorecardCapabilities {
  service: string;
  apiVersion: string;
  registryVersion: string;
  indicatorCount: number;
  scenarios: Array<{
    id: string;
    label: string;
    mode: "demo" | "real";
    description: string;
  }>;
  usage?: string;
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
  job_id?: string;
  video_id?: string;
  status?: string;
  summary?: Record<string, unknown>;
  scorecard?: ScorecardResponse;
  artifacts?: { evidence_frames?: EvidenceFrame[]; [key: string]: unknown };
  features?: { ball?: { trajectory?: BallTrajectory }; [key: string]: unknown };
  signals?: { racket?: RacketSignal; grip?: GripSignal; [key: string]: unknown };
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
  courtMode?: "auto" | "manual";
  writeAnnotatedVideo?: boolean;
  signal?: AbortSignal;
  metadata?: Record<string, string>;
}

export interface RallyMateApiConfig {
  /** API origin, for example https://autodl.example.com. Empty means same origin. */
  baseUrl: string;
  scorecardPath: string;
  jobsPath: string;
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
