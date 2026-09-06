from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from rallymate_evaluation.event_bounded_pose_gaps import (
    validate_event_bounded_pose_gap_report,
    validate_event_bounded_pose_gap_report_sources,
)


ROOT = Path(__file__).resolve().parents[1]
REPORT = (
    ROOT
    / "reports"
    / "measurement-recovery-m74"
    / "event-bounded-gap-audit-v1"
    / "report.json"
)


class M74EventBoundedGapAuditTests(unittest.TestCase):
    def test_real_report_is_source_bound_and_keeps_production_unchanged(self) -> None:
        report = json.loads(REPORT.read_text(encoding="utf-8"))
        validate_event_bounded_pose_gap_report_sources(report)
        summary = report["counterfactual_summary"]
        self.assertEqual(13, report["residual_scope"]["indicator_instance_count"])
        self.assertEqual(142, summary["unique_event_joint_observation_count"])
        self.assertEqual(7, summary["counterfactual_recovered_indicator_instance_count"])
        self.assertEqual(0, summary["production_recovered_indicator_instance_count"])
        self.assertFalse(report["decision"]["materialize_production_pose_artifact"])

    def test_report_rejects_accuracy_or_hidden_production_recovery(self) -> None:
        report = json.loads(REPORT.read_text(encoding="utf-8"))
        mutated = copy.deepcopy(report)
        mutated["safety"]["accuracy_claim"] = True
        with self.assertRaisesRegex(ValueError, "fail closed"):
            validate_event_bounded_pose_gap_report(mutated)
        mutated = copy.deepcopy(report)
        mutated["counterfactual_summary"][
            "counterfactual_recovered_indicator_instance_count"
        ] = 8
        with self.assertRaisesRegex(ValueError, "recovery count"):
            validate_event_bounded_pose_gap_report(mutated)


if __name__ == "__main__":
    unittest.main()
