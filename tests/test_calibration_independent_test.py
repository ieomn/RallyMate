from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jsonschema import Draft202012Validator, FormatChecker

from rallymate_scoring.calibration_fitting import (
    canonical_sha256,
    independent_test_seal_sha256,
)
from rallymate_scoring.calibration_independent_test import (
    IndependentTestError,
    evaluate_independent_test,
    validate_independent_test_sample,
    validate_independent_test_report,
)
from rallymate_scoring.indicator_feature_qualification import (
    IndicatorFeatureQualificationError,
    derive_feature_qualification,
)
from rallymate_scoring.quality_policy import evaluate_indicator_event_quality
from rallymate_scoring.scoring_truth_calibration_authorization import (
    SYNTHETIC_STATUS,
    build_nonproduction_truth_authorization_marker,
)


def _samples() -> list[dict]:
    rows = []
    for index, grade in enumerate(("E", "D", "C", "B", "A")):
        video_id = f"video-{index}"
        event_id = f"event-{index}"
        grade_labels = [
            {
                "schema_version": "1.0.0",
                "annotation_id": f"grade-c1-{index}",
                "video_id": video_id,
                "event_id": event_id,
                "indicator_id": "FS01-M02",
                "annotator_id": "c1",
                "label_type": "grade",
                "grade": grade,
            },
            {
                "schema_version": "1.0.0",
                "annotation_id": f"grade-c2-{index}",
                "video_id": video_id,
                "event_id": event_id,
                "indicator_id": "FS01-M02",
                "annotator_id": "c2",
                "label_type": "grade",
                "grade": grade,
            },
        ]
        identity = {
            "video_id": video_id,
            "event_id": event_id,
            "indicator_id": "FS01-M02",
            "person_track_id": 1,
        }
        rows.append(
            {
                "schema_version": "1.1.0",
                "sample_id": f"sample-{canonical_sha256(identity)[:20]}",
                "dataset_version": "v1",
                "video_id": video_id,
                "event_id": event_id,
                "event_code": "FS01",
                "indicator_id": "FS01-M02",
                "person_track_id": 1,
                "split": "independent_test",
                "feature_vector_complete": True,
                "readiness": {
                    "eligible_for_grade_modeling": True,
                    "eligible_for_grade_evaluation": True,
                    "ranking_labels_available": False,
                    "reason_codes": [],
                },
                "feature_vector": [
                    {
                        "feature_name": "feature_a",
                        "feature_version": "feature-a-v1",
                        "value": float(index * 10),
                        "unit": "body",
                        "confidence": 1.0,
                        "valid": True,
                        "reason": "valid",
                        "source_frames": [index],
                    }
                ],
                "qualification_snapshot": {
                    "snapshot_version": (
                        "calibration-feature-qualification-v1.0.0"
                    ),
                    "source_status": "verified_scoring_run_bundle_source",
                    "feature_record_present": True,
                    "feature_status": "measured",
                    "quality_policy_version": "indicator-event-quality-v1.6.0",
                    "quality_gate": evaluate_indicator_event_quality(
                        "FS01-M02", []
                    ),
                    "resolved_target_direction": False,
                    "source_feature_canonical_sha256": "b" * 64,
                    "source_metadata": {
                        "metadata_version": (
                            "indicator-feature-source-metadata-v1.0.0"
                        ),
                        "canonicalization": "rallymate-canonical-json-v1",
                        "video_id": video_id,
                        "indicator_features_raw_sha256": "d" * 64,
                        "indicator_feature_record_count": 1,
                        "scoring_summary_raw_sha256": "e" * 64,
                        "run_bundle_root_sha256": "f" * 64,
                        "run_bundle_entry_id": f"{video_id}:run-v1",
                        "run_bundle_ledger_id": "fixture-run-bundle-ledger",
                        "run_bundle_ledger_version": (
                            "fixture-run-bundle-ledger-v1"
                        ),
                        "run_bundle_ledger_canonical_sha256": "1" * 64,
                        "run_bundle_authority_id": "fixture-operator",
                    },
                },
                "manual_event": {
                    "start_ms": 0,
                    "end_ms": 1000,
                    "key_phases_ms": {},
                    "required_phase_keys": [],
                    "missing_required_phase_keys": [],
                    "required_phases_complete": True,
                    "boundary_uncertainty_ms": 0,
                    "annotator_id": "event-annotator",
                    "reviewer_id": "event-reviewer",
                    "adjudication_status": "accepted",
                },
                "semantics": {
                    "requirements_contract_status": "declared",
                    "required_keys": [],
                    "records": [],
                    "missing_keys": [],
                    "unobservable_keys": [],
                    "complete": True,
                    "fully_observable": True,
                },
                "labels": {
                    "grades": grade_labels,
                    "rankings": [],
                },
                "label_summary": {
                    "resolution_policy": "unanimous_multi_coach_only_v1",
                    "resolution_semantics": "synthetic-test-only unanimous labels",
                    "grade_status": "unanimous",
                    "resolved_grade": grade,
                    "contributing_annotation_ids": [
                        f"grade-c1-{index}",
                        f"grade-c2-{index}",
                    ],
                    "contributing_annotator_ids": ["c1", "c2"],
                    "conflicting_grades": [],
                    "grade_label_count": 2,
                    "ranking_label_count": 0,
                    "ranking_annotator_count": 0,
                },
                "groups": {
                    "player_id": f"player-{index}",
                    "session_id": f"session-{index}",
                    "view_group": "fixed-camera",
                    "leakage_group_id": f"lg-{index:016x}",
                },
                "lineage": {
                    "manual_event_sha256": "a" * 64,
                    "indicator_feature_sha256": "b" * 64,
                    "semantic_truth_sha256": canonical_sha256([]),
                    "coach_labels_sha256": canonical_sha256(grade_labels),
                    "registry_indicator_sha256": "c" * 64,
                    "join_key": {
                        "video_id": video_id,
                        "event_id": event_id,
                        "indicator_id": "FS01-M02",
                    },
                    "join_policy": "exact_manual_event_id_only_v1",
                },
            }
        )
    return rows


def _candidate(samples: list[dict]) -> dict:
    content_sha = independent_test_seal_sha256(samples)
    truth_authorization = build_nonproduction_truth_authorization_marker(
        status=SYNTHETIC_STATUS,
        input_files={
            "intake_manifest": "1" * 64,
            "truth_manifest": "2" * 64,
            "truth_validation_report": "3" * 64,
            "manual_events": "4" * 64,
            "manual_semantics": "5" * 64,
            "coach_labels": "6" * 64,
        },
    )
    return {
        "schema_version": "1.0.0",
        "artifact_scope": "synthetic_test_only_calibration_candidate",
        "candidate_id": "candidate-threshold-v1",
        "candidate_version": "candidate-threshold-v1",
        "backend": "threshold_rule",
        "indicator_id": "FS01-M02",
        "source": "coach_ground_truth_calibration_candidate",
        "truth_authorization": truth_authorization,
        "target_semantics": {
            "label_scale_order": ["E", "D", "C", "B", "A"],
            "grade_numeric_mapping": {"E": 0, "D": 1, "C": 2, "B": 3, "A": 4},
            "label_resolution_policy": "unanimous_multi_coach_only_v1",
        },
        "prepared_dataset": {
            "artifact_scope": "synthetic_test_only_calibration_input",
            "label_resolution_policy": "unanimous_multi_coach_only_v1",
            "dataset_id": "synthetic-independent",
            "dataset_version": "v1",
            "content_sha256": "a" * 64,
            "source_sha256": "b" * 64,
            "truth_authorization_binding_sha256": truth_authorization[
                "binding_sha256"
            ],
        },
        "fit_protocol": {
            "artifact_scope": "synthetic_test_only_fit_protocol",
            "label_resolution_policy": "unanimous_multi_coach_only_v1",
            "protocol_id": "synthetic-fit",
            "protocol_version": "v1",
            "content_sha256": "c" * 64,
            "source_sha256": "d" * 64,
        },
        "independent_test": {
            "status": "sealed_not_evaluated",
            "approved_for_scoring": False,
            "accessed_during_fit": False,
            "seal_id": f"seal-{content_sha[:16]}",
            "record_count": len(samples),
            "groups": sorted(row["groups"]["leakage_group_id"] for row in samples),
            "sample_ids": sorted(row["sample_id"] for row in samples),
            "content_sha256": content_sha,
            "canonicalization": "rallymate-canonical-json-v1",
        },
        "promotion": {
            "scoring_allowed": False,
            "F3_promoted": False,
            "F4_promoted": False,
            "reason": "independent_test_and_explicit_promotion_required",
        },
        "primary_feature": "feature_a",
        "primary_feature_version": "feature-a-v1",
        "unit": "body",
        "direction": "higher_is_better",
        "thresholds": [5.0, 15.0, 25.0, 35.0],
        "fit": {
            "method": "synthetic-test-thresholds-v1",
            "objective": "mean_absolute_grade_error",
            "train_metrics": {},
            "validation_metrics": {},
            "independent_test_metrics": None,
        },
    }


def _protocol() -> dict:
    return {
        "schema_version": "1.0.0",
        "artifact_scope": "synthetic_test_only_independent_test_protocol",
        "protocol_id": "synthetic-independent-protocol",
        "protocol_version": "v1",
        "indicator_id": "FS01-M02",
        "source": {
            "kind": "synthetic_test_fixture",
            "source_sha256": "e" * 64,
            "registered_at": "2026-08-13T00:00:00Z",
        },
        "label_scale": ["E", "D", "C", "B", "A"],
        "label_resolution_policy": "unanimous_multi_coach_only_v1",
        "acceptance_metrics": {
            "minimum_record_count": 5,
            "minimum_leakage_group_count": 5,
            "minimum_valid_rate": 1.0,
            "maximum_mean_absolute_grade_error": 0.0,
            "maximum_absolute_bias_grade_steps": 0.0,
            "minimum_quadratic_weighted_kappa": 1.0,
            "required_grade_coverage": ["E", "D", "C", "B", "A"],
            "required_view_groups": ["fixed-camera"],
        },
    }


def _requirements(
    *,
    features: list[str] | None = None,
    phases: list[str] | None = None,
    semantics: list[str] | None = None,
) -> dict:
    return {
        "schema_version": "1.0.0",
        "artifact_scope": "synthetic_test_only_indicator_scoring_requirements",
        "requirements_version": "synthetic-requirements-v1",
        "source": {
            "kind": "synthetic_test_fixture",
            "registry_version": "synthetic-registry-v1",
            "registry_content_sha256": "d" * 64,
            "semantic_contract_version": "synthetic-semantics-v1",
            "semantic_contract_sha256": "e" * 64,
            "canonicalization": "rallymate-canonical-json-v1",
        },
        "indicators": [
            {
                "indicator_id": "FS01-M02",
                "required_feature_names": features or ["feature_a"],
                "required_phase_keys": phases or [],
                "required_semantic_keys": semantics or [],
                "registry_indicator_sha256": "c" * 64,
            }
        ],
    }


def _external_samples() -> list[dict]:
    samples = _samples()
    for sample in samples:
        grade_labels = sample["labels"]["grades"]
        source_ids = [item["annotation_id"] for item in grade_labels]
        sample["label_summary"].update(
            {
                "resolution_policy": "external_adjudicated_v1",
                "resolution_semantics": "synthetic-test-only external adjudication",
                "grade_status": "adjudicated",
            }
        )
        sample["adjudication"] = {
            "decision_id": f"decision-{sample['event_id']}",
            "adjudicator_id": "independent-adjudicator",
            "decided_at": "2026-08-13T00:30:00Z",
            "source_label_ids": source_ids,
            "source_sha256": canonical_sha256(grade_labels),
            "grade": sample["label_summary"]["resolved_grade"],
        }
    return samples


def _external_candidate(samples: list[dict]) -> dict:
    candidate = _candidate(samples)
    candidate["target_semantics"]["label_resolution_policy"] = (
        "external_adjudicated_v1"
    )
    candidate["prepared_dataset"]["label_resolution_policy"] = (
        "external_adjudicated_v1"
    )
    candidate["fit_protocol"]["label_resolution_policy"] = (
        "external_adjudicated_v1"
    )
    return candidate


def _external_protocol() -> dict:
    protocol = _protocol()
    protocol["label_resolution_policy"] = "external_adjudicated_v1"
    return protocol


class CalibrationIndependentTestTests(unittest.TestCase):
    def test_real_sample_requires_v11_verified_qualification_snapshot(self) -> None:
        sample = _samples()[0]
        requirements = _requirements()["indicators"][0]
        validate_independent_test_sample(
            sample,
            expected_indicator_id="FS01-M02",
            expected_dataset_version="v1",
            expected_requirements=requirements,
            require_verified_qualification=True,
        )

        legacy = copy.deepcopy(sample)
        legacy["schema_version"] = "1.0.0"
        del legacy["qualification_snapshot"]
        with self.assertRaisesRegex(
            IndependentTestError, "requires schema_version 1.1.0"
        ):
            validate_independent_test_sample(
                legacy,
                expected_indicator_id="FS01-M02",
                expected_dataset_version="v1",
                expected_requirements=requirements,
                require_verified_qualification=True,
            )

        nonverified = copy.deepcopy(sample)
        nonverified["qualification_snapshot"]["source_status"] = (
            "synthetic_test_only_source"
        )
        with self.assertRaisesRegex(
            IndependentTestError, "requires verified feature source metadata"
        ):
            validate_independent_test_sample(
                nonverified,
                expected_indicator_id="FS01-M02",
                expected_dataset_version="v1",
                expected_requirements=requirements,
                require_verified_qualification=True,
            )

    def test_real_sample_missing_quality_gate_is_recomputed_as_ineligible(self) -> None:
        sample = _samples()[0]
        requirements = _requirements()["indicators"][0]
        sample["qualification_snapshot"]["quality_gate"] = None
        with self.assertRaisesRegex(
            IndependentTestError, "feature_vector_complete.*recomputed qualification"
        ):
            validate_independent_test_sample(
                sample,
                expected_indicator_id="FS01-M02",
                expected_dataset_version="v1",
                expected_requirements=requirements,
                require_verified_qualification=True,
            )

        sample["feature_vector_complete"] = False
        sample["readiness"].update(
            {
                "eligible_for_grade_modeling": False,
                "eligible_for_grade_evaluation": False,
                "reason_codes": [
                    "quality_gate_missing",
                    "required_feature_vector_incomplete",
                ],
            }
        )
        validate_independent_test_sample(
            sample,
            expected_indicator_id="FS01-M02",
            expected_dataset_version="v1",
            expected_requirements=requirements,
            require_verified_qualification=True,
        )

    def test_qualification_record_sha_and_ledger_digest_are_required(self) -> None:
        requirements = _requirements()["indicators"][0]
        sample = _samples()[0]
        sample["lineage"]["indicator_feature_sha256"] = "a" * 64
        with self.assertRaisesRegex(
            IndependentTestError, "feature-record SHA.*sample lineage"
        ):
            validate_independent_test_sample(
                sample,
                expected_indicator_id="FS01-M02",
                expected_dataset_version="v1",
                expected_requirements=requirements,
                require_verified_qualification=True,
            )

        sample = _samples()[0]
        del sample["qualification_snapshot"]["source_metadata"][
            "run_bundle_ledger_canonical_sha256"
        ]
        with self.assertRaisesRegex(
            IndependentTestError, "source metadata fields are invalid"
        ):
            validate_independent_test_sample(
                sample,
                expected_indicator_id="FS01-M02",
                expected_dataset_version="v1",
                expected_requirements=requirements,
                require_verified_qualification=True,
            )

    def test_resolved_target_direction_removes_only_its_saved_scoring_block(self) -> None:
        snapshot = copy.deepcopy(_samples()[0]["qualification_snapshot"])
        snapshot.update(
            {
                "quality_gate": evaluate_indicator_event_quality(
                    "FS02-M02", ["tactical_target_direction_not_observed"]
                ),
                "resolved_target_direction": True,
            }
        )
        feature_vector = [
            {
                "feature_name": "target_direction_alignment_error_deg",
                "feature_version": "target-direction-alignment-v1.0.0",
                "value": 8.0,
                "unit": "deg",
                "confidence": 1.0,
                "valid": True,
                "reason": "accepted_manual_target_direction",
                "source_frames": [10],
            }
        ]
        complete, reasons = derive_feature_qualification(
            snapshot=snapshot,
            indicator_id="FS02-M02",
            feature_vector=feature_vector,
            require_verified_source=True,
        )
        self.assertTrue(complete)
        self.assertEqual(reasons, [])

        snapshot["resolved_target_direction"] = False
        with self.assertRaisesRegex(
            IndicatorFeatureQualificationError,
            "resolved_target_direction differs from feature vector",
        ):
            derive_feature_qualification(
                snapshot=snapshot,
                indicator_id="FS02-M02",
                feature_vector=feature_vector,
                require_verified_source=True,
            )

    def test_sealed_test_passes_explicit_protocol_without_approving_scoring(self) -> None:
        samples = _samples()
        report = evaluate_independent_test(
            candidate=_candidate(samples),
            samples=samples,
            protocol=_protocol(),
            evaluated_at="2026-08-13T01:00:00Z",
        )
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["schema_version"], "1.2.0")
        self.assertFalse(report["approved_for_scoring"])
        self.assertEqual(report["metrics"]["overall"]["accuracy"], 1.0)
        self.assertEqual(set(report["metrics"]["by_player"]), {f"player-{i}" for i in range(5)})
        self.assertFalse(report["promotion"]["production_asset_created"])
        self.assertEqual(report["exclusions"], [])
        root = Path(__file__).resolve().parents[1]
        schema = json.loads(
            (root / "contracts/calibration-independent-test-report.schema.json").read_text(
                encoding="utf-8"
            )
        )
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(
            schema, format_checker=FormatChecker()
        ).validate(report)

    def test_full_report_reconciliation_rejects_internal_or_protocol_drift(self) -> None:
        samples = _samples()
        candidate = _candidate(samples)
        protocol = _protocol()
        report = evaluate_independent_test(
            candidate=candidate,
            samples=samples,
            protocol=protocol,
            evaluated_at="2026-08-13T01:00:00Z",
        )
        mutations = (
            (
                "coverage",
                lambda value: value["coverage"].__setitem__(
                    "evaluated_record_count", 4
                ),
            ),
            (
                "metrics",
                lambda value: value["metrics"]["overall"].__setitem__(
                    "accuracy", 0.8
                ),
            ),
            (
                "prediction",
                lambda value: value["predictions"][0].__setitem__(
                    "predicted_grade", "A"
                ),
            ),
            (
                "acceptance",
                lambda value: value["acceptance"].__setitem__("checks", []),
            ),
        )
        for label, mutate in mutations:
            drifted = copy.deepcopy(report)
            mutate(drifted)
            with self.subTest(label=label), self.assertRaises(IndependentTestError):
                validate_independent_test_report(
                    drifted,
                    protocol=protocol,
                    candidate=candidate,
                    require_nonempty_evaluation=True,
                )

        drifted_protocol = copy.deepcopy(protocol)
        drifted_protocol["acceptance_metrics"]["minimum_record_count"] = 4
        with self.assertRaisesRegex(
            IndependentTestError, "does not bind the supplied independent-test protocol"
        ):
            validate_independent_test_report(
                report,
                protocol=drifted_protocol,
                candidate=candidate,
                require_nonempty_evaluation=True,
            )

    def test_new_coverage_contract_rejects_legacy_fields_and_rate_drift(self) -> None:
        samples = _samples()
        candidate = _candidate(samples)
        protocol = _protocol()
        report = evaluate_independent_test(
            candidate=candidate,
            samples=samples,
            protocol=protocol,
            evaluated_at="2026-08-13T01:00:00Z",
        )
        mutations = (
            (
                "legacy-field",
                lambda value: (
                    value["coverage"].pop("sealed_record_count"),
                    value["coverage"].__setitem__("eligible_record_count", 5),
                ),
            ),
            (
                "completion-rate",
                lambda value: value["coverage"].__setitem__(
                    "eligible_evaluation_completion_rate", 0.9
                ),
            ),
            (
                "free-text-exclusion",
                lambda value: value["exclusions"].append(
                    {
                        "sample_id": "not-a-sealed-sample",
                        "classification": "not_evaluation_eligible",
                        "reason": "free text is not evidence",
                        "groups": value["predictions"][0]["groups"],
                    }
                ),
            ),
        )
        for label, mutate in mutations:
            drifted = copy.deepcopy(report)
            mutate(drifted)
            with self.subTest(label=label), self.assertRaises(IndependentTestError):
                validate_independent_test_report(
                    drifted,
                    protocol=protocol,
                    candidate=candidate,
                )

    def test_complete_eligible_coverage_is_required_for_production_validation(self) -> None:
        samples = _samples()
        candidate = _candidate(samples)
        protocol = _protocol()
        protocol["acceptance_metrics"].update(
            {
                "minimum_record_count": 4,
                "minimum_leakage_group_count": 4,
                "minimum_valid_rate": 0.8,
                "required_grade_coverage": ["E", "D", "C", "B"],
            }
        )
        report = evaluate_independent_test(
            candidate=candidate,
            samples=samples,
            protocol=protocol,
            evaluated_at="2026-08-13T01:00:00Z",
        )
        missing = report["predictions"].pop()
        report["exclusions"] = [
            {
                "sample_id": missing["sample_id"],
                "classification": "not_evaluation_eligible",
                "reason_codes": ["synthetic_missing_prediction"],
                "groups": missing["groups"],
            }
        ]
        report["coverage"].update(
            {
                "evaluation_eligible_record_count": 5,
                "evaluated_record_count": 4,
                "valid_rate": 0.8,
                "evaluation_eligible_rate": 1.0,
                "eligible_evaluation_completion_rate": 0.8,
                "excluded_record_count": 1,
                "exclusion_reason_counts": {"synthetic_missing_prediction": 1},
            }
        )
        with self.assertRaisesRegex(
            IndependentTestError, "complete non-empty evaluation-eligible coverage"
        ):
            validate_independent_test_report(
                report,
                protocol=protocol,
                candidate=candidate,
                require_nonempty_evaluation=True,
            )

        no_eligible = copy.deepcopy(report)
        no_eligible["coverage"].update(
            {
                "evaluation_eligible_record_count": 0,
                "evaluated_record_count": 0,
                "valid_rate": 0.0,
                "evaluation_eligible_rate": 0.0,
                "eligible_evaluation_completion_rate": 0.0,
                "excluded_record_count": 5,
            }
        )
        no_eligible["predictions"] = []
        no_eligible["exclusions"] = [
            {
                "sample_id": sample_id,
                "classification": "not_evaluation_eligible",
                "reason_codes": ["synthetic_not_eligible"],
                "groups": next(
                    row["groups"]
                    for row in report["predictions"] + [missing]
                    if row["sample_id"] == sample_id
                ),
            }
            for sample_id in candidate["independent_test"]["sample_ids"]
        ]
        no_eligible["coverage"]["exclusion_reason_counts"] = {
            "synthetic_not_eligible": 5
        }
        with self.assertRaisesRegex(
            IndependentTestError, "complete non-empty evaluation-eligible coverage"
        ):
            validate_independent_test_report(
                no_eligible,
                protocol=protocol,
                candidate=candidate,
                require_nonempty_evaluation=True,
            )

    def test_candidate_error_for_eligible_sample_never_becomes_an_exclusion(self) -> None:
        samples = _samples()
        with patch(
            "rallymate_scoring.calibration_independent_test.predict_candidate",
            side_effect=KeyError("forced"),
        ):
            with self.assertRaisesRegex(
                IndependentTestError,
                "candidate_prediction_error for evaluation-eligible sample",
            ):
                evaluate_independent_test(
                    candidate=_candidate(samples),
                    samples=samples,
                    protocol=_protocol(),
                    evaluated_at="2026-08-13T01:00:00Z",
                )

    def test_tampered_sealed_sample_is_rejected_before_metrics(self) -> None:
        samples = _samples()
        candidate = _candidate(samples)
        samples[0]["feature_vector"][0]["value"] = 999.0
        with self.assertRaisesRegex(IndependentTestError, "content hash mismatch"):
            evaluate_independent_test(
                candidate=candidate,
                samples=samples,
                protocol=_protocol(),
                evaluated_at="2026-08-13T01:00:00Z",
            )

    def test_protocol_limits_are_external_and_failure_stays_unapproved(self) -> None:
        samples = _samples()
        protocol = _protocol()
        protocol["acceptance_metrics"]["minimum_record_count"] = 6
        report = evaluate_independent_test(
            candidate=_candidate(samples),
            samples=samples,
            protocol=protocol,
            evaluated_at="2026-08-13T01:00:00Z",
        )
        self.assertEqual(report["status"], "failed")
        self.assertFalse(report["approved_for_scoring"])
        failed = [item for item in report["acceptance"]["checks"] if not item["passed"]]
        self.assertEqual([item["metric"] for item in failed], ["minimum_record_count"])

    def test_quality_blocked_numeric_feature_never_enters_test_metrics(self) -> None:
        samples = _samples()
        candidate = _candidate(samples)
        samples[0]["feature_vector_complete"] = False
        samples[0]["qualification_snapshot"]["feature_status"] = "unavailable"
        samples[0]["qualification_snapshot"]["quality_gate"] = (
            evaluate_indicator_event_quality(
                "FS01-M02", ["primary_track_coverage_low"]
            )
        )
        samples[0]["readiness"] = {
            "eligible_for_grade_modeling": False,
            "eligible_for_grade_evaluation": False,
            "ranking_labels_available": False,
            "reason_codes": [
                "indicator_feature_status_unavailable",
                "quality_gate_hard_fail",
                "quality_gate_measurement_disallowed",
                "required_feature_vector_incomplete",
            ],
        }
        # Re-seal after the simulated upstream quality decision. The evaluator must
        # still exclude the retained diagnostic number rather than predict from it.
        resealed = independent_test_seal_sha256(samples)
        candidate["independent_test"]["content_sha256"] = resealed
        candidate["independent_test"]["seal_id"] = f"seal-{resealed[:16]}"
        protocol = _protocol()
        protocol["acceptance_metrics"].update(
            {
                "minimum_record_count": 4,
                "minimum_leakage_group_count": 4,
                "minimum_valid_rate": 0.8,
                "required_grade_coverage": ["D", "C", "B", "A"],
            }
        )
        report = evaluate_independent_test(
            candidate=candidate,
            samples=samples,
            protocol=protocol,
            evaluated_at="2026-08-13T01:00:00Z",
        )
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["coverage"]["evaluated_record_count"], 4)
        self.assertEqual(report["coverage"]["sealed_record_count"], 5)
        self.assertEqual(report["coverage"]["evaluation_eligible_record_count"], 4)
        self.assertEqual(report["coverage"]["valid_rate"], 0.8)
        self.assertEqual(report["coverage"]["evaluation_eligible_rate"], 0.8)
        self.assertEqual(
            report["coverage"]["eligible_evaluation_completion_rate"], 1.0
        )
        self.assertEqual(
            report["coverage"]["exclusion_reason_counts"],
            {
                "indicator_feature_status_unavailable": 1,
                "quality_gate_hard_fail": 1,
                "quality_gate_measurement_disallowed": 1,
                "required_feature_vector_incomplete": 1,
            },
        )
        self.assertNotIn(
            samples[0]["sample_id"],
            {row["sample_id"] for row in report["predictions"]},
        )
        self.assertEqual(
            report["exclusions"],
            [
                {
                    "sample_id": samples[0]["sample_id"],
                    "classification": "not_evaluation_eligible",
                    "reason_codes": [
                        "indicator_feature_status_unavailable",
                        "quality_gate_hard_fail",
                        "quality_gate_measurement_disallowed",
                        "required_feature_vector_incomplete",
                    ],
                    "groups": samples[0]["groups"],
                }
            ],
        )

    def test_protocol_label_semantics_must_match_candidate(self) -> None:
        samples = _samples()
        protocol = _protocol()
        protocol["label_resolution_policy"] = "external_adjudicated_v1"
        with self.assertRaisesRegex(
            IndependentTestError, "label_resolution_policy mismatch"
        ):
            evaluate_independent_test(
                candidate=_candidate(samples),
                samples=samples,
                protocol=protocol,
                evaluated_at="2026-08-13T01:00:00Z",
            )

    def test_unanimous_summary_cannot_replace_missing_raw_coach_labels(self) -> None:
        samples = _samples()
        samples[0]["labels"]["grades"] = []
        samples[0]["lineage"]["coach_labels_sha256"] = canonical_sha256([])
        with self.assertRaisesRegex(IndependentTestError, "raw labels"):
            evaluate_independent_test(
                candidate=_candidate(samples),
                samples=samples,
                protocol=_protocol(),
                evaluated_at="2026-08-13T01:00:00Z",
            )

    def test_two_grade_records_from_one_coach_cannot_fake_unique_coaches(self) -> None:
        samples = _samples()
        samples[0]["labels"]["grades"][1]["annotator_id"] = "c1"
        samples[0]["lineage"]["coach_labels_sha256"] = canonical_sha256(
            samples[0]["labels"]["grades"]
        )
        with self.assertRaisesRegex(IndependentTestError, "multiple grades"):
            evaluate_independent_test(
                candidate=_candidate(samples),
                samples=samples,
                protocol=_protocol(),
                evaluated_at="2026-08-13T01:00:00Z",
            )

    def test_forged_contributing_ids_are_recomputed_from_raw_labels(self) -> None:
        samples = _samples()
        samples[0]["label_summary"]["contributing_annotation_ids"] = [
            "forged-label-a",
            "forged-label-b",
        ]
        with self.assertRaisesRegex(
            IndependentTestError, "contributing_annotation_ids.*raw labels"
        ):
            evaluate_independent_test(
                candidate=_candidate(samples),
                samples=samples,
                protocol=_protocol(),
                evaluated_at="2026-08-13T01:00:00Z",
            )

    def test_external_adjudication_binds_every_raw_source_label(self) -> None:
        samples = _external_samples()
        report = evaluate_independent_test(
            candidate=_external_candidate(samples),
            samples=samples,
            protocol=_external_protocol(),
            evaluated_at="2026-08-13T01:00:00Z",
        )
        self.assertEqual(report["status"], "passed")
        samples[0]["adjudication"]["source_label_ids"] = [
            samples[0]["labels"]["grades"][0]["annotation_id"]
        ]
        with self.assertRaisesRegex(IndependentTestError, "exactly bind raw grade labels"):
            evaluate_independent_test(
                candidate=_external_candidate(samples),
                samples=samples,
                protocol=_external_protocol(),
                evaluated_at="2026-08-13T01:00:00Z",
            )

        samples = _external_samples()
        samples[0]["adjudication"]["source_sha256"] = "f" * 64
        with self.assertRaisesRegex(IndependentTestError, "does not bind raw grade"):
            evaluate_independent_test(
                candidate=_external_candidate(samples),
                samples=samples,
                protocol=_external_protocol(),
                evaluated_at="2026-08-13T01:00:00Z",
            )

    def test_valid_seal_does_not_bypass_complete_sample_contract(self) -> None:
        samples = _samples()
        samples[0]["manual_event"]["reviewer_id"] = ""
        with self.assertRaisesRegex(
            IndependentTestError, "sample.manual_event.reviewer_id"
        ):
            evaluate_independent_test(
                candidate=_candidate(samples),
                samples=samples,
                protocol=_protocol(),
                evaluated_at="2026-08-13T01:00:00Z",
            )

    def test_missing_phase_cannot_be_resealed_as_evaluation_eligible(self) -> None:
        samples = _samples()
        samples[0]["manual_event"].update(
            {
                "key_phases_ms": {"preload_ms": None},
                "required_phase_keys": ["preload_ms"],
                "missing_required_phase_keys": ["preload_ms"],
                "required_phases_complete": False,
            }
        )
        with self.assertRaisesRegex(
            IndependentTestError, "required_phase_keys.*authoritative"
        ):
            evaluate_independent_test(
                candidate=_candidate(samples),
                samples=samples,
                protocol=_protocol(),
                evaluated_at="2026-08-13T01:00:00Z",
            )

    def test_missing_semantic_cannot_be_resealed_as_evaluation_eligible(self) -> None:
        samples = _samples()
        samples[0]["semantics"].update(
            {
                "required_keys": ["target_direction"],
                "missing_keys": ["target_direction"],
                "complete": False,
                "fully_observable": False,
            }
        )
        with self.assertRaisesRegex(
            IndependentTestError, "required_keys.*authoritative"
        ):
            evaluate_independent_test(
                candidate=_candidate(samples),
                samples=samples,
                protocol=_protocol(),
                evaluated_at="2026-08-13T01:00:00Z",
            )

    def test_resealed_sample_cannot_shrink_authoritative_requirements(self) -> None:
        cases = (
            (
                _requirements(phases=["preload_ms"]),
                "required_phase_keys does not match authoritative",
            ),
            (
                _requirements(semantics=["target_direction"]),
                "required_keys does not match authoritative",
            ),
            (
                _requirements(features=["feature_a", "feature_b"]),
                "required features",
            ),
        )
        for requirements, message in cases:
            with self.subTest(message=message):
                samples = _samples()
                with self.assertRaisesRegex(IndependentTestError, message):
                    evaluate_independent_test(
                        candidate=_candidate(samples),
                        samples=samples,
                        protocol=_protocol(),
                        indicator_requirements=requirements,
                        evaluated_at="2026-08-13T01:00:00Z",
                    )

    def test_missing_candidate_provenance_id_is_controlled_rejection(self) -> None:
        samples = _samples()
        candidate = _candidate(samples)
        del candidate["prepared_dataset"]["dataset_id"]
        with self.assertRaisesRegex(
            IndependentTestError, "prepared_dataset.dataset_id"
        ):
            evaluate_independent_test(
                candidate=candidate,
                samples=samples,
                protocol=_protocol(),
                evaluated_at="2026-08-13T01:00:00Z",
            )

    def test_protocol_template_cannot_be_mistaken_for_registered_limits(self) -> None:
        root = Path(__file__).resolve().parents[1]
        template = json.loads(
            (
                root
                / "examples"
                / "calibration-independent-test-protocol.template.json"
            ).read_text(encoding="utf-8")
        )
        with self.assertRaises(IndependentTestError):
            evaluate_independent_test(
                candidate=_candidate(_samples()),
                samples=_samples(),
                protocol=template,
                evaluated_at="2026-08-13T01:00:00Z",
            )

    def test_cli_refuses_to_overwrite_report(self) -> None:
        root = Path(__file__).resolve().parents[1]
        samples = _samples()
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            candidate_path = work / "candidate.json"
            samples_path = work / "samples.jsonl"
            protocol_path = work / "protocol.json"
            requirements_path = work / "requirements.json"
            output_path = work / "report.json"
            candidate_path.write_text(json.dumps(_candidate(samples)), encoding="utf-8")
            samples_path.write_text(
                "".join(json.dumps(row) + "\n" for row in samples), encoding="utf-8"
            )
            protocol_path.write_text(json.dumps(_protocol()), encoding="utf-8")
            requirements_path.write_text(json.dumps(_requirements()), encoding="utf-8")
            command = [
                sys.executable,
                str(root / "scripts" / "evaluate_calibration_independent_test.py"),
                "--candidate",
                str(candidate_path),
                "--samples",
                str(samples_path),
                "--protocol",
                str(protocol_path),
                "--indicator-requirements",
                str(requirements_path),
                "--evaluated-at",
                "2026-08-13T01:00:00Z",
                "--output",
                str(output_path),
            ]
            first = subprocess.run(command, cwd=root, capture_output=True, text=True)
            self.assertEqual(first.returncode, 0, first.stderr)
            second = subprocess.run(command, cwd=root, capture_output=True, text=True)
            self.assertEqual(second.returncode, 2)
            self.assertIn("refusing to overwrite", second.stderr)

    def test_cli_missing_candidate_provenance_is_clean_rejection(self) -> None:
        root = Path(__file__).resolve().parents[1]
        samples = _samples()
        candidate = _candidate(samples)
        del candidate["fit_protocol"]["protocol_version"]
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            candidate_path = work / "candidate.json"
            samples_path = work / "samples.jsonl"
            protocol_path = work / "protocol.json"
            requirements_path = work / "requirements.json"
            output_path = work / "report.json"
            candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
            samples_path.write_text(
                "".join(json.dumps(row) + "\n" for row in samples), encoding="utf-8"
            )
            protocol_path.write_text(json.dumps(_protocol()), encoding="utf-8")
            requirements_path.write_text(json.dumps(_requirements()), encoding="utf-8")
            process = subprocess.run(
                [
                    sys.executable,
                    str(root / "scripts/evaluate_calibration_independent_test.py"),
                    "--candidate",
                    str(candidate_path),
                    "--samples",
                    str(samples_path),
                    "--protocol",
                    str(protocol_path),
                    "--indicator-requirements",
                    str(requirements_path),
                    "--evaluated-at",
                    "2026-08-13T01:00:00Z",
                    "--output",
                    str(output_path),
                ],
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(process.returncode, 2)
            self.assertIn("fit_protocol.protocol_version", process.stderr)
            self.assertNotIn("Traceback", process.stderr)
            self.assertFalse(output_path.exists())


if __name__ == "__main__":
    unittest.main()
