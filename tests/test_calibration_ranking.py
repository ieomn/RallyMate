from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

try:
    from jsonschema import Draft202012Validator, FormatChecker
except ImportError:  # pragma: no cover
    Draft202012Validator = None
    FormatChecker = None

from rallymate_scoring.calibration_ranking import (
    RankingCalibrationError,
    canonical_sha256,
    evaluate_ranking_independent_test,
    fit_ranking_candidate,
    prepare_ranking_dataset,
    rank_candidate_items,
    validate_ranking_candidate,
    validate_ranking_dataset,
    validate_ranking_fit_protocol,
    validate_ranking_test_protocol,
    validate_ranking_test_report,
)
from rallymate_scoring.calibration_fitting import (
    CalibrationFitError,
    validate_calibration_candidate,
)


ROOT = Path(__file__).resolve().parents[1]
INDICATOR_ID = "FS01-M02"
FEATURES = ("hip_center_y_body", "stance_width_body")


def _label(sample: dict, coach: str, group: str, rank: int) -> dict:
    return {
        "schema_version": "1.0.0",
        "annotation_id": f"label-{sample['sample_id']}-{coach}",
        "video_id": sample["video_id"],
        "event_id": sample["event_id"],
        "indicator_id": INDICATOR_ID,
        "annotator_id": coach,
        "label_type": "ranking",
        "rank_group_id": group,
        "rank": rank,
    }


def _sample(split: str, index: int, value: float) -> dict:
    sample_id = f"sample-{split}-{index}"
    video_id = f"video-{split}-{index}"
    sample = {
        "schema_version": "1.0.0",
        "sample_id": sample_id,
        "dataset_version": "source-v1",
        "video_id": video_id,
        "event_id": f"event-{split}-{index}",
        "event_code": "FS01",
        "indicator_id": INDICATOR_ID,
        "person_track_id": 1,
        "split": split,
        "feature_vector": [
            {
                "feature_name": "hip_center_y_body",
                "feature_version": "feature-v1",
                "value": value,
                "unit": "body",
                "confidence": 0.95,
                "valid": True,
                "reason": "measured",
                "source_frames": [index],
            },
            {
                "feature_name": "stance_width_body",
                "feature_version": "feature-v1",
                "value": value * 0.25,
                "unit": "body",
                "confidence": 0.95,
                "valid": True,
                "reason": "measured",
                "source_frames": [index],
            },
        ],
        "feature_vector_complete": True,
        "manual_event": {"required_phases_complete": True},
        "semantics": {"fully_observable": True},
        "labels": {"grades": [], "rankings": []},
        "groups": {
            "player_id": f"player-{split}-{index}",
            "session_id": f"session-{split}-{index}",
            "view_group": "fixed-rear",
            "leakage_group_id": f"lg-{split}-{index}",
        },
    }
    return sample


def _samples() -> list[dict]:
    result = []
    for split in ("train", "validation", "independent_test"):
        group = f"rank-{split}"
        for index, value in enumerate((3.0, 2.0, 1.0), start=1):
            sample = _sample(split, index, value)
            for coach in ("coach-a", "coach-b"):
                sample["labels"]["rankings"].append(
                    _label(sample, coach, group, index)
                )
            result.append(sample)
    return result


def _dataset(samples: list[dict] | None = None) -> dict:
    return prepare_ranking_dataset(
        samples or _samples(),
        indicator_id=INDICATOR_ID,
        source_dataset_id="source-dataset",
        source_dataset_version="source-v1",
        source_kind="synthetic_test_fixture",
        prepared_at="2026-08-13T01:00:00Z",
    )


def _protocol(dataset: dict) -> dict:
    return {
        "schema_version": "1.0.0",
        "artifact_scope": "synthetic_test_only_ranking_fit_protocol",
        "protocol_id": "ranking-fit-protocol",
        "protocol_version": "ranking-fit-v1",
        "indicator_id": INDICATOR_ID,
        "source": {
            "kind": "synthetic_test_fixture",
            "source_sha256": "1" * 64,
            "registered_at": "2026-08-13T02:00:00Z",
        },
        "dataset_binding": {
            "dataset_id": dataset["dataset_id"],
            "dataset_version": dataset["dataset_version"],
            "content_sha256": canonical_sha256(dataset),
            "independent_test_seal_id": dataset["independent_test_seal"]["seal_id"],
            "independent_test_content_sha256": dataset["independent_test_seal"]["content_sha256"],
        },
        "target_semantics": {
            "target": "relative_order_only",
            "absolute_grade_anchor_present": False,
            "A_E_output_allowed": False,
        },
        "requirements": {
            "min_train_pairs": 1,
            "min_validation_pairs": 1,
            "min_independent_test_samples": 3,
            "min_annotators": 2,
            "min_shared_pairs": 1,
            "agreement_metric": "kendall_tau",
            "min_agreement_value": 0.5,
        },
        "backend": {
            "name": "pairwise_logistic_ranker",
            "include_equal_rank_ties": True,
            "optimizer": {
                "algorithm": "batch_gradient_descent_v1",
                "max_iterations": 2000,
                "learning_rate": 0.1,
                "l2": 0.001,
                "tolerance": 1e-10,
            },
        },
    }


def _candidate(dataset: dict | None = None) -> dict:
    dataset = dataset or _dataset()
    return fit_ranking_candidate(
        dataset, _protocol(dataset), fitted_at="2026-08-13T03:00:00Z"
    )


def _test_protocol(candidate: dict) -> dict:
    return {
        "schema_version": "1.0.0",
        "artifact_scope": "synthetic_test_only_ranking_independent_test_protocol",
        "protocol_id": "ranking-independent-test",
        "protocol_version": "ranking-independent-test-v1",
        "indicator_id": INDICATOR_ID,
        "source": {
            "kind": "synthetic_test_fixture",
            "source_sha256": "2" * 64,
            "registered_at": "2026-08-13T04:00:00Z",
        },
        "candidate_binding": {
            "candidate_id": candidate["candidate_id"],
            "candidate_version": candidate["candidate_version"],
            "candidate_sha256": canonical_sha256(candidate),
            "seal_id": candidate["independent_test"]["seal_id"],
            "seal_content_sha256": candidate["independent_test"]["content_sha256"],
        },
        "acceptance": {
            "min_evaluated_pairs": 1,
            "min_annotators": 2,
            "min_pairwise_accuracy": 0.5,
            "max_pairwise_log_loss": 1.0,
            "min_mean_kendall_tau": 0.5,
        },
        "safety": {
            "acceptance_applies_to": "relative_order_only",
            "A_E_grade_approval_possible": False,
            "production_scoring_approval_possible": False,
        },
    }


class RankingCalibrationTests(unittest.TestCase):
    def test_full_path_is_relative_only_and_seals_test(self) -> None:
        samples = _samples()
        dataset = _dataset(samples)
        validate_ranking_dataset(dataset, require_fit_ready=True)
        self.assertNotIn("independent_test", {item["split"] for item in dataset["items"]})
        test_ids = {
            sample["sample_id"] for sample in samples if sample["split"] == "independent_test"
        }
        self.assertFalse(test_ids & {item["sample_id"] for item in dataset["items"]})
        serialized = json.dumps(dataset)
        self.assertFalse(any(f"event-independent_test-{index}" in serialized for index in range(1, 4)))
        protocol = _protocol(dataset)
        validate_ranking_fit_protocol(protocol)
        candidate = fit_ranking_candidate(
            dataset, protocol, fitted_at="2026-08-13T03:00:00Z"
        )
        validate_ranking_candidate(candidate)
        self.assertIsNone(candidate["safety"]["grade"])
        self.assertIsNone(candidate["safety"]["threshold_version"])
        self.assertNotIn("thresholds", candidate)
        self.assertNotIn("cutpoints", candidate)
        self.assertEqual(candidate["target_semantics"]["target"], "relative_order_only")

        prediction = rank_candidate_items(
            candidate,
            [
                {
                    "item_id": "low",
                    "features": {FEATURES[0]: 1.0, FEATURES[1]: 0.25},
                    "unit_by_feature": candidate["unit_by_feature"],
                    "feature_version_by_feature": candidate[
                        "feature_version_by_feature"
                    ],
                },
                {
                    "item_id": "high",
                    "features": {FEATURES[0]: 3.0, FEATURES[1]: 0.75},
                    "unit_by_feature": candidate["unit_by_feature"],
                    "feature_version_by_feature": candidate[
                        "feature_version_by_feature"
                    ],
                },
            ],
        )
        self.assertEqual(prediction["status"], "candidate_relative_order_not_scored")
        self.assertIsNone(prediction["grade"])
        self.assertIsNone(prediction["threshold_version"])
        self.assertEqual(prediction["relative_order"][0]["item_id"], "high")
        self.assertIsNone(prediction["relative_order"][0]["confidence"])

        test_protocol = _test_protocol(candidate)
        validate_ranking_test_protocol(test_protocol)
        test_samples = [sample for sample in samples if sample["split"] == "independent_test"]
        report = evaluate_ranking_independent_test(
            candidate,
            test_protocol,
            test_samples,
            evaluated_at="2026-08-13T05:00:00Z",
        )
        validate_ranking_test_report(report)
        self.assertEqual(report["status"], "passed_relative_order_test")
        self.assertFalse(report["target_semantics"]["A_E_grade_approved"])
        self.assertFalse(report["target_semantics"]["production_scoring_approved"])
        self.assertIsNone(report["safety"]["grade"])

    def test_grade_labels_are_rejected_not_coerced_to_rank(self) -> None:
        samples = _samples()
        samples[0]["labels"]["grades"] = [
            {
                "schema_version": "1.0.0",
                "annotation_id": "grade-forbidden",
                "video_id": samples[0]["video_id"],
                "event_id": samples[0]["event_id"],
                "indicator_id": INDICATOR_ID,
                "annotator_id": "coach-a",
                "label_type": "grade",
                "grade": "A",
            }
        ]
        with self.assertRaisesRegex(RankingCalibrationError, "rejects grade labels"):
            _dataset(samples)

    def test_rank_group_must_not_cross_splits(self) -> None:
        samples = _samples()
        for sample in samples:
            for label in sample["labels"]["rankings"]:
                label["rank_group_id"] = "cross-split-rank-group"
        with self.assertRaisesRegex(RankingCalibrationError, "crosses train/validation/test"):
            _dataset(samples)

    def test_leakage_group_must_not_cross_splits(self) -> None:
        samples = _samples()
        samples[0]["groups"]["leakage_group_id"] = samples[3]["groups"]["leakage_group_id"]
        with self.assertRaisesRegex(RankingCalibrationError, "leakage group crosses"):
            _dataset(samples)

    def test_same_player_cannot_cross_splits_even_if_group_ids_are_forged(self) -> None:
        samples = _samples()
        samples[0]["groups"]["player_id"] = samples[3]["groups"]["player_id"]
        with self.assertRaisesRegex(RankingCalibrationError, "identity crosses"):
            _dataset(samples)

    def test_dataset_tampering_is_detected(self) -> None:
        dataset = _dataset()
        dataset["pairs"][0]["outcome"] = "right_preferred"
        with self.assertRaisesRegex(RankingCalibrationError, "do not exactly derive"):
            validate_ranking_dataset(dataset)

    def test_protocol_must_bind_exact_dataset(self) -> None:
        dataset = _dataset()
        protocol = _protocol(dataset)
        protocol["dataset_binding"]["content_sha256"] = "0" * 64
        with self.assertRaisesRegex(RankingCalibrationError, "exact dataset/seal"):
            fit_ranking_candidate(dataset, protocol, fitted_at="2026-08-13T03:00:00Z")

    def test_candidate_rejects_grade_parameters(self) -> None:
        candidate = _candidate()
        candidate["cutpoints"] = [1, 2, 3, 4]
        with self.assertRaisesRegex(RankingCalibrationError, "forbidden A-E"):
            validate_ranking_candidate(candidate)

    def test_ranking_candidate_is_rejected_by_absolute_grade_candidate_loader(self) -> None:
        candidate = _candidate()
        with self.assertRaises(CalibrationFitError):
            validate_calibration_candidate(candidate)

    def test_test_labels_and_protocol_seal_cannot_change(self) -> None:
        samples = _samples()
        dataset = _dataset(samples)
        candidate = _candidate(dataset)
        protocol = _test_protocol(candidate)
        test_samples = [copy.deepcopy(sample) for sample in samples if sample["split"] == "independent_test"]
        test_samples[0]["labels"]["rankings"][0]["rank"] = 2
        with self.assertRaisesRegex(RankingCalibrationError, "sealed hash"):
            evaluate_ranking_independent_test(
                candidate,
                protocol,
                test_samples,
                evaluated_at="2026-08-13T05:00:00Z",
            )

    def test_json_schemas_accept_artifacts_and_forbid_candidate_cutpoints(self) -> None:
        if Draft202012Validator is None:
            self.skipTest("jsonschema unavailable")
        samples = _samples()
        dataset = _dataset(samples)
        protocol = _protocol(dataset)
        candidate = fit_ranking_candidate(dataset, protocol, fitted_at="2026-08-13T03:00:00Z")
        test_protocol = _test_protocol(candidate)
        report = evaluate_ranking_independent_test(
            candidate,
            test_protocol,
            [sample for sample in samples if sample["split"] == "independent_test"],
            evaluated_at="2026-08-13T05:00:00Z",
        )
        artifacts = [
            ("calibration-ranking-dataset.schema.json", dataset),
            ("calibration-ranking-fit-protocol.schema.json", protocol),
            ("calibration-ranking-candidate.schema.json", candidate),
            ("calibration-ranking-independent-test-protocol.schema.json", test_protocol),
            ("calibration-ranking-independent-test-report.schema.json", report),
        ]
        for filename, artifact in artifacts:
            schema = json.loads((ROOT / "contracts" / filename).read_text(encoding="utf-8"))
            Draft202012Validator.check_schema(schema)
            Draft202012Validator(schema, format_checker=FormatChecker()).validate(artifact)
        bad = copy.deepcopy(candidate)
        bad["cutpoints"] = [1, 2, 3, 4]
        schema = json.loads((ROOT / "contracts" / "calibration-ranking-candidate.schema.json").read_text(encoding="utf-8"))
        self.assertTrue(list(Draft202012Validator(schema).iter_errors(bad)))

    def test_cli_fit_emits_relative_candidate_only(self) -> None:
        dataset = _dataset()
        protocol = _protocol(dataset)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dataset_path = root / "dataset.json"
            protocol_path = root / "protocol.json"
            output_path = root / "candidate.json"
            dataset_path.write_text(json.dumps(dataset), encoding="utf-8")
            protocol_path.write_text(json.dumps(protocol), encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "fit_ranking_calibration_candidate.py"),
                    "--dataset", str(dataset_path),
                    "--protocol", str(protocol_path),
                    "--fitted-at", "2026-08-13T03:00:00Z",
                    "--output", str(output_path),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            message = json.loads(completed.stdout)
            self.assertIsNone(message["grade"])
            self.assertIsNone(message["threshold_version"])
            candidate = json.loads(output_path.read_text(encoding="utf-8"))
            validate_ranking_candidate(candidate)

    def test_all_three_clis_run_end_to_end_without_A_E_output(self) -> None:
        samples = _samples()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            samples_path = root / "samples.jsonl"
            dataset_path = root / "dataset.json"
            protocol_path = root / "protocol.json"
            candidate_path = root / "candidate.json"
            test_samples_path = root / "test-samples.jsonl"
            test_protocol_path = root / "test-protocol.json"
            report_path = root / "report.json"
            samples_path.write_text(
                "".join(json.dumps(item) + "\n" for item in samples),
                encoding="utf-8",
            )
            prepare = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "prepare_ranking_calibration_dataset.py"),
                    "--samples", str(samples_path),
                    "--indicator-id", INDICATOR_ID,
                    "--source-dataset-id", "source-dataset",
                    "--source-dataset-version", "source-v1",
                    "--source-kind", "synthetic_test_fixture",
                    "--prepared-at", "2026-08-13T01:00:00Z",
                    "--output", str(dataset_path),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(prepare.returncode, 0, prepare.stderr)
            self.assertFalse(json.loads(prepare.stdout)["A_E_generated"])
            self.assertFalse(json.loads(prepare.stdout)["A_E_scoring_ready"])
            dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
            protocol = _protocol(dataset)
            protocol_path.write_text(json.dumps(protocol), encoding="utf-8")
            fit = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "fit_ranking_calibration_candidate.py"),
                    "--dataset", str(dataset_path),
                    "--protocol", str(protocol_path),
                    "--fitted-at", "2026-08-13T03:00:00Z",
                    "--output", str(candidate_path),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(fit.returncode, 0, fit.stderr)
            candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
            self.assertFalse(json.loads(fit.stdout)["A_E_scoring_ready"])
            test_protocol = _test_protocol(candidate)
            test_protocol_path.write_text(json.dumps(test_protocol), encoding="utf-8")
            test_samples = [
                item for item in samples if item["split"] == "independent_test"
            ]
            test_samples_path.write_text(
                "".join(json.dumps(item) + "\n" for item in test_samples),
                encoding="utf-8",
            )
            evaluate = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "evaluate_ranking_independent_test.py"),
                    "--candidate", str(candidate_path),
                    "--protocol", str(test_protocol_path),
                    "--samples", str(test_samples_path),
                    "--evaluated-at", "2026-08-13T05:00:00Z",
                    "--output", str(report_path),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(evaluate.returncode, 0, evaluate.stderr)
            message = json.loads(evaluate.stdout)
            self.assertFalse(message["A_E_grade_approved"])
            self.assertFalse(message["production_scoring_approved"])
            self.assertFalse(message["A_E_scoring_ready"])
            report = json.loads(report_path.read_text(encoding="utf-8"))
            validate_ranking_test_report(report)
            self.assertIsNone(report["safety"]["grade"])

    def test_without_ranking_truth_fit_leaves_no_candidate(self) -> None:
        samples = _samples()
        for sample in samples:
            sample["labels"]["rankings"] = []
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            samples_path = root / "samples.jsonl"
            dataset_path = root / "dataset.json"
            protocol_path = root / "protocol.json"
            candidate_path = root / "candidate.json"
            samples_path.write_text(
                "".join(json.dumps(item) + "\n" for item in samples),
                encoding="utf-8",
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "prepare_ranking_calibration_dataset.py"),
                    "--samples", str(samples_path),
                    "--indicator-id", INDICATOR_ID,
                    "--source-dataset-id", "source-dataset",
                    "--source-dataset-version", "source-v1",
                    "--source-kind", "synthetic_test_fixture",
                    "--prepared-at", "2026-08-13T01:00:00Z",
                    "--output", str(dataset_path),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
            self.assertEqual(dataset["readiness"]["status"], "insufficient")
            protocol = _protocol(dataset)
            protocol_path.write_text(json.dumps(protocol), encoding="utf-8")
            fit = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "fit_ranking_calibration_candidate.py"),
                    "--dataset", str(dataset_path),
                    "--protocol", str(protocol_path),
                    "--fitted-at", "2026-08-13T03:00:00Z",
                    "--output", str(candidate_path),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(fit.returncode, 2)
            self.assertIn("ranking candidate fit rejected", fit.stderr)
            self.assertFalse(candidate_path.exists())

    def test_fit_cli_protocol_gate_failure_leaves_no_candidate(self) -> None:
        dataset = _dataset()
        protocol = _protocol(dataset)
        protocol["requirements"]["min_train_pairs"] = 10_000
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dataset_path = root / "dataset.json"
            protocol_path = root / "protocol.json"
            output_path = root / "candidate.json"
            dataset_path.write_text(json.dumps(dataset), encoding="utf-8")
            protocol_path.write_text(json.dumps(protocol), encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "fit_ranking_calibration_candidate.py"),
                    "--dataset", str(dataset_path),
                    "--protocol", str(protocol_path),
                    "--fitted-at", "2026-08-13T03:00:00Z",
                    "--output", str(output_path),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 2)
            self.assertIn("ranking candidate fit rejected", completed.stderr)
            self.assertFalse(output_path.exists())


if __name__ == "__main__":
    unittest.main()
