export type SpecialistMotion = { version: "specialist-motion-assessment-v1.0.0"; family: string; status: string;
  replace_metrics: string[]; context_profile: Record<string, unknown>; evidence: Record<string, unknown>; reason?: string };
type Scope = { sourceVideoSha256: string; subjectId: string; personTrackId: number; selectionEpoch?: unknown;
  cameraEpoch?: unknown; startMs: number; endMs: number; family: string; sourceFrames?: number[];
  metrics: Record<string, { value: number; score: number }> };
const object = (value: unknown): Record<string, unknown> | null => value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;
const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const nonnegative = (value: unknown): value is number => finite(value) && value >= 0;
const count = (value: unknown): value is number => nonnegative(value) && Number.isSafeInteger(value);
const clip = (v: number) => Math.min(100, Math.max(0, v));
const ids = ["specialist_compact_preparation", "specialist_direct_drive", "specialist_short_follow_through", "specialist_terminal_control"];
export const hasSpecialistMetrics = (metrics: Scope["metrics"]) => Object.keys(metrics).some(key => key.startsWith("specialist_"));

function smallJson(value: unknown, depth = 0): boolean {
  if (depth > 5) return false;
  if (value === null || typeof value === "boolean" || finite(value)) return true;
  if (typeof value === "string") return value.length <= 200;
  if (Array.isArray(value)) return value.length <= 20 && value.every(v => smallJson(v, depth + 1));
  const row = object(value);
  return row !== null && Object.keys(row).length <= 20 && Object.entries(row).every(([key,v]) => /^[a-z_]{1,100}$/.test(key) && smallJson(v, depth + 1));
}

/** Validate optional provenance, including every score that depends on it. */
export function specialistMotionOf(value: unknown, scope: Scope): SpecialistMotion | null {
  const row = object(value), evidence = object(row?.evidence), profile = object(row?.context_profile);
  if (!row || row.version !== "specialist-motion-assessment-v1.0.0" || !["scored","not_applicable","unavailable"].includes(String(row.status)) ||
      !["baseline","serve","return","volley","defense","overhead"].includes(String(row.family)) || !Array.isArray(row.replace_metrics) ||
      !row.replace_metrics.every(key => typeof key === "string" && /^[a-z_]{1,100}$/.test(key)) || new Set(row.replace_metrics).size !== row.replace_metrics.length ||
      !evidence || !profile || !smallJson(profile) || row.reason !== undefined && (typeof row.reason !== "string" || row.reason.length > 200)) return null;
  const used = Object.keys(scope.metrics).filter(key=>key.startsWith("specialist_"));
  if (used.some(key=>!ids.includes(key)) || used.length > 0 && row.status !== "scored" || row.status === "scored" && used.length === 0) return null;
  if (evidence.source_video_sha256 !== scope.sourceVideoSha256 || evidence.subject_id !== scope.subjectId || evidence.person_track_id !== scope.personTrackId ||
      evidence.source_selection_epoch !== scope.selectionEpoch || evidence.camera_reference_epoch !== scope.cameraEpoch ||
      evidence.start_ms !== scope.startMs || evidence.end_ms !== scope.endMs || evidence.native_source_verified !== true ||
      !["left","right"].includes(String(evidence.active_wrist)) || evidence.coordinate_space !== "hip_centered_torso_normalized_body_basis" ||
      evidence.camera_reference_required_for_local_paths !== false || !Array.isArray(evidence.source_frames) || evidence.source_frames.length < 4 ||
      !evidence.source_frames.every(count) || evidence.source_frames.some((frame,i,frames)=>i>0 && frame !== frames[i-1]+1) ||
      scope.sourceFrames?.some(frame=>!(evidence.source_frames as number[]).includes(frame))) return null;
  if (typeof evidence.family_basis !== "string" || !/^[a-z_]{1,150}$/.test(evidence.family_basis) ||
      evidence.path_scope !== "observed_native_motion_phases_not_complete_recovery") return null;
  if (used.length > 0) {
    if (!["return","volley","defense"].includes(String(row.family))) return null;
    const basis = row.family === "return" ? "source_bound_opponent_serve_and_incoming_ball" : row.family === "volley" ? "bound_court_net_position_and_contact_bounce_order" : "bound_incoming_ball_time_pressure_and_body_proximity";
    if (evidence.family_basis !== basis || row.family === "return" && scope.family !== "return") return null;
    const bounds = object(evidence.phase_frame_bounds), frames = evidence.source_frames as number[];
    for (const phase of ["preparation","striking","follow_through"]) {
      const pair = bounds?.[phase];
      if (!Array.isArray(pair) || pair.length !== 2 || !pair.every(count) || pair[0] >= pair[1] || !pair.every(f=>frames.includes(f))) return null;
    }
    const preparation = bounds!.preparation as number[], striking = bounds!.striking as number[], follow = bounds!.follow_through as number[];
    if (preparation[0] !== frames[0] || preparation[1] !== striking[0] || striking[1] !== follow[0] || follow[1] !== frames.at(-1)) return null;
    const path = evidence.striking_path_torso;
    if (!nonnegative(path) || path <= .02) return null;
    const expectedReplaced = new Set<string>();
    for (const key of used) {
      const metric = scope.metrics[key]; let value: number, score: number;
      if (key === "specialist_compact_preparation" || key === "specialist_short_follow_through") {
        const prep = key === "specialist_compact_preparation", length = evidence[prep ? "preparation_path_torso" : "follow_through_path_torso"];
        if (!nonnegative(length)) return null;
        value = length/path; score = clip(100*((prep?1.8:2)-value)/(prep?1.15:1.15));
        expectedReplaced.add(prep ? "coil" : "follow_path");
      } else if (key === "specialist_direct_drive") {
        const net = evidence.striking_net_displacement_torso;
        if (!nonnegative(net) || net > path + .000001) return null;
        value = Math.min(1,net/path); score = clip((value-.4)/.5*100);
        ["coil_release","release_sequence","pattern_projected_sequence"].forEach(k=>expectedReplaced.add(k));
      } else {
        const ratio = evidence.terminal_to_peak_speed_ratio;
        if (!nonnegative(ratio) || evidence.recovery_end_verified !== true) return null;
        value=ratio; score=clip(100*(1-ratio)/.8);
      }
      if (Math.abs(metric.value-value) > .000011 || Math.abs(metric.score-score) > .011) return null;
    }
    if (row.replace_metrics.length !== expectedReplaced.size || row.replace_metrics.some(key=>!expectedReplaced.has(key) || key in scope.metrics)) return null;
  } else if (row.replace_metrics.length) return null;
  // Source frame arrays are bounded by episode geometry. Remaining evidence
  // consists of numeric paths and the finite, small provenance fields above.
  for (const [key,v] of Object.entries(evidence)) {
    if (key === "source_frames") continue;
    if (!smallJson(v)) return null;
  }
  return row as unknown as SpecialistMotion;
}

export function specialistRubricOf(value: unknown): Record<string, unknown> | null {
  const row = object(value);
  if (row?.version !== "specialist-motion-assessment-v1.0.0" || !smallJson(row) || row.coordinate_space !== "hip_centered_torso_normalized_body_basis" ||
      row.active_striking_path_minimum_torso !== .02 || row.camera_observation_required_for_native_paths !== false || row.short_swing_alone_classifies_defense_or_volley !== false) return null;
  for (const [key,low,high] of [["preparation_path_ratio",.65,1.8],["follow_path_ratio",.85,2]] as const) {
    const curve=object(row[key]);
    if (!curve || Object.keys(curve).length !== 2 || curve.full_score_at_or_below !== low || curve.zero_score_at_or_above !== high) return null;
  }
  return row;
}
