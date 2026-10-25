"""Source-pixel parity, bounded lifetime and actual cross-interpreter IPC."""
import hashlib
from multiprocessing import shared_memory
import os
from pathlib import Path
import queue
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from rallymate_vision import sam3d_runner as runner
from rallymate_vision.owned_subprocess import OwnedProcess
from rallymate_vision.sam3d_ipc import (
    SharedFrameReader, SharedFrameWriter, configured_transport, transport_metadata,
)


ROOT = Path(__file__).resolve().parents[1]
SAM_PYTHON = ROOT / 'runtime/sam3d/.venv311/Scripts/python.exe'
ECHO = Path(__file__).with_name('fixtures') / 'sam-ipc-echo-worker.py'


class SharedFrameTests(unittest.TestCase):
    def test_byte_parity_noncontiguous_reuse_and_resize(self):
        writer, reader = SharedFrameWriter(), SharedFrameReader()
        names = []
        try:
            for shape in ((31, 41, 3), (19, 23, 3), (65, 73, 3)):
                image = np.arange(np.prod(shape), dtype=np.uint8).reshape(shape)[:, ::-1]
                descriptor = writer.write(image)
                names.append(descriptor['name'])
                received = reader.read(descriptor)
                self.assertEqual(received.tobytes(), image.tobytes())
                self.assertFalse(received.flags.writeable)
                del received
            self.assertEqual(names[0], names[1])
            self.assertNotEqual(names[1], names[2])
        finally:
            reader.close()
            writer.close()
        with self.assertRaises(FileNotFoundError):
            shared_memory.SharedMemory(name=names[-1], create=False)

    def test_invalid_descriptors_and_pixels_are_rejected(self):
        writer, reader = SharedFrameWriter(), SharedFrameReader()
        try:
            with self.assertRaises(ValueError):
                writer.write(np.zeros((3, 4, 3), dtype=np.float32))
            descriptor = writer.write(np.zeros((3, 4, 3), dtype=np.uint8))
            for changed in ({'dtype': 'float32'}, {'nbytes': 10}, {'shape': [3, 4, 4]},
                            {'shape': [True, 4, 3]}, {'protocol': 'unknown'},
                            {'shape': [999999, 999999, 3]}, {'name': None}):
                with self.subTest(changed=changed), self.assertRaises(ValueError):
                    reader.read({**descriptor, **changed})
            with self.assertRaises(ValueError):
                # Windows rounds the tiny mapping to an OS page (4096 bytes).
                reader.read({**descriptor, 'shape': [60, 60, 3], 'nbytes': 10800})
        finally:
            reader.close()
            writer.close()

    def test_default_remains_npy_and_invalid_policy_fails(self):
        self.assertEqual(configured_transport({}), 'npy')
        with self.assertRaises(ValueError):
            configured_transport({'RALLYMATE_SAM3D_IPC_TRANSPORT': 'automatic'})
        self.assertEqual(transport_metadata('shared_memory')['pixels'], 'exact_uint8_bgr_no_resizing')


class ResponseStructureTests(unittest.TestCase):
    def test_reader_rejects_json_arrays_scalars_and_null(self):
        for line in ('[]\n', '"text"\n', '42\n', 'true\n', 'null\n'):
            with self.subTest(line=line):
                process = runner.Sam3DProcess.__new__(runner.Sam3DProcess)
                process.process = SimpleNamespace(stdout=[line])
                process.messages = queue.Queue()
                process._read()
                self.assertEqual(process.messages.get_nowait(), {'status': 'protocol_error'})
                self.assertEqual(process.messages.get_nowait(), {'status': 'worker_exited'})

    def test_receive_defensively_closes_before_rejecting_non_object(self):
        for value in ([], 'text', 42, True, None):
            with self.subTest(value=value):
                process = runner.Sam3DProcess.__new__(runner.Sam3DProcess)
                process.messages = queue.Queue()
                process.messages.put(value)
                process.close = Mock()
                with self.assertRaisesRegex(RuntimeError, 'protocol_error'):
                    process._receive(.1)
                process.close.assert_called_once_with()


@unittest.skipUnless(os.name == 'nt' and SAM_PYTHON.exists(), 'Windows SAM Python 3.11 runtime required')
class RealProcessTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'RALLYMATE_SAM3D_IPC_TRANSPORT': 'shared_memory',
                                         'RALLYMATE_SAM3D_FRAME_TIMEOUT': '10'})
        self.env.start()
        def spawn(arguments, **kwargs):
            # Keep the real cross-version parent, owned child tree, pipes and
            # _serve; only replace the model with a CPU byte digest fixture.
            return OwnedProcess([arguments[0], '-u', str(ECHO), *arguments[4:]], **kwargs)
        self.launch = patch.object(runner, 'OwnedProcess', side_effect=spawn)
        self.launch.start()
        self.process = runner.Sam3DProcess(SAM_PYTHON, Path('unused'),
            runtime_dir=Path(self.directory.name), device='0', inference_type='body')

    def tearDown(self):
        self.process.close()
        self.launch.stop()
        self.env.stop()
        self.directory.cleanup()

    def test_actual_parent_child_byte_identity_and_reuse(self):
        names = []
        for shape in ((544, 960, 3), (240, 320, 3), (720, 1280, 3)):
            pixels = np.arange(np.prod(shape), dtype=np.uint8).reshape(shape)[:, ::-1]
            output = self.process.infer(pixels, [[1., 2., 3., 4.]])[0]
            self.assertEqual(output['sha256'], hashlib.sha256(pixels.tobytes()).hexdigest())
            self.assertEqual(output['shape'], list(shape))
            self.assertEqual(output['boxes'], [[1., 2., 3., 4.]])
            self.assertFalse(output['writeable'])
            self.assertEqual(output['ipc_transport']['kind'], 'shared_memory')
            self.assertIn('copy_source_array_ms', output['ipc_request_timing_ms'])
            names.append(self.process.shared_writer.mapping.name)
        self.assertEqual(names[0], names[1])
        self.assertNotEqual(names[1], names[2])
        self.assertEqual(list(self.process.ipc_dir.iterdir()), [])
        self.process.close()
        with self.assertRaises(FileNotFoundError):
            shared_memory.SharedMemory(name=names[-1], create=False)

    def test_legacy_npy_mode_retains_pixels_and_removes_temporary_file(self):
        self.process.close()
        with patch.dict(os.environ, {'RALLYMATE_SAM3D_IPC_TRANSPORT': 'npy'}):
            self.process = runner.Sam3DProcess(SAM_PYTHON, Path('unused'),
                runtime_dir=Path(self.directory.name), device='0', inference_type='body')
        pixels = np.arange(39 * 51 * 3, dtype=np.uint8).reshape(39, 51, 3)[:, ::-1]
        output = self.process.infer(pixels, [[1., 2., 3., 4.]])[0]
        self.assertEqual(output['sha256'], hashlib.sha256(pixels.tobytes()).hexdigest())
        self.assertEqual(output['ipc_transport']['kind'], 'npy')
        self.assertIn('save_source_array_ms', output['ipc_request_timing_ms'])
        self.assertIsNone(self.process.shared_writer)
        self.assertEqual(list(self.process.ipc_dir.iterdir()), [])

    def test_validation_failure_does_not_skip_request_sequence(self):
        with self.assertRaises(ValueError):
            self.process.infer(np.zeros((2, 3, 3), dtype=np.float32), [])
        self.assertEqual(self.process.request_id, 0)
        self.assertEqual(self.process.infer(np.zeros((2, 3, 3), dtype=np.uint8), [])[0]['shape'], [2, 3, 3])

    def test_worker_failure_closes_mapping_and_tree(self):
        with self.assertRaisesRegex(RuntimeError, 'request failed'):
            self.process.infer(np.zeros((2, 3, 3), dtype=np.uint8), [[-1]])
        self.assertIsNone(self.process.shared_writer.mapping)
        self.assertIsNotNone(self.process.process.poll())
        self.assertTrue(self.process.owner.closed)

    def test_mismatched_response_cannot_reuse_buffer(self):
        with patch.object(self.process, '_receive', return_value={'status': 'ok', 'request_id': 999, 'outputs': []}):
            with self.assertRaisesRegex(RuntimeError, 'does not match'):
                self.process.infer(np.zeros((2, 3, 3), dtype=np.uint8), [])
        self.assertIsNone(self.process.shared_writer.mapping)
        self.assertIsNotNone(self.process.process.poll())

    def test_malformed_success_outputs_close_before_buffer_reuse(self):
        variants = ({}, {'outputs': None}, {'outputs': {}}, {'outputs': [None]},
                    {'outputs': [42]}, {'outputs': [{}], 'transport_timing': []})
        for index, fields in enumerate(variants):
            with self.subTest(fields=fields):
                if index:
                    self.process = runner.Sam3DProcess(SAM_PYTHON, Path('unused'),
                        runtime_dir=Path(self.directory.name), device='0', inference_type='body')
                response = {'status': 'ok', 'request_id': 1, **fields}
                with patch.object(self.process, '_receive', return_value=response):
                    with self.assertRaisesRegex(RuntimeError, 'protocol_error'):
                        self.process.infer(np.zeros((2, 3, 3), dtype=np.uint8), [])
                self.assertIsNone(self.process.shared_writer.mapping)
                self.assertIsNotNone(self.process.process.poll())
                self.assertTrue(self.process.owner.closed)
                with self.assertRaisesRegex(RuntimeError, 'no longer running'):
                    self.process.infer(np.zeros((2, 3, 3), dtype=np.uint8), [])

    def test_boolean_response_id_is_not_an_integer_acknowledgement(self):
        response = {'status': 'ok', 'request_id': True, 'outputs': []}
        with patch.object(self.process, '_receive', return_value=response):
            with self.assertRaisesRegex(RuntimeError, 'does not match'):
                self.process.infer(np.zeros((2, 3, 3), dtype=np.uint8), [])
        self.assertIsNone(self.process.shared_writer.mapping)
        self.assertIsNotNone(self.process.process.poll())

    def test_timeout_closes_mapping_and_tree(self):
        with patch.dict(os.environ, {'RALLYMATE_SAM3D_FRAME_TIMEOUT': '0.1'}):
            with self.assertRaisesRegex(RuntimeError, 'timeout'):
                self.process.infer(np.zeros((2, 3, 3), dtype=np.uint8), [[-2]])
        self.assertIsNone(self.process.shared_writer.mapping)
        self.assertIsNotNone(self.process.process.poll())

    def test_external_close_interrupts_blocked_request_without_deadlock(self):
        failures = []
        def infer():
            try:
                self.process.infer(np.zeros((2, 3, 3), dtype=np.uint8), [[-2]])
            except RuntimeError as error:
                failures.append(str(error))
        thread = threading.Thread(target=infer)
        thread.start()
        deadline = time.monotonic() + 3
        while self.process.shared_writer.mapping is None and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertIsNotNone(self.process.shared_writer.mapping)
        self.process.close()
        thread.join(timeout=3)
        self.assertFalse(thread.is_alive())
        self.assertTrue(failures)
        self.assertIsNone(self.process.shared_writer.mapping)


if __name__ == '__main__':
    unittest.main()
