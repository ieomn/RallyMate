"""Event-private raw ownership must survive reuse by the native read-only helpers."""
from __future__ import annotations

import copy
import unittest
from unittest.mock import patch

from rallymate_scoring import source_assessment as assessment
from rallymate_features.native_primary import NativePrimaryTimelineError
from tests.test_contextual_hip_assessment import (
    contextual_fixture, VIDEO_ID, SOURCE_ID, VIDEO_SHA, INPUT_SHA,
)


def compute(records, timeline, event):
    return assessment.compute_contextual_hip_measurement(
        records, timeline, event, video_id=VIDEO_ID, source_id=SOURCE_ID,
        video_sha256=VIDEO_SHA, input_sha256=INPUT_SHA)


class ContextualOwnedCopyTests(unittest.TestCase):
    def test_one_owned_graph_reaches_both_helpers_without_aliasing_legacy(self):
        records, timeline, event = contextual_fixture()
        shared = {'nested': [1, 2], 'tuple': (3, {'value': 4}), 'integer_keys': {7: 'seven'}}
        records[0]['copy_probe'] = records[1]['copy_probe'] = shared
        timeline[0]['copy_probe'] = {'nested': [5, 6]}
        before = copy.deepcopy((records, timeline, event))
        adapter, loader = assessment.adapt_native_primary_timeline, assessment.pose_sequence_from_records
        received = {}

        def adapt(owned, private_timeline, **kwargs):
            self.assertIsNot(owned, records)
            self.assertIsNot(owned[0], records[0])
            self.assertIsNot(owned[0]['poses'][0]['keypoints'], records[0]['poses'][0]['keypoints'])
            self.assertIsNot(owned[0]['copy_probe'], shared)
            self.assertIs(owned[0]['copy_probe'], owned[1]['copy_probe'])
            self.assertIsInstance(owned[0]['copy_probe']['tuple'], tuple)
            self.assertIn(7, owned[0]['copy_probe']['integer_keys'])
            self.assertIsNot(private_timeline[0]['copy_probe'], timeline[0]['copy_probe'])
            received['records'] = owned
            snapshot = copy.deepcopy((owned, private_timeline))
            value = adapter(owned, private_timeline, **kwargs)
            self.assertEqual((owned, private_timeline), snapshot)
            return value

        def load(owned, adapted):
            self.assertIs(owned, received['records'])
            snapshot = copy.deepcopy((owned, adapted))
            sequence = loader(owned, adapted)
            self.assertEqual((owned, adapted), snapshot)
            # Even a future helper accidentally writing its private arguments
            # must not expose writable aliases into the legacy caller's graph.
            owned[0]['copy_probe']['nested'].append(99)
            adapted[0]['copy_probe']['nested'].append(99)
            return sequence

        with patch.object(assessment, 'adapt_native_primary_timeline', adapt), \
                patch.object(assessment, 'pose_sequence_from_records', load):
            result = compute(records, timeline, event)
        self.assertEqual(result['status'], 'measured')
        self.assertEqual((records, timeline, event), before)

    def test_adapter_rejection_preserves_originals_and_never_calls_loader(self):
        records, timeline, event = contextual_fixture()
        before = copy.deepcopy((records, timeline, event))
        def reject(owned, private_timeline, **kwargs):
            owned[0]['poses'][0]['keypoints'].clear()
            private_timeline[0]['selection_epoch'] = 999
            raise NativePrimaryTimelineError('private failure')
        with patch.object(assessment, 'adapt_native_primary_timeline', reject), \
                patch.object(assessment, 'pose_sequence_from_records') as loader:
            result = compute(records, timeline, event)
        loader.assert_not_called()
        self.assertEqual(result['reason'], 'contextual_native_adapter_rejected')
        self.assertEqual((records, timeline, event), before)

    def test_loader_rejection_preserves_originals_and_existing_reason(self):
        for error in (ValueError, KeyError, TypeError, IndexError, ArithmeticError):
            with self.subTest(error=error.__name__):
                records, timeline, event = contextual_fixture()
                before = copy.deepcopy((records, timeline, event))
                def reject(owned, adapted):
                    owned[0]['poses'][0]['keypoints'].clear()
                    adapted[0]['selection_epoch'] = 999
                    raise error('private failure')
                with patch.object(assessment, 'pose_sequence_from_records', reject):
                    result = compute(records, timeline, event)
                self.assertEqual(result['reason'], 'contextual_source_loader_rejected')
                self.assertEqual((records, timeline, event), before)

    def test_unexpected_adapter_and_resource_errors_keep_propagating(self):
        for target, error in (('adapt_native_primary_timeline', RuntimeError),
                              ('adapt_native_primary_timeline', MemoryError),
                              ('pose_sequence_from_records', MemoryError)):
            with self.subTest(target=target, error=error.__name__):
                records, timeline, event = contextual_fixture()
                before = copy.deepcopy((records, timeline, event))
                with patch.object(assessment, target, side_effect=error('private failure')):
                    with self.assertRaises(error):
                        compute(records, timeline, event)
                self.assertEqual((records, timeline, event), before)


if __name__ == '__main__':
    unittest.main()
