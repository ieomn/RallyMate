from __future__ import annotations

import unittest

import numpy as np

from rallymate_events import detect_pose_events
from rallymate_events.rules import (
    PHASE_CANDIDATE_VERSION,
    PHASE_LOW_SAMPLE_PEAK_PREFIX,
    PHASE_RIGHT_BOUNDARY_CONFIRMATION_MAX_GAP_MS,
    PHASE_RIGHT_CENSORED_PEAK_PREFIX,
    _activity_onset_peak_and_slowdown,
    _low_sample_peak_only_phase,
    _right_boundary_speed_peak_confirmed_slowdown_phase,
    _right_censored_peak_only_phase,
)
from rallymate_features.schemas import PoseSequence


JOINTS = {
    "left_shoulder": (0.40, 0.30),
    "right_shoulder": (0.60, 0.30),
    "left_hip": (0.45, 0.55),
    "right_hip": (0.55, 0.55),
    "left_knee": (0.44, 0.72),
    "right_knee": (0.56, 0.72),
    "left_ankle": (0.42, 0.90),
    "right_ankle": (0.58, 0.90),
}


def _smooth_step(timestamp: int, start: int, end: int) -> float:
    if timestamp <= start:
        return 0.0
    if timestamp >= end:
        return 1.0
    phase = (timestamp - start) / (end - start)
    return 3.0 * phase**2 - 2.0 * phase**3


def _irregular_phase_sequence(
    *, ankles_missing: bool = False, symmetric_step: bool = False
) -> PoseSequence:
    increments = (33, 47, 29, 51, 38, 44)
    timestamp_values = [0]
    index = 0
    while timestamp_values[-1] < 2800:
        timestamp_values.append(
            timestamp_values[-1] + increments[index % len(increments)]
        )
        index += 1
    timestamps = np.asarray(timestamp_values, dtype=np.int64)
    coordinates = {name: [] for name in JOINTS}
    confidence = {name: np.full(timestamps.size, 0.95) for name in JOINTS}

    for timestamp in timestamps:
        body_x = 0.20 * _smooth_step(timestamp, 900, 1800)
        # Introduce a timestamp-defined in-bout heading change.
        body_y = -0.04 * _smooth_step(timestamp, 1250, 1750)
        redistribution = 0.018 * _smooth_step(timestamp, 620, 860)
        if timestamp > 900:
            redistribution *= max(0.0, 1.0 - (timestamp - 900) / 300.0)

        if timestamp < 300 or timestamp > 720:
            foot_lift = 0.0
        elif timestamp <= 480:
            foot_lift = -0.035 * _smooth_step(timestamp, 300, 480)
        else:
            foot_lift = -0.035 * (1.0 - _smooth_step(timestamp, 480, 720))

        left_step = 0.11 * _smooth_step(timestamp, 980, 1380)
        if timestamp > 1600:
            left_step = 0.11
        left_extension = 0.035 * _smooth_step(timestamp, 1050, 1400)

        for name, (base_x, base_y) in JOINTS.items():
            x = base_x + body_x
            y = base_y + body_y
            if "hip" in name:
                x += redistribution
            if "ankle" in name:
                y += foot_lift
            if name == "left_ankle":
                x += left_step
                y += left_extension
            if symmetric_step and name == "right_ankle":
                x += left_step
                y += left_extension
            coordinates[name].append([x, y])

    if ankles_missing:
        for name in ("left_ankle", "right_ankle"):
            coordinates[name] = np.full((timestamps.size, 2), np.nan).tolist()
            confidence[name][:] = 0.0

    return PoseSequence(
        timestamp_ms=timestamps,
        source_frames=np.arange(timestamps.size, dtype=np.int64),
        keypoints_xy={
            name: np.asarray(values, dtype=np.float64)
            for name, values in coordinates.items()
        },
        confidence=confidence,
    )


class EventPhaseCandidateTests(unittest.TestCase):
    def test_near_equal_foot_peaks_keep_an_ambiguous_measurable_proxy(self) -> None:
        events = detect_pose_events(
            _irregular_phase_sequence(symmetric_step=True),
            source_id="symmetric-foot-phase-test",
        )
        fs02 = next(event for event in events if event["event_code"] == "FS02")
        self.assertEqual(
            fs02["provenance"]["phase_candidates"]["lead_foot_side_proxy"],
            "left",
        )
        self.assertEqual(
            fs02["provenance"]["phase_candidates"]["lead_side_selection"][
                "status"
            ],
            "ambiguous_peak_difference_proxy",
        )
        self.assertIn("lead_foot_side_proxy_ambiguous", fs02["quality_flags"])
        self.assertIsNotNone(
            fs02["key_phases_ms"]["lead_foot_motion_onset_proxy_ms"]
        )

    def test_right_censored_activity_uses_observed_peak_only(self) -> None:
        signal = np.asarray([0.0, 1.0, 3.0], dtype=np.float64)
        indexes = np.asarray([0, 1, 2], dtype=np.int64)
        _, peak, candidate, metadata = _activity_onset_peak_and_slowdown(
            signal, indexes
        )
        self.assertIsNone(candidate)
        candidate, metadata, flag = _right_censored_peak_only_phase(
            candidate=candidate,
            peak=peak,
            indexes=indexes,
            metadata=metadata,
            phase_key="landing_proxy_ms",
        )
        self.assertEqual(candidate, 2)
        self.assertEqual(metadata["status"], "right_censored_peak_only_proxy")
        self.assertIn("not_slowdown_contact_or_landing", metadata["fallback_semantics"])
        self.assertEqual(
            flag,
            f"{PHASE_RIGHT_CENSORED_PEAK_PREFIX}landing_proxy_ms",
        )

    def test_two_sample_tail_uses_peak_without_filling_missing_values(self) -> None:
        signal = np.asarray([np.nan, 2.0, 1.0], dtype=np.float64)
        indexes = np.asarray([1, 2], dtype=np.int64)
        _, _, candidate, metadata = _activity_onset_peak_and_slowdown(
            signal, indexes
        )
        self.assertIsNone(candidate)
        candidate, metadata, flag = _low_sample_peak_only_phase(
            candidate=candidate,
            signal=signal,
            indexes=indexes,
            metadata=metadata,
            phase_key="first_step_slowdown_proxy_ms",
        )
        self.assertEqual(candidate, 1)
        self.assertEqual(metadata["fallback_finite_sample_count"], 2)
        self.assertEqual(
            flag,
            f"{PHASE_LOW_SAMPLE_PEAK_PREFIX}first_step_slowdown_proxy_ms",
        )

        unavailable, _, no_flag = _low_sample_peak_only_phase(
            candidate=None,
            signal=np.asarray([np.nan, 2.0, np.nan]),
            indexes=indexes,
            metadata={"status": "insufficient_samples"},
            phase_key="first_step_slowdown_proxy_ms",
        )
        self.assertIsNone(unavailable)
        self.assertIsNone(no_flag)

    def test_right_boundary_speed_peak_requires_observed_post_event_decrease(self) -> None:
        timestamps = np.asarray([0, 40, 95, 150, 205], dtype=np.int64)
        speed = np.asarray([1.0, 2.0, 3.0, 2.2, 1.4], dtype=np.float64)
        candidate, metadata, flag = (
            _right_boundary_speed_peak_confirmed_slowdown_phase(
                candidate=None,
                speed_signal=speed,
                timestamps=timestamps,
                peak=2,
                event_end_index=2,
                confirmation_end_index=4,
                metadata={"status": "insufficient_samples"},
                phase_key="first_step_slowdown_proxy_ms",
            )
        )
        self.assertEqual(candidate, 2)
        self.assertEqual(
            metadata["status"],
            "right_censored_speed_peak_confirmed_by_post_event_samples",
        )
        self.assertEqual(metadata["right_boundary_confirmation_indexes"], [3, 4])
        self.assertEqual(
            flag,
            f"{PHASE_RIGHT_CENSORED_PEAK_PREFIX}first_step_slowdown_proxy_ms",
        )

        for rejected_speed, rejected_timestamps in (
            (np.asarray([1.0, 2.0, 3.0, 3.0, 2.0]), timestamps),
            (np.asarray([1.0, 2.0, 3.0, np.nan, 1.0]), timestamps),
            (
                speed,
                np.asarray(
                    [
                        0,
                        40,
                        95,
                        95 + PHASE_RIGHT_BOUNDARY_CONFIRMATION_MAX_GAP_MS + 1,
                        300,
                    ],
                    dtype=np.int64,
                ),
            ),
        ):
            with self.subTest(
                speed=rejected_speed.tolist(), timestamps=rejected_timestamps.tolist()
            ):
                unavailable, rejected_metadata, no_flag = (
                    _right_boundary_speed_peak_confirmed_slowdown_phase(
                        candidate=None,
                        speed_signal=rejected_speed,
                        timestamps=rejected_timestamps,
                        peak=2,
                        event_end_index=2,
                        confirmation_end_index=4,
                        metadata={"status": "insufficient_samples"},
                        phase_key="first_step_slowdown_proxy_ms",
                    )
                )
                self.assertIsNone(unavailable)
                self.assertIsNone(no_flag)
                self.assertEqual(
                    rejected_metadata["right_boundary_confirmation_status"],
                    "not_confirmed",
                )

    def test_irregular_timestamps_produce_explainable_FS01_FS02_proxies(self) -> None:
        sequence = _irregular_phase_sequence()
        self.assertGreater(np.unique(np.diff(sequence.timestamp_ms)).size, 1)
        events = detect_pose_events(sequence, source_id="irregular-phase-test")
        by_code = {event["event_code"]: event for event in events}
        fs01 = by_code["FS01"]
        fs02 = by_code["FS02"]
        fs09 = by_code["FS09"]

        for event, names in (
            (
                fs01,
                (
                    "takeoff_proxy_ms",
                    "landing_proxy_ms",
                    "redistribution_ms",
                    "initiation_ms",
                ),
            ),
            (
                fs02,
                (
                    "direction_conversion_ms",
                    "support_extension_proxy_ms",
                    "lead_foot_motion_onset_proxy_ms",
                    "first_step_slowdown_proxy_ms",
                ),
            ),
        ):
            for name in names:
                candidate = event["key_phases_ms"][name]
                self.assertIsNotNone(candidate, name)
                self.assertIn(candidate, sequence.timestamp_ms)
                self.assertLessEqual(event["start_ms"], candidate)
                self.assertLessEqual(candidate, event["end_ms"])
                self.assertNotIn(f"{name[:-3]}_not_observed", event["quality_flags"])
            provenance = event["provenance"]
            self.assertEqual(
                provenance["phase_candidate_version"], PHASE_CANDIDATE_VERSION
            )
            self.assertEqual(
                provenance["phase_candidate_semantics"],
                "timestamp_ms_based_image_plane_pose_proxies_not_ground_contact_or_force",
            )

        self.assertLess(
            fs01["key_phases_ms"]["takeoff_proxy_ms"],
            fs01["key_phases_ms"]["landing_proxy_ms"],
        )
        self.assertLess(
            fs01["key_phases_ms"]["landing_proxy_ms"],
            fs01["key_phases_ms"]["initiation_ms"],
        )
        self.assertLessEqual(
            fs02["key_phases_ms"]["lead_foot_motion_onset_proxy_ms"],
            fs02["key_phases_ms"]["first_step_slowdown_proxy_ms"],
        )
        phase_metadata = fs02["provenance"]["phase_candidates"]
        self.assertEqual(phase_metadata["lead_foot_side_proxy"], "left")
        self.assertIn(
            "not force production",
            phase_metadata["definitions"]["support_extension_proxy_ms"],
        )
        self.assertIn(
            "not foot contact",
            phase_metadata["definitions"]["first_step_slowdown_proxy_ms"],
        )
        self.assertEqual(
            fs09["key_phases_ms"]["peak_speed_ms"],
            fs02["key_phases_ms"]["peak_speed_ms"],
        )
        for name in (
            "peak_speed_ms",
            "deceleration_peak_ms",
            "restabilization_onset_ms",
            "stable_control_onset_ms",
        ):
            candidate = fs09["key_phases_ms"][name]
            self.assertIsNotNone(candidate, name)
            self.assertIn(candidate, sequence.timestamp_ms)
            self.assertLessEqual(fs09["start_ms"], candidate)
            self.assertLessEqual(candidate, fs09["end_ms"])
            self.assertIn(
                name,
                fs09["provenance"]["phase_candidates"]["definitions"],
            )
        self.assertIn(
            "not a calibrated performance threshold",
            fs09["provenance"]["phase_candidates"]["definitions"][
                "peak_speed_ms"
            ],
        )

    def test_missing_ankles_emit_null_phases_and_traceable_flags(self) -> None:
        sequence = _irregular_phase_sequence(ankles_missing=True)
        events = detect_pose_events(sequence, source_id="missing-ankles-phase-test")
        by_code = {event["event_code"]: event for event in events}

        fs01 = by_code["FS01"]
        for name in (
            "takeoff_proxy_ms",
            "landing_proxy_ms",
            "redistribution_ms",
        ):
            self.assertIsNone(fs01["key_phases_ms"][name])
            self.assertIn(f"{name[:-3]}_not_observed", fs01["quality_flags"])

        fs02 = by_code["FS02"]
        for name in (
            "support_extension_proxy_ms",
            "lead_foot_motion_onset_proxy_ms",
            "first_step_slowdown_proxy_ms",
        ):
            self.assertIsNone(fs02["key_phases_ms"][name])
            self.assertIn(f"{name[:-3]}_not_observed", fs02["quality_flags"])

        # Body-center direction remains independently observable from shoulders/hips.
        self.assertIsNotNone(fs01["key_phases_ms"]["initiation_ms"])
        self.assertIsNotNone(fs02["key_phases_ms"]["direction_conversion_ms"])
        self.assertIsNone(
            fs02["provenance"]["phase_candidates"]["lead_foot_side_proxy"]
        )


if __name__ == "__main__":
    unittest.main()
