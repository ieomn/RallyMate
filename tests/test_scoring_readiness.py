from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from rallymate_scoring.granularity import (
    analyze_scoring_readiness,
    static_model_capability,
)
from rallymate_scoring.report import write_analysis_report

ROOT = Path(__file__).resolve().parents[1]


POSE_WAVE_V2_INDICATORS = (
    "FS01-M02",
    "FS01-M03",
    "FS01-M04",
    "FS01-M05",
    "FS02-M02",
    "FS02-M03",
    "FS02-M04",
    "FS02-M05",
    "FS09-M01",
    "FS09-M02",
    "FS09-M03",
    "FS09-M04",
    "FS09-M05",
)


class ScoringReadinessTests(unittest.TestCase):
    def _frames(self, path: Path, count: int = 10) -> None:
        with path.open("w", encoding="utf-8") as handle:
            for index in range(count):
                detections = [
                    {"class_name": "player", "track_id": 1},
                    {"class_name": "ball", "track_id": 2},
                ]
                if index < 6:
                    detections.append({"class_name": "racket", "track_id": 3})
                record = {
                    "detections": detections,
                    "poses": [
                        {
                            "person_track_id": 1,
                            "keypoints": [
                                {
                                    "downstream_joint_id": joint_id,
                                    "confidence": 0.9,
                                }
                                for joint_id in (
                                    "J004",
                                    "J006",
                                    "J007",
                                    "J033",
                                    "J034",
                                    "J071",
                                    "J072",
                                    "J101",
                                    "J121",
                                    "J103",
                                    "J123",
                                    "J141",
                                    "J161",
                                    "J143",
                                    "J163",
                                )
                            ],
                        }
                    ],
                }
                handle.write(json.dumps(record) + "\n")

    def test_static_granularity_matches_298_card_contract(self) -> None:
        report = static_model_capability()
        self.assertEqual(report["indicator_count"], 298)
        self.assertEqual(report["domains"], {"GS": 248, "FS": 50})
        self.assertEqual(
            report["pose_assessment"]["pose_dependent_indicators"], 291
        )
        self.assertEqual(
            report["pose_assessment"][
                "explicit_joint_mapping_complete_indicators"
            ],
            291,
        )
        self.assertEqual(
            sum(report["model_granularity"].values()), 298
        )
        self.assertEqual(report["model_granularity"]["unsupported"], 106)

    def test_video_evidence_is_audited_without_fabricating_scores(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            frames = Path(directory) / "frames.jsonl"
            self._frames(frames)
            summary = {
                "job_id": "test-job",
                "coverage": {
                    "pose_frame_fraction": 1.0,
                    "ball_frame_fraction": 1.0,
                    "racket_frame_fraction": 0.6,
                    "court_detected_fraction": 1.0,
                    "court_calibrated_fraction": 0.0,
                },
            }
            report = analyze_scoring_readiness(summary, frames)
            self.assertEqual(report["summary"]["indicator_count"], 298)
            self.assertEqual(report["summary"]["score_ready"], 0)
            self.assertEqual(report["summary"]["score_blocked"], 298)
            self.assertEqual(len(report["indicator_results"]), 298)
            self.assertEqual(
                report["observed_model_evidence"][
                    "primary_pose_track_fraction"
                ],
                1.0,
            )
            self.assertTrue(
                all(
                    "event_segmentation_not_implemented" in result["blockers"]
                    for result in report["indicator_results"]
                )
            )
            html_path = Path(directory) / "analysis-report.html"
            write_analysis_report(summary, report, html_path)
            rendered = html_path.read_text(encoding="utf-8")
            self.assertIn("模型推理与评分颗粒度完整分析报告", rendered)
            self.assertIn("没有评分不等于零分", rendered)
            self.assertNotIn("当前最终可评分项为 0 / 298", rendered)
            self.assertEqual(rendered.count('class="indicator"'), 298)

    def test_six_minimum_loop_indicators_are_measured_not_reported_as_zero(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            frames = Path(directory) / "frames.jsonl"
            self._frames(frames)
            validity = {
                indicator_id: {"valid": 2, "total": 3, "valid_rate": 2 / 3}
                for indicator_id in (
                    "FS01-M02",
                    "FS01-M05",
                    "FS02-M02",
                    "FS09-M03",
                    "FS09-M04",
                    "FS09-M05",
                )
            }
            summary = {
                "job_id": "test-job",
                "coverage": {
                    "pose_frame_fraction": 1.0,
                    "ball_frame_fraction": 0.0,
                    "racket_frame_fraction": 0.0,
                    "court_calibrated_fraction": 0.0,
                },
                "minimum_scoring_loop": {
                    "indicator_feature_validity": validity,
                },
            }
            report = analyze_scoring_readiness(summary, frames)
            target = {
                item["indicator_id"]: item
                for item in report["indicator_results"]
                if item["indicator_id"] in validity
            }
            self.assertEqual(len(target), 6)
            self.assertTrue(
                all(item["measurement_status"] == "measured" for item in target.values())
            )
            self.assertTrue(
                all(item["scoring_status"] == "calibration_required" for item in target.values())
            )
            self.assertTrue(
                all("event_segmentation_not_implemented" not in item["blockers"] for item in target.values())
            )
            self.assertEqual(
                report["summary"]["minimum_scoring_loop"]["measured_indicator_count"],
                6,
            )
            html_path = Path(directory) / "analysis-report.html"
            write_analysis_report(summary, report, html_path)
            rendered = html_path.read_text(encoding="utf-8")
            self.assertIn("6 项指标已接入闭环", rendered)
            self.assertIn("其余 292 项不在本轮评分闭环", rendered)
            self.assertIn("F2 · 特征已测量", rendered)
            self.assertNotIn("当前最终可评分项为 0 / 298", rendered)

    def test_pose_wave_v2_runtime_membership_drives_13_indicator_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            frames = Path(directory) / "frames.jsonl"
            self._frames(frames)
            validity = {
                indicator_id: {
                    "valid": 2 if indicator_id not in {"FS01-M03", "FS01-M04"} else 0,
                    "total": 3,
                    "valid_rate": 2 / 3 if indicator_id not in {"FS01-M03", "FS01-M04"} else 0.0,
                }
                for indicator_id in POSE_WAVE_V2_INDICATORS
            }
            registry_path = ROOT / "metric-feasibility-pose-wave-v2.json"
            summary = {
                "job_id": "pose-wave-v2-job",
                "coverage": {
                    "pose_frame_fraction": 1.0,
                    "ball_frame_fraction": 0.0,
                    "racket_frame_fraction": 0.0,
                    "court_calibrated_fraction": 0.0,
                },
                "minimum_scoring_loop": {
                    "scope": {"events": ["FS01", "FS02", "FS09"], "indicator_count": 13},
                    "indicator_feature_validity": validity,
                    "model_versions": {
                        "feasibility_registry": "pose-wave-2026-08-13.2",
                        "pose_backend": "rtmpose",
                        "feature": "rallymate-features-v0.1.0",
                    },
                    "provenance": {
                        "feasibility_registry_path": str(registry_path),
                        "feasibility_registry_sha256": "registry-sha-for-test",
                    },
                },
            }

            report = analyze_scoring_readiness(summary, frames)
            loop = report["summary"]["minimum_scoring_loop"]
            self.assertEqual(loop["indicator_count"], 13)
            self.assertEqual(loop["recognized_indicator_count"], 13)
            self.assertEqual(loop["not_in_loop_indicator_count"], 285)
            self.assertEqual(loop["measured_indicator_count"], 11)
            self.assertEqual(loop["unavailable_indicator_count"], 2)
            self.assertEqual(loop["feasibility_levels"], {"F2": 13})
            self.assertEqual(loop["registry"]["version"], "pose-wave-2026-08-13.2")
            self.assertEqual(loop["registry"]["read_status"], "loaded")
            self.assertEqual(loop["registry"]["sha256"], "registry-sha-for-test")
            self.assertEqual(loop["source"]["event_codes"], ["FS01", "FS02", "FS09"])
            self.assertEqual(loop["source"]["pose_backend"], "rtmpose")
            self.assertEqual(
                loop["source"]["feature_version"], "rallymate-features-v0.1.0"
            )
            self.assertEqual(set(loop["indicator_ids"]), set(POSE_WAVE_V2_INDICATORS))
            target_results = {
                item["indicator_id"]: item
                for item in report["indicator_results"]
                if item["indicator_id"] in validity
            }
            self.assertEqual(len(target_results), 13)
            self.assertTrue(
                all(item["feasibility_level"] == "F2" for item in target_results.values())
            )
            self.assertTrue(
                all(not item["score_ready"] for item in target_results.values())
            )

            html_path = Path(directory) / "analysis-report.html"
            write_analysis_report(summary, report, html_path)
            rendered = html_path.read_text(encoding="utf-8")
            self.assertIn("13 项指标已接入闭环", rendered)
            self.assertIn("11/13", rendered)
            self.assertIn("其余 285 项不在本轮评分闭环", rendered)
            self.assertIn("pose-wave-2026-08-13.2", rendered)
            self.assertNotIn("六指标", rendered)
            self.assertNotIn("其余 292 项", rendered)
            self.assertNotIn("F3 ·", rendered)

    def test_f4_scored_indicator_is_rendered_as_event_grade_without_total_score(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            frames = Path(directory) / "frames.jsonl"
            self._frames(frames)
            summary = {
                "job_id": "trusted-f4-report-test",
                "coverage": {
                    "pose_frame_fraction": 1.0,
                    "ball_frame_fraction": 0.0,
                    "racket_frame_fraction": 0.0,
                    "court_calibrated_fraction": 0.0,
                },
                "minimum_scoring_loop": {
                    "scope": {"events": ["FS01"], "indicator_count": 1},
                    "indicator_feature_validity": {
                        "FS01-M02": {
                            "valid": 2,
                            "total": 2,
                            "valid_rate": 1.0,
                            "feasibility_level": "F4",
                        }
                    },
                    "indicator_score_status_counts": {
                        "FS01-M02": {"scored": 2}
                    },
                    "indicator_grade_counts": {
                        "FS01-M02": {"B": 1, "C": 1}
                    },
                    "score_status_counts": {"scored": 2},
                    "grade_counts": {"B": 1, "C": 1},
                },
            }

            report = analyze_scoring_readiness(summary, frames)
            loop = report["summary"]["minimum_scoring_loop"]
            indicator = next(
                item
                for item in report["indicator_results"]
                if item["indicator_id"] == "FS01-M02"
            )
            self.assertEqual(loop["formal_grade_status"], "scored")
            self.assertEqual(loop["scored_indicator_count"], 1)
            self.assertEqual(loop["grade_counts"], {"B": 1, "C": 1})
            self.assertEqual(loop["aggregate_grade_status"], "not_designed")
            self.assertTrue(indicator["score_ready"])
            self.assertEqual(indicator["scoring_status"], "scored")
            self.assertEqual(indicator["grade_counts"], {"B": 1, "C": 1})

            html_path = Path(directory) / "analysis-report.html"
            write_analysis_report(summary, report, html_path)
            rendered = html_path.read_text(encoding="utf-8")
            self.assertIn("1 项已有受信版本化标定的事件级 A～E", rendered)
            self.assertIn("本轮不设计总分", rendered)
            self.assertIn("事件级等级", rendered)
            self.assertNotIn("正式 A～E 待教练标定", rendered)


if __name__ == "__main__":
    unittest.main()
