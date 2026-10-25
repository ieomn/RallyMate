from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from rallymate_vision.contracts import ModelConfig, PipelineRequest, ScoringConfig
from rallymate_vision.pipeline import (
    DEFAULT_SCORING_FEASIBILITY_REGISTRY,
    load_pipeline_calibrations,
    load_runtime_view_evidence,
    load_trusted_runtime_binding_registry,
    resolve_scoring_feasibility_registry,
    run_pipeline,
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

    def test_request_can_pin_any_schema_valid_registry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            explicit = ROOT / DEFAULT_SCORING_FEASIBILITY_REGISTRY
            resolved = resolve_scoring_feasibility_registry(
                self._request(root, explicit),
                ROOT,
            )
            self.assertEqual(resolved, explicit.resolve())

            for supplied in (
                ROOT / "metric-feasibility.json",
                root / "same-bytes-different-path.json",
            ):
                if not supplied.exists():
                    supplied.write_bytes(explicit.read_bytes())
                with self.subTest(supplied=supplied):
                    self.assertEqual(resolve_scoring_feasibility_registry(self._request(root, supplied), ROOT), supplied.resolve())

    def test_invalid_registry_is_rejected_before_run_directory_creation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = root / "invalid.json"
            registry.write_text('{}', encoding='utf-8')
            request = self._request(root, registry)
            request.video_path.write_bytes(b"registry-rejected-before-decode")
            with self.assertRaisesRegex(ValueError, "schema_version"):
                run_pipeline(request)
            self.assertFalse(request.output_dir.exists())

    def _explicit_rule(self):
        # Constructed mathematics for execution tests, not measured accuracy.
        return {"indicator_id": "FS01-M02", "backend": "threshold_rule",
                "threshold_version": "fixture-v1", "primary_feature": "feature_a",
                "primary_feature_version": "feature-a-v1", "unit": "body",
                "direction": "higher_is_better", "thresholds": [5., 15., 25., 35.]}

    def test_explicit_mathematics_load_without_release_or_truth_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            asset = Path(directory) / "rule.json"
            rule = self._explicit_rule()
            asset.write_text(json.dumps(rule), encoding="utf-8")
            calibrations, provenance = load_pipeline_calibrations([asset])
            self.assertEqual(calibrations, {"FS01-M02": rule})
            self.assertEqual(len(provenance[0]["sha256"]), 64)
            self.assertIsNone(provenance[0]["independent_test_status"])
            self.assertIn("not_accuracy", provenance[0]["validation_scope"])
            # A legacy ledger path is metadata, not a required authorization.
            self.assertEqual(load_pipeline_calibrations(
                [asset], trusted_ledger_path=Path(directory) / "absent-ledger.json",
                feasibility_registry={"version": "F0-fixture"})[0], calibrations)

    def test_release_labels_do_not_change_or_fill_explicit_thresholds(self):
        with tempfile.TemporaryDirectory() as directory:
            asset = Path(directory) / "rule.json"
            rule = self._explicit_rule()
            rule.update(artifact_scope="test_only", independent_test={"status": "test_only"})
            asset.write_text(json.dumps(rule), encoding="utf-8")
            calibrations, provenance = load_pipeline_calibrations([asset])
            self.assertEqual(calibrations["FS01-M02"]["thresholds"], rule["thresholds"])
            self.assertEqual(provenance[0]["independent_test_status"], "test_only")
            for bad in ([], [5., 15., 15., 35.], [5., 15., float("nan"), 35.]):
                invalid = dict(rule, thresholds=bad)
                asset.write_text(json.dumps(invalid), encoding="utf-8")
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    load_pipeline_calibrations([asset])
            asset.write_text(json.dumps(rule), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "duplicate"):
                load_pipeline_calibrations([asset, asset])

    def test_optional_runtime_annotations_need_identity_but_not_human_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "view.json"
            evidence = {"video_id": "actual-video", "video_sha256": "a" * 64}
            path.write_text(json.dumps(evidence), encoding="utf-8")
            self.assertEqual(load_runtime_view_evidence(path), evidence)
            self.assertEqual(load_trusted_runtime_binding_registry(path), evidence)
            for bad in (dict(evidence, video_sha256="bad"), dict(evidence, video_id="")):
                path.write_text(json.dumps(bad), encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_runtime_view_evidence(path)

    def test_invalid_calibration_is_rejected_before_run_directory_creation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = self._request(root)
            request.video_path.write_bytes(b"not-decoded-because-calibration-fails-first")
            unsafe = root / "unsafe-calibration.json"
            unsafe.write_text("{}", encoding="utf-8")
            request.scoring.calibration_assets = [unsafe]
            with self.assertRaisesRegex(ValueError, "unsupported scoring backend"):
                run_pipeline(request)
            self.assertFalse(request.output_dir.exists())


if __name__ == "__main__":
    unittest.main()
