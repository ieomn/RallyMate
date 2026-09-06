from __future__ import annotations

import http.client
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.range_http_server import _resolve_request_target


ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "scripts" / "range_http_server.py"
POSE_LAUNCHER = ROOT / "scripts" / "serve_pose_scoring_ab.ps1"
DOCS_LAUNCHER = ROOT / "scripts" / "serve_current_docs.ps1"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _request(port: int, path: str, *, host: str, byte_range: str | None = None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
    connection.putrequest("GET", path, skip_host=True)
    connection.putheader("Host", host)
    if byte_range is not None:
        connection.putheader("Range", byte_range)
    connection.endheaders()
    response = connection.getresponse()
    body = response.read()
    headers = dict(response.getheaders())
    status = response.status
    connection.close()
    return status, headers, body


class RangeHTTPServerPrivacyTests(unittest.TestCase):
    def _start_server(self, root: Path, *allowed: str):
        port = _free_port()
        command = [
            sys.executable,
            str(SERVER),
            "--directory",
            str(root),
            "--bind",
            "127.0.0.1",
            "--port",
            str(port),
        ]
        for value in allowed:
            command.extend(("--allow", value))
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        return port, process

    def test_loopback_host_allowlist_ranges_and_private_path_denials(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            public = root / "public"
            public.mkdir()
            (public / "public.bin").write_bytes(b"0123456789")
            (root / "unlisted.txt").write_text("not public", encoding="utf-8")
            private = root / "data" / "annotations" / "fs09-phase-truth-m77-v1"
            private.mkdir(parents=True)
            (private / "sealed-event-candidates.jsonl").write_text(
                "private", encoding="utf-8"
            )
            (root / ".hidden.txt").write_text("hidden", encoding="utf-8")
            (root / "sealed-candidates.json").write_text("private", encoding="utf-8")
            port = _free_port()
            process = subprocess.Popen(
                [
                    sys.executable,
                    str(SERVER),
                    "--directory",
                    str(root),
                    "--bind",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--allow",
                    "public",
                ],
                cwd=ROOT,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                deadline = time.monotonic() + 5
                while True:
                    try:
                        status, _headers, body = _request(
                            port,
                            "/public/public.bin",
                            host=f"127.0.0.1:{port}",
                        )
                        break
                    except OSError:
                        if time.monotonic() >= deadline:
                            self.fail("range server did not become ready")
                        time.sleep(0.05)
                self.assertEqual(200, status)
                self.assertEqual(b"0123456789", body)

                status, headers, body = _request(
                    port,
                    "/public/public.bin",
                    host=f"localhost:{port}",
                    byte_range="bytes=2-5",
                )
                self.assertEqual(206, status)
                self.assertEqual("bytes 2-5/10", headers["Content-Range"])
                self.assertEqual(
                    "allowlist-v1", headers["X-RallyMate-Range-Server"]
                )
                self.assertEqual(b"2345", body)

                for path in (
                    "/",
                    "/.hidden.txt",
                    "/unlisted.txt",
                    "/sealed-candidates.json",
                    "/data/annotations/fs09-phase-truth-m77-v1/sealed-event-candidates.jsonl",
                    "/data/annotations/fs09-phase-truth-m77-v1/%73ealed-event-candidates.jsonl",
                    "/data/annotations/fs09%252dphase%252dtruth%252dm77%252dv1/sealed%252devent%252dcandidates.jsonl",
                    "/raw-exports/annotation.csv",
                ):
                    with self.subTest(path=path):
                        status, _headers, _body = _request(
                            port, path, host=f"127.0.0.1:{port}"
                        )
                        self.assertEqual(404, status)

                status, _headers, _body = _request(
                    port, "/public/public.bin", host=f"attacker.invalid:{port}"
                )
                self.assertEqual(403, status)
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                if process.stderr is not None:
                    process.stderr.close()

    def test_non_loopback_bind_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            for bind in ("0.0.0.0", "::1"):
                with self.subTest(bind=bind):
                    completed = subprocess.run(
                        [
                            sys.executable,
                            str(SERVER),
                            "--directory",
                            temporary,
                            "--bind",
                            bind,
                            "--port",
                            "8765",
                            "--allow",
                            ".",
                        ],
                        cwd=ROOT,
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    self.assertEqual(2, completed.returncode)
                    self.assertIn("--bind must be IPv4 loopback", completed.stderr)

    def test_request_path_is_percent_decoded_exactly_once(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "public-name.txt").write_text("public", encoding="utf-8")
            port, process = self._start_server(root, ".")
            try:
                deadline = time.monotonic() + 5
                while True:
                    try:
                        status, _headers, body = _request(
                            port,
                            "/public%2dname.txt",
                            host=f"127.0.0.1:{port}",
                        )
                        break
                    except OSError:
                        if time.monotonic() >= deadline:
                            self.fail("range server did not become ready")
                        time.sleep(0.05)
                self.assertEqual(200, status)
                self.assertEqual(b"public", body)
                for encoded_path in (
                    "/public%252dname.txt",
                    "/public%name.txt",
                    "/%252e%252e/public-name.txt",
                ):
                    with self.subTest(path=encoded_path):
                        status, _headers, _body = _request(
                            port, encoded_path, host=f"localhost:{port}"
                        )
                        self.assertEqual(404, status)
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                if process.stderr is not None:
                    process.stderr.close()

    def test_symlink_or_reparse_component_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            public = root / "public"
            public.mkdir()
            link = public / "escape.txt"
            link.write_text("placeholder", encoding="utf-8")
            with patch(
                "scripts.range_http_server._is_reparse_point",
                side_effect=lambda path: Path(path) == link,
            ):
                resolved = _resolve_request_target(
                    root.resolve(),
                    "/public/escape.txt",
                    ((public.resolve(), True),),
                )
            self.assertIsNone(resolved)

    def test_pose_scoring_launcher_root_cannot_serve_other_reports(self) -> None:
        report_root = ROOT / "reports" / "pose-scoring-ab"
        port, process = self._start_server(report_root, ".")
        try:
            deadline = time.monotonic() + 5
            while True:
                try:
                    status, _headers, _body = _request(
                        port, "/index.html", host=f"127.0.0.1:{port}"
                    )
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        self.fail("range server did not become ready")
                    time.sleep(0.05)
            self.assertEqual(200, status)
            for private_path in (
                "/measurement-recovery-m74/event-bounded-gap-audit-v1/report.json",
                "/measurement-recovery-m77/review-clips-v1/manifest.json",
                "/data/annotations/fs09-phase-truth-m77-v1/sealed-event-candidates.jsonl",
            ):
                with self.subTest(path=private_path):
                    status, _headers, _body = _request(
                        port, private_path, host=f"localhost:{port}"
                    )
                    self.assertEqual(404, status)
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            if process.stderr is not None:
                process.stderr.close()

    def test_pose_scoring_launcher_refuses_prebound_port(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            port = int(listener.getsockname()[1])
            completed = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(POSE_LAUNCHER),
                    "-Port",
                    str(port),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
                timeout=15,
            )
        self.assertNotEqual(0, completed.returncode)
        self.assertIn("already in use", completed.stderr)

    def test_current_docs_launcher_is_scoped_and_refuses_prebound_port(self) -> None:
        script = DOCS_LAUNCHER.read_text(encoding="utf-8")
        self.assertIn(
            '$RelativeDocsRoot = "reports/rallymate-current-docs"', script
        )
        self.assertIn('"--allow",', script)
        self.assertIn("$RelativeDocsRoot", script)
        self.assertNotIn('"--allow",\n    "."', script)

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            port = int(listener.getsockname()[1])
            completed = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(DOCS_LAUNCHER),
                    "-Port",
                    str(port),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
                timeout=15,
            )
        self.assertNotEqual(0, completed.returncode)
        self.assertIn("already in use", completed.stderr)


if __name__ == "__main__":
    unittest.main()
