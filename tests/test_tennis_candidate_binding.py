from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from rallymate_service.tennis_assessment import analyze_tennis_assessment, build_tennis_assessment, _summary_episodes, _segments, _bound_windows, _wrist_series
from tests.test_tennis_assessment import inputs, episodes
from tests.test_tennis_subject_scoring import source_frames


SHA = 'a' * 64


def recognition(candidates):
    families = {}
    for raw in candidates:
        item = deepcopy(raw)
        item['evidence'] = {'process_continuity': item.pop('continuity'),
                            'return_context': item.pop('return_context', {}),
                            'stroke_classification': item.pop('stroke_classification', {})}
        families.setdefault(item['family'], {'episodes': []})['episodes'].append(item)
    return {'motion_analysis': {'families': families}}


def return_alias(original):
    return {**deepcopy(original), 'episode_id': original['episode_id'] + '-return', 'family': 'return',
            'classification': {'label': 'return_motion', 'status': 'rule_inferred'},
            'stroke_classification': {'label': 'forehand', 'status': 'rule_inferred'},
            'return_context': {'opponent_serve_id': 'other-serve', 'server_track_id': 9,
                               'ball_track_id': 20, 'ball_observations': 8,
                               'incoming_start_ms': original['peak_ms'] - 600,
                               'incoming_end_ms': original['peak_ms'] - 100}}


class TennisCandidateBindingTests(unittest.TestCase):
    def source_phased_episode(self):
        episode = deepcopy(episodes()[0])
        episode.update(phase_authority='source_bound_primary_motion', racket_hand='right',
                       measurement_provenance={'pose_source':'sam3d', 'observation_semantics':'monocular_model_estimate',
                                               'assessment_status':'measurement_only', 'scoring_allowed':False,
                                               'model_estimate_sample_count':29})
        return episode

    def test_bound_primary_motion_phases_are_not_retimed_using_another_coordinate_peak(self):
        timeline, records = inputs()
        episode = self.source_phased_episode()
        result = analyze_tennis_assessment(timeline, records, video_sha256=SHA, motion_episodes=[episode])
        self.assertEqual(result['observation_count'], 1)
        measured = result['episodes'][0]
        self.assertEqual([measured[k] for k in ('start_ms','peak_ms','end_ms')], [episode[k] for k in ('start_ms','peak_ms','end_ms')])
        self.assertEqual(measured['phase_refinement'], 'source_bound_primary_motion_phases')
        self.assertEqual(measured['anchor_semantics'], 'source_bound_image_plane_wrist_motion_peak_not_ball_contact')
        legacy = deepcopy(episode); del legacy['phase_authority']
        prior = analyze_tennis_assessment(timeline, records, video_sha256=SHA, motion_episodes=[legacy])['episodes'][0]
        self.assertNotEqual([measured[k] for k in ('start_ms','peak_ms','end_ms')], [prior[k] for k in ('start_ms','peak_ms','end_ms')])

    def test_phase_authority_flag_cannot_replace_source_proof_or_complete_phase_partition(self):
        timeline, records = inputs()
        for mutate in (lambda e:e.pop('measurement_provenance'),
                       lambda e:e['measurement_provenance'].update(pose_source='coco'),
                       lambda e:e['phases'][1].update(start_ms=200),
                       lambda e:e['phases'][2].update(status='not_measured')):
            episode = self.source_phased_episode(); mutate(episode)
            result = analyze_tennis_assessment(timeline, records, video_sha256=SHA, motion_episodes=[episode])
            self.assertEqual(result['observation_count'], 0)
            self.assertEqual(result['candidate_accounting']['rejection_counts'], {'phase_authority_source_unverified':1})

    def test_reviewed_other_hand_cannot_reuse_motion_phases_from_the_original_hand(self):
        timeline, records = inputs()
        episode = self.source_phased_episode()
        review = {'version':'tennis-action-context-v1', 'video_sha256':SHA, 'actions':[
            {**{key:episode[key] for key in ('episode_id','start_ms','peak_ms','end_ms','person_track_id')},
             'status':'reviewed','method':'agent_visual_review','reviewer':'test','evidence':['frames'],
             'technique':'baseline_forehand','racket_hand':'left'}]}
        result = analyze_tennis_assessment(timeline, records, video_sha256=SHA, motion_episodes=[episode], action_context=review)
        self.assertEqual(result['observation_count'],0)
        self.assertEqual(result['candidate_accounting']['rejection_counts'], {'reviewed_hand_conflicts_with_motion_phases':1})

    def test_bound_unresolved_hand_sequence_keeps_specific_unscorable_reason(self):
        timeline, records = inputs(); episode = self.source_phased_episode()
        reason = 'unresolved_racket_hand_with_competing_arm_sequence'
        episode['phase_ambiguity'] = {'reason_code':reason,'candidate_wrist':'right','competing_wrist':'left',
            'candidate_motion_peak_ms':episode['peak_ms'],'competing_motion_peak_ms':episode['peak_ms']+160}
        for phase in episode['phases']:
            phase.update(status='unavailable',start_ms=None,end_ms=None,reason_code=reason)
        reduced = _summary_episodes({'action_recognition':recognition([episode])})
        self.assertEqual(reduced[0]['phase_ambiguity'],episode['phase_ambiguity'])
        report=analyze_tennis_assessment(timeline,records,video_sha256=SHA,motion_episodes=reduced)
        self.assertEqual(report['observation_count'],0)
        self.assertEqual(report['candidate_accounting']['rejection_counts'],{reason:1})
        for mutate in (lambda e:e['phase_ambiguity'].update(competing_wrist='right'),
                       lambda e:e['phase_ambiguity'].update(competing_wrist=['left']),
                       lambda e:e['phase_ambiguity'].update(competing_motion_peak_ms=100000),
                       lambda e:e['continuity'].update(video_sha256='b'*64),
                       lambda e:e['measurement_provenance'].update(pose_source='coco')):
            bad=deepcopy(reduced[0]);mutate(bad)
            report=analyze_tennis_assessment(timeline,records,video_sha256=SHA,motion_episodes=[bad])
            self.assertEqual(report['candidate_accounting']['rejection_counts'],{'phase_authority_source_unverified':1})

    def test_non_active_wrist_jump_only_removes_metrics_that_depend_on_that_wrist(self):
        timeline, records = inputs()
        episode = self.source_phased_episode()
        for row in records:
            p=row['keypoints_3d']['right_wrist']
            row['keypoints_2d']['right_wrist']=[300+p[0]*100,300+p[2]*100]
            row['keypoints_2d']['left_wrist']=[310+p[0]*100,310+p[2]*100]
        review={'version':'tennis-action-context-v1','video_sha256':SHA,'actions':[
            {**{k:episode[k] for k in ('episode_id','start_ms','peak_ms','end_ms','person_track_id')},
             'status':'reviewed','method':'agent_visual_review','reviewer':'test','evidence':['source'],
             'technique':'baseline_two_hand_backhand','racket_hand':'right'}]}
        healthy=analyze_tennis_assessment(timeline,records,video_sha256=SHA,motion_episodes=[episode],source_frames=source_frames(records),action_context=review)
        self.assertIn('pattern_two_hand_coupling',healthy['episodes'][0]['metrics'])
        records[13]['keypoints_3d']['left_wrist'][0]+=5
        changed=analyze_tennis_assessment(timeline,records,video_sha256=SHA,motion_episodes=[episode],source_frames=source_frames(records),action_context=review)
        self.assertEqual(changed['observation_count'],1)
        action=changed['episodes'][0]
        self.assertEqual((action['start_ms'],action['end_ms']),(episode['start_ms'],episode['end_ms']))
        self.assertNotIn('pattern_two_hand_coupling',action['metrics'])
        self.assertEqual(action['metrics']['arm_space'],healthy['episodes'][0]['metrics']['arm_space'])
        self.assertIn('non_active_wrist_discontinuity_unmeasured',action['quality_reason_codes'])

    def test_bad_outer_neighbor_cannot_change_valid_center_during_smoothing(self):
        import numpy as np
        for bad_index in (0,2):
            rows=[{'local':{'right_wrist':[v,v,v],'left_wrist':[1,1,1]},'wrist_unavailable':('right',) if i==bad_index else ()}
                  for i,v in enumerate((10,0,2))]
            original=deepcopy(rows)
            measured=_wrist_series(rows,'right')
            np.testing.assert_array_equal(measured[1],[0,0,0])
            self.assertTrue(np.isnan(measured[bad_index]).all())
            np.testing.assert_array_equal(_wrist_series(rows,'left'),np.ones((3,3)))
            self.assertEqual(rows,original)
        rows=[{'local':{'right_wrist':[v,v,v]},'wrist_unavailable':('right',) if i==1 else ()}
              for i,v in enumerate((1,100,2))]
        self.assertTrue(np.isnan(_wrist_series(rows,'right')[1]).all())

    def test_bound_external_peak_requires_native_times_and_structured_local_motion_diagnostic(self):
        timeline,records=inputs();episode=self.source_phased_episode()
        reason='external_peak_motion_geometry_unverified'
        episode['phase_ambiguity']={'reason_code':reason,'candidate_wrist':'right','candidate_start_ms':800,'candidate_end_ms':1040,
            'candidate_motion_peak_ms':episode['peak_ms'],'motion_window_start_ms':episode['start_ms'],'motion_window_end_ms':episode['end_ms'],
            'local_sweep_geometry':{'status':'not_supported','tested_window_count':8,'crosses_body_midline':False,
                'horizontal_excursion_torso_units':.3,'vertical_range_torso_units':.47,'path_torso_units':.8,'minimum_wrist_y_torso_units':.1}}
        for phase in episode['phases']:phase.update(status='unavailable',start_ms=None,end_ms=None,reason_code=reason)
        result=analyze_tennis_assessment(timeline,records,video_sha256=SHA,motion_episodes=[episode])
        self.assertEqual(result['candidate_accounting']['rejection_counts'],{reason:1})
        for mutate in (lambda e:e['phase_ambiguity'].update(candidate_start_ms=400),
                       lambda e:e['phase_ambiguity'].update(candidate_end_ms=1045),
                       lambda e:e['phase_ambiguity'].update(motion_window_end_ms=100000),
                       lambda e:e['phase_ambiguity']['local_sweep_geometry'].update(status='supported'),
                       lambda e:e['phase_ambiguity']['local_sweep_geometry'].update(path_torso_units=float('nan')),
                       lambda e:e['phase_ambiguity']['local_sweep_geometry'].update(tested_window_count=-1)):
            bad=deepcopy(episode);mutate(bad)
            result=analyze_tennis_assessment(timeline,records,video_sha256=SHA,motion_episodes=[bad])
            self.assertEqual(result['candidate_accounting']['rejection_counts'],{'phase_authority_source_unverified':1})

    def test_active_wrist_jump_keeps_full_source_action_unavailable(self):
        timeline,records=inputs();episode=self.source_phased_episode()
        records[13]['keypoints_3d']['right_wrist'][0]+=5
        result=analyze_tennis_assessment(timeline,records,video_sha256=SHA,motion_episodes=[episode])
        self.assertEqual(result['observation_count'],0)
        self.assertEqual(result['candidate_accounting']['rejection_counts'],{'active_wrist_discontinuity_in_motion':1})

    def test_torso_scale_break_still_prevents_full_source_phase_scoring(self):
        timeline,records=inputs();episode=self.source_phased_episode()
        for joint,point in records[13]['keypoints_3d'].items():
            records[13]['keypoints_3d'][joint]=[value*4 for value in point]
        result=analyze_tennis_assessment(timeline,records,video_sha256=SHA,motion_episodes=[episode])
        self.assertEqual(result['observation_count'],0)
        self.assertGreater(result['quality_reason_counts']['geometry_discontinuity'],0)

    def test_first_or_last_known_bad_active_endpoint_rejects_full_window(self):
        from collections import Counter
        timeline,records=inputs();episode=self.source_phased_episode()
        for boundary in ('start_ms','end_ms'):
            segments,_=_segments(timeline,records,SHA)
            row=next(row for segment in segments for row in segment if row['time']==episode[boundary])
            row['wrist_unavailable']=('right',)
            rejected=Counter()
            self.assertEqual(_bound_windows(segments,[episode],SHA,rejected),[])
            self.assertEqual(dict(rejected),{'active_wrist_discontinuity_in_motion':1})

    def test_verified_return_replaces_same_baseline_action_once(self):
        timeline, records = inputs()
        baseline = episodes()[0]
        alias = return_alias(baseline)
        candidates = _summary_episodes({'action_recognition': recognition([baseline, alias])})
        result = analyze_tennis_assessment(timeline, records, video_sha256=SHA, motion_episodes=candidates)
        self.assertEqual(result['observation_count'], 1)
        self.assertEqual(result['episodes'][0]['episode_id'], alias['episode_id'])
        self.assertEqual(result['episodes'][0]['family'], 'return')
        self.assertEqual(result['episodes'][0]['motion_pattern']['profile_id'], 'return_forehand')
        self.assertEqual(result['episodes'][0]['motion_pattern']['classification_provenance']['method'], 'source_bound_rule_inferred')
        self.assertEqual(result['candidate_accounting']['rejection_counts'], {'same_action_represented_by_verified_return': 1})

    def test_return_label_suffix_and_manual_type_cannot_replace_incoming_evidence(self):
        timeline, records = inputs()
        baseline = episodes()[0]
        for change in ('missing', 'same_server', 'too_few_ball_frames', 'wrong_time'):
            alias = return_alias(baseline)
            if change == 'missing':
                alias['return_context'] = {}
            elif change == 'same_server':
                alias['return_context']['server_track_id'] = baseline['person_track_id']
            elif change == 'too_few_ball_frames':
                alias['return_context']['ball_observations'] = 2
            else:
                alias['return_context']['incoming_end_ms'] += 1000
            result = analyze_tennis_assessment(timeline, records, video_sha256=SHA, motion_episodes=[baseline, alias])
            self.assertEqual([e['episode_id'] for e in result['episodes']], [baseline['episode_id']])
            self.assertEqual(result['candidate_accounting']['rejection_counts'], {'return_context_unverified': 1})

    def test_return_alias_cannot_suppress_another_source_or_phase(self):
        timeline, records = inputs()
        baseline = episodes()[0]
        for key, value in (('person_track_id', 99), ('peak_ms', baseline['peak_ms'] + 20),
                           ('continuity', {**baseline['continuity'], 'source_selection_epoch': 99})):
            alias = return_alias(baseline)
            alias[key] = value
            result = analyze_tennis_assessment(timeline, records, video_sha256=SHA, motion_episodes=[baseline, alias])
            self.assertEqual([e['episode_id'] for e in result['episodes']], [baseline['episode_id']])
            self.assertNotIn('same_action_represented_by_verified_return', result['candidate_accounting']['rejection_counts'])

    def test_reviewed_sidecar_redetects_from_same_timeline_and_preserves_original_files(self):
        timeline, records = inputs()
        for row in timeline:
            row['subject_identity'] = {'version':'subject-lock-v1', 'subject_id':'test-target',
                                      'source_track_id':1, 'status':'locked', 'evidence':['test']}
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / 'test-run'
            directory.mkdir()
            historical = deepcopy(timeline)
            for row in historical:
                row['selection_status'] = 'missing'
            for filename, data in (('primary-player.jsonl', historical), ('sam3d_frames.jsonl', records),
                                   ('frames.jsonl', source_frames(records))):
                (directory / filename).write_text(''.join(json.dumps(r) + '\n' for r in data), encoding='utf-8')
            original_hashes = {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in directory.iterdir()}
            sidecar = {'version':'tennis-subject-selection-v1', 'source_video_sha256':SHA,
                       'source_artifact_sha256':{name:original_hashes[name] for name in ('sam3d_frames.jsonl','frames.jsonl')},
                       'timeline':timeline}
            (directory / 'tennis-subject-selection.json').write_text(json.dumps(sidecar), encoding='utf-8')
            summary = {'job_id':'test-run', 'minimum_scoring_loop':{'provenance':{'video_sha256':SHA}},
                       'action_recognition':recognition([{**episodes()[0], 'person_track_id':999}])}
            saved = deepcopy(summary)
            with patch('rallymate_service.tennis_assessment.detect_stroke_candidates', return_value=recognition(episodes())) as detector:
                result = build_tennis_assessment(summary, directory)
                self.assertEqual(detector.call_count, 1)
                self.assertEqual(detector.call_args.kwargs['primary_timeline'], timeline)
                self.assertEqual(detector.call_args.args[0], source_frames(records))
            self.assertGreater(result['observation_count'], 0)
            self.assertEqual(summary, saved)
            self.assertEqual(original_hashes, {name:hashlib.sha256((directory/name).read_bytes()).hexdigest() for name in original_hashes})
            sidecar['source_artifact_sha256']['frames.jsonl'] = 'b' * 64
            (directory / 'tennis-subject-selection.json').write_text(json.dumps(sidecar), encoding='utf-8')
            with patch('rallymate_service.tennis_assessment.detect_stroke_candidates') as detector:
                self.assertEqual(build_tennis_assessment(summary, directory)['status'], 'unavailable')
                detector.assert_not_called()


if __name__ == '__main__':
    unittest.main()
