from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import errno
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from rallymate_service import uploads


class LocalUploadLockTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "session.lock"

    def test_local_timeout_and_body_error_release_references(self):
        with uploads._local_file_lock(self.path, time.monotonic() + 1):
            with self.assertRaises(TimeoutError):
                with uploads._local_file_lock(self.path, time.monotonic() + 0.01):
                    self.fail("recursive acquisition must time out")
            self.assertEqual(len(uploads._local_locks), 1)
        self.assertEqual(uploads._local_locks, {})
        with self.assertRaisesRegex(RuntimeError, "body failed"):
            with uploads._local_file_lock(self.path, time.monotonic() + 1):
                raise RuntimeError("body failed")
        self.assertEqual(uploads._local_locks, {})

    def test_local_waiter_keeps_entry_and_wakes_after_release(self):
        attempted, entered = threading.Event(), threading.Event()
        def contender():
            attempted.set()
            with uploads._local_file_lock(self.root / "." / self.path.name, time.monotonic() + 2):
                entered.set()
        with ThreadPoolExecutor(1) as executor:
            with uploads._local_file_lock(self.path, time.monotonic() + 2):
                future = executor.submit(contender)
                self.assertTrue(attempted.wait(1))
                self.assertFalse(entered.wait(0.02))
            future.result(timeout=2)
        self.assertTrue(entered.is_set())
        self.assertEqual(uploads._local_locks, {})


@unittest.skipUnless(os.name == "nt", "Windows upload lock contract")
class WindowsUploadLockTests(LocalUploadLockTests):
    def test_non_contention_error_does_not_retry(self):
        import msvcrt
        error = OSError(errno.EBADF, "bad handle")
        with patch.object(msvcrt, "locking", side_effect=error) as locking, \
             patch.object(uploads.time, "sleep") as sleep:
            with self.assertRaises(OSError) as raised:
                uploads._acquire_windows_lock(MagicMock(), time.monotonic() + 1)
        self.assertIs(raised.exception, error)
        self.assertEqual(locking.call_count, 1)
        sleep.assert_not_called()

    def test_contention_retries_only_until_shared_deadline(self):
        import msvcrt
        error = OSError(errno.EACCES, "locked")
        with patch.object(msvcrt, "locking", side_effect=error) as locking, \
             patch.object(uploads.time, "monotonic", side_effect=[0.1, 0.2, 1.1]), \
             patch.object(uploads.time, "sleep") as sleep:
            with self.assertRaises(OSError) as raised:
                uploads._acquire_windows_lock(MagicMock(), 1.0)
        self.assertIs(raised.exception, error)
        self.assertEqual(locking.call_count, 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [0.005, 0.01])

    def test_unlock_and_registry_cleanup_follow_body_error(self):
        import msvcrt
        original = msvcrt.locking
        with patch.object(msvcrt, "locking", wraps=original) as locking:
            with self.assertRaisesRegex(RuntimeError, "body failed"):
                with uploads.exclusive_file(self.path):
                    raise RuntimeError("body failed")
        self.assertEqual([call.args[1] for call in locking.call_args_list], [msvcrt.LK_NBLCK, msvcrt.LK_UNLCK])
        self.assertEqual(uploads._local_locks, {})
        with uploads.exclusive_file(self.path):
            pass

    def test_failed_open_and_failed_os_acquisition_release_registry(self):
        import msvcrt
        with patch.object(Path, "open", side_effect=OSError("open failed")):
            with self.assertRaises(OSError):
                with uploads.exclusive_file(self.path):
                    self.fail("must not enter")
        self.assertEqual(uploads._local_locks, {})
        with patch.object(msvcrt, "locking", side_effect=OSError(errno.EBADF, "bad handle")):
            with self.assertRaises(OSError):
                with uploads.exclusive_file(self.path):
                    self.fail("must not enter")
        self.assertEqual(uploads._local_locks, {})

    def test_other_process_still_excluded_and_can_acquire_after_release(self):
        program = """
import sys
from pathlib import Path
from rallymate_service import uploads
uploads.UPLOAD_LOCK_TIMEOUT_SECONDS = 0.05
try:
    with uploads.exclusive_file(Path(sys.argv[1])):
        print('acquired')
except OSError:
    print('blocked')
"""
        env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
        def child():
            result = subprocess.run([sys.executable, "-c", program, str(self.path)],
                                    env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            return result.stdout.strip()
        with uploads.exclusive_file(self.path):
            self.assertEqual(child(), "blocked")
        self.assertEqual(child(), "acquired")
        self.assertEqual(uploads._local_locks, {})


if __name__ == "__main__":
    unittest.main()
