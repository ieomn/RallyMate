"""Strict optional ready-reference plumbing, using analytic source records."""
from copy import deepcopy
import json
import unittest

from rallymate_service.gs_card_execution import ANNOTATION_VERSION, compute_gs_card_execution, validate_stage_annotations
from rallymate_scoring.gs_recovery_evidence import SCORING_REVISION_ID
from tests.test_gs_recovery_evidence import fixture as geometry_fixture
from tests.test_card_formal_scoring_inputs import model_records


def fixture(*, technique="GS02", angles=None, pts_origin=0, translation_speed=0):
    samples, window, stage_timeline, reference = geometry_fixture(angles, pts_origin=pts_origin, translation_speed=translation_speed)
    by_frame = {s.frame_index: s for s in reference["samples"]+samples}
    timeline = reference["continuity_timeline"]
    records, primary, decoder = [], [], []
    job = "synthetic-m10-source-contract"
    for row in timeline:
        i, time = row["frame_index"], row["pts"]
        sample = by_frame.get(i, reference["samples"][-1])
        frame = {"index": i, "processed_index": i+83, "timestamp_ms": time,
            "timestamp_source": "decoder_pts", "width": 400, "height": 240,
            "video_sha256": "a"*64, "pts": time, "time_base_num": 1, "time_base_den": 1000}
        records.append({"job_id": job, "frame": frame,
            "camera_motion": {"reference_epoch": 0, "status": "stationary", "compensation_valid": True,
                "matrix_to_reference": [[1, 0, 0], [0, 1, 0]]},
            "poses": [{"person_track_id": 1, "source": "synthetic-contract-only", "confidence": .9,
                "coordinate_space": "original_frame", "keypoint_format": "coco17",
                "keypoints": [{"name": name, "x_px": p[0], "y_px": p[1], "confidence": .9}
                    for name, p in sample.reference_points.items()]}]})
        primary.append({"processed_index": i+83, "source_frame_index": i, "timestamp_ms": time,
            "source_track_id": 1, "selection_status": "selected", "identity_ambiguous": False, "selection_epoch": 2})
        decoder.append({"frame_index": i, "pts": time, "time_base_num": 1, "time_base_den": 1000, "video_sha256": "a"*64})
    observation = deepcopy(reference["binding"])
    observation.update(source="synthetic-mathematical-contract-only", source_description="Analytic reference, not a tennis or grip label.", uncertainty_ms=0)
    document = {"version": ANNOTATION_VERSION, "source_run_id": job, "video_sha256": "a"*64,
        "input_artifact_sha256": {name: "b"*64 for name in ("frames.jsonl", "primary-player.jsonl", "summary.json")},
        "actions": [{"action_id": "synthetic-action", "technique_code": technique, "person_track_id": 1,
            "start_ms": reference["action_start_ms"], "end_ms": decoder[-1]["pts"]}],
        "stages": [{"annotation_id": "synthetic-recovery-stage", "action_id": "synthetic-action",
            "technique_code": technique, "stage_code": technique+"-M10", "person_track_id": 1,
            "start_ms": stage_timeline[0]["pts"], "end_ms": stage_timeline[-1]["pts"],
            "binding": {"method": "manual_anchor", "version": "synthetic-math-contract-only-v1",
                "source": "synthetic-source-contract-not-real-tennis", "uncertainty_ms": 0,
                "semantics": "reviewer_estimated_source_stage_not_expert_ground_truth"},
            "recovery_reference_observation": observation}]}
    return [records, primary, decoder, document]


def compute(args):
    return compute_gs_card_execution(*args, source_job_id=args[-1]["source_run_id"])


def instances(report):
    return [instance for row in report["cards"] for instance in row["instances"]]


class GSRecoveryExecutionTests(unittest.TestCase):
    def test_all_thirteen_cards_use_real_parser_and_preserve_source_contract(self):
        count = 0
        for technique in ("GS01", "GS02", "GS03"):
            args = fixture(technique=technique)
            before = deepcopy(args)
            report = compute(args)
            self.assertEqual(args, before)
            rows = instances(report)
            count += len(rows)
            self.assertEqual(sum(row["status"] == "scored" for row in rows), 2)
            self.assertFalse(report["input"]["artifact_files_verified"])
            for row in rows:
                self.assertEqual(row["scoring_revision_id"], SCORING_REVISION_ID)
                self.assertEqual(row["source_annotation"]["action_window"], args[-1]["actions"][0])
                self.assertEqual(row["source_annotation"]["recovery_reference_observation"]["action_id"], row["action_id"])
                self.assertIsNone(row["original_grade"])
                if "recovery_evidence" in row:
                    proof = row["recovery_evidence"]
                    self.assertEqual(len(proof["stage"]["source_frame_bindings"]), 10)
                    self.assertEqual(proof["stage"]["source_geometry_series"][0]["source_binding"]["frame_index"], 6)
            json.dumps(report, allow_nan=False)
        self.assertEqual(count, 13)

    def test_optional_reference_absence_affects_rotation_not_stability(self):
        args = fixture()
        del args[-1]["stages"][0]["recovery_reference_observation"]
        rows = {r["indicator_id"]: r for r in instances(compute(args))}
        self.assertIsNone(rows["GS02-M10-02"]["score"])
        self.assertEqual(rows["GS02-M10-03"]["score"], 100)
        self.assertIn("independent_ready_reference_missing", rows["GS02-M10-02"]["reason_codes"])

    def test_unknown_score_quality_or_reference_fields_are_rejected(self):
        for key, value in (("grade", "A"), ("score", 100), ("quality_flags", []), ("reference_angle", 0), ("passed", True)):
            args = fixture()
            args[-1]["stages"][0]["recovery_reference_observation"][key] = value
            with self.assertRaisesRegex(ValueError, "schema_fields_invalid"):
                compute(args)

    def test_wrong_action_person_video_or_non_pre_action_reference_is_rejected(self):
        for key, value in (("action_id", "other"), ("person_track_id", 2), ("video_sha256", "c"*64),
                           ("method", "versioned_detector"), ("semantics", "expert_truth"), ("uncertainty_ms", True)):
            args = fixture()
            args[-1]["stages"][0]["recovery_reference_observation"][key] = value
            with self.assertRaises(ValueError, msg=key):
                compute(args)
        args = fixture()
        args[-1]["actions"][0]["start_ms"] = 80
        with self.assertRaisesRegex(ValueError, "not_independent_pre_action"):
            compute(args)

    def test_reference_frame_pts_must_match_actual_decoder(self):
        args = fixture()
        args[-1]["stages"][0]["recovery_reference_observation"]["start_frame"]["pts"] += 1
        with self.assertRaisesRegex(ValueError, "frame_or_pts_mismatch"):
            compute(args)

    def test_missing_reference_or_intervening_frame_is_not_dropped(self):
        for frame in (0, 2, 4):
            args = fixture()
            args[0] = [r for r in args[0] if r["frame"]["index"] != frame]
            args[1] = [r for r in args[1] if r["source_frame_index"] != frame]
            row = next(r for r in instances(compute(args)) if r["indicator_id"] == "GS02-M10-02")
            self.assertIsNone(row["score"], frame)
            self.assertEqual(row["recovery_evidence"]["reference"]["total_frame_count"], 4)

    def test_intervening_selector_reset_blocks_rotation_without_polluting_stability(self):
        for field in ("source_track_id", "selection_epoch"):
            args = fixture()
            args[1][4][field] = 3
            if field == "source_track_id":
                args[0][4]["poses"][0]["person_track_id"] = 3
            rows = {r["indicator_id"]: r for r in instances(compute(args))}
            self.assertIsNone(rows["GS02-M10-02"]["score"])
            self.assertEqual(rows["GS02-M10-03"]["score"], 100)

    def test_source_short_window_retains_denominator_without_saved_grade(self):
        args = fixture(angles=[30, 20, 0])
        rows = instances(compute(args))
        self.assertTrue(all(row["score"] is None for row in rows))
        self.assertTrue(all(len(row["source_stage_evidence"]["source_frames"]) == 3 for row in rows))
        self.assertTrue(all(row["status"] == "unavailable" for row in rows))

    def test_full_and_mixed_model_source_epochs_are_preserved(self):
        for selected_frames in (None, {0, 1, 2, 3, 6, 8, 10, 12, 14}):
            args = fixture()
            shim = [args[0], args[1], [], args[2], args[3]]
            model_records(shim, selected_frames)
            for record in args[0]:
                pose = record["poses"][0]
                if pose.get("source") == "sam3d":
                    pose["source_binding"]["selection_epoch"] = 0
            before = deepcopy(args)
            row = next(r for r in instances(compute(args)) if r["indicator_id"] == "GS02-M10-02")
            self.assertEqual(row["score"], 100, row["reason_codes"])
            self.assertEqual(args, before)
            proof = row["recovery_evidence"]
            self.assertEqual(proof["stage"]["window"]["selection_epoch"], 2)
            self.assertTrue(any(r.get("source_model_selection_epoch") == 0 for r in proof["reference"]["continuity_timeline"]))

    def test_model_epoch_change_in_bridge_or_bad_model_binding_is_hard(self):
        for mutation in ("epoch", "binding"):
            args = fixture()
            shim = [args[0], args[1], [], args[2], args[3]]
            model_records(shim)
            if mutation == "epoch":
                args[0][4]["poses"][0]["source_binding"]["selection_epoch"] += 1
            else:
                del args[0][4]["poses"][0]["source_binding"]
            row = next(r for r in instances(compute(args)) if r["indicator_id"] == "GS02-M10-02")
            self.assertIsNone(row["score"])

    def test_nonzero_decoder_origin_is_bound_to_action_reference_and_stage(self):
        row = next(r for r in instances(compute(fixture(pts_origin=9000))) if r["indicator_id"] == "GS02-M10-02")
        self.assertEqual(row["score"], 100)
        self.assertEqual(row["source_annotation"]["action_window"]["start_ms"], 9200)
        self.assertEqual(row["recovery_evidence"]["reference"]["action_start_ms"], 9200)

    def test_reference_is_not_allowed_on_other_source_stages(self):
        args = fixture()
        args[-1]["stages"][0]["stage_code"] = "GS02-M03"
        with self.assertRaisesRegex(ValueError, "stage_invalid"):
            validate_stage_annotations(args[-1])


if __name__ == "__main__":
    unittest.main()
