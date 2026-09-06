from __future__ import annotations

import hashlib
import http.client
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from jsonschema import Draft202012Validator, FormatChecker

import rallymate_annotation.scoring_truth_event_handoff as handoff_module
from rallymate_annotation.scoring_truth_event_handoff import (
    BUNDLE_STATUS,
    BUNDLE_VERSION,
    MANIFEST_NAME,
    PUBLIC_ARTIFACT_PATHS,
    ScoringTruthEventHandoffError,
    build_scoring_truth_event_handoff,
    validate_scoring_truth_event_handoff,
)
import scripts.serve_scoring_truth_event_handoff as server_module


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PACK = ROOT / "data" / "annotations" / "scoring-truth-pack-v1"
VIDEO_DIR = ROOT / "FULL-TEST"
SCHEMA_PATH = ROOT / "contracts" / "scoring-truth-event-handoff.schema.json"
BUILD_CLI = ROOT / "scripts" / "build_scoring_truth_event_handoff.py"
SERVER_NAME = "serve_scoring_truth_event_handoff.py"
FOURTH_VIDEO = VIDEO_DIR / "c235227fffcd3290b60572d0c3f9cc85.mp4"


def _tree(root: Path) -> set[str]:
    return {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file()
    }


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _content_root(artifacts: list[dict]) -> str:
    return hashlib.sha256(_canonical_bytes({"artifacts": artifacts})).hexdigest().upper()


def _write_manifest(path: Path, manifest: dict) -> None:
    raw = (json.dumps(manifest, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode(
        "utf-8"
    )
    if path.exists():
        path.unlink()
    path.write_bytes(raw)


def _replace_file(path: Path, raw: bytes) -> None:
    path.unlink()
    path.write_bytes(raw)


def _read_response(
    port: int,
    method: str,
    target: str,
    *,
    host: str | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    connection.putrequest(method, target, skip_host=True)
    connection.putheader("Host", host or f"127.0.0.1:{port}")
    for key, value in (headers or {}).items():
        connection.putheader(key, value)
    connection.endheaders()
    response = connection.getresponse()
    body = response.read()
    result = response.status, {key.lower(): value for key, value in response.getheaders()}, body
    connection.close()
    return result


class ScoringTruthEventHandoffTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls._temp.name)
        cls.bundle = cls.root / "event-handoff"
        cls.manifest = build_scoring_truth_event_handoff(
            SOURCE_PACK,
            cls.bundle,
            video_dir=VIDEO_DIR,
        )
        cls.schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls) -> None:
        cls._temp.cleanup()

    def _copy(self, name: str) -> Path:
        target = self.root / name
        shutil.copytree(self.bundle, target, copy_function=os.link)
        return target

    def _coordinated_rehash(self, target: Path, relative: str, raw: bytes) -> None:
        path = target / Path(*relative.split("/"))
        _replace_file(path, raw)
        manifest_path = target / MANIFEST_NAME
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        record = next(item for item in manifest["artifacts"] if item["path"] == relative)
        record["bytes"] = len(raw)
        record["sha256"] = hashlib.sha256(raw).hexdigest().upper()
        manifest["content_root_sha256"] = _content_root(manifest["artifacts"])
        _write_manifest(manifest_path, manifest)

    def test_schema_runtime_source_replay_and_moved_standalone_validation(self) -> None:
        Draft202012Validator.check_schema(self.schema)
        Draft202012Validator(
            self.schema, format_checker=FormatChecker()
        ).validate(self.manifest)
        moved = self.root / "moved" / "renamed-event-handoff"
        moved.parent.mkdir()
        shutil.copytree(self.bundle, moved, copy_function=os.link)
        snapshot = validate_scoring_truth_event_handoff(
            moved,
            source_pack_dir=SOURCE_PACK,
            video_dir=VIDEO_DIR,
        )
        self.assertTrue(snapshot["source_replayed"])
        self.assertEqual(self.manifest["bundle_id"], snapshot["bundle_id"])
        self.assertEqual(self.manifest["content_root_sha256"], snapshot["content_root_sha256"])
        completed = subprocess.run(
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
        self.assertEqual(0, completed.returncode, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertTrue(result["ok"])
        self.assertEqual(self.manifest["bundle_id"], result["bundle_id"])

    def test_server_loader_executes_the_exact_verified_snapshot_bytes(self) -> None:
        source_path = ROOT / "scripts" / SERVER_NAME
        raw = source_path.read_bytes()
        copied = self.root / "loader-snapshot.py"
        copied.write_bytes(raw)
        original_snapshot = handoff_module._snapshot_file

        def snapshot_then_replace(path: Path, *, name: str) -> bytes:
            captured = original_snapshot(path, name=name)
            if path == copied:
                path.write_bytes(b"raise RuntimeError('path was reopened')\n")
            return captured

        with patch.object(
            handoff_module, "_snapshot_file", side_effect=snapshot_then_replace
        ):
            loaded = handoff_module._load_server_module(
                copied,
                expected_sha=hashlib.sha256(raw).hexdigest().upper(),
            )
        self.assertEqual(BUNDLE_VERSION, loaded.BUNDLE_VERSION)
        self.assertEqual(BUNDLE_STATUS, loaded.BUNDLE_STATUS)

    def test_exact_three_full_video_bytes_and_no_fourth_video(self) -> None:
        expected = {
            f"media/{item['video_id']}.mp4": item["sha256"]
            for item in self.manifest["scope"]["tasks"]
        }
        media = sorted((self.bundle / "media").iterdir())
        self.assertEqual(3, len(media))
        self.assertEqual(set(expected), {path.relative_to(self.bundle).as_posix() for path in media})
        for relative, digest in expected.items():
            public_raw = (self.bundle / Path(*relative.split("/"))).read_bytes()
            source_raw = (VIDEO_DIR / Path(relative).name).read_bytes()
            self.assertEqual(source_raw, public_raw, relative)
            self.assertEqual(digest, hashlib.sha256(public_raw).hexdigest().upper())
        self.assertTrue(FOURTH_VIDEO.is_file())
        self.assertNotIn(FOURTH_VIDEO.name, "\n".join(_tree(self.bundle)))

    def test_recursive_public_byte_scan_excludes_source_only_records_and_commitments(self) -> None:
        source = json.loads((SOURCE_PACK / "manifest.json").read_text(encoding="utf-8"))
        all_public = b"\n".join(
            path.read_bytes() for path in sorted(self.bundle.rglob("*")) if path.is_file()
        ).lower()
        for word in (b"candidate", b"pilot", b"model", b"full-test", b"private"):
            self.assertNotIn(word, all_public)
        self.assertNotIn(
            hashlib.sha256((SOURCE_PACK / "manifest.json").read_bytes()).hexdigest().encode(),
            all_public,
        )
        for video in source["videos"]:
            self.assertNotIn(video["path"].encode("utf-8").lower(), all_public)
        for row in source["pilot_keypoint_events"]:
            for key in (
                "blind_clip_id",
                "candidate_event_id",
                "candidate_source",
                "candidate_start_ms",
                "candidate_end_ms",
            ):
                self.assertNotIn(str(row[key]).encode("utf-8").lower(), all_public, key)
            for flag in row["quality_flags"]:
                self.assertNotIn(flag.encode("utf-8").lower(), all_public)
        binding = source["scoring_source_binding"]
        for value in (
            binding["scope"] if "scope" in binding else None,
            binding["pose_profile"],
            binding["registry"]["path"],
            binding["bundle"]["directory"],
            *binding["versions"].values(),
            *binding["bundle"].values(),
        ):
            if isinstance(value, str):
                self.assertNotIn(value.encode("utf-8").lower(), all_public)

    def test_manifest_is_fail_closed_and_has_no_authorized_transition(self) -> None:
        self.assertEqual(BUNDLE_VERSION, self.manifest["bundle_version"])
        self.assertEqual(BUNDLE_STATUS, self.manifest["status"])
        safety = self.manifest["safety"]
        self.assertFalse(safety["annotation_execution_authorized"])
        self.assertFalse(safety["external_protocol_receipt_verified"])
        self.assertFalse(safety["mutation_enabled"])
        self.assertFalse(safety["import_enabled"])
        self.assertFalse(safety["export_enabled"])
        self.assertFalse(safety["production_enabled"])
        self.assertFalse(safety["maturity_promoted"])
        self.assertNotIn("authorized_handoff", json.dumps(self.manifest))
        readme = (self.bundle / "OPERATOR_README.md").read_text(encoding="utf-8")
        self.assertTrue(readme.startswith("# HARD STOP — technical handoff only\n"))
        self.assertIn("independent external protocol receipt", readme)
        self.assertIn("Do not edit or unlock this technical bundle", readme)
        self.assertIn("cannot collect truth", readme)

    def test_workbench_is_playback_seek_and_frame_only(self) -> None:
        html = (self.bundle / "review.html").read_text(encoding="utf-8")
        js = (self.bundle / "scoring-truth-event-workbench.js").read_text(encoding="utf-8")
        start = html.index(server_module.BOOTSTRAP_OPEN) + len(server_module.BOOTSTRAP_OPEN)
        end = html.index("</script>", start)
        bootstrap = json.loads(html[start:end].replace("<\\/", "</"))
        self.assertEqual(3, len(bootstrap["tasks"]))
        self.assertEqual(BUNDLE_STATUS, bootstrap["status"])
        for key in (
            "annotation_execution_authorized",
            "external_protocol_receipt_verified",
            "mutation_enabled",
            "import_enabled",
            "export_enabled",
        ):
            self.assertIs(False, bootstrap[key])
        for control in (
            'id="save-event" type="button" disabled data-mutation',
            'id="import-csv" type="file" disabled data-mutation',
            'id="export-events" type="button" disabled data-mutation',
            'id="mark-review-complete" type="button" disabled data-mutation',
        ):
            self.assertIn(control, html)
        self.assertIn('data-step-frames="-1"', html)
        self.assertIn('data-step-frames="1"', html)
        self.assertIn('id="seek-slider"', html)
        self.assertIn("video.currentTime", js)
        self.assertNotIn("localStorage", js)
        self.assertNotIn("FileReader", js)
        self.assertNotIn("createObjectURL", js)

    def test_every_artifact_single_byte_tamper_is_rejected(self) -> None:
        for index, relative in enumerate(PUBLIC_ARTIFACT_PATHS, start=1):
            with self.subTest(artifact=relative):
                target = self._copy(f"tamper-{index}")
                path = target / Path(*relative.split("/"))
                _replace_file(path, path.read_bytes() + b"x")
                with self.assertRaises(ScoringTruthEventHandoffError):
                    validate_scoring_truth_event_handoff(target)
                shutil.rmtree(target)

    def test_coordinated_rehash_cannot_replace_bound_content(self) -> None:
        changes = {
            "OPERATOR_README.md": lambda raw: raw + b"\n",
            "review.html": lambda raw: raw.replace(
                b"external_protocol_receipt_verified\":false",
                b"external_protocol_receipt_verified\":true ",
                1,
            ),
            "scoring-truth-event-workbench.js": lambda raw: raw + b"\n",
            "media/3ae77ee3271d67de171585a5c39ddd69.mp4": lambda raw: raw + b"x",
            SERVER_NAME: lambda raw: raw + b"\n",
        }
        for index, (relative, transform) in enumerate(changes.items(), start=1):
            with self.subTest(artifact=relative):
                target = self._copy(f"co-rehash-{index}")
                path = target / Path(*relative.split("/"))
                self._coordinated_rehash(target, relative, transform(path.read_bytes()))
                with self.assertRaises(ScoringTruthEventHandoffError):
                    validate_scoring_truth_event_handoff(target)
                shutil.rmtree(target)

    def test_extra_fourth_video_is_rejected_even_after_manifest_rehash(self) -> None:
        target = self._copy("extra-fourth")
        extra_path = target / "media" / FOURTH_VIDEO.name
        os.link(FOURTH_VIDEO, extra_path)
        manifest_path = target / MANIFEST_NAME
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        extra_raw = FOURTH_VIDEO.read_bytes()
        manifest["artifacts"].insert(
            4,
            {
                "path": f"media/{FOURTH_VIDEO.name}",
                "bytes": len(extra_raw),
                "sha256": hashlib.sha256(extra_raw).hexdigest().upper(),
            },
        )
        manifest["content_root_sha256"] = _content_root(manifest["artifacts"])
        _write_manifest(manifest_path, manifest)
        with self.assertRaisesRegex(
            ScoringTruthEventHandoffError, "artifact allowlist|file set mismatch"
        ):
            validate_scoring_truth_event_handoff(target)
        completed = subprocess.run(
            [
                sys.executable,
                str(self.bundle / SERVER_NAME),
                "--directory",
                str(target),
                "--validate-only",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(2, completed.returncode)
        self.assertRegex(completed.stderr, "artifact allowlist|file set mismatch")

    def test_build_requires_new_target_and_failure_leaves_no_final_or_staging(self) -> None:
        existing = self.root / "existing-target"
        existing.mkdir()
        marker = existing / "owner.txt"
        marker.write_text("keep", encoding="utf-8")
        with self.assertRaisesRegex(ScoringTruthEventHandoffError, "must not already exist"):
            build_scoring_truth_event_handoff(
                SOURCE_PACK, existing, video_dir=VIDEO_DIR
            )
        self.assertEqual("keep", marker.read_text(encoding="utf-8"))

        failed = self.root / "publish-fails"
        with patch.object(
            handoff_module,
            "validate_scoring_truth_event_handoff",
            side_effect=ScoringTruthEventHandoffError("injected validation failure"),
        ):
            with self.assertRaisesRegex(
                ScoringTruthEventHandoffError, "injected validation failure"
            ):
                build_scoring_truth_event_handoff(
                    SOURCE_PACK, failed, video_dir=VIDEO_DIR
                )
        self.assertFalse(failed.exists())
        self.assertEqual([], list(failed.parent.glob(f".{failed.name}.staging-*")))

    def test_cli_source_replay_and_nonloopback_bind_fail_closed(self) -> None:
        env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
        replay = subprocess.run(
            [
                sys.executable,
                str(BUILD_CLI),
                "--source-pack",
                str(SOURCE_PACK),
                "--video-directory",
                str(VIDEO_DIR),
                "--output",
                str(self.bundle),
                "--validate-only",
            ],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        self.assertEqual(0, replay.returncode, replay.stderr)
        self.assertTrue(json.loads(replay.stdout)["source_replayed"])
        rejected = subprocess.run(
            [
                sys.executable,
                str(self.bundle / SERVER_NAME),
                "--directory",
                str(self.bundle),
                "--bind",
                "0.0.0.0",
                "--port",
                "0",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(2, rejected.returncode)
        self.assertIn("127.0.0.1", rejected.stderr)
        _manifest, snapshots = server_module.validate_and_snapshot(self.bundle)
        handler = server_module.make_request_handler(snapshots)
        with self.assertRaisesRegex(
            server_module.BundleValidationError, "exact IPv4 loopback"
        ):
            server_module.SnapshotHTTPServer(("0.0.0.0", 0), handler)

    def test_snapshot_server_get_head_ranges_host_and_traversal(self) -> None:
        manifest, snapshots = server_module.validate_and_snapshot(self.bundle)
        handler = server_module.make_request_handler(snapshots)
        server = server_module.SnapshotHTTPServer(("127.0.0.1", 0), handler)
        port = int(server.server_address[1])
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            status, headers, body = _read_response(port, "GET", "/")
            self.assertEqual(200, status)
            self.assertEqual(snapshots["review.html"], body)
            self.assertEqual("snapshot-v1", headers["x-rallymate-event-handoff-server"])
            self.assertEqual("no-store", headers["cache-control"])

            media_path = manifest["scope"]["tasks"][0]["media_path"]
            status, headers, body = _read_response(port, "HEAD", f"/{media_path}")
            self.assertEqual(200, status)
            self.assertEqual(b"", body)
            self.assertEqual(len(snapshots[media_path]), int(headers["content-length"]))

            status, headers, body = _read_response(
                port, "GET", f"/{media_path}", headers={"Range": "bytes=10-19"}
            )
            self.assertEqual(206, status)
            self.assertEqual(snapshots[media_path][10:20], body)
            self.assertEqual(f"bytes 10-19/{len(snapshots[media_path])}", headers["content-range"])

            status, headers, _body = _read_response(
                port,
                "GET",
                f"/{media_path}",
                headers={"Range": f"bytes={len(snapshots[media_path])}-"},
            )
            self.assertEqual(416, status)
            self.assertEqual(f"bytes */{len(snapshots[media_path])}", headers["content-range"])

            status, _headers, _body = _read_response(
                port, "GET", "/", host="localhost:8765"
            )
            self.assertEqual(421, status)
            for target in (
                "/../event-handoff-manifest.json",
                "/%2e%2e/event-handoff-manifest.json",
                "/%252e%252e/event-handoff-manifest.json",
                "//example.invalid/review.html",
                "/C:%5cWindows%5cwin.ini",
            ):
                with self.subTest(target=target):
                    status, _headers, _body = _read_response(port, "GET", target)
                    self.assertEqual(404, status)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_server_uses_validated_snapshot_after_disk_change(self) -> None:
        target = self._copy("snapshot-freeze")
        _manifest, snapshots = server_module.validate_and_snapshot(target)
        original = snapshots["review.html"]
        _replace_file(target / "review.html", b"changed after validation")
        handler = server_module.make_request_handler(snapshots)
        server = server_module.SnapshotHTTPServer(("127.0.0.1", 0), handler)
        port = int(server.server_address[1])
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            status, _headers, body = _read_response(port, "GET", "/")
            self.assertEqual(200, status)
            self.assertEqual(original, body)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
