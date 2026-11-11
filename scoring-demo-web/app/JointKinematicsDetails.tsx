"use client";
import { useState } from "react";
import type { TennisEpisode } from "./lib/tennis-assessment";
import type { JointMeasurement, JointPhase } from "./lib/joint-kinematics";
import "./joint-kinematics.css";
const labels: Record<string, string> = { left_knee_flexion_deg: "左膝屈曲", right_knee_flexion_deg: "右膝屈曲",
  left_elbow_flexion_deg: "左肘屈曲", right_elbow_flexion_deg: "右肘屈曲", left_shoulder_elevation_deg: "左肩上举",
  right_shoulder_elevation_deg: "右肩上举", shoulder_hip_separation_deg: "肩髋分离", stance_width_shoulders: "站距 / 肩宽" };
function Measurements({ metrics }: { metrics: Record<string, JointMeasurement> }) {
  return <table><thead><tr><th>测量</th><th>中位数</th><th>10–90% 区间</th><th>角速度 P90</th></tr></thead><tbody>
    {Object.entries(metrics).filter(([, m]) => m.status === "measured").map(([key, m]) => <tr key={key}>
      <td>{labels[key] ?? key}</td><td>{m.median!.toFixed(1)}{m.unit === "deg" ? "°" : " 倍"}</td>
      <td>{m.p10!.toFixed(1)}–{m.p90!.toFixed(1)}</td><td>{m.angular_speed_p90_deg_s === undefined ? "—" : `${m.angular_speed_p90_deg_s.toFixed(1)} °/s`}</td>
    </tr>)}</tbody></table>;
}
export default function JointKinematicsDetails({ episode, jobId }: { episode: TennisEpisode; jobId: string }) {
  const [open, setOpen] = useState(false);
  const data = episode.joint_kinematics; if (!data) return null;
  return <details className="joint-kinematics" onToggle={event => setOpen(event.currentTarget.open)}><summary>关节角度与十阶段动作窗口</summary>
    {open && <>
    <p>从原视频时间戳与三维人体姿态估计计算。阶段窗口和主挥拍时刻按人体动作转折定位；真实触球需要球与球拍的观测证据确认。</p>
    <Measurements metrics={data.metrics} />
    {data.phases.map(phase => <PhaseDetails key={phase.phase_id} phase={phase} jobId={jobId} />)}
    </>}
  </details>;
}

function PhaseDetails({ phase, jobId }: { phase: JointPhase; jobId: string }) {
  const [open, setOpen] = useState(false);
  return <details onToggle={event => setOpen(event.currentTarget.open)}><summary>{phase.phase_id} {phase.label} · {(phase.start_ms / 1000).toFixed(2)}–{(phase.end_ms / 1000).toFixed(2)} 秒</summary>
    {open && <><button type="button" onClick={() => window.dispatchEvent(new CustomEvent("rallymate:seek-video", { detail: { jobId, timestampMs: phase.anchor_ms } }))}>查看这个阶段</button>
      <Measurements metrics={phase.metrics} /></>}
  </details>;
}
