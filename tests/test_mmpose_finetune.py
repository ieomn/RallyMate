from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import cv2
import numpy as np

from rallymate_training.mmpose_dataset import export_mmpose_halpe26_dataset
from rallymate_training.mmpose_finetune import (
    MMPoseFineTuneError,
    _run_mmengine,
    prepare_or_run_rtmpose_finetune,
)
from rallymate_training.pose_finetune_readiness import HALPE26_JOINTS
from tests.test_mmpose_dataset import (
    _build_pack,
    _fake_extract,
    _sha256,
    _write_governance,
)


ROOT = Path(__file__).resolve().parents[1]


def _distinct_fake_extract(video_path: Path, requests: list[dict]) -> dict:
    result = _fake_extract(video_path, requests)
    color = tuple(hashlib.sha256(video_path.name.encode("utf-8")).digest()[:3])
    for request in requests:
        output = Path(request["output_path"])
        image = cv2.imread(str(output), cv2.IMREAD_COLOR)
        if image is None:
            raise AssertionError("synthetic JPEG read failed")
        image[:, :] = color
        if not cv2.imwrite(str(output), image):
            raise AssertionError("synthetic JPEG rewrite failed")
        result[int(request["source_frame_index"])] = {
            "sha256": _sha256(output),
            "size_bytes": output.stat().st_size,
        }
    return result


class _FakeHook:
    pass


class _FakeRunner:
    def __init__(
        self,
        *,
        fail_before_start: bool = False,
        fail_after_start: bool = False,
        interrupt_after_start: bool = False,
    ) -> None:
        self.hook = None
        self.fail_before_start = fail_before_start
        self.fail_after_start = fail_after_start
        self.interrupt_after_start = interrupt_after_start

    def register_hook(self, hook, priority: str) -> None:
        if priority != "HIGHEST":
            raise AssertionError("state hook priority drift")
        self.hook = hook

    def train(self) -> None:
        if self.fail_before_start:
            raise RuntimeError("pre-start failure")
        self.hook.before_train(self)
        if self.interrupt_after_start:
            raise KeyboardInterrupt()
        if self.fail_after_start:
            raise RuntimeError("post-start failure")


def _ready_dataset(root: Path) -> tuple[Path, str]:
    train_partial = _build_pack(
        root,
        video_id="video-train-partial",
        joints=tuple(HALPE26_JOINTS),
        invisible={"nose"},
    )
    train_visible = _build_pack(
        root,
        video_id="video-train-visible",
        joints=tuple(HALPE26_JOINTS),
    )
    val = _build_pack(
        root,
        video_id="video-val",
        joints=tuple(HALPE26_JOINTS),
    )
    governance = root / "governance.csv"
    _write_governance(
        governance,
        {
            "video-train-partial": "train",
            "video-train-visible": "train",
            "video-val": "val",
        },
    )
    dataset = root / "dataset"
    with patch(
        "rallymate_training.mmpose_dataset._extract_video_frames",
        side_effect=_distinct_fake_extract,
    ):
        export_mmpose_halpe26_dataset(
            [train_partial, train_visible, val],
            governance_csv=governance,
            output_dir=dataset,
        )
    manifest = dataset / "manifest.json"
    return manifest, _sha256(manifest)


def _write_manifest(path: Path, value: dict) -> str:
    path.write_text(
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return _sha256(path)


def _refresh_artifact_bindings(dataset: Path, manifest: dict) -> None:
    for split_name in ("train", "val"):
        annotation = dataset / manifest["splits"][split_name]["annotation_file"]
        manifest["splits"][split_name]["annotation_sha256"] = _sha256(annotation)
    image_manifest = dataset / manifest["image_manifest"]["path"]
    manifest["image_manifest"]["sha256"] = _sha256(image_manifest)
    for artifact in manifest["artifacts"]:
        bound = dataset / artifact["path"]
        artifact["sha256"] = _sha256(bound)
        artifact["size_bytes"] = bound.stat().st_size


class MMPoseFineTuneTests(unittest.TestCase):
    def test_dry_run_validates_real_runtime_and_writes_immutable_plan(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest, manifest_hash = _ready_dataset(root)
            output = root / "dry-run"

            result = prepare_or_run_rtmpose_finetune(
                manifest,
                manifest_hash,
                output,
                workspace=ROOT,
            )

            self.assertEqual("dry_run_validated", result["status"])
            self.assertFalse(result["training_started"])
            self.assertFalse(result["accuracy_claim"])
            self.assertFalse(result["promotion_claim"])
            self.assertTrue(result["gates"]["all_training_gates_ready"])
            self.assertTrue(result["runtime"]["config_parse_valid"])
            self.assertEqual("CocoDataset", result["runtime"]["train_dataset_type"])
            self.assertEqual("CocoDataset", result["runtime"]["val_dataset_type"])
            self.assertEqual("CocoDataset", result["runtime"]["test_dataset_type"])
            self.assertEqual("AmpOptimWrapper", result["runtime"]["optim_wrapper_type"])
            self.assertEqual(
                result["runtime"]["val_ann_file"],
                result["runtime"]["test_ann_file"],
            )
            self.assertTrue((output / "resolved-config.py").is_file())
            self.assertTrue((output / "finetune-run-record.json").is_file())
            with self.assertRaisesRegex(MMPoseFineTuneError, "already exists"):
                prepare_or_run_rtmpose_finetune(
                    manifest, manifest_hash, output, workspace=ROOT
                )

    def test_mixed_visibility_export_is_accepted_by_real_adapter_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            train = _build_pack(
                root,
                video_id="video-train",
                joints=tuple(HALPE26_JOINTS),
            )
            validation = _build_pack(
                root,
                video_id="video-val-mixed",
                joints=("nose", "right_eye"),
                invisible={"nose"},
            )
            governance = root / "governance.csv"
            _write_governance(
                governance,
                {"video-train": "train", "video-val-mixed": "val"},
            )
            dataset = root / "dataset"
            with patch(
                "rallymate_training.mmpose_dataset._extract_video_frames",
                side_effect=_distinct_fake_extract,
            ):
                export_mmpose_halpe26_dataset(
                    [train, validation],
                    governance_csv=governance,
                    output_dir=dataset,
                )
            manifest = dataset / "manifest.json"
            val_coco = json.loads(
                (dataset / "annotations" / "val.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                "adjudicated_invisible_no_coordinate",
                val_coco["annotations"][0]["keypoint_states"][0],
            )

            result = prepare_or_run_rtmpose_finetune(
                manifest,
                _sha256(manifest),
                root / "dry-run",
                workspace=ROOT,
            )

            self.assertEqual("dry_run_validated", result["status"])
            self.assertTrue(result["runtime"]["dataset_construction_valid"])
            self.assertFalse(result["training_started"])

    def test_not_ready_and_zero_truth_status_fail_before_runner_or_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest, _ = _ready_dataset(root)
            value = json.loads(manifest.read_text(encoding="utf-8"))
            value["status"] = "annotation_required"
            value["readiness"]["mmpose_training_allowed"] = False
            manifest_hash = _write_manifest(manifest, value)
            output = root / "rejected"

            with patch(
                "rallymate_training.mmpose_finetune._validate_execute_environment"
            ), patch("rallymate_training.mmpose_finetune._run_mmengine") as runner:
                with self.assertRaisesRegex(MMPoseFineTuneError, "not ready"):
                    prepare_or_run_rtmpose_finetune(
                        manifest,
                        manifest_hash,
                        output,
                        execute=True,
                        workspace=ROOT,
                    )
                runner.assert_not_called()
            self.assertFalse(output.exists())

    def test_train_val_subject_leakage_is_rejected_even_when_manifest_claims_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest, _ = _ready_dataset(root)
            dataset = manifest.parent
            value = json.loads(manifest.read_text(encoding="utf-8"))
            train_subject = value["splits"]["train"]["subject_ids"][0]

            val_path = dataset / "annotations" / "val.json"
            val_coco = json.loads(val_path.read_text(encoding="utf-8"))
            val_coco["images"][0]["subject_id"] = train_subject
            val_path.write_text(
                json.dumps(val_coco, ensure_ascii=False, indent=2, sort_keys=True)
                + "\n",
                encoding="utf-8",
            )
            image_manifest_path = dataset / "image-manifest.jsonl"
            rows = [
                json.loads(line)
                for line in image_manifest_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            for row in rows:
                if row["split"] == "val":
                    row["subject_id"] = train_subject
            image_manifest_path.write_text(
                "".join(
                    json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                    + "\n"
                    for row in rows
                ),
                encoding="utf-8",
            )
            value["splits"]["val"]["subject_ids"] = [train_subject]
            value["splits"]["val"]["annotation_sha256"] = _sha256(val_path)
            value["image_manifest"]["sha256"] = _sha256(image_manifest_path)
            for artifact in value["artifacts"]:
                bound = dataset / artifact["path"]
                artifact["sha256"] = _sha256(bound)
                artifact["size_bytes"] = bound.stat().st_size
            manifest_hash = _write_manifest(manifest, value)

            with self.assertRaisesRegex(MMPoseFineTuneError, "subject_ids leakage"):
                prepare_or_run_rtmpose_finetune(
                    manifest,
                    manifest_hash,
                    root / "leakage",
                    workspace=ROOT,
                )

    def test_artifact_hash_drift_and_base_config_hash_drift_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest, manifest_hash = _ready_dataset(root)
            train_annotation = manifest.parent / "annotations" / "train.json"
            train_annotation.write_bytes(train_annotation.read_bytes() + b" ")
            with self.assertRaisesRegex(MMPoseFineTuneError, "artifact SHA-256 mismatch"):
                prepare_or_run_rtmpose_finetune(
                    manifest,
                    manifest_hash,
                    root / "artifact-drift",
                    workspace=ROOT,
                )

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest, manifest_hash = _ready_dataset(root)
            with patch(
                "rallymate_training.mmpose_finetune.APPROVED_BASE_CONFIG_SHA256",
                "0" * 64,
            ), self.assertRaisesRegex(MMPoseFineTuneError, "base config SHA-256 drift"):
                prepare_or_run_rtmpose_finetune(
                    manifest,
                    manifest_hash,
                    root / "base-drift",
                    workspace=ROOT,
                )

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest, manifest_hash = _ready_dataset(root)
            with patch(
                "rallymate_training.mmpose_finetune.APPROVED_CHECKPOINT_SHA256",
                "0" * 64,
            ), self.assertRaisesRegex(MMPoseFineTuneError, "checkpoint SHA-256 drift"):
                prepare_or_run_rtmpose_finetune(
                    manifest,
                    manifest_hash,
                    root / "checkpoint-drift",
                    workspace=ROOT,
                )

    def test_governance_is_rechecked_against_every_coco_image(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest, _ = _ready_dataset(root)
            value = json.loads(manifest.read_text(encoding="utf-8"))
            governance = Path(value["governance"]["path"])
            lines = governance.read_text(encoding="utf-8").splitlines()
            line_index = next(
                index
                for index, line in enumerate(lines)
                if line.startswith("video-train-partial,")
            )
            self.assertTrue(lines[line_index].endswith(",train"))
            lines[line_index] = lines[line_index][:-5] + "val"
            governance.write_text(
                "\n".join(lines) + "\n",
                encoding="utf-8",
            )
            value["governance"]["sha256"] = _sha256(governance)
            manifest_hash = _write_manifest(manifest, value)

            with self.assertRaisesRegex(MMPoseFineTuneError, "governance"):
                prepare_or_run_rtmpose_finetune(
                    manifest,
                    manifest_hash,
                    root / "governance-drift",
                    workspace=ROOT,
                )

    def test_visible_keypoint_outside_annotation_crop_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest, _ = _ready_dataset(root)
            dataset = manifest.parent
            value = json.loads(manifest.read_text(encoding="utf-8"))
            annotation = dataset / value["splits"]["train"]["annotation_file"]
            coco = json.loads(annotation.read_text(encoding="utf-8"))
            visible = next(
                row for row in coco["annotations"] if row["keypoint_states"][0]
                == "adjudicated_visible_coordinate"
            )
            visible["keypoints"][0] = 0.5
            annotation.write_text(
                json.dumps(coco, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            _refresh_artifact_bindings(dataset, value)
            manifest_hash = _write_manifest(manifest, value)

            with self.assertRaisesRegex(MMPoseFineTuneError, "outside its annotation bbox"):
                prepare_or_run_rtmpose_finetune(
                    manifest,
                    manifest_hash,
                    root / "crop-leak",
                    workspace=ROOT,
                )

    def test_decoded_pixel_leakage_is_rejected_even_when_jpeg_bytes_differ(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest, _ = _ready_dataset(root)
            dataset = manifest.parent
            value = json.loads(manifest.read_text(encoding="utf-8"))
            image_manifest = dataset / value["image_manifest"]["path"]
            rows = [
                json.loads(line)
                for line in image_manifest.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            train_row = next(row for row in rows if row["split"] == "train")
            val_row = next(row for row in rows if row["split"] == "val")
            train_image = dataset / "images" / train_row["file_name"]
            val_image = dataset / "images" / val_row["file_name"]
            self.assertTrue(
                cv2.imwrite(
                    str(train_image), np.full((20, 10, 3), 128, dtype=np.uint8)
                )
            )
            self.assertTrue(
                cv2.imwrite(str(val_image), np.full((20, 10), 128, dtype=np.uint8))
            )
            for row, image_path in (
                (train_row, train_image),
                (val_row, val_image),
            ):
                row["sha256"] = _sha256(image_path)
                row["size_bytes"] = image_path.stat().st_size
            image_manifest.write_text(
                "".join(
                    json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                    + "\n"
                    for row in rows
                ),
                encoding="utf-8",
            )
            _refresh_artifact_bindings(dataset, value)
            manifest_hash = _write_manifest(manifest, value)

            with self.assertRaisesRegex(MMPoseFineTuneError, "decoded-pixel leakage"):
                prepare_or_run_rtmpose_finetune(
                    manifest,
                    manifest_hash,
                    root / "pixel-leak",
                    workspace=ROOT,
                )

    def test_unknown_manifest_field_and_holdout_guard_drift_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest, _ = _ready_dataset(root)
            value = json.loads(manifest.read_text(encoding="utf-8"))
            value["unrecognized"] = True
            manifest_hash = _write_manifest(manifest, value)
            with self.assertRaisesRegex(MMPoseFineTuneError, "fields do not match"):
                prepare_or_run_rtmpose_finetune(
                    manifest, manifest_hash, root / "unknown", workspace=ROOT
                )

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest, _ = _ready_dataset(root)
            value = json.loads(manifest.read_text(encoding="utf-8"))
            value["sealed_holdout_guard"]["video_id"] = "forged-holdout"
            manifest_hash = _write_manifest(manifest, value)
            with self.assertRaisesRegex(MMPoseFineTuneError, "sealed-holdout"):
                prepare_or_run_rtmpose_finetune(
                    manifest, manifest_hash, root / "holdout-drift", workspace=ROOT
                )

    def test_execute_invokes_runner_only_after_all_gates(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest, manifest_hash = _ready_dataset(root)
            output = root / "execute"
            fake_runner = _FakeRunner()
            restore = Mock()
            with patch(
                "rallymate_training.mmpose_finetune._validate_execute_environment"
            ), patch(
                "rallymate_training.mmpose_finetune._build_mmengine_runner",
                return_value=(fake_runner, _FakeHook, restore),
            ) as builder:
                result = prepare_or_run_rtmpose_finetune(
                    manifest,
                    manifest_hash,
                    output,
                    execute=True,
                    workspace=ROOT,
                )
                builder.assert_called_once()
                restore.assert_called_once()
            self.assertTrue(result["training_started"])
            self.assertTrue(result["training_completed"])
            self.assertEqual("training_completed", result["status"])
            stored = json.loads(
                (output / "finetune-run-record.json").read_text(encoding="utf-8")
            )
            self.assertTrue(stored["training_started"])
            self.assertFalse(stored["accuracy_claim"])
            self.assertFalse(stored["promotion_claim"])
            self.assertEqual(
                [
                    "validated_ready_to_start",
                    "runner_initialized",
                    "training_running",
                    "training_completed",
                ],
                [item["status"] for item in stored["lifecycle"]],
            )

    def test_runner_failures_record_whether_training_really_started(self) -> None:
        for fail_after_start, expected_status, expected_started in (
            (False, "runner_start_failed", False),
            (True, "training_failed", True),
        ):
            with self.subTest(status=expected_status), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                record_path = root / "finetune-run-record.json"
                _write_manifest(
                    record_path,
                    {
                        "status": "validated_ready_to_start",
                        "training_started": False,
                        "training_completed": False,
                        "lifecycle": [],
                    },
                )
                fake_runner = _FakeRunner(
                    fail_before_start=not fail_after_start,
                    fail_after_start=fail_after_start,
                )
                restore = Mock()
                with patch(
                    "rallymate_training.mmpose_finetune._validate_execute_environment"
                ), patch(
                    "rallymate_training.mmpose_finetune._build_mmengine_runner",
                    return_value=(fake_runner, _FakeHook, restore),
                ), self.assertRaises(MMPoseFineTuneError) as caught:
                    _run_mmengine(root / "config.py", root / "python.exe", record_path)
                restore.assert_called_once()
                stored = json.loads(record_path.read_text(encoding="utf-8"))
                self.assertEqual(expected_status, stored["status"])
                self.assertEqual(expected_started, stored["training_started"])
                self.assertEqual(expected_started, caught.exception.training_started)

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            record_path = root / "finetune-run-record.json"
            _write_manifest(
                record_path,
                {
                    "status": "validated_ready_to_start",
                    "training_started": False,
                    "training_completed": False,
                    "lifecycle": [],
                },
            )
            with patch(
                "rallymate_training.mmpose_finetune._validate_execute_environment"
            ), patch(
                "rallymate_training.mmpose_finetune._build_mmengine_runner",
                side_effect=RuntimeError("runner build failed"),
            ), self.assertRaises(MMPoseFineTuneError) as caught:
                _run_mmengine(root / "config.py", root / "python.exe", record_path)
            stored = json.loads(record_path.read_text(encoding="utf-8"))
            self.assertEqual("runner_initialization_failed", stored["status"])
            self.assertFalse(stored["training_started"])
            self.assertFalse(caught.exception.training_started)

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            record_path = root / "finetune-run-record.json"
            _write_manifest(
                record_path,
                {
                    "status": "validated_ready_to_start",
                    "training_started": False,
                    "training_completed": False,
                    "lifecycle": [],
                },
            )
            restore = Mock()
            with patch(
                "rallymate_training.mmpose_finetune._validate_execute_environment"
            ), patch(
                "rallymate_training.mmpose_finetune._build_mmengine_runner",
                return_value=(
                    _FakeRunner(interrupt_after_start=True),
                    _FakeHook,
                    restore,
                ),
            ), self.assertRaises(KeyboardInterrupt):
                _run_mmengine(root / "config.py", root / "python.exe", record_path)
            restore.assert_called_once()
            stored = json.loads(record_path.read_text(encoding="utf-8"))
            self.assertEqual("training_interrupted", stored["status"])
            self.assertTrue(stored["training_started"])


if __name__ == "__main__":
    unittest.main()
