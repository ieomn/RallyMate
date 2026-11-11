from collections import defaultdict
import os
from threading import Event, Thread, get_ident
import unittest
from unittest.mock import Mock, patch

import numpy as np

from rallymate_vision.mesh_prepare import (
    PreparedMeshFrames, PreparedMeshPair, estimated_prepare_bytes, prepare_workers,
)
from rallymate_vision.owned_subprocess import ProcessTreeCleanupError
from rallymate_vision.sam3d_mesh import OfficialMeshRenderer
from rallymate_vision import sam3d_mesh


def frame(index, *, size=2):
    pixels = np.full((size, size, 3), index, dtype=np.uint8)
    pixels.setflags(write=False)
    return {"index": index, "image": pixels, "outputs": []}


class MeshPrepareTests(unittest.TestCase):
    def test_environment_is_strict_and_default_off(self):
        self.assertEqual(prepare_workers({}), 0)
        self.assertEqual(prepare_workers({"RALLYMATE_SAM3D_MESH_PREPARE_WORKERS": "2"}), 2)
        for value in ("1", "3", "true", " 2", "02", 2, None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                prepare_workers({"RALLYMATE_SAM3D_MESH_PREPARE_WORKERS": value})

    def test_serial_keeps_source_and_prepare_on_calling_thread(self):
        owner, observations = get_ident(), []
        def source():
            for index in range(4):
                observations.append(("source", get_ident()))
                yield frame(index)
        def prepare(image, outputs):
            observations.append(("prepare", get_ident()))
            return int(image[0, 0, 0])
        stream = PreparedMeshFrames(source(), prepare, workers=0)
        self.assertEqual([result for _, result in stream], list(range(4)))
        self.assertTrue(all(thread == owner for _, thread in observations))
        self.assertEqual(stream.receipt["submitted_frames"], 0)
        self.assertEqual(stream.receipt["consumed_frames"], 4)
        self.assertTrue(stream.receipt["joined"])

    def test_two_workers_finish_out_of_order_but_consume_fifo_and_source_on_owner(self):
        owner, source_threads, prepare_threads = get_ident(), [], set()
        second_ready = Event()
        def source():
            for index in range(12):
                source_threads.append(get_ident())
                yield frame(index)
        def prepare(image, outputs):
            prepare_threads.add(get_ident())
            self.assertFalse(image.flags.writeable)
            index = int(image[0, 0, 0])
            if index == 0:
                self.assertTrue(second_ready.wait(2))
            if index == 1:
                second_ready.set()
            return index
        stream = PreparedMeshFrames(source(), prepare, workers=2)
        self.assertEqual([result for _, result in stream], list(range(12)))
        self.assertEqual(set(source_threads), {owner})
        self.assertNotIn(owner, prepare_threads)
        self.assertEqual(len(prepare_threads), 2)
        receipt = stream.receipt
        self.assertEqual(receipt["peak_pending_frames"], 2)
        self.assertEqual(receipt["submitted_frames"], 12)
        self.assertEqual(sum(row["calls"] for row in receipt["per_worker"]), 12)

    def test_pending_byte_limit_and_oversize_sync_fallback_preserve_fifo(self):
        owner, calls = get_ident(), []
        frames = [frame(0), frame(1), frame(2, size=20), frame(3)]
        bound = estimated_prepare_bytes(frames[0]) + 1
        def prepare(image, outputs):
            index = int(image[0, 0, 0])
            calls.append((index, get_ident()))
            return index
        stream = PreparedMeshFrames(frames, prepare, workers=2, max_pending_bytes=bound)
        self.assertEqual([result for _, result in stream], [0, 1, 2, 3])
        receipt = stream.receipt
        self.assertLessEqual(receipt["peak_pending_estimated_bytes"], bound)
        self.assertEqual(receipt["peak_pending_frames"], 1)
        self.assertEqual(receipt["synchronous_large_frame_fallbacks"], 1)
        self.assertEqual(receipt["submitted_frames"], 3)
        self.assertLessEqual(receipt["peak_unconsumed_jobs"], 2)
        self.assertEqual(receipt["peak_deferred_input_bytes"], frames[2]["image"].nbytes)
        self.assertIn((2, owner), calls)
        self.assertLess([index for index, _ in calls].index(1), [index for index, _ in calls].index(2))

    def test_current_gl_slot_has_two_following_cpu_jobs_in_flight(self):
        started = [Event(), Event()]
        release = Event()
        def prepare(image, outputs):
            index = int(image[0, 0, 0])
            if index in (1, 2):
                started[index - 1].set()
                self.assertTrue(release.wait(2))
            return index
        stream = PreparedMeshFrames([frame(index) for index in range(4)], prepare, workers=2)
        try:
            _, first = next(stream)
            self.assertEqual(first, 0)
            # Simulate the main thread rendering frame 0: both CPU workers
            # must already own the next two frames, without a third future.
            self.assertTrue(all(event.wait(1) for event in started))
            self.assertEqual(len(stream._pending), 2)
        finally:
            release.set()
            stream.close()

    def test_prepare_exception_and_timeout_exception_are_not_swallowed(self):
        for exception in (ValueError("bad geometry"), TimeoutError("preparation itself timed out")):
            closed = []
            def source():
                try:
                    yield frame(0)
                    yield frame(1)
                finally:
                    closed.append(True)
            def prepare(image, outputs):
                raise exception
            stream = PreparedMeshFrames(source(), prepare, workers=2)
            with self.subTest(exception=type(exception)), self.assertRaises(type(exception)):
                list(stream)
            self.assertTrue(closed)
            self.assertTrue(stream.receipt["joined"])

    def test_cancellation_joins_running_prepare_before_source_close(self):
        cancelled, finished, source_closed = Event(), Event(), []
        def source():
            try:
                yield frame(0)
                yield frame(1)
            finally:
                source_closed.append(finished.is_set())
        def prepare(image, outputs):
            cancelled.set()
            finished.set()
            return 0
        def check():
            if cancelled.is_set():
                raise RuntimeError("cancelled")
        stream = PreparedMeshFrames(source(), prepare, workers=2, check_cancelled=check)
        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            list(stream)
        self.assertEqual(source_closed, [True])
        self.assertTrue(stream.receipt["joined"])

    def test_failed_join_does_not_close_source_or_claim_completed_receipt(self):
        started, release, source_closed = Event(), Event(), []
        def source():
            try:
                yield frame(0)
                yield frame(1)
                yield frame(2)
            finally:
                source_closed.append(True)
        def prepare(image, outputs):
            started.set()
            release.wait(2)
            return 0
        def check():
            if started.is_set():
                raise RuntimeError("cancelled")
        stream = PreparedMeshFrames(source(), prepare, workers=2, check_cancelled=check,
                                    join_timeout_seconds=.01)
        try:
            with self.assertRaises(ProcessTreeCleanupError):
                next(stream)
            self.assertEqual(source_closed, [])
            with self.assertRaisesRegex(RuntimeError, "Join"):
                _ = stream.receipt
            original_deadline = stream._join_deadline
            with self.assertRaises(ProcessTreeCleanupError):
                stream.close()
            self.assertEqual(stream._join_deadline, original_deadline)
        finally:
            release.set()
            for thread in stream._threads:
                thread.join(1)
            stream.close()
        self.assertTrue(stream.receipt["joined"])
        self.assertEqual(source_closed, [True])

    def test_gl_render_and_close_reject_foreign_thread(self):
        renderer = object.__new__(OfficialMeshRenderer)
        renderer._gl_owner, renderer._closed = get_ident(), False
        renderer.context = Mock()
        pixels = np.zeros((2, 2, 3), np.uint8)
        prepared = PreparedMeshPair(pixels, np.full_like(pixels, 255))
        errors = []
        def foreign():
            for call in (lambda: renderer.render_prepared_pair(prepared),
                         lambda: renderer.render(pixels, []), renderer.close):
                try:
                    call()
                except RuntimeError as error:
                    errors.append(str(error))
        thread = Thread(target=foreign)
        thread.start()
        thread.join()
        self.assertEqual(len(errors), 3)
        renderer.context.render.assert_not_called()
        renderer.context.delete.assert_not_called()
        front, side = renderer.render_prepared_pair(prepared)
        np.testing.assert_array_equal(front, pixels)
        np.testing.assert_array_equal(side, prepared.white)
        renderer.close()
        renderer.context.delete.assert_called_once()

    def test_compatibility_wrapper_uses_the_unified_gl_entry(self):
        renderer = object.__new__(OfficialMeshRenderer)
        renderer._gl_owner, renderer._closed = get_ident(), False
        renderer.timing_seconds = defaultdict(float)
        prepared, expected = object(), (object(), object())
        renderer.prepare_pair = Mock(return_value=prepared)
        renderer.render_prepared_pair = Mock(return_value=expected)
        pixels = np.zeros((2, 2, 3), np.uint8)
        self.assertIs(renderer.render_pair(pixels, []), expected)
        renderer.render_prepared_pair.assert_called_once_with(prepared)

    def test_unconfirmed_previous_join_rejects_new_render_before_opening_source(self):
        with patch.object(sam3d_mesh, '_UNJOINED_PREPARATIONS', [object()]), \
                patch('av.open') as opened, self.assertRaises(ProcessTreeCleanupError):
            sam3d_mesh.render_mesh_video('unused', 'unused', 'unused', first_frame=0,
                                        last_frame=1, stride=1, source_sha256='unused', model={})
        opened.assert_not_called()


if __name__ == '__main__':
    unittest.main()
