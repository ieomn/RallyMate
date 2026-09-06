from __future__ import annotations

import json
import copy
import tempfile
import unittest
from pathlib import Path

from rallymate_vision.contracts import ModelConfig, PipelineRequest, ScoringConfig
from rallymate_vision.pipeline import (
    DEFAULT_SCORING_FEASIBILITY_REGISTRY,
    load_pipeline_calibrations,
    resolve_scoring_feasibility_registry,
    run_pipeline,
)
from rallymate_scoring.calibration_promotion import (
    build_trusted_promotion_ledger,
    promote_calibration_candidate,
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


class PipelineScoringRegistryTests(unittest.TestCase):
    def _request(self, root: Path, registry: Path | None = None) -> PipelineRequest:
        return PipelineRequest(
            job_id="job",
            video_path=root / "video.mp4",
            output_dir=root / "out",
            models=ModelConfig(detect=root / "detect.pt", pose=root / "pose.pt"),
            scoring=ScoringConfig(feasibility_registry=registry),
        )

    def test_pipeline_defaults_to_current_pose_wave_v2_registry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            resolved = resolve_scoring_feasibility_registry(
                self._request(root),
                ROOT,
            )
            self.assertEqual(
                resolved,
                (ROOT / DEFAULT_SCORING_FEASIBILITY_REGISTRY).resolve(),
            )

    def test_request_can_only_pin_the_lifecycle_authorized_registry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            explicit = ROOT / DEFAULT_SCORING_FEASIBILITY_REGISTRY
            resolved = resolve_scoring_feasibility_registry(
                self._request(root, explicit),
                ROOT,
            )
            self.assertEqual(resolved, explicit.resolve())

            for rejected in (
                ROOT / "metric-feasibility.json",
                root / "same-bytes-different-path.json",
            ):
                if not rejected.exists():
                    rejected.write_bytes(explicit.read_bytes())
                with self.subTest(rejected=rejected), self.assertRaisesRegex(
                    ValueError, "not authorized"
                ):
                    resolve_scoring_feasibility_registry(
                        self._request(root, rejected),
                        ROOT,
                    )

    def test_historical_registry_is_rejected_before_run_directory_creation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = self._request(root, ROOT / "metric-feasibility.json")
            request.video_path.write_bytes(b"registry-rejected-before-decode")
            with self.assertRaisesRegex(ValueError, "not authorized"):
                run_pipeline(request)
            self.assertFalse(request.output_dir.exists())

    def test_pipeline_loads_only_ledger_authorized_production_calibration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            asset = root / "calibration.json"
            candidate = _candidate(synthetic=False)
            samples, requirements, independent_report = _production_replay_fixture(
                candidate
            )
            registry = _registry(level="F4")
            calibration, promotion_report = promote_calibration_candidate(
                candidate=candidate,
                independent_test_report=independent_report,
                independent_test_protocol=_independent_test_protocol(candidate),
                independent_test_samples=samples,
                indicator_requirements=requirements,
                decision=_decision(candidate, independent_report, registry),
                feasibility_registry=registry,
                promoted_at="2026-08-13T03:00:00Z",
                maturity_evidence=_maturity_evidence(
                    candidate, independent_report, registry
                ),
                verified_truth_authorization=_verified_truth_authorization(),
            )
            ledger = build_trusted_promotion_ledger(
                promotions=[(calibration, promotion_report)],
                ledger_id="operator-ledger",
                ledger_version="v1",
                authority_id="release-owner",
                registered_at="2026-08-13T04:00:00Z",
                verified_truth_authorizations={
                    "FS01-M02": _verified_truth_authorization()
                },
            )
            ledger_path = root / "trusted-ledger.json"
            asset.write_text(json.dumps(calibration), encoding="utf-8")
            ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
            calibrations, provenance = load_pipeline_calibrations(
                [asset],
                trusted_ledger_path=ledger_path,
                feasibility_registry=registry,
            )
            self.assertEqual(set(calibrations), {"FS01-M02"})
            self.assertEqual(provenance[0]["independent_test_status"], "passed")
            self.assertEqual(len(provenance[0]["sha256"]), 64)
            self.assertEqual(
                provenance[0]["trusted_promotion_ledger_version"], "v1"
            )
            self.assertEqual(
                provenance[0]["runtime_registry_content_sha256"],
                provenance[0]["promoted_registry_content_sha256"],
            )
            self.assertEqual(
                provenance[0]["maturity_evidence_content_sha256"],
                calibration["promotion_lineage"]["maturity_evidence"][
                    "content_sha256"
                ],
            )
            scored = score_indicator(
                indicator_id="FS01-M02",
                features=[
                    {
                        "feature_name": "feature_a",
                        "feature_version": "feature-a-v1",
                        "value": 20.0,
                        "unit": "body",
                        "confidence": 0.9,
                        "valid": True,
                        "reason": "valid",
                        "source_frames": [1],
                    }
                ],
                calibration=calibrations["FS01-M02"],
                model_versions={
                    "pose": "test",
                    "event": "test",
                    "feature": "test",
                },
                evidence=[],
                feasibility_level="F4",
            )
            self.assertEqual(scored["status"], "calibration_required")
            self.assertIsNone(scored["grade"])
            self.assertIn(
                "runtime_profile_binding_required", scored["reason_codes"]
            )

            payload = json.loads(asset.read_text(encoding="utf-8"))
            payload["artifact_scope"] = "test_only"
            payload["independent_test"]["status"] = "test_only"
            payload["independent_test"]["approved_for_scoring"] = False
            asset.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "production-scoped"):
                load_pipeline_calibrations(
                    [asset],
                    trusted_ledger_path=ledger_path,
                    feasibility_registry=registry,
                )

    def test_handwritten_passed_asset_is_rejected_even_with_F4_lineage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate = _candidate(synthetic=False)
            samples, requirements, independent_report = _production_replay_fixture(
                candidate
            )
            registry = _registry(level="F4")
            calibration, promotion_report = promote_calibration_candidate(
                candidate=candidate,
                independent_test_report=independent_report,
                independent_test_protocol=_independent_test_protocol(candidate),
                independent_test_samples=samples,
                indicator_requirements=requirements,
                decision=_decision(candidate, independent_report, registry),
                feasibility_registry=registry,
                promoted_at="2026-08-13T03:00:00Z",
                maturity_evidence=_maturity_evidence(
                    candidate, independent_report, registry
                ),
                verified_truth_authorization=_verified_truth_authorization(),
            )
            ledger = build_trusted_promotion_ledger(
                promotions=[(calibration, promotion_report)],
                ledger_id="operator-ledger",
                ledger_version="v1",
                authority_id="release-owner",
                registered_at="2026-08-13T04:00:00Z",
                verified_truth_authorizations={
                    "FS01-M02": _verified_truth_authorization()
                },
            )
            ledger_path = root / "trusted-ledger.json"
            ledger_path.write_text(json.dumps(ledger), encoding="utf-8")

            no_lineage = copy.deepcopy(calibration)
            no_lineage.pop("promotion_lineage")
            fake_path = root / "handwritten-passed.json"
            fake_path.write_text(json.dumps(no_lineage), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "promotion_lineage must be an object"):
                load_pipeline_calibrations(
                    [fake_path], trusted_ledger_path=ledger_path
                )

            tampered = copy.deepcopy(calibration)
            tampered["thresholds"] = [6.0, 16.0, 26.0, 36.0]
            fake_path.write_text(json.dumps(tampered), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "asset_content_sha256"):
                load_pipeline_calibrations(
                    [fake_path], trusted_ledger_path=ledger_path
                )

            with self.assertRaisesRegex(ValueError, "operator-configured"):
                load_pipeline_calibrations([fake_path])

    def test_invalid_calibration_is_rejected_before_run_directory_creation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = self._request(root)
            request.video_path.write_bytes(b"not-decoded-because-calibration-fails-first")
            unsafe = root / "unsafe-calibration.json"
            unsafe.write_text("{}", encoding="utf-8")
            request.scoring.calibration_assets = [unsafe]
            with self.assertRaisesRegex(ValueError, "unsupported scoring calibration"):
                run_pipeline(request)
            self.assertFalse(request.output_dir.exists())


if __name__ == "__main__":
    unittest.main()
