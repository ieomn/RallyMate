from __future__ import annotations

import csv
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from rallymate_evaluation.pose_diagnostic_review import (
    PoseDiagnosticReviewError,
    build_pose_diagnostic_review_queue,
    build_pose_diagnostic_review_queue_from_run,
    decisions_csv_header,
    render_pose_diagnostic_review_html,
    validate_decisions_csv,
    validate_pose_diagnostic_review_queue,
    validate_pose_diagnostic_review_queue_sources,
)
ROOT = Path(__file__).resolve().parents[1]


def _keypoint(name: str, x: float, y: float) -> dict:
    return {
        "name": name,
        "x_normalized": x,
        "y_normalized": y,
        "confidence": 0.9,
    }


def _frames() -> list[dict]:
    records = []
    for index in range(3):
        track_id = 1 if index < 2 else 2
        records.append(
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
                            _keypoint("left_hip", 0.4 + index * 0.1, 0.6),
                            _keypoint("right_hip", 0.6, 0.6),
                            _keypoint("left_wrist", 0.3, 0.5),
                            _keypoint("right_wrist", 0.7, 0.5),
                        ],
                    }
                ],
            }
        )
    return records


def _timeline() -> list[dict]:
    return [
        {
            "processed_index": index,
            "source_frame_index": index,
            "timestamp_ms": index * 40,
            "selection_status": "selected",
            "source_track_id": 1 if index < 2 else 2,
        }
        for index in range(3)
    ]


def _event(event_id: str, event_code: str) -> dict:
    return {
        "event_id": event_id,
        "event_code": event_code,
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
            "primary_identity_ambiguous_frames": [],
            "source_track_switch_candidate_count": 1,
        },
    }


def _score(
    event_id: str,
    event_code: str,
    indicator_id: str,
    feature_name: str,
) -> dict:
    return {
        "event_id": event_id,
        "event_code": event_code,
        "indicator_id": indicator_id,
        "feature": {"items": [{"feature_name": feature_name}]},
        "quality_gate": {
            "input_quality_flags": [
                "keypoint_jump_candidates_present",
                "left_right_swap_candidates_outside_indicator_joints",
                "source_track_switch_candidates_present",
            ],
            "scoring_block_flags": [
                "keypoint_jump_candidates_present",
                "source_track_switch_candidates_present",
            ],
        },
    }


def _source() -> dict:
    artifact = {"path": "C:/audit/input.jsonl", "sha256": "A" * 64}
    return {
        "run_directory": "C:/audit/run",
        "scoring_loop_version": "minimum-scoring-loop-v0.4.0",
        "pose_model": {},
        "source_video_path": "C:/audit/video.mp4",
        "source_video_sha256": "B" * 64,
        "artifacts": {
            "frames_jsonl": artifact,
            "primary_player_jsonl": artifact,
            "events_jsonl": artifact,
            "scores_jsonl": artifact,
            "summary_json": artifact,
        },
        "artifact_binding_sha256": "C" * 64,
    }


class PoseDiagnosticReviewTests(unittest.TestCase):
    def _queue(self) -> dict:
        events = [_event("event-fs01", "FS01"), _event("event-fs09", "FS09")]
        scores = [
            _score(
                "event-fs01",
                "FS01",
                "FS01-M02",
                "hip_center_y_body",
            ),
            _score(
                "event-fs09",
                "FS09",
                "FS09-M01",
                "hip_center_speed_body_s",
            ),
        ]
        return build_pose_diagnostic_review_queue(
            video_id="video-test",
            events=events,
            scores=scores,
            frames=_frames(),
            primary_timeline=_timeline(),
            source=_source(),
            review_media={
                "path": "C:/audit/review.mp4",
                "sha256": "D" * 64,
                "video_time_offset_ms": 0,
                "semantics": "review only",
            },
            generated_at="2026-08-21T00:00:00Z",
        )

    def test_exact_frame_deduplication_and_indicator_impact(self) -> None:
        queue = self._queue()
        self.assertEqual(queue["status"], "review_required")
        self.assertEqual(queue["counts"]["tasks"], 3)
        self.assertEqual(
            queue["counts"]["by_diagnostic_type"],
            {
                "keypoint_jump": 1,
                "left_right_swap": 1,
                "source_track_switch": 1,
            },
        )
        jump = next(
            item for item in queue["tasks"] if item["diagnostic_type"] == "keypoint_jump"
        )
        self.assertEqual(jump["candidate_source_frame_index"], 1)
        self.assertEqual(len(jump["event_refs"]), 2)
        self.assertEqual(
            jump["affected_indicator_ids"], ["FS01-M02", "FS09-M01"]
        )
        self.assertEqual([item["role"] for item in jump["joint_observations"]], ["previous", "candidate", "next"])
        swap = next(
            item for item in queue["tasks"] if item["diagnostic_type"] == "left_right_swap"
        )
        self.assertEqual(swap["affected_indicator_ids"], [])
        self.assertEqual(
            swap["advisory_only_indicator_ids"], ["FS01-M02", "FS09-M01"]
        )
        transition = next(
            item for item in queue["tasks"] if item["diagnostic_type"] == "source_track_switch"
        )
        self.assertEqual(
            transition["track_transition"],
            {"from_source_track_id": 1, "to_source_track_id": 2},
        )

    def test_generated_queue_cannot_contain_truth_or_scoring_claims(self) -> None:
        queue = self._queue()
        unsafe = json.loads(json.dumps(queue))
        unsafe["tasks"][0]["review_status"] = "confirmed_issue"
        unsafe["tasks"][0]["decision"] = {"value": "confirmed_issue"}
        with self.assertRaisesRegex(PoseDiagnosticReviewError, "cannot contain"):
            validate_pose_diagnostic_review_queue(unsafe)
        unsafe = json.loads(json.dumps(queue))
        unsafe["safety"]["accuracy_claim"] = True
        with self.assertRaisesRegex(PoseDiagnosticReviewError, "unsafe"):
            validate_pose_diagnostic_review_queue(unsafe)

    def test_html_is_video_seekable_and_does_not_embed_absolute_workspace_paths(self) -> None:
        queue = self._queue()
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "report" / "index.html"
            rendered = render_pose_diagnostic_review_html(queue, output_path=output)
        self.assertIn('<video id="reviewVideo"', rendered)
        self.assertIn("review_media_time_ms", rendered)
        self.assertIn("导出复核 CSV", rendered)
        self.assertIn("join('\\r\\n')", rendered)
        self.assertNotIn("join('\r\n')", rendered)
        self.assertNotIn("C:/audit", rendered)
        self.assertNotIn("C:\\audit", rendered)
        self.assertNotIn('name="grade"', rendered.lower())
        self.assertNotIn("threshold_version", rendered)

    def test_decision_csv_requires_every_task_and_annotator(self) -> None:
        queue = self._queue()
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=decisions_csv_header())
        writer.writeheader()
        for task in queue["tasks"]:
            writer.writerow(
                {
                    "task_id": task["task_id"],
                    "diagnostic_type": task["diagnostic_type"],
                    "candidate_source_frame_index": task[
                        "candidate_source_frame_index"
                    ],
                    "timestamp_ms": task["timestamp_ms"],
                    "decision": "false_positive",
                    "annotator_id": "reviewer-1",
                    "reviewed_at": "2026-08-21T00:00:00Z",
                    "notes": "synthetic contract test",
                }
            )
        result = validate_decisions_csv(buffer.getvalue(), queue)
        self.assertEqual(result["status"], "review_complete_not_adjudicated")
        self.assertEqual(result["truth_status"], "not_adjudicated")
        no_annotator = buffer.getvalue().replace("reviewer-1", "")
        with self.assertRaisesRegex(PoseDiagnosticReviewError, "annotator_id"):
            validate_decisions_csv(no_annotator, queue)

    def test_current_real_bundle_builds_safe_deduplicated_queue(self) -> None:
        run = (
            ROOT
            / "reports"
            / "fs09-pose-wave-v2"
            / "850cb0006b406c7176eeda8d711cd065-halpe26-930-1530"
        )
        review_video = (
            ROOT
            / "reports"
            / "pose-scoring-ab"
            / "rtmpose-halpe26-full-body-26points-31s-51s-browser.mp4"
        )
        queue = build_pose_diagnostic_review_queue_from_run(
            run_dir=run,
            review_video_path=review_video,
            review_video_offset_ms=31_000,
            generated_at="2026-08-21T00:00:00Z",
        )
        self.assertGreater(queue["counts"]["tasks"], 0)
        self.assertLess(
            queue["counts"]["tasks"],
            queue["counts"]["task_indicator_links"],
        )
        source_summary = json.loads(
            (run / "scoring-loop-summary.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            queue["source"]["scoring_loop_version"], source_summary["loop_version"]
        )
        self.assertTrue(
            all(task["review_status"] == "pending" for task in queue["tasks"])
        )
        self.assertFalse(queue["safety"]["accuracy_claim"])
        source_validation = validate_pose_diagnostic_review_queue_sources(queue)
        self.assertEqual(source_validation["status"], "passed")
        self.assertEqual(source_validation["validated_artifacts"], 5)
        tampered = json.loads(json.dumps(queue))
        tampered["source"]["artifacts"]["events_jsonl"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(PoseDiagnosticReviewError, "SHA mismatch"):
            validate_pose_diagnostic_review_queue_sources(tampered)

    def test_cli_writes_json_and_video_html_without_overwriting(self) -> None:
        run = (
            ROOT
            / "reports"
            / "fs09-pose-wave-v2"
            / "850cb0006b406c7176eeda8d711cd065-halpe26-930-1530"
        )
        video = (
            ROOT
            / "reports"
            / "pose-scoring-ab"
            / "rtmpose-halpe26-full-body-26points-31s-51s-browser.mp4"
        )
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "queue.json"
            report = Path(temporary) / "index.html"
            command = [
                sys.executable,
                str(ROOT / "scripts" / "build_pose_diagnostic_review_queue.py"),
                "--run-dir",
                str(run),
                "--review-video",
                str(video),
                "--review-video-offset-ms",
                "31000",
                "--output",
                str(output),
                "--html-output",
                str(report),
            ]
            first = subprocess.run(
                command, cwd=ROOT, capture_output=True, text=True, check=False
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertTrue(output.is_file())
            self.assertTrue(report.is_file())
            second = subprocess.run(
                command, cwd=ROOT, capture_output=True, text=True, check=False
            )
            self.assertNotEqual(second.returncode, 0)
            self.assertIn("refusing to overwrite", second.stderr)

    def test_decision_validation_cli_emits_non_adjudicated_report(self) -> None:
        queue_path = (
            ROOT
            / "reports"
            / "pose-diagnostic-review"
            / "halpe26-same-window"
            / "queue.json"
        )
        queue = json.loads(queue_path.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            csv_path = directory / "decisions.csv"
            report_path = directory / "validation.json"
            with csv_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=decisions_csv_header())
                writer.writeheader()
                for task in queue["tasks"]:
                    writer.writerow(
                        {
                            "task_id": task["task_id"],
                            "diagnostic_type": task["diagnostic_type"],
                            "candidate_source_frame_index": task[
                                "candidate_source_frame_index"
                            ],
                            "timestamp_ms": task["timestamp_ms"],
                            "decision": "pending",
                            "annotator_id": "",
                            "reviewed_at": "",
                            "notes": "",
                        }
                    )
            result = subprocess.run(
                [
                    sys.executable,
                    str(
                        ROOT
                        / "scripts"
                        / "validate_pose_diagnostic_review_decisions.py"
                    ),
                    "--queue",
                    str(queue_path),
                    "--decisions",
                    str(csv_path),
                    "--output",
                    str(report_path),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "review_in_progress")
            self.assertEqual(report["truth_status"], "not_adjudicated")
            self.assertEqual(report["source_validation"]["status"], "passed")
            self.assertFalse(report["safety"]["quality_gate_modified"])

    def test_schema_is_strict_about_safety_and_task_decisions(self) -> None:
        schema = json.loads(
            (
                ROOT / "contracts" / "pose-diagnostic-review-queue.schema.json"
            ).read_text(encoding="utf-8")
        )
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schema["properties"]["safety"]["properties"]["accuracy_claim"]["const"], False)
        task = schema["$defs"]["task"]
        self.assertFalse(task["additionalProperties"])
        self.assertEqual(task["properties"]["review_status"]["const"], "pending")
        self.assertEqual(task["properties"]["decision"]["type"], "null")
        decision_schema = json.loads(
            (
                ROOT
                / "contracts"
                / "pose-diagnostic-review-decision-report.schema.json"
            ).read_text(encoding="utf-8")
        )
        self.assertFalse(decision_schema["additionalProperties"])
        self.assertEqual(
            decision_schema["properties"]["truth_status"]["const"],
            "not_adjudicated",
        )


if __name__ == "__main__":
    unittest.main()
