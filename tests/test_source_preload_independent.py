"""Independent review of FS preload timing, geometry and report projection."""
import copy
from fractions import Fraction
import unittest

from rallymate_features.coordinates import pose_sequence_from_records
from rallymate_features.source_alignment import compute_source_aligned_features, prepare_source_aligned_context
from tests.test_source_preload_consumers import observed_fixture, projected
from tests.test_source_preload_translation import NAMES, run


def from_records(step_ticks=1200):
    sequence, events, timeline = observed_fixture(step_ticks)
    records, selected = [], []
    for i,t in enumerate(sequence.timestamp_ms):
        b=sequence.source_time_bindings[i]
        records.append({"frame":{"index":i,"processed_index":i,"timestamp_ms":int(t),
            "source_timestamp_ms":b["source_timestamp_ms"],"timestamp_source":"decoder_pts",
            "video_sha256":b["video_sha256"],"pts":b["pts"],"time_base_num":1,"time_base_den":30000,
            "width":1000,"height":1000},"poses":[{"source":"rtmpose","person_track_id":1,
            "keypoints":[{"name":name,"x_px":float(xy[i,0]*1000),"y_px":float(xy[i,1]*1000),"confidence":.95}
                         for name,xy in sequence.keypoints_xy.items()]}],
            "camera_motion":{"status":"stationary","reference_epoch":0,"compensation_valid":True,
                             "matrix_to_reference":[[1.,0.,0.],[0.,1.,0.]]}})
        selected.append({**timeline[i],"processed_index":i,"selection_epoch":0,"camera_reference_epoch":0})
    return records,events,selected


class IndependentPreloadReferenceTests(unittest.TestCase):
    def test_actual_record_loader_to_both_report_layers_at_exact_duration_boundary(self):
        for ticks,available in ((799,False),(800,True),(801,True)):
            with self.subTest(ticks=ticks):
                records,events,timeline=from_records(ticks)
                sequence=pose_sequence_from_records(records,timeline)
                _,rows,raw=projected(sequence,events,timeline)
                b1,b4=sequence.source_time_bindings[1],sequence.source_time_bindings[4]
                expected=Fraction((b4["pts"]-b1["pts"])*1000,30000)
                self.assertEqual(expected>=80,available)
                for name in NAMES:
                    self.assertEqual(raw[name]["valid"],available)
                    self.assertEqual(rows[name]["value"],raw[name]["value"])
                    if available:self.assertAlmostEqual(raw[name]["evidence"]["source_duration_ms"],float(expected))

    def test_large_playback_offset_does_not_change_duration_or_reference_displacement(self):
        sequence,events,timeline=observed_fixture()
        _,before=run(sequence,events,timeline)
        offset=3_600_000
        sequence.timestamp_ms[:]+=offset
        for row in timeline:row["timestamp_ms"]+=offset
        for binding in sequence.source_time_bindings.values():binding["video_timestamp_ms"]+=offset
        for event in events:
            event["start_ms"]+=offset;event["end_ms"]+=offset
            for key,value in event["key_phases_ms"].items():event["key_phases_ms"][key]=value+offset
        _,after=run(sequence,events,timeline)
        for name in NAMES:
            self.assertEqual(after[name]["value"],before[name]["value"])
            self.assertEqual(after[name]["evidence"]["source_duration_ms"],120.)

    def test_each_exact_clock_gap_is_checked_not_only_whole_window_duration(self):
        sequence,events,timeline=observed_fixture()
        context=prepare_source_aligned_context(sequence,events,primary_timeline=timeline)
        b=sequence.source_time_bindings[2]
        b["pts"]+=1800;b["source_timestamp_ms"]=round(b["pts"]/30)
        result=compute_source_aligned_features(sequence,events[0],events=events,context=context)
        rows={row['feature_name']:row for item in result['indicators'] if item['indicator_id']=='FS01-M02'
              for row in item['source_measurements']}
        for name in NAMES:
            self.assertIsNone(rows[name]["value"])
            self.assertEqual(rows[name]["reason"],"source_time_or_frame_gap")

    def test_partial_timing_proof_or_conflicting_processing_index_rejects_only_new_metrics(self):
        for defect in ("missing_binding","processed_jump","timebase","selection_epoch"):
            with self.subTest(defect=defect):
                sequence,events,timeline=observed_fixture()
                _,before=run(sequence,events,timeline)
                if defect=="missing_binding":del sequence.source_time_bindings[2]
                elif defect=="processed_jump":sequence.source_time_bindings[2]["processed_index"]+=10
                elif defect=="selection_epoch":sequence.source_time_bindings[2]["selection_epoch"]+=1
                else:
                    sequence.source_time_bindings[2]["time_base_num"]=2
                    sequence.source_time_bindings[2]["time_base_den"]=60000
                _,after=run(sequence,events,timeline)
                self.assertTrue(all(after[name]["value"] is None for name in NAMES))
                self.assertEqual({k:v for k,v in before.items() if k not in NAMES},
                                 {k:v for k,v in after.items() if k not in NAMES})

    def test_reference_pixels_are_recovered_once_with_fixed_full_window_scale(self):
        sequence,events,timeline=observed_fixture()
        for side in ("left","right"):
            # A valid reference outside current image boundaries is retained.
            sequence.keypoints_xy[side+"_hip"][:,1]-=1
            sequence.keypoints_xy[side+"_shoulder"][:,1]-=1
            for index,length in zip(range(1,5),(.2,.25,.30,.4)):
                sequence.keypoints_xy[side+"_shoulder"][index,1]=sequence.keypoints_xy[side+"_hip"][index,1]-length
        sequence.frame_transforms_to_reference[:,1,2]=-50.
        _,rows=run(sequence,events,timeline)
        self.assertAlmostEqual(rows[NAMES[0]]["value"],40.)
        self.assertAlmostEqual(rows[NAMES[1]]["value"],40/275)
        self.assertLess(rows[NAMES[0]]["evidence"]["hip_reference_y_start_px"],0)
