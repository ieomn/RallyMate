from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_script():
    path = ROOT / "scripts" / "run_small_roi_pose_recovery_experiment.py"
    spec = importlib.util.spec_from_file_location("run_small_roi_pose_recovery_experiment", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _frame(index: int, *, track: int | None, width: float = 15, pose: bool = False, extra_players: int = 0) -> dict:
    detections = []
    if track is not None:
        detections.append({"class_name": "player", "track_id": track, "bbox_px": [40, 40, 40 + width, 65]})
    for offset in range(extra_players):
        detections.insert(0, {"class_name": "player", "track_id": 100 + offset, "bbox_px": [0, 0, 80 - offset, 80 - offset]})
    return {
        "frame": {"processed_index": index, "index": index, "timestamp_ms": index * 40, "width": 100, "height": 100},
        "detections": detections,
        "poses": [{"person_track_id": track}] if pose and track is not None else [],
    }


class SmallRoiPoseRecoveryExperimentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = _load_script()

    def test_selector_only_targets_baseline_size_guard_skips(self) -> None:
        frames = [
            _frame(0, track=1),
            _frame(1, track=1, pose=True),
            _frame(2, track=None),
            _frame(3, track=1, extra_players=2),
            _frame(4, track=1, width=40),
        ]
        timeline = [{"processed_index": index, "source_track_id": 1} for index in range(5)]
        targets, counts = self.module.select_small_roi_targets(
            frame_rows=frames,
            timeline_rows=timeline,
            event_ranges=[(0, 200)],
            baseline_min_roi_size_px=32,
            experimental_min_roi_size_px=8,
            roi_margin=0.15,
            baseline_max_players=2,
        )
        self.assertEqual([0], [item["processed_index"] for item in targets])
        self.assertEqual(1, counts["selected_pose_already_present"])
        self.assertEqual(1, counts["selected_detection_missing"])
        self.assertEqual(1, counts["outside_baseline_area_schedule"])
        self.assertEqual(1, counts["pose_missing_not_explained_by_baseline_size_guard"])

    def test_validator_rejects_production_or_regression_claims(self) -> None:
        report = {
            "schema_version": "1.1.0",
            "experiment_version": "small-roi-pose-recovery-v1.1.0",
            "status": "experimental_observability_only_not_production",
            "inference": {"counts": {"inference_attempted": 2, "pose_output_recovered": 2, "pose_output_still_missing": 0}},
            "feature_vector_impact": {
                "recovered_indicator_instance_count": 2,
                "regressed_indicator_instance_count": 0,
            },
            "operational_measurement_impact": {
                "recovered_indicator_instance_count": 1,
                "regressed_indicator_instance_count": 0,
            },
            "safety": {
                "accuracy_claim": False, "ground_truth_provided": False, "production_enabled": False,
                "current_production_default_unchanged": True, "automatic_profile_fallback_enabled": False,
                "measurement_gate_modified": False, "scoring_gate_modified": False, "grades_generated": False,
                "thresholds_generated": False, "maturity_promoted": False,
                "feature_vector_status_is_not_operational_measurement_status": True,
                "recovered_means_observable_under_experimental_small_roi_not_accurate": True,
            },
        }
        self.module.validate_small_roi_experiment_report(report)
        report["safety"]["production_enabled"] = True
        with self.assertRaisesRegex(ValueError, "unsafe"):
            self.module.validate_small_roi_experiment_report(report)
        report["safety"]["production_enabled"] = False
        report["operational_measurement_impact"]["regressed_indicator_instance_count"] = 1
        with self.assertRaisesRegex(ValueError, "regressed"):
            self.module.validate_small_roi_experiment_report(report)

    def test_operational_recovery_preserves_original_measurement_gate(self) -> None:
        comparison = {
            "events": [
                {
                    "boundary": {"event_id": "event-1"},
                    "indicators": [
                        {
                            "indicator_id": "FS01-M02",
                            "models": {
                                "baseline": {"measurement_status": "measured"},
                                "experiment": {"measurement_status": "measured"},
                            },
                        }
                    ],
                },
                {
                    "boundary": {"event_id": "event-2"},
                    "indicators": [
                        {
                            "indicator_id": "FS01-M02",
                            "models": {
                                "baseline": {"measurement_status": "unavailable"},
                                "experiment": {"measurement_status": "measured"},
                            },
                        }
                    ],
                },
            ]
        }
        rows = [
            {
                "event_id": "event-1",
                "indicator_id": "FS01-M02",
                "features": [{"valid": True}],
                "feature_status": "unavailable",
                "quality_gate": {
                    "status": "hard_fail",
                    "measurement_allowed": False,
                },
            },
            {
                "event_id": "event-2",
                "indicator_id": "FS01-M02",
                "features": [{"valid": False}],
                "feature_status": "unavailable",
                "quality_gate": {
                    "status": "pass",
                    "measurement_allowed": True,
                },
            },
        ]

        impact = self.module.operational_measurement_transitions(
            comparison, "baseline", "experiment", rows
        )

        self.assertEqual(1, impact["recovered_indicator_instance_count"])
        self.assertEqual(0, impact["regressed_indicator_instance_count"])
        self.assertEqual(
            {"unavailable_to_measured": 1, "unavailable_to_unavailable": 1},
            impact["status_transitions"],
        )


if __name__ == "__main__":
    unittest.main()
