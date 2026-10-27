"""Geometric counterfactuals: no player labels, quality scores or manual windows."""
from copy import deepcopy
import math
from types import SimpleNamespace
import unittest

from rallymate_scoring.paired_motion import paired_wrist_phase_evidence, paired_arc_sweep_evidence


def samples(kind="arc", angle=0., mirror=False):
    result=[]
    for i in range(31):
        f=i/30
        theta=math.pi/2+1.5*math.pi*f
        r=.85
        center=(r*math.cos(theta),r*math.sin(theta))
        if kind=="raise": center=(.1, .9-1.8*f)
        if kind=="pickup": center=(.5, .7+math.sin(math.pi*f))
        if kind=="preparation":
            theta=math.pi/2-math.pi*.6*f
            center=(r*math.cos(theta),r*math.sin(theta))
        p={"left_shoulder":(-.3,0),"right_shoulder":(.3,0),
           "left_wrist":(center[0]-.05,center[1]),"right_wrist":(center[0]+.05,center[1])}
        if kind=="one_arm": p["right_wrist"]=(.5,.7)
        if kind in {"rigid_turn","translation"}:
            p={"left_shoulder":(-.3,0),"right_shoulder":(.3,0),"left_wrist":(-.05,.85),"right_wrist":(.05,.85)}
            if kind=="rigid_turn":
                p={k:(x*math.cos(theta)-y*math.sin(theta),x*math.sin(theta)+y*math.cos(theta)) for k,(x,y) in p.items()}
            else:
                p={k:(x+center[0],y+center[1]) for k,(x,y) in p.items()}
        p={k:(((-x if mirror else x)*math.cos(angle)-y*math.sin(angle)),
              ((-x if mirror else x)*math.sin(angle)+y*math.cos(angle))) for k,(x,y) in p.items()}
        result.append(SimpleNamespace(time=i*20,frame_index=i,track=1,selection_epoch=0,camera_reference_epoch=0,
                     normalization_basis="full_torso",center=(0.,0.),scale=100.,points=p,racket_sides={"left"},
                     timestamp_source="decoder_pts",source_binding={}))
    return result


def arc(data):
    return paired_arc_sweep_evidence(data,start_ms=0,acceleration_start_ms=100,peak_ms=300,end_ms=600,motion_wrist="left")


def phase(data):
    return paired_wrist_phase_evidence(data,start_ms=100,end_ms=300,motion_wrist="left")


class PairedMotionTests(unittest.TestCase):
    def test_observed_paired_arc_has_source_local_acceleration_support(self):
        result=arc(samples())
        self.assertEqual(result["status"],"supported")
        self.assertEqual(result["acceleration"]["start_ms"],100)
        self.assertEqual(result["acceleration"]["end_ms"],300)
        self.assertEqual([p["frame_index"] for p in result["acceleration"]["sample_binding"]],list(range(5,16)))

    def test_rotation_and_mirror_do_not_change_the_sufficient_pattern(self):
        expected=arc(samples())
        for angle in (0.,.8,math.pi/2,2.8):
            for mirror in (False,True):
                with self.subTest(angle=angle,mirror=mirror):
                    result=arc(samples(angle=angle,mirror=mirror))
                    self.assertEqual(result["status"],"supported")
                    self.assertAlmostEqual(result["stats"]["arc_radians"],expected["stats"]["arc_radians"],places=10)
                    self.assertAlmostEqual(result["stats"]["diameter_torso"],expected["stats"]["diameter_torso"],places=10)

    def test_straight_raise_pickup_and_short_preparation_do_not_form_full_arc(self):
        for kind in ("raise","pickup","preparation"):
            with self.subTest(kind=kind): self.assertEqual(arc(samples(kind))["status"],"unavailable")

    def test_one_arm_toss_or_balance_arm_does_not_establish_two_wrist_motion(self):
        self.assertEqual(arc(samples("one_arm"))["status"],"unavailable")
        self.assertEqual(phase(samples("one_arm"))["status"],"unavailable")

    def test_rigid_body_rotation_and_translation_are_not_independent_arm_sweeps(self):
        for kind in ("rigid_turn","translation"):
            with self.subTest(kind=kind): self.assertEqual(arc(samples(kind))["status"],"unavailable")

    def test_missing_racket_evidence_cannot_be_borrowed_from_after_the_window(self):
        data=samples()
        for s in data:s.racket_sides=set()
        self.assertEqual(arc(data)["status"],"unavailable")

    def test_missing_wrist_and_source_identity_camera_or_frame_gap_break_support(self):
        for kind in ("wrist","identity","camera","framegap"):
            data=samples()
            if kind=="wrist":del data[10].points["right_wrist"]
            elif kind=="identity":data[10].selection_epoch=1
            elif kind=="camera":data[10].camera_reference_epoch=1
            else:del data[10]
            with self.subTest(kind=kind):self.assertEqual(phase(data)["status"],"unavailable")

    def test_unobserved_bound_and_stationary_pair_do_not_have_supported_phase(self):
        data=samples()
        self.assertEqual(paired_wrist_phase_evidence(data,start_ms=101,end_ms=300,motion_wrist="left")["status"],"unavailable")
        for s in data:s.points=deepcopy(data[0].points)
        self.assertEqual(phase(data)["status"],"unavailable")

    def test_unrelated_opposite_directions_do_not_establish_shared_wrist_motion(self):
        data=samples()
        for s in data:
            x,y=s.points["left_wrist"];s.points["right_wrist"]=(-x,-y)
        self.assertEqual(phase(data)["status"],"unavailable")

    def test_metadata_score_or_label_cannot_change_geometric_support(self):
        data=samples(); expected=arc(data)
        for s in data:s.score=0.;s.player_label="unrelated";s.expected_sequence=["wrist","elbow","hip"]
        self.assertEqual(arc(data),expected)


if __name__=="__main__":unittest.main()
