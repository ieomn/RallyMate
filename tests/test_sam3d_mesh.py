from pathlib import Path
from collections import defaultdict
import hashlib
import io
import json
import tempfile
from types import SimpleNamespace
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

    def test_crowded_selection_diagnostics_do_not_overflow_mesh_ledger(self):
        artifact = capture_mesh_output(self.output, self.faces, self.runtime)
        selection = {"version": "secondary-subject-selection-v1", "primary_track_id": 1,
                     "decisions": [{"track_id": i, "decision": "rejected", "reason": "test_candidate"}
                                   for i in range(400)]}
        record = {"status": "ok", "mesh_artifact": artifact, "person_track_id": 8,
                  "subject_selection": selection}
        row = archive_mesh_records([record], self.output_dir, self.runtime)[0]
        self.assertLess(len(json.dumps(row)), 16384)
        self.assertEqual(row["subject_selection"]["decision"], selection["decisions"][8])
        self.assertEqual(len(record["subject_selection"]["decisions"]), 400)

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

    def archived_artifact(self):
        artifact = capture_mesh_output(self.output, self.faces, self.runtime)
        return archive_mesh_records([{'status': 'ok', 'mesh_artifact': artifact}],
                                    self.output_dir, self.runtime)[0]['mesh_artifact']

    def test_renderer_reads_each_verified_npz_once_and_keeps_exact_arrays(self):
        artifact = self.archived_artifact()
        calls, cache, timing = [], {}, defaultdict(float)
        original_open = Path.open
        def opened(path, *args, **kwargs):
            if args and args[0] == 'rb':
                calls.append(Path(path))
            return original_open(path, *args, **kwargs)
        with patch.object(Path, 'open', new=opened):
            first = mesh_module._load_verified_geometry(self.output_dir, artifact, cache, timing)
            second = mesh_module._load_verified_geometry(self.output_dir, artifact, cache, timing)
        self.assertEqual(calls.count((self.output_dir / artifact['path']).resolve()), 2)
        self.assertEqual(calls.count((self.output_dir / artifact['faces_path']).resolve()), 1)
        for actual in (first, second):
            np.testing.assert_array_equal(actual[0], self.output['pred_vertices'])
            np.testing.assert_array_equal(actual[1], self.faces)
            np.testing.assert_array_equal(actual[2], self.output['pred_cam_t'])
            self.assertEqual(actual[0].dtype, self.output['pred_vertices'].dtype)
            self.assertEqual(actual[1].dtype, self.faces.dtype)
            self.assertEqual(actual[3], float(self.output['focal_length']))
        self.assertEqual(set(timing), {'geometry_substage_file_read', 'geometry_substage_sha256',
            'geometry_substage_array_decode', 'geometry_substage_array_validation'})

    def test_renderer_rejects_geometry_or_topology_full_sha_before_decode(self):
        for key in ('path', 'faces_path'):
            with self.subTest(key=key):
                artifact = self.archived_artifact()
                path = self.output_dir / artifact[key]
                original = path.read_bytes()
                path.write_bytes(original + b'tampered-tail')
                with patch.object(mesh_module.np, 'load') as load, self.assertRaisesRegex(ValueError, 'changed after inference'):
                    mesh_module._load_verified_geometry(self.output_dir, artifact, {}, defaultdict(float))
                load.assert_not_called()
                path.write_bytes(original)

    def test_renderer_decodes_the_hashed_snapshot_without_reopening_a_replaced_file(self):
        artifact = self.archived_artifact()
        path = self.output_dir / artifact['path']
        original_sha = hashlib.sha256
        def hash_then_replace(payload):
            result = original_sha(payload)
            path.write_bytes(b'changed after the verified read')
            return result
        with patch.object(mesh_module.hashlib, 'sha256', side_effect=hash_then_replace):
            actual = mesh_module._load_verified_geometry(self.output_dir, artifact,
                {artifact['faces_sha256']: self.faces}, defaultdict(float))
        np.testing.assert_array_equal(actual[0], self.output['pred_vertices'])

    def test_renderer_keeps_shape_camera_finite_and_topology_checks(self):
        artifact = self.archived_artifact()
        path = self.output_dir / artifact['path']
        cases = [('vertices', np.ones((2, 3), np.float32)), ('vertices', np.ones((3, 2), np.float32)),
                 ('vertices', np.full((3, 3), np.nan)), ('camera', np.array([0., 0., -1.])),
                 ('focal', np.array(float('inf')))]
        for name, value in cases:
            values = dict(vertices=self.output['pred_vertices'], camera=self.output['pred_cam_t'],
                          focal=self.output['focal_length'])
            values[name] = value
            with path.open('wb') as stream:
                np.savez(stream, **values)
            artifact['sha256'] = sha256_file(path)
            with self.subTest(name=name, shape=value.shape), self.assertRaises(ValueError):
                mesh_module._load_verified_geometry(self.output_dir, artifact, {}, defaultdict(float))
        with path.open('wb') as stream:
            np.savez(stream, vertices=self.output['pred_vertices'], camera=self.output['pred_cam_t'],
                     focal=self.output['focal_length'])
        artifact['sha256'] = sha256_file(path)
        for faces in (np.array([[0, 1, 999]], dtype=np.int64), np.array([[0., 1., 2.]]),
                      np.array([[0, 1]], dtype=np.int64)):
            with self.subTest(topology=faces.tolist()), self.assertRaisesRegex(ValueError, 'topology'):
                mesh_module._load_verified_geometry(self.output_dir, artifact,
                    {artifact['faces_sha256']: faces}, defaultdict(float))

    def test_renderer_rejects_escape_and_bounded_file_oversize(self):
        artifact = self.archived_artifact()
        with self.assertRaisesRegex(ValueError, 'escapes'):
            mesh_module._load_verified_geometry(self.output_dir,
                {**artifact, 'path': '../outside.npz'}, {}, defaultdict(float))
        with patch.object(mesh_module, 'MAX_GEOMETRY_FILE_BYTES', 64), self.assertRaisesRegex(ValueError, 'file bound'):
            mesh_module._load_verified_geometry(self.output_dir, artifact, {}, defaultdict(float))

    def test_renderer_rejects_growth_or_truncation_on_the_same_open_file(self):
        artifact = self.archived_artifact()
        size = (self.output_dir / artifact['path']).stat().st_size
        for delta in (-1, 1):
            with self.subTest(delta=delta), patch.object(mesh_module.os, 'fstat',
                    return_value=SimpleNamespace(st_size=size + delta)), self.assertRaisesRegex(ValueError, 'size changed'):
                mesh_module._load_verified_geometry(self.output_dir, artifact, {}, defaultdict(float))


if __name__ == "__main__":
    unittest.main()
