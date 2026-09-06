from __future__ import annotations

import hashlib
import json
import subprocess
import shutil
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from rallymate_scoring.feasibility import load_feasibility_registry
from rallymate_scoring.loop import run_minimum_scoring_loop
from rallymate_scoring.scoring_context import build_scoring_reference_worklist
from rallymate_vision.validation import (
    OutputValidationError,
    validate_frame_observation,
    validate_run_artifacts,
)
from rallymate_vision.pose.metadata import keypoint_schema


def valid_record() -> dict:
    keypoints = []
    for definition in keypoint_schema("coco17")["keypoints"]:
        keypoints.append({
            "index": definition["index"],
            "name": definition["name"],
            "x_px": 50.0,
            "y_px": 25.0,
            "x_normalized": 0.5,
            "y_normalized": 0.5,
            "confidence": 0.9,
            "downstream_joint_id": definition["downstream_joint_id"],
        })
    return {
        "schema_version": "1.0.0",
        "frame": {
            "timestamp_ms": 40,
            "width": 100,
            "height": 50,
        },
        "detections": [
            {
                "class_name": "player",
                "track_id": 1,
                "bbox_px": [10.0, 5.0, 90.0, 50.0],
                "bbox_normalized": [0.1, 0.1, 0.9, 1.0],
                "center_px": [50.0, 27.5],
                "center_normalized": [0.5, 0.55],
            }
        ],
        "poses": [
            {
                "person_track_id": 1,
                "keypoint_format": "coco17",
                "keypoints": keypoints,
            }
        ],
        "court": {
            "status": "uncertain",
            "calibration_usable": False,
            "polygon_normalized": [],
        },
    }


def _read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _quality_gate(
    indicator_id: str,
    *,
    hard_fail: bool,
    measurement_allowed: bool | None = None,
    scoring_block_flags: list[str] | None = None,
) -> dict:
    if measurement_allowed is None:
        measurement_allowed = not hard_fail
    scoring_block_flags = list(scoring_block_flags or [])
    return {
        "schema_version": "1.0.0",
        "policy_version": "test-quality-policy-v1",
        "indicator_id": indicator_id,
        "status": "hard_fail" if hard_fail else "pass",
        "hard_fail": hard_fail,
        "measurement_allowed": measurement_allowed,
        "scoring_allowed": measurement_allowed and not hard_fail and not scoring_block_flags,
        "input_quality_flags": (
            (["primary_track_coverage_low"] if hard_fail else [])
            + scoring_block_flags
        ),
        "hard_fail_flags": ["primary_track_coverage_low"] if hard_fail else [],
        "scoring_block_flags": scoring_block_flags,
        "advisory_flags": scoring_block_flags,
        "non_blocking_flags": [],
        "unclassified_flags": [],
        "semantics": "quality_evidence_gate_only_no_A_to_E_thresholds",
    }


def _make_first_indicator_features_valid(
    destination: Path,
    *,
    hard_fail: bool,
    measurement_allowed: bool | None = None,
) -> tuple[list[dict], list[dict]]:
    indicators_path = destination / "indicator-features.jsonl"
    scores_path = destination / "scores.jsonl"
    features_path = destination / "features.jsonl"
    indicators = _read_jsonl(indicators_path)
    scores = _read_jsonl(scores_path)
    features = _read_jsonl(features_path)
    indicator = indicators[0]
    score = scores[0]
    event_id = indicator["event_id"]
    required_names = {feature["feature_name"] for feature in indicator["features"]}
    for feature in indicator["features"]:
        feature["valid"] = True
        feature["reason"] = "ok"
    for feature in features:
        if feature["event_id"] == event_id and feature["feature_name"] in required_names:
            feature["valid"] = True
            feature["reason"] = "ok"
    changed_by_key = {
        (feature["event_id"], feature["feature_name"]): feature
        for feature in features
        if feature["event_id"] == event_id
        and feature["feature_name"] in required_names
    }
    for current_indicator in indicators:
        for compact in current_indicator["features"]:
            source = changed_by_key.get(
                (current_indicator["event_id"], compact["feature_name"])
            )
            if source is not None:
                compact["valid"] = source["valid"]
                compact["reason"] = source["reason"]
    indicator_by_key = {
        (current["event_id"], current["indicator_id"]): current
        for current in indicators
    }
    for current_score in scores:
        current_indicator = indicator_by_key[
            (current_score["event_id"], current_score["indicator_id"])
        ]
        current_score["feature"] = {
            "items": json.loads(json.dumps(current_indicator["features"]))
        }
    gate = _quality_gate(
        indicator["indicator_id"],
        hard_fail=hard_fail,
        measurement_allowed=measurement_allowed,
    )
    indicator["quality_gate"] = gate
    score["quality_gate"] = json.loads(json.dumps(gate))
    score["feature"] = {"items": json.loads(json.dumps(indicator["features"]))}
    if hard_fail or not gate["measurement_allowed"]:
        indicator["feature_status"] = "unavailable"
        indicator["scoring_status"] = "unavailable"
        indicator["reason_codes"] = (
            ["event_quality_hard_fail", "primary_track_coverage_low"]
            if hard_fail
            else ["event_quality_measurement_blocked"]
        )
        score["status"] = "unavailable"
        score["reason_codes"] = list(indicator["reason_codes"])
    else:
        indicator["feature_status"] = "measured"
        indicator["scoring_status"] = "calibration_required"
        indicator["reason_codes"] = [
            "coach_calibration_missing",
            "independent_test_missing",
        ]
        score["status"] = "calibration_required"
        score["reason_codes"] = list(indicator["reason_codes"])
        summary_path = destination / "scoring-loop-summary.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        summary["score_status_counts"]["unavailable"] -= 1
        summary["score_status_counts"]["calibration_required"] += 1
        summary["indicator_feature_validity"][indicator["indicator_id"]] = {
            "valid": 2,
            "total": 2,
            "valid_rate": 1.0,
        }
        summary_path.write_text(json.dumps(summary), encoding="utf-8")
    indicator["grade"] = None
    score["grade"] = None
    _write_jsonl(indicators_path, indicators)
    _write_jsonl(scores_path, scores)
    _write_jsonl(features_path, features)
    # The current bundle contract binds every derived scoring artifact.  Test
    # mutations must refresh the copied summary so positive validator cases do
    # not depend on another test's execution order or on a legacy bundle that
    # predates artifact hashes.
    summary_path = destination / "scoring-loop-summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["artifact_sha256"] = {
        artifact_name: _sha256(destination / filename)
        for artifact_name, filename in summary["artifacts"].items()
    }
    summary_path.write_text(json.dumps(summary), encoding="utf-8")
    return indicators, scores


class OutputValidationTests(unittest.TestCase):
    def test_validation_module_imports_in_fresh_interpreter(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "from rallymate_vision.validation import validate_run_artifacts",
            ],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_valid_pose_player_link(self) -> None:
        validate_frame_observation(valid_record())

    def test_valid_halpe26_keeps_unregistered_extra_joints_null(self) -> None:
        record = valid_record()
        record["schema_version"] = "1.1.0"
        record["poses"][0]["keypoint_format"] = "halpe26"
        points = []
        for definition in keypoint_schema("halpe26")["keypoints"]:
            points.append(
                {
                    "index": definition["index"],
                    "name": definition["name"],
                    "x_px": 50.0,
                    "y_px": 25.0,
                    "x_normalized": 0.5,
                    "y_normalized": 0.5,
                    "confidence": 0.9,
                    "downstream_joint_id": definition["downstream_joint_id"],
                }
            )
        record["poses"][0]["keypoints"] = points
        validate_frame_observation(record)

    def test_valid_coco_wholebody133_preserves_first_17_downstream_mappings(self) -> None:
        record = valid_record()
        record["poses"][0]["keypoint_format"] = "coco_wholebody133"
        record["poses"][0]["keypoints"] = []
        for definition in keypoint_schema("coco_wholebody133")["keypoints"]:
            record["poses"][0]["keypoints"].append(
                {
                    "index": definition["index"],
                    "name": definition["name"],
                    "downstream_joint_id": definition["downstream_joint_id"],
                    "x_px": 20.0,
                    "y_px": 20.0,
                    "x_normalized": 0.2,
                    "y_normalized": 0.2,
                    "confidence": 0.8,
                }
            )
        validate_frame_observation(record)
        self.assertTrue(
            all(
                point["downstream_joint_id"] is None
                for point in record["poses"][0]["keypoints"][17:]
            )
        )

    def test_frame_observation_contract_capacity_includes_wholebody133(self) -> None:
        root = Path(__file__).resolve().parents[1]
        schema = json.loads(
            (root / "contracts" / "frame-observation.schema.json").read_text(
                encoding="utf-8"
            )
        )
        pose_definition = schema["properties"]["poses"]["items"]
        self.assertIn(
            "coco_wholebody133",
            pose_definition["properties"]["keypoint_format"]["enum"],
        )
        self.assertGreaterEqual(
            pose_definition["properties"]["keypoints"]["maxItems"],
            133,
        )
        self.assertGreaterEqual(
            pose_definition["properties"]["keypoints"]["items"]["properties"]
            ["index"]["maximum"],
            132,
        )

    def test_orphan_pose_is_rejected(self) -> None:
        record = valid_record()
        record["poses"][0]["person_track_id"] = 999
        with self.assertRaises(OutputValidationError):
            validate_frame_observation(record)

    def test_out_of_range_court_point_is_rejected(self) -> None:
        record = valid_record()
        record["court"]["polygon_normalized"] = [[1.2, 0.5]]
        with self.assertRaises(OutputValidationError):
            validate_frame_observation(record)

    def test_real_rtmpose_scoring_bundle_is_cross_artifact_valid(self) -> None:
        root = Path(__file__).resolve().parents[1]
        run = root / "runs" / "rtmpose-m-halpe26-online-smoke"
        result = validate_run_artifacts(run)
        registry = load_feasibility_registry(
            root / "metric-feasibility-pose-wave-v2.json"
        )
        events = _read_jsonl(run / "events.jsonl")
        features = _read_jsonl(run / "features.jsonl")
        indicators = _read_jsonl(run / "indicator-features.jsonl")
        scores = _read_jsonl(run / "scores.jsonl")
        event_counts = Counter(item["event_code"] for item in events)
        registry_ids = {
            item["indicator_id"] for item in registry["indicators"]
        }
        expected_indicator_records = sum(
            event_counts[indicator["indicator_id"].split("-", 1)[0]]
            for indicator in registry["indicators"]
        )
        self.assertEqual(result["scoring_artifacts_status"], "passed")
        self.assertEqual(result["validated_events"], len(events))
        self.assertEqual(result["validated_features"], len(features))
        self.assertEqual(
            result["validated_indicator_records"], expected_indicator_records
        )
        self.assertEqual(result["validated_scores"], expected_indicator_records)
        self.assertEqual(
            {item["indicator_id"] for item in indicators}, registry_ids
        )
        self.assertEqual({item["indicator_id"] for item in scores}, registry_ids)

    def test_current_context_evidence_video_identity_is_cross_artifact_bound(self) -> None:
        root = Path(__file__).resolve().parents[1]
        source = root / "runs" / "rtmpose-m-halpe26-online-smoke"
        source_scoring_summary = json.loads(
            (source / "scoring-loop-summary.json").read_text(encoding="utf-8")
        )
        source_versions = source_scoring_summary["model_versions"]
        pose_model = {
            "backend": source_versions["pose_backend"],
            "runtime": source_versions["pose_runtime"],
            "profile": source_versions["pose_profile"],
            "model_sha256": source_versions["pose_model_sha256"],
            "native_keypoint_format": source_versions["native_keypoint_format"],
            "native_keypoint_count": source_versions["native_keypoint_count"],
            "keypoint_schema_version": source_versions["keypoint_schema_version"],
        }
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "run"
            shutil.copytree(source, destination)
            frames_path = destination / "frames.jsonl"
            timeline_path = destination / "primary-player.jsonl"
            provenance = {
                "video_sha256": source_scoring_summary["provenance"]["video_sha256"],
                "frames_sha256": _sha256(frames_path),
            }
            run_kwargs = {
                "frames_path": frames_path,
                "primary_timeline_path": timeline_path,
                "output_dir": destination,
                "feasibility_registry_path": root
                / "metric-feasibility-pose-wave-v2.json",
                "source_id": "context-evidence-validation",
                "video_id": source_scoring_summary["video_id"],
                "pose_model": pose_model,
                "source_provenance": provenance,
            }
            first = run_minimum_scoring_loop(**run_kwargs)
            self.assertTrue(
                any(event["event_code"] == "FS02" for event in first["events"])
            )
            context = build_scoring_reference_worklist(
                events=first["events"],
                video_id=source_scoring_summary["video_id"],
                video_sha256=provenance["video_sha256"],
                created_at="2026-08-30T00:00:00+00:00",
            )
            context["source"]["created_by"] = "coach-context-1"
            for observation in context["observations"]:
                observation.update(
                    {
                        "status": "accepted",
                        "target_direction_deg": 0.0,
                        "coordinate_frame": "image_plane",
                        "confidence": 0.9,
                        "observer_id": "coach-context-1",
                        "reviewer_id": "reviewer-context-2",
                        "reason": None,
                    }
                )
            context_path = destination / "scoring-reference-context.json"
            context_path.write_text(json.dumps(context), encoding="utf-8")
            second = run_minimum_scoring_loop(
                **run_kwargs,
                scoring_reference_context_path=context_path,
            )
            self.assertEqual(second["summary"]["loop_version"], "minimum-scoring-loop-v0.7.0")
            self.assertEqual(
                validate_run_artifacts(destination)["scoring_artifacts_status"],
                "passed",
            )

            scores_path = destination / "scores.jsonl"
            summary_path = destination / "scoring-loop-summary.json"
            baseline_scores = _read_jsonl(scores_path)
            baseline_summary = json.loads(summary_path.read_text(encoding="utf-8"))
            target_score_index = next(
                index
                for index, score in enumerate(baseline_scores)
                if score["indicator_id"] == "FS02-M02"
                and score.get("scoring_context", {}).get("status") == "available"
            )

            for mutation in (
                "missing_video_id",
                "wrong_video_id",
                "missing_video_sha256",
                "wrong_video_sha256",
                "duplicate",
            ):
                scores = json.loads(json.dumps(baseline_scores))
                target_score = scores[target_score_index]
                context_evidence = next(
                    evidence
                    for evidence in target_score["evidence"]
                    if evidence.get("evidence_type") == "scoring_reference_context"
                )
                if mutation == "missing_video_id":
                    context_evidence.pop("video_id")
                    expected_error = "scoring context evidence mismatch"
                elif mutation == "wrong_video_id":
                    context_evidence["video_id"] = "wrong-video"
                    expected_error = "scoring context evidence mismatch"
                elif mutation == "missing_video_sha256":
                    context_evidence.pop("video_sha256")
                    expected_error = "scoring context evidence mismatch"
                elif mutation == "wrong_video_sha256":
                    context_evidence["video_sha256"] = "0" * 64
                    expected_error = "scoring context evidence mismatch"
                else:
                    target_score["evidence"].append(dict(context_evidence))
                    expected_error = "exactly one scoring context evidence item"
                _write_jsonl(scores_path, scores)
                summary = json.loads(json.dumps(baseline_summary))
                summary["artifact_sha256"]["scores_jsonl"] = _sha256(scores_path)
                summary_path.write_text(json.dumps(summary), encoding="utf-8")
                with self.subTest(mutation=mutation), self.assertRaisesRegex(
                    OutputValidationError,
                    expected_error,
                ):
                    validate_run_artifacts(destination)

    def test_target_direction_block_cannot_be_removed_with_pending_context(self) -> None:
        root = Path(__file__).resolve().parents[1]
        source = (
            root
            / "reports"
            / "fs09-pose-wave-v2-context-m41"
            / "850cb0006b406c7176eeda8d711cd065-m40-120f"
        )
        self.assertEqual(
            validate_run_artifacts(source)["scoring_artifacts_status"], "passed"
        )
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "run"
            shutil.copytree(source, destination)
            indicators = _read_jsonl(destination / "indicator-features.jsonl")
            scores = _read_jsonl(destination / "scores.jsonl")
            target_indicator = next(
                item for item in indicators if item["indicator_id"] == "FS02-M02"
            )
            key = (target_indicator["event_id"], target_indicator["indicator_id"])
            target_score = next(
                item
                for item in scores
                if (item["event_id"], item["indicator_id"]) == key
            )
            missing_flag = "tactical_target_direction_not_observed"
            for record in (target_indicator, target_score):
                gate = record["quality_gate"]
                gate["input_quality_flags"] = [
                    flag for flag in gate["input_quality_flags"] if flag != missing_flag
                ]
                gate["scoring_block_flags"] = [
                    flag for flag in gate["scoring_block_flags"] if flag != missing_flag
                ]
                gate["advisory_flags"] = [
                    flag for flag in gate["advisory_flags"] if flag != missing_flag
                ]
            _write_jsonl(destination / "indicator-features.jsonl", indicators)
            _write_jsonl(destination / "scores.jsonl", scores)
            with self.assertRaisesRegex(
                OutputValidationError,
                "removed target-direction block without valid context",
            ):
                validate_run_artifacts(destination)

    def test_scoring_bundle_accepts_all_valid_features_with_hard_quality_fail(self) -> None:
        root = Path(__file__).resolve().parents[1]
        source = root / "runs" / "rtmpose-m-halpe26-online-smoke"
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "run"
            shutil.copytree(source, destination)
            _make_first_indicator_features_valid(destination, hard_fail=True)
            result = validate_run_artifacts(destination)
            self.assertEqual(result["scoring_artifacts_status"], "passed")

    def test_scoring_bundle_accepts_explicit_measurement_block_without_hard_fail(self) -> None:
        root = Path(__file__).resolve().parents[1]
        source = root / "runs" / "rtmpose-m-halpe26-online-smoke"
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "run"
            shutil.copytree(source, destination)
            _make_first_indicator_features_valid(
                destination,
                hard_fail=False,
                measurement_allowed=False,
            )
            result = validate_run_artifacts(destination)
            self.assertEqual(result["scoring_artifacts_status"], "passed")

    def test_scoring_bundle_without_hard_fail_uses_required_feature_validity(self) -> None:
        root = Path(__file__).resolve().parents[1]
        source = root / "runs" / "rtmpose-m-halpe26-online-smoke"
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "run"
            shutil.copytree(source, destination)
            _make_first_indicator_features_valid(destination, hard_fail=False)
            result = validate_run_artifacts(destination)
            self.assertEqual(result["scoring_artifacts_status"], "passed")

            indicators_path = destination / "indicator-features.jsonl"
            indicators = _read_jsonl(indicators_path)
            indicators[0]["feature_status"] = "unavailable"
            _write_jsonl(indicators_path, indicators)
            with self.assertRaisesRegex(
                OutputValidationError,
                "feature_status disagrees with feature validity and quality gate",
            ):
                validate_run_artifacts(destination)

    def test_scoring_bundle_rejects_non_unavailable_status_on_hard_quality_fail(self) -> None:
        root = Path(__file__).resolve().parents[1]
        source = root / "runs" / "rtmpose-m-halpe26-online-smoke"
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "run"
            shutil.copytree(source, destination)
            _make_first_indicator_features_valid(destination, hard_fail=True)
            indicators_path = destination / "indicator-features.jsonl"
            indicators = _read_jsonl(indicators_path)
            indicators[0]["scoring_status"] = "calibration_required"
            _write_jsonl(indicators_path, indicators)
            with self.assertRaisesRegex(
                OutputValidationError,
                "scoring_status must be unavailable when quality gate blocks scoring",
            ):
                validate_run_artifacts(destination)

    def test_v04_bundle_cannot_hide_concurrent_score_block_reasons(self) -> None:
        root = Path(__file__).resolve().parents[1]
        source = root / "runs" / "rtmpose-m-halpe26-online-smoke"
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "run"
            shutil.copytree(source, destination)
            indicators, scores = _make_first_indicator_features_valid(
                destination,
                hard_fail=True,
            )
            block = "source_track_switch_candidates_present"
            for record in (indicators[0], scores[0]):
                record["quality_gate"]["input_quality_flags"].append(block)
                record["quality_gate"]["scoring_block_flags"] = [block]
                record["quality_gate"]["advisory_flags"] = [block]
                # This intentionally models the pre-v0.4 loss of a concurrent
                # block: both derived streams agree, but neither retains it in
                # reason_codes.
                record["reason_codes"] = [
                    "event_quality_hard_fail",
                    "primary_track_coverage_low",
                ]
            _write_jsonl(destination / "indicator-features.jsonl", indicators)
            _write_jsonl(destination / "scores.jsonl", scores)
            summary_path = destination / "scoring-loop-summary.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            summary["loop_version"] = "minimum-scoring-loop-v0.4.0"
            summary["artifact_sha256"] = {
                name: _sha256(destination / filename)
                for name, filename in summary["artifacts"].items()
            }
            summary_path.write_text(json.dumps(summary), encoding="utf-8")
            with self.assertRaisesRegex(
                OutputValidationError,
                "reason_codes omit active score-block evidence",
            ):
                validate_run_artifacts(destination)

    def test_scoring_bundle_rejects_score_indicator_status_mismatch(self) -> None:
        root = Path(__file__).resolve().parents[1]
        source = root / "runs" / "rtmpose-m-halpe26-online-smoke"
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "run"
            shutil.copytree(source, destination)
            summary_path = destination / "scoring-loop-summary.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            summary["provenance"]["video_path"] = None
            summary["provenance"]["video_sha256"] = None
            summary_path.write_text(json.dumps(summary), encoding="utf-8")
            scores_path = destination / "scores.jsonl"
            scores = scores_path.read_text(encoding="utf-8").splitlines()
            first = json.loads(scores[0])
            first["status"] = "calibration_required"
            scores[0] = json.dumps(first)
            scores_path.write_text("\n".join(scores) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(OutputValidationError, "status mismatch"):
                validate_run_artifacts(destination)

    def test_scoring_bundle_rejects_feature_value_cross_layer_mismatch(self) -> None:
        root = Path(__file__).resolve().parents[1]
        source = root / "runs" / "rtmpose-m-halpe26-online-smoke"
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "run"
            shutil.copytree(source, destination)
            features_path = destination / "features.jsonl"
            features = _read_jsonl(features_path)
            features[0]["value"] = 999999999.0
            _write_jsonl(features_path, features)
            with self.assertRaisesRegex(
                OutputValidationError, "does not exactly match features.jsonl"
            ):
                validate_run_artifacts(destination)

    def test_current_bundle_hashes_reject_synchronised_artifact_tamper(self) -> None:
        root = Path(__file__).resolve().parents[1]
        source = root / "runs" / "rtmpose-m-halpe26-online-smoke"
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "run"
            shutil.copytree(source, destination)
            summary_path = destination / "scoring-loop-summary.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            summary["loop_version"] = "minimum-scoring-loop-v0.3.0"
            summary["artifact_sha256"] = {
                name: _sha256(destination / filename)
                for name, filename in summary["artifacts"].items()
            }
            summary_path.write_text(json.dumps(summary), encoding="utf-8")
            validate_run_artifacts(destination)

            features_path = destination / "features.jsonl"
            indicators_path = destination / "indicator-features.jsonl"
            scores_path = destination / "scores.jsonl"
            features = _read_jsonl(features_path)
            indicators = _read_jsonl(indicators_path)
            scores = _read_jsonl(scores_path)
            target = features[0]
            target["value"] = 999999999.0
            for indicator in indicators:
                if indicator["event_id"] != target["event_id"]:
                    continue
                for feature in indicator["features"]:
                    if feature["feature_name"] == target["feature_name"]:
                        feature["value"] = target["value"]
            indicator_by_key = {
                (item["event_id"], item["indicator_id"]): item
                for item in indicators
            }
            for score in scores:
                indicator = indicator_by_key[
                    (score["event_id"], score["indicator_id"])
                ]
                score["feature"] = {
                    "items": json.loads(json.dumps(indicator["features"]))
                }
            _write_jsonl(features_path, features)
            _write_jsonl(indicators_path, indicators)
            _write_jsonl(scores_path, scores)
            with self.assertRaisesRegex(OutputValidationError, "SHA256 mismatch"):
                validate_run_artifacts(destination)

    def test_no_event_unavailable_scoring_bundle_is_valid(self) -> None:
        root = Path(__file__).resolve().parents[1]
        source = root / "runs" / "rtmpose-m-halpe26-online-smoke"
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "run"
            shutil.copytree(source, destination)
            for filename in (
                "events.jsonl",
                "features.jsonl",
                "indicator-features.jsonl",
                "scores.jsonl",
            ):
                (destination / filename).write_text("", encoding="utf-8")
            summary_path = destination / "scoring-loop-summary.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            summary["loop_version"] = "minimum-scoring-loop-v0.3.0"
            summary["status"] = "unavailable_no_pose_motion_event_candidate"
            summary["event_counts"] = {}
            summary["score_status_counts"] = {}
            summary["grade_counts"] = {}
            summary["result_state"].update(
                {
                    "measurement_status": "unavailable",
                    "scoring_status": "unavailable",
                    "grade": None,
                }
            )
            summary["artifact_sha256"] = {
                name: _sha256(destination / filename)
                for name, filename in summary["artifacts"].items()
            }
            summary_path.write_text(json.dumps(summary), encoding="utf-8")
            result = validate_run_artifacts(destination)
            self.assertEqual(result["scoring_artifacts_status"], "passed")
            self.assertEqual(result["validated_events"], 0)
            self.assertEqual(result["validated_scores"], 0)


if __name__ == "__main__":
    unittest.main()
