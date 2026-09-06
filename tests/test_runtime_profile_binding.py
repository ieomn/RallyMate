from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

try:
    from jsonschema import Draft202012Validator
except ModuleNotFoundError:  # Runtime validation has no jsonschema dependency.
    Draft202012Validator = None

from rallymate_scoring.calibration_fitting import canonical_sha256
from rallymate_scoring.calibration_promotion import (
    authorize_production_asset_with_trusted_ledger,
    bind_trusted_production_calibration_to_registry,
    build_trusted_promotion_ledger,
    promote_calibration_candidate,
)
from rallymate_scoring.runtime_profile_binding import (
    RuntimeProfileBindingError,
    RuntimeProfileBoundProductionCalibration,
    bind_production_calibrations_for_runtime,
    bind_trusted_production_calibration_to_runtime_profile,
    build_runtime_scoring_profile,
    runtime_profile_versions,
    validate_runtime_scoring_profile,
    validate_runtime_view_evidence,
    validate_trusted_runtime_binding_registry,
)
from rallymate_scoring.scoring import score_indicator
from tests.test_calibration_promotion import (
    _candidate,
    _decision,
    _independent_test_protocol,
    _maturity_evidence,
    _production_replay_fixture,
    _registry,
    _report,
    _verified_truth_authorization,
)


ROOT = Path(__file__).resolve().parents[1]


def _runtime_fixture() -> dict:
    candidate = _candidate(synthetic=False)
    samples, requirements, report = _production_replay_fixture(candidate)
    registry = _registry(level="F4")
    indicator = next(
        item
        for item in registry["indicators"]
        if item["indicator_id"] == candidate["indicator_id"]
    )
    indicator["versions"] = {
        "indicator_definition": "fs01-m02.test-v1",
        "event_contract": "events.1.0.0",
        "event_detector": "pose-motion-bout-v0.3.0",
        "phase_contract": "pose-event-phase-proxies-v0.2.0",
        "primary_player": "primary-player-v0.3.0",
        "quality_policy": "indicator-event-quality-v1.6.0",
        "feature_contract": "test-feature-contract-v1",
    }
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
        ledger_id="operator-ledger",
        ledger_version="ledger-v1",
        authority_id="release-owner",
        registered_at="2026-08-13T04:00:00Z",
        verified_truth_authorizations={
            "FS01-M02": _verified_truth_authorization()
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
            candidate["indicator_id"]: indicator["versions"][
                "indicator_definition"
            ]
        },
        "event_contract": indicator["versions"]["event_contract"],
        "event": "pose-motion-bout-v0.3.0",
        "phase_contract": indicator["versions"]["phase_contract"],
        "primary_player": "primary-player-v0.3.0",
        "quality_policy": "indicator-event-quality-v1.6.0",
        "feature_contract": {
            candidate["indicator_id"]: indicator["versions"]["feature_contract"]
        },
    }
    profile = build_runtime_scoring_profile(
        model_versions=model_versions,
        indicator=indicator,
        profile_id="wholebody133-fixed-side-v1",
        view_profile_id="fixed-side-full-body-v1",
        view_group="fixed-side",
        verification_protocol_version="view-review-v1",
    )
    evidence = {
        "schema_version": "1.0.0",
        "artifact_scope": "runtime_view_evidence",
        "evidence_id": "view-evidence-video-001",
        "video_id": "video-001",
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
        "binding_id": "runtime-binding-fs01-m02-v1",
        "status": "active",
        "indicator_id": asset["indicator_id"],
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
            "review_id": "compatibility-review-v1",
            "reviewer_id": "release-reviewer",
            "source_sha256": "D" * 64,
            "reviewed_at": "2026-08-13T04:20:00Z",
        },
        "registered_at": "2026-08-13T04:30:00Z",
    }
    binding_registry = {
        "schema_version": "1.0.0",
        "artifact_scope": "trusted_calibration_runtime_profile_bindings",
        "registry_id": "trusted-runtime-bindings",
        "registry_version": "runtime-bindings-v1",
        "source": {
            "authority_id": "release-owner",
            "source_sha256": "E" * 64,
            "registered_at": "2026-08-13T04:40:00Z",
        },
        "registered_at": "2026-08-13T05:00:00Z",
        "canonicalization": "rallymate-canonical-json-v1",
        "entries": [entry],
    }
    return {
        "asset": asset,
        "ledger": ledger,
        "registry": registry,
        "indicator": indicator,
        "trusted": trusted,
        "model_versions": model_versions,
        "profile": profile,
        "evidence": evidence,
        "binding_registry": binding_registry,
    }


def _bind(fixture: dict) -> RuntimeProfileBoundProductionCalibration:
    return bind_trusted_production_calibration_to_runtime_profile(
        fixture["trusted"],
        binding_registry=fixture["binding_registry"],
        runtime_profile=fixture["profile"],
        view_evidence=fixture["evidence"],
        feasibility_registry=fixture["registry"],
    )


class RuntimeProfileBindingTests(unittest.TestCase):
    def test_exact_binding_is_runtime_only_and_traceable(self) -> None:
        fixture = _runtime_fixture()
        bound = _bind(fixture)
        authorization = bound.authorization
        self.assertTrue(authorization["runtime_profile_binding_verified"])
        self.assertEqual(
            authorization["runtime_profile_pose"]["model_sha256"], "A" * 64
        )
        self.assertEqual(
            authorization["runtime_profile_pose"]["native_keypoint_count"], 133
        )
        self.assertEqual(
            authorization["runtime_profile_pipeline"]["event_detector"],
            "pose-motion-bout-v0.3.0",
        )
        self.assertEqual(
            authorization["runtime_profile_view"]["view_group"], "fixed-side"
        )
        self.assertEqual(authorization["runtime_view_video_id"], "video-001")
        self.assertEqual(len(authorization["runtime_view_evidence_sha256"]), 64)
        self.assertEqual(
            runtime_profile_versions(bound)["runtime_binding_id"],
            "runtime-binding-fs01-m02-v1",
        )
        with self.assertRaises(TypeError):
            json.dumps(bound)

        batch = bind_production_calibrations_for_runtime(
            {"FS01-M02": fixture["trusted"]},
            binding_registry=fixture["binding_registry"],
            view_evidence=fixture["evidence"],
            feasibility_registry=fixture["registry"],
            model_versions=fixture["model_versions"],
        )
        self.assertIsInstance(
            batch["FS01-M02"], RuntimeProfileBoundProductionCalibration
        )

        scoring_feature = {
            "feature_name": "feature_a",
            "feature_version": "feature-a-v1",
            "value": 20.0,
            "unit": "body",
            "confidence": 0.9,
            "valid": True,
            "reason": "valid",
            "source_frames": [1],
        }
        video_evidence = {
            "video_id": fixture["evidence"]["video_id"],
            "video_sha256": fixture["evidence"]["video_sha256"],
            "event_id": "event-001",
            "person_track_id": 1,
            "source_frames": [1],
        }
        scored = score_indicator(
            indicator_id="FS01-M02",
            features=[scoring_feature],
            calibration=bound,
            model_versions=fixture["model_versions"],
            evidence=[video_evidence],
            feasibility_level="F4",
        )
        self.assertEqual(scored["status"], "scored")
        self.assertEqual(scored["grade"], "C")

        for evidence in (
            [{"video_id": "video-001"}],
            [{"video_id": "video-001", "video_sha256": "9" * 64}],
            [
                video_evidence,
                {
                    "evidence_type": "auxiliary_provenance",
                    "event_id": "event-001",
                    "source_sha256": "D" * 64,
                },
            ],
            [
                video_evidence,
                {
                    "evidence_type": "auxiliary_provenance",
                    "video_id": fixture["evidence"]["video_id"],
                    "video_sha256": "9" * 64,
                    "event_id": "event-001",
                    "source_sha256": "D" * 64,
                },
            ],
        ):
            with self.subTest(evidence=evidence):
                blocked = score_indicator(
                    indicator_id="FS01-M02",
                    features=[scoring_feature],
                    calibration=bound,
                    model_versions=fixture["model_versions"],
                    evidence=evidence,
                    feasibility_level="F4",
                )
                self.assertEqual(blocked["status"], "calibration_required")
                self.assertIsNone(blocked["grade"])

    def test_pose_backend_model_sha_and_native_topology_are_exact(self) -> None:
        mutations = (
            ("backend", "yolo"),
            ("model_sha256", "9" * 64),
            ("native_keypoint_format", "coco17"),
            ("native_keypoint_count", 17),
        )
        for field, value in mutations:
            fixture = _runtime_fixture()
            fixture["profile"]["pose"][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(
                RuntimeProfileBindingError,
                "no unique active exact runtime-profile binding",
            ):
                _bind(fixture)

    def test_event_primary_quality_and_feature_contract_are_registry_exact(self) -> None:
        for field in (
            "event_detector",
            "primary_player",
            "quality_policy",
            "feature_contract",
        ):
            fixture = _runtime_fixture()
            fixture["profile"]["pipeline"][field] = "wrong-version"
            with self.subTest(field=field), self.assertRaisesRegex(
                RuntimeProfileBindingError,
                "pipeline versions do not exactly match F4 registry",
            ):
                _bind(fixture)

    def test_profile_builder_rejects_runtime_pipeline_drift(self) -> None:
        for field in (
            "event_contract",
            "event",
            "phase_contract",
            "primary_player",
            "quality_policy",
        ):
            fixture = _runtime_fixture()
            versions = copy.deepcopy(fixture["model_versions"])
            versions[field] = "stale-runtime"
            with self.subTest(field=field), self.assertRaisesRegex(
                RuntimeProfileBindingError, "does not match F4 registry"
            ):
                build_runtime_scoring_profile(
                    model_versions=versions,
                    indicator=fixture["indicator"],
                    profile_id="drift",
                    view_profile_id="fixed-side-full-body-v1",
                    view_group="fixed-side",
                    verification_protocol_version="view-review-v1",
                )

        for field in ("indicator_definition", "feature_contract"):
            fixture = _runtime_fixture()
            versions = copy.deepcopy(fixture["model_versions"])
            versions[field]["FS01-M02"] = "stale-runtime"
            with self.subTest(field=field), self.assertRaisesRegex(
                RuntimeProfileBindingError, "does not match F4 registry"
            ):
                build_runtime_scoring_profile(
                    model_versions=versions,
                    indicator=fixture["indicator"],
                    profile_id="drift",
                    view_profile_id="fixed-side-full-body-v1",
                    view_group="fixed-side",
                    verification_protocol_version="view-review-v1",
                )

    def test_view_group_protocol_and_all_constraints_are_mandatory(self) -> None:
        mutations = (
            ("view_profile_id", "fixed-rear-v1"),
            ("view_group", "fixed-rear"),
            ("verification_protocol_version", "unreviewed-v2"),
        )
        for field, value in mutations:
            fixture = _runtime_fixture()
            fixture["evidence"][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(
                RuntimeProfileBindingError,
                f"view evidence {field} does not match",
            ):
                _bind(fixture)
        fixture = _runtime_fixture()
        fixture["evidence"]["satisfied_constraints"] = []
        with self.assertRaisesRegex(
            RuntimeProfileBindingError, "satisfied_constraints must not be empty"
        ):
            _bind(fixture)
        fixture = _runtime_fixture()
        fixture["evidence"]["satisfied_constraints"].append(
            "additional_reviewed_capture_constraint"
        )
        self.assertIsInstance(_bind(fixture), RuntimeProfileBoundProductionCalibration)
        fixture = _runtime_fixture()
        fixture["evidence"]["status"] = "pending"
        with self.assertRaisesRegex(RuntimeProfileBindingError, "explicitly accepted"):
            _bind(fixture)

    def test_asset_registry_ledger_and_active_entry_identity_are_exact(self) -> None:
        mutations = (
            ("calibration_asset_sha256", "1" * 64),
            ("calibration_version", "other-calibration"),
        )
        for field, value in mutations:
            fixture = _runtime_fixture()
            fixture["binding_registry"]["entries"][0][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(
                RuntimeProfileBindingError,
                "no unique active exact runtime-profile binding",
            ):
                _bind(fixture)
        fixture = _runtime_fixture()
        fixture["binding_registry"]["entries"][0]["trusted_promotion"][
            "entry_id"
        ] = "other-entry"
        with self.assertRaisesRegex(
            RuntimeProfileBindingError,
            "no unique active exact runtime-profile binding",
        ):
            _bind(fixture)
        fixture = _runtime_fixture()
        fixture["binding_registry"]["entries"][0]["status"] = "revoked"
        with self.assertRaisesRegex(
            RuntimeProfileBindingError,
            "no unique active exact runtime-profile binding",
        ):
            _bind(fixture)

    def test_duplicate_active_exact_binding_is_rejected(self) -> None:
        fixture = _runtime_fixture()
        duplicate = copy.deepcopy(fixture["binding_registry"]["entries"][0])
        duplicate["binding_id"] = "duplicate-binding"
        fixture["binding_registry"]["entries"].append(duplicate)
        with self.assertRaisesRegex(
            RuntimeProfileBindingError,
            "no unique active exact runtime-profile binding",
        ):
            _bind(fixture)

    def test_runtime_profile_and_view_contract_schemas_are_machine_readable(self) -> None:
        fixture = _runtime_fixture()
        validate_runtime_scoring_profile(fixture["profile"])
        validate_runtime_view_evidence(fixture["evidence"])
        validate_trusted_runtime_binding_registry(fixture["binding_registry"])
        for name in (
            "runtime-scoring-profile.schema.json",
            "runtime-view-evidence.schema.json",
            "trusted-runtime-profile-bindings.schema.json",
        ):
            schema = json.loads((ROOT / "contracts" / name).read_text(encoding="utf-8"))
            self.assertEqual(
                schema["$schema"],
                "https://json-schema.org/draft/2020-12/schema",
            )
            if Draft202012Validator is not None:
                Draft202012Validator.check_schema(schema)

    def test_unknown_fields_and_tampered_profile_hash_fail_closed(self) -> None:
        fixture = _runtime_fixture()
        fixture["profile"]["pose"]["unreviewed_backend_option"] = True
        with self.assertRaisesRegex(RuntimeProfileBindingError, "unsupported fields"):
            _bind(fixture)
        fixture = _runtime_fixture()
        fixture["binding_registry"]["entries"][0][
            "runtime_profile_sha256"
        ] = "0" * 64
        with self.assertRaisesRegex(
            RuntimeProfileBindingError, "does not match runtime_profile"
        ):
            _bind(fixture)


if __name__ == "__main__":
    unittest.main()
