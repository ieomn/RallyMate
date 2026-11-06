from __future__ import annotations

import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from rallymate_vision.sam3d_runner import Sam3DEngine


def native(marker):
    return {"pred_keypoints_2d": np.full((70, 2), marker, np.float32),
            "pred_keypoints_3d": np.full((70, 3), marker, np.float32),
            "pred_cam_t": np.array([0., 0., 3.]), "focal_length": np.float32(100.)}


def request(marker, count):
    return {"frame": np.full((10, 10, 3), marker, np.uint8),
            "boxes": [[i + 1, 1., i + 2, 9.] for i in range(count)]}


class EngineBatchTests(unittest.TestCase):
    def setUp(self):
        self.engine = Sam3DEngine.__new__(Sam3DEngine)
        self.engine.inference_type = "body"
        self.engine.mesh_export_enabled = False
        self.engine.reuse_cuda_cache = False
        self.engine.inference_calls = 0
        cuda = SimpleNamespace(synchronize=Mock(), reset_peak_memory_stats=Mock(),
                               max_memory_allocated=Mock(return_value=1024),
                               max_memory_reserved=Mock(return_value=2048))
        self.engine.torch = SimpleNamespace(cuda=cuda)
        self.executor = Mock()
        self.executor.infer.side_effect = lambda rows, **kwargs: [native(int(frame[0, 0, 0]) + box[0]) for frame, box in rows]
        self.engine.body_batch_executor = self.executor
        self.engine.infer = Mock(side_effect=lambda frame, boxes: [{"status": "ok", "reason": None,
            "keypoints_2d": native(int(frame[0, 0, 0]) + box[0])["pred_keypoints_2d"].tolist()} for box in boxes])
        environment = patch.dict(os.environ, {"RALLYMATE_SAM3D_BATCH_PERSONS": "4"})
        environment.start()
        self.addCleanup(environment.stop)

    def test_nested_frame_order_empty_slots_and_singleton_tail(self):
        inputs = [request(10, 2), request(20, 0), request(30, 3)]
        outputs = self.engine.infer_batch(inputs)
        self.assertEqual([len(rows) for rows in outputs], [2, 0, 3])
        self.assertEqual([[row["keypoints_2d"][0][0] for row in rows] for rows in outputs],
                         [[11., 12.], [], [31., 32., 33.]])
        self.assertEqual(self.executor.infer.call_count, 1)
        self.assertEqual(len(self.executor.infer.call_args.args[0]), 4)
        self.assertTrue(self.executor.infer.call_args.kwargs["clear_cuda_cache"])
        self.engine.infer.assert_called_once()
        for row in outputs[0] + outputs[2][:2]:
            self.assertEqual(row["batch_execution"]["subjects"], 4)
            self.assertEqual(row["batch_execution"]["forward_calls"], 1)
            self.assertTrue(row["first_model_call"])

    def test_multiple_groups_are_bounded_and_first_call_is_not_repeated(self):
        outputs = self.engine.infer_batch([request(10, 4), request(20, 4)])
        self.assertEqual([len(call.args[0]) for call in self.executor.infer.call_args_list], [4, 4])
        self.assertTrue(all(row["first_model_call"] for row in outputs[0]))
        self.assertFalse(any(row["first_model_call"] for row in outputs[1]))
        self.assertEqual(self.engine.inference_calls, 2)
        self.engine.infer.assert_not_called()

    def test_lazy_executor_receives_requested_preprocessing_and_default_is_off(self):
        self.engine.estimator = object()
        for enabled in (None, True, False):
            self.engine.body_batch_executor = None
            if enabled is None:
                self.engine.__dict__.pop("batch_preprocess_enabled", None)
            else:
                self.engine.batch_preprocess_enabled = enabled
            with patch("rallymate_vision.sam3d_batch.BodyBatchInference", return_value=self.executor) as constructor:
                outputs = self.engine.infer_batch([request(0, 2)])
            constructor.assert_called_once_with(self.engine.estimator, batch_preprocess=bool(enabled))
            self.assertEqual([row["status"] for row in outputs[0]], ["ok", "ok"])

    def test_preprocess_receipts_snapshot_each_call_without_sharing_mutable_counters(self):
        receipt = {"candidate": "official_crop_batched_uint8_staging_v1", "batch_calls": 0,
                   "image_uploads": 0, "last_batch_subjects": 4, "last_image_uploads": 1}
        self.executor.receipt = {"batch_preprocess": receipt}

        def infer(rows, **kwargs):
            receipt["batch_calls"] += 1
            receipt["image_uploads"] += 1
            return [native(1) for _ in rows]

        self.executor.infer.side_effect = infer
        outputs = self.engine.infer_batch([request(0, 4), request(1, 4)])
        self.assertEqual(outputs[0][0]["batch_execution"]["batch_preprocess"]["batch_calls"], 1)
        self.assertEqual(outputs[1][0]["batch_execution"]["batch_preprocess"]["batch_calls"], 2)
        receipt["image_uploads"] = 100
        first = outputs[0][0]["batch_execution"]["batch_preprocess"]
        self.assertEqual(first["image_uploads"], 1)
        first["image_uploads"] = 500
        self.assertEqual(outputs[0][1]["batch_execution"]["batch_preprocess"]["image_uploads"], 1)

    def test_no_subjects_skip_model_and_preserve_empty_frames(self):
        self.assertEqual(self.engine.infer_batch([]), [])
        self.assertEqual(self.engine.infer_batch([request(0, 0), request(1, 0)]), [[], []])
        self.executor.infer.assert_not_called()
        self.engine.infer.assert_not_called()

    def test_native_shape_or_nonfinite_failure_has_no_partial_success_payload(self):
        malformed_fields = (
            ("pred_keypoints_2d", np.zeros((69, 2))),
            ("pred_keypoints_2d", np.full((70, 2), np.nan)),
            ("pred_keypoints_3d", np.zeros((70, 2))),
            ("pred_keypoints_3d", np.full((70, 3), np.inf)),
            ("pred_cam_t", np.zeros((2,))),
            ("pred_cam_t", np.array([0., 0., np.nan])),
            ("focal_length", np.array([100., 200.])),
            ("focal_length", np.float32(np.nan)),
        )
        for field, malformed in malformed_fields:
            bad = native(1)
            bad[field] = malformed
            self.executor.infer.side_effect = lambda rows, **kwargs: [native(0), bad]
            with self.subTest(field=field, malformed=malformed.shape):
                outputs = self.engine.infer_batch([request(10, 2)])[0]
                self.assertEqual([row["status"] for row in outputs], ["failed", "failed"])
                for row in outputs:
                    self.assertEqual(row["reason"], "sam_batch_inference_error")
                    for payload in ("keypoints_2d", "keypoints_3d", "mesh_artifact", "pred_cam_t", "focal_length"):
                        self.assertNotIn(payload, row)
        self.engine.infer.assert_not_called()

    def test_single_value_focal_array_is_accepted_without_changing_shape(self):
        row = native(1)
        row["focal_length"] = np.array([100.], np.float32)
        self.executor.infer.side_effect = lambda rows, **kwargs: [row, native(2)]
        outputs = self.engine.infer_batch([request(0, 2)])[0]
        self.assertEqual([item["status"] for item in outputs], ["ok", "ok"])
        self.assertEqual(outputs[0]["focal_length"], [100.])
        self.assertEqual(outputs[1]["focal_length"], 100.)

    def test_partial_mesh_capture_failure_discards_owned_geometry_and_preserves_topology(self):
        from rallymate_vision.sam3d_mesh import capture_mesh_output

        faces = np.array([[0, 1, 2]], np.int64)
        good = {**native(1), "pred_vertices": np.array(
            [[0., 0., 0.], [1., 0., 0.], [0., 1., 0.]], np.float32)}
        bad = {**native(2), "pred_vertices": np.full((3, 3), np.nan, np.float32)}
        captured = []

        def capture(output, topology, runtime):
            if captured:
                # The first native capture must really exist when the second
                # subject fails, or this would only exercise early validation.
                self.assertTrue(Path(captured[0]["path"]).is_file())
            artifact = capture_mesh_output(output, topology, runtime)
            self.assertTrue(Path(artifact["path"]).is_file())
            captured.append(artifact)
            return artifact

        with tempfile.TemporaryDirectory() as directory:
            self.engine.runtime_dir = Path(directory)
            self.engine.estimator = SimpleNamespace(faces=faces)
            self.engine.mesh_export_enabled = True
            self.executor.infer.side_effect = lambda rows, **kwargs: [good, bad]
            with patch("rallymate_vision.sam3d_mesh.capture_mesh_output", side_effect=capture) as save:
                outputs = self.engine.infer_batch([request(0, 2)])[0]
            self.assertEqual(save.call_count, 2)
            self.assertEqual(len(captured), 1)
            self.assertFalse(Path(captured[0]["path"]).exists())
            cache = Path(directory) / "cache" / "mesh"
            self.assertEqual(list(cache.glob("*.npz")), [])
            topology_files = list(cache.glob("faces-*.npy"))
            self.assertEqual(topology_files, [Path(captured[0]["faces_path"])])
            np.testing.assert_array_equal(np.load(topology_files[0], allow_pickle=False), faces)
            self.assertEqual([row["status"] for row in outputs], ["failed", "failed"])
            for row in outputs:
                self.assertEqual(row["reason"], "sam_batch_inference_error")
                self.assertEqual(row["error_type"], "ValueError")
                for field in ("mesh_artifact", "keypoints_2d", "keypoints_3d", "pred_cam_t", "focal_length"):
                    self.assertNotIn(field, row)
        self.executor.infer.assert_called_once()
        self.engine.infer.assert_not_called()

    def test_batch_exception_or_wrong_count_never_reruns_singletons(self):
        for side_effect in (RuntimeError("controlled failure"), lambda *a, **kw: [native(0)]):
            self.executor.infer.reset_mock()
            self.executor.infer.side_effect = side_effect
            outputs = self.engine.infer_batch([request(10, 2)])[0]
            self.assertTrue(all(row["status"] == "failed" for row in outputs))
            self.executor.infer.assert_called_once()
        self.engine.infer.assert_not_called()

    def test_full_mode_and_count_bytes_bbox_limits_fail_before_inference(self):
        self.engine.inference_type = "full"
        with self.assertRaisesRegex(ValueError, "body"):
            self.engine.infer_batch([request(0, 2)])
        self.engine.inference_type = "body"
        for inputs in ([request(0, 1)] * 9, [request(0, 5)],
                       [{"frame": np.zeros((10, 10, 3), np.uint8), "boxes": [[0, 0, 11, 5]]}]):
            with self.assertRaises(ValueError):
                self.engine.infer_batch(inputs)
        with patch("rallymate_vision.sam3d_runner.MAX_BATCH_SOURCE_BYTES", 100):
            with self.assertRaisesRegex(ValueError, "128 MiB"):
                self.engine.infer_batch([request(0, 1)])
        with patch.dict(os.environ, {"RALLYMATE_SAM3D_BATCH_PERSONS": "9"}):
            with self.assertRaises(ValueError):
                self.engine.infer_batch([request(0, 1)])
        self.executor.infer.assert_not_called()
        self.engine.infer.assert_not_called()

    def test_explicit_one_subject_limit_keeps_existing_single_path(self):
        with patch.dict(os.environ, {"RALLYMATE_SAM3D_BATCH_PERSONS": "1"}):
            outputs = self.engine.infer_batch([request(10, 3)])
        self.assertEqual(len(outputs[0]), 3)
        self.assertEqual(self.engine.infer.call_count, 3)
        self.executor.infer.assert_not_called()


if __name__ == "__main__":
    unittest.main()
