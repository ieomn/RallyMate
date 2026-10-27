from copy import deepcopy
import unittest

from scripts.validate_tennis_manual_patterns import extract_window, validate_return_evidence


class ManualPatternWindowTests(unittest.TestCase):
    def return_fixture(self):
        sha='a'*64
        frames=[{'frame':{'index':i,'timestamp_ms':t,'timestamp_source':'decoder_pts','pts':t,'time_base_num':1,'time_base_den':1000,
                          'width':1000,'height':600,'video_sha256':sha},'camera_motion':{'reference_epoch':0}}
                for i,t in enumerate((100,200,350,400,450))]
        annotation={'technique':'return_forehand','subject_id':'subject-1','person_track_id':1,'camera_reference_epoch':0,'peak_ms':500,
                    'method':'agent_visual_review','reviewer':'source-review',
                    'return_evidence':{'version':'manual-return-context-v1','source_video_sha256':sha,
                        'receiver_subject_id':'subject-1','receiver_source_track_id':1,'server_subject_description':'far court player',
                        'relationship_zh':'源图中对方发球后，来球接近近端接发者。',
                        'opponent_serve_frames':[{'source_frame_index':i,'timestamp_ms':frames[i]['frame']['timestamp_ms'],
                                                  'stage':stage,'server_bbox_px':[100,100,160,220]} for i,stage in enumerate(('preparation','overhead_swing'))],
                        'incoming_ball_frames':[{'source_frame_index':i,'timestamp_ms':frames[i]['frame']['timestamp_ms'],'point_px':[100+i*60,220]}
                                                for i in (2,3,4)]}}
        return annotation,frames,sha

    def test_manual_return_evidence_binds_source_clock_and_stays_a_visual_review(self):
        annotation,frames,sha=self.return_fixture()
        result=validate_return_evidence(annotation,frames,sha)
        self.assertEqual(result['status'],'review_bound')
        self.assertFalse(result['automatic_return_context'])
        self.assertEqual(result['incoming_ball_frames'][-1]['source_pts'],450)
        self.assertEqual(result['method'],'agent_visual_review')

    def test_return_technique_string_without_visual_event_evidence_is_not_verified(self):
        annotation,frames,sha=self.return_fixture()
        del annotation['return_evidence']
        self.assertEqual(validate_return_evidence(annotation,frames,sha)['status'],'not_measured')
        annotation['technique']='forehand_slice'
        self.assertEqual(validate_return_evidence(annotation,frames,sha)['status'],'not_applicable')

    def test_manual_return_wrong_source_pixels_time_and_insufficient_ball_frames_fail_closed(self):
        mutations=(lambda a,f:a['return_evidence'].update(source_video_sha256='b'*64),
                   lambda a,f:a['return_evidence'].update(receiver_source_track_id=2),
                   lambda a,f:a['return_evidence']['incoming_ball_frames'].pop(),
                   lambda a,f:a['return_evidence']['incoming_ball_frames'][1].update(point_px=[1001,10]),
                   lambda a,f:a['return_evidence']['incoming_ball_frames'][1].update(timestamp_ms=401.5),
                   lambda a,f:f[2]['frame'].update(pts=None),
                   lambda a,f:f[2]['camera_motion'].update(reference_epoch=1),
                   lambda a,f:f.append(deepcopy(f[2])))
        for mutate in mutations:
            annotation,frames,sha=self.return_fixture();mutate(annotation,frames)
            self.assertEqual(validate_return_evidence(annotation,frames,sha)['status'],'not_measured')

    def fixture(self):
        rows = [{"time": i * 20., "frame": i, "track": 1, "reference": (0, 0), "subject_id": "subject-1"} for i in range(51)]
        annotation = {"start_ms": 0, "prep_end_ms": 350, "peak_ms": 600, "end_ms": 1000,
                      "person_track_id": 1, "native_selection_epoch": 0, "camera_reference_epoch": 0, "subject_id": "subject-1"}
        return rows, annotation

    def test_manual_times_are_quantized_only_to_actual_observations(self):
        rows, annotation = self.fixture()
        selected, reason = extract_window([rows], annotation)
        self.assertIsNone(reason)
        observed, prep, peak, resolution = selected
        self.assertEqual(observed[prep - 1]["time"], 340)
        self.assertEqual(observed[peak]["time"], 600)
        self.assertEqual(resolution, 20)
        self.assertEqual(len(observed), 51)

    def test_fragmented_window_cannot_be_stitched_across_a_subject_break(self):
        rows, annotation = self.fixture()
        self.assertIsNone(extract_window([rows[:30], rows[30:]], annotation)[0])
        for field, value in (("person_track_id", 2), ("native_selection_epoch", 1), ("camera_reference_epoch", 1), ("subject_id", "other")):
            changed = {**annotation, field: value}
            self.assertIsNone(extract_window([rows], changed)[0])

    def test_incomplete_and_duplicate_manual_windows_are_missing(self):
        rows, annotation = self.fixture()
        self.assertIsNone(extract_window([rows[:-5]], annotation)[0])
        self.assertEqual(extract_window([rows, deepcopy(rows)], annotation)[1], "manual_window_has_ambiguous_source_segments")

    def test_manual_window_does_not_expand_to_find_a_better_pattern(self):
        rows, annotation = self.fixture()
        annotation.update(start_ms=100, prep_end_ms=350, end_ms=900)
        selected, reason = extract_window([rows], annotation)
        self.assertIsNone(reason)
        self.assertEqual((selected[0][0]["time"], selected[0][-1]["time"]), (100, 900))

    def test_manual_active_wrist_jump_rejects_whole_window_other_wrist_does_not(self):
        rows, annotation = self.fixture()
        annotation['racket_hand'] = 'right'
        rows[20]['wrist_discontinuities'] = ('left',)
        self.assertIsNone(extract_window([rows], annotation)[1])
        rows[20]['wrist_discontinuities'] = ('right',)
        self.assertEqual(extract_window([rows], annotation), (None, 'active_wrist_discontinuity_in_motion'))

    def test_joint_scoped_mask_omits_both_pair_endpoints_without_mutating_source(self):
        from rallymate_service.tennis_assessment import _joint_scoped_window
        rows = [{'frame':i,'local':{'left_wrist':[1,2,3],'right_wrist':[4,5,6]},'left_reach':.8,
                 'wrist_discontinuities':('left',) if i==1 else ()} for i in range(3)]
        observations = [{'frame':i,'points':{'left_wrist':[1,2],'right_wrist':[3,4]}} for i in range(3)]
        original_rows, original_observations = deepcopy(rows), deepcopy(observations)
        scoped, camera, missing = _joint_scoped_window(rows, observations)
        self.assertEqual(missing,{0:['left_wrist'],1:['left_wrist']})
        self.assertIsNone(scoped[0]['local']['left_wrist'])
        self.assertIsNone(scoped[1]['left_reach'])
        self.assertNotIn('left_wrist',camera[1]['points'])
        self.assertEqual(camera[2]['points'],observations[2]['points'])
        self.assertEqual(rows,original_rows)
        self.assertEqual(observations,original_observations)
        # The preceding observation is outside this subwindow; the remaining
        # known bad endpoint must still be omitted.
        scoped, camera, missing = _joint_scoped_window(rows[1:], observations[1:])
        self.assertEqual(missing,{1:['left_wrist']})
        self.assertIsNone(scoped[0]['local']['left_wrist'])
        self.assertNotIn('left_wrist',camera[0]['points'])

    def test_manual_first_and_last_known_bad_wrist_endpoints_cannot_escape(self):
        for index in (0, 50):
            rows, annotation = self.fixture()
            annotation['racket_hand'] = 'right'
            rows[index]['wrist_unavailable'] = ('right',)
            self.assertEqual(extract_window([rows],annotation),(None,'active_wrist_discontinuity_in_motion'))


if __name__ == "__main__":
    unittest.main()
