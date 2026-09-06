#!/usr/bin/env python3
"""Build the M69 post-router residual observability audit."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from rallymate_evaluation.pose_observability_residual import (
    build_pose_observability_residual_audit,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument(
        "--router-report", type=Path, action="append", required=True
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output already exists; residual audits are immutable")
    report = build_pose_observability_residual_audit(
        registry_path=args.registry, router_report_paths=args.router_report
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "residual_indicator_instances": report["counts"][
                    "non_hard_fail_feature_incomplete"
                ],
                "residual_events": report["counts"]["residual_event_count"],
                "candidate_frames": report["counts"]["candidate_frame_count"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
