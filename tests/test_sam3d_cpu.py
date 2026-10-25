"""CPU-only policy tests: no Torch import, models, CUDA, or service changes."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from rallymate_vision.sam3d_cpu import (
    SAM_CPU_THREADS, SAM_INTEROP_THREADS, SAM_WAIT_MODE, Sam3DCpuConfigurationError,
    configure_sam_cpu_pools, prepare_sam_worker_environment,
)


class SamCpuEnvironmentTests(unittest.TestCase):
    def test_child_overrides_inherited_hard_limits_without_changing_parent(self):
        parent = {
            "RALLYMATE_CPU_THREADS": "20", SAM_CPU_THREADS: "4",
            "OMP_NUM_THREADS": "20", "OMP_THREAD_LIMIT": "2",
            "MKL_NUM_THREADS": "20", "OPENBLAS_NUM_THREADS": "20",
            "NUMEXPR_NUM_THREADS": "20", "NUMEXPR_MAX_THREADS": "8",
            "OPENCV_FOR_THREADS_NUM": "20", "PYTHONPATH": "existing-path",
        }
        before = dict(parent)
        child = prepare_sam_worker_environment(parent)
        self.assertEqual(parent, before)
        self.assertIsNot(child, parent)
        self.assertEqual(child["PYTHONPATH"], "existing-path")
        self.assertEqual(child["RALLYMATE_CPU_THREADS"], "20")
        for name in ("OMP_NUM_THREADS", "OMP_THREAD_LIMIT", "MKL_NUM_THREADS",
                     "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS", "NUMEXPR_MAX_THREADS"):
            self.assertEqual(child[name], "4", name)
        self.assertEqual(child["OPENCV_FOR_THREADS_NUM"], "1")
        self.assertEqual(child[SAM_INTEROP_THREADS], "1")
        self.assertEqual(child["OMP_WAIT_POLICY"], "PASSIVE")
        self.assertEqual(child["KMP_BLOCKTIME"], "0")

    def test_explicit_sam_budget_precedes_unrelated_invalid_parent_value(self):
        child = prepare_sam_worker_environment({
            SAM_CPU_THREADS: "8", SAM_INTEROP_THREADS: "2", "RALLYMATE_CPU_THREADS": "invalid",
        })
        self.assertEqual(child["OMP_NUM_THREADS"], "8")
        self.assertEqual(child[SAM_INTEROP_THREADS], "2")

    def test_fallback_and_defaults(self):
        self.assertEqual(prepare_sam_worker_environment({})[SAM_CPU_THREADS], "4")
        self.assertEqual(prepare_sam_worker_environment({"RALLYMATE_CPU_THREADS": "7"})[SAM_CPU_THREADS], "7")

    def test_explicit_wait_policy_for_control_trial_is_preserved(self):
        child = prepare_sam_worker_environment({"OMP_WAIT_POLICY": "ACTIVE", "KMP_BLOCKTIME": "200"})
        self.assertEqual(child["OMP_WAIT_POLICY"], "ACTIVE")
        self.assertEqual(child["KMP_BLOCKTIME"], "200")

    def test_native_wait_policy_is_restored_only_in_the_child(self):
        parent = {SAM_WAIT_MODE: "runtime_default", "OMP_WAIT_POLICY": "PASSIVE", "KMP_BLOCKTIME": "0"}
        child = prepare_sam_worker_environment(parent)
        self.assertNotIn("OMP_WAIT_POLICY", child)
        self.assertNotIn("KMP_BLOCKTIME", child)
        self.assertEqual(parent["OMP_WAIT_POLICY"], "PASSIVE")
        self.assertEqual(parent["KMP_BLOCKTIME"], "0")

    def test_explicit_passive_mode_overrides_inherited_active_settings(self):
        child = prepare_sam_worker_environment({SAM_WAIT_MODE: "passive", "OMP_WAIT_POLICY": "ACTIVE", "KMP_BLOCKTIME": "200"})
        self.assertEqual((child["OMP_WAIT_POLICY"], child["KMP_BLOCKTIME"]), ("PASSIVE", "0"))
        with self.assertRaisesRegex(Sam3DCpuConfigurationError, SAM_WAIT_MODE):
            prepare_sam_worker_environment({SAM_WAIT_MODE: "unknown"})

    def test_invalid_selected_budget_or_interop_fails_without_mutating_input(self):
        for name in (SAM_CPU_THREADS, SAM_INTEROP_THREADS, "RALLYMATE_CPU_THREADS"):
            for value in ("", "0", "-1", "2.5", "many", True, None):
                with self.subTest(name=name, value=value):
                    env = {name: value}
                    with self.assertRaisesRegex(Sam3DCpuConfigurationError, name):
                        prepare_sam_worker_environment(env)
                    self.assertEqual(env, {name: value})


class SamCpuRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.values = {"intraop": 20, "interop": 20, "opencv": 20}
        self.setters = {
            name: Mock(side_effect=lambda value, name=name: self.values.__setitem__(name, value))
            for name in self.values
        }
        self.torch = SimpleNamespace(
            get_num_threads=lambda: self.values["intraop"], set_num_threads=self.setters["intraop"],
            get_num_interop_threads=lambda: self.values["interop"], set_num_interop_threads=self.setters["interop"],
        )
        self.cv2 = SimpleNamespace(getNumThreads=lambda: self.values["opencv"], setNumThreads=self.setters["opencv"])

    def test_actual_pools_are_verified_and_recorded(self):
        result = configure_sam_cpu_pools(self.torch, self.cv2, {SAM_CPU_THREADS: "4"})
        self.assertEqual(result, {"requested_intraop": 4, "requested_interop": 1,
                                  "torch_intraop": 4, "torch_interop": 1, "opencv": 1})
        self.assertEqual(self.values, {"intraop": 4, "interop": 1, "opencv": 1})

    def test_identical_interop_is_not_set_again_even_when_intraop_changes(self):
        configure_sam_cpu_pools(self.torch, self.cv2, {SAM_CPU_THREADS: "4"})
        self.torch.set_num_interop_threads.side_effect = RuntimeError("can only set inter-op once")
        configure_sam_cpu_pools(self.torch, self.cv2, {SAM_CPU_THREADS: "4"})
        result = configure_sam_cpu_pools(self.torch, self.cv2, {SAM_CPU_THREADS: "8"})
        self.torch.set_num_interop_threads.assert_called_once_with(1)
        self.cv2.setNumThreads.assert_called_once_with(1)
        self.assertEqual(result["torch_intraop"], 8)

    def test_runtime_rejection_names_pool_current_and_request(self):
        self.torch.set_num_interop_threads.side_effect = RuntimeError("parallel work started")
        with self.assertRaisesRegex(
            Sam3DCpuConfigurationError, "Torch inter-op.*requested=1, current=20.*parallel work started",
        ):
            configure_sam_cpu_pools(self.torch, self.cv2, {})
        self.torch.set_num_threads.assert_not_called()

    def test_setter_that_does_not_apply_limit_cannot_claim_success(self):
        self.cv2.setNumThreads.side_effect = None
        with self.assertRaisesRegex(Sam3DCpuConfigurationError, "OpenCV.*requested=1, actual=20"):
            configure_sam_cpu_pools(self.torch, self.cv2, {})

    def test_getter_failure_is_diagnostic(self):
        self.torch.get_num_interop_threads = Mock(side_effect=RuntimeError("getter failed"))
        with self.assertRaisesRegex(Sam3DCpuConfigurationError, "read Torch inter-op.*getter failed"):
            configure_sam_cpu_pools(self.torch, self.cv2, {})
        self.torch.set_num_interop_threads.assert_not_called()

    def test_invalid_environment_is_rejected_before_any_runtime_mutation(self):
        with self.assertRaisesRegex(Sam3DCpuConfigurationError, SAM_INTEROP_THREADS):
            configure_sam_cpu_pools(self.torch, self.cv2, {SAM_CPU_THREADS: "4", SAM_INTEROP_THREADS: "0"})
        for setter in self.setters.values():
            setter.assert_not_called()


if __name__ == "__main__":
    unittest.main()
