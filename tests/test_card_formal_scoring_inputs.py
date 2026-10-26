"""Application scoring contracts from core and raw pose-record inputs.

SAM records are synthetic contract data passed through the real parser, not
model inference or tennis-technique ground truth. Frozen real-video fixtures
are not rewritten by these tests.
"""
from copy import deepcopy
import unittest

from rallymate_scoring.card_evaluation import evaluate_card_window
from rallymate_scoring.card_scoring_policy import SCORING_POLICY_VERSION
from rallymate_scoring.card_rule_catalog import get_card_rule
from rallymate_service.gs_card_execution import (
    ANNOTATION_VERSION, _prepare_samples, compute_gs_card_execution,
)
from rallymate_vision.pose.metadata import keypoint_schema
from tests.test_card_evaluation import fixture as core_fixture
from tests.test_fs_card_windows import compute, instances
from tests.test_fs_step_motion_windows import fixture as record_fixture


def bind_model(sample, clock):
    sample.observation_semantics = "monocular_model_estimate"
    sample.pose_source = "sam3d"
    sample.point_confidences = {}
    sample.source_binding.update({**clock, "video_sha256": "a"*64,
        "person_track_id": sample.track, "selection_epoch": sample.selection_epoch,
        "camera_reference_epoch": sample.camera_reference_epoch})


def model_records(arguments, selected_frames=None):
    """Convert raw source records through the actual SAM binding contract."""
    groups, _ = _prepare_samples(arguments[0], arguments[1], arguments[3],
        video_sha256=arguments[-1]["video_sha256"], job_id=arguments[-1]["source_run_id"])
    bindings = {sample.frame_index: deepcopy(sample.source_binding)
                for group in groups.values() for sample in group}
    for record in arguments[0]:
        frame = record["frame"]
        if selected_frames is not None and frame["index"] not in selected_frames:
            continue
        original = {point["name"]: point for point in record["poses"][0]["keypoints"]}
        keypoints = []
        for definition in keypoint_schema("mhr70")["keypoints"]:
            point = original.get(definition["name"])
            keypoints.append({**definition, "source": "sam3d", "observation_semantics": "model_estimate",
                "confidence_semantics": "not_provided_by_sam3d", "confidence": None,
                "observed": None, "visibility": None,
                "raw_x_px": point["x_px"] if point else None,
                "raw_y_px": point["y_px"] if point else None, "in_frame": point is not None})
        record["poses"] = [{"person_track_id": 1, "source": "sam3d", "keypoint_format": "mhr70",
            "observation_semantics": "monocular_model_estimate",
            "confidence_semantics": "not_provided_by_sam3d", "confidence": None,
            "status": "ok", "coordinate_space": "original_frame",
            "source_binding": {**bindings[frame["index"]], "processed_index": frame["processed_index"]},
            "keypoints": keypoints}]


def gs_report(arguments):
    """Explicit synthetic preparation window, independent of FS step truth."""
    source = arguments[-1]
    window = source["windows"][0]
    doc = {"version": ANNOTATION_VERSION, "source_run_id": source["source_run_id"],
        "video_sha256": source["video_sha256"],
        "input_artifact_sha256": {k: v for k, v in source["input_artifact_sha256"].items() if k != "events.jsonl"},
        "actions": [{"action_id": "synthetic-gs", "technique_code": "GS01", "person_track_id": 1,
            "start_ms": window["start_ms"], "end_ms": window["end_ms"]}],
        "stages": [{"annotation_id": "synthetic-gs-stage", "action_id": "synthetic-gs", "technique_code": "GS01",
            "stage_code": "GS01-M03", "person_track_id": 1, "start_ms": window["start_ms"], "end_ms": window["end_ms"],
            "binding": {"method": "manual_anchor", "version": "synthetic-contract-v1",
                "source": "synthetic-contract-only-no-real-stage-label", "uncertainty_ms": 0,
                "semantics": "reviewer_estimated_source_stage_not_expert_ground_truth"}}]}
    return compute_gs_card_execution(arguments[0], arguments[1], arguments[3], doc,
        source_job_id=source["source_run_id"])


def gs_row(arguments):
    return next(row for row in instances(gs_report(arguments)) if row["indicator_id"] == "GS01-M03-05")


class FormalCoreInputTests(unittest.TestCase):
    def test_detector_full_model_and_mixed_mapping_have_identical_numeric_score(self):
        for count, as_mapping in ((0, False), (10, False), (1, False), (1, True)):
            rule, samples, window, timeline = core_fixture()
            for sample, clock in list(zip(samples, timeline))[:count]:
                bind_model(sample, clock)
            if as_mapping:
                samples = [vars(sample) for sample in samples]
            before = deepcopy(samples)
            result = evaluate_card_window(rule, samples, window, timeline)
            self.assertEqual(samples, before)
            self.assertEqual((result["status"], result["grade"], result["score"]), ("scored", "A", 100))
            self.assertEqual(result["scoring_policy_version"], SCORING_POLICY_VERSION)
            self.assertEqual([c["value"] for c in result["conditions"]], [1, 0])
            self.assertTrue(all(c["passed"] for c in result["conditions"]))
            self.assertIsNone(result["original_grade"])
            if count:
                self.assertEqual(result["measurement_provenance"]["model_estimate_sample_count"], count)
                self.assertEqual(result["measurement_provenance"]["detector_sample_count"], 10-count)
                self.assertNotIn("scoring_allowed", result["measurement_provenance"])
            else:
                self.assertNotIn("measurement_provenance", result)

    def test_model_missing_binding_or_common_geometry_cannot_become_formal(self):
        for field in ("missing_binding", "pts", "video_sha256", "person_track_id", "selection_epoch",
                      "camera_reference_epoch", "missing_geometry"):
            rule, samples, window, timeline = core_fixture()
            for sample, clock in zip(samples, timeline): bind_model(sample, clock)
            if field == "missing_binding": samples[0].source_binding = {}
            elif field == "missing_geometry":
                for sample in samples[:4]: del sample.points["left_ankle"]
            elif field == "video_sha256": samples[0].source_binding[field] = "b"*64
            else: samples[0].source_binding[field] += 1
            result = evaluate_card_window(rule, samples, window, timeline)
            self.assertEqual(result["status"], "unavailable", field)
            self.assertIsNone(result["grade"])
            self.assertIsNone(result["score"])
            self.assertTrue(all(c["passed"] is None for c in result["conditions"]))

    def test_missing_formula_stays_unavailable_and_does_not_receive_default_zero(self):
        rule, samples, window, timeline = core_fixture()
        bind_model(samples[0], timeline[0])
        rule["evaluation"]["kind"] = "manual_review"
        result = evaluate_card_window(rule, samples, window, timeline)
        self.assertEqual(result["status"], "unavailable")
        self.assertIsNone(result["score"])
        self.assertEqual(result["measurement_provenance"]["model_estimate_sample_count"], 1)


class FormalRawRecordTests(unittest.TestCase):
    def test_explicit_primary_video_and_epochs_must_match_source_and_be_continuous(self):
        for field, bad in (("selection_epoch", 999), ("selection_epoch", "bad"),
                           ("camera_reference_epoch", 999), ("video_sha256", "f"*64)):
            args = record_fixture()
            _, timeline = _prepare_samples(args[0], args[1], args[3],
                video_sha256=args[-1]["video_sha256"], job_id=args[-1]["source_run_id"])
            derived = {row["frame_index"]: row for row in timeline}
            for selected in args[1]:
                source = derived[selected["source_frame_index"]]
                selected.update({key: source[key] for key in ("selection_epoch", "camera_reference_epoch", "video_sha256")})
            self.assertEqual(gs_row(args)["score"], 100)
            self.assertEqual(instances(compute(args))[0]["score"], 100)
            first = args[-1]["windows"][0]["start_source_frame"]
            selected = next(row for row in args[1] if row["source_frame_index"] == first)
            selected[field] = bad
            if field == "selection_epoch" and isinstance(bad, int):
                self.assertEqual(gs_row(args)["status"], "unavailable")
                self.assertEqual(instances(compute(args))[0]["status"], "unavailable")
            else:
                for evaluate in (gs_report, compute):
                    with self.assertRaisesRegex(ValueError, "primary_source_(epoch|video)_binding_mismatch"):
                        evaluate(args)

    def test_primary_and_model_epoch_labels_are_independent_and_preserved(self):
        for primary_epoch in (None, 0, 1):
            for model_epoch in (0, 1):
                for mixed in (False, True):
                    with self.subTest(primary=primary_epoch, model=model_epoch, mixed=mixed):
                        args = record_fixture()
                        first = args[-1]["windows"][0]["start_source_frame"]
                        model_records(args, {first} if mixed else None)
                        for record in args[0]:
                            for pose in record["poses"]:
                                if pose.get("source") == "sam3d":
                                    pose["source_binding"]["selection_epoch"] = model_epoch
                        if primary_epoch is not None:
                            for row in args[1]: row["selection_epoch"] = primary_epoch
                        before = deepcopy(args)
                        result, = instances(compute(args))
                        self.assertEqual((result["status"], result["score"]), ("scored", 100))
                        self.assertEqual(gs_row(args)["score"], 100)
                        self.assertEqual(args, before)
                        proof = result["source_window_evidence"]
                        primary_label = 1 if primary_epoch is None else primary_epoch
                        self.assertEqual(result["window"]["selection_epoch"], primary_label)
                        for row, original in zip(proof["step_motion"]["source_geometry_series"], proof["source_frame_bindings"]):
                            self.assertEqual(original["selection_epoch"], primary_label)
                            if row["observation_semantics"] == "monocular_model_estimate":
                                self.assertEqual(row["source_binding"]["selection_epoch"], model_epoch)
                                self.assertEqual(row["source_model_selection_epoch"], model_epoch)
                                self.assertEqual(original["source_model_selection_epoch"], model_epoch)
                                self.assertEqual(row["selection_epoch"], primary_label)
                                self.assertEqual(row["model_epoch_semantics"], original["model_epoch_semantics"])

    def test_raw_primary_and_model_epoch_changes_are_window_local_hard_gates(self):
        for source in ("primary", "model"):
            for inside in (False, True):
                args = record_fixture()
                first = args[-1]["windows"][0]["start_source_frame"]
                model_records(args)
                for record in args[0]: record["poses"][0]["source_binding"]["selection_epoch"] = 0
                for row in args[1]: row["selection_epoch"] = 0
                index = first+2 if inside else first-1
                if source == "primary":
                    next(row for row in args[1] if row["source_frame_index"] == index)["selection_epoch"] = 2
                else:
                    next(row for row in args[0] if row["frame"]["index"] == index)["poses"][0]["source_binding"]["selection_epoch"] = 2
                for result in (gs_row(args), instances(compute(args))[0]):
                    self.assertEqual(result["status"], "unavailable" if inside else "scored", (source, inside))
                    self.assertEqual(result["score"], None if inside else 100)
                    if inside:
                        self.assertIsNone(result["grade"])
                        self.assertTrue(all(c["passed"] is None for c in result["conditions"]))

    def test_model_epoch_markers_cannot_replace_or_override_actual_binding(self):
        args = record_fixture()
        model_records(args)
        for record in args[0]:
            record["poses"][0]["source_binding"]["selection_epoch"] = 0
            record["poses"][0].update(source_model_selection_epoch=999, model_epoch_semantics="caller-claim")
        baseline = gs_row(args)
        self.assertEqual(baseline["score"], 100)
        groups, timeline = _prepare_samples(args[0], args[1], args[3],
            video_sha256=args[-1]["video_sha256"], job_id=args[-1]["source_run_id"])
        window = baseline["window"]
        timeline = [row for row in timeline if window["start_ms"] <= row["timestamp_ms"] <= window["end_ms"]]
        samples = [row for row in groups[1] if window["start_ms"] <= row.time <= window["end_ms"]]
        self.assertTrue(all(s.source_model_selection_epoch == 0 for s in samples))
        for target in ("sample", "timeline", "binding", "missing_marker", "boolean_timeline", "boolean_binding"):
            observed, clocks = deepcopy(samples), deepcopy(timeline)
            if target == "sample": observed[0].source_model_selection_epoch = 1
            elif target == "timeline": clocks[0]["source_model_selection_epoch"] = 1
            elif target == "binding": observed[0].source_binding["selection_epoch"] = 1
            elif target == "boolean_timeline": clocks[0]["source_model_selection_epoch"] = False
            elif target == "boolean_binding": observed[0].source_binding["selection_epoch"] = False
            else:
                del observed[0].source_model_selection_epoch
                del observed[0].model_epoch_semantics
            result = evaluate_card_window(get_card_rule("GS01-M03-05"), observed, window, clocks)
            self.assertEqual(result["status"], "unavailable", target)
            self.assertIsNone(result["score"])

    def test_partial_primary_labels_cannot_hide_actual_derived_epoch_breaks(self):
        for source in ("detector", "full_model", "mixed"):
            for declaration in ("absent", "complete", "partial"):
                for inside in (False, True):
                    with self.subTest(source=source, declaration=declaration, inside=inside):
                        args = record_fixture()
                        first = args[-1]["windows"][0]["start_source_frame"]
                        broken = first+5 if inside else first-1
                        if source != "detector":
                            model_records(args, {first+1} if source == "mixed" else None)
                            for record in args[0]:
                                for pose in record["poses"]:
                                    if pose.get("source") == "sam3d": pose["source_binding"]["selection_epoch"] = 0
                        if declaration != "absent":
                            for row in args[1]:
                                if declaration == "complete" or row["source_frame_index"] < broken:
                                    row["selection_epoch"] = 2
                        # Derived primary epoch becomes 2 at this frame. A
                        # partial explicit label 2 before it must not disguise
                        # the real 1 -> 2 continuity break as one stable label.
                        next(r for r in args[0] if r["frame"]["index"] == broken)["camera_motion"]["status"] = "unavailable"
                        groups, timeline = _prepare_samples(args[0], args[1], args[3],
                            video_sha256=args[-1]["video_sha256"], job_id=args[-1]["source_run_id"])
                        self.assertTrue(all("derived_selection_epoch" in r for r in timeline))
                        for result in (gs_row(args), instances(compute(args))[0]):
                            self.assertEqual(result["status"], "unavailable" if inside else "scored")
                            self.assertEqual(result["score"], None if inside else 100)
                            if inside and declaration != "absent":
                                self.assertIn("primary_continuity_epoch_changed", result["reason_codes"])

    def test_partial_or_invalid_derived_ledger_is_not_a_continuity_proof(self):
        rule, samples, window, timeline = core_fixture()
        for row in timeline: row["derived_selection_epoch"] = 7
        window["derived_selection_epoch"] = 7
        self.assertEqual(evaluate_card_window(rule, samples, window, timeline)["score"], 100)
        for value in (None, False, "7", 8):
            clocks = deepcopy(timeline)
            if value is None: del clocks[5]["derived_selection_epoch"]
            else: clocks[5]["derived_selection_epoch"] = value
            result = evaluate_card_window(rule, samples, window, clocks)
            self.assertEqual(result["status"], "unavailable", value)
            self.assertIn("primary_continuity_epoch_changed", result["reason_codes"])
        for row in timeline: del row["derived_selection_epoch"]
        result = evaluate_card_window(rule, samples, window, timeline)
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("primary_continuity_epoch_changed", result["reason_codes"])

    def test_full_and_mixed_sam_records_are_parsed_and_formally_scored(self):
        for whole_window in (True, False):
            args = record_fixture()
            first = args[-1]["windows"][0]["start_source_frame"]
            model_records(args, None if whole_window else {first})
            row, = instances(compute(args))
            self.assertEqual((row["status"], row["grade"], row["score"]), ("scored", "A", 100))
            self.assertTrue(row["source_window_evidence"]["step_cycle_bound"])
            self.assertEqual(row["measurement_provenance"]["model_estimate_sample_count"], 18 if whole_window else 1)
            report = gs_report(args)
            current = next(r for r in instances(report) if r["indicator_id"] == "GS01-M03-05")
            self.assertEqual((current["status"], current["grade"], current["score"]), ("scored", "A", 100))
            self.assertGreater(report["summary"]["channels"]["formal"]["scored_cards"], 0)

    def test_selected_invalid_sam_is_not_silently_dropped_to_a_detector_score(self):
        for damage in ("missing", "wrong_person", "wrong_pts", "wrong_video"):
            args = record_fixture()
            first = args[-1]["windows"][0]["start_source_frame"]
            model_records(args, {first})
            pose = next(r for r in args[0] if r["frame"]["index"] == first)["poses"][0]
            if damage == "missing": pose.pop("source_binding")
            elif damage == "wrong_person": pose["source_binding"]["person_track_id"] = 99
            elif damage == "wrong_pts": pose["source_binding"]["pts"] += 1
            else: pose["source_binding"]["video_sha256"] = "f"*64
            row = gs_row(args)
            self.assertEqual(row["status"], "unavailable", damage)
            self.assertIsNone(row["score"], damage)
            self.assertTrue(set(row["reason_codes"]) & {"selected_model_observation_rejected",
                "model_source_binding_unverified", "model_decoder_pts_mismatch", "video_identity_mismatch"}, row)

    def test_unrelated_person_and_outside_window_rejections_do_not_taint_selected_window(self):
        for unrelated in (True, False):
            args = record_fixture()
            first = args[-1]["windows"][0]["start_source_frame"]
            index = first if unrelated else first-1
            original = deepcopy(next(r for r in args[0] if r["frame"]["index"] == index)["poses"][0])
            model_records(args, {index})
            record = next(r for r in args[0] if r["frame"]["index"] == index)
            pose = record["poses"][0]
            pose.pop("source_binding")
            if unrelated:
                pose["person_track_id"] = 99
                record["poses"].append(original)
            row = gs_row(args)
            self.assertEqual((row["status"], row["score"]), ("scored", 100), row["reason_codes"])

    def test_detector_missing_frame_retains_seventy_percent_contract(self):
        args = record_fixture()
        first = args[-1]["windows"][0]["start_source_frame"]
        next(r for r in args[0] if r["frame"]["index"] == first)["poses"] = []
        row = gs_row(args)
        self.assertEqual((row["status"], row["score"]), ("scored", 100))
        self.assertEqual((row["quality"]["valid_frame_count"], row["quality"]["total_frame_count"]), (17, 18))

    def test_soft_flags_and_unconfirmed_identity_preserve_score_and_source_diagnostics(self):
        args = record_fixture()
        event = args[2][0]
        event["quality_flags"] = ["keypoint_jump_candidates_present", "primary_pose_coverage_low",
            "primary_identity_ambiguous", "source_track_switch_candidates_present"]
        event["track_diagnostics"].update(primary_identity_ambiguous=True,
            source_track_switch_candidate_count=2, confirmed_id_switch_count=0,
            keypoint_jump_candidate_joints=["right_ankle"])
        for row in args[1]: row["identity_ambiguous"] = True
        result, = instances(compute(args))
        self.assertEqual((result["status"], result["score"]), ("scored", 100))
        self.assertEqual(result["reason_codes"], [])
        self.assertTrue(set(event["quality_flags"]) <= set(result["advisory_flags"]))
        original, = result["source_window_evidence"]["original_event_quality"]
        self.assertEqual(original["source_track_diagnostics"], event["track_diagnostics"])
        self.assertEqual(original["blocking_flags"], [])
        self.assertEqual(gs_row(args)["status"], "scored")

    def test_hard_quality_censor_binding_and_continuity_cannot_keep_a_grade(self):
        for target in ("confirmed", "censored", "coverage", "multiple_tracks", "wrong_track", "epoch"):
            args = record_fixture()
            event = args[2][0]
            if target == "confirmed": event["track_diagnostics"]["confirmed_id_switch_count"] = 1
            elif target == "censored": event["quality_flags"] = ["window_left_censored"]
            elif target == "coverage": event["start_ms"] += 200
            elif target == "multiple_tracks": event["track_diagnostics"]["source_track_ids"] = [1, 2]
            elif target == "wrong_track": event["track_diagnostics"]["source_track_ids"] = [2]
            else:
                first = args[-1]["windows"][0]["start_source_frame"]
                row = next(r for r in args[0] if r["frame"]["index"] == first+5)
                row["camera_motion"]["reference_epoch"] += 1
            row, = instances(compute(args))
            self.assertEqual(row["status"], "unavailable", target)
            self.assertIsNone(row["score"], target)
            self.assertIsNone(row["grade"], target)
            self.assertTrue(all(c["passed"] is None for c in row["conditions"]), target)


if __name__ == "__main__":
    unittest.main()
