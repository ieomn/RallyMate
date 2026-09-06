from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from rallymate_evaluation.event_gap_keypoint_truth import (
    EventGapTruthError,
    build_event_gap_truth_pack,
    compile_event_gap_truth_pack,
    evaluate_event_gap_keypoints,
    validate_event_gap_truth_manifest,
)
from rallymate_evaluation.small_roi_keypoint_truth import (
    ADJUDICATION_FIELDS,
    ANNOTATION_FIELDS,
    _canonical_sha256,
    sha256_file,
)


ROOT = Path(__file__).resolve().parents[1]
M74_REPORT = (
    ROOT
    / "reports"
    / "measurement-recovery-m74"
    / "event-bounded-gap-audit-v1"
    / "report.json"
)
ASSETS = ROOT / "src" / "rallymate_annotation" / "assets"
REAL_PACK = ROOT / "data" / "annotations" / "event-bounded-pose-gap-truth-m75-v1"


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _write_csv(path: Path, fields: tuple[str, ...], rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields))
        writer.writeheader()
        writer.writerows(rows)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def _build_temp_pack(root: Path) -> Path:
    pack = root / "pack"
    build_event_gap_truth_pack(
        m74_report_path=M74_REPORT,
        output_dir=pack,
        asset_directory=ASSETS,
    )
    return pack


def _complete_with_synthetic_prediction_coordinates(
    pack: Path, *, reviewer_id: str = "synthetic-reviewer-c"
) -> None:
    """Contract-only fixture; these rows are never a real truth artifact."""

    predictions = {row["task_id"]: row for row in _jsonl(pack / "sealed-interpolation-predictions.jsonl")}
    annotations: list[dict] = []
    adjudications: list[dict] = []
    for task_id, prediction in sorted(predictions.items()):
        source_ids = []
        for annotator_id in ("synthetic-annotator-a", "synthetic-annotator-b"):
            annotation_id = f"{annotator_id}:{task_id}"
            source_ids.append(annotation_id)
            annotations.append(
                {
                    "annotation_id": annotation_id,
                    "task_id": task_id,
                    "annotator_id": annotator_id,
                    "visible": "true",
                    "x_normalized": str(prediction["x_normalized"]),
                    "y_normalized": str(prediction["y_normalized"]),
                    "visibility_reason": "",
                    "annotated_at": "2026-08-22T00:00:00Z",
                }
            )
        adjudications.append(
            {
                "adjudication_id": f"synthetic-decision:{task_id}",
                "task_id": task_id,
                "source_annotation_ids": ";".join(source_ids),
                "reviewer_id": reviewer_id,
                "visible": "true",
                "x_normalized": str(prediction["x_normalized"]),
                "y_normalized": str(prediction["y_normalized"]),
                "visibility_reason": "",
                "adjudicated_at": "2026-08-22T01:00:00Z",
                "status": "accepted",
            }
        )
    _write_csv(pack / "annotations.csv", ANNOTATION_FIELDS, annotations)
    _write_csv(pack / "adjudications.csv", ADJUDICATION_FIELDS, adjudications)


class EventGapKeypointTruthTests(unittest.TestCase):
    def test_real_blank_pack_is_blind_and_fail_closed(self) -> None:
        manifest = json.loads((REAL_PACK / "manifest.json").read_text(encoding="utf-8"))
        validation = json.loads(
            (REAL_PACK / "compiled" / "validation-report.json").read_text(encoding="utf-8")
        )
        report = evaluate_event_gap_keypoints(REAL_PACK)
        html = (REAL_PACK / "review.html").read_text(encoding="utf-8")
        bootstrap = json.loads(
            html.split(
                '<script id="event-gap-truth-bootstrap" type="application/json">', 1
            )[1].split("</script>", 1)[0]
        )
        tasks = _jsonl(REAL_PACK / "tasks.jsonl")

        validate_event_gap_truth_manifest(manifest)
        self.assertEqual(2, manifest["scope"]["video_count"])
        self.assertEqual(54, manifest["scope"]["frame_count"])
        self.assertEqual(142, manifest["scope"]["joint_task_count"])
        self.assertEqual(142, len(tasks))
        self.assertNotIn("sealed-interpolation-predictions.jsonl", html)
        self.assertTrue(
            all(
                "effective_confidence" not in task
                and "x_normalized" not in task
                and "y_normalized" not in task
                for task in bootstrap["tasks"]
            )
        )
        self.assertFalse(manifest["safety"]["prediction_coordinates_embedded_in_annotation_ui"])
        self.assertEqual("annotation_required", validation["status"])
        self.assertEqual(0, validation["counts"]["accepted_adjudications"])
        self.assertEqual("annotation_required", report["status"])
        self.assertIsNone(report["metrics"])
        self.assertFalse(report["acceptance"]["production_interpolation_allowed"])
        self.assertTrue(
            any(
                task["annotation_bbox_provenance"]["method"]
                == "nearest_two_sided_primary_timeline_bbox_union_for_annotation_view_only"
                for task in tasks
            )
        )

    def test_synthetic_completed_contract_computes_zero_error_without_promotion(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            pack = _build_temp_pack(Path(temp))
            _complete_with_synthetic_prediction_coordinates(pack)
            validation = compile_event_gap_truth_pack(pack)
            report = evaluate_event_gap_keypoints(pack)

            self.assertEqual("ready_for_interpolation_error_evaluation", validation["status"])
            self.assertEqual(284, validation["counts"]["raw_annotations"])
            self.assertEqual(142, validation["counts"]["accepted_adjudications"])
            self.assertEqual("evaluated_external_acceptance_protocol_required", report["status"])
            self.assertEqual(142, report["counts"]["accepted_joint_truth"])
            self.assertEqual(0.0, report["metrics"]["mae_euclidean_px"])
            self.assertEqual(0.0, report["metrics"]["p95_euclidean_px"])
            self.assertEqual(0.0, report["metrics"]["bias_x_px"])
            self.assertEqual(0.0, report["metrics"]["bias_y_px"])
            self.assertEqual(1.0, report["metrics"]["valid_rate"])
            self.assertEqual(13, len(report["per_joint"]))
            self.assertEqual(2, len(report["per_view_group"]))
            self.assertIsNone(report["acceptance"]["thresholds"])
            self.assertFalse(report["acceptance"]["production_interpolation_allowed"])
            self.assertFalse(report["safety"]["maturity_promoted"])

    def test_sealed_prediction_tampering_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            pack = _build_temp_pack(Path(temp))
            with (pack / "sealed-interpolation-predictions.jsonl").open("a", encoding="utf-8") as handle:
                handle.write("{}\n")
            validation = compile_event_gap_truth_pack(pack)

            self.assertEqual("invalid_annotations", validation["status"])
            self.assertTrue(any("SHA mismatch" in error for error in validation["errors"]))
            with self.assertRaisesRegex(EventGapTruthError, "immutable artifact SHA mismatch"):
                evaluate_event_gap_keypoints(pack)

    def test_rehashed_prediction_tampering_still_fails_m74_source_replay(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            pack = _build_temp_pack(Path(temp))
            predictions = _jsonl(pack / "sealed-interpolation-predictions.jsonl")
            predictions[0]["x_normalized"] += 0.01
            prediction_path = pack / "sealed-interpolation-predictions.jsonl"
            _write_jsonl(prediction_path, predictions)
            manifest_path = pack / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["prediction_contract_sha256"] = _canonical_sha256(predictions)
            manifest["artifacts"]["sealed-interpolation-predictions.jsonl"]["sha256"] = (
                sha256_file(prediction_path)
            )
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                encoding="utf-8",
            )

            validation = compile_event_gap_truth_pack(pack)
            self.assertEqual("invalid_annotations", validation["status"])
            self.assertTrue(
                any("does not replay bound M74 evidence" in error for error in validation["errors"])
            )

    def test_reviewer_must_be_independent(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            pack = _build_temp_pack(Path(temp))
            _complete_with_synthetic_prediction_coordinates(
                pack, reviewer_id="synthetic-annotator-a"
            )
            validation = compile_event_gap_truth_pack(pack)

            self.assertEqual("invalid_annotations", validation["status"])
            self.assertTrue(
                any("reviewer must be independent" in error for error in validation["errors"])
            )

    def test_output_directory_is_immutable(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pack = _build_temp_pack(root)
            manifest_before = (pack / "manifest.json").read_bytes()
            with self.assertRaisesRegex(EventGapTruthError, "already exists"):
                build_event_gap_truth_pack(
                    m74_report_path=M74_REPORT,
                    output_dir=pack,
                    asset_directory=ASSETS,
                )
            self.assertEqual(manifest_before, (pack / "manifest.json").read_bytes())


if __name__ == "__main__":
    unittest.main()
