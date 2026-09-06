from __future__ import annotations

import copy
import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from jsonschema import Draft202012Validator, FormatChecker

from rallymate_evaluation.fs09_phase_truth import (
    ADJUDICATION_FIELDS,
    ANNOTATION_FIELDS,
    FS09PhaseTruthError,
    PHASE_KEYS,
    PHASE_PREFIXES,
    _annotation_revision_sha256,
    build_fs09_phase_truth_pack,
    compile_fs09_phase_truth_pack,
    evaluate_fs09_phase_truth,
    ingest_fs09_phase_truth_exports as _ingest_fs09_phase_truth_exports,
    validate_fs09_phase_analysis_plan,
    validate_fs09_phase_review_clip_manifest,
    validate_fs09_phase_truth_manifest,
    validate_fs09_phase_truth_intake_manifest,
    validate_fs09_phase_truth_report,
    validate_fs09_phase_truth_report_sources,
)
from rallymate_evaluation.fs09_phase_handoff import (
    build_fs09_phase_blind_handoff,
    validate_fs09_phase_blind_handoff as _real_validate_fs09_phase_blind_handoff,
)
from rallymate_evaluation.small_roi_keypoint_truth import _canonical_sha256, sha256_file
from rallymate_features import clear_feature_cache as _real_clear_feature_cache
from rallymate_features import compute_event_features as _real_compute_event_features


ROOT = Path(__file__).resolve().parents[1]
M74 = ROOT / "reports" / "measurement-recovery-m74" / "event-bounded-gap-audit-v1" / "report.json"
ASSETS = ROOT / "src" / "rallymate_annotation" / "assets"
REAL_PACK = ROOT / "data" / "annotations" / "fs09-phase-truth-m77-v1"
REAL_REPORT = ROOT / "reports" / "measurement-recovery-m77" / "fs09-phase-truth-empty.json"
REVIEW_CLIPS = ROOT / "reports" / "measurement-recovery-m88" / "review-clips-anchor-v1" / "manifest.json"
ANALYSIS_PLAN = ROOT / "data" / "analysis-plans" / "fs09-phase-truth-m77-descriptive-v1.json"


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _write_csv(path: Path, fields: tuple[str, ...], rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields))
        writer.writeheader()
        writer.writerows(rows)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n" for row in rows), encoding="utf-8")


def _write_json(path: Path, value: dict) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _tree_snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _build(root: Path) -> Path:
    pack = root / "pack"
    build_fs09_phase_truth_pack(
        m74_report_path=M74,
        review_clip_manifest_path=REVIEW_CLIPS,
        analysis_plan_path=ANALYSIS_PLAN,
        output_dir=pack,
        asset_directory=ASSETS,
    )
    build_fs09_phase_blind_handoff(pack, root / "handoff")
    return pack


def ingest_fs09_phase_truth_exports(
    *,
    pack_dir: str | Path,
    annotation_exports: list[str | Path] | tuple[str | Path, ...],
    adjudication_export: str | Path,
    output_dir: str | Path,
    handoff_manifest_path: str | Path | None = None,
) -> dict:
    pack = Path(pack_dir).resolve()
    return _ingest_fs09_phase_truth_exports(
        pack_dir=pack,
        annotation_exports=annotation_exports,
        adjudication_export=adjudication_export,
        handoff_manifest_path=(
            Path(handoff_manifest_path)
            if handoff_manifest_path is not None
            else pack.parent / "handoff" / "handoff-manifest.json"
        ),
        output_dir=output_dir,
    )


def _complete(pack: Path, *, event_present: bool = True, reverse_phase_order: bool = False) -> None:
    candidates = {row["task_id"]: row for row in _jsonl(pack / "sealed-event-candidates.jsonl")}
    manifest = json.loads((pack / "manifest.json").read_text(encoding="utf-8"))
    handoff = json.loads(
        (pack.parent / "handoff" / "handoff-manifest.json").read_text(
            encoding="utf-8"
        )
    )
    handoff_time = datetime.fromisoformat(
        handoff["generated_at"].replace("Z", "+00:00")
    )
    now = datetime.now(timezone.utc)
    if now <= handoff_time:
        raise AssertionError("synthetic label clock did not advance past handoff creation")
    span = now - handoff_time
    annotated_at = (handoff_time + span / 5).isoformat().replace("+00:00", "Z")
    annotation_exported_at = (handoff_time + span * 2 / 5).isoformat().replace(
        "+00:00", "Z"
    )
    adjudicated_at = (handoff_time + span * 3 / 5).isoformat().replace(
        "+00:00", "Z"
    )
    adjudication_exported_at = (handoff_time + span * 4 / 5).isoformat().replace(
        "+00:00", "Z"
    )
    handoff_binding = {
        "handoff_bundle_id": handoff["bundle_id"],
        "analysis_plan_sha256": handoff["source_authority"][
            "analysis_plan_sha256"
        ],
    }
    annotations: list[dict] = []
    adjudications: list[dict] = []
    for task_id, candidate in sorted(candidates.items()):
        values = {
            "event_present": str(event_present).lower(),
            "event_start_ms": str(candidate["start_ms"]) if event_present else "",
            "event_end_ms": str(candidate["end_ms"]) if event_present else "",
            "event_reason": "" if event_present else "synthetic_event_absent",
            "confidence": "0.9",
            "notes": "synthetic_contract_only",
        }
        for prefix, key in zip(PHASE_PREFIXES, PHASE_KEYS):
            values[f"{prefix}_status"] = "observed" if event_present else "not_observed"
            values[f"{prefix}_ms"] = str(candidate["key_phases_ms"][key]) if event_present else ""
            values[f"{prefix}_reason"] = "" if event_present else "synthetic_phase_absent"
        if reverse_phase_order and event_present:
            values["peak_speed_ms"], values["stable_control_onset_ms"] = values["stable_control_onset_ms"], values["peak_speed_ms"]
        source_ids = []
        source_revisions = []
        for annotator in ("synthetic-a", "synthetic-b"):
            annotation_id = f"{annotator}:{task_id}"
            source_ids.append(annotation_id)
            annotation = {"annotation_id": annotation_id, "task_id": task_id, "annotator_id": annotator, **handoff_binding, **values, "annotated_at": annotated_at, "exported_at": annotation_exported_at}
            annotation["annotation_revision_sha256"] = _annotation_revision_sha256(annotation)
            source_revisions.append(annotation["annotation_revision_sha256"])
            annotations.append(annotation)
        ordered_sources = sorted(zip(source_ids, source_revisions, strict=True))
        adjudications.append({"adjudication_id": f"synthetic-review:{task_id}", "task_id": task_id, "source_annotation_ids": ";".join(value[0] for value in ordered_sources), "source_annotation_revision_sha256s": ";".join(value[1] for value in ordered_sources), "reviewer_id": "synthetic-reviewer", **handoff_binding, **values, "adjudicated_at": adjudicated_at, "exported_at": adjudication_exported_at, "status": "accepted"})
    _write_csv(pack / "annotations.csv", ANNOTATION_FIELDS, annotations)
    _write_csv(pack / "adjudications.csv", ADJUDICATION_FIELDS, adjudications)


def _export_files(
    root: Path,
    pack: Path,
    *,
    event_present: bool = True,
    reverse_phase_order: bool = False,
) -> tuple[list[Path], Path]:
    _complete(
        pack,
        event_present=event_present,
        reverse_phase_order=reverse_phase_order,
    )
    annotations = _read_csv(pack / "annotations.csv")
    adjudications = _read_csv(pack / "adjudications.csv")
    annotation_paths: list[Path] = []
    for annotator in ("synthetic-a", "synthetic-b"):
        path = root / f"m77-annotations-{annotator}.csv"
        _write_csv(
            path,
            ANNOTATION_FIELDS,
            [row for row in annotations if row["annotator_id"] == annotator],
        )
        annotation_paths.append(path)
    adjudication_path = root / "m77-adjudications-synthetic-reviewer.csv"
    _write_csv(adjudication_path, ADJUDICATION_FIELDS, adjudications)
    _write_csv(pack / "annotations.csv", ANNOTATION_FIELDS, [])
    _write_csv(pack / "adjudications.csv", ADJUDICATION_FIELDS, [])
    return annotation_paths, adjudication_path


def _run_evaluator_cli(pack: Path, output: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    current_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(ROOT / "src") + (
        os.pathsep + current_pythonpath if current_pythonpath else ""
    )
    return subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "evaluate_m77_fs09_phase_truth.py"),
            "--pack",
            str(pack),
            "--output",
            str(output),
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _test_only_authorized_handoff_snapshot(*args, **kwargs) -> dict:
    """Keep compile/evaluation coverage without creating a fake production receipt.

    The production handoff remains technical-only and fail-closed.  This helper changes
    only the parsed in-memory snapshot inside this test process; it neither writes an
    authorized manifest nor creates a repository-trusted authorization path.
    """
    snapshot = _real_validate_fs09_phase_blind_handoff(*args, **kwargs)
    snapshot = copy.deepcopy(snapshot)
    snapshot["manifest"]["status"] = (
        "annotation_authorized_by_verified_external_protocol_receipt"
    )
    snapshot["manifest"]["safety"]["annotation_execution_authorized"] = True
    snapshot["manifest"]["safety"]["external_protocol_receipt_verified"] = True
    return snapshot


class FS09PhaseTruthTests(unittest.TestCase):
    def setUp(self) -> None:
        self._test_authorization_patch = patch(
            "rallymate_evaluation.fs09_phase_handoff."
            "validate_fs09_phase_blind_handoff",
            side_effect=_test_only_authorized_handoff_snapshot,
        )
        self._test_authorization_patch.start()
        self.addCleanup(self._test_authorization_patch.stop)

    def test_analysis_plan_is_schema_valid_repository_freeze_not_external_registration(self) -> None:
        self.assertIn(
            "date-time",
            FormatChecker.checkers,
            "install the declared rfc3339-validator dev dependency",
        )
        self.assertFalse(
            FormatChecker().conforms("2026-99-99T99:99:99Z", "date-time")
        )
        raw = ANALYSIS_PLAN.read_bytes()
        plan = json.loads(raw.decode("utf-8"))
        clip_manifest = json.loads(REVIEW_CLIPS.read_text(encoding="utf-8"))
        schema = json.loads(
            (ROOT / "contracts" / "fs09-phase-analysis-plan.schema.json").read_text(
                encoding="utf-8"
            )
        )
        Draft202012Validator.check_schema(schema)
        self.assertEqual(
            [],
            list(
                Draft202012Validator(
                    schema, format_checker=FormatChecker()
                ).iter_errors(plan)
            ),
        )
        validate_fs09_phase_analysis_plan(plan)
        self.assertEqual(
            "5B33C036C2A7674A4414975D4457D87BE801CC9E382D636861E71ED377D340A9",
            sha256_file(ANALYSIS_PLAN),
        )
        self.assertLess(
            datetime.fromisoformat(
                clip_manifest["generated_at"].replace("Z", "+00:00")
            ),
            datetime.fromisoformat(plan["frozen_at"].replace("Z", "+00:00")),
        )
        self.assertFalse(plan["registration_claim"]["external_registry_present"])
        self.assertFalse(
            plan["registration_claim"]["externally_registered_acceptance_protocol"]
        )
        self.assertIsNone(plan["decision_authority"]["acceptance_thresholds"])
        self.assertFalse(plan["decision_authority"]["f2_to_f3_allowed"])
        self.assertFalse(plan["decision_authority"]["f3_to_f4_allowed"])

        mutations = {
            "external registration": lambda value: value["registration_claim"].__setitem__(
                "external_registry_present", True
            ),
            "threshold": lambda value: value["decision_authority"].__setitem__(
                "acceptance_thresholds", {"segment_iou_mean": 0.5}
            ),
            "zero fill": lambda value: value["metric_plan"][
                "numeric_convention"
            ].__setitem__("zero_fill_missing_allowed", True),
            "runtime change": lambda value: value["decision_authority"].__setitem__(
                "runtime_feature_change_allowed", True
            ),
            "workbench hash": lambda value: value["study_binding"][
                "workbench_artifact_sha256"
            ].__setitem__("review.html", "A" * 64),
            "blind handoff launcher hash": lambda value: value["study_binding"][
                "blind_handoff_artifact_sha256"
            ].__setitem__("serve_fs09_phase_blind_handoff.py", "A" * 64),
            "mixed handoff exports": lambda value: value["study_binding"][
                "export_provenance_contract"
            ].__setitem__("mixed_handoff_exports_allowed", True),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                changed = copy.deepcopy(plan)
                mutate(changed)
                self.assertTrue(
                    list(
                        Draft202012Validator(
                            schema, format_checker=FormatChecker()
                        ).iter_errors(changed)
                    )
                )
                with self.assertRaises(FS09PhaseTruthError):
                    validate_fs09_phase_analysis_plan(changed)

    def test_browser_review_clips_are_h264_and_map_to_source_windows(self) -> None:
        clip_manifest = json.loads(REVIEW_CLIPS.read_text(encoding="utf-8"))
        validate_fs09_phase_review_clip_manifest(clip_manifest)
        self.assertEqual(2, len(clip_manifest["clips"]))
        for clip in clip_manifest["clips"]:
            self.assertEqual(clip["source_start_ms"], clip["source_time_offset_ms"])
            self.assertEqual(
                clip["source_end_ms"] - clip["source_start_ms"],
                clip["requested_duration_ms"],
            )
            self.assertEqual("h264", clip["probe"]["codec_fourcc"])
            self.assertTrue(all(sample["decoded"] for sample in clip["probe"]["decode_samples"]))
            self.assertEqual(sha256_file(Path(clip["clip"]["path"])), clip["clip"]["sha256"])

    def test_rehashed_review_clip_probe_claim_is_redecoded_and_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            clip_manifest = json.loads(REVIEW_CLIPS.read_text(encoding="utf-8"))
            clip_manifest["clips"][0]["probe"]["fps"] += 1.0
            tampered = root / "tampered-clips.json"
            tampered.write_text(
                json.dumps(clip_manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                FS09PhaseTruthError, "evidence binding|probe does not replay"
            ):
                build_fs09_phase_truth_pack(
                    m74_report_path=M74,
                    review_clip_manifest_path=tampered,
                    analysis_plan_path=ANALYSIS_PLAN,
                    output_dir=root / "pack",
                    asset_directory=ASSETS,
                )
            self.assertFalse((root / "pack").exists())

    def test_workbench_and_local_media_are_plan_bound_not_manifest_self_declared(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pack = _build(root)
            copied_pack = root / "copied-pack"
            shutil.copytree(pack, copied_pack)
            annotation_paths, adjudication_path = _export_files(root, copied_pack)
            with self.assertRaisesRegex(FS09PhaseTruthError, "verified artifact root"):
                ingest_fs09_phase_truth_exports(
                    pack_dir=copied_pack,
                    annotation_exports=annotation_paths,
                    adjudication_export=adjudication_path,
                    output_dir=root / "must-not-exist-stale-path",
                )
            self.assertFalse((root / "must-not-exist-stale-path").exists())

        for artifact_name, expected_error in (
            ("fs09-phase-truth-workbench.js", "analysis plan evidence binding"),
            ("media/task-001-browser.mp4", "plan-bound media"),
        ):
            with self.subTest(
                artifact=artifact_name
            ), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                pack = _build(root)
                artifact_path = pack / artifact_name
                artifact_path.write_bytes(artifact_path.read_bytes() + b"tamper")
                manifest_path = pack / "manifest.json"
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifest["artifacts"][artifact_name]["sha256"] = sha256_file(
                    artifact_path
                )
                _write_json(manifest_path, manifest)
                annotation_paths, adjudication_path = _export_files(root, pack)
                with self.assertRaisesRegex(FS09PhaseTruthError, expected_error):
                    ingest_fs09_phase_truth_exports(
                        pack_dir=pack,
                        annotation_exports=annotation_paths,
                        adjudication_export=adjudication_path,
                        output_dir=root / "must-not-exist-rehash",
                    )
                self.assertFalse((root / "must-not-exist-rehash").exists())

    def test_intake_source_manifest_alias_cannot_change_artifact_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pack = _build(root)
            annotation_paths, adjudication_path = _export_files(root, pack)
            session = root / "session"
            ingest_fs09_phase_truth_exports(
                pack_dir=pack,
                annotation_exports=annotation_paths,
                adjudication_export=adjudication_path,
                output_dir=session,
            )

            alias = root / "manifest-alias"
            alias.mkdir()
            shutil.copy2(pack / "manifest.json", alias / "manifest.json")
            intake_path = session / "intake-manifest.json"
            intake = json.loads(intake_path.read_text(encoding="utf-8"))
            intake["source_pack"]["manifest"]["path"] = str(
                (alias / "manifest.json").resolve()
            )
            _write_json(intake_path, intake)

            with self.assertRaisesRegex(
                FS09PhaseTruthError, "verified artifact root"
            ):
                validate_fs09_phase_truth_intake_manifest(session, intake)
            with self.assertRaisesRegex(
                FS09PhaseTruthError, "verified artifact root"
            ):
                evaluate_fs09_phase_truth(session)

    def test_evaluation_rechecks_sources_after_feature_computation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pack = _build(root)
            annotation_paths, adjudication_path = _export_files(root, pack)
            session = root / "session"
            ingest_fs09_phase_truth_exports(
                pack_dir=pack,
                annotation_exports=annotation_paths,
                adjudication_export=adjudication_path,
                output_dir=session,
            )
            candidates_path = session / "sealed-event-candidates.jsonl"
            mutated = False

            def mutate_after_verification(*args, **kwargs):
                nonlocal mutated
                if not mutated:
                    candidates_path.write_bytes(candidates_path.read_bytes() + b" \n")
                    mutated = True
                return _real_compute_event_features(*args, **kwargs)

            with patch(
                "rallymate_evaluation.fs09_phase_truth.compute_event_features",
                side_effect=mutate_after_verification,
            ), self.assertRaisesRegex(
                FS09PhaseTruthError,
                "evidence binding|lineage mismatch|changed during evaluation",
            ):
                evaluate_fs09_phase_truth(session)

    def test_evaluation_clears_feature_cache_when_feature_computation_raises(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pack = _build(root)
            annotation_paths, adjudication_path = _export_files(root, pack)
            session = root / "session"
            ingest_fs09_phase_truth_exports(
                pack_dir=pack,
                annotation_exports=annotation_paths,
                adjudication_export=adjudication_path,
                output_dir=session,
            )
            cleared_sequences = []

            def compute_then_fail(*args, **kwargs):
                _real_compute_event_features(*args, **kwargs)
                raise RuntimeError("synthetic feature failure")

            def record_and_clear(sequence):
                cleared_sequences.append(sequence)
                _real_clear_feature_cache(sequence)

            with patch(
                "rallymate_evaluation.fs09_phase_truth.compute_event_features",
                side_effect=compute_then_fail,
            ), patch(
                "rallymate_evaluation.fs09_phase_truth.clear_feature_cache",
                side_effect=record_and_clear,
            ), self.assertRaisesRegex(RuntimeError, "synthetic feature failure"):
                evaluate_fs09_phase_truth(session)

            self.assertEqual(1, len(cleared_sequences))

    def test_real_pack_is_blind_empty_and_source_replayable(self) -> None:
        manifest = json.loads((REAL_PACK / "manifest.json").read_text(encoding="utf-8"))
        validation = json.loads((REAL_PACK / "compiled" / "validation-report.json").read_text(encoding="utf-8"))
        report = json.loads(REAL_REPORT.read_text(encoding="utf-8"))
        html = (REAL_PACK / "review.html").read_text(encoding="utf-8")
        bootstrap = json.loads(html.split('<script id="fs09-phase-truth-bootstrap" type="application/json">', 1)[1].split("</script>", 1)[0])

        validate_fs09_phase_truth_manifest(manifest)
        validate_fs09_phase_truth_report_sources(report)
        for schema_name, artifact in (
            ("fs09-phase-truth-pack.schema.json", manifest),
            ("fs09-phase-truth-validation.schema.json", validation),
            ("fs09-phase-truth-evaluation.schema.json", report),
        ):
            schema = json.loads(
                (ROOT / "contracts" / schema_name).read_text(encoding="utf-8")
            )
            Draft202012Validator.check_schema(schema)
            self.assertEqual(
                [],
                list(
                    Draft202012Validator(
                        schema, format_checker=FormatChecker()
                    ).iter_errors(artifact)
                ),
            )
            malformed_time = copy.deepcopy(artifact)
            malformed_time["generated_at"] = "2026-99-99T99:99:99Z"
            self.assertTrue(
                list(
                    Draft202012Validator(
                        schema, format_checker=FormatChecker()
                    ).iter_errors(malformed_time)
                )
            )
        for schema_name, artifact in (
            ("fs09-phase-truth-pack.schema.json", manifest),
            ("fs09-phase-truth-evaluation.schema.json", report),
        ):
            probe_tamper = copy.deepcopy(artifact)
            probe_tamper["sources"]["per_video"][0]["review_clips"][0]["probe"] = {}
            schema = json.loads(
                (ROOT / "contracts" / schema_name).read_text(encoding="utf-8")
            )
            self.assertTrue(
                list(
                    Draft202012Validator(
                        schema, format_checker=FormatChecker()
                    ).iter_errors(probe_tamper)
                )
            )
        self.assertEqual(2, len(bootstrap["tasks"]))
        self.assertEqual(set(task["task_id"] for task in bootstrap["tasks"]), set(bootstrap["review_clips"]))
        self.assertTrue(
            all(value.startswith("media/") for value in bootstrap["review_clips"].values())
        )
        self.assertEqual(
            {task["task_id"]: 24.0 for task in bootstrap["tasks"]},
            bootstrap["review_clip_fps"],
        )
        self.assertEqual(
            sha256_file(REAL_PACK / "fs09-phase-truth-workbench.js"),
            bootstrap["workbench_asset_sha256"]["js"],
        )
        self.assertIn(
            f"fs09-phase-truth-workbench.js?v={bootstrap['workbench_asset_sha256']['js']}",
            html,
        )
        self.assertTrue(all(task["source_time_offset_ms"] == task["review_start_ms"] for task in bootstrap["tasks"]))
        self.assertNotIn("video", bootstrap)
        self.assertNotIn("fs09-072", html)
        self.assertNotIn("fs09-114", html)
        self.assertNotIn("332958", html)
        self.assertNotIn("451583", html)
        self.assertNotIn("sealed-event-candidates.jsonl", html)
        self.assertTrue((REAL_PACK / "OPERATOR_README.md").is_file())
        self.assertTrue((REAL_PACK / "media" / "task-001-browser.mp4").is_file())
        self.assertTrue((REAL_PACK / "media" / "task-002-browser.mp4").is_file())
        self.assertEqual("annotation_required", validation["status"])
        self.assertEqual(0, validation["counts"]["accepted_adjudications"])
        self.assertEqual("annotation_required", report["status"])
        self.assertIsNone(report["event_metrics"])
        self.assertIsNone(report["feature_boundary_conditioning"])
        self.assertTrue(report["acceptance"]["analysis_plan_verified"])
        self.assertFalse(report["acceptance"]["runtime_feature_change_allowed"])
        self.assertEqual(
            ANALYSIS_PLAN.read_bytes(), (REAL_PACK / "analysis-plan.json").read_bytes()
        )
        self.assertEqual(
            sha256_file(ANALYSIS_PLAN), manifest["sources"]["analysis_plan"]["sha256"]
        )
        self.assertEqual(
            sha256_file(REAL_PACK / "analysis-plan.json"),
            report["sources"]["analysis_plan"]["sha256"],
        )

    def test_synthetic_equal_candidate_truth_has_zero_phase_and_boundary_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pack = _build(root)
            annotation_paths, adjudication_path = _export_files(root, pack)
            session = root / "session"
            validation = ingest_fs09_phase_truth_exports(
                pack_dir=pack,
                annotation_exports=annotation_paths,
                adjudication_export=adjudication_path,
                output_dir=session,
            )
            report = evaluate_fs09_phase_truth(session)

            self.assertEqual("ready_for_phase_evaluation", validation["status"])
            self.assertEqual("evaluated_external_acceptance_protocol_required", report["status"])
            self.assertEqual(2, report["counts"]["accepted_manual_events"])
            self.assertEqual(1.0, report["event_metrics"]["segment_iou_mean"])
            self.assertEqual(0.0, report["event_metrics"]["boundary_start_mae_ms"])
            self.assertTrue(all(metric["mae"] == 0.0 for metric in report["phase_metrics"]["by_phase"].values()))
            self.assertEqual(12, len(report["feature_boundary_conditioning"]["details"]))
            self.assertEqual(0, report["feature_boundary_conditioning"]["candidate_target_feature_valid_count"])
            self.assertEqual(0, report["feature_boundary_conditioning"]["manual_boundary_conditioned_target_feature_valid_count"])
            self.assertEqual(0, report["feature_boundary_conditioning"]["by_feature"]["hip_deceleration_to_double_support_proxy_ms"]["count"])
            self.assertFalse(report["safety"]["production_enabled"])

            validate_fs09_phase_truth_report_sources(report)

            evaluation_schema = json.loads(
                (
                    ROOT
                    / "contracts"
                    / "fs09-phase-truth-evaluation.schema.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(
                [],
                list(
                    Draft202012Validator(
                        evaluation_schema, format_checker=FormatChecker()
                    ).iter_errors(report)
                ),
            )
            backdated_report = copy.deepcopy(report)
            backdated_report["generated_at"] = "2020-01-01T00:00:00Z"
            with self.assertRaisesRegex(FS09PhaseTruthError, "predates"):
                validate_fs09_phase_truth_report_sources(backdated_report)

    def test_technical_handoff_cannot_enter_production_intake(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pack = _build(root)
            annotations, adjudication = _export_files(root, pack)
            output = root / "forbidden-technical-session"
            staging = output.with_name(f".{output.name}.building")
            self._test_authorization_patch.stop()
            try:
                with self.assertRaisesRegex(
                    FS09PhaseTruthError,
                    "technical-only.*external protocol receipt",
                ):
                    ingest_fs09_phase_truth_exports(
                        pack_dir=pack,
                        annotation_exports=annotations,
                        adjudication_export=adjudication,
                        output_dir=output,
                    )
            finally:
                self._test_authorization_patch.start()
            self.assertFalse(output.exists())
            self.assertFalse(staging.exists())

    def test_test_only_session_is_rejected_after_authorization_patch_is_removed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pack = _build(root)
            annotations, adjudication = _export_files(root, pack)
            session = root / "test-only-session"
            ingest_fs09_phase_truth_exports(
                pack_dir=pack,
                annotation_exports=annotations,
                adjudication_export=adjudication,
                output_dir=session,
            )
            intake = json.loads(
                (session / "intake-manifest.json").read_text(encoding="utf-8")
            )
            self._test_authorization_patch.stop()
            try:
                with self.assertRaisesRegex(
                    FS09PhaseTruthError, "not externally protocol-authorized"
                ):
                    validate_fs09_phase_truth_intake_manifest(session, intake)
                with self.assertRaisesRegex(
                    FS09PhaseTruthError, "not externally protocol-authorized"
                ):
                    evaluate_fs09_phase_truth(session)
            finally:
                self._test_authorization_patch.start()

    def test_export_intake_creates_new_traceable_session_without_editing_blank_pack(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pack = _build(root)
            annotation_paths, adjudication_path = _export_files(root, pack)
            blank_annotations = (pack / "annotations.csv").read_bytes()
            blank_adjudications = (pack / "adjudications.csv").read_bytes()
            session = root / "session"

            validation = ingest_fs09_phase_truth_exports(
                pack_dir=pack,
                annotation_exports=annotation_paths,
                adjudication_export=adjudication_path,
                output_dir=session,
            )

            self.assertEqual("ready_for_phase_evaluation", validation["status"])
            self.assertEqual(4, validation["counts"]["raw_annotations"])
            self.assertEqual(2, validation["counts"]["accepted_adjudications"])
            self.assertEqual(blank_annotations, (pack / "annotations.csv").read_bytes())
            self.assertEqual(blank_adjudications, (pack / "adjudications.csv").read_bytes())
            intake = json.loads((session / "intake-manifest.json").read_text(encoding="utf-8"))
            validate_fs09_phase_truth_intake_manifest(session, intake)
            source_handoff = pack.parent / "handoff"
            session_handoff = session / "blind-handoff"
            self.assertEqual(
                (source_handoff / "handoff-manifest.json").read_bytes(),
                (session_handoff / "handoff-manifest.json").read_bytes(),
            )
            self.assertEqual(
                intake["blind_handoff"]["bundle_id"],
                json.loads(
                    (source_handoff / "handoff-manifest.json").read_text(
                        encoding="utf-8"
                    )
                )["bundle_id"],
            )
            self.assertEqual(
                sorted(
                    path.relative_to(source_handoff).as_posix()
                    for path in source_handoff.rglob("*")
                    if path.is_file()
                ),
                sorted(
                    path.relative_to(session_handoff).as_posix()
                    for path in session_handoff.rglob("*")
                    if path.is_file()
                ),
            )
            for source_path in source_handoff.rglob("*"):
                if source_path.is_file():
                    relative = source_path.relative_to(source_handoff)
                    self.assertEqual(
                        source_path.read_bytes(),
                        (session_handoff / relative).read_bytes(),
                    )
            intake_schema = json.loads(
                (ROOT / "contracts" / "fs09-phase-truth-intake.schema.json").read_text(
                    encoding="utf-8"
                )
            )
            Draft202012Validator.check_schema(intake_schema)
            self.assertTrue(
                list(
                    Draft202012Validator(
                        intake_schema, format_checker=FormatChecker()
                    ).iter_errors(intake)
                ),
                "test-only in-memory authorization must never create a production-valid intake contract",
            )
            self.assertEqual(
                annotation_paths[0].read_bytes(),
                (session / "raw-exports" / "annotation-export-001.csv").read_bytes(),
            )
            report = evaluate_fs09_phase_truth(session)
            self.assertEqual(
                "evaluated_external_acceptance_protocol_required", report["status"]
            )
            self.assertFalse(report["acceptance"]["f2_to_f3_allowed"])

    def test_export_intake_rejects_handoff_plan_or_timestamp_drift_before_output(self) -> None:
        scenarios = (
            (
                "handoff bundle",
                "handoff_bundle_id",
                lambda _pack: "m88-fs09-blind-" + "A" * 64,
                "handoff bundle identity mismatch",
            ),
            (
                "analysis plan",
                "analysis_plan_sha256",
                lambda _pack: "A" * 64,
                "analysis-plan identity mismatch",
            ),
            (
                "decision export chronology",
                "exported_at",
                lambda pack: json.loads(
                    (pack.parent / "handoff" / "handoff-manifest.json").read_text(
                        encoding="utf-8"
                    )
                )["generated_at"],
                "decision/export chronology",
            ),
            (
                "decision timestamp surrounding whitespace",
                "annotated_at",
                lambda _pack: " 2026-08-30T01:50:00Z",
                "canonical RFC3339 UTC",
            ),
            (
                "export timestamp UTC offset spelling",
                "exported_at",
                lambda _pack: "2026-08-30T01:50:00+00:00",
                "canonical RFC3339 UTC",
            ),
            (
                "decision timestamp basic ISO spelling",
                "annotated_at",
                lambda _pack: "20260830T015000Z",
                "canonical RFC3339 UTC",
            ),
            (
                "decision timestamp week-date spelling",
                "annotated_at",
                lambda _pack: "2026-W35-7T01:50:00Z",
                "canonical RFC3339 UTC",
            ),
            (
                "export timestamp comma fraction",
                "exported_at",
                lambda _pack: "2026-08-30T01:50:00,123Z",
                "canonical RFC3339 UTC",
            ),
        )
        for label, field, replacement, error in scenarios:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                pack = _build(root)
                annotation_paths, adjudication_path = _export_files(root, pack)
                rows = _read_csv(annotation_paths[0])
                rows[0][field] = replacement(pack)
                _write_csv(annotation_paths[0], ANNOTATION_FIELDS, rows)
                output = root / "must-not-exist"

                with self.assertRaisesRegex(FS09PhaseTruthError, error):
                    ingest_fs09_phase_truth_exports(
                        pack_dir=pack,
                        annotation_exports=annotation_paths,
                        adjudication_export=adjudication_path,
                        output_dir=output,
                    )

                self.assertFalse(output.exists())
                self.assertFalse((root / ".must-not-exist.building").exists())

    def test_cross_artifact_chronology_is_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pack = _build(root)
            annotation_paths, adjudication_path = _export_files(root, pack)
            session = root / "session"
            ingest_fs09_phase_truth_exports(
                pack_dir=pack,
                annotation_exports=annotation_paths,
                adjudication_export=adjudication_path,
                output_dir=session,
            )
            manifest = json.loads(
                (session / "manifest.json").read_text(encoding="utf-8")
            )
            intake_path = session / "intake-manifest.json"
            validation_path = session / "compiled" / "validation-report.json"
            intake_raw = intake_path.read_bytes()
            validation_raw = validation_path.read_bytes()

            intake = json.loads(intake_raw.decode("utf-8"))
            intake["generated_at"] = manifest["generated_at"]
            _write_json(intake_path, intake)
            validation = json.loads(validation_raw.decode("utf-8"))
            validation["sources"]["intake_manifest"]["sha256"] = sha256_file(
                intake_path
            )
            _write_json(validation_path, validation)
            with self.assertRaisesRegex(
                FS09PhaseTruthError, "not after intake|chronology"
            ):
                evaluate_fs09_phase_truth(session)

            intake_path.write_bytes(intake_raw)
            validation = json.loads(validation_raw.decode("utf-8"))
            validation["generated_at"] = manifest["generated_at"]
            _write_json(validation_path, validation)
            with self.assertRaisesRegex(FS09PhaseTruthError, "validation predates"):
                evaluate_fs09_phase_truth(session)

            validation_path.write_bytes(validation_raw)
            report = evaluate_fs09_phase_truth(session)
            validate_fs09_phase_truth_report_sources(report)

    def test_export_intake_rejects_identity_or_source_mismatch_before_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pack = _build(root)
            annotation_paths, adjudication_path = _export_files(root, pack)
            with annotation_paths[1].open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            for row in rows:
                row["annotator_id"] = "synthetic-a"
                row["annotation_revision_sha256"] = _annotation_revision_sha256(row)
            _write_csv(annotation_paths[1], ANNOTATION_FIELDS, rows)
            output = root / "same-identity-session"
            with self.assertRaisesRegex(FS09PhaseTruthError, "independent annotators"):
                ingest_fs09_phase_truth_exports(
                    pack_dir=pack,
                    annotation_exports=annotation_paths,
                    adjudication_export=adjudication_path,
                    output_dir=output,
                )
            self.assertFalse(output.exists())

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pack = _build(root)
            annotation_paths, adjudication_path = _export_files(root, pack)
            with adjudication_path.open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            rows[0]["source_annotation_ids"] = "invented-a;invented-b"
            _write_csv(adjudication_path, ADJUDICATION_FIELDS, rows)
            output = root / "bad-source-session"
            with self.assertRaisesRegex(
                FS09PhaseTruthError, "exact two task annotation revisions"
            ):
                ingest_fs09_phase_truth_exports(
                    pack_dir=pack,
                    annotation_exports=annotation_paths,
                    adjudication_export=adjudication_path,
                    output_dir=output,
                )
            self.assertFalse(output.exists())

    def test_export_intake_rejects_wrong_header_before_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pack = _build(root)
            annotation_paths, adjudication_path = _export_files(root, pack)
            raw = annotation_paths[0].read_text(encoding="utf-8-sig")
            annotation_paths[0].write_text(
                raw.replace("annotation_id", "wrong_id", 1), encoding="utf-8"
            )
            output = root / "bad-header-session"
            with self.assertRaisesRegex(FS09PhaseTruthError, "headers must exactly match"):
                ingest_fs09_phase_truth_exports(
                    pack_dir=pack,
                    annotation_exports=annotation_paths,
                    adjudication_export=adjudication_path,
                    output_dir=output,
                )
            self.assertFalse(output.exists())

    def test_export_intake_raw_tamper_breaks_evaluation_lineage(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pack = _build(root)
            annotation_paths, adjudication_path = _export_files(root, pack)
            session = root / "session"
            ingest_fs09_phase_truth_exports(
                pack_dir=pack,
                annotation_exports=annotation_paths,
                adjudication_export=adjudication_path,
                output_dir=session,
            )
            raw = session / "raw-exports" / "annotation-export-001.csv"
            raw.write_bytes(raw.read_bytes() + b"\n")
            with self.assertRaisesRegex(FS09PhaseTruthError, "raw export SHA mismatch"):
                evaluate_fs09_phase_truth(session)

    def test_export_intake_rejects_session_output_inside_source_pack_without_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pack = _build(root)
            annotation_paths, adjudication_path = _export_files(root, pack)
            before = _tree_snapshot(pack)
            output = pack / "nested-session"
            staging = pack / ".nested-session.building"

            with self.assertRaisesRegex(FS09PhaseTruthError, "overlaps source pack"):
                ingest_fs09_phase_truth_exports(
                    pack_dir=pack,
                    annotation_exports=annotation_paths,
                    adjudication_export=adjudication_path,
                    output_dir=output,
                )

            self.assertFalse(output.exists())
            self.assertFalse(staging.exists())
            self.assertEqual(before, _tree_snapshot(pack))

    def test_direct_nonempty_pack_cannot_become_ready_or_evaluate(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            pack = _build(Path(temp))
            _complete(pack)

            validation = compile_fs09_phase_truth_pack(pack)

            self.assertEqual("invalid_annotations", validation["status"])
            self.assertTrue(
                any("requires atomic export intake" in error for error in validation["errors"])
            )
            self.assertFalse(validation["readiness"]["full_task_coverage"])
            self.assertFalse(validation["safety"]["ground_truth_complete"])
            with self.assertRaisesRegex(FS09PhaseTruthError, "compiled truth contains errors"):
                evaluate_fs09_phase_truth(pack)

    def test_raw_export_semantic_replacement_with_updated_lineage_sha_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pack = _build(root)
            annotation_paths, adjudication_path = _export_files(root, pack)
            session = root / "session"
            ingest_fs09_phase_truth_exports(
                pack_dir=pack,
                annotation_exports=annotation_paths,
                adjudication_export=adjudication_path,
                output_dir=session,
            )

            raw_export = session / "raw-exports" / "annotation-export-001.csv"
            raw_rows = _read_csv(raw_export)
            old_revision = raw_rows[0]["annotation_revision_sha256"]
            raw_rows[0]["notes"] = "semantic replacement after intake"
            raw_rows[0]["annotation_revision_sha256"] = _annotation_revision_sha256(
                raw_rows[0]
            )
            new_revision = raw_rows[0]["annotation_revision_sha256"]
            _write_csv(raw_export, ANNOTATION_FIELDS, raw_rows)

            raw_adjudication = session / "raw-exports" / "adjudication-export-001.csv"
            adjudication_rows = _read_csv(raw_adjudication)
            for row in adjudication_rows:
                revisions = row["source_annotation_revision_sha256s"].split(";")
                row["source_annotation_revision_sha256s"] = ";".join(
                    new_revision if value == old_revision else value
                    for value in revisions
                )
            _write_csv(raw_adjudication, ADJUDICATION_FIELDS, adjudication_rows)

            intake_path = session / "intake-manifest.json"
            intake = json.loads(intake_path.read_text(encoding="utf-8"))
            intake["exports"]["annotations"][0]["sha256"] = sha256_file(raw_export)
            intake["exports"]["adjudication"]["sha256"] = sha256_file(
                raw_adjudication
            )
            _write_json(intake_path, intake)

            validation_path = session / "compiled" / "validation-report.json"
            validation = json.loads(validation_path.read_text(encoding="utf-8"))
            validation["sources"]["intake_manifest"]["sha256"] = sha256_file(
                intake_path
            )
            _write_json(validation_path, validation)

            with self.assertRaisesRegex(FS09PhaseTruthError, "merged annotations mismatch"):
                evaluate_fs09_phase_truth(session)

    def test_manual_event_tamper_with_updated_artifact_sha_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pack = _build(root)
            annotation_paths, adjudication_path = _export_files(root, pack)
            session = root / "session"
            ingest_fs09_phase_truth_exports(
                pack_dir=pack,
                annotation_exports=annotation_paths,
                adjudication_export=adjudication_path,
                output_dir=session,
            )

            manual_path = session / "compiled" / "manual-fs09-events.jsonl"
            manual_rows = _jsonl(manual_path)
            manual_rows[0]["start_ms"] += 1
            _write_jsonl(manual_path, manual_rows)

            validation_path = session / "compiled" / "validation-report.json"
            validation = json.loads(validation_path.read_text(encoding="utf-8"))
            validation["artifacts"]["manual_events"]["sha256"] = sha256_file(
                manual_path
            )
            _write_json(validation_path, validation)

            with self.assertRaisesRegex(FS09PhaseTruthError, "manual-event lineage mismatch"):
                evaluate_fs09_phase_truth(session)

    def test_manual_event_replay_rejects_type_loose_duplicate_and_noncanonical_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pack = _build(root)
            annotation_paths, adjudication_path = _export_files(root, pack)
            session = root / "session"
            ingest_fs09_phase_truth_exports(
                pack_dir=pack,
                annotation_exports=annotation_paths,
                adjudication_export=adjudication_path,
                output_dir=session,
            )

            manual_path = session / "compiled" / "manual-fs09-events.jsonl"
            validation_path = session / "compiled" / "validation-report.json"
            original_manual = manual_path.read_bytes()
            original_validation = validation_path.read_bytes()

            def type_loose_jsonl() -> bytes:
                rows = _jsonl(manual_path)
                rows[0]["event_present"] = 1
                rows[0]["start_ms"] = float(rows[0]["start_ms"])
                return b"".join(
                    (
                        json.dumps(
                            row,
                            ensure_ascii=False,
                            separators=(",", ":"),
                            allow_nan=False,
                        )
                        + "\n"
                    ).encode("utf-8")
                    for row in rows
                )

            cases = {
                "bool-and-float-substitution": type_loose_jsonl,
                "duplicate-key": lambda: original_manual.replace(
                    b'{"schema_version"',
                    b'{"event_present":false,"schema_version"',
                    1,
                ),
                "noncanonical-whitespace": lambda: original_manual.replace(
                    b"{", b"{ ", 1
                ),
            }
            for name, mutate in cases.items():
                with self.subTest(name=name):
                    manual_path.write_bytes(original_manual)
                    validation_path.write_bytes(original_validation)
                    manual_path.write_bytes(mutate())
                    validation = json.loads(
                        validation_path.read_text(encoding="utf-8")
                    )
                    validation["artifacts"]["manual_events"]["sha256"] = (
                        sha256_file(manual_path)
                    )
                    _write_json(validation_path, validation)

                    with self.assertRaisesRegex(
                        FS09PhaseTruthError, "manual-event lineage mismatch"
                    ):
                        evaluate_fs09_phase_truth(session)

    def test_validation_replay_rejects_bool_count_and_lowercase_sha(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            pack = _build(Path(temp))
            validation_path = pack / "compiled" / "validation-report.json"
            original = validation_path.read_bytes()

            cases = {
                "bool-count": (
                    lambda value: value["counts"].__setitem__(
                        "raw_annotations", False
                    ),
                    "does not replay source CSVs",
                ),
                "lowercase-sha": (
                    lambda value: value["sources"]["manifest"].__setitem__(
                        "sha256", value["sources"]["manifest"]["sha256"].lower()
                    ),
                    "uppercase SHA-256",
                ),
            }
            for name, (mutate, error) in cases.items():
                with self.subTest(name=name):
                    validation_path.write_bytes(original)
                    validation = json.loads(
                        validation_path.read_text(encoding="utf-8")
                    )
                    mutate(validation)
                    _write_json(validation_path, validation)

                    with self.assertRaisesRegex(FS09PhaseTruthError, error):
                        evaluate_fs09_phase_truth(pack)

    def test_manifest_runtime_rejects_unknown_and_missing_schema_fields(self) -> None:
        manifest = json.loads(
            (REAL_PACK / "manifest.json").read_text(encoding="utf-8")
        )
        cases = {
            "unknown": lambda value: value.__setitem__("unexpected", True),
            "missing-workbench": lambda value: value.pop("workbench"),
        }
        for name, mutate in cases.items():
            with self.subTest(name=name):
                changed = copy.deepcopy(manifest)
                mutate(changed)
                with self.assertRaisesRegex(
                    FS09PhaseTruthError, "truth pack fields drifted"
                ):
                    validate_fs09_phase_truth_manifest(changed)

    def test_committed_intake_recompile_is_rejected_without_byte_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pack = _build(root)
            annotation_paths, adjudication_path = _export_files(root, pack)
            session = root / "session"
            ingest_fs09_phase_truth_exports(
                pack_dir=pack,
                annotation_exports=annotation_paths,
                adjudication_export=adjudication_path,
                output_dir=session,
            )
            before = _tree_snapshot(session)

            with self.assertRaisesRegex(
                FS09PhaseTruthError, "intake sessions are immutable"
            ):
                compile_fs09_phase_truth_pack(session)

            self.assertEqual(before, _tree_snapshot(session))

            (session / "compiled" / "validation-report.json").unlink()
            missing_validation = _tree_snapshot(session)
            with self.assertRaisesRegex(
                FS09PhaseTruthError, "intake sessions are immutable"
            ):
                compile_fs09_phase_truth_pack(session)
            self.assertEqual(missing_validation, _tree_snapshot(session))

    def test_intake_manifest_rejects_unknown_fields_counts_and_identity_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pack = _build(root)
            annotation_paths, adjudication_path = _export_files(root, pack)
            session = root / "session"
            ingest_fs09_phase_truth_exports(
                pack_dir=pack,
                annotation_exports=annotation_paths,
                adjudication_export=adjudication_path,
                output_dir=session,
            )
            valid = json.loads(
                (session / "intake-manifest.json").read_text(encoding="utf-8")
            )

            unknown = copy.deepcopy(valid)
            unknown["unexpected"] = True
            with self.assertRaisesRegex(FS09PhaseTruthError, "intake fields drifted"):
                validate_fs09_phase_truth_intake_manifest(session, unknown)

            wrong_count = copy.deepcopy(valid)
            wrong_count["counts"]["raw_annotations"] += 1
            with self.assertRaisesRegex(FS09PhaseTruthError, "counts drifted"):
                validate_fs09_phase_truth_intake_manifest(session, wrong_count)

            wrong_annotator = copy.deepcopy(valid)
            wrong_annotator["exports"]["annotations"][0]["annotator_id"] = "forged"
            with self.assertRaisesRegex(FS09PhaseTruthError, "raw export SHA mismatch"):
                validate_fs09_phase_truth_intake_manifest(session, wrong_annotator)

            wrong_reviewer = copy.deepcopy(valid)
            wrong_reviewer["exports"]["adjudication"]["reviewer_id"] = "forged"
            with self.assertRaisesRegex(FS09PhaseTruthError, "raw export SHA mismatch"):
                validate_fs09_phase_truth_intake_manifest(session, wrong_reviewer)

    def test_semicolon_and_casefold_equivalent_identities_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pack = _build(root)
            annotation_paths, adjudication_path = _export_files(root, pack)
            annotation_originals = [path.read_bytes() for path in annotation_paths]
            adjudication_original = adjudication_path.read_bytes()

            cases = (
                ("semicolon", annotation_paths[0], "annotator_id", "synthetic;a", "reserved delimiter"),
                ("casefold-annotators", annotation_paths[1], "annotator_id", "SYNTHETIC-A", "independent annotators"),
                ("casefold-reviewer", adjudication_path, "reviewer_id", "SYNTHETIC-A", "reviewer must be independent"),
            )
            for name, path, field, value, error in cases:
                with self.subTest(name=name):
                    annotation_paths[0].write_bytes(annotation_originals[0])
                    annotation_paths[1].write_bytes(annotation_originals[1])
                    adjudication_path.write_bytes(adjudication_original)
                    rows = _read_csv(path)
                    for row in rows:
                        row[field] = value
                        if path != adjudication_path:
                            row["annotation_revision_sha256"] = (
                                _annotation_revision_sha256(row)
                            )
                    fields = (
                        ADJUDICATION_FIELDS
                        if path == adjudication_path
                        else ANNOTATION_FIELDS
                    )
                    _write_csv(path, fields, rows)
                    output = root / f"{name}-session"
                    with self.assertRaisesRegex(FS09PhaseTruthError, error):
                        ingest_fs09_phase_truth_exports(
                            pack_dir=pack,
                            annotation_exports=annotation_paths,
                            adjudication_export=adjudication_path,
                            output_dir=output,
                        )
                    self.assertFalse(output.exists())

    def test_evaluator_cli_reports_domain_errors_without_traceback_or_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pack = _build(root)
            annotation_paths, adjudication_path = _export_files(root, pack)
            session = root / "session"
            ingest_fs09_phase_truth_exports(
                pack_dir=pack,
                annotation_exports=annotation_paths,
                adjudication_export=adjudication_path,
                output_dir=session,
            )
            raw_export = session / "raw-exports" / "annotation-export-001.csv"
            raw_export.write_bytes(raw_export.read_bytes() + b"\n")
            output = root / "reports" / "must-not-exist.json"

            result = _run_evaluator_cli(session, output)

            self.assertEqual(2, result.returncode)
            self.assertIn(
                "error: M77 intake is not externally protocol-authorized",
                result.stderr,
            )
            self.assertNotIn("Traceback", result.stderr)
            self.assertEqual("", result.stdout)
            self.assertFalse(output.exists())
            self.assertFalse(output.parent.exists())

    def test_evaluator_cli_rejects_output_inside_immutable_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pack = _build(root)
            annotation_paths, adjudication_path = _export_files(root, pack)
            session = root / "session"
            ingest_fs09_phase_truth_exports(
                pack_dir=pack,
                annotation_exports=annotation_paths,
                adjudication_export=adjudication_path,
                output_dir=session,
            )
            output = session / "reports" / "must-not-exist.json"

            result = _run_evaluator_cli(session, output)

            self.assertEqual(2, result.returncode)
            self.assertIn(
                "error: output must be outside the immutable M77 intake session",
                result.stderr,
            )
            self.assertNotIn("Traceback", result.stderr)
            self.assertEqual("", result.stdout)
            self.assertFalse(output.exists())
            self.assertFalse(output.parent.exists())

    def test_synthetic_absent_events_are_not_forced_into_feature_replay(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pack = _build(root)
            annotation_paths, adjudication_path = _export_files(
                root,
                pack,
                event_present=False,
            )
            session = root / "session"
            validation = ingest_fs09_phase_truth_exports(
                pack_dir=pack,
                annotation_exports=annotation_paths,
                adjudication_export=adjudication_path,
                output_dir=session,
            )
            report = evaluate_fs09_phase_truth(session)

            self.assertEqual("ready_for_phase_evaluation", validation["status"])
            self.assertEqual(0, report["counts"]["manual_event_present"])
            self.assertEqual(2, report["counts"]["manual_event_absent"])
            self.assertEqual(0, report["counts"]["candidate_feature_records"])
            self.assertEqual(2, report["event_metrics"]["truth_absent_count"])
            self.assertIsNone(report["event_metrics"]["segment_iou_mean"])
            self.assertEqual([], report["feature_boundary_conditioning"]["details"])
            for metric in report["phase_metrics"]["by_phase"].values():
                self.assertEqual(0, metric["count"])
                self.assertIsNone(metric["mae"])
                self.assertIsNone(metric["p95_absolute_error"])
                self.assertIsNone(metric["bias_candidate_minus_truth"])
            self.assertTrue(report["acceptance"]["analysis_plan_verified"])

            contradictory = copy.deepcopy(report)
            phase = contradictory["phase_metrics"]["details"][0]
            phase["truth_status"] = "observed"
            phase["truth_ms"] = phase["candidate_ms"]
            phase["signed_error_candidate_minus_truth_ms"] = 0
            contradictory["phase_metrics"]["by_phase"][phase["phase_key"]] = {
                "count": 1,
                "mae": 0.0,
                "p95_absolute_error": 0.0,
                "bias_candidate_minus_truth": 0.0,
            }
            with self.assertRaisesRegex(FS09PhaseTruthError, "phase missingness"):
                validate_fs09_phase_truth_report(contradictory)

    def test_phase_order_and_reviewer_independence_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pack = _build(root)
            annotation_paths, adjudication_path = _export_files(
                root,
                pack,
                reverse_phase_order=True,
            )
            with self.assertRaisesRegex(FS09PhaseTruthError, "nondecreasing"):
                ingest_fs09_phase_truth_exports(
                    pack_dir=pack,
                    annotation_exports=annotation_paths,
                    adjudication_export=adjudication_path,
                    output_dir=root / "reverse-phase-session",
                )

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pack = _build(root)
            annotation_paths, adjudication_path = _export_files(root, pack)
            rows = _read_csv(adjudication_path)
            for row in rows:
                row["reviewer_id"] = "synthetic-a"
            _write_csv(adjudication_path, ADJUDICATION_FIELDS, rows)
            with self.assertRaisesRegex(FS09PhaseTruthError, "reviewer must be independent"):
                ingest_fs09_phase_truth_exports(
                    pack_dir=pack,
                    annotation_exports=annotation_paths,
                    adjudication_export=adjudication_path,
                    output_dir=root / "same-reviewer-session",
                )

    def test_rehashed_candidate_tamper_still_fails_m74_replay(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            pack = _build(Path(temp))
            candidates = _jsonl(pack / "sealed-event-candidates.jsonl")
            candidates[0]["start_ms"] += 40
            path = pack / "sealed-event-candidates.jsonl"
            _write_jsonl(path, candidates)
            manifest_path = pack / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["candidate_contract_sha256"] = _canonical_sha256(candidates)
            manifest["artifacts"]["sealed-event-candidates.jsonl"]["sha256"] = sha256_file(path)
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")

            validation = compile_fs09_phase_truth_pack(pack)
            self.assertEqual("invalid_annotations", validation["status"])
            self.assertTrue(any("do not replay bound M74" in error for error in validation["errors"]))

    def test_analysis_plan_binding_and_freeze_chronology_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plan = json.loads(ANALYSIS_PLAN.read_text(encoding="utf-8"))
            plan["study_binding"]["task_contract_sha256"] = "A" * 64
            mismatched = root / "mismatched-plan.json"
            _write_json(mismatched, plan)
            with self.assertRaisesRegex(
                FS09PhaseTruthError, "raw SHA|task_contract_sha256|evidence binding"
            ):
                build_fs09_phase_truth_pack(
                    m74_report_path=M74,
                    review_clip_manifest_path=REVIEW_CLIPS,
                    analysis_plan_path=mismatched,
                    output_dir=root / "must-not-exist",
                    asset_directory=ASSETS,
                )
            self.assertFalse((root / "must-not-exist").exists())

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plan = json.loads(ANALYSIS_PLAN.read_text(encoding="utf-8"))
            plan["frozen_at"] = "2100-01-01T00:00:00Z"
            future = root / "future-plan.json"
            _write_json(future, plan)
            with self.assertRaisesRegex(
                FS09PhaseTruthError, "raw SHA|frozen_at|predates the frozen"
            ):
                build_fs09_phase_truth_pack(
                    m74_report_path=M74,
                    review_clip_manifest_path=REVIEW_CLIPS,
                    analysis_plan_path=future,
                    output_dir=root / "must-not-exist",
                    asset_directory=ASSETS,
                )
            self.assertFalse((root / "must-not-exist").exists())

    def test_analysis_plan_tamper_and_pre_freeze_labels_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pack = _build(root)
            plan_path = pack / "analysis-plan.json"
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            plan["interpretation_boundaries"]["supports_accuracy_claim"] = True
            _write_json(plan_path, plan)
            manifest_path = pack / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["artifacts"]["analysis-plan.json"]["sha256"] = sha256_file(
                plan_path
            )
            _write_json(manifest_path, manifest)
            validation = compile_fs09_phase_truth_pack(pack)
            self.assertEqual("invalid_annotations", validation["status"])
            self.assertTrue(
                any("analysis plan" in error.lower() for error in validation["errors"])
            )

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pack = _build(root)
            annotation_paths, adjudication_path = _export_files(root, pack)
            rows = _read_csv(annotation_paths[0])
            for row in rows:
                row["annotated_at"] = "2026-08-29T22:00:00Z"
            _write_csv(annotation_paths[0], ANNOTATION_FIELDS, rows)
            with self.assertRaisesRegex(
                FS09PhaseTruthError,
                "frozen analysis plan|source manifest|decision/export chronology",
            ):
                ingest_fs09_phase_truth_exports(
                    pack_dir=pack,
                    annotation_exports=annotation_paths,
                    adjudication_export=adjudication_path,
                    output_dir=root / "must-not-exist",
                )
            self.assertFalse((root / "must-not-exist").exists())

            annotation_paths, adjudication_path = _export_files(root, pack)
            annotation_rows = _read_csv(annotation_paths[0])
            adjudication_rows = _read_csv(adjudication_path)
            for row in adjudication_rows:
                row["adjudicated_at"] = annotation_rows[0]["annotated_at"]
            _write_csv(adjudication_path, ADJUDICATION_FIELDS, adjudication_rows)
            with self.assertRaisesRegex(
                FS09PhaseTruthError, "later than source annotations"
            ):
                ingest_fs09_phase_truth_exports(
                    pack_dir=pack,
                    annotation_exports=annotation_paths,
                    adjudication_export=adjudication_path,
                    output_dir=root / "must-not-exist-equal-time",
                )
            self.assertFalse((root / "must-not-exist-equal-time").exists())

    def test_output_directory_is_immutable(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pack = _build(root)
            before = (pack / "manifest.json").read_bytes()
            with self.assertRaisesRegex(FS09PhaseTruthError, "already exists"):
                build_fs09_phase_truth_pack(
                    m74_report_path=M74,
                    review_clip_manifest_path=REVIEW_CLIPS,
                    analysis_plan_path=ANALYSIS_PLAN,
                    output_dir=pack,
                    asset_directory=ASSETS,
                )
            self.assertEqual(before, (pack / "manifest.json").read_bytes())


if __name__ == "__main__":
    unittest.main()
