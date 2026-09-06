from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from rallymate_evaluation.pose_same_frame_diagnostic import (
    PoseSameFrameDiagnosticError,
    canonical_sha256,
    compare_model_observations,
    load_diagnostic_registry,
    select_frozen_same_frame_samples,
    summarize_model_observations,
    summarize_repeatability,
    validate_diagnostic_report,
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _pose(*, x_offset: float = 0.0, confidence: float = 0.6) -> dict:
    return {
        "person_track_id": 1,
        "confidence": confidence,
        "roi_bbox_px": [0, 0, 100, 100],
        "coordinate_space": "original_frame",
        "keypoint_format": "halpe26",
        "keypoints": [
            {
                "index": index,
                "name": f"joint-{index}",
                "downstream_joint_id": None,
                "x_px": 10.0 + index + x_offset,
                "y_px": 20.0 + index,
                "x_normalized": 0.1,
                "y_normalized": 0.2,
                "confidence": confidence,
            }
            for index in range(26)
        ],
    }


def _observations(family: str, offsets: tuple[float, float] = (0.0, 0.0)) -> list[dict]:
    del family
    return [
        {
            "sample_id": f"dev:{index}:1",
            "video_id": "dev",
            "window_id": "dev-w01",
            "window_position": index,
            "processed_index": index,
            "source_frame_index": index,
            "timestamp_ms": index * 40,
            "track_id": 1,
            "latency_ms": 10.0 + index,
            "pose": _pose(x_offset=offsets[index]),
        }
        for index in range(2)
    ]


class PoseSameFrameDiagnosticTests(unittest.TestCase):
    def _workspace(self, root: Path) -> Path:
        deployment = root / "models" / "rtmpose" / "deployment-presets.json"
        _write_json(deployment, {"default_preset": "rtmpose-m-halpe26-online"})
        source_a = root / "models" / "rtmpose" / "model-candidates.json"
        source_b = root / "models" / "rtmpose" / "m95-shadow-candidates.json"
        _write_json(source_a, {"source": "a"})
        _write_json(source_b, {"source": "b"})

        videos = []
        for index in range(3):
            video_id = f"dev-{index}"
            video = root / "FULL-TEST" / f"{video_id}.mp4"
            video.parent.mkdir(parents=True, exist_ok=True)
            video.write_bytes(f"video-{index}".encode())
            frames = root / "runs" / video_id / "frames.jsonl"
            frames.parent.mkdir(parents=True, exist_ok=True)
            frames.write_text("{}\n", encoding="utf-8")
            videos.append(
                {
                    "video_id": video_id,
                    "video_relative_path": f"FULL-TEST/{video_id}.mp4",
                    "video_sha256": _sha(video),
                    "frozen_frames_relative_path": f"runs/{video_id}/frames.jsonl",
                    "frozen_frames_sha256": _sha(frames),
                    "frame_count": 1,
                }
            )

        models = []
        for family, size in zip(("M", "L", "X"), ([256, 192], [384, 288], [384, 288])):
            checkpoint = root / "models" / "rtmpose" / f"{family}.pth"
            checkpoint.write_bytes(f"checkpoint-{family}".encode())
            config = root / "runtime" / f"{family}.py"
            config.parent.mkdir(parents=True, exist_ok=True)
            config.write_text(f"model = '{family}'\n", encoding="utf-8")
            models.append(
                {
                    "family": family,
                    "candidate_id": f"candidate-{family}",
                    "source_role": "test",
                    "model_relative_path": f"models/rtmpose/{family}.pth",
                    "checkpoint_sha256": _sha(checkpoint),
                    "checkpoint_bytes": checkpoint.stat().st_size,
                    "config_runtime_relative_path": f"runtime/{family}.py",
                    "config_sha256": _sha(config),
                    "input_size_hw": size,
                    "native_keypoint_format": "halpe26",
                }
            )

        registry = {
            "schema_version": "1.0.0",
            "protocol_version": "rtmpose-same-frame-diagnostic-2026-09-04.1",
            "production_default_binding": {
                "preset_id": "rtmpose-m-halpe26-online",
                "registry_relative_path": "models/rtmpose/deployment-presets.json",
                "registry_sha256": _sha(deployment),
            },
            "source_registry_bindings": [
                {
                    "role": "M_and_L_candidate_provenance",
                    "relative_path": "models/rtmpose/model-candidates.json",
                    "sha256": _sha(source_a),
                },
                {
                    "role": "X_candidate_and_development_protocol_provenance",
                    "relative_path": "models/rtmpose/m95-shadow-candidates.json",
                    "sha256": _sha(source_b),
                },
            ],
            "development_protocol": {
                "phase": "development_same_frame_diagnostic_only",
                "videos": videos,
                "sealed_holdout": {
                    "video_id": "holdout",
                    "video_relative_path": "FULL-TEST/holdout.mp4",
                    "video_sha256": "A" * 64,
                    "use_during_development": False,
                },
                "sampling": {
                    "windows_per_video": 3,
                    "frames_per_window": 5,
                    "max_players": 1,
                },
                "execution": {
                    "profile": "realtime",
                    "flip_test": False,
                    "roi_margin": 0.15,
                    "min_roi_size_px": 32,
                    "warmup_calls_per_model": 2,
                    "same_input_repeat_calls_per_video_and_model": 3,
                },
                "metrics": {
                    "confidence_thresholds": [0.3, 0.5, 0.7],
                    "cross_model_min_confidence": 0.3,
                    "normalized_distance_thresholds": [0.03, 0.05, 0.1],
                    "distance_normalizer": "pose_estimator_expanded_ROI_diagonal",
                    "missing_pose_counts_against_coverage_denominator": True,
                },
                "ground_truth_used": False,
                "accuracy_claim": False,
            },
            "models": models,
            "safety": {
                "changes_deployment_registry": False,
                "changes_production_default": False,
                "opens_sealed_holdout": False,
                "uses_ground_truth": False,
                "claims_RallyMate_accuracy": False,
                "promotes_candidate": False,
                "promotes_F3_or_F4": False,
                "generates_grade_or_threshold": False,
                "uses_confidence_as_accuracy": False,
                "uses_model_agreement_as_accuracy": False,
            },
        }
        _write_json(root / "models" / "rtmpose" / "m96-same-frame-diagnostic.json", registry)
        return root

    def test_registry_binds_three_models_three_development_videos_and_does_not_open_holdout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            verified = load_diagnostic_registry(self._workspace(Path(directory)))
            self.assertEqual(["M", "L", "X"], [item["family"] for item in verified["models"]])
            self.assertEqual(3, len(verified["videos"]))
            self.assertFalse(verified["holdout_identity"]["file_opened"])

    def test_registry_rejects_development_alias_of_holdout_by_hash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._workspace(Path(directory))
            path = root / "models" / "rtmpose" / "m96-same-frame-diagnostic.json"
            registry = json.loads(path.read_text(encoding="utf-8"))
            registry["development_protocol"]["sealed_holdout"]["video_sha256"] = (
                registry["development_protocol"]["videos"][0]["video_sha256"]
            )
            _write_json(path, registry)
            with self.assertRaisesRegex(PoseSameFrameDiagnosticError, "alias the sealed holdout"):
                load_diagnostic_registry(root)

    def test_registry_rejects_default_registry_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._workspace(Path(directory))
            deployment = root / "models" / "rtmpose" / "deployment-presets.json"
            _write_json(deployment, {"default_preset": "changed"})
            with self.assertRaisesRegex(PoseSameFrameDiagnosticError, "SHA-256 drifted"):
                load_diagnostic_registry(root)

    def test_same_frame_sampling_is_deterministic_consecutive_and_largest_track(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "frames.jsonl"
            rows = []
            for index in range(20):
                rows.append(
                    {
                        "frame": {
                            "index": index,
                            "processed_index": index,
                            "timestamp_ms": index * 40,
                            "width": 200,
                            "height": 100,
                        },
                        "detections": [
                            {
                                "class_name": "player",
                                "track_id": 9,
                                "confidence": 0.9,
                                "bbox_px": [0, 0, 5, 5],
                            },
                            {
                                "class_name": "player",
                                "track_id": 1,
                                "confidence": 0.8,
                                "bbox_px": [0, 0, 20, 20],
                            },
                        ],
                    }
                )
            path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
            result = select_frozen_same_frame_samples(
                path,
                video_id="dev",
                declared_frame_count=20,
                windows_per_video=3,
                frames_per_window=5,
            )
            self.assertEqual(15, len(result["samples"]))
            self.assertTrue(all(item["player"]["track_id"] == 1 for item in result["samples"]))
            self.assertEqual([0, 1, 2, 3, 4], [
                item["source_frame_index"] for item in result["samples"][:5]
            ])
            self.assertEqual([15, 16, 17, 18, 19], [
                item["source_frame_index"] for item in result["samples"][-5:]
            ])

    def test_coverage_denominator_includes_missing_pose(self) -> None:
        observations = _observations("M")
        observations[1]["pose"] = None
        summary = summarize_model_observations(
            observations,
            confidence_thresholds=[0.3, 0.5, 0.7],
            temporal_min_confidence=0.3,
        )
        coverage = summary["coverage_and_confidence"]
        self.assertEqual(52, coverage["expected_keypoint_count"])
        self.assertEqual(26, coverage["returned_keypoint_count"])
        self.assertEqual(0.5, coverage["confidence_threshold_coverage"]["0.5"]["rate_including_missing_poses"])
        self.assertEqual(0.5, coverage["pose_return_rate"])

    def test_temporal_metric_is_named_as_observed_motion_not_jitter(self) -> None:
        summary = summarize_model_observations(
            _observations("M", (0.0, 1.0)),
            confidence_thresholds=[0.3, 0.5, 0.7],
            temporal_min_confidence=0.3,
        )
        temporal = summary["temporal_continuity"]
        self.assertEqual(26, temporal["confidence_eligible_joint_pair_count"])
        self.assertGreater(temporal["normalized_coordinate_displacement"]["p50"], 0)
        self.assertIn("real subject motion", temporal["semantics"])

    def test_repeatability_and_cross_model_agreement_are_descriptive(self) -> None:
        repeat = summarize_repeatability(
            [{"sample_id": "dev:0:1", "video_id": "dev", "poses": [_pose(), _pose(), _pose()]}]
        )
        self.assertEqual(1.0, repeat["pose_presence_consistent_anchor_rate"])
        self.assertEqual(0.0, repeat["normalized_coordinate_delta_from_first_call"]["p50"])
        comparison = compare_model_observations(
            {
                "M": _observations("M", (0.0, 0.0)),
                "L": _observations("L", (1.0, 1.0)),
                "X": _observations("X", (2.0, 2.0)),
            },
            min_confidence=0.3,
            distance_thresholds=[0.03, 0.05, 0.1],
        )
        self.assertEqual(3, len(comparison["pairwise"]))
        self.assertEqual(52, comparison["three_model_consensus"]["all_three_confidence_eligible_joint_count"])
        self.assertIn("not ground-truth accuracy", comparison["semantics"])

    def test_report_validator_rejects_accuracy_claim_and_sample_hash_drift(self) -> None:
        samples = [{"sample_id": "dev:0:1"}, {"sample_id": "dev:1:1"}]
        model_values = {}
        for family in ("M", "L", "X"):
            model_values[family] = {
                "observations": _observations(family),
                "summary": {},
                "repeatability": {},
            }
        report = {
            "schema_version": "1.0.0",
            "report_version": "rtmpose-same-frame-diagnostic-v1.0.0",
            "status": "diagnostic_complete_ground_truth_required",
            "production_default": {"unchanged": True},
            "protocol": {
                "sealed_holdout_opened": False,
                "ground_truth_used": False,
                "accuracy_claim": False,
                "changes_production_default": False,
            },
            "sample_manifest": samples,
            "sample_manifest_sha256": canonical_sha256(samples),
            "models": model_values,
            "cross_model": {},
            "claims": {
                "ground_truth_used": False,
                "RallyMate_accuracy_measured": False,
                "RallyMate_accuracy_improved": False,
                "confidence_is_accuracy": False,
                "model_agreement_is_accuracy": False,
                "candidate_promoted": False,
                "production_default_changed": False,
                "F3_or_F4_promoted": False,
                "grade_or_threshold_generated": False,
            },
        }
        validate_diagnostic_report(report)
        report["claims"]["RallyMate_accuracy_improved"] = True
        with self.assertRaisesRegex(PoseSameFrameDiagnosticError, "false fields"):
            validate_diagnostic_report(report)
        report["claims"]["RallyMate_accuracy_improved"] = False
        report["sample_manifest_sha256"] = "0" * 64
        with self.assertRaisesRegex(PoseSameFrameDiagnosticError, "sample manifest SHA"):
            validate_diagnostic_report(report)


if __name__ == "__main__":
    unittest.main()
