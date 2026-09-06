from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from rallymate_events.disagreement import (
    compare_event_candidates,
    validate_event_disagreement_report,
)


def _event(event_id: str, start_ms: int, end_ms: int, *, code: str = "FS01") -> dict:
    return {
        "event_id": event_id,
        "event_code": code,
        "person_track_id": 1,
        "start_ms": start_ms,
        "end_ms": end_ms,
    }


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )


class EventDisagreementTests(unittest.TestCase):
    def test_ordered_dp_prioritises_global_match_count_over_one_high_iou_pair(self) -> None:
        left = [_event("left-1", 0, 100), _event("left-2", 100, 200)]
        right = [_event("right-1", 0, 40), _event("right-2", 0, 120)]
        report = compare_event_candidates(
            left,
            right,
            left_label="model-a",
            right_label="model-b",
            minimum_iou_thresholds=(0.1, 0.3, 0.5),
        )

        low, medium, high = report["threshold_results"]
        self.assertEqual(low["matched_count"], 2)
        self.assertEqual(
            [(row["left_event_id"], row["right_event_id"]) for row in low["matches"]],
            [("left-1", "right-1"), ("left-2", "right-2")],
        )
        self.assertEqual(low["left_to_right_match_rate"], 1.0)
        self.assertEqual(low["right_to_left_match_rate"], 1.0)
        self.assertEqual(low["unmatched"]["left_event_ids"], [])
        self.assertEqual(low["unmatched"]["right_event_ids"], [])
        self.assertEqual(low["matches"][0]["start_difference_ms"], 0)
        self.assertEqual(low["matches"][0]["end_difference_ms"], -60)
        self.assertEqual(low["matches"][0]["center_difference_ms"], -30.0)
        self.assertEqual(low["matches"][0]["duration_difference_ms"], -60)

        self.assertEqual(medium["matched_count"], 1)
        self.assertEqual(high["matched_count"], 1)
        self.assertEqual(medium["unmatched"]["left_event_ids"], ["left-2"])
        self.assertEqual(medium["unmatched"]["right_event_ids"], ["right-1"])
        self.assertFalse(report["semantics"]["accuracy_claim"])
        self.assertFalse(report["semantics"]["ground_truth_provided"])
        self.assertIn("agreement is not accuracy", report["semantics"]["interpretation"])

    def test_matching_never_crosses_event_code_or_track(self) -> None:
        left = [
            _event("left-fs01", 0, 100, code="FS01"),
            {**_event("left-track-2", 200, 300, code="FS09"), "person_track_id": 2},
        ]
        right = [
            _event("right-fs02", 0, 100, code="FS02"),
            _event("right-track-1", 200, 300, code="FS09"),
        ]
        result = compare_event_candidates(
            left,
            right,
            left_label="model-a",
            right_label="model-b",
            minimum_iou_thresholds=(0.1,),
        )["threshold_results"][0]
        self.assertEqual(result["matched_count"], 0)
        self.assertCountEqual(
            result["unmatched"]["left_event_ids"],
            ["left-fs01", "left-track-2"],
        )
        self.assertCountEqual(
            result["unmatched"]["right_event_ids"],
            ["right-fs02", "right-track-1"],
        )

    def test_validator_rejects_an_accuracy_claim(self) -> None:
        report = compare_event_candidates(
            [_event("left", 0, 100)],
            [_event("right", 0, 100)],
            left_label="model-a",
            right_label="model-b",
        )
        unsafe = copy.deepcopy(report)
        unsafe["semantics"]["accuracy_claim"] = True
        with self.assertRaisesRegex(ValueError, "accuracy_claim=false"):
            validate_event_disagreement_report(unsafe)

    def test_machine_readable_schema_locks_safety_and_hash_fields(self) -> None:
        root = Path(__file__).resolve().parents[1]
        schema = json.loads(
            (root / "contracts" / "event-disagreement.schema.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(schema["properties"]["schema_version"]["const"], "1.0.0")
        self.assertFalse(
            schema["properties"]["semantics"]["properties"]["accuracy_claim"]["const"]
        )
        self.assertIn("sha256", schema["$defs"]["input"]["required"])
        self.assertEqual(
            schema["properties"]["matching"]["properties"]["grouping_keys"]["const"],
            ["event_code", "person_track_id"],
        )

    def test_cli_hashes_raw_jsonl_and_emits_sensitivity_results(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            left_path = directory / "left.jsonl"
            right_path = directory / "right.jsonl"
            output_path = directory / "comparison.json"
            _write_jsonl(left_path, [_event("left", 0, 100)])
            _write_jsonl(right_path, [_event("right", 10, 110)])
            process = subprocess.run(
                [
                    sys.executable,
                    str(root / "scripts" / "compare_event_candidates.py"),
                    "--left-events",
                    str(left_path),
                    "--right-events",
                    str(right_path),
                    "--left-label",
                    "model-a",
                    "--right-label",
                    "model-b",
                    "--output",
                    str(output_path),
                ],
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(process.returncode, 0, process.stderr)
            report = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(
                report["matching"]["minimum_segment_iou_thresholds"],
                [0.1, 0.3, 0.5],
            )
            self.assertEqual(report["inputs"]["left"]["hash_scope"], "raw_jsonl_bytes")
            self.assertEqual(
                report["inputs"]["left"]["sha256"],
                hashlib.sha256(left_path.read_bytes()).hexdigest(),
            )
            self.assertFalse(report["semantics"]["accuracy_claim"])


if __name__ == "__main__":
    unittest.main()
