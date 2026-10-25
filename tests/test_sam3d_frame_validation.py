"""Contract tests use explicit constructed estimates, not inference ground truth."""
import json
from pathlib import Path
import unittest

import jsonschema
import numpy as np

from rallymate_vision.validation import _validate_sam_pose, validate_frame_observation, OutputValidationError


def bound_pose():
    frame = {"index": 3, "processed_index": 0, "timestamp_ms": 100, "timestamp_source": "decoder_pts", "width": 100, "height": 50}
    binding = {"video_sha256": "a" * 64, "frame_index": 3, "processed_index": 0, "pts": 3,
               "time_base_num": 1, "time_base_den": 30, "timestamp_ms": 100,
               "person_track_id": 1, "selection_epoch": 0, "camera_reference_epoch": 0}
    point = {"source": "sam3d", "observation_semantics": "model_estimate", "confidence": None,
             "confidence_semantics": "not_provided_by_sam3d",
             "observed": None, "visibility": None, "raw_x_px": 50., "raw_y_px": 25.,
             "x_px": 50., "y_px": 25., "x_normalized": .5, "y_normalized": .5, "in_frame": True}
    pose = {"source": "sam3d", "status": "ok", "confidence": None, "confidence_semantics": "not_provided_by_sam3d",
            "observation_semantics": "monocular_model_estimate", "coordinate_space": "original_frame",
            "person_track_id": 1, "keypoint_format": "mhr70", "source_binding": binding, "keypoints": [point]}
    return pose, frame


class SamFrameValidationTests(unittest.TestCase):
    def test_independently_retained_decoder_and_video_identity_must_match(self):
        for field, value in (("pts", 4), ("time_base_num", True), ("time_base_den", 60), ("video_sha256", "b" * 64)):
            pose, frame = bound_pose()
            frame[field] = value
            with self.subTest(field=field), self.assertRaises(OutputValidationError):
                _validate_sam_pose(pose, frame, "pose")
        pose, frame = bound_pose()
        frame.update(pts=3, time_base_num=1, time_base_den=30, video_sha256="A" * 64)
        _validate_sam_pose(pose, frame, "pose")

    def test_consumer_semantics_camera_epoch_and_rounded_pts_are_enforced(self):
        for mutate in (
            lambda pose, frame: pose.update(status="failed"),
            lambda pose, frame: pose["keypoints"][0].update(confidence_semantics="known_visibility"),
            lambda pose, frame: frame.update(camera_motion={"reference_epoch": 9}),
            lambda pose, frame: pose["source_binding"].update(pts=1008, time_base_den=10000),
        ):
            pose, frame = bound_pose()
            mutate(pose, frame)
            with self.assertRaises(OutputValidationError):
                _validate_sam_pose(pose, frame, "pose")

    def test_nonzero_source_pts_uses_absolute_binding_and_relative_playback(self):
        pose, frame = bound_pose()
        frame.update(timestamp_ms=0, source_timestamp_ms=100)
        _validate_sam_pose(pose, frame, "pose")
        self.assertEqual(frame["timestamp_ms"], 0)
        frame["source_timestamp_ms"] = 101
        with self.assertRaises(OutputValidationError):
            _validate_sam_pose(pose, frame, "pose")

    def test_actual_adapter_output_passes_frame_contract_without_synthetic_confidence(self):
        from rallymate_vision.sam3d_adapter import Sam3DPoseAdapter
        from rallymate_vision.pose.metadata import keypoint_schema

        class GeometryFixtureEngine:
            def infer(self, frame, boxes):
                # Deterministic transport/geometry fixture; not a model accuracy test.
                return [{"status": "ok", "keypoints_2d": [[50.123456, 25.123456] for _ in range(70)],
                         "keypoints_3d": [[0., 0., 1.] for _ in range(70)]} for _ in boxes]

        adapter = Sam3DPoseAdapter.__new__(Sam3DPoseAdapter)
        adapter.engine = GeometryFixtureEngine()
        adapter._last_seen, adapter._epochs = {}, {}
        adapter.reset_sequence()
        _, frame = bound_pose()
        binding = bound_pose()[0]["source_binding"]
        detection = {"track_id": 1, "class_name": "player", "bbox_px": [10., 5., 90., 45.],
                     "bbox_normalized": [.1, .1, .9, .9], "center_px": [50., 25.], "center_normalized": [.5, .5]}
        poses = adapter.estimate(np.zeros((50, 100, 3), dtype=np.uint8), [detection], 1, source_binding=binding)
        self.assertEqual(len(poses), 1)
        record = {"schema_version": "1.1.0", "frame": frame, "detections": [detection], "poses": poses, "court": {}}
        validate_frame_observation(record)
        self.assertEqual(poses[0]["keypoints"][0]["raw_x_px"], 50.123456)
        self.assertTrue(all(point["confidence"] is None for point in poses[0]["keypoints"]))
        self.assertEqual([point["name"] for point in poses[0]["keypoints"]], [point["name"] for point in keypoint_schema("mhr70")["keypoints"]])
        # Full frame validator must still reject confidence-less legacy output.
        poses[0]["keypoint_format"] = "coco17"
        poses[0]["source"] = "rtmpose"
        poses[0]["keypoints"] = [dict(point, **definition) for point, definition in zip(poses[0]["keypoints"], keypoint_schema("coco17")["keypoints"])]
        with self.assertRaises(OutputValidationError):
            validate_frame_observation(record)

    def test_model_estimate_keeps_missing_confidence(self):
        pose, frame = bound_pose()
        _validate_sam_pose(pose, frame, "pose")
        self.assertIsNone(pose["keypoints"][0]["confidence"])

    def test_missing_or_forged_semantics_rejected(self):
        for field, value in (("confidence", 1), ("source", "rtmpose"), ("confidence_semantics", "observed"),
                             ("observation_semantics", "observed"), ("coordinate_space", "camera_3d")):
            pose, frame = bound_pose()
            pose[field] = value
            with self.subTest(field=field), self.assertRaises(OutputValidationError):
                _validate_sam_pose(pose, frame, "pose")
        for field in ("observed", "visibility", "confidence"):
            pose, frame = bound_pose()
            del pose["keypoints"][0][field]
            with self.subTest(missing=field), self.assertRaises(OutputValidationError):
                _validate_sam_pose(pose, frame, "pose")

    def test_source_frame_person_and_pts_binding_required(self):
        for field in bound_pose()[0]["source_binding"]:
            pose, frame = bound_pose()
            del pose["source_binding"][field]
            with self.subTest(field=field), self.assertRaises(OutputValidationError):
                _validate_sam_pose(pose, frame, "pose")
        for field, value in (("frame_index", 4), ("processed_index", 1), ("person_track_id", 2), ("pts", 4),
                             ("timestamp_ms", 101), ("time_base_den", 0), ("selection_epoch", True), ("video_sha256", "bad")):
            pose, frame = bound_pose()
            pose["source_binding"][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(OutputValidationError):
                _validate_sam_pose(pose, frame, "pose")
        pose, frame = bound_pose()
        frame["timestamp_source"] = "frame_index_fps"
        with self.assertRaises(OutputValidationError):
            _validate_sam_pose(pose, frame, "pose")

    def test_out_of_frame_estimate_preserved_but_must_not_claim_in_frame(self):
        for raw, clipped in ((-5., 0.), (100., 100.), (125., 100.)):
            pose, frame = bound_pose()
            point = pose["keypoints"][0]
            point.update(raw_x_px=raw, x_px=clipped, x_normalized=clipped / 100, in_frame=False)
            _validate_sam_pose(pose, frame, "pose")
            point["in_frame"] = True
            with self.subTest(raw=raw), self.assertRaises(OutputValidationError):
                _validate_sam_pose(pose, frame, "pose")

    def test_raw_pixels_must_be_finite_and_match_compatibility_coordinates(self):
        for change in ({"raw_x_px": float("nan")}, {"raw_y_px": float("inf")}, {"raw_x_px": True},
                       {"x_px": 0}, {"x_normalized": .1}, {"in_frame": False}, {"confidence": .9}):
            pose, frame = bound_pose()
            pose["keypoints"][0].update(change)
            with self.subTest(change=change), self.assertRaises(OutputValidationError):
                _validate_sam_pose(pose, frame, "pose")

    def test_schema_null_confidence_exception_is_only_for_fully_bound_mhr70(self):
        schema = json.loads((Path(__file__).resolve().parents[1] / "contracts/frame-observation.schema.json").read_text())
        pose_schema = {**schema["properties"]["poses"]["items"], "$defs": schema["$defs"]}
        pose, _ = bound_pose()
        point = pose["keypoints"][0]
        pose["keypoints"] = [{**point, "index": index, "name": f"contract-fixture-{index}", "downstream_joint_id": None} for index in range(70)]
        jsonschema.validate(pose, pose_schema)
        for change in ({"keypoint_format": "halpe26"}, {"source": "rtmpose"}, {"confidence": 1}, {"source_binding": {}}):
            with self.subTest(change=change), self.assertRaises(jsonschema.ValidationError):
                jsonschema.validate({**pose, **change}, pose_schema)
        legacy = {**pose, "source": "rtmpose", "keypoint_format": "halpe26", "confidence": .9,
                  "keypoints": [{**point, "index": index, "name": "fixture", "downstream_joint_id": None, "confidence": .9} for index in range(26)]}
        jsonschema.validate(legacy, pose_schema)
        legacy["keypoints"][0]["confidence"] = None
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(legacy, pose_schema)


if __name__ == "__main__":
    unittest.main()
