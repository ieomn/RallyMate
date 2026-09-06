from __future__ import annotations

import csv
import hashlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from rallymate_evaluation.pose_diagnostic_review import (
    build_pose_diagnostic_review_queue,
)
from rallymate_evaluation.pose_diagnostic_truth import (
    DIAGNOSTIC_TYPES,
    PoseDiagnosticTruthError,
    coverage_csv_header,
    evaluate_pose_diagnostic_truth,
    positives_csv_header,
    validate_pose_diagnostic_evaluation,
    validate_pose_diagnostic_truth_pack_sources,
    write_blank_pose_diagnostic_truth_pack,
)


ROOT = Path(__file__).resolve().parents[1]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _canonical_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest().upper()


def _csv_text(headers: list[str], rows: list[dict[str, object]]) -> str:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=headers, lineterminator="\r\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


class PoseDiagnosticTruthTests(unittest.TestCase):
    def _source_queue(self, directory: Path, *, include_ambiguity: bool = False) -> Path:
        frames = []
        timeline = []
        for index in range(3):
            track_id = 1 if index < 2 else 2
            frames.append(
                {
                    "frame": {
                        "index": index,
                        "processed_index": index,
                        "timestamp_ms": index * 40,
                    },
                    "poses": [
                        {
                            "person_track_id": track_id,
                            "keypoints": [
                                {
                                    "name": "left_hip",
                                    "x_normalized": 0.4,
                                    "y_normalized": 0.6,
                                    "confidence": 0.9,
                                },
                                {
                                    "name": "left_wrist",
                                    "x_normalized": 0.3,
                                    "y_normalized": 0.5,
                                    "confidence": 0.9,
                                },
                                {
                                    "name": "right_wrist",
                                    "x_normalized": 0.7,
                                    "y_normalized": 0.5,
                                    "confidence": 0.9,
                                },
                            ],
                        }
                    ],
                }
            )
            timeline.append(
                {
                    "processed_index": index,
                    "source_frame_index": index,
                    "timestamp_ms": index * 40,
                    "selection_status": "selected",
                    "source_track_id": track_id,
                }
            )
        event = {
            "event_id": "event-1",
            "event_code": "FS01",
            "start_ms": 0,
            "end_ms": 80,
            "track_diagnostics": {
                "keypoint_jump_candidate_frames_by_joint": {"left_hip": [1]},
                "left_right_swap_candidate_joint_pairs": [
                    {
                        "left_joint": "left_wrist",
                        "right_joint": "right_wrist",
                        "frames": [2],
                    }
                ],
                "primary_identity_ambiguous_frames": [1] if include_ambiguity else [],
                "source_track_switch_candidate_count": 1,
            },
        }
        score = {
            "event_id": "event-1",
            "event_code": "FS01",
            "indicator_id": "FS01-M02",
            "feature": {"items": [{"feature_name": "hip_center_y_body"}]},
            "quality_gate": {
                "input_quality_flags": [
                    "keypoint_jump_candidates_present",
                    "left_right_swap_candidates_present",
                    "source_track_switch_candidates_present",
                ],
                "scoring_block_flags": [
                    "keypoint_jump_candidates_present",
                    "left_right_swap_candidates_present",
                    "source_track_switch_candidates_present",
                ],
            },
        }
        artifacts_data = {
            "frames_jsonl": frames,
            "primary_player_jsonl": timeline,
            "events_jsonl": [event],
            "scores_jsonl": [score],
            "summary_json": {"loop_version": "minimum-scoring-loop-v-test"},
        }
        bindings = {}
        for name, value in artifacts_data.items():
            suffix = ".json" if name == "summary_json" else ".jsonl"
            path = directory / f"{name}{suffix}"
            if suffix == ".jsonl":
                path.write_text(
                    "".join(
                        json.dumps(item, separators=(",", ":")) + "\n"
                        for item in value
                    ),
                    encoding="utf-8",
                )
            else:
                path.write_text(json.dumps(value), encoding="utf-8")
            bindings[name] = {"path": str(path), "sha256": _sha(path)}
        video = directory / "source.mp4"
        media = directory / "review.mp4"
        video.write_bytes(b"synthetic-source-video")
        media.write_bytes(b"synthetic-review-video")
        source = {
            "run_directory": str(directory),
            "scoring_loop_version": "minimum-scoring-loop-v-test",
            "pose_model": {"primary_player": "primary-player-v-test"},
            "source_video_path": str(video),
            "source_video_sha256": _sha(video),
            "artifacts": bindings,
            "artifact_binding_sha256": _canonical_hash(bindings),
        }
        queue = build_pose_diagnostic_review_queue(
            video_id="video-test",
            events=[event],
            scores=[score],
            frames=frames,
            primary_timeline=timeline,
            source=source,
            review_media={
                "path": str(media),
                "sha256": _sha(media),
                "video_time_offset_ms": 0,
                "semantics": "synthetic review media",
            },
            generated_at="2026-08-21T00:00:00Z",
        )
        queue_path = directory / "queue.json"
        queue_path.write_text(
            json.dumps(queue, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return queue_path

    def _blank_pack(
        self, directory: Path, *, include_ambiguity: bool = False
    ) -> tuple[dict, Path]:
        queue_path = self._source_queue(
            directory, include_ambiguity=include_ambiguity
        )
        output = directory / "truth-pack"
        manifest = write_blank_pose_diagnostic_truth_pack(
            queue_path=queue_path,
            output_dir=output,
            generated_at="2026-08-21T00:00:00Z",
        )
        return manifest, output

    def _coverage(self, *, accepted_types: set[str]) -> str:
        rows = []
        for index, diagnostic_type in enumerate(DIAGNOSTIC_TYPES):
            accepted = diagnostic_type in accepted_types
            rows.append(
                {
                    "coverage_id": f"coverage-{index + 1}",
                    "video_id": "video-test",
                    "diagnostic_type": diagnostic_type,
                    "start_source_frame_index": 0,
                    "end_source_frame_index": 2,
                    "coverage_scope": "all_model_relevant_scopes",
                    "review_status": "accepted" if accepted else "pending",
                    "annotator_ids": "coach-a;coach-b" if accepted else "",
                    "adjudicator_id": "reviewer-c" if accepted else "",
                    "adjudicated_at": "2026-08-21T01:00:00Z" if accepted else "",
                    "null_reason": "",
                    "notes": "synthetic contract truth" if accepted else "",
                }
            )
        return _csv_text(coverage_csv_header(), rows)

    def _positives(self) -> str:
        common = {
            "video_id": "video-test",
            "from_source_track_id": "",
            "to_source_track_id": "",
            "review_status": "accepted",
            "annotator_ids": "coach-a;coach-b",
            "adjudicator_id": "reviewer-c",
            "adjudicated_at": "2026-08-21T01:00:00Z",
            "notes": "synthetic contract truth",
        }
        rows = [
            {
                **common,
                "truth_id": "truth-jump",
                "diagnostic_type": "keypoint_jump",
                "source_frame_index": 1,
                "joint": "left_hip",
                "left_joint": "",
                "right_joint": "",
            },
            {
                **common,
                "truth_id": "truth-swap-missed",
                "diagnostic_type": "left_right_swap",
                "source_frame_index": 0,
                "joint": "",
                "left_joint": "left_wrist",
                "right_joint": "right_wrist",
            },
            {
                **common,
                "truth_id": "truth-ambiguity-missed",
                "diagnostic_type": "primary_identity_ambiguity",
                "source_frame_index": 1,
                "joint": "",
                "left_joint": "",
                "right_joint": "",
            },
            {
                **common,
                "truth_id": "truth-switch",
                "diagnostic_type": "source_track_switch",
                "source_frame_index": 2,
                "joint": "",
                "left_joint": "",
                "right_joint": "",
                "from_source_track_id": 1,
                "to_source_track_id": 2,
            },
        ]
        return _csv_text(positives_csv_header(), rows)

    def test_blank_pack_is_video_first_and_cannot_claim_truth(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest, output = self._blank_pack(Path(temporary))
            self.assertEqual(manifest["status"], "annotation_required")
            self.assertEqual(manifest["frame_domain"]["frame_count"], 3)
            self.assertFalse(manifest["safety"]["manual_truth_present"])
            self.assertEqual(
                (output / "pose-diagnostic-coverage.csv")
                .read_text(encoding="utf-8")
                .splitlines()[0],
                ",".join(coverage_csv_header()),
            )
            rendered = (output / "index.html").read_text(encoding="utf-8")
            self.assertIn('<video id="video"', rendered)
            self.assertIn("导出 coverage.csv", rendered)
            self.assertIn("导出 positives.csv", rendered)
            self.assertIn("不是真值", rendered)
            self.assertIn("join('\\r\\n')", rendered)
            self.assertNotIn("join('\r\n')", rendered)
            self.assertNotIn("threshold_version", rendered)
            queue, timeline = validate_pose_diagnostic_truth_pack_sources(manifest)
            self.assertEqual(queue["video_id"], "video-test")
            self.assertEqual(len(timeline), 3)

    def test_blank_pack_overwrite_refuses_changed_annotation_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            manifest, output = self._blank_pack(directory)
            with self.assertRaisesRegex(PoseDiagnosticTruthError, "non-empty"):
                write_blank_pose_diagnostic_truth_pack(
                    queue_path=Path(manifest["source_queue"]["path"]),
                    output_dir=output,
                )
            coverage = Path(manifest["annotation_files"]["coverage"]["path"])
            coverage.write_text(
                coverage.read_text(encoding="utf-8") + "# human edit\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(PoseDiagnosticTruthError, "annotation template changed"):
                write_blank_pose_diagnostic_truth_pack(
                    queue_path=Path(manifest["source_queue"]["path"]),
                    output_dir=output,
                    overwrite_unchanged_blank=True,
                )

    def test_blank_candidate_review_does_not_create_recall_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest, output = self._blank_pack(Path(temporary))
            report = evaluate_pose_diagnostic_truth(
                manifest=manifest,
                coverage_csv_text=(output / "pose-diagnostic-coverage.csv").read_text(
                    encoding="utf-8"
                ),
                positives_csv_text=(output / "pose-diagnostic-positives.csv").read_text(
                    encoding="utf-8"
                ),
                generated_at="2026-08-21T02:00:00Z",
            )
            self.assertEqual(report["status"], "annotation_required")
            self.assertTrue(
                all(
                    item["recall"] is None
                    for item in report["by_diagnostic_type"].values()
                )
            )
            self.assertFalse(
                report["safety"]["candidate_only_review_used_as_recall_truth"]
            )

    def test_full_timeline_truth_computes_exact_precision_recall_and_f1(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest, _ = self._blank_pack(Path(temporary))
            report = evaluate_pose_diagnostic_truth(
                manifest=manifest,
                coverage_csv_text=self._coverage(accepted_types=set(DIAGNOSTIC_TYPES)),
                positives_csv_text=self._positives(),
                generated_at="2026-08-21T02:00:00Z",
            )
            self.assertEqual(report["status"], "evaluated_full_timeline")
            jump = report["by_diagnostic_type"]["keypoint_jump"]
            self.assertEqual((jump["true_positive"], jump["false_positive"], jump["false_negative"]), (1, 0, 0))
            self.assertEqual((jump["precision"], jump["recall"], jump["f1"]), (1.0, 1.0, 1.0))
            swap = report["by_diagnostic_type"]["left_right_swap"]
            self.assertEqual((swap["true_positive"], swap["false_positive"], swap["false_negative"]), (0, 1, 1))
            self.assertEqual((swap["precision"], swap["recall"], swap["f1"]), (0.0, 0.0, 0.0))
            ambiguity = report["by_diagnostic_type"]["primary_identity_ambiguity"]
            self.assertIsNone(ambiguity["precision"])
            self.assertEqual(ambiguity["recall"], 0.0)
            self.assertIsNone(ambiguity["f1"])
            validate_pose_diagnostic_evaluation(report)
            self.assertFalse(report["safety"]["quality_gate_modified"])

    def test_validator_recomputes_metrics_instead_of_trusting_reported_rates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest, _ = self._blank_pack(Path(temporary))
            report = evaluate_pose_diagnostic_truth(
                manifest=manifest,
                coverage_csv_text=self._coverage(accepted_types=set(DIAGNOSTIC_TYPES)),
                positives_csv_text=self._positives(),
            )
            forged = json.loads(json.dumps(report))
            forged["by_diagnostic_type"]["left_right_swap"]["precision"] = 1.0
            with self.assertRaisesRegex(PoseDiagnosticTruthError, "does not match"):
                validate_pose_diagnostic_evaluation(forged)
            forged = json.loads(json.dumps(report))
            forged["micro_average_on_accepted_coverage"]["false_negative"] = 0
            with self.assertRaisesRegex(PoseDiagnosticTruthError, "micro count"):
                validate_pose_diagnostic_evaluation(forged)

    def test_partial_coverage_is_explicitly_not_full_timeline_recall(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest, _ = self._blank_pack(Path(temporary))
            positives = _csv_text(positives_csv_header(), [])
            report = evaluate_pose_diagnostic_truth(
                manifest=manifest,
                coverage_csv_text=self._coverage(accepted_types={"keypoint_jump"}),
                positives_csv_text=positives,
            )
            self.assertEqual(report["status"], "partial_coverage")
            jump = report["by_diagnostic_type"]["keypoint_jump"]
            self.assertEqual(jump["coverage_status"], "full_timeline")
            self.assertTrue(jump["full_timeline_recall_ready"])
            self.assertEqual(
                report["by_diagnostic_type"]["left_right_swap"]["coverage_status"],
                "missing",
            )
            self.assertFalse(report["micro_average_on_accepted_coverage"]["full_timeline"])

    def test_truth_outside_accepted_coverage_and_weak_review_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest, _ = self._blank_pack(Path(temporary))
            with self.assertRaisesRegex(PoseDiagnosticTruthError, "outside accepted coverage"):
                evaluate_pose_diagnostic_truth(
                    manifest=manifest,
                    coverage_csv_text=self._coverage(accepted_types={"keypoint_jump"}),
                    positives_csv_text=self._positives(),
                )
            weak = self._coverage(accepted_types={"keypoint_jump"}).replace(
                "coach-a;coach-b", "coach-a"
            )
            with self.assertRaisesRegex(PoseDiagnosticTruthError, "two unique"):
                evaluate_pose_diagnostic_truth(
                    manifest=manifest,
                    coverage_csv_text=weak,
                    positives_csv_text=_csv_text(positives_csv_header(), []),
                )

    def test_tampered_queue_binding_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest, _ = self._blank_pack(Path(temporary))
            queue_path = Path(manifest["source_queue"]["path"])
            queue_path.write_text(queue_path.read_text(encoding="utf-8") + " ", encoding="utf-8")
            with self.assertRaisesRegex(PoseDiagnosticTruthError, "source queue SHA mismatch"):
                evaluate_pose_diagnostic_truth(
                    manifest=manifest,
                    coverage_csv_text=self._coverage(accepted_types=set()),
                    positives_csv_text=_csv_text(positives_csv_header(), []),
                )

    def test_evaluation_binds_manifest_and_exact_input_file_content(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest, output = self._blank_pack(Path(temporary))
            coverage_path = output / "pose-diagnostic-coverage.csv"
            positives_path = output / "pose-diagnostic-positives.csv"
            coverage_text = coverage_path.read_text(encoding="utf-8")
            positives_text = positives_path.read_text(encoding="utf-8")
            report = evaluate_pose_diagnostic_truth(
                manifest=manifest,
                coverage_csv_text=coverage_text,
                positives_csv_text=positives_text,
                coverage_path=coverage_path,
                positives_path=positives_path,
            )
            self.assertRegex(
                report["source"]["truth_pack_manifest_content_sha256"],
                r"^[0-9A-F]{64}$",
            )
            with self.assertRaisesRegex(PoseDiagnosticTruthError, "differs"):
                evaluate_pose_diagnostic_truth(
                    manifest=manifest,
                    coverage_csv_text=coverage_text + " ",
                    positives_csv_text=positives_text,
                    coverage_path=coverage_path,
                    positives_path=positives_path,
                )

    def test_cli_and_machine_schemas(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            queue = self._source_queue(directory)
            output = directory / "pack"
            build = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "build_pose_diagnostic_truth_pack.py"),
                    "--queue",
                    str(queue),
                    "--output-dir",
                    str(output),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(build.returncode, 0, build.stderr)
            report_path = directory / "evaluation.json"
            evaluate = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "evaluate_pose_diagnostic_truth.py"),
                    "--manifest",
                    str(output / "manifest.json"),
                    "--output",
                    str(report_path),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(evaluate.returncode, 0, evaluate.stderr)
            self.assertEqual(
                json.loads(report_path.read_text(encoding="utf-8"))["status"],
                "annotation_required",
            )
            for schema_name in (
                "pose-diagnostic-truth-pack.schema.json",
                "pose-diagnostic-evaluation.schema.json",
            ):
                schema = json.loads(
                    (ROOT / "contracts" / schema_name).read_text(encoding="utf-8")
                )
                self.assertEqual(
                    schema["$schema"],
                    "https://json-schema.org/draft/2020-12/schema",
                )
                self.assertFalse(schema["additionalProperties"])


if __name__ == "__main__":
    unittest.main()
