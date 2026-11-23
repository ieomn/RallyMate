"""CLI for coach-free reference-population rubric calibration from SAM 3D cohorts."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence

from rallymate_scoring.reference_calibration import (
    CALIBRATABLE_METRICS,
    CALIBRATION_ORIGIN,
    CALIBRATION_SCHEMA_VERSION,
    ReferenceCalibrationError,
    calibrate_reference_population,
    evaluate_cohort,
    export_calibration_artifacts,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Derive a SHA-256-sealed, coach-free tennis rubric policy from tiered SAM 3D cohorts."
    )
    parser.add_argument(
        "--input-manifest",
        type=Path,
        required=True,
        help="Path to tiered reference cohort JSON manifest.",
    )
    parser.add_argument(
        "--output-policy",
        type=Path,
        default=None,
        help="Destination JSON path for the signed rubric policy (not written on fail-closed error).",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="Optional destination JSON path for the statistical calibration audit report.",
    )
    parser.add_argument("--min-reference-episodes", type=int, default=10)
    parser.add_argument("--min-comparison-episodes", type=int, default=10)
    parser.add_argument("--min-reference-videos", type=int, default=3)
    parser.add_argument("--min-comparison-videos", type=int, default=2)
    parser.add_argument("--min-separation-delta", type=float, default=0.0)
    parser.add_argument("--allow-no-lower-tier", action="store_true")
    parser.add_argument(
        "--metrics",
        nargs="+",
        default=list(CALIBRATABLE_METRICS),
        choices=list(CALIBRATABLE_METRICS),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = calibrate_reference_population(
            args.input_manifest,
            metrics=args.metrics,
            min_reference_episodes=args.min_reference_episodes,
            min_comparison_episodes=args.min_comparison_episodes,
            min_reference_videos=args.min_reference_videos,
            min_comparison_videos=args.min_comparison_videos,
            min_separation_delta=args.min_separation_delta,
            require_lower_tier=not args.allow_no_lower_tier,
        )
        written = export_calibration_artifacts(
            result,
            policy_path=args.output_policy,
            report_path=args.report,
        )
        summary = {
            "status": "calibrated",
            "coach_calibrated": result["policy"]["coach_calibrated"],
            "calibration_origin": result["policy"]["calibration_origin"],
            "policy_sha256": result["policy"]["policy_sha256"],
            "rules": {k: v["knots"] for k, v in result["policy"]["rules"].items()},
            "written": {k: str(v) for k, v in written.items()},
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    except ReferenceCalibrationError as exc:
        failure_report: dict[str, object] = {
            "schema_version": CALIBRATION_SCHEMA_VERSION,
            "status": "failed_closed",
            "coach_calibrated": False,
            "calibration_origin": CALIBRATION_ORIGIN,
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
        try:
            failure_report["cohort_evaluation"] = evaluate_cohort(
                args.input_manifest, metrics=args.metrics
            )
        except ReferenceCalibrationError:
            pass
        if args.report is not None:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(
                json.dumps(failure_report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                encoding="utf-8",
            )
        print(json.dumps(failure_report, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
