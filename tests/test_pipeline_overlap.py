from collections import Counter
from copy import deepcopy
import os
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from rallymate_vision.pipeline_overlap import (
    DetectionStage, PoseStage, PreparedFrameStream, overlap_enabled, require_isolated_sam,
)
from rallymate_vision.owned_subprocess import ProcessTreeCleanupError
from rallymate_vision.sam3d_pipeline_batch import sam_pose_observations
from rallymate_vision.sam3d_runner import Sam3DProcess
from tests import test_pipeline_mesh_archive as pipeline_fixture
from tests.test_sam3d_pipeline_batch import Perception, observation


class Cancelled(Exception):
    pass


class PipelineOverlapTests(unittest.TestCase):
    def test_default_off_and_explicit_switch(self):
        self.assertFalse(overlap_enabled({}))
        self.assertTrue(overlap_enabled({"RALLYMATE_SAM3D_PIPELINE_OVERLAP": "1"}))
        for invalid in ("yes", "2", True, 1):
            with self.subTest(value=invalid), self.assertRaises(ValueError):
                overlap_enabled({"RALLYMATE_SAM3D_PIPELINE_OVERLAP": invalid})

    def test_overlap_rejects_in_process_or_unknown_sam_engine_before_threads_start(self):
        for engine in (None, object()):
            with self.subTest(engine=type(engine).__name__), self.assertRaisesRegex(ValueError, "isolated SAM"):
                require_isolated_sam(SimpleNamespace(pose_backend=SimpleNamespace(engine=engine)))
        require_isolated_sam(SimpleNamespace(pose_backend=SimpleNamespace(engine=object.__new__(Sam3DProcess))))

    def test_pixels_and_nested_bindings_survive_producer_buffer_reuse(self):
        pixels = np.zeros((8, 8, 3), np.uint8)
        nested = {"binding": {"pts": -1}, "tracks": [{"epoch": -1}]}
        closed = []
        def source(check):
            try:
                for index in range(20):
                    check()
                    pixels.fill(index)
                    nested["binding"]["pts"] = index * 23
                    nested["tracks"][0]["epoch"] = index // 4
                    yield {"image": pixels, "metadata": nested}
            finally:
                closed.append(threading.get_ident())
        stream = PreparedFrameStream(source, enabled=True, max_frames=2, max_bytes=384)
        rows = list(stream)
        self.assertEqual(len(rows), 20)
        for index, row in enumerate(rows):
            np.testing.assert_array_equal(row["image"], np.full_like(pixels, index))
            self.assertEqual(row["metadata"], {"binding": {"pts": index * 23}, "tracks": [{"epoch": index // 4}]})
        self.assertEqual(closed, [stream._thread.ident])
        self.assertTrue(stream.receipt["joined"])
        self.assertEqual(stream.receipt["prepared_frames"], stream.receipt["consumed_frames"])
        self.assertLessEqual(stream.receipt["peak_ready_frames"], 2)
        self.assertLessEqual(stream.receipt["peak_ready_source_bytes"], 384)

    def test_backpressure_is_bounded_and_early_close_joins_producer(self):
        prepared = threading.Event()
        def source(check):
            for index in range(100):
                check()
                if index == 3:
                    prepared.set()
                yield observation(index)
        stream = PreparedFrameStream(source, enabled=True, max_frames=2, max_bytes=384)
        next(stream)
        self.assertTrue(prepared.wait(2))
        stream.close()
        stream.close()
        self.assertFalse(stream.alive)
        self.assertLessEqual(stream.receipt["prepared_frames"], 4)
        self.assertLessEqual(stream.receipt["peak_ready_source_bytes"], 384)
        self.assertEqual(stream.receipt["consumed_frames"], 1)
        self.assertLessEqual(stream.receipt["discarded_ready_frames"], 2)

    def test_empty_queue_cancellation_only_calls_external_check_on_consumer(self):
        cancel = threading.Event()
        callers, stopped = [], threading.Event()
        consumer = threading.get_ident()
        def check():
            callers.append(threading.get_ident())
            if cancel.is_set():
                raise Cancelled()
        def source(stop):
            try:
                while True:
                    stop()
                    cancel.wait(.005)
                yield  # make this a source iterator
            finally:
                stopped.set()
        stream = PreparedFrameStream(source, enabled=True, check_cancelled=check)
        timer = threading.Timer(.03, cancel.set)
        timer.start()
        try:
            with self.assertRaises(Cancelled):
                next(stream)
        finally:
            cancel.set()
            timer.join()
            stream.close()
        self.assertEqual(set(callers), {consumer})
        self.assertTrue(stopped.is_set())
        self.assertTrue(stream.receipt["joined"])
        self.assertGreater(stream.receipt["consumer_empty_wait_seconds"], 0)

    def test_producer_exception_propagates_and_closes_in_its_owner_thread(self):
        owner = []
        def source(check):
            try:
                raise ValueError("broken preparation")
                yield
            finally:
                owner.append(threading.get_ident())
        stream = PreparedFrameStream(source, enabled=True)
        with self.assertRaisesRegex(ValueError, "broken preparation"):
            list(stream)
        self.assertEqual(owner, [stream._thread.ident])
        self.assertTrue(stream.receipt["joined"])

    def test_unconfirmed_join_fails_closed_and_retains_resources(self):
        release, entered, source_closed = threading.Event(), threading.Event(), threading.Event()
        def source(check):
            try:
                yield observation(0)
                entered.set()
                release.wait(2)
                check()
            finally:
                source_closed.set()
        stream = PreparedFrameStream(source, enabled=True, join_timeout_seconds=.01)
        next(stream)
        self.assertTrue(entered.wait(2))
        try:
            with self.assertRaises(ProcessTreeCleanupError):
                stream.close()
            self.assertTrue(stream.alive)
            self.assertFalse(source_closed.is_set())
            with self.assertRaisesRegex(RuntimeError, "Join preparation"):
                _ = stream.receipt
        finally:
            release.set()
            stream._thread.join(2)
            stream.close()
        self.assertTrue(source_closed.is_set())
        self.assertTrue(stream.receipt["joined"])

    def test_oversized_source_is_rejected_without_unbounded_queue(self):
        stream = PreparedFrameStream(lambda _: iter([observation(0)]), enabled=True, max_bytes=1)
        with self.assertRaisesRegex(ValueError, "pixel-byte bound"):
            list(stream)
        self.assertTrue(stream.receipt["joined"])
        self.assertNotIn("enqueued_frames", stream.receipt)

    def test_next_preparation_overlaps_single_sam_and_preserves_all_batch_bindings(self):
        for batch in (1, 4, 8):
            with self.subTest(batch=batch):
                inputs = [observation(index, no_players=index == 4) for index in range(18)]
                serial_perception = Perception()
                expected = list(sam_pose_observations(deepcopy(inputs), serial_perception,
                    max_players=2, video_sha256="a" * 64, batch_frames=batch,
                    timings={}, statistics={}, check_cancelled=lambda: None))
                next_group_ready = threading.Event()
                def source(check):
                    for index, row in enumerate(inputs):
                        check()
                        if index == batch:
                            next_group_ready.set()
                        yield row
                shared = Perception()
                shared.last_pose_association_diagnostics = None
                shared.after_inference = lambda: self.assertTrue(next_group_ready.wait(2))
                pose = PoseStage(shared)
                stream = PreparedFrameStream(source, enabled=True, max_frames=max(2, batch))
                actual = list(sam_pose_observations(stream, pose, max_players=2, video_sha256="a" * 64,
                    batch_frames=batch, timings={}, statistics={}, check_cancelled=lambda: None))
                self.assertEqual([row["sam_records"] for row in actual], [row["sam_records"] for row in expected])
                self.assertEqual([row["sam_poses"] for row in actual], [row["sam_poses"] for row in expected])
                self.assertEqual(stream.receipt["consumed_frames"], 18)
                self.assertEqual(pose.calls, (18 + batch - 1) // batch)
                self.assertTrue(stream.receipt["joined"])

    def test_detection_facade_does_not_expose_or_mutate_pose_diagnostics(self):
        class Shared:
            last_pose_association_diagnostics = {"frame": {"index": 0}}
            last_detection_timing = {}
            last_detection_timings = []
            def detect(self, frame):
                self.last_detection_timing = {"wall": int(frame[0, 0, 0])}
                return []
            def estimate_poses(self):
                return []
        shared = Shared()
        pose = PoseStage(shared)
        pose.estimate_poses()
        detector = DetectionStage(shared, Counter())
        thread = threading.Thread(target=lambda: detector.detect(observation(9)["image"]))
        thread.start()
        thread.join()
        self.assertFalse(hasattr(detector, "last_pose_association_diagnostics"))
        self.assertEqual(shared.last_pose_association_diagnostics, {"frame": {"index": 0}})
        shared.last_pose_association_diagnostics["frame"]["index"] = 999
        self.assertEqual(pose.last_pose_association_diagnostics, {"frame": {"index": 0}})

    def test_real_pipeline_joins_preparation_before_decoder_close_and_publication(self):
        for mode in ("success", "cancel", "checksum_failure"):
            with self.subTest(mode=mode):
                fixture = pipeline_fixture.PipelineMeshArchiveTests()
                fixture.setUp()
                # No child/model is started: the CPU fixture exercises the
                # pipeline's already-qualified subprocess ownership boundary.
                fixture.perception.pose_backend.engine = object.__new__(Sam3DProcess)
                try:
                    original_read, original_release = fixture.capture.read, fixture.capture.release
                    owners = []
                    def read():
                        owners.append(threading.get_ident())
                        return original_read()
                    def release():
                        self.assertFalse(any(t.name == "sam-frame-prepare" and t.is_alive()
                                             for t in threading.enumerate()))
                        return original_release()
                    fixture.capture.read = read
                    fixture.capture.release = release
                    with patch.dict(os.environ, {"RALLYMATE_SAM3D_PIPELINE_OVERLAP": "1",
                                                "RALLYMATE_SAM3D_BATCH_FRAMES": "1"}):
                        fixture._run(mode)
                    self.assertEqual(len(set(owners)), 1)
                    self.assertNotEqual(owners[0], threading.get_ident())
                finally:
                    fixture.doCleanups()


if __name__ == "__main__":
    unittest.main()
