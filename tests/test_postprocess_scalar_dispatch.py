"""Scalar fast paths preserve evidence and event-private container ownership."""
from __future__ import annotations

import copy
from pathlib import Path
import tempfile
import unittest

from rallymate_scoring.artifact_evidence import (
    ArtifactEvidenceError, ArtifactEvidenceReader, EvidencePoolWriter, REFERENCE_KEY,
    iter_artifact_records,
)
from rallymate_scoring.source_assessment import _copy_contextual_inputs


class PostprocessScalarDispatchTests(unittest.TestCase):
    def test_contextual_copy_preserves_shared_children_and_cycles(self):
        child = {"coordinates": [0.0, -0.0, 1, None, True, "中文"]}
        source = [child, child]
        source.append(source)
        actual = _copy_contextual_inputs(source)
        self.assertIsNot(actual, source)
        self.assertIs(actual[0], actual[1])
        self.assertIs(actual[2], actual)
        self.assertIsNot(actual[0]["coordinates"], child["coordinates"])
        actual[0]["coordinates"].append(99)
        self.assertEqual(len(child["coordinates"]), 6)

    def test_non_json_values_preserve_deepcopy_semantics(self):
        class CustomList(list):
            pass

        child = {7: [1, 2]}
        source = {"tuple": (child,), "custom": CustomList([child]), "shared": child,
                  "bytes": b"unchanged", "set": {1, 2}, "complex": complex(1, 2)}
        actual = _copy_contextual_inputs(source)
        self.assertEqual(actual, copy.deepcopy(source))
        self.assertIsInstance(actual["tuple"], tuple)
        self.assertIsInstance(actual["custom"], CustomList)
        self.assertIs(actual["tuple"][0], actual["custom"][0])
        self.assertIs(actual["tuple"][0], actual["shared"])
        self.assertIsNot(actual["shared"], child)

    def test_reader_keeps_depth_limit_for_scalar_and_empty_container_leaves(self):
        with tempfile.TemporaryDirectory() as directory, ArtifactEvidenceReader(directory) as reader:
            allowed = {}
            rejected = {"scalar": 1}
            for _ in range(128):
                allowed = [allowed]
                rejected = [rejected]
            self.assertEqual(reader._resolve(allowed), allowed)
            with self.assertRaisesRegex(ArtifactEvidenceError, "nesting exceeds"):
                reader._resolve(rejected)
            with self.assertRaisesRegex(ArtifactEvidenceError, "nesting exceeds"):
                reader._resolve(0, depth=129)

    def test_nested_references_still_validate_and_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "features.jsonl"
            row = {"video_id": "video", "raw": [None, -0.0, True, "中文"],
                   "nested": {"geometry_mask": {"proof": list(range(3000))}}}
            before = copy.deepcopy(row)
            with EvidencePoolWriter(directory, video_id="video", source_id="source") as writer:
                writer.write_jsonl(path, [row])
            self.assertEqual(list(iter_artifact_records(path)), [before])
            self.assertEqual(row, before)
            malformed = {"video_id": "video", "deep": [{"payload": {REFERENCE_KEY: {}}}]}
            with EvidencePoolWriter(directory, video_id="video", source_id="source") as writer:
                with self.assertRaisesRegex(ArtifactEvidenceError, "reserved"):
                    writer.write_jsonl(path, [malformed])


if __name__ == "__main__":
    unittest.main()
