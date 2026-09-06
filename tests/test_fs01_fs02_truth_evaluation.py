from __future__ import annotations

import json
import math
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

from rallymate_evaluation.feature_errors import evaluate_feature_errors
from rallymate_evaluation.ground_truth import apply_keypoint_corrections
from rallymate_features import pose_sequence_from_records
from rallymate_features.fs01_fs02_features import FS01_FS02_EVENT_FEATURE_NAMES
from rallymate_features.schemas import PoseSequence


TIMESTAMPS = np.asarray(
    [
        0,
        40,
        95,
        150,
        215,
        270,
        335,
        390,
        455,
        510,
        575,
        630,
        695,
        750,
        815,
        870,
        935,
        990,
        1055,
        1110,
        1175,
    ],
    dtype=np.int64,
)
COCO17_NAMES = (
    "nose",
    "left_eye",
    "right_eye",
    "left_ear",
    "right_ear",
    "left_shoulder",
    "right_shoulder",
    "left_elbow",
    "right_elbow",
    "left_wrist",
    "right_wrist",
    "left_hip",
    "right_hip",
    "left_knee",
    "right_knee",
    "left_ankle",
    "right_ankle",
)
TARGET_FEATURES = (
    "bilateral_foot_rise_min_body",
    "support_knee_extension_velocity_deg_s",
    "post_step_hip_direction_consistency",
)


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )


def _coordinates(t: float, *, corrected: bool) -> dict[str, tuple[float, float]]:
    hip_x = 0.50 + 0.16 * t * t + 0.04 * t
    hip_y = 0.56 - 0.05 * math.sin(math.pi * t)
    rise = (
        0.055 * math.sin(math.pi * min(t / 0.55, 1.0))
        if t <= 0.55
        else 0.0
    )
    left_ankle_x = 0.35 + 0.24 * max(0.0, (t - 0.25) / 0.75) ** 1.4
    right_ankle_x = 0.65 + 0.025 * t
    left_ankle_y = 0.90 - rise
    right_ankle_y = 0.90 - (0.95 * rise if t < 0.60 else 0.0)
    left_knee_offset = 0.035 * (1.0 - t)
    right_knee_offset = 0.075 * (1.0 - t)
    values = {
        "left_hip": (hip_x - 0.05, hip_y),
        "right_hip": (hip_x + 0.05, hip_y),
        "left_ankle": (left_ankle_x, left_ankle_y),
        "right_ankle": (right_ankle_x, right_ankle_y),
        "left_knee": (
            ((hip_x - 0.05) + left_ankle_x) / 2.0 - left_knee_offset,
            (hip_y + left_ankle_y) / 2.0,
        ),
        "right_knee": (
            ((hip_x + 0.05) + right_ankle_x) / 2.0 + right_knee_offset,
            (hip_y + right_ankle_y) / 2.0,
        ),
        "left_shoulder": (hip_x - 0.10, hip_y - 0.25),
        "right_shoulder": (hip_x + 0.10, hip_y - 0.25),
        "left_elbow": (hip_x - 0.15, hip_y - 0.10),
        "right_elbow": (hip_x + 0.15, hip_y - 0.10),
        "left_wrist": (hip_x - 0.18, hip_y + 0.02),
        "right_wrist": (hip_x + 0.18, hip_y + 0.02),
        "nose": (hip_x, hip_y - 0.38),
        "left_eye": (hip_x - 0.02, hip_y - 0.40),
        "right_eye": (hip_x + 0.02, hip_y - 0.40),
        "left_ear": (hip_x - 0.04, hip_y - 0.38),
        "right_ear": (hip_x + 0.04, hip_y - 0.38),
    }
    if corrected:
        extra_rise = (
            0.018 * math.sin(math.pi * min(t / 0.55, 1.0))
            if t <= 0.55
            else 0.0
        )
        for side in ("left", "right"):
            ankle_x, ankle_y = values[f"{side}_ankle"]
            values[f"{side}_ankle"] = (ankle_x, ankle_y - extra_rise)
        left_knee_x, left_knee_y = values["left_knee"]
        right_knee_x, right_knee_y = values["right_knee"]
        values["left_knee"] = (left_knee_x - 0.010 * (1.0 - t), left_knee_y)
        values["right_knee"] = (right_knee_x + 0.025 * (1.0 - t), right_knee_y)
        late_curve = 0.04 * max(0.0, t - 0.55) ** 2 / 0.45**2
        for side in ("left", "right"):
            hip_point_x, hip_point_y = values[f"{side}_hip"]
            values[f"{side}_hip"] = (hip_point_x, hip_point_y - late_curve)
    return values


def _fixture_records() -> tuple[list[dict], list[dict], list[dict]]:
    frames: list[dict] = []
    timeline: list[dict] = []
    annotations: list[dict] = []
    for index, timestamp in enumerate(TIMESTAMPS):
        t = float(timestamp / TIMESTAMPS[-1])
        model = _coordinates(t, corrected=False)
        truth = _coordinates(t, corrected=True)
        frames.append(
            {
                "frame": {
                    "index": index,
                    "processed_index": index,
                    "timestamp_ms": int(timestamp),
                },
                "poses": [
                    {
                        "person_track_id": 7,
                        "keypoints": [
                            {
                                "name": name,
                                "x_normalized": model[name][0],
                                "y_normalized": model[name][1],
                                "confidence": 0.95,
                                "in_frame": True,
                            }
                            for name in COCO17_NAMES
                        ],
                    }
                ],
            }
        )
        timeline.append({"processed_index": index, "source_track_id": 7})
        annotations.append(
            {
                "schema_version": "1.0.0",
                "video_id": "synthetic-fs01-fs02",
                "source_frame_index": index,
                "timestamp_ms": int(timestamp),
                "primary_player_id": 1,
                "annotator_id": "adjudicated-keypoint-truth",
                "reviewer_id": "synthetic-reviewer",
                "adjudication_status": "accepted",
                "view_group": "side",
                "joints": {
                    name: {
                        "visible": True,
                        "x_normalized": truth[name][0],
                        "y_normalized": truth[name][1],
                    }
                    for name in COCO17_NAMES
                },
            }
        )
    return frames, timeline, annotations


def _event(event_id: str, code: str, *, manual: bool) -> dict:
    truth_phases = {
        "FS01": {
            "preload_ms": 150,
            "takeoff_proxy_ms": 270,
            "landing_proxy_ms": 575,
            "redistribution_ms": 750,
            "initiation_ms": 935,
        },
        "FS02": {
            "direction_conversion_ms": 215,
            "support_extension_proxy_ms": 390,
            "lead_foot_motion_onset_proxy_ms": 510,
            "first_step_slowdown_proxy_ms": 935,
        },
    }[code]
    phases = (
        truth_phases
        if manual
        else {
            name: (
                None
                if name == "landing_proxy_ms"
                else int(timestamp + 20)
            )
            for name, timestamp in truth_phases.items()
        }
    )
    record = {
        "schema_version": "1.0.0",
        "event_id": event_id,
        "person_track_id": 1,
        "event_code": code,
        "start_ms": 0 if manual else 40,
        "end_ms": 1175 if manual else 1110,
        "key_phases_ms": phases,
        "confidence": 1.0,
        "boundary_uncertainty_ms": 0,
        "quality_flags": [],
        "provenance": {
            "fixture": "synthetic-fs01-fs02-truth-evaluation",
            "source_id": "synthetic-fs01-fs02",
        },
    }
    if manual:
        record.update(
            {
                "video_id": "synthetic-fs01-fs02",
                "annotation_source": "manual",
                "annotator_id": "adjudicated-event-truth",
                "reviewer_id": "synthetic-reviewer",
                "adjudication_status": "accepted",
                "view_group": "side",
            }
        )
    return record


class FS01FS02TruthEvaluationTests(unittest.TestCase):
    def test_cli_evaluates_all_new_features_from_manual_coco17_truth(self) -> None:
        root = Path(__file__).resolve().parents[1]
        frames, timeline, annotations = _fixture_records()
        predictions = [
            _event("pred-fs01", "FS01", manual=False),
            _event("pred-fs02", "FS02", manual=False),
        ]
        truth_events = [
            _event("truth-fs01", "FS01", manual=True),
            _event("truth-fs02", "FS02", manual=True),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            paths = {
                "frames": directory / "frames.jsonl",
                "timeline": directory / "primary-player.jsonl",
                "predictions": directory / "events.jsonl",
                "events": directory / "manual-events.jsonl",
                "keypoints": directory / "manual-keypoints.jsonl",
                "semantics": directory / "manual-semantics.jsonl",
                "output": directory / "evaluation.json",
            }
            _write_jsonl(paths["frames"], frames)
            _write_jsonl(paths["timeline"], timeline)
            _write_jsonl(paths["predictions"], predictions)
            _write_jsonl(paths["events"], truth_events)
            _write_jsonl(paths["keypoints"], annotations)
            _write_jsonl(
                paths["semantics"],
                [
                    {
                        "schema_version": "1.0.0",
                        "annotation_id": "truth-target-fs02",
                        "video_id": "synthetic-fs01-fs02",
                        "event_id": "truth-fs02",
                        "indicator_id": "FS02-M02",
                        "semantic_key": "target_direction",
                        "semantic_type": "direction",
                        "observable": True,
                        "value": {
                            "direction_deg": 0.0,
                            "coordinate_frame": "image_plane",
                        },
                        "null_reason": None,
                        "annotation_confidence": 0.95,
                        "annotator_id": "adjudicated-target-truth",
                        "reviewer_id": "synthetic-reviewer",
                        "adjudication_status": "accepted",
                    }
                ],
            )
            process = subprocess.run(
                [
                    sys.executable,
                    str(root / "scripts" / "evaluate_scoring_truth.py"),
                    "--frames",
                    str(paths["frames"]),
                    "--primary-timeline",
                    str(paths["timeline"]),
                    "--predicted-events",
                    str(paths["predictions"]),
                    "--manual-events",
                    str(paths["events"]),
                    "--manual-keypoints",
                    str(paths["keypoints"]),
                    "--manual-semantics",
                    str(paths["semantics"]),
                    "--output",
                    str(paths["output"]),
                ],
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(process.returncode, 0, process.stderr)
            report = json.loads(paths["output"].read_text(encoding="utf-8"))

        self.assertEqual(report["status"], "evaluated")
        self.assertEqual(
            report["inputs"]["registry_version"], "pose-wave-2026-08-22.17"
        )
        self.assertEqual(len(report["inputs"]["sha256"]["registry"]), 64)
        event_metrics = report["event_evaluation"]
        self.assertEqual(event_metrics["event_f1"], 1.0)
        self.assertEqual(event_metrics["boundary_mae_ms"], 52.5)
        self.assertEqual(event_metrics["boundary_p95_ms"], 65.0)
        self.assertEqual(event_metrics["phase_boundary_mae_ms"], 20.0)
        self.assertEqual(event_metrics["phase_boundary_p95_ms"], 20.0)
        self.assertEqual(event_metrics["phase_boundary_valid_rate"], 0.88888889)
        self.assertEqual(
            event_metrics["phase_boundary_by_name"]["landing_proxy_ms"],
            {
                "eligible_truth_count": 1,
                "predicted_count": 0,
                "missing_prediction_count": 1,
                "valid_rate": 0.0,
                "mae_ms": None,
                "p95_ms": None,
            },
        )

        feature_evaluation = report["feature_evaluation"]
        self.assertEqual(feature_evaluation["status"], "evaluated")
        self.assertEqual(feature_evaluation["ground_truth_coverage"]["valid_rate"], 1.0)
        self.assertTrue(
            FS01_FS02_EVENT_FEATURE_NAMES.issubset(
                feature_evaluation["feature_metrics"]
            )
        )
        target_alignment = feature_evaluation["feature_metrics"][
            "target_direction_alignment_error_deg"
        ]
        self.assertEqual(target_alignment["unit"], "deg")
        self.assertEqual(target_alignment["overall"]["eligible_count"], 1)
        self.assertEqual(target_alignment["overall"]["valid_count"], 1)
        self.assertEqual(target_alignment["overall"]["valid_rate"], 1.0)
        self.assertIn(
            "target_direction_alignment_error_deg",
            feature_evaluation["error_budget"]["features"],
        )
        self.assertTrue(report["promotion_guard"]["manual_semantics_provided"])
        self.assertEqual(
            report["promotion_guard"]["required_context_features"],
            ["target_direction_alignment_error_deg"],
        )
        self.assertEqual(
            report["promotion_guard"]["manual_semantic_record_count"], 1
        )
        self.assertTrue(
            report["promotion_guard"]["context_feature_truth_complete"]
        )
        expected_units = {
            "bilateral_foot_rise_min_body": "body",
            "support_knee_extension_velocity_deg_s": "deg/s",
            "post_step_hip_direction_consistency": "ratio",
        }
        for feature_name in TARGET_FEATURES:
            metric = feature_evaluation["feature_metrics"][feature_name]
            overall = metric["overall"]
            self.assertEqual(metric["unit"], expected_units[feature_name])
            self.assertEqual(overall["eligible_count"], 1)
            self.assertEqual(overall["valid_count"], 1)
            self.assertEqual(overall["valid_rate"], 1.0)
            self.assertGreater(overall["mae"], 0.0)
            self.assertEqual(overall["p95"], overall["mae"])
            self.assertAlmostEqual(abs(overall["bias"]), overall["mae"])
            self.assertEqual(metric["by_view"]["side"], overall)
            detail = next(
                item
                for item in feature_evaluation["details"]
                if item["feature_name"] == feature_name
            )
            self.assertTrue(detail["model_valid"])
            self.assertTrue(detail["ground_truth_valid"])
            self.assertIsNotNone(detail["model_value"])
            self.assertIsNotNone(detail["ground_truth_value"])
        self.assertFalse(report["promotion_guard"]["F2_to_F3_automatic"])

    def test_quality_gate_failure_cannot_count_a_diagnostic_zero_as_valid(self) -> None:
        frames, timeline, annotations = _fixture_records()
        model = pose_sequence_from_records(frames, timeline)
        keypoints = {
            name: values.copy() for name, values in model.keypoints_xy.items()
        }
        confidence = {
            name: values.copy() for name, values in model.confidence.items()
        }
        for name in ("left_ankle", "right_ankle"):
            keypoints[name][:12] = np.nan
            confidence[name][:12] = np.nan
        sparse_model = PoseSequence(
            timestamp_ms=model.timestamp_ms.copy(),
            source_frames=model.source_frames.copy(),
            keypoints_xy=keypoints,
            confidence=confidence,
        )
        corrected = apply_keypoint_corrections(sparse_model, annotations)
        result = evaluate_feature_errors(
            sparse_model,
            corrected,
            [_event("pred-fs01", "FS01", manual=False)],
            [_event("truth-fs01", "FS01", manual=True)],
            {"FS01": ["bilateral_foot_rise_min_body"]},
        )
        metric = result["feature_metrics"]["bilateral_foot_rise_min_body"][
            "overall"
        ]
        detail = result["details"][0]
        self.assertEqual(result["status"], "insufficient_keypoint_ground_truth_coverage")
        self.assertEqual(metric["eligible_count"], 1)
        self.assertEqual(metric["valid_count"], 0)
        self.assertEqual(metric["valid_rate"], 0.0)
        self.assertIsNone(metric["mae"])
        self.assertFalse(detail["model_valid"])
        self.assertEqual(detail["model_computed_value"], 0.0)
        self.assertIsNone(detail["model_value"])
        self.assertIsNone(detail["error"])
        self.assertEqual(
            detail["model_reason"], "valid_fraction_below_quality_gate"
        )


if __name__ == "__main__":
    unittest.main()
