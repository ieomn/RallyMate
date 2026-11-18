"""Validate the Linux deployment boundary using Compose's actual config parser."""
from __future__ import annotations

import json
import ipaddress
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("docker"), "Docker Compose is required")
class ComposeDeploymentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        directory = Path(cls.temporary.name)
        empty_env = directory / "empty.env"
        empty_env.write_text("", encoding="utf-8")
        token = directory / "metrics_token"
        token.write_text("synthetic-test-metrics-token-0123456789", encoding="utf-8")
        cls.env = {name: value for name, value in os.environ.items() if not name.startswith(
            ("RALLYMATE_", "POSTGRES_", "SENTRY_", "VITE_SENTRY_", "MIMO_", "ADMIN_", "METRICS_"))
            and name not in {"DATABASE_URL", "JWT_SECRET", "PUBLIC_HOST"}}
        cls.env.update(DATABASE_URL="postgresql+psycopg://test:test@postgres:5432/test",
                       POSTGRES_PASSWORD="synthetic-database-test-password", JWT_SECRET="synthetic-jwt-test-0123456789-ABCDEF",
                       RALLYMATE_API_KEY="synthetic-maintenance-test-0123456789", PUBLIC_HOST="app.example.com",
                       RALLYMATE_TLS_DIR=directory.as_posix(), METRICS_TOKEN_FILE=token.as_posix(),
                       RALLYMATE_MODEL_LICENSE_ACK="development", RALLYMATE_ENVIRONMENT="development")
        cls.command = ["docker", "compose", "--profile", "*", "--project-name", "rallymate-config-test", "--env-file", str(empty_env),
                       "-f", str(ROOT / "deploy/docker-compose.yml")]
        cls.run_config("--quiet")
        # Only synthetic test values are rendered, captured in memory and never printed.
        cls.config = json.loads(cls.run_config("--format", "json"))

    @classmethod
    def run_config(cls, *arguments):
        result = subprocess.run([*cls.command, "config", *arguments], env=cls.env, capture_output=True, text=True)
        if result.returncode:
            raise AssertionError("Docker Compose rejected the synthetic test configuration")
        return result.stdout

    def test_only_nginx_and_loopback_adminer_publish_ports(self):
        services = self.config["services"]
        self.assertEqual({"nginx", "adminer"}, {name for name, value in services.items() if value.get("ports")})
        port = services["adminer"]["ports"][0]
        self.assertEqual(("127.0.0.1", "8080", 8080), (port["host_ip"], port["published"], port["target"]))
        self.assertEqual({"backend", "egress"}, set(services["adminer"]["networks"]))
        self.assertEqual({80, 443}, {port["target"] for port in services["nginx"]["ports"]})

    def test_migrations_remain_explicit_and_worker_has_no_account_secrets(self):
        services = self.config["services"]
        self.assertEqual(["tools"], services["migrate"]["profiles"])
        self.assertEqual(["rallymate-migrate"], services["migrate"]["command"])
        for name in ("api", "worker"):
            self.assertNotIn("migrate", services[name].get("depends_on", {}))
        self.assertNotIn("build", services["worker"])
        self.assertEqual(1, services["worker"]["deploy"]["replicas"])
        for name in ("JWT_SECRET", "ADMIN_EMAIL", "ADMIN_PASSWORD", "RALLYMATE_API_KEY"):
            self.assertNotIn(name, services["worker"]["environment"])
        self.assertEqual("/run/secrets/metrics_token", services["api"]["environment"]["METRICS_TOKEN_FILE"])

    def test_model_mounts_gpu_ownership_and_proxy_identity_are_preserved(self):
        services = self.config["services"]
        for name in ("api", "worker"):
            mounts = {item["target"]: item for item in services[name]["volumes"]}
            self.assertTrue(mounts["/app/models"]["read_only"])
            self.assertTrue(mounts["/app/calibration"]["read_only"])
            self.assertTrue(mounts["/app/runtime/sam3d"]["read_only"])
        self.assertIn("/var/lib/rallymate/gpu-state", {item["target"] for item in services["worker"]["volumes"]})
        nginx_ip = services["nginx"]["networks"]["backend"]["ipv4_address"]
        network = self.config["networks"]["backend"]["ipam"]["config"][0]
        subnet = ipaddress.ip_network(network["subnet"])
        dynamic_pool = ipaddress.ip_network(network["ip_range"])
        self.assertTrue(dynamic_pool.subnet_of(subnet))
        self.assertIn(ipaddress.ip_address(nginx_ip), subnet)
        self.assertNotIn(ipaddress.ip_address(nginx_ip), dynamic_pool)
        self.assertEqual(nginx_ip, services["api"]["environment"]["FORWARDED_ALLOW_IPS"])
        self.assertEqual({"backend"}, services["api"]["networks"].keys() & services["nginx"]["networks"].keys())
        self.assertEqual("http://api:8000", services["frontend"]["environment"]["RALLYMATE_INTERNAL_API_ORIGIN"])
        self.assertNotIn("JWT_SECRET", services["frontend"]["environment"])

    def test_missing_signing_secret_fails_configuration(self):
        result = subprocess.run([*self.command, "config", "--quiet"], env={**self.env, "JWT_SECRET": ""}, capture_output=True)
        self.assertNotEqual(0, result.returncode)

    def test_frontend_build_uses_the_original_shared_data_sources(self):
        contexts = self.config["services"]["frontend"]["build"]["additional_contexts"]
        for context, relative in (("scoring_assets", "src/rallymate_scoring/data"),
                                  ("pose_assets", "src/rallymate_vision/pose/data")):
            self.assertEqual((ROOT / relative).resolve(), Path(contexts[context]).resolve())
        dockerfile = (ROOT / "scoring-demo-web/Dockerfile").read_text(encoding="utf-8")
        self.assertIn("COPY --from=scoring_assets level_grading_v1.json relative_reference_policy.json", dockerfile)
        self.assertIn("COPY --from=pose_assets mhr70_display.json", dockerfile)

    def test_worker_cli_import_does_not_create_an_account_application(self):
        environment = {**self.env, "PYTHONPATH": str(ROOT / "src"), "JWT_SECRET": "",
                       "RALLYMATE_DATA_ROOT": str(Path(self.temporary.name) / "unused-data")}
        result = subprocess.run([sys.executable, "-c", "import sys; import rallymate_service.cli; "
                                 "assert 'rallymate_service.api' not in sys.modules"],
                                env=environment, capture_output=True, text=True)
        self.assertEqual(0, result.returncode, "Worker CLI import unexpectedly requires an account application")
        self.assertFalse(Path(environment["RALLYMATE_DATA_ROOT"]).exists())

    def test_nginx_overwrites_untrusted_identity_without_replacing_account_auth(self):
        source = (ROOT / "deploy/nginx-rallymate.conf.example").read_text(encoding="utf-8")
        for name in ("X-Forwarded-For", "X-Real-IP", "CF-Connecting-IP"):
            self.assertIn(f"proxy_set_header {name} $remote_addr;", source)
        self.assertIn("proxy_set_header Authorization $http_authorization;", source)
        self.assertIn("proxy_set_header Cookie $http_cookie;", source)
        self.assertIn("proxy_set_header Host $http_host;", source)
        self.assertIn("proxy_set_header X-Forwarded-Host $http_host;", source)
        self.assertIn("if ($host != '${PUBLIC_HOST}')", source)
        self.assertIn("client_max_body_size 2G;", source)
        self.assertIn("proxy_request_buffering off;", source)
        self.assertIn("proxy_read_timeout 3600s;", source)
        self.assertIn("^/(?:internal|metrics)(?:/|$)", source)
        self.assertNotIn("$proxy_add_x_forwarded_for", source)
        self.assertNotIn("rallymate_api_authorization", source)


if __name__ == "__main__":
    unittest.main()
