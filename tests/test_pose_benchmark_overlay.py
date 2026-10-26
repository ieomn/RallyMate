import json
from pathlib import Path
import tempfile
import unittest

from scripts.render_pose_benchmark_overlay import BINDING, load_ledger


class OverlayBindingTests(unittest.TestCase):
    def test_wrong_frame_or_pixels_or_subject_cannot_be_overlaid_on_source(self):
        row = {key: index for index, key in enumerate(BINDING)}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.jsonl"
            path.write_text(json.dumps(row), encoding="utf-8")
            self.assertEqual(load_ledger(path, [row]), [row])
            for key in ("frame_index", "rgb_sha256", "bbox_px", "person_track_id", "pts"):
                wrong = dict(row, **{key: "changed"})
                path.write_text(json.dumps(wrong), encoding="utf-8")
                with self.subTest(key=key), self.assertRaises(ValueError):
                    load_ledger(path, [row])

    def test_missing_request_is_not_silently_dropped_in_review(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.jsonl"
            path.write_text("", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_ledger(path, [{key: 1 for key in BINDING}])
