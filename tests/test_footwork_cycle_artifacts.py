"""Source-contract tests; relabelled MIT windows are not step truth evidence."""
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from rallymate_service.footwork_cycle_artifacts import (
    ANNOTATION_VERSION, SEMANTICS, SOURCE_FILES, _bind_verified_decoder_snapshot, build_footwork_cycle_artifact_report,
    compute_verified_cycle_snapshot, validate_cycle_annotations,
)

ROOT = Path(__file__).resolve().parents[1]


def fixture():
    data = json.loads((ROOT / "tests/fixtures/gs-card-real-mit-preparation-v1.json").read_text(encoding="utf-8"))["cases"][0]
    records, primary, decoded = data["frame_records"], data["primary_timeline"], data["decoder_timeline"]
    # The compact GS fixture omits the virtual primary ID. This explicit test
    # binding exercises adapter plumbing, not independently verified identity.
    for row in primary:
        row["primary_player_id"] = 1
    selected = [row for row in primary if row.get("selection_status") == "selected"]
    first, last = selected[3]["source_frame_index"], selected[20]["source_frame_index"]
    frames = {row["frame"]["index"]: row["frame"] for row in records}
    window = {"event_id": "contract-only-candidate", "event_code": "FS04", "primary_player_id": 1,
        "start_source_frame": first, "end_source_frame": last,
        "start_ms": frames[first]["timestamp_ms"], "end_ms": frames[last]["timestamp_ms"],
        "binding": {"method": "manual_anchor", "version": "contract-test-v1", "source": "explicit_contract_test_not_step_label",
            "uncertainty_ms": 100, "semantics": SEMANTICS}}
    document = {"version": ANNOTATION_VERSION, "source_run_id": data["source_job_id"],
        "video_sha256": data["video_sha256"], "input_artifact_sha256": {name: "a" * 64 for name in SOURCE_FILES},
        "windows": [window]}
    return records, primary, [], decoded, document


def instances(report):
    return [instance for row in report["cards"] for instance in row["instances"]]


class CycleArtifactContracts(unittest.TestCase):
    def test_exact_source_snapshot_preserved_and_not_certified_as_artifact_verification(self):
        arguments = fixture()
        before = deepcopy(arguments)
        report = compute_verified_cycle_snapshot(*arguments)
        self.assertEqual(arguments, before)
        self.assertEqual(len(report["cards"]), 10)
        self.assertEqual(len(instances(report)), 5)
        self.assertFalse(report["input"]["artifact_files_verified"])
        self.assertFalse(report["input"]["decoder_verified_from_original_video"])
        self.assertFalse(report["input"]["service_eligible"])
        self.assertEqual(report["scoring_policy_version"], "automatic-formal-card-scoring-v1.0.0")
        self.assertIsNone(report["overall_score"])
        for row in instances(report):
            self.assertIsNone(row["original_grade"])
            self.assertEqual(row["source_annotation"]["binding"], arguments[-1]["windows"][0]["binding"])
            self.assertEqual(row["quality"]["total_frame_count"], 18)
        json.dumps(report, allow_nan=False)

    def test_external_grade_pass_or_quality_cannot_be_supplied_by_annotation(self):
        for key, value in (("score", 100), ("grade", "A"), ("passed", True), ("quality_flags", []), ("conditions", [])):
            with self.subTest(key=key):
                document = fixture()[-1]
                document["windows"][0][key] = value
                with self.assertRaisesRegex(ValueError, "annotation_schema_fields_invalid"):
                    validate_cycle_annotations(document)

    def test_uncertainty_semantics_and_detector_authority_are_explicit(self):
        for mutation in ({"method": "versioned_detector"}, {"semantics": "expert_verified"}, {"uncertainty_ms": -1}, {"uncertainty_ms": True}):
            with self.subTest(mutation=mutation):
                document = fixture()[-1]
                document["windows"][0]["binding"].update(mutation)
                with self.assertRaisesRegex(ValueError, "cycle_manual_candidate_binding_invalid"):
                    validate_cycle_annotations(document)

    def test_duplicate_and_competing_family_windows_cannot_double_count(self):
        for same_id in (True, False):
            document = fixture()[-1]
            other = deepcopy(document["windows"][0])
            if not same_id:
                other.update(event_id="competing-label", event_code="FS03")
            document["windows"].append(other)
            with self.assertRaises(ValueError):
                validate_cycle_annotations(document)

    def test_missing_first_last_or_interior_frame_cannot_shrink_denominator(self):
        for edge in ("first", "last", "middle"):
            with self.subTest(edge=edge):
                records, primary, events, decoded, document = fixture()
                window = document["windows"][0]
                index = {"first": window["start_source_frame"], "last": window["end_source_frame"],
                         "middle": (window["start_source_frame"] + window["end_source_frame"]) // 2}[edge]
                records = [row for row in records if row["frame"]["index"] != index]
                primary = [row for row in primary if row["source_frame_index"] != index]
                with self.assertRaisesRegex(ValueError, "cycle_source_window_frames_missing"):
                    compute_verified_cycle_snapshot(records, primary, events, decoded, document)

    def test_annotation_frame_clock_person_and_foreign_source_are_rejected(self):
        for target in ("time", "frame", "person", "frame_job", "timeline_job", "video"):
            with self.subTest(target=target):
                records, primary, events, decoded, document = fixture()
                if target == "time": document["windows"][0]["start_ms"] += 20
                if target == "frame": document["windows"][0]["end_source_frame"] += 1
                if target == "person": document["windows"][0]["primary_player_id"] = 9
                if target == "frame_job": records[0]["job_id"] = "another-run"
                if target == "timeline_job": primary[0]["job_id"] = "another-run"
                if target == "video": document["video_sha256"] = "b" * 64
                with self.assertRaises(ValueError):
                    compute_verified_cycle_snapshot(records, primary, events, decoded, document)

    def test_unbound_or_malformed_original_diagnostics_cannot_be_hidden(self):
        records, primary, events, decoded, document = fixture()
        window = document["windows"][0]
        event = {"event_id": "raw", "video_id": document["source_run_id"], "start_ms": window["start_ms"],
            "end_ms": window["end_ms"], "quality_flags": "not-a-list", "track_diagnostics": {}}
        with self.assertRaisesRegex(ValueError, "cycle_original_quality_diagnostics_invalid"):
            compute_verified_cycle_snapshot(records, primary, [event], decoded, document)
        event.update(video_id="another-run", quality_flags=[])
        with self.assertRaisesRegex(ValueError, "cycle_source_event_inventory_invalid"):
            compute_verified_cycle_snapshot(records, primary, [event], decoded, document)

    def test_incomplete_jump_diagnostics_are_not_overwritten_by_other_complete_event(self):
        records, primary, _, decoded, document = fixture()
        window = document["windows"][0]
        event = {"event_id": "raw-incomplete", "video_id": document["source_run_id"], "start_ms": window["start_ms"],
            "end_ms": window["end_ms"], "quality_flags": ["keypoint_jump_candidates_present"], "track_diagnostics": {}}
        complete = deepcopy(event)
        complete.update(event_id="raw-complete", track_diagnostics={"keypoint_jump_candidate_joints": ["left_wrist"]})
        report = compute_verified_cycle_snapshot(records, primary, [event, complete], decoded, document)
        for row in instances(report):
            self.assertEqual(row["evidence"]["quality_source_event_ids"], ["raw-incomplete", "raw-complete"])
            self.assertIn("keypoint_jump_candidates_present", row["evidence"]["event_quality"]["review_flags"])
            self.assertEqual(row["status"], "unavailable")
            self.assertIn("keypoint_jump_candidates_present", row["evidence"]["event_quality"]["advisory_flags"])
            self.assertNotIn("keypoint_jump_candidates_present", row["evidence"]["event_quality"]["blocking_flags"])
            self.assertIsNone(row["score"])

    def test_original_event_duplicates_are_idempotent_but_conflicts_rejected(self):
        records, primary, _, decoded, document = fixture()
        window = document["windows"][0]
        event = {"event_id": "raw", "video_id": document["source_run_id"], "start_ms": window["start_ms"],
            "end_ms": window["end_ms"], "quality_flags": [], "track_diagnostics": {}}
        first = compute_verified_cycle_snapshot(records, primary, [event], decoded, document)
        repeated = compute_verified_cycle_snapshot(records, primary, [event, deepcopy(event)], decoded, document)
        self.assertEqual(first, repeated)
        conflict = {**event, "quality_flags": ["keypoint_jump_candidates_present"]}
        with self.assertRaisesRegex(ValueError, "cycle_duplicate_original_event_conflict"):
            compute_verified_cycle_snapshot(records, primary, [event, conflict], decoded, document)

    def test_actual_sam_fractional_pts_keep_rounded_clock_and_original_bound_epoch(self):
        from rallymate_features.coordinates import _bound_model_points, pose_sequence_from_records
        data = json.loads((ROOT / "tests/fixtures/card-execution-api-real-0a3.json").read_text(encoding="utf-8"))
        records, primary = data["records"], data["timeline"]
        # Real integer PTS from the saved original fixture. This tests metadata
        # preservation, not fresh decoding of the entire source video.
        derived = [{"frame_index": row["frame"]["index"], **{key: row["frame"][key]
            for key in ("pts", "time_base_num", "time_base_den", "video_sha256")}, "selection_epoch": 99} for row in records]
        self.assertIsNotNone(_bound_model_points(records[1], primary[1], unique_source=True))
        prepared, selected = _bind_verified_decoder_snapshot(records, primary, derived)
        self.assertEqual(prepared[1]["frame"]["source_timestamp_ms"], 2533)
        self.assertEqual(selected[1]["selection_epoch"], records[1]["poses"][0]["source_binding"]["selection_epoch"])
        self.assertIsNotNone(_bound_model_points(prepared[1], selected[1], unique_source=True))
        sequence = pose_sequence_from_records(prepared, selected)
        self.assertIsNotNone(sequence.model_estimate_bindings[1])
        self.assertEqual(prepared[1]["poses"], records[1]["poses"])
        self.assertEqual(prepared[1]["sam3d_requests"], records[1]["sam3d_requests"])
        records[1]["frame"]["source_timestamp_ms"] += 20
        with self.assertRaisesRegex(ValueError, "cycle_original_source_timestamp_mismatch"):
            _bind_verified_decoder_snapshot(records, primary, derived)

    def test_artifact_entry_rejects_wrong_directory_and_source_hash_before_decode(self):
        document = fixture()[-1]
        with tempfile.TemporaryDirectory() as temporary:
            wrong = Path(temporary)
            with patch("rallymate_service.footwork_cycle_artifacts._decode_video_timeline") as decode:
                with self.assertRaisesRegex(ValueError, "cycle_source_directory_binding_mismatch"):
                    build_footwork_cycle_artifact_report(wrong, document)
                directory = wrong / document["source_run_id"]
                directory.mkdir()
                with self.assertRaisesRegex(ValueError, "cycle_source_artifact_digest_mismatch"):
                    build_footwork_cycle_artifact_report(directory, document)
                decode.assert_not_called()


class CycleOriginalArtifactIntegration(unittest.TestCase):
    def test_original_usta_video_has_thirty_incomplete_instances_unavailable_under_current_policy(self):
        document = json.loads((ROOT / "tests/fixtures/footwork-cycle-annotations-usta-v1.json").read_text(encoding="utf-8"))
        artifact_root = Path(os.environ.get("CARD_ARTIFACT_ROOT", str(ROOT)))
        original = artifact_root / ".codex_tmp/card-video-extension-20261022/runs/fs-usta-return-footwork-heldout"
        if not all((original / name).is_file() for name in SOURCE_FILES):
            self.skipTest("integration_requires_original_usta_source_artifacts")
        summary = json.loads((original / "summary.json").read_text(encoding="utf-8"))
        if not Path(summary["input"]["video_path"]).is_file():
            self.skipTest("integration_requires_original_usta_video")
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / document["source_run_id"]
            directory.mkdir()
            for name in SOURCE_FILES:
                try:
                    os.link(original / name, directory / name)
                except OSError:
                    shutil.copyfile(original / name, directory / name)
            result = build_footwork_cycle_artifact_report(directory, document)
        self.assertTrue(result["input"]["service_eligible"])
        self.assertTrue(result["input"]["decoder_verified_from_original_video"])
        self.assertEqual(result["input"]["source_frame_count"], 539)
        self.assertEqual(result["scoring_policy_version"], "automatic-formal-card-scoring-v1.0.0")
        self.assertEqual(result["coverage"]["instance_status_counts"], {"unavailable": 30})
        self.assertIsNone(result["overall_score"])
        for row in instances(result):
            self.assertEqual(row["status"], "unavailable")
            self.assertIsNone(row["grade"])
            self.assertIsNone(row["score"])
            self.assertIsNone(row["original_grade"])
            self.assertEqual(row["source_annotation"]["binding"]["semantics"], SEMANTICS)


if __name__ == "__main__":
    unittest.main()
