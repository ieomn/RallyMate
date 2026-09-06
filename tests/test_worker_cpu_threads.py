from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from rallymate_service.config import ServiceConfigError, ServiceSettings, load_settings
from rallymate_service import worker


class _FakeTorch:
    def __init__(self, *, intraop: int = 20, interop: int = 20) -> None:
        self.intraop = intraop
        self.interop = interop
        self.calls: list[tuple[str, int]] = []
        self.interop_error: Exception | None = None

    def get_num_threads(self) -> int:
        return self.intraop

    def set_num_threads(self, value: int) -> None:
        self.calls.append(("intraop", value))
        self.intraop = value

    def get_num_interop_threads(self) -> int:
        return self.interop

    def set_num_interop_threads(self, value: int) -> None:
        self.calls.append(("interop", value))
        if self.interop_error is not None:
            raise self.interop_error
        self.interop = value


class _FakeCv2:
    def __init__(self, *, threads: int = 20) -> None:
        self.threads = threads
        self.calls: list[int] = []

    def getNumThreads(self) -> int:
        return self.threads

    def setNumThreads(self, value: int) -> None:
        self.calls.append(value)
        self.threads = value


class WorkerCpuThreadTests(unittest.TestCase):
    def test_service_settings_default_and_environment_override(self) -> None:
        settings = ServiceSettings(
            data_root=Path("service"),
            database_path=Path("service/jobs.sqlite3"),
            detect_model=Path("detect.pt"),
            pose_model=Path("pose.pt"),
        )
        self.assertEqual(settings.cpu_threads, 4)

        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {
                "RALLYMATE_DATA_ROOT": directory,
                "RALLYMATE_CPU_THREADS": "7",
            },
            clear=True,
        ):
            loaded = load_settings()
        self.assertEqual(loaded.cpu_threads, 7)

    def test_invalid_environment_value_is_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {
                "RALLYMATE_DATA_ROOT": directory,
                "RALLYMATE_CPU_THREADS": "many",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(
                ServiceConfigError,
                "RALLYMATE_CPU_THREADS must be an integer >= 1.*many",
            ):
                load_settings()

        settings = ServiceSettings(
            data_root=Path("service"),
            database_path=Path("service/jobs.sqlite3"),
            detect_model=Path("detect.pt"),
            pose_model=Path("pose.pt"),
            cpu_threads=0,
        )
        with self.assertRaisesRegex(
            ServiceConfigError, "RALLYMATE_CPU_THREADS must be an integer >= 1"
        ):
            settings.validate_license()

    def test_configures_and_verifies_all_runtime_thread_pools(self) -> None:
        fake_torch = _FakeTorch()
        fake_cv2 = _FakeCv2()
        with patch.dict(sys.modules, {"torch": fake_torch, "cv2": fake_cv2}):
            configured = worker.configure_cpu_thread_pools(4)

        self.assertEqual(
            configured,
            {
                "torch_interop": 4,
                "torch_intraop": 4,
                "opencv": 4,
                "requested": 4,
            },
        )
        self.assertEqual(fake_torch.calls, [("interop", 4), ("intraop", 4)])
        self.assertEqual(fake_cv2.calls, [4])

    def test_same_limit_is_idempotent_for_torch_interop(self) -> None:
        fake_torch = _FakeTorch(intraop=4, interop=4)
        fake_torch.interop_error = RuntimeError("must not be called twice")
        fake_cv2 = _FakeCv2(threads=4)
        with patch.dict(sys.modules, {"torch": fake_torch, "cv2": fake_cv2}):
            configured = worker.configure_cpu_thread_pools(4)

        self.assertEqual(configured["torch_interop"], 4)
        self.assertEqual(fake_torch.calls, [])
        self.assertEqual(fake_cv2.calls, [])

    def test_pool_configuration_failure_names_pool_and_requested_value(self) -> None:
        fake_torch = _FakeTorch()
        fake_torch.interop_error = RuntimeError("parallel work already started")
        fake_cv2 = _FakeCv2()
        with patch.dict(sys.modules, {"torch": fake_torch, "cv2": fake_cv2}):
            with self.assertRaisesRegex(
                worker.CpuThreadConfigurationError,
                "Torch inter-op CPU threads to 4 before model loading.*current=20",
            ):
                worker.configure_cpu_thread_pools(4)

    def test_persistent_runner_limits_threads_before_device_and_model(self) -> None:
        events: list[str] = []
        settings = Mock()
        settings.validate_license.side_effect = lambda: events.append("validate")
        settings.cpu_threads = 4
        settings.device = "0"
        settings.detect_model = Path("detect.pt")
        settings.pose_model = Path("pose.pt")
        settings.pose_backend = "rtmpose"
        settings.pose_runtime = "pytorch"
        settings.pose_profile = "realtime"
        settings.pose_config = Path("config.py")
        settings.pose_native_keypoint_format = "halpe26"
        settings.scoring_trusted_promotion_ledger = None
        settings.scoring_trusted_runtime_profile_bindings = None
        settings.scoring_runtime_view_evidence_dir = None
        settings.resolved_scoring_registry_lifecycle_manifest = Path(
            "registry-lifecycle.json"
        )

        with (
            patch.object(
                worker,
                "configure_cpu_thread_pools",
                side_effect=lambda value: events.append("threads")
                or {"requested": value},
            ),
            patch.object(
                worker,
                "resolve_device",
                side_effect=lambda value: events.append("device") or value,
            ),
            patch.object(
                worker,
                "Yolo26Perception",
                side_effect=lambda **kwargs: events.append("model") or object(),
            ),
        ):
            runner = worker.PersistentVisionRunner(settings)

        self.assertEqual(events, ["validate", "threads", "device", "model"])
        self.assertEqual(runner.cpu_thread_configuration, {"requested": 4})

    def test_thread_configuration_failure_prevents_device_and_model_loading(self) -> None:
        settings = Mock()
        settings.cpu_threads = 4
        settings.device = "0"

        with (
            patch.object(
                worker,
                "configure_cpu_thread_pools",
                side_effect=worker.CpuThreadConfigurationError(
                    "failed to set Torch inter-op CPU threads to 4"
                ),
            ),
            patch.object(worker, "resolve_device") as resolve_device,
            patch.object(worker, "Yolo26Perception") as perception,
        ):
            with self.assertRaisesRegex(
                worker.CpuThreadConfigurationError,
                "Torch inter-op CPU threads to 4",
            ):
                worker.PersistentVisionRunner(settings)

        resolve_device.assert_not_called()
        perception.assert_not_called()

    def test_worker_scripts_set_blas_and_opencv_environment_before_python(self) -> None:
        root = Path(__file__).resolve().parents[1]
        for relative_path in (
            "scripts/run_rtmpose_worker.ps1",
            "scripts/run_worker.ps1",
        ):
            with self.subTest(script=relative_path):
                source = (root / relative_path).read_text(encoding="utf-8")
                self.assertIn("[int]$CpuThreads", source)
                self.assertIn('$resolvedCpuThreads = 4', source)
                self.assertIn(
                    '$PSBoundParameters.ContainsKey("CpuThreads")', source
                )
                self.assertIn(
                    '} elseif (-not [string]::IsNullOrWhiteSpace($env:RALLYMATE_CPU_THREADS)) {',
                    source,
                )
                self.assertLess(
                    source.index('$PSBoundParameters.ContainsKey("CpuThreads")'),
                    source.index('$env:RALLYMATE_CPU_THREADS))'),
                )
                for variable in (
                    "RALLYMATE_CPU_THREADS",
                    "OMP_NUM_THREADS",
                    "OMP_THREAD_LIMIT",
                    "MKL_NUM_THREADS",
                    "OPENBLAS_NUM_THREADS",
                    "NUMEXPR_NUM_THREADS",
                    "OPENCV_FOR_THREADS_NUM",
                ):
                    self.assertIn(f"$env:{variable} = $cpuThreadValue", source)
                self.assertLess(
                    source.index("$env:OMP_NUM_THREADS = $cpuThreadValue"),
                    source.index("$Root = Split-Path"),
                )
                self.assertNotIn("$env:RALLYMATE_DEVICE =", source)

        rtmpose_source = (root / "scripts/run_rtmpose_worker.ps1").read_text(
            encoding="utf-8"
        )
        for preset in (
            "rtmpose-m-halpe26-online",
            "rtmpose-m-halpe26-analysis",
            "rtmpose-l-halpe26-analysis-shadow",
            "rtmpose-m-wholebody133-analysis",
        ):
            self.assertIn(f'{{ "{preset}" }}', rtmpose_source)


if __name__ == "__main__":
    unittest.main()
