from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from rallymate_scoring.training_dimensions import dimension_for_card

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('indicator_matrix', ROOT / 'scripts/build_indicator_acceptance_matrix.py')
matrix = importlib.util.module_from_spec(spec)
spec.loader.exec_module(matrix)


class IndicatorAcceptanceMatrixTests(unittest.TestCase):
    def setUp(self):
        catalog = json.loads((ROOT / 'src/rallymate_scoring/data/technical_review_rules.json').read_text(encoding='utf-8'))
        self.sources, indicators = [], []
        for rule in catalog['rules']:
            self.sources.append({
                'indicator_id': rule['indicator_id'], 'dimension': dimension_for_card(rule['indicator_id']),
                'event_code': rule['event_code'], 'stage_code': rule['stage_code'], 'name_zh': rule['name_zh'],
                'source_document_id': rule['source_document_id'], 'source_sha256': rule['source_document_sha256'],
                'source_table': rule['source_table'], 'rubric_sha256': rule['rubric_sha256'],
                'source_fields': {name: {'text': value, 'locator': f"fixture:{rule['indicator_id']}:{name}"}
                    for name, value in {'技术定义': rule['definition'], '计算方式': rule['calculation'],
                                        '不可评价': rule['unevaluable'], **{level + '级': text for level, text in rule['grades'].items()}}.items()},
                'source_calculation': rule['calculation'], 'source_unevaluable': rule['unevaluable'],
                'source_allows_human_qualitative_grade': rule['manual_grading_allowed'],
                'source_blockers': rule['blockers'], 'source_warnings': rule['warnings'],
            })
            blocker = next((b['code'] for b in rule['blockers'] if b['code'] in matrix.SOURCE_BLOCKERS), None)
            indicators.append({'indicator_id': rule['indicator_id'], 'status': blocker or 'not_observed',
                'source': {'document_id': rule['source_document_id'], 'sha256': rule['source_document_sha256'],
                           'table': rule['source_table'], 'rubric_sha256': rule['rubric_sha256']},
                'grade': {'status': 'source_rule_conflict' if blocker == 'source_rule_conflict' else 'calibration_required'},
                'observation_ids': [], 'observation_count': 0, 'returned_observation_count': 0,
                'observations_truncated': False, 'blockers': deepcopy(rule['blockers']), 'explanation_zh': 'fixture'})
        self.assessment = {'version': 'word-indicator-assessment-v1.0.0',
            'counts': {'formal_graded': 0, 'total': 298, 'returned_observations': 0, 'observations': 0},
            'indicators': indicators, 'observations': [], 'returned_observation_count': 0,
            'full_observation_count': 0, 'is_truncated': False}

    def add_measurement(self, ids=('GS01-M03-01', 'GS01-M03-02')):
        self.assessment['observations'] = [{'observation_id': 'shared', 'source': 'motion_joint_measurement',
            'status': 'measured', 'value': 0, 'unit': 'deg', 'full_source_rule_satisfied': False}]
        self.assessment.update(returned_observation_count=1, full_observation_count=1)
        self.assessment['counts'].update(returned_observations=1, observations=1)
        for card in self.assessment['indicators']:
            if card['indicator_id'] in ids:
                card.update(observation_ids=['shared'], observation_count=1, returned_observation_count=1)

    def build(self, **kwargs):
        return matrix.build_matrix(self.sources, self.assessment, run_id='run', **kwargs)

    def complete_rule_fixture(self):
        # A synthetic source rule exercises the future verifier boundary. It is
        # deliberately not an A-E mapping for any real Word tennis condition.
        identifier = 'FS01-M02'
        self.add_measurement((identifier,))
        source = next(row for row in self.sources if row['indicator_id'] == identifier)
        card = next(row for row in self.assessment['indicators'] if row['indicator_id'] == identifier)
        source.update(source_document_id='SYNTHETIC-TEST-ONLY', source_sha256='f' * 64, rubric_sha256='e' * 64)
        fields = {name: {'text': value, 'locator': 'synthetic:' + name} for name, value in {
            '技术定义': 'Synthetic integer evidence contract; not a tennis rule',
            '计算方式': 'A=4; B=3; C=2; D=1; E=0', '不可评价': 'Reject missing event, identity or frame evidence',
            **{level + '级': 'value=' + str(value) for level, value in zip('ABCDE', (4, 3, 2, 1, 0))}}.items()}
        source['source_fields'] = fields
        card['source'].update(document_id=source['source_document_id'], sha256=source['source_sha256'], rubric_sha256=source['rubric_sha256'])
        card['grade'] = {'status': 'graded', 'grade': 'A'}
        self.assessment['counts']['formal_graded'] = 1
        self.assessment['observations'][0].update(value=4, full_source_rule_satisfied=True)
        evidence = {'shared': {'source_frames': [{'frame_index': 5, 'timestamp_ms': 100}],
                               'person_track_id': 1, 'event_id': 'synthetic-event', 'valid': True}}

        def verifier(actual_source, actual_card, observations, raw):
            valid_source = actual_source['source_fields'] == fields
            usable = raw['shared'].get('valid') is True and raw['shared'].get('person_track_id') == 1
            value = observations['shared']['value']
            chosen = {4: 'A', 3: 'B', 2: 'C', 1: 'D', 0: 'E'}.get(value)
            return {'version': 'complete-source-grade-verification-v1', 'indicator_id': identifier,
                'source_sha256': source['source_sha256'], 'rubric_sha256': source['rubric_sha256'],
                'grade': chosen, 'all_source_conditions_checked': valid_source,
                'unevaluable_gates_passed': usable, 'event_identity_and_stage_verified': usable,
                'grade_choice_verified': chosen == actual_card['grade']['grade'],
                'complete_source_rule_satisfied': usable and valid_source and chosen is not None,
                'source_fields_checked': deepcopy(fields), 'observation_ids': ['shared'],
                'verifier_id': 'synthetic-fixture-only', 'verifier_version': '1'}

        return identifier, evidence, verifier

    def test_unobserved_cards_are_complete_catalogue_not_graded_or_failed_technique(self):
        result = self.build()
        self.assertEqual(result['states'], {'implemented': 0, 'measurement_only': 0, 'blocked': 298})
        self.assertEqual(result['coverage']['catalogue_response']['fraction'], 1)
        self.assertEqual(result['coverage']['complete_source_grade']['fraction'], 0)
        self.assertIsNone(result['coverage']['recognition_accuracy']['value'])

    def test_shared_zero_measurement_counts_unique_observation_and_two_cards(self):
        self.add_measurement()
        result = self.build()
        self.assertEqual(result['states']['measurement_only'], 2)
        self.assertEqual(result['coverage']['cards_with_any_measurement']['numerator'], 2)
        self.assertEqual(result['coverage']['card_linked_returned_observations']['numerator'], 1)
        self.assertEqual(result['observations'][0]['linked_source_card_count'], 2)
        self.assertEqual(result['observations'][0]['value'], 0)
        self.assertEqual(result['states']['implemented'], 0)

    def test_source_conflict_stays_blocked_even_with_measurement(self):
        self.add_measurement(('GS01-M10-04',))
        result = self.build()
        card = next(r for r in result['cards'] if r['indicator_id'] == 'GS01-M10-04')
        self.assertEqual(card['acceptance_state'], 'blocked')
        self.assertEqual(card['accepted_linked_observation_count'], 1)

    def test_unknown_or_duplicate_observation_reference_is_rejected(self):
        self.add_measurement()
        card = next(r for r in self.assessment['indicators'] if r['observation_ids'])
        card['observation_ids'] = ['missing']
        with self.assertRaisesRegex(matrix.MatrixAuditError, 'Unknown observation link'):
            self.build()
        card['observation_ids'] = ['shared', 'shared']
        with self.assertRaisesRegex(matrix.MatrixAuditError, 'Duplicate observation link'):
            self.build()

    def test_source_binding_mismatch_and_lost_blocker_are_rejected(self):
        card = self.assessment['indicators'][0]
        original = card['source']['sha256']
        card['source']['sha256'] = 'wrong'
        with self.assertRaisesRegex(matrix.MatrixAuditError, 'Source/rubric binding mismatch'):
            self.build()
        card['source']['sha256'] = original
        blocked = next(r for r in self.assessment['indicators'] if r['blockers'])
        blocked['blockers'] = []
        with self.assertRaisesRegex(matrix.MatrixAuditError, 'Source blockers lost'):
            self.build()

    def test_invented_formal_grade_rejected_even_if_summary_claims_zero(self):
        self.assessment['indicators'][0]['grade']['value'] = 'A'
        with self.assertRaisesRegex(matrix.MatrixAuditError, 'Unsupported formal grade'):
            self.build()

    def test_complete_source_rule_can_be_implemented_with_independent_verifier(self):
        identifier, evidence, verifier = self.complete_rule_fixture()
        result = self.build(evidence=evidence, grade_verifiers={identifier: verifier})
        self.assertEqual(result['states']['implemented'], 1)
        self.assertEqual(result['coverage']['complete_source_grade']['numerator'], 1)
        row = next(row for row in result['cards'] if row['indicator_id'] == identifier)
        self.assertTrue(row['complete_source_grade_verified'])
        self.assertEqual(row['complete_rule_verification']['verifier_id'], 'synthetic-fixture-only')

    def test_grade_claim_without_verifier_or_with_missing_raw_evidence_is_rejected(self):
        identifier, evidence, verifier = self.complete_rule_fixture()
        with self.assertRaisesRegex(matrix.MatrixAuditError, 'No complete source-rule verifier'):
            self.build(evidence=evidence)
        evidence['shared']['source_frames'] = []
        with self.assertRaisesRegex(matrix.MatrixAuditError, 'no raw frame evidence'):
            self.build(evidence=evidence, grade_verifiers={identifier: verifier})

    def test_complete_rule_rechecks_unavailable_gates_and_formal_count(self):
        identifier, evidence, verifier = self.complete_rule_fixture()
        evidence['shared']['valid'] = False
        with self.assertRaisesRegex(matrix.MatrixAuditError, 'unevaluable_gates_passed'):
            self.build(evidence=evidence, grade_verifiers={identifier: verifier})
        evidence['shared']['valid'] = True
        self.assessment['counts']['formal_graded'] = 0
        with self.assertRaisesRegex(matrix.MatrixAuditError, 'Formal grade count mismatch'):
            self.build(evidence=evidence, grade_verifiers={identifier: verifier})

    def test_full_observation_claim_does_not_promote_partial_card(self):
        self.add_measurement()
        self.assessment['observations'][0]['full_source_rule_satisfied'] = True
        with self.assertRaisesRegex(matrix.MatrixAuditError, 'no independently verified grade evidence'):
            self.build()

    def test_explicit_footwork_conditions_keep_implementation_and_event_gaps_separate(self):
        result = self.build()
        cards = {row['indicator_id']: row for row in result['cards']}
        for identifier in ('FS03-M03', 'FS04-M04'):
            self.assertEqual(len(cards[identifier]['source_subconditions']), 3)
            self.assertTrue(all(row['definition_state'] == 'explicit_condition_in_source'
                                for row in cards[identifier]['source_subconditions']))
            self.assertTrue(cards[identifier]['gap_layers']['subcondition_implementation'])
            self.assertEqual(cards[identifier]['gap_layers']['run_evidence'][0]['code'], 'event_window_missing')
            self.assertEqual(cards[identifier]['acceptance_state'], 'blocked')

    def test_unsupported_recognition_is_an_implementation_gap_not_missing_video_evidence(self):
        from rallymate_scoring.indicator_assessment import build_indicator_assessment
        self.assessment = build_indicator_assessment({})
        result = self.build()
        cards = {row['indicator_id']: row for row in result['cards']}
        for identifier in ('GS04-M05-02', 'GS05-M01-01', 'FS03-M01', 'FS10-M01'):
            row = cards[identifier]
            self.assertIn('event_type_recognizer_not_implemented',
                          {gap['code'] for gap in row['gap_layers']['subcondition_implementation']})
            self.assertEqual(row['gap_layers']['run_evidence'][0]['code'],
                             'event_evidence_not_assessable_without_recognizer')
            self.assertIn('complete_grade_verifier_not_implemented',
                          {gap['code'] for gap in row['gap_layers']['grade_mapping']})
            self.assertFalse(row['complete_source_grade_verified'])
        self.assertEqual(cards['FS02-M01']['gap_layers']['run_evidence'][0]['code'], 'event_window_missing')
        self.assertTrue(cards['GS04-M01-01']['gap_layers']['source_definition'])
        self.assertEqual(result['states'], {'implemented': 0, 'measurement_only': 0, 'blocked': 298})

    def test_preparation_audit_preserves_unsupported_type_as_algorithm_gap(self):
        from rallymate_scoring.indicator_assessment import build_indicator_assessment
        self.assessment = build_indicator_assessment({})
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            (run_dir / 'frames.jsonl').write_text(json.dumps({'frame': {'index': 1, 'timestamp_ms': 100}}) + '\n', encoding='utf-8')
            report = {'dimensions': [{'id': 'preparation', 'criteria': [], 'measured_criterion_count': 0}]}
            audit = matrix.preparation_gap_audit(self.sources, self.assessment, report, {}, run_dir)
        row = next(row for row in audit['cards'] if row['indicator_id'] == 'GS04-M05-02')
        self.assertTrue(any('当前算法尚未支持' in gap for gap in row['unimplemented_or_unconnected_algorithms']))
        self.assertIn('GS04自动类型识别尚未实现，当前不能核验', row['current_run_evidence']['missing'])
        self.assertFalse(row['grade_rule_gap']['complete_grade_verifier_implemented'])

    def test_external_candidate_evidence_gap_is_not_replaced_by_recognizer_gap(self):
        from rallymate_scoring.indicator_assessment import build_indicator_assessment
        from tests.test_indicator_assessment import external_candidate_without_valid_measurement
        records = [external_candidate_without_valid_measurement(identifier, code)
                   for identifier, code in (('FS03-M01', 'FS03'), ('GS05-M01-01', 'GS05'))]
        self.assessment = build_indicator_assessment({'job_id': 'video-1'}, records)
        result = self.build()
        for identifier in ('FS03-M01', 'GS05-M01-01'):
            row = next(row for row in result['cards'] if row['indicator_id'] == identifier)
            self.assertIn('event_type_recognizer_not_implemented',
                          {gap['code'] for gap in row['gap_layers']['subcondition_implementation']})
            self.assertEqual(row['gap_layers']['run_evidence'], [{
                'code': 'required_measurement_evidence_missing',
                'message_zh': '已有动作候选，但本项连续关节或专用证据不足。'}])
            self.assertFalse(row['complete_source_grade_verified'])
            self.assertEqual(row['accepted_linked_observation_count'], 0)
        self.assertEqual(result['observations'], [])
        self.assertEqual(result['states'], {'implemented': 0, 'measurement_only': 0, 'blocked': 298})

    def test_direction_partial_link_is_counted_without_inventing_ball_agreement(self):
        self.add_measurement(('FS02-M01',))
        self.assessment['observations'][0].update(feature_name='launch_direction_deg', source='legacy_feature')
        row = next(row for row in self.build()['cards'] if row['indicator_id'] == 'FS02-M01')
        self.assertEqual(row['acceptance_state'], 'measurement_only')
        direction, agreement = row['source_subconditions']
        self.assertEqual(direction['observation_ids'], ['shared'])
        self.assertEqual(agreement['observation_ids'], [])
        self.assertFalse(row['complete_source_grade_verified'])

    def test_preparation_projection_keeps_related_cards_unconfirmed_and_audits_all_78(self):
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            (run_dir / 'frames.jsonl').write_text(json.dumps({'frame': {'index': 1, 'timestamp_ms': 100}}) + '\n', encoding='utf-8')
            candidate = {'id': 'preparation_candidate_net_displacement_torso', 'episode_id': 'episode',
                'feature_name': 'net_displacement_torso', 'start_ms': 100, 'end_ms': 200,
                'sample_count': 1, 'source_frames': [1], 'source_timestamps_ms': [100],
                'source_card_applicability': 'unconfirmed', 'full_source_rule_satisfied': False, 'score': None,
                'related_source_cards': ['GS01-M03-02'], 'source_refs': ['fixture:GS01-M03-02:计算方式'],
                'rule_zh': 'Synthetic saved displacement'}
            report = {'dimensions': [{'id': 'preparation', 'criteria': [candidate], 'measured_criterion_count': 1}]}
            with patch('rallymate_service.process_evaluation._episodes', return_value=[{'classification': {'label': 'unclassified'}}]), \
                    patch('rallymate_service.preparation_observations.preparation_candidate_observations', return_value=[candidate]):
                audit = matrix.preparation_gap_audit(self.sources, self.assessment, report, {}, run_dir)
                self.assertEqual(audit['card_count'], 78)
                self.assertEqual(audit['generic_preparation_projection']['formal_card_coverage_added'], 0)
                related = next(row for row in audit['cards'] if row['indicator_id'] == 'GS01-M03-02')
                self.assertEqual(related['current_run_evidence']['explicitly_related_generic_projection_count'], 1)
                self.assertEqual(related['current_run_evidence']['formal_card_observation_count'], 0)
                self.assertFalse(related['current_run_evidence']['related_is_formal_applicability'])
                candidate['full_source_rule_satisfied'] = True
                with self.assertRaisesRegex(matrix.MatrixAuditError, 'promoted to a formal'):
                    matrix.preparation_gap_audit(self.sources, self.assessment, report, {}, run_dir)

    def test_preparation_projection_rejects_frame_mismatch_and_wrong_source_anchor(self):
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            (run_dir / 'frames.jsonl').write_text(json.dumps({'frame': {'index': 1, 'timestamp_ms': 100}}) + '\n', encoding='utf-8')
            candidate = {'id': 'preparation_candidate_x', 'episode_id': 'episode', 'feature_name': 'x',
                'start_ms': 100, 'end_ms': 200, 'sample_count': 1, 'source_frames': [1], 'source_timestamps_ms': [101],
                'source_card_applicability': 'unconfirmed', 'full_source_rule_satisfied': False, 'score': None,
                'related_source_cards': ['GS01-M03-02'], 'source_refs': ['wrong-anchor']}
            report = {'dimensions': [{'id': 'preparation', 'criteria': [candidate], 'measured_criterion_count': 1}]}
            with patch('rallymate_service.process_evaluation._episodes', return_value=[]), \
                    patch('rallymate_service.preparation_observations.preparation_candidate_observations', return_value=[candidate]):
                with self.assertRaisesRegex(matrix.MatrixAuditError, 'frame/time mismatch'):
                    matrix.preparation_gap_audit(self.sources, self.assessment, report, {}, run_dir)
                candidate['source_timestamps_ms'] = [100]
                with self.assertRaisesRegex(matrix.MatrixAuditError, 'incorrect Word anchor'):
                    matrix.preparation_gap_audit(self.sources, self.assessment, report, {}, run_dir)

    def test_translation_keeps_signed_zero_dual_anchors_and_local_window_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            (run_dir / 'frames.jsonl').write_text(json.dumps({'frame': {'index': 1, 'timestamp_ms': 100}}) + '\n', encoding='utf-8')
            candidate = {'id': 'preparation_translation_hip_center_dx_torso', 'episode_id': 'episode',
                'feature_name': 'hip_center_dx_torso', 'start_ms': 100, 'end_ms': 200, 'value': 0,
                'sample_count': 1, 'source_frames': [1], 'source_timestamps_ms': [500], 'video_timestamps_ms': [100],
                'source_card_applicability': 'unconfirmed', 'full_source_rule_satisfied': False, 'score': None,
                'full_phase_coverage': False, 'related_source_cards': ['GS01-M03-04', 'GS05-M01-03'],
                'source_refs': ['fixture:GS01-M03-04:计算方式', 'fixture:GS05-M01-03:计算方式'],
                'rule_zh': 'Signed image-plane translation; synthetic fixture'}
            report = {'dimensions': [{'id': 'preparation', 'criteria': [candidate], 'measured_criterion_count': 1}]}
            with patch('rallymate_service.process_evaluation._episodes', return_value=[]), \
                    patch('rallymate_service.preparation_observations.preparation_candidate_observations', return_value=[]), \
                    patch('rallymate_service.preparation_observations.preparation_translation_observations', return_value=[candidate]):
                audit = matrix.preparation_gap_audit(self.sources, self.assessment, report, {}, run_dir)
                projection = audit['generic_preparation_projection']
                self.assertEqual(projection['translation_projection_count'], 1)
                self.assertEqual(projection['candidate_projection_count'], 0)
                self.assertEqual(projection['translation_full_phase_coverage_count'], 0)
                self.assertEqual(projection['formal_card_coverage_added'], 0)
                self.assertEqual(projection['observations'][0]['value'], 0)
                for identifier in candidate['related_source_cards']:
                    row = next(row for row in audit['cards'] if row['indicator_id'] == identifier)
                    self.assertEqual(row['current_run_evidence']['explicitly_related_generic_projection_count'], 1)
                    self.assertTrue(any(item['feature_name'] == 'hip_center_dx_torso' for item in row['reusable_algorithm_candidates']))
                candidate['source_refs'].append('unrelated-paragraph')
                with self.assertRaisesRegex(matrix.MatrixAuditError, 'incorrect Word anchor'):
                    matrix.preparation_gap_audit(self.sources, self.assessment, report, {}, run_dir)

    def test_action_replay_requires_original_frame_hash_and_selected_run(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_dir = root / 'run'
            run_dir.mkdir()
            frame_path = run_dir / 'frames.jsonl'
            frame_path.write_text('{}\n', encoding='utf-8')
            action_path = root / 'action-recognition.json'
            action_path.write_text('{"episodes": []}', encoding='utf-8')
            proof_path = root / 'evidence.json'
            proof = {'source_hashes': {str(frame_path): matrix.digest(frame_path)}}
            proof_path.write_text(json.dumps(proof), encoding='utf-8')
            action, hashes = matrix.load_action_replay(action_path, run_dir)
            self.assertEqual(action, {'episodes': []})
            self.assertEqual(hashes[str(frame_path)], matrix.digest(frame_path))
            frame_path.write_text('changed\n', encoding='utf-8')
            with self.assertRaisesRegex(matrix.MatrixAuditError, 'does not bind selected run'):
                matrix.load_action_replay(action_path, run_dir)
            proof['source_hashes'] = {str(action_path): matrix.digest(action_path)}
            proof_path.write_text(json.dumps(proof), encoding='utf-8')
            with self.assertRaisesRegex(matrix.MatrixAuditError, 'lacks frame-artifact binding'):
                matrix.load_action_replay(action_path, run_dir)

    def test_bad_sampling_counts_and_missing_card_are_rejected(self):
        self.add_measurement()
        self.assessment['full_observation_count'] = 20
        with self.assertRaisesRegex(matrix.MatrixAuditError, 'Summary full count mismatch'):
            self.build()
        self.assessment['full_observation_count'] = 1
        self.assessment['indicators'].pop()
        with self.assertRaisesRegex(matrix.MatrixAuditError, 'exact 298 source IDs'):
            self.build()

    def test_nonfinite_measurement_and_boolean_are_not_evidence(self):
        self.add_measurement()
        for value in (True, float('nan'), float('inf')):
            self.assessment['observations'][0]['value'] = value
            with self.assertRaisesRegex(matrix.MatrixAuditError, 'Non-finite measurement'):
                self.build()

    def test_raw_feature_frame_and_value_binding_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def write(name, values):
                (root / name).write_text(''.join(json.dumps(v)+'\n' for v in values), encoding='utf-8')
            write('events.jsonl', [{'event_id': 'event', 'event_code': 'FS01', 'start_ms': 0, 'end_ms': 100}])
            write('frames.jsonl', [{'frame': {'index': 5, 'timestamp_ms': 100}}])
            write('indicator-features.jsonl', [{'indicator_id': 'FS01-M02', 'event_id': 'event',
                'features': [{'feature_name': 'hip_center_y_body', 'valid': True, 'value': 0, 'unit': 'body', 'source_frames': [5]}]}])
            obs = {'observation_id': 'o', 'indicator_id': 'FS01-M02', 'event_id': 'event', 'feature_name': 'hip_center_y_body',
                'source': 'legacy_feature', 'value': 0, 'unit': 'body', 'source_frames': [5]}
            evidence, counts = matrix.load_run_evidence(root, {'observations': [obs]}, {})
            self.assertEqual(evidence['o']['replay']['start_ms'], 100)
            self.assertFalse(evidence['o']['replay']['interpolation_used'])
            self.assertEqual(counts['explicit_frame_evidence_observations'], 1)
            obs['source_frames'] = [5, 5]
            write('indicator-features.jsonl', [{'indicator_id': 'FS01-M02', 'event_id': 'event',
                'features': [{'feature_name': 'hip_center_y_body', 'valid': True, 'value': 0, 'unit': 'body', 'source_frames': [5, 5]}]}])
            evidence, counts = matrix.load_run_evidence(root, {'observations': [obs]}, {})
            self.assertEqual(len(evidence['o']['source_frames']), 1)
            self.assertEqual(evidence['o']['raw_source_frame_references'], [5, 5])
            self.assertEqual(counts['observations_with_repeated_endpoint_references'], 1)
            obs['value'] = 100
            with self.assertRaisesRegex(matrix.MatrixAuditError, 'Legacy value/unit mismatch'):
                matrix.load_run_evidence(root, {'observations': [obs]}, {})
            obs['value'] = 0
            write('frames.jsonl', [{'frame': {'index': 9, 'timestamp_ms': 200}}])
            with self.assertRaisesRegex(matrix.MatrixAuditError, 'Out-of-artifact source frame'):
                matrix.load_run_evidence(root, {'observations': [obs]}, {})


if __name__ == '__main__':
    unittest.main()
