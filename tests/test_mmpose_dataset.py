from __future__ import annotations

import csv
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import cv2
import numpy as np

from rallymate_training.mmpose_dataset import (
    MMPoseDatasetExportError,
    _extract_video_frames,
    export_mmpose_halpe26_dataset,
)
from rallymate_training.pose_finetune_readiness import (
    GOVERNANCE_FIELDS,
    HALPE26_JOINTS,
)
from rallymate_vision.pose.metadata import keypoint_schema


ANNOTATION_FIELDS = (
    "annotation_id",
    "task_id",
    "annotator_id",
    "visible",
    "x_normalized",
    "y_normalized",
    "visibility_reason",
    "annotated_at",
)
ADJUDICATION_FIELDS = (
    "adjudication_id",
    "task_id",
    "source_annotation_ids",
    "reviewer_id",
    "visible",
    "x_normalized",
    "y_normalized",
    "visibility_reason",
    "adjudicated_at",
    "status",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest().upper()


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )


def _write_csv(path: Path, fields: tuple[str, ...], rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields), extrasaction="raise")
        writer.writeheader()
        writer.writerows(rows)


def _binding(path: Path) -> dict[str, str]:
    return {"path": str(path.resolve()), "sha256": _sha256(path)}


def _build_pack(
    root: Path,
    *,
    video_id: str,
    joints: tuple[str, ...],
    invisible: set[str] | None = None,
    bbox: list[float] | None = None,
    x_normalized: float = 0.25,
    y_normalized: float = 0.75,
) -> Path:
    invisible = invisible or set()
    pack = root / f"pack-{video_id}-{len(list(root.glob('pack-*')))}"
    compiled = pack / "compiled"
    compiled.mkdir(parents=True)
    video = root / f"{video_id}.mp4"
    if not video.exists():
        video.write_bytes(f"synthetic-video-{video_id}".encode("ascii"))
    tasks = [
        {
            "schema_version": "1.0.0",
            "task_id": f"{pack.name}:{video_id}:0:{joint_name}",
            "video_id": video_id,
            "source_frame_index": 0,
            "timestamp_ms": 0,
            "frame_width": 10,
            "frame_height": 20,
            "primary_player_id": 1,
            "joint_name": joint_name,
            "bbox_px": bbox or [1.0, 2.0, 9.0, 18.0],
            "annotation_bbox_provenance": {
                "method": "detector_primary_timeline_bbox",
                "identity_truth_claim": False,
            },
        }
        for joint_name in joints
    ]
    _write_jsonl(pack / "tasks.jsonl", tasks)
    task_contract = _canonical_sha256(tasks)
    manifest = {
        "schema_version": "1.0.0",
        "pack_version": "synthetic-halpe26-export-v1",
        "video_id": video_id,
        "sources": {"video": _binding(video)},
        "scope": {
            "joint_task_count": len(tasks),
            "required_independent_annotators": 2,
            "required_independent_reviewer": 1,
        },
        "task_contract_sha256": task_contract,
        "safety": {"accuracy_claim": False},
    }
    _write_json(pack / "manifest.json", manifest)

    annotations: list[dict[str, Any]] = []
    adjudications: list[dict[str, Any]] = []
    source_ids_by_joint: dict[str, list[str]] = {}
    manual_joints: dict[str, dict[str, Any]] = {}
    for task in tasks:
        joint_name = task["joint_name"]
        source_ids = []
        is_visible = joint_name not in invisible
        for annotator in ("annotator-a", "annotator-b"):
            annotation_id = f"{annotator}:{task['task_id']}"
            source_ids.append(annotation_id)
            annotations.append(
                {
                    "annotation_id": annotation_id,
                    "task_id": task["task_id"],
                    "annotator_id": annotator,
                    "visible": "true" if is_visible else "false",
                    "x_normalized": str(x_normalized) if is_visible else "",
                    "y_normalized": str(y_normalized) if is_visible else "",
                    "visibility_reason": "" if is_visible else "occluded",
                    "annotated_at": "2026-01-01T00:00:00Z",
                }
            )
        source_ids_by_joint[joint_name] = source_ids
        adjudications.append(
            {
                "adjudication_id": f"decision:{task['task_id']}",
                "task_id": task["task_id"],
                "source_annotation_ids": ";".join(source_ids),
                "reviewer_id": "reviewer-c",
                "visible": "true" if is_visible else "false",
                "x_normalized": str(x_normalized) if is_visible else "",
                "y_normalized": str(y_normalized) if is_visible else "",
                "visibility_reason": "" if is_visible else "occluded",
                "adjudicated_at": "2026-01-01T01:00:00Z",
                "status": "accepted",
            }
        )
        manual_joints[joint_name] = {
            "visible": is_visible,
            "x_normalized": x_normalized if is_visible else None,
            "y_normalized": y_normalized if is_visible else None,
            "visibility_reason": None if is_visible else "occluded",
        }
    _write_csv(pack / "annotations.csv", ANNOTATION_FIELDS, annotations)
    _write_csv(pack / "adjudications.csv", ADJUDICATION_FIELDS, adjudications)
    manual = [
        {
            "schema_version": "1.0.0",
            "video_id": video_id,
            "source_frame_index": 0,
            "timestamp_ms": 0,
            "primary_player_id": 1,
            "annotator_id": "adjudicated-independent",
            "reviewer_id": "reviewer-c",
            "adjudication_status": "accepted",
            "view_group": "synthetic-camera",
            "joints": manual_joints,
            "provenance": {
                "pack_version": manifest["pack_version"],
                "task_contract_sha256": task_contract,
                "source_annotation_ids_by_joint": source_ids_by_joint,
            },
        }
    ]
    manual_path = compiled / "manual-keypoints.jsonl"
    _write_jsonl(manual_path, manual)
    validation = {
        "schema_version": "1.0.0",
        "pack_version": manifest["pack_version"],
        "status": "ready_for_keypoint_error_evaluation",
        "counts": {
            "joint_tasks": len(tasks),
            "raw_annotations": len(annotations),
            "tasks_with_two_independent_annotators": len(tasks),
            "accepted_adjudications": len(adjudications),
            "compiled_keypoint_records": 1,
            "compiled_joint_values": len(joints),
        },
        "errors": [],
        "readiness": {"full_task_coverage": True},
        "sources": {
            "manifest": _binding(pack / "manifest.json"),
            "tasks": _binding(pack / "tasks.jsonl"),
            "annotations": _binding(pack / "annotations.csv"),
            "adjudications": _binding(pack / "adjudications.csv"),
        },
        "artifacts": {"manual_keypoints": _binding(manual_path)},
    }
    _write_json(compiled / "validation-report.json", validation)
    return pack


def _write_governance(path: Path, split_by_video: dict[str, str]) -> None:
    rows = [
        {
            "video_id": video_id,
            "subject_id": f"subject-{video_id}",
            "session_id": f"session-{video_id}",
            "camera_id": f"camera-{video_id}",
            "consent": "granted",
            "license": "internal-research",
            "split": split,
        }
        for video_id, split in sorted(split_by_video.items())
    ]
    _write_csv(path, GOVERNANCE_FIELDS, rows)


def _fake_extract(video_path: Path, requests: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    base_value = hashlib.sha256(video_path.name.encode("utf-8")).digest()[0]
    for request in requests:
        output = Path(request["output_path"])
        output.parent.mkdir(parents=True, exist_ok=True)
        image = np.full(
            (int(request["height"]), int(request["width"]), 3),
            (base_value + int(request["source_frame_index"])) % 256,
            dtype=np.uint8,
        )
        if not cv2.imwrite(str(output), image):
            raise AssertionError("synthetic JPEG write failed")
        result[int(request["source_frame_index"])] = {
            "sha256": _sha256(output),
            "size_bytes": output.stat().st_size,
        }
    return result


def _identical_fake_extract(
    _: Path, requests: list[dict[str, Any]]
) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    for request in requests:
        output = Path(request["output_path"])
        output.parent.mkdir(parents=True, exist_ok=True)
        image = np.full(
            (int(request["height"]), int(request["width"]), 3),
            128,
            dtype=np.uint8,
        )
        if not cv2.imwrite(str(output), image):
            raise AssertionError("synthetic JPEG write failed")
        result[int(request["source_frame_index"])] = {
            "sha256": _sha256(output),
            "size_bytes": output.stat().st_size,
        }
    return result


def _same_pixels_distinct_jpeg_extract(
    video_path: Path, requests: list[dict[str, Any]]
) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    use_grayscale = video_path.stem == "video-b"
    for request in requests:
        output = Path(request["output_path"])
        output.parent.mkdir(parents=True, exist_ok=True)
        shape = (
            (int(request["height"]), int(request["width"]))
            if use_grayscale
            else (int(request["height"]), int(request["width"]), 3)
        )
        image = np.full(shape, 128, dtype=np.uint8)
        if not cv2.imwrite(str(output), image):
            raise AssertionError("synthetic JPEG write failed")
        result[int(request["source_frame_index"])] = {
            "sha256": _sha256(output),
            "size_bytes": output.stat().st_size,
        }
    return result


class MMPoseDatasetExportTests(unittest.TestCase):
    def _ready_inputs(self, root: Path) -> tuple[list[Path], Path]:
        train = _build_pack(
            root,
            video_id="video-a",
            joints=tuple(HALPE26_JOINTS),
        )
        val = _build_pack(
            root,
            video_id="video-b",
            joints=("nose", "right_eye"),
            invisible={"nose"},
        )
        governance = root / "governance.csv"
        _write_governance(governance, {"video-a": "train", "video-b": "val"})
        return [train, val], governance

    def test_exports_deterministic_coco_with_explicit_joint_states_and_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            packs, governance = self._ready_inputs(root)
            first = root / "dataset-a"
            second = root / "dataset-b"
            with patch(
                "rallymate_training.mmpose_dataset._extract_video_frames",
                side_effect=_fake_extract,
            ):
                manifest = export_mmpose_halpe26_dataset(
                    packs, governance_csv=governance, output_dir=first
                )
                export_mmpose_halpe26_dataset(
                    reversed(packs), governance_csv=governance, output_dir=second
                )

            self.assertEqual("ready_for_mmpose_training", manifest["status"])
            expected_names = [
                item["name"] for item in keypoint_schema("halpe26")["keypoints"]
            ]
            self.assertEqual(expected_names, manifest["topology"]["ordered_keypoints"])
            self.assertEqual("images", manifest["splits"]["train"]["image_root"])
            self.assertEqual("train", manifest["splits"]["train"]["image_prefix"])
            self.assertTrue(manifest["readiness"]["mmpose_training_allowed"])
            self.assertFalse(manifest["safety"]["accuracy_claim"])
            self.assertFalse(manifest["safety"]["holdout_test_used"])

            val_coco = json.loads(
                (first / "annotations" / "val.json").read_text(encoding="utf-8")
            )
            annotation = val_coco["annotations"][0]
            self.assertEqual(
                "adjudicated_invisible_no_coordinate",
                annotation["keypoint_states"][0],
            )
            self.assertEqual("not_annotated", annotation["keypoint_states"][1])
            self.assertEqual([0.0, 0.0, 0], annotation["keypoints"][:3])
            self.assertEqual([1.0, 2.0, 8.0, 16.0], annotation["bbox"])
            self.assertEqual(
                {
                    "ground_truth": False,
                    "immutable_task_field": "bbox_px",
                    "origin": "detector_derived",
                    "role": "annotation_crop",
                },
                annotation["bbox_provenance"],
            )
            train_coco = json.loads(
                (first / "annotations" / "train.json").read_text(encoding="utf-8")
            )
            self.assertEqual([2.5, 15.0, 2], train_coco["annotations"][0]["keypoints"][:3])
            self.assertEqual(26, train_coco["annotations"][0]["num_keypoints"])

            for artifact in manifest["artifacts"]:
                path = first / artifact["path"]
                self.assertEqual(artifact["sha256"], _sha256(path))
                self.assertEqual(artifact["size_bytes"], path.stat().st_size)
            for relative in (
                "manifest.json",
                "annotations/train.json",
                "annotations/val.json",
                "image-manifest.jsonl",
            ):
                self.assertEqual((first / relative).read_bytes(), (second / relative).read_bytes())

            image_manifest_row = json.loads(
                (first / "image-manifest.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()[0]
            )
            self.assertFalse(image_manifest_row["accuracy_claim"])
            self.assertFalse(image_manifest_row["bbox_ground_truth_claim"])

    def test_refuses_holdout_and_empty_validation_split(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            packs, governance = self._ready_inputs(root)
            _write_governance(governance, {"video-a": "train", "video-b": "test"})
            with self.assertRaisesRegex(
                MMPoseDatasetExportError,
                "development dataset export only accepts train/val",
            ):
                export_mmpose_halpe26_dataset(
                    packs, governance_csv=governance, output_dir=root / "holdout"
                )
            _write_governance(governance, {"video-a": "train", "video-b": "train"})
            with self.assertRaisesRegex(MMPoseDatasetExportError, "empty split: val"):
                export_mmpose_halpe26_dataset(
                    packs, governance_csv=governance, output_dir=root / "empty-val"
                )

    def test_refuses_immutable_bbox_outside_frame_even_when_truth_is_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            train = _build_pack(
                root,
                video_id="video-a",
                joints=tuple(HALPE26_JOINTS),
                bbox=[1.0, 2.0, 11.0, 18.0],
            )
            val = _build_pack(
                root,
                video_id="video-b",
                joints=tuple(HALPE26_JOINTS),
            )
            governance = root / "governance.csv"
            _write_governance(governance, {"video-a": "train", "video-b": "val"})
            with self.assertRaisesRegex(
                MMPoseDatasetExportError, "outside its immutable frame dimensions"
            ):
                export_mmpose_halpe26_dataset(
                    [train, val],
                    governance_csv=governance,
                    output_dir=root / "dataset",
                )

    def test_refuses_duplicate_cross_pack_supervision_and_existing_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = _build_pack(
                root, video_id="video-a", joints=tuple(HALPE26_JOINTS)
            )
            duplicate = _build_pack(
                root, video_id="video-a", joints=tuple(HALPE26_JOINTS)
            )
            governance = root / "governance.csv"
            _write_governance(governance, {"video-a": "train"})
            with self.assertRaisesRegex(
                MMPoseDatasetExportError, "appears in multiple packs"
            ):
                export_mmpose_halpe26_dataset(
                    [first, duplicate],
                    governance_csv=governance,
                    output_dir=root / "duplicate",
                )
            existing = root / "existing"
            existing.mkdir()
            with self.assertRaisesRegex(MMPoseDatasetExportError, "overwrite"):
                export_mmpose_halpe26_dataset(
                    [first], governance_csv=governance, output_dir=existing
                )

    def test_refuses_source_hash_drift_before_extracting(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            packs, governance = self._ready_inputs(root)
            (root / "video-a.mp4").write_bytes(b"changed")
            with patch(
                "rallymate_training.mmpose_dataset._extract_video_frames"
            ) as extractor:
                with self.assertRaisesRegex(
                    MMPoseDatasetExportError, "ready_for_dataset_export"
                ):
                    export_mmpose_halpe26_dataset(
                        packs,
                        governance_csv=governance,
                        output_dir=root / "dataset",
                    )
                extractor.assert_not_called()

    def test_refuses_source_alias_across_train_val(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            packs, governance = self._ready_inputs(root)
            validation = packs[1]
            manifest_path = validation / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["sources"]["video"] = _binding(root / "video-a.mp4")
            _write_json(manifest_path, manifest)
            report_path = validation / "compiled" / "validation-report.json"
            report = json.loads(report_path.read_text(encoding="utf-8"))
            report["sources"]["manifest"] = _binding(manifest_path)
            _write_json(report_path, report)

            with patch(
                "rallymate_training.mmpose_dataset._extract_video_frames"
            ) as extractor, self.assertRaisesRegex(
                MMPoseDatasetExportError, "ready_for_dataset_export"
            ):
                export_mmpose_halpe26_dataset(
                    packs,
                    governance_csv=governance,
                    output_dir=root / "aliased",
                )
            extractor.assert_not_called()

    def test_refuses_known_m95_holdout_even_when_governance_calls_it_val(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            holdout_id = "c235227fffcd3290b60572d0c3f9cc85"
            train = _build_pack(
                root,
                video_id="video-a",
                joints=tuple(HALPE26_JOINTS),
            )
            holdout = _build_pack(
                root,
                video_id=holdout_id,
                joints=tuple(HALPE26_JOINTS),
            )
            governance = root / "governance.csv"
            _write_governance(
                governance, {"video-a": "train", holdout_id: "val"}
            )

            with patch(
                "rallymate_training.mmpose_dataset._extract_video_frames"
            ) as extractor, self.assertRaisesRegex(
                MMPoseDatasetExportError, "ready_for_dataset_export"
            ):
                export_mmpose_halpe26_dataset(
                    [train, holdout],
                    governance_csv=governance,
                    output_dir=root / "holdout-alias",
                )
            extractor.assert_not_called()

    def test_refuses_global_only_halpe26_coverage_when_train_is_partial(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            train = _build_pack(root, video_id="video-a", joints=("nose",))
            validation = _build_pack(
                root,
                video_id="video-b",
                joints=tuple(HALPE26_JOINTS),
            )
            governance = root / "governance.csv"
            _write_governance(governance, {"video-a": "train", "video-b": "val"})

            with patch(
                "rallymate_training.mmpose_dataset._extract_video_frames"
            ) as extractor, self.assertRaisesRegex(
                MMPoseDatasetExportError, "train split lacks visible coordinate"
            ):
                export_mmpose_halpe26_dataset(
                    [train, validation],
                    governance_csv=governance,
                    output_dir=root / "partial-train",
                )
            extractor.assert_not_called()

    def test_refuses_any_export_frame_without_a_visible_coordinate(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            train = _build_pack(
                root,
                video_id="video-a",
                joints=tuple(HALPE26_JOINTS),
            )
            validation = _build_pack(
                root,
                video_id="video-b",
                joints=("nose",),
                invisible={"nose"},
            )
            governance = root / "governance.csv"
            _write_governance(governance, {"video-a": "train", "video-b": "val"})

            with patch(
                "rallymate_training.mmpose_dataset._extract_video_frames"
            ) as extractor, self.assertRaisesRegex(
                MMPoseDatasetExportError, "frame has no visible coordinate"
            ):
                export_mmpose_halpe26_dataset(
                    [train, validation],
                    governance_csv=governance,
                    output_dir=root / "zero-visible-frame",
                )
            extractor.assert_not_called()

    def test_refuses_half_open_boundary_and_visible_point_outside_bbox(self) -> None:
        for case, kwargs, message in (
            (
                "half-open-frame",
                {"x_normalized": 1.0},
                "point is invalid",
            ),
            (
                "outside-bbox",
                {"bbox": [5.0, 2.0, 9.0, 18.0]},
                "visible coordinate is outside task bbox",
            ),
        ):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                train = _build_pack(
                    root,
                    video_id="video-a",
                    joints=tuple(HALPE26_JOINTS),
                    **kwargs,
                )
                validation = _build_pack(
                    root,
                    video_id="video-b",
                    joints=("nose",),
                )
                governance = root / "governance.csv"
                _write_governance(
                    governance, {"video-a": "train", "video-b": "val"}
                )

                with patch(
                    "rallymate_training.mmpose_dataset._extract_video_frames"
                ) as extractor, self.assertRaisesRegex(
                    MMPoseDatasetExportError, message
                ):
                    export_mmpose_halpe26_dataset(
                        [train, validation],
                        governance_csv=governance,
                        output_dir=root / case,
                    )
                extractor.assert_not_called()

    def test_refuses_cross_split_jpeg_content_sha_leakage(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            packs, governance = self._ready_inputs(root)
            output = root / "jpeg-bytes"
            with patch(
                "rallymate_training.mmpose_dataset._extract_video_frames",
                side_effect=_identical_fake_extract,
            ), self.assertRaisesRegex(
                MMPoseDatasetExportError,
                "train/val JPEG content SHA-256 leakage",
            ):
                export_mmpose_halpe26_dataset(
                    packs,
                    governance_csv=governance,
                    output_dir=output,
                )
            self.assertFalse(output.exists())

    def test_refuses_cross_split_decoded_pixel_leakage(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            packs, governance = self._ready_inputs(root)
            output = root / "decoded-pixels"
            with patch(
                "rallymate_training.mmpose_dataset._extract_video_frames",
                side_effect=_same_pixels_distinct_jpeg_extract,
            ), self.assertRaisesRegex(
                MMPoseDatasetExportError,
                "train/val decoded-pixel leakage",
            ):
                export_mmpose_halpe26_dataset(
                    packs,
                    governance_csv=governance,
                    output_dir=output,
                )
            self.assertFalse(output.exists())

    def test_refuses_noncanonical_governance_split_spelling(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            packs, governance = self._ready_inputs(root)
            _write_governance(governance, {"video-a": "Train", "video-b": "Val"})
            output = root / "noncanonical-splits"

            with patch(
                "rallymate_training.mmpose_dataset._extract_video_frames"
            ) as extractor, self.assertRaisesRegex(
                MMPoseDatasetExportError, "split must be train, val or test"
            ):
                export_mmpose_halpe26_dataset(
                    packs,
                    governance_csv=governance,
                    output_dir=output,
                )
            extractor.assert_not_called()
            self.assertFalse(output.exists())

    def test_refuses_source_alias_within_one_split(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            train = _build_pack(
                root,
                video_id="video-a",
                joints=tuple(HALPE26_JOINTS),
            )
            alias = _build_pack(root, video_id="video-b", joints=("nose",))
            validation = _build_pack(root, video_id="video-c", joints=("nose",))
            alias_manifest_path = alias / "manifest.json"
            alias_manifest = json.loads(
                alias_manifest_path.read_text(encoding="utf-8")
            )
            alias_manifest["sources"]["video"] = _binding(root / "video-a.mp4")
            _write_json(alias_manifest_path, alias_manifest)
            alias_report_path = alias / "compiled" / "validation-report.json"
            alias_report = json.loads(alias_report_path.read_text(encoding="utf-8"))
            alias_report["sources"]["manifest"] = _binding(alias_manifest_path)
            _write_json(alias_report_path, alias_report)
            governance = root / "governance.csv"
            _write_governance(
                governance,
                {"video-a": "train", "video-b": "train", "video-c": "val"},
            )
            output = root / "same-split-source-alias"

            with patch(
                "rallymate_training.mmpose_dataset._extract_video_frames"
            ) as extractor, self.assertRaisesRegex(
                MMPoseDatasetExportError, "reused across video ids"
            ):
                export_mmpose_halpe26_dataset(
                    [train, alias, validation],
                    governance_csv=governance,
                    output_dir=output,
                )
            extractor.assert_not_called()
            self.assertFalse(output.exists())

    def test_refuses_one_video_frame_split_across_truth_packs(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            midpoint = len(HALPE26_JOINTS) // 2
            train_first = _build_pack(
                root,
                video_id="video-a",
                joints=tuple(HALPE26_JOINTS[:midpoint]),
            )
            train_second = _build_pack(
                root,
                video_id="video-a",
                joints=tuple(HALPE26_JOINTS[midpoint:]),
            )
            validation = _build_pack(root, video_id="video-b", joints=("nose",))
            governance = root / "governance.csv"
            _write_governance(governance, {"video-a": "train", "video-b": "val"})
            output = root / "cross-pack-frame"

            with patch(
                "rallymate_training.mmpose_dataset._extract_video_frames"
            ) as extractor, self.assertRaisesRegex(
                MMPoseDatasetExportError, "split across packs"
            ):
                export_mmpose_halpe26_dataset(
                    [train_first, train_second, validation],
                    governance_csv=governance,
                    output_dir=output,
                )
            extractor.assert_not_called()
            self.assertFalse(output.exists())

    def test_refuses_source_drift_during_extract_and_publishes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            packs, governance = self._ready_inputs(root)
            output = root / "dataset"

            def mutate_source(
                video_path: Path, requests: list[dict[str, Any]]
            ) -> dict[int, dict[str, Any]]:
                result = _fake_extract(video_path, requests)
                video_path.write_bytes(video_path.read_bytes() + b"-drift")
                return result

            with patch(
                "rallymate_training.mmpose_dataset._extract_video_frames",
                side_effect=mutate_source,
            ), self.assertRaisesRegex(
                MMPoseDatasetExportError, "changed during export"
            ):
                export_mmpose_halpe26_dataset(
                    packs, governance_csv=governance, output_dir=output
                )
            self.assertFalse(output.exists())

    def test_frame_extractor_reads_exact_indexes_and_verifies_dimensions(self) -> None:
        class FakeCapture:
            def __init__(self, _: str) -> None:
                self.index = 0
                self.frames = [
                    np.full((4, 6, 3), value, dtype=np.uint8)
                    for value in (10, 20, 30)
                ]

            def isOpened(self) -> bool:
                return True

            def read(self) -> tuple[bool, np.ndarray | None]:
                if self.index >= len(self.frames):
                    return False, None
                frame = self.frames[self.index]
                self.index += 1
                return True, frame

            def release(self) -> None:
                pass

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            requests = [
                {
                    "source_frame_index": index,
                    "width": 6,
                    "height": 4,
                    "output_path": root / f"{index}.jpg",
                }
                for index in (0, 2)
            ]
            with patch(
                "rallymate_training.mmpose_dataset.cv2.VideoCapture", FakeCapture
            ):
                result = _extract_video_frames(root / "source.mp4", requests)
            self.assertEqual({0, 2}, set(result))
            self.assertEqual((4, 6), cv2.imread(str(root / "2.jpg")).shape[:2])

            invalid = [
                {
                    "source_frame_index": 0,
                    "width": 7,
                    "height": 4,
                    "output_path": root / "invalid.jpg",
                }
            ]
            with patch(
                "rallymate_training.mmpose_dataset.cv2.VideoCapture", FakeCapture
            ), self.assertRaisesRegex(
                MMPoseDatasetExportError, "dimensions differ"
            ):
                _extract_video_frames(root / "source.mp4", invalid)


if __name__ == "__main__":
    unittest.main()
