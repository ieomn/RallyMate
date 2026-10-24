from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
import hashlib
import os
from pathlib import Path
import tempfile
import unittest

import numpy as np

from rallymate_features.coordinates import pose_sequence_from_records
from rallymate_features.source_alignment import clear_source_aligned_feature_cache
from rallymate_scoring.footwork_cycle_locator import locate_footwork_cycles
from rallymate_service.footwork_cycle_artifacts import _bind_verified_decoder_snapshot
from tests.test_footwork_cycle_card_evidence import synthetic_cycle, real_fixture

ROOT = Path(__file__).resolve().parents[1]


def native_fixture():
    data = json.loads((ROOT / "tests/fixtures/footwork-cycle-locator-native-e420-v1.json").read_text(encoding="utf-8"))
    records, primary = _bind_verified_decoder_snapshot(data["records"], data["primary_timeline"], data["verified_source_timeline"])
    return pose_sequence_from_records(records, primary), primary, data


def scan(fixture, events=()):
    sequence, event, primary, _ = fixture
    return locate_footwork_cycles(sequence, primary_timeline=primary, source_run_id=event["video_id"], events=events)


class FootworkCycleLocatorTests(unittest.TestCase):
    def tearDown(self):
        clear_source_aligned_feature_cache()

    def test_real_native_candidate_has_exact_source_rows_and_no_family_or_grade(self):
        sequence, primary, data = native_fixture()
        before = deepcopy(primary)
        report = locate_footwork_cycles(sequence, primary_timeline=primary, source_run_id=data["provenance"]["source_run_id"])
        self.assertEqual(report["summary"], {"motion_candidate_count": 2,
            "complete_visual_motion_cycle_count": 1, "incomplete_motion_candidate_count": 1})
        right = next(c for c in report["candidates"] if c["moving_ankle"] == "right")
        self.assertEqual((right["start_source_frame"], right["end_source_frame"]), (112, 136))
        self.assertEqual((right["motion"]["start_source_frame"], right["motion"]["end_source_frame"]), (124, 132))
        self.assertAlmostEqual(right["motion"]["motion_duration_ms"], 800 / 3)
        self.assertAlmostEqual(right["motion"]["preceding_low_motion_duration_ms"], 400)
        self.assertAlmostEqual(right["motion"]["following_low_motion_duration_ms"], 400 / 3)
        self.assertTrue(right["complete_visual_motion_cycle"])
        self.assertEqual(report["source_frames"], list(range(112, 137)))
        self.assertEqual(primary, before)
        self.assertFalse(report["file_artifacts_verified"])
        for candidate in report["candidates"]:
            self.assertIsNone(candidate["event_code"])
            self.assertIsNone(candidate["grade"])
            self.assertIsNone(candidate["score"])
        for source in report["segments"][0]["source_frame_bindings"]:
            self.assertEqual(source["person_track_id"], 1)
            self.assertEqual(source["model_geometry_source"]["source_binding"]["selection_epoch"], 0)
            self.assertEqual(source["selection_epoch"], 0)
        json.dumps(report, allow_nan=False)

    def test_real_usta_six_motion_runs_remain_incomplete_and_keep_the_36_frame_denominator(self):
        result = scan(real_fixture())
        self.assertEqual(result["source_frame_count"], 36)
        self.assertEqual(result["summary"], {"motion_candidate_count": 6,
            "complete_visual_motion_cycle_count": 0, "incomplete_motion_candidate_count": 6})
        self.assertEqual(result["segments"][0]["common_valid_frame_count"], 36)
        self.assertTrue(all(c["reason_codes"] for c in result["candidates"]))
        self.assertTrue(any("following_low_motion_below_80_ms" in c["reason_codes"] for c in result["candidates"]))

    def test_real_source_geometry_independently_reproduces_all_three_phase_boundaries(self):
        sequence, primary, data = native_fixture()
        result = locate_footwork_cycles(sequence, primary_timeline=primary, source_run_id=data["provenance"]["source_run_id"])
        segment = result["segments"][0]
        points = {name: np.asarray([r["points"][name] for r in segment["source_geometry_series"]])
                  for name in segment["required_joint_names"]}
        shoulder = (points["left_shoulder"] + points["right_shoulder"]) / 2
        hip = (points["left_hip"] + points["right_hip"]) / 2
        scale = np.median(np.linalg.norm(shoulder - hip, axis=1))
        clocks = np.asarray([r["pts"] * r["time_base_num"] * 1000 / r["time_base_den"] for r in segment["source_frame_bindings"]])
        speed = np.linalg.norm(np.diff(points["right_ankle"], axis=0), axis=1) / np.diff(clocks) * 1000 / scale
        # Original frames124..132 are eight adjacent moving pairs. Neither a
        # saved candidate boolean nor a window endpoint flag enters this check.
        self.assertTrue(np.all(speed[:12] <= .5))
        self.assertTrue(np.all(speed[12:20] > .5))
        self.assertTrue(np.all(speed[20:] <= .5))
        delta = (np.median(points["right_ankle"][20:], axis=0) - np.median(points["right_ankle"][:13], axis=0)) / scale
        self.assertGreaterEqual(abs(delta[0]), .08)
        self.assertAlmostEqual(float(np.linalg.norm(delta)), .20872979674622785)

    def test_controlled_math_complete_pairs_are_not_real_video_or_card_labels(self):
        result = scan(synthetic_cycle())
        self.assertEqual(result["summary"]["complete_visual_motion_cycle_count"], 2)
        self.assertEqual(len(result["segments"][0]["series"]["sequential_follow_pairs"]), 1)
        self.assertIsNone(result["score"])
        self.assertFalse(result["source_grade_supported"])

    def test_joint_gap_splits_without_connecting_or_dropping_original_denominator(self):
        fixture = synthetic_cycle()
        fixture[0].confidence["left_ankle"][10] = .1
        result = scan(fixture)
        self.assertEqual(result["source_frame_count"], 22)
        self.assertEqual(result["excluded_frame_count"], 1)
        self.assertEqual(result["excluded_frames"][0]["source_binding"]["frame_index"], 10)
        self.assertFalse(result["excluded_frames"][0]["required_joint_validity"]["left_ankle"])
        self.assertEqual(result["boundaries"][0]["source_frame"], 10)
        self.assertTrue(all(not (c["start_source_frame"] < 10 < c["end_source_frame"]) for c in result["candidates"]))

    def test_missing_decoder_binding_is_not_replaced_by_geometry(self):
        fixture = synthetic_cycle()
        fixture[0].source_time_bindings.pop(8)
        result = scan(fixture)
        self.assertIn({"source_frame": 8, "reason": "source_clock_binding_unverified", "kind": "excluded_frame"}, result["boundaries"])
        self.assertTrue(all(8 not in c["source_frames"] for c in result["candidates"]))

    def test_raw_primary_duplicate_or_wrong_person_cannot_authorize_candidate(self):
        for duplicate in (False, True):
            fixture = synthetic_cycle()
            if duplicate:
                fixture[2].append(deepcopy(fixture[2][8]))
            else:
                fixture[2][8]["source_track_id"] = 99
            result = scan(fixture)
            self.assertTrue(any(b["source_frame"] == 8 and b["reason"] == "selected_subject_binding_unverified" for b in result["boundaries"]))

    def test_source_epoch_change_cuts_even_if_all_joints_remain_valid(self):
        fixture = synthetic_cycle()
        for row in list(fixture[0].source_time_bindings.values())[10:]:
            row["selection_epoch"] = 1
        result = scan(fixture)
        self.assertIn({"before_source_frame": 9, "source_frame": 10,
            "reason": "source_continuity_changed:selection_epoch", "kind": "continuity_break"}, result["boundaries"])
        self.assertTrue(all(not(c["start_source_frame"] < 10 <= c["end_source_frame"]) for c in result["candidates"]))

    def test_explicit_optional_primary_source_and_model_clocks_cannot_contradict_the_decoder(self):
        for field, value in (("video_sha256", "b" * 64), ("pts", 9999), ("source_timestamp_ms", 9999)):
            fixture = synthetic_cycle()
            fixture[2][8][field] = value
            result = scan(fixture)
            self.assertTrue(any(b["source_frame"] == 8 for b in result["boundaries"]))
        sequence, primary, data = native_fixture()
        sequence.model_estimate_bindings[16]["timestamp_ms"] += 1
        result = locate_footwork_cycles(sequence, primary_timeline=primary, source_run_id=data["provenance"]["source_run_id"])
        self.assertTrue(any(b["source_frame"] == 128 and b["reason"] == "model_geometry_source_timestamp_mismatch" for b in result["boundaries"]))

    def test_missing_source_frame_never_joins_two_ends_of_a_motion(self):
        sequence, event, timeline, binding = synthetic_cycle()
        keep = np.arange(len(sequence.source_frames)) != 10
        sequence = replace(sequence, timestamp_ms=sequence.timestamp_ms[keep], source_frames=sequence.source_frames[keep],
            keypoints_xy={k: v[keep] for k, v in sequence.keypoints_xy.items()},
            confidence={k: v[keep] for k, v in sequence.confidence.items()},
            frame_dimensions_px=sequence.frame_dimensions_px[keep],
            frame_transforms_to_reference=sequence.frame_transforms_to_reference[keep],
            camera_motion_status=tuple(v for i, v in enumerate(sequence.camera_motion_status) if keep[i]),
            camera_reference_epochs=tuple(v for i, v in enumerate(sequence.camera_reference_epochs) if keep[i]),
            source_time_bindings={k: v for k, v in sequence.source_time_bindings.items() if k != 10})
        result = scan((sequence, event, timeline, binding))
        self.assertIn({"before_source_frame": 9, "source_frame": 11,
            "reason": "source_frame_or_processed_gap", "kind": "continuity_break"}, result["boundaries"])
        self.assertTrue(all(not(c["start_source_frame"] < 10 < c["end_source_frame"]) for c in result["candidates"]))

    def test_real_model_request_invalidity_keeps_frame_and_breaks_motion(self):
        _, _, data = native_fixture()
        broken = deepcopy(data)
        record = next(r for r in broken["records"] if r["frame"]["index"] == 128)
        record["sam3d_requests"][0]["status"] = "error"
        records, primary = _bind_verified_decoder_snapshot(broken["records"], broken["primary_timeline"], broken["verified_source_timeline"])
        sequence = pose_sequence_from_records(records, primary)
        result = locate_footwork_cycles(sequence, primary_timeline=primary, source_run_id=data["provenance"]["source_run_id"])
        self.assertEqual(result["source_frame_count"], 25)
        self.assertEqual(result["summary"]["complete_visual_motion_cycle_count"], 0)
        self.assertTrue(any(b["source_frame"] == 128 and b["reason"] == "model_geometry_source_binding_missing" for b in result["boundaries"]))

    def test_real_observed_motion_does_not_survive_a_truncated_following_low_segment(self):
        sequence, primary, data = native_fixture()
        # Change no coordinates: derive a genuinely truncated raw snapshot.
        keep = lambda r: r["frame"]["index"] <= 133
        records, selected = _bind_verified_decoder_snapshot(list(filter(keep, data["records"])),
            [r for r in data["primary_timeline"] if r["source_frame_index"] <= 133],
            [r for r in data["verified_source_timeline"] if r["frame_index"] <= 133])
        result = locate_footwork_cycles(pose_sequence_from_records(records, selected), primary_timeline=selected,
            source_run_id=data["provenance"]["source_run_id"])
        self.assertEqual(result["summary"]["complete_visual_motion_cycle_count"], 0)
        right = next(c for c in result["candidates"] if c["moving_ankle"] == "right")
        self.assertIn("following_low_motion_below_80_ms", right["reason_codes"])

    def test_camera_failure_is_not_repaired_by_finite_points(self):
        fixture = synthetic_cycle()
        fixture[0].frame_transforms_to_reference[8] = np.nan
        result = scan(fixture)
        self.assertTrue(any(b["source_frame"] == 8 and b["reason"] == "stable_reference_transform_unverified" for b in result["boundaries"]))

    def test_original_phase_censor_is_retained_but_does_not_erase_new_independent_scan(self):
        fixture = synthetic_cycle()
        event = deepcopy(fixture[1])
        event["quality_flags"] = ["phase_boundary_censored:landing_proxy_ms", "primary_identity_ambiguous"]
        for row in fixture[2]:
            row["identity_ambiguous"] = True
        result = scan(fixture, [event])
        self.assertEqual(result["summary"]["complete_visual_motion_cycle_count"], 2)
        quality = result["segments"][0]["associated_event_quality"][0]["quality"]
        self.assertIn("phase_boundary_censored:landing_proxy_ms", quality["raw_input_quality_flags"])
        self.assertIn("primary_identity_ambiguous", quality["advisory_flags"])

    def test_confirmed_identity_switch_still_excludes_its_actual_source_window(self):
        fixture = synthetic_cycle()
        event = deepcopy(fixture[1])
        event.update(start_ms=280, end_ms=320, quality_flags=["confirmed_id_switch_present"])
        result = scan(fixture, [event])
        self.assertEqual(result["excluded_frame_count"], 2)
        self.assertTrue(all(not(c["start_source_frame"] <= 7 <= c["end_source_frame"]) for c in result["candidates"]))

    def test_short_window_and_static_crossed_snapshot_never_authorize_motion(self):
        fixture = synthetic_cycle("FS03", cross=True)
        for points in fixture[0].keypoints_xy.values():
            points[:] = points[-1]
        self.assertEqual(scan(fixture)["summary"]["motion_candidate_count"], 0)

    def test_duplicate_source_event_conflict_or_cross_run_is_rejected(self):
        fixture = synthetic_cycle()
        event = deepcopy(fixture[1])
        conflict = deepcopy(event)
        conflict["end_ms"] = 800
        with self.assertRaisesRegex(ValueError, "source_event_id_conflict"):
            scan(fixture, [event, conflict])
        event["video_id"] = "other-run"
        with self.assertRaisesRegex(ValueError, "source_event_inventory_invalid"):
            scan(fixture, [event])


class OriginalVideoLocatorReplayTests(unittest.TestCase):
    def test_native_e420_original_media_replay_keeps_each_failed_candidate_and_exact_fixture_rows(self):
        from scripts.replay_footwork_cycle_locator import replay
        artifact_root = Path(os.environ.get("CARD_ARTIFACT_ROOT", str(ROOT)))
        data = json.loads((ROOT / "tests/fixtures/footwork-cycle-locator-native-e420-v1.json").read_text(encoding="utf-8"))
        run = artifact_root / data["provenance"]["source_run_directory"]
        video = artifact_root / ".codex_tmp/local-migration-20261019/local-data/uploads/0a3df3a3-2629-4dd5-80a1-54e4b11f7341.mp4"
        if not run.is_dir() or not video.is_file():
            self.skipTest("original_native_e420_media_not_available")
        with (run / "frames.jsonl").open("rb") as stream:
            observed = {no: line for no, line in enumerate(stream, 1) if 113 <= no <= 137}
        for proof in data["provenance"]["line_evidence"]:
            self.assertEqual(hashlib.sha256(observed[proof["source_line_number_1based"]]).hexdigest(), proof["original_jsonl_line_sha256"])
        with tempfile.TemporaryDirectory() as temporary:
            result = replay(run, video, Path(temporary) / "native-e420-replay.json")
        self.assertEqual(result["input"]["input_artifact_sha256"], data["provenance"]["input_artifact_sha256"])
        self.assertEqual(result["source_frame_count"], 2911)
        self.assertEqual(result["summary"], {"motion_candidate_count": 261,
            "complete_visual_motion_cycle_count": 63, "incomplete_motion_candidate_count": 198})
        self.assertEqual(result["scored_card_instance_count"], 0)
        self.assertEqual(result["tennis_family_assignment_count"], 0)
        self.assertTrue(result["file_artifacts_verified"])
        self.assertTrue(result["decoder_verified_from_original_video"])
        self.assertEqual(result["input"]["input_artifact_sha256_after"], result["input"]["input_artifact_sha256"])
        self.assertEqual(len(result["source_frame_verification_ledger"]), 2911)

    def test_original_usta_full_scan_reports_no_complete_cycle_without_changing_the_six_manual_windows(self):
        from scripts.replay_footwork_cycle_locator import replay
        artifact_root = Path(os.environ.get("CARD_ARTIFACT_ROOT", str(ROOT)))
        run = artifact_root / ".codex_tmp/card-video-extension-20261022/runs/fs-usta-return-footwork-heldout"
        video = artifact_root / ".codex_tmp/card-video-extension-20261022/media/usta-footwork.mp4"
        if not run.is_dir() or not video.is_file():
            self.skipTest("original_usta_media_not_available")
        manual = ROOT / "tests/fixtures/footwork-cycle-annotations-usta-v1.json"
        before = manual.read_bytes()
        with tempfile.TemporaryDirectory() as temporary:
            result = replay(run, video, Path(temporary) / "usta-replay.json")
        self.assertEqual(manual.read_bytes(), before)
        self.assertEqual(result["source_frame_count"], 539)
        self.assertEqual(result["excluded_frame_count"], 4)
        self.assertEqual(result["summary"], {"motion_candidate_count": 58,
            "complete_visual_motion_cycle_count": 0, "incomplete_motion_candidate_count": 58})
        self.assertEqual([b["source_frame"] for b in result["boundaries"]], [11940, 12110, 12111, 12279])


if __name__ == "__main__":
    unittest.main()
