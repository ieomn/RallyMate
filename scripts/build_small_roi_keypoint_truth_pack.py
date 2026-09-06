#!/usr/bin/env python3
"""Build a blind, independently adjudicated keypoint truth pack for small ROIs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from rallymate_evaluation.small_roi_keypoint_truth import build_small_roi_truth_pack


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment-report", type=Path, required=True)
    parser.add_argument("--experimental-frames", type=Path, required=True)
    parser.add_argument("--primary-timeline", type=Path, required=True)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--gap-audit", type=Path, required=True)
    parser.add_argument("--comparison-video", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    manifest = build_small_roi_truth_pack(
        experiment_report_path=args.experiment_report,
        experimental_frames_path=args.experimental_frames,
        primary_timeline_path=args.primary_timeline,
        video_path=args.video,
        gap_audit_path=args.gap_audit,
        comparison_video_path=args.comparison_video,
        output_dir=args.output,
        asset_directory=root / "src" / "rallymate_annotation" / "assets",
    )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "status": manifest["status"],
                "frames": manifest["scope"]["frame_count"],
                "joint_tasks": manifest["scope"]["joint_task_count"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
