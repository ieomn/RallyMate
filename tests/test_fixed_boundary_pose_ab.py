from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from rallymate_evaluation import fixed_boundary_ab
from rallymate_evaluation.fixed_boundary_ab import (
    evaluate_fixed_boundary_pose_ab_files,
    validate_fixed_boundary_ab_report,
)


ROOT = Path(__file__).resolve().parents[1]
MODEL_A_SHA = "A" * 64
MODEL_B_SHA = "B" * 64


def _points(hip_shift: float, confidence: float = 0.99) -> list[dict]:
    coordinates = {
        "left_shoulder": (0.45, 0.30),
        "right_shoulder": (0.55, 0.30),
        "left_hip": (0.47, 0.50 + hip_shift),
        "right_hip": (0.53, 0.50 + hip_shift),
        "left_knee": (0.46, 0.70),
        "right_knee": (0.54, 0.70),
        "left_ankle": (0.44, 0.90),
        "right_ankle": (0.56, 0.90),
    }
    return [
        {
            "index": index,
            "name": name,
            "x_normalized": xy[0],
            "y_normalized": xy[1],
            "confidence": confidence,
        }
        for index, (name, xy) in enumerate(coordinates.items())
    ]


def _records(track_id: int, hip_shift: float) -> list[dict]:
    records = []
    for index in range(12):
        records.append(
            {
                "schema_version": "1.1.0",
                "frame": {
                    "index": index,
                    "processed_index": index,
                    "timestamp_ms": index * 100,
                    "width": 1000,
                    "height": 1000,
                },
                "detections": [
                    {
                        "class_name": "player",
                        "track_id": track_id,
                        "bbox_px": [300, 200, 700, 950],
                        "confidence": 0.99,
                    }
                ],
                "poses": [
                    {
                        "person_track_id": track_id,
                        "keypoint_format": "coco17",
                        "keypoints": _points(hip_shift),
                    }
                ],
            }
        )
    return records


def _timeline(track_id: int) -> list[dict]:
    return [
        {
            "processed_index": index,
            "source_frame_index": index,
            "timestamp_ms": index * 100,
            "primary_player_id": 1,
            "selection_status": "selected",
            "source_track_id": track_id,
        }
        for index in range(12)
    ]


def _registry() -> dict:
    return {
        "schema_version": "2.0.0",
        "registry_version": "synthetic-fixed-boundary-v1",
        "updated_at": "2026-08-13T00:00:00+08:00",
        "scope": {"events": ["FS01"], "indicator_count": 1},
        "level_definitions": {
            "F0": "structure",
            "F1": "event",
            "F2": "feature",
            "F3": "calibration",
            "F4": "independent",
        },
        "indicators": [
            {
                "indicator_id": "FS01-M02",
                "feasibility_level": "F2",
                "required_events": ["FS01.preload"],
                "required_features": ["hip_center_y_body", "stance_width_body"],
                "view_constraints": ["fixed_camera"],
                "ground_truth_requirements": ["manual_keypoints"],
                "current_blockers": ["truth_missing"],
                "acceptance_metrics": {"feature_mae": None},
                "versions": {"indicator_definition": "synthetic.1"},
            }
        ],
    }


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(item) + "\n" for item in records), encoding="utf-8"
    )


class FixedBoundaryPoseABTests(unittest.TestCase):
    def test_pose_ab_excludes_non_pose_scoring_context_from_measurement_vector(self) -> None:
        registry = _registry()
        indicator = registry["indicators"][0]
        indicator["required_features"] = [
            "hip_center_y_body",
            "stance_width_body",
            "target_direction_alignment_error_deg",
        ]
        indicator["measurement_features"] = [
            "hip_center_y_body",
            "stance_width_body",
        ]

        by_event = fixed_boundary_ab._registry_index(registry)

        self.assertEqual(
            ["hip_center_y_body", "stance_width_body"],
            by_event["FS01"][0]["required_features"],
        )

    def test_file_pipeline_preserves_boundaries_and_reports_disagreement_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            events_path = root / "events.jsonl"
            registry_path = root / "registry.json"
            a_frames, b_frames = root / "a-frames.jsonl", root / "b-frames.jsonl"
            a_timeline, b_timeline = root / "a-timeline.jsonl", root / "b-timeline.jsonl"
            event = {
                "event_id": "common-fs01-001",
                "person_track_id": 1,
                "event_code": "FS01",
                "start_ms": 0,
                "end_ms": 1000,
                "key_phases_ms": {"preload_ms": 200},
                "confidence": 0.75,
                "boundary_uncertainty_ms": 100,
                "quality_flags": ["source_model_specific_candidate_flag"],
            }
            _write_jsonl(events_path, [event])
            registry_path.write_text(json.dumps(_registry()), encoding="utf-8")
            _write_jsonl(a_frames, _records(7, 0.00))
            _write_jsonl(b_frames, _records(8, 0.01))
            _write_jsonl(a_timeline, _timeline(7))
            _write_jsonl(b_timeline, _timeline(8))
            report = evaluate_fixed_boundary_pose_ab_files(
                events_path=events_path,
                registry_path=registry_path,
                model_inputs={
                    "model_a": {
                        "frames_path": a_frames,
                        "primary_timeline_path": a_timeline,
                        "model_sha256": MODEL_A_SHA,
                        "pose_backend": "synthetic",
                    },
                    "model_b": {
                        "frames_path": b_frames,
                        "primary_timeline_path": b_timeline,
                        "model_sha256": MODEL_B_SHA,
                        "pose_backend": "synthetic",
                    },
                },
                boundary_source_kind="candidate",
                boundary_source_label="model_a candidates reused as immutable boundaries",
                source_id="synthetic-video",
            )
        self.assertEqual(
            "candidate_source_not_truth",
            report["comparison_semantics"]["event_boundary_truth_status"],
        )
        self.assertFalse(report["comparison_semantics"]["accuracy_claim"])
        self.assertEqual("common-fs01-001", report["events"][0]["boundary"]["event_id"])
        self.assertEqual(0, report["events"][0]["boundary"]["start_ms"])
        self.assertEqual(1000, report["events"][0]["boundary"]["end_ms"])
        self.assertFalse(
            report["events"][0]["boundary_source_annotations"][
                "used_as_model_quality_flags"
            ]
        )
        self.assertEqual(
            64,
            len(
                report["events"][0]["boundary_source_annotations"][
                    "source_record_sha256"
                ]
            ),
        )
        indicator = report["indicator_metrics"]["FS01-M02"]
        self.assertEqual(1, indicator["paired_validity"]["both_measured_count"])
        self.assertEqual("not_evaluated", indicator["event_quality_gate"]["status"])
        metric = report["feature_metrics"]["hip_center_y_body"]
        self.assertEqual("evaluated", metric["cross_model_disagreement"]["status"])
        self.assertFalse(metric["cross_model_disagreement"]["accuracy_claim"])
        self.assertGreater(
            metric["cross_model_disagreement"][
                "mean_absolute_cross_model_disagreement"
            ],
            0,
        )
        pair = next(
            item
            for item in report["feature_pairs"]
            if item["feature_name"] == "hip_center_y_body"
        )
        self.assertTrue(pair["comparison"]["eligible"])
        for model_name in report["model_order"]:
            self.assertIn("confidence", pair["models"][model_name])
            self.assertIn("provenance", pair["models"][model_name])
            self.assertIn("source_frames", pair["models"][model_name])
        validate_fixed_boundary_ab_report(report)
        schema = json.loads(
            (ROOT / "contracts" / "fixed-boundary-pose-ab.schema.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertTrue(set(schema["required"]).issubset(report))
        self.assertEqual(schema["properties"]["schema_version"]["const"], "1.0.0")

    def test_direction_uses_wrapped_difference_and_codes_only_use_agreement(self) -> None:
        signature = "C" * 64
        base = {
            "valid": True,
            "value": 179.0,
            "unit": "deg",
            "feature_version": "v1",
            "semantic_signature_sha256": signature,
        }
        direction = fixed_boundary_ab._compare_feature_pair(
            "launch_direction_deg",
            base,
            {**base, "value": -179.0},
            model_order=("a", "b"),
        )
        self.assertEqual(-2.0, direction["signed_cross_model_disagreement"])
        self.assertEqual(2.0, direction["absolute_cross_model_disagreement"])
        code = fixed_boundary_ab._compare_feature_pair(
            "launch_side_code",
            {**base, "unit": "code", "value": -1},
            {**base, "unit": "code", "value": 1},
            model_order=("a", "b"),
        )
        self.assertTrue(code["eligible"])
        self.assertFalse(code["category_agreement"])
        self.assertIsNone(code["signed_cross_model_disagreement"])
        self.assertIsNone(code["absolute_cross_model_disagreement"])

    def test_invalid_or_semantically_mismatched_pair_never_enters_numeric_metric(self) -> None:
        base = {
            "valid": True,
            "value": 1.0,
            "unit": "body",
            "feature_version": "v1",
            "semantic_signature_sha256": "D" * 64,
        }
        invalid = fixed_boundary_ab._compare_feature_pair(
            "stance_width_body",
            base,
            {**base, "valid": False},
            model_order=("a", "b"),
        )
        self.assertFalse(invalid["eligible"])
        self.assertTrue(invalid["missing_inconsistency"])
        mismatch = fixed_boundary_ab._compare_feature_pair(
            "stance_width_body",
            base,
            {**base, "semantic_signature_sha256": "E" * 64},
            model_order=("a", "b"),
        )
        self.assertFalse(mismatch["eligible"])
        self.assertEqual("feature_semantics_mismatch", mismatch["reason"])

    def test_strict_validator_rejects_accuracy_or_scoring_claims(self) -> None:
        # A minimal invalid mutation test uses a real valid report produced by
        # the integration case, keeping the validator tied to executable data.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            events_path, registry_path = root / "events.jsonl", root / "registry.json"
            a_frames, b_frames = root / "a.jsonl", root / "b.jsonl"
            a_timeline, b_timeline = root / "at.jsonl", root / "bt.jsonl"
            _write_jsonl(
                events_path,
                [
                    {
                        "event_id": "e1",
                        "person_track_id": 1,
                        "event_code": "FS01",
                        "start_ms": 0,
                        "end_ms": 1000,
                    }
                ],
            )
            registry_path.write_text(json.dumps(_registry()), encoding="utf-8")
            _write_jsonl(a_frames, _records(1, 0.0))
            _write_jsonl(b_frames, _records(2, 0.0))
            _write_jsonl(a_timeline, _timeline(1))
            _write_jsonl(b_timeline, _timeline(2))
            report = evaluate_fixed_boundary_pose_ab_files(
                events_path=events_path,
                registry_path=registry_path,
                model_inputs={
                    "a": {
                        "frames_path": a_frames,
                        "primary_timeline_path": a_timeline,
                        "model_sha256": MODEL_A_SHA,
                    },
                    "b": {
                        "frames_path": b_frames,
                        "primary_timeline_path": b_timeline,
                        "model_sha256": MODEL_B_SHA,
                    },
                },
                boundary_source_kind="external",
                boundary_source_label="external common boundary",
                source_id="synthetic",
            )
        accuracy_claim = copy.deepcopy(report)
        accuracy_claim["comparison_semantics"]["accuracy_claim"] = True
        with self.assertRaisesRegex(ValueError, "accuracy_claim=false"):
            validate_fixed_boundary_ab_report(accuracy_claim)
        score_claim = copy.deepcopy(report)
        score_claim["grade"] = "A"
        with self.assertRaisesRegex(ValueError, "prohibited scoring keys"):
            validate_fixed_boundary_ab_report(score_claim)
        threshold_claim = copy.deepcopy(report)
        threshold_claim["threshold"] = 0.5
        with self.assertRaisesRegex(ValueError, "prohibited scoring keys"):
            validate_fixed_boundary_ab_report(threshold_claim)
        provenance_diagnostic = copy.deepcopy(report)
        provenance_diagnostic["feature_pairs"][0]["models"]["a"][
            "provenance"
        ]["threshold"] = 0.5
        validate_fixed_boundary_ab_report(provenance_diagnostic)


if __name__ == "__main__":
    unittest.main()
