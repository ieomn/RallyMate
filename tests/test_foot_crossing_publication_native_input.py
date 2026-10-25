"""Native business-input adapter checks on five separate saved two-frame pairs.

These are offline CPU fixtures, not a 97-second replay or an accuracy test.
Original events are deliberately absent: every default-producer check uses [].
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from rallymate_features.coordinates import pose_sequence_from_records
from rallymate_features.foot_crossing import inspect_foot_crossing_pair
from rallymate_service import foot_crossing_candidates as reader


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/foot-crossing-native-business-pairs.json"
SPEC = importlib.util.spec_from_file_location(
    "native_foot_crossing_publication_under_test",
    ROOT / "scripts/publish_foot_crossing_candidates.py",
)
publisher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(publisher)
VIDEO_SHA = "71d3f59b7a8b966ef7645cac412cb03679376f70a2f080d270391a32697d7fa9"
SOURCE_JOB = "e4204ec3-7d84-4a90-a961-9dad01b43c2e"
DISPLAY_JOB = "66883aeb-e0c3-4065-80fd-f67aa108b208"


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


class NativeFootCrossingPublicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = json.loads(FIXTURE.read_bytes())

    def pair(self, first=0):
        pair = next(row for row in self.fixture["pairs"]
                    if row["source_frame_indices"] == [first, first + 1])
        records = [json.loads(row["frame"]["original_jsonl_line"])
                   for row in pair["samples"]]
        timeline = [json.loads(row["primary"]["original_jsonl_line"])
                    for row in pair["samples"]]
        self.assertEqual(len(records), 2)
        self.assertEqual(len(timeline), 2)
        return records, timeline

    def adapt(self, records, timeline):
        adapted, report = publisher._adapt_primary_timeline(
            records, timeline, video_sha256=VIDEO_SHA)
        self.assertIsInstance(report, dict)
        self.assertEqual(len(adapted), len(timeline))
        return adapted, report

    def record_observation(self, first, sequence, result, adapted):
        """Emit only bounded test observations for the private validation receipt."""
        print("NATIVE_PAIR_OBSERVATION " + json.dumps({
            "source_frame_indices": [first, first + 1],
            "bound_source_frames": sorted(sequence.source_time_bindings),
            "selected_track_ids": [row.get("source_track_id") for row in adapted],
            "selection_epochs": [row.get("selection_epoch") for row in adapted],
            "camera_reference_epochs": [row.get("camera_reference_epoch") for row in adapted],
            "pair_status": result["status"], "pair_reason": result["reason"],
            "all_source_times_ms": sequence.timestamp_ms.tolist(),
            "bound_native_pts": {str(k): v["pts"] for k, v in sequence.source_time_bindings.items()},
            "full_video_scope": False,
        }, sort_keys=True))

    def assert_conflict(self, mutate):
        records, timeline = self.pair()
        mutate(records, timeline)
        before = copy.deepcopy((records, timeline))
        with self.assertRaises(publisher.PublicationError):
            self.adapt(records, timeline)
        self.assertEqual((records, timeline), before)

    def assert_unavailable(self, mutate):
        records, timeline = self.pair()
        mutate(records, timeline)
        before = copy.deepcopy((records, timeline))
        adapted, report = self.adapt(records, timeline)
        self.assertNotIn("selection_epoch", adapted[0])
        self.assertNotIn("camera_reference_epoch", adapted[0])
        self.assertEqual(report["adapted_rows"], 1)
        self.assertEqual(sum(report["not_adapted_reasons"].values()), 1)
        sequence = pose_sequence_from_records(records, adapted)
        self.assertNotIn(0, sequence.source_time_bindings)
        result = inspect_foot_crossing_pair(sequence, 0, primary_timeline=adapted)
        self.assertEqual((result["status"], result["reason"]),
                         ("excluded", "source_time_binding_unavailable"))
        self.assertEqual((records, timeline), before)

    def test_fixture_keeps_exact_original_lines_and_full_file_provenance(self):
        self.assertEqual(self.fixture["schema_version"],
                         "foot-crossing-native-business-pairs-fixture-v1")
        provenance = self.fixture["provenance"]
        self.assertEqual(provenance["source_job_id"], SOURCE_JOB)
        self.assertEqual(provenance["display_job_id"], DISPLAY_JOB)
        self.assertEqual(provenance["video_sha256"], VIDEO_SHA)
        expected = {
            "frames": (138799669, "6fee1cf13414306ea8019960d777b6849c7d6a0dcb80701625bff9100fccafd7"),
            "primary": (1891825, "09a27e0fa7e67f7b55d336eb5c9f8c1e35ce030d137f7987b5d2bdbf6947bdeb"),
        }
        for key, (size, digest) in expected.items():
            pin = provenance["source_inputs"][key]
            self.assertEqual((pin["bytes"], pin["sha256"], pin["line_count"]),
                             (size, digest, 2911))
        self.assertEqual([p["source_frame_indices"] for p in self.fixture["pairs"]],
                         [[0, 1], [170, 171], [637, 638], [674, 675], [821, 822]])
        for pair in self.fixture["pairs"]:
            for index, sample in zip(pair["source_frame_indices"], pair["samples"]):
                for key in ("frame", "primary"):
                    evidence = sample[key]
                    raw = evidence["original_jsonl_line"].encode("utf-8")
                    self.assertEqual(evidence["line_number_1based"], index + 1)
                    self.assertTrue(raw.endswith(b"\n"))
                    self.assertEqual(sha(raw), evidence["line_sha256_including_newline"])
                record = json.loads(sample["frame"]["original_jsonl_line"])
                primary = json.loads(sample["primary"]["original_jsonl_line"])
                self.assertEqual(record["frame"]["index"], index)
                self.assertEqual(primary["source_frame_index"], index)
                self.assertNotIn("selection_epoch", primary)

    def assert_baseline_unavailable(self, first):
        records, timeline = self.pair(first)
        sequence = pose_sequence_from_records(records, timeline)
        self.assertEqual(sequence.source_time_bindings, {})
        result = inspect_foot_crossing_pair(sequence, 0, primary_timeline=timeline)
        self.assertEqual((result["status"], result["reason"]),
                         ("excluded", "source_time_binding_unavailable"))

    def test_native_pair_0_1_baseline_has_no_source_time_binding(self):
        self.assert_baseline_unavailable(0)

    def test_native_pair_170_171_baseline_has_no_source_time_binding(self):
        self.assert_baseline_unavailable(170)

    def assert_adapted_pair(self, first):
        records, timeline = self.pair(first)
        before = copy.deepcopy((records, timeline))
        adapted, report = self.adapt(records, timeline)
        self.assertIsNot(adapted, timeline)
        self.assertEqual(report["adapted_rows"], 2)
        self.assertEqual(report["added_selection_epochs"], 2)
        self.assertEqual(report["added_camera_reference_epochs"], 2)
        self.assertFalse(report["source_files_changed"])
        sequence = pose_sequence_from_records(records, adapted)
        self.assertEqual(set(sequence.source_time_bindings), {first, first + 1})
        for record, old, selected in zip(records, timeline, adapted):
            frame = record["frame"]
            native = record["poses"][0]["source_binding"]
            binding = sequence.source_time_bindings[frame["index"]]
            self.assertEqual(selected, {
                **old, "selection_epoch": native["selection_epoch"],
                "camera_reference_epoch": native["camera_reference_epoch"],
            })
            self.assertIsNot(selected, old)
            for key in ("pts", "time_base_num", "time_base_den", "processed_index"):
                self.assertEqual(binding[key], frame[key])
            self.assertEqual(binding["frame_index"], frame["index"])
            self.assertEqual(binding["video_sha256"], VIDEO_SHA)
            self.assertEqual(binding["source_timestamp_ms"], frame["source_timestamp_ms"])
            self.assertEqual(binding["video_timestamp_ms"], frame["timestamp_ms"])
            self.assertEqual(binding["person_track_id"], old["source_track_id"])
            self.assertEqual(binding["selection_epoch"], native["selection_epoch"])
            self.assertEqual(binding["camera_reference_epoch"], native["camera_reference_epoch"])
        result = inspect_foot_crossing_pair(sequence, 0, primary_timeline=adapted)
        self.assertNotEqual(result["reason"], "source_time_binding_unavailable")
        self.assertEqual((records, timeline), before)
        self.record_observation(first, sequence, result, adapted)

    def test_native_pair_0_1_adapts_without_changing_original_observations(self):
        self.assert_adapted_pair(0)

    def test_native_pair_170_171_retains_native_identity_and_time(self):
        self.assert_adapted_pair(170)

    def test_real_637_638_does_not_borrow_another_tracks_native_epoch(self):
        records, timeline = self.pair(637)
        before = copy.deepcopy((records, timeline))
        self.assertEqual(timeline[1]["source_track_id"], 47)
        self.assertEqual([p["person_track_id"] for p in records[1]["poses"]], [59])
        self.assertEqual([r["person_track_id"] for r in records[1]["sam3d_requests"]], [59])
        adapted, report = self.adapt(records, timeline)
        self.assertEqual(report["adapted_rows"], 1)
        self.assertEqual(report["not_adapted_reasons"],
                         {"selected_track_request_or_pose_missing": 1})
        self.assertEqual(adapted[1], timeline[1])
        self.assertNotIn("selection_epoch", adapted[1])
        self.assertNotIn("camera_reference_epoch", adapted[1])
        sequence = pose_sequence_from_records(records, adapted)
        self.assertEqual(set(sequence.source_time_bindings), {637})
        result = inspect_foot_crossing_pair(sequence, 0, primary_timeline=adapted)
        self.assertEqual((result["status"], result["reason"]),
                         ("excluded", "source_time_binding_unavailable"))
        self.assertEqual((records, timeline), before)
        self.record_observation(637, sequence, result, adapted)

    def test_real_674_675_missing_primary_and_skipped_request_stay_unbound(self):
        records, timeline = self.pair(674)
        before = copy.deepcopy((records, timeline))
        self.assertEqual(timeline[1]["selection_status"], "missing")
        self.assertIsNone(timeline[1]["source_track_id"])
        self.assertEqual(records[1]["poses"], [])
        request = records[1]["sam3d_requests"][0]
        self.assertEqual((request["status"], request["reason"]),
                         ("skipped", "no_detected_subject"))
        self.assertIsNone(request["person_track_id"])
        self.assertIsNone(request["selection_epoch"])
        adapted, report = self.adapt(records, timeline)
        self.assertEqual(report["adapted_rows"], 1)
        self.assertEqual(report["not_adapted_reasons"], {"selected_identity_unavailable": 1})
        self.assertEqual(adapted[1], timeline[1])
        self.assertNotIn("selection_epoch", adapted[1])
        self.assertNotIn("camera_reference_epoch", adapted[1])
        sequence = pose_sequence_from_records(records, adapted)
        self.assertEqual(set(sequence.source_time_bindings), {674})
        result = inspect_foot_crossing_pair(sequence, 0, primary_timeline=adapted)
        self.assertEqual((result["status"], result["reason"]),
                         ("excluded", "source_time_binding_unavailable"))
        self.assertEqual((records, timeline), before)
        self.record_observation(674, sequence, result, adapted)

    def test_real_821_822_bound_identity_and_epoch_change_is_not_continuous(self):
        records, timeline = self.pair(821)
        before = copy.deepcopy((records, timeline))
        self.assertEqual([row["source_track_id"] for row in timeline], [70, 47])
        self.assertEqual([row["poses"][0]["source_binding"]["selection_epoch"]
                          for row in records], [0, 1])
        adapted, report = self.adapt(records, timeline)
        self.assertEqual(report["adapted_rows"], 2)
        self.assertEqual(report["not_adapted_reasons"], {})
        self.assertEqual([row["selection_epoch"] for row in adapted], [0, 1])
        self.assertEqual([row["source_track_id"] for row in adapted], [70, 47])
        sequence = pose_sequence_from_records(records, adapted)
        self.assertEqual(set(sequence.source_time_bindings), {821, 822})
        result = inspect_foot_crossing_pair(sequence, 0, primary_timeline=adapted)
        self.assertEqual((result["status"], result["reason"]),
                         ("excluded", "subject_identity_changed"))
        self.assertEqual((records, timeline), before)
        self.record_observation(821, sequence, result, adapted)

    def assert_default_producer(self, first):
        pair = next(row for row in self.fixture["pairs"]
                    if row["source_frame_indices"] == [first, first + 1])
        with tempfile.TemporaryDirectory() as temporary:
            staging = Path(temporary)
            source_bytes = {
                "frames.jsonl": "".join(s["frame"]["original_jsonl_line"]
                                       for s in pair["samples"]).encode("utf-8"),
                "primary-player.jsonl": "".join(s["primary"]["original_jsonl_line"]
                                               for s in pair["samples"]).encode("utf-8"),
                # No original event context: this is a two-frame geometry test.
                "events.jsonl": b"",
            }
            for name, raw in source_bytes.items():
                (staging / name).write_bytes(raw)
            job = {"id": DISPLAY_JOB, "summary": {"minimum_scoring_loop": {
                "video_id": SOURCE_JOB, "provenance": {"video_sha256": VIDEO_SHA},
            }}}
            inputs = {"frames": {"sha256": sha(source_bytes["frames.jsonl"])}}
            # No injected producer, mocked loader or mocked candidate writer.
            diagnostic = publisher._produce(staging, job, inputs, reader)
            self.assertIsInstance(diagnostic, dict)
            self.assertEqual(diagnostic["bound_frame_count"], 2)
            self.assertEqual(diagnostic["unavailable_frame_reasons"], {})
            artifact = json.loads((staging / reader.ARTIFACT_NAME).read_bytes())
            self.assertEqual(artifact["counts"]["frames"], 2)
            self.assertEqual(artifact["counts"]["adjacent_sequence_pairs"], 1)
            self.assertEqual(artifact["counts"]["reasons"].get(
                "source_time_binding_unavailable", 0), 0)
            self.assertEqual(artifact["frames_sha256"], sha(source_bytes["frames.jsonl"]))
            self.assertEqual(artifact["primary_timeline_sha256"],
                             sha(source_bytes["primary-player.jsonl"]))
            self.assertEqual(artifact["events_sha256"], sha(b""))
            self.assertFalse(artifact["scoring_allowed"])
            for name, raw in source_bytes.items():
                self.assertEqual((staging / name).read_bytes(), raw)

    def test_default_produce_0_1_uses_adapter_and_keeps_staged_input_bytes(self):
        self.assert_default_producer(0)

    def test_default_produce_170_171_is_a_separate_pair_without_event_context(self):
        self.assert_default_producer(170)

    def test_existing_complete_native_epochs_are_validated_and_preserved(self):
        records, timeline = self.pair(170)
        for record, row in zip(records, timeline):
            binding = record["poses"][0]["source_binding"]
            row["selection_epoch"] = binding["selection_epoch"]
            row["camera_reference_epoch"] = binding["camera_reference_epoch"]
        before = copy.deepcopy((records, timeline))
        adapted, _ = self.adapt(records, timeline)
        self.assertEqual(adapted, timeline)
        self.assertEqual((records, timeline), before)

    def test_wrong_native_pts_timebase_or_source_time_is_rejected(self):
        for key, value in (("pts", 20), ("time_base_num", 2),
                           ("time_base_den", 300), ("source_timestamp_ms", 9)):
            with self.subTest(field=key):
                self.assert_conflict(lambda r, t, k=key, v=value: r[0]["frame"].__setitem__(k, v))

    def test_wrong_video_hash_in_frame_or_pose_is_rejected(self):
        for target in ("frame", "pose"):
            def mutate(records, timeline):
                row = records[0]["frame"] if target == "frame" else records[0]["poses"][0]["source_binding"]
                row["video_sha256"] = "a" * 64
            with self.subTest(target=target):
                self.assert_conflict(mutate)

    def test_wrong_primary_frame_processed_index_or_video_time_is_rejected(self):
        for key, value in (("source_frame_index", 7), ("processed_index", 7), ("timestamp_ms", 7)):
            with self.subTest(field=key):
                self.assert_conflict(lambda r, t, k=key, v=value: t[0].__setitem__(k, v))

    def test_wrong_native_frame_binding_is_rejected(self):
        for key in ("frame_index", "processed_index", "timestamp_ms", "relative_timestamp_ms"):
            with self.subTest(field=key):
                self.assert_conflict(lambda r, t, k=key: r[0]["poses"][0]["source_binding"].__setitem__(k, 7))

    def test_missing_selected_track_stays_unbound_and_bound_track_conflict_raises(self):
        for target in ("primary", "ledger", "pose_binding"):
            def mutate(records, timeline):
                if target == "primary":
                    timeline[0]["source_track_id"] = 99
                elif target == "ledger":
                    records[0]["sam3d_requests"][0]["person_track_id"] = 99
                else:
                    records[0]["poses"][0]["source_binding"]["person_track_id"] = 99
            with self.subTest(target=target):
                if target in ("primary", "ledger"):
                    # No matching selected-track evidence is not permission to
                    # borrow another track's epoch. It stays explicitly unbound.
                    self.assert_unavailable(mutate)
                else:
                    # A matched envelope with a different internal track is a
                    # contradiction, not missing evidence.
                    self.assert_conflict(mutate)

    def test_wrong_selection_epoch_is_not_overwritten(self):
        for target in ("primary", "ledger", "pose_binding"):
            def mutate(records, timeline):
                row = timeline[0] if target == "primary" else (
                    records[0]["sam3d_requests"][0] if target == "ledger"
                    else records[0]["poses"][0]["source_binding"])
                row["selection_epoch"] = 9
            with self.subTest(target=target):
                self.assert_conflict(mutate)

    def test_wrong_camera_epoch_is_not_overwritten(self):
        for target in ("primary", "camera", "pose_binding"):
            def mutate(records, timeline):
                if target == "camera":
                    records[0]["camera_motion"]["reference_epoch"] = 9
                else:
                    row = timeline[0] if target == "primary" else records[0]["poses"][0]["source_binding"]
                    row["camera_reference_epoch"] = 9
            with self.subTest(target=target):
                self.assert_conflict(mutate)

    def test_explicit_none_and_boolean_epochs_cannot_be_treated_as_missing(self):
        for key in ("selection_epoch", "camera_reference_epoch"):
            for value in (None, False, True):
                with self.subTest(field=key, value=value):
                    self.assert_conflict(lambda r, t, k=key, v=value: t[0].__setitem__(k, v))

    def test_absent_ledger_or_pose_cannot_create_epochs(self):
        for key in ("sam3d_requests", "poses"):
            with self.subTest(field=key):
                self.assert_unavailable(lambda r, t, k=key: r[0].pop(k))

    def test_missing_native_epoch_does_not_default_to_zero(self):
        for target in ("ledger", "pose_binding"):
            def mutate(records, timeline):
                row = records[0]["sam3d_requests"][0] if target == "ledger" else records[0]["poses"][0]["source_binding"]
                row.pop("selection_epoch")
            with self.subTest(target=target):
                self.assert_unavailable(mutate)

    def test_synthetic_epoch_transition_on_native_pair_remains_a_boundary(self):
        records, timeline = self.pair(170)
        records[1]["sam3d_requests"][0]["selection_epoch"] = 1
        records[1]["poses"][0]["source_binding"]["selection_epoch"] = 1
        adapted, _ = self.adapt(records, timeline)
        self.assertEqual([r["selection_epoch"] for r in adapted], [0, 1])
        sequence = pose_sequence_from_records(records, adapted)
        result = inspect_foot_crossing_pair(sequence, 0, primary_timeline=adapted)
        self.assertEqual(result["reason"], "subject_identity_changed")


if __name__ == "__main__":
    unittest.main()
