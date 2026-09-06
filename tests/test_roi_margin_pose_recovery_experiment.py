from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_script():
    path = ROOT / "scripts" / "run_roi_margin_pose_recovery_experiment.py"
    spec = importlib.util.spec_from_file_location(
        "run_roi_margin_pose_recovery_experiment", path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _frame(
    index: int,
    *,
    track: int | None = 1,
    pose: bool = True,
    bbox: list[float] | None = None,
    extra_players: int = 0,
) -> dict:
    detections = []
    if track is not None:
        detections.append(
            {
                "class_name": "player",
                "track_id": track,
                "bbox_px": bbox or [30, 20, 70, 80],
            }
        )
    for offset in range(extra_players):
        detections.insert(
            0,
            {
                "class_name": "player",
                "track_id": 100 + offset,
                "bbox_px": [0, 0, 95 - offset, 95 - offset],
            },
        )
    return {
        "frame": {
            "processed_index": index,
            "index": index,
            "timestamp_ms": index * 40,
            "width": 100,
            "height": 100,
        },
        "detections": detections,
        "poses": [
            {
                "person_track_id": track,
                "keypoints": [
                    {"confidence": 0.9, "in_frame": True},
                    {"confidence": 0.1, "in_frame": True},
                ],
            }
        ]
        if pose and track is not None
        else [],
    }


class RoiMarginPoseRecoveryExperimentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = _load_script()

    def test_selector_targets_only_existing_pose_and_changed_crop(self) -> None:
        frames = [
            _frame(0),
            _frame(1, pose=False),
            _frame(2, track=None, pose=False),
            _frame(3, extra_players=2),
            _frame(4, bbox=[0, 0, 100, 100]),
        ]
        timeline = [
            {"processed_index": index, "source_track_id": 1}
            for index in range(len(frames))
        ]
        targets, counts = self.module.select_roi_margin_targets(
            frame_rows=frames,
            timeline_rows=timeline,
            event_ranges=[(0, 200)],
            baseline_roi_margin=0.15,
            experimental_roi_margin=0.30,
            min_roi_size_px=32,
            baseline_max_players=2,
        )
        self.assertEqual([0], [item["processed_index"] for item in targets])
        self.assertEqual(1, targets[0]["baseline_valid_keypoint_count"])
        self.assertEqual(1, counts["selected_pose_missing"])
        self.assertEqual(1, counts["selected_detection_missing"])
        self.assertEqual(1, counts["outside_baseline_area_schedule"])
        self.assertEqual(1, counts["experimental_crop_unchanged_after_clamp"])

    def test_selector_can_preselect_only_clipped_baseline_rois(self) -> None:
        frames = [
            _frame(0),
            _frame(1, bbox=[0, 20, 40, 80]),
        ]
        timeline = [
            {"processed_index": index, "source_track_id": 1}
            for index in range(len(frames))
        ]
        targets, counts = self.module.select_roi_margin_targets(
            frame_rows=frames,
            timeline_rows=timeline,
            event_ranges=[(0, 80)],
            baseline_roi_margin=0.15,
            experimental_roi_margin=0.30,
            min_roi_size_px=32,
            baseline_max_players=2,
            require_baseline_crop_clipped=True,
        )
        self.assertEqual([1], [item["processed_index"] for item in targets])
        self.assertTrue(targets[0]["baseline_crop_clipped"])
        self.assertEqual(1, counts["baseline_crop_not_clipped"])

    def test_selector_rejects_nonexpanding_margin_and_duplicate_timeline(self) -> None:
        frame = _frame(0)
        with self.assertRaisesRegex(ValueError, "greater"):
            self.module.select_roi_margin_targets(
                frame_rows=[frame],
                timeline_rows=[{"processed_index": 0, "source_track_id": 1}],
                event_ranges=[(0, 40)],
                baseline_roi_margin=0.15,
                experimental_roi_margin=0.15,
                min_roi_size_px=32,
                baseline_max_players=2,
            )
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.module.select_roi_margin_targets(
                frame_rows=[frame],
                timeline_rows=[
                    {"processed_index": 0, "source_track_id": 1},
                    {"processed_index": 0, "source_track_id": 1},
                ],
                event_ranges=[(0, 40)],
                baseline_roi_margin=0.15,
                experimental_roi_margin=0.30,
                min_roi_size_px=32,
                baseline_max_players=2,
            )

    def test_report_validator_keeps_results_experimental_even_with_gain(self) -> None:
        impact = {
            "status_transitions": {"unavailable_to_measured": 1},
            "recovered_indicator_instance_count": 1,
            "regressed_indicator_instance_count": 0,
            "recovered_indicator_instances": [
                {"event_id": "event-1", "indicator_id": "FS01-M02"}
            ],
            "regressed_indicator_instances": [],
        }
        report = {
            "schema_version": "1.0.0",
            "experiment_version": "roi-margin-pose-recovery-v1.0.0",
            "status": "experimental_observability_only_not_production",
            "settings": {
                "baseline_roi_margin": 0.15,
                "experimental_roi_margin": 0.30,
            },
            "inference": {
                "counts": {
                    "inference_attempted": 1,
                    "pose_output_produced": 1,
                    "pose_output_missing": 0,
                },
                "frame_results": [
                    {
                        "processed_index": 0,
                    }
                ],
            },
            "feature_vector_impact": impact,
            "operational_measurement_impact": impact,
            "safety": {
                "accuracy_claim": False,
                "ground_truth_provided": False,
                "production_enabled": False,
                "current_production_default_unchanged": True,
                "automatic_profile_fallback_enabled": False,
                "measurement_gate_modified": False,
                "scoring_gate_modified": False,
                "event_boundaries_modified": False,
                "grades_generated": False,
                "thresholds_generated": False,
                "maturity_promoted": False,
                "same_model_weights_detection_track_and_event_boundaries": True,
                "experimental_frames_replace_same_track_pose_without_baseline_fallback": True,
                "feature_vector_status_is_not_operational_measurement_status": True,
                "recovered_means_observable_under_experimental_crop_not_accurate": True,
            },
        }
        self.module.validate_roi_margin_experiment_report(report)
        report["safety"]["production_enabled"] = True
        with self.assertRaisesRegex(ValueError, "unsafe"):
            self.module.validate_roi_margin_experiment_report(report)


if __name__ == "__main__":
    unittest.main()
