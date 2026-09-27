import type { TrajectoryPreviewResponse } from "./lib/api-types";
import { trajectoryAvailability } from "./lib/trajectory-viewer";

export default function BallTrajectorySummary({ trajectory, pending = false, error }: { trajectory: TrajectoryPreviewResponse | null; pending?: boolean; error?: string | null }) {
  const reconstruction = trajectory?.ball.reconstruction;
  const availability = trajectoryAvailability(trajectory, pending, error);
  if (!reconstruction) return <>
    <div className="frame-toolbar"><span>球路信息</span><span>{availability.title}</span></div>
    <p className="trajectory-disclosure">{availability.description}</p>
  </>;
  const summary = reconstruction?.summary;
  const segments = reconstruction?.segments ?? [];
  const moving = segments.filter(item => item.analysis?.motion_status === "moving");
  const stationary = segments.filter(item => item.analysis?.motion_status === "stationary");
  const start = trajectory?.source.start_timestamp_ms ?? 0;
  const end = trajectory?.source.end_timestamp_ms ?? Math.max(start, ...segments.map(item => item.end_ms));
  const duration = Math.max(1, end - start);
  return <>
    <div className="frame-toolbar"><span>球路信息</span><span>{availability.title}</span></div>
    <p className="trajectory-disclosure">{availability.description}</p>
    <p className="trajectory-disclosure">球路片段、观测点和插值点均不作为击球次数；未确认触球时不显示击球计数。</p>
    <details className="trajectory-observation-details"><summary>查看球路观测详情</summary>
    <div className="trajectory-summary-grid">
      <div><strong>{summary?.observed_count ?? trajectory?.ball.observed_count ?? "—"}</strong><span>球检测观测点</span></div>
      <div><strong>{summary ? `${(summary.coverage_fraction * 100).toFixed(1)}%` : "—"}</strong><span>视频帧观测覆盖</span></div>
      <div><strong>{summary?.segment_count ?? "—"}</strong><span>独立轨迹片段</span></div>
      <div><strong>{summary?.interpolated_count ?? "—"}</strong><span>短缺口插值点</span></div>
    </div>
    <svg className="trajectory-timeline" viewBox="0 0 520 84" role="img" aria-label="全视频球观测时间分布，绿色为移动，灰色为静止，蓝色为零散观测">
      <text x="10" y="12" fill="#aebbb4" fontSize="10">全视频观测时间分布</text>
      <path d="M10 70 H510" stroke="#536258" />
      {segments.map(item => <rect key={item.segment_id} x={10 + (item.start_ms - start) / duration * 500} y={item.analysis?.motion_status === "moving" ? 22 : item.analysis?.motion_status === "stationary" ? 38 : 54} width={Math.max(1, (item.end_ms - item.start_ms) / duration * 500)} height="10" fill={item.analysis?.motion_status === "moving" ? "#c9ff43" : item.analysis?.motion_status === "stationary" ? "#748278" : "#71a7ff"} />)}
      <text x="10" y="83" fill="#aebbb4" fontSize="9">{(start / 1000).toFixed(1)}s</text><text x="478" y="83" fill="#aebbb4" fontSize="9">{(end / 1000).toFixed(1)}s</text>
    </svg>
    <p className="trajectory-disclosure">移动 {moving.length} 段 · 静止/微动 {stationary.length} 段 · 零散 {segments.length - moving.length - stationary.length} 段</p>
    <p className="trajectory-disclosure">有球观测 {summary?.observed_frame_count ?? "—"} / {trajectory?.source.frame_count ?? "—"} 帧；平均检测置信度 {summary?.confidence?.mean?.toFixed(2) ?? "—"}。</p>
    <p className="trajectory-disclosure">可在高级叠加中选择球路片段。低置信观测、静态点和长漏检不会连接成线；插值仅供查看，不参与评分。</p>
    {!!summary && (summary.returned_segment_count ?? segments.length) < summary.segment_count && <p className="trajectory-disclosure">片段较多，已按全视频时间抽样展示 {segments.length} 段。</p>}
    </details>
  </>;
}
