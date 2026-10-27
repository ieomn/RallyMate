"""Shared object proximity is not handedness or evidence from another action."""
from copy import deepcopy
import unittest
from unittest.mock import patch
from rallymate_scoring.stroke_candidates import Sample,_associate_rackets,_racket_support_with_shared_preparation


def sequence():
    rows=[]
    for i in range(24):
        pts={"left_shoulder":(-.3,0),"right_shoulder":(.3,0),"left_wrist":(-.05,.5),"right_wrist":(.05,.5)}
        rows.append(Sample(i*50,i,1,(300,200),100,pts,set(),timestamp_source="decoder_pts"))
    for i in (10,11):
        _associate_rackets([rows[i]],[{"class_name":"racket","confidence":.9,"track_id":7,"bbox_px":[294,249,306,251]}])
    rows[20].points.update(left_wrist=(-.6,.5),right_wrist=(.8,0))
    _associate_rackets([rows[20]],[{"class_name":"racket","confidence":.9,"track_id":9,"bbox_px":[379,199,381,201]}])
    return rows


def support(rows):return _racket_support_with_shared_preparation(rows,"right",15,18,21)


class SharedRacketPreparationTests(unittest.TestCase):
    def test_paired_model_proximity_supports_object_only_after_separated_same_side_evidence(self):
        rows=sequence();count,proof=support(rows)
        self.assertEqual(count,3)
        self.assertEqual(rows[10].racket_sides,set())
        self.assertEqual(rows[11].racket_sides,set())
        self.assertEqual([x["racket_track_id"] for x in proof["shared_preparation_observations"]],[7,7])
        self.assertEqual(proof["separated_motion_observation"]["racket_track_id"],9)
        self.assertEqual(proof["candidate_geometry_start_ms"],750)
        self.assertEqual(proof["preparation_horizon_start_ms"],250)

    def test_shared_proximity_without_independent_later_side_cannot_identify_swing_side(self):
        rows=sequence();rows[20].racket_observations=[];rows[20].racket_sides=set()
        self.assertEqual(support(rows),(0,None))

    def test_remote_old_preparation_is_not_available_to_later_candidate(self):
        rows=sequence()
        for i in (10,11):
            rows[i].racket_observations=[]
        for i in (1,2):
            _associate_rackets([rows[i]],[{"class_name":"racket","confidence":.9,"track_id":7,"bbox_px":[294,249,306,251]}])
        self.assertEqual(support(rows),(1,None))

    def test_source_camera_identity_or_opposite_hand_evidence_breaks_transfer(self):
        for kind in ("identity","camera","opposite","missing"):
            rows=sequence()
            if kind=="identity":rows[14].selection_epoch=1
            elif kind=="camera":rows[14].camera_reference_epoch=1
            elif kind=="missing":del rows[14].points["left_wrist"]
            else:
                rows[14].points.update(left_wrist=(-.8,0),right_wrist=(.6,.5))
                rows[14].racket_sides={"left"}
            with self.subTest(kind=kind):self.assertEqual(support(rows),(1,None))

    def test_two_plausible_rackets_or_two_target_players_are_not_unique_support(self):
        rows=sequence()
        for i in (10,11):rows[i].racket_observations.append(deepcopy(rows[i].racket_observations[0]))
        self.assertEqual(support(rows),(1,None))
        a=sequence()[0];b=deepcopy(a);b.track=2
        _associate_rackets([a,b],[{"class_name":"racket","confidence":.9,"track_id":7,"bbox_px":[294,249,306,251]}])
        self.assertFalse(a.racket_observations);self.assertFalse(b.racket_observations)

    def test_separated_wrist_association_preserves_existing_nearest_wrist_rule(self):
        row=sequence()[0];row.points.update(left_wrist=(0,0),right_wrist=(.5,0))
        _associate_rackets([row],[{"class_name":"racket","confidence":.9,"track_id":7,"bbox_px":[310,199,326,201]}])
        self.assertEqual(row.racket_sides,{"left"})
        self.assertEqual(row.racket_observations[0]["association"],"single_wrist_proximity")
        row=sequence()[0];row.points.update(left_wrist=(0,0),right_wrist=(.6,0))
        _associate_rackets([row],[{"class_name":"racket","confidence":.9,"track_id":7,"bbox_px":[320,199,360,201]}])
        self.assertEqual(row.racket_sides,{"right"})

    def test_near_one_wrist_only_is_not_model_shared_support(self):
        row=sequence()[0];row.points["right_wrist"]=(.8,.5)
        _associate_rackets([row],[{"class_name":"racket","confidence":.9,"track_id":7,"bbox_px":[294,249,306,251]}])
        self.assertEqual(row.racket_observations[0]["association"],"single_wrist_proximity")
        self.assertEqual(row.racket_sides,{"left"})

    def test_more_shared_preparation_frames_cannot_replace_direct_local_support(self):
        from rallymate_scoring.stroke_candidates import _detect_tracks
        direct={"candidate_id":"direct","family":"baseline","person_track_id":1,
                "racket_hand_candidate":"right","start_ms":300,"peak_ms":500,"end_ms":700,
                "evidence":{"racket_associated_frames":2,"racket_support_transfer":None}}
        shared=deepcopy(direct)
        shared.update(candidate_id="shared",start_ms=800,peak_ms=900,end_ms=1100)
        shared["evidence"].update(racket_associated_frames=20,racket_support_transfer={"version":"shared-racket-preparation-v1"})
        with patch("rallymate_scoring.stroke_candidates._detect_segment",side_effect=lambda s,side: [shared,direct] if side=="right" else []), \
             patch("rallymate_scoring.stroke_analysis.candidate_hand_evidence",return_value={"status":"unresolved"}), \
             patch("rallymate_scoring.stroke_candidates._group_supporting_sweeps",side_effect=lambda cs,_:cs):
            self.assertEqual([c["candidate_id"] for c in _detect_tracks({1:sequence()})],["direct"])
            shared.update(start_ms=1800,peak_ms=1900,end_ms=2100)
            # A genuinely separate proposal is retained; provenance preference
            # does not merge actions outside the existing suppression window.
            self.assertEqual([c["candidate_id"] for c in _detect_tracks({1:sequence()})],["direct","shared"])


if __name__=="__main__":unittest.main()
