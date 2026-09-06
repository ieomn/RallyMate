from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from rallymate_vision.contracts import ContractError, load_request
from rallymate_vision.pose.presets import resolve_pose_deployment_preset


class ContractTests(unittest.TestCase):
    def test_relative_paths_resolve_from_request(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request_path = root / "request.json"
            request_path.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0.0",
                        "job_id": "job",
                        "source": {"video_path": "input.mp4"},
                        "output": {"directory": "out"},
                        "models": {
                            "detect": "detect.pt",
                            "pose": "pose.pt",
                        },
                        "scoring": {
                            "feasibility_registry": "registry.json",
                            "calibration_assets": ["calibration-a.json"],
                            "reference_context": "target-directions.json",
                        },
                    }
                ),
                encoding="utf-8",
            )
            request = load_request(request_path)
            self.assertEqual(request.video_path, root / "input.mp4")
            self.assertEqual(request.output_dir, root / "out")
            self.assertEqual(request.models.detect, root / "detect.pt")
            self.assertEqual(request.models.pose_backend, "yolo")
            self.assertEqual(request.models.pose_runtime, "pytorch")
            self.assertEqual(request.models.pose_profile, "realtime")
            self.assertEqual(
                request.scoring.feasibility_registry,
                root / "registry.json",
            )
            self.assertEqual(
                request.scoring.calibration_assets,
                [root / "calibration-a.json"],
            )
            self.assertEqual(
                request.scoring.reference_context,
                root / "target-directions.json",
            )

    def test_duplicate_calibration_asset_paths_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            request_path = Path(directory) / "request.json"
            request_path.write_text(
                json.dumps(
                    {
                        "job_id": "job",
                        "source": {"video_path": "input.mp4"},
                        "output": {"directory": "out"},
                        "models": {"detect": "d.pt", "pose": "p.pt"},
                        "scoring": {
                            "calibration_assets": ["a.json", "a.json"]
                        },
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ContractError, "duplicates"):
                load_request(request_path)

    def test_scoring_must_be_an_object(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            request_path = Path(directory) / "request.json"
            request_path.write_text(
                json.dumps(
                    {
                        "job_id": "job",
                        "source": {"video_path": "input.mp4"},
                        "output": {"directory": "out"},
                        "models": {"detect": "d.pt", "pose": "p.pt"},
                        "scoring": "metric-feasibility.json",
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(ContractError):
                load_request(request_path)

    def test_job_request_cannot_select_registry_lifecycle_authority(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            request_path = Path(directory) / "request.json"
            request_path.write_text(
                json.dumps(
                    {
                        "job_id": "job",
                        "source": {"video_path": "input.mp4"},
                        "output": {"directory": "out"},
                        "models": {"detect": "d.pt", "pose": "p.pt"},
                        "scoring": {
                            "registry_lifecycle_manifest": "attacker-manifest.json"
                        },
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ContractError, "unsupported fields"):
                load_request(request_path)

    def test_pose_backend_configuration_is_validated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            request_path = Path(directory) / "request.json"
            request_path.write_text(
                json.dumps(
                    {
                        "job_id": "job",
                        "source": {"video_path": "input.mp4"},
                        "output": {"directory": "out"},
                        "models": {
                            "detect": "d.pt",
                            "pose": "p.onnx",
                            "pose_backend": "rtmpose",
                            "pose_runtime": "onnxruntime",
                            "pose_profile": "analysis",
                            "pose_config": "rtmpose.json",
                            "pose_native_keypoint_format": "coco_wholebody133",
                        },
                    }
                ),
                encoding="utf-8",
            )
            request = load_request(request_path)
            self.assertEqual(request.models.pose_backend, "rtmpose")
            self.assertEqual(request.models.pose_runtime, "onnxruntime")
            self.assertEqual(request.models.pose_profile, "analysis")
            self.assertEqual(request.models.pose_config, Path(directory) / "rtmpose.json")
            self.assertEqual(
                request.models.pose_native_keypoint_format,
                "coco_wholebody133",
            )

    def test_unknown_pose_deployment_preset_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            request_path = Path(directory) / "request.json"
            request_path.write_text(
                json.dumps(
                    {
                        "job_id": "job",
                        "source": {"video_path": "input.mp4"},
                        "output": {"directory": "out"},
                        "models": {
                            "detect": "d.pt",
                            "pose": "p.pt",
                            "pose_preset": "unregistered-preset",
                        },
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(ContractError):
                load_request(request_path)

    def test_pose_preset_rejects_every_mismatched_provenance_field(self) -> None:
        workspace = Path(__file__).resolve().parents[1]
        preset = resolve_pose_deployment_preset(
            "rtmpose-m-halpe26-online", workspace
        )
        valid_models = {
            "detect": str(workspace / "models" / "yolo26n.pt"),
            "pose": str(preset.model_path),
            "pose_backend": preset.pose_backend,
            "pose_runtime": preset.pose_runtime,
            "pose_profile": preset.pose_profile,
            "pose_config": str(preset.config_path),
            "pose_preset": preset.preset_id,
            "pose_native_keypoint_format": preset.native_keypoint_format,
        }
        mismatches = {
            "pose": str(workspace / "models" / "wrong-pose.pth"),
            "pose_backend": "yolo",
            "pose_runtime": "onnxruntime",
            "pose_profile": "analysis",
            "pose_config": str(workspace / "models" / "wrong-config.py"),
            "pose_native_keypoint_format": "coco17",
        }

        with tempfile.TemporaryDirectory() as directory:
            for field_name, wrong_value in mismatches.items():
                with self.subTest(field=field_name):
                    request_path = Path(directory) / f"{field_name}.json"
                    models = dict(valid_models)
                    models[field_name] = wrong_value
                    request_path.write_text(
                        json.dumps(
                            {
                                "schema_version": "1.0.0",
                                "job_id": f"mismatch-{field_name}",
                                "source": {"video_path": "input.mp4"},
                                "output": {"directory": "out"},
                                "models": models,
                            }
                        ),
                        encoding="utf-8",
                    )
                    with self.assertRaisesRegex(
                        ContractError, "does not match pose preset"
                    ):
                        load_request(request_path)

    def test_manual_court_requires_four_normalized_points(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            request_path = Path(directory) / "request.json"
            request_path.write_text(
                json.dumps(
                    {
                        "job_id": "job",
                        "source": {"video_path": "input.mp4"},
                        "output": {"directory": "out"},
                        "models": {"detect": "d.pt", "pose": "p.pt"},
                        "court": {
                            "mode": "manual",
                            "manual_polygon_normalized": [[0, 0], [1, 0]],
                        },
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(ContractError):
                load_request(request_path)

    def test_confidence_above_one_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            request_path = Path(directory) / "request.json"
            request_path.write_text(
                json.dumps(
                    {
                        "job_id": "job",
                        "source": {"video_path": "input.mp4"},
                        "output": {"directory": "out"},
                        "models": {
                            "detect": "d.pt",
                            "pose": "p.pt",
                            "detect_confidence": 1.2,
                        },
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(ContractError):
                load_request(request_path)

    def test_invalid_court_refresh_policy_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            request_path = Path(directory) / "request.json"
            request_path.write_text(
                json.dumps(
                    {
                        "job_id": "job",
                        "source": {"video_path": "input.mp4"},
                        "output": {"directory": "out"},
                        "models": {"detect": "d.pt", "pose": "p.pt"},
                        "court": {"refresh_policy": "every_frame_forever"},
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(ContractError):
                load_request(request_path)


if __name__ == "__main__":
    unittest.main()
