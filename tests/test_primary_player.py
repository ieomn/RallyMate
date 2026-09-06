from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

try:
    from jsonschema import Draft202012Validator
except ModuleNotFoundError:  # Runtime selection does not depend on jsonschema.
    Draft202012Validator = None

from rallymate_tracking.primary_player import (
    diagnose_primary_timeline,
    primary_timeline_algorithm_version,
    registry_required_primary_player_version,
    select_primary_player_timeline,
)
from rallymate_vision.pose.metadata import keypoint_schema


def _pose(track_id: int, offset: float = 0.0, valid: bool = True) -> dict:
    points = []
    for definition in keypoint_schema("coco17")["keypoints"]:
        index = definition["index"]
        points.append(
            {
                "index": index,
                "name": definition["name"],
                "downstream_joint_id": definition["downstream_joint_id"],
                "x_px": 40.0 + index + offset * 100,
                "y_px": 30.0 + index,
                "x_normalized": 0.4 + index * 0.001 + offset,
                "y_normalized": 0.3 + index * 0.001,
                "confidence": 0.9 if valid else 0.1,
            }
        )
    return {
        "person_track_id": track_id,
        "confidence": 0.9,
        "keypoint_format": "coco17",
        "keypoints": points,
    }


def _record(index: int, players: list[tuple[int, list[float], bool]]) -> dict:
    return {
        "frame": {
            "index": index,
            "processed_index": index,
            "timestamp_ms": index * 40,
            "width": 200,
            "height": 120,
        },
        "detections": [
            {
                "class_name": "player",
                "track_id": track_id,
                "bbox_px": bbox,
                "confidence": 0.9,
            }
            for track_id, bbox, _ in players
        ],
        "poses": [
            _pose(track_id, offset=index * 0.002, valid=valid)
            for track_id, _, valid in players
            if valid
        ],
    }


class PrimaryPlayerTests(unittest.TestCase):
    def test_timeline_version_binding_rejects_legacy_or_mixed_artifacts(self) -> None:
        timeline = select_primary_player_timeline(
            [_record(index, [(1, [30, 15, 100, 115], True)]) for index in range(2)]
        )["timeline"]
        self.assertEqual(
            primary_timeline_algorithm_version(
                timeline, expected_version="primary-player-v0.3.0"
            ),
            "primary-player-v0.3.0",
        )
        legacy = copy.deepcopy(timeline)
        for item in legacy:
            item["selection_algorithm_version"] = "primary-player-v0.2.0"
        with self.assertRaisesRegex(ValueError, "does not match feasibility registry"):
            primary_timeline_algorithm_version(
                legacy, expected_version="primary-player-v0.3.0"
            )
        mixed = copy.deepcopy(timeline)
        mixed[-1]["selection_algorithm_version"] = "primary-player-v0.2.0"
        with self.assertRaisesRegex(ValueError, "mixes algorithm versions"):
            primary_timeline_algorithm_version(mixed)

        self.assertEqual(
            registry_required_primary_player_version(
                [
                    {"versions": {"primary_player": "primary-player-v0.3.0"}},
                    {"versions": {"primary_player": "primary-player-v0.3.0"}},
                ]
            ),
            "primary-player-v0.3.0",
        )

    def test_persistent_pose_track_beats_short_lived_larger_box(self) -> None:
        records = []
        for index in range(8):
            players = [(1, [20 + index, 20, 80 + index, 110], True)]
            if index < 2:
                players.append((2, [5, 5, 190, 118], False))
            records.append(_record(index, players))
        result = select_primary_player_timeline(records)
        self.assertEqual(
            {item["source_track_id"] for item in result["timeline"]}, {1}
        )
        self.assertEqual(
            {item["primary_player_id"] for item in result["timeline"]}, {1}
        )
        self.assertGreater(result["timeline"][0]["selection_score_margin"], 0.0)
        self.assertFalse(result["timeline"][0]["identity_ambiguous"])
        self.assertEqual(
            result["timeline"][0]["identity_ambiguity_status"],
            "distinct_score",
        )

    def test_equivalent_players_are_order_invariant_and_report_ambiguity(self) -> None:
        records = [
            _record(
                index,
                [
                    (7, [25 + index, 15, 95 + index, 115], True),
                    (3, [25 + index, 15, 95 + index, 115], True),
                ],
            )
            for index in range(6)
        ]
        reversed_records = copy.deepcopy(records)
        for record in reversed_records:
            record["detections"].reverse()

        forward = select_primary_player_timeline(records)
        reversed_result = select_primary_player_timeline(reversed_records)

        self.assertEqual(forward, reversed_result)
        self.assertEqual(
            [item["source_track_id"] for item in forward["timeline"]],
            [3] * 6,
        )
        for item in forward["timeline"]:
            self.assertEqual(item["selection_candidate_count"], 2)
            self.assertIsNotNone(item["selection_best_score"])
            self.assertIsNotNone(item["selection_second_best_score"])
            self.assertEqual(item["selection_score_margin"], 0.0)
            self.assertTrue(item["identity_ambiguous"])
            self.assertEqual(
                item["identity_ambiguity_status"], "ambiguous_score_tie"
            )
            self.assertEqual(item["selection_competing_source_track_id"], 7)

        diagnostics = diagnose_primary_timeline(records, forward["timeline"])
        self.assertTrue(diagnostics["primary_identity_ambiguous"])
        self.assertEqual(
            diagnostics["primary_identity_ambiguity_status"],
            "score_tie_detected",
        )
        self.assertEqual(diagnostics["primary_identity_ambiguous_frames"], list(range(6)))
        self.assertEqual(diagnostics["selection_score_margin_min"], 0.0)
        self.assertIn("primary_identity_ambiguous", diagnostics["quality_flags"])
        self.assertIn("not_identity_accuracy", diagnostics["diagnostic_semantics"])

    def test_single_candidate_does_not_claim_identity_accuracy(self) -> None:
        records = [_record(index, [(4, [30, 15, 100, 115], True)]) for index in range(3)]
        result = select_primary_player_timeline(records)
        diagnostics = diagnose_primary_timeline(records, result["timeline"])
        self.assertTrue(
            all(
                item["identity_ambiguity_status"] == "single_candidate"
                and item["identity_ambiguous"] is False
                and item["selection_score_margin"] is None
                for item in result["timeline"]
            )
        )
        self.assertFalse(diagnostics["primary_identity_ambiguous"])
        self.assertEqual(
            diagnostics["primary_identity_ambiguity_status"],
            "no_score_tie_detected",
        )
        self.assertNotIn("primary_identity_ambiguous", diagnostics["quality_flags"])

    def test_legacy_timeline_remains_diagnosable_without_false_uniqueness(self) -> None:
        records = [_record(index, [(1, [30, 15, 100, 115], True)]) for index in range(3)]
        result = select_primary_player_timeline(records)
        legacy_timeline = copy.deepcopy(result["timeline"])
        new_fields = {
            "selection_candidate_count",
            "selection_best_score",
            "selection_second_best_score",
            "selection_score_margin",
            "selection_competing_source_track_id",
            "identity_ambiguous",
            "identity_ambiguity_status",
        }
        for item in legacy_timeline:
            for field in new_fields:
                item.pop(field)
        diagnostics = diagnose_primary_timeline(records, legacy_timeline)
        self.assertIsNone(diagnostics["primary_identity_ambiguous"])
        self.assertEqual(
            diagnostics["primary_identity_ambiguity_status"],
            "not_assessed_legacy_timeline",
        )
        self.assertNotIn("primary_identity_ambiguous", diagnostics["quality_flags"])

    def test_primary_player_contract_accepts_new_and_legacy_timeline_records(self) -> None:
        if Draft202012Validator is None:
            self.skipTest("jsonschema unavailable")
        schema_path = Path(__file__).parents[1] / "contracts" / "primary-player.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema)
        record = select_primary_player_timeline(
            [_record(0, [(1, [30, 15, 100, 115], True)])]
        )["timeline"][0]
        validator.validate(record)

        legacy = copy.deepcopy(record)
        for field in (
            "selection_candidate_count",
            "selection_best_score",
            "selection_second_best_score",
            "selection_score_margin",
            "selection_competing_source_track_id",
            "identity_ambiguous",
            "identity_ambiguity_status",
        ):
            legacy.pop(field)
        validator.validate(legacy)

    def test_fragmented_source_tracks_are_stitched_under_stable_primary_id(self) -> None:
        records = []
        for index in range(6):
            track_id = 1 if index < 3 else 9
            records.append(
                _record(index, [(track_id, [30 + index, 15, 100 + index, 115], True)])
            )
        result = select_primary_player_timeline(records)
        diagnostics = diagnose_primary_timeline(records, result["timeline"])
        self.assertEqual(
            [item["primary_player_id"] for item in result["timeline"]], [1] * 6
        )
        self.assertEqual(diagnostics["source_track_ids"], [1, 9])
        self.assertEqual(diagnostics["source_track_switch_candidate_count"], 1)
        self.assertIsNone(diagnostics["confirmed_id_switch_count"])
        self.assertEqual(
            diagnostics["confirmed_id_switch_status"], "ground_truth_required"
        )

    def test_missing_pose_is_reported_not_zero_filled(self) -> None:
        records = [
            _record(index, [(1, [30, 15, 100, 115], index != 2)])
            for index in range(5)
        ]
        result = select_primary_player_timeline(records)
        diagnostics = diagnose_primary_timeline(records, result["timeline"])
        self.assertEqual(diagnostics["track_coverage_fraction"], 1.0)
        self.assertEqual(diagnostics["pose_coverage_fraction"], 0.8)
        self.assertEqual(diagnostics["longest_pose_missing_frames"], 1)
        self.assertEqual(diagnostics["longest_pose_missing_ms"], 40)

    def test_diagnostics_recompute_validity_from_current_pose_artifact(self) -> None:
        records = [_record(index, [(1, [30, 15, 100, 115], True)]) for index in range(4)]
        result = select_primary_player_timeline(records)
        for item in result["timeline"]:
            item["keypoint_valid_fraction"] = 0.0  # stale value from another backend
        diagnostics = diagnose_primary_timeline(records, result["timeline"])
        self.assertEqual(diagnostics["keypoint_valid_fraction"], 1.0)

    def test_out_of_frame_joint_is_excluded_from_current_pose_validity(self) -> None:
        records = [_record(0, [(1, [30, 15, 100, 115], True)])]
        point = next(
            item
            for item in records[0]["poses"][0]["keypoints"]
            if item["name"] == "left_shoulder"
        )
        point["in_frame"] = False
        result = select_primary_player_timeline(records)
        result["timeline"][0]["keypoint_valid_fraction"] = 1.0
        diagnostics = diagnose_primary_timeline(records, result["timeline"])
        self.assertEqual(diagnostics["keypoint_valid_fraction"], 0.875)

    def test_jump_and_swap_diagnostics_preserve_joint_level_evidence(self) -> None:
        records = [
            _record(index, [(1, [30, 15, 100, 115], True)])
            for index in range(8)
        ]
        for record in records:
            points = {point["name"]: point for point in record["poses"][0]["keypoints"]}
            points["left_wrist"]["x_normalized"] = 0.2
            points["right_wrist"]["x_normalized"] = 0.8
        jump_points = {
            point["name"]: point for point in records[4]["poses"][0]["keypoints"]
        }
        jump_points["left_shoulder"]["x_normalized"] += 0.5
        jump_points["left_wrist"]["x_normalized"] = 0.8
        jump_points["right_wrist"]["x_normalized"] = 0.2

        result = select_primary_player_timeline(records)
        diagnostics = diagnose_primary_timeline(records, result["timeline"])

        self.assertIn(
            "left_shoulder", diagnostics["keypoint_jump_candidate_joints"]
        )
        self.assertTrue(
            diagnostics["keypoint_jump_candidate_frames_by_joint"][
                "left_shoulder"
            ]
        )
        wrist_pair = next(
            pair
            for pair in diagnostics["left_right_swap_candidate_joint_pairs"]
            if pair["left_joint"] == "left_wrist"
            and pair["right_joint"] == "right_wrist"
        )
        self.assertIn(4, wrist_pair["frames"])
        self.assertIn(
            "jump_and_swap_evidence_is_joint_scoped",
            diagnostics["diagnostic_semantics"],
        )


if __name__ == "__main__":
    unittest.main()
