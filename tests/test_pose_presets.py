from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from rallymate_service.config import load_settings
from rallymate_vision.contracts import load_request
from rallymate_vision.pose.presets import resolve_pose_deployment_preset


class PoseDeploymentPresetTests(unittest.TestCase):
    def test_online_and_analysis_presets_resolve_expected_models(self) -> None:
        root = Path(__file__).resolve().parents[1]
        online = resolve_pose_deployment_preset("rtmpose-m-halpe26-online", root)
        analysis = resolve_pose_deployment_preset("rtmpose-m-halpe26-analysis", root)
        self.assertEqual(online.pose_profile, "realtime")
        self.assertEqual(online.input_size_hw, (256, 192))
        self.assertIn("256x192", online.model_path.name)
        self.assertEqual(analysis.pose_profile, "analysis")
        self.assertEqual(analysis.input_size_hw, (384, 288))
        self.assertIn("384x288", analysis.model_path.name)
        shadow = resolve_pose_deployment_preset(
            "rtmpose-l-halpe26-analysis-shadow", root
        )
        self.assertEqual(shadow.pose_profile, "analysis")
        self.assertEqual(shadow.input_size_hw, (384, 288))
        self.assertEqual(shadow.model_path.name, "rtmpose-l_halpe26_384x288.pth")
        self.assertEqual(
            shadow.config_path.name,
            "rtmpose-l_8xb512-700e_body8-halpe26-384x288.py",
        )
        self.assertEqual(
            shadow.role,
            "offline_shadow_analysis_candidate_not_accuracy_validated_not_F4",
        )
        self.assertEqual(
            shadow.promotion_status,
            "offline_shadow_only_not_accuracy_validated_not_F4_promoted",
        )
        self.assertTrue(shadow.model_path.is_file())
        self.assertTrue(shadow.config_path.is_file())
        wholebody = resolve_pose_deployment_preset(
            "rtmpose-m-wholebody133-analysis", root
        )
        self.assertEqual(wholebody.pose_profile, "analysis")
        self.assertEqual(wholebody.native_keypoint_format, "coco_wholebody133")
        self.assertEqual(wholebody.input_size_hw, (256, 192))

    def test_service_preset_overrides_individual_pose_environment_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {
                "RALLYMATE_DATA_ROOT": directory,
                "RALLYMATE_POSE_PRESET": "rtmpose-m-halpe26-online",
                "RALLYMATE_POSE_BACKEND": "yolo",
                "RALLYMATE_POSE_PROFILE": "analysis",
            },
            clear=False,
        ):
            settings = load_settings()
        self.assertEqual(settings.pose_preset, "rtmpose-m-halpe26-online")
        self.assertEqual(settings.pose_backend, "rtmpose")
        self.assertEqual(settings.pose_profile, "realtime")
        self.assertIn("256x192", settings.pose_model.name)

    def test_shadow_preset_is_explicit_and_does_not_change_the_default(self) -> None:
        root = Path(__file__).resolve().parents[1]
        registry = json.loads(
            (root / "models" / "rtmpose" / "deployment-presets.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(registry["default_preset"], "rtmpose-m-halpe26-online")
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {
                "RALLYMATE_DATA_ROOT": directory,
                "RALLYMATE_POSE_PRESET": "rtmpose-l-halpe26-analysis-shadow",
            },
            clear=True,
        ):
            settings = load_settings()
        self.assertEqual(
            settings.pose_preset, "rtmpose-l-halpe26-analysis-shadow"
        )
        self.assertEqual(settings.pose_backend, "rtmpose")
        self.assertEqual(settings.pose_profile, "analysis")
        self.assertEqual(settings.pose_native_keypoint_format, "halpe26")
        self.assertEqual(settings.pose_model.name, "rtmpose-l_halpe26_384x288.pth")

    def test_shadow_preset_parses_in_request_contract(self) -> None:
        root = Path(__file__).resolve().parents[1]
        shadow = resolve_pose_deployment_preset(
            "rtmpose-l-halpe26-analysis-shadow", root
        )
        with tempfile.TemporaryDirectory() as directory:
            request_path = Path(directory) / "request.json"
            request_path.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0.0",
                        "job_id": "shadow-contract-test",
                        "source": {"video_path": "video.mp4"},
                        "output": {"directory": "output"},
                        "models": {
                            "detect": str(root / "models" / "yolo26n.pt"),
                            "pose": str(shadow.model_path),
                            "pose_backend": shadow.pose_backend,
                            "pose_runtime": shadow.pose_runtime,
                            "pose_profile": shadow.pose_profile,
                            "pose_config": str(shadow.config_path),
                            "pose_preset": shadow.preset_id,
                            "pose_native_keypoint_format": (
                                shadow.native_keypoint_format
                            ),
                        },
                    }
                ),
                encoding="utf-8",
            )
            request = load_request(request_path)
        self.assertEqual(
            request.models.pose_preset, "rtmpose-l-halpe26-analysis-shadow"
        )
        self.assertEqual(request.models.pose_backend, "rtmpose")
        self.assertEqual(request.models.pose_profile, "analysis")

    def test_m71_candidate_registry_remains_an_immutable_historical_source(
        self,
    ) -> None:
        root = Path(__file__).resolve().parents[1]
        registry_path = root / "models" / "rtmpose" / "model-candidates.json"
        registry_raw = registry_path.read_bytes()
        registry = json.loads(registry_raw.decode("utf-8"))
        candidate = next(
            item
            for item in registry["candidates"]
            if item["candidate_id"] == "rtmpose-l-halpe26-384x288"
        )
        self.assertEqual(
            hashlib.sha256(registry_raw).hexdigest().upper(),
            "8E53F978AC0D178056515629FF7AEF0C2486D1400451B24A943574EA4F152DC0",
        )
        self.assertEqual(
            registry["registry_version"],
            "rtmpose-halpe26-candidates-2026-08-22.2",
        )
        self.assertEqual(
            candidate["official_metrics"],
            {"pck_at_0_1": 95.60, "auc": 74.40},
        )

    def test_shadow_preset_is_exposed_by_explicit_startup_arguments(self) -> None:
        root = Path(__file__).resolve().parents[1]
        for relative_path in (
            "scripts/run_api.ps1",
            "scripts/run_local_inference.ps1",
            "scripts/run_worker.ps1",
            "scripts/run_rtmpose_service.ps1",
            "scripts/run_rtmpose_worker.ps1",
        ):
            with self.subTest(script=relative_path):
                source = (root / relative_path).read_text(encoding="utf-8")
                self.assertIn("rtmpose-l-halpe26-analysis-shadow", source)
        for relative_path in (
            "scripts/run_rtmpose_service.ps1",
            "scripts/run_rtmpose_worker.ps1",
        ):
            with self.subTest(profile=relative_path):
                source = (root / relative_path).read_text(encoding="utf-8")
                self.assertIn('"Shadow"', source)

    def test_split_process_scripts_share_the_same_pose_preset_contract(self) -> None:
        root = Path(__file__).resolve().parents[1]
        api_source = (root / "scripts" / "run_api.ps1").read_text(
            encoding="utf-8"
        )
        worker_source = (root / "scripts" / "run_worker.ps1").read_text(
            encoding="utf-8"
        )
        expected_presets = (
            "rtmpose-m-halpe26-online",
            "rtmpose-m-halpe26-analysis",
            "rtmpose-l-halpe26-analysis-shadow",
            "rtmpose-m-wholebody133-analysis",
            "yolo-baseline",
        )
        for preset in expected_presets:
            with self.subTest(preset=preset):
                self.assertIn(preset, api_source)
                self.assertIn(preset, worker_source)
        for source in (api_source, worker_source):
            self.assertIn(
                '[string]$PosePreset = "rtmpose-m-halpe26-online"', source
            )
            self.assertIn('$env:RALLYMATE_POSE_PRESET = $PosePreset', source)
        self.assertIn(
            "API snapshots this preset into every generated request", api_source
        )

        rtmpose_service_source = (
            root / "scripts" / "run_rtmpose_service.ps1"
        ).read_text(encoding="utf-8")
        rtmpose_worker_source = (
            root / "scripts" / "run_rtmpose_worker.ps1"
        ).read_text(encoding="utf-8")
        expected_profiles = {
            "Online": "rtmpose-m-halpe26-online",
            "Analysis": "rtmpose-m-halpe26-analysis",
            "Shadow": "rtmpose-l-halpe26-analysis-shadow",
            "WholeBody": "rtmpose-m-wholebody133-analysis",
        }
        for profile, preset in expected_profiles.items():
            mapping = f'"{profile}" {{ "{preset}" }}'
            with self.subTest(profile=profile):
                self.assertIn(mapping, rtmpose_service_source)
                self.assertIn(mapping, rtmpose_worker_source)

    def test_yolo_baseline_remains_an_explicit_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {
                "RALLYMATE_DATA_ROOT": directory,
                "RALLYMATE_POSE_PRESET": "yolo-baseline",
            },
            clear=True,
        ):
            settings = load_settings()
        self.assertEqual(settings.pose_preset, "yolo-baseline")
        self.assertEqual(settings.pose_backend, "yolo")
        self.assertEqual(settings.pose_native_keypoint_format, "coco17")

    def test_generic_requests_use_halpe26_and_current_scoring_registry(self) -> None:
        root = Path(__file__).resolve().parents[1]
        for name in (
            "request.json",
            "request-quick.json",
            "request-serve-full.json",
            "request-match-auto.json",
            "request-match-auto-quick.json",
            "request-match-manual.json",
        ):
            with self.subTest(name=name):
                request = load_request(root / "examples" / name)
                self.assertEqual(request.models.pose_backend, "rtmpose")
                self.assertEqual(
                    request.models.pose_preset, "rtmpose-m-halpe26-online"
                )
                self.assertEqual(
                    request.models.pose_native_keypoint_format, "halpe26"
                )
                self.assertEqual(
                    request.scoring.feasibility_registry,
                    root / "metric-feasibility-pose-wave-v2.json",
                )

    def test_preset_config_falls_back_to_installed_mmpose_package(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry_dir = root / "models" / "rtmpose"
            registry_dir.mkdir(parents=True)
            model = registry_dir / "model.pth"
            model.write_bytes(b"model")
            package = root / "installed" / "mmpose"
            config = package / ".mim" / "configs" / "model.py"
            config.parent.mkdir(parents=True)
            config.write_text("model = {}\n", encoding="utf-8")
            (registry_dir / "deployment-presets.json").write_text(
                json.dumps(
                    {
                        "registry_version": "test-preset-registry-v1",
                        "default_preset": "rtmpose-test",
                        "presets": [
                            {
                                "preset_id": "rtmpose-test",
                                "role": "test",
                                "pose_backend": "rtmpose",
                                "pose_runtime": "pytorch",
                                "pose_profile": "realtime",
                                "model_relative_path": "models/rtmpose/model.pth",
                                "config_runtime_relative_path": "missing/config.py",
                                "config_package_relative_path": ".mim/configs/model.py",
                                "native_keypoint_format": "halpe26",
                                "input_size_hw": [256, 192],
                                "promotion_status": "test",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            spec = SimpleNamespace(submodule_search_locations=[str(package)])
            with patch(
                "rallymate_vision.pose.presets.importlib.util.find_spec",
                return_value=spec,
            ):
                preset = resolve_pose_deployment_preset("rtmpose-test", root)
            self.assertEqual(preset.config_path, config.resolve())

    def test_service_defaults_to_halpe26_and_keeps_wholebody_explicit(self) -> None:
        root = Path(__file__).resolve().parents[1]
        registry = __import__("json").loads(
            (root / "models" / "rtmpose" / "deployment-presets.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            registry["default_preset"], "rtmpose-m-halpe26-online"
        )
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"RALLYMATE_DATA_ROOT": directory},
            clear=True,
        ):
            default_settings = load_settings()
        self.assertEqual(
            default_settings.pose_preset, "rtmpose-m-halpe26-online"
        )
        self.assertEqual(default_settings.pose_backend, "rtmpose")
        self.assertEqual(default_settings.pose_native_keypoint_format, "halpe26")
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {
                "RALLYMATE_DATA_ROOT": directory,
                "RALLYMATE_POSE_PRESET": "rtmpose-m-wholebody133-analysis",
            },
            clear=False,
        ):
            settings = load_settings()
        self.assertEqual(settings.pose_backend, "rtmpose")
        self.assertEqual(settings.pose_profile, "analysis")
        self.assertEqual(
            settings.pose_native_keypoint_format,
            "coco_wholebody133",
        )


if __name__ == "__main__":
    unittest.main()
