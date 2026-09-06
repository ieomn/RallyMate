from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from rallymate_scoring.calibration_fitting import (
    CalibrationFitError,
    fit_calibration_candidates,
    independent_test_seal_sha256,
    predict_candidate,
    validate_calibration_candidate,
    validate_prepared_dataset,
)
from rallymate_scoring.scoring import score_indicator
from rallymate_scoring.scoring_truth_calibration_authorization import (
    DIAGNOSTIC_STATUS,
    SYNTHETIC_STATUS,
    build_nonproduction_truth_authorization_marker,
)
from tests.test_calibration_promotion import _truth_authorization_fixture


FEATURES = ["feature_a", "feature_b"]


def _dataset(*, test_only: bool = True) -> dict:
    records = []
    for split, group, offset in (
        ("train", "group-train", 0.0),
        ("validation", "group-validation", 0.1),
    ):
        for grade_index, grade in enumerate(("E", "D", "C", "B", "A")):
            for repeat in range(2):
                record_id = f"{split}-{grade}-{repeat}"
                records.append(
                    {
                        "record_id": record_id,
                        "video_id": f"video-{group}",
                        "event_id": f"event-{record_id}",
                        "group_id": group,
                        "leakage_component_id": group,
                        "split": split,
                        "features": {
                            "feature_a": grade_index * 10.0 + repeat + offset,
                            "feature_b": grade_index * 3.0 - repeat + offset,
                        },
                        "grade": grade,
                        "annotator_ids": ["coach-1", "coach-2"],
                        "label_source_ids": [
                            f"label-{record_id}-coach-1",
                            f"label-{record_id}-coach-2",
                        ],
                        "consensus_status": "unanimous",
                    }
                )
    payload = {
        "schema_version": "1.0.0",
        "artifact_scope": (
            "synthetic_test_only_calibration_input"
            if test_only
            else "calibration_input"
        ),
        "dataset_id": "synthetic-test-dataset" if test_only else "human-dataset",
        "dataset_version": "v1",
        "source": {
            "kind": "synthetic_test_fixture" if test_only else "human_coach_ground_truth",
            "manifest_id": "fixture-manifest" if test_only else "human-label-manifest-v1",
            "source_sha256": "a" * 64,
            "prepared_at": "2026-08-13T00:00:00Z",
        },
        "indicator_id": "FS01-M02",
        "feature_order": FEATURES,
        "unit_by_feature": {"feature_a": "body", "feature_b": "deg"},
        "feature_version_by_feature": {
            "feature_a": "feature-a-v1",
            "feature_b": "feature-b-v1",
        },
        "label_scale": ["E", "D", "C", "B", "A"],
        "label_resolution_policy": "unanimous_multi_coach_only_v1",
        "split_policy": {
            "strategy": "group_holdout",
            "group_key": "video_id",
            "train_groups": ["group-train"],
            "validation_groups": ["group-validation"],
            "independent_test_groups": ["group-independent"],
        },
        "agreement": {
            "status": "evaluated",
            "metric": "quadratic_weighted_kappa",
            "value": 1.0,
            "annotator_count": 2,
            "shared_item_count": 20,
        },
        "records": records,
        "independent_test_seal": {
            "seal_id": "sealed-test-v1",
            "indicator_id": "FS01-M02",
            "record_count": 10,
            "groups": ["group-independent"],
            "sample_ids": [f"sealed-sample-{index}" for index in range(10)],
            "content_sha256": "b" * 64,
            "canonicalization": "rallymate-canonical-json-v1",
            "labels_withheld": True,
        },
        "readiness": {
            "status": "prepared_for_external_protocol_review",
            "blockers": [],
            "F3_claimed": False,
        },
    }
    payload["truth_authorization"] = build_nonproduction_truth_authorization_marker(
        status=SYNTHETIC_STATUS if test_only else DIAGNOSTIC_STATUS,
        input_files={
            "intake_manifest": "1" * 64,
            "truth_manifest": "2" * 64,
            "truth_validation_report": "3" * 64,
            "manual_events": "4" * 64,
            "manual_semantics": "5" * 64,
            "coach_labels": "6" * 64,
        },
    )
    _refresh_integrity(payload)
    return payload


def _refresh_integrity(dataset: dict) -> None:
    split_counts = {}
    grade_counts = {"train": {}, "validation": {}}
    for record in dataset["records"]:
        split = record["split"]
        split_counts[split] = split_counts.get(split, 0) + 1
        if split in grade_counts:
            grade = record["grade"]
            grade_counts[split][grade] = grade_counts[split].get(grade, 0) + 1
    dataset["integrity"] = {
        "record_count": len(dataset["records"]),
        "split_counts": dict(sorted(split_counts.items())),
        "grade_counts_by_split": {
            split: dict(sorted(counts.items())) for split, counts in grade_counts.items()
        },
        "source_dataset_sha256": dataset["source"]["source_sha256"],
        "canonicalization": "rallymate-canonical-json-v1",
    }


def _protocol(*, test_only: bool = True, indicator_id: str = "FS01-M02") -> dict:
    return {
        "schema_version": "1.1.0",
        "artifact_scope": (
            "synthetic_test_only_fit_protocol"
            if test_only
            else "calibration_fit_protocol"
        ),
        "indicator_id": indicator_id,
        "protocol_id": "synthetic-test-protocol" if test_only else "registered-protocol",
        "protocol_version": "v1",
        "candidate_version_prefix": "test-candidate" if test_only else "candidate",
        "source": {
            "kind": "synthetic_test_fixture" if test_only else "preregistered_calibration_protocol",
            "source_sha256": "c" * 64,
            "registered_at": "2026-08-13T00:00:00Z",
        },
        "label_scale": ["E", "D", "C", "B", "A"],
        "label_resolution_policy": "unanimous_multi_coach_only_v1",
        "requirements": {
            "min_records_by_split": {
                "train": 10,
                "validation": 10,
                "independent_test": 10,
            },
            "min_records_per_grade_by_split": {
                "train": {grade: 2 for grade in ("E", "D", "C", "B", "A")},
                "validation": {grade: 2 for grade in ("E", "D", "C", "B", "A")},
            },
            "min_annotators": 2,
            "min_shared_items": 10,
            "agreement_metric": "quadratic_weighted_kappa",
            "min_agreement_value": 0.9,
        },
        "backends": {
            "threshold_rule": {
                "enabled": True,
                "primary_feature": "feature_a",
                "direction": "higher_is_better",
                "objective": "mean_absolute_grade_error",
            },
            "ordinal_regression": {
                "enabled": True,
                "optimizer": {
                    "algorithm": "projected_gradient_descent_v1",
                    "max_iterations": 800,
                    "learning_rate": 0.05,
                    "l2": 0.01,
                    "tolerance": 1e-8,
                    "min_cutpoint_gap": 0.01,
                },
            },
        },
    }


class CalibrationFittingTests(unittest.TestCase):
    def test_empty_not_ready_package_preserves_null_feature_contract_without_fitting(self) -> None:
        dataset = _dataset()
        dataset["records"] = []
        dataset["unit_by_feature"] = {name: None for name in FEATURES}
        dataset["feature_version_by_feature"] = {name: None for name in FEATURES}
        dataset["split_policy"]["train_groups"] = []
        dataset["split_policy"]["validation_groups"] = []
        dataset["split_policy"]["independent_test_groups"] = []
        dataset["agreement"] = {
            "status": "not_evaluated",
            "metric": "quadratic_weighted_kappa",
            "value": None,
            "annotator_count": 0,
            "shared_item_count": 0,
        }
        dataset["independent_test_seal"] = {
            "seal_id": "empty-seal",
            "indicator_id": "FS01-M02",
            "record_count": 0,
            "groups": [],
            "sample_ids": [],
            "content_sha256": independent_test_seal_sha256([]),
            "canonicalization": "rallymate-canonical-json-v1",
            "labels_withheld": True,
        }
        dataset["readiness"] = {
            "status": "insufficient",
            "blockers": ["human_annotations_missing"],
            "F3_claimed": False,
        }
        _refresh_integrity(dataset)
        validate_prepared_dataset(dataset, require_fit_ready=False)
        with self.assertRaisesRegex(CalibrationFitError, "readiness blockers"):
            fit_calibration_candidates(dataset, _protocol())

    def test_independent_test_seal_is_order_invariant_but_content_sensitive(self) -> None:
        first = {"sample_id": "sample-b", "grade": "A", "text": "球员"}
        second = {"sample_id": "sample-a", "grade": "C", "value": 1.0}
        expected = independent_test_seal_sha256([first, second])
        self.assertEqual(expected, independent_test_seal_sha256([second, first]))
        changed = copy.deepcopy(second)
        changed["grade"] = "D"
        self.assertNotEqual(expected, independent_test_seal_sha256([first, changed]))

    def test_fits_both_non_production_candidates_without_opening_test_seal(self) -> None:
        candidates = fit_calibration_candidates(_dataset(), _protocol())
        self.assertEqual(
            {candidate["backend"] for candidate in candidates},
            {"threshold_rule", "ordinal_regression"},
        )
        for candidate in candidates:
            validate_calibration_candidate(candidate)
            self.assertEqual(
                candidate["artifact_scope"],
                "synthetic_test_only_calibration_candidate",
            )
            self.assertFalse(candidate["promotion"]["scoring_allowed"])
            self.assertFalse(candidate["promotion"]["F3_promoted"])
            self.assertFalse(candidate["promotion"]["F4_promoted"])
            self.assertFalse(candidate["independent_test"]["accessed_during_fit"])
            self.assertIsNone(candidate["fit"]["independent_test_metrics"])
            prediction = predict_candidate(
                candidate, {"feature_a": 40.0, "feature_b": 12.0}
            )
            self.assertEqual(prediction["status"], "candidate_prediction_not_scored")
            self.assertIn(prediction["grade"], ("E", "D", "C", "B", "A"))

    def test_serialized_unverified_real_input_cannot_create_candidate(self) -> None:
        dataset = _dataset(test_only=False)
        binding, _ = _truth_authorization_fixture(synthetic=False)
        dataset["truth_authorization"] = binding
        with self.assertRaisesRegex(
            CalibrationFitError, "same-process verified authorized-intake object"
        ):
            fit_calibration_candidates(
                dataset, _protocol(test_only=False)
            )

    def test_private_test_issuer_exercises_real_fit_binding_replay(self) -> None:
        dataset = _dataset(test_only=False)
        binding, wrapper = _truth_authorization_fixture(synthetic=False)
        dataset["truth_authorization"] = binding
        candidates = fit_calibration_candidates(
            dataset,
            _protocol(test_only=False),
            verified_truth_authorization=wrapper,
        )
        self.assertEqual(len(candidates), 2)
        for candidate in candidates:
            self.assertEqual(candidate["artifact_scope"], "calibration_candidate")
            self.assertEqual(candidate["truth_authorization"], binding)
            self.assertEqual(
                candidate["prepared_dataset"][
                    "truth_authorization_binding_sha256"
                ].upper(),
                binding["binding_sha256"],
            )

    def test_candidate_scope_cannot_be_flipped_from_synthetic_to_real(self) -> None:
        candidate = fit_calibration_candidates(_dataset(), _protocol())[0]
        candidate["artifact_scope"] = "calibration_candidate"
        with self.assertRaisesRegex(
            CalibrationFitError, "truth authorization lineage|scope mismatch"
        ):
            validate_calibration_candidate(candidate)

    def test_candidate_semantics_must_match_both_hashed_input_provenances(self) -> None:
        candidate = fit_calibration_candidates(_dataset(), _protocol())[0]
        candidate["fit_protocol"]["label_resolution_policy"] = "external_adjudicated_v1"
        with self.assertRaisesRegex(CalibrationFitError, "resolution policy mismatch"):
            validate_calibration_candidate(candidate)

    def test_candidate_requires_scope_specific_provenance_identifiers(self) -> None:
        candidate = fit_calibration_candidates(_dataset(), _protocol())[0]
        del candidate["prepared_dataset"]["dataset_id"]
        with self.assertRaisesRegex(
            CalibrationFitError, "prepared_dataset.dataset_id"
        ):
            validate_calibration_candidate(candidate)
        candidate = fit_calibration_candidates(_dataset(), _protocol())[0]
        del candidate["prepared_dataset"]["dataset_version"]
        with self.assertRaisesRegex(
            CalibrationFitError, "prepared_dataset.dataset_version"
        ):
            validate_calibration_candidate(candidate)
        candidate = fit_calibration_candidates(_dataset(), _protocol())[0]
        del candidate["fit_protocol"]["protocol_id"]
        with self.assertRaisesRegex(CalibrationFitError, "fit_protocol.protocol_id"):
            validate_calibration_candidate(candidate)
        candidate = fit_calibration_candidates(_dataset(), _protocol())[0]
        del candidate["fit_protocol"]["protocol_version"]
        with self.assertRaisesRegex(
            CalibrationFitError, "fit_protocol.protocol_version"
        ):
            validate_calibration_candidate(candidate)

    def test_candidate_schema_has_scope_specific_provenance_requirements(self) -> None:
        root = Path(__file__).resolve().parents[1]
        schema = json.loads(
            (root / "contracts/calibration-candidate.schema.json").read_text(
                encoding="utf-8"
            )
        )
        conditions = schema["$defs"]["provenance"]["allOf"]
        required_sets = {tuple(item["then"]["required"]) for item in conditions}
        self.assertIn(
            (
                "dataset_id",
                "dataset_version",
                "truth_authorization_binding_sha256",
            ),
            required_sets,
        )
        self.assertIn(("protocol_id", "protocol_version"), required_sets)

    def test_protocol_numbers_are_external_gates_not_internal_defaults(self) -> None:
        protocol = _protocol()
        protocol["requirements"]["min_records_by_split"]["train"] = 11
        with self.assertRaisesRegex(CalibrationFitError, "protocol gate failed"):
            fit_calibration_candidates(_dataset(), protocol)

    def test_missing_grade_is_mathematical_hard_failure_even_if_protocol_min_is_zero(self) -> None:
        dataset = _dataset()
        dataset["records"] = [
            record
            for record in dataset["records"]
            if not (record["split"] == "train" and record["grade"] == "A")
        ]
        _refresh_integrity(dataset)
        protocol = _protocol()
        protocol["requirements"]["min_records_by_split"]["train"] = 0
        protocol["requirements"]["min_records_per_grade_by_split"]["train"]["A"] = 0
        with self.assertRaisesRegex(CalibrationFitError, "all five grades"):
            fit_calibration_candidates(dataset, protocol)

    def test_majority_or_implicit_consensus_is_never_accepted(self) -> None:
        dataset = _dataset()
        dataset["records"][0]["consensus_status"] = "majority"
        with self.assertRaisesRegex(CalibrationFitError, "not an explicit unanimous"):
            fit_calibration_candidates(dataset, _protocol())

    def test_independent_test_rows_are_rejected_from_prepared_input(self) -> None:
        dataset = _dataset()
        exposed = copy.deepcopy(dataset["records"][0])
        exposed["record_id"] = "exposed-test-row"
        exposed["split"] = "independent_test"
        exposed["group_id"] = "group-independent"
        dataset["records"].append(exposed)
        with self.assertRaisesRegex(CalibrationFitError, "must remain sealed"):
            validate_prepared_dataset(dataset)

    def test_group_and_component_leakage_is_rejected(self) -> None:
        dataset = _dataset()
        dataset["records"][-1]["leakage_component_id"] = "group-train"
        with self.assertRaisesRegex(CalibrationFitError, "crosses"):
            fit_calibration_candidates(dataset, _protocol())

    def test_candidate_validator_rejects_test_performance_or_promotion_claim(self) -> None:
        candidate = fit_calibration_candidates(_dataset(), _protocol())[0]
        candidate["fit"]["independent_test_metrics"] = {"accuracy": 1.0}
        with self.assertRaisesRegex(CalibrationFitError, "must not contain"):
            validate_calibration_candidate(candidate)
        candidate = fit_calibration_candidates(_dataset(), _protocol())[0]
        candidate["promotion"]["scoring_allowed"] = True
        with self.assertRaisesRegex(CalibrationFitError, "cannot claim"):
            validate_calibration_candidate(candidate)

    def test_candidate_cannot_be_passed_to_production_scoring_contract(self) -> None:
        candidate = fit_calibration_candidates(_dataset(), _protocol())[0]
        feature = {
            "feature_name": "feature_a",
            "feature_version": "feature-a-v1",
            "value": 20.0,
            "unit": "body",
            "confidence": 1.0,
            "valid": True,
            "reason": "valid",
            "source_frames": [1],
        }
        result = score_indicator(
            indicator_id="FS01-M02",
            features=[feature],
            calibration=candidate,
            model_versions={"pose": "test", "event": "test", "feature": "test"},
            evidence=[],
            feasibility_level="F4",
        )
        self.assertEqual(result["status"], "calibration_required")
        self.assertIsNone(result["grade"])
        self.assertIsNone(result["threshold_version"])
        self.assertIn("calibration_candidate_not_promoted", result["reason_codes"])

    def test_cli_gate_failure_creates_no_output_directory_or_asset(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            dataset_path = directory / "prepared.json"
            protocol_path = directory / "protocol.json"
            output = directory / "must-not-exist"
            dataset = _dataset(test_only=False)
            dataset["agreement"]["status"] = "not_evaluated"
            dataset["agreement"]["value"] = None
            dataset_path.write_text(json.dumps(dataset), encoding="utf-8")
            protocol_path.write_text(
                json.dumps(_protocol(test_only=False)), encoding="utf-8"
            )
            process = subprocess.run(
                [
                    sys.executable,
                    str(root / "scripts" / "fit_calibration_candidate.py"),
                    "--prepared-dataset",
                    str(dataset_path),
                    "--fit-protocol",
                    str(protocol_path),
                    "--output-directory",
                    str(output),
                ],
                cwd=root,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(process.returncode, 2)
            self.assertIn("fit rejected", process.stderr)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
