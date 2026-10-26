import type { MotionFocus } from "./lib/motion-focus";

export default function MotionFocusOverlay({ focus }: { focus: MotionFocus }) {
  const { width, height, markers, bones, trails } = focus;
  const radius = Math.min(width, height) * .007;
  return <svg className="motion-focus-overlay" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="xMidYMid meet" role="img" aria-label="动作标记：全身关节、身体连线与手部轨迹">
    <g className="motion-focus-bones">{bones.map(({ from, to }) => <line key={`${from.name}-${to.name}`} x1={from.x * width} y1={from.y * height} x2={to.x * width} y2={to.y * height} vectorEffect="non-scaling-stroke" />)}</g>
    <g className="motion-focus-trails">{trails.flatMap(trail => trail.points.slice(1).map((point, index) => {
      const before = trail.points[index];
      return <line key={`${trail.name}-${point.timestamp_ms}`} x1={before.x * width} y1={before.y * height} x2={point.x * width} y2={point.y * height} opacity={.16 + .7 * (index + 1) / (trail.points.length - 1)} vectorEffect="non-scaling-stroke" />;
    }))}</g>
    {markers.map(point => point.name.endsWith("wrist")
      ? <g key={point.name} className="motion-focus-hand"><circle cx={point.x * width} cy={point.y * height} r={radius} vectorEffect="non-scaling-stroke" /><circle className="motion-focus-core" cx={point.x * width} cy={point.y * height} r={radius * .3} /></g>
      : point.name.endsWith("ankle") ? <ellipse key={point.name} className="motion-focus-foot" cx={point.x * width} cy={point.y * height} rx={radius * 1.5} ry={radius * .55} vectorEffect="non-scaling-stroke" />
      : <circle key={point.name} className="motion-focus-joint" cx={point.x * width} cy={point.y * height} r={radius * .65} vectorEffect="non-scaling-stroke" />)}
  </svg>;
}
