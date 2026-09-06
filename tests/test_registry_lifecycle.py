from __future__ import annotations

import copy
import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any, Callable

try:
    from jsonschema import Draft202012Validator
except ModuleNotFoundError:  # Runtime validation has no jsonschema dependency.
    Draft202012Validator = None

from rallymate_scoring.feasibility import load_feasibility_registry
from rallymate_scoring.registry_lifecycle import (
    RegistryLifecycleError,
    load_registry_lifecycle,
    resolve_current_scoring_requirements,
    resolve_registry_role,
    resolve_runtime_feasibility_registry,
    validate_registry_lifecycle_artifacts,
)


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "registry-lifecycle.json"
SCHEMA_PATH = ROOT / "contracts" / "registry-lifecycle.schema.json"

KNOWN_RAW_SHA256 = {
    "roles.runtime_feasibility": (
        "e6b7c3a05da08a7bc96821de3662113111653e10ec46d20a037578ad3f6e3305"
    ),
    "roles.current_scoring_requirements": (
        "552c4aeeff4bc68fb3fdf82e6a6e5a31ea978edbb5c87e0b8bf967d4b4f2fb95"
    ),
    "non_runtime_artifacts.historical_feasibility": (
        "5f7952c6d9514856ad979e27a556cfa2d80b188eaeffd8333a25f32aacf5dfcc"
    ),
    "non_runtime_artifacts.measurement_plans": (
        "f22eb67ca23e927a8c7712221e8fee5cda16488a9133a65cb0c4eeb290132a08"
    ),
}


class RegistryLifecycleTests(unittest.TestCase):
    def _copy_authority(self, root: Path) -> Path:
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        for section in ("roles", "non_runtime_artifacts"):
            for binding in manifest[section].values():
                relative_path = binding["relative_path"]
                shutil.copy2(ROOT / relative_path, root / relative_path)
        path = root / "registry-lifecycle.json"
        path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return path

    def _manifest(self, path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))

    def _write_manifest(self, path: Path, manifest: dict[str, Any]) -> None:
        path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _mutate_artifact_and_rebind_hash(
        self,
        manifest_path: Path,
        *,
        section: str,
        name: str,
        mutate: Callable[[dict[str, Any]], None],
    ) -> None:
        manifest = self._manifest(manifest_path)
        binding = manifest[section][name]
        artifact_path = manifest_path.parent / binding["relative_path"]
        payload = json.loads(artifact_path.read_text(encoding="utf-8"))
        mutate(payload)
        raw_bytes = (
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8")
        artifact_path.write_bytes(raw_bytes)
        binding["file_sha256"] = hashlib.sha256(raw_bytes).hexdigest()
        self._write_manifest(manifest_path, manifest)

    def _inject_raw_artifact_member_and_rebind_hash(
        self,
        manifest_path: Path,
        *,
        raw_member: bytes,
    ) -> None:
        manifest = self._manifest(manifest_path)
        binding = manifest["roles"]["runtime_feasibility"]
        artifact_path = manifest_path.parent / binding["relative_path"]
        raw_bytes = artifact_path.read_bytes()
        object_start = raw_bytes.index(b"{")
        invalid_raw_bytes = (
            raw_bytes[: object_start + 1]
            + raw_member
            + b","
            + raw_bytes[object_start + 1 :]
        )
        artifact_path.write_bytes(invalid_raw_bytes)
        binding["file_sha256"] = hashlib.sha256(invalid_raw_bytes).hexdigest()
        self._write_manifest(manifest_path, manifest)

    def test_checked_in_schema_and_manifest_are_strict_and_valid(self) -> None:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        manifest = load_registry_lifecycle(MANIFEST_PATH)
        self.assertEqual(
            schema["$schema"], "https://json-schema.org/draft/2020-12/schema"
        )
        self.assertEqual(
            set(manifest),
            {
                "schema_version",
                "artifact_scope",
                "authority_version",
                "roles",
                "non_runtime_artifacts",
            },
        )
        self.assertNotIn("latest", json.dumps(manifest))
        self.assertNotIn("entries", manifest)
        if Draft202012Validator is not None:
            Draft202012Validator.check_schema(schema)
            self.assertEqual(
                [], list(Draft202012Validator(schema).iter_errors(manifest))
            )

    def test_checked_in_authority_matches_all_known_raw_hashes_and_versions(
        self,
    ) -> None:
        resolved = validate_registry_lifecycle_artifacts(MANIFEST_PATH)
        self.assertEqual(set(resolved), set(KNOWN_RAW_SHA256))
        for slot, expected_sha256 in KNOWN_RAW_SHA256.items():
            artifact = resolved[slot]
            self.assertEqual(artifact.file_sha256, expected_sha256)
            self.assertEqual(
                hashlib.sha256(artifact.raw_bytes).hexdigest(), expected_sha256
            )
            self.assertEqual(artifact.path.read_bytes(), artifact.raw_bytes)
            self.assertEqual(artifact.manifest_path, MANIFEST_PATH.resolve())
            self.assertEqual(
                artifact.manifest_sha256,
                hashlib.sha256(MANIFEST_PATH.read_bytes()).hexdigest(),
            )

        runtime = resolved["roles.runtime_feasibility"]
        requirements = resolved["roles.current_scoring_requirements"]
        historical = resolved[
            "non_runtime_artifacts.historical_feasibility"
        ]
        planning = resolved["non_runtime_artifacts.measurement_plans"]
        self.assertEqual(runtime.lifecycle, "current")
        self.assertEqual(runtime.embedded_version, "pose-wave-2026-08-22.17")
        self.assertEqual(
            runtime.source_registry_version,
            historical.embedded_version,
        )
        self.assertEqual(requirements.lifecycle, "derived_current")
        self.assertEqual(
            requirements.source_registry_version,
            runtime.embedded_version,
        )
        self.assertEqual(historical.lifecycle, "historical")
        self.assertEqual(planning.lifecycle, "planning_only")
        self.assertEqual(
            planning.source_registry_version, "pose-wave-2026-08-21.8"
        )

    def test_runtime_role_returns_same_validated_payload_as_low_level_loader(
        self,
    ) -> None:
        resolved = resolve_runtime_feasibility_registry(MANIFEST_PATH)
        self.assertEqual(resolved.role, "runtime_feasibility")
        self.assertEqual(
            resolved.payload,
            load_feasibility_registry(
                ROOT / "metric-feasibility-pose-wave-v2.json"
            ),
        )
        requirements = resolve_current_scoring_requirements(MANIFEST_PATH)
        self.assertEqual(requirements.role, "current_scoring_requirements")
        self.assertEqual(requirements.kind, "indicator_scoring_requirements")

    def test_unknown_and_non_runtime_names_cannot_be_resolved_as_roles(self) -> None:
        for role in ("historical_feasibility", "measurement_plans", "latest"):
            with self.subTest(role=role):
                with self.assertRaisesRegex(
                    RegistryLifecycleError, "unsupported registry lifecycle role"
                ):
                    resolve_registry_role(MANIFEST_PATH, role)  # type: ignore[arg-type]

    def test_runtime_roles_reject_wrong_lifecycle_and_kind(self) -> None:
        mutations = (
            ("runtime_feasibility", "lifecycle", "historical", "lifecycle"),
            ("runtime_feasibility", "lifecycle", "planning_only", "lifecycle"),
            (
                "current_scoring_requirements",
                "lifecycle",
                "current",
                "lifecycle",
            ),
            (
                "runtime_feasibility",
                "kind",
                "metric_measurement_plans",
                "kind",
            ),
        )
        for role, field, value, message in mutations:
            with self.subTest(role=role, field=field, value=value):
                with tempfile.TemporaryDirectory() as directory:
                    path = self._copy_authority(Path(directory))
                    manifest = self._manifest(path)
                    manifest["roles"][role][field] = value
                    self._write_manifest(path, manifest)
                    with self.assertRaisesRegex(RegistryLifecycleError, message):
                        resolve_registry_role(path, role)  # type: ignore[arg-type]

    def test_manifest_rejects_duplicate_paths_in_fixed_slots(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self._copy_authority(Path(directory))
            manifest = self._manifest(path)
            manifest["non_runtime_artifacts"]["historical_feasibility"][
                "relative_path"
            ] = manifest["roles"]["runtime_feasibility"]["relative_path"]
            self._write_manifest(path, manifest)
            with self.assertRaisesRegex(RegistryLifecycleError, "paths must be unique"):
                load_registry_lifecycle(path)

    def test_manifest_rejects_normalized_path_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self._copy_authority(Path(directory))
            manifest = self._manifest(path)
            manifest["non_runtime_artifacts"]["historical_feasibility"][
                "relative_path"
            ] = "./" + manifest["roles"]["runtime_feasibility"]["relative_path"]
            self._write_manifest(path, manifest)
            with self.assertRaisesRegex(
                RegistryLifecycleError, "manifest root"
            ):
                load_registry_lifecycle(path)

    def test_relative_paths_reject_traversal_absolute_and_windows_drive_forms(
        self,
    ) -> None:
        if Draft202012Validator is None:
            self.fail("jsonschema is required by the dev/test dependency set")
        schema_validator = Draft202012Validator(
            json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        )
        for invalid_path in (
            "../metric-feasibility-pose-wave-v2.json",
            "./metric-feasibility-pose-wave-v2.json",
            "nested//metric-feasibility-pose-wave-v2.json",
            "nested/",
            "/metric-feasibility-pose-wave-v2.json",
            "C:/metric-feasibility-pose-wave-v2.json",
            "nested\\metric-feasibility-pose-wave-v2.json",
        ):
            with self.subTest(invalid_path=invalid_path):
                with tempfile.TemporaryDirectory() as directory:
                    path = self._copy_authority(Path(directory))
                    manifest = self._manifest(path)
                    manifest["roles"]["runtime_feasibility"][
                        "relative_path"
                    ] = invalid_path
                    self.assertTrue(
                        list(schema_validator.iter_errors(manifest)),
                        f"JSON Schema accepted unsafe path: {invalid_path}",
                    )
                    self._write_manifest(path, manifest)
                    with self.assertRaisesRegex(
                        RegistryLifecycleError,
                        "relative path|manifest root|forward-slash",
                    ):
                        resolve_runtime_feasibility_registry(path)

    def test_raw_file_sha_binding_fails_before_changed_json_is_used(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = self._copy_authority(root)
            manifest = self._manifest(path)
            target = root / manifest["roles"]["runtime_feasibility"][
                "relative_path"
            ]
            target.write_bytes(target.read_bytes() + b" ")
            with self.assertRaisesRegex(
                RegistryLifecycleError, "raw file SHA-256 mismatch"
            ):
                resolve_runtime_feasibility_registry(path)

    def test_each_kind_requires_its_exact_embedded_version_field(self) -> None:
        cases = (
            (
                "roles",
                "runtime_feasibility",
                lambda payload: payload.__setitem__("registry_version", "changed"),
            ),
            (
                "roles",
                "current_scoring_requirements",
                lambda payload: payload.__setitem__(
                    "requirements_version", "changed"
                ),
            ),
            (
                "non_runtime_artifacts",
                "historical_feasibility",
                lambda payload: payload.__setitem__("registry_version", "changed"),
            ),
            (
                "non_runtime_artifacts",
                "measurement_plans",
                lambda payload: payload.__setitem__("registry_version", "changed"),
            ),
        )
        for section, name, mutate in cases:
            with self.subTest(slot=f"{section}.{name}"):
                with tempfile.TemporaryDirectory() as directory:
                    path = self._copy_authority(Path(directory))
                    self._mutate_artifact_and_rebind_hash(
                        path,
                        section=section,
                        name=name,
                        mutate=mutate,
                    )
                    with self.assertRaisesRegex(
                        RegistryLifecycleError, "embedded version mismatch"
                    ):
                        validate_registry_lifecycle_artifacts(path)

    def test_each_derived_kind_requires_its_exact_source_registry_version(
        self,
    ) -> None:
        cases = (
            (
                "roles",
                "runtime_feasibility",
                lambda payload: payload["scope"].__setitem__(
                    "extends_registry", "changed"
                ),
            ),
            (
                "roles",
                "current_scoring_requirements",
                lambda payload: payload["source"].__setitem__(
                    "registry_version", "changed"
                ),
            ),
            (
                "non_runtime_artifacts",
                "measurement_plans",
                lambda payload: payload.__setitem__(
                    "source_feasibility_registry_version", "changed"
                ),
            ),
        )
        for section, name, mutate in cases:
            with self.subTest(slot=f"{section}.{name}"):
                with tempfile.TemporaryDirectory() as directory:
                    path = self._copy_authority(Path(directory))
                    self._mutate_artifact_and_rebind_hash(
                        path,
                        section=section,
                        name=name,
                        mutate=mutate,
                    )
                    with self.assertRaisesRegex(
                        RegistryLifecycleError, "source registry version mismatch"
                    ):
                        validate_registry_lifecycle_artifacts(path)

    def test_manifest_source_chain_cannot_be_redirected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self._copy_authority(Path(directory))
            manifest = self._manifest(path)
            manifest["roles"]["current_scoring_requirements"][
                "source_registry_version"
            ] = "historical-version"
            self._write_manifest(path, manifest)
            with self.assertRaisesRegex(
                RegistryLifecycleError,
                "must derive from runtime feasibility",
            ):
                load_registry_lifecycle(path)

    def test_duplicate_json_keys_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "registry-lifecycle.json"
            path.write_text(
                '{"schema_version":"1.0.0","schema_version":"1.0.0"}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RegistryLifecycleError, "duplicate JSON"):
                load_registry_lifecycle(path)

    def test_noncanonical_json_is_rejected_in_manifest_and_artifact(self) -> None:
        for case_name, manifest_raw, expected_error in (
            (
                "named-constant",
                b'{"schema_version":"1.0.0","authority_version":NaN}',
                "non-finite JSON constant",
            ),
            (
                "overflow-float",
                b'{"schema_version":"1.0.0","authority_version":1e9999}',
                "non-finite JSON number",
            ),
            (
                "isolated-surrogate",
                b'{"schema_version":"1.0.0","authority_version":"\\ud800"}',
                "cannot be encoded as UTF-8",
            ),
        ):
            with self.subTest(manifest=case_name):
                with tempfile.TemporaryDirectory() as directory:
                    manifest_path = Path(directory) / "registry-lifecycle.json"
                    manifest_path.write_bytes(manifest_raw)
                    with self.assertRaisesRegex(
                        RegistryLifecycleError, expected_error
                    ):
                        load_registry_lifecycle(manifest_path)

        for case_name, raw_member, expected_error in (
            (
                "named-constant",
                b'"nonfinite_probe":NaN',
                "non-finite JSON constant",
            ),
            (
                "overflow-float",
                b'"nonfinite_probe":1e9999',
                "non-finite JSON number",
            ),
            (
                "isolated-surrogate",
                b'"surrogate_probe":"\\ud800"',
                "cannot be encoded as UTF-8",
            ),
        ):
            with self.subTest(artifact=case_name):
                with tempfile.TemporaryDirectory() as directory:
                    manifest_path = self._copy_authority(Path(directory))
                    self._inject_raw_artifact_member_and_rebind_hash(
                        manifest_path, raw_member=raw_member
                    )
                    with self.assertRaisesRegex(
                        RegistryLifecycleError, expected_error
                    ):
                        resolve_runtime_feasibility_registry(manifest_path)


if __name__ == "__main__":
    unittest.main()
