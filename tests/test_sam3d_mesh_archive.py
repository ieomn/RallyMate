from pathlib import Path
from concurrent.futures import Future
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import numpy as np

from rallymate_vision.pose.metadata import sha256_file
from rallymate_vision.sam3d_mesh import capture_mesh_output
from rallymate_vision.sam3d_mesh_archive import BoundedMeshArchiver
from rallymate_vision import sam3d_mesh_archive as archive_module


class BoundedMeshArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.runtime = self.root / "runtime"
        self.output = self.root / "output"
        self.output.mkdir()
        self.vertices = np.array([[0., 0., 0.], [1., 0., 0.], [0., 1., 0.]], np.float32)
        self.faces = np.array([[0, 1, 2]], np.int32)

    def record(self, frame=0, track=4):
        artifact = capture_mesh_output({"pred_vertices": self.vertices,
            "pred_cam_t": np.array([0., 0., 3.], np.float32), "focal_length": np.float32(100)},
            self.faces, self.runtime)
        return {"status": "ok", "frame_index": frame, "processed_index": frame,
                "pts": frame * 20, "time_base_num": 1, "time_base_den": 600,
                "person_track_id": track, "selection_epoch": 0,
                "video_sha256": "a" * 64, "bbox_px": [1., 2., 3., 4.], "mesh_artifact": artifact}

    def archiver(self, **kwargs):
        instance = BoundedMeshArchiver(self.output, self.runtime, **kwargs)
        self.addCleanup(instance.close)
        return instance

    def test_fifo_multisubject_empty_failed_and_skipped_frames_keep_full_binding(self):
        first, second = self.record(0, 4), self.record(0, 7)
        frames = [[first, second], [], [{"status": "failed", "frame_index": 2}],
                  [{"status": "skipped", "frame_index": 3}]]
        archiver = self.archiver()
        batches = []
        for frame in frames:
            batches.extend(archiver.submit(frame))
        batches.extend(archiver.finish())
        self.assertEqual([batch.sequence for batch in batches], [0, 1, 2, 3])
        self.assertEqual([len(batch.records) for batch in batches], [2, 0, 1, 1])
        self.assertEqual([len(batch.mesh_records) for batch in batches], [2, 0, 0, 0])
        for native, ledger in zip(batches[0].records, batches[0].mesh_records):
            self.assertEqual(native["person_track_id"], ledger["person_track_id"])
            self.assertEqual(native["pts"], ledger["pts"])
            artifact = native["mesh_artifact"]
            path = self.output / artifact["path"]
            self.assertEqual(sha256_file(path), artifact["sha256"])
            with np.load(path, allow_pickle=False) as data:
                self.assertEqual(data["vertices"].dtype, self.vertices.dtype)
                np.testing.assert_array_equal(data["vertices"], self.vertices)
        self.assertTrue(Path(first["mesh_artifact"]["path"]).is_absolute())
        self.assertTrue(archiver.receipt["closed"])
        self.assertEqual(archiver.receipt["collected_frames"], 4)
        self.assertLessEqual(archiver.receipt["peak_pending_frames"], 2)

    def test_records_are_owned_deep_copies_before_background_access(self):
        started, release = threading.Event(), threading.Event()
        def archive(records, *_):
            started.set()
            self.assertTrue(release.wait(5))
            records[0]["mesh_artifact"]["path"] = "verified/job.npz"
            return [{"binding": records[0]["nested"]["binding"]}]
        archiver = self.archiver(_archive=archive)
        record = {"status": "ok", "mesh_artifact": {"path": "worker.npz"},
                  "nested": {"binding": [0, 10]}}
        self.assertEqual(archiver.submit([record]), [])
        self.assertTrue(started.wait(5))
        record["nested"]["binding"][0] = 99
        self.assertEqual(record["mesh_artifact"]["path"], "worker.npz")
        release.set()
        batch = archiver.finish()[0]
        self.assertEqual(batch.records[0]["nested"]["binding"], [0, 10])
        self.assertEqual(batch.records[0]["mesh_artifact"]["path"], "verified/job.npz")
        self.assertEqual(record["mesh_artifact"]["path"], "worker.npz")

    def test_third_submission_applies_backpressure_without_discarding_results(self):
        entered, release = threading.Event(), threading.Event()
        def archive(records, *_):
            if records[0]["index"] == 0:
                entered.set()
                self.assertTrue(release.wait(5))
            return []
        archiver = self.archiver(_archive=archive)
        self.assertEqual(archiver.submit([{"index": 0}]), [])
        self.assertTrue(entered.wait(5))
        self.assertEqual(archiver.submit([{"index": 1}]), [])
        self.assertEqual(archiver.collect_ready(), [])
        self.assertEqual(archiver.receipt["pending_frames"], 2)
        returned = threading.Event()
        collected = []
        def submit_third():
            collected.extend(archiver.submit([{"index": 2}]))
            returned.set()
        control = threading.Thread(target=submit_third)
        control.start()
        self.assertFalse(returned.wait(.05))
        release.set()
        control.join(5)
        self.assertFalse(control.is_alive())
        collected.extend(archiver.finish())
        self.assertEqual([batch.sequence for batch in collected], [0, 1, 2])
        self.assertEqual(archiver.receipt["peak_pending_frames"], 2)

    def test_checksum_failure_propagates_and_prevents_future_submission(self):
        record = self.record()
        with Path(record["mesh_artifact"]["path"]).open("ab") as handle:
            handle.write(b"tampered")
        archiver = self.archiver()
        archiver.submit([record])
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            archiver.finish()
        self.assertTrue(archiver.receipt["closed"])
        self.assertEqual(archiver.receipt["collected_frames"], 0)
        self.assertFalse((self.output / "sam3d-mesh-data").exists())
        with self.assertRaisesRegex(RuntimeError, "closed"):
            archiver.submit([])

    def test_containment_failure_remains_enforced_in_background(self):
        record = self.record()
        record["mesh_artifact"]["path"] = str(self.root / "not-owned.npz")
        archiver = self.archiver()
        archiver.submit([record])
        with self.assertRaisesRegex(ValueError, "escapes"):
            archiver.finish()

    def test_close_joins_active_file_work_and_cancels_pending_frame_without_publication(self):
        started, release, completed, closed = (threading.Event() for _ in range(4))
        calls = []
        def archive(records, *_):
            calls.append(records[0]["index"])
            started.set()
            self.assertTrue(release.wait(5))
            completed.set()
            return []
        archiver = self.archiver(_archive=archive)
        archiver.submit([{"index": 0}])
        self.assertTrue(started.wait(5))
        archiver.submit([{"index": 1}])
        def close():
            archiver.close()
            self.assertTrue(completed.is_set())
            closed.set()
        closer = threading.Thread(target=close)
        closer.start()
        self.assertFalse(closed.wait(.05))
        release.set()
        closer.join(5)
        self.assertTrue(closed.is_set())
        self.assertEqual(calls, [0])
        self.assertEqual(archiver.receipt["collected_frames"], 0)
        self.assertEqual(archiver.receipt["pending_frames"], 0)
        archiver.close()

    def test_queue_bound_validation_is_strict(self):
        for value in (0, 3, True, 1.5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.archiver(max_pending_frames=value)

    def test_failed_shutdown_keeps_archive_exception_and_allows_join_retry(self):
        primary = ValueError("original checksum failure")
        cleanup = OSError("injected join failure")
        future = Future()
        future.set_exception(primary)
        pool = Mock()
        pool.submit.return_value = future
        pool.shutdown.side_effect = [cleanup, None]
        with patch.object(archive_module, "ThreadPoolExecutor", return_value=pool):
            archiver = self.archiver()
        archiver.submit([{"status": "ok"}])
        with self.assertRaises(ValueError) as caught:
            archiver.collect_ready()
        self.assertIs(caught.exception, primary)
        self.assertIs(caught.exception.__cause__, cleanup)
        self.assertFalse(archiver.receipt["accepting"])
        self.assertFalse(archiver.receipt["closed"])
        self.assertFalse(archiver.receipt["joined"])
        with self.assertRaisesRegex(RuntimeError, "closed"):
            archiver.submit([])
        archiver.close()
        self.assertEqual(pool.shutdown.call_count, 2)
        self.assertTrue(archiver.receipt["closed"])
        self.assertTrue(archiver.receipt["joined"])

    def test_cleanup_failure_during_cancellation_preserves_cancellation_exception(self):
        pool = Mock()
        cleanup = OSError("injected shutdown failure")
        pool.shutdown.side_effect = [cleanup, None]
        with patch.object(archive_module, "ThreadPoolExecutor", return_value=pool):
            archiver = self.archiver()
        cancellation = RuntimeError("original cancellation")
        try:
            raise cancellation
        except RuntimeError:
            with self.assertRaises(RuntimeError) as caught:
                archiver.close()
        self.assertIs(caught.exception, cancellation)
        self.assertIs(caught.exception.__cause__, cleanup)
        self.assertFalse(archiver.receipt["closed"])
        archiver.close()
        self.assertTrue(archiver.receipt["closed"])


if __name__ == "__main__":
    unittest.main()
