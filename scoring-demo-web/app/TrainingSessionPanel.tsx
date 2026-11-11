"use client";

import { trainingSessionAnalysisOf } from "./lib/training-session-analysis";
import "./training-session-analysis.css";

const angles: Record<string,string> = { left_knee_flexion_deg:"左膝屈曲",right_knee_flexion_deg:"右膝屈曲",left_elbow_flexion_deg:"左肘屈曲",right_elbow_flexion_deg:"右肘屈曲",left_shoulder_elevation_deg:"左肩抬举",right_shoulder_elevation_deg:"右肩抬举",shoulder_hip_separation_deg:"肩髋分离" };
const time = (ms:number) => `${Math.floor(ms/60000)}:${(ms/1000%60).toFixed(1).padStart(4,"0")}`;

export default function TrainingSessionPanel({ analysis, sourceRunId, sourceVideoSha256, subjectId, onSeek }: {
  analysis?: unknown; sourceRunId?: unknown; sourceVideoSha256?: unknown; subjectId?: unknown; onSeek?: (timeMs:number)=>void;
}) {
  const session = trainingSessionAnalysisOf(analysis, { source_run_id:sourceRunId,source_video_sha256:sourceVideoSha256,subject_id:subjectId });
  if (!session) return null;
  const clips = session.training.active_clips, ranges = Object.entries(session.movement_ranges.metrics);
  if (!clips.length && !ranges.length && !session.match.confirmed_point_count) return null;
  const rates = [["一发得分率",session.match.first_serve_points_won],["二发得分率",session.match.second_serve_points_won],["保发率",session.match.hold_rate],["破发率",session.match.break_rate]] as const;
  const renderClip = (clip:typeof clips[number], index:number) => <li key={`${clip.start_ms}-${clip.camera_reference_epoch}-${index}`}>
    <span>动作片段 {index+1}<small>{time(clip.start_ms)}–{time(clip.end_ms)}</small></span>
    {onSeek ? <button type="button" onClick={()=>onSeek(clip.start_ms)}>回看</button> : <span>{((clip.end_ms-clip.start_ms)/1000).toFixed(1)} 秒</span>}
  </li>;
  return <section className="training-session-analysis" aria-label="训练片段与活动角度">
    <h3>训练片段与活动角度</h3>
    {clips.length > 0 && <><p>已整理 {clips.length} 个动作片段，共 {(session.training.active_duration_ms/1000).toFixed(1)} 秒。</p>
      <ol className="training-session-clips">{clips.slice(0,4).map(renderClip)}</ol>
      {clips.length > 4 && <details><summary>查看其余 {clips.length-4} 个片段</summary><ol className="training-session-clips" start={5}>{clips.slice(4).map((clip,index)=>renderClip(clip,index+4))}</ol></details>}
    </>}
    {ranges.length > 0 && <details><summary>查看当前动作中的关节角度</summary>
      <div className="training-session-ranges">{ranges.map(([name,r])=><div key={name}><span>{angles[name]??name}</span><strong>{r.observed_min !== null && r.observed_max !== null ? `${r.observed_min.toFixed(1)}°–${r.observed_max.toFixed(1)}°` : `典型 ${r.median_episode_p10.toFixed(1)}°–${r.median_episode_p90.toFixed(1)}°`}</strong><small>{r.episode_count} 个动作 · 每次活动幅度中位数 {r.median_episode_range.toFixed(1)}°</small></div>)}</div>
      <p className="training-session-note">角度来自视频人体模型，反映本次动作中的活动范围。</p>
    </details>}
    {session.match.confirmed_point_count > 0 && <details><summary>比赛记录 · {session.match.confirmed_point_count} 个已确认得分点</summary>
      <div className="training-session-ranges">{rates.filter(([,r])=>r.percent!==null).map(([name,r])=><div key={name}><span>{name}</span><strong>{r.percent!.toFixed(1)}%</strong><small>{r.numerator} / {r.denominator}</small></div>)}
        {session.match.aces.count !== null && <div><span>Ace</span><strong>{session.match.aces.count}</strong></div>}
        {session.match.return_aces.count !== null && <div><span>接发直接得分</span><strong>{session.match.return_aces.count}</strong></div>}
        {session.match.unforced_errors.count !== null && <div><span>非受迫性失误</span><strong>{session.match.unforced_errors.count}</strong></div>}
      </div>
    </details>}
  </section>;
}
