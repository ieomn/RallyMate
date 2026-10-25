import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from scripts.compare_sam_cpu_product_runs import array_difference, cpu_accounting, mesh_arrays
from scripts.compare_sam_product_runs import digest


def sample(elapsed, api_cpu, sam_cpu, *, created=20, status="running"):
    return {"elapsed_seconds": elapsed, "job": {"status": status}, "processes": [
        {"pid": 1, "create_time": 10, "role": "api", "cpu_seconds": api_cpu},
        {"pid": 2, "create_time": created, "role": "sam", "cpu_seconds": sam_cpu},
        {"pid": 3, "create_time": 30, "role": "sam", "cpu_seconds": 0},
    ]}


class CpuProductAuditTests(unittest.TestCase):
    def test_cpu_core_seconds_use_counter_deltas_and_exact_sample_interval(self):
        samples = [sample(2, 100, 500, status="queued"), sample(3, 101, 504), sample(4, 103, 506, status="succeeded")]
        # Percent samples are deliberately unrelated; they cannot alter accounting.
        for row in samples:
            for process in row["processes"]:
                process["cpu_percent"] = 99999
        result = cpu_accounting(samples)
        self.assertTrue(result["same_full_sample_interval_for_api_and_sam"])
        self.assertEqual(result["cpu_core_seconds_by_role"], {"api": 3, "sam": 6})
        self.assertEqual(result["api_plus_sam_cpu_core_seconds"], 9)
        self.assertEqual(result["api_plus_sam_average_cores"], 4.5)
        self.assertEqual(result["sampled_wall_seconds"], 2)

    def test_pid_reuse_is_not_merged_and_missing_cpu_is_not_extrapolated(self):
        result = cpu_accounting([sample(0, 2, 100), sample(1, 3, 200), sample(2, 4, 1, created=99)])
        self.assertFalse(result["same_full_sample_interval_for_api_and_sam"])
        self.assertIsNone(result["api_plus_sam_cpu_core_seconds"])
        self.assertEqual(len([p for p in result["per_process"] if p["pid"] == 2]), 2)

    def test_missing_role_or_creation_time_does_not_fabricate_sam_attribution(self):
        samples = [sample(0, 2, 100), sample(1, 3, 120)]
        for row in samples:
            row["processes"][1].pop("role")
            row["processes"][1].pop("create_time")
        result = cpu_accounting(samples)
        self.assertFalse(result["same_full_sample_interval_for_api_and_sam"])
        # The tagged zero-CPU launcher cannot make the unclassified actual worker disappear.
        self.assertTrue(result["issues"])

    def test_decreasing_counter_is_reported_invalid(self):
        result = cpu_accounting([sample(0, 2, 100), sample(1, 3, 90)])
        self.assertFalse(result["same_full_sample_interval_for_api_and_sam"])
        self.assertIsNone(result["api_plus_sam_cpu_core_seconds"])

    def test_numeric_tolerance_never_changes_strict_equality(self):
        result = array_difference(np.array([1.]), np.array([1.000001]), .0001)
        self.assertTrue(result["within_atol"])
        self.assertFalse(result["numeric_exact"])
        self.assertGreater(result["max_abs_difference"], 0)
        self.assertFalse(array_difference([0.], [float("nan")], .0001)["within_atol"])
        self.assertFalse(array_difference([1., 2.], [1.], .0001)["shape_and_finite_valid"])

    def test_mesh_arrays_validate_bytes_and_native_camera_without_renderer(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            data = folder / "sam3d-mesh-data"
            data.mkdir()
            faces = np.array([[0, 1, 2]], dtype=np.int64)
            vertices = np.zeros((3, 3), dtype=np.float32)
            camera = np.array([0, 0, 2], dtype=np.float32)
            np.save(data / "faces.npy", faces)
            np.savez(data / "mesh.npz", vertices=vertices, camera=camera, focal=np.asarray(1000.))
            row = {"pred_cam_t": camera.tolist(), "focal_length": 1000., "mesh_artifact": {
                "path": "sam3d-mesh-data/mesh.npz", "sha256": digest(data / "mesh.npz"),
                "faces_path": "sam3d-mesh-data/faces.npy", "faces_sha256": digest(data / "faces.npy"),
                "vertex_count": 3, "face_count": 1}}
            self.assertTrue(np.array_equal(mesh_arrays(folder, row, {})["vertices"], vertices))
            row["pred_cam_t"][2] = 3
            with self.assertRaisesRegex(ValueError, "native estimator"):
                mesh_arrays(folder, row, {})
            row["pred_cam_t"][2] = 2
            with (data / "mesh.npz").open("ab") as handle:
                handle.write(b"changed")
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                mesh_arrays(folder, row, {})


if __name__ == "__main__":
    unittest.main()
