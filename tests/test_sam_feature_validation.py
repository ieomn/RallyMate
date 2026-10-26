"""Small structural regressions for SAM feature validation, not accuracy evidence."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from rallymate_vision.validation import (
    OutputValidationError,
    _compact_feature,
    _validate_feature_result,
    _validate_scoring_artifacts,
)


# Deliberately independent of the validator's field constants and predicates.
BASE_FIELDS = (
    "feature_name", "feature_version", "value", "unit", "valid",
    "confidence", "reason", "source_frames",
)
MODEL_FIELDS = (
    "measurement_provenance", "source_window", "geometry_mask",
    "required_joints_used", "selected_side",
)
PROVENANCE = {
    "pose_source": "sam3d",
    "observation_semantics": "monocular_model_estimate",
    "assessment_status": "measurement_only",
    "scoring_allowed": False,
    "confidence_available": False,
    "visibility_verified": False,
}


def sam_feature():
    frames = [9, 10, 11, 12]
    local = [300, 333, 367, 400]
    source = [75300, 75333, 75367, 75400]
    return {
        "feature_name": "left_ankle_speed_body_s", "feature_version": "fixture-v1",
        "value": 1.25, "unit": "body/s", "valid": True, "confidence": None,
        "reason": "model_estimate_measurement_only", "source_frames": [9, 12],
        "measurement_provenance": copy.deepcopy(PROVENANCE),
        "source_window": {
            "start_ms": 300, "end_ms": 400, "source_frames": frames,
            "video_timestamps_ms": local, "source_timestamps_ms": source,
            "person_track_id": 7, "selection_epoch": 2, "camera_reference_epoch": 3,
            "video_sha256": "a" * 64,
            "source_bindings": [
                {"video_sha256": "a" * 64, "frame_index": frame,
                 "processed_index": index, "pts": source[index],
                 "time_base_num": 1, "time_base_den": 1000,
                 "timestamp_ms": source[index], "person_track_id": 7,
                 "selection_epoch": 2, "camera_reference_epoch": 3}
                for index, frame in enumerate(frames)
            ],
        },
        "geometry_mask": {"left_ankle": [True, True, False, True]},
        "required_joints_used": ["left_ankle"], "selected_side": "left",
    }


def unavailable_sam_feature():
    feature = sam_feature()
    feature.update(valid=False, value=None, source_frames=[], reason="source_unavailable")
    for name in ("source_window", "geometry_mask", "required_joints_used", "selected_side"):
        feature.pop(name)
    return feature


def ordinary_feature():
    feature = sam_feature()
    for field in MODEL_FIELDS:
        feature.pop(field)
    feature.update(confidence=0.75, reason="ok")
    return feature


def expected_compact(feature):
    return copy.deepcopy({key: feature[key] for key in (*BASE_FIELDS, *MODEL_FIELDS) if key in feature})


class SamFeatureValidationTests(unittest.TestCase):
    def rejects(self, feature):
        with self.assertRaises(OutputValidationError):
            _validate_feature_result(feature, "fixture.feature")

    def test_bound_null_confidence_and_both_clocks_are_preserved(self):
        feature = sam_feature()
        before = copy.deepcopy(feature)
        _validate_feature_result(feature, "fixture.feature")
        self.assertEqual(feature, before)
        self.assertIsNone(feature["confidence"])
        self.assertNotEqual(feature["source_window"]["video_timestamps_ms"],
                            feature["source_window"]["source_timestamps_ms"])

    def test_zero_is_valid_measured_value_with_nonempty_representative_frames(self):
        feature = sam_feature()
        feature["value"] = 0.0
        _validate_feature_result(feature, "fixture.feature")

    def test_single_representative_frame_is_allowed_inside_verified_window(self):
        feature = sam_feature()
        feature["source_frames"] = [10]
        _validate_feature_result(feature, "fixture.feature")

    def test_each_sam_provenance_field_is_required_and_exact(self):
        replacements = {
            "pose_source": "rtmpose", "observation_semantics": "observed",
            "assessment_status": "scored", "scoring_allowed": True,
            "confidence_available": True, "visibility_verified": True,
        }
        for key, replacement in replacements.items():
            for missing in (False, True):
                with self.subTest(field=key, missing=missing):
                    feature = sam_feature()
                    if missing:
                        del feature["measurement_provenance"][key]
                    else:
                        feature["measurement_provenance"][key] = replacement
                    self.rejects(feature)
        for key in ("scoring_allowed", "confidence_available", "visibility_verified"):
            with self.subTest(field=key, false_integer=0):
                feature = sam_feature()
                feature["measurement_provenance"][key] = 0
                self.rejects(feature)

    def test_missing_or_malformed_sam_marker_cannot_authorize_null(self):
        for marker in (None, {}, [], "sam3d"):
            with self.subTest(marker=marker):
                feature = sam_feature()
                feature["measurement_provenance"] = marker
                self.rejects(feature)
        feature = sam_feature()
        del feature["measurement_provenance"]
        self.rejects(feature)

    def test_sam_confidence_must_remain_null_even_if_numeric_value_is_bounded(self):
        for value in (0, 0.5, 1, False, True, float("nan"), float("inf"), -0.1, 1.1):
            with self.subTest(confidence=repr(value)):
                feature = sam_feature()
                feature["confidence"] = value
                self.rejects(feature)

    def test_every_binding_field_is_required(self):
        for field in sam_feature()["source_window"]["source_bindings"][0]:
            with self.subTest(field=field):
                feature = sam_feature()
                del feature["source_window"]["source_bindings"][1][field]
                self.rejects(feature)

    def test_window_clock_identity_and_continuity_mutations_reject(self):
        for field, replacement in (
            ("video_sha256", "b" * 64), ("person_track_id", 9),
            ("selection_epoch", 4), ("camera_reference_epoch", 5),
            ("frame_index", 99), ("processed_index", 99),
            ("pts", 0), ("timestamp_ms", 0), ("time_base_num", 0),
            ("time_base_den", 0),
        ):
            with self.subTest(field=field):
                feature = sam_feature()
                feature["source_window"]["source_bindings"][1][field] = replacement
                self.rejects(feature)
        feature = sam_feature()
        feature["source_window"]["video_timestamps_ms"][1] += 10
        self.rejects(feature)

    def test_window_requires_matching_boolean_geometry_mask(self):
        for mask in (None, {}, [], {"left_ankle": [True]}, {"left_ankle": [1] * 4},
                     {"left_ankle": [True, True, None, True]}):
            with self.subTest(mask=mask):
                feature = sam_feature()
                feature["geometry_mask"] = mask
                self.rejects(feature)
        feature = sam_feature()
        del feature["geometry_mask"]
        self.rejects(feature)

    def test_valid_requires_nonempty_source_frames_inside_window(self):
        for frames in ([], [8], [9, 13], [True], [9.0], [-1], None):
            with self.subTest(frames=frames):
                feature = sam_feature()
                feature["source_frames"] = frames
                self.rejects(feature)

    def test_unavailable_without_window_preserves_unknown_not_fabricated_evidence(self):
        feature = unavailable_sam_feature()
        before = copy.deepcopy(feature)
        _validate_feature_result(feature, "fixture.feature")
        self.assertEqual(feature, before)

    def test_no_window_rejects_measurement_or_any_geometry_claim(self):
        for field, value in (
            ("valid", True), ("value", 0), ("value", 1.25),
            ("source_frames", [9]), ("geometry_mask", {}),
            ("geometry_mask", None), ("geometry_mask", {"left_ankle": [False]}),
        ):
            with self.subTest(field=field, value=value):
                feature = unavailable_sam_feature()
                feature[field] = value
                self.rejects(feature)

    def test_unavailable_with_verified_window_remains_valid_structure(self):
        feature = sam_feature()
        feature.update(valid=False, value=None, source_frames=[], reason="insufficient_geometry")
        _validate_feature_result(feature, "fixture.feature")

    def test_non_sam_confidence_endpoints_remain_accepted(self):
        for value in (0, 0.75, 1):
            with self.subTest(confidence=value):
                feature = ordinary_feature()
                feature["confidence"] = value
                _validate_feature_result(feature, "fixture.feature")

    def test_non_sam_rejects_null_bool_nonfinite_and_out_of_range_confidence(self):
        for value in (None, False, True, float("nan"), float("inf"), -float("inf"), -0.1, 1.1):
            with self.subTest(confidence=repr(value)):
                feature = ordinary_feature()
                feature["confidence"] = value
                self.rejects(feature)

    def test_compact_preserves_all_five_optional_fields_exactly(self):
        feature = sam_feature()
        feature.update(event_id="event-1", raw_value=[1.0, 1.5], provenance={"debug": "not compact"})
        self.assertEqual(_compact_feature(feature), expected_compact(feature))

    def test_compact_preserves_each_optional_field_without_materializing_absent_fields(self):
        for field in MODEL_FIELDS:
            with self.subTest(field=field):
                feature = ordinary_feature()
                feature[field] = copy.deepcopy(sam_feature()[field])
                self.assertEqual(_compact_feature(feature), expected_compact(feature))
        self.assertEqual(_compact_feature(ordinary_feature()), ordinary_feature())
        self.assertEqual(_compact_feature(unavailable_sam_feature()), unavailable_sam_feature())


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _write_bundle(directory, *, feature_mutator=None, compact_mutator=None, score_mutator=None):
    """Four native SAM frames plus a complete, hashed scoring bundle; no scoring loop."""
    from rallymate_features.coordinates import pose_sequence_from_records
    from rallymate_features.schemas import model_geometry_evidence
    from rallymate_scoring.scoring import score_indicator
    from tests.test_sam_footwork import sam_motion_fixture

    frames, primary = sam_motion_fixture(source_offset_ms=75000)
    frames, primary = frames[:4], primary[:4]
    for row in primary:
        row["schema_version"] = "1.0.0"
    sequence = pose_sequence_from_records(frames, primary)
    proof = model_geometry_evidence(sequence, range(4))
    feature = sam_feature()
    feature.update(proof)
    feature["source_frames"] = [0, 3]
    identity = {"video_id": "fixture-video", "event_id": "fs09-fixture", "event_code": "FS09", "person_track_id": 1}
    feature.update(identity)
    if feature_mutator:
        feature_mutator(feature)
    compact = expected_compact(feature)
    if compact_mutator:
        compact_mutator(compact)
    score = {**score_indicator(indicator_id="FS09-M01", features=[copy.deepcopy(compact)],
                 calibration=None, model_versions={"pose": "sam3d-fixture"},
                 evidence=[{"event_id": identity["event_id"]}]), **identity, "indicator_id": "FS09-M01"}
    indicator = {**identity, **copy.deepcopy(proof), "indicator_id": "FS09-M01", "features": [compact],
                 "feature_status": "measured", "scoring_status": score["status"], "grade": None,
                 "reason_codes": copy.deepcopy(score["reason_codes"]), "quality_gate": copy.deepcopy(score["quality_gate"])}
    if score_mutator:
        score_mutator(score)
    event = {**identity, **copy.deepcopy(proof), "schema_version": "1.0.0", "start_ms": 0, "end_ms": 120,
             "key_phases_ms": {}, "confidence": 0.6, "boundary_uncertainty_ms": 40,
             "quality_flags": [], "provenance": {"source_id": "structural-fixture"},
             "track_diagnostics": {key: 0 for key in (
                 "track_coverage_fraction", "source_track_switch_candidate_count",
                 "confirmed_id_switch_count", "keypoint_valid_fraction",
                 "left_right_swap_candidate_frames", "keypoint_jump_candidate_frames",
                 "longest_pose_missing_frames", "longest_pose_missing_ms")}}
    streams = {"frames.jsonl": frames, "primary-player.jsonl": primary, "events.jsonl": [event],
               "features.jsonl": [feature], "indicator-features.jsonl": [indicator], "scores.jsonl": [score]}
    for name, rows in streams.items():
        (directory / name).write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    (directory / "event-feature-errors.json").write_text(json.dumps({"schema_version": "1.0.0", "errors": []}), encoding="utf-8")
    artifacts = {name: name for name in (*streams, "event-feature-errors.json") if name != "frames.jsonl"}
    summary = {"schema_version": "1.0.0", "loop_version": "minimum-scoring-loop-v0.8.0",
               "video_id": identity["video_id"], "model_versions": {"pose": "sam3d-fixture"},
               "provenance": {"frames_sha256": _sha(directory / "frames.jsonl")},
               "indicator_feature_validity": {"FS09-M01": {"valid": 1, "total": 1}},
               "score_status_counts": {score["status"]: 1}, "grade_counts": {}, "event_counts": {"FS09": 1},
               "artifacts": artifacts, "artifact_sha256": {key: _sha(directory / name) for key, name in artifacts.items()}}
    (directory / "scoring-loop-summary.json").write_text(json.dumps(summary), encoding="utf-8")


class SamFeatureBundleValidationTests(unittest.TestCase):
    def validate(self, directory):
        return _validate_scoring_artifacts(directory, frame_count=4,
            frame_timestamps={i: i * 40 for i in range(4)}, frame_processed_indexes=list(range(4)),
            frames_path=directory / "frames.jsonl")

    def test_small_native_source_bound_bundle_passes_without_scoring_or_gpu(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            _write_bundle(directory)
            result = self.validate(directory)
            self.assertEqual(result["scoring_artifacts_status"], "passed")
            self.assertEqual(result["validated_features"], 1)

    def test_structurally_valid_compact_mutation_still_breaks_exact_equality(self):
        def mutate_window(compact):
            for binding in compact["source_window"]["source_bindings"]:
                binding["pts"] += 1000
                binding["timestamp_ms"] += 1000
            compact["source_window"]["source_timestamps_ms"] = [
                value + 1000 for value in compact["source_window"]["source_timestamps_ms"]]

        mutations = {
            "value": lambda row: row.update(value=2.5),
            "selected_side": lambda row: row.update(selected_side="right"),
            "required_joints": lambda row: row.update(required_joints_used=["right_ankle"]),
            "source_window": mutate_window,
            "geometry_mask": lambda row: row["geometry_mask"]["left_ankle"].__setitem__(1, False),
            "provenance_extra": lambda row: row["measurement_provenance"].update(extra_claim="changed"),
            "missing_selected_side": lambda row: row.pop("selected_side"),
            "missing_required_joints": lambda row: row.pop("required_joints_used"),
        }
        for name, mutate in mutations.items():
            with self.subTest(mutation=name), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                _write_bundle(directory, compact_mutator=mutate)
                with self.assertRaisesRegex(OutputValidationError, "does not exactly match features.jsonl"):
                    self.validate(directory)

    def test_score_feature_proof_cannot_differ_from_indicator_compact(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            _write_bundle(directory, score_mutator=lambda score:
                score["feature"]["items"][0].update(selected_side="right"))
            with self.assertRaisesRegex(OutputValidationError, "does not exactly match indicator-features.jsonl"):
                self.validate(directory)

    def test_sam_source_context_prevents_stripping_all_proof_and_numeric_downgrade(self):
        def strip_proof(feature):
            for field in MODEL_FIELDS:
                feature.pop(field, None)
            feature["confidence"] = 0.9

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            _write_bundle(directory, feature_mutator=strip_proof)
            with self.assertRaises(OutputValidationError):
                self.validate(directory)

    def test_source_geometry_requires_complete_native_mask_not_invented_false_joints(self):
        mutations = {
            "invented_false_joint": lambda feature: feature.update(geometry_mask={"fabricated_joint": [False] * 4}),
            "missing_required_joint": lambda feature: feature["geometry_mask"].pop("left_ankle"),
            "missing_other_native_joint": lambda feature: feature["geometry_mask"].pop("right_ankle"),
        }
        for name, mutate in mutations.items():
            with self.subTest(mutation=name), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                _write_bundle(directory, feature_mutator=mutate)
                with self.assertRaises(OutputValidationError):
                    self.validate(directory)

    def test_small_real_cpu_producer_bundle_passes_complete_scoring_validation(self):
        from rallymate_scoring.loop import run_minimum_scoring_loop
        from tests.test_sam_footwork import sam_motion_fixture

        frames, primary = sam_motion_fixture(source_offset_ms=75000)
        for row in primary:
            row["schema_version"] = "1.0.0"
            row["selection_algorithm_version"] = "primary-player-v0.3.0"
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            frames_path, primary_path = directory / "frames.jsonl", directory / "primary-player.jsonl"
            for path, records in ((frames_path, frames), (primary_path, primary)):
                path.write_text("".join(json.dumps(row) + "\n" for row in records), encoding="utf-8")
            produced = run_minimum_scoring_loop(
                frames_path=frames_path, primary_timeline_path=primary_path, output_dir=directory,
                feasibility_registry_path=Path(__file__).resolve().parents[1] / "metric-feasibility-pose-wave-v2.json",
                source_id="small-sam-validation-fixture", video_id="fixture-video",
                source_provenance={"frames_sha256": _sha(frames_path), "video_sha256": "a" * 64},
            )
            self.assertTrue(produced["events"])
            result = _validate_scoring_artifacts(directory, frame_count=len(frames),
                frame_timestamps={row["frame"]["index"]: row["frame"]["timestamp_ms"] for row in frames},
                frame_processed_indexes=[row["frame"]["processed_index"] for row in frames], frames_path=frames_path)
            self.assertEqual(result["scoring_artifacts_status"], "passed")
            self.assertGreater(result["validated_features"], 0)


if __name__ == "__main__":
    unittest.main()
