"""Source-bound SAM mesh capture and the official renderer, with a reused GL context.

Mesh arrays are the estimator's actual outputs. No surface is reconstructed from
keypoints. Binary arrays stay off the line-oriented worker protocol.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from fractions import Fraction
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import types
from typing import Callable

import numpy as np

from rallymate_vision.pose.metadata import sha256_file


VERSION = "sam3d-official-mesh-v1"
VIDEO_NAME = "sam3d-mesh.mp4"
SIDE_VIDEO_NAME = "sam3d-mesh-side.mp4"
PREVIEW_NAME = "sam3d-mesh-preview.jpg"
MANIFEST_NAME = "sam3d-mesh-manifest.json"
LEDGER_NAME = "sam3d-mesh-frames.jsonl"
LIGHT_BLUE = (0.65098039, 0.74117647, 0.85882353)
# The capture contract permits <=100000 float vertices and <=200000 integer
# faces. Each native NPY/NPZ is below this bound even with 16-byte floats.
MAX_GEOMETRY_FILE_BYTES = 8 * 1024 * 1024
# Only populated on an unconfirmed CPU-thread join. Keep the context alive on
# its owning process; worker reference release must not trigger GL __del__.
# No subsequent render is admitted until that isolated process exits.
_UNJOINED_PREPARATIONS = []


def _contained(path: Path, root: Path) -> Path:
    resolved, root = path.resolve(), root.resolve()
    if resolved == root or root not in resolved.parents:
        raise ValueError("mesh artifact escapes its owned directory")
    return resolved


def _arrays(output: dict, faces: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, float]:
    vertices = np.asarray(output["pred_vertices"])
    faces = np.asarray(faces)
    camera = np.asarray(output["pred_cam_t"])
    focal = np.asarray(output["focal_length"])
    if (vertices.ndim != 2 or vertices.shape[1] != 3 or not 3 <= len(vertices) <= 100000
            or vertices.dtype.kind != "f" or not np.isfinite(vertices).all()):
        raise ValueError("invalid SAM mesh vertices")
    if (faces.ndim != 2 or faces.shape[1] != 3 or not 1 <= len(faces) <= 200000
            or faces.dtype.kind not in "iu" or faces.min() < 0 or faces.max() >= len(vertices)):
        raise ValueError("invalid SAM mesh topology")
    if camera.shape != (3,) or not np.isfinite(camera).all() or camera[2] <= 0:
        raise ValueError("invalid SAM mesh camera")
    if focal.size != 1 or not np.isfinite(focal).all() or float(focal.reshape(-1)[0]) <= 0:
        raise ValueError("invalid SAM mesh focal length")
    return vertices, faces, camera, float(focal.reshape(-1)[0])


def capture_mesh_output(output: dict, faces: np.ndarray, runtime_dir: Path) -> dict:
    """Called in the model process; retain exact dtype and values, atomically."""
    vertices, faces, camera, focal = _arrays(output, faces)
    root = Path(runtime_dir).resolve() / "cache" / "mesh"
    root.mkdir(parents=True, exist_ok=True)
    topology_hash = hashlib.sha256(
        faces.dtype.str.encode() + str(faces.shape).encode() + faces.tobytes(order="C")
    ).hexdigest()
    topology = root / f"faces-{topology_hash}.npy"
    if not topology.exists():
        with tempfile.NamedTemporaryFile(dir=root, suffix=".npy", delete=False) as handle:
            temporary = Path(handle.name)
            np.save(handle, faces, allow_pickle=False)
        try:
            temporary.replace(topology)
        finally:
            temporary.unlink(missing_ok=True)
    faces_sha256 = sha256_file(topology)
    # Hash the exact NPZ bytes before publishing them. Reopening a freshly
    # written file for this self-check is expensive on the local filesystem;
    # the parent still verifies the actual disk bytes at the archive boundary.
    with io.BytesIO() as buffer:
        np.savez(buffer, vertices=vertices, camera=camera, focal=np.asarray(focal))
        payload = buffer.getvalue()
    mesh_sha256 = hashlib.sha256(payload).hexdigest().upper()
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=root, suffix=".npz", delete=False) as handle:
            temporary = Path(handle.name)
            if handle.write(payload) != len(payload):
                raise OSError("Incomplete SAM mesh write")
        path = temporary.with_suffix(".mesh.npz")
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return {
        "version": VERSION, "path": str(path), "sha256": mesh_sha256,
        "faces_path": str(topology), "faces_sha256": faces_sha256,
        "vertex_count": len(vertices), "face_count": len(faces),
        "geometry": "official_estimator_pred_vertices",
    }


def archive_mesh_records(records: list[dict], output_dir: Path, runtime_dir: Path) -> list[dict]:
    """Bind binary output to source PTS/track records and move it into this job."""
    output_dir = Path(output_dir).resolve()
    target = output_dir / "sam3d-mesh-data"
    root = Path(runtime_dir).resolve() / "cache" / "mesh"
    archived = []
    for record in records:
        if record.get("status") != "ok":
            if record.get("reason") == "sam_pose_quality_rejected" and record.get("mesh_artifact"):
                removed = discard_unarchived_mesh_records([record], runtime_dir)
                if removed:
                    record.pop("mesh_artifact")
                    record["mesh_discard_reason"] = "sam_pose_quality_rejected"
            continue
        artifact = record.get("mesh_artifact")
        if not artifact:
            continue
        if artifact.get("version") != VERSION:
            raise ValueError("unsupported SAM mesh artifact")
        source = _contained(Path(artifact["path"]), root)
        topology = _contained(Path(artifact["faces_path"]), root)
        if sha256_file(source) != artifact["sha256"] or sha256_file(topology) != artifact["faces_sha256"]:
            raise ValueError("SAM mesh capture checksum mismatch")
        target.mkdir(exist_ok=True)
        destination = target / source.name
        if destination.exists():
            raise ValueError("SAM mesh capture name collision")
        topology_destination = target / topology.name
        if not topology_destination.exists():
            # A topology remains in the worker cache for subsequent frames.
            topology_destination.write_bytes(topology.read_bytes())
        elif sha256_file(topology_destination) != artifact["faces_sha256"]:
            raise ValueError("SAM mesh topology collision")
        source.replace(destination)
        artifact = {**artifact, "path": str(destination.relative_to(output_dir)),
                    "faces_path": str(topology_destination.relative_to(output_dir))}
        record["mesh_artifact"] = artifact
        binding_keys = (
            "video_sha256", "frame_index", "processed_index", "pts", "time_base_num",
            "time_base_den", "timestamp_ms", "relative_timestamp_ms", "person_track_id",
            "selection_epoch", "image_size", "bbox_px", "inference_type",
            "pose_quality",
        )
        mesh_record = {**{key: record[key] for key in binding_keys if key in record},
                       "mesh_artifact": artifact}
        selection = record.get("subject_selection")
        if isinstance(selection, dict):
            # Full candidate diagnostics stay in native/frame archives. A mesh
            # ledger has a bounded per-line contract even in crowded scenes.
            mesh_record["subject_selection"] = {
                "version": selection.get("version"),
                "primary_track_id": selection.get("primary_track_id"),
                "decision": next((item for item in selection.get("decisions", [])
                                  if item.get("track_id") == record.get("person_track_id")), None),
            }
        archived.append(mesh_record)
    return archived


def discard_unarchived_mesh_records(records: list[dict], runtime_dir: Path) -> int:
    """Discard only verified captures still owned by the supplied records.

    This never scans the cache or removes shared topology. Archived records use
    relative paths, and captures already moved by the archive worker are absent.
    Invalid, changed or inaccessible files are retained rather than allowing
    best-effort cancellation cleanup to replace the original failure.
    """
    root = Path(runtime_dir).resolve() / "cache" / "mesh"
    removed = 0
    for record in records:
        artifact = record.get("mesh_artifact") if isinstance(record, dict) else None
        if not isinstance(artifact, dict) or artifact.get("version") != VERSION:
            continue
        raw_path, digest = artifact.get("path"), artifact.get("sha256")
        if (not isinstance(raw_path, str) or not isinstance(digest, str) or len(digest) != 64
                or any(value not in "0123456789abcdefABCDEF" for value in digest)):
            continue
        try:
            path = Path(raw_path)
            if not path.is_absolute() or path.is_symlink() or not path.name.endswith(".mesh.npz"):
                continue
            source = _contained(path, root)
            if source.parent != root.resolve() or not source.is_file():
                continue
            if sha256_file(source).upper() != digest.upper():
                continue
            source.unlink(missing_ok=True)
            removed += 1
        except (OSError, ValueError):
            continue
    return removed


class OfficialMeshRenderer:
    """The official Renderer.__call__, differing only in context lifetime.

    Clone the function globals rather than modifying the installed module or
    pyrender globally. Materials, camera, rotations, lighting and compositing
    remain the official implementation.
    """
    def __init__(self, width: int, height: int, runtime_dir: Path):
        repo = Path(runtime_dir).resolve() / "repo"
        if str(repo) not in sys.path:
            sys.path.insert(0, str(repo))
        import pyrender
        import trimesh
        from sam_3d_body.visualization.renderer import Renderer, create_raymond_lights

        self.context = pyrender.OffscreenRenderer(viewport_width=width, viewport_height=height)
        self.timing_seconds = defaultdict(float)
        self.width, self.height = width, height
        self._closed = False
        self._gl_owner = threading.get_ident()
        owner = self

        class BorrowedContext:
            def render(self, *args, **kwargs):
                return owner.context.render(*args, **kwargs)

            def delete(self):
                pass

        def reuse_context(*, viewport_height, viewport_width, **kwargs):
            if (viewport_width, viewport_height) != (owner.width, owner.height):
                raise ValueError("official mesh viewport changed")
            return BorrowedContext()

        proxy = types.SimpleNamespace(**vars(pyrender))
        proxy.OffscreenRenderer = reuse_context
        globals_copy = dict(Renderer.__call__.__globals__, pyrender=proxy)
        self.call = types.FunctionType(Renderer.__call__.__code__, globals_copy,
                                       Renderer.__call__.__name__, Renderer.__call__.__defaults__,
                                       Renderer.__call__.__closure__)
        self.call.__kwdefaults__ = Renderer.__call__.__kwdefaults__
        self.renderer_class = Renderer
        self.pyrender, self.trimesh = pyrender, trimesh
        self.create_raymond_lights = create_raymond_lights
        self.renderer_sha256 = sha256_file(repo / "sam_3d_body" / "visualization" / "renderer.py")

    @staticmethod
    def _composite(image: np.ndarray, rgba: np.ndarray) -> np.ndarray:
        """Official alpha equation, restricted to the nonempty mesh rectangle."""
        output = image.copy()
        alpha = rgba[:, :, 3]
        occupied_rows = np.flatnonzero(alpha.any(axis=1))
        if not len(occupied_rows):
            return output
        occupied_columns = np.flatnonzero(alpha.any(axis=0))
        y0, y1 = occupied_rows[0], occupied_rows[-1] + 1
        x0, x1 = occupied_columns[0], occupied_columns[-1] + 1
        mask = alpha[y0:y1, x0:x1, None].astype(np.float32) / 255.
        color = rgba[y0:y1, x0:x1, :3].astype(np.float32)
        background = image[y0:y1, x0:x1].astype(np.float32)
        output[y0:y1, x0:x1] = np.clip(color * mask + background * (1 - mask), 0, 255).astype(np.uint8)
        return output

    def prepare_pair(self, image: np.ndarray, outputs: list[tuple]):
        """Pure CPU construction; each call owns its scene and dynamic normals.

        The arithmetic/process/material/light order matches the serial path.
        No OffscreenRenderer or GL operation may be introduced in this method.
        """
        from rallymate_vision.mesh_prepare import PreparedMeshPair
        if self._closed:
            raise RuntimeError("mesh renderer is closed")
        white = np.full_like(image, 255)
        if not outputs:
            return PreparedMeshPair(image=image, white=white)
        p, t = self.pyrender, self.trimesh
        vertices, faces, offset = [], [], 0
        for v, f, camera, _ in outputs:
            vertices.append(v + camera)
            faces.append(f + offset)
            offset += len(v)
        merged = np.concatenate(vertices)
        anchor = (merged.max(axis=0) + merged.min(axis=0)) / 2
        focal = outputs[0][3]
        if any(not np.isclose(item[3], focal, rtol=1e-6) for item in outputs):
            raise ValueError("different cameras in one SAM mesh frame")
        mesh = t.Trimesh((merged - anchor).copy(), np.concatenate(faces))
        flip = t.transformations.rotation_matrix(np.radians(180), [1, 0, 0])
        mesh.apply_transform(flip)
        camera_pose = np.eye(4)
        camera_pose[:3, 3] = anchor * [-1., 1., 1.]
        material = p.MetallicRoughnessMaterial(
            metallicFactor=0., alphaMode="OPAQUE",
            baseColorFactor=(LIGHT_BLUE[2], LIGHT_BLUE[1], LIGHT_BLUE[0], 1.),
        )
        mesh = p.Mesh.from_trimesh(mesh, material=material)
        scene = p.Scene(bg_color=[1., 1., 1., 0.], ambient_light=(.3, .3, .3))
        mesh_node = scene.add(mesh, "mesh")
        camera = p.IntrinsicsCamera(fx=focal, fy=focal, cx=self.width / 2.,
                                    cy=self.height / 2., zfar=1e12)
        camera_node = scene.add(camera, pose=camera_pose)
        for light in self.create_raymond_lights():
            scene.add_node(light)
        return PreparedMeshPair(image=image, white=white, scene=scene, mesh_node=mesh_node,
            camera_node=camera_node, camera_pose=camera_pose, flip=flip, merged=merged, focal=focal)

    def render_prepared_pair(self, prepared) -> tuple[np.ndarray, np.ndarray]:
        """Only the GL owner renders, changes scene poses and composites BGR."""
        if threading.get_ident() != self._gl_owner:
            raise RuntimeError("Mesh GL rendering must retain its creating thread")
        if self._closed:
            raise RuntimeError("mesh renderer is closed")
        image, white = prepared.image, prepared.white
        if prepared.scene is None:
            return image.copy(), white
        p, t = self.pyrender, self.trimesh
        scene, mesh_node, camera_node = prepared.scene, prepared.mesh_node, prepared.camera_node
        camera_pose, flip, merged, focal = prepared.camera_pose, prepared.flip, prepared.merged, prepared.focal
        started = time.perf_counter()
        rgba, _ = self.context.render(scene, flags=p.RenderFlags.RGBA)
        self.timing_seconds["front_gl_render_and_readback"] += time.perf_counter() - started
        started = time.perf_counter()
        front = self._composite(image, rgba)
        self.timing_seconds["front_composite"] += time.perf_counter() - started
        rotation = t.transformations.rotation_matrix(np.radians(90), [0, 1, 0])
        scene.set_pose(mesh_node, flip @ rotation @ np.linalg.inv(flip))
        # Center only the alternative display camera, so a player near a
        # source-image edge is still fully visible from the side.
        span = np.ptp(merged, axis=0)
        side_distance = span[0] / 2 + focal * max(span[1] / self.height, span[2] / self.width) * 1.15
        camera_pose[:3, 3] = [0., 0., max(float(side_distance), .01)]
        scene.set_pose(camera_node, camera_pose)
        started = time.perf_counter()
        rgba, _ = self.context.render(scene, flags=p.RenderFlags.RGBA)
        self.timing_seconds["side_gl_render_and_readback"] += time.perf_counter() - started
        started = time.perf_counter()
        side = self._composite(white, rgba)
        self.timing_seconds["side_composite"] += time.perf_counter() - started
        return front, side

    def render_pair(self, image: np.ndarray, outputs: list[tuple]) -> tuple[np.ndarray, np.ndarray]:
        """Compatible serial entry; both paths use the same GL implementation."""
        if threading.get_ident() != self._gl_owner:
            raise RuntimeError("Mesh GL rendering must retain its creating thread")
        started = time.perf_counter()
        prepared = self.prepare_pair(image, outputs)
        self.timing_seconds["mesh_and_scene"] += time.perf_counter() - started
        return self.render_prepared_pair(prepared)

    def render(self, image: np.ndarray, outputs: list[tuple], *, side_view: bool = False) -> np.ndarray:
        if threading.get_ident() != self._gl_owner:
            raise RuntimeError("Mesh GL rendering must retain its creating thread")
        if self._closed:
            raise RuntimeError("mesh renderer is closed")
        if not outputs:
            return np.full_like(image, 255) if side_view else image.copy()
        # The official demo's multi-person composition puts each true mesh in
        # camera coordinates, then renders them together with a depth buffer.
        vertices, faces, offset = [], [], 0
        for v, f, camera, _ in outputs:
            vertices.append(v + camera)
            faces.append(f + offset)
            offset += len(v)
        merged = np.concatenate(vertices)
        anchor = (merged.max(axis=0) + merged.min(axis=0)) / 2
        # Every subject from one source image uses the same official intrinsics.
        focal = outputs[0][3]
        if any(not np.isclose(item[3], focal, rtol=1e-6) for item in outputs):
            raise ValueError("different cameras in one SAM mesh frame")
        renderer = self.renderer_class(focal_length=focal, faces=np.concatenate(faces))
        background = np.full_like(image, 255) if side_view else image
        rendered = self.call(renderer, merged - anchor, anchor, background,
                             mesh_base_color=LIGHT_BLUE, scene_bg_color=(1, 1, 1),
                             side_view=side_view)
        return np.clip(rendered * 255, 0, 255).astype(np.uint8)

    def close(self):
        if threading.get_ident() != self._gl_owner:
            raise RuntimeError("Mesh GL cleanup must retain its creating thread")
        if not self._closed:
            self.context.delete()
            self._closed = True


def _read_ledger(path: Path, output_dir: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if len(line) > 16384 or len(rows) >= 100000:
                raise ValueError("mesh ledger exceeds bounds")
            row = json.loads(line)
            for key in ("path", "faces_path"):
                _contained(output_dir / row["mesh_artifact"][key], output_dir / "sam3d-mesh-data")
            rows.append(row)
    if not rows:
        raise ValueError("no actual SAM mesh geometry was captured")
    return rows


def _verified_geometry_bytes(path: Path, digest: str, label: str, timings) -> bytes:
    """Read once; hashing and decoding must observe the identical byte snapshot."""
    started = time.perf_counter()
    with path.open("rb") as stream:
        # A fixed 8 MiB read allocates that full buffer for every ~222 KiB
        # native NPZ on Windows. Size the bounded read from the same open fd;
        # an extra byte detects growth, while a short read detects truncation.
        size = os.fstat(stream.fileno()).st_size
        if size > MAX_GEOMETRY_FILE_BYTES:
            raise ValueError(f"mesh {label} exceeds native geometry file bound")
        payload = stream.read(size + 1)
        if len(payload) != size:
            raise ValueError(f"mesh {label} size changed during verified read")
    timings["geometry_substage_file_read"] += time.perf_counter() - started
    if len(payload) > MAX_GEOMETRY_FILE_BYTES:
        raise ValueError(f"mesh {label} exceeds native geometry file bound")
    started = time.perf_counter()
    actual = hashlib.sha256(payload).hexdigest().upper()
    timings["geometry_substage_sha256"] += time.perf_counter() - started
    if actual != digest:
        raise ValueError(f"mesh {label} changed after inference")
    return payload


def _load_verified_geometry(output_dir: Path, artifact: dict, faces_cache: dict, timings) -> tuple:
    """Keep all geometry/shape checks; avoid hash-read then reopening each NPZ."""
    root = output_dir / "sam3d-mesh-data"
    path = _contained(output_dir / artifact["path"], root)
    topology = _contained(output_dir / artifact["faces_path"], root)
    payload = _verified_geometry_bytes(path, artifact["sha256"], "geometry", timings)
    topology_sha = artifact["faces_sha256"]
    if topology_sha not in faces_cache:
        topology_bytes = _verified_geometry_bytes(topology, topology_sha, "topology", timings)
        started = time.perf_counter()
        faces_cache[topology_sha] = np.load(io.BytesIO(topology_bytes), allow_pickle=False)
        timings["geometry_substage_array_decode"] += time.perf_counter() - started
    started = time.perf_counter()
    with np.load(io.BytesIO(payload), allow_pickle=False) as data:
        output = {"pred_vertices": data["vertices"], "pred_cam_t": data["camera"],
                  "focal_length": data["focal"]}
    timings["geometry_substage_array_decode"] += time.perf_counter() - started
    started = time.perf_counter()
    arrays = _arrays(output, faces_cache[topology_sha])
    timings["geometry_substage_array_validation"] += time.perf_counter() - started
    return arrays


def _open_video_outputs(av, destinations, *, width, height, rate, time_base,
                        preference=None, thread_count=None):
    """Open both real encoders before frames; an unavailable auto NVENC falls back.

    Codec registration alone does not mean the driver can open an NVENC session.
    An explicit h264_nvenc request is strict, useful for acceptance measurements.
    """
    preference = preference or os.environ.get("RALLYMATE_SAM3D_MESH_ENCODER", "auto")
    if preference not in {"auto", "libx264", "h264_nvenc"}:
        raise ValueError("unsupported SAM mesh video encoder")
    if thread_count is None:
        thread_count = int(os.environ.get("RALLYMATE_SAM3D_MESH_ENCODER_THREADS", "2"))
    if not 1 <= thread_count <= 8:
        raise ValueError("mesh encoder threads must be between 1 and 8")
    codecs = ("h264_nvenc", "libx264") if preference == "auto" else (preference,)
    fallback_reason = None
    for codec in codecs:
        outputs = []
        try:
            for destination in destinations:
                container = av.open(str(destination), "w", options={"movflags": "+faststart"})
                # Register ownership before any operation which can fail.
                outputs.append((container, None))
                stream = container.add_stream(codec, rate=rate)
                outputs[-1] = (container, stream)
                stream.width, stream.height = width, height
                stream.pix_fmt = "yuv420p"
                stream.time_base = time_base
                stream.codec_context.time_base = time_base
                stream.codec_context.max_b_frames = 0
                stream.codec_context.thread_count = thread_count
                stream.options = ({"preset": "p4", "tune": "hq", "rc": "vbr", "cq": "19", "b": "0",
                                   "rc-lookahead": "0", "bf": "0"}
                                  if codec == "h264_nvenc" else {"preset": "veryfast", "crf": "21"})
                stream.codec_context.open()
            return outputs, {"codec": codec, "requested": preference,
                             "thread_count": thread_count, "fallback_reason": fallback_reason}
        except Exception as exc:
            for container, _ in outputs:
                try:
                    container.close()
                except Exception:
                    pass
            if preference != "auto" or codec != "h264_nvenc":
                raise
            fallback_reason = type(exc).__name__
    raise RuntimeError("no SAM mesh encoder was opened")


def render_mesh_video(source: Path, output_dir: Path, runtime_dir: Path, *,
                      first_frame: int, last_frame: int, stride: int, source_sha256: str,
                      model: dict, max_height: int = 720,
                      check_cancelled: Callable[[], None] | None = None) -> dict:
    """Encode actual source PTS with PyAV; missing people remain genuinely empty."""
    import av
    import cv2
    from rallymate_vision.mesh_prepare import (
        PreparedMeshFrames, MeshEncodingQueue, prepare_workers, encode_overlap_enabled,
    )
    from rallymate_vision.owned_subprocess import ProcessTreeCleanupError
    if _UNJOINED_PREPARATIONS:
        raise ProcessTreeCleanupError("Previous mesh CPU preparation is unresolved; process exit is required")
    cpu_workers = prepare_workers(os.environ)
    encoding_overlap = encode_overlap_enabled(os.environ)
    started = time.perf_counter()
    timings = defaultdict(float)
    output_dir = Path(output_dir).resolve()
    rows = _read_ledger(output_dir / LEDGER_NAME, output_dir)
    identity_started = time.perf_counter()
    if sha256_file(source).lower() != source_sha256.lower():
        raise ValueError("mesh replay source changed")
    timings["source_identity_validation_before"] += time.perf_counter() - identity_started
    by_frame = defaultdict(list)
    for row in rows:
        if row["video_sha256"].lower() != source_sha256.lower():
            raise ValueError("mesh/source video mismatch")
        by_frame[row["frame_index"]].append(row)
    faces_cache = {}
    renderer = None
    preparation = None
    encoding = None
    containers = []
    pending = [output_dir / f".{name}.partial.mp4" for name in (VIDEO_NAME, SIDE_VIDEO_NAME)]
    first_pts = None
    source_first_pts = None
    count = mesh_frames = subjects = 0
    last_rendered_frame = None
    preview_source_frame = None
    timestamps = []
    encoder = None
    time_base = width = height = w0 = h0 = None

    def verified_frames():
        nonlocal renderer, containers, encoder, first_pts, source_first_pts, time_base, width, height, w0, h0
        open_started = time.perf_counter()
        with av.open(str(source)) as source_container:
            timings["source_open"] += time.perf_counter() - open_started
            stream = source_container.streams.video[0]
            time_base = Fraction(stream.time_base)

            def timed_decode():
                iterator = iter(source_container.decode(stream))
                index = 0
                while True:
                    decode_started = time.perf_counter()
                    try:
                        decoded = next(iterator)
                    except StopIteration:
                        break
                    key = ("source_prefix_decode" if index < first_frame else
                           "source_post_interval_decode" if index > last_frame else "selected_source_decode")
                    timings[key] += time.perf_counter() - decode_started
                    yield index, decoded
                    index += 1

            for index, decoded in timed_decode():
                if check_cancelled:
                    check_cancelled()
                if decoded.pts is None:
                    raise ValueError("mesh replay requires real decoder PTS")
                pts = Fraction(decoded.pts) * decoded.time_base
                if source_first_pts is None:
                    source_first_pts = pts
                if index > last_frame:
                    break
                if index < first_frame or (index - first_frame) % stride:
                    continue
                if first_pts is None:
                    first_pts = pts
                    h0, w0 = decoded.height, decoded.width
                    scale = min(1., max_height / h0)
                    width, height = max(2, round(w0 * scale / 2) * 2), max(2, round(h0 * scale / 2) * 2)
                    # Keep aspect ratio uniform; a possible subpixel center
                    # difference comes only from mandatory even H.264 dimensions.
                    scale = width / w0
                    setup_started = time.perf_counter()
                    renderer = OfficialMeshRenderer(width, height, runtime_dir)
                    timings["renderer_setup"] += time.perf_counter() - setup_started
                    encoder_started = time.perf_counter()
                    containers, encoder = _open_video_outputs(
                        av, pending, width=width, height=height,
                        rate=stream.average_rate or 30, time_base=time_base)
                    timings["encoder_setup"] += time.perf_counter() - encoder_started
                original = decoded.to_ndarray(format="bgr24")
                image = cv2.resize(original, (width, height), interpolation=cv2.INTER_AREA)
                outputs = []
                geometry_started = time.perf_counter()
                for row in by_frame.pop(index, []):
                    expected = Fraction(row["pts"] * row["time_base_num"], row["time_base_den"])
                    if pts != expected or row["image_size"] != [w0, h0]:
                        raise ValueError("mesh source frame/time/dimensions changed")
                    artifact = row["mesh_artifact"]
                    arrays = _load_verified_geometry(output_dir, artifact, faces_cache, timings)
                    # The main thread alone verifies/caches geometry. CPU scene
                    # jobs receive read-only arrays; shared topology never mutates.
                    for array in arrays[:3]:
                        array.setflags(write=False)
                    outputs.append((*arrays[:3], arrays[3] * scale))
                timings["geometry_read_and_validation"] += time.perf_counter() - geometry_started
                relative_pts = (pts - first_pts) / time_base
                if relative_pts.denominator != 1:
                    raise ValueError("mesh replay PTS cannot be represented exactly")
                image.setflags(write=False)
                yield {"index": index, "image": image, "outputs": outputs, "relative_pts": int(relative_pts)}

    def encode_pair(images, relative_pts):
        # Exactly one codec owner writes both views chronologically. The GL
        # owner can prepare/render the next frame while this native work runs.
        for image_out, (container, out_stream) in zip(images, containers):
            encoded = av.VideoFrame.from_ndarray(image_out, format="bgr24")
            encoded.pts, encoded.time_base = relative_pts, time_base
            for packet in out_stream.encode(encoded):
                container.mux(packet)

    try:
        encoding = MeshEncodingQueue(encode_pair, enabled=encoding_overlap,
            check_cancelled=check_cancelled or (lambda: None))
        preparation = PreparedMeshFrames(verified_frames(),
            lambda image, outputs: renderer.prepare_pair(image, outputs), workers=cpu_workers,
            check_cancelled=check_cancelled or (lambda: None))
        for work, prepared in preparation:
            index, outputs, relative_pts = work["index"], work["outputs"], work["relative_pts"]
            subjects += len(outputs)
            mesh_frames += bool(outputs)
            overlay, side = renderer.render_prepared_pair(prepared)
            if count == 0 or outputs and preview_source_frame is None:
                cv2.imwrite(str(output_dir / PREVIEW_NAME), overlay)
                if outputs:
                    preview_source_frame = index
            timestamps.append(relative_pts)
            encoding.submit((overlay, side), relative_pts)
            count += 1
            last_rendered_frame = index
        preparation.close()
        preparation_receipt = preparation.receipt
        encoding.finish()
        encoding_receipt = encoding.receipt
        timings["video_encode"] = encoding_receipt["encode_wall_seconds"]
        timings["video_encode_foreground_wait"] = encoding_receipt["foreground_backpressure_seconds"]
        timings["mesh_and_scene"] = (preparation_receipt["prepare_worker_wall_seconds_sum"]
                                    + preparation_receipt["synchronous_prepare_wall_seconds"])
        timings["mesh_prepare_foreground_wait"] = preparation_receipt["foreground_wait_seconds"]
        if by_frame:
            raise ValueError("mesh ledger contains frames outside the rendered source interval")
        if not count or not mesh_frames:
            raise ValueError("mesh replay contains no inferred human surface")
        identity_started = time.perf_counter()
        if sha256_file(source).lower() != source_sha256.lower():
            raise ValueError("mesh replay source changed during rendering")
        timings["source_identity_validation_after"] += time.perf_counter() - identity_started
        flush_started = time.perf_counter()
        for container, out_stream in containers:
            for packet in out_stream.encode():
                container.mux(packet)
            container.close()
        containers.clear()
        timings["encoder_flush"] += time.perf_counter() - flush_started
        # Decode both deliverables; verify full PTS sequence, not just duration.
        validation_started = time.perf_counter()
        for path in pending:
            with av.open(str(path)) as check:
                actual = [Fraction(frame.pts) * frame.time_base for frame in check.decode(video=0)]
            expected = [value * time_base for value in timestamps]
            if actual != expected:
                raise ValueError("encoded mesh video did not preserve every source presentation time")
        timings["encoded_video_pts_validation"] += time.perf_counter() - validation_started
        for path, name in zip(pending, (VIDEO_NAME, SIDE_VIDEO_NAME)):
            path.replace(output_dir / name)
        manifest = {
            "version": VERSION, "status": "ready",
            "renderer": "official-sam3d-pyrender-persistent-context",
            "renderer_sha256": renderer.renderer_sha256,
            "renderer_adapter_sha256": sha256_file(Path(__file__)),
            "renderer_optimization": "one_mesh_scene_two_views_float32_occupied_rectangle_composite",
            "video_encoder": encoder,
            "geometry": "official_estimator_pred_vertices",
            "appearance": "official_LIGHT_BLUE_raymond_lighting_opaque_surface",
            "side_view_degrees": 90, "model": model,
            "side_view_camera": "centered_and_fitted_display_camera_only_not_measurement_coordinates",
            "source_video_sha256": source_sha256, "source_frame_start": first_frame,
            "source_frame_end": last_rendered_frame + 1, "source_frame_end_semantics": "exclusive",
            "frame_stride": stride,
            "frame_count": count, "mesh_frame_count": mesh_frames, "subject_count": subjects,
            "source_timestamp_origin_ms": float(first_pts * 1000),
            "source_relative_origin_ms": float((first_pts - source_first_pts) * 1000),
            "timing_preserved": True, "timebase": "source_pts_relative_to_first_selected_frame",
            "width": width, "height": height, "source_width": w0, "source_height": h0,
            "render_seconds": round(time.perf_counter() - started, 6),
            "mesh_prepare": preparation_receipt,
            "mesh_encode": encoding_receipt,
            "stage_seconds": {key: round(value, 6) for key, value in
                              {**timings, **renderer.timing_seconds}.items()},
            "stage_timing_semantics": "geometry_substage_* are included in geometry_read_and_validation; mesh_and_scene is joined CPU prepare wall sum; video_encode is joined codec worker wall when overlap enabled; prepare/encode can overlap main-thread decode/GL; foreground waits measure only consumer blocking; do not add overlapping/child stages as pipeline wall",
            "ledger": LEDGER_NAME, "ledger_sha256": sha256_file(output_dir / LEDGER_NAME),
            "video": VIDEO_NAME, "side_video": SIDE_VIDEO_NAME, "preview_image": PREVIEW_NAME,
            "preview_source_frame": preview_source_frame,
            "manifest": MANIFEST_NAME,
            "files": {name: {"sha256": sha256_file(output_dir / name),
                             "bytes": (output_dir / name).stat().st_size}
                      for name in (VIDEO_NAME, SIDE_VIDEO_NAME, PREVIEW_NAME)},
        }
        (output_dir / MANIFEST_NAME).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        return manifest
    finally:
        # A failed join deliberately prevents GL/resource teardown. The owning
        # process must exit or be stopped before its GPU slot can be reused.
        cleanup_error = None
        for resource in (preparation, encoding):
            if resource is not None:
                try:
                    resource.close()
                except ProcessTreeCleanupError as error:
                    cleanup_error = cleanup_error or error
        if cleanup_error is not None:
            _UNJOINED_PREPARATIONS.append((renderer, preparation, encoding, containers))
            raise cleanup_error
        for container, _ in containers:
            container.close()
        if renderer is not None:
            renderer.close()
        for path in pending:
            path.unlink(missing_ok=True)


def render_mesh_subprocess(source: Path, output_dir: Path, runtime_dir: Path, *,
                           first_frame: int, last_frame: int, stride: int, source_sha256: str,
                           model: dict, check_cancelled: Callable[[], None]) -> dict:
    """Use the installed SAM Python/OpenGL dependencies, with cancellable polling."""
    from rallymate_vision.owned_subprocess import OwnedProcess
    config = {"source": str(source.resolve()), "output_dir": str(output_dir.resolve()),
              "runtime_dir": str(runtime_dir.resolve()), "first_frame": first_frame,
              "last_frame": last_frame, "stride": stride, "source_sha256": source_sha256,
              "model": model}
    config_path = output_dir / "sam3d-mesh-render-request.json"
    config_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    python = os.environ.get("RALLYMATE_SAM3D_PYTHON") or sys.executable
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(filter(None, (str(Path(__file__).resolve().parents[1]), env.get("PYTHONPATH"))))
    with (output_dir / "sam3d-mesh-render.log").open("w", encoding="utf-8") as log:
        process = OwnedProcess([python, "-u", "-m", "rallymate_vision.sam3d_mesh",
                                    "--render-request", str(config_path)],
                                   stdout=log, stderr=log, env=env)
        try:
            while process.poll() is None:
                check_cancelled()
                time.sleep(.1)
            if process.returncode:
                raise RuntimeError("official SAM mesh rendering failed; see sam3d-mesh-render.log")
        except BaseException:
            process.stop()
            raise
        finally:
            process.close()
    return json.loads((output_dir / MANIFEST_NAME).read_text(encoding="utf-8"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--render-request", type=Path, required=True)
    args = parser.parse_args()
    request = json.loads(args.render_request.read_text(encoding="utf-8"))
    for key in ("source", "output_dir", "runtime_dir"):
        request[key] = Path(request[key])
    render_mesh_video(**request)
