from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from rallymate_vision.pose.adapters import PoseEstimator
from rallymate_vision.pose.base import PoseBackendOutput, PoseModelMetadata
from rallymate_vision.pose.metadata import keypoint_schema
from rallymate_vision.pose.registry import build_pose_backend


class StubBackend:
    def __init__(self, outputs):
        self.outputs = outputs
        self.roi_shapes = []
        self.timestamps = None

    def load(self) -> None:
        return None

    def infer(self, rois, *, timestamps_ms=None):
        self.roi_shapes = [roi.shape for roi in rois]
        self.timestamps = timestamps_ms
        return self.outputs

    def metadata(self) -> PoseModelMetadata:
        return PoseModelMetadata(
            backend="stub",
            runtime="test",
            profile="realtime",
            model_name="stub-coco17",
            model_path="stub.pt",
            model_sha256="0" * 64,
            input_size=(640, 640),
            native_keypoint_format="coco17",
            native_keypoint_count=17,
            keypoint_schema_version="1.0.0",
            load_seconds=0.0,
            load_semantics="test",
        )


class PoseBackendTests(unittest.TestCase):
    def test_roi_adapter_preserves_legacy_coco17_contract(self) -> None:
        xy = np.asarray([[10.0 + i, 20.0 + i] for i in range(17)])
        scores = np.linspace(0.5, 0.9, 17)
        backend = StubBackend([PoseBackendOutput(xy, scores, 0.876543)])
        estimator = PoseEstimator(backend)
        frame = np.zeros((120, 200, 3), dtype=np.uint8)
        players = [
            {"track_id": 3, "bbox_px": [10, 10, 40, 60]},
            {"track_id": 9, "bbox_px": [50, 20, 150, 100]},
        ]

        poses = estimator.estimate(
            frame, players, max_players=1, timestamp_ms=1234
        )

        self.assertEqual(len(poses), 1)
        pose = poses[0]
        self.assertEqual(pose["person_track_id"], 9)
        self.assertEqual(pose["roi_bbox_px"], [35, 8, 165, 112])
        self.assertEqual(pose["keypoint_format"], "coco17")
        self.assertEqual(len(pose["keypoints"]), 17)
        self.assertEqual(pose["keypoints"][0]["name"], "nose")
        self.assertEqual(pose["keypoints"][0]["downstream_joint_id"], "J004")
        self.assertEqual(pose["keypoints"][0]["x_px"], 45.0)
        self.assertEqual(pose["keypoints"][0]["y_px"], 28.0)
        self.assertEqual(pose["confidence"], 0.87654)
        self.assertEqual(backend.roi_shapes, [(104, 130, 3)])
        self.assertEqual(backend.timestamps, [1234])

    def test_backend_must_return_one_slot_per_roi(self) -> None:
        backend = StubBackend([])
        estimator = PoseEstimator(backend)
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        with self.assertRaisesRegex(ValueError, "one output slot per ROI"):
            estimator.estimate(
                frame,
                [{"track_id": 1, "bbox_px": [10, 10, 80, 90]}],
                max_players=1,
            )

    def test_small_roi_guard_is_configurable_but_default_is_unchanged(self) -> None:
        xy = np.asarray([[1.0 + i, 2.0 + i] for i in range(17)])
        scores = np.full(17, 0.8)
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        player = {"track_id": 7, "bbox_px": [40, 40, 55, 65]}

        default_backend = StubBackend([])
        self.assertEqual(
            [], PoseEstimator(default_backend).estimate(frame, [player], max_players=1)
        )
        self.assertEqual([], default_backend.roi_shapes)

        experimental_backend = StubBackend(
            [PoseBackendOutput(xy, scores, 0.8)]
        )
        poses = PoseEstimator(
            experimental_backend, min_roi_size_px=8
        ).estimate(frame, [player], max_players=1)
        self.assertEqual(1, len(poses))
        self.assertEqual(7, poses[0]["person_track_id"])
        self.assertEqual([(32, 20, 3)], experimental_backend.roi_shapes)

        with self.assertRaisesRegex(ValueError, "positive integer"):
            PoseEstimator(experimental_backend, min_roi_size_px=0)
        with self.assertRaisesRegex(ValueError, "finite non-negative"):
            PoseEstimator(experimental_backend, roi_margin=-0.1)

    def test_halpe26_registry_preserves_official_first_17_order(self) -> None:
        coco = keypoint_schema("coco17")["keypoints"]
        halpe = keypoint_schema("halpe26")["keypoints"]
        self.assertEqual([item["name"] for item in halpe[:17]], [item["name"] for item in coco])
        self.assertEqual(
            [item["name"] for item in halpe[17:]],
            [
                "head",
                "neck",
                "hip",
                "left_big_toe",
                "right_big_toe",
                "left_small_toe",
                "right_small_toe",
                "left_heel",
                "right_heel",
            ],
        )
        self.assertTrue(
            all(item["downstream_joint_id"] is None for item in halpe[17:])
        )

    def test_coco_wholebody133_registry_preserves_official_groups(self) -> None:
        coco = keypoint_schema("coco17")["keypoints"]
        wholebody = keypoint_schema("coco_wholebody133")
        points = wholebody["keypoints"]
        self.assertEqual(len(points), 133)
        self.assertEqual([item["name"] for item in points[:17]], [item["name"] for item in coco])
        self.assertEqual(
            [item["name"] for item in points[17:23]],
            [
                "left_big_toe",
                "left_small_toe",
                "left_heel",
                "right_big_toe",
                "right_small_toe",
                "right_heel",
            ],
        )
        self.assertEqual(points[23]["name"], "face-0")
        self.assertEqual(points[90]["name"], "face-67")
        self.assertEqual(points[91]["name"], "left_hand_root")
        self.assertEqual(points[112]["name"], "right_hand_root")
        self.assertEqual(points[132]["name"], "right_pinky_finger4")
        self.assertTrue(all(item["downstream_joint_id"] is None for item in points[17:]))

    def test_rtmpose_factory_forwards_registered_native_topology(self) -> None:
        with patch(
            "rallymate_vision.pose.registry.RtmposePoseBackend"
        ) as backend_class:
            build_pose_backend(
                "rtmpose",
                model_path=Path("wholebody.pth"),
                config_path=Path("wholebody.py"),
                device="0",
                input_size=640,
                confidence=0.25,
                runtime="pytorch",
                profile="analysis",
                native_keypoint_format="coco_wholebody133",
            )
        self.assertEqual(
            backend_class.call_args.kwargs["native_keypoint_format"],
            "coco_wholebody133",
        )
