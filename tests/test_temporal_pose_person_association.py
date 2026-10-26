"""Source-local duplicate-person hard gates, with unchanged numeric formulas."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import unittest

import numpy as np

from rallymate_features.coordinates import pose_sequence_from_records
from rallymate_features.source_alignment import clear_source_aligned_feature_cache, prepare_source_aligned_context
from rallymate_scoring.temporal_card_evidence import compute_temporal_card_evidence
from rallymate_service.card_execution import build_card_execution_from_records
from tests.test_temporal_card_evidence import fixture, card


def original_records():
    """Explicit synthetic detector records; no media/inference claim."""
    sequence, events, primary = fixture()
    records = []
    for i, timestamp in enumerate(sequence.timestamp_ms):
        frame = {"index": i, "processed_index": i, "timestamp_ms": int(timestamp),
            "source_timestamp_ms": int(timestamp), "timestamp_source": "decoder_pts", "width": 1280, "height": 1280,
            "video_sha256": "a"*64, "pts": int(timestamp), "time_base_num": 1, "time_base_den": 1000}
        pose = {"person_track_id": 1, "source": "yolo_pose", "coordinate_space": "original_frame",
            "keypoints": [{"name": name, "x_px": float(xy[i, 0]*1280), "y_px": float(xy[i, 1]*1280),
                "confidence": .95, "in_frame": True, "confidence_in_range": True} for name, xy in sequence.keypoints_xy.items()]}
        records.append({"job_id": "test-video", "frame": frame, "poses": [pose],
            "detections": [{"class_name": "player", "track_id": 1, "bbox_px": [200, 150, 1080, 1200]}],
            "camera_motion": {"status": "stationary", "reference_epoch": 0, "compensation_valid": True,
                "matrix_to_reference": [[1., 0., 0.], [0., 1., 0.]]}})
        primary[i].update(processed_index=i, selection_epoch=0, camera_reference_epoch=0)
    return records, events, primary


def duplicate(records, index, *, selected_rejected=True, other_only=False):
    record = records[index]
    first = deepcopy(record["poses"][0])
    if other_only:
        for point in first["keypoints"]: point["x_px"] += 50
        first["person_track_id"] = 2
        second = deepcopy(first); second["person_track_id"] = 3
        record["poses"].extend([first, second])
        record["detections"].extend({"class_name": "player", "track_id": track, "bbox_px": [250, 150, 1130, 1200]} for track in (2, 3))
    else:
        first["person_track_id"] = 2; record["poses"].append(first)
        record["detections"].append({"class_name": "player", "track_id": 2,
            "bbox_px": [200, 150, 1080, 1200] if selected_rejected else [0, 0, 150, 150]})


class TemporalPosePersonAssociationTests(unittest.TestCase):
    def setUp(self):
        clear_source_aligned_feature_cache()
        self.addCleanup(clear_source_aligned_feature_cache)

    def compute(self, records, events, primary, event_index=0):
        sequence = pose_sequence_from_records(records, primary)
        return sequence, compute_temporal_card_evidence(sequence, events[event_index], events=events, primary_timeline=primary)

    def test_raw_parser_computes_audit_and_does_not_trust_saved_clear_flags(self):
        records, events, primary = original_records(); duplicate(records, 2)
        records[2]["pose_person_association"] = {"rejected_person_track_ids": [], "conflicts": []}
        original = deepcopy(records)
        sequence, result = self.compute(records, events, primary)
        ledger = sequence.coordinate_metadata["source_pose_person_association"]["rows_by_source_frame"]
        self.assertEqual(ledger["2"]["audit"]["rejected_person_track_ids"], [1, 2])
        self.assertEqual(ledger["2"]["selected_source_track_id"], 1)
        self.assertEqual(records, original)
        self.assertEqual(card(result, "FS01-M02")["reason"], "selected_pose_person_association_conflict")

    def test_one_conflicted_frame_keeps_all_geometry_and_full_denominator_but_cannot_score(self):
        records, events, primary = original_records(); duplicate(records, 2)
        sequence, result = self.compute(records, events, primary)
        row = card(result, "FS01-M02")
        self.assertTrue(all(np.isfinite(xy).all() for xy in sequence.keypoints_xy.values()))
        self.assertEqual(row["evidence"]["source_frames"], [1, 2, 3, 4])
        self.assertEqual((row["evidence"]["coverage"]["valid_frame_count"], row["evidence"]["coverage"]["total_frame_count"]), (4, 4))
        self.assertEqual(row["evidence"]["pose_person_association"]["rejected_source_frames"], [2])
        self.assertTrue(all(c["passed"] is None for c in row["conditions"]))
        formal = build_card_execution_from_records(records, events, primary, source_run_id="test-video")
        instance = next(c for c in formal["cards"] if c["indicator_id"] == "FS01-M02")["instances"][0]
        self.assertEqual(instance["status"], "unavailable")
        self.assertTrue(all(instance.get(k) is None for k in ("grade", "proxy_grade", "score")))

    def test_preceding_witness_and_confirmation_are_consumed_but_unrelated_search_tail_is_not(self):
        for index in (0, 6):
            records, events, primary = original_records(); duplicate(records, index)
            _, result = self.compute(records, events, primary)
            row = card(result, "FS01-M02")
            self.assertEqual(row["evidence"]["source_frames"], [1, 2, 3, 4])
            self.assertEqual(row["reason"], "selected_pose_person_association_conflict")
            self.assertIn(index, row["evidence"]["phase_pose_person_association"]["rejected_source_frames"])
        records, events, primary = original_records(); duplicate(records, 10)
        _, result = self.compute(records, events, primary)
        self.assertEqual(card(result, "FS01-M02")["status"], "measured_visual_proxy")
        self.assertEqual(card(result, "FS01-M05")["status"], "measured_visual_proxy")

    def test_fallback_candidate_rejection_retains_full_support_and_reason(self):
        for index in (0, 2, 6):
            records, events, primary = original_records(); events[0]["key_phases_ms"].pop("takeoff_proxy_ms")
            duplicate(records, index)
            _, result = self.compute(records, events, primary)
            row = card(result, "FS01-M02"); fallback = row["evidence"]["fallback"]
            self.assertIsNone(fallback["selected"])
            self.assertEqual(row["reason"], "selected_pose_person_association_conflict")
            rejected = fallback["rejected_candidates"][0]
            self.assertIn(index, rejected["decision_support_source_frames"])
            self.assertIn(index, rejected["pose_person_association"]["rejected_source_frames"])
            self.assertTrue(all(c["passed"] is None for c in row["conditions"]))

    def test_fallback_outside_consumed_support_is_preserved_without_blocking(self):
        records, events, primary = original_records(); events[0]["key_phases_ms"].pop("takeoff_proxy_ms")
        duplicate(records, 15)
        _, result = self.compute(records, events, primary)
        row = card(result, "FS01-M02")
        self.assertEqual(row["status"], "measured_visual_proxy")
        self.assertNotIn(15, row["evidence"]["fallback"]["selected"]["decision_support_source_frames"])
        proof = next(r for r in row["evidence"]["fallback"]["proof"]["rows"] if r["frame_index"] == 15)
        self.assertEqual(proof["pose_person_association"]["audit"]["rejected_person_track_ids"], [1, 2])

    def test_successor_gap_conflict_does_not_pollute_other_stop_cards(self):
        records, events, primary = original_records()
        _, baseline = self.compute(records, events, primary, 2)
        duplicate(records, 33)
        _, result = self.compute(records, events, primary, 2)
        for identifier in ("FS09-M03", "FS09-M04"):
            self.assertEqual(card(result, identifier), card(baseline, identifier))
        row = card(result, "FS09-M05")
        self.assertEqual(row["reason"], "selected_pose_person_association_conflict")
        self.assertNotIn(33, row["evidence"]["source_frames"])
        self.assertEqual(row["evidence"]["successor"]["pose_person_association"]["rejected_source_frames"], [33])
        self.assertNotIn("successor_interval_ms", row["measurements"])

    def test_other_person_duplicate_and_unique_selected_owner_do_not_block(self):
        for options in ({"other_only": True}, {"selected_rejected": False}):
            records, events, primary = original_records(); duplicate(records, 2, **options)
            _, result = self.compute(records, events, primary)
            self.assertEqual(card(result, "FS01-M02")["status"], "measured_visual_proxy")

    def test_epoch_or_primary_numeric_id_is_not_used_as_raw_conflict_id(self):
        records, events, primary = original_records(); duplicate(records, 2)
        for record, selection in zip(records, primary):
            for pose in record["poses"]:
                if pose["person_track_id"] == 1: pose["person_track_id"] = 7
            for detection in record["detections"]:
                if detection["track_id"] == 1: detection["track_id"] = 7
            selection.update(source_track_id=7, selection_epoch=9)
        for event in events: event["track_diagnostics"]["source_track_ids"] = [7]
        _, result = self.compute(records, events, primary)
        row = card(result, "FS01-M02")
        self.assertEqual(row["reason"], "selected_pose_person_association_conflict")
        self.assertEqual(row["evidence"]["pose_person_association"]["selected_source_track_id"], 7)

    def test_metadata_survives_context_and_slice_without_global_block(self):
        records, events, primary = original_records(); duplicate(records, 33)
        sequence = pose_sequence_from_records(records, primary)
        sliced = sequence.slice(slice(0, 21))
        self.assertIn("33", sliced.coordinate_metadata["source_pose_person_association"]["rows_by_source_frame"])
        context = prepare_source_aligned_context(sliced, events, primary_timeline=primary)
        result = compute_temporal_card_evidence(sliced, events[0], events=events, primary_timeline=primary, context=context)
        self.assertEqual(card(result, "FS01-M02")["status"], "measured_visual_proxy")

    def test_metadata_wrong_binding_or_incomplete_row_cannot_be_authorized(self):
        for kind in ("pts", "track", "missing", "wrong_version", "malformed_rows", "cleared_rejected"):
            records, events, primary = original_records(); duplicate(records, 2)
            sequence = pose_sequence_from_records(records, primary)
            ledger = sequence.coordinate_metadata["source_pose_person_association"]
            if kind == "pts": ledger["rows_by_source_frame"]["2"]["pts"] += 1
            if kind == "track": ledger["rows_by_source_frame"]["2"]["selected_source_track_id"] = 999
            if kind == "missing": ledger["rows_by_source_frame"].pop("2")
            if kind == "wrong_version": ledger["version"] = "unknown"
            if kind == "malformed_rows": ledger["rows_by_source_frame"] = []
            if kind == "cleared_rejected": ledger["rows_by_source_frame"]["2"]["audit"]["rejected_person_track_ids"] = []
            result = compute_temporal_card_evidence(sequence, events[0], events=events, primary_timeline=primary)
            row = card(result, "FS01-M02")
            self.assertEqual(row["reason"], "source_pose_association_evidence_invalid")
            self.assertTrue(all(c["passed"] is None for c in row["conditions"]))

    def test_original_candidate_advisories_are_unchanged(self):
        records, events, primary = original_records()
        events[0]["quality_flags"] = ["primary_identity_ambiguous", "source_track_switch_candidates_present"]
        events[0]["track_diagnostics"].update(primary_identity_ambiguous=True, source_track_switch_candidate_count=1)
        _, result = self.compute(records, events, primary)
        self.assertEqual(card(result, "FS01-M02")["status"], "measured_visual_proxy")

    def test_legacy_direct_sequence_does_not_claim_audit_success(self):
        sequence, events, primary = fixture()
        row = card(compute_temporal_card_evidence(sequence, events[0], events=events, primary_timeline=primary), "FS01-M02")
        self.assertEqual(row["status"], "measured_visual_proxy")
        self.assertNotIn("pose_person_association", row["evidence"])

    def test_three_recorded_usta_frames_recompute_original_rejected_ids(self):
        payload = json.loads((Path(__file__).parent/"fixtures/pose-association-usta-11986-v1.json").read_text(encoding="utf-8"))
        records = [r["raw_record"] for r in payload["frames"]]
        primary = [r["primary_record"] for r in payload["frames"]]
        original = deepcopy(records)
        sequence = pose_sequence_from_records(records, primary)
        ledger = sequence.coordinate_metadata["source_pose_person_association"]["rows_by_source_frame"]
        self.assertEqual([ledger[str(r["frame_index"])]["audit"]["rejected_person_track_ids"] for r in payload["frames"]], [[], [2], []])
        self.assertEqual(records, original)
        # Existing exported records have no full decoder binding. This test is
        # audit evidence only; it makes no new formal-score or decoding claim.
        self.assertEqual(sequence.source_time_bindings, {})

    def test_both_cycle_consumers_reject_synthetic_conflict_in_real_observation_window(self):
        from tests.test_footwork_cycle_card_evidence import real_fixture, compute

        # The geometry is recorded USTA data. Only the audit conflict below is
        # injected; this is a consumer contract test, not a new media finding.
        for code in ("FS03", "FS04"):
            with self.subTest(code=code):
                sequence, event, primary, binding = real_fixture()
                event = deepcopy(event); event["event_code"] = code
                baseline = compute((sequence, event, primary, binding))
                clean_source = deepcopy(sequence)
                row = sequence.coordinate_metadata["source_pose_person_association"]["rows_by_source_frame"][str(int(sequence.source_frames[10]))]
                selected = row["selected_source_track_id"]
                row["audit"]["rejected_person_track_ids"] = [selected]
                row["audit"]["conflicts"] = [{"reason_code": "pose_duplicate_body_different_tracks",
                    "rejected_person_track_ids": [selected], "track_ids": [selected, selected + 1000],
                    "test_origin": "explicit_synthetic_conflict_on_recorded_geometry"}]
                result = compute((sequence, event, primary, binding))
                for before, after in zip(baseline["cards"], result["cards"]):
                    self.assertNotEqual(before["reason"], "selected_pose_person_association_conflict")
                    self.assertEqual(after["reason"], "selected_pose_person_association_conflict")
                    self.assertEqual(after["evidence"]["coverage"], before["evidence"]["coverage"])
                    self.assertEqual(after["evidence"]["source_frames"], before["evidence"]["source_frames"])
                    self.assertEqual(after["evidence"]["pose_person_association"]["rejected_source_frames"], [int(sequence.source_frames[10])])
                    self.assertTrue(all(c["passed"] is None for c in after["conditions"]))
                    self.assertIsNone(after["score"])
                self.assertEqual(compute((clean_source, event, primary, binding)), baseline)


if __name__ == "__main__":
    unittest.main()
