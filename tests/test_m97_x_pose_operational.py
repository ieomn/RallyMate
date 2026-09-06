from __future__ import annotations

import copy
import unittest
from pathlib import Path
from unittest.mock import patch

import rallymate_evaluation.pose_x_operational as operational
from rallymate_evaluation.pose_x_operational import (
    PER_VIDEO_REPORT_VERSION,
    PoseXOperationalError,
    assert_m71_comparable_scope,
    compare_recovery_sets,
    expected_per_video_status,
    latency_distribution,
    load_x_operational_protocol,
    validate_per_video_report,
)


ROOT = Path(__file__).resolve().parents[1]
VIDEO_ID = "3ae77ee3271d67de171585a5c39ddd69"


def _impact(
    recovered: tuple[tuple[str, str], ...] = (),
    regressed: tuple[tuple[str, str], ...] = (),
) -> dict[str, object]:
    return {
        "recovered_indicator_instance_count": len(recovered),
        "regressed_indicator_instance_count": len(regressed),
        "recovered_indicator_instances": [
            {"event_id": event_id, "indicator_id": indicator_id}
            for event_id, indicator_id in recovered
        ],
        "regressed_indicator_instances": [
            {"event_id": event_id, "indicator_id": indicator_id}
            for event_id, indicator_id in regressed
        ],
    }


def _report() -> dict[str, object]:
    candidate_feature = _impact((("event-1", "FS01-M01"),))
    candidate_operational = _impact((("event-1", "FS01-M01"),))
    m70_feature = _impact((("event-1", "FS01-M01"),))
    m70_operational = _impact((("event-1", "FS01-M01"),))
    m71_feature = _impact(
        (("event-1", "FS01-M01"), ("event-2", "FS02-M01"))
    )
    m71_operational = _impact(
        (("event-1", "FS01-M01"), ("event-2", "FS02-M01"))
    )
    report: dict[str, object] = {
        "schema_version": "1.0.0",
        "report_version": PER_VIDEO_REPORT_VERSION,
        "status": "pending",
        "video_id": VIDEO_ID,
        "comparability": {
            "same_processed_and_source_frames_as_M71": True,
            "same_timestamps_tracks_and_ROI_contexts_as_M71": True,
            "same_analysis_profile_and_flip_test_as_M71": True,
            "same_M70_router_baseline_as_M71": True,
            "same_M68_fixed_boundary_comparison_baseline_as_M71": True,
        },
        "inference": {
            "target_frame_count": 2,
            "observation_row_count": 2,
            "pose_output_produced": 2,
            "pose_output_missing": 0,
            "reason_counts": {"candidate_pose_produced": 2},
            "latency_ms": latency_distribution([1.0, 2.0]),
        },
        "router_audit": {
            "selected_frame_count": 1,
            "rejected_frame_count": 1,
            "selection_uses_feature_values": False,
            "selection_uses_event_outcomes": False,
            "selection_uses_grades_or_thresholds": False,
            "required_joint_validity_regression_allowed": False,
        },
        "comparison_to_m68": {
            "feature_vector": candidate_feature,
            "operational_measurement": candidate_operational,
        },
        "comparison_to_m70": {
            "feature_vector": compare_recovery_sets(candidate_feature, m70_feature),
            "operational_measurement": compare_recovery_sets(
                candidate_operational, m70_operational
            ),
        },
        "comparison_to_m71": {
            "feature_vector": compare_recovery_sets(candidate_feature, m71_feature),
            "operational_measurement": compare_recovery_sets(
                candidate_operational, m71_operational
            ),
        },
        "decision": {
            "production_default_changed": False,
            "candidate_promoted": False,
            "operational_gain_is_observability_not_accuracy": True,
        },
        "safety": {
            field: False
            for field in (
                "accuracy_claim",
                "ground_truth_provided",
                "sealed_holdout_opened_or_hashed",
                "production_enabled",
                "automatic_profile_fallback_enabled",
                "feature_gate_modified",
                "measurement_gate_modified",
                "event_boundaries_modified",
                "routing_uses_feature_values",
                "routing_uses_event_outcomes",
                "routing_uses_grades_or_thresholds",
                "grades_generated",
                "thresholds_generated",
                "maturity_promoted",
            )
        },
    }
    report["status"] = expected_per_video_status(report)
    return report


class M97XOperationalTests(unittest.TestCase):
    def test_protocol_binds_only_three_development_videos_and_never_hashes_holdout(self) -> None:
        hashed: list[Path] = []
        real_sha256 = operational.sha256_file

        def recording_sha256(path: str | Path) -> str:
            hashed.append(Path(path).resolve())
            return real_sha256(path)

        with patch.object(operational, "sha256_file", side_effect=recording_sha256):
            verified = load_x_operational_protocol(ROOT)
        self.assertEqual(sum(item["target_frame_count"] for item in verified["videos"]), 258)
        self.assertEqual(len(verified["videos"]), 3)
        self.assertTrue(verified["production_default"]["unchanged"])
        self.assertFalse(verified["holdout_guard"]["file_opened_or_hashed"])
        holdout = (ROOT / verified["holdout_guard"]["declared_relative_path"]).resolve()
        self.assertNotIn(holdout, hashed)

    def test_frame_and_roi_comparability_fails_closed_on_context_drift(self) -> None:
        rows = [
            {
                "processed_index": 4,
                "source_frame_index": 8,
                "timestamp_ms": 267,
                "track_id": 1,
                "roi_margin": 0.15,
                "min_roi_size_px": 32,
            },
            {
                "processed_index": 5,
                "source_frame_index": 10,
                "timestamp_ms": 333,
                "track_id": 1,
                "roi_margin": 0.30,
                "min_roi_size_px": 32,
            },
        ]
        ablation = {"video_id": VIDEO_ID, "inference": {"frame_results": rows}}
        m71 = {
            "video_id": VIDEO_ID,
            "inference": {"frame_results": copy.deepcopy(rows)},
        }
        self.assertEqual(
            assert_m71_comparable_scope(
                ablation, m71, expected_video_id=VIDEO_ID, expected_target_count=2
            ),
            rows,
        )
        m71["inference"]["frame_results"][1]["roi_margin"] = 0.15
        with self.assertRaises(PoseXOperationalError):
            assert_m71_comparable_scope(
                ablation, m71, expected_video_id=VIDEO_ID, expected_target_count=2
            )

    def test_recovery_set_comparison_reports_additions_and_losses(self) -> None:
        candidate = _impact(
            (("e1", "i1"), ("e3", "i3")),
            (("e4", "i4"),),
        )
        reference = _impact(
            (("e1", "i1"), ("e2", "i2")),
            (),
        )
        result = compare_recovery_sets(candidate, reference)
        self.assertEqual(result["shared_recovery_count"], 1)
        self.assertEqual(result["additional_recoveries"], [{"event_id": "e3", "indicator_id": "i3"}])
        self.assertEqual(result["lost_reference_recoveries"], [{"event_id": "e2", "indicator_id": "i2"}])
        self.assertEqual(result["additional_candidate_regression_count"], 1)

    def test_per_video_status_exposes_m71_loss_and_claims_fail_closed(self) -> None:
        report = _report()
        self.assertEqual(
            report["status"],
            "experimental_x_extension_regression_free_to_m70_but_does_not_preserve_m71_recovery",
        )
        validate_per_video_report(report)

        bad = copy.deepcopy(report)
        bad["status"] = "experimental_x_extension_preserves_m71_recovery_requires_truth"
        with self.assertRaises(PoseXOperationalError):
            validate_per_video_report(bad)

        bad = copy.deepcopy(report)
        bad["safety"]["accuracy_claim"] = True
        with self.assertRaises(PoseXOperationalError):
            validate_per_video_report(bad)

        bad = copy.deepcopy(report)
        bad["comparison_to_m71"]["feature_vector"]["lost_reference_recovery_count"] = 0
        with self.assertRaises(PoseXOperationalError):
            validate_per_video_report(bad)

    def test_latency_distribution_is_exact_and_rejects_nonfinite_values(self) -> None:
        self.assertEqual(
            latency_distribution([1.0, 2.0, 3.0, 4.0]),
            {"count": 4, "mean": 2.5, "p50": 2.5, "p95": 3.85, "min": 1.0, "max": 4.0},
        )
        with self.assertRaises(PoseXOperationalError):
            latency_distribution([1.0, float("nan")])


if __name__ == "__main__":
    unittest.main()
