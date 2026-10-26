"""Numerical invariance tests; the real fixture is stored geometry, not truth."""
import copy
import json
import math
from pathlib import Path
import unittest

from rallymate_scoring.vector_kinematics import vector_trajectory, VERSION
from rallymate_scoring.stroke_candidates import Sample, _sample
from rallymate_scoring.temporal_recognition import wrist_kinematics
from rallymate_scoring.process_motion import _trajectory


def samples(times, points):
    return [Sample(t,i,1,(0.,0.),100.,{'right_wrist':p},set(),timestamp_source='decoder_pts')
            for i,(t,p) in enumerate(zip(times,points))]


def rotate(p, angle, shift=(0.,0.)):
    c,s=math.cos(angle),math.sin(angle)
    return c*p[0]-s*p[1]+shift[0],s*p[0]+c*p[1]+shift[1]


class VectorKinematicsTests(unittest.TestCase):
    def assertPointsClose(self,a,b):
        self.assertEqual(len(a),len(b))
        for p,q in zip(a,b):
            if p is None or q is None:self.assertEqual(p,q)
            else:
                for x,y in zip(p,q):self.assertAlmostEqual(x,y,places=10)

    def test_irregular_actual_times_preserve_constant_velocity_without_endpoint_zero(self):
        times=[0,17,39,70,104,139,181]
        p=[(2*t/1000-3,-3*t/1000+4) for t in times]
        result=vector_trajectory(times,p)
        self.assertPointsClose(result.points,p)
        self.assertPointsClose(result.interval_velocity,[(2.,-3.)]*6)
        for speed in result.interval_speed:self.assertAlmostEqual(speed,math.sqrt(13))
        _, speed=wrist_kinematics(samples(times,p),'right')
        self.assertNotEqual(speed[0],0);self.assertNotEqual(speed[-1],0)

    def test_interval_speed_is_not_mislabeled_center_derivative_on_unequal_steps(self):
        result=vector_trajectory([0,20,100],[(0,0),(.0004,0),(.01,0)])
        self.assertAlmostEqual(result.interval_speed[0],.02)
        self.assertAlmostEqual(result.interval_speed[1],.12)
        self.assertEqual(result.interval_midpoint_ms,[10,60])

    def test_full_vector_smoothing_commutes_with_rigid_transform_and_uniform_scale(self):
        times=[i*30 for i in range(19)]
        p=[(math.cos(i*.42)+(.2 if i==9 else 0), math.sin(i*.42)) for i in range(19)]
        baseline=vector_trajectory(times,p)
        for angle in (.3,math.pi/4,1.7):
            changed=vector_trajectory(times,[rotate(v,angle,(10,-20)) for v in p])
            self.assertPointsClose(changed.points,[rotate(v,angle,(10,-20)) for v in baseline.points])
            for a,b in zip(baseline.interval_speed,changed.interval_speed):self.assertAlmostEqual(a,b,places=10)
        scaled=vector_trajectory(times,[(x*3,y*3) for x,y in p])
        for a,b in zip(baseline.interval_speed,scaled.interval_speed):self.assertAlmostEqual(a*3,b,places=10)

    def test_missing_point_frame_gap_and_identity_break_are_not_smoothed_across(self):
        times=[i*30 for i in range(9)];p=[(i*.1,0.) for i in range(9)]
        missing=p[:];missing[4]=None
        result=vector_trajectory(times,missing)
        self.assertIsNone(result.points[4]);self.assertEqual(result.interval_speed[3:5],[None,None])
        self.assertEqual(result.points[3],p[3]);self.assertEqual(result.points[5],p[5])
        result=vector_trajectory(times,p,frame_indices=[0,1,2,3,7,8,9,10,11])
        self.assertIsNone(result.interval_speed[3])
        flags=[True]*8;flags[3]=False
        result=vector_trajectory(times,p,continuous=flags)
        self.assertIsNone(result.interval_speed[3])
        broken=samples(times,p);broken[4].selection_epoch=2
        with self.assertRaises(ValueError):wrist_kinematics(broken,'right')

    def test_long_time_gap_and_nonmonotonic_clock_do_not_create_velocity(self):
        p=[(i*.1,0.) for i in range(7)]
        for times in ([0,30,60,600,630,660,690],[0,30,60,40,70,100,130]):
            result=vector_trajectory(times,p)
            self.assertIsNone(result.interval_speed[2])

    def test_noise_is_attenuated_but_observed_endpoints_are_not_hidden(self):
        times=[i*25 for i in range(13)];p=[(t/1000,0.) for t in times];p[6]=(p[6][0],.2)
        result=vector_trajectory(times,p)
        self.assertLess(abs(result.points[6][1]),.2)
        self.assertEqual(result.points[0],p[0]);self.assertEqual(result.points[-1],p[-1])
        # Isotropic time weights, not independent coordinate medians.
        self.assertEqual(len(set(result.segment_ids)),1)

    def test_two_production_entry_points_use_identical_filtered_points_and_interval_speeds(self):
        times=[i*33 for i in range(17)];p=[(math.sin(i*.4),math.cos(i*.4)) for i in range(17)]
        data=samples(times,p);a,sp=wrist_kinematics(data,'right');b,sq=_trajectory(data,'right')
        self.assertEqual(a,b);self.assertEqual(sp[1:],sq);self.assertEqual(max(sp),max(sq))

    def test_raw_pixel_scaling_normalization_preserves_the_joint_speed(self):
        data=[];enlarged=[]
        for i in range(13):
            points={'left_shoulder':(80,40),'right_shoulder':(120,40),'left_hip':(90,140),'right_hip':(110,140),'right_wrist':(150+i*3,70+i)}
            pose={'person_track_id':1,'keypoints':[{'name':n,'x_px':p[0],'y_px':p[1],'confidence':.9} for n,p in points.items()]}
            frame={'index':i,'timestamp_ms':i*30,'timestamp_source':'decoder_pts'}
            data.append(_sample(pose,frame));other=copy.deepcopy(pose)
            for point in other['keypoints']:point['x_px']*=2.5;point['y_px']*=2.5
            enlarged.append(_sample(other,frame))
        a,sa=wrist_kinematics(data,'right');b,sb=wrist_kinematics(enlarged,'right')
        self.assertPointsClose(a,b)
        for v,w in zip(sa,sb):self.assertAlmostEqual(v,w,places=10)

    def test_real_user_window_rotation_translation_and_shared_measurement(self):
        fixture=json.loads((Path(__file__).parent/'fixtures/wrist-kinematics-b10-real-window.json').read_text(encoding='utf8'))
        self.assertEqual(len(fixture['rows']),28)
        times=[r['timestamp_ms'] for r in fixture['rows']]
        p=[tuple((v-c)/r['scale_reference_px'] for v,c in zip(r['wrist_reference_px'],r['shoulder_center_reference_px'])) for r in fixture['rows']]
        current=samples(times,p)
        for sample,row in zip(current,fixture['rows']):sample.frame_index=row['frame_index']
        a,speed=wrist_kinematics(current,'right');b,process=_trajectory(current,'right')
        self.assertEqual(a,b);self.assertEqual(speed[1:],process)
        transformed=samples(times,[rotate(v,math.pi/4,(5,-3)) for v in p])
        _,rotated=wrist_kinematics(transformed,'right')
        for x,y in zip(speed,rotated):self.assertAlmostEqual(x,y,places=10)
        old_delta=fixture['old_peak_speed']-fixture['old_peak_after_coordinate_rotation_45deg']
        self.assertGreater(old_delta,.8,'frozen old calculation exhibits the rotation-dependent error')
        self.assertEqual(VERSION,'time-weighted-vector-kinematics-v1.0.0')


if __name__=='__main__':unittest.main()
