from __future__ import annotations

import json
import importlib.util
import unittest
from copy import deepcopy
from pathlib import Path

from rallymate_evaluation.residual_computability import (
    ResidualComputabilityError,
    build_residual_computability_audit,
    validate_residual_computability_audit,
)
from rallymate_features.event_features import FEATURE_DEFINITIONS


ROOT = Path(__file__).resolve().parents[1]
MODEL = "experiment"
FEATURE = "hip_center_y_body"


def _feature(event_id: str, *, valid: bool, frame: int) -> dict:
    definition = FEATURE_DEFINITIONS[FEATURE]
    return {
        "event_id": event_id,
        "feature_name": FEATURE,
        "models": {
            MODEL: {
                "valid": valid,
                "value": 0.5 if valid else None,
                "unit": definition["unit"],
                "confidence": 0.8 if valid else 0.0,
                "reason": "valid" if valid else "valid_fraction_below_quality_gate",
                "feature_version": definition["version"],
                "source_frames": [frame] if valid else [],
            }
        },
    }


def _event(event_id: str, start: int, end: int, *, measured: bool) -> dict:
    return {
        "boundary": {
            "event_id": event_id,
            "event_code": "FS01",
            "person_track_id": 1,
            "start_ms": start,
            "end_ms": end,
        },
        "indicators": [
            {
                "indicator_id": "FS01-M02",
                "required_features": [FEATURE],
                "models": {
                    MODEL: {
                        "measurement_status": "measured" if measured else "unavailable"
                    }
                },
            }
        ],
    }


def _frame(index: int, timestamp_ms: int, track_id: int, bbox: list[float]) -> dict:
    return {
        "frame": {
            "processed_index": index,
            "index": index,
            "timestamp_ms": timestamp_ms,
            "width": 100,
            "height": 100,
        },
        "detections": [
            {"class_name": "player", "track_id": track_id, "bbox_px": bbox}
        ],
        "poses": [],
    }


class ResidualComputabilityTests(unittest.TestCase):
    def _build(self) -> dict:
        events = [
            _event("start", 0, 0, measured=False),
            _event("border", 100, 100, measured=False),
            _event("switch", 200, 233, measured=False),
            _event("measured", 300, 300, measured=True),
        ]
        comparison = {
            "comparison_semantics": {"accuracy_claim": False},
            "models": {MODEL: {}},
            "registry_source": {"indicator_ids": ["FS01-M02"]},
            "events": events,
            "feature_pairs": [
                _feature("start", valid=False, frame=0),
                _feature("border", valid=False, frame=1),
                _feature("switch", valid=False, frame=2),
                _feature("measured", valid=True, frame=4),
            ],
        }
        registry = {
            "registry_version": "test-registry",
            "indicators": [
                {
                    "indicator_id": "FS01-M02",
                    "feasibility_level": "F2",
                    "required_events": ["FS01.preload"],
                    "required_features": [FEATURE],
                }
            ],
        }
        frames = [
            _frame(0, 0, 1, [10, 10, 50, 90]),
            _frame(1, 100, 1, [0, 10, 40, 90]),
            _frame(2, 200, 1, [10, 10, 50, 90]),
            _frame(3, 233, 2, [50, 10, 90, 90]),
            _frame(4, 300, 1, [10, 10, 50, 90]),
        ]
        timeline = [
            {"processed_index": 0, "source_track_id": 1},
            {"processed_index": 1, "source_track_id": 1},
            {"processed_index": 2, "source_track_id": 1},
            {"processed_index": 3, "source_track_id": 2},
            {"processed_index": 4, "source_track_id": 1},
        ]
        source = {"path": "synthetic", "sha256": "A" * 64}
        return build_residual_computability_audit(
            comparison=comparison,
            registry=registry,
            frames=frames,
            timeline=timeline,
            model_key=MODEL,
            sources={name: source for name in ("comparison", "registry", "frames", "timeline")},
        )

    def test_classifies_structural_residuals_without_weakening_gates(self) -> None:
        report = self._build()
        self.assertEqual(
            "all_registry_indicators_have_real_measured_instances_residuals_fail_closed",
            report["status"],
        )
        self.assertEqual(1, report["counts"]["indicator_with_measured_instance_count"])
        self.assertEqual(3, report["counts"]["unavailable_indicator_event_instance_count"])
        self.assertEqual(
            {
                "primary_bbox_clipped_at_image_boundary",
                "primary_source_track_transition",
                "video_start_boundary_censored",
            },
            {item["classification"] for item in report["residual_events"]},
        )
        self.assertTrue(
            all(item["current_decision"] == "keep_unavailable" for item in report["residual_events"])
        )

    def test_validator_rejects_zero_fill_or_accuracy_claim(self) -> None:
        report = self._build()
        tampered = deepcopy(report)
        tampered["safety"]["zero_fill_used"] = True
        with self.assertRaisesRegex(ResidualComputabilityError, "unsafe"):
            validate_residual_computability_audit(tampered)
        tampered = deepcopy(report)
        tampered["residual_events"][0]["recoverable_by_zero_fill"] = True
        with self.assertRaisesRegex(ResidualComputabilityError, "quality invariant"):
            validate_residual_computability_audit(tampered)

    def test_real_report_proves_all_13_have_measured_examples(self) -> None:
        report = json.loads(
            (ROOT / "reports" / "residual-indicator-computability-small-roi-v1.json").read_text(
                encoding="utf-8"
            )
        )
        validate_residual_computability_audit(report)
        self.assertEqual(13, report["counts"]["indicator_with_measured_instance_count"])
        self.assertEqual(0, report["counts"]["indicator_without_measured_instance_count"])
        self.assertEqual(423, report["counts"]["measured_indicator_event_instance_count"])
        self.assertEqual(19, report["counts"]["unavailable_indicator_event_instance_count"])
        self.assertTrue(all(item["evidence_example"] for item in report["per_indicator"]))

    def test_video_ranges_merge_overlapping_residual_events(self) -> None:
        path = ROOT / "scripts" / "render_residual_computability_video.py"
        spec = importlib.util.spec_from_file_location("render_residual_computability_video", path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        ranges = module.merge_residual_ranges(
            [
                {"event_id": "a", "start_ms": 100, "end_ms": 200, "classification": "primary_bbox_clipped_at_image_boundary", "affected_indicator_instance_count": 2},
                {"event_id": "b", "start_ms": 180, "end_ms": 300, "classification": "primary_source_track_transition", "affected_indicator_instance_count": 3},
            ],
            20,
        )
        self.assertEqual(1, len(ranges))
        self.assertEqual(80, ranges[0]["start_ms"])
        self.assertEqual(320, ranges[0]["end_ms"])
        self.assertEqual(5, ranges[0]["affected_indicator_instance_count"])


if __name__ == "__main__":
    unittest.main()
