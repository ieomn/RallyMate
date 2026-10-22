"""Real source-observation regressions and fail-closed GS annotation contracts."""
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from rallymate_service.gs_card_execution import (
    ANNOTATION_VERSION, build_gs_card_execution_report, build_gs_research_replay_report, compute_gs_card_execution,
    validate_stage_annotations,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/gs-card-real-mit-preparation-v1.json"


def _instances(report):
    return [instance for card in report["cards"] for instance in card["instances"]]


class GSCardExecutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        cls.expected = json.loads((ROOT / "tests/fixtures/card-video-validation-results-20261022.json").read_text(encoding="utf-8"))
        cls.cases = cls.fixture["cases"]

    def compute(self, case=None, document=None):
        case = self.cases[0] if case is None else case
        return compute_gs_card_execution(case["frame_records"], case["primary_timeline"], case["decoder_timeline"],
            case["annotations"] if document is None else document, source_job_id=case["source_job_id"])

    def test_real_source_observations_reproduce_all_sixty_existing_instances(self):
        observed, indicators = Counter(), set()
        for case in self.cases:
            report = self.compute(case)
            self.assertEqual(len(report["cards"]), 248)
            self.assertFalse(report["input"]["artifact_files_verified"])
            self.assertFalse(report["input"]["decoder_verified_from_original_video"])
            self.assertFalse(report["input"]["service_eligible"])
            reference = next(row for row in self.expected["cases"] if row["case_id"] == case["case_id"])
            expected = {(row["event_id"], row["indicator_id"]): row
                        for stage in reference["stage_measurements"] for row in stage["card_evaluations"]}
            actual = _instances(report)
            self.assertEqual(len(actual), len(expected))
            for row in actual:
                original = expected[row["event_id"], row["indicator_id"]]
                for key in ("status", "proxy_grade", "score", "original_grade", "source_grade_supported", "reason_codes"):
                    self.assertEqual(row[key], original[key], (row["event_id"], row["indicator_id"], key))
                self.assertEqual(len(row["conditions"]), len(original["conditions"]))
                for computed, saved in zip(row["conditions"], original["conditions"]):
                    for key in ("metric", "value", "operator", "threshold", "unit", "passed"):
                        self.assertEqual(computed[key], saved[key], (row["indicator_id"], key))
                self.assertIsNone(row["original_grade"])
                self.assertFalse(row["source_grade_supported"])
                self.assertIn("document_sha256", row["source"])
                self.assertEqual(row["source_annotation"]["binding"]["method"], "manual_anchor")
                observed[row["status"]] += 1
                indicators.add(row["indicator_id"])
        self.assertEqual(observed, {"proxy_scored": 27, "review_required": 16, "unavailable": 17})
        self.assertEqual(len(indicators), 15)

    def test_wrong_technique_is_rejected(self):
        document = deepcopy(self.cases[0]["annotations"])
        document["stages"][0]["technique_code"] = "GS02"
        with self.assertRaisesRegex(ValueError, "technique_stage_or_person_mismatch"):
            self.compute(document=document)

    def test_explicit_foreign_primary_run_cannot_bind_matching_frame_numbers(self):
        for key in ("job_id", "video_id"):
            case = deepcopy(self.cases[0])
            for row in case["primary_timeline"]:
                row[key] = "a-different-source-run"
            with self.assertRaisesRegex(ValueError, "primary_source_run_binding_mismatch"):
                self.compute(case)

    def test_wrong_stage_and_generic_three_phase_name_are_rejected(self):
        for stage in ("GS02-M03", "preparation", "GS01-M11", "M03"):
            document = deepcopy(self.cases[0]["annotations"])
            document["stages"][0]["stage_code"] = stage
            with self.assertRaises(ValueError, msg=stage):
                self.compute(document=document)

    def test_cross_video_source_is_rejected(self):
        document = deepcopy(self.cases[0]["annotations"])
        document["video_sha256"] = self.cases[1]["video_sha256"]
        with self.assertRaisesRegex(ValueError, "decoder_source_binding_invalid"):
            self.compute(document=document)

    def test_source_job_binding_is_rejected(self):
        case = deepcopy(self.cases[0])
        case["frame_records"][0]["job_id"] = "other-job"
        with self.assertRaisesRegex(ValueError, "source_frame_job_binding_mismatch"):
            self.compute(case)

    def test_overlapping_action_windows_cannot_duplicate_card_scores(self):
        document = deepcopy(self.cases[0]["annotations"])
        action = deepcopy(document["actions"][0]); action["action_id"] = "second-overlapping-action"
        stage = deepcopy(document["stages"][0]); stage.update(annotation_id="overlap", action_id=action["action_id"])
        document["actions"].append(action); document["stages"].append(stage)
        with self.assertRaisesRegex(ValueError, "overlapping_stage_annotation_conflict"):
            self.compute(document=document)

    def test_duplicate_same_action_stage_is_rejected(self):
        document = deepcopy(self.cases[0]["annotations"])
        stage = deepcopy(document["stages"][0]); stage["annotation_id"] = "duplicate-phase"
        document["stages"].append(stage)
        with self.assertRaisesRegex(ValueError, "duplicate_action_stage_annotation"):
            self.compute(document=document)

    def test_same_interval_cannot_be_relabelled_as_multiple_source_stages(self):
        document = deepcopy(self.cases[0]["annotations"])
        stage = deepcopy(document["stages"][0]); stage.update(annotation_id="copied-phase", stage_code="GS01-M04")
        document["stages"].append(stage)
        with self.assertRaisesRegex(ValueError, "identical_window_reused"):
            self.compute(document=document)

    def test_legitimate_partial_overlap_of_distinct_stages_is_not_forced_into_equal_parts(self):
        document = deepcopy(self.cases[0]["annotations"])
        stage = deepcopy(document["stages"][0]); stage.update(annotation_id="independently-reviewed-next-stage", stage_code="GS01-M04")
        stage["start_ms"] += 400; stage["end_ms"] += 200
        document["stages"].append(stage)
        validate_stage_annotations(document)

    def test_reversed_and_nested_source_stages_are_rejected(self):
        for stage_code, start_delta, end_delta in (("GS01-M02", 100, 100), ("GS01-M04", 100, -100)):
            document = deepcopy(self.cases[0]["annotations"])
            stage = deepcopy(document["stages"][0]); stage.update(annotation_id="inconsistent-stage", stage_code=stage_code)
            stage["start_ms"] += start_delta; stage["end_ms"] += end_delta
            document["stages"].append(stage)
            with self.assertRaisesRegex(ValueError, "source_stage_order_or_containment_conflict"):
                validate_stage_annotations(document)

    def test_external_grade_or_pass_flags_are_not_input_fields(self):
        for key, value in (("grade", "A"), ("score", 100), ("passed", True), ("conditions", [])):
            document = deepcopy(self.cases[0]["annotations"])
            document["stages"][0][key] = value
            with self.assertRaisesRegex(ValueError, "annotation_schema_fields_invalid"):
                validate_stage_annotations(document)

    def test_unregistered_automatic_stage_detector_is_rejected(self):
        document = deepcopy(self.cases[0]["annotations"])
        document["stages"][0]["binding"].update(method="versioned_detector", version="caller-invented-detector-v1",
            semantics="registered_local_visual_proxy_not_full_source_stage")
        with self.assertRaisesRegex(ValueError, "automatic_stage_detector_unregistered"):
            self.compute(document=document)

    def test_missing_stages_remain_unexecuted_and_do_not_receive_E(self):
        document = deepcopy(self.cases[0]["annotations"])
        document["stages"] = []
        report = self.compute(document=document)
        self.assertEqual(report["coverage"]["computed_card_instance_count"], 0)
        for row in report["cards"]:
            self.assertFalse(row["instances"])
            if row["event_code"] == "GS01":
                self.assertEqual(row["run_status"], "not_executed")
                self.assertIn("gs_stage_annotation_missing", row["reason_codes"])
            else:
                self.assertEqual(row["run_status"], "not_observed")
        self.assertIsNone(report["summary"]["channels"]["visual_proxy"]["score"])

    def test_declared_person_must_be_primary_in_the_entire_window(self):
        document = deepcopy(self.cases[0]["annotations"])
        document["actions"][0]["person_track_id"] = 999
        document["stages"][0]["person_track_id"] = 999
        report = self.compute(document=document)
        affected = [row for row in _instances(report) if row["event_id"] == document["stages"][0]["annotation_id"]]
        self.assertEqual(len(affected), 5)
        self.assertTrue(all(row["status"] == "unavailable" and row["score"] is None for row in affected))
        self.assertTrue(all("stage_primary_person_binding_unverified" in row["reason_codes"] for row in affected))

    def test_camera_epoch_change_blocks_the_entire_bound_window(self):
        case = deepcopy(self.cases[0]); stage = case["annotations"]["stages"][0]
        target = next(row for row in case["frame_records"] if stage["start_ms"] + 150 < row["frame"]["timestamp_ms"] < stage["end_ms"])
        target["camera_motion"]["reference_epoch"] += 100
        report = self.compute(case)
        affected = [row for row in _instances(report) if row["event_id"] == stage["annotation_id"]]
        self.assertTrue(all(row["status"] == "unavailable" and row["score"] is None for row in affected))
        self.assertTrue(all("stage_identity_or_camera_epoch_changed" in row["reason_codes"] for row in affected))

    def test_primary_ambiguity_is_not_bridged(self):
        case = deepcopy(self.cases[0]); stage = case["annotations"]["stages"][0]
        target = next(row for row in case["primary_timeline"] if stage["start_ms"] < row["timestamp_ms"] < stage["end_ms"])
        target["identity_ambiguous"] = True
        report = self.compute(case)
        affected = [row for row in _instances(report) if row["event_id"] == stage["annotation_id"]]
        self.assertTrue(all(row["status"] == "unavailable" for row in affected))

    def test_wrong_pts_and_primary_frame_time_are_rejected(self):
        case = deepcopy(self.cases[0]); case["decoder_timeline"][10]["pts"] += 100000
        with self.assertRaisesRegex(ValueError, "decoder_pts_nonmonotonic"):
            self.compute(case)
        case = deepcopy(self.cases[0]); case["primary_timeline"][10]["timestamp_ms"] += 1
        with self.assertRaisesRegex(ValueError, "primary_frame_time_binding_mismatch"):
            self.compute(case)

    def test_removed_source_frame_cannot_reduce_the_scoring_denominator(self):
        case = deepcopy(self.cases[0]); stage = case["annotations"]["stages"][0]
        index = next(i for i, row in enumerate(case["frame_records"]) if stage["start_ms"] + 100 < row["frame"]["timestamp_ms"] < stage["end_ms"])
        processed = case["frame_records"][index]["frame"]["processed_index"]
        case["frame_records"].pop(index)
        case["primary_timeline"] = [row for row in case["primary_timeline"] if row["processed_index"] != processed]
        report = self.compute(case)
        affected = [row for row in _instances(report) if row["event_id"] == stage["annotation_id"]]
        self.assertTrue(all(row["status"] == "unavailable" and row["score"] is None for row in affected))
        self.assertTrue(all("source_decoder_window_inventory_incomplete" in row["reason_codes"] for row in affected))

    def test_missing_first_or_last_frame_cannot_silently_shorten_the_stage(self):
        for endpoint in (0, -1):
            case = deepcopy(self.cases[0]); stage = case["annotations"]["stages"][0]
            eligible = [row for row in case["frame_records"] if stage["start_ms"] <= row["frame"]["timestamp_ms"] <= stage["end_ms"]]
            removed = eligible[endpoint]["frame"]
            case["frame_records"] = [row for row in case["frame_records"] if row["frame"]["index"] != removed["index"]]
            case["primary_timeline"] = [row for row in case["primary_timeline"] if row["processed_index"] != removed["processed_index"]]
            report = self.compute(case)
            affected = [row for row in _instances(report) if row["event_id"] == stage["annotation_id"]]
            self.assertTrue(all(row["status"] == "unavailable" and row["score"] is None for row in affected))
            self.assertTrue(all("source_decoder_window_inventory_incomplete" in row["reason_codes"] for row in affected))
            bound = next(row for row in report["stage_windows"] if row["annotation_id"] == stage["annotation_id"])
            self.assertIn(removed["index"], bound["source_frames"])
            self.assertEqual(len(bound["source_frames"]), len(eligible))
            self.assertEqual(bound["observed_source_frame_count"], len(eligible) - 1)

    def test_malformed_annotation_and_legacy_schema_are_rejected(self):
        document = deepcopy(self.cases[0]["annotations"]); document["version"] = "preparation-acceleration-follow-through-v1"
        with self.assertRaisesRegex(ValueError, "annotation_version_or_run_id_invalid"):
            self.compute(document=document)
        document = deepcopy(self.cases[0]["annotations"]); document["stages"][0]["person_track_id"] = True
        with self.assertRaisesRegex(ValueError, "technique_stage_or_person_mismatch"):
            self.compute(document=document)


class GSArtifactIntegrationTests(unittest.TestCase):
    """Explicit integration: requires the original local videos and run files."""
    def test_three_original_videos_recompute_through_production_artifact_entry_point(self):
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        manifest = json.loads((ROOT / "tests/fixtures/card-video-cases.json").read_text(encoding="utf-8"))
        manifest_cases = {case["id"]: case for case in manifest["cases"]}
        missing = [case["case_id"] for case in fixture["cases"]
                   if not (ROOT / manifest_cases[case["case_id"]]["local_video"]).is_file()
                   or not (ROOT / manifest_cases[case["case_id"]]["run_dir"] / "frames.jsonl").is_file()]
        if missing:
            self.skipTest("integration_requires_original_local_source_artifacts:" + ",".join(missing))
        counts = Counter()
        for case in fixture["cases"]:
            reference = manifest_cases[case["case_id"]]
            original_directory = ROOT / reference["run_dir"]
            # The original research alias is intentionally not accepted by the
            # production entry point. Migration preserves every source byte.
            with self.assertRaisesRegex(ValueError, "source_summary_job_binding_mismatch"):
                build_gs_card_execution_report(original_directory, case["annotations"], video_path=ROOT / reference["local_video"])
            with tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary) / case["source_job_id"]
                directory.mkdir()
                for name in ("frames.jsonl", "primary-player.jsonl", "summary.json"):
                    shutil.copyfile(original_directory / name, directory / name)
                annotations = deepcopy(case["annotations"])
                annotations["source_run_id"] = case["source_job_id"]
                report = build_gs_card_execution_report(directory, annotations, video_path=ROOT / reference["local_video"])
            self.assertTrue(report["input"]["artifact_files_verified"])
            self.assertTrue(report["input"]["decoder_verified_from_original_video"])
            self.assertTrue(report["input"]["service_eligible"])
            self.assertEqual(report["source_run_id"], case["source_job_id"])
            self.assertEqual(report["input"]["annotation_version"], ANNOTATION_VERSION)
            counts.update(report["coverage"]["instance_status_counts"])
        self.assertEqual(counts, {"proxy_scored": 27, "review_required": 16, "unavailable": 17})


if __name__ == "__main__":
    unittest.main()
