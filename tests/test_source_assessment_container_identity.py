"""Container source identity stays distinct from display IDs and contextual rows."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import os
from pathlib import Path
import unittest

from rallymate_service.source_assessment import project_source_assessment
from tests.test_contextual_hip_projection import digest, fixture, project


BASELINE_ENV = "RALLYMATE_TEST_SOURCE_ASSESSMENT_BASELINE"
BASELINE_SHA256 = "ce1ef56773177ff06651f4d1bf3a19637a78aaebd53ecd6432fa331c7cd039aa"


class SourceAssessmentContainerIdentityTests(unittest.TestCase):
    def test_opaque_source_differs_from_video_and_preserves_original_string(self):
        artifact, events, item = fixture()
        source = "  upstream:opaque/source-context  "
        artifact["source_id"] = source
        events[0]["provenance"]["source_id"] = source
        item["source_id"] = source
        item["window_input_sha256"]["event"] = digest(events[0])
        before = copy.deepcopy((artifact, events))
        result = project(artifact, events)
        self.assertEqual(result["source_id"], source)
        self.assertEqual(result["video_id"], "display-job")
        self.assertNotEqual(result["source_id"], result["video_id"])
        self.assertEqual(result["contextual_measurements"][0]["source_id"], source)
        self.assertEqual((artifact, events), before)

    def test_historical_missing_source_never_comes_from_context_event_or_video(self):
        artifact, events, item = fixture()
        del artifact["source_id"]
        self.assertEqual(item["source_id"], "source-job")
        self.assertEqual(events[0]["provenance"]["source_id"], "source-job")
        result = project(artifact, events)
        self.assertIn("source_id", result)
        self.assertIsNone(result["source_id"])
        self.assertEqual(result["contextual_measurements"], [])
        del artifact["records"][0]["contextual_measurements"]
        self.assertIsNone(project(artifact, events)["source_id"])

    def test_invalid_source_values_project_null_without_type_coercion(self):
        for value in (None, "", " \t\n\u3000", False, True, 0, 123, [], {}, "x" * 257):
            with self.subTest(value=value):
                artifact, events, _ = fixture()
                artifact["source_id"] = value
                self.assertIsNone(project(artifact, events)["source_id"])

    def test_source_length_boundary_counts_original_string_and_preserves_unicode(self):
        for value in ("x" * 256, " " + "x" * 254 + " ", " 数据源★ "):
            with self.subTest(value=value):
                artifact, events, _ = fixture()
                artifact["source_id"] = value
                self.assertEqual(project(artifact, events)["source_id"], value)
        artifact, events, _ = fixture()
        artifact["source_id"] = " " + "x" * 255 + " "
        self.assertIsNone(project(artifact, events)["source_id"])

    def test_wrong_context_source_still_rejected_without_changing_container(self):
        artifact, events, item = fixture()
        item["source_id"] = "another-source"
        result = project(artifact, events)
        self.assertEqual(result["source_id"], "source-job")
        self.assertEqual(result["contextual_measurements"], [])

    def test_context_list_is_complete_past_episode_and_indicator_display_limits(self):
        artifact, events, _ = fixture()
        template_event, template_record = events[0], artifact["records"][0]
        events, records = [], []
        for index in range(301):
            identifier = f"synthetic-event-{index:03d}"
            event, record = copy.deepcopy(template_event), copy.deepcopy(template_record)
            event["event_id"] = record["event_id"] = identifier
            item = record["contextual_measurements"][0]
            item["event_id"] = identifier
            item["window_input_sha256"]["event"] = digest(event)
            events.append(event)
            records.append(record)
        artifact["records"] = records
        result = project(artifact, events)
        contextual = result["contextual_measurements"]
        self.assertEqual(len(contextual), 301)
        self.assertEqual([item["event_id"] for item in contextual], [event["event_id"] for event in events])
        self.assertTrue(all(item["source_id"] == result["source_id"] == "source-job" for item in contextual))
        indicator = next(item for item in result["indicators"] if item["indicator_id"] == "FS02-M01")
        self.assertEqual(indicator["window_count"], 301)
        self.assertEqual(indicator["returned_window_count"], 80)
        self.assertTrue(indicator["is_truncated"])

    @unittest.skipUnless(os.environ.get(BASELINE_ENV), "Explicit private frozen service baseline is not supplied")
    def test_removing_only_new_top_source_matches_frozen_service_output(self):
        baseline_path = Path(os.environ[BASELINE_ENV]).resolve()
        self.assertEqual(hashlib.sha256(baseline_path.read_bytes()).hexdigest(), BASELINE_SHA256)
        spec = importlib.util.spec_from_file_location("frozen_contextual_service_before_container_source", baseline_path)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        baseline = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(baseline)
        for case in ("measured", "absent_context", "unavailable", "bad_identity", "duplicate_event", "bad_version"):
            with self.subTest(case=case):
                artifact, events, item = fixture()
                if case == "absent_context":
                    del artifact["records"][0]["contextual_measurements"]
                    del artifact["source_id"]
                elif case == "unavailable":
                    item.update(status="unavailable", reason="hip_geometry_unavailable", source_reference=None,
                        hip_path_length_px=None, hip_net_displacement_px=None, hip_path_over_net_ratio=None)
                elif case == "bad_identity": item["source_id"] = "wrong-source"
                elif case == "duplicate_event": events.append(copy.deepcopy(events[0]))
                elif case == "bad_version": artifact["version"] = "unknown-version"
                before = copy.deepcopy((artifact, events))
                expected = baseline.project_source_assessment(artifact, events, video_id="display-job",
                    duration_ms=4000, video_sha256="a" * 64)
                actual = project_source_assessment(artifact, events, video_id="display-job",
                    duration_ms=4000, video_sha256="a" * 64)
                # Existing early fail-closed responses stay unchanged; the
                # additive field belongs to the successful projection envelope.
                if case not in {"duplicate_event", "bad_version"}:
                    self.assertIn("source_id", actual)
                actual.pop("source_id", None)
                self.assertEqual(actual, expected)
                self.assertEqual((artifact, events), before)


if __name__ == "__main__":
    unittest.main()
