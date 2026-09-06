from __future__ import annotations

import copy
import unittest

from rallymate_evaluation.pose_policy_composition import (
    add_missing_keypoints_from_mapped_topology_candidate_rows,
    add_missing_keypoints_from_same_topology_candidate_rows,
    compose_disjoint_pose_policy_rows,
)


def _frame(index: int, pose_value: str | None) -> dict:
    poses = (
        [{"person_track_id": 1, "value": pose_value}]
        if pose_value is not None
        else []
    )
    return {
        "frame": {"processed_index": index, "timestamp_ms": index * 40},
        "detections": [{"track_id": 1}],
        "poses": poses,
    }


class PosePolicyCompositionTests(unittest.TestCase):
    def test_disjoint_same_track_pose_changes_are_composed(self) -> None:
        baseline = [_frame(0, "base0"), _frame(1, None), _frame(2, "base2")]
        margin = [_frame(0, "margin0"), _frame(1, None), _frame(2, "base2")]
        small = [_frame(0, "base0"), _frame(1, "small1"), _frame(2, "base2")]
        rows, audit = compose_disjoint_pose_policy_rows(
            baseline_rows=baseline,
            timeline_rows=[
                {"processed_index": index, "source_track_id": 1}
                for index in range(3)
            ],
            experiments=[
                {
                    "name": "margin",
                    "frame_rows": margin,
                    "target_processed_indexes": {0},
                },
                {
                    "name": "small",
                    "frame_rows": small,
                    "target_processed_indexes": {1},
                },
            ],
        )
        self.assertEqual("margin0", rows[0]["poses"][0]["value"])
        self.assertEqual("small1", rows[1]["poses"][0]["value"])
        self.assertEqual("base2", rows[2]["poses"][0]["value"])
        self.assertEqual(2, audit["target_frame_count"])
        self.assertTrue(audit["target_sets_disjoint"])

    def test_overlap_and_non_pose_mutation_are_rejected(self) -> None:
        baseline = [_frame(0, "base")]
        timeline = [{"processed_index": 0, "source_track_id": 1}]
        experiment = [_frame(0, "changed")]
        with self.assertRaisesRegex(ValueError, "overlap"):
            compose_disjoint_pose_policy_rows(
                baseline_rows=baseline,
                timeline_rows=timeline,
                experiments=[
                    {"name": "a", "frame_rows": experiment, "target_processed_indexes": {0}},
                    {"name": "b", "frame_rows": experiment, "target_processed_indexes": {0}},
                ],
            )

    def test_missing_keypoint_addition_preserves_every_valid_baseline_point(self) -> None:
        def pose_frame(index: int, left: float, right: float) -> dict:
            return {
                "frame": {"processed_index": index, "timestamp_ms": index * 40},
                "detections": [{"track_id": 1}],
                "poses": [{
                    "person_track_id": 1,
                    "keypoint_format": "halpe26",
                    "confidence": 0.5,
                    "keypoints": [
                        {"index": 0, "name": "left_ankle", "x_normalized": 0.2, "y_normalized": 0.8, "confidence": left},
                        {"index": 1, "name": "right_ankle", "x_normalized": 0.8, "y_normalized": 0.8, "confidence": right},
                    ],
                }],
            }

        baseline = [pose_frame(0, 0.9, 0.1), pose_frame(1, 0.9, 0.9)]
        candidate = [pose_frame(0, 0.3, 0.8), pose_frame(1, 0.1, 0.1)]
        candidate[0]["poses"][0]["keypoints"][0]["x_normalized"] = 0.7
        fused, audit = add_missing_keypoints_from_same_topology_candidate_rows(
            baseline_rows=baseline,
            candidate_rows=candidate,
            timeline_rows=[
                {"processed_index": 0, "source_track_id": 1},
                {"processed_index": 1, "source_track_id": 1},
            ],
            target_processed_indexes={0, 1},
            allowed_joint_names={"left_ankle", "right_ankle"},
        )
        points = fused[0]["poses"][0]["keypoints"]
        self.assertEqual(0.2, points[0]["x_normalized"])
        self.assertEqual(0.8, points[1]["confidence"])
        self.assertEqual(1, audit["changed_frame_count"])
        self.assertEqual(1, audit["added_valid_joint_observation_count"])
        self.assertEqual(0, audit["baseline_valid_coordinates_overwritten"])

    def test_missing_keypoint_addition_rejects_topology_or_non_pose_drift(self) -> None:
        baseline = [{
            "frame": {"processed_index": 0},
            "detections": [{"track_id": 1}],
            "poses": [{"person_track_id": 1, "keypoint_format": "halpe26", "keypoints": [
                {"index": 0, "name": "left_ankle", "x_normalized": 0.2, "y_normalized": 0.8, "confidence": 0.1}
            ]}],
        }]
        timeline = [{"processed_index": 0, "source_track_id": 1}]
        candidate = copy.deepcopy(baseline)
        candidate[0]["poses"][0]["keypoints"][0]["name"] = "right_ankle"
        with self.assertRaisesRegex(ValueError, "topology/order"):
            add_missing_keypoints_from_same_topology_candidate_rows(
                baseline_rows=baseline,
                candidate_rows=candidate,
                timeline_rows=timeline,
                target_processed_indexes={0},
                allowed_joint_names={"left_ankle"},
            )
        candidate = copy.deepcopy(baseline)
        candidate[0]["detections"] = []
        with self.assertRaisesRegex(ValueError, "non-Pose"):
            add_missing_keypoints_from_same_topology_candidate_rows(
                baseline_rows=baseline,
                candidate_rows=candidate,
                timeline_rows=timeline,
                target_processed_indexes={0},
                allowed_joint_names={"left_ankle"},
            )
        mutated = [_frame(0, "changed")]
        mutated[0]["detections"] = []
        with self.assertRaisesRegex(ValueError, "non-Pose"):
            compose_disjoint_pose_policy_rows(
                baseline_rows=baseline,
                timeline_rows=timeline,
                experiments=[
                    {"name": "a", "frame_rows": mutated, "target_processed_indexes": {0}}
                ],
            )

    def test_mapped_topology_addition_preserves_halpe_identity_and_valid_points(self) -> None:
        baseline = [{
            "frame": {"processed_index": 0, "timestamp_ms": 0},
            "detections": [{"track_id": 1}],
            "poses": [{
                "person_track_id": 1,
                "keypoint_format": "halpe26",
                "keypoints": [
                    {
                        "index": 5,
                        "name": "left_shoulder",
                        "downstream_joint_id": "J033",
                        "x_normalized": 0.2,
                        "y_normalized": 0.3,
                        "confidence": 0.9,
                    },
                    {
                        "index": 20,
                        "name": "left_big_toe",
                        "downstream_joint_id": None,
                        "x_normalized": 0.1,
                        "y_normalized": 0.9,
                        "confidence": 0.1,
                    },
                ],
            }],
        }]
        candidate = copy.deepcopy(baseline)
        candidate_pose = candidate[0]["poses"][0]
        candidate_pose["keypoint_format"] = "coco_wholebody133"
        candidate_pose["keypoints"] = [
            {
                "index": 5,
                "name": "left_shoulder",
                "downstream_joint_id": "J033",
                "x_normalized": 0.8,
                "y_normalized": 0.4,
                "confidence": 0.95,
            },
            {
                "index": 17,
                "name": "left_big_toe",
                "downstream_joint_id": None,
                "x_normalized": 0.4,
                "y_normalized": 0.85,
                "confidence": 0.8,
            },
        ]
        fused, audit = add_missing_keypoints_from_mapped_topology_candidate_rows(
            baseline_rows=baseline,
            candidate_rows=candidate,
            timeline_rows=[{"processed_index": 0, "source_track_id": 1}],
            target_processed_indexes={0},
            target_to_candidate_joint_names={
                "left_shoulder": "left_shoulder",
                "left_big_toe": "left_big_toe",
            },
            baseline_keypoint_format="halpe26",
            candidate_keypoint_format="coco_wholebody133",
        )
        points = fused[0]["poses"][0]["keypoints"]
        self.assertEqual(baseline[0]["poses"][0]["keypoints"][0], points[0])
        self.assertEqual(20, points[1]["index"])
        self.assertEqual("left_big_toe", points[1]["name"])
        self.assertEqual(0.4, points[1]["x_normalized"])
        self.assertEqual(0.8, points[1]["confidence"])
        self.assertEqual("halpe26", fused[0]["poses"][0]["keypoint_format"])
        self.assertEqual(1, audit["added_valid_joint_observation_count"])
        self.assertEqual(0, audit["baseline_valid_coordinates_overwritten"])
        self.assertTrue(audit["baseline_topology_preserved"])

    def test_mapped_topology_addition_rejects_format_map_or_non_pose_drift(self) -> None:
        baseline = [{
            "frame": {"processed_index": 0},
            "detections": [{"track_id": 1}],
            "poses": [{
                "person_track_id": 1,
                "keypoint_format": "halpe26",
                "keypoints": [{
                    "index": 15,
                    "name": "left_ankle",
                    "downstream_joint_id": "J143",
                    "x_normalized": 0.2,
                    "y_normalized": 0.8,
                    "confidence": 0.1,
                }],
            }],
        }]
        candidate = copy.deepcopy(baseline)
        candidate[0]["poses"][0]["keypoint_format"] = "coco_wholebody133"
        candidate[0]["poses"][0]["keypoints"][0].update(
            {"index": 15, "confidence": 0.9}
        )
        kwargs = {
            "baseline_rows": baseline,
            "candidate_rows": candidate,
            "timeline_rows": [{"processed_index": 0, "source_track_id": 1}],
            "target_processed_indexes": {0},
            "target_to_candidate_joint_names": {"left_ankle": "left_ankle"},
            "baseline_keypoint_format": "halpe26",
            "candidate_keypoint_format": "coco_wholebody133",
        }
        wrong_format = copy.deepcopy(candidate)
        wrong_format[0]["poses"][0]["keypoint_format"] = "halpe26"
        with self.assertRaisesRegex(ValueError, "candidate keypoint format"):
            add_missing_keypoints_from_mapped_topology_candidate_rows(
                **{**kwargs, "candidate_rows": wrong_format}
            )
        with self.assertRaisesRegex(ValueError, "absent"):
            add_missing_keypoints_from_mapped_topology_candidate_rows(
                **{
                    **kwargs,
                    "target_to_candidate_joint_names": {
                        "right_ankle": "left_ankle"
                    },
                }
            )
        drifted = copy.deepcopy(candidate)
        drifted[0]["detections"] = []
        with self.assertRaisesRegex(ValueError, "non-Pose"):
            add_missing_keypoints_from_mapped_topology_candidate_rows(
                **{**kwargs, "candidate_rows": drifted}
            )


if __name__ == "__main__":
    unittest.main()
