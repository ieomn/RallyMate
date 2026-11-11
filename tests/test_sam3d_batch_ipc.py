"""Real CPU worker checks for bounded SAM batch pixel transport and ownership."""
import hashlib
from multiprocessing import shared_memory
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import numpy as np

from rallymate_vision import sam3d_runner as runner
from rallymate_vision.owned_subprocess import OwnedProcess


ROOT = Path(__file__).resolve().parents[1]
SAM_PYTHON = ROOT / "runtime/sam3d/.venv311/Scripts/python.exe"
ECHO = Path(__file__).with_name("fixtures") / "sam-batch-ipc-echo-worker.py"


def item(marker=1, shape=(16, 20, 3), subjects=1):
    frame = np.full(shape, marker, dtype=np.uint8)[:, ::-1]
    return {"frame": frame, "boxes": [[1. + i, 2., 9. + i, 11.] for i in range(subjects)]}


class BatchBoundsTests(unittest.TestCase):
    def test_frame_count_pixels_and_total_bytes_rejected_before_transport(self):
        self.assertEqual(runner.MAX_BATCH_FRAMES, 8)
        self.assertEqual(runner.MAX_BATCH_SOURCE_BYTES, 256 * 1024 * 1024)
        huge_view = np.broadcast_to(np.uint8(1), (10240, 10240, 3))
        cases = ([item() for _ in range(9)], [{"frame": huge_view, "boxes": []}],
                 [{"frame": np.zeros((8, 8, 3), dtype=np.float32), "boxes": []}],
                 [item(subjects=5)], [dict(item(), boxes=[[1., 2., 999., 11.]])])
        for requests in cases:
            with self.subTest(kind=str(type(requests))), self.assertRaises(ValueError):
                runner._validate_frame_requests(requests)

    def test_eight_4k_frames_fit_the_same_transport_bound_as_staging(self):
        frame = np.broadcast_to(np.uint8(1), (2160, 3840, 3))
        runner._validate_frame_requests([{"frame": frame, "boxes": []} for _ in range(8)])
        with self.assertRaisesRegex(ValueError, "256 MiB"):
            runner._validate_frame_requests([{"frame": frame, "boxes": []},
                {"frame": np.broadcast_to(np.uint8(1), (8192, 10240, 3)), "boxes": []}])


@unittest.skipUnless(os.name == "nt" and SAM_PYTHON.exists(), "Windows SAM Python 3.11 runtime required")
class RealBatchProcessTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.env = patch.dict(os.environ, {"RALLYMATE_SAM3D_IPC_TRANSPORT": "shared_memory",
                                          "RALLYMATE_SAM3D_FRAME_TIMEOUT": "10"})
        self.env.start()
        self.addCleanup(self.env.stop)
        def spawn(arguments, **kwargs):
            return OwnedProcess([arguments[0], "-u", str(ECHO), *arguments[4:]], **kwargs)
        self.launch = patch.object(runner, "OwnedProcess", side_effect=spawn)
        self.launch.start()
        self.addCleanup(self.launch.stop)
        self.workers = []
        self.addCleanup(self.close_workers)
        self.process = self.new_worker()

    def new_worker(self, transport="shared_memory"):
        with patch.dict(os.environ, {"RALLYMATE_SAM3D_IPC_TRANSPORT": transport}):
            process = runner.Sam3DProcess(SAM_PYTHON, Path("unused"),
                runtime_dir=Path(self.directory.name), device="0", inference_type="body")
        self.workers.append(process)
        return process

    def close_workers(self):
        for process in self.workers:
            process.close()

    def names(self):
        return [writer.mapping.name for writer in [self.process.shared_writer, *self.process.batch_writers]
                if writer is not None and writer.mapping is not None]

    def assert_closed(self, names=()):
        self.assertTrue(self.process.owner.closed)
        self.assertIsNotNone(self.process.process.poll())
        for writer in [self.process.shared_writer, *self.process.batch_writers]:
            if writer is not None:
                self.assertIsNone(writer.mapping)
        for name in names:
            with self.assertRaises(FileNotFoundError):
                shared_memory.SharedMemory(name=name, create=False)

    def assert_outputs(self, requests, outputs):
        self.assertEqual(len(outputs), len(requests))
        for source, rows in zip(requests, outputs):
            self.assertEqual(len(rows), len(source["boxes"]))
            for index, (row, box) in enumerate(zip(rows, source["boxes"])):
                self.assertEqual(row["sha256"], hashlib.sha256(source["frame"].tobytes()).hexdigest())
                self.assertEqual(row["shape"], list(source["frame"].shape))
                self.assertEqual(row["box"], box)
                self.assertEqual(row["subject_index"], index)
                self.assertIn("worker_load_source_array_ms", row["ipc_request_timing_ms"])
                self.assertIn("do_not_sum_across_frames_or_subjects", row["ipc_timing_scope"])
                self.assertEqual(row["ipc_request_identity"], {"version": "sam-ipc-request-v1",
                    "session_id": self.process.session_id, "request_id": self.process.request_id})

    def test_mixed_subject_slots_and_variable_shapes_do_not_overwrite_pixels(self):
        inputs = [item(1, subjects=0), item(2, (24, 32, 3), 1), item(3, (33, 45, 3), 2)]
        self.assert_outputs(inputs, self.process.infer_batch(inputs))
        names = self.names()
        self.assertEqual(len(names), 3)
        self.assertEqual(len(set(names)), 3)
        for index, source in enumerate(inputs):
            source["frame"].fill(40 + index)
        smaller = [item(8, (16, 18, 3), 2), item(9, (18, 24, 3), 0)]
        self.assert_outputs(smaller, self.process.infer_batch(smaller))
        self.assertEqual(self.names(), names[:2])
        with self.assertRaises(FileNotFoundError):
            shared_memory.SharedMemory(name=names[2], create=False)
        larger = [item(11, (32, 48, 3), 1), item(12, (48, 64, 3), 1), item(13, (64, 96, 3), 2)]
        self.assert_outputs(larger, self.process.infer_batch(larger))
        self.assertTrue(all(old != new for old, new in zip(names, self.names())))
        final_names = self.names()
        self.process.close()
        self.assert_closed(final_names)

    def test_retained_slot_pool_is_bounded_across_alternating_shapes(self):
        prior_names = []
        with patch.object(runner, "MAX_BATCH_SOURCE_BYTES", 1024):
            for large_index in range(8):
                inputs = [{"frame": np.full((15, 16, 3) if i == large_index else (3, 3, 3),
                                             i + 1, np.uint8), "boxes": []}
                          for i in range(8)]
                self.assertEqual(self.process.infer_batch(inputs), [[] for _ in range(8)])
                self.assertLessEqual(sum(writer.capacity for writer in self.process.batch_writers), 1024)
                # The old enlarged slot plus the newly enlarged slot would
                # exceed the total pool budget. Both parent and child handles
                # must be gone after the next acknowledged request.
                current_names = self.names()
                self.assertFalse(set(prior_names) & set(current_names))
                for name in prior_names:
                    with self.assertRaises(FileNotFoundError):
                        shared_memory.SharedMemory(name=name, create=False)
                prior_names = current_names

    def test_single_batch_single_share_one_monotonic_request_sequence(self):
        source = item(4)
        self.process.infer(source["frame"], source["boxes"])
        single_name = self.process.shared_writer.mapping.name
        self.assertEqual(self.process.request_id, 1)
        self.assert_outputs([item(5), item(6, subjects=2)], self.process.infer_batch([item(5), item(6, subjects=2)]))
        self.assertEqual(self.process.request_id, 2)
        self.assertNotIn(single_name, [writer.mapping.name for writer in self.process.batch_writers])
        output = self.process.infer(source["frame"], source["boxes"])
        self.assertEqual(output[0]["sha256"], hashlib.sha256(source["frame"].tobytes()).hexdigest())
        self.assertEqual(self.process.request_id, 3)
        self.assertEqual(self.process.shared_writer.mapping.name, single_name)

    def test_validation_and_empty_batches_do_not_consume_wire_request_ids(self):
        self.assertEqual(self.process.infer_batch([]), [])
        with self.assertRaises(ValueError):
            self.process.infer_batch([dict(item(), boxes=[[0., 0., 999., 999.]])])
        self.assertEqual(self.process.request_id, 0)
        self.assertEqual(self.names(), [])
        self.assert_outputs([item()], self.process.infer_batch([item()]))
        self.assertEqual(self.process.request_id, 1)

    def test_npy_batch_retains_pixel_order_and_cleans_every_temporary_file(self):
        self.process.close()
        self.process = self.new_worker("npy")
        inputs = [item(4), item(7, (24, 38, 3), 2), item(9, subjects=0)]
        self.assert_outputs(inputs, self.process.infer_batch(inputs))
        self.assertIsNone(self.process.shared_writer)
        self.assertEqual(self.process.batch_writers, [])
        self.assertEqual(list(self.process.ipc_dir.iterdir()), [])

    def test_worker_exception_or_malformed_nested_cardinality_closes_all_slots(self):
        for index, marker in enumerate((254, 250, 249, 248)):
            with self.subTest(marker=marker):
                if index:
                    self.process = self.new_worker()
                self.process.infer(item(1)["frame"], item(1)["boxes"])
                names = self.names()
                with self.assertRaisesRegex(RuntimeError, "protocol_error"):
                    self.process.infer_batch([item(marker), item(2, subjects=2)])
                self.assert_closed(names)
                with self.assertRaisesRegex(RuntimeError, "no longer running"):
                    self.process.infer_batch([item()])

    def test_malformed_response_protocol_timing_and_cardinality_close_worker(self):
        changes = ({"batch_protocol": "future-version"}, {"batch_protocol": None},
                   {"outputs": None}, {"outputs": [[]]}, {"outputs": [[{}], [42]]},
                   {"transport_timing": []})
        for index, fields in enumerate(changes):
            with self.subTest(fields=fields):
                if index:
                    self.process = self.new_worker()
                original_receive = self.process._receive
                def malformed(timeout):
                    result = original_receive(timeout)
                    return {**result, **fields}
                with patch.object(self.process, "_receive", side_effect=malformed):
                    with self.assertRaisesRegex(RuntimeError, "protocol_error"):
                        self.process.infer_batch([item(1), item(2)])
                self.assert_closed()

    def test_wrong_or_boolean_ack_id_cannot_authorize_buffer_reuse(self):
        for index, request_id in enumerate((999, True)):
            with self.subTest(request_id=request_id):
                if index:
                    self.process = self.new_worker()
                original_receive = self.process._receive
                def malformed(timeout):
                    return {**original_receive(timeout), "request_id": request_id}
                with patch.object(self.process, "_receive", side_effect=malformed):
                    with self.assertRaisesRegex(RuntimeError, "does not match"):
                        self.process.infer_batch([item(1), item(2)])
                self.assert_closed()

    def test_timeout_terminates_worker_before_releasing_all_slots(self):
        with patch.dict(os.environ, {"RALLYMATE_SAM3D_FRAME_TIMEOUT": "0.1"}):
            with self.assertRaisesRegex(RuntimeError, "timeout"):
                self.process.infer_batch([item(253), item(3, subjects=2)])
        self.assert_closed()

    def test_external_close_interrupts_blocked_batch_without_deadlock(self):
        failures = []
        def infer():
            try:
                self.process.infer_batch([item(253), item(3, subjects=2)])
            except RuntimeError as error:
                failures.append(str(error))
        thread = threading.Thread(target=infer)
        thread.start()
        deadline = time.monotonic() + 3
        while len(self.process.batch_writers) < 2 and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertEqual(len(self.process.batch_writers), 2)
        names = self.names()
        self.process.close()
        thread.join(timeout=3)
        self.assertFalse(thread.is_alive())
        self.assertTrue(failures)
        self.assert_closed(names)


if __name__ == "__main__":
    unittest.main()
