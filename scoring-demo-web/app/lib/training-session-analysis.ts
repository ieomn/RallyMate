type Clip = { start_ms: number; end_ms: number; episode_ids: string[]; source_frames: number[]; source_video_sha256: string; subject_id: string; source_track_id: number; source_selection_epoch: number; camera_reference_epoch: number };
type Rate = { numerator: number; denominator: number; percent: number | null };
type Range = { unit: "deg"; episode_count: number; observed_min: number | null; observed_max: number | null; median_episode_p10: number; median_episode_p90: number; median_episode_range: number; episodes: unknown[] };
export type TrainingSessionAnalysis = {
  version: "training-session-analysis-v1.0.0"; source_run_id: string | null; source_video_sha256: string; subject_id: string;
  training: { status: string; active_clips: Clip[]; active_duration_ms: number; source_duration_ms: number | null; unselected_intervals: { start_ms: number; end_ms: number }[]; interval_semantics: string; rest_intervals_status: string; rejected_episode_reasons: Record<string, number> };
  match: { status: string; confirmed_point_count: number; unknown_outcome_count: number; first_serve_points_won: Rate; second_serve_points_won: Rate; hold_rate: Rate; break_rate: Rate;
    aces: { count: number | null; classified_service_points: number }; return_aces: Rate & { count: number | null; classified_return_points: number }; unforced_errors: { count: number | null; classified_points: number } };
  movement_ranges: { status: string; metrics: Record<string, Range>; semantics: string; quantile_semantics: string };
  training_match_comparison: { status: string; metrics: unknown[]; psychological_score: null; [key: string]: unknown };
  ideal_template: Record<string, unknown>;
};

const object = (v: unknown): Record<string, unknown> => v !== null && typeof v === "object" && !Array.isArray(v) ? v as Record<string, unknown> : {};
const finite = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
const count = (v: unknown): v is number => finite(v) && Number.isInteger(v) && v >= 0;
const text = (v: unknown): v is string => typeof v === "string" && v.length > 0;
const sha = (v: unknown) => text(v) && /^[a-f0-9]{64}$/i.test(v);
const close = (a: number, b: number) => Math.abs(a-b) < .0002;
const median = (values:number[]) => { const sorted=[...values].sort((a,b)=>a-b), i=Math.floor(sorted.length/2); return sorted.length%2 ? sorted[i] : (sorted[i-1]+sorted[i])/2; };
const windowValid = (r: Record<string, unknown>, duration: number | null) => finite(r.start_ms) && finite(r.end_ms) && r.start_ms >= 0 && r.end_ms > r.start_ms && (duration === null || r.end_ms <= duration);
const rateValid = (raw: unknown) => {
  const r = object(raw);
  return count(r.numerator) && count(r.denominator) && r.numerator <= r.denominator &&
    (r.denominator === 0 ? r.percent === null : finite(r.percent) && close(r.percent,100*r.numerator/r.denominator));
};

export function trainingSessionAnalysisOf(value: unknown, expected: {
  source_run_id?: unknown; source_video_sha256?: unknown; subject_id?: unknown; source_duration_ms?: unknown;
} = {}): TrainingSessionAnalysis | null {
  const row = object(value), t = object(row.training), m = object(row.match), rom = object(row.movement_ranges);
  if (row.version !== "training-session-analysis-v1.0.0" || !sha(row.source_video_sha256) || !text(row.subject_id) ||
      row.source_run_id !== null && !text(row.source_run_id)) return null;
  for (const key of ["source_run_id", "source_video_sha256", "subject_id"] as const) if (expected[key] !== undefined && row[key] !== expected[key]) return null;
  if (t.source_duration_ms !== null && (!finite(t.source_duration_ms) || t.source_duration_ms <= 0) ||
      expected.source_duration_ms !== undefined && t.source_duration_ms !== expected.source_duration_ms) return null;
  const duration = t.source_duration_ms as number | null;
  if (!Array.isArray(t.active_clips) || !Array.isArray(t.unselected_intervals) || !finite(t.active_duration_ms) || t.active_duration_ms < 0) return null;
  const intervals: number[][] = [];
  for (const raw of t.active_clips) {
    const c = object(raw);
    if (!windowValid(c,duration) || c.source_video_sha256 !== row.source_video_sha256 || c.subject_id !== row.subject_id ||
        !count(c.source_track_id) || !count(c.source_selection_epoch) || !count(c.camera_reference_epoch) ||
        !Array.isArray(c.source_frames) || !c.source_frames.length || !c.source_frames.every(count) || c.source_frames.some((f, i, frames) => i > 0 && f <= frames[i-1]) ||
        !Array.isArray(c.episode_ids) || !c.episode_ids.length || !c.episode_ids.every(text)) return null;
    intervals.push([c.start_ms as number,c.end_ms as number]);
  }
  const union: number[][] = [];
  for (const interval of intervals.sort((a,b) => a[0]-b[0])) {
    const last = union.at(-1);
    if (last && interval[0] <= last[1]) last[1] = Math.max(last[1],interval[1]); else union.push([...interval]);
  }
  if (!close(t.active_duration_ms,union.reduce((sum,[a,b])=>sum+b-a,0))) return null;
  const expectedRest: number[][] = [];
  if (duration !== null && union.length) {
    let cursor = 0;
    for (const [start,end] of union) { if (start > cursor) expectedRest.push([cursor,start]); cursor = end; }
    if (cursor < duration) expectedRest.push([cursor,duration]);
  }
  if (t.unselected_intervals.length !== expectedRest.length || t.unselected_intervals.some((raw,i) => {
    const w = object(raw); return !windowValid(w,duration) || !close(Number(w.start_ms),expectedRest[i][0]) || !close(Number(w.end_ms),expectedRest[i][1]);
  })) return null;
  if (!count(m.confirmed_point_count) || !count(m.unknown_outcome_count) || m.unknown_outcome_count > m.confirmed_point_count ||
      !["first_serve_points_won","second_serve_points_won","hold_rate","break_rate","return_aces"].every(key=>rateValid(m[key]))) return null;
  for (const [key,denominator] of [["aces","classified_service_points"],["return_aces","classified_return_points"],["unforced_errors","classified_points"]]) {
    const c = object(m[key]);
    if (!count(c[denominator]) || Number(c[denominator]) > m.confirmed_point_count || (c[denominator] === 0 ? c.count !== null : !count(c.count) || c.count > Number(c[denominator]))) return null;
  }
  const returnedAces = object(m.return_aces);
  if (returnedAces.count !== null && returnedAces.count !== returnedAces.numerator || returnedAces.denominator !== returnedAces.classified_return_points) return null;
  for (const raw of Object.values(object(rom.metrics))) {
    const r = object(raw);
    if (r.unit !== "deg" || !count(r.episode_count) || r.episode_count < 1 || !Array.isArray(r.episodes) || r.episodes.length !== r.episode_count ||
        !finite(r.median_episode_p10) || !finite(r.median_episode_p90) || r.median_episode_p90 < r.median_episode_p10 || !finite(r.median_episode_range) || r.median_episode_range < 0 ||
        r.observed_min !== null && !finite(r.observed_min) || r.observed_max !== null && !finite(r.observed_max) ||
        finite(r.observed_min) && finite(r.observed_max) && r.observed_max < r.observed_min) return null;
    const episodes = r.episodes.map(object), lows:number[] = [], highs:number[] = [];
    for (const e of episodes) {
      if (!text(e.episode_id) || !windowValid(e,duration) || !finite(e.p10) || !finite(e.p90) || e.p90 < e.p10 || !finite(e.range) || e.range < 0 || !count(e.observation_count) || e.observation_count < 1 ||
          !Array.isArray(e.source_frames) || !e.source_frames.length || !e.source_frames.every(count)) return null;
      if (!t.active_clips.some(raw=>{const c=object(raw); return (c.episode_ids as string[]).includes(e.episode_id as string) && Number(c.start_ms)<=Number(e.start_ms) && Number(c.end_ms)>=Number(e.end_ms) && (e.source_frames as number[]).every(f=>(c.source_frames as number[]).includes(f));})) return null;
      if (e.min !== null && e.min !== undefined || e.max !== null && e.max !== undefined) {
        if (!finite(e.min) || !finite(e.max) || e.max < e.min || !close(e.range,e.max-e.min) || e.p10 < e.min || e.p90 > e.max) return null;
        lows.push(e.min); highs.push(e.max);
      }
    }
    if (!close(r.median_episode_p10,median(episodes.map(e=>Number(e.p10)))) || !close(r.median_episode_p90,median(episodes.map(e=>Number(e.p90)))) || !close(r.median_episode_range,median(episodes.map(e=>Number(e.range)))) ||
        (lows.length ? !close(Number(r.observed_min),Math.min(...lows)) || !close(Number(r.observed_max),Math.max(...highs)) : r.observed_min !== null || r.observed_max !== null)) return null;
  }
  const comparison = object(row.training_match_comparison);
  if (comparison.psychological_score !== null || !Array.isArray(comparison.metrics)) return null;
  return row as unknown as TrainingSessionAnalysis;
}
