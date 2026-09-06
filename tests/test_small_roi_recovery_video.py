from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_script():
    path = ROOT / "scripts" / "render_small_roi_recovery_comparison.py"
    spec = importlib.util.spec_from_file_location("render_small_roi_recovery_comparison", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SmallRoiRecoveryVideoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = _load_script()

    def test_event_ranges_merge_with_padding_and_preserve_codes(self) -> None:
        merged = self.module.merge_event_ranges(
            [
                {"event_id": "a", "event_code": "FS01", "start_ms": 100, "end_ms": 200},
                {"event_id": "b", "event_code": "FS02", "start_ms": 250, "end_ms": 300},
                {"event_id": "c", "event_code": "FS09", "start_ms": 1000, "end_ms": 1100},
            ],
            50,
        )
        self.assertEqual(2, len(merged))
        self.assertEqual(["a", "b"], merged[0]["event_ids"])
        self.assertEqual(["FS01", "FS02"], merged[0]["event_codes"])
        self.assertEqual(50, merged[0]["start_ms"])
        self.assertEqual(350, merged[0]["end_ms"])

    def test_explicit_ffmpeg_path_must_exist(self) -> None:
        with self.assertRaises(FileNotFoundError):
            self.module.resolve_ffmpeg(ROOT / "does-not-exist" / "ffmpeg.exe")

if __name__ == "__main__":
    unittest.main()
