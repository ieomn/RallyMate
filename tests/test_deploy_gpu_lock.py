"""Regression: the POSIX GPU flock must live in the shared node state dir.

In Docker each container has a private /tmp tmpfs, so a lock under
tempfile.gettempdir() let two containers (or replicas) both "own" one physical
GPU. The lock now lives at <gpu-state>/locks/<uuid>.lock, i.e. on the
/var/lib/rallymate/gpu-state volume that compose mounts into the worker.
"""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from rallymate_service import gpu_fleet
from rallymate_service.gpu_fleet import GPUWorkerSlot, gpu_lock_path, node_gpu_state_root
from rallymate_service.mesh_render_worker import RenderLock

ROOT = Path(__file__).resolve().parents[1]
GPU_ID = 'GPU-00000000-0000-0000-0000-0000000000AB'


class _PosixOs:
    """Route gpu_fleet down its POSIX branch on any host; everything else is real os."""
    name = 'posix'

    def __getattr__(self, attribute):
        return getattr(os, attribute)


class GPULockLocationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.state = Path(temporary.name).resolve()

    def test_lock_path_is_inside_the_state_directory_not_tempdir(self):
        with patch('tempfile.gettempdir', side_effect=AssertionError('temp dir must not be used')):
            path = gpu_lock_path(GPU_ID, self.state)
        self.assertEqual(self.state / 'locks' / (GPU_ID.lower() + '.lock'), path)
        self.assertTrue(path.parent.is_dir())
        # Lock files never collide with the *.json ownership journal that
        # executor_runtime globs for crash recovery.
        self.assertEqual([], list(self.state.glob('*.json')))

    def test_default_lock_follows_node_state_root(self):
        with patch('rallymate_service.gpu_fleet.node_gpu_state_root', return_value=self.state):
            self.assertEqual(self.state / 'locks' / (GPU_ID.lower() + '.lock'), gpu_lock_path(GPU_ID))

    def test_container_default_state_root_is_the_compose_volume(self):
        if os.name == 'nt':
            self.assertEqual('gpu-state', node_gpu_state_root().name)
        else:
            self.assertEqual(Path('/var/lib/rallymate/gpu-state'), node_gpu_state_root())
        compose = (ROOT / 'deploy/docker-compose.yml').read_text(encoding='utf-8')
        self.assertIn(':/var/lib/rallymate/gpu-state\n', compose)
        self.assertIn('/var/lib/rallymate/gpu-state', (ROOT / 'deploy/Dockerfile').read_text(encoding='utf-8'))

    def test_posix_slot_holds_the_shared_lock_file_and_excludes_other_holders(self):
        with patch.object(gpu_fleet, 'os', _PosixOs()), \
                patch('tempfile.gettempdir', side_effect=AssertionError('temp dir must not be used')):
            slot = GPUWorkerSlot(self.state, GPU_ID, state_directory=self.state)
            self.assertIsInstance(slot.lock, RenderLock)
            self.assertEqual(self.state / 'locks' / (GPU_ID.lower() + '.lock'), slot.lock.path)
            self.assertTrue(slot.acquire())
            try:
                # A second process/container opening the same shared file is refused.
                rival = RenderLock(slot.lock.path)
                self.assertFalse(rival.acquire())
            finally:
                slot.close()
            rival = RenderLock(slot.lock.path)
            self.assertTrue(rival.acquire())
            rival.close()

    def test_source_no_longer_references_tempdir_locks(self):
        source = (ROOT / 'src/rallymate_service/gpu_fleet.py').read_text(encoding='utf-8')
        self.assertNotIn('rallymate-gpu-worker-locks', source)
        self.assertNotIn('import tempfile', source)


if __name__ == '__main__':
    unittest.main()
