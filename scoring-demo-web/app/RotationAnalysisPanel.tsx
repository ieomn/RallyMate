import type { MotionEpisode } from "./lib/api-types";
import { ROTATION_METRICS, motionMetricText } from "./lib/motion-analysis";

export default function RotationAnalysisPanel({ episode, seek }: { episode: MotionEpisode; seek: (timestampMs: number) => void }) {
  const rotation = episode.rotation_analysis;
  if (!rotation) return null;
  const windows = rotation.local_windows ?? [];
  const phases = rotation.phase_measurements ?? [];
  return <section className="rotation-analysis" aria-label="二维转体观察">
    <div className="motion-analysis-heading"><h3>二维转体观察</h3><span>{rotation.status === "unavailable" ? "测量证据不足" : rotation.status === "partial" ? "部分指标可测" : "连续测量可用"}</span></div>
    <p className="motion-phase-disclosure">技术评分：{rotation.score_status === "insufficient_evidence" ? "证据不足" : "待教练标定"}。肩髋线夹角是画面投影，不能据此判断真实三维转体幅度或动力链先后。</p>
    <div className="motion-metrics rotation-metrics">{ROTATION_METRICS.map(metric => {
      const evidence = rotation.metric_evidence[metric.key];
      const available = evidence?.status === "measured";
      const start = evidence?.start_ms;
      return <article key={metric.key}><span>{metric.label}</span><strong>{available ? motionMetricText(episode.metrics[metric.key]) : "—"}</strong><small>{metric.unit} · 有效采样 {Math.round((evidence?.coverage_fraction ?? 0) * 100)}%</small>{available && typeof start === "number" ? <button type="button" onClick={() => seek(start)}>复核测量区间 ↗</button> : <small>{evidence?.reason_zh || "连续观测不足"}</small>}</article>;
    })}</div>
    {!!phases.length && <details className="rotation-window-details"><summary>按动作阶段查看独立测量</summary><p>只比较当前连续动作的相应阶段；阶段边界为二维运动估计。</p><div className="rotation-window-grid">{phases.map((phase, index) => <article key={`${phase.phase}-${index}`}><h4>{phase.label_zh || phase.phase}</h4><button type="button" onClick={() => seek(phase.start_ms)}>{(phase.start_ms / 1000).toFixed(2)}–{(phase.end_ms / 1000).toFixed(2)} 秒 ↗</button>{ROTATION_METRICS.filter(metric => phase.metric_evidence[metric.key]?.status === "measured").map(metric => <p key={metric.key}>{metric.label}<strong>{motionMetricText(phase.metrics[metric.key])} {metric.unit}</strong></p>)}</article>)}</div></details>}
    {!!windows.length && <details className="rotation-window-details"><summary>查看局部连续测量 · {windows.length} 个窗口</summary><p>局部窗口独立保留，不代替整段覆盖，也不会合并为完整转体结论。</p><div className="rotation-window-grid">{windows.map((window, index) => <article key={`${window.window_id}-${index}`}><h4>{window.axis === "shoulder" ? "肩线" : window.axis === "hip" ? "髋线" : "肩髋夹角"} · 连续窗口</h4><button type="button" onClick={() => seek(window.start_ms)}>{(window.start_ms / 1000).toFixed(2)}–{(window.end_ms / 1000).toFixed(2)} 秒 ↗</button>{ROTATION_METRICS.filter(metric => window.metric_evidence[metric.key]?.status === "measured").map(metric => <p key={metric.key}>{metric.label}<strong>{motionMetricText(window.metrics[metric.key])} {metric.unit}</strong></p>)}</article>)}</div></details>}
  </section>;
}
