from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from rallymate_evaluation.pose_shadow_smoke import (
    PoseShadowSmokeError,
    _read_frozen_samples,
    load_shadow_registry,
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


class PoseShadowRegistryTests(unittest.TestCase):
    def _workspace(self, root: Path) -> Path:
        deployment = root / "models" / "rtmpose" / "deployment-presets.json"
        _write_json(deployment, {"default_preset": "rtmpose-m-halpe26-online"})
        checkpoint = root / "models" / "rtmpose" / "x.pth"
        checkpoint.write_bytes(b"checkpoint")
        config = root / "runtime" / "x.py"
        config.parent.mkdir(parents=True)
        config.write_text("model = {}\n", encoding="utf-8")
        video = root / "FULL-TEST" / "dev.mp4"
        video.parent.mkdir(parents=True)
        video.write_bytes(b"video")
        frames = root / "runs" / "dev" / "frames.jsonl"
        frames.parent.mkdir(parents=True)
        frames.write_text(
            json.dumps(
                {
                    "frame": {"processed_index": 0, "index": 0, "timestamp_ms": 0},
                    "detections": [
                        {"class_name": "player", "bbox_px": [0, 0, 10, 20]}
                    ],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        registry = {
            "schema_version": "1.0.0",
            "registry_version": "rtmpose-shadow-candidates-2026-09-04.1",
            "production_default_binding": {
                "preset_id": "rtmpose-m-halpe26-online",
                "registry_relative_path": "models/rtmpose/deployment-presets.json",
                "registry_sha256": _sha(deployment),
            },
            "development_protocol": {
                "phase": "development_smoke_only",
                "videos": [
                    {
                        "video_id": "dev",
                        "video_relative_path": "FULL-TEST/dev.mp4",
                        "video_sha256": _sha(video),
                        "frozen_frames_relative_path": "runs/dev/frames.jsonl",
                        "frozen_frames_sha256": _sha(frames),
                        "frame_count": 1,
                    }
                ],
                "sealed_holdout": {
                    "video_id": "holdout",
                    "use_during_development": False,
                },
                "ground_truth_used": False,
                "accuracy_claim": False,
            },
            "candidates": [
                {
                    "candidate_id": "x",
                    "model_relative_path": "models/rtmpose/x.pth",
                    "checkpoint_sha256": _sha(checkpoint),
                    "checkpoint_bytes": checkpoint.stat().st_size,
                    "config_runtime_relative_path": "runtime/x.py",
                    "config_sha256": _sha(config),
                    "pose_backend": "rtmpose",
                    "pose_runtime": "pytorch",
                    "pose_profile": "analysis",
                    "native_keypoint_format": "halpe26",
                    "input_size_hw": [384, 288],
                }
            ],
            "safety": {
                "changes_M94_deployment_registry": False,
                "changes_production_default": False,
                "ground_truth_used": False,
                "RallyMate_accuracy_claim": False,
                "candidate_promoted": False,
                "F3_or_F4_promoted": False,
                "grade_or_threshold_generated": False,
            },
        }
        _write_json(root / "models" / "rtmpose" / "m95-shadow-candidates.json", registry)
        return root

    def test_registry_binds_default_candidate_and_development_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._workspace(Path(directory))
            verified = load_shadow_registry(root)
            self.assertEqual("rtmpose-m-halpe26-online", verified["production_default_preset"])
            self.assertEqual("dev", verified["videos"][0]["video_id"])
            self.assertEqual("x", verified["candidates"][0]["candidate_id"])

    def test_checkpoint_tamper_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._workspace(Path(directory))
            (root / "models" / "rtmpose" / "x.pth").write_bytes(b"tampered")
            with self.assertRaisesRegex(PoseShadowSmokeError, "SHA-256 drifted"):
                load_shadow_registry(root)

    def test_holdout_cannot_appear_in_development_videos(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._workspace(Path(directory))
            path = root / "models" / "rtmpose" / "m95-shadow-candidates.json"
            registry = json.loads(path.read_text(encoding="utf-8"))
            registry["development_protocol"]["videos"][0]["video_id"] = "holdout"
            _write_json(path, registry)
            with self.assertRaisesRegex(PoseShadowSmokeError, "include holdout"):
                load_shadow_registry(root)

    def test_sample_selection_is_stable_and_requires_declared_frame_count(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "frames.jsonl"
            rows = [
                {
                    "frame": {
                        "processed_index": index,
                        "index": index,
                        "timestamp_ms": index * 40,
                    },
                    "detections": [
                        {"class_name": "player", "bbox_px": [1, 2, 11, 22]}
                    ]
                    if index != 2
                    else [],
                }
                for index in range(5)
            ]
            path.write_text(
                "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
            )
            samples = _read_frozen_samples(
                path, video_id="dev", declared_frame_count=5, sample_count=3
            )
            self.assertEqual([0, 3, 4], [row["processed_index"] for row in samples])
            with self.assertRaisesRegex(PoseShadowSmokeError, "frame count drifted"):
                _read_frozen_samples(
                    path, video_id="dev", declared_frame_count=4, sample_count=1
                )


if __name__ == "__main__":
    unittest.main()
