export type ModalityAssessment = {
  version: "observed-modalities-v1.0.0"; source_video_sha256: string; frame_count: number;
  summary: { ball_observed_frames: number; racket_observed_frames: number; court_calibrated_frames: number; confirmed_landing_count: number };
  per_episode: Record<string, unknown>[]; tactics: Record<string, unknown>; limitations: string[]; missing_modality_disables_pose_score: false;
};
const object = (value: unknown): Record<string, unknown> | null => value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;
const count = (v: unknown): v is number => typeof v === "number" && Number.isSafeInteger(v) && v >= 0;
export function modalityAssessmentOf(value: unknown, sha: string, episodes: { episode_id: string; start_ms: number; end_ms: number }[]): ModalityAssessment | null {
  const row = object(value), summary = object(row?.summary);
  if (!row || row.version !== "observed-modalities-v1.0.0" || row.source_video_sha256 !== sha || row.missing_modality_disables_pose_score !== false
    || !count(row.frame_count) || !summary || !Array.isArray(row.per_episode) || row.per_episode.length > episodes.length
    || !Array.isArray(row.limitations) || !row.limitations.every(x => typeof x === "string" && x.length < 1000) || !object(row.tactics)) return null;
  for (const name of ["ball_observed_frames", "racket_observed_frames", "court_calibrated_frames", "confirmed_landing_count"]) {
    if (!count(summary[name]) || summary[name] > row.frame_count) return null;
  }
  const ids = new Set();
  for (const raw of row.per_episode) {
    const item = object(raw), episode = episodes.find(e => e.episode_id === item?.episode_id), ball = object(item?.ball), racket = object(item?.racket), court = object(item?.court);
    if (!item || !episode || ids.has(episode.episode_id) || item.start_ms !== episode.start_ms || item.end_ms !== episode.end_ms
      || !ball || !racket || !court || !count(ball.total_frame_count) || !count(ball.observed_frame_count) || ball.observed_frame_count > ball.total_frame_count
      || !count(racket.observed_frame_count) || racket.observed_frame_count > ball.total_frame_count
      || !count(court.calibrated_frame_count) || court.calibrated_frame_count > ball.total_frame_count
      || ball.physical_speed_m_s !== null || ball.spin_rpm !== null || ball.speed_unit !== "frame_long_edge_per_second") return null;
    ids.add(episode.episode_id);
  }
  // Preserve measured source samples for export after bounding the JSON tree.
  let budget = 1000000;
  function finiteJson(v: unknown, depth = 0): boolean {
    if (--budget < 0 || depth > 14) return false;
    if (v === null || typeof v === "boolean") return true;
    if (typeof v === "number") return Number.isFinite(v);
    if (typeof v === "string") return v.length <= 4096;
    if (Array.isArray(v)) return v.every(x => finiteJson(x, depth + 1));
    const record = object(v);
    return record !== null && Object.entries(record).every(([key, x]) => key.length < 160 && finiteJson(x, depth + 1));
  }
  return finiteJson(row) ? row as unknown as ModalityAssessment : null;
}
