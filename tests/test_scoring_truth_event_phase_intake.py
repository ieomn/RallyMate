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
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest import mock

from jsonschema import Draft202012Validator

import rallymate_annotation.scoring_truth_event_phase_intake as intake_module
from rallymate_annotation.scoring_truth_event_phase_intake import (
    EVENT_ANNOTATIONS_PATH,
    FULL_VIDEO_REVIEW_PATH,
    INTAKE_MANIFEST_NAME,
    MANUAL_EVENTS_PATH,
    RAW_A_PATH,
    RAW_B_PATH,
    RAW_C_PATH,
    ScoringTruthEventPhaseIntakeError,
    ingest_scoring_truth_event_phase,
    validate_scoring_truth_event_phase_intake,
)
from rallymate_annotation.scoring_truth_event_execution import (
    scoring_truth_event_manifest_binding_sha256,
)


ROOT = Path(__file__).resolve().parents[1]
PYTHON = Path(sys.executable)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest().upper()


def _raw(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _phases(*, unobservable_landing: bool = False) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {
        "preload_ms": {"status": "observed", "timestamp_ms": 100, "reason": ""},
        "takeoff_proxy_ms": {"status": "observed", "timestamp_ms": 200, "reason": ""},
        "landing_proxy_ms": {"status": "observed", "timestamp_ms": 300, "reason": ""},
        "redistribution_ms": {"status": "observed", "timestamp_ms": 400, "reason": ""},
        "initiation_ms": {"status": "observed", "timestamp_ms": 500, "reason": ""},
    }
    if unobservable_landing:
        result["landing_proxy_ms"] = {
            "status": "unobservable",
            "timestamp_ms": None,
            "reason": "occluded in the synthetic fixture",
        }
    return result


def _binding() -> dict[str, object]:
    return {
        "binding_version": "scoring-truth-event-authorization-binding-v2.0.0",
        "status": "operator_reviewed_local_release_verified",
        "canonicalization": "rallymate-canonical-json-v1",
        "plan": {
            "plan_id": "synthetic-m93-plan",
            "plan_version": "scoring-truth-event-annotation-plan-v2.0.0",
            "raw_sha256": "1" * 64,
        },
        "release_record": {
            "release_id": "synthetic-m93-release",
            "release_version": "scoring-truth-operator-reviewed-local-release-v1.0.0",
            "raw_sha256": "2" * 64,
            "released_at": "2026-09-02T00:00:00.000Z",
            "reviewer_id": "synthetic-operator",
            "reviewer_role": "annotation_release_operator",
            "decision": "operator_released_for_independent_event_phase_annotation",
        },
        "technical_handoff": {
            "manifest_raw_sha256": "3" * 64,
            "bundle_version": "scoring-truth-event-handoff-v1.0.0",
            "bundle_id": "synthetic-m89-bundle",
            "content_root_sha256": "4" * 64,
            "source_projection_sha256": "5" * 64,
        },
        "scope_digest_sha256": "6" * 64,
        "role_protocol_digest_sha256": "7" * 64,
        "operator_release_record_verified": True,
        "annotation_workflow_release_only": True,
        "binding_sha256": "8" * 64,
    }


def _source_event(slot: str) -> dict[str, object]:
    annotation_id = f"m93:{slot}:synthetic-event"
    return {
        "annotation_id": annotation_id,
        "event_id": annotation_id,
        "event_code": "FS01",
        "start_ms": 0,
        "end_ms": 600,
        "phase_observations": _phases(),
        "confidence_milli": 875,
        "boundary_uncertainty_ms": 15,
        "notes": f"synthetic source {slot}",
        "annotated_at": "2026-09-02T00:01:00.000Z",
        "annotation_revision_sha256": ("A" if slot == "A" else "B") * 64,
    }


def _review(slot: str, index: int) -> dict[str, object]:
    return {
        "completed": True,
        "reviewed_at": f"2026-09-02T00:0{index + 1}:00.000Z",
        "notes": f"synthetic full review {slot}-{index}",
        "review_revision_sha256": ("C" if slot == "A" else "D") * 64,
    }


class SyntheticInputs:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.plan_path = root / "plan.json"
        self.release_path = root / "release.json"
        self.handoff = root / "handoff"
        self.handoff.mkdir()
        self.plan = {
            "scope": {
                "tasks": [
                    {
                        "task_id": f"m89:video-{index}:full-video-event-phase",
                        "video_id": f"video-{index}",
                    }
                    for index in range(1, 4)
                ]
            }
        }
        self.plan_path.write_bytes(_raw(self.plan))
        self.release_path.write_bytes(_raw({"synthetic": True}))
        (self.handoff / "event-handoff-manifest.json").write_bytes(
            _raw({"synthetic": True})
        )
        self.binding = _binding()
        self.execution_id = "synthetic-m93-execution"
        self.bundle_refs = {
            "A": {
                "bundle_id": "synthetic-execution-A",
                "manifest_binding_sha256": "9" * 64,
            },
            "B": {
                "bundle_id": "synthetic-execution-B",
                "manifest_binding_sha256": "E" * 64,
            },
            "C": {
                "bundle_id": "synthetic-adjudication-C",
                "manifest_binding_sha256": "F" * 64,
            },
        }
        self.bundle_snapshots = {
            "A": self._bundle_snapshot("A", "0" * 64),
            "B": self._bundle_snapshot("B", "1" * 64),
            "C": self._bundle_snapshot("C", "2" * 64),
        }
        self.submissions = {
            "A": self._annotation_submission("A", "coach-a", "3" * 64),
            "B": self._annotation_submission("B", "coach-b", "4" * 64),
        }
        self.submission_raw = {
            slot: _raw(submission) for slot, submission in self.submissions.items()
        }
        self.submissions["C"] = self._adjudication_submission()
        self.submission_raw["C"] = _raw(self.submissions["C"])
        self.submission_snapshots = {
            slot: self._submission_snapshot(slot) for slot in ("A", "B", "C")
        }
        self.bundle_dirs = {
            "A": root / "execution-A",
            "B": root / "execution-B",
            "C": root / "adjudication-C",
        }
        self.submission_paths = {
            "A": root / "A.json",
            "B": root / "B.json",
            "C": root / "C.json",
        }

    @property
    def video_ids(self) -> list[str]:
        return [f"video-{index}" for index in range(1, 4)]

    def _bundle_snapshot(self, slot: str, content_root: str) -> dict[str, object]:
        manifest: dict[str, object] = {
            "bundle_id": self.bundle_refs[slot]["bundle_id"],
            "execution_id": self.execution_id,
            "generated_at": (
                "2026-09-02T00:07:00Z"
                if slot == "C"
                else "2026-09-02T00:00:30Z"
            ),
            "manifest_binding_sha256": "0" * 64,
            "content_root_sha256": content_root,
            "event_authorization": self.binding,
        }
        if slot != "C":
            manifest["role_slot"] = slot
        else:
            manifest["role"] = {"slot": "C"}
        manifest["manifest_binding_sha256"] = (
            scoring_truth_event_manifest_binding_sha256(manifest)
        )
        self.bundle_refs[slot]["manifest_binding_sha256"] = manifest[
            "manifest_binding_sha256"
        ]
        raw = _raw(manifest)
        return {
            "bundle_dir": self.root / f"bundle-{slot}",
            "manifest": manifest,
            "manifest_raw": raw,
            "manifest_sha256": _sha(raw),
            "manifest_binding_sha256": manifest["manifest_binding_sha256"],
            "content_root_sha256": content_root,
            "artifacts": [],
        }

    def _annotation_submission(
        self, slot: str, annotator_id: str, revision: str
    ) -> dict[str, object]:
        videos = []
        for index, video_id in enumerate(self.video_ids):
            videos.append(
                {
                    "task_id": f"m89:{video_id}:full-video-event-phase",
                    "video_id": video_id,
                    "full_video_review": _review(slot, index),
                    "events": [_source_event(slot)] if index == 0 else [],
                    "video_revision_sha256": (
                        ("5" if slot == "A" else "6") * 63 + str(index)
                    ),
                }
            )
        return {
            "schema_version": "1.0.0",
            "submission_version": "scoring-truth-event-annotation-submission-v1.0.0",
            "status": "full_video_event_phase_annotation_submitted",
            "artifact_scope": "independent_full_video_event_phase_annotation",
            "execution_id": self.execution_id,
            "execution_bundle": self.bundle_refs[slot],
            "authorization_binding_sha256": self.binding["binding_sha256"],
            "submission_id": f"submission-{slot.lower()}-synthetic",
            "role_slot": slot,
            "annotator_id": annotator_id,
            "videos": videos,
            "submitted_at": "2026-09-02T00:05:00.000Z",
            "submission_revision_sha256": revision,
            "exported_at": "2026-09-02T00:06:00.000Z",
        }

    def _adjudication_submission(self) -> dict[str, object]:
        source_summary = {}
        for slot in ("A", "B"):
            submission = self.submissions[slot]
            source_summary[slot] = {
                "submission_id": submission["submission_id"],
                "raw_sha256": _sha(self.submission_raw[slot]),
                "annotator_id": submission["annotator_id"],
                "execution_bundle": submission["execution_bundle"],
                "submission_revision_sha256": submission[
                    "submission_revision_sha256"
                ],
                "video_revision_sha256_by_video": {
                    video["video_id"]: video["video_revision_sha256"]
                    for video in submission["videos"]
                },
            }
        source_video_revisions = {
            slot: self.submissions[slot]["videos"][0]["video_revision_sha256"]
            for slot in ("A", "B")
        }
        refs = [
            {
                "role_slot": slot,
                "annotation_id": self.submissions[slot]["videos"][0]["events"][0][
                    "annotation_id"
                ],
                "annotation_revision_sha256": self.submissions[slot]["videos"][0][
                    "events"
                ][0]["annotation_revision_sha256"],
                "relation": "merge_source",
            }
            for slot in ("A", "B")
        ]
        final_event = {
            "event_id": "m93:C:synthetic-final-event",
            "event_code": "FS01",
            "start_ms": 0,
            "end_ms": 600,
            "phase_observations": _phases(unobservable_landing=True),
            "confidence_milli": 900,
            "boundary_uncertainty_ms": 10,
            "notes": "synthetic final decision",
        }
        video_adjudications = [
            {
                "task_id": f"m89:{video_id}:full-video-event-phase",
                "video_id": video_id,
                "completed": True,
                "notes": f"synthetic C full-video review {video_id}",
                "adjudicated_at": "2026-09-02T00:07:00.000Z",
                "review_revision_sha256": (str(index + 7) * 64),
            }
            for index, video_id in enumerate(self.video_ids)
        ]
        return {
            "schema_version": "1.0.0",
            "adjudication_version": "scoring-truth-event-adjudication-submission-v1.0.0",
            "status": "event_phase_adjudication_finalized",
            "artifact_scope": "independent_full_video_event_phase_adjudication",
            "execution_id": self.execution_id,
            "adjudication_bundle": self.bundle_refs["C"],
            "authorization_binding_sha256": self.binding["binding_sha256"],
            "reviewer_slot": "C",
            "reviewer_id": "coach-c",
            "source_submissions": source_summary,
            "video_adjudications": video_adjudications,
            "decisions": [
                {
                    "adjudication_id": "m93:C:synthetic-adjudication",
                    "video_id": "video-1",
                    "decision_status": "accepted_event",
                    "source_video_revisions": source_video_revisions,
                    "source_annotation_revisions": refs,
                    "event": final_event,
                    "decision_reason": "merged synthetic A and B",
                    "adjudicated_at": "2026-09-02T00:08:00.000Z",
                    "adjudication_revision_sha256": "A" * 64,
                }
            ],
            "adjudicated_at": "2026-09-02T00:09:00.000Z",
            "adjudication_submission_revision_sha256": "B" * 64,
            "exported_at": "2026-09-02T00:10:00.000Z",
        }

    def _submission_snapshot(self, slot: str) -> dict[str, object]:
        submission = self.submissions[slot]
        raw = self.submission_raw[slot]
        result = {
            "submission": submission,
            "submission_raw": raw,
            "submission_raw_sha256": _sha(raw),
        }
        if slot == "C":
            result["adjudication_submission_revision_sha256"] = submission[
                "adjudication_submission_revision_sha256"
            ]
        else:
            result["submission_revision_sha256"] = submission[
                "submission_revision_sha256"
            ]
        return result

    def execution_api(self):
        def validate_bundle(_path, *, expected_role_slot=None):
            return self.bundle_snapshots[expected_role_slot]

        def validate_submission(_submission, bundle):
            slot = "A" if Path(bundle).name.endswith("A") else "B"
            return self.submission_snapshots[slot]

        def validate_c_bundle(_path):
            return self.bundle_snapshots["C"]

        def validate_c_submission(_submission, _bundle):
            return self.submission_snapshots["C"]

        return (
            validate_bundle,
            validate_submission,
            validate_c_bundle,
            validate_c_submission,
        )

    def kwargs(self, output: Path) -> dict[str, Path]:
        return {
            "plan_path": self.plan_path,
            "release_record_path": self.release_path,
            "handoff_dir": self.handoff,
            "execution_a_bundle_dir": self.bundle_dirs["A"],
            "execution_a_submission_path": self.submission_paths["A"],
            "execution_b_bundle_dir": self.bundle_dirs["B"],
            "execution_b_submission_path": self.submission_paths["B"],
            "adjudication_bundle_dir": self.bundle_dirs["C"],
            "adjudication_submission_path": self.submission_paths["C"],
            "output_dir": output,
        }

    @contextmanager
    def patched(self):
        with ExitStack() as stack:
            stack.enter_context(
                mock.patch.object(
                    intake_module,
                    "verify_scoring_truth_event_authorization",
                    return_value=self.binding,
                )
            )
            stack.enter_context(
                mock.patch.object(
                    intake_module,
                    "validate_scoring_truth_authorization_binding",
                    return_value=None,
                )
            )
            stack.enter_context(
                mock.patch.object(
                    intake_module, "_execution_api", return_value=self.execution_api()
                )
            )
            yield


class ScoringTruthEventPhaseIntakeTests(unittest.TestCase):
    def _ingest(self, root: Path) -> tuple[SyntheticInputs, Path, dict[str, object]]:
        inputs = SyntheticInputs(root)
        output = root / "intake"
        with inputs.patched():
            manifest = ingest_scoring_truth_event_phase(**inputs.kwargs(output))
        return inputs, output, manifest

    def test_positive_private_intake_preserves_raw_and_projects_final_event(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs, output, manifest = self._ingest(root)
            self.assertEqual(
                "private_event_phase_intake_finalized_annotation_only",
                manifest["status"],
            )
            self.assertEqual("PRIVATE", manifest["classification"])
            self.assertEqual(inputs.submission_raw["A"], (output / RAW_A_PATH).read_bytes())
            self.assertEqual(inputs.submission_raw["B"], (output / RAW_B_PATH).read_bytes())
            self.assertEqual(inputs.submission_raw["C"], (output / RAW_C_PATH).read_bytes())

            record = json.loads((output / MANUAL_EVENTS_PATH).read_text(encoding="utf-8"))
            event_schema = json.loads(
                (ROOT / "contracts" / "events.schema.json").read_text(
                    encoding="utf-8"
                )
            )
            Draft202012Validator(event_schema).validate(record)
            self.assertEqual(900, record["confidence_milli"])
            self.assertEqual(0.9, record["confidence"])
            self.assertIsNone(record["key_phases_ms"]["landing_proxy_ms"])
            self.assertEqual(2, len(record["source_annotation_revisions"]))
            self.assertEqual(
                manifest["revision_lineage"], record["revision_lineage"]
            )

            with (output / EVENT_ANNOTATIONS_PATH).open(
                encoding="utf-8", newline=""
            ) as handle:
                event_rows = list(csv.DictReader(handle))
            self.assertEqual(1, len(event_rows))
            self.assertEqual("", event_rows[0]["landing_proxy_ms"])
            self.assertIn(
                "manual_phase_unobservable:landing_proxy_ms",
                event_rows[0]["quality_flags"],
            )
            with (output / FULL_VIDEO_REVIEW_PATH).open(
                encoding="utf-8", newline=""
            ) as handle:
                review_rows = list(csv.DictReader(handle))
            self.assertEqual(6, len(review_rows))
            report = json.loads(
                (output / "compiled" / "validation-report.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(3, report["counts"]["video_adjudications"])
            self.assertFalse(report["safety"]["calibration_authorized"])

            with inputs.patched():
                replay = validate_scoring_truth_event_phase_intake(output)
            self.assertEqual(manifest["content_root_sha256"], replay["content_root_sha256"])

    def test_manifest_validates_against_draft_2020_12_schema(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            inputs, output, manifest = self._ingest(Path(directory))
            schema = json.loads(
                (ROOT / "contracts" / "scoring-truth-event-phase-intake.schema.json").read_text(
                    encoding="utf-8"
                )
            )
            Draft202012Validator.check_schema(schema)
            Draft202012Validator(schema).validate(manifest)
            with inputs.patched():
                validate_scoring_truth_event_phase_intake(output)

    def test_generated_at_covers_all_source_manifests_and_exports(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = SyntheticInputs(root)
            output = root / "intake"
            with inputs.patched(), mock.patch.object(
                intake_module,
                "_utc_now",
                return_value="2026-09-02T00:00:01.000000Z",
            ):
                manifest = ingest_scoring_truth_event_phase(
                    **inputs.kwargs(output)
                )
            self.assertEqual(
                "2026-09-02T00:10:00.000000Z", manifest["generated_at"]
            )

            manifest_path = output / INTAKE_MANIFEST_NAME
            changed = json.loads(manifest_path.read_text(encoding="utf-8"))
            changed["generated_at"] = "2026-09-02T00:00:01.000000Z"
            manifest_path.write_text(
                json.dumps(changed, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            with inputs.patched(), self.assertRaisesRegex(
                ScoringTruthEventPhaseIntakeError, "predates a source bundle"
            ):
                validate_scoring_truth_event_phase_intake(output)

    def test_roles_are_distinct_case_insensitively_and_failure_is_atomic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = SyntheticInputs(root)
            inputs.submissions["B"]["annotator_id"] = "COACH-A"
            inputs.submission_raw["B"] = _raw(inputs.submissions["B"])
            inputs.submission_snapshots["B"] = inputs._submission_snapshot("B")
            output = root / "rejected"
            with inputs.patched(), self.assertRaisesRegex(
                ScoringTruthEventPhaseIntakeError, "must be distinct"
            ):
                ingest_scoring_truth_event_phase(**inputs.kwargs(output))
            self.assertFalse(output.exists())
            self.assertEqual([], list(root.glob(".rejected.building-*")))

    def test_missing_c_source_coverage_is_rejected_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = SyntheticInputs(root)
            decision = inputs.submissions["C"]["decisions"][0]
            decision["source_annotation_revisions"] = decision[
                "source_annotation_revisions"
            ][:1]
            inputs.submission_raw["C"] = _raw(inputs.submissions["C"])
            inputs.submission_snapshots["C"] = inputs._submission_snapshot("C")
            output = root / "rejected"
            with inputs.patched(), self.assertRaisesRegex(
                ScoringTruthEventPhaseIntakeError, "source coverage"
            ):
                ingest_scoring_truth_event_phase(**inputs.kwargs(output))
            self.assertFalse(output.exists())
            self.assertEqual([], list(root.glob(".rejected.building-*")))

    def test_missing_video_adjudication_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = SyntheticInputs(root)
            inputs.submissions["C"]["video_adjudications"].pop()
            inputs.submission_raw["C"] = _raw(inputs.submissions["C"])
            inputs.submission_snapshots["C"] = inputs._submission_snapshot("C")
            output = root / "rejected"
            with inputs.patched(), self.assertRaisesRegex(
                ScoringTruthEventPhaseIntakeError, "exactly three"
            ):
                ingest_scoring_truth_event_phase(**inputs.kwargs(output))
            self.assertFalse(output.exists())

    def test_raw_compiled_and_topology_changes_fail_closed(self) -> None:
        cases = ("raw", "compiled_rehash", "extra")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                inputs, output, _manifest = self._ingest(root)
                if case == "raw":
                    target = output / RAW_A_PATH
                    target.write_bytes(target.read_bytes() + b" ")
                elif case == "extra":
                    (output / "extra.txt").write_bytes(b"extra")
                else:
                    target = output / MANUAL_EVENTS_PATH
                    target.write_bytes(b"\n")
                    manifest_path = output / INTAKE_MANIFEST_NAME
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                    artifact = next(
                        item
                        for item in manifest["artifacts"]
                        if item["path"] == MANUAL_EVENTS_PATH
                    )
                    artifact["bytes"] = 1
                    artifact["sha256"] = _sha(b"\n")
                    manifest["compilation"]["manual_events"]["sha256"] = _sha(b"\n")
                    manifest["content_root_sha256"] = intake_module._content_root(
                        manifest["artifacts"]
                    )
                    manifest_path.write_text(
                        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                    )
                with inputs.patched(), self.assertRaises(
                    ScoringTruthEventPhaseIntakeError
                ):
                    validate_scoring_truth_event_phase_intake(output)

    def test_cli_validation_error_is_exit_two_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            malformed = root / "malformed"
            malformed.mkdir()
            (malformed / INTAKE_MANIFEST_NAME).write_bytes(b"{}\n")
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(ROOT / "src")
            result = subprocess.run(
                [
                    str(PYTHON),
                    str(ROOT / "scripts" / "ingest_scoring_truth_event_phase.py"),
                    "--validate-only",
                    str(malformed),
                ],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(2, result.returncode)
            self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
