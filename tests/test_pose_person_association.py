from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from rallymate_vision.pose.adapters import PoseEstimator
from rallymate_vision.pose.association import (
    DUPLICATE_JOINTS, MIN_IOU_MARGIN, MIN_TARGET_IOU, POLICY_VERSION,
    audit_pose_person_associations, person_box_ownership, select_target_pose_candidate,
)
from rallymate_vision.pose.base import PoseBackendOutput
from rallymate_vision.pose.yolo_backend import YoloPoseBackend
from tests.test_pose_backend import StubBackend


FIXTURE = Path(__file__).parent / "fixtures/pose-association-usta-11986-v1.json"


class Tensor:
    """Only the tensor operations used by the backend; no model inference."""
    def __init__(self, value): self.value = np.asarray(value)
    def __len__(self): return len(self.value)
    def __getitem__(self, index): return Tensor(self.value[index])
    def detach(self): return self
    def cpu(self): return self
    def numpy(self): return self.value
    def item(self): return self.value.item()


def result(boxes, confidences=None, xy=None, scores=None):
    n = len(boxes)
    return SimpleNamespace(
        boxes=SimpleNamespace(xyxy=Tensor(boxes), conf=Tensor(confidences if confidences is not None else [0.8] * n)),
        keypoints=SimpleNamespace(xy=Tensor(xy if xy is not None else [[[20 + j, 30 + j] for j in range(17)]] * n), conf=Tensor(scores if scores is not None else [[0.9] * 17] * n)),
    )


def backend(results):
    obj = object.__new__(YoloPoseBackend)
    obj.model = SimpleNamespace(predict=lambda rois, **kwargs: results)
    obj.input_size = 640
    obj.confidence = 0.25
    obj.device = "cpu"
    obj.metadata = StubBackend([]).metadata
    return obj


def body(track, offset=0):
    coordinates = [(30, 30), (70, 30), (35, 70), (65, 70), (30, 120), (70, 120)]
    return {"person_track_id": track, "coordinate_space": "original_frame", "keypoints": [{"name": name, "x_px": x + offset, "y_px": y, "confidence": .95} for name, (x, y) in zip(DUPLICATE_JOINTS, coordinates)]}


class CandidateAssociationTests(unittest.TestCase):
    def test_policy_constants_are_frozen(self):
        self.assertEqual((MIN_TARGET_IOU, MIN_IOU_MARGIN), (.5, .1))

    def test_legacy_metadata_has_no_new_empty_policy_field(self):
        self.assertNotIn("person_association_policy_version", StubBackend([]).metadata().to_dict())

    def test_finite_but_overflowing_geometry_cannot_become_an_iou_match(self):
        r = select_target_pose_candidate([[0, 0, 1e200, 1e200]], [0, 0, 1e200, 1e200], roi_size=(10 ** 201, 10 ** 201))
        self.assertIsNone(r["selected_index"])

    def test_geometry_wins_over_confidence(self):
        b = backend([result([[60, 10, 95, 90], [10, 10, 50, 90]], [.99, .4])])
        output = b.infer_targeted([np.zeros((100, 100, 3))], [[10, 10, 50, 90]])[0]
        self.assertEqual(output.person_bbox_xyxy, (10, 10, 50, 90))
        self.assertEqual(output.pose_score, .4)
        self.assertEqual(output.person_association["selected_index"], 1)

    def test_duplicate_candidate_is_ambiguous_not_confidence_tiebreak(self):
        b = backend([result([[10, 10, 50, 90], [11, 10, 51, 90]], [.99, .4])])
        self.assertIsNone(b.infer_targeted([np.zeros((100, 100, 3))], [[10, 10, 50, 90]])[0])
        self.assertEqual(b.last_association_diagnostics[0]["reason_code"], "pose_target_candidate_ambiguous")

    def test_no_target_multiple_rejected_single_compatible(self):
        b = backend([result([[10, 10, 50, 90], [60, 10, 95, 90]])])
        self.assertEqual(b.infer([np.zeros((100, 100, 3))]), [None])
        b.model = SimpleNamespace(predict=lambda *a, **k: [result([[10, 10, 50, 90]])])
        self.assertIsNotNone(b.infer([np.zeros((100, 100, 3))])[0])

    def test_invalid_candidate_or_target_never_clipped_to_valid(self):
        for box in ([0, 0, 0, 90], [-1, 0, 30, 90], [0, 0, 101, 90], [0, 0, float("nan"), 90], [0, 0, float("inf"), 90], [0, 0, 10 ** 400, 90]):
            with self.subTest(box=box):
                self.assertIsNone(select_target_pose_candidate([box], [10, 10, 50, 90], roi_size=(100, 100))["selected_index"])
                self.assertIsNone(select_target_pose_candidate([[10, 10, 50, 90]], box, roi_size=(100, 100))["selected_index"])

    def test_below_iou_does_not_fall_back_to_confidence(self):
        r = select_target_pose_candidate([[60, 10, 95, 90]], [10, 10, 50, 90], roi_size=(100, 100))
        self.assertEqual(r["reason_code"], "pose_target_iou_below_minimum")

    def test_backend_requires_exact_result_and_target_counts(self):
        b = backend([])
        with self.assertRaisesRegex(ValueError, "one result per ROI"):
            b.infer([np.zeros((100, 100, 3))])
        with self.assertRaisesRegex(ValueError, "one entry per ROI"):
            b.infer_targeted([np.zeros((100, 100, 3))], [])

    def test_missing_or_misaligned_confidence_does_not_create_ones(self):
        r = result([[10, 10, 50, 90]])
        r.keypoints.conf = None
        b = backend([r])
        self.assertEqual(b.infer([np.zeros((100, 100, 3))]), [None])
        self.assertEqual(b.last_association_diagnostics[0]["reason_code"], "pose_candidate_keypoint_confidence_missing")
        r.keypoints.conf = Tensor([[.8] * 17] * 2)
        self.assertEqual(b.infer([np.zeros((100, 100, 3))]), [None])
        self.assertEqual(b.last_association_diagnostics[0]["reason_code"], "pose_candidate_arrays_misaligned")

    def test_adapter_passes_original_target_in_correct_roi_coordinates(self):
        class Targeted(StubBackend):
            def infer_targeted(self, rois, target_boxes_xyxy, *, timestamps_ms=None):
                self.targets = target_boxes_xyxy
                return [PoseBackendOutput(np.full((17, 2), 20.), np.ones(17), .8, tuple(target_boxes_xyxy[0]))]
        b = Targeted([])
        e = PoseEstimator(b)
        out = e.estimate(np.zeros((120, 200, 3)), [{"track_id": 9, "bbox_px": [50, 20, 150, 100]}], 1)
        self.assertEqual(b.targets, [[15, 12, 115, 92]])
        self.assertEqual(out[0]["person_association"]["target_box_px"], [50, 20, 150, 100])

    def test_adapter_checks_nonprocessed_competing_player(self):
        class Targeted(StubBackend):
            def infer_targeted(self, rois, target_boxes_xyxy, **kwargs):
                return [PoseBackendOutput(np.full((17, 2), 20.), np.ones(17), .9, (25, 10, 75, 90))]
        e = PoseEstimator(Targeted([]), roi_margin=0)
        # Player 2 is not processed at max_players=1 but owns this candidate box.
        out = e.estimate(np.zeros((150, 150, 3)), [{"track_id": 1, "bbox_px": [20, 20, 120, 140]}, {"track_id": 2, "bbox_px": [45, 30, 95, 110]}], 1)
        self.assertEqual(out, [])
        self.assertEqual(e.last_association_diagnostics["rois"][0]["reason_code"], "pose_candidate_wrong_or_ambiguous_person")

    def test_adapter_invalid_or_duplicate_target_mapping_fails(self):
        e = PoseEstimator(StubBackend([]))
        for players in ([{"track_id": True, "bbox_px": [0, 0, 100, 100]}], [{"track_id": -1, "bbox_px": [0, 0, 100, 100]}], [{"track_id": 1, "bbox_px": [-1, 0, 100, 100]}], [{"track_id": 1, "bbox_px": [0, 0, 90, 100]}] * 2):
            with self.subTest(players=players), self.assertRaises(ValueError):
                e.estimate(np.zeros((100, 100, 3)), players, 2)


class DuplicateBodyTests(unittest.TestCase):
    def test_negative_track_id_and_unbounded_json_integer_are_not_valid_proof(self):
        self.assertEqual(audit_pose_person_associations([body(-1), body(2)], [], frame_size=(150, 150))["conflicts"], [])
        self.assertIsNone(person_box_ownership([25, 25, 75, 125], [{"track_id": -1, "bbox_px": [25, 25, 75, 125]}], frame_size=(150, 150))["preferred_person_track_id"])
        p = body(2); p["keypoints"][0]["x_px"] = 10 ** 400
        self.assertEqual(audit_pose_person_associations([body(1), p], [], frame_size=(150, 150))["conflicts"], [])

    def test_duplicate_with_unique_bbox_owner_rejects_wrong_assignment(self):
        a = audit_pose_person_associations([body(1), body(2, .5)], [{"track_id": 1, "bbox_px": [25, 25, 75, 125]}, {"track_id": 2, "bbox_px": [5, 5, 120, 145]}], frame_size=(150, 150))
        self.assertEqual(a["rejected_person_track_ids"], [2])
        self.assertEqual(a["conflicts"][0]["preferred_person_track_id"], 1)

    def test_ambiguous_duplicate_rejects_both_and_order_does_not_matter(self):
        players = [{"track_id": 1, "bbox_px": [25, 25, 75, 125]}, {"track_id": 2, "bbox_px": [25, 25, 75, 125]}]
        for poses in ([body(1), body(2)], [body(2), body(1)]):
            self.assertEqual(audit_pose_person_associations(poses, players, frame_size=(150, 150))["rejected_person_track_ids"], [1, 2])

    def test_missing_low_confidence_or_distinct_sixth_joint_is_not_duplicate_proof(self):
        for mutation in ("missing", "low", "different"):
            p = body(2)
            if mutation == "missing": p["keypoints"].pop()
            elif mutation == "low": p["keypoints"][-1]["confidence"] = .34999
            else: p["keypoints"][-1]["x_px"] += 20
            self.assertEqual(audit_pose_person_associations([body(1), p], [], frame_size=(150, 150))["rejected_person_track_ids"], [])

    def test_missing_size_and_non_original_coordinates_have_no_verdict(self):
        a = audit_pose_person_associations([body(1), body(2)], [])
        self.assertEqual(a["status"], "unavailable")
        self.assertEqual(a["rejected_person_track_ids"], [])
        p = body(2); p["coordinate_space"] = "normalized"
        a = audit_pose_person_associations([body(1), p], [], frame_size=(150, 150))
        self.assertEqual(a["conflicts"], [])
        self.assertEqual(a["unassessed_person_track_ids"], [2])

    def test_no_velocity_or_timestamp_is_used_as_identity_proof(self):
        p = body(2, 70)
        p.update(timestamp_ms=-99, velocity_torso_s=9999)
        self.assertEqual(audit_pose_person_associations([body(1), p], [], frame_size=(200, 150))["conflicts"], [])

    def test_adapter_rejects_cross_roi_duplicate_for_legacy_backend(self):
        f = json.loads(FIXTURE.read_text(encoding="utf-8"))["frames"][1]
        outputs = []
        for r in f["roi_results"]:
            c = r["candidates"][r["argmax_box_confidence_index"]]
            outputs.append(PoseBackendOutput(np.asarray(c["keypoints_roi_px"]), np.asarray(c["keypoint_confidences"]), c["box_confidence"]))
        e = PoseEstimator(StubBackend(outputs))
        out = e.estimate(np.zeros((720, 1280, 3)), f["players"], 2)
        self.assertEqual([p["person_track_id"] for p in out], [1])
        self.assertEqual(e.last_association_diagnostics["duplicate_audit"]["rejected_person_track_ids"], [2])


class SavedRealUstaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_raw_pose_and_primary_remain_original_and_pts_bound(self):
        for row in self.fixture["frames"]:
            self.assertEqual(row["poses"], row["raw_record"]["poses"])
            self.assertEqual(row["primary_record"]["source_track_id"], 2)
            self.assertEqual(row["primary_record"]["source_frame_index"], row["frame_index"])
            self.assertEqual(row["actual_decoder_binding"]["frame_index"], row["frame_index"])
            self.assertEqual(row["actual_decoder_binding"]["video_sha256"], self.fixture["video_sha256"])
            for key in ("raw_line_sha256", "primary_line_sha256"):
                self.assertEqual(len(row[key]), 64)
        self.assertEqual([r["frame_index"] for r in self.fixture["frames"]], [11985, 11986, 11987])

    def test_real_raw_wrong_person_conflict_is_local_to_11986(self):
        audits = [audit_pose_person_associations(r["poses"], r["players"], frame_size=tuple(r["frame_size"])) for r in self.fixture["frames"]]
        self.assertEqual([a["rejected_person_track_ids"] for a in audits], [[], [2], []])
        conflict = audits[1]["conflicts"][0]
        self.assertEqual(conflict["preferred_person_track_id"], 1)
        self.assertTrue(all(v <= conflict["tolerance_px"] for v in conflict["per_joint_distance_px"].values()))
        self.assertEqual(audits[2]["unassessed_person_track_ids"], [1])

    def test_saved_actual_six_roi_candidates_select_five_reject_one(self):
        for row in self.fixture["frames"]:
            results = [result([c["bbox_roi_px"] for c in r["candidates"]], [c["box_confidence"] for c in r["candidates"]], [c["keypoints_roi_px"] for c in r["candidates"]], [c["keypoint_confidences"] for c in r["candidates"]]) for r in row["roi_results"]]
            e = PoseEstimator(backend(results))
            before = copy.deepcopy(row)
            poses = e.estimate(np.zeros((720, 1280, 3)), row["players"], 2)
            self.assertEqual([p["person_track_id"] for p in poses], [1] if row["frame_index"] == 11986 else [2, 1])
            for pose in poses:
                original = next(p for p in row["poses"] if p["person_track_id"] == pose["person_track_id"])
                self.assertEqual({k: v for k, v in pose.items() if k != "person_association"}, original)
            self.assertEqual(row, before)
            if row["frame_index"] == 11986:
                d = e.last_association_diagnostics["rois"][0]
                self.assertEqual(d["reason_code"], "pose_target_candidate_ambiguous")
                self.assertAlmostEqual(d["backend"]["iou_margin"], .025940123796769354)


if __name__ == "__main__": unittest.main()
