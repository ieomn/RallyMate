from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from rallymate_evaluation.small_roi_keypoint_truth import (
    ADJUDICATION_FIELDS,
    ANNOTATION_FIELDS,
    JOINTS,
    SmallRoiTruthError,
    build_small_roi_truth_pack,
    compile_small_roi_truth_pack,
    evaluate_small_roi_keypoints,
    sha256_file,
)


ROOT = Path(__file__).resolve().parents[1]


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def _write_csv(path: Path, fields: tuple[str, ...], rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields))
        writer.writeheader()
        writer.writerows(rows)


def _frame(index: int) -> dict:
    keypoints = [
        {
            "index": joint_index,
            "name": joint,
            "x_px": 50.0,
            "y_px": 50.0,
            "x_normalized": 0.5,
            "y_normalized": 0.5,
            "confidence": 0.9,
            "in_frame": True,
        }
        for joint_index, joint in enumerate(JOINTS)
    ]
    return {
        "schema_version": "1.0.0",
        "frame": {
            "processed_index": index,
            "index": index,
            "source_frame_index": index,
            "timestamp_ms": index * 40,
            "width": 100,
            "height": 100,
        },
        "detections": [
            {
                "class_name": "player",
                "track_id": 7,
                "bbox_px": [40.0, 35.0, 60.0 + index * 4.0, 65.0],
            }
        ],
        "poses": [
            {
                "person_track_id": 7,
                "keypoint_format": "halpe26",
                "keypoints": keypoints,
            }
        ],
    }


class SmallRoiKeypointTruthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.frames_path = self.root / "frames.jsonl"
        _write_jsonl(self.frames_path, [_frame(0), _frame(1)])
        self.timeline_path = self.root / "timeline.jsonl"
        _write_jsonl(
            self.timeline_path,
            [
                {
                    "processed_index": index,
                    "source_frame_index": index,
                    "source_track_id": 7,
                }
                for index in range(2)
            ],
        )
        self.video_path = self.root / "source.mp4"
        self.video_path.write_bytes(b"synthetic-video-contract-test")
        self.comparison_path = self.root / "comparison.mp4"
        self.comparison_path.write_bytes(b"synthetic-comparison-contract-test")
        self.gap_path = self.root / "gap.json"
        _write_json(
            self.gap_path,
            {
                "event_failures": [
                    {"event_id": "event-1", "start_ms": 0, "end_ms": 80}
                ]
            },
        )
        self.report_path = self.root / "experiment.json"
        _write_json(
            self.report_path,
            {
                "schema_version": "1.0.0",
                "experiment_version": "small-roi-pose-recovery-v1.0.0",
                "status": "experimental_observability_only_not_production",
                "inference": {"recovered_processed_indices": [0, 1]},
                "artifacts": {
                    "experimental_frames": {"sha256": sha256_file(self.frames_path)}
                },
                "safety": {"production_enabled": False, "accuracy_claim": False},
            },
        )
        self.pack = self.root / "pack"
        build_small_roi_truth_pack(
            experiment_report_path=self.report_path,
            experimental_frames_path=self.frames_path,
            primary_timeline_path=self.timeline_path,
            video_path=self.video_path,
            gap_audit_path=self.gap_path,
            comparison_video_path=self.comparison_path,
            output_dir=self.pack,
            asset_directory=ROOT / "src" / "rallymate_annotation" / "assets",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _complete_annotations(self, *, reviewer_id: str = "reviewer-c") -> None:
        tasks = [
            json.loads(line)
            for line in (self.pack / "tasks.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        annotations = []
        adjudications = []
        for task in tasks:
            task_id = task["task_id"]
            source_ids = []
            for annotator_id in ("annotator-a", "annotator-b"):
                annotation_id = f"{annotator_id}:{task_id}"
                source_ids.append(annotation_id)
                annotations.append(
                    {
                        "annotation_id": annotation_id,
                        "task_id": task_id,
                        "annotator_id": annotator_id,
                        "visible": "true",
                        "x_normalized": "0.5",
                        "y_normalized": "0.5",
                        "visibility_reason": "",
                        "annotated_at": "2026-08-21T00:00:00Z",
                    }
                )
            adjudications.append(
                {
                    "adjudication_id": f"decision:{task_id}",
                    "task_id": task_id,
                    "source_annotation_ids": ";".join(source_ids),
                    "reviewer_id": reviewer_id,
                    "visible": "true",
                    "x_normalized": "0.5",
                    "y_normalized": "0.5",
                    "visibility_reason": "",
                    "adjudicated_at": "2026-08-21T01:00:00Z",
                    "status": "accepted",
                }
            )
        _write_csv(self.pack / "annotations.csv", ANNOTATION_FIELDS, annotations)
        _write_csv(self.pack / "adjudications.csv", ADJUDICATION_FIELDS, adjudications)

    def test_blank_pack_is_blind_and_does_not_report_accuracy(self) -> None:
        manifest = json.loads((self.pack / "manifest.json").read_text(encoding="utf-8"))
        validation = json.loads(
            (self.pack / "compiled" / "validation-report.json").read_text(encoding="utf-8")
        )
        report = evaluate_small_roi_keypoints(self.pack)
        html = (self.pack / "review.html").read_text(encoding="utf-8")

        self.assertEqual(2, manifest["scope"]["frame_count"])
        self.assertEqual(2 * len(JOINTS), manifest["scope"]["joint_task_count"])
        self.assertFalse(manifest["safety"]["model_keypoints_embedded_in_annotation_ui"])
        self.assertNotIn('"x_px"', html)
        self.assertNotIn('"keypoints"', html)
        self.assertEqual("annotation_required", validation["status"])
        self.assertEqual("annotation_required", report["status"])
        self.assertIsNone(report["metrics"])
        self.assertFalse(report["routing_decision"]["switch_allowed"])

    def test_complete_independent_truth_produces_zero_error_metrics(self) -> None:
        self._complete_annotations()
        validation = compile_small_roi_truth_pack(self.pack)
        report = evaluate_small_roi_keypoints(self.pack)

        self.assertEqual("ready_for_keypoint_error_evaluation", validation["status"])
        self.assertEqual(2 * len(JOINTS), validation["counts"]["accepted_adjudications"])
        self.assertEqual(
            "evaluated_pending_external_acceptance_protocol", report["status"]
        )
        self.assertEqual(0.0, report["metrics"]["mean_euclidean_error_px"])
        self.assertEqual(0.0, report["metrics"]["p95_euclidean_error_px"])
        self.assertEqual(1.0, report["metrics"]["valid_rate"])
        self.assertIsNone(report["pck"]["threshold"])
        self.assertFalse(report["routing_decision"]["switch_allowed"])

    def test_reviewer_must_be_independent(self) -> None:
        self._complete_annotations(reviewer_id="annotator-a")
        validation = compile_small_roi_truth_pack(self.pack)

        self.assertEqual("invalid_annotations", validation["status"])
        self.assertTrue(
            any("reviewer must be independent" in error for error in validation["errors"])
        )
        with self.assertRaisesRegex(SmallRoiTruthError, "invalid annotations"):
            evaluate_small_roi_keypoints(self.pack)

    def test_immutable_task_tampering_is_rejected(self) -> None:
        with (self.pack / "tasks.jsonl").open("a", encoding="utf-8") as handle:
            handle.write("{}\n")
        validation = compile_small_roi_truth_pack(self.pack)
        self.assertEqual("invalid_annotations", validation["status"])
        self.assertTrue(any("hash mismatch" in error for error in validation["errors"]))

    def test_evaluator_rejects_csv_changed_after_compilation(self) -> None:
        with (self.pack / "annotations.csv").open("a", encoding="utf-8") as handle:
            handle.write("\n")
        with self.assertRaisesRegex(SmallRoiTruthError, "compiled source hash mismatch"):
            evaluate_small_roi_keypoints(self.pack)


if __name__ == "__main__":
    unittest.main()
