from __future__ import annotations

import base64
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest

import numpy as np

from rallymate_service.mesh_geometry import (
    GEOMETRY_PAYLOAD_VERSION,
    MAX_ALLOWED_FRAMES,
    MAX_PAYLOAD_BYTES,
    MeshGeometryUnavailable,
    QUANTIZATION_MIN,
    QUANTIZATION_SPAN,
    build_mesh_geometry_payload,
    mesh_geometry_availability,
)
from rallymate_vision.sam3d_mesh import LEDGER_NAME, VERSION as ARCHIVE_VERSION


VIDEO_SHA = "a" * 64
JOB_ID = "7f1c9b2e-4d8a-4e21-9a10-2b3c4d5e6f70"


def _make_uv_sphere(lat_steps: int, lon_steps: int, *, radius: float = 0.85, extra_unused_verts: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """Generate a smooth closed 3D mesh with exact known vertex/face counts."""
    verts: list[list[float]] = []
    for i in range(lat_steps + 1):
        theta = np.pi * i / lat_steps
        y = -radius * np.cos(theta)  # SAM 3D camera space: -Y is up
        ring_r = radius * np.sin(theta)
        for j in range(lon_steps):
            phi = 2.0 * np.pi * j / lon_steps
            verts.append([ring_r * np.cos(phi), y, ring_r * np.sin(phi)])
    for k in range(extra_unused_verts):
        verts.append([99.0 + k, -99.0, 99.0])
    vertices = np.asarray(verts, dtype=np.float32)

    faces: list[list[int]] = []
    for i in range(lat_steps):
        for j in range(lon_steps):
            jn = (j + 1) % lon_steps
            v00 = i * lon_steps + j
            v01 = i * lon_steps + jn
            v10 = (i + 1) * lon_steps + j
            v11 = (i + 1) * lon_steps + jn
            faces.append([v00, v10, v11])
            faces.append([v00, v11, v01])
    return vertices, np.asarray(faces, dtype=np.int32)


def _write_archive(
    output_dir: Path,
    frame_vertices: list[np.ndarray],
    faces: np.ndarray,
    *,
    cameras: list[np.ndarray] | None = None,
    timestamps_ms: list[float] | None = None,
    video_sha: str = VIDEO_SHA,
    add_secondary_track: bool = False,
) -> dict:
    """Create a realistic SAM 3D mesh archive matching sam3d_mesh.py layout."""
    output_dir.mkdir(parents=True, exist_ok=True)
    data_dir = output_dir / "sam3d-mesh-data"
    data_dir.mkdir(exist_ok=True)

    faces_buf = io.BytesIO()
    np.save(faces_buf, faces, allow_pickle=False)
    faces_bytes = faces_buf.getvalue()
    faces_sha = hashlib.sha256(faces_bytes).hexdigest().upper()
    faces_rel = f"sam3d-mesh-data/faces-{faces_sha[:16].lower()}.npy"
    (output_dir / faces_rel).write_bytes(faces_bytes)

    ledger_lines: list[str] = []
    for idx, verts in enumerate(frame_vertices):
        cam = (
            cameras[idx]
            if cameras is not None
            else np.array([1.25 + idx * 0.01, -0.4, 4.2], dtype=np.float32)
        )
        ts = timestamps_ms[idx] if timestamps_ms is not None else float(idx * 50)
        npz_buf = io.BytesIO()
        np.savez(npz_buf, vertices=verts, camera=cam, focal=np.asarray(1200.0, dtype=np.float32))
        npz_bytes = npz_buf.getvalue()
        mesh_sha = hashlib.sha256(npz_bytes).hexdigest().upper()
        mesh_rel = f"sam3d-mesh-data/frame-{idx:04d}-primary.mesh.npz"
        (output_dir / mesh_rel).write_bytes(npz_bytes)

        if add_secondary_track:
            # Put a distractor secondary track BEFORE primary in the ledger to verify primary selection.
            sec_buf = io.BytesIO()
            np.savez(sec_buf, vertices=verts + 10.0, camera=cam, focal=np.asarray(1200.0, dtype=np.float32))
            sec_bytes = sec_buf.getvalue()
            sec_sha = hashlib.sha256(sec_bytes).hexdigest().upper()
            sec_rel = f"sam3d-mesh-data/frame-{idx:04d}-secondary.mesh.npz"
            (output_dir / sec_rel).write_bytes(sec_bytes)
            ledger_lines.append(
                json.dumps(
                    {
                        "video_sha256": video_sha,
                        "frame_index": idx * 2,
                        "processed_index": idx,
                        "pts": idx * 1500,
                        "time_base_num": 1,
                        "time_base_den": 30000,
                        "timestamp_ms": ts + 1000.0,
                        "relative_timestamp_ms": ts,
                        "person_track_id": 99,
                        "subject_selection": {"version": "v1", "primary_track_id": 7, "decision": None},
                        "mesh_artifact": {
                            "version": ARCHIVE_VERSION,
                            "path": sec_rel,
                            "sha256": sec_sha,
                            "faces_path": faces_rel,
                            "faces_sha256": faces_sha,
                            "vertex_count": int(len(verts)),
                            "face_count": int(len(faces)),
                            "geometry": "official_estimator_pred_vertices",
                        },
                    }
                )
            )

        ledger_lines.append(
            json.dumps(
                {
                    "video_sha256": video_sha,
                    "frame_index": idx * 2,
                    "processed_index": idx,
                    "pts": idx * 1500,
                    "time_base_num": 1,
                    "time_base_den": 30000,
                    "timestamp_ms": ts + 1000.0,
                    "relative_timestamp_ms": ts,
                    "person_track_id": 7,
                    "subject_selection": {"version": "v1", "primary_track_id": 7, "decision": None},
                    "mesh_artifact": {
                        "version": ARCHIVE_VERSION,
                        "path": mesh_rel,
                        "sha256": mesh_sha,
                        "faces_path": faces_rel,
                        "faces_sha256": faces_sha,
                        "vertex_count": int(len(verts)),
                        "face_count": int(len(faces)),
                        "geometry": "official_estimator_pred_vertices",
                    },
                }
            )
        )

    (output_dir / LEDGER_NAME).write_text("\n".join(ledger_lines) + "\n", encoding="utf-8")
    return {
        "id": JOB_ID,
        "status": "succeeded",
        "cancel_requested": False,
        "deleted_at": None,
        "output_dir": str(output_dir),
        "summary": {
            "minimum_scoring_loop": {"provenance": {"video_sha256": video_sha}},
        },
    }


def _dequantize_frame(payload: dict, frame_idx: int) -> np.ndarray:
    raw = base64.b64decode(payload["frames"][frame_idx]["vertices_b64"])
    q = np.frombuffer(raw, dtype="<i2").reshape(payload["vertex_count"], 3).astype(np.float64)
    bmin = np.asarray(payload["bounds_min"], dtype=np.float64)
    bmax = np.asarray(payload["bounds_max"], dtype=np.float64)
    return bmin + ((q - QUANTIZATION_MIN) / QUANTIZATION_SPAN) * (bmax - bmin)


class MeshGeometryBuilderTests(unittest.TestCase):
    def test_quantization_accuracy_centering_unit_height_and_vertex_compaction(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "run"
            base_v, faces = _make_uv_sphere(8, 12, radius=0.85, extra_unused_verts=25)
            # 3 animated frames with slight motion and non-origin court camera offset.
            frames_v = [
                (base_v + np.array([i * 0.05, 0.0, 0.0], dtype=np.float32))
                for i in range(3)
            ]
            cameras = [np.array([4.5, -1.2, 7.8], dtype=np.float32) for _ in range(3)]
            job = _write_archive(output_dir, frames_v, faces, cameras=cameras, add_secondary_track=True)

            self.assertTrue(mesh_geometry_availability(job))
            payload = build_mesh_geometry_payload(job)

            self.assertEqual(payload["version"], GEOMETRY_PAYLOAD_VERSION)
            self.assertEqual(payload["job_id"], JOB_ID)
            self.assertEqual(payload["coordinate_system"], "y_up_right_handed_centered")
            self.assertFalse(payload["decimated"])
            # 25 unreferenced vertices must be stripped by compaction.
            self.assertEqual(payload["original_vertex_count"], len(base_v))
            self.assertEqual(payload["vertex_count"], len(base_v) - 25)
            self.assertEqual(payload["face_count"], len(faces))
            self.assertEqual(payload["topology"]["index_type"], "uint16")
            self.assertEqual(payload["material"]["base_color_rgb"], [166, 189, 219])
            self.assertEqual(payload["selected_frame_count"], 3)
            # Primary track 7 must be chosen over distractor track 99.
            self.assertTrue(all(f["person_track_id"] == 7 for f in payload["frames"]))

            # Verify indices decode cleanly and all stay within [0, vertex_count).
            idx_bytes = base64.b64decode(payload["topology"]["indices_b64"])
            indices = np.frombuffer(idx_bytes, dtype="<u2")
            self.assertEqual(len(indices), payload["face_count"] * 3)
            self.assertLess(int(indices.max()), payload["vertex_count"])

            # Verify centering around (0, 0, 0), unit height (~1.0), and Int16 accuracy < 1e-3.
            center = np.asarray(payload["normalization"]["center"], dtype=np.float64)
            scale = float(payload["normalization"]["scale"])
            self.assertAlmostEqual(scale, 1.7, places=3)  # diameter of radius=0.85 sphere

            bmin = np.asarray(payload["bounds_min"], dtype=np.float64)
            bmax = np.asarray(payload["bounds_max"], dtype=np.float64)
            midpoint = (bmin + bmax) * 0.5
            np.testing.assert_allclose(midpoint, [0.0, 0.0, 0.0], atol=1e-6)
            self.assertAlmostEqual(float(bmax[1] - bmin[1]), 1.0, places=3)

            used = np.unique(faces)
            for i in range(3):
                expected = frames_v[i][used].astype(np.float64) + cameras[i].astype(np.float64)
                expected[:, 1] *= -1.0
                expected[:, 2] *= -1.0
                expected_norm = (expected - center) / scale
                decoded = _dequantize_frame(payload, i)
                max_err = float(np.max(np.abs(decoded - expected_norm)))
                self.assertLess(max_err, 1e-3, f"frame {i} quantization error too high: {max_err}")

    def test_frame_filtering_striding_and_clamping(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "run"
            base_v, faces = _make_uv_sphere(4, 6)
            timestamps = [float(i * 100) for i in range(120)]
            job = _write_archive(output_dir, [base_v] * 120, faces, timestamps_ms=timestamps)

            # Time window filter [2000, 6000] has 41 frames -> stride to 10.
            windowed = build_mesh_geometry_payload(job, start_ms=2000, end_ms=6000, max_frames=10)
            self.assertEqual(windowed["source_frame_count"], 120)
            self.assertEqual(windowed["selected_frame_count"], 10)
            self.assertEqual(windowed["frames"][0]["timestamp_ms"], 2000.0)
            self.assertEqual(windowed["frames"][-1]["timestamp_ms"], 6000.0)

            # Requesting max_frames > 90 clamps cleanly to 90.
            clamped = build_mesh_geometry_payload(job, max_frames=500)
            self.assertEqual(clamped["selected_frame_count"], MAX_ALLOWED_FRAMES)

            # Out-of-range window raises clean 404 MeshGeometryUnavailable.
            with self.assertRaises(MeshGeometryUnavailable) as ctx:
                build_mesh_geometry_payload(job, start_ms=99999, end_ms=100500)
            self.assertEqual(ctx.exception.status_code, 404)

            # Inverted range raises clean 400.
            with self.assertRaises(MeshGeometryUnavailable) as ctx_inv:
                build_mesh_geometry_payload(job, start_ms=5000, end_ms=1000)
            self.assertEqual(ctx_inv.exception.status_code, 400)

    def test_deterministic_face_decimation_and_payload_size_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "run"
            # 120 * 150 * 2 = 36,000 faces (real MHR scale ~36.8k faces, ~18k vertices!)
            base_v, faces = _make_uv_sphere(120, 150)
            self.assertEqual(len(faces), 36000)
            job = _write_archive(output_dir, [base_v] * 60, faces)

            payload_a = build_mesh_geometry_payload(job, max_frames=60, max_faces=12000)
            payload_b = build_mesh_geometry_payload(job, max_frames=60, max_faces=12000)

            self.assertTrue(payload_a["decimated"])
            self.assertLessEqual(payload_a["face_count"], 12000)
            self.assertGreater(payload_a["face_count"], 2000)
            self.assertLess(payload_a["vertex_count"], payload_a["original_vertex_count"])
            # Deterministic output across repeated calls.
            self.assertEqual(payload_a["topology"]["indices_b64"], payload_b["topology"]["indices_b64"])
            self.assertEqual(payload_a["frames"][0]["vertices_b64"], payload_b["frames"][0]["vertices_b64"])

            # Serialized JSON must stay strictly below 4 MiB even for 60 frames!
            wire_bytes = len(json.dumps(payload_a, separators=(",", ":"), allow_nan=False).encode("utf-8"))
            self.assertLess(wire_bytes, MAX_PAYLOAD_BYTES)

            # Even when caller requests max_faces=40000 with 60 frames, auto-budget keeps payload < 4 MiB.
            heavy = build_mesh_geometry_payload(job, max_frames=60, max_faces=40000)
            heavy_bytes = len(json.dumps(heavy, separators=(",", ":"), allow_nan=False).encode("utf-8"))
            self.assertLess(heavy_bytes, MAX_PAYLOAD_BYTES)
            self.assertGreaterEqual(heavy["selected_frame_count"], 1)

    def test_lifecycle_guards_reject_non_succeeded_cancelled_and_deleted_jobs(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "run"
            base_v, faces = _make_uv_sphere(4, 6)
            job = _write_archive(output_dir, [base_v], faces)

            for patch, expected_status in [
                ({"status": "running"}, 409),
                ({"status": "failed"}, 409),
                ({"cancel_requested": True}, 409),
                ({"deleted_at": "2026-11-24T00:00:00Z"}, 404),
            ]:
                mutated = {**job, **patch}
                self.assertFalse(mesh_geometry_availability(mutated))
                with self.assertRaises(MeshGeometryUnavailable) as ctx:
                    build_mesh_geometry_payload(mutated)
                self.assertEqual(ctx.exception.status_code, expected_status)

    def test_path_traversal_symlink_escape_and_checksum_tamper_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output_dir = root / "run"
            base_v, faces = _make_uv_sphere(4, 6)
            job = _write_archive(output_dir, [base_v], faces)

            # Create an outside NPZ file with matching hash to attempt path traversal escape.
            outside = root / "outside.mesh.npz"
            orig_npz = next((output_dir / "sam3d-mesh-data").glob("*.mesh.npz"))
            outside.write_bytes(orig_npz.read_bytes())

            ledger_path = output_dir / LEDGER_NAME
            original_line = ledger_path.read_text(encoding="utf-8").strip()
            row = json.loads(original_line)

            # 1. Parent traversal (../outside.mesh.npz)
            escaped_row = json.loads(original_line)
            escaped_row["mesh_artifact"]["path"] = "../outside.mesh.npz"
            ledger_path.write_text(json.dumps(escaped_row) + "\n", encoding="utf-8")
            self.assertFalse(mesh_geometry_availability(job))
            with self.assertRaises(MeshGeometryUnavailable) as ctx:
                build_mesh_geometry_payload(job)
            self.assertEqual(ctx.exception.code, "path_escape")

            # 2. Absolute path escape
            abs_row = json.loads(original_line)
            abs_row["mesh_artifact"]["path"] = str(outside.resolve())
            ledger_path.write_text(json.dumps(abs_row) + "\n", encoding="utf-8")
            with self.assertRaises(MeshGeometryUnavailable) as ctx:
                build_mesh_geometry_payload(job)
            self.assertEqual(ctx.exception.code, "path_escape")

            # 3. File in output_dir outside sam3d-mesh-data/
            sibling = output_dir / "escape.mesh.npz"
            sibling.write_bytes(orig_npz.read_bytes())
            sib_row = json.loads(original_line)
            sib_row["mesh_artifact"]["path"] = "escape.mesh.npz"
            ledger_path.write_text(json.dumps(sib_row) + "\n", encoding="utf-8")
            with self.assertRaises(MeshGeometryUnavailable) as ctx:
                build_mesh_geometry_payload(job)
            self.assertEqual(ctx.exception.code, "path_escape")

            # 4. Symlink inside sam3d-mesh-data pointing outside (skip only if OS blocks symlinks)
            link_path = output_dir / "sam3d-mesh-data" / "symlink.mesh.npz"
            try:
                os.symlink(outside, link_path)
            except OSError:
                link_path = None
            if link_path is not None:
                sym_row = json.loads(original_line)
                sym_row["mesh_artifact"]["path"] = "sam3d-mesh-data/symlink.mesh.npz"
                ledger_path.write_text(json.dumps(sym_row) + "\n", encoding="utf-8")
                self.assertFalse(mesh_geometry_availability(job))
                with self.assertRaises(MeshGeometryUnavailable) as ctx:
                    build_mesh_geometry_payload(job)
                self.assertEqual(ctx.exception.code, "path_escape")
                link_path.unlink()

            # 5. Tampered checksum on valid contained file
            ledger_path.write_text(json.dumps(row) + "\n", encoding="utf-8")
            orig_npz.write_bytes(orig_npz.read_bytes() + b"tamper")
            with self.assertRaises(MeshGeometryUnavailable) as ctx:
                build_mesh_geometry_payload(job)
            self.assertEqual(ctx.exception.code, "checksum_mismatch")

    def test_edge_cases_backslash_paths_flat_mesh_and_provenance_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "run"
            # Flat triangle in XZ plane (Y span == 0) to verify zero-span axis quantization stays finite & exact.
            flat_v = np.array([[0.0, 0.5, 0.0], [1.0, 0.5, 0.0], [0.5, 0.5, 1.0]], dtype=np.float32)
            flat_f = np.array([[0, 1, 2]], dtype=np.int32)
            job = _write_archive(output_dir, [flat_v], flat_f)

            # Rewrite ledger paths with Windows backslash separators (matching archive_mesh_records on Windows).
            ledger_path = output_dir / LEDGER_NAME
            row = json.loads(ledger_path.read_text(encoding="utf-8").strip())
            row["mesh_artifact"]["path"] = row["mesh_artifact"]["path"].replace("/", "\\")
            row["mesh_artifact"]["faces_path"] = row["mesh_artifact"]["faces_path"].replace("/", "\\")
            ledger_path.write_text(json.dumps(row) + "\n", encoding="utf-8")

            payload = build_mesh_geometry_payload(job)
            decoded = _dequantize_frame(payload, 0)
            self.assertTrue(np.isfinite(decoded).all())
            np.testing.assert_allclose(decoded[:, 1], 0.0, atol=1e-6)

            # Provenance video_sha256 mismatch must be rejected.
            mismatched = {
                **job,
                "summary": {"minimum_scoring_loop": {"provenance": {"video_sha256": "b" * 64}}},
            }
            self.assertFalse(mesh_geometry_availability(mismatched))
            with self.assertRaises(MeshGeometryUnavailable) as ctx:
                build_mesh_geometry_payload(mismatched)
            self.assertEqual(ctx.exception.code, "source_mismatch")

            # Missing ledger cleanly reports unavailable.
            ledger_path.unlink()
            self.assertFalse(mesh_geometry_availability(job))
            with self.assertRaises(MeshGeometryUnavailable) as ctx_missing:
                build_mesh_geometry_payload(job)
            self.assertEqual(ctx_missing.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
