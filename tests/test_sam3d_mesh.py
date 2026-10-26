from pathlib import Path
import io
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from rallymate_vision.sam3d_mesh import capture_mesh_output, archive_mesh_records
from rallymate_vision import sam3d_mesh as mesh_module
from rallymate_vision.pose.metadata import sha256_file


class MeshCaptureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.runtime = self.root / "runtime"
        self.output_dir = self.root / "job"
        self.output_dir.mkdir()
        self.output = {"pred_vertices": np.array([[0., 0., 0.], [1., 0., 0.], [0., 1., 0.]], np.float32),
                       "pred_cam_t": np.array([0., 0., 3.], np.float32), "focal_length": np.array(100., np.float32)}
        self.faces = np.array([[0, 1, 2]], np.int32)

    def test_binary_geometry_retains_values_dtype_and_source_identity(self):
        artifact = capture_mesh_output(self.output, self.faces, self.runtime)
        record = {"status": "ok", "mesh_artifact": artifact, "frame_index": 41, "pts": 822,
                  "time_base_num": 1, "time_base_den": 600, "person_track_id": 8,
                  "selection_epoch": 2, "video_sha256": "a" * 64, "image_size": [1280, 720]}
        rows = archive_mesh_records([record], self.output_dir, self.runtime)
        self.assertEqual(rows[0]["pts"], 822)
        self.assertEqual(rows[0]["person_track_id"], 8)
        self.assertFalse(Path(artifact["path"]).exists())
        with np.load(self.output_dir / rows[0]["mesh_artifact"]["path"], allow_pickle=False) as data:
            self.assertEqual(data["vertices"].dtype, np.float32)
            np.testing.assert_array_equal(data["vertices"], self.output["pred_vertices"])
            np.testing.assert_array_equal(data["camera"], self.output["pred_cam_t"])

    def test_tampered_geometry_is_not_rebound_to_source_frame(self):
        artifact = capture_mesh_output(self.output, self.faces, self.runtime)
        with Path(artifact["path"]).open("ab") as handle:
            handle.write(b"tampered")
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            archive_mesh_records([{"status": "ok", "mesh_artifact": artifact}], self.output_dir, self.runtime)
        self.assertFalse((self.output_dir / "sam3d-mesh-data").exists())

    def test_path_outside_worker_cache_is_rejected(self):
        artifact = capture_mesh_output(self.output, self.faces, self.runtime)
        artifact["path"] = str(self.root / "unrelated.npz")
        with self.assertRaisesRegex(ValueError, "escapes"):
            archive_mesh_records([{"status": "ok", "mesh_artifact": artifact}], self.output_dir, self.runtime)

    def test_nonfinite_geometry_and_invalid_camera_cannot_become_video(self):
        for key, value in (("pred_vertices", np.full((3, 3), np.nan, np.float32)),
                           ("pred_cam_t", np.array([0., 0., -1.])),
                           ("focal_length", np.inf)):
            with self.subTest(key=key), self.assertRaises(ValueError):
                capture_mesh_output({**self.output, key: value}, self.faces, self.runtime)

    def test_topology_outside_actual_vertex_array_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "topology"):
            capture_mesh_output(self.output, np.array([[0, 1, 3]]), self.runtime)

    def test_memory_npz_preserves_legacy_container_bytes_for_all_geometry_dtypes(self):
        for dtype in (np.float32, np.float64):
            for face_dtype in (np.int32, np.int64):
                with self.subTest(dtype=dtype, face_dtype=face_dtype):
                    # Strided source arrays must retain exact values and dtype.
                    vertices = np.tile(self.output['pred_vertices'].astype(dtype), (1, 2))[:, ::2]
                    output = {**self.output, 'pred_vertices': vertices,
                              'pred_cam_t': self.output['pred_cam_t'].astype(dtype)}
                    artifact = capture_mesh_output(output, self.faces.astype(face_dtype), self.runtime)
                    expected = io.BytesIO()
                    np.savez(expected, vertices=vertices, camera=output['pred_cam_t'],
                             focal=np.asarray(float(output['focal_length'])))
                    self.assertEqual(Path(artifact['path']).read_bytes(), expected.getvalue())
                    self.assertEqual(artifact['sha256'], sha256_file(artifact['path']))
                    self.assertEqual(artifact['sha256'], artifact['sha256'].upper())
                    with np.load(artifact['path'], allow_pickle=False) as actual:
                        for key, value in [('vertices', vertices), ('camera', output['pred_cam_t']),
                                           ('focal', np.asarray(float(output['focal_length'])))]:
                            self.assertEqual(actual[key].dtype, value.dtype)
                            self.assertEqual(actual[key].shape, value.shape)
                            np.testing.assert_array_equal(actual[key], value)

    def test_capture_reads_topology_checksum_but_does_not_reopen_mesh_for_self_hash(self):
        seen = []
        def checked(path):
            seen.append(Path(path))
            self.assertEqual(Path(path).suffix, '.npy')
            return sha256_file(path)
        with patch.object(mesh_module, 'sha256_file', side_effect=checked):
            artifact = capture_mesh_output(self.output, self.faces, self.runtime)
        self.assertEqual(len(seen), 1)
        self.assertEqual(artifact['sha256'], sha256_file(artifact['path']))

    def test_rename_failure_removes_unpublished_mesh_temporary(self):
        artifact = capture_mesh_output(self.output, self.faces, self.runtime)
        cache = Path(artifact['path']).parent
        before = set(cache.iterdir())
        original = Path.replace
        def replace(source, destination):
            if source.suffix == '.npz':
                raise OSError('simulated atomic rename failure')
            return original(source, destination)
        with patch.object(Path, 'replace', new=replace), self.assertRaisesRegex(OSError, 'atomic rename'):
            capture_mesh_output(self.output, self.faces, self.runtime)
        self.assertEqual(set(cache.iterdir()), before)

    def test_write_failure_removes_unpublished_mesh_temporary(self):
        artifact = capture_mesh_output(self.output, self.faces, self.runtime)
        cache = Path(artifact['path']).parent
        before = set(cache.iterdir())
        original = mesh_module.tempfile.NamedTemporaryFile
        def broken(*args, **kwargs):
            handle = original(*args, **kwargs)
            handle.write = lambda payload: (_ for _ in ()).throw(OSError('simulated write failure'))
            return handle
        with patch.object(mesh_module.tempfile, 'NamedTemporaryFile', side_effect=broken), self.assertRaisesRegex(OSError, 'write failure'):
            capture_mesh_output(self.output, self.faces, self.runtime)
        self.assertEqual(set(cache.iterdir()), before)

    def test_memory_serialization_failure_creates_no_mesh_temporary(self):
        artifact = capture_mesh_output(self.output, self.faces, self.runtime)
        cache = Path(artifact['path']).parent
        before = set(cache.iterdir())
        with patch.object(mesh_module.np, 'savez', side_effect=ValueError('serialization failure')):
            with self.assertRaisesRegex(ValueError, 'serialization failure'):
                capture_mesh_output(self.output, self.faces, self.runtime)
        self.assertEqual(set(cache.iterdir()), before)

    def test_parent_rejects_corrupted_topology_after_memory_capture(self):
        artifact = capture_mesh_output(self.output, self.faces, self.runtime)
        with Path(artifact['faces_path']).open('ab') as handle:
            handle.write(b'corrupted')
        with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
            archive_mesh_records([{'status': 'ok', 'mesh_artifact': artifact}], self.output_dir, self.runtime)


if __name__ == "__main__":
    unittest.main()
