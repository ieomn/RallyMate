from __future__ import annotations

import csv
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from rallymate_training.pose_finetune_readiness import (
    GOVERNANCE_FIELDS,
    HALPE26_JOINTS,
    M95_SHADOW_REGISTRY_SHA256,
    PoseFineTuneReadinessError,
    audit_pose_finetune_readiness,
    load_m95_sealed_holdout_guard,
)


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


def _write_jsonl(path: Path, values: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(
            json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n"
            for value in values
        ),
        encoding="utf-8",
    )


def _write_csv(
    path: Path, fields: tuple[str, ...], rows: list[dict[str, Any]]
) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields), extrasaction="raise")
        writer.writeheader()
        writer.writerows(rows)


def _binding(path: Path) -> dict[str, str]:
    return {"path": str(path.resolve()), "sha256": _sha256(path)}


def _build_pack(
    root: Path,
    *,
    video_id: str = "video-a",
    reviewer_id: str = "reviewer-c",
    complete: bool = True,
    joints: tuple[str, ...] = HALPE26_JOINTS,
) -> Path:
    pack = root / f"pack-{video_id}"
    compiled = pack / "compiled"
    compiled.mkdir(parents=True)
    video = root / f"{video_id}.mp4"
    video.write_bytes(f"synthetic-{video_id}".encode("ascii"))

    tasks = [
        {
            "schema_version": "1.0.0",
            "task_id": f"{video_id}:0:{joint_name}",
            "video_id": video_id,
            "source_frame_index": 0,
            "timestamp_ms": 0,
            "joint_name": joint_name,
        }
        for joint_name in joints
    ]
    _write_jsonl(pack / "tasks.jsonl", tasks)
    task_contract = _canonical_sha256(tasks)
    manifest = {
        "schema_version": "1.0.0",
        "pack_version": "synthetic-halpe26-v1",
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
    if complete:
        for task in tasks:
            source_ids = []
            for annotator_id in ("annotator-a", "annotator-b"):
                annotation_id = f"{annotator_id}:{task['task_id']}"
                source_ids.append(annotation_id)
                annotations.append(
                    {
                        "annotation_id": annotation_id,
                        "task_id": task["task_id"],
                        "annotator_id": annotator_id,
                        "visible": "true",
                        "x_normalized": "0.25",
                        "y_normalized": "0.75",
                        "visibility_reason": "",
                        "annotated_at": "2026-01-01T00:00:00Z",
                    }
                )
            source_ids_by_joint[task["joint_name"]] = source_ids
            adjudications.append(
                {
                    "adjudication_id": f"decision:{task['task_id']}",
                    "task_id": task["task_id"],
                    "source_annotation_ids": ";".join(source_ids),
                    "reviewer_id": reviewer_id,
                    "visible": "true",
                    "x_normalized": "0.25",
                    "y_normalized": "0.75",
                    "visibility_reason": "",
                    "adjudicated_at": "2026-01-01T01:00:00Z",
                    "status": "accepted",
                }
            )
    _write_csv(pack / "annotations.csv", ANNOTATION_FIELDS, annotations)
    _write_csv(pack / "adjudications.csv", ADJUDICATION_FIELDS, adjudications)

    manual_records = []
    if complete:
        manual_records.append(
            {
                "schema_version": "1.0.0",
                "video_id": video_id,
                "source_frame_index": 0,
                "timestamp_ms": 0,
                "primary_player_id": 1,
                "annotator_id": "adjudicated-independent",
                "reviewer_id": reviewer_id,
                "adjudication_status": "accepted",
                "view_group": "synthetic-camera",
                "joints": {
                    joint_name: {
                        "visible": True,
                        "x_normalized": 0.25,
                        "y_normalized": 0.75,
                        "visibility_reason": None,
                    }
                    for joint_name in joints
                },
                "provenance": {
                    "pack_version": manifest["pack_version"],
                    "task_contract_sha256": task_contract,
                    "source_annotation_ids_by_joint": source_ids_by_joint,
                },
            }
        )
    manual_path = compiled / "manual-keypoints.jsonl"
    _write_jsonl(manual_path, manual_records)
    validation = {
        "schema_version": "1.0.0",
        "pack_version": manifest["pack_version"],
        "status": "ready_for_keypoint_error_evaluation"
        if complete
        else "annotation_required",
        "counts": {
            "joint_tasks": len(tasks),
            "raw_annotations": len(annotations),
            "tasks_with_two_independent_annotators": len(tasks) if complete else 0,
            "accepted_adjudications": len(adjudications),
            "compiled_keypoint_records": len(manual_records),
            "compiled_joint_values": len(joints) if complete else 0,
        },
        "errors": [],
        "readiness": {"full_task_coverage": complete},
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


def _write_governance(path: Path, rows: list[dict[str, str]]) -> None:
    _write_csv(path, GOVERNANCE_FIELDS, rows)


def _governance_row(
    video_id: str,
    *,
    split: str = "train",
    subject_id: str | None = None,
) -> dict[str, str]:
    return {
        "video_id": video_id,
        "subject_id": subject_id or f"subject-{video_id}",
        "session_id": f"session-{video_id}",
        "camera_id": "camera-a",
        "consent": "granted",
        "license": "internal-research",
        "split": split,
    }


class PoseFineTuneReadinessTests(unittest.TestCase):
    def test_complete_halpe26_truth_and_governance_are_export_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pack = _build_pack(root)
            governance = root / "governance.csv"
            _write_governance(governance, [_governance_row("video-a")])

            report = audit_pose_finetune_readiness(
                [pack], governance_csv=governance
            )

            self.assertEqual("pose-finetune-readiness-v1.2.0", report["report_version"])
            self.assertEqual("ready_for_dataset_export", report["status"])
            self.assertEqual(1, report["counts"]["accepted_frames"])
            self.assertEqual(26, report["counts"]["accepted_joint_values"])
            self.assertTrue(report["readiness"]["dataset_export_allowed"])
            self.assertTrue(
                report["readiness"][
                    "two_annotator_independent_reviewer_lineage"
                ]
            )
            self.assertTrue(
                all(
                    item["visible_with_coordinates"] == 1
                    for item in report["halpe26_supervision"]
                )
            )
            self.assertFalse(report["accuracy_claim"])
            self.assertFalse(report["training_started"])

    def test_ready_truth_without_governance_requires_governance(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            pack = _build_pack(Path(temp))

            report = audit_pose_finetune_readiness([pack])

            self.assertEqual("governance_required", report["status"])
            self.assertTrue(report["readiness"]["all_halpe26_joints_supervised"])
            self.assertFalse(report["readiness"]["governance_complete"])

    def test_zero_truth_fails_closed_as_annotation_required_and_is_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pack = _build_pack(root, complete=False)
            governance = root / "governance.csv"
            _write_governance(governance, [_governance_row("video-a")])
            before = {
                str(path.relative_to(pack)): _sha256(path)
                for path in pack.rglob("*")
                if path.is_file()
            }

            first = audit_pose_finetune_readiness(
                [pack], governance_csv=governance
            )
            second = audit_pose_finetune_readiness(
                [pack], governance_csv=governance
            )
            after = {
                str(path.relative_to(pack)): _sha256(path)
                for path in pack.rglob("*")
                if path.is_file()
            }

            self.assertEqual("annotation_required", first["status"])
            self.assertEqual(0, first["counts"]["accepted_joint_values"])
            self.assertFalse(first["readiness"]["dataset_export_allowed"])
            self.assertIn("no accepted adjudicated keypoint truth", first["blockers"])
            self.assertEqual(first, second)
            self.assertEqual(before, after)

    def test_reviewer_must_be_independent_from_both_annotators(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pack = _build_pack(root, reviewer_id="annotator-a")
            governance = root / "governance.csv"
            _write_governance(governance, [_governance_row("video-a")])

            report = audit_pose_finetune_readiness(
                [pack], governance_csv=governance
            )

            self.assertEqual("annotation_required", report["status"])
            self.assertFalse(
                report["readiness"][
                    "two_annotator_independent_reviewer_lineage"
                ]
            )
            self.assertTrue(
                any("reviewer is not independent" in item for item in report["blockers"])
            )

    def test_compiled_artifact_hash_mismatch_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pack = _build_pack(root)
            governance = root / "governance.csv"
            _write_governance(governance, [_governance_row("video-a")])
            with (pack / "compiled" / "manual-keypoints.jsonl").open(
                "a", encoding="utf-8"
            ) as handle:
                handle.write("\n")

            report = audit_pose_finetune_readiness(
                [pack], governance_csv=governance
            )

            self.assertEqual("annotation_required", report["status"])
            self.assertFalse(report["readiness"]["compiled_bindings_valid"])
            self.assertTrue(
                any("manual_keypoints" in item and "mismatch" in item for item in report["blockers"])
            )

    def test_compiled_validation_status_must_explicitly_be_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pack = _build_pack(root)
            validation_path = pack / "compiled" / "validation-report.json"
            validation = json.loads(validation_path.read_text(encoding="utf-8"))
            validation["status"] = "annotation_required"
            _write_json(validation_path, validation)
            governance = root / "governance.csv"
            _write_governance(governance, [_governance_row("video-a")])

            report = audit_pose_finetune_readiness(
                [pack], governance_csv=governance
            )

            self.assertEqual("annotation_required", report["status"])
            self.assertFalse(report["readiness"]["dataset_export_allowed"])
            self.assertIn(
                "compiled validation status must be "
                "ready_for_keypoint_error_evaluation",
                report["packs"][0]["issues"],
            )

    def test_subject_split_leakage_requires_governance_repair(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first_pack = _build_pack(root, video_id="video-a")
            second_pack = _build_pack(root, video_id="video-b")
            governance = root / "governance.csv"
            _write_governance(
                governance,
                [
                    _governance_row(
                        "video-a", split="train", subject_id="same-subject"
                    ),
                    _governance_row(
                        "video-b", split="test", subject_id="same-subject"
                    ),
                ],
            )

            report = audit_pose_finetune_readiness(
                [first_pack, second_pack], governance_csv=governance
            )

            self.assertEqual("governance_required", report["status"])
            self.assertFalse(
                report["readiness"]["subject_session_leakage_free"]
            )
            self.assertIn(
                "subject_id spans splits: same-subject",
                report["governance"]["issues"],
            )

    def test_source_path_and_hash_alias_cannot_cross_train_val(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            train = _build_pack(root, video_id="video-a")
            validation = _build_pack(root, video_id="video-b")
            validation_manifest_path = validation / "manifest.json"
            validation_manifest = json.loads(
                validation_manifest_path.read_text(encoding="utf-8")
            )
            validation_manifest["sources"]["video"] = _binding(root / "video-a.mp4")
            _write_json(validation_manifest_path, validation_manifest)
            validation_report_path = validation / "compiled" / "validation-report.json"
            validation_report = json.loads(
                validation_report_path.read_text(encoding="utf-8")
            )
            validation_report["sources"]["manifest"] = _binding(
                validation_manifest_path
            )
            _write_json(validation_report_path, validation_report)
            governance = root / "governance.csv"
            _write_governance(
                governance,
                [
                    _governance_row("video-a", split="train"),
                    _governance_row("video-b", split="val"),
                ],
            )

            report = audit_pose_finetune_readiness(
                [train, validation], governance_csv=governance
            )

            self.assertEqual("governance_required", report["status"])
            self.assertFalse(report["readiness"]["dataset_export_allowed"])
            self.assertFalse(
                report["readiness"][
                    "source_path_sha_cross_split_leakage_free"
                ]
            )
            self.assertEqual(
                {"resolved_path", "sha256"},
                {
                    group["dimension"]
                    for group in report["source_alias_cross_split_groups"]
                },
            )

    def test_sealed_holdout_id_is_rejected_before_source_is_opened(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            holdout_id = "c235227fffcd3290b60572d0c3f9cc85"
            pack = _build_pack(root, video_id=holdout_id)
            governance = root / "governance.csv"
            _write_governance(governance, [_governance_row(holdout_id)])

            report = audit_pose_finetune_readiness(
                [pack], governance_csv=governance
            )

            self.assertEqual("annotation_required", report["status"])
            self.assertEqual(
                M95_SHADOW_REGISTRY_SHA256,
                report["sealed_holdout_guard"]["registry_sha256"],
            )
            source = report["packs"][0]["source_videos"][0]
            self.assertTrue(source["sealed_holdout_rejected"])
            self.assertIsNone(source["observed_sha256"])
            self.assertTrue(
                any("sealed holdout source is forbidden" in item for item in report["blockers"])
            )

    def test_sealed_holdout_path_and_declared_sha_aliases_are_rejected(self) -> None:
        guard = load_m95_sealed_holdout_guard()
        for alias_dimension in ("path", "sha256"):
            with self.subTest(alias_dimension=alias_dimension), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                pack = _build_pack(root, video_id=f"dev-{alias_dimension}")
                manifest_path = pack / "manifest.json"
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                if alias_dimension == "path":
                    manifest["sources"]["video"]["path"] = guard["video_path"]
                else:
                    manifest["sources"]["video"]["sha256"] = guard["video_sha256"]
                _write_json(manifest_path, manifest)
                validation_path = pack / "compiled" / "validation-report.json"
                validation = json.loads(validation_path.read_text(encoding="utf-8"))
                validation["sources"]["manifest"] = _binding(manifest_path)
                _write_json(validation_path, validation)
                governance = root / "governance.csv"
                _write_governance(
                    governance, [_governance_row(f"dev-{alias_dimension}")]
                )

                report = audit_pose_finetune_readiness(
                    [pack], governance_csv=governance
                )

                source = report["packs"][0]["source_videos"][0]
                self.assertEqual("annotation_required", report["status"])
                self.assertTrue(source["sealed_holdout_rejected"])
                self.assertIsNone(source["observed_sha256"])

    def test_shadow_registry_hash_drift_stops_the_audit(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            pack = _build_pack(Path(temp))
            with patch(
                "rallymate_training.pose_finetune_readiness.M95_SHADOW_REGISTRY_SHA256",
                "0" * 64,
            ), self.assertRaisesRegex(
                PoseFineTuneReadinessError, "registry SHA-256 drift"
            ):
                audit_pose_finetune_readiness([pack])

    def test_train_split_itself_must_cover_every_visible_halpe26_joint(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            train = _build_pack(root, video_id="video-a", joints=("nose",))
            validation = _build_pack(root, video_id="video-b")
            governance = root / "governance.csv"
            _write_governance(
                governance,
                [
                    _governance_row("video-a", split="train"),
                    _governance_row("video-b", split="val"),
                ],
            )

            report = audit_pose_finetune_readiness(
                [train, validation], governance_csv=governance
            )
            self.assertTrue(report["readiness"]["all_halpe26_joints_supervised"])
            self.assertEqual("annotation_required", report["status"])
            self.assertFalse(
                report["readiness"]["train_split_all_halpe26_joints_supervised"]
            )
            self.assertFalse(report["readiness"]["dataset_export_allowed"])
            self.assertEqual(25, sum(
                row["visible_with_coordinates"] == 0
                for row in report["train_halpe26_supervision"]
            ))


if __name__ == "__main__":
    unittest.main()
