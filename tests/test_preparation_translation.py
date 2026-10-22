import copy
import json
import unittest

from rallymate_scoring.preparation_translation import analyze_preparation_translation
from rallymate_scoring.process_motion import analyze_process_motion
from rallymate_scoring.stroke_candidates import Sample
from rallymate_scoring.stroke_candidates import _sample


def fixture():
    result=[]
    for i,time in enumerate(range(0,241,40)):
        dx,dy=time*.1,time*.05
        raw={'left_hip':(290+dx,350+dy),'right_hip':(310+dx,350+dy),
             'left_knee':(280+dx,390+dy),'right_knee':(320+dx,390+dy),
             'left_ankle':(290+dx,430+dy),'right_ankle':(310+dx,430+dy)}
        result.append(Sample(time,i,1,(300+dx,250+dy),100,
            {name:((x-300-dx)/100,(y-250-dy)/100) for name,(x,y) in raw.items()},set(),
            camera_reference_epoch=0,camera_compensated=True,reference_points=raw,
            point_confidences={name:.9 for name in raw},timestamp_source='decoder_pts'))
    return result,[{'phase':'preparation','status':'measured','start_ms':0,'end_ms':240}]


def metric(result,name):return next(m for m in result['measurements'] if m['feature_name']==name)


class PreparationTranslationTests(unittest.TestCase):
    def test_stable_reference_translation_survives_body_recentering(self):
        samples,phases=fixture()
        result=analyze_preparation_translation(samples,phases)
        for name,value in [('hip_center_dx_torso',.24),('hip_center_dy_torso',.12),
                           ('hip_center_mean_velocity_x_torso_per_s',1.),('hip_center_mean_velocity_y_torso_per_s',.5)]:
            row=metric(result,name)
            self.assertEqual(row['status'],'measured')
            self.assertAlmostEqual(row['windows'][0]['value'],value)
            self.assertEqual(row['windows'][0]['source_frames'],list(range(7)))
            self.assertEqual(row['windows'][0]['source_timestamps_ms'],list(range(0,241,40)))
        self.assertFalse(result['formal_stage_verified'])
        self.assertIsNone(result['score'])
        self.assertEqual(analyze_process_motion(samples,phases,'right')['preparation_translation'],result)
        json.dumps(result,allow_nan=False)

    def test_fixed_window_scale_and_actual_irregular_time_denominator(self):
        samples,phases=fixture()
        for i,sample in enumerate(samples): sample.scale=100+i*10
        samples[-1].time=260
        phases[0]['end_ms']=260
        result=analyze_preparation_translation(samples,phases)
        row=metric(result,'hip_center_mean_velocity_x_torso_per_s')['windows'][0]
        self.assertEqual(row['fixed_scale_px'],130)
        self.assertAlmostEqual(row['value'],24/130/.260)

    def test_local_joint_occlusion_only_splits_that_measurement(self):
        samples,phases=fixture()
        del samples[3].reference_points['left_knee']
        result=analyze_preparation_translation(samples,phases)
        self.assertEqual(metric(result,'hip_center_dx_torso')['status'],'measured')
        self.assertEqual(metric(result,'right_knee_flexion_change_deg')['status'],'measured')
        left=metric(result,'left_knee_flexion_change_deg')
        self.assertEqual(left['status'],'partial')
        self.assertEqual([w['source_frames'] for w in left['windows']],[[0,1,2],[4,5,6]])

    def test_reference_or_frame_changes_never_join_windows(self):
        for defect in ('track','selection_epoch','camera_reference_epoch','frame_index'):
            samples,phases=fixture()
            setattr(samples[3],defect,99)
            result=analyze_preparation_translation(samples,phases)
            row=metric(result,'hip_center_dx_torso')
            self.assertFalse(row['full_phase_coverage'],defect)
            self.assertTrue(all(len(w['source_frames'])==3 for w in row['windows']),defect)

    def test_unverified_camera_fps_or_missing_confidence_does_not_supply_motion(self):
        for defect in ('camera','fps','confidence','overscore','huge_score'):
            samples,phases=fixture()
            for sample in samples:
                if defect=='camera': sample.camera_compensated=False
                elif defect=='fps':sample.timestamp_source='fps_fallback'
                elif defect=='confidence':sample.point_confidences={}
                else:sample.point_confidences={name:1.2 if defect=='overscore' else 10**1000 for name in sample.reference_points}
            self.assertEqual(analyze_preparation_translation(samples,phases)['status'],'unavailable',defect)

    def test_sample_reference_points_use_validated_camera_transform_before_translation(self):
        samples,phases=fixture()
        base={**samples[0].reference_points,'left_shoulder':(270,250),'right_shoulder':(330,250)}
        corrected=[]
        for i in range(7):
            pan=3*i
            pose={'person_track_id':1,'keypoints':[{'name':name,'x_px':x+pan,'y_px':y,'confidence':.9} for name,(x,y) in base.items()]}
            frame={'index':i,'processed_index':i,'timestamp_ms':i*40,'timestamp_source':'decoder_pts',
                'camera_motion_status':'compensated','camera_motion':{'compensation_valid':True,'reference_epoch':0,
                'matrix_to_reference':[[1,0,-pan],[0,1,0]]}}
            sample=_sample(pose,frame)
            self.assertEqual(sample.reference_points['left_hip'],base['left_hip'])
            corrected.append(sample)
        row=metric(analyze_preparation_translation(corrected,phases),'hip_center_dx_torso')['windows'][0]
        self.assertEqual(row['value'],0.)
        frame['camera_motion']['matrix_to_reference']=[[2,1,0],[0,1,0]]
        self.assertIsNone(_sample(pose,frame))

    def test_sam_null_confidence_uses_absolute_pts_and_preserves_relative_playback(self):
        samples,phases=fixture()
        for sample in samples:
            sample.pose_source='sam3d'; sample.observation_semantics='monocular_model_estimate';sample.point_confidences={}
            sample.source_binding={'video_sha256':'a'*64,'pts':75000+sample.time,'time_base_num':1,'time_base_den':1000,
                'timestamp_ms':75000+sample.time,'frame_index':sample.frame_index,'processed_index':sample.frame_index,
                'person_track_id':1,'selection_epoch':0,'camera_reference_epoch':0}
        result=analyze_preparation_translation(samples,phases)
        row=metric(result,'hip_center_mean_velocity_x_torso_per_s')['windows'][0]
        self.assertAlmostEqual(row['value'],1.)
        self.assertEqual(row['source_timestamps_ms'][0],75000)
        self.assertEqual(row['video_timestamps_ms'][0],0)
        self.assertEqual(row['time_basis'],'bound_source_pts')
        self.assertFalse(row['measurement_provenance']['confidence_available'])
        self.assertFalse(row['measurement_provenance']['scoring_allowed'])
        changed=copy.deepcopy(samples)
        changed[3].source_binding['pts']+=1000
        changed[3].source_binding['timestamp_ms']+=1000
        self.assertFalse(metric(analyze_preparation_translation(changed,phases),'hip_center_dx_torso')['full_phase_coverage'])
        for key,value in [('video_sha256','invalid'),('person_track_id',2),('camera_reference_epoch',8),('timestamp_ms',999),('frame_index',99)]:
            changed=copy.deepcopy(samples)
            for sample in changed:sample.source_binding[key]=value
            self.assertEqual(analyze_preparation_translation(changed,phases)['status'],'unavailable',key)


if __name__=='__main__':unittest.main()
