from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from rallymate_evaluation.scoring_truth import _freeze_input, _parse_jsonl


TIMESTAMPS = (0, 40, 95, 165, 250, 350, 470)
BASE = {
    "left_shoulder": (0.40, 0.30),
    "right_shoulder": (0.60, 0.30),
    "left_hip": (0.45, 0.55),
    "right_hip": (0.55, 0.55),
    "left_knee": (0.44, 0.72),
    "right_knee": (0.56, 0.72),
    "left_ankle": (0.42, 0.90),
    "right_ankle": (0.58, 0.90),
}


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )


def _event(event_id: str, start_ms: int, *, manual: bool) -> dict:
    record = {
        "schema_version": "1.0.0",
        "event_id": event_id,
        "person_track_id": 1,
        "event_code": "FS09",
        "start_ms": start_ms,
        "end_ms": 470,
        "key_phases_ms": {"deceleration_peak_ms": 250},
        "confidence": 1.0,
        "boundary_uncertainty_ms": 0,
        "quality_flags": [],
        "provenance": {
            "fixture": "synthetic-cli-test",
            "source_id": "synthetic-video",
        },
    }
    if manual:
        record.update(
            {
                "video_id": "synthetic-video",
                "annotation_source": "manual",
                "annotator_id": "adjudicated-truth",
                "reviewer_id": "synthetic-reviewer",
                "adjudication_status": "accepted",
                "view_group": "side",
            }
        )
    return record


class TruthEvaluationCliTests(unittest.TestCase):
    def test_input_snapshot_hashes_the_exact_bytes_supplied_to_the_parser(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "records.jsonl"
            original = b'{"value":1}\n'
            replacement = b'{"value":2}\n'
            path.write_bytes(original)

            def parse_and_replace(raw: bytes) -> list[dict]:
                path.write_bytes(replacement)
                return _parse_jsonl(raw, label="race fixture")

            records, digest = _freeze_input(
                path,
                label="race fixture",
                parser=parse_and_replace,
            )
            self.assertEqual(records, [{"value": 1}])
            self.assertEqual(digest, hashlib.sha256(original).hexdigest().upper())
            self.assertNotEqual(digest, hashlib.sha256(path.read_bytes()).hexdigest().upper())

    def test_cli_computes_event_feature_metrics_and_error_budget(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            frames = []
            timeline = []
            annotations = []
            for index, timestamp in enumerate(TIMESTAMPS):
                shift = timestamp / 1000 * 0.05
                points = []
                joints = {}
                for name, (x, y) in BASE.items():
                    model_x = x + shift
                    truth_x = (0.50 if name == "left_knee" else x) + shift
                    points.append(
                        {
                            "name": name,
                            "x_normalized": model_x,
                            "y_normalized": y,
                            "confidence": 0.9,
                        }
                    )
                    joints[name] = {
                        "visible": True,
                        "x_normalized": truth_x,
                        "y_normalized": y,
                    }
                frames.append(
                    {
                        "frame": {
                            "index": index,
                            "processed_index": index,
                            "timestamp_ms": timestamp,
                        },
                        "poses": [{"person_track_id": 7, "keypoints": points}],
                    }
                )
                timeline.append(
                    {"processed_index": index, "source_track_id": 7}
                )
                annotations.append(
                    {
                        "schema_version": "1.0.0",
                        "video_id": "synthetic-video",
                        "source_frame_index": index,
                        "timestamp_ms": timestamp,
                        "primary_player_id": 1,
                        "annotator_id": "adjudicated-truth",
                        "reviewer_id": "synthetic-reviewer",
                        "adjudication_status": "accepted",
                        "view_group": "side",
                        "joints": joints,
                    }
                )
            paths = {
                "frames": directory / "frames.jsonl",
                "timeline": directory / "primary-player.jsonl",
                "predictions": directory / "events.jsonl",
                "events": directory / "manual-events.jsonl",
                "keypoints": directory / "manual-keypoints.jsonl",
                "output": directory / "evaluation.json",
            }
            _write_jsonl(paths["frames"], frames)
            _write_jsonl(paths["timeline"], timeline)
            _write_jsonl(paths["predictions"], [_event("prediction", 40, manual=False)])
            _write_jsonl(paths["events"], [_event("truth", 0, manual=True)])
            _write_jsonl(paths["keypoints"], annotations)
            process = subprocess.run(
                [
                    sys.executable,
                    str(root / "scripts" / "evaluate_scoring_truth.py"),
                    "--frames",
                    str(paths["frames"]),
                    "--primary-timeline",
                    str(paths["timeline"]),
                    "--predicted-events",
                    str(paths["predictions"]),
                    "--manual-events",
                    str(paths["events"]),
                    "--manual-keypoints",
                    str(paths["keypoints"]),
                    "--registry",
                    str(root / "metric-feasibility.json"),
                    "--output",
                    str(paths["output"]),
                ],
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(process.returncode, 0, process.stderr)
            report = json.loads(paths["output"].read_text(encoding="utf-8"))
            self.assertEqual(report["event_evaluation"]["event_f1"], 1.0)
            self.assertEqual(report["event_evaluation"]["boundary_mae_ms"], 20.0)
            self.assertEqual(
                report["event_evaluation"]["phase_boundary_mae_ms"], 0.0
            )
            self.assertEqual(
                report["event_evaluation"]["phase_boundary_by_name"]
                ["deceleration_peak_ms"]["predicted_count"],
                1,
            )
            knee = report["feature_evaluation"]["feature_metrics"][
                "left_knee_flexion_deg"
            ]
            self.assertGreater(knee["overall"]["mae"], 0.0)
            self.assertEqual(knee["overall"]["valid_rate"], 1.0)
            self.assertIn(
                "pose_error",
                report["feature_evaluation"]["error_budget"]["features"][
                    "left_knee_flexion_deg"
                ],
            )
            self.assertFalse(report["promotion_guard"]["F2_to_F3_automatic"])
            self.assertEqual(
                report["promotion_guard"]["required_context_features"], []
            )
            self.assertEqual(
                report["promotion_guard"]["manual_semantic_record_count"], 0
            )
            self.assertTrue(
                report["promotion_guard"]["context_feature_truth_complete"]
            )


if __name__ == "__main__":
    unittest.main()
