from __future__ import annotations

import argparse
import json
from pathlib import Path

from rallymate_scoring.calibration_ranking import (
    RankingCalibrationError,
    load_jsonl,
    prepare_ranking_dataset,
    write_json,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare a leakage-safe, ranking-only calibration dataset from complete "
            "compiled samples. Independent-test labels are sealed and omitted. This "
            "command cannot generate A-E grades or thresholds."
        )
    )
    parser.add_argument("--samples", type=Path, required=True)
    parser.add_argument("--indicator-id", required=True)
    parser.add_argument("--source-dataset-id", required=True)
    parser.add_argument("--source-dataset-version", required=True)
    parser.add_argument(
        "--source-kind",
        choices=["human_coach_ranking_ground_truth", "synthetic_test_fixture"],
        required=True,
    )
    parser.add_argument("--prepared-at", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.output.exists():
            raise RankingCalibrationError(f"refusing to overwrite output: {args.output}")
        dataset = prepare_ranking_dataset(
            load_jsonl(args.samples),
            indicator_id=args.indicator_id,
            source_dataset_id=args.source_dataset_id,
            source_dataset_version=args.source_dataset_version,
            source_kind=args.source_kind,
            prepared_at=args.prepared_at,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        write_json(args.output, dataset)
    except (OSError, RankingCalibrationError, ValueError) as exc:
        parser.exit(2, f"ranking dataset preparation rejected: {exc}\n")
    print(
        json.dumps(
            {
                "status": dataset["readiness"]["status"],
                "output": str(args.output.resolve()),
                "indicator_id": dataset["indicator_id"],
                "target": "relative_order_only",
                "A_E_generated": False,
                "A_E_scoring_ready": False,
                "independent_test_labels_withheld": True,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
