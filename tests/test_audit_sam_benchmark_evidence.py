"""Audit bookkeeping tests; synthetic coordinates do not validate model accuracy."""
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.audit_sam_benchmark_evidence import (
    BODY, BINDING, RTMPOSE_RAW_SCORE_SEMANTICS, audit_embedded_baseline, audit_ledger, decoder_mode_valid, main, manifest_scope, paired_disagreement, raw_points,
)


def fixture(count=3):
    rows = []
    for index in range(count):
        points = {name: [50., 60.] for name in BODY}
        points.update(left_shoulder=[40., 40.], right_shoulder=[60., 40.],
                      left_hip=[40., 70.], right_hip=[60., 70.], right_wrist=[70. + index, 40.])
        rows.append(dict(video_sha256='a'*64, frame_index=index, processed_index=index,
            pts=index*20, time_base_num=1, time_base_den=1000, timestamp_ms=index*20,
            person_track_id=1, selection_epoch=0, camera_reference_epoch=0,
            image_size=[100,100], bbox_px=[20,10,90,95], rgb_sha256=str(index)*64,
            model_kind='sam3d', status='ok', confidence=None, keypoint_scores=None,
            confidence_semantics='not_provided_by_sam3d', coordinate_system_2d='image_pixels_x_right_y_down',
            keypoints_2d=points))
    manifest = {'requests':[{key:copy.deepcopy(row[key]) for key in BINDING} for row in rows], 'protocol':{'stride':1}}
    return manifest, rows


class BenchmarkEvidenceAuditTests(unittest.TestCase):
    def test_linear_motion_has_no_three_frame_residual(self):
        manifest, rows = fixture()
        audit = audit_ledger(manifest, rows)
        self.assertEqual(audit['binding_status'],'passed')
        wrist = audit['per_joint_temporal']['right_wrist']
        self.assertEqual(wrist['step_torso']['count'],2)
        self.assertAlmostEqual(wrist['step_torso']['mean'],1/30)
        self.assertAlmostEqual(wrist['three_frame_linear_residual_torso']['max'],0)
        self.assertFalse(audit['actual_identity_correctness_verified'])
        self.assertEqual(audit['eligible_joint_frame_counts']['right_wrist'],3)

    def test_out_of_frame_or_nonfinite_raw_points_not_clipped(self):
        _, rows = fixture(1)
        rows[0]['keypoints_2d'].update(right_wrist=[100,40],left_wrist=[-1,40],nose=[float('nan'),40])
        points, counts = raw_points(rows[0])
        self.assertNotIn('right_wrist',points)
        self.assertNotIn('left_wrist',points)
        self.assertNotIn('nose',points)
        self.assertEqual(counts['out_of_frame'],2)
        self.assertEqual(counts['missing_or_nonfinite'],1)

    def test_confidence_gate_retained_for_rtmpose_unknown_sam_not_filled(self):
        _, rows = fixture(1)
        row=rows[0]
        self.assertEqual(len(raw_points(row)[0]),17)
        row['model_kind']='rtmpose'
        row['keypoint_scores']={name: None for name in BODY}
        self.assertEqual(len(raw_points(row)[0]),0)
        row['keypoint_scores'].update(right_wrist=.35,left_wrist=.349)
        self.assertEqual(set(raw_points(row)[0]),{'right_wrist'})

    def test_only_explicit_rtmpose_raw_response_can_exceed_one_and_raw_is_preserved(self):
        _,rows=fixture(1); row=rows[0]
        row.update(model_kind='rtmpose',confidence_semantics=RTMPOSE_RAW_SCORE_SEMANTICS,keypoint_scores={name:.9 for name in BODY})
        row['keypoint_scores']['right_ear']=1.08
        original=copy.deepcopy(row)
        self.assertIn('right_ear',raw_points(row)[0])
        self.assertEqual(raw_points(row)[1]['declared_raw_response_above_one_eligible'],1)
        self.assertEqual(row,original)
        for value in (None,-1,float('nan'),float('inf'),True):
            row['keypoint_scores']['right_ear']=value
            self.assertNotIn('right_ear',raw_points(row)[0])
        row['keypoint_scores']['right_ear']=1.08
        for semantics in (None,'probability','unknown'):
            row['confidence_semantics']=semantics
            self.assertNotIn('right_ear',raw_points(row)[0])

    def test_full_decoder_accepts_repeated_body_hint_with_exact_two_hand_calls(self):
        for body,hand,expected in ((1,2,True),(2,2,True),(0,2,False),(2,1,False),(2,3,False),(True,2,False)):
            trace={'requested_mode':'full','mode_assertion_passed':True,'full_hand_boxes_returned':True,'observed_decoder_calls':{'body':body,'hand':hand}}
            self.assertEqual(decoder_mode_valid(trace,'full'),expected)
        manifest,rows=fixture(1);manifest['video']={'sha256':'a'*64}
        rows[0]['execution_trace']={'requested_mode':'full','mode_assertion_passed':True,'full_hand_boxes_returned':True,'observed_decoder_calls':{'body':2,'hand':2}}
        report={'kind':'sam3d','model':{'mode':'full'},'manifest_sha256':'same','video':manifest['video'],'counts':{'requested':1,'ok':1}}
        self.assertEqual(audit_ledger(manifest,rows,report=report,manifest_sha256='same')['binding_status'],'passed')
        for hand_boxes in (False, None, 1):
            rows[0]['execution_trace']['full_hand_boxes_returned'] = hand_boxes
            self.assertFalse(decoder_mode_valid(rows[0]['execution_trace'], 'full'))

    def test_paired_residual_uses_same_joint_triples_and_common_image_scale(self):
        _,first=fixture();second=copy.deepcopy(first)
        for i,(a,b) in enumerate(zip(first,second)):
            a['keypoints_2d']['right_wrist']=[70+i*i,40]
            b['keypoints_2d']['right_wrist']=[70+2*i*i,40]
        del second[1]['keypoints_2d']['left_ear']
        residual=paired_disagreement(first,second)['matched_three_frame_residual_image_diagonal']
        self.assertEqual(residual['shared_frame_triples'],1)
        self.assertEqual(residual['shared_joint_triplet_samples'],16)
        self.assertEqual(residual['per_joint']['left_ear']['first']['count'],0)
        self.assertEqual(residual['per_joint']['left_ear']['second']['count'],0)
        self.assertAlmostEqual(residual['per_joint']['right_wrist']['first']['mean'],1/(20000**.5))
        self.assertAlmostEqual(residual['per_joint']['right_wrist']['second']['mean'],2/(20000**.5))
        self.assertEqual(residual['all_joints']['first']['count'],residual['all_joints']['second']['count'])
        second[1]['selection_epoch']=1
        self.assertEqual(paired_disagreement(first,second)['matched_three_frame_residual_image_diagonal']['shared_frame_triples'],0)

    def test_full_manifest_denominator_retains_skips_tracks_and_epoch_breaks(self):
        manifest,rows=fixture(5)
        for index,(request,row) in enumerate(zip(manifest['requests'],rows)):
            request['request_status']='ready'
            if index>=2:
                request.update(person_track_id=2,selection_epoch=1);row.update(person_track_id=2,selection_epoch=1)
        manifest['requests'][2]['request_status']='skipped'
        manifest['requests'][2]['request_reason']='identity_ambiguous'
        rows[2].update(status='skipped',reason='identity_ambiguous')
        audit=audit_ledger(manifest,rows)
        self.assertEqual(audit['binding_status'],'passed')
        self.assertEqual(audit['coverage_denominator'],{'all_manifest_requests_including_skipped':5,'source_valid_ok_fraction':.8})
        self.assertEqual(audit['counts']['manifest_request_statuses'],{'ready':4,'skipped':1})
        self.assertEqual(audit['counts']['skipped_reasons'],{'identity_ambiguous':1})
        self.assertEqual(audit['manifest_source_tracks'],[1,2])
        self.assertEqual(audit['manifest_selection_epochs'],[0,1])
        scope=manifest_scope(manifest)
        self.assertEqual(scope['total_requested_frames'],5)
        self.assertEqual([item['count'] for item in scope['segments']],[2,1,2])
        self.assertEqual(scope['skip_reasons'],{'identity_ambiguous':1})
        self.assertEqual(audit['per_joint_temporal']['right_wrist']['step_torso']['count'],2)
        self.assertEqual(paired_disagreement(rows,rows)['matched_three_frame_residual_image_diagonal']['shared_frame_triples'],0)
        rows[2]['status']='ok'
        self.assertIn('manifest_skip_not_preserved',{item['code'] for item in audit_ledger(manifest,rows)['issues']})

    def test_duplicates_missing_frames_or_foreign_binding_exclude_evidence(self):
        manifest, rows = fixture()
        rows[2]['person_track_id']=9
        rows=[rows[0],copy.deepcopy(rows[0]),rows[2]]
        audit=audit_ledger(manifest,rows)
        codes={item['code'] for item in audit['issues']}
        self.assertTrue({'duplicate_ledger_frame','requested_frame_missing','source_binding_mismatch'}.issubset(codes))
        self.assertEqual(audit['counts']['source_valid_ok_frames'],0)
        self.assertEqual(paired_disagreement(rows,rows)['source_bound_frame_pairs'],1)
        self.assertEqual(paired_disagreement(rows,rows,eligible_frame_indexes=set())['source_bound_frame_pairs'],0)

    def test_failed_or_missing_middle_frame_never_bridged(self):
        manifest,rows=fixture()
        rows[1]['status']='failed'
        audit=audit_ledger(manifest,rows)
        self.assertEqual(audit['per_joint_temporal']['right_wrist']['step_torso']['count'],0)
        self.assertEqual(audit['per_joint_temporal']['right_wrist']['three_frame_linear_residual_torso']['count'],0)

    def test_no_unilateral_torso_fallback(self):
        manifest,rows=fixture()
        for row in rows: row['keypoints_2d']['left_hip']=[-10,40]
        audit=audit_ledger(manifest,rows)
        self.assertEqual(audit['counts']['bilateral_torso_frames'],0)
        self.assertEqual(audit['measurement_eligible_frame_counts'],{})

    def test_invalid_clock_fake_confidence_and_wrong_units_rejected(self):
        for field,value,code in [('timestamp_ms',500,'invalid_pts_time'),('confidence',1,'fabricated_sam_confidence'),
                                 ('coordinate_system_2d','model_units','wrong_2d_units')]:
            manifest,rows=fixture(1)
            rows[0][field]=value
            audit=audit_ledger(manifest,rows)
            self.assertIn(code,{item['code'] for item in audit['issues']})
            self.assertEqual(audit['counts']['source_valid_ok_frames'],0)

    def test_embedded_rtmpose_baseline_must_match_independent_raw_output(self):
        _,rows=fixture(1)
        base=copy.deepcopy(rows)
        rows[0]['baseline']={'source_binding':{key:base[0][key] for key in BINDING},
            **{key:copy.deepcopy(base[0][key]) for key in ('keypoints_2d','keypoint_scores','coordinate_system_2d')}}
        self.assertEqual(audit_embedded_baseline(rows,base)['matches'],1)
        rows[0]['baseline']['keypoints_2d']['right_wrist'][0]+=1
        self.assertEqual(audit_embedded_baseline(rows,base)['matches'],0)

    def test_skipped_rows_keep_denominator_but_do_not_require_invented_embedded_baseline(self):
        _,rows=fixture(2);base=copy.deepcopy(rows)
        rows[0]['status']='skipped';base[1]['status']='failed'
        result=audit_embedded_baseline(rows,base)
        self.assertEqual(result['input_rows'],2)
        self.assertEqual(result['skipped_non_ok_rows'],1)
        self.assertEqual(result['independent_baseline_non_ok_rows'],1)
        self.assertEqual(result['checked_rows'],0)
        self.assertEqual(result['mismatches'],[])

    def test_embedded_baseline_mismatch_fails_cli_even_if_outer_source_binding_matches(self):
        for tampered in (False,True):
            with self.subTest(tampered=tampered),tempfile.TemporaryDirectory() as directory:
                root=Path(directory);manifest,rows=fixture(1);manifest['video']={'sha256':'a'*64}
                baseline=copy.deepcopy(rows)
                baseline[0].update(model_kind='rtmpose',confidence_semantics=RTMPOSE_RAW_SCORE_SEMANTICS,keypoint_scores={name:.9 for name in BODY})
                rows[0]['baseline']={'source_binding':{key:baseline[0][key] for key in BINDING},
                    **{key:copy.deepcopy(baseline[0][key]) for key in ('keypoints_2d','keypoint_scores','coordinate_system_2d')}}
                if tampered: rows[0]['baseline']['keypoints_2d']['right_wrist'][0]+=100
                (root/'manifest.json').write_text(json.dumps(manifest),encoding='utf-8')
                for name,records in (('rtmpose',baseline),('sam',rows)):
                    (root/(name+'.jsonl')).write_text('\n'.join(json.dumps(row) for row in records)+'\n',encoding='utf-8')
                with patch('sys.stdout',new=io.StringIO()):
                    code=main(['--manifest',str(root/'manifest.json'),'--ledger','rtmpose='+str(root/'rtmpose.jsonl'),
                               '--ledger','sam='+str(root/'sam.jsonl'),'--output',str(root/'audit.json')])
                saved=json.loads((root/'audit.json').read_text(encoding='utf-8'))
                self.assertEqual(code,int(tampered))
                self.assertEqual(saved['datasets']['sam']['binding_status'],'failed' if tampered else 'passed')
                self.assertEqual(len(saved['datasets']['sam']['embedded_baseline_check']['mismatches']),int(tampered))


if __name__=='__main__': unittest.main()
