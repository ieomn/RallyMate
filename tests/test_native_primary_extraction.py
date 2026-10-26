"""Pure native-adapter extraction parity; no publication, database or inference."""
from __future__ import annotations

import ast
import copy
import hashlib
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from rallymate_features import native_primary


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "native_primary_publisher_compatibility", ROOT / "scripts/publish_foot_crossing_candidates.py"
)
publisher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(publisher)
VIDEO_SHA = "a" * 64


def inputs():
    records, timeline = [], []
    for index in (159, 160):
        timestamp = round(index * 1000 / 30)
        binding = {
            "frame_index": index, "processed_index": index, "video_sha256": VIDEO_SHA,
            "pts": index * 20, "time_base_num": 1, "time_base_den": 600,
            "timestamp_ms": timestamp, "relative_timestamp_ms": timestamp,
            "timestamp_source": "decoder_pts", "person_track_id": 8,
            "selection_epoch": 7, "camera_reference_epoch": 3,
        }
        records.append({
            "frame": {"index": index, "processed_index": index, "video_sha256": VIDEO_SHA,
                      "pts": index * 20, "time_base_num": 1, "time_base_den": 600,
                      "source_timestamp_ms": timestamp, "timestamp_ms": timestamp,
                      "timestamp_source": "decoder_pts"},
            "camera_motion": {"reference_epoch": 3},
            "poses": [{"source": "sam3d", "status": "ok", "person_track_id": 8,
                       "source_binding": binding}],
            "sam3d_requests": [{"person_track_id": 8, "status": "ok", "selection_epoch": 7}],
        })
        timeline.append({
            "schema_version": "1.0.0", "selection_algorithm_version": "primary-player-v0.3.0",
            "selection_status": "selected", "identity_ambiguous": False,
            "primary_player_id": 1, "source_track_id": 8,
            "processed_index": index, "source_frame_index": index, "timestamp_ms": timestamp,
        })
    return records, timeline


class NativePrimaryExtractionTests(unittest.TestCase):
    def call(self, records, timeline):
        return native_primary.adapt_native_primary_timeline(
            records, timeline, video_sha256=VIDEO_SHA
        )

    def test_accepted_function_body_ast_has_canonical_empty_fields(self):
        # Python 3.13 changed ast.dump's default empty-list formatting. The
        # archived publisher body and extracted body match with empty fields
        # explicit on every Python version; this pins that same frozen body.
        source = Path(native_primary.__file__).read_text(encoding="utf-8")
        node = next(node for node in ast.parse(source).body
                    if isinstance(node, ast.FunctionDef) and node.name == "adapt_native_primary_timeline")
        options = {"show_empty": True} if sys.version_info >= (3, 13) else {}
        body = ast.dump(ast.Module(body=node.body, type_ignores=[]), include_attributes=False, **options)
        self.assertEqual(hashlib.sha256(body.encode()).hexdigest(),
                         "76b399e69d440ad9c19248f4b52772df52c96ca194bc912530afc2655db9f473")

    def test_join_uses_frame_identity_not_input_order_or_other_track(self):
        records, timeline = inputs()
        expected, _ = self.call(records, timeline)
        records.reverse()
        for record in records:
            other_pose = copy.deepcopy(record["poses"][0])
            other_pose["person_track_id"] = 99
            other_pose["source_binding"].update(person_track_id=99, selection_epoch=999)
            record["poses"].insert(0, other_pose)
            record["sam3d_requests"].insert(0, {"person_track_id": 99, "status": "ok", "selection_epoch": 999})
        original = copy.deepcopy((records, timeline))
        adapted, report = self.call(records, timeline)
        self.assertEqual(adapted, expected)
        self.assertEqual((records, timeline), original)
        self.assertEqual(report["adapted_rows"], 2)
        repeated, repeat_report = self.call(records, adapted)
        self.assertEqual(repeated, adapted)
        self.assertEqual(repeat_report["adapted_rows"], 0)

    def test_adds_only_proven_epochs_without_changing_input(self):
        records, timeline = inputs()
        original = copy.deepcopy((records, timeline))
        adapted, report = self.call(records, timeline)
        self.assertEqual((records, timeline), original)
        self.assertIsNot(adapted, timeline)
        for before, after in zip(timeline, adapted):
            self.assertIsNot(before, after)
            self.assertEqual(after, {**before, "selection_epoch": 7, "camera_reference_epoch": 3})
        self.assertEqual(report, {
            "adapter_version": "native-sam-primary-epochs-v1.0.0", "input_rows": 2,
            "adapted_rows": 2, "added_selection_epochs": 2, "added_camera_reference_epochs": 2,
            "evidence": "unique_same_frame_selected_track_SAM_request_and_pose_binding_and_camera",
            "source_files_changed": False, "not_adapted_reasons": {},
        })

    def test_publisher_wrapper_returns_identical_values(self):
        records, timeline = inputs()
        self.assertEqual(self.call(records, timeline), publisher._adapt_primary_timeline(
            records, timeline, video_sha256=VIDEO_SHA))
        self.assertEqual(native_primary.PRIMARY_TIMELINE_ADAPTER_VERSION,
                         publisher.PRIMARY_TIMELINE_ADAPTER_VERSION)
        self.assertIn("src/rallymate_features/native_primary.py", publisher.REQUIRED_CODE)

    def test_existing_matching_epochs_are_preserved_and_not_counted(self):
        records, timeline = inputs()
        for row in timeline:
            row.update(selection_epoch=7, camera_reference_epoch=3)
        adapted, report = self.call(records, timeline)
        self.assertEqual(adapted, timeline)
        self.assertEqual(report["adapted_rows"], 0)
        self.assertEqual(report["added_selection_epochs"], 0)
        self.assertEqual(report["added_camera_reference_epochs"], 0)

    def test_missing_proof_stays_unbound(self):
        cases = [
            (lambda r, t: r[0].pop("sam3d_requests"), "request_or_pose_evidence_missing"),
            (lambda r, t: r[0].update(sam3d_requests=[]), "selected_track_request_or_pose_missing"),
            (lambda r, t: r[0]["sam3d_requests"][0].update(person_track_id=9),
             "selected_track_request_or_pose_missing"),
            (lambda r, t: r[0]["poses"][0].update(status="failed"), "selected_SAM_evidence_unavailable"),
            (lambda r, t: r[0]["camera_motion"].pop("reference_epoch"),
             "epoch_or_source_binding_evidence_missing"),
        ]
        for mutate, reason in cases:
            with self.subTest(reason=reason):
                records, timeline = inputs()
                mutate(records, timeline)
                original = copy.deepcopy((records, timeline))
                adapted, report = self.call(records, timeline)
                self.assertEqual(adapted[0], timeline[0])
                self.assertNotIn("selection_epoch", adapted[0])
                self.assertEqual(report["not_adapted_reasons"], {reason: 1})
                self.assertEqual(report["adapted_rows"], 1)
                self.assertEqual((records, timeline), original)

    def assert_conflict(self, mutate, reason):
        records, timeline = inputs()
        mutate(records, timeline)
        original = copy.deepcopy((records, timeline))
        with self.assertRaises(native_primary.NativePrimaryTimelineError) as native:
            self.call(records, timeline)
        with self.assertRaises(publisher.PublicationError) as legacy:
            publisher._adapt_primary_timeline(records, timeline, video_sha256=VIDEO_SHA)
        self.assertEqual(str(native.exception), reason)
        self.assertEqual(str(legacy.exception), reason)
        self.assertIsInstance(legacy.exception.__cause__, native_primary.NativePrimaryTimelineError)
        self.assertEqual((records, timeline), original)

    def test_clock_identity_and_epoch_conflicts_keep_legacy_errors(self):
        cases = [
            (lambda r, t: r[0]["poses"][0]["source_binding"].update(pts=1),
             "native_primary_decoder_binding_conflict"),
            (lambda r, t: r[0]["poses"][0]["source_binding"].update(video_sha256="b" * 64),
             "native_primary_video_identity_conflict"),
            (lambda r, t: r[0]["sam3d_requests"][0].update(selection_epoch=8),
             "native_primary_selection_epoch_conflict"),
            (lambda r, t: r[0]["camera_motion"].update(reference_epoch=4),
             "native_primary_camera_epoch_conflict"),
            (lambda r, t: t[0].update(timestamp_ms=1),
             "native_primary_binding_primary_timeline_frame_or_video_time_mismatch"),
        ]
        for mutate, reason in cases:
            with self.subTest(reason=reason):
                self.assert_conflict(mutate, reason)

    def test_present_null_bool_or_different_epoch_is_not_missing(self):
        for key in ("selection_epoch", "camera_reference_epoch"):
            for value in (None, False, True, 99):
                with self.subTest(key=key, value=value):
                    self.assert_conflict(lambda r, t: t[0].update({key: value}),
                                         "native_primary_existing_epoch_conflict")

    def test_duplicate_joins_remain_rejected(self):
        for mutate, reason in [
            (lambda r, t: t.append(copy.deepcopy(t[0])), "native_primary_frame_join_ambiguous"),
            (lambda r, t: r.append(copy.deepcopy(r[0])), "native_primary_frame_join_ambiguous"),
            (lambda r, t: r[1]["frame"].update(index=159), "native_primary_source_frame_duplicated"),
            (lambda r, t: r[0]["poses"].append(copy.deepcopy(r[0]["poses"][0])),
             "native_primary_request_or_pose_ambiguous"),
        ]:
            with self.subTest(reason=reason):
                self.assert_conflict(mutate, reason)

    def test_optional_request_clock_fields_cannot_conflict(self):
        for field, value in (("pts", 1), ("processed_index", True), ("video_sha256", "b" * 64)):
            with self.subTest(field=field):
                self.assert_conflict(lambda r, t: r[0]["sam3d_requests"][0].update({field: value}),
                                     "native_primary_request_binding_conflict")

    def test_unselected_or_different_schema_is_unchanged(self):
        for field, value, reason in [
            ("schema_version", "other", "different_input_schema_unchanged"),
            ("identity_ambiguous", True, "selected_identity_unavailable"),
            ("primary_player_id", 8, "selected_identity_unavailable"),
            ("selection_status", "missing", "selected_identity_unavailable"),
        ]:
            with self.subTest(field=field):
                records, timeline = inputs()
                timeline[0][field] = value
                adapted, report = self.call(records, timeline)
                self.assertEqual(adapted[0], timeline[0])
                self.assertEqual(report["not_adapted_reasons"], {reason: 1})

    def test_invalid_digest_is_mapped_to_original_exception(self):
        records, timeline = inputs()
        with self.assertRaisesRegex(native_primary.NativePrimaryTimelineError, "^invalid_expected_digest$"):
            native_primary.adapt_native_primary_timeline(records, timeline, video_sha256="invalid")
        with self.assertRaisesRegex(publisher.PublicationError, "^invalid_expected_digest$"):
            publisher._adapt_primary_timeline(records, timeline, video_sha256="invalid")

    def test_wrapper_does_not_hide_unrelated_errors(self):
        with patch.object(native_primary, "adapt_native_primary_timeline", side_effect=TypeError("fixture")):
            with self.assertRaisesRegex(TypeError, "^fixture$"):
                publisher._adapt_primary_timeline([], [], video_sha256=VIDEO_SHA)

    def test_pure_call_performs_no_file_io(self):
        records, timeline = inputs()
        with patch("builtins.open", side_effect=AssertionError("file IO forbidden")), \
             patch.object(Path, "open", side_effect=AssertionError("file IO forbidden")):
            adapted, report = self.call(records, timeline)
        self.assertEqual(len(adapted), 2)
        self.assertEqual(report["adapted_rows"], 2)


if __name__ == "__main__":
    unittest.main()
