"""Real native e420 regression plus explicitly synthetic contract challenges."""
from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from rallymate_service.micro_adjustment_cycles import (
    ANNOTATION_VERSION, _episodes, build_micro_adjustment_cycle_report,
    compute_micro_adjustment_cycles, validate_micro_adjustment_cycle_annotations,
)
from rallymate_service.gs_card_execution import _prepare_samples
from tests.test_fs_step_motion_windows import fixture as raw_fixture

ROOT = Path(__file__).resolve().parents[1]


def document():
    return json.loads((ROOT/'tests/fixtures/micro-adjustment-cycles-e420-annotations-v1.json').read_text(encoding='utf-8'))


def real_args():
    source = json.loads((ROOT/'tests/fixtures/directional-micro-adjustment-native-e420-v1.json').read_text(encoding='utf-8'))
    return [source[key] for key in ('records', 'primary_timeline', 'source_events', 'decoder_timeline')] + [document()]


def synthetic(mode='sequential'):
    args = raw_fixture()
    row = args[-1]['windows'][0]
    row.update(indicator_id='FS05-M02')
    row.pop('step_motion_observation')
    args[-1]['version'] = ANNOTATION_VERSION
    first = row['start_source_frame']
    ramp = lambda i, a, b: max(0, min(1, (i-a)/(b-a)))
    for record in args[0]:
        i = record['frame']['index']-first
        for point in record['poses'][0]['keypoints']:
            if point['name'] not in {'left_ankle', 'right_ankle'}:
                continue
            point['y_px'] = 160
            point['x_px'] = 180 if point['name']=='left_ankle' else 220
            if mode == 'static':
                shift = 0
            elif mode == 'same_foot':
                shift = 10*(ramp(i, 3, 6)+ramp(i, 10, 13)) if point['name']=='right_ankle' else 0
            elif mode == 'simultaneous' or point['name']=='right_ankle':
                shift = 10*ramp(i, 3, 6)
            else:
                shift = 10*ramp(i, 10, 13)
            point['x_px'] += shift
    return args


def compute(args):
    return compute_micro_adjustment_cycles(*args, source_job_id=args[-1]['source_run_id'])


def instance(report):
    card, = report['cards']
    result, = card['instances']
    return result


def evidence(result):
    return result['source_window_evidence']['micro_adjustment_cycles']


class MicroAdjustmentCycleTests(unittest.TestCase):
    def assertUnavailable(self, result):
        self.assertEqual(result['status'], 'unavailable')
        self.assertIsNone(result['score'])
        self.assertIsNone(result['grade'])
        self.assertTrue(all(condition['passed'] is None for condition in result['conditions']))

    def test_native_fixed_window_one_cycle_is_computable_failure_not_missing(self):
        args = real_args()
        before = deepcopy(args)
        report = compute(args)
        result, data = instance(report), evidence(instance(report))
        self.assertEqual(args, before)
        self.assertFalse(report['input']['artifact_files_verified'])
        self.assertEqual(result['status'], 'scored')
        self.assertEqual(result['score'], 60)
        self.assertEqual([c['passed'] for c in result['conditions']], [False, True])
        self.assertEqual(result['conditions'][0]['value'], 1)
        self.assertAlmostEqual(result['conditions'][1]['value'], .30026233094030963)
        self.assertEqual(data['source_frames'], list(range(112, 137)))
        self.assertEqual(data['valid_frame_count'], 25)
        self.assertEqual(data['auxiliary']['complete_visual_ankle_cycle_count'], 1)
        self.assertEqual(data['auxiliary']['incomplete_ankle_motion_run_count'], 1)
        self.assertEqual(data['auxiliary']['observed_complete_episode_frequency_hz'], 1.25)
        left = next(c for c in data['cycles'] if c['moving_ankle']=='left')
        right = next(c for c in data['cycles'] if c['moving_ankle']=='right')
        self.assertIn('preceding_low_motion_censored_at_window_start', left['reason_codes'])
        self.assertEqual((right['motion_start_source_frame'], right['motion_end_source_frame']), (124, 132))
        self.assertTrue(right['complete_visual_motion_cycle'])
        self.assertEqual(data['reason_codes'], [])
        for row in data['source_geometry_series']:
            self.assertEqual(row['source_binding']['selection_epoch'], 0)
            self.assertEqual(row['selection_epoch'], 1)
        self.assertIsNone(result['original_grade'])
        self.assertFalse(data['contact_verified'])
        self.assertEqual(report['coverage']['adapter_card_count'], 1)
        json.dumps(report, allow_nan=False)

    def test_sequential_both_feet_and_same_foot_count_twice_but_simultaneous_counts_once(self):
        for mode, count, score in (('sequential', 2, 100), ('same_foot', 2, 100), ('simultaneous', 1, 60)):
            with self.subTest(mode=mode):
                result = instance(compute(synthetic(mode)))
                self.assertEqual(result['score'], score, result['reason_codes'])
                self.assertEqual(evidence(result)['measurements']['complete_visual_adjustment_episode_count'], count)
                self.assertEqual(evidence(result)['auxiliary']['complete_visual_ankle_cycle_count'], 2)

    def test_positive_overlap_merges_transitively_and_shared_endpoint_does_not(self):
        def cycle(name, start, end, complete=True):
            return {'cycle_id': name, 'motion_start_source_frame': start,
                'motion_end_source_frame': end, 'complete_visual_motion_cycle': complete}
        runs = [cycle('right-a', 10, 20), cycle('left-bridge', 18, 24, False), cycle('right-b', 22, 30), cycle('left-next', 30, 40)]
        episodes = _episodes(runs)
        self.assertEqual(len(episodes), 2)
        self.assertEqual(episodes[0]['complete_cycle_ids'], ['right-a', 'right-b'])
        self.assertEqual(episodes[0]['incomplete_cycle_ids'], ['left-bridge'])
        self.assertTrue(all(e['complete_visual_adjustment_episode'] for e in episodes))
        self.assertEqual(_episodes([cycle('partial-only', 10, 20, False)])[0]['complete_visual_adjustment_episode'], False)

    def test_static_complete_geometry_is_zero_episodes_and_failed_conditions(self):
        result = instance(compute(synthetic('static')))
        self.assertEqual(result['status'], 'scored')
        self.assertEqual(result['score'], 20)
        self.assertEqual([c['value'] for c in result['conditions']], [0, 0])
        self.assertEqual([c['passed'] for c in result['conditions']], [False, False])

    def test_other_foot_large_partial_path_is_retained_not_null(self):
        args = synthetic('same_foot')
        first = args[-1]['windows'][0]['start_source_frame']
        for record in args[0]:
            i = record['frame']['index']-first
            if 0 <= i <= 5:
                point = next(p for p in record['poses'][0]['keypoints'] if p['name']=='left_ankle')
                point['y_px'] += 35 if i%2 else 0
        result = instance(compute(args))
        self.assertEqual(result['status'], 'scored', result['reason_codes'])
        self.assertFalse(result['conditions'][1]['passed'])
        self.assertGreater(result['conditions'][1]['value'], .5)
        self.assertTrue(any(not c['complete_visual_motion_cycle'] for c in evidence(result)['cycles']))

    def test_actual_model_or_primary_binding_changes_cannot_survive_local_normalization(self):
        for field in ('selection_epoch', 'camera_reference_epoch', 'person_track_id', 'pts', 'video_sha256', 'missing'):
            args = real_args()
            binding = args[0][8]['poses'][0]['source_binding']
            if field=='missing':
                del args[0][8]['poses'][0]['source_binding']
            else:
                binding[field] = 'a'*64 if field=='video_sha256' else 999
            with self.subTest(field=field):
                self.assertUnavailable(instance(compute(args)))
        args = real_args()
        args[1][8]['source_track_id'] = 3
        self.assertUnavailable(instance(compute(args)))

    def test_consistent_primary_zero_and_model_seven_are_distinct_valid_namespaces(self):
        args = real_args()
        for selected in args[1]:
            selected['selection_epoch'] = 0
        for record in args[0]:
            record['poses'][0]['source_binding']['selection_epoch'] = 7
            for request in record['sam3d_requests']:
                if request['person_track_id']==1:
                    request['selection_epoch'] = 7
        result = instance(compute(args))
        self.assertEqual(result['score'], 60)
        self.assertEqual({r['source_binding']['selection_epoch'] for r in evidence(result)['source_geometry_series']}, {7})
        self.assertEqual({r['selection_epoch'] for r in evidence(result)['source_geometry_series']}, {0})

    def test_partial_primary_declarations_cannot_hide_actual_camera_break(self):
        args = real_args()
        for selected in args[1][:8]:
            selected['selection_epoch'] = 2
        args[0][8]['camera_motion']['status'] = 'unavailable'
        self.assertUnavailable(instance(compute(args)))

    def test_raw_joint_gap_or_low_confidence_does_not_shrink_denominator(self):
        for field in ('missing', 'low'):
            args = synthetic()
            pose = args[0][10]['poses'][0]
            if field=='missing':
                pose['keypoints'] = [p for p in pose['keypoints'] if p['name']!='left_shoulder']
            else:
                next(p for p in pose['keypoints'] if p['name']=='left_ankle')['confidence'] = .1
            result = instance(compute(args))
            self.assertUnavailable(result)
            self.assertEqual(evidence(result)['total_frame_count'], 18)

    def test_original_geometry_must_match_before_local_full_torso_reexpression(self):
        def corrupted(*args, **kwargs):
            groups, timeline = _prepare_samples(*args, **kwargs)
            sample = groups[1][8]
            sample.reference_points['left_ankle'] = (999, 999)
            sample.points['left_ankle'] = tuple((999-sample.center[i])/sample.scale for i in (0, 1))
            return groups, timeline
        with patch('rallymate_service.micro_adjustment_cycles._prepare_samples', corrupted):
            result = instance(compute(real_args()))
        self.assertUnavailable(result)
        self.assertIn('direction_original_geometry_or_normalization_unverified', result['reason_codes'])

    def test_original_source_quality_requires_full_coverage_and_confirmed_identity(self):
        for kind in ('coverage', 'confirmed', 'wrong_track'):
            args = synthetic()
            if kind=='coverage': args[2][0]['start_ms'] += 100
            elif kind=='confirmed': args[2][0]['track_diagnostics']['confirmed_id_switch_count'] = 1
            else: args[2][0]['track_diagnostics']['source_track_ids'] = [99]
            with self.subTest(kind=kind): self.assertUnavailable(instance(compute(args)))
        args = synthetic()
        args[2][0]['quality_flags'] = ['primary_identity_ambiguous']
        result = instance(compute(args))
        self.assertEqual(result['score'], 100)
        self.assertIn('primary_identity_ambiguous', result['advisory_flags'])

    def test_supplied_score_cycle_label_target_or_duplicate_window_cannot_authorize(self):
        for key in ('score', 'conditions', 'complete_cycle_count', 'direction_reference', 'step_motion_observation'):
            doc = document();doc['windows'][0][key] = 2
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_micro_adjustment_cycle_annotations(doc)
        doc = document();duplicate = deepcopy(doc['windows'][0]);duplicate['annotation_id'] = 'another-label';doc['windows'].append(duplicate)
        with self.assertRaises(ValueError): validate_micro_adjustment_cycle_annotations(doc)

    def test_original_boundary_frame_missing_is_rejected(self):
        args = real_args();args[0].pop(0)
        with self.assertRaisesRegex(ValueError, 'primary_frame_not_in_source_snapshot'):
            compute(args)
        args[1].pop(0)
        with self.assertRaisesRegex(ValueError, 'original_frame_inventory_incomplete'):
            compute(args)

    def test_original_media_entry_reads_native_run_not_same_video_production_run(self):
        artifact = Path(os.environ.get('CARD_ARTIFACT_ROOT', str(ROOT)))
        run = artifact/'.codex_tmp/autodl-sam-coordination-20261018/failed-final-job-preserved/e4204ec3-7d84-4a90-a961-9dad01b43c2e'
        video = artifact/'.codex_tmp/local-migration-20261019/local-data/uploads/0a3df3a3-2629-4dd5-80a1-54e4b11f7341.mp4'
        if not run.is_dir() or not video.is_file():
            self.skipTest('original_native_e420_media_unavailable')
        expected = document()['input_artifact_sha256']
        digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
        before = {name: digest(run/name) for name in expected}
        report = build_micro_adjustment_cycle_report(run, document(), video_path=video)
        self.assertEqual(before, expected)
        self.assertEqual({name: digest(run/name) for name in expected}, before)
        self.assertTrue(report['input']['artifact_files_verified'])
        self.assertTrue(report['input']['decoder_verified_from_original_video'])
        self.assertEqual(report['input']['source_frame_count'], 2911)
        result = instance(report)
        self.assertEqual(result['score'], 60)
        self.assertEqual(evidence(result)['source_frames'], list(range(112, 137)))
        self.assertEqual({p['input_geometry']['normalization_basis'] for p in evidence(result)['local_normalization']}, {'right_shoulder_right_torso'})
        production = artifact/'.codex_tmp/local-migration-20261019/local-data/runs/0a3df3a3-2629-4dd5-80a1-54e4b11f7341'
        with self.assertRaisesRegex(ValueError, 'source_directory_mismatch'):
            build_micro_adjustment_cycle_report(production, document(), video_path=video)
        changed = document();changed['input_artifact_sha256']['frames.jsonl'] = '0'*64
        with self.assertRaisesRegex(ValueError, 'source_artifact_digest_mismatch'):
            build_micro_adjustment_cycle_report(run, changed, video_path=video)
