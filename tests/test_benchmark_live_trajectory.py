from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from scripts.benchmark_live_trajectory import read_complete_lines, run_benchmark
from tests.test_trajectory import _frame


class LiveTrajectoryBenchmarkTests(unittest.TestCase):
    def test_input_rows_are_complete_and_byte_bounded(self):
        with self.assertRaisesRegex(ValueError, "complete JSONL"):
            read_complete_lines(io.BytesIO(b'{"frame":'), 1, 1024)
        with self.assertRaisesRegex(ValueError, "byte budget"):
            read_complete_lines(io.BytesIO(b'{}\n'), 1, 2)
        with self.assertRaisesRegex(ValueError, "fewer lines"):
            read_complete_lines(io.BytesIO(b'{}\n'), 2, 1024)

    def test_scratch_appends_measure_actual_parse_counts_without_mutating_source(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source.jsonl"
            content = "".join(json.dumps(_frame(index, ball_x=.2 + .01 * index)) + "\n" for index in range(4)).encode()
            source.write_bytes(content)
            calls = []
            guard = SimpleNamespace(check=lambda: calls.append(True), receipt={"mode": "test_read_only"})
            with redirect_stdout(io.StringIO()):
                result = run_benchmark(source=source, output=root / "output", initial_lines=2,
                    append_lines=1, polls=2, max_input_bytes=10000, sample_limit=2, guard=guard)
            self.assertEqual(result["status"], "completed_exact_comparison")
            self.assertEqual(result["steady_state"]["full_parsed_lines"], 7)
            self.assertEqual(result["steady_state"]["incremental_parsed_lines"], 2)
            self.assertTrue(result["source_unchanged"])
            self.assertEqual(source.read_bytes(), content)
            self.assertEqual(list((root / "output").iterdir()), [root / "output/benchmark.json"])
            self.assertGreater(len(calls), 3)


if __name__ == "__main__":
    unittest.main()
