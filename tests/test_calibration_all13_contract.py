from __future__ import annotations

import json
import unittest
from collections import Counter
from pathlib import Path

from rallymate_annotation.truth_pack import (
    SEMANTIC_REQUIREMENTS_BY_INDICATOR,
)
from rallymate_features.event_features import FEATURE_DEFINITIONS
from rallymate_scoring.calibration_fitting import (
    CalibrationFitError,
    canonical_sha256,
    fit_calibration_candidates,
    independent_test_seal_sha256,
    predict_candidate,
    validate_fit_protocol,
)
from rallymate_scoring.calibration_independent_test import (
    evaluate_independent_test,
)
from rallymate_scoring.feasibility import load_feasibility_registry
from rallymate_scoring.indicator_requirements import (
    build_indicator_requirements_snapshot,
    required_phase_keys,
    validate_indicator_requirements_snapshot,
)
from rallymate_scoring.scoring_context import SCORING_CONTEXT_FEATURE_DEFINITIONS


ROOT = Path(__file__).resolve().parents[1]
GRADES = ("E", "D", "C", "B", "A")
BACKENDS = ("threshold_rule", "ordinal_regression")
ALL_FEATURE_DEFINITIONS = {
    **FEATURE_DEFINITIONS,
    **SCORING_CONTEXT_FEATURE_DEFINITIONS,
}
EXPECTED_INDICATOR_IDS = (
    "FS01-M02",
    "FS01-M03",
    "FS01-M04",
    "FS01-M05",
    "FS02-M02",
    "FS02-M03",
    "FS02-M04",
    "FS02-M05",
    "FS09-M01",
    "FS09-M02",
    "FS09-M03",
    "FS09-M04",
    "FS09-M05",
)


def _synthetic_value(grade_index: int, feature_index: int, offset: float) -> float:
    """Separable contract-test data, never a RallyMate scoring standard."""

    return float(grade_index * 10.0 + feature_index * 0.01 + offset)


def _fit_protocol(indicator: dict) -> dict:
    return {
        "schema_version": "1.1.0",
        "artifact_scope": "synthetic_test_only_fit_protocol",
        "indicator_id": indicator["indicator_id"],
        "protocol_id": f"synthetic-contract-fit-{indicator['indicator_id']}",
        "protocol_version": "synthetic-contract-only-v1",
        "candidate_version_prefix": "synthetic-all13-do-not-deploy",
        "source": {
            "kind": "synthetic_test_fixture",
            "source_sha256": canonical_sha256(
                {
                    "purpose": "all13-contract-test-only",
                    "indicator_id": indicator["indicator_id"],
                }
            ),
            "registered_at": "2026-08-13T00:00:00Z",
        },
        "label_scale": list(GRADES),
        "label_resolution_policy": "unanimous_multi_coach_only_v1",
        "requirements": {
            "min_records_by_split": {
                "train": 5,
                "validation": 5,
                "independent_test": 5,
            },
            "min_records_per_grade_by_split": {
                "train": {grade: 1 for grade in GRADES},
                "validation": {grade: 1 for grade in GRADES},
            },
            "min_annotators": 2,
            "min_shared_items": 5,
            "agreement_metric": "quadratic_weighted_kappa",
            "min_agreement_value": 1.0,
        },
        "backends": {
            "threshold_rule": {
                "enabled": True,
                "primary_feature": indicator["required_features"][0],
                "direction": "higher_is_better",
                "objective": "mean_absolute_grade_error",
            },
            "ordinal_regression": {
                "enabled": True,
                "optimizer": {
                    "algorithm": "projected_gradient_descent_v1",
                    "max_iterations": 200,
                    "learning_rate": 0.05,
                    "l2": 0.01,
                    "tolerance": 1e-8,
                    "min_cutpoint_gap": 0.01,
                },
            },
        },
    }


def _independent_protocol(indicator_id: str) -> dict:
    # These permissive numeric limits are explicitly synthetic test gates. They
    # prove evaluator plumbing only and must never be copied into production.
    return {
        "schema_version": "1.0.0",
        "artifact_scope": "synthetic_test_only_independent_test_protocol",
        "protocol_id": f"synthetic-contract-independent-{indicator_id}",
        "protocol_version": "synthetic-contract-only-v1",
        "indicator_id": indicator_id,
        "source": {
            "kind": "synthetic_test_fixture",
            "source_sha256": canonical_sha256(
                {
                    "purpose": "all13-independent-contract-test-only",
                    "indicator_id": indicator_id,
                }
            ),
            "registered_at": "2026-08-13T00:00:00Z",
        },
        "label_scale": list(GRADES),
        "label_resolution_policy": "unanimous_multi_coach_only_v1",
        "acceptance_metrics": {
            "minimum_record_count": 5,
            "minimum_leakage_group_count": 5,
            "minimum_valid_rate": 1.0,
            "maximum_mean_absolute_grade_error": 4.0,
            "maximum_absolute_bias_grade_steps": 4.0,
            "minimum_quadratic_weighted_kappa": -1.0,
            "required_grade_coverage": list(GRADES),
            "required_view_groups": ["synthetic-fixed-camera"],
        },
    }


def _grade_labels(
    *, indicator_id: str, video_id: str, event_id: str, grade: str
) -> list[dict]:
    return [
        {
            "schema_version": "1.0.0",
            "annotation_id": f"label-{indicator_id}-{grade}-{coach_id}",
            "video_id": video_id,
            "event_id": event_id,
            "indicator_id": indicator_id,
            "annotator_id": coach_id,
            "label_type": "grade",
            "grade": grade,
        }
        for coach_id in ("synthetic-coach-1", "synthetic-coach-2")
    ]


def _independent_samples(
    *,
    indicator: dict,
    requirements: dict,
    dataset_version: str,
) -> list[dict]:
    indicator_id = indicator["indicator_id"]
    event_code = indicator_id.split("-", 1)[0]
    feature_names = requirements["required_feature_names"]
    phase_names = requirements["required_phase_keys"]
    semantic_names = requirements["required_semantic_keys"]
    records = []
    for grade_index, grade in enumerate(GRADES):
        video_id = f"synthetic-independent-{indicator_id}-{grade}"
        event_id = f"synthetic-{event_code.lower()}-{indicator_id[-2:]}-{grade}"
        identity = {
            "video_id": video_id,
            "event_id": event_id,
            "indicator_id": indicator_id,
            "person_track_id": 1,
        }
        labels = _grade_labels(
            indicator_id=indicator_id,
            video_id=video_id,
            event_id=event_id,
            grade=grade,
        )
        phases = {
            name: 100 + phase_index * 50
            for phase_index, name in enumerate(phase_names)
        }
        semantics = [
            {
                "annotation_id": f"semantic-{indicator_id}-{grade}-{name}",
                "video_id": video_id,
                "event_id": event_id,
                "indicator_id": indicator_id,
                "semantic_key": name,
                "observable": True,
                "value": {"synthetic_contract_value": name},
                "adjudication_status": "accepted",
            }
            for name in semantic_names
        ]
        records.append(
            {
                "schema_version": "1.0.0",
                "sample_id": "sample-" + canonical_sha256(identity)[:20],
                "dataset_version": dataset_version,
                **identity,
                "event_code": event_code,
                "split": "independent_test",
                "feature_vector": [
                    {
                        "feature_name": name,
                        "feature_version": ALL_FEATURE_DEFINITIONS[name]["version"],
                        "value": _synthetic_value(
                            grade_index, feature_index, 0.2
                        ),
                        "unit": ALL_FEATURE_DEFINITIONS[name]["unit"],
                        "confidence": 1.0,
                        "valid": True,
                        "reason": "synthetic_contract_fixture_only",
                        "source_frames": [grade_index * 10 + feature_index],
                    }
                    for feature_index, name in enumerate(feature_names)
                ],
                "feature_vector_complete": True,
                "manual_event": {
                    "start_ms": 0,
                    "end_ms": 1000,
                    "key_phases_ms": phases,
                    "required_phase_keys": list(phase_names),
                    "missing_required_phase_keys": [],
                    "required_phases_complete": True,
                    "boundary_uncertainty_ms": 0,
                    "annotator_id": "synthetic-event-annotator",
                    "reviewer_id": "synthetic-event-reviewer",
                    "adjudication_status": "accepted",
                },
                "semantics": {
                    "requirements_contract_status": "declared",
                    "required_keys": list(semantic_names),
                    "records": semantics,
                    "missing_keys": [],
                    "unobservable_keys": [],
                    "complete": True,
                    "fully_observable": True,
                },
                "labels": {"grades": labels, "rankings": []},
                "label_summary": {
                    "resolution_policy": "unanimous_multi_coach_only_v1",
                    "resolution_semantics": (
                        "synthetic contract labels, not coach ground truth"
                    ),
                    "grade_status": "unanimous",
                    "resolved_grade": grade,
                    "contributing_annotation_ids": [
                        item["annotation_id"] for item in labels
                    ],
                    "contributing_annotator_ids": [
                        "synthetic-coach-1",
                        "synthetic-coach-2",
                    ],
                    "conflicting_grades": [],
                    "grade_label_count": 2,
                    "ranking_label_count": 0,
                    "ranking_annotator_count": 0,
                },
                "groups": {
                    "player_id": f"synthetic-player-{grade_index}",
                    "session_id": f"synthetic-session-{grade_index}",
                    "view_group": "synthetic-fixed-camera",
                    "leakage_group_id": f"lg-{grade_index + 1:016x}",
                },
                "readiness": {
                    "eligible_for_grade_modeling": True,
                    "eligible_for_grade_evaluation": True,
                    "ranking_labels_available": False,
                    "reason_codes": [],
                },
                "lineage": {
                    "manual_event_sha256": canonical_sha256(
                        {"synthetic_event": identity}
                    ),
                    "indicator_feature_sha256": canonical_sha256(
                        {"synthetic_features": identity}
                    ),
                    "semantic_truth_sha256": canonical_sha256(semantics),
                    "coach_labels_sha256": canonical_sha256(labels),
                    "registry_indicator_sha256": requirements[
                        "registry_indicator_sha256"
                    ],
                    "join_key": {
                        "video_id": video_id,
                        "event_id": event_id,
                        "indicator_id": indicator_id,
                    },
                    "join_policy": "exact_manual_event_id_only_v1",
                },
            }
        )
    return records


def _prepared_dataset(
    *, indicator: dict, requirements: dict
) -> tuple[dict, list[dict]]:
    indicator_id = indicator["indicator_id"]
    feature_order = list(indicator["required_features"])
    dataset_version = f"synthetic-all13-dataset-{indicator_id}-v1"
    independent = _independent_samples(
        indicator=indicator,
        requirements=requirements,
        dataset_version=dataset_version,
    )
    records = []
    for split, offset in (("train", 0.0), ("validation", 0.1)):
        group = f"synthetic-{split}-{indicator_id}"
        for grade_index, grade in enumerate(GRADES):
            record_id = f"synthetic-{indicator_id}-{split}-{grade}"
            records.append(
                {
                    "record_id": record_id,
                    "video_id": f"video-{record_id}",
                    "event_id": f"event-{record_id}",
                    "group_id": group,
                    "leakage_component_id": group,
                    "split": split,
                    "features": {
                        name: _synthetic_value(
                            grade_index, feature_index, offset
                        )
                        for feature_index, name in enumerate(feature_order)
                    },
                    "grade": grade,
                    "annotator_ids": [
                        "synthetic-coach-1",
                        "synthetic-coach-2",
                    ],
                    "label_source_ids": [
                        f"label-{record_id}-synthetic-coach-1",
                        f"label-{record_id}-synthetic-coach-2",
                    ],
                    "consensus_status": "unanimous",
                }
            )
    split_counts = Counter(record["split"] for record in records)
    grade_counts = {
        split: dict(
            sorted(
                Counter(
                    record["grade"]
                    for record in records
                    if record["split"] == split
                ).items()
            )
        )
        for split in ("train", "validation")
    }
    source_sha256 = canonical_sha256(
        {"purpose": "synthetic-all13-prepared-contract", "indicator": indicator_id}
    )
    seal_sha256 = independent_test_seal_sha256(independent)
    dataset = {
        "schema_version": "1.0.0",
        "artifact_scope": "synthetic_test_only_calibration_input",
        "dataset_id": f"synthetic-all13-{indicator_id}",
        "dataset_version": dataset_version,
        "source": {
            "kind": "synthetic_test_fixture",
            "manifest_id": "synthetic-all13-contract-manifest",
            "source_sha256": source_sha256,
            "prepared_at": "2026-08-13T00:00:00Z",
        },
        "indicator_id": indicator_id,
        "feature_order": feature_order,
        "unit_by_feature": {
            name: ALL_FEATURE_DEFINITIONS[name]["unit"] for name in feature_order
        },
        "feature_version_by_feature": {
            name: ALL_FEATURE_DEFINITIONS[name]["version"] for name in feature_order
        },
        "label_scale": list(GRADES),
        "label_resolution_policy": "unanimous_multi_coach_only_v1",
        "split_policy": {
            "strategy": "group_holdout",
            "group_key": "leakage_component_id",
            "train_groups": [f"synthetic-train-{indicator_id}"],
            "validation_groups": [f"synthetic-validation-{indicator_id}"],
            "independent_test_groups": [
                record["groups"]["leakage_group_id"] for record in independent
            ],
        },
        "agreement": {
            "status": "evaluated",
            "metric": "quadratic_weighted_kappa",
            "value": 1.0,
            "annotator_count": 2,
            "shared_item_count": 10,
        },
        "records": records,
        "independent_test_seal": {
            "seal_id": f"seal-{seal_sha256[:16]}",
            "indicator_id": indicator_id,
            "record_count": len(independent),
            "groups": sorted(
                record["groups"]["leakage_group_id"] for record in independent
            ),
            "sample_ids": sorted(record["sample_id"] for record in independent),
            "content_sha256": seal_sha256,
            "canonicalization": "rallymate-canonical-json-v1",
            "labels_withheld": True,
        },
        "readiness": {
            "status": "prepared_for_external_protocol_review",
            "blockers": [],
            "F3_claimed": False,
        },
        "integrity": {
            "record_count": len(records),
            "split_counts": dict(sorted(split_counts.items())),
            "grade_counts_by_split": grade_counts,
            "source_dataset_sha256": source_sha256,
            "canonicalization": "rallymate-canonical-json-v1",
        },
    }
    return dataset, independent


class CalibrationAll13ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = load_feasibility_registry(
            ROOT / "metric-feasibility-pose-wave-v2.json"
        )
        cls.indicator_by_id = {
            item["indicator_id"]: item for item in cls.registry["indicators"]
        }
        cls.checked_requirements = json.loads(
            (
                ROOT / "indicator-scoring-requirements-pose-wave-v1.json"
            ).read_text(encoding="utf-8")
        )
        cls.requirements_by_id = validate_indicator_requirements_snapshot(
            cls.checked_requirements
        )
        cls.synthetic_requirements = build_indicator_requirements_snapshot(
            registry=cls.registry,
            semantic_requirements=SEMANTIC_REQUIREMENTS_BY_INDICATOR,
            requirements_version="synthetic-all13-contract-only-v1",
            semantic_contract_version="synthetic-mirror-contract-only-v1",
            artifact_scope=(
                "synthetic_test_only_indicator_scoring_requirements"
            ),
        )
        cls.synthetic_requirements_by_id = validate_indicator_requirements_snapshot(
            cls.synthetic_requirements
        )

    def test_authoritative_requirement_sets_exactly_cover_all_13(self) -> None:
        expected = set(EXPECTED_INDICATOR_IDS)
        self.assertEqual(set(self.indicator_by_id), expected)
        self.assertEqual(set(self.requirements_by_id), expected)
        self.assertEqual(set(SEMANTIC_REQUIREMENTS_BY_INDICATOR), expected)
        observed_features = set()
        for indicator_id in EXPECTED_INDICATOR_IDS:
            indicator = self.indicator_by_id[indicator_id]
            requirements = self.requirements_by_id[indicator_id]
            self.assertEqual(
                requirements["required_feature_names"],
                indicator["required_features"],
            )
            self.assertEqual(
                requirements["required_phase_keys"],
                required_phase_keys(indicator),
            )
            self.assertEqual(
                requirements["required_semantic_keys"],
                sorted(SEMANTIC_REQUIREMENTS_BY_INDICATOR[indicator_id]),
            )
            for name in indicator["required_features"]:
                self.assertIn(name, ALL_FEATURE_DEFINITIONS)
                self.assertTrue(ALL_FEATURE_DEFINITIONS[name]["unit"])
                self.assertTrue(ALL_FEATURE_DEFINITIONS[name]["version"])
                observed_features.add(name)
        self.assertEqual(len(observed_features), 52)

    def test_fit_protocol_requires_and_binds_one_indicator(self) -> None:
        schema = json.loads(
            (ROOT / "contracts/calibration-fit-protocol.schema.json").read_text(
                encoding="utf-8"
            )
        )
        template = json.loads(
            (ROOT / "examples/calibration-fit-protocol.template.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(schema["properties"]["schema_version"]["const"], "1.1.0")
        self.assertIn("indicator_id", schema["required"])
        self.assertEqual(
            schema["properties"]["indicator_id"]["pattern"],
            r"^(GS|FS)[0-9]{2}-M[0-9]{2}(-[0-9]{2})?$",
        )
        self.assertEqual(template["schema_version"], "1.1.0")
        self.assertIn("indicator_id", template)

        indicator = self.indicator_by_id["FS01-M02"]
        dataset, _ = _prepared_dataset(
            indicator=indicator,
            requirements=self.requirements_by_id[indicator["indicator_id"]],
        )
        protocol = _fit_protocol(indicator)
        audit = validate_fit_protocol(protocol)
        self.assertEqual(audit["indicator_id"], indicator["indicator_id"])

        missing = json.loads(json.dumps(protocol))
        del missing["indicator_id"]
        with self.assertRaisesRegex(CalibrationFitError, "fit_protocol.indicator_id"):
            validate_fit_protocol(missing)

        mismatched = json.loads(json.dumps(protocol))
        mismatched["indicator_id"] = "FS09-M05"
        with self.assertRaisesRegex(
            CalibrationFitError, "dataset/protocol indicator_id mismatch"
        ):
            fit_calibration_candidates(dataset, mismatched)

    def test_all13_test_only_fit_predict_and_sealed_evaluation_portfolio(self) -> None:
        candidate_pairs = set()
        report_pairs = set()
        for indicator_id in EXPECTED_INDICATOR_IDS:
            with self.subTest(indicator_id=indicator_id):
                indicator = self.indicator_by_id[indicator_id]
                requirements = self.synthetic_requirements_by_id[indicator_id]
                dataset, independent = _prepared_dataset(
                    indicator=indicator,
                    requirements=requirements,
                )
                protocol = _fit_protocol(indicator)
                candidates = fit_calibration_candidates(dataset, protocol)
                self.assertEqual(
                    {candidate["backend"] for candidate in candidates},
                    set(BACKENDS),
                )
                validation_features = next(
                    record["features"]
                    for record in dataset["records"]
                    if record["split"] == "validation"
                    and record["grade"] == "C"
                )
                for candidate in candidates:
                    self.assertEqual(
                        candidate["artifact_scope"],
                        "synthetic_test_only_calibration_candidate",
                    )
                    self.assertEqual(candidate["indicator_id"], indicator_id)
                    self.assertEqual(
                        candidate["prepared_dataset"]["artifact_scope"],
                        "synthetic_test_only_calibration_input",
                    )
                    self.assertEqual(
                        candidate["fit_protocol"]["artifact_scope"],
                        "synthetic_test_only_fit_protocol",
                    )
                    self.assertFalse(candidate["promotion"]["scoring_allowed"])
                    self.assertFalse(candidate["promotion"]["F3_promoted"])
                    self.assertFalse(candidate["promotion"]["F4_promoted"])
                    prediction = predict_candidate(candidate, validation_features)
                    self.assertIn(prediction["grade"], GRADES)
                    if candidate["backend"] == "threshold_rule":
                        self.assertIsNone(prediction["probabilities"])
                    else:
                        self.assertEqual(
                            set(prediction["probabilities"]), set(GRADES)
                        )
                    candidate_pairs.add((indicator_id, candidate["backend"]))

                    report = evaluate_independent_test(
                        candidate=candidate,
                        samples=independent,
                        protocol=_independent_protocol(indicator_id),
                        indicator_requirements=self.synthetic_requirements,
                        evaluated_at="2026-08-13T01:00:00Z",
                    )
                    self.assertEqual(
                        report["artifact_scope"],
                        "synthetic_test_only_independent_test_report",
                    )
                    self.assertEqual(report["status"], "passed")
                    self.assertFalse(report["approved_for_scoring"])
                    self.assertEqual(
                        report["coverage"]["evaluated_record_count"], 5
                    )
                    self.assertFalse(
                        report["promotion"]["production_asset_created"]
                    )
                    self.assertFalse(
                        report["promotion"]["registry_F4_promoted"]
                    )
                    report_pairs.add((indicator_id, candidate["backend"]))

        expected_pairs = {
            (indicator_id, backend)
            for indicator_id in EXPECTED_INDICATOR_IDS
            for backend in BACKENDS
        }
        self.assertEqual(candidate_pairs, expected_pairs)
        self.assertEqual(report_pairs, expected_pairs)
        self.assertEqual(len(candidate_pairs), 26)


if __name__ == "__main__":
    unittest.main()
