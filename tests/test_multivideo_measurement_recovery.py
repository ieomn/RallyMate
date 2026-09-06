from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from rallymate_evaluation.multivideo_measurement_recovery import (
    build_multivideo_measurement_recovery_report,
    validate_multivideo_measurement_recovery_report,
)


ROOT = Path(__file__).resolve().parents[1]
GAPS = ROOT / "reports" / "measurement-recovery-m66" / "gap-audits"
EXPERIMENTS = (
    ROOT / "reports" / "measurement-recovery-m66" / "small-roi-v1.1"
)
VIDEO_IDS = (
    "3ae77ee3271d67de171585a5c39ddd69",
    "850cb0006b406c7176eeda8d711cd065",
    "8d7754d0de6d315674013d5b69a0b6ba",
)


def _build():
    return build_multivideo_measurement_recovery_report(
        recovery_id="synthetic-test-replay-of-real-m66-sources",
        gap_audit_paths=[GAPS / f"{video_id}.json" for video_id in VIDEO_IDS],
        experiment_report_paths=[
            EXPERIMENTS / video_id / "report.json" for video_id in VIDEO_IDS[1:]
        ],
        generated_at="2026-08-22T00:00:00+00:00",
    )


class MultivideoMeasurementRecoveryTests(unittest.TestCase):
    def test_real_sources_replay_to_exact_vector_and_operational_counts(self):
        report = _build()
        self.assertEqual(
            report["baseline"],
            {
                "indicator_instances": 2366,
                "operational_measured": 2291,
                "operational_unavailable": 75,
                "feature_vector_complete": 2297,
                "feature_vector_incomplete": 69,
                "measurement_hard_fail": 18,
            },
        )
        self.assertEqual(report["experiment"]["eligible_target_frames"], 149)
        self.assertEqual(report["experiment"]["pose_output_recovered_frames"], 149)
        self.assertEqual(report["experiment"]["feature_vector_recovered"], 22)
        self.assertEqual(
            report["experiment"]["operational_measurement_recovered"], 20
        )
        self.assertEqual(
            report["experimental_projection"],
            {
                "operational_measured": 2311,
                "operational_unavailable": 55,
                "feature_vector_complete": 2319,
                "feature_vector_incomplete": 47,
                "measurement_hard_fail": 18,
                "non_hard_fail_feature_incomplete": 37,
            },
        )
        self.assertFalse(report["safety"]["production_enabled"])
        self.assertFalse(report["safety"]["accuracy_claim"])

    def test_missing_experiment_for_an_eligible_video_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "require an experiment"):
            build_multivideo_measurement_recovery_report(
                recovery_id="missing-experiment",
                gap_audit_paths=[GAPS / f"{video_id}.json" for video_id in VIDEO_IDS],
                experiment_report_paths=[
                    EXPERIMENTS / VIDEO_IDS[2] / "report.json"
                ],
            )

    def test_self_reported_experiment_gain_is_replayed_not_trusted(self):
        source = EXPERIMENTS / VIDEO_IDS[1] / "report.json"
        payload = json.loads(source.read_text(encoding="utf-8"))
        payload["feature_vector_impact"]["recovered_indicator_instance_count"] += 1
        with TemporaryDirectory() as temp_dir:
            tampered = Path(temp_dir) / "report.json"
            tampered.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "did not replay"):
                build_multivideo_measurement_recovery_report(
                    recovery_id="tamper",
                    gap_audit_paths=[GAPS / f"{video_id}.json" for video_id in VIDEO_IDS],
                    experiment_report_paths=[
                        tampered,
                        EXPERIMENTS / VIDEO_IDS[2] / "report.json",
                    ],
                )

    def test_projection_cannot_change_hard_fail_count_or_safety(self):
        report = _build()
        tampered = copy.deepcopy(report)
        tampered["experimental_projection"]["measurement_hard_fail"] -= 1
        with self.assertRaisesRegex(ValueError, "cannot change measurement hard fails"):
            validate_multivideo_measurement_recovery_report(tampered)
        tampered = copy.deepcopy(report)
        tampered["safety"]["automatic_fallback_enabled"] = True
        with self.assertRaisesRegex(ValueError, "unsafe claim"):
            validate_multivideo_measurement_recovery_report(tampered)


if __name__ == "__main__":
    unittest.main()
