#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from rallymate_evaluation.fs09_phase_truth import (
    REVIEW_CLIP_RENDERER_VERSION,
    TASK_WINDOW_CONTRACT,
    _phase_items,
    probe_fs09_phase_review_clip,
)
from rallymate_evaluation.small_roi_keypoint_truth import _source, sha256_file


def _metadata_audit(ffprobe: Path, clip: Path, source_video: Path) -> dict[str, object]:
    command = [
        str(ffprobe),
        "-v", "error",
        "-show_entries", "format_tags:stream_tags",
        "-of", "json",
        str(clip),
    ]
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        raise RuntimeError(f"ffprobe metadata audit failed: {completed.stderr.strip()}")
    try:
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError("ffprobe metadata audit returned invalid JSON") from exc
    forbidden_keys = {
        "comment", "creation_time", "date", "description", "filename", "location",
        "location-eng", "source", "timecode", "title",
    }
    forbidden_key_hits: set[str] = set()
    text_values: list[str] = []

    def walk(value: object) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                normalized = str(key).strip().lower()
                if normalized in forbidden_keys:
                    forbidden_key_hits.add(normalized)
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
        elif isinstance(value, str):
            text_values.append(value)

    walk(payload)
    source_tokens = {
        str(source_video).casefold(),
        source_video.name.casefold(),
        source_video.stem.casefold(),
    }
    source_identity_hits = sorted(
        token for token in source_tokens if token and any(token in text.casefold() for text in text_values)
    )
    if forbidden_key_hits or source_identity_hits:
        raise RuntimeError(
            "rendered clip exposes forbidden metadata: "
            f"keys={sorted(forbidden_key_hits)}, source_identity={source_identity_hits}"
        )
    return {
        "ffprobe_sha256": sha256_file(ffprobe),
        "forbidden_tag_keys_present": [],
        "source_path_or_filename_present": False,
        "passed": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Render browser-seekable padded clips for M77 blind FS09 phase truth.")
    parser.add_argument("--m74-report", type=Path, required=True)
    parser.add_argument("--ffmpeg", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    args = parser.parse_args()
    report_path = args.m74_report.resolve()
    ffmpeg = args.ffmpeg.resolve()
    ffprobe = ffmpeg.with_name("ffprobe.exe" if ffmpeg.suffix.lower() == ".exe" else "ffprobe")
    final = args.output_directory.resolve()
    staging = final.with_name(f".{final.name}.building")
    if final.exists() or staging.exists():
        parser.error("output or staging directory already exists")
    if not ffmpeg.is_file():
        parser.error("ffmpeg executable does not exist")
    if not ffprobe.is_file():
        parser.error("ffprobe executable does not exist beside ffmpeg")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    items = _phase_items(report)
    source = next(row for row in report["sources"]["per_video"] if row["video_id"] == items[0]["video_id"])
    m73 = json.loads(Path(source["m73_report"]["path"]).read_text(encoding="utf-8"))
    video = Path(m73["sources"]["video"]["path"]).resolve()
    staging.mkdir(parents=True)
    clips = []
    try:
        for index, item in enumerate(items, start=1):
            task_id = f"m77:{item['video_id']}:fs09-phase-{index:03d}"
            window = TASK_WINDOW_CONTRACT[index - 1]
            if not task_id.endswith(str(window["task_suffix"])):
                raise RuntimeError("frozen review-window task order drifted")
            start = int(window["review_start_ms"])
            end = int(window["review_end_ms"])
            anchor = int(window["target_selection_anchor_ms"])
            output = staging / f"task-{index:03d}-browser.mp4"
            command = [
                str(ffmpeg), "-y", "-loglevel", "error",
                "-ss", f"{start / 1000:.3f}", "-i", str(video),
                "-t", f"{(end - start) / 1000:.3f}", "-an",
                "-map_metadata", "-1", "-map_chapters", "-1",
                "-metadata", "title=", "-metadata", "comment=",
                "-metadata", "creation_time=", "-metadata", "timecode=",
                "-c:v", "libx264", "-preset", "medium", "-crf", "18",
                "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(output),
            ]
            completed = subprocess.run(command, check=False, capture_output=True, text=True)
            if completed.returncode != 0:
                raise RuntimeError(f"ffmpeg failed for {task_id}: {completed.stderr.strip()}")
            probe = probe_fs09_phase_review_clip(output)
            clips.append(
                {
                    "task_id": task_id,
                    "source_start_ms": start,
                    "source_end_ms": end,
                    "source_time_offset_ms": start,
                    "target_selection_anchor_ms": anchor,
                    "requested_duration_ms": end - start,
                    "clip": {
                        "path": str((final / output.name).resolve()),
                        "sha256": sha256_file(output),
                    },
                    "probe": probe,
                    "metadata_audit": _metadata_audit(ffprobe, output, video),
                }
            )
        manifest = {
            "schema_version": "1.0.0",
            "renderer_version": REVIEW_CLIP_RENDERER_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "status": "passed",
            "sources": {"m74_report": _source(report_path), "video": _source(video), "ffmpeg": _source(ffmpeg), "ffprobe": _source(ffprobe)},
            "window_contract": {
                "kind": "pre_frozen_candidate_selected_coarse_anchor",
                "anchor_selects_action_only": True,
                "anchor_constrains_submitted_boundary_or_phase": False,
                "anchor_is_candidate_boundary_or_phase": False,
                "exact_candidate_boundaries_derivable_from_window": False,
                "candidate_selected_coarse_localization_disclosed": True,
                "tasks": [
                    {
                        "task_id": clip["task_id"],
                        "review_start_ms": clip["source_start_ms"],
                        "review_end_ms": clip["source_end_ms"],
                        "target_selection_anchor_ms": clip["target_selection_anchor_ms"],
                    }
                    for clip in clips
                ],
            },
            "clips": clips,
            "safety": {
                "pose_overlay_rendered": False,
                "candidate_boundary_overlay_rendered": False,
                "candidate_phase_overlay_rendered": False,
                "audio_included": False,
                "source_metadata_or_filename_retained": False,
                "target_selection_anchor_is_boundary_or_phase": False,
                "accuracy_claim": False,
                "production_enabled": False,
            },
        }
        (staging / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        staging.replace(final)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise
    print(json.dumps({"status": "passed", "output": str(final), "clip_count": len(clips)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
