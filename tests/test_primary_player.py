from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

try:
    from jsonschema import Draft202012Validator
except ModuleNotFoundError:  # Runtime selection does not depend on jsonschema.
    Draft202012Validator = None

from rallymate_tracking.primary_player import (
    KEYPOINT_CONFIDENCE_MIN,
    build_primary_player_artifacts,
    diagnose_primary_timeline,
    primary_timeline_algorithm_version,
    registry_required_primary_player_version,
    select_primary_player_timeline,
)
from rallymate_features.coordinates import point_series, pose_sequence_from_records
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


def _sam_record(index: int) -> dict:
    """Constructed transport fixture; no model inference or accuracy claim."""
    record = _record(index, [(1, [30, 15, 100, 115], True)])
    record["frame"].update(timestamp_source="decoder_pts", source_timestamp_ms=index * 40,
                           pts=index * 24, time_base_num=1, time_base_den=600, video_sha256="a" * 64)
    record["camera_motion"] = {"reference_epoch": 0}
    record["sam3d_requests"] = [{"person_track_id": 1, "selection_epoch": 0, "status": "ok"}]
    binding = {"video_sha256": "a" * 64, "frame_index": index, "processed_index": index,
               "pts": index * 24, "time_base_num": 1, "time_base_den": 600,
               "timestamp_ms": index * 40, "person_track_id": 1,
               "selection_epoch": 0, "camera_reference_epoch": 0}
    points = [{**definition, "x_px": 50., "y_px": 25., "raw_x_px": 50., "raw_y_px": 25.,
               "x_normalized": .25, "y_normalized": 25 / 120, "in_frame": True,
               "confidence": None, "confidence_semantics": "not_provided_by_sam3d",
               "source": "sam3d", "observation_semantics": "model_estimate",
               "observed": None, "visibility": None}
              for definition in keypoint_schema("mhr70")["keypoints"]]
    record["poses"] = [{"person_track_id": 1, "source": "sam3d", "status": "ok",
                         "keypoint_format": "mhr70", "coordinate_space": "original_frame",
                         "confidence": None, "confidence_semantics": "not_provided_by_sam3d",
                         "observation_semantics": "monocular_model_estimate",
                         "source_binding": binding, "keypoints": points}]
    return record


class SamPrimaryGeometryTests(unittest.TestCase):
    def test_full_artifact_writer_serializes_model_and_detector_metadata(self):
        for model in (True, False):
            with self.subTest(model=model), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                records = ([_sam_record(index) for index in range(3)] if model else
                           [_record(index, [(1, [30, 15, 100, 115], True)]) for index in range(3)])
                frames, timeline, summary = (root / name for name in ("frames.jsonl", "primary-player.jsonl", "primary-player-summary.json"))
                frames.write_text("".join(json.dumps(row) + "\n" for row in records), encoding="utf-8")
                before = frames.read_bytes()
                result = build_primary_player_artifacts(frames, timeline, summary)
                saved = json.loads(summary.read_text(encoding="utf-8"))
                self.assertEqual(saved, result)
                self.assertEqual(len([json.loads(line) for line in timeline.read_text(encoding="utf-8").splitlines()]), 3)
                prior = saved["track_priors"]["1"]
                expected = "model_geometry_coverage" if model else "confidence_qualified_target_joint_coverage"
                self.assertEqual(prior["pose_selection_semantics"], [expected])
                self.assertEqual(prior["mean_pose_selection_fraction"], 1.)
                self.assertEqual(prior["mean_pose_valid_fraction"], 0. if model else 1.)
                self.assertEqual(frames.read_bytes(), before)
                if Draft202012Validator is not None:
                    schema = json.loads((Path(__file__).parents[1] / "contracts/primary-player.schema.json").read_text(encoding="utf-8"))
                    for line in timeline.read_text(encoding="utf-8").splitlines():
                        Draft202012Validator(schema).validate(json.loads(line))

    @staticmethod
    def _diagnose(records, timeline=None):
        if timeline is None:
            timeline = select_primary_player_timeline(records)["timeline"]
        return diagnose_primary_timeline(records, timeline)

    def test_sam_geometry_selection_support_is_separate_from_unchanged_confidence(self):
        records = [_sam_record(index) for index in range(3)]
        original = copy.deepcopy(records)
        selected = select_primary_player_timeline(records)
        diagnostics = self._diagnose(records, selected["timeline"])
        geometry = diagnostics["model_estimate_geometry"]
        self.assertEqual(diagnostics["keypoint_valid_fraction"], 0.0)
        self.assertEqual(geometry["geometry_coverage_fraction"], 1.0)
        self.assertEqual(geometry["target_joint_geometry_fraction"], 1.0)
        self.assertEqual(geometry["in_frame_joint_count"], 51)
        self.assertFalse(geometry["confidence_available"])
        self.assertFalse(geometry["visibility_verified"])
        self.assertFalse(geometry["accuracy_validated"])
        self.assertFalse(geometry["scoring_allowed"])
        self.assertTrue(geometry["used_for_primary_selection"])
        self.assertEqual(selected["timeline"][0]["keypoint_valid_fraction"], 0.)
        self.assertEqual(selected["timeline"][0]["model_geometry_coverage"], 1.)
        self.assertEqual(selected["timeline"][0]["pose_selection_semantics"], "model_geometry_coverage")
        self.assertEqual(selected["track_priors"][1]["mean_pose_valid_fraction"], 0.)
        self.assertEqual(selected["track_priors"][1]["mean_pose_selection_fraction"], 1.)
        self.assertEqual(select_primary_player_timeline(records), selected)
        self.assertEqual(records, original)
        self.assertTrue(all(point["confidence"] is None for row in records for point in row["poses"][0]["keypoints"]))
        self.assertEqual(geometry["frame_evidence"][1]["source_binding"], records[1]["poses"][0]["source_binding"])

    def test_geometry_support_stays_distinct_without_replacing_a_separate_identity_anchor(self):
        records = [_sam_record(index) for index in range(4)]
        for record in records:
            other = copy.deepcopy(record["poses"][0])
            other["person_track_id"] = 2
            other["source_binding"]["person_track_id"] = 2
            # A larger detected person has no in-frame model geometry. This is
            # a transport/selection regression, not a claim of identity accuracy.
            for point in other["keypoints"]:
                point.update(raw_x_px=-1., x_px=0., x_normalized=0., in_frame=False)
            record["poses"].append(other)
            record["sam3d_requests"].append({"person_track_id": 2, "selection_epoch": 0, "status": "ok"})
            record["detections"].append({"class_name": "player", "track_id": 2,
                                         "bbox_px": [0, 0, 150, 120], "confidence": .9})
        before = copy.deepcopy(records)
        selected = select_primary_player_timeline(records)
        self.assertEqual({row["source_track_id"] for row in selected["timeline"]}, {2})
        self.assertTrue(all(row["selection_identity_guard"] == "retained_identity_anchor" for row in selected["timeline"]))
        self.assertEqual(selected["track_priors"][2]["mean_pose_selection_fraction"], 0.)
        self.assertEqual(selected["track_priors"][1]["mean_pose_selection_fraction"], 1.)
        self.assertEqual(records, before)
        # Removing independent provenance removes the geometry preference.
        for record in records:
            record["sam3d_requests"] = []
        missing = select_primary_player_timeline(records)
        self.assertEqual({row["source_track_id"] for row in missing["timeline"]}, {2})
        self.assertTrue(all(row["model_geometry_coverage"] is None for row in missing["timeline"]))

    def test_short_target_dropout_is_missing_instead_of_borrowing_a_spectator(self):
        records = [_sam_record(index) for index in range(3)]
        middle = records[1]
        middle["detections"][0].update(track_id=2, bbox_px=[120, 5, 135, 30])
        middle["poses"][0]["person_track_id"] = 2
        middle["poses"][0]["source_binding"]["person_track_id"] = 2
        middle["sam3d_requests"][0]["person_track_id"] = 2
        original = copy.deepcopy(records)
        timeline = select_primary_player_timeline(records)["timeline"]
        self.assertEqual([row["source_track_id"] for row in timeline], [1, None, 1])
        self.assertEqual(timeline[1]["selection_status"], "missing")
        self.assertEqual(timeline[1]["selection_reason_code"], "short_target_detection_dropout")
        self.assertIsNone(timeline[1]["bbox_px"])
        self.assertFalse(timeline[1]["pose_present"])
        self.assertEqual(records, original)
        # A changed camera reference does not justify maintaining subject identity.
        records[2]["camera_motion"]["reference_epoch"] = 1
        records[2]["poses"][0]["source_binding"]["camera_reference_epoch"] = 1
        self.assertIsNone(select_primary_player_timeline(records)["timeline"][1]["source_track_id"])

    def test_geometry_may_compare_only_an_overlapping_neighbor_anchor_tracklet(self):
        from rallymate_tracking.primary_player import _frame_candidates, _guard_model_identity
        records = [_sam_record(index) for index in range(3)]
        for record in records:
            other = copy.deepcopy(record["poses"][0])
            other["person_track_id"] = 2
            other["source_binding"]["person_track_id"] = 2
            record["poses"].append(other)
            record["sam3d_requests"].append({"person_track_id": 2, "selection_epoch": 0, "status": "ok"})
            record["detections"].append({"class_name": "player", "track_id": 2,
                                         "bbox_px": [32, 15, 102, 115], "confidence": .9})
        frames = [{"timestamp_ms": index * 40, "candidates": _frame_candidates(record)} for index, record in enumerate(records)]
        anchors = [{"candidate": frames[i]["candidates"][track]} for i, track in enumerate([0, 0, 1])]
        proposed = [{"candidate": frames[i]["candidates"][track]} for i, track in enumerate([0, 1, 1])]
        accepted = _guard_model_identity(frames, proposed, anchors)
        self.assertEqual(accepted[1]["identity_guard"], "overlapping_anchor_tracklet")
        self.assertEqual(accepted[1]["candidate"]["source_track_id"], 2)
        frames[1]["candidates"][1]["bbox_px"] = [120, 5, 135, 30]
        rejected = _guard_model_identity(frames, proposed, anchors)
        self.assertEqual(rejected[1]["candidate"]["source_track_id"], 1)
        self.assertEqual(rejected[1]["identity_guard"], "retained_identity_anchor")

    def test_model_selection_coverage_uses_original_in_frame_target_points_only(self):
        record = _sam_record(0)
        record["poses"][0]["keypoints"][13].update(raw_x_px=-10, x_px=0, x_normalized=0, in_frame=True)
        row = select_primary_player_timeline([record])["timeline"][0]
        self.assertEqual(row["model_geometry_coverage"], 7 / 8)
        self.assertEqual(row["pose_selection_fraction"], 7 / 8)
        self.assertEqual(row["keypoint_valid_fraction"], 0.)

    def test_model_selection_rejects_unverified_provenance_and_duplicate_sources(self):
        changes = [
            lambda r: r["frame"].update(video_sha256="b" * 64),
            lambda r: r["frame"].update(pts=10),
            lambda r: r["frame"].update(time_base_den=30),
            lambda r: r["camera_motion"].update(reference_epoch=1),
            lambda r: r["sam3d_requests"][0].update(selection_epoch=1),
            lambda r: r["sam3d_requests"][0].update(status="failed"),
            lambda r: r["poses"][0]["keypoints"][5].update(confidence=1.),
            lambda r: r["poses"].append(copy.deepcopy(r["poses"][0])),
            lambda r: r["poses"].append(_pose(1)),
        ]
        for mutate in changes:
            record = _sam_record(0)
            mutate(record)
            row = select_primary_player_timeline([record])["timeline"][0]
            self.assertIsNone(row["model_geometry_coverage"])
            self.assertEqual(row["pose_selection_fraction"], 0.)
            self.assertTrue(row["model_geometry_reason"])
        records = [_sam_record(0), _sam_record(0)]
        records[1]["frame"]["processed_index"] = 1
        records[1]["poses"][0]["source_binding"]["processed_index"] = 1
        self.assertTrue(all(row["model_geometry_reason"] == "duplicate_source_frame"
                            for row in select_primary_player_timeline(records)["timeline"]))
        record = _sam_record(0)
        record["poses"] = []
        row = select_primary_player_timeline([record])["timeline"][0]
        self.assertEqual(row["pose_selection_semantics"], "model_geometry_coverage")
        self.assertIsNone(row["model_geometry_coverage"])
        self.assertEqual(row["model_geometry_reason"], "selected_pose_missing_or_conflicting")
        record = _sam_record(0)
        for point in record["poses"][0]["keypoints"]:
            point["confidence"] = 1.
        row = select_primary_player_timeline([record])["timeline"][0]
        self.assertEqual(row["keypoint_valid_fraction"], 0.)
        self.assertEqual(row["pose_selection_fraction"], 0.)
        self.assertIsNone(row["model_geometry_coverage"])

    def test_missing_invalid_and_off_image_points_stay_in_denominator(self):
        records = [_sam_record(index) for index in range(3)]
        point = records[1]["poses"][0]["keypoints"][5]  # left shoulder
        point.update(raw_x_px=-1., x_px=0., x_normalized=0., in_frame=False)
        records[2]["poses"] = []
        geometry = self._diagnose(records)["model_estimate_geometry"]
        self.assertEqual(geometry["status"], "partial")
        self.assertEqual(geometry["interval_frame_count"], 3)
        self.assertEqual(geometry["source_bound_frame_count"], 2)
        self.assertEqual(geometry["in_frame_joint_count"], 33)
        self.assertAlmostEqual(geometry["geometry_coverage_fraction"], 33 / 51, places=6)
        self.assertEqual(geometry["target_joint_geometry_fraction"], 15 / 24)
        self.assertEqual(geometry["joint_coverage"]["left_shoulder"]["in_frame_count"], 1)
        self.assertEqual(geometry["reason_counts"]["selected_pose_missing_or_conflicting"], 1)

    def test_source_decoder_camera_and_joint_contract_mismatches_fail_closed(self):
        mutations = {
            "frame_video": lambda r: r["frame"].update(video_sha256="b" * 64),
            "bound_video": lambda r: r["poses"][0]["source_binding"].update(video_sha256="b" * 64),
            "missing_video": lambda r: r["frame"].pop("video_sha256"),
            "frame_pts": lambda r: r["frame"].update(pts=99),
            "frame_timebase": lambda r: r["frame"].update(time_base_den=30),
            "frame_boolean_pts": lambda r: r["frame"].update(pts=False),
            "camera_epoch": lambda r: r["camera_motion"].update(reference_epoch=2),
            "missing_camera": lambda r: r.pop("camera_motion"),
            "non_decoder_clock": lambda r: r["frame"].update(timestamp_source="fps_fallback"),
            "forged_joint_confidence": lambda r: r["poses"][0]["keypoints"][5].update(confidence=1.),
            "duplicate_pose": lambda r: r["poses"].append(copy.deepcopy(r["poses"][0])),
            "missing_detection": lambda r: r.update(detections=[]),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                records = [_sam_record(0)]
                timeline = select_primary_player_timeline(records)["timeline"]
                mutate(records[0])
                geometry = self._diagnose(records, timeline)["model_estimate_geometry"]
                self.assertEqual(geometry["source_bound_frame_count"], 0)
                self.assertEqual(geometry["geometry_coverage_fraction"], 0.)
                self.assertEqual(sum(geometry["reason_counts"].values()), 1)

    def test_ambiguous_or_mismatched_primary_timeline_cannot_count_geometry(self):
        for change in ({"identity_ambiguous": True}, {"selection_status": "missing"},
                       {"source_frame_index": 42}, {"timestamp_ms": 100}):
            with self.subTest(change=change):
                records = [_sam_record(0)]
                timeline = select_primary_player_timeline(records)["timeline"]
                timeline[0].update(change)
                geometry = self._diagnose(records, timeline)["model_estimate_geometry"]
                self.assertEqual(geometry["source_bound_frame_count"], 0)

    def test_nonzero_original_pts_and_unmapped_mesh_points_remain_distinct(self):
        records = [_sam_record(0)]
        records[0]["frame"].update(pts=60, source_timestamp_ms=100)
        records[0]["poses"][0]["source_binding"].update(pts=60, timestamp_ms=100)
        geometry = self._diagnose(records)["model_estimate_geometry"]
        self.assertEqual(geometry["geometry_coverage_fraction"], 1.)
        self.assertEqual(geometry["mapped_joint_count"], 17)
        self.assertNotIn("left_big_toe_tip", geometry["joint_coverage"])
        self.assertEqual(geometry["frame_evidence"][0]["timestamp_ms"], 0)
        self.assertEqual(geometry["frame_evidence"][0]["source_binding"]["timestamp_ms"], 100)

    def test_legacy_observations_have_no_sam_coverage_fraction(self):
        records = [_record(0, [(1, [30, 15, 100, 115], True)])]
        diagnostics = self._diagnose(records)
        self.assertEqual(diagnostics["keypoint_valid_fraction"], 1.)
        geometry = diagnostics["model_estimate_geometry"]
        self.assertEqual(geometry["status"], "not_applicable")
        self.assertIsNone(geometry["geometry_coverage_fraction"])
        self.assertEqual(geometry["frame_evidence"], [])

    def test_duplicate_source_frames_do_not_inflate_geometry_evidence(self):
        records = [_sam_record(0), _sam_record(0)]
        records[1]["frame"]["processed_index"] = 1
        records[1]["poses"][0]["source_binding"]["processed_index"] = 1
        geometry = self._diagnose(records)["model_estimate_geometry"]
        self.assertEqual(geometry["source_bound_frame_count"], 0)
        self.assertEqual(geometry["reason_counts"], {"duplicate_source_frame": 2})

    def test_selection_epoch_requires_independent_successful_request_ledger(self):
        mutations = {
            "forged_pose_epoch": lambda r: r["poses"][0]["source_binding"].update(selection_epoch=999),
            "missing_ledger": lambda r: r.pop("sam3d_requests"),
            "wrong_request_track": lambda r: r["sam3d_requests"][0].update(person_track_id=2),
            "boolean_request_epoch": lambda r: r["sam3d_requests"][0].update(selection_epoch=False),
            "failed_request": lambda r: r["sam3d_requests"][0].update(status="failed"),
            "wrong_optional_request_frame": lambda r: r["sam3d_requests"][0].update(frame_index=1),
            "boolean_optional_request_pts": lambda r: r["sam3d_requests"][0].update(pts=False),
            "wrong_optional_request_camera": lambda r: r["sam3d_requests"][0].update(camera_reference_epoch=9),
            "duplicate_request": lambda r: r["sam3d_requests"].append(copy.deepcopy(r["sam3d_requests"][0])),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                record = _sam_record(0)
                mutate(record)
                geometry = self._diagnose([record])["model_estimate_geometry"]
                self.assertEqual(geometry["source_bound_frame_count"], 0)
                self.assertEqual(geometry["geometry_coverage_fraction"], 0.)
                self.assertEqual(sum(geometry["reason_counts"].values()), 1)
        record = _sam_record(0)
        record["poses"][0]["source_binding"]["selection_epoch"] = 7
        record["sam3d_requests"][0]["selection_epoch"] = 7
        geometry = self._diagnose([record])["model_estimate_geometry"]
        self.assertEqual(geometry["source_bound_frame_count"], 1)
        self.assertEqual(geometry["frame_evidence"][0]["request_ledger"]["selection_epoch"], 7)

    def test_same_source_frame_with_changed_track_is_still_duplicate(self):
        records = [_sam_record(0), _sam_record(0)]
        second = records[1]
        second["frame"]["processed_index"] = 1
        second["detections"][0]["track_id"] = 2
        second["poses"][0]["person_track_id"] = 2
        second["poses"][0]["source_binding"].update(person_track_id=2, processed_index=1)
        second["sam3d_requests"][0]["person_track_id"] = 2
        geometry = self._diagnose(records)["model_estimate_geometry"]
        self.assertEqual(geometry["interval_frame_count"], 2)
        self.assertEqual(geometry["independent_source_frame_count"], 1)
        self.assertEqual(geometry["duplicate_source_frame_count"], 1)
        self.assertEqual(geometry["source_bound_frame_count"], 0)
        self.assertEqual(geometry["reason_counts"], {"duplicate_source_frame": 2})

        independent = _sam_record(1)
        independent["frame"]["processed_index"] = 2
        independent["poses"][0]["source_binding"]["processed_index"] = 2
        records.append(independent)
        geometry = self._diagnose(records)["model_estimate_geometry"]
        self.assertEqual(geometry["interval_frame_count"], 3)
        self.assertEqual(geometry["independent_source_frame_count"], 2)
        self.assertEqual(geometry["source_bound_frame_count"], 1)
        self.assertEqual(geometry["source_bound_frame_fraction"], .5)
        self.assertEqual(geometry["geometry_coverage_fraction"], .5)

    def test_multiple_people_in_one_frame_are_not_duplicate_source_frames(self):
        record = _sam_record(0)
        timeline = select_primary_player_timeline([record])["timeline"]
        other = copy.deepcopy(record["poses"][0])
        other["person_track_id"] = 2
        other["source_binding"]["person_track_id"] = 2
        record["poses"].append(other)
        record["sam3d_requests"].append({"person_track_id": 2, "selection_epoch": 0, "status": "ok"})
        record["detections"].append({"class_name": "player", "track_id": 2,
                                     "bbox_px": [110, 15, 130, 45], "confidence": .9})
        geometry = self._diagnose([record], timeline)["model_estimate_geometry"]
        self.assertEqual(geometry["independent_source_frame_count"], 1)
        self.assertEqual(geometry["source_bound_frame_count"], 1)
        self.assertEqual(geometry["duplicate_source_frame_count"], 0)


class PrimaryPlayerTests(unittest.TestCase):
    def test_selection_threshold_preserves_020_boundary_without_lowering_measurement_gate(self) -> None:
        self.assertEqual(KEYPOINT_CONFIDENCE_MIN, 0.20)
        for confidence, expected_valid_fraction in [(0.19, 0.0), (0.20, 1.0), (0.24, 1.0), (0.25, 1.0)]:
            with self.subTest(confidence=confidence):
                records = [_record(index, [(1, [30, 15, 100, 115], True)]) for index in range(4)]
                for record in records:
                    for point in record["poses"][0]["keypoints"]:
                        point["confidence"] = confidence
                result = select_primary_player_timeline(records)
                self.assertEqual(result["timeline"][0]["keypoint_valid_fraction"], expected_valid_fraction)
                diagnostics = diagnose_primary_timeline(records, result["timeline"])
                self.assertEqual(diagnostics["keypoint_valid_fraction"], expected_valid_fraction)
                sequence = pose_sequence_from_records(records, result["timeline"])
                _, measurable = point_series(sequence, "left_ankle")
                self.assertEqual(measurable.tolist(), [confidence >= 0.25] * 4)

    def test_low_confidence_primary_stays_stable_during_brief_background_detection(self) -> None:
        records = []
        for index in range(12):
            players = [(1, [30 + index, 15, 100 + index, 115], True)]
            if index in (5, 6):
                players.append((2, [2, 2, 195, 118], True))
            record = _record(index, players)
            for point in record["poses"][0]["keypoints"]:
                point["confidence"] = 0.22
            records.append(record)
        result = select_primary_player_timeline(records)
        self.assertEqual([item["source_track_id"] for item in result["timeline"]], [1] * 12)
        self.assertEqual([item["primary_player_id"] for item in result["timeline"]], [1] * 12)

    def test_nonfinite_or_out_of_frame_points_are_not_primary_pose_evidence(self) -> None:
        for field, value in [
            ("x_normalized", float("nan")),
            ("y_normalized", float("inf")),
            ("x_normalized", -0.1),
            ("y_normalized", 1.1),
            ("confidence", float("inf")),
            ("confidence", 1.1),
            ("confidence", None),
        ]:
            with self.subTest(field=field, value=value):
                records = [_record(0, [(1, [30, 15, 100, 115], True)])]
                point = next(point for point in records[0]["poses"][0]["keypoints"] if point["name"] == "left_ankle")
                point[field] = value
                result = select_primary_player_timeline(records)
                self.assertEqual(result["timeline"][0]["keypoint_valid_fraction"], 0.875)
                diagnostics = diagnose_primary_timeline(records, result["timeline"])
                self.assertEqual(diagnostics["keypoint_valid_fraction"], 0.875)

    def test_timeline_version_binding_rejects_legacy_or_mixed_artifacts(self) -> None:
        timeline = select_primary_player_timeline(
            [_record(index, [(1, [30, 15, 100, 115], True)]) for index in range(2)]
        )["timeline"]
        self.assertEqual(
            primary_timeline_algorithm_version(
                timeline, expected_version="primary-player-v0.4.0"
            ),
            "primary-player-v0.4.0",
        )
        legacy = copy.deepcopy(timeline)
        for item in legacy:
            item["selection_algorithm_version"] = "primary-player-v0.2.0"
        with self.assertRaisesRegex(ValueError, "does not match feasibility registry"):
            primary_timeline_algorithm_version(
                legacy, expected_version="primary-player-v0.4.0"
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
            [None] * 6,
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
