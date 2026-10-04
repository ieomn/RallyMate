import type { PosePreviewFrame, PosePreviewResponse } from "./lib/api-types";
import { poseEdges } from "./lib/pose-playback";

export default function PoseSkeletonOverlay({ frame, preview, aspectRatio }: { frame: PosePreviewFrame; preview: PosePreviewResponse; aspectRatio: number }) {
  const width = frame.width || preview.source.width || aspectRatio * 1000;
  const height = frame.height || preview.source.height || 1000;
  const edges = poseEdges(frame, preview.skeleton_edges);
  const radius = Math.max(width, height) * .004;
  return <svg className="pose-skeleton-overlay" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" role="img" aria-label="当前帧人体骨架">
    <g className="pose-bones-halo">{edges.map(edge => <line key={`${edge.from}-${edge.to}`} x1={edge.start.x * width} y1={edge.start.y * height} x2={edge.end.x * width} y2={edge.end.y * height} vectorEffect="non-scaling-stroke" />)}</g>
    <g className="pose-bones">{edges.map(edge => <line key={`${edge.from}-${edge.to}`} x1={edge.start.x * width} y1={edge.start.y * height} x2={edge.end.x * width} y2={edge.end.y * height} className={edge.from.startsWith("left_") || edge.to.startsWith("left_") ? "pose-side-left" : edge.from.startsWith("right_") || edge.to.startsWith("right_") ? "pose-side-right" : "pose-center"} vectorEffect="non-scaling-stroke" />)}</g>
    <g className="pose-joints">{frame.keypoints.map(point => <circle key={point.name} cx={point.x * width} cy={point.y * height} r={radius} vectorEffect="non-scaling-stroke" />)}</g>
  </svg>;
}
