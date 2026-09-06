from __future__ import annotations

import json
import tempfile
from pathlib import Path

import cv2
from fastapi.testclient import TestClient

from rallymate_service.api import create_app
from rallymate_service.config import ServiceSettings
from rallymate_service.database import JobDatabase
from rallymate_service.worker import PersistentVisionRunner, process_one


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data" / "samples" / "pexels-tennis-match-992693.mp4"


def make_consecutive_clip(source: Path, target: Path, frame_count: int = 12) -> None:
    capture = cv2.VideoCapture(str(source))
    if not capture.isOpened():
        raise RuntimeError(f"could not open integration video: {source}")
    fps = capture.get(cv2.CAP_PROP_FPS) or 25.0
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    writer = cv2.VideoWriter(
        str(target), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height)
    )
    if not writer.isOpened():
        capture.release()
        raise RuntimeError("could not create integration clip")
    written = 0
    while written < frame_count:
        ok, frame = capture.read()
        if not ok:
            break
        writer.write(frame)
        written += 1
    capture.release()
    writer.release()
    if written != frame_count:
        raise RuntimeError(f"expected {frame_count} frames but wrote {written}")


def main() -> None:
    if not SOURCE.exists():
        raise FileNotFoundError(
            f"integration sample is missing: {SOURCE}; run download_assets.py"
        )
    with tempfile.TemporaryDirectory(prefix="rallymate-service-") as directory:
        root = Path(directory)
        clip = root / "tennis.mp4"
        make_consecutive_clip(SOURCE, clip)
        settings = ServiceSettings(
            data_root=root / "service",
            database_path=root / "service" / "jobs.sqlite3",
            detect_model=ROOT / "models" / "yolo26n.pt",
            pose_model=ROOT / "models" / "yolo26n-pose.pt",
            device="0",
            max_upload_bytes=200 * 1024 * 1024,
            max_video_duration_seconds=30,
        )
        settings.ensure_directories()
        database = JobDatabase(settings.database_path)
        app = create_app(settings, database)
        runner = PersistentVisionRunner(settings)
        with TestClient(app) as client:
            with clip.open("rb") as handle:
                response = client.post(
                    "/v1/jobs",
                    files={"video": ("tennis.mp4", handle, "video/mp4")},
                    data={
                        "court_mode": "auto",
                        "write_annotated_video": "false",
                    },
                )
            response.raise_for_status()
            job_id = response.json()["id"]
            if not process_one(
                settings, database, "integration-worker", runner=runner
            ):
                raise RuntimeError("worker did not claim the integration job")
            status = client.get(f"/v1/jobs/{job_id}")
            status.raise_for_status()
            payload = status.json()
            if payload["status"] != "succeeded":
                raise RuntimeError(json.dumps(payload, ensure_ascii=False, indent=2))
            summary = payload["summary"]
            readiness_path = Path(database.get_job(job_id)["output_dir"]) / "scoring-readiness.json"
            if not readiness_path.exists():
                raise RuntimeError("scoring-readiness.json was not generated")
            readiness = json.loads(readiness_path.read_text(encoding="utf-8"))
            if readiness["summary"]["indicator_count"] != 298:
                raise RuntimeError("scoring readiness did not audit all 298 indicators")
            report_path = Path(database.get_job(job_id)["output_dir"]) / "analysis-report.html"
            if not report_path.exists() or "298 项逐条审计" not in report_path.read_text(encoding="utf-8"):
                raise RuntimeError("complete HTML analysis report was not generated")
            print(
                json.dumps(
                    {
                        "job_id": job_id,
                        "status": payload["status"],
                        "processed_frames": summary["processing"]["processed_frames"],
                        "elapsed_seconds": summary["processing"]["elapsed_seconds"],
                        "device_used": summary["runtime"]["device_used"],
                        "coverage": summary["coverage"],
                        "validation": summary["validation"],
                        "scoring_readiness": summary["scoring_readiness"],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )


if __name__ == "__main__":
    main()
