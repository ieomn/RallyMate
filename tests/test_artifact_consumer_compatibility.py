from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from rallymate_scoring.artifact_evidence import (
    ArtifactEvidenceError, EvidencePoolWriter, POOL_FILENAME,
)
from rallymate_scoring.calibration_dataset import _parse_jsonl_bytes
from rallymate_scoring.indicator_feature_qualification import (
    IndicatorFeatureQualificationError, _parse_jsonl,
)
from rallymate_visualization.scoring_observer import _read_jsonl


class ArtifactConsumerCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.path = self.root / "indicator-features.jsonl"
        self.record = {"video_id": "video-a", "event_id": "event-a", "value": 1.25,
                       "source_window": {"source_frames": list(range(2000))}}

    def write_pool(self):
        with EvidencePoolWriter(self.root, video_id="video-a", source_id="task-a") as writer:
            writer.write_jsonl(self.path, [self.record])

    def test_observer_reads_native_and_pooled_evidence_equivalently(self):
        self.path.write_text(json.dumps(self.record) + "\n", encoding="utf-8")
        native = _read_jsonl(self.path)
        self.write_pool()
        self.assertEqual(_read_jsonl(self.path), native)

    def test_observer_cannot_silently_display_unresolved_missing_pool(self):
        self.write_pool()
        (self.root / POOL_FILENAME).unlink()
        with self.assertRaises(ArtifactEvidenceError):
            _read_jsonl(self.path)

    def test_byte_snapshot_compiler_rejects_unfrozen_external_dependencies(self):
        self.write_pool()
        with self.assertRaisesRegex(ValueError, "pooled evidence requires a frozen dependency snapshot"):
            _parse_jsonl_bytes(self.path.read_bytes(), label="indicator-features")

    def test_source_qualification_rejects_unfrozen_external_dependencies(self):
        self.write_pool()
        with self.assertRaisesRegex(IndicatorFeatureQualificationError, "pooled evidence requires a frozen dependency snapshot"):
            _parse_jsonl(self.path.read_bytes(), field="indicator features")

    def test_old_native_snapshots_and_duplicate_key_rejection_are_unchanged(self):
        raw = (json.dumps(self.record) + "\n").encode()
        self.assertEqual(_parse_jsonl_bytes(raw, label="old"), [self.record])
        self.assertEqual(_parse_jsonl(raw, field="old"), [self.record])
        duplicate = b'{"value":1,"value":2}\n'
        with self.assertRaisesRegex(ValueError, "duplicate JSON key"):
            _parse_jsonl_bytes(duplicate, label="old")
        with self.assertRaisesRegex(IndicatorFeatureQualificationError, "duplicate JSON key"):
            _parse_jsonl(duplicate, field="old")


if __name__ == "__main__":
    unittest.main()
