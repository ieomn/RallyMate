from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from rallymate_annotation.scoring_evidence_plan import (
    build_scoring_truth_evidence_plan,
    validate_scoring_truth_evidence_plan,
    validate_scoring_truth_evidence_plan_sources,
    write_scoring_truth_evidence_plan,
)


ROOT = Path(__file__).resolve().parents[1]
READINESS = ROOT / "reports" / "scoring-truth-action-readiness-halpe256-full-m45.json"


class ScoringTruthEvidencePlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.plan = build_scoring_truth_evidence_plan(
            readiness_path=READINESS,
            generated_at="2026-08-22T00:00:00+00:00",
        )

    def test_current_plan_covers_every_work_item_and_instance(self) -> None:
        plan = self.plan
        self.assertEqual(plan["status"], "annotation_required")
        self.assertEqual(plan["counts"]["input_work_items"], 168)
        self.assertEqual(plan["counts"]["input_indicator_instances"], 266)
        self.assertEqual(plan["counts"]["input_instance_action_links"], 481)
        self.assertEqual(
            plan["counts"]["by_status"]["annotation_required"],
            plan["counts"]["evidence_units"],
        )
        validate_scoring_truth_evidence_plan(plan)
        validate_scoring_truth_evidence_plan_sources(plan)

    def test_full_timeline_pose_truth_is_deduplicated_by_type(self) -> None:
        rows = {
            unit.get("diagnostic_type"): unit
            for unit in self.plan["units"]
            if unit["evidence_type"] == "full_timeline_pose_diagnostic_truth"
        }
        self.assertEqual(
            set(rows),
            {
                "keypoint_jump",
                "left_right_swap",
                "primary_identity_ambiguity",
                "source_track_switch",
            },
        )
        self.assertEqual(rows["keypoint_jump"]["work_item_count"], 54)
        self.assertEqual(rows["left_right_swap"]["work_item_count"], 28)
        self.assertEqual(rows["primary_identity_ambiguity"]["work_item_count"], 10)
        self.assertEqual(rows["source_track_switch"]["work_item_count"], 10)

    def test_every_input_work_item_has_at_least_one_unit(self) -> None:
        linked = {
            item_id
            for unit in self.plan["units"]
            for item_id in unit["linked_work_item_ids"]
        }
        source = json.loads(READINESS.read_text(encoding="utf-8"))
        self.assertEqual(linked, {row["work_item_id"] for row in source["items"]})

    def test_validator_rejects_forged_completion(self) -> None:
        forged = copy.deepcopy(self.plan)
        forged["units"][0]["status"] = "evidence_satisfied"
        with self.assertRaisesRegex(ValueError, "status counts|priority order"):
            validate_scoring_truth_evidence_plan(forged)

    def test_source_replay_rejects_synchronised_forgery(self) -> None:
        forged = copy.deepcopy(self.plan)
        forged["units"][0]["status"] = "evidence_satisfied"
        forged["units"].sort(
            key=lambda row: (
                row["status"] == "evidence_satisfied",
                -row["indicator_instance_count"],
                -row["work_item_count"],
                row["start_ms"] is None,
                row["start_ms"] or 0,
                row["deduplication_key"],
            )
        )
        for index, unit in enumerate(forged["units"], 1):
            unit["priority_rank"] = index
        forged["counts"]["by_status"] = {
            "evidence_satisfied": 1,
            "review_in_progress_not_adjudicated": 0,
            "annotation_required": forged["counts"]["evidence_units"] - 1,
        }
        forged["counts"]["by_evidence_type"]["full_timeline_pose_diagnostic_truth"] = {
            "total": 4,
            "evidence_satisfied": 1,
            "review_in_progress_not_adjudicated": 0,
            "annotation_required": 3,
        }
        forged["status"] = "evidence_acquisition_in_progress"
        validate_scoring_truth_evidence_plan(forged)
        with self.assertRaisesRegex(ValueError, "differs from source replay"):
            validate_scoring_truth_evidence_plan_sources(forged)

    def test_writer_outputs_video_first_non_scoring_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = write_scoring_truth_evidence_plan(
                self.plan, output_dir=Path(directory)
            )
            document = paths["html"].read_text(encoding="utf-8")
            self.assertIn("<video", document)
            self.assertIn("不会自动修改评分", document)
            self.assertNotIn("grade=A", document)
            self.assertTrue(paths["csv"].read_text(encoding="utf-8-sig").startswith("priority_rank,"))

    def test_machine_schema_accepts_real_plan(self) -> None:
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema unavailable")
        schema = json.loads(
            (ROOT / "contracts" / "scoring-truth-evidence-plan.schema.json").read_text(
                encoding="utf-8"
            )
        )
        jsonschema.Draft202012Validator(schema).validate(self.plan)


if __name__ == "__main__":
    unittest.main()
