from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import numpy as np

from rallymate_vision.sam3d_mesh import capture_mesh_output
from rallymate_vision.sam3d_mesh_capture import BoundedMeshWriter


def native_mesh(marker=0):
    return {"pred_vertices": np.array([[0., 0., marker], [1., 0., marker], [0., 1., marker]], np.float32),
            "pred_cam_t": np.array([0., 0., 3.], np.float32), "focal_length": np.float32(100.)}


class MeshCaptureTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.faces = np.array([[0, 1, 2]], np.int64)

    def test_background_capture_owns_exact_values_until_publication(self):
        entered, release = threading.Event(), threading.Event()

        def capture(output, faces, root):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("fixture writer timed out")
            return capture_mesh_output(output, faces, root)

        writer = BoundedMeshWriter(self.root)
        source, expected = native_mesh(7), native_mesh(7)
        try:
            with patch("rallymate_vision.sam3d_mesh.capture_mesh_output", side_effect=capture):
                future, _ = writer.submit([source], self.faces)
                self.assertTrue(entered.wait(5))
                source["pred_vertices"].fill(-9)
                self.faces.fill(-1)
                self.assertFalse(future.done())
                release.set()
                artifacts, wall = future.result(timeout=5)
            self.assertGreaterEqual(wall, 0)
            with np.load(artifacts[0]["path"], allow_pickle=False) as data:
                np.testing.assert_array_equal(data["vertices"], expected["pred_vertices"])
                self.assertEqual(data["vertices"].dtype, expected["pred_vertices"].dtype)
            np.testing.assert_array_equal(np.load(artifacts[0]["faces_path"], allow_pickle=False), [[0, 1, 2]])
        finally:
            release.set()
            writer.close()

    def test_backpressure_bounds_running_and_queued_groups_and_bytes(self):
        entered, release, submitted = threading.Event(), threading.Event(), threading.Event()
        writer = BoundedMeshWriter(self.root, max_groups=1)
        completed = []

        def capture(output, faces, root):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("fixture writer timed out")
            return capture_mesh_output(output, faces, root)

        def second():
            completed.append(writer.submit([native_mesh(2)], self.faces)[0])
            submitted.set()

        thread = threading.Thread(target=second)
        try:
            with patch("rallymate_vision.sam3d_mesh.capture_mesh_output", side_effect=capture):
                first, _ = writer.submit([native_mesh(1)], self.faces)
                self.assertTrue(entered.wait(5))
                thread.start()
                self.assertFalse(submitted.wait(.05))
                self.assertEqual(writer.receipt["peak_pending_groups"], 1)
                release.set()
                thread.join(5)
                self.assertFalse(thread.is_alive())
                first.result(timeout=5)
                completed[0].result(timeout=5)
            expected_bytes = self.faces.nbytes + sum(array.nbytes for array in native_mesh().values())
            self.assertEqual(writer.receipt["peak_pending_array_bytes"], expected_bytes)
        finally:
            release.set()
            thread.join(5)
            writer.close()

    def test_failure_discards_partial_group_and_preserves_shared_topology(self):
        bad = native_mesh()
        bad["pred_vertices"].fill(np.nan)
        writer = BoundedMeshWriter(self.root)
        try:
            future, _ = writer.submit([native_mesh(), bad], self.faces)
            with self.assertRaises(ValueError):
                future.result(timeout=5)
            self.assertEqual(list((self.root / "cache/mesh").glob("*.npz")), [])
            self.assertEqual(len(list((self.root / "cache/mesh").glob("faces-*.npy"))), 1)
            self.assertEqual(writer._bytes, 0)
            self.assertEqual(writer._groups, 0)
            good, _ = writer.submit([native_mesh()], self.faces)
            self.assertEqual(len(good.result(timeout=5)[0]), 1)
        finally:
            writer.close()

    def test_oversized_group_rejected_before_capture_and_close_is_idempotent(self):
        writer = BoundedMeshWriter(self.root, max_bytes=1)
        with self.assertRaisesRegex(ValueError, "budget"):
            writer.submit([native_mesh()], self.faces)
        self.assertEqual(writer.receipt["submitted_groups"], 0)
        writer.close()
        writer.close()
        writer = BoundedMeshWriter(self.root)
        writer.close()
        with self.assertRaisesRegex(RuntimeError, "closed"):
            writer.submit([native_mesh()], self.faces)


if __name__ == "__main__":
    unittest.main()
