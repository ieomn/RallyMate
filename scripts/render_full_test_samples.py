"""Create source-video inspection sheets; green boxes are recorded target selection."""
from __future__ import annotations

import json
from pathlib import Path
import sys

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from validate_full_test_scoring import database_jobs, inventory, matching_run, write_json


def main():
    out = ROOT / "reports/scoring-refactor-20261108"
    target = out / "visual-review"
    target.mkdir(parents=True, exist_ok=True)
    jobs = database_jobs(ROOT / "service_data/jobs.sqlite3")
    manifest = []
    for video in inventory(ROOT / "FULL-TEST"):
        job = matching_run(video, jobs)
        indices = sorted(set(int(video["frames"] * ratio) for ratio in (.10, .30, .60, .85)))
        assessment_path = out / "assessments" / (Path(video["name"]).stem + ".json")
        if assessment_path.is_file():
            assessment = json.loads(assessment_path.read_text(encoding="utf-8"))
            if assessment.get("episodes"):
                indices[0] = int(round(assessment["episodes"][0]["peak_ms"] * video["fps"] / 1000))
        selections = {}
        if job:
            sidecar = Path(job["output_dir"]) / "tennis-subject-selection.json"
            if sidecar.is_file():
                document = json.loads(sidecar.read_text(encoding="utf-8"))
                if document.get("source_video_sha256") != video["sha256"]:
                    raise ValueError("Selection sidecar belongs to a different source video")
                for row in document["timeline"]:
                    if row["source_frame_index"] in indices:
                        selections[row["source_frame_index"]] = row
            else:
                with (Path(job["output_dir"]) / "primary-player.jsonl").open(encoding="utf-8") as handle:
                    for line in handle:
                        row = json.loads(line)
                        if row["source_frame_index"] in indices:
                            selections[row["source_frame_index"]] = row
        cap = cv2.VideoCapture(video["source"])
        sheet = np.full((760, 1280, 3), 25, dtype=np.uint8)
        rendered = []
        for index, frame_index in enumerate(indices):
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
            ok, frame = cap.read()
            if not ok:
                raise RuntimeError(f"Cannot decode source frame {video['name']}:{frame_index}")
            selected = selections.get(frame_index, {})
            box = selected.get("bbox_px")
            if box:
                x1, y1, x2, y2 = map(lambda v: int(round(v)), box)
                cv2.rectangle(frame, (x1, y1), (x2, y2), (80, 255, 80), 2)
            scale = min(640 / frame.shape[1], 335 / frame.shape[0])
            thumb = cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
            x, y = (index % 2) * 640 + (640 - thumb.shape[1]) // 2, (index // 2) * 380 + 40
            sheet[y:y + thumb.shape[0], x:x + thumb.shape[1]] = thumb
            text = f"{video['name'][:8]} f{frame_index} {frame_index / video['fps']:.2f}s track={selected.get('source_track_id', '?')}"
            cv2.putText(sheet, text, ((index % 2) * 640 + 10, (index // 2) * 380 + 25),
                        cv2.FONT_HERSHEY_SIMPLEX, .6, (240, 240, 240), 1, cv2.LINE_AA)
            rendered.append({"frame_index": frame_index, "timestamp_approx_ms": frame_index / video["fps"] * 1000,
                             "source_track_id": selected.get("source_track_id"), "bbox_px": box})
        cap.release()
        output_path = target / (Path(video["name"]).stem + ".jpg")
        cv2.imencode(".jpg", sheet, [cv2.IMWRITE_JPEG_QUALITY, 93])[1].tofile(str(output_path))
        manifest.append({"name": video["name"], "sha256": video["sha256"], "frames": rendered,
                         "contact_sheet": str(output_path), "target_run": job["id"] if job else None})
    write_json(target / "manifest.json", manifest)
    print(json.dumps({"rendered_videos": len(manifest), "samples_per_video": 4}))


if __name__ == "__main__":
    main()
