"""A duplicated person's skeleton must not become a valid partial window.

Controlled mutations of recorded MIT data test source-window scope. Original
observations and their computed unaffected windows remain unchanged.
"""
from copy import deepcopy
import json
from pathlib import Path
import unittest
import tempfile
from unittest.mock import patch

from rallymate_service.gs_card_execution import compute_gs_card_execution


ROOT = Path(__file__).resolve().parents[1]
REASON = "selected_pose_person_association_conflict"


def instances(report):
    return {(row["event_id"], row["indicator_id"]): row
            for card in report["cards"] for row in card["instances"]}


def duplicate(record, source, *, ids, offset=0):
    """Inject explicitly synthetic duplicate identities with equal boxes."""
    points = source["keypoints"]
    box = [min(p["x_px"] for p in points), min(p["y_px"] for p in points),
           max(p["x_px"] for p in points), max(p["y_px"] for p in points)]
    for track in ids:
        pose = deepcopy(source)
        pose["person_track_id"] = track
        for point in pose["keypoints"]:
            point["x_px"] += offset
            if "x_normalized" in point:
                point["x_normalized"] = point["x_px"] / record["frame"]["width"]
        record["poses"] = [p for p in record["poses"] if p["person_track_id"] != track] + [pose]
        record["detections"] = [d for d in record.get("detections", [])
                                if not (d.get("class_name") == "player" and d.get("track_id") == track)]
        record["detections"].append({"class_name": "player", "track_id": track,
            "confidence": .9, "bbox_px": [box[0]+offset, box[1], box[2]+offset, box[3]]})


class CardPosePersonAssociationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.case = json.loads((ROOT / "tests/fixtures/gs-card-real-mit-preparation-v1.json").read_text(encoding="utf-8"))["cases"][0]
        cls.original = cls.compute(cls.case)

    @staticmethod
    def compute(case):
        return compute_gs_card_execution(case["frame_records"], case["primary_timeline"],
            case["decoder_timeline"], case["annotations"], source_job_id=case["source_job_id"])

    def target(self, case, *, inside=True):
        stage = case["annotations"]["stages"][0]
        return next(r for r in case["frame_records"] if r["poses"] and
            ((stage["start_ms"] <= r["frame"]["timestamp_ms"] <= stage["end_ms"])
             if inside else r["frame"]["timestamp_ms"] > stage["end_ms"] + 200)
            and all(any(p["name"] == name and p["confidence"] >= .35 for p in r["poses"][0]["keypoints"])
                    for name in ("left_shoulder", "right_shoulder", "left_hip", "right_hip", "left_ankle", "right_ankle")))

    def test_one_conflicted_frame_blocks_whole_selected_window_not_only_missing_subset(self):
        case = deepcopy(self.case)
        record = self.target(case)
        selected = next(p for p in record["poses"] if p["person_track_id"] == 1)
        duplicate(record, selected, ids=[1, 999])
        before = deepcopy(case)
        rows, original = instances(self.compute(case)), instances(self.original)
        affected = case["annotations"]["stages"][0]["annotation_id"]
        self.assertTrue(any(row["status"] == "scored" for key, row in original.items() if key[0] == affected))
        for key, row in rows.items():
            if key[0] != affected:
                self.assertEqual(row, original[key])
                continue
            self.assertEqual(row["status"], "unavailable")
            self.assertIsNone(row["score"])
            self.assertIn(REASON, row["reason_codes"])
            self.assertTrue(all(c["passed"] is None for c in row["conditions"]))
            ledger = row["source_stage_evidence"]["source_frame_bindings"]
            rejected = [r for r in ledger if r.get("rejected_pose_person_track_id") == 1]
            self.assertEqual([r["frame_index"] for r in rejected], [record["frame"]["index"]])
            self.assertIn(1, rejected[0]["pose_person_association"]["rejected_person_track_ids"])
            self.assertGreater((len(ledger)-1)/len(ledger), .7)
        self.assertEqual(case, before)

    def test_duplicate_unselected_people_do_not_block_selected_person(self):
        case = deepcopy(self.case)
        record = self.target(case)
        selected = next(p for p in record["poses"] if p["person_track_id"] == 1)
        duplicate(record, selected, ids=[998, 999], offset=100)
        self.assertEqual(instances(self.compute(case)), instances(self.original))

    def test_conflict_outside_all_annotated_windows_keeps_formal_scores(self):
        case = deepcopy(self.case)
        record = self.target(case, inside=False)
        self.assertTrue(all(not s["start_ms"] <= record["frame"]["timestamp_ms"] <= s["end_ms"]
                            for s in case["annotations"]["stages"]))
        selected = next(p for p in record["poses"] if p["person_track_id"] == 1)
        duplicate(record, selected, ids=[1, 999])
        self.assertEqual(instances(self.compute(case)), instances(self.original))

    def test_policy_revision_invalidates_finished_report_cache(self):
        from rallymate_service import card_execution as execution
        from rallymate_vision.pose import association
        from rallymate_scoring import temporal_card_evidence
        execution.clear_card_execution_cache()
        self.addCleanup(execution.clear_card_execution_cache)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / self.case["source_job_id"]
            directory.mkdir()
            for name, rows in (("frames.jsonl", self.case["frame_records"]),
                               ("primary-player.jsonl", self.case["primary_timeline"]), ("events.jsonl", [])):
                (directory/name).write_text("".join(json.dumps(r)+"\n" for r in rows), encoding="utf-8")
            original_reader = execution._read_records
            with patch.object(execution, "_read_records", wraps=original_reader) as reader:
                before = execution.build_card_execution_report(directory)
                self.assertEqual(reader.call_count, 3)
                self.assertEqual(execution.build_card_execution_report(directory), before)
                self.assertEqual(reader.call_count, 3)
                with patch.object(association, "POLICY_VERSION", association.POLICY_VERSION + "-changed"):
                    execution.build_card_execution_report(directory)
                self.assertEqual(reader.call_count, 6)
                with patch.object(temporal_card_evidence, "POSE_ASSOCIATION_GATE_VERSION",
                                  temporal_card_evidence.POSE_ASSOCIATION_GATE_VERSION + "-changed"):
                    execution.build_card_execution_report(directory)
                self.assertEqual(reader.call_count, 9)


if __name__ == "__main__":
    unittest.main()
