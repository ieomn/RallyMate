"""Durable encoded mesh segments, resumed without repeating completed GL work."""
from __future__ import annotations

from collections import defaultdict
from fractions import Fraction
import json
from itertools import zip_longest
import math
import os
from pathlib import Path
import shutil
import time

from rallymate_vision.mesh_render_task import atomic_json
from rallymate_vision.pose.metadata import sha256_file

CHECKPOINT_NAME = "sam3d-mesh-checkpoint.json"
RESULT_NAME = "sam3d-mesh-render-result.json"
SEGMENTS_NAME = ".sam3d-mesh-segments"
CHECKPOINT_VERSION = "sam3d-mesh-segments-v1"


class SourceFrames:
    """One sequential decoder per turn; resume at a verified last-frame PTS."""
    def __init__(self, source, previous=None, check_cancelled=lambda: None):
        import av
        self.source = Path(source)
        self.source_stat = self.source.stat()
        self.container = av.open(str(source))
        self.stream = self.container.streams.video[0]
        self.next_index = self.decoded_frames = self.seek_prefix_frames = 0
        self.source_first_pts = None
        self.renderer = None
        self.renderer_binding = None
        self.faces_cache = {}
        self.check_cancelled = check_cancelled
        try:
            if previous is not None:
                anchor = Fraction(*previous["source_last_pts"])
                ticks = anchor / self.stream.time_base
                if ticks.denominator != 1:
                    raise ValueError("mesh resume anchor cannot be represented exactly")
                self.container.seek(int(ticks), stream=self.stream, backward=True, any_frame=False)
                self.iterator = iter(self.container.decode(self.stream))
                for frame in self.iterator:
                    self.check_cancelled()
                    self.decoded_frames += 1
                    self.seek_prefix_frames += 1
                    pts = Fraction(frame.pts)*frame.time_base
                    if pts == anchor:
                        break
                    if pts > anchor:
                        raise ValueError("mesh resume anchor missing from source")
                else:
                    raise ValueError("mesh resume anchor missing from source")
                self.next_index = previous["source_frame_end"]
                self.source_first_pts = Fraction(*previous["source_stream_origin"])
            else:
                self.iterator = iter(self.container.decode(self.stream))
        except BaseException:
            self.container.close()
            raise

    def frames(self, first, last, timings):
        while self.next_index <= last:
            self.check_cancelled()
            started = time.perf_counter()
            try:
                frame = next(self.iterator)
            except StopIteration:
                return
            timings["source_prefix_decode" if self.next_index < first else "selected_source_decode"] += time.perf_counter()-started
            self.decoded_frames += 1
            index = self.next_index
            self.next_index += 1
            if frame.pts is None:
                raise ValueError("mesh replay requires real decoder PTS")
            if self.source_first_pts is None:
                self.source_first_pts = Fraction(frame.pts)*frame.time_base
            yield index, frame

    def close(self):
        self.container.close()
        if self.renderer is not None:
            from rallymate_vision import sam3d_mesh as mesh
            # A preparation/encoding join failure retains its GL owner until
            # the isolated process exits; do not invalidate borrowed scenes.
            if not any(item[0] is self.renderer for item in mesh._UNJOINED_PREPARATIONS):
                self.renderer.close()
                self.renderer = None

    def get_renderer(self, width, height, runtime, factory):
        binding = (width, height, str(Path(runtime).resolve()))
        if self.renderer is None:
            self.renderer = factory(width, height, runtime)
            self.renderer_binding = binding
        elif binding != self.renderer_binding:
            raise ValueError("mesh renderer dimensions or runtime changed across segments")
        return self.renderer

    def verify_source(self):
        current = self.source.stat()
        if (current.st_size, current.st_mtime_ns, current.st_ino) != (
                self.source_stat.st_size, self.source_stat.st_mtime_ns, self.source_stat.st_ino):
            raise ValueError("mesh replay source changed during rendering")


def _aggregate_receipts(receipts):
    """Keep final manifests bounded; full receipts remain with each segment."""
    first = receipts[0]
    result = {key: value for key, value in first.items() if key != "per_worker"}
    for key, value in first.items():
        if type(value) in (int, float):
            values = [item[key] for item in receipts]
            result[key] = max(values) if key.startswith(("peak_", "max_")) or key == "workers" else sum(values)
    result["joined"] = all(item["joined"] for item in receipts)
    result["segment_count"] = len(receipts)
    result["aggregation"] = "counters_and_wall_sums; maximum_retained_peaks_and_bounds"
    return result


def _read_json(path):
    if path.resolve() != path.absolute() or path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError("invalid mesh checkpoint path or size")
    return json.loads(path.read_text(encoding="utf-8"))


def _segment_frames():
    raw = os.environ.get("RALLYMATE_SAM3D_MESH_SEGMENT_FRAMES", "60")
    if not raw.isdecimal() or not 1 <= int(raw) <= 1800:
        raise ValueError("Mesh segment frames must be an integer from 1 to 1800")
    return int(raw)


def _identity(source, output, runtime, first, last, stride, source_sha, model, height, frames):
    from rallymate_vision import sam3d_mesh as mesh
    return {"source_video_sha256": source_sha, "ledger_sha256": sha256_file(output / mesh.LEDGER_NAME),
        "source": str(source.resolve()), "runtime": str(runtime.resolve()), "model": model,
        "first_frame": first, "last_frame": last, "stride": stride, "max_height": height,
        "segment_frames": frames, "renderer_adapter_sha256": sha256_file(Path(mesh.__file__)),
        "segment_adapter_sha256": sha256_file(Path(__file__)),
        "renderer_source_sha256": sha256_file(runtime / "repo" / "sam_3d_body" / "visualization" / "renderer.py")
            if (runtime / "repo" / "sam_3d_body" / "visualization" / "renderer.py").is_file() else None,
        "appearance": list(mesh.LIGHT_BLUE),
        "encoder": {key: os.environ.get(key) for key in
                    ("RALLYMATE_SAM3D_MESH_ENCODER", "RALLYMATE_SAM3D_MESH_ENCODER_THREADS")}}


def _verified_segments(output, checkpoint, identity):
    from rallymate_vision import sam3d_mesh as mesh
    if checkpoint.get("version") != CHECKPOINT_VERSION or checkpoint.get("identity") != identity:
        raise ValueError("mesh checkpoint identity changed")
    segments = checkpoint.get("segments")
    if not isinstance(segments, list):
        raise ValueError("invalid mesh checkpoint segments")
    expected_first = identity["first_frame"]
    for number, entry in enumerate(segments):
        directory = output / SEGMENTS_NAME / f"{number:08d}"
        if directory.resolve() != directory.absolute():
            raise ValueError("mesh segment cannot be a symlink")
        manifest_path = directory / mesh.MANIFEST_NAME
        if sha256_file(manifest_path) != entry["manifest_sha256"]:
            raise ValueError("mesh segment manifest changed")
        manifest = _read_json(manifest_path)
        expected_count = min(identity["segment_frames"],
            (identity["last_frame"] - expected_first) // identity["stride"] + 1)
        if (manifest.get("status") != "ready" or manifest.get("source_frame_start") != expected_first
                or manifest.get("frame_count") != expected_count or expected_count < 1
                or manifest.get("source_frame_end") != expected_first + (expected_count-1)*identity["stride"] + 1
                or manifest.get("frame_stride") != identity["stride"]
                or manifest.get("source_video_sha256") != identity["source_video_sha256"]
                or manifest.get("ledger_sha256") != identity["ledger_sha256"]):
            raise ValueError("mesh checkpoint segment selection changed")
        for name in (mesh.VIDEO_NAME, mesh.SIDE_VIDEO_NAME, mesh.PREVIEW_NAME):
            path = directory / name
            if path.resolve() != path.absolute() or sha256_file(path) != manifest["files"][name]["sha256"]:
                raise ValueError("mesh checkpoint media changed")
        expected_first += expected_count * identity["stride"]
    return segments, expected_first


def _completed_result(output, checkpoint, identity):
    """Verify the completed media before treating removed segments as reusable."""
    from rallymate_vision import sam3d_mesh as mesh
    if checkpoint.get("version") != CHECKPOINT_VERSION or checkpoint.get("identity") != identity:
        raise ValueError("mesh checkpoint identity changed")
    result_path = output / RESULT_NAME
    if result_path.resolve() != result_path.absolute() or sha256_file(result_path) != checkpoint.get("result_sha256"):
        raise ValueError("completed mesh result changed")
    result = _read_json(result_path)
    if (result.get("status") != "ready" or result.get("source_video_sha256") != identity["source_video_sha256"]
            or result.get("ledger_sha256") != identity["ledger_sha256"]):
        raise ValueError("completed mesh identity changed")
    published = _read_json(output / mesh.MANIFEST_NAME)
    # The service attaches these two ownership fields after child completion.
    # Its immutable media/provenance fields still match the child receipt.
    if {key: value for key, value in published.items() if key not in {"source_job_id", "task_sha256"}} != result:
        raise ValueError("completed mesh manifest changed")
    for name in (mesh.VIDEO_NAME, mesh.SIDE_VIDEO_NAME, mesh.PREVIEW_NAME):
        path = output / name
        entry = result["files"][name]
        if (path.resolve() != path.absolute() or path.stat().st_size != entry["bytes"]
                or sha256_file(path) != entry["sha256"]):
            raise ValueError("completed mesh media changed")
    return result


def _collect_completed_segments(output, checkpoint):
    """Best-effort cleanup of fixed generated files, after a complete receipt.

    Each resolved directory must remain a direct child of the owned cache.
    Files are removed individually; unknown files and symlinks are retained.
    A killed cleanup can restart from its complete receipt without rendering.
    """
    from rallymate_vision import sam3d_mesh as mesh
    checkpoint_path = output / CHECKPOINT_NAME
    root = output / SEGMENTS_NAME
    previous_retention = checkpoint.get("cache_retention")
    checkpoint["cache_retention"] = "cleanup_pending"
    try:
        atomic_json(checkpoint_path, checkpoint)
    except OSError:
        return  # Completed media stay ready even if cleanup state cannot persist.
    changed = previous_retention in {"partial", "cleanup_pending"}
    try:
        if root.resolve() != root.absolute() or root.parent != output:
            raise ValueError("mesh cleanup root escapes owned output")
        for index, entry in enumerate(checkpoint["segments"]):
            directory = root / f"{index:08d}"
            if not directory.exists():
                continue
            if directory.resolve() != directory.absolute() or directory.parent != root:
                raise ValueError("mesh cleanup segment escapes owned cache")
            manifest_path = directory / mesh.MANIFEST_NAME
            if manifest_path.exists():
                if manifest_path.resolve() != manifest_path.absolute() or sha256_file(manifest_path) != entry["manifest_sha256"]:
                    raise ValueError("mesh cleanup segment manifest changed")
                manifest = _read_json(manifest_path)
                paths = [directory / name for name in (mesh.VIDEO_NAME, mesh.SIDE_VIDEO_NAME, mesh.PREVIEW_NAME)]
                # Verify all surviving files before deleting any of this part.
                for path in paths:
                    if path.exists() and (path.resolve() != path.absolute()
                            or sha256_file(path) != manifest["files"][path.name]["sha256"]):
                        raise ValueError("mesh cleanup segment media changed")
                for path in paths:
                    if path.exists():
                        path.unlink()
                        changed = True
                manifest_path.unlink()
                changed = True
            # This succeeds for an interrupted, already emptied directory;
            # unknown files prevent removal instead of being recursively lost.
            directory.rmdir()
            changed = True
        if root.exists():
            root.rmdir()
        checkpoint["cache_retention"] = "removed"
        checkpoint.pop("cache_cleanup_error_type", None)
    except (OSError, ValueError, KeyError, TypeError) as error:
        checkpoint["cache_retention"] = "partial" if changed else "retained"
        checkpoint["cache_cleanup_error_type"] = type(error).__name__
    try:
        atomic_json(checkpoint_path, checkpoint)
    except OSError:
        pass  # The durable cleanup_pending receipt is retryable on next entry.


def _remux_parts(parts, destination, *, check_cancelled=lambda: None):
    """Copy encoded H.264 packets; exact rational source PTS determine offsets."""
    import av
    base_origin = Fraction(*parts[0][1]["source_pts_origin"])
    target = None
    try:
        target = av.open(str(destination), "w")
        output_stream = None
        codec_identity = None
        for path, manifest in parts:
            check_cancelled()
            offset = Fraction(*manifest["source_pts_origin"]) - base_origin
            with av.open(str(path)) as source:
                stream = source.streams.video[0]
                current_codec = (stream.codec_context.name, stream.width, stream.height, stream.codec_context.extradata)
                if codec_identity is not None and codec_identity != current_codec:
                    raise ValueError("mesh segment encoder parameters changed")
                codec_identity = current_codec
                if output_stream is None:
                    output_stream = target.add_stream_from_template(stream)
                    # Codec headers can describe only a nominal frame rate.
                    # Preserve the MP4 source clock explicitly so the muxer
                    # cannot round fine-grained VFR timestamps to that rate.
                    output_stream.time_base = stream.time_base
                for packet in source.demux(stream):
                    check_cancelled()
                    if packet.dts is None:
                        continue
                    ticks = offset / packet.time_base
                    if ticks.denominator != 1:
                        raise ValueError("mesh segment timestamps cannot be represented exactly")
                    packet.pts += int(ticks)
                    packet.dts += int(ticks)
                    packet.stream = output_stream
                    target.mux(packet)
        target.close()
        target = None
        def expected_pts():
            for path, manifest in parts:
                offset = Fraction(*manifest["source_pts_origin"])-base_origin
                with av.open(str(path)) as source:
                    for frame in source.decode(video=0):
                        yield Fraction(frame.pts)*frame.time_base + offset
        count = 0
        expected = expected_pts()
        try:
            with av.open(str(destination)) as source:
                actual = (Fraction(frame.pts)*frame.time_base for frame in source.decode(video=0))
                for left, right in zip_longest(expected, actual):
                    check_cancelled()
                    if left != right:
                        raise ValueError("joined mesh video did not preserve every source presentation time")
                    count += 1
        finally:
            expected.close()
        if count != sum(manifest["frame_count"] for _, manifest in parts):
            raise ValueError("joined mesh video frame count changed")
    finally:
        if target is not None:
            target.close()


def render_mesh_resumable(source: Path, output_dir: Path, runtime_dir: Path, *,
                          first_frame: int, last_frame: int, stride: int, source_sha256: str,
                          model: dict, max_height: int = 720, time_slice_seconds: float | None = None,
                          check_cancelled=None) -> dict:
    """Commit each complete segment before yielding a fair worker turn.

    Time slices end at segment boundaries, so even a slow renderer saves at
    least one segment per turn. Cancellation can interrupt the active segment;
    committed segments remain reusable after process termination or restart.
    """
    from rallymate_vision import sam3d_mesh as mesh
    source, output, runtime = Path(source), Path(output_dir).resolve(), Path(runtime_dir)
    check_cancelled = check_cancelled or (lambda: None)
    if time_slice_seconds is not None and (not math.isfinite(time_slice_seconds) or time_slice_seconds <= 0):
        raise ValueError("Mesh time slice must be a positive finite number")
    if not 0 <= first_frame <= last_frame or stride < 1:
        raise ValueError("invalid mesh segment selection")
    started = time.perf_counter()
    check_cancelled()
    if sha256_file(source).lower() != source_sha256.lower():
        raise ValueError("mesh replay source changed")
    frames = _segment_frames()
    identity = _identity(source, output, runtime, first_frame, last_frame, stride, source_sha256, model, max_height, frames)
    rows = mesh._read_ledger(output / mesh.LEDGER_NAME, output)
    if any(not first_frame <= row["frame_index"] <= last_frame or (row["frame_index"]-first_frame) % stride
           or row["video_sha256"].lower() != source_sha256.lower() for row in rows):
        raise ValueError("mesh ledger contains frames outside the rendered source interval")
    root = output / SEGMENTS_NAME
    checkpoint_path = output / CHECKPOINT_NAME
    checkpoint = (_read_json(checkpoint_path) if checkpoint_path.exists() else
                  {"version": CHECKPOINT_VERSION, "identity": identity, "segments": []})
    if checkpoint.get("state") == "complete":
        result = _completed_result(output, checkpoint, identity)
        _collect_completed_segments(output, checkpoint)
        return result
    if root.resolve() != root.absolute():
        raise ValueError("mesh segment root cannot be a symlink")
    root.mkdir(exist_ok=True)
    segments, next_frame = _verified_segments(output, checkpoint, identity)
    resumed_frames = sum(entry["frame_count"] for entry in segments)
    previous = _read_json(root / f"{len(segments)-1:08d}" / mesh.MANIFEST_NAME) if segments else None
    decoder = SourceFrames(source, previous, check_cancelled) if next_frame <= last_frame else None
    try:
        while next_frame <= last_frame:
            check_cancelled()
            segment_end = min(last_frame, next_frame + (frames-1)*stride)
            directory = root / f"{len(segments):08d}"
            if directory.resolve() != directory.absolute():
                raise ValueError("mesh segment cannot be a symlink")
            directory.mkdir(exist_ok=True)
            manifest = mesh.render_mesh_video(source, directory, runtime, first_frame=next_frame,
                last_frame=segment_end, stride=stride, source_sha256=source_sha256, model=model,
                max_height=max_height, check_cancelled=check_cancelled, _geometry_dir=output,
                _rows=[row for row in rows if next_frame <= row["frame_index"] <= segment_end],
                _allow_empty=True, _source_session=decoder)
            segments.append({"manifest_sha256": sha256_file(directory / mesh.MANIFEST_NAME),
                             "frame_count": manifest["frame_count"]})
            atomic_json(checkpoint_path, checkpoint)
            next_frame = manifest["source_frame_end"] - 1 + stride
            if next_frame <= last_frame and time_slice_seconds is not None and time.perf_counter()-started >= time_slice_seconds:
                if sha256_file(source).lower() != source_sha256.lower() or sha256_file(output / mesh.LEDGER_NAME) != identity["ledger_sha256"]:
                    raise ValueError("mesh render source or ledger changed")
                result = {"status": "pending", "source_video_sha256": source_sha256,
                    "ledger_sha256": identity["ledger_sha256"], "completed_segments": len(segments),
                    "completed_frames": sum(entry["frame_count"] for entry in segments),
                    "next_source_frame": next_frame, "resumed_frames": resumed_frames,
                    "checkpoint": CHECKPOINT_NAME, "reason": "mesh_time_slice_complete"}
                atomic_json(output / RESULT_NAME, result)
                return result
    finally:
        if decoder is not None:
            decoder.close()
    check_cancelled()
    manifests = [_read_json(root / f"{number:08d}" / mesh.MANIFEST_NAME) for number in range(len(segments))]
    if not sum(item["mesh_frame_count"] for item in manifests):
        raise ValueError("mesh replay contains no inferred human surface")
    if sha256_file(source).lower() != source_sha256.lower() or sha256_file(output / mesh.LEDGER_NAME) != identity["ledger_sha256"]:
        raise ValueError("mesh render source or ledger changed")
    pending = [output / f".{name}.joined.mp4" for name in (mesh.VIDEO_NAME, mesh.SIDE_VIDEO_NAME)]
    join_started = time.perf_counter()
    try:
        for name, target in zip((mesh.VIDEO_NAME, mesh.SIDE_VIDEO_NAME), pending):
            _remux_parts([(root / f"{number:08d}" / name, item) for number, item in enumerate(manifests)],
                         target, check_cancelled=check_cancelled)
        preview = next((i for i, item in enumerate(manifests) if item["preview_source_frame"] is not None), 0)
        preview_tmp = output / f".{mesh.PREVIEW_NAME}.tmp"
        shutil.copyfile(root / f"{preview:08d}" / mesh.PREVIEW_NAME, preview_tmp)
        check_cancelled()
        for target, name in zip(pending, (mesh.VIDEO_NAME, mesh.SIDE_VIDEO_NAME)):
            target.replace(output / name)
        preview_tmp.replace(output / mesh.PREVIEW_NAME)
        stages = defaultdict(float)
        for item in manifests:
            for key, value in item["stage_seconds"].items():
                stages[key] += value
        stages["segment_join_and_validation"] = time.perf_counter()-join_started
        result = {**manifests[0], "source_frame_end": manifests[-1]["source_frame_end"],
            "source_last_pts": manifests[-1]["source_last_pts"],
            **{key: sum(item[key] for item in manifests) for key in ("frame_count", "mesh_frame_count", "subject_count")},
            "preview_source_frame": manifests[preview]["preview_source_frame"],
            "render_seconds": sum(item["render_seconds"] for item in manifests) + stages["segment_join_and_validation"],
            "stage_seconds": {key: round(value, 6) for key, value in stages.items()},
            "mesh_segments": {"count": len(manifests), "max_frames": frames, "resumed_frames": resumed_frames,
                "checkpoint": CHECKPOINT_NAME, "join": "encoded_packet_copy_exact_source_pts",
                "checkpoint_semantics": "completed_output_receipt_and_segment_cache_retention",
                "turn_source_decoded_frames": decoder.decoded_frames if decoder else 0,
                "turn_seek_prefix_frames": decoder.seek_prefix_frames if decoder else 0,
                "time_slice_seconds": time_slice_seconds, "time_slice_boundary": "complete_segment"},
            "mesh_prepare": _aggregate_receipts([item["mesh_prepare"] for item in manifests]),
            "mesh_encode": _aggregate_receipts([item["mesh_encode"] for item in manifests]),
            "files": {name: {"sha256": sha256_file(output / name), "bytes": (output / name).stat().st_size}
                      for name in (mesh.VIDEO_NAME, mesh.SIDE_VIDEO_NAME, mesh.PREVIEW_NAME)}}
        atomic_json(output / mesh.MANIFEST_NAME, result)
        atomic_json(output / RESULT_NAME, result)
        checkpoint.update(state="complete", result_sha256=sha256_file(output / RESULT_NAME), cache_retention="retained")
        atomic_json(checkpoint_path, checkpoint)
        _collect_completed_segments(output, checkpoint)
        return result
    finally:
        for target in pending:
            target.unlink(missing_ok=True)
        (output / f".{mesh.PREVIEW_NAME}.tmp").unlink(missing_ok=True)
