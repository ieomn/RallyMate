from __future__ import annotations

import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from rallymate_scoring.scoring_loop_report import _load_jsonl


class ScoringLoopReportLoadingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "facts.jsonl"

    def load(self, content: str) -> list[dict]:
        self.path.write_bytes(content.encode("utf-8"))
        return _load_jsonl(self.path)

    def test_preserves_order_fields_and_duplicate_records(self) -> None:
        rows = [
            {"event_id": "second", "extra": {"原始": [None, False, 2.5]}},
            {"event_id": "first", "unknown_field": "保留"},
            {"event_id": "second", "extra": {"原始": [None, False, 2.5]}},
        ]
        content = "\r\n  \r\n" + "\r\n\n".join(
            json.dumps(row, ensure_ascii=False) for row in rows
        )
        self.assertEqual(self.load(content), rows)

    def test_empty_and_whitespace_only_inputs(self) -> None:
        for content in ("", "\n", " \t\r\n\r\n", "\v\f\x1c\x1d\x1e\x85\u2028\u2029"):
            with self.subTest(content=repr(content)):
                self.assertEqual(self.load(content), [])

    def test_existing_duplicate_json_key_policy_is_last_value(self) -> None:
        self.assertEqual(self.load('{"value":1,"value":2}\n'), [{"value": 2}])

    def test_existing_nonfinite_json_policy_is_preserved(self) -> None:
        row = self.load('{"nan":NaN,"positive":Infinity,"negative":-Infinity}')[0]
        self.assertTrue(math.isnan(row["nan"]))
        self.assertEqual(row["positive"], math.inf)
        self.assertEqual(row["negative"], -math.inf)

    def test_invalid_json_retains_line_number_and_cause(self) -> None:
        with self.assertRaisesRegex(ValueError, r"invalid JSONL report fact at .*:4$") as caught:
            self.load('\r\n{"valid":true}\r\n\r\nbad\n')
        self.assertIsInstance(caught.exception.__cause__, json.JSONDecodeError)

    def test_nonobject_retains_line_number(self) -> None:
        for value in ("[]", "null", "3", '"text"'):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, r"must be an object at .*:2$"):
                    self.load("\n" + value)

    def test_all_existing_splitlines_separators_are_preserved(self) -> None:
        separators = ("\n", "\r", "\r\n", "\v", "\f", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029")
        for separator in separators:
            with self.subTest(separator=repr(separator)):
                self.assertEqual(self.load('{"a":1}' + separator + '{"b":2}'), [{"a": 1}, {"b": 2}])
                with self.assertRaisesRegex(ValueError, r"invalid JSONL report fact at .*:3$"):
                    self.load('{"a":1}' + separator + separator + 'bad')
        # A literal Unicode line separator inside a string was rejected by the
        # old splitlines reader; streaming must not silently change that policy.
        with self.assertRaisesRegex(ValueError, r"invalid JSONL report fact at .*:1$"):
            self.load('{"text":"left\u2028right"}')

    def test_does_not_read_the_whole_file_as_text(self) -> None:
        self.path.write_bytes(b'{"value":1}\n{"value":2}\n')
        with patch.object(Path, "read_text", side_effect=AssertionError("whole-file read")):
            self.assertEqual(_load_jsonl(self.path), [{"value": 1}, {"value": 2}])


if __name__ == "__main__":
    unittest.main()
