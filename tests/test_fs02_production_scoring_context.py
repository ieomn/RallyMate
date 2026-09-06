from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from rallymate_scoring.batch import score_indicator_records
from rallymate_scoring.calibration_fitting import (
    canonical_sha256,
    independent_test_seal_sha256,
)
from rallymate_scoring.calibration_independent_test import evaluate_independent_test
from rallymate_scoring.calibration_promotion import (
    authorize_production_asset_with_trusted_ledger,
    bind_trusted_production_calibration_to_registry,
    build_trusted_promotion_ledger,
    promote_calibration_candidate,
)
from rallymate_scoring.quality_policy import evaluate_indicator_event_quality
from rallymate_scoring.runtime_profile_binding import (
    bind_trusted_production_calibration_to_runtime_profile,
    build_runtime_scoring_profile,
)
from rallymate_scoring.scoring import score_indicator
from rallymate_scoring.scoring_context import (
    TARGET_DIRECTION_MISSING_FLAG,
    build_scoring_reference_worklist,
    canonical_sha256 as scoring_context_sha256,
    compact_target_direction_alignment_feature,
    resolve_target_direction_context,
    validate_resolved_target_direction_context,
    validate_scoring_reference_context,
)
from tests.test_calibration_promotion import (
    _candidate,
    _decision,
    _independent_test_protocol,
    _report,
    _verified_truth_authorization,
)
from tests.test_maturity_evidence import (
    _production_shaped_bundle,
    _rehash,
)
from tests.test_calibration_independent_test import _samples as _independent_samples


ROOT = Path(__file__).resolve().parents[1]
INDICATOR_ID = "FS02-M02"


def _f4_fs02_registry() -> tuple[dict, dict]:
    """Make an in-memory F4 fixture from the checked-in current FS02 contract."""

    registry = json.loads(
        (ROOT / "metric-feasibility-pose-wave-v2.json").read_text(encoding="utf-8")
    )
    registry["registry_version"] += "-fs02-context-f4-regression"
    registry["updated_at"] = "2026-08-13T00:00:00Z"
    indicator = next(
        item for item in registry["indicators"] if item["indicator_id"] == INDICATOR_ID
    )
    indicator["feasibility_level"] = "F4"
    indicator["current_blockers"] = []
    return registry, indicator


def _fs02_maturity_evidence(
    candidate: dict,
    report: dict,
    registry: dict,
) -> dict:
    """Adapt the production-shaped contract fixture to the real FS02 feature set."""

    bundle = _production_shaped_bundle()
    bundle["bundle_id"] = "contract-test-FS02-M02-maturity-v1"
    bundle["indicator_id"] = INDICATOR_ID
    for transition in bundle["transitions"]:
        transition["transition_id"] = transition["transition_id"].replace(
            "FS01-M02", INDICATOR_ID
        )
        transition["registry_before"]["indicator_id"] = INDICATOR_ID
        transition["registry_after"]["indicator_id"] = INDICATOR_ID

    bundle["transitions"][0]["evidence"]["payload"]["event_evaluation"][
        "event_codes"
    ] = ["FS02"]

    required_features = next(
        item["required_features"]
        for item in registry["indicators"]
        if item["indicator_id"] == INDICATOR_ID
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
            "indicator_id": INDICATOR_ID,
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


def _fs02_production_replay_fixture(candidate: dict) -> tuple[list[dict], dict, dict]:
    """Create exact source inputs for the FS02 production promotion fixture."""

    samples = _independent_samples()
    values_by_grade = {"E": 40.0, "D": 25.0, "C": 15.0, "B": 7.0, "A": 2.0}
    for sample in samples:
        sample["indicator_id"] = INDICATOR_ID
        sample["event_code"] = "FS02"
        for label in sample["labels"]["grades"]:
            label["indicator_id"] = INDICATOR_ID
        sample["lineage"]["coach_labels_sha256"] = canonical_sha256(
            sample["labels"]["grades"]
        )
        sample["lineage"]["join_key"]["indicator_id"] = INDICATOR_ID
        sample["feature_vector"] = [
            {
                "feature_name": "target_direction_alignment_error_deg",
                "feature_version": "target-direction-alignment-v1.0.0",
                "value": values_by_grade[sample["label_summary"]["resolved_grade"]],
                "unit": "deg",
                "confidence": 1.0,
                "valid": True,
                "reason": "valid",
                "source_frames": [0],
            }
        ]
        qualification_snapshot = sample["qualification_snapshot"]
        qualification_snapshot.update(
            {
                "quality_gate": evaluate_indicator_event_quality(
                    INDICATOR_ID, [TARGET_DIRECTION_MISSING_FLAG]
                ),
                "resolved_target_direction": True,
            }
        )
        qualification_snapshot["source_metadata"]["video_id"] = sample["video_id"]
        identity = {
            "video_id": sample["video_id"],
            "event_id": sample["event_id"],
            "indicator_id": INDICATOR_ID,
            "person_track_id": sample["person_track_id"],
        }
        sample["sample_id"] = "sample-" + canonical_sha256(identity)[:20]
    seal = independent_test_seal_sha256(samples)
    candidate["independent_test"].update(
        {
            "seal_id": f"seal-{seal[:16]}",
            "record_count": len(samples),
            "groups": sorted(item["groups"]["leakage_group_id"] for item in samples),
            "sample_ids": sorted(item["sample_id"] for item in samples),
            "content_sha256": seal,
        }
    )
    pre_f4_registry = _production_shaped_bundle()["transitions"][3][
        "registry_before"
    ]
    requirements = {
        "schema_version": "1.0.0",
        "artifact_scope": "indicator_scoring_requirements",
        "requirements_version": "fs02-production-replay-requirements-v1",
        "source": {
            "kind": "versioned_registry_and_manual_truth_contract",
            "registry_version": pre_f4_registry["registry_version"],
            "registry_content_sha256": pre_f4_registry["content_sha256"],
            "semantic_contract_version": "fs02-production-replay-semantics-v1",
            "semantic_contract_sha256": "d" * 64,
            "canonicalization": "rallymate-canonical-json-v1",
        },
        "indicators": [
            {
                "indicator_id": INDICATOR_ID,
                "required_feature_names": ["target_direction_alignment_error_deg"],
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


def _fs02_runtime_fixture() -> dict:
    registry, indicator = _f4_fs02_registry()
    candidate = _candidate(synthetic=False)
    candidate.update(
        {
            "candidate_id": "candidate-fs02-m02-target-direction-v1",
            "candidate_version": "candidate-fs02-m02-target-direction-v1",
            "indicator_id": INDICATOR_ID,
            "primary_feature": "target_direction_alignment_error_deg",
            "primary_feature_version": "target-direction-alignment-v1.0.0",
            "unit": "deg",
            "direction": "lower_is_better",
            "thresholds": [5.0, 10.0, 20.0, 30.0],
        }
    )
    samples, requirements, report = _fs02_production_replay_fixture(candidate)
    maturity_evidence = _fs02_maturity_evidence(candidate, report, registry)
    with patch(
        "tests.test_calibration_promotion._maturity_evidence",
        return_value=maturity_evidence,
    ):
        decision = _decision(candidate, report, registry)
    asset, promotion = promote_calibration_candidate(
        candidate=candidate,
        independent_test_report=report,
        independent_test_protocol=_independent_test_protocol(candidate),
        independent_test_samples=samples,
        indicator_requirements=requirements,
        decision=decision,
        feasibility_registry=registry,
        promoted_at="2026-08-13T03:00:00Z",
        maturity_evidence=maturity_evidence,
        verified_truth_authorization=_verified_truth_authorization(),
    )
    ledger = build_trusted_promotion_ledger(
        promotions=[(asset, promotion)],
        ledger_id="operator-ledger-fs02",
        ledger_version="ledger-fs02-v1",
        authority_id="release-owner",
        registered_at="2026-08-13T04:00:00Z",
        verified_truth_authorizations={
            INDICATOR_ID: _verified_truth_authorization()
        },
    )
    trusted = bind_trusted_production_calibration_to_registry(
        authorize_production_asset_with_trusted_ledger(asset, ledger),
        registry,
    )

    model_versions = {
        "pose_backend": "rtmpose",
        "pose_runtime": "pytorch",
        "pose_profile": "analysis",
        "pose_model_sha256": "A" * 64,
        "native_keypoint_format": "coco_wholebody133",
        "native_keypoint_count": 133,
        "keypoint_schema_version": "1.0.0",
        "indicator_definition": {
            INDICATOR_ID: indicator["versions"]["indicator_definition"]
        },
        "event_contract": indicator["versions"]["event_contract"],
        "event": indicator["versions"]["event_detector"],
        "phase_contract": indicator["versions"]["phase_contract"],
        "primary_player": indicator["versions"]["primary_player"],
        "quality_policy": indicator["versions"]["quality_policy"],
        "feature_contract": {
            INDICATOR_ID: indicator["versions"]["feature_contract"]
        },
    }
    profile = build_runtime_scoring_profile(
        model_versions=model_versions,
        indicator=indicator,
        profile_id="wholebody133-fixed-side-fs02-v1",
        view_profile_id="fixed-side-full-body-fs02-v1",
        view_group="fixed-side",
        verification_protocol_version="view-review-v1",
    )
    view_evidence = {
        "schema_version": "1.0.0",
        "artifact_scope": "runtime_view_evidence",
        "evidence_id": "view-evidence-fs02-video-001",
        "video_id": "fs02-video-001",
        "video_sha256": "B" * 64,
        "view_profile_id": profile["view"]["view_profile_id"],
        "view_group": profile["view"]["view_group"],
        "verification_protocol_version": profile["view"][
            "verification_protocol_version"
        ],
        "satisfied_constraints": list(profile["view"]["required_constraints"]),
        "status": "accepted",
        "verifier_id": "capture-reviewer-1",
        "verified_at": "2026-08-13T04:15:00Z",
        "source_sha256": "C" * 64,
        "canonicalization": "rallymate-canonical-json-v1",
    }
    authorization = trusted.authorization
    entry = {
        "binding_id": "runtime-binding-fs02-m02-v1",
        "status": "active",
        "indicator_id": INDICATOR_ID,
        "backend": asset["backend"],
        "calibration_version": asset["threshold_version"],
        "calibration_asset_sha256": canonical_sha256(asset),
        "trusted_promotion": {
            "ledger_id": authorization["ledger_id"],
            "ledger_version": authorization["ledger_version"],
            "entry_id": authorization["entry_id"],
        },
        "feasibility_registry": {
            "registry_version": registry["registry_version"],
            "content_sha256": canonical_sha256(registry),
        },
        "runtime_profile": copy.deepcopy(profile),
        "runtime_profile_sha256": canonical_sha256(profile),
        "compatibility_review": {
            "review_id": "compatibility-review-fs02-v1",
            "reviewer_id": "release-reviewer",
            "source_sha256": "D" * 64,
            "reviewed_at": "2026-08-13T04:20:00Z",
        },
        "registered_at": "2026-08-13T04:30:00Z",
    }
    binding_registry = {
        "schema_version": "1.0.0",
        "artifact_scope": "trusted_calibration_runtime_profile_bindings",
        "registry_id": "trusted-runtime-bindings-fs02",
        "registry_version": "runtime-bindings-fs02-v1",
        "source": {
            "authority_id": "release-owner",
            "source_sha256": "E" * 64,
            "registered_at": "2026-08-13T04:40:00Z",
        },
        "registered_at": "2026-08-13T05:00:00Z",
        "canonicalization": "rallymate-canonical-json-v1",
        "entries": [entry],
    }
    bound = bind_trusted_production_calibration_to_runtime_profile(
        trusted,
        binding_registry=binding_registry,
        runtime_profile=profile,
        view_evidence=view_evidence,
        feasibility_registry=registry,
    )
    return {
        "bound": bound,
        "indicator": indicator,
        "registry": registry,
        "model_versions": model_versions,
        "view_evidence": view_evidence,
    }


def _feature(
    name: str,
    *,
    value: float,
    unit: str,
    version: str = "1.0.0",
) -> dict:
    return {
        "feature_name": name,
        "feature_version": version,
        "value": value,
        "unit": unit,
        "confidence": 0.9,
        "valid": True,
        "reason": "valid_contract_fixture_measurement",
        "source_frames": [10, 20],
    }


class FS02ProductionScoringContextTests(unittest.TestCase):
    def test_runtime_bound_fs02_context_scores_and_each_evidence_item_is_bound(self) -> None:
        fixture = _fs02_runtime_fixture()
        video_id = fixture["view_evidence"]["video_id"]
        video_sha256 = fixture["view_evidence"]["video_sha256"]
        event = {
            "schema_version": "1.0.0",
            "video_id": video_id,
            "event_id": "fs02-event-001",
            "event_code": "FS02",
            "person_track_id": 1,
            "start_ms": 1000,
            "end_ms": 1600,
            "key_phases_ms": {"direction_conversion_ms": 1300},
            "confidence": 0.9,
            "boundary_uncertainty_ms": 10,
            "quality_flags": [TARGET_DIRECTION_MISSING_FLAG],
            "provenance": {"detector_version": "manual-contract-fixture"},
        }
        launch = _feature(
            "launch_direction_deg",
            value=20.0,
            unit="deg",
            version=fixture["indicator"]["versions"]["feature_contract"],
        )
        reference = build_scoring_reference_worklist(
            events=[event],
            video_id=video_id,
            video_sha256=video_sha256,
            created_at="2026-08-13T00:00:00Z",
        )
        reference["source"]["created_by"] = "coach-1"
        reference["observations"][0].update(
            {
                "status": "accepted",
                "target_direction_deg": 5.0,
                "coordinate_frame": "image_plane",
                "confidence": 0.95,
                "observer_id": "coach-1",
                "reviewer_id": "reviewer-2",
                "reason": None,
            }
        )
        observation = validate_scoring_reference_context(
            reference,
            expected_video_id=video_id,
            expected_video_sha256=video_sha256,
        )[event["event_id"]]
        context = resolve_target_direction_context(
            event=event,
            launch_direction_feature=launch,
            observation=observation,
            source_sha256=scoring_context_sha256(reference),
        )
        validate_resolved_target_direction_context(
            context,
            event=event,
            launch_direction_feature=launch,
        )
        context_feature = compact_target_direction_alignment_feature(
            context,
            launch_direction_feature=launch,
        )

        measurement_features = [
            _feature("body_center_speed_body_s", value=1.2, unit="body/s"),
            _feature(
                "hip_center_relative_to_ankle_support", value=0.1, unit="ratio"
            ),
            _feature("torso_lean_deg", value=12.0, unit="deg"),
            launch,
        ]
        scoring_features = [*measurement_features, context_feature]
        self.assertEqual(
            [item["feature_name"] for item in scoring_features],
            fixture["indicator"]["required_features"],
        )
        record = {
            "video_id": video_id,
            "event_id": event["event_id"],
            "event_code": "FS02",
            "person_track_id": 1,
            "indicator_id": INDICATOR_ID,
            "feasibility_level": "F4",
            "features": measurement_features,
            "feature_status": "measured",
            "scoring_features": scoring_features,
            "scoring_feature_status": "measured",
            "scoring_context": context,
            # The accepted context resolves the event's explicit target-direction
            # flag before the authoritative production gate is compared.
            "quality_gate": evaluate_indicator_event_quality(INDICATOR_ID, []),
            "provenance": {
                "video_sha256": video_sha256,
                "frames_sha256": "F" * 64,
            },
        }
        outputs, report = score_indicator_records(
            [record],
            calibrations={INDICATOR_ID: fixture["bound"]},
            model_versions=fixture["model_versions"],
            authoritative_events=[event],
            authoritative_indicators=fixture["registry"]["indicators"],
        )

        scored = outputs[0]
        self.assertEqual(scored["status"], "scored")
        self.assertEqual(scored["grade"], "C")
        self.assertEqual(report["status_counts"], {"scored": 1})
        context_evidence = next(
            item
            for item in scored["evidence"]
            if item.get("evidence_type") == "scoring_reference_context"
        )
        self.assertEqual(context_evidence["video_id"], video_id)
        self.assertEqual(context_evidence["video_sha256"], video_sha256)
        self.assertEqual(context_evidence["event_id"], event["event_id"])

        identity_mutations = (
            ("missing_video_id", "video_id", None, "runtime_view_video_mismatch"),
            ("wrong_video_id", "video_id", "other-video", "runtime_view_video_mismatch"),
            (
                "missing_video_sha256",
                "video_sha256",
                None,
                "runtime_view_video_sha256_missing",
            ),
            (
                "wrong_video_sha256",
                "video_sha256",
                "9" * 64,
                "runtime_view_video_sha256_mismatch",
            ),
        )
        for name, field, value, expected_reason in identity_mutations:
            evidence = copy.deepcopy(scored["evidence"])
            auxiliary = next(
                item
                for item in evidence
                if item.get("evidence_type") == "scoring_reference_context"
            )
            if value is None:
                auxiliary.pop(field)
            else:
                auxiliary[field] = value
            with self.subTest(mutation=name):
                blocked = score_indicator(
                    indicator_id=INDICATOR_ID,
                    features=scoring_features,
                    calibration=fixture["bound"],
                    model_versions=fixture["model_versions"],
                    evidence=evidence,
                    feasibility_level="F4",
                    event_quality_flags=[],
                )
                self.assertEqual(blocked["status"], "calibration_required")
                self.assertIsNone(blocked["grade"])
                self.assertIn(expected_reason, blocked["reason_codes"])

        for name, field, value, expected_reason in (
            ("primary_wrong_video_id", "video_id", "other-video", "runtime_view_video_mismatch"),
            (
                "primary_missing_video_sha256",
                "video_sha256",
                None,
                "runtime_view_video_sha256_missing",
            ),
        ):
            evidence = copy.deepcopy(scored["evidence"])
            primary = next(
                item
                for item in evidence
                if item.get("evidence_type") != "scoring_reference_context"
            )
            if value is None:
                primary.pop(field)
            else:
                primary[field] = value
            with self.subTest(mutation=name):
                blocked = score_indicator(
                    indicator_id=INDICATOR_ID,
                    features=scoring_features,
                    calibration=fixture["bound"],
                    model_versions=fixture["model_versions"],
                    evidence=evidence,
                    feasibility_level="F4",
                    event_quality_flags=[],
                )
                self.assertEqual(blocked["status"], "calibration_required")
                self.assertIsNone(blocked["grade"])
                self.assertIn(expected_reason, blocked["reason_codes"])


if __name__ == "__main__":
    unittest.main()
