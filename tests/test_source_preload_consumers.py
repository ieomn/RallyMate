from __future__ import annotations

import copy
import unittest

from rallymate_features.schemas import model_geometry_evidence
from rallymate_features.source_alignment import clear_source_aligned_feature_cache, compute_source_aligned_features, prepare_source_aligned_context
from rallymate_scoring.indicator_assessment import build_indicator_assessment
from rallymate_scoring.source_assessment import ARTIFACT_VERSION, source_binding
from rallymate_service.source_assessment import SEMANTICS, project_source_assessment
from tests.test_source_preload_translation import NAMES, fixture, run


def observed_fixture(step_ticks=1200):
    sequence, events, timeline = fixture(model=False, step_ticks=step_ticks)
    for i, video_ms in enumerate(sequence.timestamp_ms):
        pts = 2250000 + i * step_ticks
        sequence.source_time_bindings[i] = {
            'frame_index': i, 'processed_index': i, 'video_sha256': 'a' * 64,
            'pts': pts, 'time_base_num': 1, 'time_base_den': 30000,
            'source_timestamp_ms': round(pts / 30), 'video_timestamp_ms': int(video_ms),
            'timestamp_source': 'decoder_pts', 'primary_player_id': 1, 'person_track_id': 1,
            'selection_epoch': 0, 'camera_reference_epoch': 0,
        }
    return sequence, events, timeline


def projected(sequence, events, timeline):
    if sequence.model_estimate_bindings is not None:
        events[0].update(model_geometry_evidence(sequence, range(len(sequence.timestamp_ms))))
    record, rows = run(sequence, events, timeline)
    artifact = {'version': ARTIFACT_VERSION, 'video_id': 'video-a', 'video_sha256': 'a' * 64,
                'source_binding': source_binding(), 'score_semantics': SEMANTICS,
                'technical_grade': None, 'technical_score_0_to_100': None, 'records': [record]}
    report = project_source_assessment(artifact, events, video_id='video-a', duration_ms=1000,
                                      video_sha256='a' * 64)
    card = next(row for row in report['indicators'] if row['indicator_id'] == 'FS01-M02')
    return report, {row['feature_name']: row for row in card['windows'][0]['measurements']}, rows


class SourcePreloadConsumerTests(unittest.TestCase):
    def test_independent_decoder_binding_enables_confidence_measurements_without_sam_proof(self):
        sequence, events, timeline = observed_fixture()
        report, rows, raw = projected(sequence, events, timeline)
        for name, expected in zip(NAMES, (40., .16)):
            self.assertAlmostEqual(rows[name]['value'], expected)
            self.assertEqual(rows[name]['evidence'], raw[name]['evidence'])
            self.assertEqual(rows[name]['source_frames'], [1, 2, 3, 4])
            self.assertEqual(rows[name]['evidence']['time_basis'], 'bound_source_pts')
            self.assertNotIn('measurement_provenance', rows[name])
            self.assertFalse(rows[name]['evidence']['source_clause_complete'])
        assessment = build_indicator_assessment({}, source_assessment=report)
        observations = [row for row in assessment['observations'] if row.get('feature_name') in NAMES]
        self.assertEqual(len(observations), 2)
        self.assertEqual(assessment['counts']['formal_graded'], 0)

    def test_sam_proof_survives_both_consumer_layers(self):
        report, rows, raw = projected(*fixture())
        for name in NAMES:
            self.assertEqual(rows[name]['status'], 'measured')
            self.assertEqual(rows[name]['value'], raw[name]['value'])
            self.assertEqual(rows[name]['source_window'], raw[name]['source_window'])
            self.assertFalse(rows[name]['measurement_provenance']['visibility_verified'])
        assessment = build_indicator_assessment({}, source_assessment=report)
        self.assertEqual(len([row for row in assessment['observations'] if row.get('feature_name') in NAMES]), 2)
        self.assertEqual(assessment['counts']['formal_graded'], 0)

    def test_confidence_path_uses_exact_80ms_duration(self):
        for ticks, valid in ((800, True), (798, False), (600, False)):
            _, rows = run(*observed_fixture(ticks))
            self.assertEqual(rows[NAMES[0]]['valid'], valid)
            self.assertEqual(rows[NAMES[1]]['valid'], valid)

    def test_decoder_binding_defects_do_not_change_legacy_values(self):
        for key, value in (('video_sha256', 'b' * 64), ('selection_epoch', 1),
                           ('camera_reference_epoch', 1), ('person_track_id', 2),
                           ('primary_player_id', 2), ('processed_index', 20),
                           ('video_timestamp_ms', 81), ('pts', 2250000),
                           ('source_timestamp_ms', 0)):
            sequence, events, timeline = observed_fixture()
            _, baseline = run(sequence, events, timeline)
            context = prepare_source_aligned_context(sequence, events, primary_timeline=timeline)
            sequence.source_time_bindings[2][key] = value
            clear_source_aligned_feature_cache()
            result = compute_source_aligned_features(sequence, events[0], events=events, context=context)
            changed = {row['feature_name']: row for item in result['indicators'] if item['indicator_id'] == 'FS01-M02'
                       for row in item['source_measurements']}
            self.assertIsNone(changed[NAMES[0]]['value'], key)
            self.assertEqual({k: v for k, v in baseline.items() if k not in NAMES},
                             {k: v for k, v in changed.items() if k not in NAMES}, key)

    def test_signed_reference_coordinates_and_zero_survive_consumers(self):
        sequence, events, timeline = observed_fixture()
        # A stable reference may have a negative origin. This does not affect displacement.
        for points in sequence.keypoints_xy.values():
            points[:, 1] -= 1.
        _, rows, _ = projected(sequence, events, timeline)
        self.assertLess(rows[NAMES[0]]['evidence']['hip_reference_y_start_px'], 0)
        self.assertAlmostEqual(rows[NAMES[0]]['value'], 40.)
        report, _, _ = projected(*observed_fixture())
        for value in (0., -2.):
            changed = copy.deepcopy(report)
            selected = next(row for row in changed['indicators'] if row['indicator_id'] == 'FS01-M02')['windows'][0]['measurements']
            for row in selected:
                if row['feature_name'] in NAMES:
                    row['value'] = value
            assessment = build_indicator_assessment({}, source_assessment=changed)
            actual = [row['value'] for row in assessment['observations'] if row.get('feature_name') in NAMES]
            self.assertEqual(actual, [value, value])


if __name__ == '__main__':
    unittest.main()
