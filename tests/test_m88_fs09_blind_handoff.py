from __future__ import annotations

import copy
import hashlib
import http.client
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from jsonschema import Draft202012Validator, FormatChecker

import rallymate_evaluation.fs09_phase_handoff as handoff_module
from rallymate_evaluation.fs09_phase_handoff import (
    BUNDLE_STATUS,
    BUNDLE_VERSION,
    FS09PhaseBlindHandoffError,
    build_fs09_phase_blind_handoff,
    validate_fs09_phase_blind_handoff,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PACK = ROOT / "data" / "annotations" / "fs09-phase-truth-m77-v1"
SCHEMA = ROOT / "contracts" / "fs09-phase-blind-handoff.schema.json"
BUILD_CLI = ROOT / "scripts" / "build_m88_fs09_phase_blind_handoff.py"
SERVER_NAME = "serve_fs09_phase_blind_handoff.py"
MANIFEST_NAME = "handoff-manifest.json"
PUBLIC_PATHS = {
    "OPERATOR_README.md",
    "analysis-plan.json",
    "fs09-phase-truth-workbench.css",
    "fs09-phase-truth-workbench.js",
    "media/task-001-browser.mp4",
    "media/task-002-browser.mp4",
    "review.html",
    SERVER_NAME,
}


def _write_manifest(path: Path, manifest: dict) -> None:
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _content_root(artifacts: list[dict]) -> str:
    canonical = json.dumps(
        {"artifacts": artifacts},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest().upper()


def _tree(root: Path) -> set[str]:
    return {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file()
    }


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


class FS09PhaseBlindHandoffTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls._temp.name)
        cls.bundle = cls.root / "built-handoff"
        cls.manifest = build_fs09_phase_blind_handoff(SOURCE_PACK, cls.bundle)
        cls.schema = json.loads(SCHEMA.read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls) -> None:
        cls._temp.cleanup()

    def _copy(self, name: str) -> Path:
        target = self.root / name
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(self.bundle, target)
        return target

    def _coordinated_rebind(
        self,
        target: Path,
        *,
        mutate_plan,
        replacement_js: bytes | None = None,
        canonical_review_transform=None,
        replacement_readme: bytes | None = None,
    ) -> None:
        manifest_path = target / MANIFEST_NAME
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        authority = manifest["source_authority"]
        canonical_review = handoff_module._recover_canonical_review_html(
            (target / "review.html").read_bytes(),
            bundle_id=manifest["bundle_id"],
            analysis_plan_sha256=authority["analysis_plan_sha256"],
        )
        plan_path = target / "analysis-plan.json"
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        changed_paths = {"analysis-plan.json", "review.html"}
        if replacement_js is not None:
            js_path = target / "fs09-phase-truth-workbench.js"
            old_js_sha = hashlib.sha256(js_path.read_bytes()).hexdigest().upper()
            new_js_sha = hashlib.sha256(replacement_js).hexdigest().upper()
            canonical_review = canonical_review.replace(
                old_js_sha.encode("ascii"), new_js_sha.encode("ascii")
            )
            plan["study_binding"]["workbench_artifact_sha256"][
                "fs09-phase-truth-workbench.js"
            ] = new_js_sha
            plan["study_binding"]["workbench_artifact_sha256"][
                "review.html"
            ] = hashlib.sha256(canonical_review).hexdigest().upper()
            js_path.write_bytes(replacement_js)
            changed_paths.add("fs09-phase-truth-workbench.js")
        if canonical_review_transform is not None:
            canonical_review = canonical_review_transform(canonical_review)
            plan["study_binding"]["workbench_artifact_sha256"][
                "review.html"
            ] = hashlib.sha256(canonical_review).hexdigest().upper()
        if replacement_readme is not None:
            readme_path = target / "OPERATOR_README.md"
            readme_path.write_bytes(replacement_readme)
            plan["study_binding"]["blind_handoff_artifact_sha256"][
                "OPERATOR_README.md"
            ] = hashlib.sha256(replacement_readme).hexdigest().upper()
            changed_paths.add("OPERATOR_README.md")
        mutate_plan(plan)
        plan_path.write_text(
            json.dumps(plan, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        plan_sha = hashlib.sha256(plan_path.read_bytes()).hexdigest().upper()
        authority["analysis_plan_sha256"] = plan_sha
        bundle_id = handoff_module._bundle_identity(
            authority,
            manifest["scope"]["task_ids"],
            manifest["generated_at"],
        )
        manifest["bundle_id"] = bundle_id
        (target / "review.html").write_bytes(
            handoff_module._public_review_html(
                canonical_review,
                bundle_id=bundle_id,
                analysis_plan_sha256=plan_sha,
            )
        )
        for relative in changed_paths:
            record = next(
                item for item in manifest["artifacts"] if item["path"] == relative
            )
            raw = (target / relative).read_bytes()
            record["bytes"] = len(raw)
            record["sha256"] = hashlib.sha256(raw).hexdigest().upper()
        manifest["content_root_sha256"] = _content_root(manifest["artifacts"])
        _write_manifest(manifest_path, manifest)

    def _run_bundled_validate_only(self, target: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(target / SERVER_NAME),
                "--directory",
                str(target),
                "--validate-only",
            ],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_schema_runtime_and_source_replay_accept_moved_bundle(self) -> None:
        Draft202012Validator.check_schema(self.schema)
        Draft202012Validator(
            self.schema, format_checker=FormatChecker()
        ).validate(self.manifest)
        moved = self.root / "moved" / "renamed-handoff"
        moved.parent.mkdir()
        shutil.copytree(self.bundle, moved)
        snapshot = validate_fs09_phase_blind_handoff(
            moved, source_pack_dir=SOURCE_PACK
        )
        self.assertEqual(BUNDLE_VERSION, snapshot["manifest"]["bundle_version"])
        self.assertEqual(snapshot["bundle_id"], self.manifest["bundle_id"])
        self.assertEqual(
            snapshot["content_root_sha256"], self.manifest["content_root_sha256"]
        )
        self.assertEqual(
            snapshot["manifest_sha256"],
            hashlib.sha256(snapshot["manifest_raw"]).hexdigest().upper(),
        )
        self.assertEqual(PUBLIC_PATHS, set(snapshot["artifacts"]))
        self.assertEqual(moved.resolve(), snapshot["bundle_dir"])
        for name, artifact in snapshot["artifacts"].items():
            self.assertEqual(artifact["bytes"], len(artifact["raw"]), name)
            self.assertEqual(
                artifact["sha256"],
                hashlib.sha256(artifact["raw"]).hexdigest().upper(),
                name,
            )

    def test_manifest_is_technical_only_until_external_protocol_is_verified(self) -> None:
        self.assertEqual(
            "technical_handoff_verified_external_protocol_required",
            BUNDLE_STATUS,
        )
        self.assertEqual(BUNDLE_STATUS, self.manifest["status"])
        self.assertRegex(
            self.manifest["generated_at"],
            r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$",
        )
        self.assertTrue(
            self.manifest["source_authority"]["manifest_generated_at"].endswith("Z")
        )
        self.assertEqual(
            {
                "annotation_execution_authorized": False,
                "external_protocol_receipt_verified": False,
                "coarse_target_selection_anchor_included": True,
                "target_selection_anchor_is_candidate_boundary_or_phase": False,
                "exact_candidate_boundaries_derivable_from_public_window": False,
                "exact_candidate_boundaries_or_phases_embedded_in_bundle": False,
                "isolated_public_bundle_only_required": True,
                "coarse_anchor_is_confidential": False,
                "candidate_contract_hash_is_confidential": False,
                "sealed_candidates_artifact_hash_is_confidential": False,
                "candidate_derived_hashes_are_confidential": False,
                "candidate_derived_hashes_are_content_commitments_only": True,
            },
            {
                key: self.manifest["safety"][key]
                for key in (
                    "annotation_execution_authorized",
                    "external_protocol_receipt_verified",
                    "coarse_target_selection_anchor_included",
                    "target_selection_anchor_is_candidate_boundary_or_phase",
                    "exact_candidate_boundaries_derivable_from_public_window",
                    "exact_candidate_boundaries_or_phases_embedded_in_bundle",
                    "isolated_public_bundle_only_required",
                    "coarse_anchor_is_confidential",
                    "candidate_contract_hash_is_confidential",
                    "sealed_candidates_artifact_hash_is_confidential",
                    "candidate_derived_hashes_are_confidential",
                    "candidate_derived_hashes_are_content_commitments_only",
                )
            },
        )
        readme = (self.bundle / "OPERATOR_README.md").read_text(encoding="utf-8")
        self.assertTrue(readme.startswith("# HARD STOP — technical handoff only\n"))
        self.assertIn("Do not begin annotator A work", readme)
        self.assertIn("reviewer C work, or the first browser save", readme)
        self.assertIn("independent external protocol receipt or trust anchor", readme)
        self.assertIn("binds the raw `handoff-manifest.json` SHA-256", readme)
        self.assertIn("predate every label", readme)
        self.assertIn("This bundle is technical-only", readme)
        self.assertIn("Exact sealed candidate boundary and phase values are not embedded", readme)
        self.assertIn("coarse candidate-selected localization", readme)
        self.assertIn("not a claim of global secrecy", readme)
        self.assertIn(
            "Neither the coarse anchors nor any candidate-derived hashes", readme
        )
        self.assertIn(
            "are confidential against known-source or dictionary lookup", readme
        )
        self.assertIn("candidate-derived hashes", readme)
        self.assertIn("candidate-contract hash and sealed-candidates-artifact hash", readme)
        self.assertIn("content commitments only", readme)
        self.assertIn("do not edit, unlock, or use this technical bundle", readme)
        self.assertIn("future controlled tool must generate a separate", readme)
        self.assertIn("M88 does not provide that receipt-to-authorized-handoff path", readme)
        self.assertIn("only separate verified copies of the authorized public bundle", readme)
        self.assertIn("must not receive the repository, reports, runtime event files", readme)

    def test_noncanonical_utc_offset_timestamp_is_rejected(self) -> None:
        target = self._copy("noncanonical-generated-at")
        manifest_path = target / MANIFEST_NAME
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["generated_at"] = manifest["generated_at"].removesuffix("Z") + "+00:00"
        _write_manifest(manifest_path, manifest)
        with self.assertRaisesRegex(
            FS09PhaseBlindHandoffError, "canonical RFC3339 UTC"
        ):
            validate_fs09_phase_blind_handoff(target)
        errors = list(
            Draft202012Validator(
                self.schema, format_checker=FormatChecker()
            ).iter_errors(manifest)
        )
        self.assertTrue(errors)
        completed = subprocess.run(
            [
                sys.executable,
                str(target / SERVER_NAME),
                "--directory",
                str(target),
                "--validate-only",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(2, completed.returncode, completed.stdout)
        self.assertIn("canonical RFC3339 UTC", completed.stderr)

    def test_generated_at_is_part_of_bundle_identity(self) -> None:
        class TwoBuildClock(datetime):
            values = iter(
                (
                    datetime(2026, 8, 31, 0, 0, 0, 1, tzinfo=timezone.utc),
                    datetime(2026, 8, 31, 0, 0, 0, 2, tzinfo=timezone.utc),
                )
            )

            @classmethod
            def now(cls, tz=None):  # type: ignore[no-untyped-def]
                value = next(cls.values)
                return value if tz is None else value.astimezone(tz)

        first = self.root / "identity-first"
        second = self.root / "identity-second"
        with patch.object(handoff_module, "datetime", TwoBuildClock):
            first_manifest = build_fs09_phase_blind_handoff(SOURCE_PACK, first)
            second_manifest = build_fs09_phase_blind_handoff(SOURCE_PACK, second)
        self.assertNotEqual(first_manifest["generated_at"], second_manifest["generated_at"])
        self.assertNotEqual(first_manifest["bundle_id"], second_manifest["bundle_id"])
        self.assertEqual(
            first_manifest["source_authority"], second_manifest["source_authority"]
        )
        self.assertIn(
            first_manifest["bundle_id"],
            (first / "review.html").read_text(encoding="utf-8"),
        )
        self.assertIn(
            second_manifest["bundle_id"],
            (second / "review.html").read_text(encoding="utf-8"),
        )

    def test_public_tree_excludes_candidate_records_and_exact_private_timestamps(self) -> None:
        self.assertEqual(PUBLIC_PATHS | {MANIFEST_NAME}, _tree(self.bundle))
        self.assertFalse((self.bundle / "manifest.json").exists())
        self.assertFalse((self.bundle / "tasks.jsonl").exists())
        self.assertFalse((self.bundle / "sealed-event-candidates.jsonl").exists())
        self.assertFalse((self.bundle / "compiled").exists())
        manifest_text = (self.bundle / MANIFEST_NAME).read_text(encoding="utf-8")
        self.assertNotIn(str(SOURCE_PACK.resolve()), manifest_text)
        self.assertNotRegex(manifest_text, r"[A-Za-z]:[\\/]")
        for record in self.manifest["artifacts"]:
            self.assertFalse(Path(record["path"]).is_absolute())
            self.assertNotIn("..", Path(record["path"]).parts)

        private_candidates = [
            json.loads(line)
            for line in (SOURCE_PACK / "sealed-event-candidates.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
            if line.strip()
        ]
        text = "\n".join(
            (self.bundle / name).read_text(encoding="utf-8")
            for name in PUBLIC_PATHS
            if Path(name).suffix in {".json", ".html", ".js", ".css", ".md", ".py"}
        )
        self.assertNotIn("sealed-event-candidates.jsonl", text)
        self.assertNotIn(str(SOURCE_PACK.resolve()), text)
        self.assertNotRegex(text, r"[A-Za-z]:\\Users\\")
        for candidate in private_candidates:
            for value in (
                candidate["event_id"],
                candidate["start_ms"],
                candidate["end_ms"],
                *candidate["key_phases_ms"].values(),
            ):
                self.assertNotIn(str(value), text)

        private_tasks = {
            row["task_id"]: row
            for row in (
                json.loads(line)
                for line in (SOURCE_PACK / "tasks.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
                if line.strip()
            )
        }
        margins: list[tuple[int, int]] = []
        for candidate in private_candidates:
            task = private_tasks[candidate["task_id"]]
            self.assertEqual(5000, task["review_end_ms"] - task["review_start_ms"])
            self.assertNotEqual(
                candidate["start_ms"], task["review_start_ms"] + 1500
            )
            self.assertNotEqual(
                candidate["end_ms"], task["review_end_ms"] - 1500
            )
            public_time_constants = {
                task["review_start_ms"],
                task["review_end_ms"],
                task["source_time_offset_ms"],
                task["target_selection_anchor_ms"],
            }
            self.assertTrue(
                {
                    candidate["start_ms"],
                    candidate["end_ms"],
                    *candidate["key_phases_ms"].values(),
                }.isdisjoint(public_time_constants)
            )
            self.assertIn(
                "does_not_constrain_submitted_boundary_or_phase",
                task["target_selection_rule"],
            )
            margins.append(
                (
                    candidate["start_ms"] - task["review_start_ms"],
                    task["review_end_ms"] - candidate["end_ms"],
                )
            )
        self.assertNotIn((1500, 1500), margins)
        self.assertEqual(len(margins), len(set(margins)))

    def test_public_html_has_handoff_gate_and_replays_private_html(self) -> None:
        html = (self.bundle / "review.html").read_text(encoding="utf-8")
        self.assertIn(self.manifest["bundle_id"], html)
        self.assertIn(
            self.manifest["source_authority"]["analysis_plan_sha256"], html
        )
        self.assertIn("交回人工导出", html)
        self.assertIn('"annotation_execution_authorized": false', html)
        self.assertIn('"external_protocol_receipt_verified": false', html)
        self.assertNotIn("Python 门禁", html)
        self.assertNotIn("scripts/compile_m77", html)

    def test_every_artifact_single_byte_tamper_is_rejected(self) -> None:
        for index, relative in enumerate(sorted(PUBLIC_PATHS), start=1):
            with self.subTest(artifact=relative):
                tampered = self._copy(f"artifact-tamper-{index}")
                path = tampered / relative
                path.write_bytes(path.read_bytes() + b"x")
                with self.assertRaisesRegex(
                    FS09PhaseBlindHandoffError, "artifact mismatch"
                ):
                    validate_fs09_phase_blind_handoff(tampered)

    def test_coordinated_rehash_cannot_replace_plan_bound_assets_or_review(self) -> None:
        for index, relative in enumerate(
            ("fs09-phase-truth-workbench.js", "review.html"), start=1
        ):
            with self.subTest(artifact=relative):
                tampered = self._copy(f"coordinated-tamper-{index}")
                path = tampered / relative
                path.write_bytes(path.read_bytes() + b"\n")
                manifest_path = tampered / MANIFEST_NAME
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                record = next(
                    item for item in manifest["artifacts"] if item["path"] == relative
                )
                raw = path.read_bytes()
                record["bytes"] = len(raw)
                record["sha256"] = hashlib.sha256(raw).hexdigest().upper()
                manifest["content_root_sha256"] = _content_root(manifest["artifacts"])
                _write_manifest(manifest_path, manifest)
                with self.assertRaises(FS09PhaseBlindHandoffError):
                    validate_fs09_phase_blind_handoff(tampered)

    def test_bundled_server_rejects_coordinated_protocol_gate_rehash(self) -> None:
        tampered = self._copy("standalone-semantic-rehash")
        review_path = tampered / "review.html"
        review = review_path.read_text(encoding="utf-8")
        old = '"annotation_execution_authorized": false'
        self.assertEqual(1, review.count(old))
        review_path.write_text(
            review.replace(old, '"annotation_execution_authorized": true', 1),
            encoding="utf-8",
        )
        manifest_path = tampered / MANIFEST_NAME
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        record = next(
            item for item in manifest["artifacts"] if item["path"] == "review.html"
        )
        raw = review_path.read_bytes()
        record["bytes"] = len(raw)
        record["sha256"] = hashlib.sha256(raw).hexdigest().upper()
        manifest["content_root_sha256"] = _content_root(manifest["artifacts"])
        _write_manifest(manifest_path, manifest)
        completed = subprocess.run(
            [
                sys.executable,
                str(tampered / SERVER_NAME),
                "--directory",
                str(tampered),
                "--validate-only",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(2, completed.returncode, completed.stdout)
        self.assertIn("bootstrap contract drifted", completed.stderr)

    def test_bundled_server_rejects_coordinated_metric_plan_rehash(self) -> None:
        tampered = self._copy("standalone-metric-plan-rehash")

        def mutate(plan: dict) -> None:
            plan["metric_plan"]["numeric_convention"][
                "output_rounding_decimal_places"
            ] = 7

        self._coordinated_rebind(tampered, mutate_plan=mutate)
        completed = self._run_bundled_validate_only(tampered)
        self.assertEqual(2, completed.returncode, completed.stdout)
        self.assertIn("metric plan drifted", completed.stderr)

    def test_bundled_server_rejects_coordinated_javascript_rehash(self) -> None:
        tampered = self._copy("standalone-javascript-rehash")
        original_js = (tampered / "fs09-phase-truth-workbench.js").read_bytes()
        self._coordinated_rebind(
            tampered,
            mutate_plan=lambda _plan: None,
            replacement_js=original_js + b"\n// coordinated semantic tamper\n",
        )
        completed = self._run_bundled_validate_only(tampered)
        self.assertEqual(2, completed.returncode, completed.stdout)
        self.assertIn("JS authority drifted", completed.stderr)

    def test_bundled_server_rejects_coordinated_outer_html_rehash(self) -> None:
        tampered = self._copy("standalone-outer-html-rehash")

        def inject_inline_script(raw: bytes) -> bytes:
            marker = b"</body>"
            self.assertEqual(1, raw.count(marker))
            return raw.replace(
                marker,
                b"<script>window.annotation_execution_authorized=true</script></body>",
                1,
            )

        self._coordinated_rebind(
            tampered,
            mutate_plan=lambda _plan: None,
            canonical_review_transform=inject_inline_script,
        )
        completed = self._run_bundled_validate_only(tampered)
        self.assertEqual(2, completed.returncode, completed.stdout)
        self.assertIn("canonical review authority drifted", completed.stderr)

    def test_bundled_server_rejects_coordinated_public_readme_rehash(self) -> None:
        tampered = self._copy("standalone-public-readme-rehash")
        self._coordinated_rebind(
            tampered,
            mutate_plan=lambda _plan: None,
            replacement_readme=b"# Instructions removed\n",
        )
        completed = self._run_bundled_validate_only(tampered)
        self.assertEqual(2, completed.returncode, completed.stdout)
        self.assertIn("public README authority drifted", completed.stderr)

    def test_manifest_bundle_id_content_root_and_paths_are_strict(self) -> None:
        mutations = (
            ("bundle-id", lambda value: value.__setitem__("bundle_id", "m88-fs09-blind-" + "A" * 64)),
            (
                "generated-at",
                lambda value: value.__setitem__(
                    "generated_at", "2099-01-01T00:00:00Z"
                ),
            ),
            ("content-root", lambda value: value.__setitem__("content_root_sha256", "A" * 64)),
            ("absolute-path", lambda value: value["artifacts"][0].__setitem__("path", "C:/private.txt")),
            ("source-sha", lambda value: value["source_authority"].__setitem__("manifest_sha256", "A" * 64)),
        )
        for index, (name, mutate) in enumerate(mutations, start=1):
            with self.subTest(mutation=name):
                target = self._copy(f"manifest-tamper-{index}")
                manifest_path = target / MANIFEST_NAME
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                mutate(manifest)
                _write_manifest(manifest_path, manifest)
                with self.assertRaises(FS09PhaseBlindHandoffError):
                    validate_fs09_phase_blind_handoff(target)

    def test_extra_file_and_extra_directory_are_rejected(self) -> None:
        extra_file = self._copy("extra-file")
        (extra_file / "unexpected.txt").write_text("unexpected", encoding="utf-8")
        with self.assertRaisesRegex(FS09PhaseBlindHandoffError, "tree"):
            validate_fs09_phase_blind_handoff(extra_file)

        extra_dir = self._copy("extra-directory")
        (extra_dir / "unexpected-directory").mkdir()
        with self.assertRaisesRegex(FS09PhaseBlindHandoffError, "tree"):
            validate_fs09_phase_blind_handoff(extra_dir)

    def test_builder_is_atomic_rejects_overlap_existing_and_leaves_no_residue(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            existing = root / "existing"
            existing.mkdir()
            with self.assertRaises(FS09PhaseBlindHandoffError):
                build_fs09_phase_blind_handoff(SOURCE_PACK, existing)

            overlap = SOURCE_PACK / "m88-overlap-must-not-exist"
            staging_overlap = overlap.with_name(f".{overlap.name}.building")
            self.assertFalse(overlap.exists())
            self.assertFalse(staging_overlap.exists())
            with self.assertRaises(FS09PhaseBlindHandoffError):
                build_fs09_phase_blind_handoff(SOURCE_PACK, overlap)
            self.assertFalse(overlap.exists())
            self.assertFalse(staging_overlap.exists())

            stale_source = root / "stale-source-copy"
            shutil.copytree(SOURCE_PACK, stale_source)
            failed_output = root / "failed-output"
            failed_staging = failed_output.with_name(f".{failed_output.name}.building")
            with self.assertRaises(FS09PhaseBlindHandoffError):
                build_fs09_phase_blind_handoff(stale_source, failed_output)
            self.assertFalse(failed_output.exists())
            self.assertFalse(failed_staging.exists())

            forced_output = root / "forced-failure"
            forced_staging = forced_output.with_name(f".{forced_output.name}.building")
            with patch.object(
                handoff_module,
                "validate_fs09_phase_blind_handoff",
                side_effect=FS09PhaseBlindHandoffError("forced failure"),
            ):
                with self.assertRaisesRegex(
                    FS09PhaseBlindHandoffError, "forced failure"
                ):
                    build_fs09_phase_blind_handoff(SOURCE_PACK, forced_output)
            self.assertFalse(forced_output.exists())
            self.assertFalse(forced_staging.exists())

    def test_build_cli_and_bundled_server_validate_after_move(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "cli-handoff"
            env = os.environ.copy()
            env["PYTHONPATH"] = str(ROOT / "src") + (
                os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    str(BUILD_CLI),
                    "--source-pack",
                    str(SOURCE_PACK),
                    "--output",
                    str(output),
                ],
                capture_output=True,
                text=True,
                check=False,
                env=env,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)
            result = json.loads(completed.stdout)
            self.assertEqual(BUNDLE_STATUS, result["status"])
            moved = Path(temp) / "moved-handoff"
            output.rename(moved)
            validation = subprocess.run(
                [
                    sys.executable,
                    str(moved / SERVER_NAME),
                    "--directory",
                    str(moved),
                    "--validate-only",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(0, validation.returncode, validation.stderr)
            payload = json.loads(validation.stdout)
            self.assertTrue(payload["ok"])
            self.assertEqual(BUNDLE_STATUS, payload["bundle_status"])
            self.assertEqual(
                hashlib.sha256((moved / MANIFEST_NAME).read_bytes())
                .hexdigest()
                .upper(),
                payload["manifest_sha256"],
            )
            self.assertEqual(
                self.manifest["source_authority"]["analysis_plan_sha256"],
                payload["analysis_plan_sha256"],
            )
            self.assertEqual(result["bundle_id"], payload["bundle_id"])
            non_loopback = subprocess.run(
                [
                    sys.executable,
                    str(moved / SERVER_NAME),
                    "--directory",
                    str(moved),
                    "--bind",
                    "0.0.0.0",
                    "--validate-only",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(2, non_loopback.returncode, non_loopback.stdout)
            self.assertIn("loopback only", non_loopback.stderr)

    def test_standalone_http_server_has_root_range_and_private_path_denials(self) -> None:
        served = self._copy("http-served")
        media_size = (served / "media" / "task-002-browser.mp4").stat().st_size
        port = _free_port()
        process = subprocess.Popen(
            [
                sys.executable,
                str(served / SERVER_NAME),
                "--directory",
                str(served),
                "--bind",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            assert process.stdout is not None
            started = json.loads(process.stdout.readline())
            self.assertEqual("serving", started["status"])
            self.assertEqual(BUNDLE_STATUS, started["bundle_status"])
            self.assertEqual(
                hashlib.sha256((served / MANIFEST_NAME).read_bytes())
                .hexdigest()
                .upper(),
                started["manifest_sha256"],
            )

            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            connection.request("GET", "/")
            response = connection.getresponse()
            root_body = response.read()
            self.assertEqual(200, response.status)
            self.assertIn(b"RallyMate", root_body)
            self.assertNotIn(b"Directory listing", root_body)
            connection.close()

            for host in (
                f"localhost:{port}",
                f"127.0.0.1.evil.invalid:{port}",
                f"evil.invalid:{port}",
            ):
                with self.subTest(host=host):
                    connection = http.client.HTTPConnection(
                        "127.0.0.1", port, timeout=5
                    )
                    connection.request("GET", "/", headers={"Host": host})
                    response = connection.getresponse()
                    body = response.read()
                    self.assertEqual(421, response.status)
                    self.assertIn(b"host_not_allowed", body)
                    connection.close()

            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            connection.putrequest("GET", "/", skip_host=True)
            connection.putheader("Host", f"127.0.0.1:{port}")
            connection.putheader("Host", f"evil.invalid:{port}")
            connection.endheaders()
            response = connection.getresponse()
            body = response.read()
            self.assertEqual(421, response.status)
            self.assertIn(b"host_not_allowed", body)
            connection.close()

            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            connection.request(
                "GET",
                "/media/task-002-browser.mp4",
                headers={"Range": "bytes=100-199"},
            )
            response = connection.getresponse()
            body = response.read()
            self.assertEqual(206, response.status)
            self.assertEqual(
                f"bytes 100-199/{media_size}", response.getheader("Content-Range")
            )
            self.assertEqual(100, len(body))
            connection.close()

            for path in (
                "/sealed-event-candidates.jsonl",
                "/../sealed-event-candidates.jsonl",
                "/%2e%2e/sealed-event-candidates.jsonl",
                "/media/../analysis-plan.json",
                "/unknown",
            ):
                with self.subTest(path=path):
                    connection = http.client.HTTPConnection(
                        "127.0.0.1", port, timeout=5
                    )
                    connection.request("GET", path)
                    response = connection.getresponse()
                    response.read()
                    self.assertEqual(404, response.status)
                    connection.close()

            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            connection.request(
                "GET",
                "/media/task-002-browser.mp4",
                headers={"Range": "bytes=200-100"},
            )
            response = connection.getresponse()
            response.read()
            self.assertEqual(416, response.status)
            self.assertEqual(
                f"bytes */{media_size}", response.getheader("Content-Range")
            )
            connection.close()

            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            connection.request("POST", "/")
            response = connection.getresponse()
            response.read()
            self.assertEqual(405, response.status)
            self.assertEqual("GET, HEAD", response.getheader("Allow"))
            connection.close()
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()


if __name__ == "__main__":
    unittest.main()
