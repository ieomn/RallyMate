"""Geometry export keeps exact proof values and independent mutable outputs."""
from __future__ import annotations

from dataclasses import replace
import json
import unittest
from unittest.mock import patch

import numpy as np

from rallymate_features import schemas


def native_sequence():
    count = 5
    names = ("left_ankle", "right_ankle")
    bindings = tuple({
        "video_sha256": "a" * 64, "frame_index": 100 + i, "processed_index": i,
        "pts": 75000 + i * 40, "time_base_num": 1, "time_base_den": 1000,
        "person_track_id": 7, "selection_epoch": 2, "camera_reference_epoch": 3,
    } for i in range(count))
    return schemas.PoseSequence(
        timestamp_ms=np.arange(count) * 40, source_frames=np.arange(100, 100 + count),
        keypoints_xy={name: np.ones((count, 2)) for name in names},
        confidence={name: np.full(count, np.nan) for name in names},
        model_estimate_masks={"left_ankle": np.array([True, False, True, False, True]),
                              "right_ankle": np.array([False, True, True, False, True])},
        model_estimate_bindings=bindings,
    )


class ModelGeometryAllocationTests(unittest.TestCase):
    def test_present_joints_allocate_no_fallback_and_preserve_exact_values(self):
        sequence = native_sequence()
        with patch.object(schemas.np, "zeros", side_effect=AssertionError("unused fallback allocation")):
            actual = schemas.model_geometry_evidence(sequence, [1, 2, 3])
        expected = {
            "measurement_provenance": dict(schemas.MODEL_MEASUREMENT_PROVENANCE),
            "source_window": {
                "start_ms": 40, "end_ms": 120,
                "source_frames": [101, 102, 103], "video_timestamps_ms": [40, 80, 120],
                "source_timestamps_ms": [75040.0, 75080.0, 75120.0],
                "source_bindings": [dict(row) for row in sequence.model_estimate_bindings[1:4]],
                "video_sha256": "a" * 64, "person_track_id": 7,
                "selection_epoch": 2, "camera_reference_epoch": 3,
            },
            "geometry_mask": {"left_ankle": [False, True, False], "right_ankle": [True, True, False]},
        }
        self.assertEqual(actual, expected)
        self.assertEqual(json.dumps(actual, ensure_ascii=False), json.dumps(expected, ensure_ascii=False))

    def test_missing_joint_stays_false_and_allocates_only_once(self):
        sequence = native_sequence()
        original = np.zeros
        with patch.object(schemas.np, "zeros", wraps=original) as zeros:
            result = schemas.model_geometry_evidence(sequence, range(5), ["right_ankle", "missing"])
        self.assertEqual(result["geometry_mask"]["missing"], [False] * 5)
        self.assertEqual(result["geometry_mask"]["right_ankle"], [False, True, True, False, True])
        zeros.assert_called_once_with(5, dtype=bool)

    def test_each_call_has_independent_lists_bindings_and_provenance(self):
        sequence = native_sequence()
        first = schemas.model_geometry_evidence(sequence, range(5))
        second = schemas.model_geometry_evidence(sequence, range(5))
        before = json.dumps(second)
        first["geometry_mask"]["left_ankle"][0] = False
        first["source_window"]["source_bindings"][0]["pts"] = -1
        first["source_window"]["source_frames"][0] = -1
        first["measurement_provenance"]["scoring_allowed"] = True
        self.assertEqual(json.dumps(second), before)
        self.assertTrue(sequence.model_estimate_masks["left_ankle"][0])
        self.assertEqual(sequence.model_estimate_bindings[0]["pts"], 75000)

    def test_empty_missing_and_discontinuous_bindings_preserve_unavailable_evidence(self):
        sequence = native_sequence()
        provenance = {"measurement_provenance": dict(schemas.MODEL_MEASUREMENT_PROVENANCE)}
        self.assertEqual(schemas.model_geometry_evidence(sequence, []), provenance)
        self.assertEqual(schemas.model_geometry_evidence(sequence, [0, 2]), provenance)
        without_model = replace(sequence, model_estimate_bindings=None, model_estimate_masks={})
        self.assertEqual(schemas.model_geometry_evidence(without_model, range(5)), {})
        bindings = list(sequence.model_estimate_bindings)
        bindings[2] = None
        masks = {name: values.copy() for name, values in sequence.model_estimate_masks.items()}
        for mask in masks.values():
            mask[2] = False
        missing = replace(sequence, model_estimate_bindings=tuple(bindings), model_estimate_masks=masks)
        self.assertEqual(schemas.model_geometry_evidence(missing, range(5)), provenance)


if __name__ == "__main__":
    unittest.main()
