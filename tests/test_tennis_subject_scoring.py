from copy import deepcopy
import unittest

from rallymate_service.tennis_assessment import analyze_tennis_assessment
from rallymate_service.tennis_source_motion import source_motion_by_frame, verified_action_context
from rallymate_service.tennis_subjects import reviewed_subject_timeline
from tests.test_tennis_assessment import inputs, episodes


SHA = 'a' * 64


def context(candidates, technique='baseline_two_hand_backhand'):
    return {'version': 'tennis-action-context-v1', 'video_sha256': SHA, 'actions': [
        {**{k: e[k] for k in ('episode_id', 'start_ms', 'peak_ms', 'end_ms', 'person_track_id')},
         'status': 'reviewed', 'method': 'agent_visual_review', 'reviewer': 'test',
         'evidence': {'frames': [e['start_ms'], e['peak_ms'], e['end_ms']]}, 'technique': technique}
        for e in candidates]}


def source_frames(records):
    return [{'frame': {**{k: r[k] for k in ('pts', 'time_base_num', 'time_base_den', 'video_sha256')}, 'index': r['frame_index']},
             'camera_motion': {'compensation_valid': True, 'reference_epoch': 0,
                               'matrix_to_reference': [[1, 0, 0], [0, 1, 0]]}} for r in records]


class SubjectScoringTests(unittest.TestCase):
    def test_reviewed_reselection_requires_same_original_source_artifacts(self):
        document = {'version': 'tennis-subject-selection-v1', 'source_video_sha256': SHA,
                    'source_artifact_sha256': {'sam3d_frames.jsonl': 'b' * 64, 'frames.jsonl': 'c' * 64},
                    'timeline': inputs()[0]}
        args = {'video_sha256': SHA, 'native_sha256': 'b' * 64, 'frames_sha256': 'c' * 64}
        self.assertEqual(reviewed_subject_timeline(document, **args), document['timeline'])
        for key in args:
            with self.assertRaisesRegex(ValueError, 'source_mismatch'):
                reviewed_subject_timeline(document, **{**args, key: 'f' * 64})

    def separated(self, linked=False):
        timeline, records = inputs()
        candidates = episodes()
        for selected, native in zip(timeline[30:], records[30:]):
            selected['source_track_id'] = native['person_track_id'] = 2
            native['selection_epoch'] = 1
        for e in candidates[1:]:
            e['person_track_id'] = 2
            e['continuity']['source_selection_epoch'] = 1
        if linked:
            for row in timeline:
                row['subject_identity'] = {'version': 'subject-lock-v1', 'subject_id': 'target',
                                           'source_track_id': row['source_track_id'], 'evidence': ['source_bound_identity'],
                                           'status': 'locked' if row['source_track_id'] == 1 else 'reidentified'}
        return timeline, records, candidates

    def report(self, timeline, records, candidates, **kw):
        return analyze_tennis_assessment(timeline, records, video_sha256=SHA, motion_episodes=candidates, **kw)

    def test_constant_primary_id_does_not_merge_people(self):
        t, r, e = self.separated()
        report = self.report(t, r, e)
        self.assertGreater(report['candidate_accounting']['other_subject_count'], 0)
        self.assertEqual({x['source_track_id'] for x in report['episodes']}, {1})
        self.assertFalse(report['subject_scope']['cross_subject_aggregation'])
        other = [x for x in report['episode_validity'] if x['status'] == 'other_subject']
        self.assertGreater(len(other), 0)
        self.assertNotIn('rejected', {x['status'] for x in other})

    def test_verified_reidentification_can_join_different_tracks(self):
        t, r, e = self.separated(linked=True)
        report = self.report(t, r, e)
        self.assertEqual({x['source_track_id'] for x in report['episodes']}, {1, 2})
        self.assertEqual({x['subject_id'] for x in report['episodes']}, {'target'})

    def test_unknown_period_does_not_invalidate_clean_earlier_action(self):
        t, r, e = self.separated(linked=True)
        for row in t[30:]:
            row['subject_identity'] = {'version': 'subject-lock-v1', 'subject_id': None, 'status': 'unknown'}
        report = self.report(t, r, e)
        self.assertEqual(report['observation_count'], 1)
        self.assertEqual(report['episodes'][0]['episode_id'], e[0]['episode_id'])
        self.assertEqual(report['quality_reason_counts']['subject_identity_unverified'], len(t) - 30)

    def test_identity_proof_for_a_different_track_is_not_reused(self):
        t, r, e = self.separated(linked=True)
        for row in t[30:]:
            row['subject_identity']['source_track_id'] = 1
        report = self.report(t, r, e)
        self.assertEqual(report['observation_count'], 1)
        self.assertEqual(report['quality_reason_counts']['subject_identity_unverified'], len(t) - 30)

    def test_reviewed_incomplete_action_is_rejected_at_episode_level(self):
        t, r = inputs()
        e = episodes()
        review = context(e)
        review['actions'][0]['validity'] = {'status': 'invalid', 'reason_codes': ['preparation_only']}
        report = self.report(t, r, e, action_context=review)
        self.assertEqual(report['observation_count'], 2)
        self.assertEqual(report['episode_validity'][0]['status'], 'rejected')
        self.assertIn('preparation_only', report['episode_validity'][0]['review_reason_codes'])

    def test_unbound_review_never_changes_type_or_score(self):
        t, r = inputs()
        e = episodes()
        expected = self.report(t, r, e)
        for field in ('video_sha256', 'track', 'time', 'camera_reference_epoch', 'native_selection_epoch'):
            review = context(e)
            if field == 'video_sha256':
                review['video_sha256'] = 'b' * 64
            elif field in ('track', 'time'):
                for a in review['actions']:
                    a['person_track_id' if field == 'track' else 'start_ms'] += 1
            else:
                for a in review['actions']:
                    a[field] = 999
            self.assertEqual(self.report(t, r, e, action_context=review), expected)

    def test_patterns_contribute_to_actual_dimensions_and_not_only_explanation(self):
        t, r = inputs()
        e = episodes()
        # Independent, bound pixel trajectories expose opposite wrist motion.
        for row in r:
            for side in ('left', 'right'):
                p = row['keypoints_3d']['right_wrist']
                sign = 1 if side == 'right' else -1
                row['keypoints_2d'][side + '_wrist'] = [300 + sign * p[0] * 100, 300 + sign * p[2] * 100]
        source = source_frames(r)
        generic = self.report(t, r, e, source_frames=source)
        typed = self.report(t, r, e, source_frames=source, action_context=context(e))
        measured = [m for d in typed['dimensions'] for m in d['metrics'] if m['id'] == 'pattern_two_hand_coupling']
        self.assertTrue(measured)
        self.assertGreater(measured[0]['observation_count'], 0)
        self.assertNotEqual(typed['overall_score'], generic['overall_score'])
        self.assertFalse(typed['pattern_coverage']['full_source_technique_covered'])
        for episode in typed['episodes']:
            self.assertNotIn('follow_deceleration', episode['metrics'])
            if 'pattern_phase_continuity' in episode['metrics']:
                self.assertNotIn('follow_path', episode['metrics'])
        broken = deepcopy(source)
        for row in broken:
            row['camera_motion']['compensation_valid'] = False
        missing = self.report(t, r, e, source_frames=broken, action_context=context(e))
        self.assertFalse(any('pattern_two_hand_coupling' in x['metrics'] for x in missing['episodes']))

    def test_source_motion_rejects_wrong_camera_or_decoder_binding(self):
        _, r = inputs()
        source = source_frames(r)
        source[0]['frame']['pts'] += 1
        source[1]['camera_motion']['reference_epoch'] = 1
        source[2]['camera_motion']['matrix_to_reference'] = [[1, 0.3, 0], [0, 1, 0]]
        result = source_motion_by_frame(r, source, SHA)
        self.assertFalse(any(key[0] in {0, 1, 2} for key in result))
        self.assertEqual(len(result), len(r) - 3)

    def test_nonpixel_projection_and_conflicting_selected_epochs_are_rejected(self):
        t, r = inputs()
        source = source_frames(r)
        r[0]['coordinate_system_2d'] = 'normalized_coordinates'
        result = source_motion_by_frame(r, source, SHA)
        self.assertNotIn((0, 1, 0), result)
        for row in t:
            row['selection_epoch'] = 999
        report = self.report(t, r, episodes())
        self.assertEqual(report['observation_count'], 0)
        self.assertEqual(report['quality_reason_counts']['source_binding_mismatch'], len(t))

    def test_review_cannot_change_baseline_preparation_into_complete_serve(self):
        t, r = inputs()
        e = episodes()
        report = self.report(t, r, e, action_context=context(e, 'serve'))
        self.assertEqual(report['observation_count'], 0)
        self.assertEqual(report['candidate_accounting']['rejection_counts'], {'reviewed_technique_conflicts_with_detected_phases': 3})


if __name__ == '__main__':
    unittest.main()
