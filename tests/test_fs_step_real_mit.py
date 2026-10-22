"""A frozen real negative development case, not a technical step ground truth.

Prepared samples came from the complete 249-frame original run. The unit adapter
test substitutes only that preparation; the opt-in test verifies original media.
"""
from copy import deepcopy
from datetime import datetime
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import shutil
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from rallymate_scoring.card_evaluation import evaluate_card_window
from rallymate_scoring.card_rule_catalog import get_card_rule
from rallymate_scoring.step_motion_evidence import compute_step_motion_evidence
from rallymate_service.fs_card_windows import build_fs_card_window_report, compute_fs_card_windows


def canonical(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def previous_shape(value):
    """Remove only the declared 1.0.2 source-context additions for comparison."""
    if isinstance(value, list):
        return [previous_shape(row) for row in value]
    if not isinstance(value, dict):
        return value
    result = {key: previous_shape(item) for key, item in value.items()}
    for key in ("version", "step_motion_version"):
        if result.get(key) == "step-motion-evidence-v1.0.2":
            result[key] = "step-motion-evidence-v1.0.1"
    if "source_geometry_series" in result:
        for row in result["source_geometry_series"]:
            for key in ("normalization_basis", "pose_source", "source_binding"):
                del row[key]
    return result


class RealMitStepDevelopmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = json.loads((Path(__file__).parent / "fixtures/fs-step-real-mit-development-v1.json").read_text(encoding="utf-8"))
        cls.case = cls.fixture["case"]

    def samples(self, rows=None):
        return [SimpleNamespace(**row) for row in (self.case["samples"] if rows is None else rows)]

    def evaluate(self, rows=None, window=None):
        return evaluate_card_window(get_card_rule("FS07-M02"), self.samples(rows),
            self.case["window"] if window is None else window, self.case["timeline"])

    def test_real_endpoint_displacement_does_not_substitute_for_complete_cycle(self):
        actual = self.evaluate()
        self.assertEqual(actual, self.case["numeric_core_expected"])
        self.assertEqual(actual["status"], "unavailable")
        self.assertIsNone(actual["score"])
        self.assertIsNone(actual["conditions"][0]["value"])
        self.assertIsNone(actual["conditions"][0]["passed"])
        motion = actual["conditions"][0]["evidence"]["step_motion_evidence"]
        self.assertEqual(motion["valid_frame_count"], 10)
        self.assertEqual(motion["total_frame_count"], 10)
        self.assertFalse(motion["complete_visual_motion_cycle"])
        left = motion["feet"]["left"]
        self.assertEqual([row["moving"] for row in left["phases"]], [False, True, False, True, False])
        self.assertGreater(left["net_displacement_torso"], .15)
        self.assertEqual(left["reason_codes"], ["step_motion_requires_single_low_move_low"])
        self.assertEqual(left["phases"][-1]["source_frames"], [37, 38])
        self.assertFalse(left["phases"][-1]["enough_frames"])
        self.assertFalse(left["phases"][-1]["enough_duration"])
        self.assertFalse(motion["contact_verified"])
        self.assertFalse(motion["technical_step_verified"])

    def test_adapter_retains_original_quality_nulls_and_all_source_events(self):
        self.assertEqual(len(self.case["source_events"]), 33)
        # This is an explicit downstream contract test, not a full-video replay.
        with patch("rallymate_service.fs_card_windows._prepare_samples",
                   return_value=({1: self.samples()}, self.case["timeline"])):
            report = compute_fs_card_windows(self.case["records"], self.case["primary"],
                self.case["source_events"], self.case["decoder_timeline"], self.fixture["annotation_document"],
                source_job_id=self.fixture["visual_observation"]["source_run_id"])
        rows = [row for card in report["cards"] for row in card["instances"]]
        self.assertEqual(rows, [self.case["expected"]])
        self.assertIn("fs_window_original_quality_requires_review", rows[0]["reason_codes"])
        self.assertTrue(rows[0]["source_window_evidence"]["original_event_quality"])
        self.assertFalse(rows[0]["source_window_evidence"]["step_cycle_bound"])
        self.assertFalse(report["input"]["artifact_files_verified"])
        self.assertIsNone(report["overall_score"])
        for key in ("score", "proxy_grade", "original_grade"):
            self.assertIsNone(rows[0][key])

    def test_visual_binding_all_ten_original_frames_pts_and_person(self):
        visual = self.fixture["visual_observation"]
        self.assertEqual(visual["dataset_role"], "previously_observed_development_not_independent_validation")
        self.assertEqual(visual["source_frame_interval_inclusive"], [29, 38])
        self.assertEqual(visual["anatomical_moving_foot"], "left")
        self.assertEqual(visual["uncertainty_ms"], 67)
        self.assertEqual([r["movement_onset_frame_bracket"] for r in visual["reviewers"]], [[32, 34], [31, 33]])
        for rows, key in ((self.case["records"], None), (self.case["timeline"], "frame_index"),
                          (visual["source_frames"], "frame_index")):
            indices = [r[key] if key else r["frame"]["index"] for r in rows]
            self.assertEqual(indices, list(range(29, 39)))
        for filename, field in (("frames.jsonl", "records"), ("primary-player.jsonl", "primary")):
            receipts = self.fixture["source_row_receipts"][filename]
            self.assertEqual([row["frame_index"] for row in receipts], list(range(29, 39)))
            for row, receipt in zip(self.case[field], receipts):
                self.assertEqual(canonical(row), receipt["canonical_json_sorted_utf8_sha256"])
                self.assertRegex(receipt["raw_line_including_line_ending_sha256"], r"^[a-f0-9]{64}$")
        for sample, time, frame in zip(self.case["samples"], self.case["timeline"], visual["source_frames"]):
            binding = sample["source_binding"]
            self.assertEqual(time["selected_person_track_id"], 1)
            self.assertEqual(binding["person_track_id"], 1)
            self.assertEqual(binding["video_sha256"], visual["video_sha256"])
            for key in ("frame_index", "pts", "time_base_num", "time_base_den"):
                self.assertEqual(binding[key], frame[key])
                self.assertEqual(binding[key], time[key])
            for key in ("selection_epoch", "camera_reference_epoch"):
                self.assertEqual(binding[key], time[key])
            exact = Fraction(time["pts"] * time["time_base_num"] * 1000, time["time_base_den"])
            self.assertLessEqual(abs(float(exact) - time["timestamp_ms"]), .51)
            self.assertRegex(frame["original_png_sha256"], r"^[a-f0-9]{64}$")

    def test_new_source_context_matches_samples_without_inventing_missing_model_metadata(self):
        evidence = self.case["expected"]["source_window_evidence"]["step_motion"]
        self.assertEqual(evidence["version"], "step-motion-evidence-v1.0.2")
        self.assertEqual(len(evidence["source_geometry_series"]), 10)
        for geometry, sample in zip(evidence["source_geometry_series"], self.case["samples"]):
            for key in ("normalization_basis", "pose_source", "observation_semantics"):
                self.assertEqual(geometry[key], sample[key])
            for key, value in geometry["source_binding"].items():
                self.assertEqual(value, sample["source_binding"][key])
            for joint in evidence["required_joint_names"]:
                self.assertEqual(geometry["normalized_points"][joint], sample["points"][joint])
                self.assertEqual(geometry["point_confidences"][joint], sample["point_confidences"][joint])
        self.assertFalse(evidence["geometry_is_visibility"])

    def test_synthetic_joint_gap_keeps_original_denominator_and_breaks_continuity(self):
        rows = deepcopy(self.case["samples"])
        rows[5]["point_confidences"]["left_ankle"] = 0
        result = self.evaluate(rows)
        motion = result["conditions"][0]["evidence"]["step_motion_evidence"]
        self.assertEqual(motion["total_frame_count"], 10)
        self.assertEqual(motion["valid_frame_count"], 9)
        self.assertEqual(len(motion["source_geometry_series"]), 10)
        self.assertIn("step_motion_common_joints_not_continuous", motion["reason_codes"])
        self.assertIsNone(result["conditions"][0]["value"])

    def test_synthetic_side_change_is_not_replaced_by_faster_left_foot(self):
        window = deepcopy(self.case["window"])
        window["step_motion_observation"]["moving_foot"] = "right"
        motion = compute_step_motion_evidence(self.samples(), window, self.case["timeline"])
        self.assertEqual(motion["moving_foot_observation"]["moving_foot"], "right")
        self.assertFalse(motion["complete_visual_motion_cycle"])
        self.assertEqual([p["moving"] for p in motion["feet"]["right"]["phases"]], [False])
        self.assertIn("step_motion_declared_side_conflicts_with_paths", motion["reason_codes"])

    def test_freeze_precedes_first_numeric_execution_and_upgrade_changes_only_source_context(self):
        fixture = self.fixture
        self.assertLess(datetime.fromisoformat(fixture["visual_observation"]["frozen_at_utc"]),
                        datetime.fromisoformat(fixture["first_numeric_execution"]["started_at_utc"]))
        self.assertFalse(fixture["visual_observation"]["new_step_motion_numeric_output_read_or_executed_before_freeze"])
        for name, value in (("visual-freeze.json", fixture["visual_observation"]),
                            ("fs-card-window-annotations.json", fixture["annotation_document"])):
            serialized = (json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode("utf-8")
            self.assertEqual(hashlib.sha256(serialized).hexdigest(), fixture["freeze_lock"]["files"][name]["raw_sha256"])
        comparison = fixture["version_comparison"]
        self.assertEqual(canonical(previous_shape(self.case["expected"])), comparison["previous_instance_canonical_sha256"])
        self.assertEqual(canonical(previous_shape(self.case["numeric_core_expected"])), comparison["previous_numeric_core_canonical_sha256"])
        self.assertEqual(comparison["previous_export_canonical_sha256"],
                         comparison["current_export_after_only_removing_declared_additions_canonical_sha256"])
        self.assertEqual(fixture["source_closure"]["http"]["unauthorized_status"], 401)
        self.assertEqual([r["status"] for r in fixture["source_closure"]["http"]["requests"]], [200, 200])

    @unittest.skipUnless(os.environ.get("FS_STEP_MIT_ARTIFACT_ROOT"), "requires explicitly supplied original MIT media and full raw run")
    def test_original_full_media_artifact_adapter_replay(self):
        root = Path(os.environ["FS_STEP_MIT_ARTIFACT_ROOT"])
        visual = self.fixture["visual_observation"]
        source, video = root / visual["source_run"], root / visual["source_video"]
        self.assertEqual(hashlib.sha256(video.read_bytes()).hexdigest(), visual["video_sha256"])
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory) / visual["source_run_id"]
            run.mkdir()
            for name, digest in visual["input_artifact_sha256"].items():
                self.assertEqual(hashlib.sha256((source / name).read_bytes()).hexdigest(), digest)
                shutil.copyfile(source / name, run / name)
            report = build_fs_card_window_report(run, self.fixture["annotation_document"], video_path=video)
        self.assertTrue(report["input"]["artifact_files_verified"])
        self.assertEqual([r for card in report["cards"] for r in card["instances"]], [self.case["expected"]])


if __name__ == "__main__":
    unittest.main()
