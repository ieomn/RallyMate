from __future__ import annotations

import csv
import hashlib
import http.client
import io
import json
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from rallymate_evaluation.m96_pose_pilot import (
    ADJUDICATION_FIELDS,
    ADJUDICATION_SUBMISSION_VERSION,
    ANNOTATION_FIELDS,
    ANNOTATION_SUBMISSION_VERSION,
    M96PosePilotError,
    SAFETY,
    build_m96_adjudication_bundle,
    role_service_contract,
    validate_m96_adjudication_bundle,
    validate_m96_adjudication_submission,
    validate_m96_pose_pilot,
)


ROOT = Path(__file__).resolve().parents[1]
PILOT = ROOT / "data" / "annotations" / "m96-halpe26-development-pilot-v1"
SERVER = ROOT / "scripts" / "serve_m96_pose_pilot.py"


def _csv_bytes(fields: tuple[str, ...], rows: list[dict[str, str]]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields, lineterminator="\r\n")
    writer.writeheader()
    writer.writerows(rows)
    return b"\xef\xbb\xbf" + output.getvalue().encode("utf-8")


def _annotation_rows(
    validated: dict,
    slot: str,
    role_id: str,
    *,
    disagree_first: bool = False,
) -> list[dict[str, str]]:
    manifest = validated["roles"][slot]
    rows = []
    for index, task in enumerate(validated["tasks"]):
        x = "0.26000000" if disagree_first and index == 0 else "0.25000000"
        rows.append(
            {
                "schema_version": "1.0.0",
                "submission_version": ANNOTATION_SUBMISSION_VERSION,
                "bundle_id": manifest["bundle_id"],
                "role_slot": slot,
                "task_contract_sha256": manifest["task_contract_sha256"],
                "annotation_id": f"synthetic-test-ann:{slot}:{index:04d}",
                "task_id": task["task_id"],
                "frame_id": task["frame_id"],
                "video_id": task["video_id"],
                "source_frame_index": str(task["source_frame_index"]),
                "joint_index": str(task["joint_index"]),
                "joint_name": task["joint_name"],
                "annotator_id": role_id,
                "visible": "true",
                "x_normalized": x,
                "y_normalized": "0.50000000",
                "visibility_reason": "",
                "annotated_at": "2026-09-05T00:00:00Z",
            }
        )
    return rows


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _request(port: int, path: str, *, host: str, byte_range: str | None = None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    connection.putrequest("GET", path, skip_host=True)
    connection.putheader("Host", host)
    if byte_range:
        connection.putheader("Range", byte_range)
    connection.endheaders()
    response = connection.getresponse()
    body = response.read()
    headers = dict(response.getheaders())
    status = response.status
    connection.close()
    return status, headers, body


class M96PosePilotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.validated = validate_m96_pose_pilot(ROOT, PILOT)

    def test_blank_pilot_is_deterministic_complete_and_evaluation_only(self) -> None:
        validated = self.validated
        self.assertEqual(624, len(validated["tasks"]))
        self.assertEqual(SAFETY, validated["manifest"]["safety"])
        self.assertFalse(SAFETY["dataset_export_allowed"])
        self.assertFalse(SAFETY["training_allowed"])
        self.assertFalse(SAFETY["promotion_allowed"])
        self.assertFalse(SAFETY["accuracy_claim_generated"])

        protocol = json.loads(validated["snapshots"]["protocol.json"])
        self.assertEqual(24, len(protocol["selected_frames"]))
        for video in protocol["source_bindings"]["videos"]:
            selected = [
                frame
                for frame in protocol["selected_frames"]
                if frame["video_id"] == video["video_id"]
            ]
            self.assertEqual(list(range(1, 9)), [frame["sample_ordinal"] for frame in selected])
            self.assertEqual(sorted(frame["timestamp_ms"] for frame in selected), [frame["timestamp_ms"] for frame in selected])
            self.assertEqual(8, len({frame["source_frame_index"] for frame in selected}))
        for frame_id in {task["frame_id"] for task in validated["tasks"]}:
            frame_tasks = [task for task in validated["tasks"] if task["frame_id"] == frame_id]
            self.assertEqual(list(range(26)), [task["joint_index"] for task in frame_tasks])

        task_raw = validated["snapshots"]["tasks.jsonl"]
        self.assertNotIn(b"c235227fffcd3290b60572d0c3f9cc85", task_raw)
        self.assertNotIn(b"41AE2B5C12A92E8883B159F0963741451D42A515BB82F11194DEC1F4FB1C05D6", task_raw)
        for forbidden in (b'"bbox_px"', b'"keypoints"', b'"confidence"', b'"x_normalized"', b'"y_normalized"'):
            self.assertNotIn(forbidden, task_raw)
        report = json.loads(validated["snapshots"]["validation-report.json"])
        self.assertEqual(0, report["counts"]["human_annotation_rows"])
        self.assertEqual(0, report["counts"]["accuracy_metrics"])

    def test_role_packages_are_separate_and_ui_supports_draft_csv_restore(self) -> None:
        self.assertNotEqual(
            self.validated["roles"]["A"]["bundle_id"],
            self.validated["roles"]["B"]["bundle_id"],
        )
        js = (PILOT / "annotator-A" / "m96-pose-pilot-workbench.js").read_text(encoding="utf-8")
        self.assertIn("localStorage", js)
        self.assertIn("importCsv", js)
        self.assertIn("CSV", js)
        for slot in ("A", "B"):
            html = (PILOT / f"annotator-{slot}" / "review.html").read_text(encoding="utf-8")
            self.assertIn(f'"role_slot":"{slot}"', html)
            self.assertNotIn('"sources":[', html)
            self.assertIn('"model_coordinates_in_tasks":false', html)

    def test_governance_template_has_no_inferred_values(self) -> None:
        rows = list(
            csv.DictReader(
                io.StringIO(self.validated["snapshots"]["governance.template.csv"].decode("utf-8"), newline="")
            )
        )
        self.assertEqual(3, len(rows))
        for row in rows:
            for field, value in row.items():
                if field != "video_id":
                    self.assertTrue(value.startswith("REPLACE_WITH_"), (field, value))

    def test_atomic_A_B_intake_preserves_raw_bytes_and_builds_only_C_disagreements(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            a_raw = _csv_bytes(ANNOTATION_FIELDS, _annotation_rows(self.validated, "A", "role-A"))
            b_raw = _csv_bytes(
                ANNOTATION_FIELDS,
                _annotation_rows(self.validated, "B", "role-B", disagree_first=True),
            )
            a_path, b_path = base / "A.csv", base / "B.csv"
            a_path.write_bytes(a_raw)
            b_path.write_bytes(b_raw)
            output = base / "C-bundle"
            manifest = build_m96_adjudication_bundle(
                ROOT,
                PILOT,
                a_path,
                b_path,
                output,
                generated_at="2026-09-05T00:01:00Z",
            )
            self.assertEqual(1, manifest["scope"]["disagreement_count"])
            self.assertEqual(a_raw, (output / "submissions" / "annotator-A.csv").read_bytes())
            self.assertEqual(b_raw, (output / "submissions" / "annotator-B.csv").read_bytes())
            self.assertEqual(hashlib.sha256(a_raw).hexdigest().upper(), manifest["sources"][0]["raw_sha256"])
            checked = validate_m96_adjudication_bundle(ROOT, output)
            self.assertEqual(1, len(checked["disagreement_tasks"]))
            html = (output / "adjudicator-C" / "review.html").read_text(encoding="utf-8")
            self.assertEqual(1, html.count('"sources":['))
            self.assertNotIn("candidate", html.lower())

            c_task = checked["disagreement_tasks"][0]
            c_rows = [
                {
                    "schema_version": "1.0.0",
                    "submission_version": ADJUDICATION_SUBMISSION_VERSION,
                    "adjudication_bundle_id": checked["C_role"]["bundle_id"],
                    "task_contract_sha256": checked["C_role"]["task_contract_sha256"],
                    "adjudication_id": "synthetic-test-adj:C:0000",
                    "task_id": c_task["task_id"],
                    "source_annotation_a_id": c_task["sources"][0]["annotation_id"],
                    "source_annotation_b_id": c_task["sources"][1]["annotation_id"],
                    "reviewer_id": "role-C",
                    "visible": "true",
                    "x_normalized": "0.25500000",
                    "y_normalized": "0.50000000",
                    "visibility_reason": "",
                    "adjudicated_at": "2026-09-05T00:02:00Z",
                }
            ]
            c_path = base / "C.csv"
            c_path.write_bytes(_csv_bytes(ADJUDICATION_FIELDS, c_rows))
            result = validate_m96_adjudication_submission(ROOT, output, c_path)
            self.assertEqual("role-C", result["reviewer_id"])
            c_rows[0]["reviewer_id"] = "role-A"
            c_path.write_bytes(_csv_bytes(ADJUDICATION_FIELDS, c_rows))
            with self.assertRaisesRegex(M96PosePilotError, "must differ"):
                validate_m96_adjudication_submission(ROOT, output, c_path)

    def test_invalid_or_reused_A_B_identity_leaves_no_partial_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            a_path, b_path = base / "A.csv", base / "B.csv"
            a_path.write_bytes(_csv_bytes(ANNOTATION_FIELDS, _annotation_rows(self.validated, "A", "same-role")))
            b_path.write_bytes(_csv_bytes(ANNOTATION_FIELDS, _annotation_rows(self.validated, "B", "same-role")))
            output = base / "must-not-exist"
            with self.assertRaisesRegex(M96PosePilotError, "must be distinct"):
                build_m96_adjudication_bundle(ROOT, PILOT, a_path, b_path, output)
            self.assertFalse(output.exists())
            self.assertEqual([], list(base.glob(".must-not-exist.staging-*")))

            b_path.write_bytes(_csv_bytes(ANNOTATION_FIELDS, _annotation_rows(self.validated, "B", "role-B")[:-1]))
            with self.assertRaisesRegex(M96PosePilotError, "exactly 624"):
                build_m96_adjudication_bundle(ROOT, PILOT, a_path, b_path, output)
            self.assertFalse(output.exists())

    def test_loopback_server_exposes_only_selected_role_and_three_videos(self) -> None:
        contract = role_service_contract(ROOT, PILOT, "A")
        port = _free_port()
        process = subprocess.Popen(
            [
                sys.executable,
                str(SERVER),
                "--workspace",
                str(ROOT),
                "--bundle",
                str(PILOT),
                "--role",
                "A",
                "--port",
                str(port),
            ],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            deadline = time.monotonic() + 12
            while True:
                try:
                    status, _, body = _request(
                        port, contract["entrypoint"], host=f"127.0.0.1:{port}"
                    )
                    break
                except OSError:
                    if process.poll() is not None:
                        self.fail(process.stderr.read() if process.stderr else "server exited")
                    if time.monotonic() >= deadline:
                        self.fail("M96 role server did not become ready")
                    time.sleep(0.05)
            self.assertEqual(200, status)
            self.assertIn(b"M96 Halpe26", body)
            video_path = "/" + self.validated["roles"]["A"]["video_sources"][0]["relative_path"]
            status, headers, body = _request(
                port, video_path, host=f"localhost:{port}", byte_range="bytes=0-15"
            )
            self.assertEqual(206, status)
            self.assertEqual(16, len(body))
            self.assertEqual("allowlist-v1", headers["X-RallyMate-Range-Server"])
            denied = (
                contract["entrypoint"].replace("annotator-A", "annotator-B"),
                "/data/annotations/m96-halpe26-development-pilot-v1/protocol.json",
                "/models/rtmpose/m95-shadow-candidates.json",
                "/README.md",
            )
            for path in denied:
                with self.subTest(path=path):
                    status, _, _ = _request(port, path, host=f"127.0.0.1:{port}")
                    self.assertEqual(404, status)
            status, _, _ = _request(port, contract["entrypoint"], host=f"attacker.invalid:{port}")
            self.assertEqual(403, status)
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            if process.stdout:
                process.stdout.close()
            if process.stderr:
                process.stderr.close()


if __name__ == "__main__":
    unittest.main()
