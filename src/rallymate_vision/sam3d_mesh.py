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
        )
        archived.append({**{key: record[key] for key in binding_keys if key in record},
                         "mesh_artifact": artifact})
    return archived


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

    def render_pair(self, image: np.ndarray, outputs: list[tuple]) -> tuple[np.ndarray, np.ndarray]:
        """Reuse one official-material mesh/scene for front and rotated views.

        The first implementation called the official wrapper twice, rebuilding
        normals and GL buffers and converting two full frames to float64. This
        keeps its same opaque material, intrinsics, lights and shader, changes
        only the mesh node/camera between views, and composites the occupied ROI.
        """
        if self._closed:
            raise RuntimeError("mesh renderer is closed")
        white = np.full_like(image, 255)
        if not outputs:
            return image.copy(), white
        started = time.perf_counter()
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
        self.timing_seconds["mesh_and_scene"] += time.perf_counter() - started
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

    def render(self, image: np.ndarray, outputs: list[tuple], *, side_view: bool = False) -> np.ndarray:
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
    started = time.perf_counter()
    output_dir = Path(output_dir).resolve()
    rows = _read_ledger(output_dir / LEDGER_NAME, output_dir)
    if sha256_file(source).lower() != source_sha256.lower():
        raise ValueError("mesh replay source changed")
    by_frame = defaultdict(list)
    for row in rows:
        if row["video_sha256"].lower() != source_sha256.lower():
            raise ValueError("mesh/source video mismatch")
        by_frame[row["frame_index"]].append(row)
    faces_cache = {}
    renderer = None
    containers = []
    pending = [output_dir / f".{name}.partial.mp4" for name in (VIDEO_NAME, SIDE_VIDEO_NAME)]
    first_pts = None
    source_first_pts = None
    count = mesh_frames = subjects = 0
    last_rendered_frame = None
    preview_source_frame = None
    timestamps = []
    timings = defaultdict(float)
    encoder = None
    try:
        with av.open(str(source)) as source_container:
            stream = source_container.streams.video[0]
            time_base = Fraction(stream.time_base)
            for index, decoded in enumerate(source_container.decode(stream)):
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
                    path = output_dir / artifact["path"]
                    topology = output_dir / artifact["faces_path"]
                    if sha256_file(path) != artifact["sha256"]:
                        raise ValueError("mesh geometry changed after inference")
                    if artifact["faces_sha256"] not in faces_cache:
                        if sha256_file(topology) != artifact["faces_sha256"]:
                            raise ValueError("mesh topology changed after inference")
                        faces_cache[artifact["faces_sha256"]] = np.load(topology, allow_pickle=False)
                    with np.load(path, allow_pickle=False) as data:
                        arrays = _arrays({"pred_vertices": data["vertices"], "pred_cam_t": data["camera"],
                                          "focal_length": data["focal"]}, faces_cache[artifact["faces_sha256"]])
                    outputs.append((*arrays[:3], arrays[3] * scale))
                timings["geometry_read_and_validation"] += time.perf_counter() - geometry_started
                subjects += len(outputs)
                mesh_frames += bool(outputs)
                overlay, side = renderer.render_pair(image, outputs)
                if count == 0 or outputs and preview_source_frame is None:
                    cv2.imwrite(str(output_dir / PREVIEW_NAME), overlay)
                    if outputs:
                        preview_source_frame = index
                relative_pts = (pts - first_pts) / time_base
                if relative_pts.denominator != 1:
                    raise ValueError("mesh replay PTS cannot be represented exactly")
                timestamps.append(int(relative_pts))
                encode_started = time.perf_counter()
                for image_out, (container, out_stream) in zip((overlay, side), containers):
                    encoded = av.VideoFrame.from_ndarray(image_out, format="bgr24")
                    encoded.pts, encoded.time_base = int(relative_pts), time_base
                    for packet in out_stream.encode(encoded):
                        container.mux(packet)
                timings["video_encode"] += time.perf_counter() - encode_started
                count += 1
                last_rendered_frame = index
        if by_frame:
            raise ValueError("mesh ledger contains frames outside the rendered source interval")
        if not count or not mesh_frames:
            raise ValueError("mesh replay contains no inferred human surface")
        if sha256_file(source).lower() != source_sha256.lower():
            raise ValueError("mesh replay source changed during rendering")
        for container, out_stream in containers:
            for packet in out_stream.encode():
                container.mux(packet)
            container.close()
        containers.clear()
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
            "stage_seconds": {key: round(value, 6) for key, value in
                              {**timings, **renderer.timing_seconds}.items()},
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
