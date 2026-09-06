from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from rallymate_scoring.loop import run_minimum_scoring_loop
from rallymate_scoring.scoring_loop_report import (
    build_scoring_loop_report_html,
    write_scoring_loop_report,
)


def _point(index: int, name: str, x: float, y: float) -> dict:
    return {
        "index": index,
        "name": name,
        "x_normalized": x,
        "y_normalized": y,
        "confidence": 0.95,
    }


class ScoringLoopTests(unittest.TestCase):
    def test_static_pose_produces_valid_no_event_unavailable_bundle(self) -> None:
        root = Path(__file__).resolve().parents[1]
        names = [
            "nose", "left_eye", "right_eye", "left_ear", "right_ear",
            "left_shoulder", "right_shoulder", "left_elbow", "right_elbow",
            "left_wrist", "right_wrist", "left_hip", "right_hip",
            "left_knee", "right_knee", "left_ankle", "right_ankle",
        ]
        coords = [(0.5, 0.15)] * 17
        coords[5:7] = [(0.43, 0.35), (0.57, 0.35)]
        coords[11:17] = [
            (0.45, 0.58), (0.55, 0.58), (0.44, 0.75),
            (0.56, 0.75), (0.42, 0.92), (0.58, 0.92),
        ]
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            frames = output / "frames.jsonl"
            timeline = output / "primary-player.jsonl"
            frame_records = []
            timeline_records = []
            for index in range(16):
                frame_records.append({
                    "frame": {
                        "processed_index": index,
                        "index": index,
                        "timestamp_ms": index * 40,
                    },
                    "poses": [{
                        "person_track_id": 10,
                        "keypoints": [
                            _point(i, name, *coords[i])
                            for i, name in enumerate(names)
                        ],
                    }],
                })
                timeline_records.append({
                    "processed_index": index,
                    "source_track_id": 10,
                    "primary_player_id": 1,
                    "selection_status": "selected",
                    "keypoint_valid_fraction": 1.0,
                })
            frames.write_text(
                "".join(json.dumps(item) + "\n" for item in frame_records),
                encoding="utf-8",
            )
            timeline.write_text(
                "".join(json.dumps(item) + "\n" for item in timeline_records),
                encoding="utf-8",
            )
            result = run_minimum_scoring_loop(
                frames_path=frames,
                primary_timeline_path=timeline,
                output_dir=output,
                feasibility_registry_path=root / "metric-feasibility.json",
                source_id="static-no-event",
                pose_model={"backend": "test", "runtime": "cpu"},
            )
            self.assertEqual(result["events"], [])
            self.assertEqual(result["indicator_records"], [])
            self.assertEqual(result["scores"], [])
            self.assertEqual((output / "features.jsonl").read_text(), "")
            self.assertEqual(
                result["summary"]["status"],
                "unavailable_no_pose_motion_event_candidate",
            )
            self.assertEqual(
                result["summary"]["result_state"]["scoring_status"],
                "unavailable",
            )
            self.assertEqual(result["summary"]["grade_counts"], {})

    def test_no_calibration_never_emits_grade_or_threshold(self) -> None:
        root = Path(__file__).resolve().parents[1]
        names = [
            "nose", "left_eye", "right_eye", "left_ear", "right_ear",
            "left_shoulder", "right_shoulder", "left_elbow", "right_elbow",
            "left_wrist", "right_wrist", "left_hip", "right_hip",
            "left_knee", "right_knee", "left_ankle", "right_ankle",
        ]
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            frames = output / "frames.jsonl"
            timeline = output / "primary-player.jsonl"
            frame_records = []
            timeline_records = []
            for index in range(16):
                shift = 0.02 * min(index, 8)
                coords = [(0.5, 0.15)] * 17
                coords[5:7] = [(0.43 + shift, 0.35), (0.57 + shift, 0.35)]
                coords[11:17] = [
                    (0.45 + shift, 0.58), (0.55 + shift, 0.58),
                    (0.44 + shift, 0.75), (0.56 + shift, 0.75),
                    (0.42 + shift, 0.92), (0.58 + shift, 0.92),
                ]
                frame_records.append({
                    "frame": {"processed_index": index, "index": index, "timestamp_ms": index * 40},
                    "poses": [{"person_track_id": 10, "keypoints": [
                        _point(i, name, coords[i][0], coords[i][1]) for i, name in enumerate(names)
                    ]}],
                })
                timeline_records.append({
                    "processed_index": index,
                    "source_track_id": 10,
                    "primary_player_id": 1,
                    "selection_status": "selected",
                    "keypoint_valid_fraction": 1.0,
                })
            frames.write_text("".join(json.dumps(item) + "\n" for item in frame_records), encoding="utf-8")
            timeline.write_text("".join(json.dumps(item) + "\n" for item in timeline_records), encoding="utf-8")
            result = run_minimum_scoring_loop(
                frames_path=frames,
                primary_timeline_path=timeline,
                output_dir=output,
                feasibility_registry_path=root / "metric-feasibility.json",
                source_id="test-video",
                pose_model={
                    "backend": "test",
                    "runtime": "cpu",
                    "profile": "unit",
                    "native_keypoint_format": "coco17",
                    "native_keypoint_count": 17,
                },
            )
            self.assertTrue((output / "events.jsonl").exists())
            self.assertTrue(result["events"])
            diagnostics = result["events"][0]["track_diagnostics"]
            self.assertEqual(diagnostics["track_coverage_fraction"], 1.0)
            self.assertIsNone(diagnostics["confirmed_id_switch_count"])
            self.assertIn("keypoint_valid_fraction", diagnostics)
            self.assertTrue(result["scores"])
            self.assertTrue(all(item["grade"] is None for item in result["scores"]))
            self.assertTrue(all(item["threshold_version"] is None for item in result["scores"]))
            fs01_m02 = next(
                item for item in result["indicator_records"]
                if item["indicator_id"] == "FS01-M02"
            )
            self.assertEqual(len(fs01_m02["supplemental_features"]), 2)
            self.assertEqual(fs01_m02["supplemental_feature_status"], "unavailable")
            self.assertTrue(
                all(not item["valid"] for item in fs01_m02["supplemental_features"])
            )
            self.assertTrue(
                all(
                    item["semantics"]
                    == "fine_foot_keypoint_evidence_not_A_to_E_threshold"
                    for item in fs01_m02["supplemental_features"]
                )
            )
            model_versions = result["summary"]["model_versions"]
            self.assertEqual(model_versions["native_keypoint_format"], "coco17")
            self.assertEqual(model_versions["native_keypoint_count"], 17)
            self.assertEqual(result["summary"]["feature_error_evaluation"]["status"], "ground_truth_required")
            self.assertEqual(
                result["summary"]["result_state"]["measurement_status"],
                "measured",
            )
            self.assertEqual(
                result["summary"]["result_state"]["scoring_status"],
                "calibration_required",
            )
            self.assertIsNone(result["summary"]["result_state"]["grade"])
            self.assertEqual(
                result["summary"]["result_state"]["aggregate_grade_status"],
                "not_designed",
            )
            self.assertEqual(result["summary"]["grade_counts"], {})
            self.assertEqual(
                result["summary"]["indicator_feature_validity"]["FS01-M02"][
                    "score_status_counts"
                ],
                {"calibration_required": 1},
            )
            self.assertEqual(
                result["summary"]["indicator_feature_validity"]["FS01-M02"][
                    "grade_counts"
                ],
                {},
            )
            report = write_scoring_loop_report(result, output / "scoring-loop-report.html")
            report_text = report.read_text(encoding="utf-8")
            self.assertIn("事件时间轴", report_text)
            self.assertIn("FS01-M02", report_text)
            self.assertIn("calibration_required", report_text)
            self.assertIn("总等级：<code>未设计</code>", report_text)
            self.assertIn("truth missing", report_text)
            self.assertNotIn("contract field missing", report_text)

    def test_report_renders_event_level_grade_without_aggregate_grade(self) -> None:
        result = {
            "summary": {
                "video_id": "synthetic-f4-report",
                "loop_version": "minimum-scoring-loop-v0.2.0",
                "status": "event_indicators_scored_with_versioned_calibration",
                "scope": {"indicator_count": 1},
                "score_status_counts": {"scored": 1},
                "grade_counts": {"C": 1},
                "result_state": {
                    "measurement_status": "measured",
                    "measured_indicator_count": 1,
                    "target_indicator_count": 1,
                    "scoring_status": "scored",
                    "grade": None,
                    "aggregate_grade_status": "not_designed",
                    "ui_message_zh": "1项 Pose 指标已有事件级等级",
                },
                "event_evaluation": {},
                "feature_error_evaluation": {
                    "status": "evaluated",
                    "error_budget": {},
                },
                "model_versions": {"calibration": {"FS01-M02": "threshold-v1"}},
            },
            "events": [
                {
                    "event_id": "fs01-manual-001",
                    "event_code": "FS01",
                    "start_ms": 1000,
                    "end_ms": 1400,
                }
            ],
            "indicator_records": [
                {
                    "indicator_id": "FS01-M02",
                    "event_id": "fs01-manual-001",
                    "feature_status": "measured",
                    "scoring_status": "scored",
                    "grade": "C",
                    "features": [
                        {
                            "feature_name": "hip_center_y_body",
                            "value": 2.5,
                            "unit": "body",
                            "valid": True,
                            "reason": "valid",
                            "source_frames": [30, 36, 42],
                        }
                    ],
                    "supplemental_features": [],
                }
            ],
            "scores": [
                {
                    "indicator_id": "FS01-M02",
                    "event_id": "fs01-manual-001",
                    "status": "scored",
                    "grade": "C",
                    "confidence": 0.91,
                    "threshold_version": "threshold-v1",
                    "reason_codes": ["scored_with_threshold_rule"],
                    "feedback": "事件级测试反馈",
                }
            ],
            "feasibility": {
                "indicators": [
                    {
                        "indicator_id": "FS01-M02",
                        "name_zh": "重心预加载",
                        "feasibility_level": "F4",
                        "current_blockers": [],
                    }
                ]
            },
        }

        rendered = build_scoring_loop_report_html(result)
        self.assertIn("事件级 A～E 分布", rendered)
        self.assertIn("&#x27;C&#x27;: 1", rendered)
        self.assertIn("fs01-manual-001", rendered)
        self.assertIn("threshold-v1", rendered)
        self.assertIn("0.91", rendered)
        self.assertIn("事件级测试反馈", rendered)
        self.assertIn("总等级：<code>未设计</code>", rendered)
        self.assertNotIn("有效特征只进入 calibration_required", rendered)

        # The score-facing indicator contract is intentionally compact. The
        # writer must join canonical features.jsonl and scores.jsonl facts.
        canonical_feature = {
            "event_id": "fs01-manual-001",
            "feature_name": "hip_center_y_body",
            "feature_version": "1.0.0",
            "value": 2.5,
            "unit": "body",
            "confidence": 0.9,
            "valid": True,
            "reason": "valid",
            "source_frames": [30, 36, 42],
            "raw_value": [
                {"timestamp_ms": 1000, "source_frame": 30, "value": 2.4},
                {"timestamp_ms": 1200, "source_frame": 36, "value": 2.6},
            ],
            "smoothed_value": [
                {"timestamp_ms": 1000, "source_frame": 30, "value": 2.45},
                {"timestamp_ms": 1200, "source_frame": 36, "value": 2.55},
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            (output / "features.jsonl").write_text(
                json.dumps(canonical_feature) + "\n", encoding="utf-8"
            )
            (output / "scores.jsonl").write_text(
                json.dumps(result["scores"][0], ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            compact_result = dict(result)
            compact_result.pop("scores")
            report = write_scoring_loop_report(
                compact_result, output / "scoring-loop-report.html"
            ).read_text(encoding="utf-8")
        self.assertIn("series(n=2, numeric=2)", report)
        self.assertIn("threshold-v1", report)
        self.assertIn("事件级测试反馈", report)
        self.assertNotIn("contract field missing", report)

    def test_report_renders_all_contract_facts_and_all_event_instances(self) -> None:
        events = []
        records = []
        scores = []
        for index, scoring_status in enumerate(
            ("scored", "calibration_required", "unavailable"), start=1
        ):
            event_id = f"fs01-rich-{index:03d}"
            start_ms = index * 1000
            source_frames = [index * 10, index * 10 + 2]
            events.append(
                {
                    "event_id": event_id,
                    "event_code": "FS01",
                    "person_track_id": 7,
                    "start_ms": start_ms,
                    "end_ms": start_ms + 400,
                    "key_phases_ms": {
                        "preload_ms": start_ms + 40,
                        "takeoff_proxy_ms": start_ms + 120,
                        "landing_proxy_ms": start_ms + 260,
                        "redistribution_ms": start_ms + 300,
                        "initiation_ms": start_ms + 380,
                    },
                    "confidence": 0.83,
                    "boundary_uncertainty_ms": 45,
                    "quality_flags": ["event_ground_truth_missing", "rule_candidate"],
                    "track_diagnostics": {
                        "track_coverage_fraction": 0.98,
                        "source_track_switch_candidate_count": 1,
                        "confirmed_id_switch_count": None,
                        "keypoint_valid_fraction": 0.94,
                        "left_right_swap_candidate_frames": [source_frames[0]],
                        "keypoint_jump_candidate_frames": [source_frames[1]],
                        "longest_pose_missing_frames": 2,
                        "longest_pose_missing_ms": 80,
                    },
                }
            )
            raw = [
                {
                    "timestamp_ms": start_ms + offset,
                    "source_frame": source_frames[0] + sample,
                    "value": 1.0 + sample * 0.1,
                }
                for sample, offset in enumerate((0, 40, 90))
            ]
            smoothed = [
                {**item, "value": item["value"] + 0.02}
                for item in raw
            ]
            feature_status = "unavailable" if scoring_status == "unavailable" else "measured"
            records.append(
                {
                    "indicator_id": "FS01-M02",
                    "event_id": event_id,
                    "event_code": "FS01",
                    "person_track_id": 7,
                    "feature_status": feature_status,
                    "scoring_status": scoring_status,
                    "grade": "C" if scoring_status == "scored" else None,
                    "reason_codes": [
                        "required_feature_unavailable"
                        if scoring_status == "unavailable"
                        else "coach_calibration_missing"
                    ],
                    "features": [
                        {
                            "feature_name": "hip_center_y_body",
                            "feature_version": "1.0.0",
                            "value": 1.23 if feature_status == "measured" else None,
                            "unit": "body",
                            "confidence": 0.77,
                            "valid": feature_status == "measured",
                            "reason": "valid" if feature_status == "measured" else "missing_pose",
                            "source_frames": source_frames,
                            "raw_value": raw,
                            "smoothed_value": smoothed,
                        }
                    ],
                    "supplemental_features": [],
                    "quality_gate": {
                        "policy_version": "indicator-event-quality-v1.6.0",
                        "status": "pass",
                        "hard_fail": False,
                        "measurement_allowed": True,
                        "scoring_allowed": True,
                        "input_quality_flags": ["event_ground_truth_missing"],
                    },
                }
            )
            scores.append(
                {
                    "indicator_id": "FS01-M02",
                    "event_id": event_id,
                    "status": scoring_status,
                    "grade": "C" if scoring_status == "scored" else None,
                    "confidence": 0.91 if scoring_status == "scored" else 0.55,
                    "threshold_version": "threshold-v1" if scoring_status == "scored" else None,
                    "reason_codes": [
                        "scored_with_threshold_rule"
                        if scoring_status == "scored"
                        else scoring_status
                    ],
                    "feedback": f"event feedback {index}",
                    "quality_gate": records[-1]["quality_gate"],
                    "evidence": [
                        {"source_frames": source_frames},
                        {"source_frame": source_frames[-1] + 1},
                    ],
                    "model_versions": {"pose": "rtmpose-wholebody133"},
                }
            )

        stats = {
            "mae": 0.12,
            "p95": 0.2,
            "bias": -0.03,
            "valid_count": 2,
            "eligible_count": 3,
            "valid_rate": 0.66666667,
        }
        result = {
            "summary": {
                "video_id": "rich-report",
                "loop_version": "minimum-scoring-loop-v0.3.0",
                "status": "mixed_event_results",
                "scope": {"indicator_count": 1},
                "score_status_counts": {
                    "scored": 1,
                    "calibration_required": 1,
                    "unavailable": 1,
                },
                "result_state": {
                    "measurement_status": "measured",
                    "measured_indicator_count": 1,
                    "target_indicator_count": 1,
                    "scoring_status": "scored",
                    "ui_message_zh": "事件级事实测试",
                },
                "event_evaluation": {
                    "event_f1": 0.8,
                    "mean_segment_iou": 0.7,
                    "boundary_mae_ms": 30.0,
                    "boundary_p95_ms": 60.0,
                    "phase_boundary_by_name": {"preload_ms": {"mae_ms": 20.0}},
                },
                "feature_error_evaluation": {
                    "status": "evaluated_with_incomplete_feature_truth",
                    "ground_truth_coverage": {
                        "eligible_feature_event_pairs": 3,
                        "valid_feature_event_pairs": 2,
                        "valid_rate": 0.66666667,
                    },
                    "feature_metrics": {
                        "hip_center_y_body": {
                            "unit": "body",
                            "overall": stats,
                            "by_view": {"fixed_side": stats},
                        }
                    },
                    "error_budget": {
                        "method": "counterfactual",
                        "features": {"hip_center_y_body": {"pose_error": stats}},
                    },
                },
                "model_versions": {"pose": "rtmpose-wholebody133"},
            },
            "events": events,
            "indicator_records": records,
            "scores": scores,
            "feasibility": {
                "indicators": [
                    {
                        "indicator_id": "FS01-M02",
                        "name_zh": "重心预加载",
                        "feasibility_level": "F4",
                        "current_blockers": [],
                    }
                ]
            },
        }

        rendered = build_scoring_loop_report_html(result)
        self.assertEqual(rendered.count('class="instance-card"'), 3)
        for index in range(1, 4):
            self.assertIn(f"fs01-rich-{index:03d}", rendered)
        for phase_name in (
            "preload_ms",
            "takeoff_proxy_ms",
            "landing_proxy_ms",
            "redistribution_ms",
            "initiation_ms",
        ):
            self.assertIn(phase_name, rendered)
        self.assertIn("boundary ±45 ms", rendered)
        self.assertIn("event_ground_truth_missing", rendered)
        self.assertIn("track_coverage_fraction", rendered)
        self.assertIn("left_right_swap_candidate_frames", rendered)
        self.assertIn("series(n=3, numeric=3)", rendered)
        self.assertIn("indicator-event-quality-v1.6.0", rendered)
        self.assertIn("合并证据帧", rendered)
        self.assertIn("特征测量状态", rendered)
        self.assertIn("评分状态", rendered)
        self.assertIn("feature_status=measured", rendered)
        self.assertIn("score.status=calibration_required", rendered)
        self.assertIn("view: <code>fixed_side</code>", rendered)
        self.assertIn("0.66666667", rendered)
        self.assertNotIn("truth missing", rendered)
        self.assertIn("合法 F4 事件级 A～E 分布", rendered)
        self.assertIn("总等级：<code>未设计</code>", rendered)
        self.assertNotIn("另有", rendered)

    def test_report_escapes_facts_and_bounds_long_feature_series(self) -> None:
        malicious = '<img src=x onerror="alert(1)">'
        long_series = [
            {"timestamp_ms": index * 40, "source_frame": index, "value": index / 10}
            for index in range(10_000)
        ]
        result = {
            "summary": {
                "video_id": malicious,
                "loop_version": "minimum-scoring-loop-v0.3.0",
                "status": "calibration_required",
                "scope": {"indicator_count": 1},
                "score_status_counts": {"calibration_required": 1},
                "result_state": {},
                "event_evaluation": {},
                "feature_error_evaluation": {
                    "status": "ground_truth_required",
                    "feature_metrics": {},
                    "error_budget": {},
                },
                "model_versions": {},
            },
            "events": [
                {
                    "event_id": malicious,
                    "event_code": "FS01",
                    "person_track_id": 1,
                    "start_ms": 0,
                    "end_ms": 100,
                    "key_phases_ms": {malicious: 40},
                    "confidence": 0.5,
                    "boundary_uncertainty_ms": 20,
                    "quality_flags": [malicious],
                    "track_diagnostics": {"diagnostic": malicious},
                }
            ],
            "indicator_records": [
                {
                    "indicator_id": "FS01-M02",
                    "event_id": malicious,
                    "event_code": "FS01",
                    "person_track_id": 1,
                    "feature_status": "measured",
                    "scoring_status": "calibration_required",
                    "grade": None,
                    "features": [
                        {
                            "feature_name": malicious,
                            "feature_version": "v1",
                            "value": 1.0,
                            "unit": "body",
                            "confidence": 0.5,
                            "valid": True,
                            "reason": malicious,
                            "source_frames": [1],
                            "raw_value": long_series,
                            "smoothed_value": long_series,
                        }
                    ],
                    "supplemental_features": [],
                    "quality_gate": {"status": "pass", "diagnostic": malicious},
                    "reason_codes": [malicious],
                }
            ],
            "scores": [
                {
                    "indicator_id": "FS01-M02",
                    "event_id": malicious,
                    "status": "calibration_required",
                    "grade": None,
                    "confidence": 0.5,
                    "threshold_version": None,
                    "reason_codes": [malicious],
                    "feedback": malicious,
                    "evidence": [{"source_frames": [1]}],
                    "model_versions": {},
                }
            ],
            "feasibility": {
                "indicators": [
                    {
                        "indicator_id": "FS01-M02",
                        "name_zh": malicious,
                        "feasibility_level": "F2",
                        "current_blockers": [malicious],
                    }
                ]
            },
        }

        rendered = build_scoring_loop_report_html(result)
        self.assertNotIn("<img src=x", rendered)
        self.assertIn("&lt;img src=x onerror=&quot;alert(1)&quot;&gt;", rendered)
        self.assertIn("series(n=10000, numeric=10000)", rendered)
        self.assertLess(len(rendered), 200_000)
        self.assertIn("truth missing", rendered)


if __name__ == "__main__":
    unittest.main()
