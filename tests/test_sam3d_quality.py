"""Quality rejection is auditable and cannot reach scoring or mesh replay."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from rallymate_vision.sam3d_quality import Sam3DPoseQualityGate, CORE
from rallymate_vision.sam3d_adapter import Sam3DPoseAdapter
from rallymate_vision.sam3d_mesh import archive_mesh_records, capture_mesh_output
from rallymate_vision.pose.metadata import MHR70_NAMES


def native(index=0):
    xy = [[50., 15.], [40., 30.], [60., 30.], [44., 55.], [56., 55.],
          [43., 70.], [57., 70.], [42., 90.], [58., 90.]]
    return {"video_sha256": "a" * 64, "frame_index": index, "processed_index": index,
            "person_track_id": 1, "selection_epoch": 0, "camera_reference_epoch": 0,
            "timestamp_ms": round(index * 1000 / 30), "pts": index,
            "time_base_num": 1, "time_base_den": 30,
            "bbox_px": [20., 10., 80., 95.], "image_size": [100, 100],
            "keypoints_2d": dict(zip(CORE, xy))}


class PoseQualityTests(unittest.TestCase):
    def test_real_regression_preserves_partial_far_player_and_rejects_inverted_background(self):
        fixture = json.loads((Path(__file__).parent / "fixtures/false-person-regression.json").read_text())
        rows = fixture["native_records"]
        for row in rows:
            # These examples are discontinuous; do not invent temporal adjacency.
            result = Sam3DPoseQualityGate().evaluate(row)
            if row["frame_index"] in (206, 942, 1402):
                self.assertFalse(result["accepted"], row["frame_index"])
                self.assertIn("inverted_body_projection", result["reasons"])
            elif row["frame_index"] in (0, 205, 208, 214, 217, 219, 277, 742):
                self.assertTrue(result["accepted"], (row["frame_index"], result))

    def test_upright_horizontal_lunge_and_offscreen_feet_are_not_inversions(self):
        record = native()
        original = deepcopy(record)
        self.assertTrue(Sam3DPoseQualityGate().evaluate(record)["accepted"])
        self.assertEqual(record, original)
        for name in ("left_shoulder", "right_shoulder"):
            record["keypoints_2d"][name][1] = 55.
        self.assertTrue(Sam3DPoseQualityGate().evaluate(record)["accepted"])
        record = native()
        record["bbox_px"][3] = 100.
        for name in ("left_ankle", "right_ankle"):
            record["keypoints_2d"][name][1] = 140.
        self.assertTrue(Sam3DPoseQualityGate().evaluate(record)["accepted"])

    def test_detects_gross_projection_offset_without_using_hands(self):
        record = native()
        record["keypoints_2d"]["left_wrist"] = [-500., -500.]
        self.assertTrue(Sam3DPoseQualityGate().evaluate(record)["accepted"])
        for name in CORE:
            record["keypoints_2d"][name][0] += 150.
        result = Sam3DPoseQualityGate().evaluate(record)
        self.assertIn("pose_projection_outside_person_bbox", result["reasons"])

    def test_temporal_state_is_bounded_by_track_video_camera_and_selection_epoch(self):
        first = native()
        first["bbox_px"] = [0., 0., 100., 100.]
        for name in CORE:
            first["keypoints_2d"][name] = [5., 5.]
        second = deepcopy(first)
        second["timestamp_ms"] = 33
        for name in CORE:
            second["keypoints_2d"][name] = [95., 95.]
        gate = Sam3DPoseQualityGate()
        self.assertTrue(gate.evaluate(first)["accepted"])
        self.assertIn("pose_temporal_projection_jump", gate.evaluate(second)["reasons"])
        for field, value in (("person_track_id", 2), ("video_sha256", "b" * 64),
                             ("camera_reference_epoch", 1), ("selection_epoch", 1),
                             ("timestamp_ms", 300)):
            row = {**second, field: value}
            self.assertTrue(gate.evaluate(row)["accepted"], field)

    def test_invalid_geometry_fails_closed(self):
        for change in ({"bbox_px": [0, 0, 0, 0]}, {"image_size": [100, 0]}, {"keypoints_2d": {}}):
            self.assertFalse(Sam3DPoseQualityGate().evaluate({**native(), **change})["accepted"])

    def test_rejected_adapter_output_preserves_native_points_but_not_scored_pose_or_mesh(self):
        fixture = json.loads((Path(__file__).parent / "fixtures/false-person-regression.json").read_text())
        bad = next(r for r in fixture["native_records"] if r["frame_index"] == 206)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = capture_mesh_output({
                "pred_vertices": np.array([[0., 0., 0.], [1., 0., 0.], [0., 1., 0.]], np.float32),
                "pred_cam_t": np.array([0., 0., 3.]), "focal_length": 100.,
            }, np.array([[0, 1, 2]], np.int32), root / "runtime")
            record = {key: value for key, value in bad.items() if key != "keypoints_2d"}
            output = {"status": "ok", "keypoints_2d": [bad["keypoints_2d"][k] for k in MHR70_NAMES],
                      "keypoints_3d": [[0., 0., 1.] for _ in MHR70_NAMES], "mesh_artifact": artifact}
            prepared = {"width": 1280, "height": 720, "quality_gate": Sam3DPoseQualityGate(),
                        "accepted": [(record, {"person_track_id": bad["person_track_id"]})]}
            self.assertEqual(Sam3DPoseAdapter._decode_frame_outputs(prepared, [output]), [])
            self.assertEqual(record["status"], "skipped")
            self.assertEqual(record["keypoints_2d"], bad["keypoints_2d"])
            self.assertEqual(archive_mesh_records([record], root / "job", root / "runtime"), [])
            self.assertFalse(Path(artifact["path"]).exists())
            self.assertTrue(Path(artifact["faces_path"]).exists())
            self.assertNotIn("mesh_artifact", record)

    def test_real_rejection_has_identical_ordered_single_and_batch_results(self):
        from tests.test_sam3d_adapter_batch import adapter
        fixture = json.loads((Path(__file__).parent / "fixtures/false-person-regression.json").read_text())
        rows = [next(r for r in fixture["native_records"] if r["frame_index"] == i) for i in (205, 206, 208)]

        class Engine:
            def infer(self, frame, boxes):
                row = rows[int(frame[0, 0, 0])]
                return [{"status": "ok", "keypoints_2d": [row["keypoints_2d"][k] for k in MHR70_NAMES],
                         "keypoints_3d": [[0., 0., 1.] for _ in MHR70_NAMES]}]

            def infer_batch(self, requests):
                return [self.infer(r["frame"], r["boxes"]) for r in requests]

        requests = [{"frame": np.full((720, 1280, 3), i, np.uint8),
                     "player_detections": [{"track_id": row["person_track_id"], "bbox_px": row["bbox_px"]}],
                     "max_players": 1, "source_binding": {
                         "video_sha256": "a" * 64, "frame_index": row["frame_index"],
                         "processed_index": i, "camera_reference_epoch": 0,
                         "timestamp_ms": round(row["frame_index"] * 1000 / 30),
                         "pts": row["frame_index"], "time_base_num": 1, "time_base_den": 30}}
                    for i, row in enumerate(rows)]
        single = adapter(Engine())
        expected = []
        for request in requests:
            poses = single.estimate(request["frame"], request["player_detections"], 1,
                                    source_binding=request["source_binding"])
            expected.append({"poses": poses, "records": deepcopy(single.last_records)})
        actual = adapter(Engine()).estimate_batch(requests)
        self.assertEqual(actual, expected)
        self.assertEqual([len(item["poses"]) for item in actual], [1, 0, 1])


if __name__ == "__main__":
    unittest.main()
