import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

from rallymate_scoring.source_assessment import ARTIFACT_NAME
from rallymate_service.source_assessment import load_source_aligned_assessment
from tests.test_source_assessment import fixture


class ReportDeliverySourceCacheTests(unittest.TestCase):
    def test_inner_source_cache_rejects_same_length_rewrites_with_restored_mtime(self):
        for changed in (ARTIFACT_NAME, "events.jsonl", "primary-player.jsonl", "frames.jsonl"):
            with self.subTest(dependency=changed), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                artifact, events = fixture()
                data = {"events.jsonl": ("".join(json.dumps(e) + "\n" for e in events)).encode(),
                        "primary-player.jsonl": b'{"player":1}\n', "frames.jsonl": b'{"frame":1}\n'}
                artifact.update(events_sha256=hashlib.sha256(data["events.jsonl"]).hexdigest(),
                    primary_timeline_sha256=hashlib.sha256(data["primary-player.jsonl"]).hexdigest(),
                    frames_sha256=hashlib.sha256(data["frames.jsonl"]).hexdigest())
                data[ARTIFACT_NAME] = json.dumps(artifact).encode()
                for name, raw in data.items(): (root / name).write_bytes(raw)
                args = dict(video_id="video", duration_ms=100, video_sha256="a" * 64,
                            frames_sha256=artifact["frames_sha256"])
                first = load_source_aligned_assessment(root, **args)
                self.assertEqual(first["status"], "available")
                path = root / changed; stat = path.stat()
                before = data[changed]
                after = (before.replace(b'"value": 0.12', b'"value": 0.21') if changed == ARTIFACT_NAME else
                         before.replace(b'event-0', b'event-1') if changed == "events.jsonl" else
                         before.replace(b':1', b':2'))
                self.assertNotEqual(before, after)
                self.assertEqual(len(before), len(after))
                with path.open("r+b") as stream: stream.write(after)
                os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
                second = load_source_aligned_assessment(root, **args)
                if changed == ARTIFACT_NAME:
                    row = second["indicators"][0]["windows"][0]["measurements"][0]
                    self.assertEqual(row["value"], .21)
                else:
                    self.assertEqual(second["status"], "unavailable")
