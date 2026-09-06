from __future__ import annotations

import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from jsonschema import Draft202012Validator, FormatChecker

from rallymate_scoring.calibration import (
    validate_ordinal_model,
    validate_threshold_calibration,
)
from rallymate_scoring.calibration_fitting import (
    canonical_sha256,
    independent_test_seal_sha256,
)
from rallymate_scoring.calibration_independent_test import (
    _acceptance,
    _grouped_metrics,
    _metrics,
    evaluate_independent_test,
)
from rallymate_scoring.calibration_promotion import (
    CalibrationPromotionError,
    authorize_production_asset_with_trusted_ledger,
    bind_trusted_production_calibration_to_registry,
    build_trusted_promotion_ledger,
    promote_calibration_candidate,
    validate_promotion_decision,
)
from rallymate_scoring.maturity_evidence import (
    maturity_evidence_binding,
)
from rallymate_scoring.scoring_truth_calibration_authorization import (
    SYNTHETIC_STATUS,
    _issue_verified_scoring_truth_calibration_authorization,
    build_nonproduction_truth_authorization_marker,
    scoring_truth_calibration_authorization_binding_sha256,
)
from rallymate_scoring.scoring import score_indicator
from tests.test_maturity_evidence import _production_shaped_bundle, _rehash
from tests.test_scoring_truth_authorization import _make_evidence, _verify
from tests.test_calibration_independent_test import _samples as _independent_samples


_PRODUCTION_TRUTH_FIXTURE = None


def _truth_authorization_fixture(*, synthetic: bool):
    """Return test-only lineage and, for real-shaped tests, a live private wrapper."""

    if synthetic:
        binding = build_nonproduction_truth_authorization_marker(
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
        return binding, None

    global _PRODUCTION_TRUTH_FIXTURE
    if _PRODUCTION_TRUTH_FIXTURE is None:
        with tempfile.TemporaryDirectory(prefix="rallymate-m90-auth-work-") as work:
            with tempfile.TemporaryDirectory(
                prefix="rallymate-m90-auth-anchor-"
            ) as anchor:
                evidence = _make_evidence(Path(work), Path(anchor))
                event_binding = _verify(evidence)
        binding = {
            "binding_version": (
                "scoring-truth-calibration-authorization-binding-v1.0.0"
            ),
            "status": "verified_authorized_intake_for_calibration",
            "canonicalization": "rallymate-canonical-json-v1",
            "authorization_id": "private-test-issuer-m90-calibration-001",
            "event_protocol_authorization": event_binding,
            "intake": {
                "intake_id": "private-test-intake-only",
                "intake_version": "private-test-intake-v1",
                "content_root_sha256": "7" * 64,
            },
            "revision_lineage": {
                "revision_id": "private-test-final-revision",
                "annotator_a_id": "private-test-annotator-a",
                "annotator_b_id": "private-test-annotator-b",
                "reviewer_c_id": "private-test-reviewer-c",
                "annotator_a_export_root_sha256": "8" * 64,
                "annotator_b_export_root_sha256": "9" * 64,
                "reviewer_c_adjudication_root_sha256": "A" * 64,
                "roles_distinct": True,
                "revision_finalized": True,
            },
            "input_files": {
                "intake_manifest": "B" * 64,
                "truth_manifest": "C" * 64,
                "truth_validation_report": "D" * 64,
                "manual_events": "E" * 64,
                "manual_semantics": "F" * 64,
                "coach_labels": "0" * 64,
            },
        }
        binding["binding_sha256"] = (
            scoring_truth_calibration_authorization_binding_sha256(binding)
        )
        wrapper = _issue_verified_scoring_truth_calibration_authorization(binding)
        _PRODUCTION_TRUTH_FIXTURE = (copy.deepcopy(binding), wrapper)
    binding, wrapper = _PRODUCTION_TRUTH_FIXTURE
    return copy.deepcopy(binding), wrapper


def _verified_truth_authorization():
    return _truth_authorization_fixture(synthetic=False)[1]


def _candidate(*, synthetic: bool = True) -> dict:
    truth_authorization, _ = _truth_authorization_fixture(synthetic=synthetic)
    return {
        "schema_version": "1.0.0",
        "artifact_scope": (
            "synthetic_test_only_calibration_candidate"
            if synthetic
            else "calibration_candidate"
        ),
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
            "dataset_id": "dataset-v1",
            "dataset_version": "v1",
            "artifact_scope": (
                "synthetic_test_only_calibration_input"
                if synthetic
                else "calibration_input"
            ),
            "label_resolution_policy": "unanimous_multi_coach_only_v1",
            "content_sha256": "a" * 64,
            "source_sha256": "b" * 64,
            "truth_authorization_binding_sha256": truth_authorization[
                "binding_sha256"
            ],
        },
        "fit_protocol": {
            "protocol_id": "fit-v1",
            "protocol_version": "v1",
            "artifact_scope": (
                "synthetic_test_only_fit_protocol"
                if synthetic
                else "calibration_fit_protocol"
            ),
            "label_resolution_policy": "unanimous_multi_coach_only_v1",
            "content_sha256": "c" * 64,
            "source_sha256": "d" * 64,
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
        "independent_test": {
            "status": "sealed_not_evaluated",
            "approved_for_scoring": False,
            "accessed_during_fit": False,
            "seal_id": "seal-123",
            "record_count": 5,
            "groups": ["g1", "g2", "g3", "g4", "g5"],
            "sample_ids": ["s1", "s2", "s3", "s4", "s5"],
            "content_sha256": "e" * 64,
            "canonicalization": "rallymate-canonical-json-v1",
        },
        "promotion": {
            "scoring_allowed": False,
            "F3_promoted": False,
            "F4_promoted": False,
            "reason": "independent_test_and_explicit_promotion_required",
        },
    }


def _ordinal_candidate(*, synthetic: bool = True) -> dict:
    candidate = _candidate(synthetic=synthetic)
    candidate.update(
        {
            "candidate_id": "candidate-ordinal-v1",
            "candidate_version": "candidate-ordinal-v1",
            "backend": "ordinal_regression",
            "feature_order": ["feature_a", "feature_b"],
            "unit_by_feature": {"feature_a": "body", "feature_b": "deg"},
            "feature_version_by_feature": {
                "feature_a": "feature-a-v1",
                "feature_b": "feature-b-v1",
            },
            "coefficients": [0.25, -0.5],
            "intercept": 0.125,
            "cutpoints": [-1.5, -0.5, 0.5, 1.5],
        }
    )
    for field in (
        "primary_feature",
        "primary_feature_version",
        "unit",
        "direction",
        "thresholds",
    ):
        candidate.pop(field)
    return candidate


def _independent_test_protocol(candidate: dict) -> dict:
    synthetic = candidate["artifact_scope"].startswith("synthetic_test_only")
    return {
        "schema_version": "1.0.0",
        "artifact_scope": (
            "synthetic_test_only_independent_test_protocol"
            if synthetic
            else "independent_test_protocol"
        ),
        "protocol_id": "independent-protocol-v1",
        "protocol_version": "v1",
        "indicator_id": candidate["indicator_id"],
        "source": {
            "kind": (
                "synthetic_test_fixture"
                if synthetic
                else "preregistered_independent_test_protocol"
            ),
            "source_sha256": "1" * 64,
            "registered_at": "2026-08-13T00:00:00Z",
        },
        "label_scale": ["E", "D", "C", "B", "A"],
        "label_resolution_policy": candidate["target_semantics"][
            "label_resolution_policy"
        ],
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


def _production_replay_fixture(
    candidate: dict,
    *,
    include_extra_source_rows: bool = False,
) -> tuple[list[dict], dict, dict]:
    """Build real-shaped source inputs and their exact evaluator report."""

    samples = _independent_samples()
    if include_extra_source_rows:
        extra = copy.deepcopy(samples[0])
        extra["split"] = "train"
        samples.append(extra)
        other_indicator = copy.deepcopy(samples[1])
        other_indicator["indicator_id"] = "FS01-M05"
        other_indicator["split"] = "validation"
        samples.append(other_indicator)
    sealed_samples = [
        item
        for item in samples
        if item["indicator_id"] == candidate["indicator_id"]
        and item["split"] == "independent_test"
    ]
    seal = independent_test_seal_sha256(sealed_samples)
    candidate["independent_test"].update(
        {
            "seal_id": f"seal-{seal[:16]}",
            "record_count": len(sealed_samples),
            "groups": sorted(
                item["groups"]["leakage_group_id"] for item in sealed_samples
            ),
            "sample_ids": sorted(item["sample_id"] for item in sealed_samples),
            "content_sha256": seal,
        }
    )
    pre_f4_registry = _production_shaped_bundle()["transitions"][3][
        "registry_before"
    ]
    requirements = {
        "schema_version": "1.0.0",
        "artifact_scope": "indicator_scoring_requirements",
        "requirements_version": "production-replay-requirements-v1",
        "source": {
            "kind": "versioned_registry_and_manual_truth_contract",
            "registry_version": pre_f4_registry["registry_version"],
            "registry_content_sha256": pre_f4_registry["content_sha256"],
            "semantic_contract_version": "production-replay-semantics-v1",
            "semantic_contract_sha256": "d" * 64,
            "canonicalization": "rallymate-canonical-json-v1",
        },
        "indicators": [
            {
                "indicator_id": candidate["indicator_id"],
                "required_feature_names": ["feature_a"],
                "required_phase_keys": [],
                "required_semantic_keys": [],
                "registry_indicator_sha256": "c" * 64,
            }
        ],
    }
    protocol = _independent_test_protocol(candidate)
    report = evaluate_independent_test(
        candidate=candidate,
        samples=samples,
        protocol=protocol,
        indicator_requirements=requirements,
        evaluated_at="2026-08-13T01:00:00Z",
    )
    return samples, requirements, report


def _report(candidate: dict) -> dict:
    synthetic = candidate["artifact_scope"].startswith("synthetic_test_only")
    protocol = _independent_test_protocol(candidate)
    predictions = []
    metrics = {}
    checks = []
    if not synthetic:
        for index, (sample_id, leakage_group_id, grade) in enumerate(
            zip(
                candidate["independent_test"]["sample_ids"],
                candidate["independent_test"]["groups"],
                ("E", "D", "C", "B", "A"),
            )
        ):
            predictions.append(
                {
                    "sample_id": sample_id,
                    "video_id": f"video-{index}",
                    "event_id": f"event-{index}",
                    "expected_grade": grade,
                    "predicted_grade": grade,
                    "grade_probabilities": None,
                    "groups": {
                        "player_id": f"player-{index}",
                        "session_id": f"session-{index}",
                        "view_group": "fixed-camera",
                        "leakage_group_id": leakage_group_id,
                    },
                }
            )
        overall = _metrics(predictions)
        metrics = {
            "overall": overall,
            "by_view_group": _grouped_metrics(predictions, "view_group"),
            "by_player": _grouped_metrics(predictions, "player_id"),
            "by_session": _grouped_metrics(predictions, "session_id"),
        }
        passed, checks = _acceptance(
            metrics=overall,
            valid_rate=1.0,
            rows=predictions,
            protocol=protocol,
        )
        if not passed:
            raise AssertionError("production-shaped independent-test fixture must pass")
    report = {
        "schema_version": "1.0.0" if synthetic else "1.2.0",
        "artifact_scope": (
            "synthetic_test_only_independent_test_report"
            if synthetic
            else "independent_test_report"
        ),
        "report_version": "independent-test-v1",
        "status": "passed",
        "approved_for_scoring": False,
        "indicator_id": candidate["indicator_id"],
        "candidate": {
            "candidate_id": candidate["candidate_id"],
            "candidate_version": candidate["candidate_version"],
            "backend": candidate["backend"],
            "content_sha256": canonical_sha256(candidate),
        },
        "dataset": {
            "dataset_id": candidate["prepared_dataset"]["dataset_id"],
            "dataset_version": candidate["prepared_dataset"]["dataset_version"],
            "seal_id": candidate["independent_test"]["seal_id"],
            "content_sha256": candidate["independent_test"]["content_sha256"],
            "record_count": candidate["independent_test"]["record_count"],
            "leakage_groups": candidate["independent_test"]["groups"],
            "canonicalization": "rallymate-canonical-json-v1",
        },
        "protocol": {
            "protocol_id": protocol["protocol_id"],
            "protocol_version": protocol["protocol_version"],
            "content_sha256": canonical_sha256(protocol),
            "source_sha256": protocol["source"]["source_sha256"],
            "registered_at": protocol["source"]["registered_at"],
        },
        "indicator_requirements": {
            "requirements_version": "synthetic-requirements-v1",
            "content_sha256": "3" * 64,
            "registry_version": "synthetic-requirements-registry-v1",
            "registry_content_sha256": "4" * 64,
            "registry_indicator_sha256": "5" * 64,
            "semantic_contract_version": "synthetic-semantics-v1",
            "semantic_contract_sha256": "6" * 64,
        },
        "evaluated_at": "2026-08-13T01:00:00Z",
        "coverage": (
            {}
            if synthetic
            else {
                "sealed_record_count": len(predictions),
                "evaluation_eligible_record_count": len(predictions),
                "evaluated_record_count": len(predictions),
                "valid_rate": 1.0,
                "evaluation_eligible_rate": 1.0,
                "eligible_evaluation_completion_rate": 1.0,
                "excluded_record_count": 0,
                "exclusion_reason_counts": {},
            }
        ),
        "metrics": metrics,
        "acceptance": {
            "passed": True,
            "checks": checks,
            "semantics": "synthetic external limits",
        },
        "predictions": predictions,
        "promotion": {
            "production_asset_created": False,
            "registry_F4_promoted": False,
            "explicit_human_approval_required": True,
        },
    }
    if not synthetic:
        report["exclusions"] = []
    if not synthetic:
        pre_f4_registry = _production_shaped_bundle()["transitions"][3][
            "registry_before"
        ]
        report["indicator_requirements"].update(
            {
                "registry_version": pre_f4_registry["registry_version"],
                "registry_content_sha256": pre_f4_registry["content_sha256"],
                "registry_indicator_sha256": "5" * 64,
            }
        )
    return report


def _registry(*, level: str = "F2") -> dict:
    return {
        "schema_version": "1.0.0",
        "registry_version": "registry-v1",
        "updated_at": "2026-08-13T00:00:00Z",
        "scope": {"events": ["FS01", "FS02", "FS09"]},
        "level_definitions": {
            "F0": "structure",
            "F1": "event",
            "F2": "feature",
            "F3": "calibration",
            "F4": "independent",
        },
        "indicators": [
            {
                "indicator_id": indicator_id,
                "feasibility_level": level if indicator_id == "FS01-M02" else "F2",
                "required_events": [indicator_id.split("-")[0]],
                "required_features": ["feature_a"],
                "view_constraints": ["fixed_camera"],
                "ground_truth_requirements": ["human_truth"],
                "current_blockers": [] if level == "F4" else ["not_F4"],
                "acceptance_metrics": {"feature_mae": None},
                "versions": {"indicator_definition": "v1"},
            }
            for indicator_id in (
                "FS01-M02",
                "FS01-M05",
                "FS02-M02",
                "FS09-M03",
                "FS09-M04",
                "FS09-M05",
            )
        ],
    }


def _write_registry_lifecycle(
    directory: Path,
    *,
    registry_path: Path,
    registry: dict,
) -> tuple[Path, Path]:
    """Register a production-shaped fixture as the sole current runtime role."""

    source_version = registry["scope"]["extends_registry"]
    historical = _registry(level="F2")
    historical["registry_version"] = source_version
    historical_path = directory / "historical-feasibility.json"
    historical_path.write_text(json.dumps(historical), encoding="utf-8")
    manifest = {
        "schema_version": "1.0.0",
        "artifact_scope": "registry_lifecycle_authority",
        "authority_version": "test-promotion-registry-lifecycle-v1",
        "roles": {
            "runtime_feasibility": {
                "kind": "metric_feasibility_registry",
                "lifecycle": "current",
                "relative_path": registry_path.name,
                "file_sha256": hashlib.sha256(registry_path.read_bytes()).hexdigest(),
                "embedded_version": registry["registry_version"],
                "source_registry_version": source_version,
            },
            "current_scoring_requirements": {
                "kind": "indicator_scoring_requirements",
                "lifecycle": "derived_current",
                "relative_path": "unused-current-requirements.json",
                "file_sha256": "1" * 64,
                "embedded_version": "unused-current-requirements-v1",
                "source_registry_version": registry["registry_version"],
            },
        },
        "non_runtime_artifacts": {
            "historical_feasibility": {
                "kind": "metric_feasibility_registry",
                "lifecycle": "historical",
                "relative_path": historical_path.name,
                "file_sha256": hashlib.sha256(
                    historical_path.read_bytes()
                ).hexdigest(),
                "embedded_version": source_version,
            },
            "measurement_plans": {
                "kind": "metric_measurement_plans",
                "lifecycle": "planning_only",
                "relative_path": "unused-measurement-plans.json",
                "file_sha256": "2" * 64,
                "embedded_version": "unused-measurement-plans-v1",
                "source_registry_version": registry["registry_version"],
            },
        },
    }
    manifest_path = directory / "registry-lifecycle.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path, historical_path


def _decision(candidate: dict, report: dict, registry: dict) -> dict:
    synthetic = candidate["artifact_scope"].startswith("synthetic_test_only")
    decision = {
        "schema_version": "1.1.0",
        "artifact_scope": (
            "synthetic_test_only_calibration_promotion_decision"
            if synthetic
            else "calibration_promotion_decision"
        ),
        "decision_id": "decision-v1",
        "decision_version": "v1",
        "calibration_version": "calibration-v1",
        "indicator_id": candidate["indicator_id"],
        "decision": "approved",
        "requested_artifact_scope": "test_only" if synthetic else "production",
        "reviewer": {"reviewer_id": "reviewer-1", "role": "calibration_owner"},
        "source": {
            "kind": "synthetic_test_fixture" if synthetic else "human_promotion_record",
            "source_sha256": "2" * 64,
            "registered_at": "2026-08-13T01:30:00Z",
        },
        "decided_at": "2026-08-13T02:00:00Z",
        "candidate": {
            "candidate_id": candidate["candidate_id"],
            "candidate_version": candidate["candidate_version"],
            "backend": candidate["backend"],
            "content_sha256": canonical_sha256(candidate),
        },
        "prepared_dataset": copy.deepcopy(candidate["prepared_dataset"]),
        "fit_protocol": copy.deepcopy(candidate["fit_protocol"]),
        "independent_test": {
            "seal_id": candidate["independent_test"]["seal_id"],
            "content_sha256": candidate["independent_test"]["content_sha256"],
            "record_count": candidate["independent_test"]["record_count"],
            "canonicalization": candidate["independent_test"]["canonicalization"],
        },
        "independent_test_report": {
            "report_version": report["report_version"],
            "content_sha256": canonical_sha256(report),
        },
        "independent_test_protocol": copy.deepcopy(report["protocol"]),
        "feasibility_registry": {
            "registry_version": registry["registry_version"],
            "content_sha256": canonical_sha256(registry),
            "indicator_level": next(
                item["feasibility_level"]
                for item in registry["indicators"]
                if item["indicator_id"] == candidate["indicator_id"]
            ),
        },
        "attestations": {
            "candidate_lineage_reviewed": True,
            "independent_test_reviewed": True,
            "registry_maturity_reviewed": True,
            "no_manual_threshold_or_coefficient_edits": True,
        },
    }
    if not synthetic:
        # Production decisions must bind the exact maturity bundle presented to
        # promotion. F2 rejection fixtures use a structurally valid placeholder;
        # the registry gate rejects before the nonexistent F4 bundle is consumed.
        if next(
            item["feasibility_level"]
            for item in registry["indicators"]
            if item["indicator_id"] == candidate["indicator_id"]
        ) == "F4":
            decision["maturity_evidence"] = maturity_evidence_binding(
                _maturity_evidence(candidate, report, registry)
            )
        else:
            decision["maturity_evidence"] = {
                "bundle_id": "synthetic-pending-F4-evidence",
                "bundle_version": "maturity-evidence-bundle-v1",
                "artifact_scope": "maturity_evidence",
                "indicator_id": candidate["indicator_id"],
                "content_sha256": "7" * 64,
                "final_transition_sha256": "8" * 64,
                "final_registry": {
                    "registry_version": registry["registry_version"],
                    "content_sha256": canonical_sha256(registry),
                    "indicator_id": candidate["indicator_id"],
                    "indicator_level": "F4",
                },
                "canonicalization": "rallymate-canonical-json-v1",
            }
        decision["attestations"]["maturity_evidence_reviewed"] = True
    return decision


def _maturity_evidence(candidate: dict, report: dict, registry: dict) -> dict:
    """Build a production-shaped fixture; its numbers are contract test data only."""

    bundle = _production_shaped_bundle()
    required_features = next(
        item["required_features"]
        for item in registry["indicators"]
        if item["indicator_id"] == candidate["indicator_id"]
    )
    f2 = bundle["transitions"][1]["evidence"]["payload"]
    template_metric = copy.deepcopy(f2["feature_evaluation"]["features"][0])
    template_budget = copy.deepcopy(f2["error_budget"]["features"][0])
    f2["feature_evaluation"]["features"] = []
    f2["error_budget"]["features"] = []
    f2["grade_gap_assessment"]["feature_decisions"] = []
    for feature_name in required_features:
        metric = copy.deepcopy(template_metric)
        metric["feature_name"] = feature_name
        budget = copy.deepcopy(template_budget)
        budget["feature_name"] = feature_name
        f2["feature_evaluation"]["features"].append(metric)
        f2["error_budget"]["features"].append(budget)
        f2["grade_gap_assessment"]["feature_decisions"].append(
            {"feature_name": feature_name, "status": "passed"}
        )

    event_codes = sorted(
        {
            requirement.split(".", 1)[0]
            for item in registry["indicators"]
            if item["indicator_id"] == candidate["indicator_id"]
            for requirement in item["required_events"]
        }
    )
    bundle["transitions"][0]["evidence"]["payload"]["event_evaluation"][
        "event_codes"
    ] = event_codes

    timestamps = (
        ("2026-08-13T00:05:00Z", "2026-08-13T00:10:00Z"),
        ("2026-08-13T00:20:00Z", "2026-08-13T00:25:00Z"),
        ("2026-08-13T00:30:00Z", "2026-08-13T00:35:00Z"),
        (report["evaluated_at"], "2026-08-13T01:10:00Z"),
    )
    for transition, (evaluated_at, reviewed_at) in zip(
        bundle["transitions"], timestamps, strict=True
    ):
        transition["evidence"]["evaluated_at"] = evaluated_at
        transition["acceptance"]["reviewed_at"] = reviewed_at
    bundle["created_at"] = "2026-08-13T01:20:00Z"

    final_registry = bundle["transitions"][3]["registry_after"]
    final_registry.update(
        {
            "registry_version": registry["registry_version"],
            "content_sha256": canonical_sha256(registry),
            "indicator_id": candidate["indicator_id"],
            "indicator_level": "F4",
        }
    )
    bundle["final_registry"] = copy.deepcopy(final_registry)

    f4_transition = bundle["transitions"][3]
    protocol_reference = {
        "artifact_id": report["protocol"]["protocol_id"],
        "artifact_version": report["protocol"]["protocol_version"],
        "content_sha256": report["protocol"]["content_sha256"],
        "source_sha256": report["protocol"]["source_sha256"],
        "source_kind": "external_preregistered_protocol",
        "registered_at": report["protocol"]["registered_at"],
    }
    f4_transition["acceptance"]["protocol"] = copy.deepcopy(protocol_reference)
    f4 = f4_transition["evidence"]["payload"]["independent_test"]
    f4["protocol"] = copy.deepcopy(protocol_reference)
    f4["dataset"].update(
        {
            "artifact_id": candidate["prepared_dataset"]["dataset_id"],
            "artifact_version": candidate["prepared_dataset"]["dataset_version"],
            "content_sha256": candidate["independent_test"]["content_sha256"],
        }
    )
    f4["report"].update(
        {
            "artifact_id": report["report_version"],
            "artifact_version": report["report_version"],
            "content_sha256": canonical_sha256(report),
            "registered_at": report["evaluated_at"],
        }
    )
    _rehash(bundle)
    return bundle


class CalibrationPromotionTests(unittest.TestCase):
    def test_human_decision_template_is_not_a_valid_approval_record(self) -> None:
        root = Path(__file__).resolve().parents[1]
        template = json.loads(
            (
                root / "examples" / "calibration-promotion-decision.template.json"
            ).read_text(encoding="utf-8")
        )
        with self.assertRaises(CalibrationPromotionError):
            validate_promotion_decision(template)

    def test_synthetic_candidate_creates_valid_test_only_asset(self) -> None:
        candidate = _candidate()
        report = _report(candidate)
        registry = _registry()
        asset, promotion = promote_calibration_candidate(
            candidate=candidate,
            independent_test_report=report,
            decision=_decision(candidate, report, registry),
            feasibility_registry=registry,
            promoted_at="2026-08-13T03:00:00Z",
        )
        validate_threshold_calibration(asset)
        self.assertEqual(asset["artifact_scope"], "test_only")
        self.assertEqual(asset["thresholds"], candidate["thresholds"])
        self.assertEqual(asset["independent_test"]["status"], "test_only")
        self.assertFalse(asset["independent_test"]["approved_for_scoring"])
        self.assertFalse(promotion["production_asset_created"])
        self.assertFalse(promotion["feasibility_registry"]["registry_mutated"])
        root = Path(__file__).resolve().parents[1]
        schema = json.loads(
            (
                root
                / "contracts"
                / "calibration-independent-test-report.schema.json"
            ).read_text(encoding="utf-8")
        )
        Draft202012Validator(schema).validate(report)

    def test_synthetic_candidate_rejects_production_only_replay_inputs(self) -> None:
        candidate = _candidate()
        report = _report(candidate)
        registry = _registry()
        base_arguments = {
            "candidate": candidate,
            "independent_test_report": report,
            "decision": _decision(candidate, report, registry),
            "feasibility_registry": registry,
            "promoted_at": "2026-08-13T03:00:00Z",
        }
        for field, value in (
            ("independent_test_samples", []),
            ("indicator_requirements", {}),
            ("independent_test_source_replay", {}),
            ("promotion_input_snapshots", {}),
        ):
            with self.subTest(field=field):
                with self.assertRaisesRegex(
                    CalibrationPromotionError,
                    rf"synthetic/test-only promotion cannot accept production-only inputs: {field}",
                ):
                    promote_calibration_candidate(**base_arguments, **{field: value})

    def test_ordinal_candidate_parameters_are_copied_to_test_only_asset(self) -> None:
        candidate = _ordinal_candidate()
        report = _report(candidate)
        registry = _registry()
        asset, _ = promote_calibration_candidate(
            candidate=candidate,
            independent_test_report=report,
            decision=_decision(candidate, report, registry),
            feasibility_registry=registry,
            promoted_at="2026-08-13T03:00:00Z",
        )
        validate_ordinal_model(asset)
        self.assertEqual(asset["coefficients"], candidate["coefficients"])
        self.assertEqual(asset["intercept"], candidate["intercept"])
        self.assertEqual(asset["cutpoints"], candidate["cutpoints"])
        self.assertEqual(
            asset["feature_version_by_feature"],
            candidate["feature_version_by_feature"],
        )

    def test_real_candidate_at_F2_is_rejected_before_asset(self) -> None:
        candidate = _candidate(synthetic=False)
        samples, requirements, report = _production_replay_fixture(candidate)
        registry = _registry(level="F2")
        with self.assertRaisesRegex(CalibrationPromotionError, "already be F4"):
            promote_calibration_candidate(
                candidate=candidate,
                independent_test_report=report,
                independent_test_protocol=_independent_test_protocol(candidate),
                independent_test_samples=samples,
                indicator_requirements=requirements,
                decision=_decision(candidate, report, registry),
                feasibility_registry=registry,
                promoted_at="2026-08-13T03:00:00Z",
                verified_truth_authorization=_verified_truth_authorization(),
            )

    def test_real_candidate_at_F4_creates_production_asset(self) -> None:
        candidate = _candidate(synthetic=False)
        samples, requirements, report = _production_replay_fixture(candidate)
        registry = _registry(level="F4")
        protocol = _independent_test_protocol(candidate)
        maturity = _maturity_evidence(candidate, report, registry)
        decision = _decision(candidate, report, registry)
        asset, promotion = promote_calibration_candidate(
            candidate=candidate,
            independent_test_report=report,
            independent_test_protocol=protocol,
            independent_test_samples=samples,
            indicator_requirements=requirements,
            decision=decision,
            feasibility_registry=registry,
            promoted_at="2026-08-13T03:00:00Z",
            maturity_evidence=maturity,
            verified_truth_authorization=_verified_truth_authorization(),
        )
        validate_threshold_calibration(asset)
        self.assertEqual(asset["artifact_scope"], "production")
        self.assertTrue(asset["independent_test"]["approved_for_scoring"])
        self.assertTrue(promotion["production_asset_created"])
        self.assertEqual(
            asset["promotion_lineage"]["independent_test_source_replay"]["samples"][
                "sealed_record_count"
            ],
            5,
        )
        snapshots = asset["promotion_lineage"]["promotion_input_snapshots"]
        self.assertEqual(promotion["promotion_input_snapshots"], snapshots)
        self.assertEqual(asset["promotion_lineage"]["schema_version"], "1.3.0")
        self.assertEqual(promotion["schema_version"], "1.3.0")
        for field, payload in (
            ("candidate", candidate),
            ("independent_test_report", report),
            ("independent_test_protocol", protocol),
            ("decision", decision),
            ("maturity_evidence", maturity),
        ):
            self.assertEqual(
                snapshots[field],
                {
                    "source_kind": "in_memory_canonical_json",
                    "canonical_sha256": canonical_sha256(payload),
                },
            )
        self.assertEqual(
            snapshots["registry_lifecycle_authority"],
            {
                "source_kind": "in_memory_canonical_json",
                "artifact_canonical_sha256": canonical_sha256(registry),
            },
        )

    def test_production_persists_exact_file_input_snapshot_bindings(self) -> None:
        candidate = _candidate(synthetic=False)
        samples, requirements, report = _production_replay_fixture(candidate)
        registry = _registry(level="F4")
        protocol = _independent_test_protocol(candidate)
        maturity = _maturity_evidence(candidate, report, registry)
        decision = _decision(candidate, report, registry)
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)

            def file_snapshot(name: str, payload: dict) -> dict:
                path = work / f"{name}.json"
                raw = (
                    json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
                ).encode("utf-8")
                path.write_bytes(raw)
                return {
                    "source_kind": "file_bytes",
                    "source_path": str(path.resolve()),
                    "raw_sha256": hashlib.sha256(raw).hexdigest(),
                    "canonical_sha256": canonical_sha256(payload),
                }

            snapshots = {
                "candidate": file_snapshot("candidate", candidate),
                "independent_test_report": file_snapshot("report", report),
                "independent_test_protocol": file_snapshot("protocol", protocol),
                "decision": file_snapshot("decision", decision),
                "maturity_evidence": file_snapshot("maturity", maturity),
            }
            manifest_path = work / "registry-lifecycle.json"
            manifest_raw = b'{"snapshot":"test-authority"}\n'
            manifest_path.write_bytes(manifest_raw)
            registry_path = work / "registry.json"
            registry_raw = (
                json.dumps(registry, ensure_ascii=False, indent=2) + "\n"
            ).encode("utf-8")
            registry_path.write_bytes(registry_raw)
            snapshots["registry_lifecycle_authority"] = {
                "source_kind": "registry_lifecycle_verified_file_bytes",
                "manifest_path": str(manifest_path.resolve()),
                "manifest_raw_sha256": hashlib.sha256(manifest_raw).hexdigest(),
                "authority_version": "test-authority-v1",
                "authority_slot": "roles.runtime_feasibility",
                "artifact_path": str(registry_path.resolve()),
                "artifact_raw_sha256": hashlib.sha256(registry_raw).hexdigest(),
                "artifact_canonical_sha256": canonical_sha256(registry),
                "embedded_version": registry["registry_version"],
            }
            asset, promotion = promote_calibration_candidate(
                candidate=candidate,
                independent_test_report=report,
                independent_test_protocol=protocol,
                independent_test_samples=samples,
                indicator_requirements=requirements,
                promotion_input_snapshots=snapshots,
                decision=decision,
                feasibility_registry=registry,
                promoted_at="2026-08-13T03:00:00Z",
                maturity_evidence=maturity,
                verified_truth_authorization=_verified_truth_authorization(),
            )
        self.assertEqual(
            asset["promotion_lineage"]["promotion_input_snapshots"], snapshots
        )
        self.assertEqual(promotion["promotion_input_snapshots"], snapshots)
        mismatched = copy.deepcopy(snapshots)
        mismatched["candidate"]["canonical_sha256"] = "0" * 64
        with self.assertRaisesRegex(
            CalibrationPromotionError,
            "promotion_input_snapshots.candidate.canonical_sha256 lineage mismatch",
        ):
            promote_calibration_candidate(
                candidate=candidate,
                independent_test_report=report,
                independent_test_protocol=protocol,
                independent_test_samples=samples,
                indicator_requirements=requirements,
                promotion_input_snapshots=mismatched,
                decision=decision,
                feasibility_registry=registry,
                promoted_at="2026-08-13T03:00:00Z",
                maturity_evidence=maturity,
                verified_truth_authorization=_verified_truth_authorization(),
            )

    def test_production_requires_replay_sources_and_rejects_coordinated_exclusion(self) -> None:
        candidate = _candidate(synthetic=False)
        samples, requirements, report = _production_replay_fixture(
            candidate, include_extra_source_rows=True
        )
        registry = _registry(level="F4")
        common = {
            "candidate": candidate,
            "independent_test_report": report,
            "independent_test_protocol": _independent_test_protocol(candidate),
            "decision": _decision(candidate, report, registry),
            "feasibility_registry": registry,
            "promoted_at": "2026-08-13T03:00:00Z",
            "maturity_evidence": _maturity_evidence(candidate, report, registry),
            "verified_truth_authorization": _verified_truth_authorization(),
        }
        with self.assertRaisesRegex(
            CalibrationPromotionError, "non-empty independent_test_samples"
        ):
            promote_calibration_candidate(**common)
        with self.assertRaisesRegex(
            CalibrationPromotionError, "requires indicator_requirements"
        ):
            promote_calibration_candidate(
                **common, independent_test_samples=samples
            )

        coordinated = copy.deepcopy(report)
        moved = coordinated["predictions"].pop()
        coordinated["exclusions"] = [
            {
                "sample_id": moved["sample_id"],
                "classification": "not_evaluation_eligible",
                "reason_codes": ["invented_not_eligible"],
                "groups": moved["groups"],
            }
        ]
        coordinated["coverage"].update(
            {
                "evaluation_eligible_record_count": 4,
                "evaluated_record_count": 4,
                "valid_rate": 0.8,
                "evaluation_eligible_rate": 0.8,
                "eligible_evaluation_completion_rate": 1.0,
                "excluded_record_count": 1,
                "exclusion_reason_counts": {"invented_not_eligible": 1},
            }
        )
        overall = _metrics(coordinated["predictions"])
        coordinated["metrics"] = {
            "overall": overall,
            "by_view_group": _grouped_metrics(
                coordinated["predictions"], "view_group"
            ),
            "by_player": _grouped_metrics(coordinated["predictions"], "player_id"),
            "by_session": _grouped_metrics(coordinated["predictions"], "session_id"),
        }
        protocol = _independent_test_protocol(candidate)
        protocol["acceptance_metrics"].update(
            {
                "minimum_record_count": 4,
                "minimum_leakage_group_count": 4,
                "minimum_valid_rate": 0.8,
                "required_grade_coverage": sorted(
                    {row["expected_grade"] for row in coordinated["predictions"]}
                ),
            }
        )
        coordinated["protocol"] = {
            "protocol_id": protocol["protocol_id"],
            "protocol_version": protocol["protocol_version"],
            "content_sha256": canonical_sha256(protocol),
            "source_sha256": protocol["source"]["source_sha256"],
            "registered_at": protocol["source"]["registered_at"],
        }
        passed, checks = _acceptance(
            metrics=overall,
            valid_rate=0.8,
            rows=coordinated["predictions"],
            protocol=protocol,
        )
        self.assertTrue(passed)
        coordinated["acceptance"] = {
            "passed": passed,
            "checks": checks,
            "semantics": coordinated["acceptance"]["semantics"],
        }
        coordinated["status"] = "passed"
        decision = _decision(candidate, coordinated, registry)
        maturity = _maturity_evidence(candidate, coordinated, registry)
        decision["maturity_evidence"] = maturity_evidence_binding(maturity)
        with self.assertRaisesRegex(
            CalibrationPromotionError, "does not exactly match production source replay"
        ):
            promote_calibration_candidate(
                **{
                    **common,
                    "independent_test_report": coordinated,
                    "independent_test_protocol": protocol,
                    "decision": decision,
                    "maturity_evidence": maturity,
                },
                independent_test_samples=samples,
                indicator_requirements=requirements,
            )

    def test_production_rejects_missing_protocol_or_unreconciled_report(self) -> None:
        candidate = _candidate(synthetic=False)
        report = _report(candidate)
        registry = _registry(level="F4")

        def promotion_args(value: dict) -> dict:
            return {
                "candidate": candidate,
                "independent_test_report": value,
                "decision": _decision(candidate, value, registry),
                "feasibility_registry": registry,
                "promoted_at": "2026-08-13T03:00:00Z",
                "maturity_evidence": _maturity_evidence(
                    candidate, value, registry
                ),
                "verified_truth_authorization": _verified_truth_authorization(),
            }

        with self.assertRaisesRegex(
            CalibrationPromotionError, "exact independent-test protocol payload"
        ):
            promote_calibration_candidate(**promotion_args(report))

        mutations = (
            (
                "empty-placeholder",
                lambda value: (
                    value.__setitem__("coverage", {}),
                    value.__setitem__("metrics", {}),
                    value["acceptance"].__setitem__("checks", []),
                    value.__setitem__("predictions", []),
                    value.__setitem__("exclusions", []),
                ),
            ),
            (
                "coverage",
                lambda value: value["coverage"].__setitem__(
                    "evaluated_record_count", 4
                ),
            ),
            (
                "prediction",
                lambda value: value["predictions"][0].__setitem__(
                    "predicted_grade", "A"
                ),
            ),
            (
                "metrics",
                lambda value: value["metrics"]["overall"].__setitem__(
                    "accuracy", 0.8
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
            with self.subTest(label=label), self.assertRaisesRegex(
                CalibrationPromotionError,
                "invalid production independent-test evidence",
            ):
                promote_calibration_candidate(
                    **promotion_args(drifted),
                    independent_test_protocol=_independent_test_protocol(candidate),
                )

        drifted_protocol = _independent_test_protocol(candidate)
        drifted_protocol["acceptance_metrics"]["minimum_record_count"] = 4
        with self.assertRaisesRegex(
            CalibrationPromotionError,
            "does not bind the supplied independent-test protocol",
        ):
            promote_calibration_candidate(
                **promotion_args(report),
                independent_test_protocol=drifted_protocol,
            )

    def test_production_rejects_incomplete_or_zero_eligible_coverage(self) -> None:
        candidate = _candidate(synthetic=False)
        registry = _registry(level="F4")
        report = _report(candidate)

        def promotion_args(value: dict) -> dict:
            return {
                "candidate": candidate,
                "independent_test_report": value,
                "independent_test_protocol": _independent_test_protocol(candidate),
                "decision": _decision(candidate, value, registry),
                "feasibility_registry": registry,
                "promoted_at": "2026-08-13T03:00:00Z",
                "maturity_evidence": _maturity_evidence(candidate, value, registry),
                "verified_truth_authorization": _verified_truth_authorization(),
            }

        incomplete = copy.deepcopy(report)
        incomplete["coverage"].update(
            {
                "evaluation_eligible_record_count": 5,
                "evaluated_record_count": 4,
                "valid_rate": 0.8,
                "evaluation_eligible_rate": 1.0,
                "eligible_evaluation_completion_rate": 0.8,
                "excluded_record_count": 1,
            }
        )
        with self.assertRaisesRegex(
            CalibrationPromotionError,
            "complete non-empty evaluation-eligible coverage",
        ):
            promote_calibration_candidate(**promotion_args(incomplete))

        zero_eligible = copy.deepcopy(report)
        zero_eligible["coverage"].update(
            {
                "evaluation_eligible_record_count": 0,
                "evaluated_record_count": 0,
                "valid_rate": 0.0,
                "evaluation_eligible_rate": 0.0,
                "eligible_evaluation_completion_rate": 0.0,
                "excluded_record_count": 5,
            }
        )
        with self.assertRaisesRegex(
            CalibrationPromotionError,
            "complete non-empty evaluation-eligible coverage",
        ):
            promote_calibration_candidate(**promotion_args(zero_eligible))

    def test_serialized_or_drifted_truth_binding_cannot_promote_or_build_ledger(self) -> None:
        candidate = _candidate(synthetic=False)
        samples, requirements, report = _production_replay_fixture(candidate)
        registry = _registry(level="F4")
        maturity = _maturity_evidence(candidate, report, registry)
        decision = _decision(candidate, report, registry)
        common = {
            "candidate": candidate,
            "independent_test_report": report,
            "independent_test_protocol": _independent_test_protocol(candidate),
            "independent_test_samples": samples,
            "indicator_requirements": requirements,
            "decision": decision,
            "feasibility_registry": registry,
            "promoted_at": "2026-08-13T03:00:00Z",
            "maturity_evidence": maturity,
        }
        with self.assertRaisesRegex(
            CalibrationPromotionError,
            "same-process verified authorized-intake object",
        ):
            promote_calibration_candidate(**common)
        with self.assertRaisesRegex(
            CalibrationPromotionError,
            "same-process verified authorized-intake object",
        ):
            promote_calibration_candidate(
                **common,
                verified_truth_authorization=candidate["truth_authorization"],
            )

        drifted_binding = copy.deepcopy(candidate["truth_authorization"])
        drifted_binding["authorization_id"] = "private-test-drifted-authorization"
        drifted_binding["binding_sha256"] = (
            scoring_truth_calibration_authorization_binding_sha256(
                drifted_binding
            )
        )
        drifted_wrapper = _issue_verified_scoring_truth_calibration_authorization(
            drifted_binding
        )
        with self.assertRaisesRegex(
            CalibrationPromotionError, "differs from live verification"
        ):
            promote_calibration_candidate(
                **common, verified_truth_authorization=drifted_wrapper
            )

        asset, promotion = promote_calibration_candidate(
            **common,
            verified_truth_authorization=_verified_truth_authorization(),
        )
        ledger_args = {
            "promotions": [(asset, promotion)],
            "ledger_id": "trusted-ledger-live-replay",
            "ledger_version": "v1",
            "authority_id": "release-owner",
            "registered_at": "2026-08-13T04:00:00Z",
        }
        with self.assertRaisesRegex(
            CalibrationPromotionError, "same-process verified truth authorization"
        ):
            build_trusted_promotion_ledger(**ledger_args)
        with self.assertRaisesRegex(
            CalibrationPromotionError, "truth authorization is not live"
        ):
            build_trusted_promotion_ledger(
                **ledger_args,
                verified_truth_authorizations={
                    "FS01-M02": candidate["truth_authorization"]
                },
            )
        with self.assertRaisesRegex(
            CalibrationPromotionError, "differs from live verification"
        ):
            build_trusted_promotion_ledger(
                **ledger_args,
                verified_truth_authorizations={"FS01-M02": drifted_wrapper},
            )

    def test_production_requires_complete_maturity_evidence(self) -> None:
        candidate = _candidate(synthetic=False)
        report = _report(candidate)
        registry = _registry(level="F4")
        with self.assertRaisesRegex(
            CalibrationPromotionError, "complete F0-to-F4 maturity evidence"
        ):
            promote_calibration_candidate(
                candidate=candidate,
                independent_test_report=report,
                independent_test_protocol=_independent_test_protocol(candidate),
                decision=_decision(candidate, report, registry),
                feasibility_registry=registry,
                promoted_at="2026-08-13T03:00:00Z",
                verified_truth_authorization=_verified_truth_authorization(),
            )

    def test_maturity_features_and_report_registry_binding_cannot_be_forged(self) -> None:
        candidate = _candidate(synthetic=False)
        registry = _registry(level="F4")

        report = _report(candidate)
        report["indicator_requirements"]["registry_content_sha256"] = "9" * 64
        maturity = _maturity_evidence(candidate, report, registry)
        decision = _decision(candidate, report, registry)
        decision["maturity_evidence"] = maturity_evidence_binding(maturity)
        with self.assertRaisesRegex(
            CalibrationPromotionError,
            "indicator_requirements.registry_content_sha256 lineage mismatch",
        ):
            promote_calibration_candidate(
                candidate=candidate,
                independent_test_report=report,
                independent_test_protocol=_independent_test_protocol(candidate),
                decision=decision,
                feasibility_registry=registry,
                promoted_at="2026-08-13T03:00:00Z",
                maturity_evidence=maturity,
                verified_truth_authorization=_verified_truth_authorization(),
            )

        report = _report(candidate)
        maturity = _maturity_evidence(candidate, report, registry)
        f2 = maturity["transitions"][1]["evidence"]["payload"]
        f2["feature_evaluation"]["features"][0]["feature_name"] = "wrong_feature"
        f2["error_budget"]["features"][0]["feature_name"] = "wrong_feature"
        f2["grade_gap_assessment"]["feature_decisions"][0][
            "feature_name"
        ] = "wrong_feature"
        _rehash(maturity, start=1)
        decision = _decision(candidate, report, registry)
        decision["maturity_evidence"] = maturity_evidence_binding(maturity)
        with self.assertRaisesRegex(
            CalibrationPromotionError,
            "evaluated features do not exactly match",
        ):
            promote_calibration_candidate(
                candidate=candidate,
                independent_test_report=report,
                independent_test_protocol=_independent_test_protocol(candidate),
                decision=decision,
                feasibility_registry=registry,
                promoted_at="2026-08-13T03:00:00Z",
                maturity_evidence=maturity,
                verified_truth_authorization=_verified_truth_authorization(),
            )

    def test_ledger_and_registry_authorization_still_requires_runtime_profile(self) -> None:
        candidate = _candidate(synthetic=False)
        samples, requirements, report = _production_replay_fixture(candidate)
        registry = _registry(level="F4")
        asset, promotion = promote_calibration_candidate(
            candidate=candidate,
            independent_test_report=report,
            independent_test_protocol=_independent_test_protocol(candidate),
            independent_test_samples=samples,
            indicator_requirements=requirements,
            decision=_decision(candidate, report, registry),
            feasibility_registry=registry,
            promoted_at="2026-08-13T03:00:00Z",
            maturity_evidence=_maturity_evidence(candidate, report, registry),
            verified_truth_authorization=_verified_truth_authorization(),
        )
        ledger = build_trusted_promotion_ledger(
            promotions=[(asset, promotion)],
            ledger_id="trusted-ledger",
            ledger_version="v1",
            authority_id="release-owner",
            registered_at="2026-08-13T04:00:00Z",
            verified_truth_authorizations={
                "FS01-M02": _verified_truth_authorization()
            },
        )
        self.assertEqual(ledger["schema_version"], "1.2.0")
        root = Path(__file__).resolve().parents[1]
        for schema_name, artifact in (
            ("calibration-candidate.schema.json", candidate),
            ("calibration-independent-test-report.schema.json", report),
            (
                "calibration-promotion-lineage.schema.json",
                asset["promotion_lineage"],
            ),
            ("calibration-promotion-report.schema.json", promotion),
            ("trusted-calibration-promotion-ledger.schema.json", ledger),
        ):
            schema = json.loads(
                (root / "contracts" / schema_name).read_text(encoding="utf-8")
            )
            Draft202012Validator.check_schema(schema)
            Draft202012Validator(
                schema, format_checker=FormatChecker()
            ).validate(artifact)

        ledger_schema = json.loads(
            (
                root
                / "contracts"
                / "trusted-calibration-promotion-ledger.schema.json"
            ).read_text(encoding="utf-8")
        )
        Draft202012Validator.check_schema(ledger_schema)
        ledger_validator = Draft202012Validator(
            ledger_schema, format_checker=FormatChecker()
        )
        ledger_validator.validate(ledger)
        for missing_field in (
            "independent_test_source_replay",
            "promotion_input_snapshots",
        ):
            with self.subTest(ledger_lineage_missing=missing_field):
                invalid_ledger = copy.deepcopy(ledger)
                del invalid_ledger["entries"][0]["promotion_lineage"][missing_field]
                self.assertTrue(list(ledger_validator.iter_errors(invalid_ledger)))
        invalid_ledger = copy.deepcopy(ledger)
        invalid_ledger["entries"][0]["promotion_lineage"]["schema_version"] = (
            "1.2.0"
        )
        self.assertTrue(list(ledger_validator.iter_errors(invalid_ledger)))

        synthetic_candidate = _candidate()
        synthetic_report = _report(synthetic_candidate)
        synthetic_registry = _registry()
        _, synthetic_promotion = promote_calibration_candidate(
            candidate=synthetic_candidate,
            independent_test_report=synthetic_report,
            decision=_decision(
                synthetic_candidate, synthetic_report, synthetic_registry
            ),
            feasibility_registry=synthetic_registry,
            promoted_at="2026-08-13T03:00:00Z",
        )
        promotion_schema = json.loads(
            (
                root / "contracts" / "calibration-promotion-report.schema.json"
            ).read_text(encoding="utf-8")
        )
        Draft202012Validator.check_schema(promotion_schema)
        promotion_validator = Draft202012Validator(
            promotion_schema, format_checker=FormatChecker()
        )
        promotion_validator.validate(synthetic_promotion)
        for forbidden_field in (
            "independent_test_source_replay",
            "promotion_input_snapshots",
        ):
            with self.subTest(synthetic_report_forbidden=forbidden_field):
                invalid_promotion = copy.deepcopy(synthetic_promotion)
                invalid_promotion[forbidden_field] = promotion[forbidden_field]
                self.assertTrue(
                    list(promotion_validator.iter_errors(invalid_promotion))
                )
        trusted = authorize_production_asset_with_trusted_ledger(asset, ledger)
        self.assertEqual(
            trusted.authorization["promoted_registry_version"],
            registry["registry_version"],
        )
        self.assertEqual(
            trusted.authorization["promoted_registry_content_sha256"],
            canonical_sha256(registry),
        )
        trusted = bind_trusted_production_calibration_to_registry(
            trusted, registry
        )
        with self.assertRaises(TypeError):
            json.dumps(trusted)
        result = score_indicator(
            indicator_id="FS01-M02",
            features=[
                {
                    "feature_name": "feature_a",
                    "feature_version": "feature-a-v1",
                    "value": 20.0,
                    "unit": "body",
                    "confidence": 1.0,
                    "valid": True,
                    "reason": "valid",
                    "source_frames": [1],
                }
            ],
            calibration=trusted,
            model_versions={"pose": "synthetic"},
            evidence=[],
            feasibility_level="F4",
        )
        self.assertEqual(result["status"], "calibration_required")
        self.assertIsNone(result["grade"])
        self.assertIn("runtime_profile_binding_required", result["reason_codes"])
        self.assertEqual(
            result["model_versions"]["maturity_evidence_bundle"],
            asset["promotion_lineage"]["maturity_evidence"]["bundle_id"],
        )

    def test_failed_or_preapproved_report_is_rejected(self) -> None:
        candidate = _candidate()
        registry = _registry()
        for field, value, pattern in (
            ("status", "failed", "status must be passed"),
            ("approved_for_scoring", True, "must remain unapproved"),
        ):
            report = _report(candidate)
            decision = _decision(candidate, report, registry)
            report[field] = value
            with self.subTest(field=field), self.assertRaisesRegex(
                CalibrationPromotionError, pattern
            ):
                promote_calibration_candidate(
                    candidate=candidate,
                    independent_test_report=report,
                    decision=decision,
                    feasibility_registry=registry,
                    promoted_at="2026-08-13T03:00:00Z",
                )

    def test_tampered_candidate_report_or_registry_lineage_is_rejected(self) -> None:
        mutations = (
            lambda decision: decision["candidate"].__setitem__("content_sha256", "9" * 64),
            lambda decision: decision["independent_test_report"].__setitem__(
                "content_sha256", "9" * 64
            ),
            lambda decision: decision["feasibility_registry"].__setitem__(
                "content_sha256", "9" * 64
            ),
            lambda decision: decision["independent_test_protocol"].__setitem__(
                "protocol_version", "wrong"
            ),
        )
        for mutate in mutations:
            candidate = _candidate()
            report = _report(candidate)
            registry = _registry()
            decision = _decision(candidate, report, registry)
            mutate(decision)
            with self.subTest(mutate=mutate), self.assertRaisesRegex(
                CalibrationPromotionError, "lineage mismatch"
            ):
                promote_calibration_candidate(
                    candidate=candidate,
                    independent_test_report=report,
                    decision=decision,
                    feasibility_registry=registry,
                    promoted_at="2026-08-13T03:00:00Z",
                )

    def test_decision_time_order_and_attestations_are_enforced(self) -> None:
        candidate = _candidate()
        report = _report(candidate)
        registry = _registry()
        decision = _decision(candidate, report, registry)
        decision["decided_at"] = "2026-08-13T00:30:00Z"
        with self.assertRaises(CalibrationPromotionError):
            promote_calibration_candidate(
                candidate=candidate,
                independent_test_report=report,
                decision=decision,
                feasibility_registry=registry,
                promoted_at="2026-08-13T03:00:00Z",
            )
        decision = _decision(candidate, report, registry)
        decision["attestations"]["registry_maturity_reviewed"] = False
        with self.assertRaisesRegex(CalibrationPromotionError, "attestations"):
            promote_calibration_candidate(
                candidate=candidate,
                independent_test_report=report,
                decision=decision,
                feasibility_registry=registry,
                    promoted_at="2026-08-13T03:00:00Z",
                )

    def test_cli_production_stays_closed_without_live_truth_authority(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            candidate = _candidate(synthetic=False)
            report = _report(candidate)
            registry = _registry(level="F4")
            registry["scope"]["extends_registry"] = "historical-registry-v1"
            maturity = _maturity_evidence(candidate, report, registry)
            decision = _decision(candidate, report, registry)
            protocol = _independent_test_protocol(candidate)

            input_paths = {
                "candidate": work / "candidate.json",
                "report": work / "independent.json",
                "protocol": work / "independent-protocol.json",
                "samples": work / "independent-samples.jsonl",
                "requirements": work / "indicator-requirements.json",
                "decision": work / "decision.json",
                "registry": work / "current-feasibility.json",
                "maturity": work / "maturity.json",
            }
            for name, payload in (
                ("candidate", candidate),
                ("report", report),
                ("protocol", protocol),
                ("decision", decision),
                ("registry", registry),
                ("maturity", maturity),
            ):
                input_paths[name].write_text(json.dumps(payload), encoding="utf-8")
            input_paths["samples"].write_text("{}\n", encoding="utf-8")
            input_paths["requirements"].write_text("{}", encoding="utf-8")
            manifest_path, historical_path = _write_registry_lifecycle(
                work,
                registry_path=input_paths["registry"],
                registry=registry,
            )
            identical_copy = work / "byte-identical-current-copy.json"
            identical_copy.write_bytes(input_paths["registry"].read_bytes())

            def command_for(pin: Path, prefix: str) -> tuple[list[str], tuple[Path, ...]]:
                outputs = (
                    work / f"{prefix}-asset.json",
                    work / f"{prefix}-promotion.json",
                    work / f"{prefix}-ledger.json",
                )
                return (
                    [
                        sys.executable,
                        str(root / "scripts" / "promote_calibration_candidate.py"),
                        "--candidate",
                        str(input_paths["candidate"]),
                        "--independent-test-report",
                        str(input_paths["report"]),
                        "--independent-test-protocol",
                        str(input_paths["protocol"]),
                        "--independent-test-samples",
                        str(input_paths["samples"]),
                        "--indicator-requirements",
                        str(input_paths["requirements"]),
                        "--decision",
                        str(input_paths["decision"]),
                        "--feasibility-registry",
                        str(pin),
                        "--registry-lifecycle-manifest",
                        str(manifest_path),
                        "--maturity-evidence",
                        str(input_paths["maturity"]),
                        "--promoted-at",
                        "2026-08-13T03:00:00Z",
                        "--asset-output",
                        str(outputs[0]),
                        "--promotion-report-output",
                        str(outputs[1]),
                        "--trusted-ledger-output",
                        str(outputs[2]),
                        "--ledger-id",
                        "trusted-ledger-v1",
                        "--ledger-version",
                        "v1",
                        "--ledger-authority-id",
                        "release-owner",
                        "--ledger-registered-at",
                        "2026-08-13T04:00:00Z",
                    ],
                    outputs,
                )

            current_command, current_outputs = command_for(
                input_paths["registry"], "current"
            )
            blocked_current = subprocess.run(
                current_command, cwd=root, capture_output=True, text=True
            )
            self.assertEqual(blocked_current.returncode, 2, blocked_current.stderr)
            self.assertIn(
                "same-process verified authorized-intake object is required",
                blocked_current.stderr,
            )
            self.assertTrue(all(not path.exists() for path in current_outputs))

            for label, rejected_pin in (
                ("historical", historical_path),
                ("identical-copy", identical_copy),
            ):
                with self.subTest(pin=label):
                    rejected_command, rejected_outputs = command_for(
                        rejected_pin, label
                    )
                    rejected = subprocess.run(
                        rejected_command, cwd=root, capture_output=True, text=True
                    )
                    self.assertEqual(rejected.returncode, 2, rejected.stderr)
                    self.assertIn("must exactly match", rejected.stderr)
                    self.assertTrue(
                        all(not path.exists() for path in rejected_outputs)
                    )

    def test_cli_object_inputs_reject_ambiguous_json_before_write(self) -> None:
        root = Path(__file__).resolve().parents[1]
        candidate = _candidate()
        report = _report(candidate)
        registry = _registry()
        decision = _decision(candidate, report, registry)
        protocol = _independent_test_protocol(candidate)
        invalid_cases = (
            ("duplicate", b'{"probe":1,"probe":2}', "duplicate JSON key"),
            ("nan", b'{"probe":NaN}', "non-finite JSON constant"),
            ("infinity", b'{"probe":Infinity}', "non-finite JSON constant"),
            ("negative-infinity", b'{"probe":-Infinity}', "non-finite JSON constant"),
            ("overflow-float", b'{"probe":1e9999}', "non-finite JSON number"),
            (
                "isolated-surrogate",
                b'{"probe":"\\ud800"}',
                "cannot be encoded as UTF-8",
            ),
            ("invalid-utf8", b"{\xff}", "cannot read"),
        )
        for target in (
            "candidate",
            "report",
            "protocol",
            "decision",
            "registry",
            "maturity",
        ):
            for case_name, invalid_raw, expected_error in invalid_cases:
                with self.subTest(target=target, invalid=case_name):
                    with tempfile.TemporaryDirectory() as directory:
                        work = Path(directory)
                        paths = {
                            "candidate": work / "candidate.json",
                            "report": work / "report.json",
                            "protocol": work / "protocol.json",
                            "decision": work / "decision.json",
                            "registry": work / "registry.json",
                            "maturity": work / "maturity.json",
                        }
                        for name, payload in (
                            ("candidate", candidate),
                            ("report", report),
                            ("protocol", protocol),
                            ("decision", decision),
                            ("registry", registry),
                        ):
                            paths[name].write_text(
                                json.dumps(payload), encoding="utf-8"
                            )
                        paths[target].write_bytes(invalid_raw)
                        outputs = (
                            work / "asset.json",
                            work / "promotion.json",
                        )
                        command = [
                            sys.executable,
                            str(root / "scripts" / "promote_calibration_candidate.py"),
                            "--candidate", str(paths["candidate"]),
                            "--independent-test-report", str(paths["report"]),
                            "--independent-test-protocol", str(paths["protocol"]),
                            "--decision", str(paths["decision"]),
                            "--feasibility-registry", str(paths["registry"]),
                            "--promoted-at", "2026-08-13T03:00:00Z",
                            "--asset-output", str(outputs[0]),
                            "--promotion-report-output", str(outputs[1]),
                        ]
                        if target == "maturity":
                            command.extend(
                                ["--maturity-evidence", str(paths["maturity"])]
                            )
                        result = subprocess.run(
                            command, cwd=root, capture_output=True, text=True
                        )
                        self.assertEqual(result.returncode, 2, result.stderr)
                        self.assertIn(expected_error, result.stderr)
                        self.assertNotIn("Traceback", result.stderr)
                        self.assertTrue(
                            all(not path.exists() for path in outputs)
                        )

    def test_object_snapshot_hashes_exact_bytes_and_canonical_payload(self) -> None:
        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location(
            "rallymate_test_promote_snapshot_loader",
            root / "scripts" / "promote_calibration_candidate.py",
        )
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "object.json"
            raw = b'{\n  "value": 1,\n  "text": "snapshot"\n}\n'
            path.write_bytes(raw)
            payload, source = module._load_object_snapshot(path)
        self.assertEqual(payload, {"value": 1, "text": "snapshot"})
        self.assertEqual(source["raw_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(source["canonical_sha256"], canonical_sha256(payload))

    def test_cli_replay_inputs_reject_duplicate_keys_and_nonfinite_constants_before_write(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            inputs = {
                "candidate": work / "candidate.json",
                "report": work / "report.json",
                "protocol": work / "protocol.json",
                "decision": work / "decision.json",
                "samples": work / "samples.jsonl",
                "requirements": work / "requirements.json",
            }
            inputs["candidate"].write_text(
                '{"independent_test":{"record_count":1}}', encoding="utf-8"
            )
            for name in ("report", "protocol"):
                inputs[name].write_text("{}", encoding="utf-8")
            inputs["decision"].write_text(
                '{"requested_artifact_scope":"production"}', encoding="utf-8"
            )

            def command(prefix: str) -> tuple[list[str], tuple[Path, Path]]:
                outputs = (work / f"{prefix}-asset.json", work / f"{prefix}-report.json")
                return (
                    [
                        sys.executable,
                        str(root / "scripts" / "promote_calibration_candidate.py"),
                        "--candidate", str(inputs["candidate"]),
                        "--independent-test-report", str(inputs["report"]),
                        "--independent-test-protocol", str(inputs["protocol"]),
                        "--independent-test-samples", str(inputs["samples"]),
                        "--indicator-requirements", str(inputs["requirements"]),
                        "--decision", str(inputs["decision"]),
                        "--feasibility-registry", str(work / "unused-registry.json"),
                        "--promoted-at", "2026-08-13T03:00:00Z",
                        "--asset-output", str(outputs[0]),
                        "--promotion-report-output", str(outputs[1]),
                    ],
                    outputs,
                )

            inputs["samples"].write_text(
                '{"sample_id":"first","sample_id":"second"}\n',
                encoding="utf-8",
            )
            inputs["requirements"].write_text("{}", encoding="utf-8")
            command_line, outputs = command("duplicate")
            result = subprocess.run(command_line, cwd=root, capture_output=True, text=True)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("duplicate JSON key", result.stderr)
            self.assertTrue(all(not path.exists() for path in outputs))

            inputs["samples"].write_text("{}\n", encoding="utf-8")
            inputs["requirements"].write_text('{"bad":NaN}', encoding="utf-8")
            command_line, outputs = command("nonfinite")
            result = subprocess.run(command_line, cwd=root, capture_output=True, text=True)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("non-finite JSON constant", result.stderr)
            self.assertTrue(all(not path.exists() for path in outputs))

            for case_name, invalid_raw, expected_error in (
                (
                    "overflow-float-jsonl",
                    b'{"probe":1e9999}\n',
                    "non-finite JSON number",
                ),
                (
                    "isolated-surrogate-jsonl",
                    b'{"probe":"\\ud800"}\n',
                    "cannot be encoded as UTF-8",
                ),
            ):
                with self.subTest(case=case_name):
                    inputs["samples"].write_bytes(invalid_raw)
                    inputs["requirements"].write_text("{}", encoding="utf-8")
                    command_line, outputs = command(case_name)
                    result = subprocess.run(
                        command_line, cwd=root, capture_output=True, text=True
                    )
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertIn(expected_error, result.stderr)
                    self.assertNotIn("Traceback", result.stderr)
                    self.assertTrue(all(not path.exists() for path in outputs))

    def test_cli_rechecks_every_production_input_before_any_output_write(self) -> None:
        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location(
            "rallymate_test_promote_cli",
            root / "scripts" / "promote_calibration_candidate.py",
        )
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            paths = {
                "candidate": work / "candidate.json",
                "report": work / "report.json",
                "protocol": work / "protocol.json",
                "decision": work / "decision.json",
                "samples": work / "samples.jsonl",
                "requirements": work / "requirements.json",
                "maturity": work / "maturity.json",
            }
            paths["candidate"].write_text(
                '{"independent_test":{"record_count":1}}', encoding="utf-8"
            )
            for name in ("report", "protocol", "requirements", "maturity"):
                paths[name].write_text("{}", encoding="utf-8")
            paths["decision"].write_text(
                '{"requested_artifact_scope":"production"}', encoding="utf-8"
            )
            paths["samples"].write_text('{"sample_id":"before"}\n', encoding="utf-8")
            original_bytes = {
                name: path.read_bytes() for name, path in paths.items()
            }
            registry_authority = SimpleNamespace(
                payload={},
                manifest_path=work / "registry-lifecycle.json",
                manifest_sha256="a" * 64,
                authority_version="test-authority-v1",
                authority_slot="roles.runtime_feasibility",
                path=work / "unused-registry.json",
                file_sha256="b" * 64,
                embedded_version="registry-v1",
            )
            for target in (
                "candidate",
                "report",
                "protocol",
                "decision",
                "maturity",
                "samples",
                "requirements",
            ):
                with self.subTest(changed_input=target):
                    for name, raw in original_bytes.items():
                        paths[name].write_bytes(raw)
                    outputs = (
                        work / f"{target}-asset.json",
                        work / f"{target}-promotion.json",
                        work / f"{target}-ledger.json",
                    )

                    def mutate_input(**_kwargs):
                        paths[target].write_bytes(b'{"changed":true}\n')
                        return (
                            {
                                "artifact_scope": "production",
                                "indicator_id": "FS01-M02",
                            },
                            {"status": "promoted"},
                        )

                    argv = [
                        "promote_calibration_candidate.py",
                        "--candidate", str(paths["candidate"]),
                        "--independent-test-report", str(paths["report"]),
                        "--independent-test-protocol", str(paths["protocol"]),
                        "--independent-test-samples", str(paths["samples"]),
                        "--indicator-requirements", str(paths["requirements"]),
                        "--decision", str(paths["decision"]),
                        "--feasibility-registry", str(work / "unused-registry.json"),
                        "--maturity-evidence", str(paths["maturity"]),
                        "--promoted-at", "2026-08-13T03:00:00Z",
                        "--asset-output", str(outputs[0]),
                        "--promotion-report-output", str(outputs[1]),
                        "--trusted-ledger-output", str(outputs[2]),
                        "--ledger-id", "ledger-v1",
                        "--ledger-version", "v1",
                        "--ledger-authority-id", "release-owner",
                        "--ledger-registered-at", "2026-08-13T04:00:00Z",
                    ]
                    with (
                        patch.object(sys, "argv", argv),
                        patch.object(
                            module,
                            "_resolve_pinned_runtime_feasibility_registry",
                            return_value=registry_authority,
                        ),
                        patch.object(module, "_recheck_runtime_registry_authority"),
                        patch.object(
                            module, "validate_maturity_evidence_for_registry"
                        ),
                        patch.object(
                            module,
                            "promote_calibration_candidate",
                            side_effect=mutate_input,
                        ),
                        patch.object(
                            module,
                            "build_trusted_promotion_ledger",
                            return_value={},
                        ),
                    ):
                        stderr = io.StringIO()
                        with contextlib.redirect_stderr(stderr), self.assertRaises(
                            SystemExit
                        ) as raised:
                            module.main()
                    self.assertEqual(raised.exception.code, 2)
                    self.assertIn(
                        "input changed during promotion",
                        stderr.getvalue(),
                    )
                    self.assertTrue(
                        all(not path.exists() for path in outputs)
                    )

    def test_cli_rechecks_synthetic_inputs_before_test_only_output(self) -> None:
        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location(
            "rallymate_test_promote_synthetic_snapshot_cli",
            root / "scripts" / "promote_calibration_candidate.py",
        )
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        candidate = _candidate()
        report = _report(candidate)
        registry = _registry()
        protocol = _independent_test_protocol(candidate)
        decision = _decision(candidate, report, registry)
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            paths = {
                "candidate": work / "candidate.json",
                "report": work / "report.json",
                "protocol": work / "protocol.json",
                "decision": work / "decision.json",
                "registry": work / "registry.json",
            }
            for name, payload in (
                ("candidate", candidate),
                ("report", report),
                ("protocol", protocol),
                ("decision", decision),
                ("registry", registry),
            ):
                paths[name].write_text(json.dumps(payload), encoding="utf-8")
            original_bytes = {
                name: path.read_bytes() for name, path in paths.items()
            }
            original_promote = module.promote_calibration_candidate
            for target in paths:
                with self.subTest(changed_input=target):
                    for name, raw in original_bytes.items():
                        paths[name].write_bytes(raw)
                    outputs = (
                        work / f"synthetic-{target}-asset.json",
                        work / f"synthetic-{target}-promotion.json",
                    )

                    def promote_then_mutate(**kwargs):
                        result = original_promote(**kwargs)
                        paths[target].write_bytes(b'{"changed":true}\n')
                        return result

                    argv = [
                        "promote_calibration_candidate.py",
                        "--candidate", str(paths["candidate"]),
                        "--independent-test-report", str(paths["report"]),
                        "--independent-test-protocol", str(paths["protocol"]),
                        "--decision", str(paths["decision"]),
                        "--feasibility-registry", str(paths["registry"]),
                        "--promoted-at", "2026-08-13T03:00:00Z",
                        "--asset-output", str(outputs[0]),
                        "--promotion-report-output", str(outputs[1]),
                    ]
                    with (
                        patch.object(sys, "argv", argv),
                        patch.object(
                            module,
                            "promote_calibration_candidate",
                            side_effect=promote_then_mutate,
                        ),
                    ):
                        stderr = io.StringIO()
                        with contextlib.redirect_stderr(stderr), self.assertRaises(
                            SystemExit
                        ) as raised:
                            module.main()
                    self.assertEqual(raised.exception.code, 2)
                    self.assertIn(
                        "input changed during promotion", stderr.getvalue()
                    )
                    self.assertTrue(
                        all(not path.exists() for path in outputs)
                    )

    def test_cli_refuses_overwrite_and_F2_rejection_leaves_no_files(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            candidate = _candidate()
            report = _report(candidate)
            registry = _registry()
            decision = _decision(candidate, report, registry)
            paths = {
                "candidate": work / "candidate.json",
                "report": work / "independent.json",
                "decision": work / "decision.json",
                "registry": work / "registry.json",
            }
            for name, payload in (
                ("candidate", candidate),
                ("report", report),
                ("decision", decision),
                ("registry", registry),
            ):
                paths[name].write_text(json.dumps(payload), encoding="utf-8")
            asset = work / "asset.json"
            promotion = work / "promotion.json"
            command = [
                sys.executable,
                str(root / "scripts" / "promote_calibration_candidate.py"),
                "--candidate",
                str(paths["candidate"]),
                "--independent-test-report",
                str(paths["report"]),
                "--decision",
                str(paths["decision"]),
                "--feasibility-registry",
                str(paths["registry"]),
                "--promoted-at",
                "2026-08-13T03:00:00Z",
                "--asset-output",
                str(asset),
                "--promotion-report-output",
                str(promotion),
            ]
            first = subprocess.run(command, cwd=root, capture_output=True, text=True)
            self.assertEqual(first.returncode, 0, first.stderr)
            second = subprocess.run(command, cwd=root, capture_output=True, text=True)
            self.assertEqual(second.returncode, 2)
            self.assertIn("refusing to overwrite", second.stderr)

            real = _candidate(synthetic=False)
            real_report = _report(real)
            real_registry = _registry(level="F2")
            real_decision = _decision(real, real_report, real_registry)
            for name, payload in (
                ("candidate", real),
                ("report", real_report),
                ("decision", real_decision),
                ("registry", real_registry),
            ):
                paths[name].write_text(json.dumps(payload), encoding="utf-8")
            blocked_asset = work / "blocked-asset.json"
            blocked_promotion = work / "blocked-promotion.json"
            blocked_command = list(command)
            blocked_command[blocked_command.index(str(asset))] = str(blocked_asset)
            blocked_command[blocked_command.index(str(promotion))] = str(blocked_promotion)
            blocked = subprocess.run(
                blocked_command, cwd=root, capture_output=True, text=True
            )
            self.assertEqual(blocked.returncode, 2)
            self.assertFalse(blocked_asset.exists())
            self.assertFalse(blocked_promotion.exists())


if __name__ == "__main__":
    unittest.main()
