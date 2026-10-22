"""Contract-only FS relabelling of raw poses is never real technique evidence."""
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from rallymate_service.fs_card_windows import (
    ANNOTATION_VERSION, SEMANTICS, SOURCE_FILES, build_fs_card_window_report,
    compute_fs_card_windows, supported_rules, validate_fs_card_annotations,
)
from tests.test_footwork_cycle_artifacts import fixture as source_fixture


def fixture(identifier="FS01-M04"):
    records, primary, events, decoded, original = source_fixture()
    row = original["windows"][0]
    document = {"version": ANNOTATION_VERSION, "source_run_id": original["source_run_id"],
        "video_sha256": original["video_sha256"], "input_artifact_sha256": original["input_artifact_sha256"],
        "windows": [{"annotation_id": "contract-only-not-technique-truth", "indicator_id": identifier,
            "person_track_id": row["primary_player_id"],
            **{key: row[key] for key in ("start_source_frame", "end_source_frame", "start_ms", "end_ms")},
            "binding": {"method": "manual_anchor", "version": "contract-test-only-v1", "source": "test-only-no-visual-annotation",
                "uncertainty_ms": 100, "semantics": SEMANTICS}}]}
    return records, primary, events, decoded, document


def compute(arguments):
    return compute_fs_card_windows(*arguments, source_job_id=arguments[-1]["source_run_id"])


def instances(report):
    return [instance for row in report["cards"] for instance in row["instances"]]


class FSCardWindowContracts(unittest.TestCase):
    def test_owned_cards_are_disjoint_and_full_original_inventory_is_accounted_for(self):
        from rallymate_scoring.card_rule_catalog import load_card_rules
        from rallymate_scoring.temporal_card_evidence import CARD_CONDITIONS
        rules = supported_rules()
        other = {row["indicator_id"] for row in load_card_rules()["cards"]
            if row["event_code"] in {"FS03", "FS04"}} | set(CARD_CONDITIONS)
        self.assertEqual(len(rules), 35)
        self.assertFalse(set(rules) & other)
        self.assertEqual(len(set(rules) | other), 50)
        self.assertEqual(Counter(rule["evaluation"]["kind"] for rule in rules.values()),
            {"visual_proxy": 20, "manual_review": 15})

    def test_all_thirty_five_cards_have_an_explicit_execution_disposition_without_source_grades(self):
        for identifier, rule in supported_rules().items():
            with self.subTest(identifier=identifier):
                arguments = fixture(identifier)
                before = deepcopy(arguments)
                report = compute(arguments)
                self.assertEqual(arguments, before)
                self.assertEqual(len(report["cards"]), 35)
                self.assertEqual(report["coverage"]["computed_card_instance_count"], 1)
                row, = instances(report)
                self.assertEqual(row["indicator_id"], identifier)
                self.assertIsNone(row["original_grade"])
                self.assertFalse(row["source_grade_supported"])
                self.assertIsNone(report["overall_score"])
                self.assertFalse(report["input"]["artifact_files_verified"])
                self.assertFalse(report["input"]["decoder_verified_from_original_video"])
                self.assertFalse(report["input"]["service_eligible"])
                if rule["evaluation"]["kind"] == "manual_review":
                    self.assertEqual(row["status"], "review_required")
                    self.assertIsNone(row["score"])
                else:
                    self.assertEqual(row["quality"]["total_frame_count"], 18)
                    self.assertFalse(row["quality"]["visibility_verified"])
                json.dumps(report, allow_nan=False)

    def test_four_cycle_dependent_rules_cannot_use_a_manual_window_as_a_complete_step_cycle(self):
        for identifier in ("FS02-M04", "FS07-M02", "FS08-M02", "FS08-M03"):
            arguments = list(fixture(identifier))
            # Controlled repeated poses isolate the cycle gate from earlier
            # missing-joint gates. These are synthetic data, never a real result.
            pose = deepcopy(next(record["poses"][0] for record in arguments[0]
                if record["poses"] and record["frame"]["index"] == 10))
            for point in pose["keypoints"]:
                point["confidence"] = .9
            camera = deepcopy(arguments[0][10]["camera_motion"])
            for record in arguments[0]:
                record["poses"] = [deepcopy(pose)]
                record["camera_motion"] = deepcopy(camera)
            arguments[1] = [{"processed_index": record["frame"]["processed_index"],
                "source_frame_index": record["frame"]["index"], "timestamp_ms": record["frame"]["timestamp_ms"],
                "source_track_id": 1, "selection_status": "selected", "identity_ambiguous": False}
                for record in arguments[0]]
            row, = instances(compute(arguments))
            self.assertEqual(row["status"], "unavailable")
            self.assertIsNone(row["score"])
            self.assertFalse(row["source_window_evidence"]["step_cycle_bound"])
            self.assertTrue(any("complete_step_cycle_not_bound" in condition["evidence"]["reason_codes"]
                for condition in row["conditions"]))

    def test_external_grades_quality_and_automatic_labels_are_rejected(self):
        for key in ("score", "conditions", "quality_flags", "step_cycle_binding", "racket_side"):
            document = fixture()[-1]
            document["windows"][0][key] = 100
            with self.assertRaises(ValueError):
                validate_fs_card_annotations(document)
        for fields in ({"method": "versioned_detector"}, {"semantics": "expert_truth"},
                       {"uncertainty_ms": -1}, {"uncertainty_ms": True}):
            document = fixture()[-1]
            document["windows"][0]["binding"].update(fields)
            with self.assertRaises(ValueError):
                validate_fs_card_annotations(document)
        for identifier in ("GS01-M03-01", "FS03-M02", "FS01-M02", "FS09-M05"):
            with self.assertRaises(ValueError):
                validate_fs_card_annotations(fixture(identifier)[-1])

    def test_same_card_duplicate_windows_and_competing_techniques_cannot_inflate_coverage(self):
        for identifier in ("FS01-M04", "FS02-M02"):
            document = fixture()[-1]
            second = deepcopy(document["windows"][0])
            second.update(annotation_id="second-id", indicator_id=identifier)
            document["windows"].append(second)
            with self.assertRaises(ValueError):
                validate_fs_card_annotations(document)

    def test_missing_first_middle_last_source_frame_never_shrinks_denominator(self):
        for position in (0, 8, -1):
            arguments = list(fixture())
            window = arguments[-1]["windows"][0]
            index = list(range(window["start_source_frame"], window["end_source_frame"] + 1))[position]
            arguments[0] = [row for row in arguments[0] if row["frame"]["index"] != index]
            arguments[1] = [row for row in arguments[1] if row["source_frame_index"] != index]
            with self.assertRaisesRegex(ValueError, "original_frame_inventory_incomplete"):
                compute(arguments)

    def test_wrong_source_clock_and_identity_never_score(self):
        for target in ("video", "run", "time", "person"):
            arguments = list(fixture())
            document, row = arguments[-1], arguments[-1]["windows"][0]
            if target == "video": document["video_sha256"] = "b" * 64
            if target == "run": arguments[0][0]["job_id"] = "other-run"
            if target == "time": row["end_ms"] += 20
            if target == "person": row["person_track_id"] = 99
            if target != "person":
                with self.assertRaises(ValueError):
                    compute(arguments)
            else:
                result, = instances(compute(arguments))
                self.assertEqual(result["status"], "unavailable")
                self.assertIsNone(result["score"])

    def test_quality_from_every_original_overlapping_event_is_preserved(self):
        arguments = list(fixture())
        window = arguments[-1]["windows"][0]
        raw = {"video_id": arguments[-1]["source_run_id"], "person_track_id": 1, "event_id": "raw-one",
            "start_ms": window["start_ms"], "end_ms": window["end_ms"],
            "quality_flags": ["keypoint_jump_candidates_present"], "track_diagnostics": {
                "source_track_ids": [1], "primary_identity_ambiguous": False, "source_track_switch_candidate_count": 0}}
        second = deepcopy(raw)
        second.update(event_id="raw-two")
        second["track_diagnostics"]["keypoint_jump_candidate_joints"] = ["left_wrist"]
        arguments[2] = [raw, second, deepcopy(second)]
        result, = instances(compute(arguments))
        self.assertEqual(result["status"], "review_required")
        self.assertIsNone(result["score"])
        self.assertTrue(all(condition["passed"] is None for condition in result["conditions"]))
        evidence = result["source_window_evidence"]["original_event_quality"]
        self.assertEqual(len(evidence), 2)
        for entry in evidence:
            self.assertEqual(entry["source_start_ms"], raw["start_ms"])
            self.assertEqual(entry["source_end_ms"], raw["end_ms"])
            self.assertEqual(entry["source_track_ids"], [1])
            self.assertIs(entry["primary_identity_ambiguous"], False)
            self.assertEqual(entry["source_track_switch_candidate_count"], 0)
        second["event_id"] = "raw-one"
        with self.assertRaisesRegex(ValueError, "conflicting_source_event"):
            compute(arguments)

    def test_quality_coverage_is_not_assumed_from_absence_wrong_track_or_a_short_fragment(self):
        def original(arguments):
            window = arguments[-1]["windows"][0]
            return {"video_id": arguments[-1]["source_run_id"], "person_track_id": 1, "event_id": "source-event",
                "start_ms": window["start_ms"], "end_ms": window["end_ms"], "quality_flags": [],
                "track_diagnostics": {"source_track_ids": [1], "primary_identity_ambiguous": False,
                    "source_track_switch_candidate_count": 0}}
        for variant in ("complete", "absent", "wrong_raw_track", "one_millisecond", "unknown_track", "other_identity_warning"):
            arguments = list(fixture())
            event = original(arguments)
            arguments[2] = [event]
            if variant == "absent": arguments[2] = []
            if variant == "wrong_raw_track": event["track_diagnostics"]["source_track_ids"] = [2]
            if variant == "one_millisecond": event["end_ms"] = event["start_ms"] + 1
            if variant == "unknown_track": event["track_diagnostics"].pop("source_track_ids")
            if variant == "other_identity_warning":
                other = deepcopy(event)
                other.update(event_id="other-track", person_track_id=2, quality_flags=["primary_identity_ambiguous"])
                other["track_diagnostics"].update(source_track_ids=[2], primary_identity_ambiguous=True)
                arguments[2].append(other)
            row, = instances(compute(arguments))
            self.assertEqual(row["status"], "proxy_scored" if variant == "complete" else "review_required", variant)
            self.assertEqual(row["score"] is not None, variant == "complete", variant)
            if variant == "wrong_raw_track":
                evidence, = row["source_window_evidence"]["original_event_quality"]
                self.assertEqual(evidence["source_track_ids"], [2])
                self.assertFalse(evidence["applies_to_selected_person"])

    def test_explicit_foreign_primary_run_fields_are_rejected(self):
        for key in ("job_id", "video_id"):
            arguments = list(fixture())
            for row in arguments[1]: row[key] = "foreign-run"
            with self.assertRaisesRegex(ValueError, "primary_run_binding_mismatch"):
                compute(arguments)

    def test_empty_annotations_do_not_invent_windows_or_scores(self):
        arguments = list(fixture())
        arguments[-1]["windows"] = []
        report = compute(arguments)
        self.assertFalse(instances(report))
        self.assertEqual({row["run_status"] for row in report["cards"]}, {"not_executed"})

    def test_artifact_wrapper_requires_matching_sources_before_decode_and_detects_midrun_changes(self):
        arguments = fixture()
        document = deepcopy(arguments[-1])
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / document["source_run_id"]
            directory.mkdir()
            for name in SOURCE_FILES:
                (directory / name).write_text("{}", encoding="utf-8")
            video = Path(temporary) / "contract-only-video.mp4"
            video.write_bytes(b"not-real-video; decoder-is-mocked-for-contract-test")
            from rallymate_service.gs_card_execution import _sha
            summary = {"job_id": directory.name, "input": {"video_path": str(video)},
                "minimum_scoring_loop": {"provenance": {"video_sha256": _sha(video), "frames_sha256": _sha(directory / "frames.jsonl")}}}
            (directory / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
            document["video_sha256"] = _sha(video)
            document["input_artifact_sha256"] = {name: _sha(directory / name) for name in SOURCE_FILES}
            report = compute(arguments)
            def changed(*args, **kwargs):
                (directory / "events.jsonl").write_text('{"changed":true}', encoding="utf-8")
                return report
            with patch("rallymate_service.fs_card_windows._decode_video_timeline", return_value=arguments[3]), \
                 patch("rallymate_service.fs_card_windows._records", return_value=[]), \
                 patch("rallymate_service.fs_card_windows.compute_fs_card_windows", side_effect=changed):
                with self.assertRaisesRegex(ValueError, "source_changed_during_execution"):
                    build_fs_card_window_report(directory, document)


if __name__ == "__main__":
    unittest.main()
