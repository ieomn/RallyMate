from __future__ import annotations

import argparse
import json
from pathlib import Path

from rallymate_scoring.manual_event_features import build_manual_event_features


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Measure registry features using accepted manual event IDs/boundaries/phases. "
            "No candidate detector or A-E thresholds are used."
        )
    )
    parser.add_argument("--frames", type=Path, required=True)
    parser.add_argument("--primary-timeline", type=Path, required=True)
    parser.add_argument("--manual-events", type=Path, required=True)
    parser.add_argument("--feasibility-registry", type=Path, required=True)
    parser.add_argument("--video-id", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--pose-model-json",
        type=Path,
        help="Optional JSON object with backend/runtime/profile/model_sha256/topology fields",
    )
    args = parser.parse_args()
    pose_model = (
        json.loads(args.pose_model_json.read_text(encoding="utf-8"))
        if args.pose_model_json
        else None
    )
    result = build_manual_event_features(
        frames_path=args.frames,
        primary_timeline_path=args.primary_timeline,
        manual_events_path=args.manual_events,
        feasibility_registry_path=args.feasibility_registry,
        video_id=args.video_id,
        output_dir=args.output_dir,
        pose_model=pose_model,
    )
    print(
        json.dumps(
            {
                "summary": str(args.output_dir.resolve() / "summary.json"),
                "status": result["summary"]["status"],
                "manual_events": len(result["events"]),
                "indicator_records": len(result["indicator_records"]),
                "candidate_event_detector_used": False,
                "generated_thresholds": False,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
