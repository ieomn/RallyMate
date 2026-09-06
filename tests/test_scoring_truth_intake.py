from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from rallymate_annotation.scoring_truth_intake import (
    EXPORT_FIELDS,
    EXPORT_FILENAMES,
    INTAKE_STATUS,
    ScoringTruthIntakeError,
    ingest_scoring_truth_exports,
    validate_scoring_truth_intake,
)


ROOT = Path(__file__).resolve().parents[1]


def _csv_bytes(
    header: tuple[str, ...] | list[str], rows: list[list[str]] | None = None
) -> bytes:
    handle = io.StringIO(newline="")
    writer = csv.writer(handle, lineterminator="\r\n")
    writer.writerow(header)
    writer.writerows(rows or [])
    return b"\xef\xbb\xbf" + handle.getvalue().encode("utf-8")


def _make_source(root: Path, *, status: str = "test_only_annotation_required") -> Path:
    source = root / "synthetic-private-candidate-pack-test-only"
    source.mkdir()
    manifest = {
        "schema_version": "1.0.0",
        "pack_version": "synthetic-scoring-truth-pack-test-only",
        "created_at": "2026-08-30T00:00:00Z",
        "status": status,
        "videos": [{"video_id": "synthetic-video-test-only"}],
        "counts": {"dense_keypoint_frames": 0, "keypoint_joint_rows": 0},
        "candidate_source_binding": {
            "semantics": "synthetic_candidate_only_not_truth_test_fixture",
            "candidate_events_are_truth": False,
        },
        "safety": {
            "generated_thresholds": False,
            "automatic_F3_or_F4_promotion": False,
        },
    }
    (source / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (source / "README.md").write_bytes(
        b"PRIVATE synthetic candidate-containing test fixture only\r\n"
    )
    static = source / "static"
    static.mkdir()
    (static / "private-note.txt").write_bytes(b"candidate information\x00private")
    compiled = source / "compiled"
    compiled.mkdir()
    (compiled / "stale-output-must-not-be-copied.txt").write_bytes(b"stale")
    for filename, header in EXPORT_FIELDS.items():
        (source / filename).write_bytes(_csv_bytes(header))
    return source


def _exports(source: Path) -> dict[str, Path]:
    return {name: source / name for name in EXPORT_FILENAMES}


def _canonical_root(artifacts: list[dict]) -> str:
    rows = [
        {
            "path": item["path"],
            "bytes": item["bytes"],
            "sha256": item["sha256"],
            "role": item["role"],
        }
        for item in sorted(artifacts, key=lambda item: item["path"])
    ]
    raw = json.dumps(
        rows,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest().upper()


class ScoringTruthIntakeTests(unittest.TestCase):
    def test_blank_intake_is_atomic_replayable_and_machine_schema_valid(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = _make_source(root)
            session = root / "session-v1"

            manifest = ingest_scoring_truth_exports(
                source, _exports(source), session
            )

            self.assertEqual(manifest, validate_scoring_truth_intake(session))
            self.assertEqual(manifest["status"], INTAKE_STATUS)
            self.assertEqual(manifest["compilation"]["status"], "annotation_required")
            self.assertEqual(
                manifest["compilation"]["readiness_interpretation"],
                "diagnostic_only_not_authorization_or_calibration_eligibility",
            )
            self.assertFalse(
                (session / "compiled" / "stale-output-must-not-be-copied.txt").exists()
            )
            for filename in EXPORT_FILENAMES:
                source_raw = (source / filename).read_bytes()
                self.assertEqual((session / filename).read_bytes(), source_raw)
                self.assertEqual(
                    (session / "raw-exports" / filename).read_bytes(), source_raw
                )
            report = json.loads(
                (session / "compiled" / "validation-report.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(report["outputs"]["manual_events"], "compiled/manual-events.jsonl")
            self.assertNotIn(str(session), json.dumps(report, ensure_ascii=False))

            schema = json.loads(
                (ROOT / "contracts" / "scoring-truth-intake.schema.json").read_text(
                    encoding="utf-8"
                )
            )
            Draft202012Validator.check_schema(schema)
            Draft202012Validator(
                schema, format_checker=FormatChecker()
            ).validate(manifest)

    def test_invalid_annotations_leave_no_final_or_staging_tree(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = _make_source(root)
            event = source / "event-annotations.csv"
            row = [""] * len(EXPORT_FIELDS[event.name])
            row[EXPORT_FIELDS[event.name].index("event_id")] = "invalid-event"
            row[EXPORT_FIELDS[event.name].index("adjudication_status")] = "accepted"
            event.write_bytes(_csv_bytes(EXPORT_FIELDS[event.name], [row]))
            output = root / "rejected"

            with self.assertRaisesRegex(
                ScoringTruthIntakeError, "compiler rejected"
            ):
                ingest_scoring_truth_exports(source, _exports(source), output)

            self.assertFalse(output.exists())
            self.assertEqual(list(root.glob(".rejected.building-*")), [])

    def test_csv_encoding_header_column_and_repeated_header_are_fail_closed(self) -> None:
        mutations = {
            "invalid_utf8": lambda path, header: path.write_bytes(b"\xff\xfe\x00"),
            "duplicate_field": lambda path, header: path.write_bytes(
                _csv_bytes([*header[:-1], header[0]])
            ),
            "wrong_column_count": lambda path, header: path.write_bytes(
                _csv_bytes(header, [["only-one-column"]])
            ),
            "repeated_header_row": lambda path, header: path.write_bytes(
                _csv_bytes(header, [list(header)])
            ),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source = _make_source(root)
                event = source / "event-annotations.csv"
                mutate(event, EXPORT_FIELDS[event.name])
                output = root / "rejected"
                with self.assertRaises(ScoringTruthIntakeError):
                    ingest_scoring_truth_exports(source, _exports(source), output)
                self.assertFalse(output.exists())
                self.assertEqual(list(root.glob(".rejected.building-*")), [])

    def test_export_membership_overlap_and_existing_output_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = _make_source(root)
            exports = _exports(source)

            missing = dict(exports)
            missing.pop("coach-labels.csv")
            with self.assertRaisesRegex(ScoringTruthIntakeError, "exactly five"):
                ingest_scoring_truth_exports(source, missing, root / "missing")
            extra = dict(exports)
            extra["unexpected.csv"] = source / "event-annotations.csv"
            with self.assertRaisesRegex(ScoringTruthIntakeError, "extra"):
                ingest_scoring_truth_exports(source, extra, root / "extra")

            with self.assertRaisesRegex(ScoringTruthIntakeError, "ancestors"):
                ingest_scoring_truth_exports(source, exports, source / "nested")
            with self.assertRaisesRegex(ScoringTruthIntakeError, "ancestors"):
                ingest_scoring_truth_exports(source, exports, root)

            existing = root / "existing"
            existing.mkdir()
            sentinel = existing / "sentinel.bin"
            sentinel.write_bytes(b"preserve")
            with self.assertRaises(FileExistsError):
                ingest_scoring_truth_exports(source, exports, existing)
            self.assertEqual(sentinel.read_bytes(), b"preserve")

    def test_original_source_and_export_tampering_are_detected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = _make_source(root)
            original_manifest = (source / "manifest.json").read_bytes()
            session = root / "source-tamper-session"
            ingest_scoring_truth_exports(source, _exports(source), session)
            (source / "manifest.json").write_bytes(original_manifest + b"\n")
            with self.assertRaisesRegex(ScoringTruthIntakeError, "source bytes changed"):
                validate_scoring_truth_intake(session)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = _make_source(root)
            session = root / "export-tamper-session"
            ingest_scoring_truth_exports(source, _exports(source), session)
            path = source / "event-annotations.csv"
            path.write_bytes(path.read_bytes() + b"\r\n")
            with self.assertRaisesRegex(ScoringTruthIntakeError, "source export changed"):
                validate_scoring_truth_intake(session)

    def test_session_extra_missing_and_byte_tampering_are_detected(self) -> None:
        cases = ("extra", "missing", "changed")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source = _make_source(root)
                session = root / "session"
                ingest_scoring_truth_exports(source, _exports(source), session)
                if case == "extra":
                    (session / "unexpected.txt").write_bytes(b"extra")
                    pattern = "topology"
                elif case == "missing":
                    (session / "raw-exports" / "coach-labels.csv").unlink()
                    pattern = "missing|topology"
                else:
                    target = session / "raw-exports" / "coach-labels.csv"
                    target.write_bytes(target.read_bytes() + b"changed")
                    pattern = "bytes changed"
                with self.assertRaisesRegex(ScoringTruthIntakeError, pattern):
                    validate_scoring_truth_intake(session)

    def test_compiled_artifact_co_rehash_is_rejected_by_replay(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = _make_source(root)
            session = root / "session"
            ingest_scoring_truth_exports(source, _exports(source), session)
            target = session / "compiled" / "manual-events.jsonl"
            target.write_bytes(b'{}\n')
            manifest_path = session / "intake-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            artifact = next(
                item
                for item in manifest["artifacts"]
                if item["path"] == "compiled/manual-events.jsonl"
            )
            raw = target.read_bytes()
            artifact["bytes"] = len(raw)
            artifact["sha256"] = hashlib.sha256(raw).hexdigest().upper()
            manifest["content_root_sha256"] = _canonical_root(manifest["artifacts"])
            manifest["intake_id"] = (
                "scoring-truth-intake-" + manifest["content_root_sha256"][:24]
            )
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
            )

            with self.assertRaisesRegex(ScoringTruthIntakeError, "replay"):
                validate_scoring_truth_intake(session)

    def test_authorization_invariant_is_replayed_not_inferred_from_source_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = _make_source(
                root, status="technical_handoff_verified_external_protocol_required"
            )
            session = root / "session"
            manifest = ingest_scoring_truth_exports(source, _exports(source), session)
            safety = manifest["safety"]
            for key in (
                "operator_authorized",
                "external_protocol_receipt_verified",
                "technical_handoff_accepted_as_authorization",
                "source_authorization_accepted",
                "human_truth_ready_claimed",
                "calibration_eligible",
                "promotion_eligible",
                "production_use_authorized",
                "grades_generated_by_intake",
                "thresholds_generated",
                "automatic_F3_or_F4_promotion",
            ):
                self.assertFalse(safety[key], key)
            self.assertEqual(manifest["classification"], "PRIVATE")
            self.assertEqual(
                manifest["source_pack"]["retention_requirement"],
                "original_source_pack_and_all_five_export_paths_must_remain_"
                "available_and_byte_exact_for_private_authority_replay",
            )

            tampered = copy.deepcopy(manifest)
            tampered["safety"]["operator_authorized"] = True
            (session / "intake-manifest.json").write_text(
                json.dumps(tampered, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            with self.assertRaisesRegex(ScoringTruthIntakeError, "safety"):
                validate_scoring_truth_intake(session)

    def test_generated_at_requires_a_real_canonical_utc_instant(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = _make_source(root)
            session = root / "session"
            manifest = ingest_scoring_truth_exports(source, _exports(source), session)
            manifest["generated_at"] = "2026-99-99T25:61:61.000000Z"
            (session / "intake-manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            with self.assertRaisesRegex(ScoringTruthIntakeError, "semantically valid"):
                validate_scoring_truth_intake(session)

    def test_cli_reports_validation_errors_as_exit_two_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = _make_source(root)
            (source / "event-annotations.csv").write_bytes(b"\xff")
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(ROOT / "src")
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "ingest_scoring_truth_exports.py"),
                    "--source-pack",
                    str(source),
                    "--exports-dir",
                    str(source),
                    "--output-dir",
                    str(root / "output"),
                ],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("strict UTF-8 CSV", result.stderr)
            self.assertNotIn("Traceback", result.stderr)
            self.assertFalse((root / "output").exists())


if __name__ == "__main__":
    unittest.main()
