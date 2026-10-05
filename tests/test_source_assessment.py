from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from rallymate_scoring.source_assessment import ARTIFACT_NAME, ARTIFACT_VERSION, source_binding
from rallymate_service.source_assessment import load_source_aligned_assessment, project_source_assessment, SEMANTICS
from scripts.build_technical_review_rules import build, SOURCE, OUTPUT


def fixture(n=1):
    events, records = [], []
    for index in range(n):
        event_id = f"event-{index}"
        events.append({"event_id": event_id, "event_code": "FS01", "person_track_id": 1,
                       "video_id": "video", "start_ms": index * 100, "end_ms": (index + 1) * 100})
        records.append({"event_id": event_id, "video_id": "video", "person_track_id": 1, "indicators": [{
            "indicator_id": "FS01-M02", "source_rule_gate": {"pose_coverage": {
                "valid_frame_count": 7, "total_frame_count": 10, "required_joint_names": ["left_hip", "right_hip"]}},
            "source_measurements": [{"feature_name": "preload_hip_height_drop_body", "name_zh": "下降量", "value": .12,
                "unit": "body", "status": "measured", "valid": True, "reason": None,
                "source_ref": "CARD-FS:word/document.xml:P0115",
                "window_ms": {"start_ms": index * 100, "end_ms": (index + 1) * 100}}],
            "limitations_zh": ["二维测量，未标定技术等级。"]}]})
    artifact = {"version": ARTIFACT_VERSION, "video_id": "video", "video_sha256": "a" * 64,
                "source_binding": source_binding(), "score_semantics": SEMANTICS,
                "technical_grade": None, "technical_score_0_to_100": None, "records": records}
    return artifact, events


class SourceAssessmentTests(unittest.TestCase):
    def test_exact_seventy_percent_passes_visibility_without_a_grade(self):
        artifact, events = fixture()
        report = project_source_assessment(artifact, events, video_id="video", duration_ms=100)
        row = report["indicators"][0]["windows"][0]
        self.assertEqual("sufficient", row["visibility"]["status"])
        self.assertEqual(.7, row["visibility"]["valid_frame_ratio"])
        self.assertIsNone(report["technical_grade"])
        self.assertFalse(row["complete_source_rule_verified"])

    def test_mismatched_video_source_and_invented_grade_fail_closed(self):
        artifact, events = fixture()
        for key, value in (("video_id", "other"), ("technical_grade", "A"), ("source_binding", {})):
            changed = copy.deepcopy(artifact)
            changed[key] = value
            self.assertEqual("unavailable", project_source_assessment(changed, events, video_id="video", duration_ms=100)["status"])
        self.assertEqual("unavailable", project_source_assessment(artifact, events, video_id="video", duration_ms=100, video_sha256="b" * 64)["status"])

    def test_wrong_person_out_of_bounds_and_duplicate_events_do_not_become_evidence(self):
        artifact, events = fixture()
        artifact["records"][0]["person_track_id"] = 2
        self.assertEqual([], project_source_assessment(artifact, events, video_id="video", duration_ms=100)["indicators"][0]["windows"])
        artifact, events = fixture()
        self.assertEqual([], project_source_assessment(artifact, events, video_id="video", duration_ms=99)["indicators"][0]["windows"])
        self.assertEqual("unavailable", project_source_assessment(artifact, events * 2, video_id="video", duration_ms=100)["status"])

    def test_absent_or_nonfinite_measurement_is_not_imputed(self):
        artifact, events = fixture()
        artifact["records"][0]["indicators"][0]["source_measurements"][0]["value"] = float("nan")
        report = project_source_assessment(artifact, events, video_id="video", duration_ms=100)
        measurement = report["indicators"][0]["windows"][0]["measurements"][0]
        self.assertEqual("unavailable", measurement["status"])
        self.assertIsNone(measurement["value"])

    def test_simultaneous_observed_successor_keeps_zero_without_imputing_missing(self):
        artifact, events = fixture()
        item = artifact["records"][0]["indicators"][0]
        item["indicator_id"] = "FS01-M05"
        events[0]["key_phases_ms"] = {"landing_proxy_ms": 50}
        events.append({"event_id": "successor", "event_code": "FS02", "person_track_id": 1,
                       "video_id": "video", "start_ms": 50, "end_ms": 100})
        item["event_association"] = {"successor_event_id": "successor", "successor_event_code": "FS02", "successor_start_ms": 50,
            "source_event_id": "event-0", "anchor_phase_key": "landing_proxy_ms", "anchor_ms": 50, "link_measurement_status": "measured"}
        row = item["source_measurements"][0]
        row.update(feature_name="landing_proxy_to_next_fs02_ms", source_ref="CARD-FS:word/document.xml:P0220",
                   value=0, unit="ms", window_ms={"start_ms": 50, "end_ms": 50})
        measurement = project_source_assessment(artifact, events, video_id="video", duration_ms=100)["indicators"][2]["windows"][0]["measurements"][0]
        self.assertEqual(("measured", 0), (measurement["status"], measurement["value"]))
        row.update(value=None, valid=False, status="unavailable")
        measurement = project_source_assessment(artifact, events, video_id="video", duration_ms=100)["indicators"][2]["windows"][0]["measurements"][0]
        self.assertIsNone(measurement["value"])

    def test_measurement_cannot_borrow_another_event_window_source_or_unit(self):
        artifact, events = fixture()
        for changes in ({"window_ms": {"start_ms": 500, "end_ms": 600}}, {"source_ref": "unrelated-source"}, {"unit": "m"}):
            changed = copy.deepcopy(artifact)
            changed["records"][0]["indicators"][0]["source_measurements"][0].update(changes)
            row = project_source_assessment(changed, events, video_id="video", duration_ms=1000)["indicators"][0]["windows"][0]["measurements"][0]
            self.assertEqual("unavailable", row["status"])
            self.assertIsNone(row["value"])
        artifact["records"][0]["indicators"][0]["source_measurements"][0]["feature_name"] = "invented_feature"
        self.assertEqual([], project_source_assessment(artifact, events, video_id="video", duration_ms=1000)["indicators"][0]["windows"][0]["measurements"])

    def test_truncation_preserves_full_counts_and_explicit_missing_examples(self):
        artifact, events = fixture(200)
        for record in artifact["records"][100:]:
            record["indicators"][0]["source_measurements"][0].update(status="unavailable", valid=False, value=None)
        indicator = project_source_assessment(artifact, events, video_id="video", duration_ms=20000)["indicators"][0]
        self.assertEqual((200, 100, 100, 80), tuple(indicator[k] for k in ("window_count", "measured_window_count", "unavailable_window_count", "returned_window_count")))
        self.assertTrue(indicator["is_truncated"])
        self.assertEqual(20, sum(w["measurements"][0]["status"] == "unavailable" for w in indicator["windows"]))

    def test_file_bindings_and_cache_invalidation(self):
        artifact, events = fixture()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            event_data = "".join(json.dumps(event) + "\n" for event in events).encode()
            primary_data = b'{"player":1}\n'
            frames_data = b'{"frame":1}\n'
            (root / "events.jsonl").write_bytes(event_data)
            (root / "primary-player.jsonl").write_bytes(primary_data)
            (root / "frames.jsonl").write_bytes(frames_data)
            artifact.update(events_sha256=hashlib.sha256(event_data).hexdigest(),
                            primary_timeline_sha256=hashlib.sha256(primary_data).hexdigest(), frames_sha256=hashlib.sha256(frames_data).hexdigest())
            (root / ARTIFACT_NAME).write_text(json.dumps(artifact), encoding="utf-8")
            args = dict(video_id="video", duration_ms=100, video_sha256="a" * 64, frames_sha256=artifact["frames_sha256"])
            self.assertEqual("available", load_source_aligned_assessment(root, **args)["status"])
            (root / "primary-player.jsonl").write_bytes(b'{"player":2}\n')
            self.assertEqual("unavailable", load_source_aligned_assessment(root, **args)["status"])
            (root / "primary-player.jsonl").write_bytes(primary_data)
            (root / "frames.jsonl").write_bytes(b'{"frame":2}\n')
            self.assertEqual("unavailable", load_source_aligned_assessment(root, **args)["status"])

    def test_packaged_rules_are_reproducible_and_do_not_fix_source_conflicts(self):
        compiled = build(SOURCE.read_bytes())
        self.assertEqual(compiled, json.loads(OUTPUT.read_text(encoding="utf-8")))
        self.assertEqual(298, len(compiled["rules"]))
        self.assertEqual(246, len(compiled["visual_rules"]))
        self.assertEqual(100, sum(not r["manual_grading_allowed"] for r in compiled["rules"]))
        self.assertEqual(7, len(compiled["source_documents"]))
        self.assertEqual(hashlib.sha256(SOURCE.read_bytes()).hexdigest(), compiled["source_reference_sha256"])
        self.assertFalse(compiled["policy"]["optional_absence_deducts_points"])
        self.assertIsNone(compiled["policy"]["numeric_grade_mapping"])


if __name__ == "__main__":
    unittest.main()
