"""SAM MHR70 adapter with explicit unknown confidence and source provenance."""
from __future__ import annotations

import math
import os
from fractions import Fraction
from pathlib import Path
from typing import Sequence

import numpy as np

from rallymate_vision.pose.base import PoseModelMetadata
from rallymate_vision.pose.metadata import MHR70_NAMES, keypoint_schema
from rallymate_vision.sam3d_evaluation import COORDINATES_2D, COORDINATES_3D, FRAME_VERSION, measure_sam3d_frame
from rallymate_vision.sam3d_runner import Sam3DEngine, Sam3DProcess


class Sam3DPoseAdapter:
    """Full source frame + tracked bbox, not the confidence-based ROI protocol."""

    def __init__(self, model_path: Path, *, device: str, runtime: str = "pytorch",
                 profile: str = "realtime", config_path: Path | None = None):
        if runtime != "pytorch":
            raise ValueError("SAM 3D requires the official pytorch runtime")
        self.model_path, self.profile = Path(model_path).resolve(), profile
        expected_config = self.model_path.parent / "model_config.yaml"
        if config_path is not None and Path(config_path).resolve() != expected_config:
            raise ValueError("SAM pose_config must be the checkpoint's model_config.yaml")
        runtime_dir = Path(os.environ.get("RALLYMATE_SAM3D_RUNTIME",
                                         Path(__file__).resolve().parents[2] / "runtime" / "sam3d"))
        kwargs = dict(runtime_dir=runtime_dir, device=device,
                      inference_type=os.environ.get("RALLYMATE_SAM3D_MODE", "body"))
        python = os.environ.get("RALLYMATE_SAM3D_PYTHON")
        self.engine = (Sam3DProcess(Path(python), self.model_path, **kwargs) if python
                       else Sam3DEngine(self.model_path, **kwargs))
        self.last_records: list[dict] = []
        self._last_seen: dict = {}
        self._epochs: dict = {}
        self._video_sha: str | None = None

    def reset_sequence(self) -> None:
        self.last_records = []
        self._last_seen.clear()
        self._epochs.clear()
        self._video_sha = None

    def metadata(self) -> PoseModelMetadata:
        raw = self.engine.metadata
        return PoseModelMetadata(
            backend="sam3d", runtime="pytorch", profile=self.profile,
            model_name=f"sam-3d-body-{raw['model_variant']}", model_path=str(self.model_path),
            model_sha256=raw["model_sha256"], config_path=raw["config_path"],
            config_sha256=raw["config_sha256"], input_size=tuple(raw["input_size"]),
            native_keypoint_format="mhr70", native_keypoint_count=70,
            keypoint_schema_version="1.0.0", load_seconds=round(raw["load_seconds"], 6),
            load_semantics="official_sam_checkpoint_hash_verified_materialized_on_cuda",
            pose_score_semantics="not_provided_by_sam3d",
        )

    def details(self) -> dict:
        return {**self.metadata().to_dict(), **self.engine.metadata,
                "profile": self.profile,
                "joint_mapping": keypoint_schema("mhr70"),
                "observation_semantics": "monocular_model_estimate",
                "scoring_confidence_available": False}

    @staticmethod
    def _source_valid(binding: dict) -> bool:
        sha = binding.get("video_sha256")
        if not isinstance(sha, str) or len(sha) != 64 or any(c not in "0123456789abcdefABCDEF" for c in sha):
            return False
        for key in ("frame_index", "processed_index", "camera_reference_epoch"):
            if isinstance(binding.get(key), bool) or not isinstance(binding.get(key), int) or binding[key] < 0:
                return False
        for key in ("pts", "time_base_num", "time_base_den"):
            if isinstance(binding.get(key), bool) or not isinstance(binding.get(key), int):
                return False
        if binding["time_base_num"] <= 0 or binding["time_base_den"] <= 0:
            return False
        timestamp = binding.get("timestamp_ms")
        return (isinstance(timestamp, (int, float)) and math.isfinite(timestamp)
                and abs(timestamp - binding["pts"] * binding["time_base_num"] / binding["time_base_den"] * 1000) <= .51)

    def estimate(self, frame: np.ndarray, player_detections: Sequence[dict], max_players: int,
                 *, source_binding: dict | None = None) -> list[dict]:
        source = dict(source_binding or {})
        # Also normalize at the adapter boundary for callers that supply decoded
        # milliseconds as a float. Preserve the exact PTS/time base and reject
        # mismatched envelopes rather than repairing their provenance silently.
        if self._source_valid(source):
            source["timestamp_ms"] = round(Fraction(
                source["pts"] * source["time_base_num"] * 1000, source["time_base_den"],
            ))
        if source.get("video_sha256") != self._video_sha:
            self.reset_sequence()
            self._video_sha = source.get("video_sha256")
        self.last_records = []
        height, width = frame.shape[:2]
        players = sorted(player_detections,
                         key=lambda p: (p["bbox_px"][2] - p["bbox_px"][0]) * (p["bbox_px"][3] - p["bbox_px"][1]),
                         reverse=True)[:max_players]
        if not players:
            self.last_records.append({**source, "schema_version": FRAME_VERSION, "status": "skipped",
                                      "reason": "no_detected_subject", "person_track_id": None,
                                      "selection_epoch": None, "image_size": [width, height]})
            return []
        requests, accepted = [], []
        for player in players:
            track_id = player.get("track_id")
            processed = source.get("processed_index")
            if track_id in self._last_seen and processed != self._last_seen[track_id] + 1:
                self._epochs[track_id] += 1
            self._epochs.setdefault(track_id, 0)
            if isinstance(processed, int):
                self._last_seen[track_id] = processed
            binding = {**source, "person_track_id": track_id,
                       "selection_epoch": self._epochs[track_id]}
            box = np.asarray(player["bbox_px"], dtype=float)
            record = {**binding, "schema_version": FRAME_VERSION, "image_size": [width, height],
                      "bbox_px": box.tolist(), "status": "skipped", "confidence": None,
                      "confidence_semantics": "not_provided_by_sam3d",
                      "selection_semantics": "detector_track_epoch_primary_selection_applied_downstream",
                      "coordinate_system_2d": COORDINATES_2D, "coordinate_system_3d": COORDINATES_3D,
                      "units_3d": "model_units", "camera_intrinsics": "official_default_fov_uncalibrated",
                      "observation_semantics": "monocular_model_estimate"}
            self.last_records.append(record)
            if not self._source_valid(source) or track_id is None:
                record["reason"] = "source_binding_unverified"
                continue
            if (box.shape != (4,) or not np.isfinite(box).all() or not
                    (0 <= box[0] < box[2] <= width and 0 <= box[1] < box[3] <= height)):
                record["reason"] = "invalid_source_bbox"
                continue
            requests.append(box.tolist())
            accepted.append((record, binding))
        if not requests:
            return []
        try:
            outputs = self.engine.infer(frame, requests)
            if len(outputs) != len(requests):
                raise ValueError("SAM worker output count does not match subject requests")
        except Exception as exc:
            for record, _ in accepted:
                record.update(status="failed", reason="sam_worker_error", error_type=type(exc).__name__)
            return []
        poses = []
        for (record, binding), output in zip(accepted, outputs):
            record.update({key: value for key, value in output.items() if key not in {"keypoints_2d", "keypoints_3d"}})
            if output["status"] != "ok":
                continue
            xy, xyz = output["keypoints_2d"], output["keypoints_3d"]
            record["keypoints_2d"] = dict(zip(MHR70_NAMES, xy))
            record["keypoints_3d"] = dict(zip(MHR70_NAMES, xyz))
            record["measurements_3d"] = measure_sam3d_frame(record)
            points = []
            for definition, point, point3d in zip(keypoint_schema("mhr70")["keypoints"], xy, xyz):
                raw_x, raw_y = float(point[0]), float(point[1])
                x, y = min(width, max(0., raw_x)), min(height, max(0., raw_y))
                serialized = {
                    **definition, "x_px": round(x, 3), "y_px": round(y, 3),
                    "raw_x_px": raw_x, "raw_y_px": raw_y,
                    "x_normalized": round(x / width, 6), "y_normalized": round(y / height, 6),
                    "confidence": None, "confidence_semantics": "not_provided_by_sam3d",
                    "source": "sam3d", "observation_semantics": "model_estimate",
                    "observed": None, "visibility": None,
                    "in_frame": 0 <= raw_x < width and 0 <= raw_y < height,
                    "model_coordinates_3d": point3d,
                }
                points.append(serialized)
            poses.append({"person_track_id": binding["person_track_id"], "confidence": None,
                          "confidence_semantics": "not_provided_by_sam3d", "source": "sam3d",
                          "observation_semantics": "monocular_model_estimate", "status": "ok",
                          "source_binding": binding, "roi_bbox_px": record["bbox_px"],
                          "coordinate_space": "original_frame", "keypoint_format": "mhr70",
                          "keypoints": points, "native_keypoints_2d": xy, "native_keypoints_3d": xyz,
                          "coordinate_system_3d": COORDINATES_3D, "units_3d": "model_units",
                          "camera_intrinsics": "official_default_fov_uncalibrated",
                          "measurements_3d": record["measurements_3d"]})
        return poses

    def close(self):
        self.engine.close()
