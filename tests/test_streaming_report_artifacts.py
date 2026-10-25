import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from rallymate_service.artifact_jsonl import ArtifactRecordLimit, read_event_records
from rallymate_service.user_demo import load_indicator_feature_records, UserDemoResultError
from rallymate_service.footwork_review import load_footwork_review


class StreamingReportArtifactTests(unittest.TestCase):
    def test_jsonl_does_not_materialize_whole_file_and_preserves_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'records.jsonl'
            path.write_text('\n{"value":0}\n\n{"value":null,"中文":"脚步"}\n', encoding='utf-8')
            with patch.object(Path, 'read_text', side_effect=AssertionError('whole-file copy')):
                self.assertEqual(load_indicator_feature_records(path), [{'value': 0}, {'value': None, '中文': '脚步'}])

    def test_regular_json_export_compatibility(self):
        rows = [{'value': 0}, {'value': None}]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'records.jsonl'
            for document in (rows, {'records': rows}):
                for indent in (None, 2):
                    path.write_text(json.dumps(document, indent=indent), encoding='utf-8')
                    self.assertEqual(load_indicator_feature_records(path), rows)

    def test_later_jsonl_records_still_reject_invalid_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'records.jsonl'
            for invalid in ('{"value":NaN}', '{"value":1,"value":2}', 'false', '{'):
                path.write_text('{}\n'+invalid+'\n', encoding='utf-8')
                with self.assertRaises(UserDemoResultError):
                    load_indicator_feature_records(path)

    def test_event_bounds_are_enforced_during_read(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'events.jsonl'
            path.write_bytes(b'{}\n{}\n')
            with self.assertRaises(ArtifactRecordLimit):
                read_event_records(path, max_lines=1)
            with self.assertRaises(ArtifactRecordLimit):
                read_event_records(path, max_bytes=5)
            self.assertEqual(read_event_records(path, max_bytes=6, max_lines=2), [{}, {}])

    def test_legitimate_event_artifact_above_old_16mib_limit_reaches_review(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'events.jsonl'
            row = {'event_id': 'x', 'padding': 'x' * (1024 * 1024)}
            with path.open('w', encoding='utf-8') as stream:
                for _ in range(17):
                    stream.write(json.dumps(row)+'\n')
            with patch('rallymate_service.footwork_review.build_footwork_review', return_value={'ok': True}) as build:
                self.assertEqual(load_footwork_review(path, []), {'ok': True})
                self.assertEqual(len(build.call_args.args[0]), 17)


if __name__ == '__main__':
    unittest.main()
