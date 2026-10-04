from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from rallymate_events.rules import detect_pose_events, _local_activity_thresholds
from rallymate_features.event_features import compute_event_features, compute_feature, clear_feature_cache
from rallymate_features.schemas import EventInterval
from test_events import _motion_sequence


class CameraGuardEventBoundaryTests(unittest.TestCase):
    def tearDown(self):
        clear_feature_cache()

    def test_recursive_windows_preserve_global_evidence_indexes_and_unique_ids(self):
        source = _motion_sequence()
        points = {name: values.copy() for name, values in source.keypoints_xy.items()}
        rise = .03 * np.sin(np.clip((source.timestamp_ms - 400) / 400, 0, 1) * np.pi)
        for name, values in points.items():
            if "ankle" in name:
                values[:, 1] -= rise
        count = len(source.timestamp_ms)
        offset_ms = int(source.timestamp_ms[-1]) + 80
        timestamps = np.r_[source.timestamp_ms, source.timestamp_ms[-1] + 40, source.timestamp_ms + offset_ms]
        combined = replace(source, timestamp_ms=timestamps,
                           source_frames=np.arange(len(timestamps)) * 2 + 100,
                           keypoints_xy={name: np.concatenate([values, values[-1:], values]) for name, values in points.items()},
                           confidence={name: np.full(len(timestamps), .9) for name in points},
                           camera_motion_status=("stationary",) * count + ("moving",) + ("stationary",) * count)
        events = detect_pose_events(combined, source_id="two-camera-windows")
        self.assertEqual(len(events), 6)
        self.assertEqual(len({event["event_id"] for event in events}), 6)
        before = {event["event_code"]: event for event in events if event["start_ms"] < offset_ms}
        after = {event["event_code"]: event for event in events if event["start_ms"] >= offset_ms}
        for code, first in before.items():
            second = after[code]
            self.assertEqual(second["start_ms"] - first["start_ms"], offset_ms)
            for key in ["event_kinematic_coverage", "event_motion_reference"]:
                for boundary in ["start_index", "end_index"]:
                    self.assertEqual(second["provenance"][key][boundary] - first["provenance"][key][boundary], count + 1)
            for key, value in first["key_phases_ms"].items():
                self.assertEqual(second["key_phases_ms"][key], value + offset_ms if value is not None else None)
            start_index = second["provenance"]["event_kinematic_coverage"]["start_index"]
            self.assertEqual(int(timestamps[start_index]), second["start_ms"])

    def test_feature_smoothing_cannot_read_across_camera_cut(self):
        source = _motion_sequence()
        statuses = tuple("moving" if t == 400 else "stationary" for t in source.timestamp_ms)
        baseline = replace(source, camera_motion_status=statuses)
        points = {name: values.copy() for name, values in source.keypoints_xy.items()}
        for values in points.values():
            values[source.timestamp_ms < 400, 0] += 0.1
        shifted = replace(source, keypoints_xy=points, camera_motion_status=statuses)
        event = EventInterval("manual-or-detected", "FS01", 440, 680)
        names = ["body_center_speed_body_s", "hip_center_lateral_variability_body",
                 "hip_center_speed_body_s", "left_knee_flexion_deg"]
        reference = compute_event_features(baseline, event, names)
        actual = compute_event_features(shifted, event, names)
        self.assertTrue(all(feature.valid for feature in actual))
        np.testing.assert_allclose([item.value for item in actual], [item.value for item in reference])
        direct = compute_feature(shifted, event, names[0])
        self.assertEqual(direct.value, reference[0].value)
        window = direct.provenance["coordinate_metadata"]["analysis_window"]
        self.assertEqual(window["start_ms"], 440)
        self.assertEqual(window["first_source_frame"], 11)
        self.assertEqual(window["sample_count"], len(source.timestamp_ms) - 11)
        self.assertEqual(len(baseline.timestamp_ms), len(source.timestamp_ms))
        np.testing.assert_array_equal(baseline.keypoints_xy["left_hip"], source.keypoints_xy["left_hip"])

    def test_manual_event_crossing_camera_cut_is_rejected_in_full(self):
        source = _motion_sequence()
        for status in ["moving", "unavailable"]:
            with self.subTest(status=status):
                guarded = replace(source, camera_motion_status=tuple(status if t == 400 else "stationary"
                                                                     for t in source.timestamp_ms))
                for start, end in [(200, 680), (400, 680), (420, 680), (200, 420)]:
                    event = EventInterval("crossing-cut", "FS01", start, end)
                    features = compute_event_features(guarded, event, ["left_knee_flexion_deg", "body_center_speed_body_s"])
                    self.assertTrue(all(not feature.valid and feature.value is None for feature in features))
                    self.assertTrue(all(feature.reason == "event_crosses_unverified_camera_window" for feature in features))
                    self.assertTrue(all(feature.source_frames == [] and feature.raw_value is None for feature in features))

    def test_manual_event_builder_cannot_bypass_camera_gate(self):
        from test_manual_event_features import ManualEventFeaturesTests
        from rallymate_scoring.manual_event_features import build_manual_event_features

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            frames, timeline, events = ManualEventFeaturesTests()._inputs(directory)
            records = [json.loads(line) for line in frames.read_text(encoding="utf-8").splitlines()]
            for record in records:
                record["camera_motion"] = {"status": "moving" if record["frame"]["timestamp_ms"] == 200 else "stationary"}
            frames.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
            result = build_manual_event_features(
                frames_path=frames, primary_timeline_path=timeline, manual_events_path=events,
                feasibility_registry_path=Path(__file__).resolve().parents[1] / "metric-feasibility-pose-wave-v2.json",
                video_id="video-manual", output_dir=directory / "guarded-manual-features",
            )
            blocked = [feature for feature in result["features"] if feature["event_id"] == "manual-fs01"]
            self.assertTrue(blocked)
            self.assertTrue(all(not feature["valid"] and feature["value"] is None for feature in blocked))
            self.assertTrue(all(feature["reason"] == "event_crosses_unverified_camera_window" for feature in blocked))
            self.assertTrue(any(feature["valid"] for feature in result["features"] if feature["event_id"] == "manual-fs02"))

    def test_camera_break_prevents_other_segment_position_from_changing_event(self):
        source = _motion_sequence()
        statuses = tuple("moving" if t == 400 else "stationary" for t in source.timestamp_ms)
        baseline = detect_pose_events(replace(source, camera_motion_status=statuses), source_id="camera-cut")
        points = {name: values.copy() for name, values in source.keypoints_xy.items()}
        for values in points.values():
            # All points stay in frame. Only the earlier stationary view is
            # translated; the observed movement after the camera break is identical.
            values[source.timestamp_ms < 400, 0] += 0.1
        shifted = detect_pose_events(replace(source, keypoints_xy=points, camera_motion_status=statuses),
                                     source_id="camera-cut")
        def later_events(events):
            return [(event["event_code"], event["start_ms"], event["end_ms"], event["key_phases_ms"])
                    for event in events if event["start_ms"] > 400]
        self.assertTrue(later_events(baseline))
        self.assertEqual(later_events(shifted), later_events(baseline))

    def test_blocked_preparation_cannot_supply_phases_outside_clipped_event(self):
        source = _motion_sequence()
        for blocked_end in (200, 400, 520, 640):
            with self.subTest(blocked_end=blocked_end):
                points = {name: values.copy() for name, values in source.keypoints_xy.items()}
                for name, values in points.items():
                    if "ankle" in name:
                        values[:, 1] -= .2 * np.sin(np.minimum(source.timestamp_ms / blocked_end, 1) * np.pi)
                sequence = replace(source, keypoints_xy=points,
                                   camera_motion_status=tuple("moving" if t < blocked_end else "stationary"
                                                              for t in source.timestamp_ms))
                events = detect_pose_events(sequence, source_id="blocked-preparation")
                self.assertTrue(events)
                for event in events:
                    self.assertGreaterEqual(event["start_ms"], blocked_end)
                    self.assertTrue(all(value is None or event["start_ms"] <= value <= event["end_ms"]
                                        for value in event["key_phases_ms"].values()))

    def test_sparse_threshold_window_excludes_first_sample_beyond_radius(self):
        timestamps = np.asarray([0, 100, 200, 300])
        baseline = _local_activity_thresholds(timestamps, np.ones(4), np.ones(4, dtype=bool))
        extended = _local_activity_thresholds(np.append(timestamps, 10000),
                                             np.asarray([1., 1., 1., 1., 1000.]),
                                             np.ones(5, dtype=bool))
        np.testing.assert_allclose(extended[:4], baseline)


if __name__ == "__main__":
    unittest.main()
