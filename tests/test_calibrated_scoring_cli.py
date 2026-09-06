from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rallymate_scoring.quality_policy import evaluate_indicator_event_quality
from rallymate_scoring.registry_lifecycle import resolve_runtime_feasibility_registry
from rallymate_scoring.run_bundle_binding import (
    build_scoring_run_bundle_entry,
    build_trusted_scoring_run_bundle_ledger,
)
from rallymate_vision.pose.metadata import sha256_file
from tests import test_runtime_profile_binding as runtime_profile_fixtures


def _lifecycle_runtime_fixture() -> dict:
    """Build the existing production fixture with an explicit source registry."""

    original_registry = runtime_profile_fixtures._registry

    def extended_registry(*args, **kwargs):
        registry = original_registry(*args, **kwargs)
        registry["scope"]["extends_registry"] = (
            "minimum-scoring-loop-2026-08-13.1"
        )
        return registry

    with patch.object(runtime_profile_fixtures, "_registry", extended_registry):
        return runtime_profile_fixtures._runtime_fixture()


class CalibratedScoringCliTests(unittest.TestCase):
    def _write_registry_lifecycle(
        self,
        directory: Path,
        *,
        registry_path: Path,
        registry: dict,
    ) -> Path:
        root = Path(__file__).resolve().parents[1]
        historical_path = directory / "historical-feasibility.json"
        historical_path.write_bytes((root / "metric-feasibility.json").read_bytes())
        source_version = registry["scope"]["extends_registry"]
        manifest = {
            "schema_version": "1.0.0",
            "artifact_scope": "registry_lifecycle_authority",
            "authority_version": "test-registry-lifecycle-v1",
            "roles": {
                "runtime_feasibility": {
                    "kind": "metric_feasibility_registry",
                    "lifecycle": "current",
                    "relative_path": registry_path.name,
                    "file_sha256": hashlib.sha256(
                        registry_path.read_bytes()
                    ).hexdigest(),
                    "embedded_version": registry["registry_version"],
                    "source_registry_version": source_version,
                },
                "current_scoring_requirements": {
                    "kind": "indicator_scoring_requirements",
                    "lifecycle": "derived_current",
                    "relative_path": "unused-current-requirements.json",
                    "file_sha256": "1" * 64,
                    "embedded_version": "unused-requirements-v1",
                    "source_registry_version": registry["registry_version"],
                },
            },
            "non_runtime_artifacts": {
                "historical_feasibility": {
                    "kind": "metric_feasibility_registry",
                    "lifecycle": "historical",
                    "relative_path": historical_path.name,
                    "file_sha256": hashlib.sha256(
                        historical_path.read_bytes()
                    ).hexdigest(),
                    "embedded_version": source_version,
                },
                "measurement_plans": {
                    "kind": "metric_measurement_plans",
                    "lifecycle": "planning_only",
                    "relative_path": "unused-measurement-plans.json",
                    "file_sha256": "2" * 64,
                    "embedded_version": "unused-measurement-plans-v1",
                    "source_registry_version": "planning-source-v1",
                },
            },
        }
        manifest_path = directory / "registry-lifecycle.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        return manifest_path

    def _inputs(
        self,
        directory: Path,
        fixture: dict,
        *,
        event_quality_flags: list[str] | None = None,
        feature_quality_flags: list[str] | None = None,
    ) -> tuple[Path, Path]:
        features = directory / "indicator-features.jsonl"
        events = directory / "events.jsonl"
        summary = directory / "scoring-loop-summary.json"
        features.write_text(
            json.dumps(
                {
                    "video_id": fixture["evidence"]["video_id"],
                    "event_id": "synthetic-event",
                    "event_code": "FS01",
                    "person_track_id": 1,
                    "indicator_id": "FS01-M02",
                    "feasibility_level": "F4",
                    "features": [
                        {
                            "feature_name": "feature_a",
                            "feature_version": "feature-a-v1",
                            "value": 20.0,
                            "unit": "body",
                            "confidence": 0.8,
                            "valid": True,
                            "reason": "valid",
                            "source_frames": [10],
                        }
                    ],
                    "feature_status": "measured",
                    "quality_gate": evaluate_indicator_event_quality(
                        "FS01-M02", feature_quality_flags or []
                    ),
                    "provenance": {
                        "video_sha256": fixture["evidence"]["video_sha256"],
                        "frames_sha256": "c" * 64,
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        events.write_text(
            json.dumps(
                {
                    "schema_version": "1.0.0",
                    "video_id": fixture["evidence"]["video_id"],
                    "event_id": "synthetic-event",
                    "event_code": "FS01",
                    "person_track_id": 1,
                    "start_ms": 0,
                    "end_ms": 100,
                    "key_phases_ms": {"preload_ms": 50},
                    "confidence": 0.9,
                    "boundary_uncertainty_ms": 10,
                    "quality_flags": event_quality_flags or [],
                    "provenance": {"detector_version": "synthetic-event-v1"},
                }
            )
            + "\n",
            encoding="utf-8",
        )
        auxiliary_artifacts = {
            "features_jsonl": directory / "features.jsonl",
            "scores_jsonl": directory / "scores.jsonl",
            "event_feature_errors_json": directory / "event-feature-errors.json",
        }
        auxiliary_artifacts["features_jsonl"].write_text("", encoding="utf-8")
        auxiliary_artifacts["scores_jsonl"].write_text("", encoding="utf-8")
        auxiliary_artifacts["event_feature_errors_json"].write_text(
            "{}", encoding="utf-8"
        )
        summary.write_text(
            json.dumps(
                {
                    "schema_version": "1.0.0",
                    "loop_version": "minimum-scoring-loop-v0.6.0",
                    "video_id": fixture["evidence"]["video_id"],
                    "model_versions": fixture["model_versions"],
                    "provenance": {
                        "video_sha256": fixture["evidence"]["video_sha256"]
                    },
                    "artifacts": {
                        "events_jsonl": events.name,
                        "features_jsonl": auxiliary_artifacts[
                            "features_jsonl"
                        ].name,
                        "indicator_features_jsonl": features.name,
                        "scores_jsonl": auxiliary_artifacts["scores_jsonl"].name,
                        "event_feature_errors_json": auxiliary_artifacts[
                            "event_feature_errors_json"
                        ].name,
                    },
                    "artifact_sha256": {
                        "events_jsonl": sha256_file(events),
                        "features_jsonl": sha256_file(
                            auxiliary_artifacts["features_jsonl"]
                        ),
                        "indicator_features_jsonl": sha256_file(features),
                        "scores_jsonl": sha256_file(
                            auxiliary_artifacts["scores_jsonl"]
                        ),
                        "event_feature_errors_json": sha256_file(
                            auxiliary_artifacts["event_feature_errors_json"]
                        ),
                    },
                }
            ),
            encoding="utf-8",
        )
        return features, summary

    def _production_command(
        self,
        directory: Path,
        fixture: dict,
        features: Path,
        summary: Path,
    ) -> tuple[list[str], list[str], Path, Path, Path, Path, Path]:
        root = Path(__file__).resolve().parents[1]
        calibration = directory / "calibration.json"
        ledger_path = directory / "trusted-ledger.json"
        registry_path = directory / "feasibility-registry.json"
        runtime_bindings_path = directory / "trusted-runtime-bindings.json"
        view_evidence_path = directory / "runtime-view-evidence.json"
        run_bundle_ledger_path = directory / "trusted-run-bundles.json"
        output = directory / "rescored-scores.jsonl"
        audit_path = directory / "scores-report.json"
        calibration.write_text(json.dumps(fixture["asset"]), encoding="utf-8")
        ledger_path.write_text(json.dumps(fixture["ledger"]), encoding="utf-8")
        registry_path.write_text(json.dumps(fixture["registry"]), encoding="utf-8")
        lifecycle_path = self._write_registry_lifecycle(
            directory,
            registry_path=registry_path,
            registry=fixture["registry"],
        )
        registry_authority = resolve_runtime_feasibility_registry(lifecycle_path)
        summary_payload = json.loads(summary.read_text(encoding="utf-8"))
        summary_payload["model_versions"]["feasibility_registry"] = (
            registry_authority.embedded_version
        )
        summary_payload["provenance"].update(
            {
                "feasibility_registry_sha256": registry_authority.file_sha256,
                "registry_lifecycle": {
                    "authority_version": registry_authority.authority_version,
                    "role": registry_authority.role,
                    "kind": registry_authority.kind,
                    "lifecycle": registry_authority.lifecycle,
                    "embedded_version": registry_authority.embedded_version,
                    "manifest_path": str(registry_authority.manifest_path),
                    "manifest_sha256": registry_authority.manifest_sha256,
                    "artifact_path": str(registry_authority.path),
                    "artifact_sha256": registry_authority.file_sha256,
                },
            }
        )
        summary.write_text(json.dumps(summary_payload), encoding="utf-8")
        runtime_bindings_path.write_text(
            json.dumps(fixture["binding_registry"]), encoding="utf-8"
        )
        view_evidence_path.write_text(
            json.dumps(fixture["evidence"]), encoding="utf-8"
        )
        run_bundle_entry = build_scoring_run_bundle_entry(
            summary=summary_payload,
            scoring_summary_sha256=sha256_file(summary),
            entry_id="synthetic-video-run-v1",
            review_id="synthetic-run-review-v1",
            reviewer_id="release-reviewer",
            review_source_sha256="9" * 64,
            reviewed_at="2026-08-13T04:35:00Z",
            registered_at="2026-08-13T04:40:00Z",
        )
        run_bundle_ledger = build_trusted_scoring_run_bundle_ledger(
            entries=[run_bundle_entry],
            ledger_id="operator-run-bundles",
            ledger_version="run-bundles-v1",
            authority_id="release-owner",
            registered_at="2026-08-13T04:45:00Z",
        )
        run_bundle_ledger_path.write_text(
            json.dumps(run_bundle_ledger), encoding="utf-8"
        )
        base_command = [
            sys.executable,
            str(root / "scripts" / "score_calibrated_indicators.py"),
            "--indicator-features",
            str(features),
            "--scoring-summary",
            str(summary),
            "--calibration",
            str(calibration),
            "--output",
            str(output),
            "--report",
            str(audit_path),
        ]
        authorized_command = [
            *base_command,
            "--trusted-promotion-ledger",
            str(ledger_path),
            "--registry-lifecycle-manifest",
            str(lifecycle_path),
            "--feasibility-registry",
            str(registry_path),
            "--trusted-runtime-profile-bindings",
            str(runtime_bindings_path),
            "--runtime-view-evidence",
            str(view_evidence_path),
            "--trusted-scoring-run-bundles",
            str(run_bundle_ledger_path),
        ]
        return (
            base_command,
            authorized_command,
            output,
            audit_path,
            ledger_path,
            runtime_bindings_path,
            run_bundle_ledger_path,
        )

    def test_cli_requires_trusted_ledger_for_production_asset(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            fixture = _lifecycle_runtime_fixture()
            features, summary = self._inputs(directory, fixture)
            (
                base_command,
                authorized_command,
                output,
                audit_path,
                ledger_path,
                runtime_bindings_path,
                run_bundle_ledger_path,
            ) = self._production_command(directory, fixture, features, summary)
            blocked = subprocess.run(
                base_command, cwd=root, capture_output=True, text=True, check=False
            )
            self.assertNotEqual(blocked.returncode, 0)
            self.assertIn("trusted promotion ledger", blocked.stderr)

            process = subprocess.run(
                authorized_command,
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(process.returncode, 0, process.stderr)
            result = json.loads(output.read_text(encoding="utf-8").strip())
            audit = json.loads(audit_path.read_text(encoding="utf-8"))
            self.assertEqual(result["status"], "scored")
            self.assertEqual(result["grade"], "C")
            self.assertEqual(
                result["model_versions"]["trusted_promotion_ledger"], "ledger-v1"
            )
            self.assertEqual(
                audit["inputs"]["trusted_promotion_ledger"],
                str(ledger_path.resolve()),
            )
            self.assertEqual(
                audit["inputs"]["trusted_runtime_profile_bindings"],
                str(runtime_bindings_path.resolve()),
            )
            lifecycle_path = directory / "registry-lifecycle.json"
            registry_path = directory / "feasibility-registry.json"
            self.assertEqual(
                audit["inputs"]["registry_lifecycle_manifest"],
                str(lifecycle_path.resolve()),
            )
            self.assertEqual(
                audit["inputs"]["registry_lifecycle_manifest_sha256"],
                hashlib.sha256(lifecycle_path.read_bytes()).hexdigest(),
            )
            self.assertEqual(
                audit["inputs"]["registry_lifecycle_authority_version"],
                "test-registry-lifecycle-v1",
            )
            self.assertEqual(
                audit["inputs"]["registry_lifecycle_authority_slot"],
                "roles.runtime_feasibility",
            )
            self.assertEqual(
                audit["inputs"]["registry_lifecycle_role"],
                "runtime_feasibility",
            )
            self.assertEqual(
                audit["inputs"]["feasibility_registry"],
                str(registry_path.resolve()),
            )
            self.assertEqual(
                audit["inputs"]["feasibility_registry_raw_file_sha256"],
                hashlib.sha256(registry_path.read_bytes()).hexdigest(),
            )
            self.assertEqual(
                audit["inputs"]["trusted_scoring_run_bundles"],
                str(run_bundle_ledger_path.resolve()),
            )
            self.assertEqual(
                audit["inputs"]["trusted_scoring_run_bundle_entry_id"],
                "synthetic-video-run-v1",
            )
            self.assertEqual(
                audit["inputs"]["trusted_scoring_run_bundle_ledger_id"],
                "operator-run-bundles",
            )
            self.assertEqual(
                audit["inputs"]["trusted_scoring_run_bundle_authority_id"],
                "release-owner",
            )
            self.assertEqual(
                audit["inputs"]["trusted_scoring_run_bundle_review_id"],
                "synthetic-run-review-v1",
            )
            self.assertEqual(
                result["model_versions"]["trusted_scoring_run_bundle_ledger"],
                "run-bundles-v1",
            )

    def test_registry_path_pin_rejects_non_runtime_artifacts_without_outputs(
        self,
    ) -> None:
        root = Path(__file__).resolve().parents[1]
        for case in ("historical", "unregistered", "same_byte_copy"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                fixture = _lifecycle_runtime_fixture()
                features, summary = self._inputs(directory, fixture)
                _, command, output, report, _, _, _ = self._production_command(
                    directory, fixture, features, summary
                )
                pin_index = command.index("--feasibility-registry") + 1
                authorized_registry = Path(command[pin_index])
                if case == "historical":
                    rejected_path = directory / "historical-feasibility.json"
                elif case == "unregistered":
                    rejected_path = directory / "unregistered-feasibility.json"
                    rejected_path.write_bytes(
                        authorized_registry.read_bytes() + b"\n"
                    )
                else:
                    rejected_path = directory / "same-byte-copy.json"
                    rejected_path.write_bytes(authorized_registry.read_bytes())
                command[pin_index] = str(rejected_path)

                process = subprocess.run(
                    command,
                    cwd=root,
                    capture_output=True,
                    text=True,
                    check=False,
                )

                self.assertNotEqual(process.returncode, 0)
                self.assertIn("lifecycle-authorized", process.stderr)
                self.assertFalse(output.exists())
                self.assertFalse(report.exists())

    def test_operator_authorized_bundle_requires_current_lifecycle_provenance(
        self,
    ) -> None:
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            fixture = _lifecycle_runtime_fixture()
            features, summary = self._inputs(directory, fixture)
            (
                _,
                authorized_command,
                output,
                report,
                _,
                _,
                run_bundle_ledger_path,
            ) = self._production_command(directory, fixture, features, summary)
            payload = json.loads(summary.read_text(encoding="utf-8"))
            payload["provenance"].pop("registry_lifecycle")
            summary.write_text(json.dumps(payload), encoding="utf-8")
            entry = build_scoring_run_bundle_entry(
                summary=payload,
                scoring_summary_sha256=sha256_file(summary),
                entry_id="unbound-registry-run-v1",
                review_id="unbound-registry-review-v1",
                reviewer_id="release-reviewer",
                review_source_sha256="8" * 64,
                reviewed_at="2026-08-13T04:35:00Z",
                registered_at="2026-08-13T04:40:00Z",
            )
            ledger = build_trusted_scoring_run_bundle_ledger(
                entries=[entry],
                ledger_id="operator-run-bundles",
                ledger_version="run-bundles-v1",
                authority_id="release-owner",
                registered_at="2026-08-13T04:45:00Z",
            )
            run_bundle_ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
            process = subprocess.run(
                authorized_command,
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(process.returncode, 0)
            self.assertIn("registry lifecycle provenance", process.stderr)
            self.assertFalse(output.exists())
            self.assertFalse(report.exists())

    def test_production_rejects_deleted_event_quality_flag_without_outputs(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            fixture = _lifecycle_runtime_fixture()
            features, summary = self._inputs(
                directory,
                fixture,
                event_quality_flags=["source_track_switch_candidates_present"],
                feature_quality_flags=[],
            )
            _, command, output, report, _, _, _ = self._production_command(
                directory, fixture, features, summary
            )

            process = subprocess.run(
                command, cwd=root, capture_output=True, text=True, check=False
            )

            self.assertNotEqual(process.returncode, 0)
            self.assertIn("authoritative event", process.stderr)
            self.assertFalse(output.exists())
            self.assertFalse(report.exists())

    def test_production_rejects_missing_or_mismatched_artifact_binding(self) -> None:
        root = Path(__file__).resolve().parents[1]
        for case in (
            "missing_hash",
            "mismatched_hash",
            "missing_event",
            "unconsumed_artifact_mismatch",
        ):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                fixture = _lifecycle_runtime_fixture()
                features, summary = self._inputs(directory, fixture)
                _, command, output, report, _, _, _ = self._production_command(
                    directory, fixture, features, summary
                )
                summary_payload = json.loads(summary.read_text(encoding="utf-8"))
                events = features.with_name("events.jsonl")
                if case == "missing_hash":
                    summary_payload["artifact_sha256"].pop("events_jsonl")
                elif case == "mismatched_hash":
                    events.write_text(events.read_text(encoding="utf-8") + "\n")
                elif case == "missing_event":
                    events.write_text("", encoding="utf-8")
                else:
                    source_scores = directory / "scores.jsonl"
                    source_scores.write_text(
                        '{"tampered":true}\n', encoding="utf-8"
                    )
                summary.write_text(json.dumps(summary_payload), encoding="utf-8")

                process = subprocess.run(
                    command, cwd=root, capture_output=True, text=True, check=False
                )

                self.assertNotEqual(process.returncode, 0)
                self.assertFalse(output.exists())
                self.assertFalse(report.exists())

    def test_production_requires_operator_run_bundle_ledger(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            fixture = _lifecycle_runtime_fixture()
            features, summary = self._inputs(directory, fixture)
            _, command, output, report, _, _, ledger = self._production_command(
                directory, fixture, features, summary
            )
            ledger_index = command.index("--trusted-scoring-run-bundles")
            command_without_ledger = command[:ledger_index] + command[ledger_index + 2 :]

            process = subprocess.run(
                command_without_ledger,
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertNotEqual(process.returncode, 0)
            self.assertIn("--trusted-scoring-run-bundles", process.stderr)
            self.assertFalse(output.exists())
            self.assertFalse(report.exists())
            self.assertTrue(ledger.exists())

    def test_coordinated_bundle_rewrite_fails_external_trust_root(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            fixture = _lifecycle_runtime_fixture()
            quality_flag = "source_track_switch_candidates_present"
            features, summary = self._inputs(
                directory,
                fixture,
                event_quality_flags=[quality_flag],
                feature_quality_flags=[quality_flag],
            )
            _, command, output, report, _, _, _ = self._production_command(
                directory, fixture, features, summary
            )

            feature_record = json.loads(features.read_text(encoding="utf-8"))
            feature_record["quality_gate"] = evaluate_indicator_event_quality(
                "FS01-M02", []
            )
            feature_record["feature_status"] = "measured"
            features.write_text(
                json.dumps(feature_record) + "\n", encoding="utf-8"
            )
            events = features.with_name("events.jsonl")
            event_record = json.loads(events.read_text(encoding="utf-8"))
            event_record["quality_flags"] = []
            events.write_text(json.dumps(event_record) + "\n", encoding="utf-8")
            summary_payload = json.loads(summary.read_text(encoding="utf-8"))
            summary_payload["artifact_sha256"]["events_jsonl"] = sha256_file(events)
            summary_payload["artifact_sha256"][
                "indicator_features_jsonl"
            ] = sha256_file(features)
            summary.write_text(json.dumps(summary_payload), encoding="utf-8")

            process = subprocess.run(
                command, cwd=root, capture_output=True, text=True, check=False
            )

            self.assertNotEqual(process.returncode, 0)
            self.assertIn("operator-controlled ledger", process.stderr)
            self.assertFalse(output.exists())
            self.assertFalse(report.exists())

    def test_summary_raw_file_bytes_not_only_canonical_json_are_bound(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            fixture = _lifecycle_runtime_fixture()
            features, summary = self._inputs(directory, fixture)
            _, command, output, report, _, _, _ = self._production_command(
                directory, fixture, features, summary
            )
            unchanged_payload = json.loads(summary.read_text(encoding="utf-8"))
            summary.write_text(
                json.dumps(unchanged_payload, indent=2), encoding="utf-8"
            )

            process = subprocess.run(
                command, cwd=root, capture_output=True, text=True, check=False
            )

            self.assertNotEqual(process.returncode, 0)
            self.assertIn("operator-controlled ledger", process.stderr)
            self.assertFalse(output.exists())
            self.assertFalse(report.exists())

    def test_ledger_authorized_summary_video_id_must_match_view(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            fixture = _lifecycle_runtime_fixture()
            features, summary = self._inputs(directory, fixture)
            summary_payload = json.loads(summary.read_text(encoding="utf-8"))
            summary_payload["video_id"] = "different-summary-video"
            summary.write_text(json.dumps(summary_payload), encoding="utf-8")
            _, command, output, report, _, _, _ = self._production_command(
                directory, fixture, features, summary
            )

            process = subprocess.run(
                command, cwd=root, capture_output=True, text=True, check=False
            )

            self.assertNotEqual(process.returncode, 0)
            self.assertIn(
                "run-bundle video_id does not exactly match runtime view evidence",
                process.stderr,
            )
            self.assertFalse(output.exists())
            self.assertFalse(report.exists())

    def test_outputs_cannot_overwrite_bundle_or_trust_inputs(self) -> None:
        root = Path(__file__).resolve().parents[1]
        for case in (
            "output_bundle_artifact",
            "output_hardlink_alias",
            "report_trust_ledger",
            "same_output_report",
        ):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                fixture = _lifecycle_runtime_fixture()
                features, summary = self._inputs(directory, fixture)
                _, command, output, report, _, _, run_ledger = (
                    self._production_command(directory, fixture, features, summary)
                )
                protected = directory / "scores.jsonl"
                protected_before = protected.read_bytes()
                ledger_before = run_ledger.read_bytes()
                output_index = command.index("--output") + 1
                report_index = command.index("--report") + 1
                if case == "output_bundle_artifact":
                    command[output_index] = str(protected)
                elif case == "output_hardlink_alias":
                    hardlink_alias = directory / "scores-hardlink.jsonl"
                    hardlink_alias.hardlink_to(protected)
                    command[output_index] = str(hardlink_alias)
                elif case == "report_trust_ledger":
                    command[report_index] = str(run_ledger)
                else:
                    command[report_index] = command[output_index]

                process = subprocess.run(
                    command, cwd=root, capture_output=True, text=True, check=False
                )

                self.assertNotEqual(process.returncode, 0)
                expected_error = (
                    "paths must be different"
                    if case == "same_output_report"
                    else "must not overwrite"
                )
                self.assertIn(expected_error, process.stderr)
                self.assertEqual(protected.read_bytes(), protected_before)
                self.assertEqual(run_ledger.read_bytes(), ledger_before)
                self.assertFalse(output.exists())
                self.assertFalse(report.exists())


if __name__ == "__main__":
    unittest.main()
