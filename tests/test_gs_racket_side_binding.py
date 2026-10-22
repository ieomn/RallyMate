"""Manual wrist-side provenance; synthetic motion is mathematical testing only."""
from copy import deepcopy
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import unittest

from rallymate_service.gs_card_execution import (
    ANNOTATION_VERSION, RACKET_SIDE_SEMANTICS, RACKET_SIDE_VERSION,
    build_gs_card_execution_report, compute_gs_card_execution, validate_stage_annotations,
)

ROOT = Path(__file__).resolve().parents[1]


def synthetic_case(count=12, technique="GS01", side="right"):
    """Constant left speed and increasing right speed; no real tennis truth."""
    video, job = "a" * 64, "synthetic-wrist-math-only"
    points = {"nose": (200, 45), "left_shoulder": (185, 60), "right_shoulder": (215, 60),
        "left_hip": (187.5, 110), "right_hip": (212.5, 110),
        "left_knee": (180, 140), "right_knee": (220, 140),
        "left_ankle": (175, 160), "right_ankle": (225, 160),
        "left_elbow": (170, 75), "right_elbow": (230, 75),
        "left_wrist": (165, 85), "right_wrist": (235, 85)}
    records, primary, decoder = [], [], []
    for i in range(count):
        raw = dict(points)
        raw["left_wrist"] = (165 + i * .1, 85)
        raw["right_wrist"] = (235 + i * i * .25, 85)
        records.append({"job_id": job,
            "frame": {"index": i, "processed_index": i, "timestamp_ms": i * 40,
                "timestamp_source": "decoder_pts", "width": 432, "height": 240, "coordinate_origin": "top_left"},
            "camera_motion": {"status": "stationary", "compensation_valid": True,
                "matrix_to_reference": [[1., 0., 0.], [0., 1., 0.]], "reference_epoch": 0},
            "poses": [{"person_track_id": 1, "confidence": .99,
                "keypoints": [{"name": name, "x_px": xy[0], "y_px": xy[1], "confidence": .99}
                              for name, xy in raw.items()]}]})
        primary.append({"processed_index": i, "source_frame_index": i, "timestamp_ms": i * 40,
            "selection_status": "selected", "source_track_id": 1, "identity_ambiguous": False})
        decoder.append({"frame_index": i, "pts": i * 40, "time_base_num": 1, "time_base_den": 1000, "video_sha256": video})
    action = {"action_id": "synthetic-action", "technique_code": technique, "person_track_id": 1,
        "start_ms": 0, "end_ms": (count - 1) * 40}
    if side is not None:
        action["racket_side_observation"] = {"version": RACKET_SIDE_VERSION, "method": "manual_visual_observation",
            "action_id": action["action_id"], "video_sha256": video, "person_track_id": 1,
            "measurement_wrist_side": side, "observed_racket_hands": "both" if technique == "GS02" else side,
            "source": "synthetic mathematical unit fixture; not real visual evidence",
            "source_description": "Assigned wrist side for arithmetic only.", "uncertainty_ms": 0,
            "semantics": RACKET_SIDE_SEMANTICS,
            "source_frames": [{key: decoder[0][key] for key in ("frame_index", "pts", "time_base_num", "time_base_den")}]}
    document = {"version": ANNOTATION_VERSION, "source_run_id": job, "video_sha256": video,
        "input_artifact_sha256": {key: "b" * 64 for key in ("frames.jsonl", "primary-player.jsonl", "summary.json")},
        "actions": [action], "stages": [{"annotation_id": "synthetic-m07", "action_id": action["action_id"],
            "technique_code": technique, "stage_code": technique + "-M07", "person_track_id": 1,
            "start_ms": 0, "end_ms": (count - 1) * 40,
            "binding": {"method": "manual_anchor", "version": "synthetic-math-v1", "source": "synthetic math only",
                "uncertainty_ms": 0, "semantics": "reviewer_estimated_source_stage_not_expert_ground_truth"}}]}
    return {"frame_records": records, "primary_timeline": primary, "decoder_timeline": decoder,
        "annotations": document, "source_job_id": job}


def compute(case):
    return compute_gs_card_execution(case["frame_records"], case["primary_timeline"], case["decoder_timeline"],
        case["annotations"], source_job_id=case["source_job_id"])


def wrist(report, technique="GS01"):
    return next(card["instances"][0] for card in report["cards"] if card["indicator_id"] == technique + "-M07-01")


class GSRacketSideBindingTests(unittest.TestCase):
    def test_explicit_side_selects_declared_wrist_not_faster_wrist(self):
        right, left = wrist(compute(synthetic_case())), wrist(compute(synthetic_case(side="left")))
        self.assertEqual(right["status"], "proxy_scored")
        self.assertAlmostEqual(right["conditions"][0]["value"], 2.)
        self.assertAlmostEqual(left["conditions"][0]["value"], 0.)
        self.assertTrue(right["conditions"][0]["passed"])
        self.assertFalse(left["conditions"][0]["passed"])
        self.assertFalse(right["source_grade_supported"])
        self.assertIsNone(right["original_grade"])

    def test_unknown_side_and_generic_event_side_stay_unavailable(self):
        case = synthetic_case(side=None)
        for row in case["frame_records"]:
            row["racket_side"] = "right"
            row["poses"][0]["racket_side"] = "right"
        actual = wrist(compute(case))
        self.assertEqual(actual["status"], "unavailable")
        self.assertIsNone(actual["score"])
        self.assertIn("racket_side_unresolved", actual["conditions"][0]["evidence"]["reason_codes"])
        self.assertNotIn("racket_side", actual["window"])

    def test_short_stage_cannot_be_rescued_by_side_binding(self):
        for count in (3, 5, 6):
            actual = wrist(compute(synthetic_case(count=count)))
            self.assertEqual(actual["status"], "unavailable")
            self.assertIn("continuous_endpoint_window_missing", actual["conditions"][0]["evidence"]["reason_codes"])
            self.assertIsNone(actual["score"])

    def test_exactly_seven_frames_preserves_minimum_three_endpoint_speeds(self):
        actual = wrist(compute(synthetic_case(count=7)))
        self.assertEqual(actual["status"], "proxy_scored")
        self.assertAlmostEqual(actual["conditions"][0]["value"], .75)

    def test_double_backhand_requires_both_observed_hands_but_not_dominant_hand(self):
        for side in ("left", "right"):
            case = synthetic_case(technique="GS02", side=side)
            actual = wrist(compute(case), "GS02")
            self.assertEqual(actual["status"], "proxy_scored")
            self.assertFalse(actual["source_annotation"]["racket_side_observation"]["dominant_hand_established"])
            case["annotations"]["actions"][0]["racket_side_observation"]["observed_racket_hands"] = side
            with self.assertRaisesRegex(ValueError, "hands_or_technique_mismatch"):
                compute(case)

    def test_evidence_carries_exact_pts_document_and_sampled_only_semantics(self):
        report = compute(synthetic_case())
        actual = wrist(report)
        proof = actual["source_annotation"]["racket_side_observation"]
        self.assertEqual(proof, actual["window"]["racket_side_binding"])
        self.assertEqual(proof["document_sha256"], report["input"]["annotation_document_sha256"])
        self.assertEqual(proof["document_version"], ANNOTATION_VERSION)
        self.assertEqual(proof["technique_code"], "GS01")
        self.assertEqual(proof["action_window_ms"], {"start_ms": 0, "end_ms": 440})
        self.assertEqual(proof["evidence_scope"], "sampled_frames_only")
        self.assertTrue(proof["stage_continuity_verified"])
        self.assertFalse(proof["continuous_racket_hold_established"])
        self.assertFalse(proof["expert_adjudicated"])
        self.assertEqual(proof["source_frame_bindings"][0], {"frame_index": 0, "pts": 0,
            "time_base_num": 1, "time_base_den": 1000, "video_sha256": "a" * 64,
            "person_track_id": 1, "timestamp_ms": 0., "selection_epoch": 1, "camera_reference_epoch": 0})
        self.assertFalse(report["input"]["artifact_files_verified"])

    def test_wrong_action_person_video_and_side_are_rejected(self):
        for key, value in (("action_id", "other-action"), ("person_track_id", 2), ("video_sha256", "c" * 64),
                           ("measurement_wrist_side", "dominant"), ("observed_racket_hands", "left")):
            with self.subTest(key=key):
                case = synthetic_case(); case["annotations"]["actions"][0]["racket_side_observation"][key] = value
                with self.assertRaises(ValueError):
                    compute(case)

    def test_unknown_fields_cannot_supply_quality_grade_or_coords(self):
        for key, value in (("score", 100), ("quality", {"valid": True}), ("passed", True),
                           ("dominant_hand", "right"), ("wrist_speed", 99), ("racket_side", "right")):
            case = synthetic_case(); case["annotations"]["actions"][0]["racket_side_observation"][key] = value
            with self.assertRaisesRegex(ValueError, "annotation_schema_fields_invalid"):
                compute(case)
        case = synthetic_case(); case["annotations"]["actions"][0]["racket_side_observation"]["source_frames"][0]["quality"] = True
        with self.assertRaisesRegex(ValueError, "annotation_schema_fields_invalid"):
            compute(case)

    def test_exact_pts_and_timebase_must_match_not_only_equal_timestamp(self):
        for changes in ({"pts": 1}, {"time_base_num": 2, "time_base_den": 2000}, {"frame_index": 99}):
            case = synthetic_case()
            case["annotations"]["actions"][0]["racket_side_observation"]["source_frames"][0].update(changes)
            with self.assertRaisesRegex(ValueError, "source_frame_or_pts_mismatch"):
                compute(case)

    def test_missing_or_duplicate_or_reversed_observation_frames_rejected(self):
        for frames in ([], [{"frame_index": 0, "pts": 0, "time_base_num": 1, "time_base_den": 1000}] * 2,
                       [{"frame_index": i, "pts": i * 40, "time_base_num": 1, "time_base_den": 1000} for i in (2, 1)]):
            case = synthetic_case(); case["annotations"]["actions"][0]["racket_side_observation"]["source_frames"] = frames
            with self.assertRaises(ValueError):
                compute(case)

    def test_unselected_missing_or_unobserved_wrist_cannot_bind(self):
        for kind in ("unselected", "missing", "wrist"):
            case = synthetic_case()
            if kind == "unselected":
                case["primary_timeline"][0]["identity_ambiguous"] = True
            elif kind == "missing":
                case["frame_records"].pop(0); case["primary_timeline"].pop(0)
            else:
                case["frame_records"][0]["poses"][0]["keypoints"] = [p for p in case["frame_records"][0]["poses"][0]["keypoints"] if p["name"] != "right_wrist"]
            with self.assertRaisesRegex(ValueError, "racket_side_"):
                compute(case)

    def test_observation_frame_cannot_come_from_another_action_interval(self):
        case = synthetic_case(); action = case["annotations"]["actions"][0]
        action["start_ms"] = 40; case["annotations"]["stages"][0]["start_ms"] = 40
        with self.assertRaisesRegex(ValueError, "source_frame_outside_action"):
            compute(case)

    def test_invalid_wrist_confidence_cannot_authorize_manual_measurement_side(self):
        for confidence in (.2, 1.1, True, None):
            case = synthetic_case()
            point = next(p for p in case["frame_records"][0]["poses"][0]["keypoints"] if p["name"] == "right_wrist")
            point["confidence"] = confidence
            with self.assertRaisesRegex(ValueError, "source_wrist_observation_missing"):
                compute(case)

    def test_source_epoch_is_preserved_and_cannot_transfer_to_new_stage_epoch(self):
        case = synthetic_case(count=15)
        for record in case["frame_records"][2:]:
            record["camera_motion"]["reference_epoch"] = 9
        case["annotations"]["stages"][0]["start_ms"] = 120
        actual = wrist(compute(case))
        self.assertEqual(actual["window"]["camera_reference_epoch"], 9)
        self.assertEqual(actual["window"]["selection_epoch"], 2)
        self.assertFalse(actual["window"]["racket_side_binding"]["stage_continuity_verified"])
        self.assertNotIn("racket_side", actual["window"])
        self.assertEqual(actual["status"], "unavailable")
        self.assertIn("racket_side_unresolved", actual["conditions"][0]["evidence"]["reason_codes"])

    def test_continuity_flag_never_hides_stage_internal_epoch_change_or_missing_frame(self):
        for kind in ("camera", "missing"):
            case = synthetic_case()
            if kind == "camera":
                case["frame_records"][6]["camera_motion"]["reference_epoch"] = 9
            else:
                case["frame_records"].pop(6); case["primary_timeline"].pop(6)
            actual = wrist(compute(case))
            self.assertFalse(actual["window"]["racket_side_binding"]["stage_continuity_verified"])
            self.assertNotIn("racket_side", actual["window"])
            self.assertEqual(actual["status"], "unavailable")

    def test_observation_changes_canonical_document_digest_and_never_mutates_inputs(self):
        case = synthetic_case(); saved = deepcopy(case)
        before = compute(case)
        self.assertEqual(case, saved)
        case["annotations"]["actions"][0]["racket_side_observation"]["source_description"] = "Different review description"
        after = compute(case)
        self.assertNotEqual(before["input"]["annotation_document_sha256"], after["input"]["annotation_document_sha256"])

    def test_old_real_annotation_documents_remain_valid_without_side(self):
        fixture = json.loads((ROOT / "tests/fixtures/gs-card-real-mit-preparation-v1.json").read_text(encoding="utf-8"))
        for case in fixture["cases"]:
            self.assertEqual(validate_stage_annotations(case["annotations"]), case["annotations"])

    def test_binding_version_semantics_and_uncertainty_are_strict(self):
        for key, value in (("version", "caller-version"), ("method", "versioned_detector"),
                           ("semantics", "expert_ground_truth"), ("uncertainty_ms", -1),
                           ("uncertainty_ms", True), ("uncertainty_ms", float("nan")), ("source_description", " ")):
            case = synthetic_case(); case["annotations"]["actions"][0]["racket_side_observation"][key] = value
            with self.assertRaises(ValueError):
                compute(case)

    def test_real_sampled_side_evidence_does_not_lengthen_or_score_short_m07_windows(self):
        fixture = json.loads((ROOT / "tests/fixtures/gs-racket-side-observations-20261022.json").read_text(encoding="utf-8"))
        old_path = ROOT / fixture["base_stage_manifest"]
        self.assertEqual(fixture["base_stage_manifest_digest_kind"], "canonical_json_sorted_utf8_v1")
        canonical = json.dumps(json.loads(old_path.read_text(encoding="utf-8")), sort_keys=True,
            ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
        self.assertEqual(hashlib.sha256(canonical).hexdigest(), fixture["base_stage_manifest_sha256"])
        raw = {case["case_id"]: case for case in json.loads((ROOT /
            "tests/fixtures/gs-card-real-mit-preparation-v1.json").read_text(encoding="utf-8"))["cases"]}
        counts, frame_counts = Counter(), []
        for reviewed in fixture["cases"]:
            case = deepcopy(raw[reviewed["case_id"]]); case["annotations"] = reviewed["annotations"]
            observed = compute(case)
            counts.update(observed["coverage"]["instance_status_counts"])
            frame_counts.append(len(observed["stage_windows"][0]["source_frames"]))
            actual = wrist(observed, reviewed["annotations"]["actions"][0]["technique_code"])
            self.assertEqual(actual["window"]["racket_side"], "right")
            self.assertEqual(actual["status"], "unavailable")
            self.assertIsNone(actual["score"])
            self.assertFalse(actual["source_grade_supported"])
            no_side = deepcopy(case)
            no_side["annotations"]["actions"][0].pop("racket_side_observation")
            previous = compute(no_side)
            self.assertEqual(observed["coverage"], previous["coverage"])
            self.assertEqual(observed["summary"], previous["summary"])
            for a, b in zip(observed["cards"], previous["cards"]):
                for x, y in zip(a["instances"], b["instances"]):
                    for key in ("status", "proxy_grade", "score", "conditions", "reason_codes"):
                        self.assertEqual(x[key], y[key])
        self.assertEqual(frame_counts, [5, 2, 3])
        self.assertEqual(counts, {"proxy_scored": 1, "review_required": 5, "unavailable": 9})

    @unittest.skipUnless(os.environ.get("GS_RACKET_ARTIFACT_ROOT"), "requires explicitly selected original MIT artifacts")
    def test_original_video_artifact_replay_verifies_all_three_sampled_side_bindings(self):
        artifact_root = Path(os.environ["GS_RACKET_ARTIFACT_ROOT"])
        fixture = json.loads((ROOT / "tests/fixtures/gs-racket-side-observations-20261022.json").read_text(encoding="utf-8"))
        counts = Counter()
        for case in fixture["cases"]:
            image = artifact_root / case["source_frame_image"]
            self.assertEqual(hashlib.sha256(image.read_bytes()).hexdigest(), case["source_frame_image_sha256"])
            actual = build_gs_card_execution_report(artifact_root / case["run_dir"], case["annotations"],
                video_path=artifact_root / case["local_video"])
            self.assertTrue(actual["input"]["artifact_files_verified"])
            self.assertTrue(actual["input"]["decoder_verified_from_original_video"])
            self.assertTrue(actual["input"]["service_eligible"])
            self.assertEqual(actual["input"]["video_sha256"], case["video_sha256"])
            counts.update(actual["coverage"]["instance_status_counts"])
        self.assertEqual(counts, {"proxy_scored": 1, "review_required": 5, "unavailable": 9})


if __name__ == "__main__":
    unittest.main()
