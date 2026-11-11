import unittest
from unittest.mock import patch

from rallymate_service import report_evidence_transport as codec


class ReportEvidenceDiagnosticsTests(unittest.TestCase):
    def test_exhausted_budget_identifies_field_without_payload(self):
        observations = []
        with patch.object(codec, 'MAX_BASE_NODES', 7):
            with self.assertRaises(codec.ReportEvidenceLimitError) as caught:
                codec.pack_report_evidence({'small': [1], 'large': list(range(20))},
                                           diagnostics_callback=observations.append)
        error = caught.exception
        self.assertEqual((error.budget, error.actual, error.maximum), ('base nodes', 8, 7))
        self.assertEqual(error.diagnostics, observations[0])
        self.assertEqual(error.diagnostics['active_top_level_field'], 'large')
        self.assertEqual(error.diagnostics['top_level_fields']['small']['base_nodes'], 2)
        self.assertEqual(error.diagnostics['top_level_fields']['large']['base_nodes'], 5)
        self.assertNotIn('payload', error.diagnostics)

    def test_diagnostics_do_not_change_native_result_or_limits(self):
        value = {'a': [1, 2], 'b': {'valid': True}}
        observations = []
        self.assertIs(codec.pack_report_evidence(value, diagnostics_callback=observations.append), value)
        self.assertEqual(observations[0]['state'], 'complete')
        self.assertIsNone(observations[0]['active_top_level_field'])
        self.assertEqual(observations[0]['base_nodes'], 6)
        self.assertEqual(observations[0]['references'], 0)


if __name__ == '__main__':
    unittest.main()
