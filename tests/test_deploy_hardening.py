"""Static deployment hardening checks that do not need Docker."""
from __future__ import annotations

from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy"


def _service_block(compose: str, name: str) -> str:
    match = re.search(rf"^  {name}:\n(.*?)(?=^  [a-z][\w-]*:\n|^\S)", compose, re.MULTILINE | re.DOTALL)
    if not match:
        raise AssertionError(f"service {name} not found")
    return match.group(1)


def _location_block(source: str, header: str) -> str:
    start = source.index(header)
    depth, index = 0, source.index("{", start)
    for position in range(index, len(source)):
        depth += {"{": 1, "}": -1}.get(source[position], 0)
        if depth == 0:
            return source[start:position + 1]
    raise AssertionError(f"unterminated block {header}")


class NginxHardeningTests(unittest.TestCase):
    source = (DEPLOY / "nginx-rallymate.conf.example").read_text(encoding="utf-8")

    def test_version_hidden_and_security_headers_sent_once_from_server_level(self):
        self.assertIn("server_tokens off;", self.source)
        for header in ('Strict-Transport-Security "max-age=31536000; includeSubDomains" always;',
                       'X-Content-Type-Options "nosniff" always;',
                       'X-Frame-Options "DENY" always;',
                       'Referrer-Policy "strict-origin-when-cross-origin" always;'):
            self.assertIn("add_header " + header, self.source)
        for name in ("Strict-Transport-Security", "X-Content-Type-Options", "X-Frame-Options", "Referrer-Policy"):
            self.assertIn(f"proxy_hide_header {name};", self.source)
        # nginx drops inherited add_header/proxy_hide_header as soon as a
        # location defines its own, so locations must define none.
        for match in re.finditer(r"^\s*location\b[^{]*\{", self.source, re.MULTILINE):
            block = _location_block(self.source, match.group(0).strip())
            self.assertNotIn("add_header", block, match.group(0))
            self.assertNotIn("proxy_hide_header", block, match.group(0))

    def test_body_size_is_small_by_default_and_large_only_for_upload_routes(self):
        server = _location_block(self.source, "server {\n    listen 443 ssl;")
        self.assertRegex(server, r"\n    client_max_body_size 1m;\n")
        jobs = _location_block(self.source, "location = /v1/jobs {")
        self.assertIn("client_max_body_size 2G;", jobs)
        self.assertIn("proxy_pass http://rallymate_api;", jobs)
        api = _location_block(self.source, "location ^~ /v1/ {")
        chunks = _location_block(api, "location ~ ^/v1/uploads/[^/]+/chunks/[0-9]+$ {")
        self.assertIn("client_max_body_size 5m;", chunks)
        self.assertIn("proxy_pass http://rallymate_api;", chunks)
        self.assertEqual(2, self.source.count("client_max_body_size 2G;") + self.source.count("client_max_body_size 5m;"))
        chunk_route = re.compile(r"^/v1/uploads/[^/]+/chunks/[0-9]+$")
        self.assertTrue(chunk_route.match("/v1/uploads/abc123/chunks/7"))
        # DELETE /v1/uploads/{id} and other session routes use the small default via ^~ /v1/.
        for path in ("/v1/uploads/abc123", "/v1/uploads/abc123/complete", "/v1/uploads"):
            self.assertFalse(chunk_route.match(path), path)

    def test_hsts_is_not_sent_on_the_plain_http_redirect_server(self):
        plain = _location_block(self.source, "server {\n    listen 80;")
        self.assertNotIn("add_header", plain)


class ComposeHardeningTests(unittest.TestCase):
    compose = (DEPLOY / "docker-compose.yml").read_text(encoding="utf-8")

    def test_adminer_is_opt_in_and_has_no_egress(self):
        adminer = _service_block(self.compose, "adminer")
        self.assertIn("profiles: [tools]", adminer)
        self.assertIn("networks: [backend]\n", adminer)
        self.assertNotIn("egress", adminer)
        self.assertIn('ports: ["127.0.0.1:8080:8080"]', adminer)

    def test_api_multipart_spool_is_disk_backed_not_the_small_tmpfs(self):
        api = _service_block(self.compose, "api")
        self.assertIn("TMPDIR: /app/upload-tmp", api)
        self.assertIn("- api_upload_tmp:/app/upload-tmp", api)
        self.assertRegex(self.compose, r"\nvolumes:\n(?:  .*\n)*  api_upload_tmp: \{\}")
        dockerfile = (DEPLOY / "Dockerfile.api").read_text(encoding="utf-8")
        self.assertRegex(dockerfile, r"mkdir -p [^\n]*/app/upload-tmp")
        self.assertRegex(dockerfile, r"chown -R rallymate:rallymate [^\n]*/app/upload-tmp")


class DockerfileLayerTests(unittest.TestCase):
    def assert_dependencies_before_source(self, name: str):
        source = (DEPLOY / name).read_text(encoding="utf-8")
        source_copy = source.index("COPY src /app/src")
        self.assertLess(source.index("torch==2.11.0"), source_copy)
        self.assertLess(source.index('project["optional-dependencies"]'), source_copy)
        self.assertLess(source.index("COPY pyproject.toml /app/"), source_copy)
        self.assertRegex(source[source_copy:], r"pip install [^\n]*--no-deps --no-build-isolation -e \.")
        return source

    def test_gpu_image_installs_heavy_dependencies_before_copying_source(self):
        source = self.assert_dependencies_before_source("Dockerfile")
        for extra in ("service", "training", "rtmpose"):
            self.assertIn(f'"{extra}"', source)
        self.assertIn("/var/lib/rallymate/gpu-state", source)

    def test_api_image_installs_heavy_dependencies_before_copying_source(self):
        self.assert_dependencies_before_source("Dockerfile.api")


class AutodlHygieneTests(unittest.TestCase):
    def test_linux_consumed_files_are_lf(self):
        names = ["autodl/runtime.sam3d.env.example", "autodl/runtime.env.example", "autodl/requirements-sam-core.in",
                 "autodl/requirements-constraints.txt", ".env.example", "Dockerfile", "Dockerfile.api",
                 "nginx-rallymate.conf.example", "docker-compose.yml"]
        names += [path.relative_to(DEPLOY).as_posix() for path in (DEPLOY / "autodl").glob("*.sh")]
        for name in names:
            with self.subTest(name=name):
                self.assertNotIn(b"\r\n", (DEPLOY / name).read_bytes())

    def test_private_ssh_endpoint_is_not_committed(self):
        source = (DEPLOY / "autodl/check_remote.py").read_text(encoding="utf-8")
        self.assertIn('"AUTODL_SSH_HOST"', source)
        self.assertIn('"AUTODL_SSH_PORT"', source)
        self.assertNotRegex(source, r"seetacloud|\bHOST\s*=\s*\"")
        for path in list((DEPLOY / "autodl").glob("*.md")) + [DEPLOY / "README.md"]:
            with self.subTest(path=path.name):
                self.assertNotIn("seetacloud", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
