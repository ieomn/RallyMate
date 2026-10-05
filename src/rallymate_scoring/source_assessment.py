"""Persist additive source-aligned observations without changing legacy scores."""
from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from typing import Any

from rallymate_features.schemas import PoseSequence
from rallymate_scoring.source_aligned_features import (
    clear_source_aligned_feature_cache, compute_source_aligned_features,
    prepare_source_aligned_context,
)

ARTIFACT_NAME = "source-aligned-measurements.json"
ARTIFACT_VERSION = "source-aligned-measurements-artifact-v1.0.0"


@lru_cache(maxsize=1)
def source_binding() -> dict:
    catalog = json.loads(files("rallymate_scoring").joinpath("data/technical_review_rules.json").read_text(encoding="utf-8"))
    return {"reference_version": catalog["source_reference_version"],
            "reference_sha256": catalog["source_reference_sha256"],
            "document": next(item for item in catalog["source_documents"] if item["id"] == "CARD-FS")}


def file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_source_aligned_assessment(output_dir: Path, sequence: PoseSequence,
                                    events: list[dict[str, Any]], *, video_id: str,
                                    video_sha256: str | None = None,
                                    frames_sha256: str | None = None,
                                    frames_path: Path | None = None,
                                    primary_timeline_path: Path | None = None,
                                    primary_timeline: list[dict] | None = None) -> dict:
    context = prepare_source_aligned_context(sequence, events, primary_timeline=primary_timeline)
    try:
        records = [compute_source_aligned_features(sequence, event, events=events, context=context)
                   for event in events if event.get("event_code") in {"FS01", "FS09"}]
    finally:
        clear_source_aligned_feature_cache(sequence)
    artifact = {
        "version": ARTIFACT_VERSION, "video_id": video_id,
        "source_binding": source_binding(),
        "video_sha256": video_sha256.lower() if video_sha256 else None,
        "frames_sha256": frames_sha256.lower() if frames_sha256 else file_sha(frames_path or output_dir / "frames.jsonl"),
        "events_sha256": file_sha(output_dir / "events.jsonl"),
        "primary_timeline_sha256": file_sha(primary_timeline_path or output_dir / "primary-player.jsonl"),
        "score_semantics": "source_aligned_measurement_not_technical_grade",
        "technical_grade": None, "technical_score_0_to_100": None,
        "records": records,
    }
    path = output_dir / ARTIFACT_NAME
    temporary = output_dir / (ARTIFACT_NAME + ".tmp")
    temporary.write_bytes((json.dumps(artifact, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8"))
    temporary.replace(path)
    return {"path": ARTIFACT_NAME, "sha256": file_sha(path), "event_count": len(records),
            "indicator_window_count": sum(len(r["indicators"]) for r in records)}
