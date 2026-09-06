from __future__ import annotations

import argparse
import json
from pathlib import Path

from rallymate_evaluation.scoring_truth import build_scoring_truth_evaluation


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate RallyMate event and feature errors against manual truth"
    )
    parser.add_argument("--frames", type=Path, required=True)
    parser.add_argument("--primary-timeline", type=Path, required=True)
    parser.add_argument("--predicted-events", type=Path, required=True)
    parser.add_argument("--manual-events", type=Path)
    parser.add_argument("--manual-keypoints", type=Path)
    parser.add_argument(
        "--manual-semantics",
        type=Path,
        help=(
            "Accepted semantic ground truth JSONL used by scoring-context "
            "features such as FS02-M02 target-direction alignment"
        ),
    )
    parser.add_argument(
        "--registry",
        type=Path,
        default=Path("metric-feasibility-pose-wave-v2.json"),
        help="Versioned feasibility registry evaluated by this report",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = build_scoring_truth_evaluation(
        frames_path=args.frames,
        primary_timeline_path=args.primary_timeline,
        predicted_events_path=args.predicted_events,
        manual_events_path=args.manual_events,
        manual_keypoints_path=args.manual_keypoints,
        manual_semantics_path=args.manual_semantics,
        registry_path=args.registry,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {"output": str(args.output), "status": report["status"]},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
