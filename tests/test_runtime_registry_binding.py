from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from rallymate_scoring.calibration_fitting import canonical_sha256
from rallymate_scoring.calibration_promotion import (
    CalibrationPromotionError,
    authorize_production_asset_with_trusted_ledger,
    bind_trusted_production_calibration_to_registry,
    build_trusted_promotion_ledger,
    promote_calibration_candidate,
)
from rallymate_scoring.scoring import score_indicator
from rallymate_vision.contracts import ModelConfig, PipelineRequest, ScoringConfig
from rallymate_vision.pipeline import run_pipeline
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


def _promoted_fixture() -> tuple[dict, dict, dict]:
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
        ledger_id="operator-ledger",
        ledger_version="v1",
        authority_id="release-owner",
        registered_at="2026-08-13T04:00:00Z",
        verified_truth_authorizations={
            "FS01-M02": _verified_truth_authorization()
        },
    )
    return asset, ledger, registry


def _features() -> list[dict]:
    return [
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
    ]


class RuntimeRegistryBindingTests(unittest.TestCase):
    def test_ledger_authorization_exposes_promoted_registry_identity(self) -> None:
        asset, ledger, registry = _promoted_fixture()
        trusted = authorize_production_asset_with_trusted_ledger(asset, ledger)
        authorization = trusted.authorization
        self.assertEqual(
            authorization["promoted_registry_version"], registry["registry_version"]
        )
        self.assertEqual(
            authorization["promoted_registry_content_sha256"],
            canonical_sha256(registry),
        )
        self.assertEqual(
            authorization["promoted_registry_indicator_id"], "FS01-M02"
        )
        self.assertEqual(
            authorization["promoted_registry_indicator_level"], "F4"
        )
        self.assertNotIn("runtime_registry_binding_verified", authorization)

    def test_equal_version_but_changed_content_is_rejected(self) -> None:
        asset, ledger, registry = _promoted_fixture()
        changed = copy.deepcopy(registry)
        changed["updated_at"] = "2026-08-13T05:00:00Z"
        trusted = authorize_production_asset_with_trusted_ledger(asset, ledger)
        with self.assertRaisesRegex(
            CalibrationPromotionError, "content does not match promoted registry"
        ):
            bind_trusted_production_calibration_to_registry(trusted, changed)

    def test_different_registry_version_is_rejected(self) -> None:
        asset, ledger, registry = _promoted_fixture()
        changed = copy.deepcopy(registry)
        changed["registry_version"] = "registry-v2"
        trusted = authorize_production_asset_with_trusted_ledger(asset, ledger)
        with self.assertRaisesRegex(
            CalibrationPromotionError, "version does not match promoted registry"
        ):
            bind_trusted_production_calibration_to_registry(trusted, changed)

    def test_current_non_f4_indicator_is_rejected(self) -> None:
        asset, ledger, registry = _promoted_fixture()
        changed = copy.deepcopy(registry)
        changed["indicators"][0]["feasibility_level"] = "F3"
        trusted = authorize_production_asset_with_trusted_ledger(asset, ledger)
        with self.assertRaisesRegex(CalibrationPromotionError, "is not F4"):
            bind_trusted_production_calibration_to_registry(trusted, changed)

    def test_ledger_authorized_but_registry_unbound_asset_cannot_score(self) -> None:
        asset, ledger, _ = _promoted_fixture()
        trusted = authorize_production_asset_with_trusted_ledger(asset, ledger)
        result = score_indicator(
            indicator_id="FS01-M02",
            features=_features(),
            calibration=trusted,
            model_versions={"pose": "test"},
            evidence=[],
            feasibility_level="F4",
        )
        self.assertEqual(result["status"], "calibration_required")
        self.assertIn(
            "runtime_feasibility_registry_binding_required",
            result["reason_codes"],
        )

    def test_trusted_asset_requires_exact_promoted_feature_contract(self) -> None:
        asset, ledger, registry = _promoted_fixture()
        trusted = bind_trusted_production_calibration_to_registry(
            authorize_production_asset_with_trusted_ledger(asset, ledger),
            registry,
        )
        result = score_indicator(
            indicator_id="FS01-M02",
            features=[
                *_features(),
                {
                    "feature_name": "unpromoted_extra_feature",
                    "feature_version": "test-only",
                    "value": 1.0,
                    "unit": "body",
                    "confidence": 1.0,
                    "valid": True,
                    "reason": "valid",
                    "source_frames": [1],
                },
            ],
            calibration=trusted,
            model_versions={"pose": "test"},
            evidence=[],
            feasibility_level="F4",
        )
        self.assertEqual(result["status"], "unavailable")
        self.assertIsNone(result["grade"])
        self.assertIn("required_feature_contract_mismatch", result["reason_codes"])

    def test_pipeline_rejects_unregistered_calibration_registry_before_creating_run_dir(self) -> None:
        asset, ledger, registry = _promoted_fixture()
        changed = copy.deepcopy(registry)
        changed["updated_at"] = "2026-08-13T05:00:00Z"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            video = root / "video.mp4"
            calibration_path = root / "calibration.json"
            ledger_path = root / "ledger.json"
            registry_path = root / "registry.json"
            video.write_bytes(b"registry-preflight-fails-before-video-decode")
            calibration_path.write_text(json.dumps(asset), encoding="utf-8")
            ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
            registry_path.write_text(json.dumps(changed), encoding="utf-8")
            output_dir = root / "run"
            request = PipelineRequest(
                job_id="binding-fail",
                video_path=video,
                output_dir=output_dir,
                models=ModelConfig(
                    detect=root / "detect.pt", pose=root / "pose.pt"
                ),
                scoring=ScoringConfig(
                    feasibility_registry=registry_path,
                    calibration_assets=[calibration_path],
                ),
            )
            with self.assertRaisesRegex(ValueError, "not authorized"):
                run_pipeline(
                    request, trusted_promotion_ledger_path=ledger_path
                )
            self.assertFalse(output_dir.exists())


if __name__ == "__main__":
    unittest.main()
