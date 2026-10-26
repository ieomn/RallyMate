from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
import subprocess
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

from rallymate_vision.owned_subprocess import OwnedProcess


class OwnedProcessPortableTests(unittest.TestCase):
    def process(self):
        owned = OwnedProcess.__new__(OwnedProcess)
        owned.job, owned.closed = None, False
        owned.process = MagicMock(pid=12345)
        owned.process.poll.return_value = 0
        return owned

    def test_exited_posix_leader_still_cleans_descendant_group(self):
        owned = self.process()
        with patch("rallymate_vision.owned_subprocess.os.killpg", create=True) as kill, \
                patch("rallymate_vision.owned_subprocess.signal.SIGKILL", 9, create=True):
            owned.close()
            owned.close()
        kill.assert_called_once_with(12345, 9)
        owned.process.wait.assert_called_once_with(timeout=5)

    def test_disappeared_posix_group_is_successful_cleanup(self):
        owned = self.process()
        with patch("rallymate_vision.owned_subprocess.os.killpg", create=True, side_effect=ProcessLookupError), \
                patch("rallymate_vision.owned_subprocess.signal.SIGKILL", 9, create=True):
            owned.close()
        self.assertTrue(owned.closed)

    @unittest.skipUnless(os.name == "nt", "Windows constructor cleanup path")
    def test_failed_assignment_closes_job_even_if_direct_wait_also_fails(self):
        job, process = MagicMock(), MagicMock()
        job.assign_and_resume.side_effect = OSError("assignment failed")
        process.poll.return_value = None
        process.wait.side_effect = subprocess.TimeoutExpired("child", 5)
        with patch("rallymate_vision.owned_subprocess._WindowsJob", return_value=job), \
                patch("rallymate_vision.owned_subprocess.subprocess.Popen", return_value=process):
            with self.assertRaises(subprocess.TimeoutExpired):
                OwnedProcess(["test-only"])
        job.close.assert_called_once()

    @unittest.skipUnless(os.name == "posix", "POSIX process group integration test")
    def test_actual_exited_launcher_does_not_leave_writing_descendant(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            heartbeat = directory / "heartbeat.txt"
            child = directory / "child.py"
            child.write_text("import time,sys\nfrom pathlib import Path\n"
                             "p=Path(sys.argv[1])\n"
                             "for i in range(1000):\n p.write_text(str(i))\n time.sleep(.02)\n", encoding="utf-8")
            parent = directory / "parent.py"
            parent.write_text("import subprocess,sys\n"
                              "subprocess.Popen([sys.executable,sys.argv[1],sys.argv[2]])\n", encoding="utf-8")
            process = OwnedProcess([sys.executable, str(parent), str(child), str(heartbeat)])
            try:
                process.process.wait(timeout=5)
                deadline = time.monotonic()+5
                while not heartbeat.is_file():
                    if time.monotonic() > deadline:
                        self.fail("descendant did not start")
                    time.sleep(.02)
                process.close()
                time.sleep(.1)
                before = heartbeat.read_text()
                time.sleep(.15)
                self.assertEqual(heartbeat.read_text(), before)
            finally:
                process.close()


@unittest.skipUnless(os.name == "nt", "Windows job-object lifecycle test")
class OwnedSubprocessTests(unittest.TestCase):
    def test_actual_launcher_and_descendant_tree_ends_before_stop_returns(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            pid_file = directory / "child.json"
            child = directory / "child.py"
            child.write_text("import os,json,time,sys\nfrom pathlib import Path\n"
                             "Path(sys.argv[1]).write_text(json.dumps({'pid':os.getpid()}))\n"
                             "time.sleep(30)\n", encoding="utf-8")
            parent = directory / "parent.py"
            parent.write_text("import subprocess,sys,time\n"
                              "subprocess.Popen([sys.executable,sys.argv[1],sys.argv[2]],creationflags=subprocess.CREATE_NO_WINDOW)\n"
                              "time.sleep(30)\n", encoding="utf-8")
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel.OpenProcess.restype = wintypes.HANDLE
            kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            kernel.WaitForSingleObject.restype = wintypes.DWORD
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            process = OwnedProcess([sys.executable, str(parent), str(child), str(pid_file)])
            handle = None
            try:
                deadline = time.monotonic() + 5
                while not pid_file.is_file():
                    if time.monotonic() >= deadline:
                        self.fail("owned descendant did not start")
                    time.sleep(.02)
                descendant = json.loads(pid_file.read_text())["pid"]
                handle = kernel.OpenProcess(0x00100000, False, descendant)
                self.assertTrue(handle)
                self.assertEqual(kernel.WaitForSingleObject(handle, 0), 258)
                self.assertGreaterEqual(process.job.active(), 2)
                process.stop()
                self.assertEqual(process.job.active(), 0)
                self.assertEqual(kernel.WaitForSingleObject(handle, 0), 0)
                self.assertIsNotNone(process.poll())
            finally:
                process.close()
                process.close()  # Closing is idempotent.
                if handle:
                    kernel.CloseHandle(handle)


if __name__ == "__main__":
    unittest.main()
