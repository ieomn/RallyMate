"""Check the policy crosses the real process boundary before model loading."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch

from rallymate_vision import sam3d_runner as runner


class SamCpuProcessTests(unittest.TestCase):
    def test_child_environment_is_prepared_without_changing_parent(self):
        child = MagicMock()
        child.poll.return_value = 0
        child.stdout.__iter__.return_value = iter(())
        parent = {"RALLYMATE_CPU_THREADS": "4", "RALLYMATE_SAM3D_CPU_THREADS": "1",
                  "OMP_THREAD_LIMIT": "20", "NUMEXPR_MAX_THREADS": "8"}
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, parent, clear=True), \
                patch.object(runner, "OwnedProcess", return_value=SimpleNamespace(process=child, close=Mock())) as spawn, \
                patch.object(runner.Sam3DProcess, "_receive", return_value={"status": "ready", "metadata": {}}):
            process = runner.Sam3DProcess(Path("python"), Path("checkpoint"),
                runtime_dir=Path(directory), device="0", inference_type="body")
            process.close()
            env = spawn.call_args.kwargs["env"]
            self.assertEqual(env["OMP_THREAD_LIMIT"], "1")
            self.assertEqual(env["NUMEXPR_MAX_THREADS"], "1")
            self.assertEqual(env["OMP_WAIT_POLICY"], "PASSIVE")
            self.assertEqual(env["KMP_BLOCKTIME"], "0")
            self.assertEqual(env["RALLYMATE_SAM3D_ISOLATED_WORKER"], "1")
            self.assertEqual(os.environ["OMP_THREAD_LIMIT"], "20")
            self.assertNotIn("OMP_WAIT_POLICY", os.environ)

    def test_server_applies_policy_before_loading_and_reports_effective_pools(self):
        events = []
        torch = SimpleNamespace(get_num_threads=lambda: 1, get_num_interop_threads=lambda: 1)
        cv2 = SimpleNamespace(getNumThreads=lambda: 1)
        engine = SimpleNamespace(metadata={"cpu_threads": {"logical_cpu_count": 20}}, close=Mock())
        args = SimpleNamespace(checkpoint=Path("checkpoint"), runtime_dir=Path("runtime"),
                               device="0", inference_type="body")
        capture = io.StringIO()
        with patch.dict(sys.modules, {"torch": torch, "cv2": cv2}), \
                patch.dict(os.environ, {"OMP_WAIT_POLICY": "PASSIVE", "KMP_BLOCKTIME": "0"}), \
                patch.object(runner, "configure_sam_cpu_pools",
                             side_effect=lambda *a: events.append("policy") or {"requested_intraop": 1}), \
                patch.object(runner, "Sam3DEngine", side_effect=lambda *a, **kw: events.append("model") or engine), \
                patch.object(sys, "stdin", io.StringIO("")), contextlib.redirect_stdout(capture):
            self.assertEqual(runner._serve(args), 0)
        self.assertEqual(events, ["policy", "model"])
        receipt = json.loads(capture.getvalue())["metadata"]["cpu_threads"]
        self.assertEqual(receipt["logical_cpu_count"], 20)
        self.assertEqual([receipt[k] for k in ("torch_intraop", "torch_interop", "opencv")], [1, 1, 1])
        self.assertEqual(receipt["omp_wait_policy"], "PASSIVE")
        engine.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
