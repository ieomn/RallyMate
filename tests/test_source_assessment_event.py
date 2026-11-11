import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rallymate_service import source_assessment as source
from tests.test_source_assessment import fixture


class SourceAssessmentEventTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.artifact, self.events = fixture(2)
        contents = {
            "events.jsonl": "".join(json.dumps(row) + "\n" for row in self.events),
            "primary-player.jsonl": "{}\n", "frames.jsonl": "{}\n",
        }
        fields = {"events.jsonl": "events_sha256", "primary-player.jsonl": "primary_timeline_sha256", "frames.jsonl": "frames_sha256"}
        for name, content in contents.items():
            data = content.encode()
            (self.root / name).write_bytes(data)
            self.artifact[fields[name]] = hashlib.sha256(data).hexdigest()
        (self.root / source.ARTIFACT_NAME).write_text(json.dumps(self.artifact), encoding="utf-8")

    def load(self, **kwargs):
        return source.load_source_assessment_event(self.root, event_id="event-1", video_id="video",
            duration_ms=200, video_sha256="a" * 64, frames_sha256=self.artifact["frames_sha256"], **kwargs)

    def test_selected_event_preserves_original_measurements_and_excludes_other_events(self):
        actual = self.load()
        expected = source.project_source_assessment({**self.artifact, "records": [self.artifact["records"][1]]},
            self.events, video_id="video", duration_ms=200, video_sha256="a" * 64)
        self.assertEqual(actual.pop("detail_scope"), {"kind": "event", "event_id": "event-1", "whole_video": False})
        self.assertEqual(actual, expected)
        self.assertEqual(actual["indicators"][0]["windows"][0]["event_id"], "event-1")

    def test_changed_dependency_is_not_shown_as_valid_measurement(self):
        (self.root / "frames.jsonl").write_text('{"changed":true}\n', encoding="utf-8")
        self.assertEqual(self.load()["status"], "unavailable")

    def test_callback_value_error_propagates_instead_of_becoming_empty_report(self):
        def cancel():
            raise ValueError("request cancelled")
        with self.assertRaisesRegex(ValueError, "request cancelled"):
            self.load(cancellation_callback=cancel)

    def test_dependency_changed_during_projection_is_rejected(self):
        original = source.project_source_assessment
        def mutate(*args, **kwargs):
            result = original(*args, **kwargs)
            (self.root / "primary-player.jsonl").write_text('{"changed":true}\n', encoding="utf-8")
            return result
        with patch.object(source, "project_source_assessment", mutate):
            self.assertEqual(self.load()["status"], "unavailable")


if __name__ == "__main__":
    unittest.main()
