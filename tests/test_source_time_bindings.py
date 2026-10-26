"""Decoder timing transport tests, not pose accuracy or technique grading."""
import copy
from dataclasses import replace
from fractions import Fraction
import unittest

import numpy as np

from rallymate_features.coordinates import pose_sequence_from_records
from rallymate_features.schemas import validated_source_time_binding
from tests.test_sam_footwork import sam_motion_fixture


def fixture(pts=(7100, 7120, 7140), *, denominator=600, source_origin_ms=0):
    records, timeline = [], []
    for n, value in enumerate(pts):
        exact = Fraction(value * 1000, denominator)
        source_ms, video_ms = round(exact), round(exact - source_origin_ms)
        index = 355 + n
        frame = {"index": index, "processed_index": 10+n, "timestamp_ms": video_ms,
            "source_timestamp_ms": source_ms, "timestamp_source": "decoder_pts", "video_sha256": "a"*64,
            "pts": value, "time_base_num": 1, "time_base_den": denominator, "width": 1000, "height": 1000}
        records.append({"frame": frame,
            "poses": [{"person_track_id": 7, "source": "rtmpose", "keypoints": [
                {"name": "left_hip", "x_px": 400., "y_px": 550., "confidence": .8}]}],
            "camera_motion": {"status": "stationary", "reference_epoch": 2,
                "compensation_valid": True, "matrix_to_reference": [[1.,0.,0.],[0.,1.,0.]]},
            "benchmark_provenance": {"selected_person_track_id": 7, "selection_epoch": 3}})
        timeline.append({"processed_index": 10+n, "source_frame_index": index, "timestamp_ms": video_ms,
            "source_timestamp_ms": source_ms, "primary_player_id": 1, "source_track_id": 7,
            "selection_status": "selected", "identity_ambiguous": False,
            "selection_epoch": 3, "camera_reference_epoch": 2})
    return records, timeline


def exact_time(binding):
    return Fraction(binding["pts"] * binding["time_base_num"] * 1000, binding["time_base_den"])


class SourceTimeBindingTests(unittest.TestCase):
    def test_retains_exact_source_frame_pts_and_identity_without_model_promotion(self):
        records, timeline = fixture()
        before = copy.deepcopy(records)
        sequence = pose_sequence_from_records(records, timeline)
        self.assertEqual(list(sequence.source_time_bindings), [355,356,357])
        binding = sequence.source_time_bindings[355]
        self.assertEqual((binding["pts"], binding["time_base_num"], binding["time_base_den"]), (7100,1,600))
        self.assertEqual(exact_time(binding), Fraction(35500,3))
        self.assertEqual((binding["source_timestamp_ms"],binding["video_timestamp_ms"]), (11833,11833))
        self.assertEqual((binding["primary_player_id"], binding["person_track_id"], binding["selection_epoch"],
                          binding["camera_reference_epoch"]), (1,7,3,2))
        self.assertIsNone(sequence.model_estimate_bindings)
        self.assertEqual(sequence.model_estimate_masks, {})
        np.testing.assert_array_equal(sequence.confidence["left_hip"], [.8,.8,.8])
        np.testing.assert_array_equal(sequence.keypoints_xy["left_hip"], [[.4,.55]]*3)
        self.assertEqual(records, before)

    def test_nonzero_source_origin_and_strict_80ms_boundary_survive_rounding(self):
        for last, comparison in ((10798, -1), (10800, 0), (10802, 1)):
            with self.subTest(last_pts=last):
                sequence = pose_sequence_from_records(*fixture((10000,10400,last), denominator=10000, source_origin_ms=1000))
                self.assertEqual(sequence.timestamp_ms.tolist(), [0,40,80])
                a,b = sequence.source_time_bindings[355],sequence.source_time_bindings[357]
                delta = exact_time(b)-exact_time(a)
                self.assertEqual((delta>80)-(delta<80), comparison)
                self.assertEqual(a["source_timestamp_ms"],1000)
                self.assertEqual(a["video_timestamp_ms"],0)

    def test_rejects_missing_invalid_or_non_decoder_source_evidence_without_changing_xy(self):
        defects = {"no_pts": lambda f:f.pop("pts"), "negative_pts":lambda f:f.update(pts=-1),
            "bool_pts":lambda f:f.update(pts=True), "bad_timebase":lambda f:f.update(time_base_den=0),
            "float_timebase":lambda f:f.update(time_base_num=1.0), "bad_sha":lambda f:f.update(video_sha256="not-sha"),
            "wrong_source_ms":lambda f:f.update(source_timestamp_ms=0),
            "infinite_source":lambda f:f.update(source_timestamp_ms=float("inf")),
            "huge_source":lambda f:f.update(source_timestamp_ms=10**1000),
            "estimated_time":lambda f:f.update(timestamp_source="fps_estimate")}
        for name, mutate in defects.items():
            with self.subTest(name=name):
                records,timeline=fixture(); mutate(records[1]["frame"])
                sequence=pose_sequence_from_records(records,timeline)
                self.assertNotIn(356,sequence.source_time_bindings)
                self.assertIn(355,sequence.source_time_bindings)
                self.assertIn(357,sequence.source_time_bindings)
                self.assertIn(356,sequence.coordinate_metadata["source_time_binding"]["unavailable_source_frames"])
                self.assertTrue(np.isfinite(sequence.keypoints_xy["left_hip"][1]).all())

    def test_source_timestamp_rounding_tolerance_is_only_for_export_consistency(self):
        records,timeline=fixture()
        value=pose_sequence_from_records(records,timeline).source_time_bindings[357]
        for offset, valid in ((.5,True),(.51,True),(.52,False)):
            changed=copy.deepcopy(value); changed["source_timestamp_ms"]=float(exact_time(value))+offset
            self.assertEqual(validated_source_time_binding(changed) is not None,valid)

    def test_rejects_ambiguous_duplicate_or_contradictory_identity_epoch(self):
        for defect in ("ambiguous","duplicate_timeline","track","selection_epoch","camera_epoch","video_sha","frame","source_ms"):
            with self.subTest(defect=defect):
                records,timeline=fixture()
                if defect=="ambiguous":timeline[1]["identity_ambiguous"]=True
                elif defect=="duplicate_timeline":timeline.append(copy.deepcopy(timeline[1]))
                elif defect=="track":timeline[1]["source_track_id"]=8
                elif defect=="selection_epoch":records[1]["benchmark_provenance"]["selection_epoch"]=4
                elif defect=="camera_epoch":timeline[1]["camera_reference_epoch"]=2.0
                elif defect=="video_sha":timeline[1]["video_sha256"]="b"*64
                elif defect=="frame":timeline[1]["source_frame_index"]=100
                else:timeline[1]["source_timestamp_ms"]+=1
                sequence=pose_sequence_from_records(records,timeline)
                self.assertNotIn(356,sequence.source_time_bindings)
        records,timeline=fixture()
        records[1]["frame"]["index"]=355;timeline[1]["source_frame_index"]=355
        sequence=pose_sequence_from_records(records,timeline)
        self.assertNotIn(355,sequence.source_time_bindings)

    def test_clock_origin_is_constant_and_cannot_drift_between_frames(self):
        records,timeline=fixture((10000,10400,10800,11200),denominator=10000,source_origin_ms=1000)
        records[2]["frame"]["timestamp_ms"]+=2;timeline[2]["timestamp_ms"]+=2
        sequence=pose_sequence_from_records(records,timeline)
        self.assertNotIn(357,sequence.source_time_bindings)
        self.assertIn(358,sequence.source_time_bindings)
        self.assertEqual(sequence.coordinate_metadata["source_time_binding"]["unavailable_source_frames"][357],
                         "source_and_video_time_origin_mismatch")

    def test_missing_pose_keeps_time_only_legacy_has_no_fabricated_bindings(self):
        records,timeline=fixture();records[1]["poses"]=[]
        sequence=pose_sequence_from_records(records,timeline)
        self.assertIn(356,sequence.source_time_bindings)
        self.assertTrue(np.isnan(sequence.keypoints_xy["left_hip"][1]).all())
        for record in records:record["frame"].pop("timestamp_source")
        legacy=pose_sequence_from_records(records,timeline)
        self.assertEqual(legacy.source_time_bindings,{})
        np.testing.assert_equal(legacy.keypoints_xy["left_hip"],sequence.keypoints_xy["left_hip"])

    def test_slice_and_replace_preserve_independent_source_binding_and_model_masks(self):
        records,timeline=fixture();sequence=pose_sequence_from_records(records,timeline)
        sliced=sequence.slice(slice(1,3))
        self.assertEqual(list(sliced.source_time_bindings),[356,357])
        self.assertIsNot(sliced.source_time_bindings[356],sequence.source_time_bindings[356])
        self.assertEqual(replace(sliced,coordinate_metadata={}).source_time_bindings,sliced.source_time_bindings)
        records,timeline=sam_motion_fixture(source_offset_ms=1000)
        model=pose_sequence_from_records(records[:4],timeline[:4]);part=model.slice(slice(1,3))
        self.assertEqual(list(part.source_time_bindings),[1,2])
        self.assertEqual(part.model_estimate_bindings,model.model_estimate_bindings[1:3])
        for name,mask in part.model_estimate_masks.items():
            np.testing.assert_array_equal(mask,model.model_estimate_masks[name][1:3])
        self.assertTrue(all(np.isnan(values).all() for values in part.confidence.values()))

    def test_schema_rejects_unmatched_time_binding_even_for_direct_construction(self):
        sequence=pose_sequence_from_records(*fixture())
        for defect in ("frame","video","canonical","epoch","invalid","extra"):
            with self.subTest(defect=defect):
                bindings=copy.deepcopy(sequence.source_time_bindings)
                b=bindings[355]
                if defect=="frame":b["frame_index"]=999
                elif defect=="video":b["video_timestamp_ms"]+=1
                elif defect=="canonical":b["primary_player_id"]=99
                elif defect=="epoch":b["camera_reference_epoch"]+=1
                elif defect=="invalid":b["time_base_den"]=0
                else:b["visibility_verified"]=True
                with self.assertRaises(ValueError):replace(sequence,source_time_bindings=bindings)
