from __future__ import annotations

import csv
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from rallymate_annotation.scoring_truth_refresh import (
    build_scoring_truth_refresh,
    load_latest_scoring_truth_refresh,
    validate_scoring_truth_refresh_manifest,
)


ROOT = Path(__file__).resolve().parents[1]
VIDEO_ID = "850cb0006b406c7176eeda8d711cd065"
PACK = ROOT / "data" / "annotations" / "scoring-truth-pack-v1"
CURRENT_RUN = ROOT / "reports" / "scoring-candidate-multivideo-m53" / "runs" / VIDEO_ID
FRAMES = CURRENT_RUN / "frames.jsonl"
TIMELINE = (
    ROOT
    / "reports"
    / "pose-scoring-current-inputs"
    / "primary-player-v0.3.0"
    / VIDEO_ID
    / "primary-player.jsonl"
)
EVENTS = CURRENT_RUN / "events.jsonl"
REGISTRY = ROOT / "metric-feasibility-pose-wave-v2.json"
WORKLIST = (
    ROOT
    / "reports"
    / "scoring-truth-action-worklist-halpe256-full-m53"
    / "worklist.json"
)
POSE_MANIFEST = (
    ROOT
    / "reports"
    / "pose-diagnostic-truth"
    / "halpe26-full-m53-v1"
    / "manifest.json"
)
REFERENCE = (
    ROOT
    / "data"
    / "annotations"
    / "scoring-reference-context-v1"
    / f"{VIDEO_ID}-fs02-target-directions.json"
)
LATEST = ROOT / "reports" / "scoring-truth-refresh" / "latest.json"


class ScoringTruthRefreshTests(unittest.TestCase):
    def _build(self, *, pack: Path, output_root: Path, refresh_id: str) -> dict:
        return build_scoring_truth_refresh(
            pack_dir=pack,
            frames_path=FRAMES,
            primary_timeline_path=TIMELINE,
            predicted_events_path=EVENTS,
            registry_path=REGISTRY,
            worklist_path=WORKLIST,
            pose_truth_manifest_path=POSE_MANIFEST,
            reference_context_path=REFERENCE,
            output_root=output_root,
            refresh_id=refresh_id,
            generated_at="2026-08-22T00:00:00+00:00",
        )

    def test_current_latest_snapshot_is_hash_bound_and_fail_closed(self) -> None:
        manifest = load_latest_scoring_truth_refresh(LATEST)
        self.assertEqual(manifest["refresh_id"], "m53-empty-registry-17-v1")
        self.assertEqual(manifest["states"]["action_readiness"], "annotation_required")
        self.assertEqual(manifest["counts"]["work_items"], 168)
        self.assertEqual(manifest["counts"]["evidence_units"], 87)
        self.assertEqual(manifest["counts"]["satisfied_evidence_units"], 0)
        self.assertFalse(manifest["safety"]["grades_generated"])
        self.assertFalse(manifest["safety"]["thresholds_generated"])

    def test_full_refresh_uses_immutable_snapshots_and_publishes_latest_last(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            copied_pack = base / "pack"
            shutil.copytree(PACK, copied_pack)
            output_root = base / "refreshes"
            manifest = self._build(
                pack=copied_pack,
                output_root=output_root,
                refresh_id="synthetic-empty-refresh",
            )
            latest = load_latest_scoring_truth_refresh(output_root / "latest.json")
            self.assertEqual(latest, manifest)
            self.assertEqual(manifest["counts"]["manual_events"], 0)
            self.assertTrue(
                Path(manifest["artifacts"]["manual_events"]["path"]).is_file()
            )
            self.assertNotIn(".inprogress", "".join(str(path) for path in output_root.rglob("*")))
            validate_scoring_truth_refresh_manifest(manifest, verify_sources=True)

    def test_invalid_annotations_fail_preflight_without_latest_pointer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            copied_pack = base / "pack"
            shutil.copytree(PACK, copied_pack)
            event_csv = copied_pack / "event-annotations.csv"
            with event_csv.open("r", encoding="utf-8-sig", newline="") as handle:
                fieldnames = next(csv.reader(handle))
            invalid = {name: "" for name in fieldnames}
            invalid.update(
                {
                    "event_id": "invalid-accepted-event",
                    "adjudication_status": "accepted",
                }
            )
            with event_csv.open("a", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fieldnames)
                writer.writerow(invalid)
            output_root = base / "refreshes"
            with self.assertRaisesRegex(ValueError, "preflight failed"):
                self._build(
                    pack=copied_pack,
                    output_root=output_root,
                    refresh_id="must-not-publish",
                )
            self.assertFalse((output_root / "latest.json").exists())
            self.assertFalse((output_root / "must-not-publish").exists())

    def test_unbound_compiled_file_cannot_enter_refresh_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            copied_pack = base / "pack"
            shutil.copytree(PACK, copied_pack)
            stale = copied_pack / "compiled" / "unbound-stale-truth.jsonl"
            stale.write_text('{"private":"stale"}\n', encoding="utf-8")
            output_root = base / "refreshes"
            with self.assertRaisesRegex(ValueError, "compiled truth topology mismatch"):
                self._build(
                    pack=copied_pack,
                    output_root=output_root,
                    refresh_id="must-not-copy-unbound-file",
                )
            self.assertFalse((output_root / "latest.json").exists())
            self.assertFalse((output_root / "must-not-copy-unbound-file").exists())

    def test_latest_pointer_hash_tampering_is_rejected(self) -> None:
        latest = json.loads(LATEST.read_text(encoding="utf-8"))
        latest["manifest"]["sha256"] = "0" * 64
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "latest.json"
            path.write_text(json.dumps(latest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "manifest SHA mismatch"):
                load_latest_scoring_truth_refresh(path)

    def test_source_fingerprint_tampering_is_rejected(self) -> None:
        manifest = load_latest_scoring_truth_refresh(LATEST)
        manifest["source_fingerprint_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "source fingerprint mismatch"):
            validate_scoring_truth_refresh_manifest(manifest)

    def test_machine_schema_accepts_current_manifest(self) -> None:
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema unavailable")
        manifest = load_latest_scoring_truth_refresh(LATEST)
        schema = json.loads(
            (ROOT / "contracts" / "scoring-truth-refresh.schema.json").read_text(
                encoding="utf-8"
            )
        )
        jsonschema.Draft202012Validator(schema).validate(manifest)


if __name__ == "__main__":
    unittest.main()
