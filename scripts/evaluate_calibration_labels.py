from __future__ import annotations

import argparse
import json
from pathlib import Path

from rallymate_scoring.calibration import (
    calibration_scoring_readiness,
    compute_annotator_agreement,
    load_coach_labels,
    validate_ordinal_model,
    validate_threshold_calibration,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate coach labels and optional versioned scoring calibration"
    )
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--calibration", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    labels = load_coach_labels(args.labels)
    agreement = compute_annotator_agreement(labels)
    artifacts = []
    for path in args.calibration:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("backend") == "threshold_rule":
            validate_threshold_calibration(payload)
        elif payload.get("backend") == "ordinal_regression":
            validate_ordinal_model(payload)
        else:
            raise ValueError(f"unsupported calibration backend in {path}")
        scoring_ready, promotion_reason = calibration_scoring_readiness(payload)
        artifacts.append(
            {
                "path": str(path),
                "indicator_id": payload["indicator_id"],
                "backend": payload["backend"],
                "version": payload.get("threshold_version") or payload.get("model_version"),
                "ground_truth_dataset_version": payload["ground_truth_dataset_version"],
                "status": (
                    "validated_independent_test_gate_passed_registry_F4_not_evaluated"
                    if scoring_ready
                    else "validated_not_independently_promoted"
                ),
                "promotion_reason": promotion_reason,
                "independent_test": payload["independent_test"],
            }
        )
    all_artifacts_ready = bool(artifacts) and all(
        item["status"]
        == "validated_independent_test_gate_passed_registry_F4_not_evaluated"
        for item in artifacts
    )
    report = {
        "schema_version": "1.0.0",
        "status": (
            "calibration_artifacts_independent_test_ready_registry_F4_required"
            if all_artifacts_ready
            else "calibration_artifacts_validated_not_promoted"
            if artifacts
            else "coach_labels_validated_calibration_required"
        ),
        "agreement": agreement,
        "calibration_artifacts": artifacts,
        "safety": {
            "generated_thresholds": False,
            "trained_ordinal_model": False,
            "F4_promotion": False,
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "status": report["status"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
