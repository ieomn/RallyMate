from __future__ import annotations

import copy
import hashlib
import json
import unittest
from pathlib import Path

try:
    from jsonschema import Draft202012Validator, FormatChecker
except ModuleNotFoundError:  # The runtime contract does not depend on jsonschema.
    Draft202012Validator = None
    FormatChecker = None

from rallymate_scoring.calibration_fitting import canonical_sha256
from rallymate_scoring.maturity_evidence import (
    MATURITY_EVIDENCE_BUNDLE_VERSION,
    MaturityEvidenceError,
    PRODUCTION_SCOPE,
    TEST_ONLY_SCOPE,
    canonical_transition_sha256,
    maturity_evidence_binding,
    validate_maturity_evidence_bundle,
    validate_maturity_evidence_for_registry,
)


ROOT = Path(__file__).resolve().parents[1]


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _reference(label: str, *, source_kind: str = "synthetic_test_fixture") -> dict:
    return {
        "artifact_id": label,
        "artifact_version": "v1",
        "content_sha256": _sha(f"content:{label}"),
        "source_sha256": _sha(f"source:{label}"),
        "source_kind": source_kind,
        "registered_at": "2026-08-13T00:00:00Z",
    }


def _registry(level: str) -> dict:
    return {
        "registry_version": f"registry-{level.lower()}-v1",
        "content_sha256": _sha(f"registry:{level}"),
        "indicator_id": "FS01-M02",
        "indicator_level": level,
    }


def _error_stats() -> dict:
    return {
        "mae": 0.1,
        "p95": 0.2,
        "bias": -0.01,
        "valid_rate": 1.0,
        "eligible_count": 8,
        "valid_count": 8,
    }


def _metric_summary(name: str) -> list[dict]:
    return [{"metric_name": name, "value": 0.8, "unit": "ratio"}]


def _payload(to_level: str, acceptance_protocol: dict) -> dict:
    if to_level == "F1":
        return {
            "event_evaluation": {
                "status": "evaluated",
                "event_codes": ["FS01"],
                "event_f1": 0.9,
                "mean_segment_iou": 0.82,
                "boundary_mae_ms": 24.0,
                "evaluated_event_count": 12,
            },
            "manual_event_truth": _reference("manual-events"),
        }
    if to_level == "F2":
        feature = {
            "feature_name": "hip_center_y_body",
            "unit": "body",
            **_error_stats(),
            "by_view": [{"view_group": "fixed_side", **_error_stats()}],
        }
        budget = {
            "feature_name": "hip_center_y_body",
            "pose_error": _error_stats(),
            "event_boundary_error": _error_stats(),
            "smoothing_error": _error_stats(),
            "missing_value_impact": {
                "mean_valid_fraction_loss": 0.01,
                "p95_valid_fraction_loss": 0.03,
            },
        }
        return {
            "feature_evaluation": {"status": "evaluated", "features": [feature]},
            "error_budget": {
                "method": "one_factor_counterfactual_differences_not_additive_shapley_decomposition",
                "features": [budget],
            },
            "manual_keypoint_truth": _reference("manual-keypoints"),
            "manual_event_truth": _reference("manual-events-f2"),
            "grade_gap_assessment": {
                "status": "passed",
                "method": "external_preregistered_protocol",
                "feature_decisions": [
                    {"feature_name": "hip_center_y_body", "status": "passed"}
                ],
                "report": _reference("grade-gap-report"),
                "semantics": "feature_error_materially_smaller_than_potential_grade_separation",
            },
        }
    if to_level == "F3":
        return {
            "grade_separation": {
                "status": "passed",
                "method": "ordinal_group_separation",
                "metric_summary": _metric_summary("ordinal_separation"),
                "report": _reference("grade-separation-report"),
            },
            "multi_coach_agreement": {
                "status": "evaluated",
                "coach_count": 3,
                "metric_summary": _metric_summary("weighted_kappa"),
                "report": _reference("coach-agreement-report"),
                "coach_label_source": _reference("coach-labels"),
            },
            "internal_validation": {
                "status": "validated",
                "validation_scheme": "grouped_video_holdout",
                "metric_summary": _metric_summary("macro_mae"),
                "report": _reference("internal-validation-report"),
            },
        }
    return {
        "independent_test": {
            "status": "passed",
            "approved_for_scoring": True,
            "dataset": _reference("independent-test-dataset"),
            "report": _reference("independent-test-report"),
            "protocol": copy.deepcopy(acceptance_protocol),
            "metric_summary": _metric_summary("independent_grade_mae"),
            "independence_attestation": {
                "train_test_disjoint": True,
                "held_out_until_final_evaluation": True,
                "source": _reference("independence-attestation"),
            },
        }
    }


def _rehash(bundle: dict, start: int = 0) -> None:
    previous = None if start == 0 else bundle["transitions"][start - 1]["transition_sha256"]
    for index in range(start, len(bundle["transitions"])):
        transition = bundle["transitions"][index]
        transition["previous_transition_sha256"] = previous
        transition["evidence"]["payload_sha256"] = canonical_sha256(
            transition["evidence"]["payload"]
        )
        transition["transition_sha256"] = canonical_transition_sha256(transition)
        previous = transition["transition_sha256"]


def _bundle() -> dict:
    levels = (("F0", "F1"), ("F1", "F2"), ("F2", "F3"), ("F3", "F4"))
    transitions = []
    for index, (before, after) in enumerate(levels, start=1):
        protocol = _reference(f"protocol-{after}")
        payload = _payload(after, protocol)
        transitions.append(
            {
                "transition_id": f"FS01-M02-{before}-to-{after}",
                "from_level": before,
                "to_level": after,
                "previous_transition_sha256": None,
                "transition_sha256": _sha("uninitialized"),
                "registry_before": _registry(before),
                "registry_after": _registry(after),
                "evidence": {
                    "evidence_id": f"evidence-{after}",
                    "evidence_version": "v1",
                    "evaluated_at": f"2026-08-13T0{index}:00:00Z",
                    "source": _reference(f"evaluation-{after}"),
                    "payload": payload,
                    "payload_sha256": canonical_sha256(payload),
                },
                "acceptance": {
                    "decision": "passed",
                    "protocol": protocol,
                    "reviewer": {
                        "reviewer_id": f"reviewer-{index}",
                        "role": "maturity_reviewer",
                    },
                    "reviewed_at": f"2026-08-13T0{index}:30:00Z",
                    "source": _reference(f"review-{after}"),
                },
            }
        )
    bundle = {
        "schema_version": "1.0.0",
        "bundle_version": MATURITY_EVIDENCE_BUNDLE_VERSION,
        "artifact_scope": TEST_ONLY_SCOPE,
        "bundle_id": "synthetic-FS01-M02-maturity-v1",
        "indicator_id": "FS01-M02",
        "canonicalization": "rallymate-canonical-json-v1",
        "created_at": "2026-08-13T05:00:00Z",
        "source": _reference("bundle-source"),
        "transitions": transitions,
        "final_registry": copy.deepcopy(transitions[-1]["registry_after"]),
    }
    _rehash(bundle)
    return bundle


def _production_shaped_bundle() -> dict:
    bundle = _bundle()
    bundle["artifact_scope"] = PRODUCTION_SCOPE
    bundle["source"]["source_kind"] = "human_bundle_record"
    for transition in bundle["transitions"]:
        transition["evidence"]["source"]["source_kind"] = "evaluation_report"
        transition["acceptance"]["protocol"]["source_kind"] = (
            "external_preregistered_protocol"
        )
        transition["acceptance"]["source"]["source_kind"] = "human_review_record"
    f1 = bundle["transitions"][0]["evidence"]["payload"]
    f1["manual_event_truth"]["source_kind"] = "manual_event_annotations"
    f2 = bundle["transitions"][1]["evidence"]["payload"]
    f2["manual_keypoint_truth"]["source_kind"] = "manual_corrected_keypoints"
    f2["manual_event_truth"]["source_kind"] = "manual_event_annotations"
    f2["grade_gap_assessment"]["report"]["source_kind"] = (
        "coach_ground_truth_grade_gap_analysis"
    )
    f3 = bundle["transitions"][2]["evidence"]["payload"]
    f3["grade_separation"]["report"]["source_kind"] = "evaluation_report"
    f3["multi_coach_agreement"]["report"]["source_kind"] = "evaluation_report"
    f3["multi_coach_agreement"]["coach_label_source"]["source_kind"] = (
        "multi_coach_human_labels"
    )
    f3["internal_validation"]["report"]["source_kind"] = "evaluation_report"
    f4_transition = bundle["transitions"][3]
    f4 = f4_transition["evidence"]["payload"]["independent_test"]
    f4["dataset"]["source_kind"] = "independent_human_test_set"
    f4["report"]["source_kind"] = "evaluation_report"
    f4["protocol"] = copy.deepcopy(f4_transition["acceptance"]["protocol"])
    f4["independence_attestation"]["source"]["source_kind"] = "human_review_record"
    _rehash(bundle)
    return bundle


def _actual_f4_registry() -> dict:
    return {
        "schema_version": "2.0.0",
        "registry_version": "registry-f4-v1",
        "updated_at": "2026-08-13T04:30:00Z",
        "scope": {"events": ["FS01"], "indicator_count": 1},
        "level_definitions": {
            "F0": "structurally supported",
            "F1": "event localized",
            "F2": "feature measurable",
            "F3": "calibratable",
            "F4": "independently tested",
        },
        "indicators": [
            {
                "indicator_id": "FS01-M02",
                "feasibility_level": "F4",
                "required_events": ["FS01.loading"],
                "required_features": ["hip_center_y_body"],
                "view_constraints": ["fixed_camera"],
                "ground_truth_requirements": ["human_event_and_keypoint_truth"],
                "current_blockers": [],
                "acceptance_metrics": {"external_protocol_decision": None},
                "versions": {"feature_contract": "synthetic-v1"},
            }
        ],
    }


class MaturityEvidenceTests(unittest.TestCase):
    def test_complete_synthetic_chain_validates_and_is_machine_schema_valid(self) -> None:
        bundle = _bundle()
        audit = validate_maturity_evidence_bundle(
            bundle, expected_scope=TEST_ONLY_SCOPE
        )
        self.assertTrue(audit["valid"])
        self.assertEqual(len(audit["transition_sha256s"]), 4)
        self.assertEqual(audit["final_registry"]["indicator_level"], "F4")

        schema = json.loads(
            (ROOT / "contracts" / "maturity-evidence-bundle.schema.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(schema["properties"]["schema_version"]["const"], "1.0.0")
        self.assertEqual(schema["properties"]["transitions"]["minItems"], 4)
        self.assertEqual(schema["properties"]["transitions"]["maxItems"], 4)
        if Draft202012Validator is not None and FormatChecker is not None:
            errors = list(
                Draft202012Validator(
                    schema, format_checker=FormatChecker()
                ).iter_errors(bundle)
            )
            self.assertEqual([], errors, "\n".join(error.message for error in errors))

    def test_chain_is_fail_closed_when_any_transition_is_missing(self) -> None:
        bundle = _bundle()
        bundle["transitions"].pop(2)
        with self.assertRaisesRegex(MaturityEvidenceError, "exactly four"):
            validate_maturity_evidence_bundle(bundle)

    def test_payload_and_transition_hash_tampering_are_rejected(self) -> None:
        payload_tamper = _bundle()
        payload_tamper["transitions"][0]["evidence"]["payload"][
            "event_evaluation"
        ]["boundary_mae_ms"] = 999.0
        with self.assertRaisesRegex(MaturityEvidenceError, "payload hash mismatch"):
            validate_maturity_evidence_bundle(payload_tamper)

        transition_tamper = _bundle()
        transition_tamper["transitions"][1]["acceptance"]["reviewer"][
            "reviewer_id"
        ] = "tampered-reviewer"
        with self.assertRaisesRegex(MaturityEvidenceError, "transition hash mismatch"):
            validate_maturity_evidence_bundle(transition_tamper)

    def test_registry_history_and_previous_hash_must_be_sequential(self) -> None:
        registry_tamper = _bundle()
        registry_tamper["transitions"][2]["registry_before"]["content_sha256"] = _sha(
            "unrelated-registry"
        )
        _rehash(registry_tamper, start=2)
        with self.assertRaisesRegex(MaturityEvidenceError, "prior registry_after"):
            validate_maturity_evidence_bundle(registry_tamper)

        previous_hash_tamper = _bundle()
        previous_hash_tamper["transitions"][1]["previous_transition_sha256"] = _sha(
            "wrong-previous"
        )
        with self.assertRaisesRegex(MaturityEvidenceError, "previous transition hash"):
            validate_maturity_evidence_bundle(previous_hash_tamper)

    def test_f2_requires_error_budget_and_external_grade_gap_decisions(self) -> None:
        no_budget = _bundle()
        del no_budget["transitions"][1]["evidence"]["payload"]["error_budget"]
        _rehash(no_budget, start=1)
        with self.assertRaisesRegex(MaturityEvidenceError, "missing required fields"):
            validate_maturity_evidence_bundle(no_budget)

        failed_gap = _bundle()
        failed_gap["transitions"][1]["evidence"]["payload"][
            "grade_gap_assessment"
        ]["status"] = "failed"
        _rehash(failed_gap, start=1)
        with self.assertRaisesRegex(MaturityEvidenceError, "grade-gap assessment"):
            validate_maturity_evidence_bundle(failed_gap)

    def test_embedded_numeric_acceptance_threshold_is_rejected(self) -> None:
        bundle = _bundle()
        bundle["transitions"][0]["evidence"]["payload"]["event_evaluation"][
            "minimum_event_f1_for_promotion"
        ] = 0.8
        _rehash(bundle)
        with self.assertRaisesRegex(MaturityEvidenceError, "unsupported fields"):
            validate_maturity_evidence_bundle(bundle)

    def test_production_scope_requires_human_and_external_source_kinds(self) -> None:
        mislabeled = _bundle()
        mislabeled["artifact_scope"] = PRODUCTION_SCOPE
        with self.assertRaisesRegex(MaturityEvidenceError, "human_bundle_record"):
            validate_maturity_evidence_bundle(mislabeled)

        production = _production_shaped_bundle()
        production["transitions"][1]["acceptance"]["protocol"][
            "source_kind"
        ] = "synthetic_test_fixture"
        _rehash(production, start=1)
        with self.assertRaisesRegex(
            MaturityEvidenceError, "external_preregistered_protocol"
        ):
            validate_maturity_evidence_bundle(production)

        missing_human_truth = _production_shaped_bundle()
        missing_human_truth["transitions"][0]["evidence"]["payload"][
            "manual_event_truth"
        ]["source_kind"] = "synthetic_test_fixture"
        _rehash(missing_human_truth)
        with self.assertRaisesRegex(MaturityEvidenceError, "manual_event_annotations"):
            validate_maturity_evidence_bundle(missing_human_truth)

    def test_production_shaped_chain_with_external_bindings_validates(self) -> None:
        audit = validate_maturity_evidence_bundle(
            _production_shaped_bundle(), expected_scope=PRODUCTION_SCOPE
        )
        self.assertEqual(audit["artifact_scope"], PRODUCTION_SCOPE)
        self.assertEqual(
            audit["semantics"],
            "external_protocol_evidence_gate_no_numeric_thresholds_embedded",
        )

    def test_bundle_binds_to_exact_current_f4_registry(self) -> None:
        bundle = _bundle()
        registry = _actual_f4_registry()
        final_binding = bundle["transitions"][-1]["registry_after"]
        final_binding["registry_version"] = registry["registry_version"]
        final_binding["content_sha256"] = canonical_sha256(registry)
        bundle["final_registry"] = copy.deepcopy(final_binding)
        _rehash(bundle, start=3)
        audit = validate_maturity_evidence_for_registry(
            bundle, registry, expected_scope=TEST_ONLY_SCOPE
        )
        self.assertTrue(audit["registry_bound"])

        registry["updated_at"] = "2026-08-13T04:31:00Z"
        with self.assertRaisesRegex(MaturityEvidenceError, "content SHA-256"):
            validate_maturity_evidence_for_registry(bundle, registry)

    def test_binding_contains_final_transition_and_bundle_hash(self) -> None:
        bundle = _bundle()
        binding = maturity_evidence_binding(bundle)
        self.assertEqual(binding["content_sha256"], canonical_sha256(bundle))
        self.assertEqual(
            binding["final_transition_sha256"],
            bundle["transitions"][-1]["transition_sha256"],
        )
        self.assertNotIn("transitions", binding)

    def test_review_timestamp_and_preregistration_order_are_enforced(self) -> None:
        bundle = _bundle()
        bundle["transitions"][0]["acceptance"]["protocol"][
            "registered_at"
        ] = "2026-08-13T01:10:00Z"
        _rehash(bundle)
        with self.assertRaisesRegex(MaturityEvidenceError, "before evaluation"):
            validate_maturity_evidence_bundle(bundle)

    def test_non_evidence_template_cannot_validate(self) -> None:
        template = json.loads(
            (ROOT / "examples" / "maturity-evidence-bundle.template.json").read_text(
                encoding="utf-8"
            )
        )
        with self.assertRaises(MaturityEvidenceError):
            validate_maturity_evidence_bundle(template)


if __name__ == "__main__":
    unittest.main()
