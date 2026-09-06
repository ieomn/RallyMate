from __future__ import annotations

import argparse
import json
from pathlib import Path

from rallymate_scoring.calibration_dataset import compile_calibration_dataset


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Compile exact-join, leakage-safe calibration data from manual truth, "
            "coach labels and versioned indicator features. Does not fit thresholds."
        )
    )
    parser.add_argument("--feasibility-registry", type=Path, required=True)
    parser.add_argument(
        "--indicator-features", type=Path, action="append", required=True
    )
    parser.add_argument("--manual-events", type=Path, required=True)
    parser.add_argument("--manual-semantics", type=Path, required=True)
    parser.add_argument("--coach-labels", type=Path, required=True)
    parser.add_argument("--truth-intake-manifest", type=Path)
    parser.add_argument("--truth-manifest", type=Path)
    parser.add_argument("--truth-validation-report", type=Path)
    parser.add_argument("--group-metadata", type=Path)
    parser.add_argument("--split-policy", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--synthetic-test-only",
        action="store_true",
        help=(
            "emit a synthetic_test_only_calibration_input for test mechanics; "
            "this can never authorize production scoring"
        ),
    )
    args = parser.parse_args()
    manifest = compile_calibration_dataset(
        feasibility_registry_path=args.feasibility_registry,
        indicator_feature_paths=args.indicator_features,
        manual_events_path=args.manual_events,
        manual_semantics_path=args.manual_semantics,
        coach_labels_path=args.coach_labels,
        truth_intake_manifest_path=args.truth_intake_manifest,
        truth_manifest_path=args.truth_manifest,
        truth_validation_report_path=args.truth_validation_report,
        group_metadata_path=args.group_metadata,
        split_policy_path=args.split_policy,
        output_dir=args.output_dir,
        synthetic_test_only=args.synthetic_test_only,
    )
    print(
        json.dumps(
            {
                "manifest": str(args.output_dir.resolve() / "manifest.json"),
                "dataset_id": manifest["dataset_id"],
                "status": manifest["status"],
                "artifact_scope": manifest["artifact_scope"],
                "sample_count": manifest["counts"]["samples"],
                "generated_thresholds": manifest["safety"]["generated_thresholds"],
                "F3_claimed": manifest["safety"]["automatic_F3_or_F4_promotion"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
