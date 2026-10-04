import type { MotionEpisode } from "./lib/api-types";
import { ROTATION_METRICS, motionMetricText } from "./lib/motion-analysis";

export default function RotationAnalysisPanel({ episode, seek }: { episode: MotionEpisode; seek: (timestampMs: number) => void }) {
  const rotation = episode.rotation_analysis;
  if (!rotation) return null;
  return <section className="rotation-analysis" aria-label="二维转体观察">
    <div className="motion-analysis-heading"><h3>二维转体观察</h3><span>{rotation.status === "unavailable" ? "测量证据不足" : rotation.status === "partial" ? "部分指标可测" : "连续测量可用"}</span></div>
    <p className="motion-phase-disclosure">技术评分：{rotation.score_status === "insufficient_evidence" ? "证据不足" : "待教练标定"}。肩髋线夹角是画面投影，不能据此判断真实三维转体幅度或动力链先后。</p>
    <div className="motion-metrics rotation-metrics">{ROTATION_METRICS.map(metric => {
      const evidence = rotation.metric_evidence[metric.key];
      const available = evidence?.status === "measured";
      const start = evidence?.start_ms;
      return <article key={metric.key}><span>{metric.label}</span><strong>{available ? motionMetricText(episode.metrics[metric.key]) : "—"}</strong><small>{metric.unit} · 有效采样 {Math.round((evidence?.coverage_fraction ?? 0) * 100)}%</small>{available && typeof start === "number" ? <button type="button" onClick={() => seek(start)}>复核测量区间 ↗</button> : <small>{evidence?.reason_zh || "连续观测不足"}</small>}</article>;
    })}</div>
  </section>;
}
