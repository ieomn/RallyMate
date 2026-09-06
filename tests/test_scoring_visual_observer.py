from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

from rallymate_visualization.scoring_observer import (
    OBSERVER_VERSION,
    validate_observer_manifest,
    validate_video_observer_payload,
)


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "reports" / "scoring-visual-observer-m81"
RUNS = ROOT / "reports" / "scoring-candidate-multivideo-m78" / "runs"
REGISTRY_PATH = ROOT / "metric-feasibility-pose-wave-v2.json"
VIDEO_SCHEMA_PATH = ROOT / "contracts" / "scoring-visual-observer-video.schema.json"
MANIFEST_SCHEMA_PATH = (
    ROOT / "contracts" / "scoring-visual-observer-manifest.schema.json"
)


def _jsonl_count(path: Path) -> int:
    with path.open("r", encoding="utf-8") as stream:
        return sum(1 for line in stream if line.strip())


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _canonical_sha256(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest().upper()


class ScoringVisualObserverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        cls.manifest = json.loads((OUTPUT / "manifest.json").read_text(encoding="utf-8"))
        cls.payloads = {
            item["video_id"]: json.loads(
                (OUTPUT / item["data_file"]).read_text(encoding="utf-8")
            )
            for item in cls.manifest["videos"]
        }
        cls.manifest_video_by_id = {
            item["video_id"]: item for item in cls.manifest["videos"]
        }

    def test_real_three_video_observer_replays_current_sources(self) -> None:
        validate_observer_manifest(self.manifest, OUTPUT, self.registry)
        expected_ids = {
            "3ae77ee3271d67de171585a5c39ddd69",
            "850cb0006b406c7176eeda8d711cd065",
            "8d7754d0de6d315674013d5b69a0b6ba",
        }
        self.assertEqual(expected_ids, set(self.payloads))
        for video_id, payload in self.payloads.items():
            self.assertEqual(OBSERVER_VERSION, payload["observer_version"])
            self.assertEqual(
                _jsonl_count(RUNS / video_id / "frames.jsonl"),
                payload["video"]["frame_count"],
            )
            self.assertEqual(
                _jsonl_count(RUNS / video_id / "events.jsonl"),
                payload["summary"]["event_count"],
            )
            self.assertEqual(
                _jsonl_count(RUNS / video_id / "indicator-features.jsonl"),
                payload["summary"]["indicator_instance_count"],
            )
            self.assertEqual(
                payload["source_sha256"]["video"],
                _sha256(ROOT / "FULL-TEST" / f"{video_id}.mp4"),
            )
            expected_summary_sha256 = _sha256(
                RUNS / video_id / "scoring-loop-summary.json"
            )
            self.assertEqual(
                expected_summary_sha256,
                payload["source_sha256"]["summary"],
            )
            self.assertEqual(
                expected_summary_sha256,
                self.manifest_video_by_id[video_id]["source_summary_sha256"],
            )

    def test_manifest_and_video_payloads_match_v1_1_schemas(self) -> None:
        manifest_schema = json.loads(
            MANIFEST_SCHEMA_PATH.read_text(encoding="utf-8")
        )
        video_schema = json.loads(VIDEO_SCHEMA_PATH.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(manifest_schema)
        Draft202012Validator.check_schema(video_schema)
        Draft202012Validator(manifest_schema).validate(self.manifest)
        video_validator = Draft202012Validator(video_schema)
        for payload in self.payloads.values():
            video_validator.validate(payload)

    def test_builder_requires_explicit_runs_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "observer"
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "build_scoring_visual_observer_m81.py"),
                    "--output",
                    str(output),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertNotEqual(0, result.returncode)
        self.assertIn("--runs-root", result.stderr)
        self.assertFalse(output.exists())

    def test_every_indicator_is_visible_but_no_grade_or_threshold_is_created(self) -> None:
        registry_ids = {item["indicator_id"] for item in self.registry["indicators"]}
        for payload in self.payloads.values():
            observed_ids = {
                indicator["indicator_id"]
                for event in payload["events"]
                for indicator in event["indicators"]
            }
            self.assertEqual(registry_ids, observed_ids)
            self.assertEqual(0, payload["summary"]["grade_count"])
            self.assertEqual(0, payload["summary"]["threshold_count"])
            self.assertFalse(payload["safety"]["grades_or_thresholds_generated"])
            self.assertTrue(payload["safety"]["pose_overlay_is_model_output_not_truth"])
            self.assertTrue(all(len(item["joint_names"]) == 26 for item in [payload]))
            self.assertTrue(
                all(
                    indicator["grade"] is None
                    for event in payload["events"]
                    for indicator in event["indicators"]
                )
            )

    def test_pose_or_status_tampering_breaks_canonical_content_binding(self) -> None:
        payload = copy.deepcopy(next(iter(self.payloads.values())))
        first_pose_frame = next(frame for frame in payload["frames"] if frame["points"])
        first_pose_frame["points"][0][0] = 0.999999
        with self.assertRaisesRegex(ValueError, "content hash mismatch"):
            validate_video_observer_payload(payload, self.registry)

        payload = copy.deepcopy(next(iter(self.payloads.values())))
        payload["events"][0]["indicators"][0]["grade"] = "A"
        with self.assertRaises(ValueError):
            validate_video_observer_payload(payload, self.registry)

    def test_rehashed_manifest_cannot_switch_source_summary(self) -> None:
        manifest = copy.deepcopy(self.manifest)
        manifest["videos"][0]["source_summary_sha256"] = "0" * 64
        unhashed = dict(manifest)
        unhashed.pop("content_sha256")
        manifest["content_sha256"] = _canonical_sha256(unhashed)
        with self.assertRaisesRegex(ValueError, "source summary binding mismatch"):
            validate_observer_manifest(manifest, OUTPUT, self.registry)

    def test_browser_assets_expose_video_pose_events_features_and_safety(self) -> None:
        html = (OUTPUT / "index.html").read_text(encoding="utf-8")
        script = (OUTPUT / "scoring-observer.js").read_text(encoding="utf-8")
        for required in (
            'id="source-video"',
            'id="pose-canvas"',
            'id="timeline"',
            'id="indicator-list"',
            'id="feature-list"',
            "不是 A～E 正式评分",
        ):
            self.assertIn(required, html)
        self.assertIn("data_file_sha256", script)
        self.assertIn("pose_overlay_is_model_output_not_truth", json.dumps(next(iter(self.payloads.values()))))
        main_report = (ROOT / "reports" / "pose-scoring-ab" / "index.html").read_text(
            encoding="utf-8"
        )
        self.assertIn("M81 三视频动态评分观察器", main_report)
        self.assertIn("../scoring-visual-observer-m81/index.html", main_report)


if __name__ == "__main__":
    unittest.main()
