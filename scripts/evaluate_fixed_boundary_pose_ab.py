#!/usr/bin/env python3
"""Evaluate paired pose-feature disagreement on one immutable event file."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from rallymate_evaluation.fixed_boundary_ab import (
    evaluate_fixed_boundary_pose_ab_files,
)


def _model_input(
    *,
    frames: Path,
    timeline: Path,
    model_sha256: str,
    backend: str,
    profile: str,
) -> dict[str, object]:
    return {
        "frames_path": frames,
        "primary_timeline_path": timeline,
        "model_sha256": model_sha256,
        "pose_backend": backend,
        "pose_profile": profile,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Compare two pose models on exactly the same external event IDs, "
            "tracks, boundaries, and phases. The output is cross-model "
            "disagreement, never truth error, accuracy, a threshold, or a grade."
        )
    )
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--source-id", required=True)
    parser.add_argument(
        "--boundary-source-kind",
        choices=("candidate", "manual", "external"),
        required=True,
    )
    parser.add_argument("--boundary-source-label", required=True)
    parser.add_argument("--model-a-name", required=True)
    parser.add_argument("--model-a-frames", type=Path, required=True)
    parser.add_argument("--model-a-timeline", type=Path, required=True)
    parser.add_argument("--model-a-sha256", required=True)
    parser.add_argument("--model-a-backend", default="unknown")
    parser.add_argument("--model-a-profile")
    parser.add_argument("--model-b-name", required=True)
    parser.add_argument("--model-b-frames", type=Path, required=True)
    parser.add_argument("--model-b-timeline", type=Path, required=True)
    parser.add_argument("--model-b-sha256", required=True)
    parser.add_argument("--model-b-backend", default="unknown")
    parser.add_argument("--model-b-profile")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.model_a_name == args.model_b_name:
        parser.error("model A and model B names must differ")

    report = evaluate_fixed_boundary_pose_ab_files(
        events_path=args.events,
        registry_path=args.registry,
        model_inputs={
            args.model_a_name: _model_input(
                frames=args.model_a_frames,
                timeline=args.model_a_timeline,
                model_sha256=args.model_a_sha256,
                backend=args.model_a_backend,
                profile=args.model_a_profile or args.model_a_name,
            ),
            args.model_b_name: _model_input(
                frames=args.model_b_frames,
                timeline=args.model_b_timeline,
                model_sha256=args.model_b_sha256,
                backend=args.model_b_backend,
                profile=args.model_b_profile or args.model_b_name,
            ),
        },
        boundary_source_kind=args.boundary_source_kind,
        boundary_source_label=args.boundary_source_label,
        source_id=args.source_id,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": report["status"],
                "output": str(args.output.resolve()),
                "boundary_truth_status": report["comparison_semantics"][
                    "event_boundary_truth_status"
                ],
                "indicator_count": len(report["indicator_metrics"]),
                "feature_count": len(report["feature_metrics"]),
                "accuracy_claim": False,
                "event_quality_gate_status": "not_evaluated",
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
