from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_script():
    path = ROOT / "scripts" / "build_m67_measurement_recovery_summary.py"
    spec = importlib.util.spec_from_file_location("build_m67_summary", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _margin(video_id: str, recovered: int, regressed: int) -> dict:
    impact = {
        "recovered_indicator_instance_count": recovered,
        "regressed_indicator_instance_count": regressed,
    }
    return {
        "status": "experimental_observability_only_not_production",
        "video_id": video_id,
        "inference": {
            "counts": {"inference_attempted": 2, "pose_output_produced": 2},
            "valid_keypoint_count_transition_counts": {"increased": 1, "decreased": 1},
        },
        "feature_vector_impact": impact,
        "operational_measurement_impact": impact,
        "safety": {"accuracy_claim": False, "production_enabled": False},
    }


def _combined(video_id: str, recovered: int, regressed: int) -> dict:
    value = _margin(video_id, recovered, regressed)
    value["composition_audit"] = {
        "target_sets_disjoint": True,
        "target_frame_count": 3,
        "non_pose_frame_data_preserved": True,
        "non_selected_poses_preserved": True,
    }
    return value


class M67MeasurementRecoverySummaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = _load_script()

    def test_summary_recomputes_balanced_projection_and_keeps_it_experimental(self) -> None:
        m66 = {
            "scope": {
                "indicator_count": 13,
                "current_profile": "pose",
                "registry_version": "registry",
                "registry_sha256": "A" * 64,
            },
            "baseline": {
                "indicator_instances": 30,
                "operational_measured": 20,
                "operational_unavailable": 10,
                "feature_vector_complete": 22,
                "feature_vector_incomplete": 8,
                "measurement_hard_fail": 2,
            },
            "experiment": {
                "eligible_target_frames": 3,
                "pose_output_recovered_frames": 3,
                "feature_vector_recovered": 2,
                "regressed_feature_vectors": 0,
                "operational_measurement_recovered": 2,
                "regressed_operational_measurements": 0,
            },
        }
        videos = ["a", "b", "c"]
        margin = [_margin(video, 1, 0) for video in videos]
        clipped = [_margin(video, 0, 0) for video in videos]
        combined = [_combined(video, 2, 0) for video in videos]
        report = self.module.build_summary(
            m66=m66,
            margin_reports=margin,
            clipped_reports=clipped,
            combined_reports=combined,
            source_records={},
        )
        self.assertEqual(26, report["composed_experimental_projection"]["operational_measured"])
        self.assertEqual(4, report["composed_experimental_projection"]["operational_unavailable"])
        self.assertFalse(report["decision"]["production_default_changed"])
        self.assertFalse(report["safety"]["accuracy_claim"])


if __name__ == "__main__":
    unittest.main()
