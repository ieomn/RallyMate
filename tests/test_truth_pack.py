from __future__ import annotations

import csv
import json
import re
import tempfile
import unittest
from pathlib import Path

from rallymate_annotation import build_truth_pack, compile_truth_pack
from rallymate_annotation.truth_pack import (
    ALL_INDICATORS,
    DEFAULT_CANDIDATE_EVENTS_TEMPLATE,
    EVENT_CSV_FIELDS,
    EVENT_PHASES,
    INDICATORS_BY_EVENT,
    KEYPOINT_JOINTS,
    KEYPOINT_CSV_FIELDS,
    REQUIRED_PHASES_BY_EVENT,
    SEMANTIC_REQUIREMENTS_BY_INDICATOR,
    SEMANTIC_CSV_FIELDS,
    SEMANTIC_TYPE_BY_KEY,
    TRUTH_PACK_VERSION,
    TRUTH_WORKBENCH_VERSION,
    VIDEO_IDS,
    REVIEW_CSV_FIELDS,
    _review_html,
    _write_workbench_assets,
)


EVENT_FIELDS = [
    "video_id",
    "event_id",
    "event_code",
    "person_track_id",
    "start_ms",
    "end_ms",
    *EVENT_PHASES,
    "annotation_confidence",
    "boundary_uncertainty_ms",
    "view_group",
    "annotator_id",
    "reviewer_id",
    "adjudication_status",
    "quality_flags",
]
KEYPOINT_FIELDS = [
    "video_id",
    "source_frame_index",
    "timestamp_ms",
    "source_clip_ids",
    "primary_player_id",
    "joint_name",
    "visible",
    "x_normalized",
    "y_normalized",
    "visibility_reason",
    "view_group",
    "annotator_id",
    "reviewer_id",
    "adjudication_status",
]
SEMANTIC_FIELDS = [
    "annotation_id",
    "video_id",
    "event_id",
    "candidate_event_id",
    "blind_clip_id",
    "indicator_id",
    "semantic_key",
    "semantic_type",
    "observable",
    "direction_deg",
    "coordinate_frame",
    "side",
    "timestamp_ms",
    "interval_start_ms",
    "interval_end_ms",
    "contact_state",
    "camera_motion_observed",
    "camera_audit_method",
    "null_reason",
    "annotation_confidence",
    "annotator_id",
    "reviewer_id",
    "adjudication_status",
]
COACH_FIELDS = [
    "annotation_id",
    "video_id",
    "event_id",
    "candidate_event_id",
    "blind_clip_id",
    "indicator_id",
    "annotator_id",
    "label_type",
    "grade",
    "rank_group_id",
    "rank",
]
REVIEW_FIELDS = [
    "video_id",
    "annotator_id",
    "full_video_review_completed",
    "reviewed_at",
    "notes",
]


def _csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_pack(
    pack: Path,
    *,
    events: list[dict] | None = None,
    keypoints: list[dict] | None = None,
    semantics: list[dict] | None = None,
    coaches: list[dict] | None = None,
    reviews: list[dict] | None = None,
) -> None:
    keypoints = keypoints or []
    (pack / "manifest.json").write_text(
        json.dumps(
            {
                "pack_version": "test-pack",
                "videos": [{"video_id": "video"}],
                "counts": {
                    "dense_keypoint_frames": len(
                        {
                            (row["video_id"], row["source_frame_index"])
                            for row in keypoints
                        }
                    ),
                    "keypoint_joint_rows": len(keypoints),
                },
            }
        ),
        encoding="utf-8",
    )
    _csv(pack / "event-annotations.csv", EVENT_FIELDS, events or [])
    _csv(pack / "keypoint-annotations.csv", KEYPOINT_FIELDS, keypoints)
    _csv(pack / "semantic-annotations.csv", SEMANTIC_FIELDS, semantics or [])
    _csv(pack / "coach-labels.csv", COACH_FIELDS, coaches or [])
    _csv(pack / "full-video-review-completion.csv", REVIEW_FIELDS, reviews or [])


def _events(*, count_per_code: int = 1, empty_phases: bool = False) -> list[dict]:
    rows = []
    for code_index, event_code in enumerate(INDICATORS_BY_EVENT):
        for event_index in range(count_per_code):
            start_ms = 1000 + code_index * 10000 + event_index * 1000
            row = {
                "video_id": "video",
                "event_id": f"{event_code.lower()}-{event_index + 1}",
                "event_code": event_code,
                "person_track_id": 1,
                "start_ms": start_ms,
                "end_ms": start_ms + 900,
                "annotation_confidence": 0.9,
                "boundary_uncertainty_ms": 20,
                "view_group": "fixed-rear",
                "annotator_id": "event-final",
                "reviewer_id": "event-reviewer",
                "adjudication_status": "accepted",
                "quality_flags": "",
            }
            row.update({phase: "" for phase in EVENT_PHASES})
            if not empty_phases:
                for phase_index, phase in enumerate(
                    REQUIRED_PHASES_BY_EVENT[event_code], start=1
                ):
                    row[phase] = start_ms + phase_index * 100
            rows.append(row)
    return rows


def _reviews() -> list[dict]:
    return [
        {
            "video_id": "video",
            "annotator_id": annotator_id,
            "full_video_review_completed": "true",
            "reviewed_at": "2026-08-13T00:00:00Z",
            "notes": "",
        }
        for annotator_id in ("event-a", "event-b")
    ]


def _keypoints_for_first_event_per_code(events: list[dict]) -> list[dict]:
    first_by_code = {}
    for event in events:
        first_by_code.setdefault(event["event_code"], event)
    rows = []
    for frame_index, event in enumerate(first_by_code.values(), start=1):
        timestamp_ms = int(event["start_ms"]) + 450
        for joint_index, joint in enumerate(KEYPOINT_JOINTS):
            rows.append(
                {
                    "video_id": "video",
                    "source_frame_index": frame_index,
                    "timestamp_ms": timestamp_ms,
                    "source_clip_ids": "",
                    "primary_player_id": 1,
                    "joint_name": joint,
                    "visible": "true",
                    "x_normalized": 0.2 + joint_index * 0.01,
                    "y_normalized": 0.3 + joint_index * 0.01,
                    "visibility_reason": "",
                    "view_group": "fixed-rear",
                    "annotator_id": "keypoint-final",
                    "reviewer_id": "keypoint-reviewer",
                    "adjudication_status": "accepted",
                }
            )
    return rows


def _unobservable_semantics(events: list[dict]) -> list[dict]:
    first_by_code = {}
    for event in events:
        first_by_code.setdefault(event["event_code"], event)
    rows = []
    for indicator_id in ALL_INDICATORS:
        event_code = indicator_id.split("-", maxsplit=1)[0]
        event = first_by_code[event_code]
        for semantic_key in SEMANTIC_REQUIREMENTS_BY_INDICATOR[indicator_id]:
            rows.append(
                {
                    "annotation_id": f"{indicator_id}-{semantic_key}",
                    "video_id": "video",
                    "event_id": event["event_id"],
                    "candidate_event_id": "",
                    "blind_clip_id": "",
                    "indicator_id": indicator_id,
                    "semantic_key": semantic_key,
                    "semantic_type": SEMANTIC_TYPE_BY_KEY[semantic_key],
                    "observable": "false",
                    "direction_deg": "",
                    "coordinate_frame": "",
                    "side": "",
                    "timestamp_ms": "",
                    "interval_start_ms": "",
                    "interval_end_ms": "",
                    "contact_state": "",
                    "camera_motion_observed": "",
                    "camera_audit_method": "",
                    "null_reason": "not_observable_in_fixed_2d_video",
                    "annotation_confidence": 0.95,
                    "annotator_id": "semantic-final",
                    "reviewer_id": "semantic-reviewer",
                    "adjudication_status": "accepted",
                }
            )
    return rows


def _coach_labels(
    events: list[dict], *, indicators: tuple[str, ...] = ALL_INDICATORS
) -> list[dict]:
    by_code = {
        event_code: [event for event in events if event["event_code"] == event_code]
        for event_code in INDICATORS_BY_EVENT
    }
    rows = []
    for indicator_id in indicators:
        event_code = indicator_id.split("-", maxsplit=1)[0]
        for event_index, event in enumerate(by_code[event_code]):
            for annotator_id in ("coach-a", "coach-b"):
                rows.append(
                    {
                        "annotation_id": (
                            f"{event['event_id']}-{indicator_id}-{annotator_id}"
                        ),
                        "video_id": "video",
                        "event_id": event["event_id"],
                        "candidate_event_id": "",
                        "blind_clip_id": "",
                        "indicator_id": indicator_id,
                        "annotator_id": annotator_id,
                        "label_type": "grade",
                        "grade": "A" if event_index == 0 else "B",
                        "rank_group_id": "",
                        "rank": "",
                    }
                )
    return rows


class TruthPackTests(unittest.TestCase):
    def test_build_refuses_existing_output_before_writing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "existing-pack"
            output.mkdir()
            sentinel = output / "human-annotations.csv"
            sentinel.write_bytes(b"must remain byte-for-byte unchanged\r\n")

            with self.assertRaisesRegex(FileExistsError, "already exists"):
                build_truth_pack(root, output)

            self.assertEqual(
                sentinel.read_bytes(), b"must remain byte-for-byte unchanged\r\n"
            )
            self.assertEqual(
                sorted(path.relative_to(output).as_posix() for path in output.rglob("*")),
                ["human-annotations.csv"],
            )

    def test_current_pack_version_requires_exact_canonical_scope(self) -> None:
        mutations = {
            "missing_video": lambda manifest: manifest["videos"].pop(),
            "extra_video": lambda manifest: manifest["videos"].append(
                {"video_id": "unexpected-video"}
            ),
            "duplicate_video": lambda manifest: manifest["videos"].__setitem__(
                -1, {"video_id": VIDEO_IDS[0]}
            ),
            "missing_event": lambda manifest: manifest["scope"]["events"].pop(),
            "extra_event": lambda manifest: manifest["scope"]["events"].append(
                "FS99"
            ),
            "duplicate_event": lambda manifest: manifest["scope"][
                "events"
            ].__setitem__(-1, manifest["scope"]["events"][0]),
            "missing_indicator": lambda manifest: manifest["scope"][
                "indicators"
            ].pop(),
            "extra_indicator": lambda manifest: manifest["scope"][
                "indicators"
            ].append("FS99-M99"),
            "duplicate_indicator": lambda manifest: manifest["scope"][
                "indicators"
            ].__setitem__(-1, manifest["scope"]["indicators"][0]),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                pack = Path(directory)
                _write_pack(pack)
                manifest_path = pack / "manifest.json"
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifest.update(
                    {
                        "pack_version": TRUTH_PACK_VERSION,
                        "videos": [{"video_id": video_id} for video_id in VIDEO_IDS],
                        "scope": {
                            "events": list(INDICATORS_BY_EVENT),
                            "indicators": sorted(ALL_INDICATORS),
                        },
                    }
                )
                mutate(manifest)
                manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

                result = compile_truth_pack(pack)

                self.assertEqual(result["status"], "invalid_annotations")
                self.assertTrue(result["errors"])
                self.assertEqual(
                    len(result["readiness"]["indicator_readiness_matrix"]),
                    len(VIDEO_IDS) * len(ALL_INDICATORS),
                )
                self.assertFalse((pack / "compiled").exists())

    def test_invalid_compile_preserves_existing_compiled_tree_byte_for_byte(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pack = Path(directory)
            events = _events()
            events[0]["annotation_confidence"] = ""
            _write_pack(pack, events=events)
            compiled = pack / "compiled"
            nested = compiled / "by-video" / "legacy-video"
            nested.mkdir(parents=True)
            (compiled / "manual-events.jsonl").write_bytes(
                b'{"known":"good"}\r\n'
            )
            (compiled / "validation-report.json").write_bytes(b'{"old":true}\n')
            (nested / "stale-but-preserved.bin").write_bytes(b"\x00\x01\xff")
            before = {
                path.relative_to(compiled).as_posix(): path.read_bytes()
                for path in compiled.rglob("*")
                if path.is_file()
            }

            result = compile_truth_pack(pack)

            after = {
                path.relative_to(compiled).as_posix(): path.read_bytes()
                for path in compiled.rglob("*")
                if path.is_file()
            }
            self.assertEqual(result["status"], "invalid_annotations")
            self.assertTrue(result["errors"])
            self.assertEqual(after, before)

    def test_truth_pack_scope_covers_all_current_pose_only_indicators(self) -> None:
        self.assertEqual(TRUTH_PACK_VERSION, "scoring-truth-pack-v0.3.0")
        self.assertEqual(
            set(ALL_INDICATORS),
            {
                "FS01-M02", "FS01-M03", "FS01-M04", "FS01-M05",
                "FS02-M02", "FS02-M03", "FS02-M04", "FS02-M05",
                "FS09-M01", "FS09-M02", "FS09-M03", "FS09-M04", "FS09-M05",
            },
        )
        for phase in (
            "takeoff_proxy_ms",
            "landing_proxy_ms",
            "support_extension_proxy_ms",
            "lead_foot_motion_onset_proxy_ms",
            "first_step_slowdown_proxy_ms",
        ):
            self.assertIn(phase, EVENT_PHASES)
        self.assertIn("{video_id}", DEFAULT_CANDIDATE_EVENTS_TEMPLATE)

    def test_workbench_contract_matches_compiler_csv_headers(self) -> None:
        self.assertEqual(tuple(EVENT_FIELDS), EVENT_CSV_FIELDS)
        self.assertEqual(tuple(KEYPOINT_FIELDS), KEYPOINT_CSV_FIELDS)
        self.assertEqual(tuple(SEMANTIC_FIELDS), SEMANTIC_CSV_FIELDS)
        self.assertEqual(tuple(REVIEW_FIELDS), REVIEW_CSV_FIELDS)

    def test_offline_workbench_embeds_only_manual_templates_and_all_js_selectors_exist(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pack = Path(directory)
            _csv(pack / "event-annotations.csv", EVENT_FIELDS, [])
            _csv(
                pack / "keypoint-annotations.csv",
                KEYPOINT_FIELDS,
                [
                    {
                        "video_id": "video",
                        "source_frame_index": 7,
                        "timestamp_ms": 250,
                        "source_clip_ids": "pilot-001",
                        "primary_player_id": 1,
                        "joint_name": "left_ankle",
                        "visible": "",
                        "x_normalized": "",
                        "y_normalized": "",
                        "visibility_reason": "",
                        "view_group": "",
                        "annotator_id": "",
                        "reviewer_id": "",
                        "adjudication_status": "",
                    }
                ],
            )
            _csv(pack / "semantic-annotations.csv", SEMANTIC_FIELDS, [])
            _csv(pack / "full-video-review-completion.csv", REVIEW_FIELDS, [])
            manifest = {
                "pack_version": "test-pack",
                "status": "annotation_required_no_truth_or_thresholds_generated",
                "videos": [
                    {
                        "video_id": "video",
                        "path": r"C:\private\must-not-leak\video.mp4",
                        "sha256": "abc",
                        "candidate_event_count": 1,
                    }
                ],
                "pilot_keypoint_events": [
                    {
                        "blind_clip_id": "pilot-001",
                        "video_id": "video",
                        "candidate_event_id": "candidate-not-truth",
                        "event_code": "FS01",
                        "candidate_start_ms": 100,
                        "candidate_end_ms": 300,
                    }
                ],
                "safety": {},
            }
            _write_workbench_assets(pack)
            workbench = _review_html(manifest, pack)
            self.assertNotIn(r"C:\private", workbench)
            self.assertIn("../../../FULL-TEST/video.mp4", workbench)
            self.assertIn("truth-workbench.css", workbench)
            self.assertIn("truth-workbench.js", workbench)
            match = re.search(
                r'<script id="truth-workbench-bootstrap" type="application/json">(.*?)</script>',
                workbench,
                flags=re.DOTALL,
            )
            self.assertIsNotNone(match)
            bootstrap = json.loads(match.group(1))
            self.assertEqual(bootstrap["workbench_version"], TRUTH_WORKBENCH_VERSION)
            keypoint = bootstrap["rows"]["keypoints"][0]
            self.assertEqual(keypoint["x_normalized"], "")
            self.assertEqual(keypoint["y_normalized"], "")
            self.assertFalse(bootstrap["safety"]["model_keypoints_embedded"])
            self.assertFalse(bootstrap["safety"]["grades_or_thresholds_supported"])
            self.assertFalse(
                bootstrap["safety"]["annotation_execution_authorized"]
            )
            self.assertNotIn("coach_labels", bootstrap["rows"])

            html_ids = set(re.findall(r'id="([A-Za-z0-9_-]+)"', workbench))
            javascript = (pack / "truth-workbench.js").read_text(encoding="utf-8")
            id_selectors = set(
                re.findall(r'\$\("#([A-Za-z0-9_-]+)"', javascript)
            )
            self.assertEqual(id_selectors - html_ids, set())
            self.assertIn('row.adjudication_status === "accepted"', javascript)
            self.assertIn("validateEventRow(row)", javascript)
            self.assertIn("showDirectoryPicker", javascript)
            self.assertIn("applyExecutionAuthorizationGate", javascript)
            self.assertIn('node.disabled = true', javascript)
            css = (pack / "truth-workbench.css").read_text(encoding="utf-8")
            self.assertIn("@media (max-width: 900px)", css)

    def test_blank_pack_reports_missing_truth_without_generating_thresholds(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pack = Path(directory)
            _write_pack(pack)
            result = compile_truth_pack(pack)
            self.assertEqual(result["status"], "annotation_required")
            self.assertIsNone(result["candidate_source_binding"])
            self.assertIsNone(result["scoring_source_binding"])
            self.assertEqual(result["counts"]["manual_events"], 0)
            self.assertFalse(result["safety"]["generated_thresholds"])
            self.assertIn("video", result["readiness"]["missing_full_video_reviews"])
            self.assertEqual(len(result["readiness"]["indicator_readiness_matrix"]), 13)

    def test_only_fs01_cannot_satisfy_event_readiness(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pack = Path(directory)
            events = [event for event in _events() if event["event_code"] == "FS01"]
            _write_pack(pack, events=events, reviews=_reviews())
            result = compile_truth_pack(pack)
            self.assertFalse(result["readiness"]["required_event_code_coverage"])
            self.assertFalse(result["readiness"]["full_video_event_truth"])
            missing_codes = {
                item["event_code"]
                for item in result["readiness"]["missing_event_phase_cells"]
            }
            self.assertEqual(missing_codes, {"FS02", "FS09"})

    def test_events_with_empty_required_phases_cannot_be_ready(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pack = Path(directory)
            _write_pack(pack, events=_events(empty_phases=True), reviews=_reviews())
            result = compile_truth_pack(pack)
            self.assertTrue(result["readiness"]["required_event_code_coverage"])
            self.assertFalse(result["readiness"]["required_event_phase_coverage"])
            fs01 = next(
                item
                for item in result["readiness"]["event_phase_coverage"]
                if item["event_code"] == "FS01"
            )
            self.assertIn(
                "preload_ms", fs01["events"][0]["missing_required_phases"]
            )

    def test_two_coaches_on_one_indicator_cannot_satisfy_thirteen_indicator_matrix(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pack = Path(directory)
            events = _events(count_per_code=2)
            _write_pack(
                pack,
                events=events,
                coaches=_coach_labels(events, indicators=("FS01-M02",)),
                reviews=_reviews(),
            )
            result = compile_truth_pack(pack)
            self.assertFalse(result["readiness"]["multi_coach_overlap"])
            missing = {
                item["indicator_id"]
                for item in result["readiness"]["missing_coach_indicator_cells"]
            }
            self.assertNotIn("FS01-M02", missing)
            self.assertIn("FS01-M03", missing)
            self.assertIn("FS09-M05", missing)
            self.assertEqual(len(missing), 12)

    def test_unobservable_semantic_truth_requires_reason_and_never_gets_a_value(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pack = Path(directory)
            events = _events()
            semantic = next(
                row
                for row in _unobservable_semantics(events)
                if row["indicator_id"] == "FS02-M02"
            )
            _write_pack(pack, events=events, semantics=[semantic], reviews=_reviews())
            result = compile_truth_pack(pack)
            self.assertEqual(result["errors"], [])
            record = json.loads(
                (pack / "compiled" / "manual-semantics.jsonl")
                .read_text(encoding="utf-8")
                .strip()
            )
            self.assertFalse(record["observable"])
            self.assertIsNone(record["value"])
            self.assertEqual(record["null_reason"], "not_observable_in_fixed_2d_video")
            schema = json.loads(
                (
                    Path(__file__).resolve().parents[1]
                    / "contracts"
                    / "semantic-ground-truth.schema.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
            self.assertIn("observable", schema["required"])
            self.assertIn("null_reason", schema["required"])
            self.assertFalse(
                result["safety"]["unobservable_semantics_filled_with_guessed_values"]
            )

    def test_unobservable_semantic_without_reason_is_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pack = Path(directory)
            events = _events()
            semantic = next(
                row
                for row in _unobservable_semantics(events)
                if row["indicator_id"] == "FS02-M02"
            )
            semantic["null_reason"] = ""
            _write_pack(pack, events=events, semantics=[semantic], reviews=_reviews())
            result = compile_truth_pack(pack)
            self.assertEqual(result["status"], "invalid_annotations")
            self.assertTrue(
                any("observable=false" in error for error in result["errors"])
            )

    def test_complete_explicit_matrix_can_reach_review_ready_without_thresholds(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pack = Path(directory)
            events = _events(count_per_code=2)
            keypoints = _keypoints_for_first_event_per_code(events)
            _write_pack(
                pack,
                events=events,
                keypoints=keypoints,
                semantics=_unobservable_semantics(events),
                coaches=_coach_labels(events),
                reviews=_reviews(),
            )
            result = compile_truth_pack(pack)
            self.assertEqual(
                result["status"], "ready_for_evaluation_and_calibration_review"
            )
            self.assertEqual(result["errors"], [])
            self.assertTrue(result["readiness"]["full_video_event_truth"])
            self.assertTrue(result["readiness"]["dense_adjudicated_keypoint_truth"])
            self.assertTrue(result["readiness"]["semantic_annotation_completion"])
            self.assertTrue(result["readiness"]["multi_coach_overlap"])
            self.assertEqual(
                len(result["readiness"]["indicator_readiness_matrix"]), 13
            )
            self.assertTrue(
                all(
                    item["covered"]
                    for item in result["readiness"]["indicator_readiness_matrix"]
                )
            )
            self.assertGreater(
                len(result["readiness"]["unobservable_semantic_cells"]), 0
            )
            self.assertFalse(result["safety"]["generated_thresholds"])


if __name__ == "__main__":
    unittest.main()
