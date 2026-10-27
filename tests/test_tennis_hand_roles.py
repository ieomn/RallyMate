from copy import deepcopy
import math
from types import SimpleNamespace
import unittest

from rallymate_scoring.paired_motion import paired_wrist_phase_evidence
from rallymate_scoring.stroke_candidates import _normalization
from rallymate_service.tennis_assessment import analyze_tennis_assessment, _summary_episodes
from tests.test_tennis_assessment import inputs
from tests.test_tennis_candidate_binding import recognition
from tests import test_tennis_candidate_binding as binding_tests
from tests.test_tennis_subject_scoring import source_frames


SHA = 'a' * 64


def fixture():
    timeline, records = inputs()
    episode = binding_tests.TennisCandidateBindingTests().source_phased_episode()
    episode.update(motion_wrist='left', racket_hand='left', racket_hand_semantics='motion_wrist_legacy_alias')
    episode['continuity'].update(selection_epoch=7, normalization_basis='full_torso')
    for row in records:
        x = 300 + 120 * math.sin(row['timestamp_ms'] / 1000 * math.tau)
        row['keypoints_2d'].update(left_wrist=[x - 5, 260], right_wrist=[x + 5, 260])
        row['keypoints_3d']['left_wrist'] = [v + (-.05 if i == 0 else 0) for i, v in enumerate(row['keypoints_3d']['right_wrist'])]
    samples = []
    for row in records:
        center, scale, basis = _normalization(row['keypoints_2d'])
        samples.append(SimpleNamespace(time=row['timestamp_ms'], frame_index=row['frame_index'], track=1,
            selection_epoch=7, camera_reference_epoch=0, center=center, scale=scale, normalization_basis=basis,
            pose_source='sam3d', observation_semantics='monocular_model_estimate',
            source_binding={k: row[k] for k in ('video_sha256','frame_index','person_track_id','selection_epoch',
                            'camera_reference_epoch','pts','time_base_num','time_base_den','timestamp_ms')},
            points={name: ((p[0]-center[0])/scale, (p[1]-center[1])/scale) for name,p in row['keypoints_2d'].items()}))
    episode['paired_wrist_acceleration'] = paired_wrist_phase_evidence(samples,
        start_ms=episode['phases'][1]['start_ms'], end_ms=episode['peak_ms'], motion_wrist='left')
    assert episode['paired_wrist_acceleration']['status'] == 'supported'
    context = {'version':'tennis-action-context-v1','video_sha256':SHA,'actions':[
        {**{k:episode[k] for k in ('episode_id','start_ms','peak_ms','end_ms','person_track_id')},
         'status':'reviewed','method':'agent_visual_review','reviewer':'source-audit','evidence':{'frames':[0,440,640]},
         'technique':'baseline_two_hand_backhand','racket_hand':'right',
         'source_selection_epoch':0,'camera_reference_epoch':0}]}
    return timeline, records, episode, context


def report(timeline, records, episode, context, **kw):
    return analyze_tennis_assessment(timeline, records, video_sha256=SHA, source_run_id='roles-test',
        motion_episodes=[episode], source_frames=source_frames(records), action_context=context, **kw)


class TennisHandRoleTests(unittest.TestCase):
    def test_two_hand_roles_keep_phase_wrist_but_measure_reviewed_primary_hand(self):
        data = fixture(); result = report(*data)
        self.assertEqual(result['version'], 'tennis-action-assessment-v1.4.0')
        self.assertEqual(result['rubric']['version'], 'tennis-action-rubric-v1.3.0')
        self.assertEqual(result['observation_count'], 1)
        action = result['episodes'][0]; roles = action['hand_roles']
        self.assertEqual((roles['motion_wrist'], roles['measurement_hand'], roles['primary_racket_hand']), ('left','right','right'))
        self.assertEqual(roles['relationship'], 'verified_two_hand')
        self.assertEqual(roles['primary_hand_provenance'], {'method':'agent_visual_review','reviewer':'source-audit'})
        self.assertEqual(roles['paired_wrist_evidence']['verification_method'], 'recomputed_from_source_bound_native_image_observations')
        self.assertEqual([action[k] for k in ('start_ms','peak_ms','end_ms')], [data[2][k] for k in ('start_ms','peak_ms','end_ms')])
        same_side = deepcopy(data); same_side[2].update(motion_wrist='right', racket_hand='right')
        self.assertEqual(action['metrics'], report(*same_side)['episodes'][0]['metrics'])

    def test_single_hand_and_slice_cannot_claim_the_other_wrists_phase(self):
        for technique in ('baseline_forehand','baseline_one_hand_backhand','backhand_slice','forehand_slice'):
            data = fixture(); data[3]['actions'][0]['technique'] = technique
            actual = report(*data)
            self.assertEqual(actual['observation_count'], 0)
            self.assertIn('reviewed_hand_conflicts_with_motion_phases', actual['candidate_accounting']['rejection_counts'])

    def test_upstream_supported_flag_cannot_replace_original_bilateral_geometry(self):
        for change in ('stationary', 'opposed', 'separated', 'missing'):
            data = fixture()
            for row in data[1]:
                point = row['keypoints_2d']['left_wrist']
                if change == 'stationary': row['keypoints_2d']['right_wrist'] = [300,260]
                elif change == 'opposed': row['keypoints_2d']['right_wrist'] = [600-point[0],260]
                elif change == 'separated': row['keypoints_2d']['right_wrist'][1] += 120
                elif 440 <= row['timestamp_ms'] <= 640: row['keypoints_2d'].pop('right_wrist')
            actual = report(*data)
            self.assertEqual(actual['observation_count'], 0, change)

    def test_evidence_cannot_be_borrowed_from_another_window_frame_track_or_epoch(self):
        mutations = [lambda p:p.update(start_ms=400), lambda p:p.update(motion_wrist='right'),
            lambda p:p['source'].update(video_sha256='b'*64), lambda p:p['source'].update(person_track_id=2),
            lambda p:p['source'].update(source_selection_epoch=1), lambda p:p['source'].update(camera_reference_epoch=1),
            lambda p:p['sample_binding'][0].update(frame_index=0),
            lambda p:p['sample_binding'][0]['source_binding'].update(pts=0),
            lambda p:p['sample_binding'][0]['source_binding'].update(selection_epoch=1),
            lambda p:p['sample_binding'].pop()]
        for mutate in mutations:
            data = fixture(); mutate(data[2]['paired_wrist_acceleration'])
            self.assertEqual(report(*data)['observation_count'], 0)

    def test_a_bad_phase_wrist_cannot_be_ignored_when_scoring_the_other_hand(self):
        for side in ('left','right'):
            data = fixture(); data[1][13]['keypoints_3d'][side+'_wrist'][0] += 5
            actual = report(*data)
            self.assertEqual(actual['observation_count'], 0)
            self.assertIn('active_wrist_discontinuity_in_motion' if side=='right' else 'two_hand_phase_wrist_discontinuity',
                          actual['candidate_accounting']['rejection_counts'])

    def test_legacy_alias_conflict_is_not_a_valid_new_motion_wrist_contract(self):
        data=fixture(); data[2]['motion_wrist']='right'
        result=report(*data)
        self.assertEqual(result['candidate_accounting']['rejection_counts'], {'phase_authority_source_unverified':1})

    def test_unknown_hand_is_reported_as_motion_only_without_claiming_grip(self):
        data=fixture(); result=report(data[0],data[1],data[2],None)
        roles=result['episodes'][0]['hand_roles']
        self.assertEqual(roles['relationship'],'motion_only')
        self.assertIsNone(roles['primary_racket_hand'])
        self.assertEqual(roles['measurement_hand'],'left')

    def test_independent_automatic_racket_association_conflict_is_not_silently_ignored(self):
        data=fixture(); data[2]['hand_evidence']={'hand':'right','separated_wrist_racket_votes':{'right':8}}
        result=report(data[0],data[1],data[2],None)
        self.assertEqual(result['candidate_accounting']['rejection_counts'], {'racket_association_conflicts_with_motion_wrist':1})

    def test_legacy_refinement_cannot_export_an_unresolved_primary_motion_conflict(self):
        data=fixture(); data[2].pop('phase_authority')
        data[2]['classification']={'status':'rule_inferred','label':'two_handed_backhand'}
        data[2]['hand_evidence']={'hand':'right','separated_wrist_racket_votes':{'right':8}}
        result=report(data[0],data[1],data[2],None)
        self.assertEqual(result['candidate_accounting']['rejection_counts'], {'racket_association_conflicts_with_motion_wrist':1})

    def test_summary_keeps_role_proof_separate_from_classification(self):
        data=fixture(); item=recognition([data[2]])
        episode=item['motion_analysis']['families']['baseline']['episodes'][0]
        episode['evidence']['paired_wrist_acceleration']=data[2]['paired_wrist_acceleration']
        reduced=_summary_episodes({'action_recognition':item})[0]
        self.assertEqual(reduced['motion_wrist'],'left')
        self.assertEqual(reduced['paired_wrist_acceleration'],data[2]['paired_wrist_acceleration'])


if __name__ == '__main__':
    unittest.main()
