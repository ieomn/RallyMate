#!/usr/bin/env python3
"""One-time, opt-in repair of completed replay pixels; inference evidence is untouched.

Run without --apply to inspect plans. Existing legacy backups make reruns skip
that job. Source videos, SQLite rows, requests and summary files are read-only.
"""
from __future__ import annotations

import argparse
from fractions import Fraction
import json
import math
import os
from pathlib import Path
import sqlite3
import struct
import subprocess
import tempfile


def probe(path: Path) -> dict:
    result = subprocess.run([
        "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
        "stream=codec_name,pix_fmt,width,height,avg_frame_rate,nb_frames,duration",
        "-of", "json", str(path),
    ], check=True, capture_output=True, text=True)
    streams = json.loads(result.stdout)["streams"]
    if len(streams) != 1:
        raise ValueError("expected exactly one selected video stream")
    return streams[0]


def inside(path: str | Path, parent: Path) -> Path:
    resolved = Path(path).resolve(strict=True)
    if not resolved.is_relative_to(parent):
        raise ValueError(f"path outside expected directory: {resolved}")
    return resolved


def ensure_idle(db: sqlite3.Connection, job_id: str) -> None:
    if db.execute("select count(*) from jobs where status='running'").fetchone()[0]:
        raise ValueError("a job is running; defer this maintenance")
    row = db.execute("select status from jobs where id=?", (job_id,)).fetchone()
    if row is None or row[0] != "succeeded":
        raise ValueError("job is no longer succeeded")


def plan_job(row: sqlite3.Row, data_root: Path) -> dict:
    source = inside(row["video_path"], data_root / "uploads")
    request_path = inside(row["request_path"], data_root / "requests")
    output = inside(row["output_dir"], data_root / "runs")
    if output.name != row["id"]:
        raise ValueError("output directory and job id disagree")
    request = json.loads(request_path.read_text())
    summary = json.loads(row["summary_json"] or "{}")
    disk_summary = json.loads((output / "summary.json").read_text())
    if summary.get("processing") != disk_summary.get("processing") or summary.get("input") != disk_summary.get("input"):
        raise ValueError("database and artifact summaries disagree")
    if Path(request["source"]["video_path"]).resolve() != source:
        raise ValueError("request does not refer to this original upload")
    processing = summary["processing"]
    settings = request["processing"]
    metadata = summary["input"]["video"]
    start, end, stride, count = [processing[key] for key in ("source_start_frame", "source_end_frame_exclusive", "frame_stride", "processed_frames")]
    if any(type(value) is not int for value in (start, end, stride, count)) or start < 0 or end <= start or stride < 1 or count < 1:
        raise ValueError("missing or invalid frame range")
    fps = float(metadata["fps"])
    if not math.isfinite(fps) or fps <= 0 or settings["frame_stride"] != stride or int(settings["start_ms"] / 1000 * fps) != start:
        raise ValueError("request and processed frame range disagree")
    last = start + (count - 1) * stride
    if last >= end or end - last > stride or last >= metadata["frame_count"]:
        raise ValueError("processed frame count does not match its range")
    if settings.get("end_ms") is not None and last >= int(settings["end_ms"] / 1000 * fps):
        raise ValueError("processed range extends beyond the requested end")
    if settings.get("max_frames") is not None and count > settings["max_frames"]:
        raise ValueError("processed count exceeds the requested maximum")
    seen = 0
    with (output / "frames.jsonl").open() as stream:
        for line in stream:
            if not line.strip():
                continue
            record = json.loads(line)
            frame = record["frame"]
            expected_index = start + seen * stride
            if record.get("job_id") != row["id"] or frame["index"] != expected_index or frame["processed_index"] != seen:
                raise ValueError("per-frame evidence has gaps or belongs to another task")
            if abs(frame["timestamp_ms"] - int(expected_index / fps * 1000)) > 2:
                raise ValueError("frame evidence and source timing disagree")
            seen += 1
    if seen != count:
        raise ValueError("frame evidence count disagrees with summary")
    replay = output / "annotated.mp4"
    old = probe(replay)
    replay_fps = Fraction(old["avg_frame_rate"])
    if int(old["nb_frames"]) != count or abs(float(replay_fps) - max(fps / stride, 1)) > .001:
        raise ValueError("existing replay frame count or cadence is inconsistent")
    if old["width"] != metadata["width"] or old["height"] != metadata["height"]:
        raise ValueError("existing replay dimensions disagree with inference")
    return {"job_id": row["id"], "filename": row["original_filename"], "source": source,
            "output": output, "start": start, "last": last, "stride": stride, "count": count,
            "source_fps": fps, "replay_fps": str(replay_fps), "old_probe": old}


def faststart(path: Path) -> bool:
    atoms = []
    with path.open("rb") as stream:
        while header := stream.read(8):
            if len(header) != 8:
                return False
            size, kind = struct.unpack(">I4s", header)
            header_size = 8
            if size == 1:
                size = struct.unpack(">Q", stream.read(8))[0]
                header_size = 16
            if size < header_size:
                break
            atoms.append(kind)
            stream.seek(size - header_size, 1)
    return b"moov" in atoms and b"mdat" in atoms and atoms.index(b"moov") < atoms.index(b"mdat")


def check_pixels(plan: dict, replay: Path, work: Path) -> list[dict]:
    import cv2
    import numpy as np

    source = cv2.VideoCapture(str(plan["source"]))
    clean = cv2.VideoCapture(str(replay))
    comparisons = []
    try:
        indices = sorted({0, plan["count"] // 2, plan["count"] - 1, min(plan["count"] - 1, int(5.5 * float(Fraction(plan["replay_fps"]))))})
        for index in indices:
            source.set(cv2.CAP_PROP_POS_FRAMES, plan["start"] + index * plan["stride"])
            clean.set(cv2.CAP_PROP_POS_FRAMES, index)
            source_ok, original = source.read()
            clean_ok, decoded = clean.read()
            if not source_ok or not clean_ok or original.shape != decoded.shape:
                raise ValueError("sampled source and replacement frames cannot be aligned")
            error = float(np.abs(original.astype(np.int16) - decoded.astype(np.int16)).mean())
            if error > 8:
                raise ValueError(f"replacement frame {index} differs unexpectedly from the original ({error:.3f})")
            comparisons.append({"replay_frame": index, "source_frame": plan["start"] + index * plan["stride"], "mean_pixel_error": round(error, 4)})
            if index == indices[min(1, len(indices) - 1)]:
                cv2.imwrite(str(work / "verification.jpg"), decoded)
    finally:
        source.release()
        clean.release()
    return comparisons


def repair(plan: dict, db: sqlite3.Connection) -> dict:
    output = plan["output"]
    replay, preview = output / "annotated.mp4", output / "preview.jpg"
    legacy, legacy_preview = output / "annotated.legacy.mp4", output / "preview.legacy.jpg"
    if legacy.exists() or legacy_preview.exists():
        return {"job_id": plan["job_id"], "status": "skipped", "reason": "legacy backup already exists; never overwrite it"}
    ensure_idle(db, plan["job_id"])
    with tempfile.TemporaryDirectory(prefix=".clean-replay-", dir=output) as temporary:
        work = Path(temporary)
        new_video, new_preview = work / "annotated.mp4", work / "preview.jpg"
        selection = f"select=between(n\\,{plan['start']}\\,{plan['last']})*not(mod(n-{plan['start']}\\,{plan['stride']})),setpts=N/({plan['replay_fps']}*TB)"
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(plan["source"]), "-map", "0:v:0", "-vf", selection,
                        "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-threads", "2", "-pix_fmt", "yuv420p",
                        "-r", plan["replay_fps"], "-frames:v", str(plan["count"]), "-movflags", "+faststart", str(new_video)], check=True)
        new_probe = probe(new_video)
        if new_probe["codec_name"] != "h264" or new_probe["pix_fmt"] != "yuv420p" or int(new_probe["nb_frames"]) != plan["count"]:
            raise ValueError("replacement failed codec/pixel-format/frame-count verification")
        if Fraction(new_probe["avg_frame_rate"]) != Fraction(plan["replay_fps"]) or abs(float(new_probe["duration"]) - float(plan["old_probe"]["duration"])) > .001:
            raise ValueError("replacement cadence or duration changed")
        if not faststart(new_video):
            raise ValueError("replacement MP4 is missing faststart")
        comparisons = check_pixels(plan, new_video, work)
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(new_video), "-frames:v", "1", "-q:v", "2", str(new_preview)], check=True)
        ensure_idle(db, plan["job_id"])
        # All outputs have been checked before the one-time rename and atomic
        # replacement. An interrupted replacement retains the original backup.
        replay.rename(legacy)
        if preview.exists():
            preview.rename(legacy_preview)
        try:
            os.replace(new_video, replay)
            os.replace(new_preview, preview)
            os.replace(work / "verification.jpg", output / "clean-replay-verification.jpg")
        except BaseException:
            os.replace(legacy, replay)
            if legacy_preview.exists():
                os.replace(legacy_preview, preview)
            raise
    return {"job_id": plan["job_id"], "filename": plan["filename"], "status": "repaired",
            "source_range": [plan["start"], plan["last"]], "frame_stride": plan["stride"], "probe": new_probe,
            "faststart": True, "samples": comparisons, "backup": str(legacy), "preview": str(preview)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--job-id", action="append", help="Process only these completed jobs, in the supplied order")
    parser.add_argument("--apply", action="store_true", help="Prepare, verify and atomically install clean replays")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    root = args.root.resolve(strict=True)
    data_root = (root / "service_data").resolve(strict=True)
    db = sqlite3.connect(f"file:{data_root / 'jobs.sqlite3'}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    reports = []
    try:
        ids = args.job_id or [row[0] for row in db.execute("select id from jobs where status='succeeded' order by created_at")]
        for job_id in ids:
            try:
                ensure_idle(db, job_id)
                row = db.execute("select * from jobs where id=?", (job_id,)).fetchone()
                plan = plan_job(row, data_root)
                report = repair(plan, db) if args.apply else {"status": "planned", **{key: str(value) if isinstance(value, Path) else value for key, value in plan.items()}}
            except Exception as error:
                report = {"job_id": job_id, "status": "skipped", "reason": str(error)}
            reports.append(report)
            print(json.dumps(report, ensure_ascii=False), flush=True)
        if args.report:
            args.report.write_text(json.dumps(reports, ensure_ascii=False, indent=2) + "\n")
    finally:
        db.close()


if __name__ == "__main__":
    main()
