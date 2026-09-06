#!/usr/bin/env python3
"""Evaluate M44 work-item evidence without changing score or quality state."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from rallymate_annotation.scoring_action_readiness import (
    build_scoring_truth_action_readiness,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worklist", type=Path, required=True)
    parser.add_argument("--truth-validation", type=Path, required=True)
    parser.add_argument("--manual-events", type=Path, required=True)
    parser.add_argument("--manual-keypoints", type=Path, required=True)
    parser.add_argument("--manual-semantics", type=Path, required=True)
    parser.add_argument("--scoring-truth-evaluation", type=Path, required=True)
    parser.add_argument("--pose-truth-manifest", type=Path, required=True)
    parser.add_argument("--pose-truth-evaluation", type=Path, required=True)
    parser.add_argument("--reference-context", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    if args.output.exists() and not args.overwrite:
        parser.error(f"refusing to overwrite {args.output}")
    try:
        report = build_scoring_truth_action_readiness(
            worklist_path=args.worklist,
            truth_validation_path=args.truth_validation,
            manual_events_path=args.manual_events,
            manual_keypoints_path=args.manual_keypoints,
            manual_semantics_path=args.manual_semantics,
            scoring_truth_evaluation_path=args.scoring_truth_evaluation,
            pose_truth_manifest_path=args.pose_truth_manifest,
            pose_truth_evaluation_path=args.pose_truth_evaluation,
            reference_context_path=args.reference_context,
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": report["status"],
                "work_items": report["counts"]["work_items"],
                "by_status": report["counts"]["by_status"],
                "indicator_instances_by_status": report["counts"][
                    "indicator_instances_by_status"
                ],
                "output": str(args.output.resolve()),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
