"""Additive episode source identity, using bounded synthetic observations only."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from rallymate_service.footwork_review import build_footwork_review, load_footwork_review
from tests.test_model_footwork_review import fixture as model_fixture


def event(**changes):
    return {"event_id": "fs01-synthetic", "video_id": "original-video", "event_code": "FS01",
            "person_track_id": 1, "start_ms": 100, "end_ms": 500, **changes}


def indicator(**changes):
    return {"event_id": "fs01-synthetic", "video_id": "original-video", "event_code": "FS01",
            "person_track_id": 1, "indicator_id": "FS01-M02", "feature_status": "measured",
            "scoring_status": "calibration_required", "grade": "A",
            "quality_gate": {"measurement_allowed": True, "scoring_allowed": True},
            "features": [{"feature_name": "stance_width_body", "value": .7, "unit": "body",
                          "confidence": .9, "valid": True}], **changes}


def strip_episode_source(result):
    copy = deepcopy(result)
    for episode in copy["episodes"]:
        episode.pop("source_id", None)
    return copy


class FootworkEpisodeSourceBindingTests(unittest.TestCase):
    def project(self, events, records=None):
        return build_footwork_review(events, [indicator()] if records is None else records,
                                    video_id="original-video")

    def test_legal_source_value_is_preserved_without_trimming_or_defaulting(self):
        baseline = self.project([event()])
        for source in ("recorded-source", " original-source \t", "x" * 256, "合成来源"):
            with self.subTest(source_length=len(source)):
                original = event(provenance={"source_id": source, "unrelated": {"keep": [1, 2]}})
                before = deepcopy(original)
                result = self.project([original])
                self.assertEqual(result["episodes"][0]["source_id"], source)
                self.assertEqual(strip_episode_source(result), baseline)
                self.assertEqual(original, before)

    def test_video_and_source_can_differ_without_using_display_job_identity(self):
        original = event(job_id="display-recovery-job", source_id="untrusted-top-level-source",
                         provenance={"source_id": "recorded-inference-source"})
        before = deepcopy(original)
        records = [indicator(job_id="display-recovery-job", provenance={"source_id": "different-record-source"})]
        result = self.project([original], records)
        episode = result["episodes"][0]
        self.assertEqual(episode["video_id"], "original-video")
        self.assertEqual(episode["source_id"], "recorded-inference-source")
        self.assertNotIn("job_id", episode)
        self.assertEqual(result["episode_count"], 1)
        self.assertEqual(result["rejected_indicator_count"], 0)
        self.assertEqual(original, before)
        for field in ("provenance", "source_id"):
            absent = deepcopy(original)
            absent.pop(field)
            if field == "source_id":
                absent["provenance"] = {}
            self.assertNotIn("source_id", self.project([absent], records)["episodes"][0])

    def test_same_event_in_separate_sources_retains_distinct_identity(self):
        first = self.project([event(provenance={"source_id": "source-A"})])
        second = self.project([event(provenance={"source_id": "source-B"})])
        self.assertEqual(first["episodes"][0]["event_id"], second["episodes"][0]["event_id"])
        self.assertEqual((first["episodes"][0]["source_id"], second["episodes"][0]["source_id"]),
                         ("source-A", "source-B"))
        self.assertEqual(strip_episode_source(first), strip_episode_source(second))
        # This only preserves the discriminator for the frontend; it is not a
        # frontend matching test or authorization to join these two sources.

    def test_missing_or_invalid_source_is_omitted_with_identical_legacy_output(self):
        baseline = self.project([event()])
        invalid = [None, False, 7, 2.5, [], {}, "", " \t\r\n", "x" * 257]
        for source in invalid:
            with self.subTest(type=type(source).__name__, string_length=len(source) if isinstance(source, str) else None):
                original = event(provenance={"source_id": source})
                before = deepcopy(original)
                result = self.project([original])
                self.assertEqual(result, baseline)
                self.assertNotIn("source_id", result["episodes"][0])
                self.assertEqual(original, before)
        for provenance in (None, False, "recorded-source", [], {}):
            with self.subTest(provenance_type=type(provenance).__name__):
                original = event(provenance=provenance)
                before = deepcopy(original)
                self.assertEqual(self.project([original]), baseline)
                self.assertEqual(original, before)

    def test_existing_event_and_indicator_filters_and_counts_are_unchanged(self):
        events = [event(), event(event_id="wrong-video", video_id="other-video"),
                  event(event_id="bad-interval", end_ms=100), event(event_id="other-family", event_code="FS03")]
        records = [indicator(), indicator(person_track_id=2), indicator(event_id="absent")]
        baseline = self.project(events, records)
        self.assertEqual(baseline["episode_count"], 1)
        self.assertEqual(baseline["rejected_event_count"], 3)
        self.assertEqual(baseline["rejected_indicator_count"], 2)
        for source in ("recorded-source", None):
            augmented = deepcopy(events)
            for row in augmented:
                row["provenance"] = {"source_id": source}
            before = deepcopy((augmented, records))
            result = self.project(augmented, records)
            self.assertEqual(strip_episode_source(result), baseline)
            self.assertEqual((augmented, records), before)
            self.assertNotIn('"grade"', json.dumps(result))
            self.assertNotIn('"score"', json.dumps(result))

    def test_duplicate_conflict_policy_is_not_redefined_by_additive_source(self):
        original = event(provenance={"source_id": "source-A"})
        collapsed = self.project([original, deepcopy(original)])
        self.assertEqual(collapsed["episode_count"], 1)
        self.assertEqual(collapsed["episodes"][0]["source_id"], "source-A")
        different = event(provenance={"source_id": "source-B"})
        before = deepcopy((original, different))
        result = self.project([original, different])
        self.assertEqual(result["episodes"], [])
        self.assertEqual(result["rejected_event_count"], 1)
        self.assertEqual(result["rejected_indicator_count"], 1)
        self.assertEqual((original, different), before)

    def test_truncated_episode_order_and_total_counts_are_unchanged(self):
        events = [event(event_id=f"event-{index}", start_ms=start, end_ms=start + 100)
                  for index, start in enumerate((700, 100, 400))]
        with patch("rallymate_service.footwork_review.MAX_RETURNED_EPISODES", 1):
            baseline = self.project(events, [])
            augmented = deepcopy(events)
            for index, row in enumerate(augmented):
                row["provenance"] = {"source_id": f"source-{index}"}
            before = deepcopy(augmented)
            result = self.project(augmented, [])
        self.assertEqual(strip_episode_source(result), baseline)
        self.assertEqual(result["episode_count"], 3)
        self.assertEqual(result["returned_episode_count"], 1)
        self.assertIs(result["is_truncated"], True)
        self.assertEqual(result["episodes"][0]["source_id"], "source-1")
        self.assertEqual(augmented, before)

    def test_model_evidence_and_unknown_confidence_are_unchanged(self):
        original, record = model_fixture()
        baseline = build_footwork_review([original], [record], video_id="video-a")
        original["provenance"] = {"source_id": "original-model-source"}
        before = deepcopy((original, record))
        result = build_footwork_review([original], [record], video_id="video-a")
        self.assertEqual(strip_episode_source(result), baseline)
        episode = result["episodes"][0]
        self.assertEqual(episode["source_id"], "original-model-source")
        self.assertEqual(episode["person_track_id"], 1)
        self.assertEqual(episode["source_window"]["person_track_id"], 17)
        self.assertEqual(result["measurement_summary"]["measured_feature_count"], 2)
        for item in episode["indicators"][0]["measurements"]:
            if item["status"] == "measured":
                self.assertIsNone(item["confidence"])
        self.assertEqual((original, record), before)

    def test_saved_event_reader_preserves_input_bytes_and_same_projection(self):
        original = event(provenance={"source_id": "original-saved-source"})
        payload = (json.dumps(original) + "\n").encode("utf-8")
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "events.jsonl"
            path.write_bytes(payload)
            result = load_footwork_review(path, [indicator()], video_id="original-video")
            self.assertEqual(result, self.project([original]))
            self.assertEqual(result["episodes"][0]["source_id"], "original-saved-source")
            self.assertEqual(path.read_bytes(), payload)


if __name__ == "__main__":
    unittest.main()
