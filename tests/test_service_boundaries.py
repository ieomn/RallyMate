from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from rallymate_scoring.technique_registry import technique_catalog
from rallymate_service.api import (
    _public_error_detail,
    _redact_readiness_reason,
    create_app,
)
from rallymate_service.config import ServiceConfigError, ServiceSettings
from rallymate_service.database import JobDatabase


class ServiceBoundaryTests(unittest.TestCase):
    def _settings(self, root: Path, **overrides) -> ServiceSettings:
        detect = root / "detect.pt"
        pose = root / "pose.pt"
        detect.write_bytes(b"test")
        pose.write_bytes(b"test")
        values = {
            "data_root": root / "service",
            "database_path": root / "service" / "jobs.sqlite3",
            "detect_model": detect,
            "pose_model": pose,
        }
        values.update(overrides)
        return ServiceSettings(**values)

    def test_web_url_and_cors_validation_rejects_ambiguous_values(self) -> None:
        invalid_values = (
            {"public_base_url": "https://"},
            {"public_base_url": "https://api.example.com/base"},
            {"public_base_url": "https://api.example.com?token=leak"},
            {"public_base_url": "https://user:pass@api.example.com"},
            {"cors_origins": ("ftp://app.example.com",)},
            {"cors_origins": ("https://app.example.com/path",)},
            {"cors_origins": ("null",)},
        )
        for values in invalid_values:
            with self.subTest(values=values), tempfile.TemporaryDirectory() as directory:
                with self.assertRaises(ServiceConfigError):
                    self._settings(Path(directory), **values).validate_license(
                        check_registry_authority=False
                    )

    def test_production_cors_wildcard_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = self._settings(
                Path(directory),
                environment="production",
                model_license_ack="enterprise",
                cors_origins=("*",),
            )
            with self.assertRaisesRegex(ServiceConfigError, "explicit origins"):
                settings.validate_license(check_registry_authority=False)

    def test_public_base_url_cors_wildcard_is_rejected_even_outside_production(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = self._settings(
                Path(directory),
                public_base_url="https://api.example.com",
                cors_origins=("*",),
            )
            with self.assertRaisesRegex(ServiceConfigError, "public deployment"):
                settings.validate_license(check_registry_authority=False)

    def test_production_readiness_requires_bearer_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = self._settings(
                root,
                environment="production",
                model_license_ack="enterprise",
            )
            app = create_app(settings, JobDatabase(settings.database_path))
            with TestClient(app) as client:
                response = client.get("/health/ready")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], "not_ready")
            self.assertIn("RALLYMATE_API_KEY", " ".join(response.json()["reasons"]))

    def test_production_readiness_rejects_placeholder_and_short_keys_without_app_build_failure(self) -> None:
        cases = (
            "replace-with-a-long-random-token",
            "too-short",
            "CHANGE_ME_" + "x" * 40,
            "dummy-token-" + "x" * 40,
        )
        for api_key in cases:
            with self.subTest(api_key=api_key), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                settings = self._settings(
                    root,
                    environment="production",
                    model_license_ack="enterprise",
                    api_key=api_key,
                )
                # create_app remains constructible for compatibility with test
                # fixtures; the deployment gate is enforced by /health/ready.
                app = create_app(settings, JobDatabase(settings.database_path))
                with TestClient(app) as client:
                    response = client.get(
                        "/health/ready",
                        headers={"Authorization": f"Bearer {api_key}"},
                    )
                self.assertEqual(response.status_code, 200)
                reasons = " ".join(response.json()["reasons"])
                self.assertIn("RALLYMATE_API_KEY", reasons)
                self.assertEqual(response.json()["status"], "not_ready")

    def test_readiness_does_not_expose_worker_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = self._settings(
                root,
                environment="production",
                model_license_ack="enterprise",
                api_key="safe-key-" + "x" * 48,
            )
            app = create_app(settings, JobDatabase(settings.database_path))
            with TestClient(app) as client:
                response = client.get(
                    "/health/ready",
                    headers={"Authorization": f"Bearer {settings.api_key}"},
                )
            self.assertEqual(response.status_code, 200)
            reasons = " ".join(response.json()["reasons"])
            self.assertNotIn(str(root), reasons)
            self.assertNotIn("detect.pt", reasons)

    def test_path_redaction_handles_spaces_and_embedded_paths(self) -> None:
        message = r"registry missing: C:\Program Files\RallyMate\config.json; retry"
        redacted = _redact_readiness_reason(message)
        self.assertNotIn("Program Files", redacted)
        self.assertEqual(redacted, "registry missing: <redacted>; retry")

    def test_path_redaction_handles_bracketed_paths_and_nested_error_values(self) -> None:
        message = r"failed: C:\tmp\foo[bar]\secret.json; retry"
        redacted = _redact_readiness_reason(message)
        self.assertNotIn("foo[bar]", redacted)
        self.assertNotIn("secret.json", redacted)

        with tempfile.TemporaryDirectory() as directory:
            settings = self._settings(
                Path(directory),
                environment="production",
                model_license_ack="enterprise",
                public_base_url="https://api.example.com",
            )
            detail = _public_error_detail(
                {"errors": [r"bad input: C:\worker\private\frame.json"]},
                settings,
            )
            self.assertNotIn("private", json.dumps(detail))

    def test_public_production_api_fails_closed_without_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = self._settings(
                root,
                environment="production",
                model_license_ack="enterprise",
                public_base_url="https://api.example.com",
            )
            app = create_app(settings, JobDatabase(settings.database_path))
            with TestClient(app) as client:
                self.assertEqual(client.get("/v1/meta").status_code, 200)
                response = client.get("/v1/model-capabilities")
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json()["detail"]["code"], "authentication_not_configured")

    def test_create_app_validates_injected_web_settings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = self._settings(root, public_base_url="https://")
            with self.assertRaises(ServiceConfigError):
                create_app(settings, JobDatabase(settings.database_path))

    def test_cors_preflight_and_request_id_are_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = self._settings(
                root,
                cors_origins=("https://app.example.com",),
            )
            app = create_app(settings, JobDatabase(settings.database_path))
            with TestClient(app) as client:
                preflight = client.options(
                    "/v1/meta",
                    headers={
                        "Origin": "https://app.example.com",
                        "Access-Control-Request-Method": "GET",
                        "Access-Control-Request-Headers": "Authorization",
                    },
                )
                self.assertEqual(preflight.status_code, 200)
                self.assertEqual(
                    preflight.headers.get("access-control-allow-origin"),
                    "https://app.example.com",
                )

                valid = client.get(
                    "/health/live", headers={"X-Request-ID": "trace-123._a"}
                )
                self.assertEqual(valid.headers.get("x-request-id"), "trace-123._a")

                invalid_value = "x" * 129
                replaced = client.get(
                    "/health/live", headers={"X-Request-ID": invalid_value}
                )
                self.assertNotEqual(replaced.headers.get("x-request-id"), invalid_value)
                self.assertRegex(
                    replaced.headers.get("x-request-id", ""),
                    r"^[0-9a-f]{32}$",
                )

    def test_openapi_description_declares_technique_and_evidence_surface(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = create_app(
                self._settings(root),
                JobDatabase(root / "service" / "jobs.sqlite3"),
            )
            with TestClient(app) as client:
                info = client.get("/openapi.json").json()["info"]
        self.assertIn("24-technique", info["description"])
        self.assertIn("trajectory", info["description"])
        self.assertIn("evidence-gated", info["description"])

    def test_registry_cache_refreshes_on_content_change(self) -> None:
        source = Path("src/rallymate_scoring/data/technique_metrics.json").resolve()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "technique_metrics.json"
            target.write_bytes(source.read_bytes())
            first = technique_catalog(target)
            payload = json.loads(target.read_text(encoding="utf-8"))
            payload["registry_version"] = "2026-09-12.2"
            target.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            second = technique_catalog(target)
            self.assertNotEqual(first["registry_sha256"], second["registry_sha256"])
            self.assertEqual(second["registry_version"], "2026-09-12.2")
            self.assertEqual(
                second["registry_sha256"],
                hashlib.sha256(target.read_bytes()).hexdigest(),
            )

    def test_technique_registry_requires_source_document_hashes(self) -> None:
        source = Path("src/rallymate_scoring/data/technique_metrics.json").resolve()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "technique_metrics.json"
            payload = json.loads(source.read_text(encoding="utf-8"))
            payload["source_documents"][0].pop("source_sha256", None)
            target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "source_sha256"):
                technique_catalog(target)

    def test_calibration_assets_cannot_live_in_job_writable_roots(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for writable in ("uploads", "requests", "runs"):
                settings = self._settings(
                    root,
                    scoring_calibration_assets=(
                        root / "service" / writable / "asset.json",
                    ),
                    scoring_trusted_promotion_ledger=root / "ledger.json",
                    scoring_trusted_runtime_profile_bindings=root / "bindings.json",
                    scoring_runtime_view_evidence_dir=root / "evidence",
                )
                with self.subTest(writable=writable), self.assertRaisesRegex(
                    ServiceConfigError, "job-writable"
                ):
                    settings.validate_license(check_registry_authority=False)


if __name__ == "__main__":
    unittest.main()
