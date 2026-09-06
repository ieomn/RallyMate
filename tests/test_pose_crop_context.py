from __future__ import annotations

import json
import unittest
from pathlib import Path

from rallymate_evaluation.pose_crop_context import classify_routed_pose_sources


ROOT = Path(__file__).resolve().parents[1]
VIDEO_ID = "850cb0006b406c7176eeda8d711cd065"
REPORT = (
    ROOT
    / "reports"
    / "measurement-recovery-m68"
    / "required-joint-superset"
    / VIDEO_ID
    / "report.json"
)


def _rows(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


class PoseCropContextReplayTests(unittest.TestCase):
    def test_real_m68_sources_replay_exact_composition_counts(self) -> None:
        report = json.loads(REPORT.read_text(encoding="utf-8"))
        baseline = _rows(Path(report["sources"]["baseline_frames"]["path"]))
        routed = _rows(Path(report["artifacts"]["routed_frames"]["path"]))
        timeline = _rows(Path(report["sources"]["primary_timeline"]["path"]))
        experiments = {
            name: _rows(Path(binding["frames"]["path"]))
            for name, binding in report["sources"]["experiments"].items()
        }
        _, audit = classify_routed_pose_sources(
            baseline_rows=baseline,
            routed_rows=routed,
            primary_timeline=timeline,
            experiment_rows_by_name=experiments,
            target_processed_indexes={
                int(row["frame"]["processed_index"]) for row in baseline
            },
        )
        self.assertEqual(
            audit["source_frame_count_by_policy"],
            {
                "baseline": 2751,
                "roi_margin_candidate": 29,
                "small_roi_min8": 131,
            },
        )
        self.assertFalse(audit["classification_uses_feature_values"])

    def test_unknown_changed_pose_source_fails_closed(self) -> None:
        base = {
            "frame": {"processed_index": 0},
            "poses": [{"person_track_id": 1, "value": "base"}],
        }
        routed = {
            "frame": {"processed_index": 0},
            "poses": [{"person_track_id": 1, "value": "unknown"}],
        }
        candidate = {
            "frame": {"processed_index": 0},
            "poses": [{"person_track_id": 1, "value": "candidate"}],
        }
        with self.assertRaisesRegex(ValueError, "exactly one"):
            classify_routed_pose_sources(
                baseline_rows=[base],
                routed_rows=[routed],
                primary_timeline=[{"processed_index": 0, "source_track_id": 1}],
                experiment_rows_by_name={"candidate": [candidate]},
                target_processed_indexes={0},
            )


if __name__ == "__main__":
    unittest.main()
