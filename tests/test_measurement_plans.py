from __future__ import annotations

import copy
import unittest
from pathlib import Path

from rallymate_scoring.feasibility import (
    indicator_ids_at_or_above,
    load_feasibility_registry,
)
from rallymate_scoring.measurement_plans import build_measurement_plan_registry, classify_clause


ROOT = Path(__file__).resolve().parents[1]
V2_FEASIBILITY_PATH = ROOT / "metric-feasibility-pose-wave-v2.json"
PROMOTED_POSE_WAVE = {
    "FS01-M03",
    "FS01-M04",
    "FS02-M03",
    "FS02-M04",
    "FS02-M05",
}


class MeasurementPlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.feasibility = load_feasibility_registry(V2_FEASIBILITY_PATH)
        cls.current_f2 = indicator_ids_at_or_above(cls.feasibility, "F2")
        cls.registry = build_measurement_plan_registry(
            generated_at="2026-08-13T00:00:00+00:00",
            feasibility_registry=cls.feasibility,
        )
        cls.by_id = {plan["indicator_id"]: plan for plan in cls.registry["plans"]}

    def test_all_298_cards_compile_to_unique_traceable_plans(self) -> None:
        self.assertEqual(298, len(self.registry["plans"]))
        self.assertEqual(298, len(self.by_id))
        for plan in self.registry["plans"]:
            self.assertTrue(plan["measurement_requirements"])
            self.assertTrue(plan["primitive_ids"])
            for requirement in plan["measurement_requirements"]:
                self.assertTrue(requirement["source_clause"])
                self.assertTrue(requirement["primitive_ids"])

    def test_current_f2_is_derived_from_registry_and_current_scope_is_complete(self) -> None:
        self.assertEqual(13, len(self.current_f2))
        self.assertEqual(
            self.current_f2,
            {
                indicator_id
                for indicator_id, plan in self.by_id.items()
                if plan["runtime_status"] == "implemented_f2_calibration_required"
            },
        )
        self.assertEqual(
            set(),
            {
                indicator_id
                for indicator_id, plan in self.by_id.items()
                if plan["next_implementation_wave"]
            },
        )
        self.assertTrue(PROMOTED_POSE_WAVE.issubset(self.current_f2))
        self.assertEqual(
            self.feasibility["registry_version"],
            self.registry["source_feasibility_registry_version"],
        )
        self.assertEqual(
            sorted(self.current_f2),
            self.registry["summary"]["current_f2_indicator_ids"],
        )

    def test_changing_feasibility_maturity_changes_plans_without_code_list(self) -> None:
        downgraded = copy.deepcopy(self.feasibility)
        fs09_m01 = next(
            item
            for item in downgraded["indicators"]
            if item["indicator_id"] == "FS09-M01"
        )
        fs09_m01["feasibility_level"] = "F1"
        registry = build_measurement_plan_registry(
            generated_at="2026-08-13T00:00:00+00:00",
            feasibility_registry=downgraded,
        )
        by_id = {plan["indicator_id"]: plan for plan in registry["plans"]}
        self.assertEqual(12, registry["summary"]["current_f2_indicator_count"])
        self.assertEqual(1, registry["summary"]["next_pose_wave_count"])
        self.assertEqual(
            "feature_implementation_required",
            by_id["FS09-M01"]["runtime_status"],
        )
        self.assertTrue(by_id["FS09-M01"]["next_implementation_wave"])

    def test_existing_f3_or_f4_metric_remains_feature_measurable(self) -> None:
        promoted = copy.deepcopy(self.feasibility)
        promoted["indicators"][0]["feasibility_level"] = "F3"
        registry = build_measurement_plan_registry(
            generated_at="2026-08-13T00:00:00+00:00",
            feasibility_registry=promoted,
        )
        by_id = {plan["indicator_id"]: plan for plan in registry["plans"]}
        indicator_id = promoted["indicators"][0]["indicator_id"]
        self.assertEqual(
            "implemented_f2_calibration_required",
            by_id[indicator_id]["runtime_status"],
        )

    def test_no_plan_can_emit_a_grade_or_threshold_without_calibration(self) -> None:
        self.assertEqual("forbidden", self.registry["policy"]["grade_without_calibration"])
        self.assertFalse(self.registry["policy"]["frame_coverage_is_accuracy"])
        for plan in self.registry["plans"]:
            self.assertIsNone(plan["output_policy"]["grade"])
            self.assertIsNone(plan["output_policy"]["threshold_version"])
            self.assertIn(plan["output_policy"]["when_valid_features"], {"calibration_required"})
            self.assertEqual("unavailable", plan["output_policy"]["when_invalid_or_missing"])

    def test_known_source_clauses_map_to_required_observation_families(self) -> None:
        self.assertIn("pose.body_center_kinematics", classify_clause("髋中心二维速度矢量"))
        self.assertIn("pose.foot_support_kinematics", classify_clause("双踝相对地面基线的上移量"))
        self.assertIn("racket.keypoint_geometry", classify_clause("球拍关键点轨迹/拍轴/拍面二维投影（专项模型后）"))
        self.assertIn("ball.trajectory_geometry", classify_clause("球轨迹"))
        self.assertIn("court.metric_geometry", classify_clause("场地坐标轨迹平滑度"))

    def test_five_new_current_event_pose_only_indicators_are_F2(self) -> None:
        self.assertEqual(5, len(PROMOTED_POSE_WAVE))
        for indicator_id in PROMOTED_POSE_WAVE:
            plan = self.by_id[indicator_id]
            self.assertEqual(["pose"], plan["dependencies"])
            self.assertEqual("candidate_rule_baseline", plan["event_localization"]["status"])
            self.assertEqual(
                "implemented_f2_calibration_required", plan["runtime_status"]
            )
            self.assertFalse(plan["next_implementation_wave"])

    def test_coarse_partial_primitives_are_not_promoted_by_status_sync(self) -> None:
        catalog = {
            item["primitive_id"]: item for item in self.registry["primitive_catalog"]
        }
        self.assertEqual(
            "partial_feature_expansion_required",
            catalog["pose.foot_support_kinematics"]["implementation_status"],
        )
        self.assertEqual(
            "partial_feature_expansion_required",
            catalog["pose.action_continuity"]["implementation_status"],
        )


if __name__ == "__main__":
    unittest.main()
