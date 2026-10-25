from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from rallymate_service.api import create_app
from rallymate_service.config import ServiceSettings
from rallymate_service.deployment_identity import load_deployment_identity


UNAVAILABLE = {"source_manifest_sha256": None, "source_files_verified": False,
               "manifest_unchanged_since_startup": False}


class DeploymentIdentityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "src" / "private-name.py"
        self.source.parent.mkdir()
        self.source.write_bytes(b"private file content must not be exposed")
        self.manifest = self.root / "source-manifest.json"
        self.entry = {"path": "src/private-name.py", "bytes": self.source.stat().st_size,
                      "sha256": hashlib.sha256(self.source.read_bytes()).hexdigest()}

    def write_manifest(self, entries=None):
        self.manifest.write_text(json.dumps({"kind": "test-snapshot", "files": entries or [self.entry]}), encoding="utf-8")

    def test_valid_manifest_binds_exact_bytes_without_disclosing_files(self):
        self.write_manifest()
        status = load_deployment_identity(self.root).public_status()
        self.assertEqual(status, {"source_manifest_sha256": hashlib.sha256(self.manifest.read_bytes()).hexdigest(),
                                  "source_files_verified": True, "manifest_unchanged_since_startup": True})
        self.assertNotIn("private", json.dumps(status))
        self.assertNotIn(str(self.root), json.dumps(status))

    def test_missing_malformed_empty_or_duplicate_key_manifest_is_unavailable(self):
        self.assertEqual(load_deployment_identity(self.root).public_status(), UNAVAILABLE)
        for raw in (b"{", b"[]", b'{"files":[]}', b'{"files":{}}', b'{"files":[],"files":[]}', b'\xff'):
            with self.subTest(raw=raw):
                self.manifest.write_bytes(raw)
                self.assertEqual(load_deployment_identity(self.root).public_status(), UNAVAILABLE)

    def test_every_file_hash_size_and_existence_are_checked(self):
        for entries in ([self.entry, {"path": "missing.py", "sha256": "0" * 64}],
                        [{**self.entry, "sha256": "0" * 64}], [{**self.entry, "sha256": "x"}],
                        [{**self.entry, "bytes": 0}], [{**self.entry, "bytes": True}],
                        [self.entry, self.entry], [None]):
            with self.subTest(entries=entries):
                self.write_manifest(entries)
                self.assertEqual(load_deployment_identity(self.root).public_status(), UNAVAILABLE)
        self.write_manifest()
        self.source.write_bytes(b"changed source bytes")
        self.assertEqual(load_deployment_identity(self.root).public_status(), UNAVAILABLE)

    def test_absolute_traversal_drive_and_ambiguous_paths_fail_closed(self):
        for name in ("../private.py", "src/../private.py", "/private.py", "C:/private.py",
                     "C:private.py", "\\\\host\\private.py", "src\\private-name.py", "src//private-name.py",
                     "./src/private-name.py", "src/./private-name.py", "", "src/private-name.py\x00"):
            with self.subTest(name=name):
                self.write_manifest([{**self.entry, "path": name}])
                self.assertEqual(load_deployment_identity(self.root).public_status(), UNAVAILABLE)

    def test_resolved_symlink_escape_is_rejected_before_reading_external_content(self):
        self.write_manifest()
        original_resolve = Path.resolve
        external = self.root.parent / "external-secret.py"

        def resolved(path, *args, **kwargs):
            if path == self.source:
                return external
            return original_resolve(path, *args, **kwargs)

        # Emulates OS symlink resolution without requiring Windows symlink privileges.
        with patch.object(Path, "resolve", resolved), patch("rallymate_service.deployment_identity._file_digest") as digest:
            self.assertEqual(load_deployment_identity(self.root).public_status(), UNAVAILABLE)
        digest.assert_not_called()

    def test_manifest_change_after_startup_retains_original_identity_and_flags_change(self):
        self.write_manifest()
        identity = load_deployment_identity(self.root)
        startup = identity.public_status()["source_manifest_sha256"]
        self.manifest.write_bytes(self.manifest.read_bytes() + b"\n")
        self.assertEqual(identity.public_status(), {"source_manifest_sha256": startup,
                         "source_files_verified": True, "manifest_unchanged_since_startup": False})
        self.manifest.unlink()
        self.assertEqual(identity.public_status(), UNAVAILABLE)

    def test_absent_at_startup_cannot_be_promoted_by_later_manifest(self):
        identity = load_deployment_identity(self.root)
        self.write_manifest()
        self.assertEqual(identity.public_status(), UNAVAILABLE)

    def test_manifest_change_during_initial_hashing_is_rejected(self):
        self.write_manifest()
        original = self.manifest.read_bytes()
        with patch("rallymate_service.deployment_identity._manifest_bytes", side_effect=[original, original + b"\n"]):
            self.assertEqual(load_deployment_identity(self.root).public_status(), UNAVAILABLE)

    def test_unreadable_manifest_or_source_is_unavailable(self):
        self.write_manifest()
        for function in ("_manifest_bytes", "_file_digest"):
            with self.subTest(function=function), patch("rallymate_service.deployment_identity." + function, side_effect=PermissionError("private path")):
                self.assertEqual(load_deployment_identity(self.root).public_status(), UNAVAILABLE)

    def test_meta_exposes_identity_and_change_without_breaking_service(self):
        self.write_manifest()
        identity = load_deployment_identity(self.root)
        settings = ServiceSettings(data_root=self.root / "service", database_path=self.root / "service/jobs.sqlite3",
                                   detect_model=self.root / "detect.pt", pose_model=self.root / "pose.pt")
        with patch("rallymate_service.api.load_deployment_identity", return_value=identity):
            app = create_app(settings)
        with TestClient(app) as client:
            self.assertEqual(client.get("/v1/meta").json()["deployment"], identity.public_status())
            self.manifest.unlink()
            response = client.get("/v1/meta")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["deployment"], UNAVAILABLE)
            self.assertEqual(client.get("/health/live").status_code, 200)


if __name__ == "__main__":
    unittest.main()
