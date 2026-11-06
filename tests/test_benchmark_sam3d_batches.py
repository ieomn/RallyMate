from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from scripts.benchmark_sam3d_batches import (
    BINDING_FIELDS, array_difference, build_worker_profile, compare_snapshots,
    decode_bound_frames, load_archived_snapshots, select_records, snapshots,
    validate_archive_filesystem, verify_batch_execution, _existing_filesystem_device,
)
from rallymate_vision.pose.metadata import sha256_file
from rallymate_vision.sam3d_mesh import capture_mesh_output


def binding(index=0, *, track=1):
    return dict(zip(BINDING_FIELDS, ["a" * 64, index, index, 100 + index * 25, 1, 1000,
                                     100 + index * 25, track, 0, 0, [8, 6], [1, 1, 7, 5]]))


def snapshot(index=0):
    return {"binding": binding(index), "keypoints_2d": np.zeros((70, 2)),
            "keypoints_3d": np.zeros((70, 3)), "vertices": np.zeros((100, 3)),
            "camera": np.array([0, 0, 5.]), "focal": np.array(800.), "faces": np.array([[0, 1, 2]])}


class SamBatchBenchmarkTests(unittest.TestCase):
    def test_archive_filesystem_uses_existing_ancestor_before_any_cache_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime, artifacts = root / "runtime", root / "reports" / "case-artifacts"
            validate_archive_filesystem(runtime, artifacts)
            self.assertEqual(_existing_filesystem_device(runtime / "cache" / "mesh"), root.stat().st_dev)
            self.assertFalse(runtime.exists())
            self.assertFalse(artifacts.exists())

    def test_archive_filesystem_rejects_cross_volume_before_model_load(self):
        runtime, artifacts = Path("runtime"), Path("reports") / "case-artifacts"
        with patch("scripts.benchmark_sam3d_batches._existing_filesystem_device", side_effect=[11, 22]) as device:
            with self.assertRaisesRegex(ValueError, "share a filesystem"):
                validate_archive_filesystem(runtime, artifacts)
        self.assertEqual([call.args[0] for call in device.call_args_list],
                         [runtime / "cache" / "mesh", artifacts])

    def test_execution_receipt_proves_group_upload_and_rejects_ignored_switch(self):
        receipt = {"candidate": "official_crop_batched_uint8_staging_v1",
                   "last_batch_subjects": 8, "last_image_uploads": 1,
                   "batch_calls": 3, "image_uploads": 3}
        outputs = [[{"batch_execution": {"subjects": 8, "forward_calls": 1,
                                         "batch_preprocess": deepcopy(receipt)}}] for _ in range(8)]
        verify_batch_execution(outputs, 8, batch_preprocess=True)
        with self.assertRaisesRegex(ValueError, "disabled"):
            verify_batch_execution(outputs, 8, batch_preprocess=False)
        for key, value in (("candidate", "other"), ("last_batch_subjects", 4),
                           ("last_image_uploads", 8), ("image_uploads", 24),
                           ("batch_calls", 0), ("batch_calls", True)):
            broken = deepcopy(outputs)
            broken[0][0]["batch_execution"]["batch_preprocess"][key] = value
            with self.subTest(key=key, value=value), self.assertRaisesRegex(ValueError, "receipt"):
                verify_batch_execution(broken, 8, batch_preprocess=True)
        for row in outputs:
            row[0]["batch_execution"]["batch_preprocess"] = {}
        verify_batch_execution(outputs, 8, batch_preprocess=False)
        with self.assertRaisesRegex(ValueError, "receipt"):
            verify_batch_execution(outputs, 8, batch_preprocess=True)

    def test_execution_receipt_rejects_microbatches_and_missing_subjects(self):
        outputs = [[{"batch_execution": {"subjects": 4, "forward_calls": 1}}] for _ in range(8)]
        with self.assertRaisesRegex(ValueError, "exactly one forward"):
            verify_batch_execution(outputs, 8, batch_preprocess=False)
        with self.assertRaisesRegex(ValueError, "subject count"):
            verify_batch_execution(outputs[:-1], 8, batch_preprocess=False)
        with self.assertRaisesRegex(ValueError, "subject count"):
            verify_batch_execution([], 8, batch_preprocess=True)

    def test_preprocess_cli_precedence_and_recorded_default_are_reproducible(self):
        options = dict(profile="production", batch_sizes=[1, 8], max_players=1)
        environment = {"RALLYMATE_SAM3D_BATCH_PREPROCESS": "1", "RALLYMATE_SAM3D_BATCH_GRAPH_SIZES": "8"}
        inherited = build_worker_profile(**options, environment=environment)
        self.assertEqual(inherited["RALLYMATE_SAM3D_BATCH_PREPROCESS"], "1")
        self.assertEqual(inherited["RALLYMATE_SAM3D_BATCH_GRAPH_SIZES"], "8")
        self.assertEqual(inherited["RALLYMATE_SAM3D_BATCH_PERSONS"], "8")
        explicit = build_worker_profile(**options, batch_preprocess=0, environment=environment)
        self.assertEqual(explicit["RALLYMATE_SAM3D_BATCH_PREPROCESS"], "0")
        self.assertEqual(environment["RALLYMATE_SAM3D_BATCH_PREPROCESS"], "1")
        self.assertEqual(build_worker_profile(**options, environment={})["RALLYMATE_SAM3D_BATCH_PREPROCESS"], "0")
        with self.assertRaises(ValueError):
            build_worker_profile(**options, environment={"RALLYMATE_SAM3D_BATCH_PREPROCESS": "yes"})

    def test_eager_profile_does_not_inherit_incompatible_graph_shapes(self):
        settings = build_worker_profile(profile="eager", batch_sizes=[1, 4], max_players=2,
            batch_preprocess=1, environment={"RALLYMATE_SAM3D_BATCH_GRAPH_SIZES": "4,8"})
        self.assertEqual(settings["RALLYMATE_SAM3D_CUDA_GRAPH"], "0")
        self.assertEqual(settings["RALLYMATE_SAM3D_BATCH_GRAPH_SIZES"], "")
        self.assertEqual(settings["RALLYMATE_SAM3D_BATCH_PREPROCESS"], "1")

    def test_cuda_wait_policy_is_explicit_in_profile_instead_of_hidden_inheritance(self):
        options = dict(profile="production", batch_sizes=[1, 8], max_players=1)
        self.assertEqual(build_worker_profile(**options, environment={})["RALLYMATE_SAM3D_CUDA_WAIT_MODE"],
                         "runtime_default")
        actual = build_worker_profile(**options, environment={"RALLYMATE_SAM3D_CUDA_WAIT_MODE": "blocking_sync"})
        self.assertEqual(actual["RALLYMATE_SAM3D_CUDA_WAIT_MODE"], "blocking_sync")
        self.assertEqual(actual["RALLYMATE_SAM3D_WAIT_MODE"], "passive")
        with self.assertRaisesRegex(ValueError, "CUDA wait mode"):
            build_worker_profile(**options, environment={"RALLYMATE_SAM3D_CUDA_WAIT_MODE": "passive"})

    def test_comparison_checks_all_mesh_vertices_camera_and_focal(self):
        reference = [snapshot()]
        actual = deepcopy(reference)
        actual[0]["vertices"][99, 2] = .03
        actual[0]["camera"][0] = .2
        actual[0]["focal"] += 1
        diff = compare_snapshots(reference, actual)
        self.assertFalse(diff["all_exact"])
        self.assertEqual(diff["max_abs_by_field"]["vertices"], .03)
        self.assertEqual(diff["max_abs_by_field"]["camera"], .2)
        self.assertEqual(diff["max_abs_by_field"]["focal"], 1)
        self.assertEqual(diff["max_abs_by_field"]["keypoints_3d"], 0)

    def test_shape_drift_is_json_serializable_not_exact(self):
        reference, actual = [snapshot()], [snapshot()]
        actual[0]["vertices"] = np.zeros((99, 3))
        diff = compare_snapshots(reference, actual)
        self.assertFalse(diff["all_exact"])
        self.assertIsNone(diff["max_abs_by_field"]["vertices"])
        json.dumps(diff, allow_nan=False)

    def test_binding_and_failed_geometry_are_never_silently_compared(self):
        with self.assertRaisesRegex(ValueError, "ordering"):
            compare_snapshots([snapshot()], [snapshot(1)])
        with self.assertRaisesRegex(ValueError, "non-finite"):
            array_difference([1], [np.nan])

    def test_archive_source_sampler_preserves_subject_order_and_skips_failures(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "native.jsonl"
            rows = [{**binding(0), "status": "failed"}, {**binding(1, track=4), "status": "ok"},
                    {**binding(1, track=9), "status": "ok"}, {**binding(2), "status": "ok"},
                    {**binding(3), "status": "ok"}]
            path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
            selected = select_records(path, count=2, start_frame=0, max_players=2)
            self.assertEqual([[row["person_track_id"] for row in group] for group in selected], [[4, 9], [1]])
            self.assertEqual([group[0]["frame_index"] for group in selected], [1, 2])

    def test_decoded_pixels_must_match_archive_pts_size_and_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory) / "source.mp4"
            video.write_bytes(b"source hash fixture")
            records = [[{**binding(0), "video_sha256": sha256_file(video)}]]
            class Decoder:
                def __init__(self, path):
                    self.binding = {"pts": 100, "time_base_num": 1, "time_base_den": 1000}
                def read(self): return True, np.ones((6, 8, 3), dtype=np.uint8)
                def release(self): pass
            with patch("scripts.benchmark_sam3d_batches.SourcePTSDecoder", Decoder):
                frames, _ = decode_bound_frames(video, records, max_decode_frames=10)
                self.assertEqual(frames[0]["boxes"], [[1, 1, 7, 5]])
                records[0][0]["pts"] = 101
                with self.assertRaisesRegex(ValueError, "PTS"):
                    decode_bound_frames(video, records, max_decode_frames=10)

    def test_snapshot_preserves_complete_verified_native_mesh_for_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            vertices = np.arange(12, dtype=np.float32).reshape(4, 3)
            faces = np.array([[0, 1, 2], [0, 2, 3]], dtype=np.int32)
            artifact = capture_mesh_output({"pred_vertices": vertices,
                "pred_cam_t": np.array([0., 0., 5.], np.float32), "focal_length": np.float32(800)}, faces, root / "runtime")
            outputs = [[{"status": "ok", "mesh_artifact": artifact,
                         "keypoints_2d": np.ones((70, 2)).tolist(), "keypoints_3d": np.ones((70, 3)).tolist()}]]
            actual = snapshots(outputs, [{"bindings": [binding()]}], runtime=root / "runtime", archive=root / "saved")
            np.testing.assert_array_equal(actual[0]["vertices"], vertices)
            np.testing.assert_array_equal(actual[0]["faces"], faces)
            self.assertTrue((root / "saved/native-results.json").is_file())
            self.assertEqual(len(list((root / "saved/sam3d-mesh-data").glob("*.npz"))), 1)
            reloaded = load_archived_snapshots(root / "saved")
            self.assertTrue(compare_snapshots(actual, reloaded)["all_exact"])
            mesh_path = next((root / "saved/sam3d-mesh-data").glob("*.npz"))
            mesh_path.write_bytes(mesh_path.read_bytes() + b"tampered")
            with self.assertRaisesRegex(ValueError, "checksum"):
                load_archived_snapshots(root / "saved")


if __name__ == "__main__":
    unittest.main()
