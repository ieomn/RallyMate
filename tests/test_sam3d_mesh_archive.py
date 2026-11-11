from pathlib import Path
from concurrent.futures import Future
import os
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import numpy as np

from rallymate_vision.pose.metadata import sha256_file
from rallymate_vision.sam3d_mesh import archive_mesh_records, capture_mesh_output
from rallymate_vision.sam3d_mesh_archive import BoundedMeshArchiver, configured_archive_pending_frames
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
        for value in (0, 3, 5, 9, True, 1.5, "4"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.archiver(max_pending_frames=value)

    def test_environment_fits_one_batch_retains_scalar_default_and_honors_explicit_caps(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(configured_archive_pending_frames(), 2)
            for batch, expected in (("1", 2), ("2", 2), ("3", 4), ("4", 4), ("8", 8)):
                os.environ["RALLYMATE_SAM3D_BATCH_FRAMES"] = batch
                self.assertEqual(configured_archive_pending_frames(), expected)
            os.environ["RALLYMATE_SAM3D_BATCH_FRAMES"] = "9"
            with self.assertRaisesRegex(ValueError, "BATCH_FRAMES"):
                configured_archive_pending_frames()
            os.environ["RALLYMATE_SAM3D_BATCH_FRAMES"] = "8"
            for value in ("2", "4", "8"):
                os.environ["RALLYMATE_SAM3D_ARCHIVE_PENDING_FRAMES"] = value
                self.assertEqual(configured_archive_pending_frames(), int(value))
            for value in ("", "0", "1", "3", "9", "true", "4.0"):
                os.environ["RALLYMATE_SAM3D_ARCHIVE_PENDING_FRAMES"] = value
                with self.subTest(value=value), self.assertRaisesRegex(ValueError, "ARCHIVE_PENDING_FRAMES"):
                    configured_archive_pending_frames()

    def test_batch_sized_queues_preserve_fifo_and_apply_backpressure_at_the_bound(self):
        for capacity in (4, 8):
            with self.subTest(capacity=capacity):
                entered, release, returned = (threading.Event() for _ in range(3))
                self.addCleanup(release.set)
                calls, collected, errors = [], [], []

                def archive(records, *_):
                    index = records[0]["index"]
                    calls.append(index)
                    if index == 0:
                        entered.set()
                        if not release.wait(5):
                            raise RuntimeError("test release timeout")
                    return []

                archiver = self.archiver(max_pending_frames=capacity, _archive=archive)
                self.assertEqual(archiver.submit([{"index": 0}]), [])
                self.assertTrue(entered.wait(5))
                for index in range(1, capacity):
                    self.assertEqual(archiver.submit([{"index": index}]), [])
                self.assertEqual(calls, [0])  # One worker owns all topology writes.
                self.assertEqual(archiver.receipt["pending_frames"], capacity)

                def submit_over_bound():
                    try:
                        collected.extend(archiver.submit([{"index": capacity}]))
                    except BaseException as error:
                        errors.append(error)
                    finally:
                        returned.set()

                control = threading.Thread(target=submit_over_bound)
                control.start()
                self.assertFalse(returned.wait(.05))
                release.set()
                control.join(5)
                self.assertFalse(control.is_alive())
                self.assertEqual(errors, [])
                collected.extend(archiver.finish())
                self.assertEqual([batch.sequence for batch in collected], list(range(capacity + 1)))
                self.assertEqual(calls, list(range(capacity + 1)))
                self.assertEqual(archiver.receipt["peak_pending_frames"], capacity)
                self.assertEqual(archiver.receipt["worker_threads"], 1)

    def test_eight_slot_cancellation_joins_active_and_discards_all_queued_publication(self):
        entered, release, closed = (threading.Event() for _ in range(3))
        self.addCleanup(release.set)
        calls, errors = [], []

        def archive(records, *_):
            calls.append(records[0]["index"])
            entered.set()
            if not release.wait(5):
                raise RuntimeError("test release timeout")
            return []

        archiver = self.archiver(max_pending_frames=8, _archive=archive)
        archiver.submit([{"index": 0}])
        self.assertTrue(entered.wait(5))
        for index in range(1, 8):
            archiver.submit([{"index": index}])

        def close():
            try:
                archiver.close()
            except BaseException as error:
                errors.append(error)
            finally:
                closed.set()

        closer = threading.Thread(target=close)
        closer.start()
        self.assertFalse(closed.wait(.05))
        release.set()
        closer.join(5)
        self.assertFalse(closer.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(calls, [0])
        self.assertEqual(archiver.receipt["collected_frames"], 0)
        self.assertEqual(archiver.receipt["pending_frames"], 0)
        self.assertTrue(archiver.receipt["joined"])

    def test_cancel_removes_owned_queued_captures_only_after_active_archive_joins(self):
        records = [self.record(index) for index in range(8)]
        paths = [Path(row["mesh_artifact"]["path"]) for row in records]
        unrelated = self.record(99)
        topology = Path(records[0]["mesh_artifact"]["faces_path"])
        entered, release, joining = threading.Event(), threading.Event(), threading.Event()
        errors, calls = [], []
        self.addCleanup(release.set)

        def archive(rows, *args):
            calls.append(rows[0]["frame_index"])
            entered.set()
            if not release.wait(5):
                raise TimeoutError("test archive not released")
            return archive_mesh_records(rows, *args)

        archiver = self.archiver(max_pending_frames=8, _archive=archive)
        archiver.submit([records[0]])
        self.assertTrue(entered.wait(5))
        for row in records[1:]:
            archiver.submit([row])
        shutdown = archiver._pool.shutdown
        def observed_shutdown(**kwargs):
            joining.set()
            return shutdown(**kwargs)
        def close():
            try:
                archiver.close()
            except BaseException as error:
                errors.append(error)
        with patch.object(archiver._pool, "shutdown", side_effect=observed_shutdown):
            closer = threading.Thread(target=close)
            closer.start()
            self.assertTrue(joining.wait(5))
            # Queued files must not be deleted until the active archive stops.
            self.assertTrue(all(path.is_file() for path in paths))
            release.set()
            closer.join(5)
        self.assertFalse(closer.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(calls, [0])
        self.assertTrue((self.output / "sam3d-mesh-data" / paths[0].name).is_file())
        self.assertTrue(all(not path.exists() for path in paths))
        self.assertTrue(Path(unrelated["mesh_artifact"]["path"]).is_file())
        self.assertTrue(topology.is_file())
        self.assertEqual(archiver.receipt["collected_frames"], 0)
        archiver.close()

    def test_partial_archive_failure_preserves_moved_file_and_discards_remaining_owned_capture(self):
        records = [self.record(0, track) for track in (4, 7)]
        paths = [Path(row["mesh_artifact"]["path"]) for row in records]
        unrelated = self.record(99)
        def archive(rows, *args):
            archive_mesh_records(rows[:1], *args)
            raise ValueError("failure after first subject move")
        archiver = self.archiver(_archive=archive)
        archiver.submit(records)
        with self.assertRaisesRegex(ValueError, "after first subject move"):
            archiver.finish()
        self.assertTrue((self.output / "sam3d-mesh-data" / paths[0].name).is_file())
        self.assertTrue(all(not path.exists() for path in paths))
        self.assertTrue(Path(unrelated["mesh_artifact"]["path"]).is_file())
        self.assertTrue(Path(records[0]["mesh_artifact"]["faces_path"]).is_file())

    def test_submit_previous_failure_discards_current_capture_before_future_acceptance(self):
        previous, current, unrelated = self.record(0), self.record(1), self.record(99)
        failure = ValueError("previous archive failure")
        future = Future()
        future.set_exception(failure)
        pool = Mock()
        pool.submit.return_value = future
        with patch.object(archive_module, "ThreadPoolExecutor", return_value=pool):
            archiver = self.archiver()
        archiver.submit([previous])
        with self.assertRaises(ValueError) as caught:
            archiver.submit([current])
        self.assertIs(caught.exception, failure)
        pool.submit.assert_called_once()  # The current frame never reached executor.
        self.assertFalse(Path(previous["mesh_artifact"]["path"]).exists())
        self.assertFalse(Path(current["mesh_artifact"]["path"]).exists())
        self.assertTrue(Path(unrelated["mesh_artifact"]["path"]).exists())
        self.assertTrue(Path(current["mesh_artifact"]["faces_path"]).exists())

    def test_executor_rejection_discards_current_capture_after_shutdown(self):
        current = self.record()
        path = Path(current["mesh_artifact"]["path"])
        pool = Mock()
        pool.submit.side_effect = RuntimeError("executor rejected frame")
        pool.shutdown.side_effect = lambda **kwargs: self.assertTrue(path.is_file())
        with patch.object(archive_module, "ThreadPoolExecutor", return_value=pool):
            archiver = self.archiver()
        with self.assertRaisesRegex(RuntimeError, "executor rejected frame"):
            archiver.submit([current])
        pool.shutdown.assert_called_once_with(wait=True, cancel_futures=True)
        self.assertFalse(path.exists())

    def test_failed_join_retains_owned_files_until_successful_retry(self):
        current = self.record()
        path = Path(current["mesh_artifact"]["path"])
        pool = Mock()
        pool.submit.return_value = Future()
        pool.shutdown.side_effect = [OSError("join failure"), None]
        with patch.object(archive_module, "ThreadPoolExecutor", return_value=pool):
            archiver = self.archiver()
        archiver.submit([current])
        with self.assertRaisesRegex(OSError, "join failure"):
            archiver.close()
        self.assertTrue(path.is_file())
        self.assertFalse(archiver.receipt["joined"])
        archiver.close()
        self.assertFalse(path.exists())
        self.assertTrue(archiver.receipt["joined"])

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
