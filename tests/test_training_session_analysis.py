from copy import deepcopy
import math
import unittest

from rallymate_service.training_session_analysis import (
    build_training_session_analysis, compare_training_match, match_statistics, scale_ideal_skeleton,
)


SHA = 'a' * 64


def episode(identifier='e1', start=100, end=1000, epoch=0):
    return {'episode_id': identifier, 'start_ms': start, 'end_ms': end, 'subject_id': 'p1',
            'source_track_id': 1, 'source_selection_epoch': 0, 'camera_reference_epoch': epoch,
            'joint_kinematics': {'metrics': {'left_knee_flexion_deg': {'status': 'measured', 'unit': 'deg',
                'min': 10, 'max': 90, 'p10': 20, 'p90': 80, 'range': 80, 'observation_count': 4, 'source_frames': [3,4,5,6]}}}}


def assessment(episodes=None):
    return {'source_video_sha256': SHA, 'source_run_id': 'job-1', 'source_duration_ms': 3000,
            'subject_scope': {'selected_subject_id': 'p1'}, 'episodes': episodes if episodes is not None else [episode()]}


def point(identifier, **extra):
    return {'point_id': identifier, 'confirmed': True, 'source_video_sha256': SHA, 'subject_id': 'p1',
            'server_id': 'p1', 'winner_id': 'p1', 'serve_number': 1, **extra}


class TrainingSessionTests(unittest.TestCase):
    def test_merge_only_overlapping_source_identity_bound_clips(self):
        report = build_training_session_analysis(assessment([episode(), episode('e2', 800, 1400), episode('e3', 1600, 2000)]))
        clips = report['training']['active_clips']
        self.assertEqual([(c['start_ms'], c['end_ms']) for c in clips], [(100,1400),(1600,2000)])
        self.assertEqual(clips[0]['episode_ids'], ['e1','e2'])
        self.assertEqual(clips[0]['source_frames'], [3,4,5,6])
        self.assertEqual(report['training']['active_duration_ms'], 1700)
        self.assertEqual(report['training']['unselected_intervals'], [{'start_ms':0.,'end_ms':100}, {'start_ms':1400,'end_ms':1600}, {'start_ms':2000,'end_ms':3000.}])

    def test_camera_epoch_boundaries_never_merge_and_overlap_is_counted_once(self):
        report = build_training_session_analysis(assessment([episode(), episode('e2',800,1400,epoch=1)]))
        self.assertEqual(len(report['training']['active_clips']), 2)
        self.assertEqual(report['training']['active_duration_ms'], 1300)

    def test_missing_source_duration_never_invents_rest_or_video_length(self):
        source = assessment(); source.pop('source_duration_ms')
        result = build_training_session_analysis(source)['training']
        self.assertIsNone(result['source_duration_ms'])
        self.assertEqual(result['unselected_intervals'], [])
        self.assertEqual(len(result['active_clips']), 1)

    def test_real_numeric_scalar_timestamps_work_before_json_serialization(self):
        class NativeFloat(float):
            pass
        e = episode(start=NativeFloat(100),end=NativeFloat(1000))
        result = build_training_session_analysis(assessment([e]))
        self.assertEqual(len(result['training']['active_clips']), 1)
        self.assertEqual(result['training']['active_duration_ms'], 900)
        e['start_ms'] = True
        self.assertEqual(build_training_session_analysis(assessment([e]))['training']['active_clips'], [])

    def test_invalid_source_subject_and_out_of_bounds_episodes_are_reported(self):
        other = episode('other'); other['subject_id'] = 'other'
        far = episode('far', 4000,5000)
        missing = episode('missing'); missing['joint_kinematics'] = {}
        result = build_training_session_analysis(assessment([other,far,missing]))
        self.assertEqual(result['training']['active_clips'], [])
        self.assertEqual(result['training']['rejected_episode_reasons'], {'episode_subject_mismatch':1, 'episode_outside_source_duration':1, 'source_frame_binding_missing':1})
        source = assessment(); source['source_video_sha256'] = 'bad'
        self.assertEqual(build_training_session_analysis(source)['movement_ranges']['metrics'], {})

    def test_rom_preserves_actual_extrema_and_does_not_pool_episode_quantiles(self):
        second = episode('e2', 1500,2000)
        second['joint_kinematics']['metrics']['left_knee_flexion_deg'].update(min=20,max=70,p10=25,p90=65,range=50)
        report = build_training_session_analysis(assessment([episode(), second]))
        rom = report['movement_ranges']['metrics']['left_knee_flexion_deg']
        self.assertEqual((rom['observed_min'],rom['observed_max']), (10,90))
        self.assertEqual((rom['median_episode_p10'],rom['median_episode_p90']), (22.5,72.5))
        self.assertEqual(rom['median_episode_range'], 65)
        self.assertIn('not_clinical', report['movement_ranges']['semantics'])

    def test_confirmed_point_rates_use_explicit_denominators_and_unknowns_stay_unknown(self):
        rows = [point('p1', terminal_shot='ace'), point('p2', winner_id='opponent', terminal_shot='unforced_error',error_player_id='p1'),
                point('p3', serve_number=2), point('p4', winner_id=None), point('p5', confirmed=False),
                point('p6', server_id='opponent', terminal_shot='return_ace')]
        result = match_statistics(rows, source_video_sha256=SHA, subject_id='p1')
        self.assertEqual(result['first_serve_points_won'], {'numerator':1,'denominator':2,'percent':50.})
        self.assertEqual(result['second_serve_points_won']['percent'], 100)
        self.assertEqual(result['unknown_outcome_count'], 1)
        self.assertEqual(result['aces'], {'count':1,'classified_service_points':2})
        self.assertEqual(result['return_aces']['percent'], 100)
        self.assertEqual(result['unforced_errors']['count'], 1)
        self.assertIsNone(result['hold_rate']['percent'])
        self.assertEqual(result['rejected_records'], {'point_not_confirmed':1})

    def test_deduplicate_points_and_games_and_reject_conflicts(self):
        one = point('1', game_complete=True,game_id='g1',game_server_id='p1',game_winner_id='p1')
        two = point('2', game_complete=True,game_id='g1',game_server_id='p1',game_winner_id='p1')
        three = point('3', server_id='other',game_complete=True,game_id='g2',game_server_id='other',game_winner_id='p1')
        result = match_statistics([one,deepcopy(one),two,three,point('x'),point('x',winner_id='other')], source_video_sha256=SHA, subject_id='p1')
        self.assertEqual(result['confirmed_point_count'], 3)
        self.assertEqual(result['hold_rate'], {'numerator':1,'denominator':1,'percent':100.})
        self.assertEqual(result['break_rate']['percent'], 100)
        self.assertEqual(result['rejected_records'], {'duplicate_point_records':1,'conflicting_point_records':2})

    def test_no_confirmed_points_does_not_claim_zero_aces_or_losses(self):
        result = build_training_session_analysis(assessment())['match']
        self.assertIsNone(result['aces']['count'])
        self.assertIsNone(result['unforced_errors']['count'])
        self.assertIsNone(result['first_serve_points_won']['percent'])
        rows = [point('x', source_video_sha256='b'*64),point('y',subject_id='other')]
        self.assertEqual(match_statistics(rows, source_video_sha256=SHA,subject_id='p1')['confirmed_point_count'],0)

    def test_conflicting_terminal_outcomes_and_game_results_never_pollute_denominators(self):
        rows = [point('ace',terminal_shot='ace',winner_id='other'),
                point('return',terminal_shot='return_ace',server_id='p1',winner_id='p1'),
                point('error',terminal_shot='unforced_error',error_player_id='p1'),
                point('game1',game_complete=True,game_id='g1',game_server_id='p1',game_winner_id='p1'),
                point('game2',game_complete=True,game_id='g1',game_server_id='p1',game_winner_id='other'),
                point('unknown',winner_id=None), point('unknown-serve',serve_number=None)]
        result=match_statistics(rows,source_video_sha256=SHA,subject_id='p1')
        self.assertEqual(result['first_serve_points_won']['denominator'],2)
        self.assertEqual(result['first_serve_points_won']['numerator'],2)
        self.assertIsNone(result['aces']['count'])
        self.assertIsNone(result['hold_rate']['percent'])
        self.assertEqual(result['rejected_records'],{'terminal_outcome_conflict':3,'conflicting_game_records':1})

    def test_template_retargeting_preserves_directions_and_measured_bone_lengths(self):
        template = {'template_id':'coach-1','root':'hip','length_unit':'torso_ratio', 'points':{'hip':[0,0,0],'knee':[0,-1,0],'ankle':[1,-2,0]},
                    'bones':[{'start':'hip','end':'knee','length_id':'L1'},{'start':'knee','end':'ankle','length_id':'L2'}]}
        lengths = {'L1':{'value':2,'unit':'torso_ratio'},'L2':{'value':3,'unit':'torso_ratio'}}
        result = scale_ideal_skeleton(template,lengths)
        self.assertEqual(result['points']['knee'], [0,-2,0])
        self.assertAlmostEqual(math.dist(result['points']['ankle'],result['points']['knee']),3)
        self.assertEqual(template['points']['knee'],[0,-1,0])
        wrong = deepcopy(lengths); wrong['L1']['unit']='m'
        with self.assertRaises(ValueError): scale_ideal_skeleton(template,wrong)
        with self.assertRaises(ValueError): scale_ideal_skeleton(template,{})
        cycle = deepcopy(template); cycle['bones'][0]['start']='ankle'
        with self.assertRaises(ValueError): scale_ideal_skeleton(cycle,lengths)

    def test_match_comparison_requires_same_subject_and_matching_units(self):
        comparison = {'subject_id':'p1','same_subject_confirmed':True,'rating':3,
                      'training':{'subject_id':'p1','source_video_sha256':SHA,'metrics':{'rhythm_ms':{'value':500,'unit':'ms','sample_count':10}}},
                      'match':{'subject_id':'p1','source_video_sha256':'b'*64,'metrics':{'rhythm_ms':{'value':650,'unit':'ms','sample_count':8}}}}
        report=compare_training_match(comparison)
        self.assertEqual(report['metrics'][0]['delta'],150)
        self.assertEqual(report['metrics'][0]['relative_change_percent'],30)
        self.assertEqual(report['mode'],'training_match_behavior_comparison')
        self.assertIsNone(report['psychological_score'])
        comparison['rating']=2.5
        self.assertEqual(compare_training_match(comparison)['mode'],'basic_habits')
        comparison['match']['metrics']['rhythm_ms']['unit']='s'
        self.assertEqual(compare_training_match(comparison)['metrics'],[])
        comparison['match']['subject_id']='other'
        self.assertEqual(compare_training_match(comparison)['status'],'same_subject_source_evidence_required')


if __name__ == '__main__':
    unittest.main()
