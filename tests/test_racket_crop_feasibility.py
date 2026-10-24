"""Source-bound development observation replay; synthetic cases are not videos."""
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from replay_racket_crop_feasibility import VERSION, merge_crop_observations, replay


def example():
    original, primary, supplemental, timeline = [], [], [], []
    for i in range(3):
        original.append({"frame": {"index": i, "processed_index": i + 30, "timestamp_ms": i * 40,
            "timestamp_source": "decoder_pts", "width": 640, "height": 480}, "detections": []})
        primary.append({"source_frame_index": i, "processed_index": i + 30, "timestamp_ms": i * 40,
            "source_track_id": 2, "selection_status": "selected", "bbox_px": [90, 90, 110, 110]})
        timeline.append({"frame_index": i, "pts": i, "time_base_num": 1, "time_base_den": 25})
        supplemental.append({"source_frame_index": i, "processed_index": i + 30, "source_timestamp_ms": i * 40,
            "source_track_id": 2, "pts": i, "time_base_num": 1, "time_base_den": 25,
            "original_primary_bbox_px": [90, 90, 110, 110], "status": "inferred", "crop_bbox_px": [80, 80, 120, 120],
            "detections": [{"source": VERSION, "class_id": 38, "class_name": "racket", "confidence": .5,
                "crop_bbox_px": [80, 80, 120, 120], "crop_local_bbox_px": [5, 5, 15, 15], "bbox_px": [85, 85, 95, 95]}]})
    return original, primary, supplemental, timeline


class RacketCropFeasibilityTests(unittest.TestCase):
    def test_nonzero_processed_origin_and_inputs_unchanged(self):
        values = example(); before = deepcopy(values)
        output = merge_crop_observations(*values)
        self.assertEqual(values, before)
        self.assertTrue(all(len(row["detections"]) == 1 for row in output))
        output[0]["detections"][0]["bbox_px"][0] = 0
        self.assertEqual(values, before)

    def test_empty_frames_still_preserve_denominator(self):
        values = example(); values[2][1]["detections"] = []
        result = merge_crop_observations(*values)
        self.assertEqual(len(result), 3)
        self.assertEqual(result[1]["detections"], [])

    def test_frame_clock_identity_and_mapping_mutations_rejected(self):
        edits = [lambda v: v[2][1].update(pts=7), lambda v: v[1][1].update(identity_ambiguous=True),
            lambda v: v[2][1].update(source_track_id=9), lambda v: v[2][1].update(crop_bbox_px=[0, 0, 40, 40]),
            lambda v: v[2][1]["detections"][0].update(bbox_px=[85, 85, 96, 95]),
            lambda v: v[2][1]["detections"][0].update(confidence=float("nan")),
            lambda v: v[2][1]["detections"][0].update(class_id=True),
            lambda v: v[2].pop(), lambda v: v[3].append(deepcopy(v[3][0])),
            lambda v: v[0][1]["frame"].update(timestamp_source="fps_fallback")]
        for edit in edits:
            with self.subTest(edit=repr(edit)):
                values = example(); edit(values)
                with self.assertRaises(ValueError): merge_crop_observations(*values)

    def test_coherent_repeated_processed_index_and_bool_clock_rejected(self):
        values = example()
        values[0][1]["frame"]["processed_index"] = values[1][1]["processed_index"] = values[2][1]["processed_index"] = 30
        with self.assertRaises(ValueError): merge_crop_observations(*values)
        for table, field in ((1, "source_frame_index"), (2, "pts"), (2, "source_frame_index"), (3, "frame_index")):
            values = example(); values[table][1][field] = True
            with self.assertRaises(ValueError): merge_crop_observations(*values)

    def test_frozen_full_interval_and_failure_are_preserved(self):
        evidence = json.loads((ROOT / "tests/fixtures/racket-crop-development-atp-20261024.json").read_text(encoding="utf-8"))
        rows = evidence["supplemental_observations"]
        self.assertEqual([r["source_frame_index"] for r in rows], list(range(200, 775)))
        self.assertEqual(sum(bool(r["detections"]) for r in rows), 70)
        self.assertEqual(sum(len(r["detections"]) for r in rows), 73)
        self.assertEqual(evidence["expected"]["missed_actions"], 8)
        self.assertEqual(evidence["expected"]["crop_matches"], 0)

    def test_missing_required_raw_digests_rejected_before_reading_inputs(self):
        evidence = json.loads((ROOT / "tests/fixtures/racket-crop-development-atp-20261024.json").read_text(encoding="utf-8"))
        evidence["plan"]["source_inputs"] = {}
        with self.assertRaisesRegex(ValueError, "required_input_digests_missing"): replay(evidence, ROOT)

    @unittest.skipUnless(os.environ.get("CARD_ARTIFACT_ROOT"), "original video and raw observations required")
    def test_actual_original_video_and_raw_observation_replay(self):
        evidence = json.loads((ROOT / "tests/fixtures/racket-crop-development-atp-20261024.json").read_text(encoding="utf-8"))
        result = replay(evidence, Path(os.environ["CARD_ARTIFACT_ROOT"]))
        self.assertEqual(result["source_frame_count"], 575)
        self.assertEqual(result["supplemental_racket_boxes"], 73)
        for name in ("baseline", "crop"):
            self.assertEqual(result[name]["matching"]["matched_count"], 0)
            self.assertEqual(result[name]["matching"]["missed_count"], 8)
            self.assertIsNone(result[name]["matching"]["precision"])
        self.assertTrue(result["original_inputs_unchanged"])
        self.assertIsNone(result["score"])


if __name__ == "__main__": unittest.main()
