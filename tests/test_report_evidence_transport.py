from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from rallymate_service.report_evidence_transport import (
    MODEL_PROOF_CODEC, ReportEvidenceTransportError, pack_report_evidence, unpack_report_evidence,
)
from rallymate_service import report_evidence_transport as transport
from tests.test_model_measurement_contract import model_window_fixture
from tests.test_trajectory_api import TrajectoryApiTests


def native_fixture():
    proof = model_window_fixture()
    return {'job_id': 'model-report', 'rows': [
        {'value': 0, 'confidence': None, **copy.deepcopy(proof)},
        {'value': 12.5, 'grade': None, **copy.deepcopy(proof)},
        {'value': None, 'reason': 'unavailable'},
    ]}


class ReportEvidenceTransportTests(unittest.TestCase):
    def test_pack_and_decode_charge_the_same_reference_budget_at_boundaries(self):
        budgets = []
        original_budget = transport._Budget

        class ObservedBudget(original_budget):
            def __init__(self):
                super().__init__()
                budgets.append(self)

        raw = native_fixture()
        with patch.object(transport, '_Budget', ObservedBudget):
            encoded = pack_report_evidence(raw)
            self.assertEqual(unpack_report_evidence(encoded), raw)
        self.assertEqual(vars(budgets[0]), vars(budgets[1]))
        for setting, field in [('MAX_EXPANDED_REPORT_BYTES', 'bytes'), ('MAX_BASE_NODES', 'nodes'),
                               ('MAX_MODEL_PROOF_REFERENCES', 'references')]:
            limit = getattr(budgets[0], field)
            with self.subTest(setting=setting), patch.object(transport, setting, limit):
                self.assertEqual(unpack_report_evidence(pack_report_evidence(raw)), raw)
            with self.subTest(setting=setting, below=True), patch.object(transport, setting, limit - 1):
                with self.assertRaises(ReportEvidenceTransportError):
                    pack_report_evidence(raw)
                with self.assertRaises(ReportEvidenceTransportError):
                    unpack_report_evidence(encoded)

    def test_exact_roundtrip_dedup_null_zero_and_no_input_mutation(self):
        original = native_fixture()
        before = copy.deepcopy(original)
        packed = pack_report_evidence(original)
        self.assertEqual(original, before)
        self.assertEqual(packed['report_transport'], MODEL_PROOF_CODEC)
        self.assertEqual(packed['model_evidence_codec'], MODEL_PROOF_CODEC)
        self.assertEqual(len(packed['model_evidence_pool']), 1)
        self.assertEqual([row.get('model_evidence_ref') for row in packed['rows']], [0, 0, None])
        self.assertNotIn('source_window', packed['rows'][0])
        self.assertEqual(unpack_report_evidence(json.loads(json.dumps(packed))), before)
        packed['model_evidence_pool'][0]['evidence']['geometry_mask']['left_hip'][0] = False
        self.assertEqual(original, before)

    def test_optional_source_track_and_complete_content_dedup(self):
        original = native_fixture()
        original['rows'][1]['source_track_id'] = 1
        packed = pack_report_evidence(original)
        self.assertEqual(len(packed['model_evidence_pool']), 2)
        self.assertEqual(unpack_report_evidence(packed), original)
        original = native_fixture()
        original['rows'][1]['source_window']['source_timestamps_ms'][0] = 75300.0
        self.assertEqual(len(pack_report_evidence(original)['model_evidence_pool']), 1)

    def test_native_without_proofs_preserves_legacy_and_partial_objects(self):
        original = {'value': 0, 'confidence': None, 'legacy': {'source_window': None}}
        self.assertIs(pack_report_evidence(original), original)
        self.assertIs(unpack_report_evidence(original), original)

    def test_rejects_malformed_refs_conflicts_unknown_fields_and_nested_pool(self):
        for defect in ('missing_marker', 'unknown_codec', 'unknown_ref', 'bool_ref', 'inline_conflict', 'duplicate_id',
                       'duplicate_content', 'unused_proof', 'unknown_proof_field', 'unknown_binding_field',
                       'wrong_track', 'visibility_promotion', 'nested_pool', 'reserved_key'):
            with self.subTest(defect=defect):
                packed = pack_report_evidence(native_fixture())
                entry = packed['model_evidence_pool'][0]
                if defect == 'missing_marker': del packed['report_transport']
                elif defect == 'unknown_codec': packed['model_evidence_codec'] = 'future'
                elif defect == 'unknown_ref': packed['rows'][0]['model_evidence_ref'] = 100
                elif defect == 'bool_ref': packed['rows'][0]['model_evidence_ref'] = True
                elif defect == 'inline_conflict': packed['rows'][0]['geometry_mask'] = {}
                elif defect == 'duplicate_id': entry['id'] = True
                elif defect in ('duplicate_content', 'unused_proof'):
                    extra = copy.deepcopy(entry); extra['id'] = 1
                    if defect == 'unused_proof': extra['evidence']['geometry_mask']['left_hip'][0] = False
                    packed['model_evidence_pool'].append(extra)
                elif defect == 'unknown_proof_field': entry['evidence']['extra'] = 'discarding would be lossy'
                elif defect == 'unknown_binding_field': entry['evidence']['source_window']['source_bindings'][0]['extra'] = 1
                elif defect == 'wrong_track': entry['evidence']['source_track_id'] = 999
                elif defect == 'visibility_promotion': entry['evidence']['measurement_provenance']['visibility_verified'] = True
                elif defect == 'nested_pool': packed['rows'][0]['model_evidence_pool'] = []
                else: packed['rows'][0]['__proto__'] = {}
                with self.assertRaises(ReportEvidenceTransportError):
                    unpack_report_evidence(packed)
        with self.assertRaises(ReportEvidenceTransportError):
            unpack_report_evidence({'model_evidence_ref': 0})

    def test_packer_rejects_invalid_proof_instead_of_dropping_unknown_values(self):
        for change in ('nonfinite', 'unknown', 'unsafe_integer', 'wrong_epoch', 'wrong_mask'):
            value = native_fixture()
            proof = value['rows'][0]
            if change == 'nonfinite': proof['source_window']['source_timestamps_ms'][0] = float('nan')
            elif change == 'unknown': proof['measurement_provenance']['extra'] = None
            elif change == 'unsafe_integer': proof['source_window']['source_bindings'][0]['pts'] = 2**60
            elif change == 'wrong_epoch': proof['source_window']['source_bindings'][0]['selection_epoch'] = 100
            else: proof['geometry_mask']['left_hip'][0] = 1
            with self.subTest(change=change), self.assertRaises(ReportEvidenceTransportError):
                pack_report_evidence(value)

    def test_size_reference_node_depth_and_pool_budgets_fail_closed(self):
        for name, limit in (('MAX_MODEL_PROOFS', 0), ('MAX_MODEL_PROOF_REFERENCES', 1),
                            ('MAX_EXPANDED_REPORT_BYTES', 100), ('MAX_BASE_NODES', 2), ('MAX_DEPTH', 1)):
            with self.subTest(budget=name), patch('rallymate_service.report_evidence_transport.' + name, limit):
                with self.assertRaises(ReportEvidenceTransportError):
                    pack_report_evidence(native_fixture())
        packed = pack_report_evidence(native_fixture())
        with patch('rallymate_service.report_evidence_transport.MAX_MODEL_PROOF_REFERENCES', 1):
            with self.assertRaises(ReportEvidenceTransportError):
                unpack_report_evidence(packed)


class ReportEvidenceTransportApiTests(unittest.TestCase):
    def test_opt_in_api_roundtrips_and_default_native_artifacts_stay_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = TrajectoryApiTests()
            app, settings, database = fixture._app(Path(directory))
            job = fixture._succeeded_job(settings, database, running=True)
            summary = {'job_id': job, 'status': 'completed'}
            database.mark_succeeded(job, summary)
            path = settings.runs_dir / job / 'summary.json'
            path.write_text(json.dumps(summary), encoding='utf-8')
            before = path.read_bytes()
            supplied = native_fixture()
            supplied['job_id'] = job
            with patch('rallymate_service.api.build_user_demo_result', side_effect=lambda *args, **kwargs: copy.deepcopy(supplied)), TestClient(app) as client:
                url = f'/v1/jobs/{job}/demo-result?report_contract=current'
                native = client.get(url)
                self.assertEqual(native.status_code, 200)
                self.assertNotIn('report_transport', native.json())
                encoded = client.get(url + '&report_transport=model-proof-pool-v1')
                self.assertEqual(encoded.status_code, 200)
                self.assertEqual(encoded.json()['report_transport'], MODEL_PROOF_CODEC)
                self.assertEqual(unpack_report_evidence(encoded.json()), native.json())
                self.assertEqual(client.get(url + '&report_transport=native').json(), native.json())
                self.assertEqual(client.get(url + '&report_transport=unknown').status_code, 422)
                with patch('rallymate_service.api.pack_report_evidence') as packer:
                    self.assertEqual(client.get(f'/v1/jobs/{job}/demo-result').status_code, 200)
                    packer.assert_not_called()
                self.assertEqual(path.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
