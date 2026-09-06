from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from rallymate_tracking import build_primary_player_artifacts
from rallymate_tracking.primary_player import PRIMARY_PLAYER_ALGORITHM_VERSION


VIDEO_ID = "850cb0006b406c7176eeda8d711cd065"
WINDOW_START = 930
WINDOW_END_EXCLUSIVE = 1530


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _detection_signature(record: dict[str, Any]) -> list[tuple[Any, ...]]:
    return [
        (
            detection.get("class_name"),
            detection.get("track_id"),
            detection.get("bbox_px"),
        )
        for detection in record.get("detections", [])
    ]


def main() -> None:
    root = Path.cwd()
    full_frames = (
        root
        / "runs"
        / "pose-ab"
        / "rtmpose-m-halpe26-256x192"
        / VIDEO_ID
        / "frames.jsonl"
    )
    halpe_window_frames = (
        root
        / "reports"
        / "pose-scoring-same-window-inputs"
        / "halpe26-930-1530"
        / "frames.jsonl"
    )
    wholebody_window_frames = (
        root
        / "reports"
        / "pose-scoring-same-window-inputs"
        / "wholebody133-930-1530"
        / "frames.jsonl"
    )
    output_root = (
        root
        / "reports"
        / "pose-scoring-current-inputs"
        / PRIMARY_PLAYER_ALGORITHM_VERSION
    )
    full_output = output_root / VIDEO_ID
    window_output = output_root / "shared-930-1530"
    full_output.mkdir(parents=True, exist_ok=True)
    window_output.mkdir(parents=True, exist_ok=True)

    full_timeline_path = full_output / "primary-player.jsonl"
    full_summary_path = full_output / "primary-player-summary.json"
    build_primary_player_artifacts(
        full_frames,
        full_timeline_path,
        full_summary_path,
    )

    full_timeline = _load_jsonl(full_timeline_path)
    versions = {
        str(record.get("selection_algorithm_version")) for record in full_timeline
    }
    if versions != {PRIMARY_PLAYER_ALGORITHM_VERSION}:
        raise RuntimeError(f"unexpected full timeline versions: {sorted(versions)}")
    window_timeline = [
        record
        for record in full_timeline
        if WINDOW_START <= int(record["processed_index"]) < WINDOW_END_EXCLUSIVE
    ]
    expected_count = WINDOW_END_EXCLUSIVE - WINDOW_START
    if len(window_timeline) != expected_count:
        raise RuntimeError(
            f"expected {expected_count} timeline rows, found {len(window_timeline)}"
        )
    if [int(record["processed_index"]) for record in window_timeline] != list(
        range(WINDOW_START, WINDOW_END_EXCLUSIVE)
    ):
        raise RuntimeError("window timeline processed indexes are not contiguous")
    window_timeline_path = window_output / "primary-player.jsonl"
    _write_jsonl(window_timeline_path, window_timeline)

    halpe_window = _load_jsonl(halpe_window_frames)
    wholebody_window = _load_jsonl(wholebody_window_frames)
    if len(halpe_window) != expected_count or len(wholebody_window) != expected_count:
        raise RuntimeError("same-window pose inputs do not contain exactly 600 rows")
    same_frame_metadata = sum(
        left["frame"] == right["frame"]
        for left, right in zip(halpe_window, wholebody_window)
    )
    same_detection_track_bbox = sum(
        _detection_signature(left) == _detection_signature(right)
        for left, right in zip(halpe_window, wholebody_window)
    )
    if same_frame_metadata != expected_count:
        raise RuntimeError("same-window pose inputs do not share exact frame metadata")
    if same_detection_track_bbox != expected_count:
        raise RuntimeError(
            "same-window pose inputs do not share exact detection/track/bbox data"
        )
    if [
        (record["frame"]["processed_index"], record["frame"]["timestamp_ms"])
        for record in halpe_window
    ] != [
        (record["processed_index"], record["timestamp_ms"])
        for record in window_timeline
    ]:
        raise RuntimeError("timeline slice does not align with same-window frame timestamps")

    provenance = {
        "schema_version": "1.0.0",
        "status": "current_primary_player_inputs_ready",
        "video_id": VIDEO_ID,
        "primary_player_algorithm_version": PRIMARY_PLAYER_ALGORITHM_VERSION,
        "derivation": (
            "full timeline selected once from the complete Halpe26 pose artifact; "
            "the 930:1530 processed-index slice is reused by both same-window pose "
            "models after exact frame and detection/track/bbox equality checks"
        ),
        "full": {
            "frames_path": str(full_frames.resolve()),
            "frames_sha256": _sha256(full_frames),
            "timeline_path": str(full_timeline_path.resolve()),
            "timeline_sha256": _sha256(full_timeline_path),
            "summary_path": str(full_summary_path.resolve()),
            "summary_sha256": _sha256(full_summary_path),
            "row_count": len(full_timeline),
        },
        "window": {
            "start_processed_index": WINDOW_START,
            "end_processed_index_exclusive": WINDOW_END_EXCLUSIVE,
            "row_count": len(window_timeline),
            "timeline_path": str(window_timeline_path.resolve()),
            "timeline_sha256": _sha256(window_timeline_path),
            "halpe_frames_path": str(halpe_window_frames.resolve()),
            "halpe_frames_sha256": _sha256(halpe_window_frames),
            "wholebody_frames_path": str(wholebody_window_frames.resolve()),
            "wholebody_frames_sha256": _sha256(wholebody_window_frames),
        },
        "cross_model_input_assertions": {
            "frame_metadata_equal_rows": same_frame_metadata,
            "detection_track_bbox_equal_rows": same_detection_track_bbox,
            "expected_rows": expected_count,
            "pose_outputs_intentionally_model_specific": True,
        },
    }
    provenance_path = output_root / "provenance.json"
    provenance_path.write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output_root / "README.md").write_text(
        "# Current primary-player scoring inputs\n\n"
        f"Algorithm: `{PRIMARY_PLAYER_ALGORITHM_VERSION}`.\n\n"
        f"The complete `{VIDEO_ID}` Halpe26 frame artifact is selected once, then "
        f"processed indexes `{WINDOW_START}` through `{WINDOW_END_EXCLUSIVE - 1}` "
        "are sliced as the shared 600-frame comparison timeline. Halpe26 and "
        "WholeBody133 have identical frame metadata and identical "
        "detection/track/bbox payloads on all 600 rows; their pose keypoints remain "
        "model-specific and are read from their respective frame artifacts. See "
        "`provenance.json` for source paths and SHA-256 values. Legacy v0.1 inputs "
        "are retained elsewhere and are not overwritten.\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": provenance["status"],
                "full_timeline": str(full_timeline_path.resolve()),
                "window_timeline": str(window_timeline_path.resolve()),
                "full_timeline_sha256": provenance["full"]["timeline_sha256"],
                "window_timeline_sha256": provenance["window"]["timeline_sha256"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
