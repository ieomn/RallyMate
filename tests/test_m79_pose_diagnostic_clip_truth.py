from __future__ import annotations

import copy
import csv
import importlib.util
import json
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

from rallymate_evaluation.pose_diagnostic_clip_truth import (
    ADJUDICATION_HEADERS,
    COVERAGE_HEADERS,
    POSITIVE_HEADERS,
    PoseDiagnosticClipTruthError,
    canonical_sha256,
    compile_clip_truth,
    file_sha256,
    validate_clip_truth_manifest_sources,
    validate_empty_clip_truth_report,
)


ROOT = Path(__file__).resolve().parents[1]
REAL_PACK = ROOT / "data" / "annotations" / "pose-diagnostic-clip-truth-m80-v1.2.2"


def _write_csv(path: Path, headers: tuple[str, ...], rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(headers), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _copy_contract_shell(destination: Path) -> tuple[dict, dict]:
    manifest = json.loads((REAL_PACK / "manifest.json").read_text(encoding="utf-8"))
    sealed = json.loads((REAL_PACK / "sealed-candidates.json").read_text(encoding="utf-8"))
    (destination / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return manifest, sealed


def _coverage_id(clip_id: str, diagnostic_type: str, annotator: str) -> str:
    return f"synthetic-cov:{clip_id}:{diagnostic_type}:{annotator}"


def _complete_synthetic_contract(pack: Path, manifest: dict, sealed: dict) -> None:
    coverage: list[dict] = []
    for video in manifest["plan"]["videos"]:
        for segment in video["segments"]:
            for diagnostic_type in manifest["plan"]["diagnostic_types"]:
                for annotator in ("synthetic-a", "synthetic-b"):
                    coverage.append(
                        {
                            "annotation_id": _coverage_id(
                                segment["clip_id"], diagnostic_type, annotator
                            ),
                            "clip_id": segment["clip_id"],
                            "video_id": video["video_id"],
                            "diagnostic_type": diagnostic_type,
                            "start_source_frame_index": segment[
                                "source_start_frame_index"
                            ],
                            "end_source_frame_index": segment[
                                "source_end_frame_index"
                            ],
                            "status": "completed",
                            "annotator_id": annotator,
                            "annotated_at": "2026-08-22T00:00:00Z",
                            "null_reason": "",
                            "notes": "synthetic_contract_only",
                        }
                    )

    first = sealed["tasks"][0]
    positives: list[dict] = []
    positive_ids: list[str] = []
    for annotator in ("synthetic-a", "synthetic-b"):
        positive_id = f"synthetic-positive:{annotator}:{first['task_id']}"
        positive_ids.append(positive_id)
        row = {
            "positive_id": positive_id,
            "coverage_annotation_id": _coverage_id(
                first["clip_id"], first["diagnostic_type"], annotator
            ),
            "video_id": first["video_id"],
            "diagnostic_type": first["diagnostic_type"],
            "source_frame_index": first["source_frame_index"],
            "joint": "",
            "left_joint": "",
            "right_joint": "",
            "notes": "synthetic_contract_only",
        }
        if first["diagnostic_type"] == "keypoint_jump":
            row["joint"] = first["joints"][0]
        else:
            row["left_joint"], row["right_joint"] = first["joint_pairs"][0]
        positives.append(row)

    adjudications: list[dict] = []
    for task in sealed["tasks"]:
        is_first = task["task_id"] == first["task_id"]
        adjudications.append(
            {
                "adjudication_id": f"synthetic-adj:{task['task_id']}",
                "task_id": task["task_id"],
                "decision": "confirmed_true" if is_first else "confirmed_false",
                "source_coverage_annotation_ids": ";".join(
                    _coverage_id(
                        task["clip_id"], task["diagnostic_type"], annotator
                    )
                    for annotator in ("synthetic-a", "synthetic-b")
                ),
                "source_positive_annotation_ids": (
                    ";".join(positive_ids) if is_first else ""
                ),
                "reviewer_id": "synthetic-reviewer",
                "adjudicated_at": "2026-08-22T01:00:00Z",
                "reason": "",
                "notes": "synthetic_contract_only",
            }
        )
    _write_csv(pack / "coverage-annotations.csv", COVERAGE_HEADERS, coverage)
    _write_csv(pack / "positive-annotations.csv", POSITIVE_HEADERS, positives)
    _write_csv(
        pack / "candidate-adjudications.csv", ADJUDICATION_HEADERS, adjudications
    )


class PoseDiagnosticClipTruthM79Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = json.loads(
            (REAL_PACK / "manifest.json").read_text(encoding="utf-8")
        )
        cls.sealed = json.loads(
            (REAL_PACK / "sealed-candidates.json").read_text(encoding="utf-8")
        )
        cls.empty_report = json.loads(
            (REAL_PACK / "compiled" / "evaluation.json").read_text(encoding="utf-8")
        )

    def test_real_pack_is_empty_blind_and_source_replayable(self) -> None:
        validate_clip_truth_manifest_sources(self.manifest)
        validate_empty_clip_truth_report(self.empty_report)
        counts = self.manifest["plan"]["counts"]
        self.assertEqual(3, counts["videos"])
        self.assertEqual(103, counts["clips"])
        self.assertEqual(1371, counts["candidate_tasks"])
        self.assertEqual(
            {"keypoint_jump": 1017, "left_right_swap": 354},
            counts["candidate_tasks_by_type"],
        )
        self.assertEqual(1336, counts["unique_affected_indicator_instances"])
        self.assertEqual(9636, counts["source_frame_observations"])
        self.assertEqual(103, len(list((REAL_PACK / "media").glob("*.mp4"))))
        self.assertEqual(
            103, len(list((REAL_PACK / "pose-evidence").glob("*.json")))
        )
        self.assertEqual(0, self.empty_report["counts"]["coverage_annotations"])
        self.assertIsNone(self.empty_report["metrics_by_diagnostic_type"])

        review = (REAL_PACK / "review.html").read_text(encoding="utf-8")
        adjudicate = (REAL_PACK / "adjudicate.html").read_text(encoding="utf-8")
        for leaked in (
            "pdr-",
            "affected_indicator_instances",
            "candidate_source_frame_index",
            "sealed-candidates",
            "pose_evidence_url",
            "pose-evidence/",
            "C:\\Users\\private-user",
        ):
            self.assertNotIn(leaked, review)
        self.assertIn("candidate_task_ids_included\":false", review)
        self.assertIn("pdr-", adjudicate)
        self.assertIn("coverage CSV（选择两份）", adjudicate)
        self.assertIn("第三方裁决", adjudicate)
        self.assertIn("pose_evidence_url", adjudicate)
        self.assertIn("pose-diagnostic-adjudication-overlay-v1.0.0", adjudicate)
        self.assertIn("完整骨架", adjudicate)

    def test_every_clip_is_independent_h264_with_exact_frame_count(self) -> None:
        for video in self.manifest["plan"]["videos"]:
            for segment in video["segments"]:
                media = segment["clip_media"]
                self.assertEqual("h264", media["probe"]["codec_fourcc"])
                self.assertEqual(segment["frame_count"], media["probe"]["frame_count"])
                self.assertTrue(
                    all(item["decoded"] for item in media["probe"]["decode_samples"])
                )
                self.assertEqual(file_sha256(media["path"]), media["sha256"])
                pose_binding = segment["adjudication_pose_evidence"]
                pose_path = Path(pose_binding["path"])
                self.assertEqual(file_sha256(pose_path), pose_binding["sha256"])
                pose = json.loads(pose_path.read_text(encoding="utf-8"))
                self.assertEqual(segment["clip_id"], pose["clip_id"])
                self.assertEqual(segment["frame_count"], len(pose["frames"]))
                self.assertEqual(
                    segment["source_timestamps_ms"],
                    [row["timestamp_ms"] for row in pose["frames"]],
                )
                self.assertEqual(
                    pose_binding["pose_frame_count"],
                    sum(bool(row["pose_present"]) for row in pose["frames"]),
                )

    def test_rehashed_pose_evidence_tamper_fails_source_frame_replay(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = copy.deepcopy(self.manifest)
            segment = manifest["plan"]["videos"][0]["segments"][0]
            evidence = json.loads(
                Path(segment["adjudication_pose_evidence"]["path"]).read_text(
                    encoding="utf-8"
                )
            )
            evidence["frames"][0]["keypoints"][0][1] += 100.0
            path = root / "tampered-pose-evidence.json"
            path.write_text(
                json.dumps(evidence, ensure_ascii=False, indent=2, allow_nan=False)
                + "\n",
                encoding="utf-8",
            )
            segment["adjudication_pose_evidence"]["path"] = str(path.resolve())
            segment["adjudication_pose_evidence"]["sha256"] = file_sha256(path)
            segment["adjudication_pose_evidence"]["canonical_sha256"] = canonical_sha256(
                evidence
            )
            with self.assertRaisesRegex(
                PoseDiagnosticClipTruthError, "does not replay from source frames"
            ):
                validate_clip_truth_manifest_sources(manifest)

    def test_rehashed_sealed_candidate_tamper_fails_queue_replay(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = copy.deepcopy(self.manifest)
            sealed = copy.deepcopy(self.sealed)
            sealed["tasks"][0]["timestamp_ms"] += 1
            path = root / "sealed.json"
            path.write_text(
                json.dumps(sealed, ensure_ascii=False, indent=2, allow_nan=False)
                + "\n",
                encoding="utf-8",
            )
            manifest["artifacts"]["sealed_candidates"] = {
                "path": str(path.resolve()),
                "sha256": file_sha256(path),
                "canonical_sha256": canonical_sha256(sealed),
            }
            with self.assertRaisesRegex(
                PoseDiagnosticClipTruthError, "do not replay from source queues"
            ):
                validate_clip_truth_manifest_sources(manifest)

    def test_synthetic_two_annotator_third_party_flow_only_reports_precision(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            pack = Path(temp)
            manifest, sealed = _copy_contract_shell(pack)
            _complete_synthetic_contract(pack, manifest, sealed)
            report = compile_clip_truth(pack)
            self.assertEqual("completed_candidate_precision_only", report["status"])
            self.assertEqual(412, report["counts"]["coverage_annotations"])
            self.assertEqual(1371, report["counts"]["candidate_adjudications"])
            self.assertEqual(2, report["counts"]["positive_annotations"])
            first_type = sealed["tasks"][0]["diagnostic_type"]
            self.assertGreater(
                report["metrics_by_diagnostic_type"][first_type][
                    "candidate_precision"
                ],
                0.0,
            )
            for metrics in report["metrics_by_diagnostic_type"].values():
                self.assertIsNone(metrics["recall"])
                self.assertIsNone(metrics["f1"])
            self.assertFalse(any(report["acceptance"].values()))
            self.assertFalse(any(report["safety"].values()))

    def test_reviewer_cannot_be_one_of_the_two_annotators(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            pack = Path(temp)
            manifest, sealed = _copy_contract_shell(pack)
            _complete_synthetic_contract(pack, manifest, sealed)
            with (pack / "candidate-adjudications.csv").open(
                encoding="utf-8-sig", newline=""
            ) as handle:
                rows = list(csv.DictReader(handle))
            rows[0]["reviewer_id"] = "synthetic-a"
            _write_csv(pack / "candidate-adjudications.csv", ADJUDICATION_HEADERS, rows)
            with self.assertRaisesRegex(
                PoseDiagnosticClipTruthError, "independent reviewer"
            ):
                compile_clip_truth(pack)

    def test_range_server_returns_partial_content(self) -> None:
        spec = importlib.util.spec_from_file_location(
            "range_http_server_m79", ROOT / "scripts" / "range_http_server.py"
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "sample.bin").write_bytes(b"0123456789")
            handler = lambda *args, **kwargs: module.RangeRequestHandler(
                *args,
                directory=str(root),
                allowed_entries=((root.resolve(), True),),
                **kwargs,
            )
            server = module.ThreadingHTTPServer(("127.0.0.1", 0), handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = urllib.request.Request(
                    f"http://127.0.0.1:{server.server_port}/sample.bin",
                    headers={"Range": "bytes=2-5"},
                )
                with urllib.request.urlopen(request, timeout=5) as response:
                    self.assertEqual(206, response.status)
                    self.assertEqual("bytes 2-5/10", response.headers["Content-Range"])
                    self.assertEqual("bytes", response.headers["Accept-Ranges"])
                    self.assertEqual(b"2345", response.read())
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
