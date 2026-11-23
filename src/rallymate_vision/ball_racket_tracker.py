"""2D Constant-Acceleration Kalman ball tracker and kinematic contact-candidate detector.

This module upgrades ball tracking across short motion-blur dropouts and detects
kinematic ball-racket contact candidates. Candidate outputs explicitly preserve
``confirmed_contact=False`` and never emit confirmed hit counts or technique grades.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

import numpy as np

from rallymate_vision.utils import clamp


BALL_CLASS_ALIASES = frozenset({"ball", "sports ball", "tennis_ball"})
RACKET_HEAD_CLASS_ALIASES = frozenset({"racket_head"})
RACKET_CLASS_ALIASES = frozenset({"racket", "tennis racket", "tennis_racket", "racket_head"})
CONTACT_CANDIDATE_SEMANTICS = "kinematic_ball_racket_contact_candidate"


def _finite_float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    val = float(value)
    return val if math.isfinite(val) else None


def _extract_bbox_px(det: Mapping[str, Any], width: int, height: int) -> list[float] | None:
    raw = det.get("bbox_px")
    if isinstance(raw, (list, tuple)) and len(raw) == 4:
        vals = [_finite_float(v) for v in raw]
        if all(v is not None for v in vals):
            x1, y1, x2, y2 = (float(v) for v in vals)
            if x2 >= x1 and y2 >= y1:
                return [
                    clamp(x1, 0.0, float(width)),
                    clamp(y1, 0.0, float(height)),
                    clamp(x2, 0.0, float(width)),
                    clamp(y2, 0.0, float(height)),
                ]
    norm = det.get("bbox_normalized")
    if isinstance(norm, (list, tuple)) and len(norm) == 4:
        vals = [_finite_float(v) for v in norm]
        if all(v is not None for v in vals):
            x1, y1, x2, y2 = (float(v) for v in vals)
            if x2 >= x1 and y2 >= y1:
                return [
                    clamp(x1 * width, 0.0, float(width)),
                    clamp(y1 * height, 0.0, float(height)),
                    clamp(x2 * width, 0.0, float(width)),
                    clamp(y2 * height, 0.0, float(height)),
                ]
    cx_cy = det.get("center_px")
    if isinstance(cx_cy, (list, tuple)) and len(cx_cy) == 2:
        cx, cy = _finite_float(cx_cy[0]), _finite_float(cx_cy[1])
        if cx is not None and cy is not None:
            half = max(3.0, min(width, height) * 0.008)
            return [
                clamp(cx - half, 0.0, float(width)),
                clamp(cy - half, 0.0, float(height)),
                clamp(cx + half, 0.0, float(width)),
                clamp(cy + half, 0.0, float(height)),
            ]
    return None


def _bbox_center(bbox: Sequence[float]) -> tuple[float, float]:
    return 0.5 * (float(bbox[0]) + float(bbox[2])), 0.5 * (float(bbox[1]) + float(bbox[3]))


def _point_to_bbox_distance(x: float, y: float, bbox: Sequence[float]) -> float:
    x1, y1, x2, y2 = (float(v) for v in bbox)
    dx = max(x1 - x, 0.0, x - x2)
    dy = max(y1 - y, 0.0, y - y2)
    return math.hypot(dx, dy)


@dataclass
class _BallKalmanTrack:
    track_id: int
    state: np.ndarray  # [x, y, vx, vy, ax, ay]
    covariance: np.ndarray  # (6, 6)
    last_observed_xy: tuple[float, float]
    last_bbox_px: list[float]
    last_timestamp_ms: int | None
    last_frame_index: int | None
    hits: int = 1
    missed: int = 0
    last_confidence: float | None = None
    history: list[dict[str, Any]] = field(default_factory=list)


class KalmanBallTracker:
    """6-state ``[x, y, vx, vy, ax, ay]`` constant-acceleration 2D Kalman ball tracker.

    Features:
    - Mahalanobis + Euclidean gating with adaptive maneuver covariance boost across sharp
      bounces and racket impacts to keep ``track_id`` stable.
    - Explicit coasting across 1..``max_coast_frames`` (default 6) motion-blur dropouts,
      clearly tagging every point as ``"observed"`` vs ``"kalman_predicted"``.
    - Source-timestamp VFR-aware state transition matrices ``F(dt)`` and ``Q(dt)``.
    """

    _H = np.array(
        [
            [1.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0, 0.0, 0.0, 0.0],
        ],
        dtype=np.float64,
    )

    def __init__(
        self,
        *,
        max_coast_frames: int = 6,
        min_hits_to_coast: int = 1,
        default_fps: float = 30.0,
        mahalanobis_gate_sq: float = 11.34,  # ~99.6% chi^2(2)
        euclidean_gate_frac: float = 0.08,
        min_euclidean_gate_px: float = 42.0,
        measurement_std_px: float = 4.0,
        process_jerk_std: float = 2800.0,
        maneuver_vel_std: float = 1200.0,
        maneuver_accel_std: float = 6000.0,
    ) -> None:
        if max_coast_frames < 0 or max_coast_frames > 30:
            raise ValueError("max_coast_frames must be in [0, 30]")
        if default_fps <= 0.0:
            raise ValueError("default_fps must be positive")
        self.max_coast_frames = int(max_coast_frames)
        self.min_hits_to_coast = max(1, int(min_hits_to_coast))
        self.default_fps = float(default_fps)
        self.mahalanobis_gate_sq = float(mahalanobis_gate_sq)
        self.euclidean_gate_frac = float(euclidean_gate_frac)
        self.min_euclidean_gate_px = float(min_euclidean_gate_px)
        self.measurement_std_px = float(measurement_std_px)
        self.process_jerk_std = float(process_jerk_std)
        self.maneuver_vel_std = float(maneuver_vel_std)
        self.maneuver_accel_std = float(maneuver_accel_std)

        self._tracks: list[_BallKalmanTrack] = []
        self._next_id = 1
        self._last_timestamp_ms: int | None = None
        self._step_counter = 0

    def reset(self) -> None:
        self._tracks.clear()
        self._next_id = 1
        self._last_timestamp_ms = None
        self._step_counter = 0

    @property
    def active_tracks(self) -> list[_BallKalmanTrack]:
        return list(self._tracks)

    @staticmethod
    def _transition_matrix(dt: float) -> np.ndarray:
        dt2 = 0.5 * dt * dt
        return np.array(
            [
                [1.0, 0.0, dt, 0.0, dt2, 0.0],
                [0.0, 1.0, 0.0, dt, 0.0, dt2],
                [0.0, 0.0, 1.0, 0.0, dt, 0.0],
                [0.0, 0.0, 0.0, 1.0, 0.0, dt],
                [0.0, 0.0, 0.0, 0.0, 1.0, 0.0],
                [0.0, 0.0, 0.0, 0.0, 0.0, 1.0],
            ],
            dtype=np.float64,
        )

    def _process_noise(self, dt: float) -> np.ndarray:
        g = np.array([(dt ** 3) / 6.0, 0.5 * (dt ** 2), dt], dtype=np.float64)
        q1d = (self.process_jerk_std ** 2) * np.outer(g, g)
        # Small positive floor guarantees SPD stability for tiny dt.
        q1d += np.diag([1e-2, 1e-1, 10.0])
        q = np.zeros((6, 6), dtype=np.float64)
        for i in range(3):
            for j in range(3):
                val = q1d[i, j]
                q[2 * i, 2 * j] = val
                q[2 * i + 1, 2 * j + 1] = val
        return q

    def _measurement_noise(self) -> np.ndarray:
        var = self.measurement_std_px ** 2
        return np.array([[var, 0.0], [0.0, var]], dtype=np.float64)

    def _initial_covariance(self) -> np.ndarray:
        sz2 = self.measurement_std_px ** 2
        return np.diag([sz2, sz2, 1600.0 ** 2, 1600.0 ** 2, 4500.0 ** 2, 4500.0 ** 2]).astype(np.float64)

    def _maneuver_covariance(self) -> np.ndarray:
        return np.diag(
            [
                0.0,
                0.0,
                self.maneuver_vel_std ** 2,
                self.maneuver_vel_std ** 2,
                self.maneuver_accel_std ** 2,
                self.maneuver_accel_std ** 2,
            ]
        ).astype(np.float64)

    def _dt_seconds(self, track: _BallKalmanTrack, timestamp_ms: int | None) -> float:
        if timestamp_ms is not None and track.last_timestamp_ms is not None:
            dt_ms = timestamp_ms - track.last_timestamp_ms
            if dt_ms > 0:
                return max(1e-3, float(dt_ms) / 1000.0)
        return 1.0 / self.default_fps

    @staticmethod
    def _emit_point(
        track: _BallKalmanTrack,
        *,
        observed: bool,
        frame_width: int,
        frame_height: int,
        frame_index: int | None,
        timestamp_ms: int | None,
        confidence: float | None,
    ) -> dict[str, Any]:
        x, y, vx, vy, ax, ay = (float(v) for v in track.state.tolist())
        x_clamped = clamp(x, 0.0, float(frame_width))
        y_clamped = clamp(y, 0.0, float(frame_height))
        if observed:
            bbox = list(track.last_bbox_px)
        else:
            bw = max(4.0, track.last_bbox_px[2] - track.last_bbox_px[0])
            bh = max(4.0, track.last_bbox_px[3] - track.last_bbox_px[1])
            bbox = [
                round(clamp(x_clamped - 0.5 * bw, 0.0, float(frame_width)), 3),
                round(clamp(y_clamped - 0.5 * bh, 0.0, float(frame_height)), 3),
                round(clamp(x_clamped + 0.5 * bw, 0.0, float(frame_width)), 3),
                round(clamp(y_clamped + 0.5 * bh, 0.0, float(frame_height)), 3),
            ]
        return {
            "track_id": track.track_id,
            "class_name": "ball",
            "point_source": "observed" if observed else "kalman_predicted",
            "observed": bool(observed),
            "coast_age": 0 if observed else int(track.missed),
            "frame_index": frame_index,
            "timestamp_ms": timestamp_ms,
            "frame_width": int(frame_width),
            "frame_height": int(frame_height),
            "x_px": round(x_clamped, 3),
            "y_px": round(y_clamped, 3),
            "x_normalized": round(clamp(x_clamped / max(float(frame_width), 1.0), 0.0, 1.0), 6),
            "y_normalized": round(clamp(y_clamped / max(float(frame_height), 1.0), 0.0, 1.0), 6),
            "vx_px_s": round(vx, 4),
            "vy_px_s": round(vy, 4),
            "ax_px_s2": round(ax, 4),
            "ay_px_s2": round(ay, 4),
            "speed_px_s": round(math.hypot(vx, vy), 4),
            "accel_px_s2": round(math.hypot(ax, ay), 4),
            "state_6d": [round(float(v), 6) for v in track.state.tolist()],
            "bbox_px": bbox,
            "confidence": round(float(confidence), 5) if (observed and confidence is not None) else None,
        }

    def update(
        self,
        detections: Sequence[Mapping[str, Any]],
        frame_width: int,
        frame_height: int,
        *,
        frame_index: int | None = None,
        timestamp_ms: int | None = None,
        emit_coasted: bool = True,
    ) -> list[dict[str, Any]]:
        """Update ball tracks for one frame and return observed + coasted ball points."""
        if frame_width <= 0 or frame_height <= 0:
            raise ValueError("frame_width and frame_height must be positive")
        if timestamp_ms is not None:
            if isinstance(timestamp_ms, bool) or not isinstance(timestamp_ms, int) or timestamp_ms < 0:
                raise ValueError("timestamp_ms must be a non-negative integer")
            if self._last_timestamp_ms is not None and timestamp_ms < self._last_timestamp_ms:
                # Non-monotonic seek or new clip resets tracker identities fail-closed.
                self._tracks.clear()
            self._last_timestamp_ms = timestamp_ms

        resolved_frame_index = self._step_counter if frame_index is None else int(frame_index)
        self._step_counter += 1
        frame_diag = math.hypot(float(frame_width), float(frame_height))
        R = self._measurement_noise()

        # Filter to ball detections with valid geometry.
        ball_dets: list[tuple[dict[str, Any], list[float], np.ndarray, float]] = []
        for det in detections:
            cls_name = str(det.get("class_name", "ball")).strip().lower()
            if cls_name not in BALL_CLASS_ALIASES:
                continue
            bbox = _extract_bbox_px(det, frame_width, frame_height)
            if bbox is None:
                continue
            cx, cy = _bbox_center(bbox)
            conf = _finite_float(det.get("confidence"))
            conf_val = clamp(conf, 0.0, 1.0) if conf is not None else 0.5
            ball_dets.append((dict(det), bbox, np.array([cx, cy], dtype=np.float64), conf_val))
        ball_dets.sort(key=lambda item: item[3], reverse=True)

        # Predict every active track forward.
        predictions: list[tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, float]] = []
        for track in self._tracks:
            dt = self._dt_seconds(track, timestamp_ms)
            F = self._transition_matrix(dt)
            Q = self._process_noise(dt)
            x_pred = F @ track.state
            P_pred = F @ track.covariance @ F.T + Q
            predictions.append((F, x_pred, P_pred, Q, dt))

        # Associate detections greedily by minimum gated cost.
        matched_track_indices: set[int] = set()
        matched_det_indices: set[int] = set()
        candidate_pairs: list[tuple[float, int, int, float, float]] = []
        for t_idx, track in enumerate(self._tracks):
            F, x_pred, P_pred, _, dt = predictions[t_idx]
            z_pred = self._H @ x_pred
            S = self._H @ P_pred @ self._H.T + R
            S_inv = np.linalg.inv(S)
            base_gate_px = max(self.min_euclidean_gate_px, self.euclidean_gate_frac * frame_diag) * (
                1.0 + 0.35 * track.missed
            )
            for d_idx, (_, _, z, _) in enumerate(ball_dets):
                innov = z - z_pred
                maha_sq = float(innov.T @ S_inv @ innov)
                euclid_px = float(np.hypot(innov[0], innov[1]))
                obs_step_px = math.hypot(
                    float(z[0]) - track.last_observed_xy[0],
                    float(z[1]) - track.last_observed_xy[1],
                )
                passes_maha = maha_sq <= self.mahalanobis_gate_sq and euclid_px <= base_gate_px * 1.8
                passes_euclid = euclid_px <= base_gate_px
                passes_impact = track.missed <= 1 and obs_step_px <= base_gate_px * 1.15
                if not (passes_maha or passes_euclid or passes_impact):
                    continue
                cost = 0.5 * min(maha_sq, 36.0) + 0.5 * (euclid_px / max(base_gate_px, 1.0)) * self.mahalanobis_gate_sq
                candidate_pairs.append((cost, t_idx, d_idx, maha_sq, euclid_px))

        candidate_pairs.sort(key=lambda item: item[0])
        track_to_det: dict[int, tuple[int, float]] = {}
        for _, t_idx, d_idx, maha_sq, _ in candidate_pairs:
            if t_idx in matched_track_indices or d_idx in matched_det_indices:
                continue
            matched_track_indices.add(t_idx)
            matched_det_indices.add(d_idx)
            track_to_det[t_idx] = (d_idx, maha_sq)

        outputs: list[dict[str, Any]] = []
        identity_6 = np.eye(6, dtype=np.float64)
        maneuver_q = self._maneuver_covariance()

        # Update matched tracks and coast unmatched tracks.
        surviving_tracks: list[_BallKalmanTrack] = []
        for t_idx, track in enumerate(self._tracks):
            F, x_pred, P_pred, _, _ = predictions[t_idx]
            if t_idx in track_to_det:
                d_idx, maha_sq = track_to_det[t_idx]
                _, bbox, z, conf_val = ball_dets[d_idx]
                P_use = P_pred
                if track.hits >= 2 and maha_sq > self.mahalanobis_gate_sq:
                    # Sharp bounce or racket impact: boost velocity/accel uncertainty before update.
                    P_use = P_pred + F @ maneuver_q @ F.T
                S_use = self._H @ P_use @ self._H.T + R
                K = P_use @ self._H.T @ np.linalg.inv(S_use)
                innov = z - (self._H @ x_pred)
                x_new = x_pred + K @ innov
                I_KH = identity_6 - K @ self._H
                P_new = I_KH @ P_use @ I_KH.T + K @ R @ K.T

                track.state = x_new
                track.covariance = 0.5 * (P_new + P_new.T)
                track.last_observed_xy = (float(z[0]), float(z[1]))
                track.last_bbox_px = list(bbox)
                track.last_timestamp_ms = timestamp_ms
                track.last_frame_index = resolved_frame_index
                track.hits += 1
                track.missed = 0
                track.last_confidence = conf_val

                point = self._emit_point(
                    track,
                    observed=True,
                    frame_width=frame_width,
                    frame_height=frame_height,
                    frame_index=resolved_frame_index,
                    timestamp_ms=timestamp_ms,
                    confidence=conf_val,
                )
                track.history.append(point)
                outputs.append(point)
                surviving_tracks.append(track)
            else:
                track.missed += 1
                if track.missed <= self.max_coast_frames:
                    track.state = x_pred
                    track.covariance = 0.5 * (P_pred + P_pred.T)
                    track.last_timestamp_ms = timestamp_ms
                    track.last_frame_index = resolved_frame_index
                    if emit_coasted and track.hits >= self.min_hits_to_coast:
                        point = self._emit_point(
                            track,
                            observed=False,
                            frame_width=frame_width,
                            frame_height=frame_height,
                            frame_index=resolved_frame_index,
                            timestamp_ms=timestamp_ms,
                            confidence=None,
                        )
                        track.history.append(point)
                        outputs.append(point)
                    surviving_tracks.append(track)

        # Spawn new tracks for unmatched detections.
        for d_idx, (_, bbox, z, conf_val) in enumerate(ball_dets):
            if d_idx in matched_det_indices:
                continue
            track = _BallKalmanTrack(
                track_id=self._next_id,
                state=np.array([float(z[0]), float(z[1]), 0.0, 0.0, 0.0, 0.0], dtype=np.float64),
                covariance=self._initial_covariance(),
                last_observed_xy=(float(z[0]), float(z[1])),
                last_bbox_px=list(bbox),
                last_timestamp_ms=timestamp_ms,
                last_frame_index=resolved_frame_index,
                hits=1,
                missed=0,
                last_confidence=conf_val,
            )
            self._next_id += 1
            point = self._emit_point(
                track,
                observed=True,
                frame_width=frame_width,
                frame_height=frame_height,
                frame_index=resolved_frame_index,
                timestamp_ms=timestamp_ms,
                confidence=conf_val,
            )
            track.history.append(point)
            outputs.append(point)
            surviving_tracks.append(track)

        self._tracks = surviving_tracks
        return outputs

    def track_sequence(
        self,
        frame_records: Sequence[Mapping[str, Any]],
        *,
        emit_coasted: bool = True,
    ) -> list[dict[str, Any]]:
        """Run tracker sequentially over RallyMate frame records or detection dicts."""
        self.reset()
        trajectory: list[dict[str, Any]] = []
        for idx, record in enumerate(frame_records):
            frame_meta = record.get("frame") if isinstance(record.get("frame"), Mapping) else record
            width = int(frame_meta.get("width") or record.get("frame_width") or 1280)
            height = int(frame_meta.get("height") or record.get("frame_height") or 720)
            frame_index = frame_meta.get("index", record.get("frame_index", idx))
            timestamp_ms = frame_meta.get("timestamp_ms", record.get("timestamp_ms"))
            dets = record.get("detections", [])
            trajectory.extend(
                self.update(
                    dets,
                    width,
                    height,
                    frame_index=int(frame_index) if frame_index is not None else idx,
                    timestamp_ms=int(timestamp_ms) if timestamp_ms is not None else None,
                    emit_coasted=emit_coasted,
                )
            )
        return trajectory


def _index_racket_observations(
    racket_observations: Sequence[Mapping[str, Any]] | Mapping[int, Sequence[Mapping[str, Any]]] | None,
) -> list[dict[str, Any]]:
    if racket_observations is None:
        return []
    indexed: list[dict[str, Any]] = []
    if isinstance(racket_observations, Mapping):
        for frame_idx, items in racket_observations.items():
            for item in items:
                row = dict(item)
                row.setdefault("frame_index", int(frame_idx))
                indexed.append(row)
        return indexed
    for item in racket_observations:
        if "detections" in item and isinstance(item.get("frame"), Mapping):
            f_meta = item["frame"]
            for det in item.get("detections", []):
                cls_name = str(det.get("class_name", "")).strip().lower()
                if cls_name in RACKET_CLASS_ALIASES:
                    indexed.append(
                        {
                            **det,
                            "frame_index": f_meta.get("index"),
                            "timestamp_ms": f_meta.get("timestamp_ms"),
                            "frame_width": f_meta.get("width"),
                            "frame_height": f_meta.get("height"),
                        }
                    )
        else:
            cls_name = str(item.get("class_name", "tennis_racket")).strip().lower()
            if cls_name in RACKET_CLASS_ALIASES:
                indexed.append(dict(item))
    return indexed


def _index_wrist_observations(
    wrist_observations: Sequence[Mapping[str, Any]] | Mapping[int, Sequence[Mapping[str, Any]]] | None,
) -> list[dict[str, Any]]:
    if wrist_observations is None:
        return []
    raw_items: list[Mapping[str, Any]] = []
    if isinstance(wrist_observations, Mapping):
        for frame_idx, items in wrist_observations.items():
            for item in items:
                raw_items.append({"frame_index": int(frame_idx), **item})
    else:
        raw_items = list(wrist_observations)

    indexed: list[dict[str, Any]] = []
    for item in raw_items:
        # Support full RallyMate frame/pose records directly as well as flat wrist dicts.
        if "poses" in item and isinstance(item.get("frame"), Mapping):
            f_meta = item["frame"]
            for pose in item.get("poses", []):
                for kp in pose.get("keypoints", []):
                    kp_name = str(kp.get("name", ""))
                    if kp_name in {"left_wrist", "right_wrist"}:
                        x_px = _finite_float(kp.get("x_px"))
                        y_px = _finite_float(kp.get("y_px"))
                        if x_px is not None and y_px is not None:
                            indexed.append(
                                {
                                    "frame_index": f_meta.get("index"),
                                    "timestamp_ms": f_meta.get("timestamp_ms"),
                                    "person_track_id": pose.get("person_track_id"),
                                    "side": kp_name.split("_")[0],
                                    "x_px": x_px,
                                    "y_px": y_px,
                                    "confidence": _finite_float(kp.get("confidence")),
                                }
                            )
        else:
            x_px = _finite_float(item.get("x_px", item.get("x")))
            y_px = _finite_float(item.get("y_px", item.get("y")))
            if x_px is not None and y_px is not None:
                indexed.append(
                    {
                        "frame_index": item.get("frame_index"),
                        "timestamp_ms": item.get("timestamp_ms"),
                        "person_track_id": item.get("person_track_id"),
                        "side": item.get("side", "unknown"),
                        "x_px": x_px,
                        "y_px": y_px,
                        "confidence": _finite_float(item.get("confidence")),
                    }
                )
    return indexed


def _ball_xy_px(point: Mapping[str, Any], width: int, height: int) -> tuple[float, float] | None:
    x_px = _finite_float(point.get("x_px"))
    y_px = _finite_float(point.get("y_px"))
    if x_px is not None and y_px is not None:
        return x_px, y_px
    x_norm = _finite_float(point.get("x_normalized", point.get("x")))
    y_norm = _finite_float(point.get("y_normalized", point.get("y")))
    if x_norm is not None and y_norm is not None:
        return x_norm * width, y_norm * height
    bbox = _extract_bbox_px(point, width, height)
    if bbox is not None:
        return _bbox_center(bbox)
    return None


def _point_time_ms(point: Mapping[str, Any], index_fallback: int, default_fps: float) -> int:
    ts = point.get("timestamp_ms")
    if isinstance(ts, (int, float)) and not isinstance(ts, bool) and math.isfinite(ts):
        return int(round(float(ts)))
    fi = point.get("frame_index")
    idx = int(fi) if isinstance(fi, int) and not isinstance(fi, bool) else index_fallback
    return int(round(idx * (1000.0 / default_fps)))


def _closest_racket_distance(
    ball_x: float,
    ball_y: float,
    frame_index: int | None,
    timestamp_ms: int,
    racket_list: Sequence[Mapping[str, Any]],
    wrist_list: Sequence[Mapping[str, Any]],
    width: int,
    height: int,
    diag: float,
    *,
    max_distance_norm: float = 0.14,
    max_time_gap_ms: int = 60,
    max_frame_gap: int = 2,
) -> tuple[float | None, str, list[float] | None, str | None]:
    best_head: tuple[float, list[float]] | None = None
    best_racket: tuple[float, list[float]] | None = None

    for item in racket_list:
        item_ts = item.get("timestamp_ms")
        item_fi = item.get("frame_index")
        if item_ts is not None and abs(int(item_ts) - timestamp_ms) > max_time_gap_ms:
            continue
        if item_ts is None and frame_index is not None and item_fi is not None:
            if abs(int(item_fi) - frame_index) > max_frame_gap:
                continue
        bbox = _extract_bbox_px(item, width, height)
        if bbox is None:
            continue
        dist_norm = _point_to_bbox_distance(ball_x, ball_y, bbox) / diag
        cls_name = str(item.get("class_name", "")).strip().lower()
        if cls_name in RACKET_HEAD_CLASS_ALIASES:
            if best_head is None or dist_norm < best_head[0]:
                best_head = (dist_norm, bbox)
        else:
            if best_racket is None or dist_norm < best_racket[0]:
                best_racket = (dist_norm, bbox)

    best_wrist: tuple[float, str | None] | None = None
    for item in wrist_list:
        item_ts = item.get("timestamp_ms")
        item_fi = item.get("frame_index")
        if item_ts is not None and abs(int(item_ts) - timestamp_ms) > max_time_gap_ms:
            continue
        if item_ts is None and frame_index is not None and item_fi is not None:
            if abs(int(item_fi) - frame_index) > max_frame_gap:
                continue
        dist_norm = math.hypot(ball_x - float(item["x_px"]), ball_y - float(item["y_px"])) / diag
        side = str(item.get("side") or "") or None
        if best_wrist is None or dist_norm < best_wrist[0]:
            best_wrist = (dist_norm, side)

    # Prefer racket_head > tennis_racket > wrist_fallback among candidates inside
    # the proximity threshold so a distant background player never shadows a nearby hit.
    if best_head is not None and best_head[0] <= max_distance_norm:
        return best_head[0], "racket_head", best_head[1], None
    if best_racket is not None and best_racket[0] <= max_distance_norm:
        return best_racket[0], "tennis_racket", best_racket[1], None
    if best_wrist is not None and best_wrist[0] <= max_distance_norm:
        return best_wrist[0], "wrist_fallback", None, best_wrist[1]

    candidates: list[tuple[float, str, list[float] | None, str | None]] = []
    if best_head is not None:
        candidates.append((best_head[0], "racket_head", best_head[1], None))
    if best_racket is not None:
        candidates.append((best_racket[0], "tennis_racket", best_racket[1], None))
    if best_wrist is not None:
        candidates.append((best_wrist[0], "wrist_fallback", None, best_wrist[1]))
    if candidates:
        return min(candidates, key=lambda c: c[0])
    return None, "unavailable", None, None


def _local_wrist_speed_norm_s(
    wrist_list: Sequence[Mapping[str, Any]],
    timestamp_ms: int,
    window_ms: int,
    diag: float,
    default_fps: float,
) -> tuple[float | None, str | None]:
    if not wrist_list:
        return None, None
    grouped: dict[tuple[Any, str], list[tuple[int, float, float]]] = {}
    for idx, item in enumerate(wrist_list):
        t = _point_time_ms(item, idx, default_fps)
        if abs(t - timestamp_ms) > window_ms:
            continue
        key = (item.get("person_track_id"), str(item.get("side") or "unknown"))
        grouped.setdefault(key, []).append((t, float(item["x_px"]), float(item["y_px"])))

    peak_speed: float | None = None
    peak_side: str | None = None
    for (_, side), pts in grouped.items():
        pts.sort(key=lambda p: p[0])
        for (t0, x0, y0), (t1, x1, y1) in zip(pts, pts[1:]):
            dt_s = (t1 - t0) / 1000.0
            if dt_s <= 1e-4 or dt_s > max(0.25, window_ms / 1000.0):
                continue
            speed_norm_s = math.hypot(x1 - x0, y1 - y0) / (diag * dt_s)
            if peak_speed is None or speed_norm_s > peak_speed:
                peak_speed = speed_norm_s
                peak_side = side
    return peak_speed, peak_side


def detect_contact_candidates(
    ball_track: Sequence[Mapping[str, Any]],
    racket_observations: Sequence[Mapping[str, Any]] | Mapping[int, Sequence[Mapping[str, Any]]] | None = None,
    wrist_observations: Sequence[Mapping[str, Any]] | Mapping[int, Sequence[Mapping[str, Any]]] | None = None,
    *,
    frame_width: int | None = None,
    frame_height: int | None = None,
    default_fps: float = 30.0,
    min_turn_angle_deg: float = 35.0,
    min_impulse_norm_s2: float = 2.0,
    min_ball_speed_norm_s: float = 0.12,
    max_racket_distance_norm: float = 0.14,
    wrist_window_ms: int = 160,
    min_wrist_speed_norm_s: float = 0.25,
    require_racket_or_wrist_proximity: bool = True,
    min_confidence: float = 0.45,
    nms_window_ms: int = 200,
) -> list[dict[str, Any]]:
    """Detect kinematic ball-racket contact candidates without claiming confirmed contact.

    Fuses:
    (a) Ball velocity turning angle & acceleration impulse (from observed/Kalman states),
    (b) Spatial proximity to ``racket_head`` / ``tennis_racket`` bbox (or active wrist fallback),
    (c) Local wrist swing speed window.
    """
    if not ball_track:
        return []

    first = ball_track[0]
    width = int(frame_width or first.get("frame_width") or 1280)
    height = int(frame_height or first.get("frame_height") or 720)
    diag = max(math.hypot(float(width), float(height)), 1.0)

    racket_list = _index_racket_observations(racket_observations)
    wrist_list = _index_wrist_observations(wrist_observations)

    by_track: dict[Any, list[Mapping[str, Any]]] = {}
    for point in ball_track:
        by_track.setdefault(point.get("track_id"), []).append(point)

    raw_candidates: list[dict[str, Any]] = []
    for track_id, points in by_track.items():
        ordered = sorted(
            enumerate(points),
            key=lambda pair: (
                _point_time_ms(pair[1], pair[0], default_fps),
                int(pair[1].get("frame_index") or pair[0]),
            ),
        )
        prepared: list[tuple[Mapping[str, Any], int, int, float, float]] = []
        for pos, (orig_idx, pt) in enumerate(ordered):
            xy = _ball_xy_px(pt, width, height)
            if xy is None:
                continue
            t_ms = _point_time_ms(pt, pos, default_fps)
            f_idx = pt.get("frame_index")
            resolved_f = int(f_idx) if isinstance(f_idx, int) and not isinstance(f_idx, bool) else pos
            prepared.append((pt, resolved_f, t_ms, xy[0], xy[1]))

        if len(prepared) < 3:
            continue

        for i in range(1, len(prepared) - 1):
            _, _, t_prev, x_prev, y_prev = prepared[i - 1]
            pt_curr, f_curr, t_curr, x_curr, y_curr = prepared[i]
            _, _, t_next, x_next, y_next = prepared[i + 1]

            dt_in = (t_curr - t_prev) / 1000.0
            dt_out = (t_next - t_curr) / 1000.0
            if dt_in <= 1e-4 or dt_out <= 1e-4:
                continue

            vx_in = (x_curr - x_prev) / dt_in
            vy_in = (y_curr - y_prev) / dt_in
            vx_out = (x_next - x_curr) / dt_out
            vy_out = (y_next - y_curr) / dt_out

            speed_in_px = math.hypot(vx_in, vy_in)
            speed_out_px = math.hypot(vx_out, vy_out)
            speed_in_norm = speed_in_px / diag
            speed_out_norm = speed_out_px / diag

            if max(speed_in_norm, speed_out_norm) < min_ball_speed_norm_s:
                continue

            if speed_in_px > 1e-6 and speed_out_px > 1e-6:
                cos_angle = clamp((vx_in * vx_out + vy_in * vy_out) / (speed_in_px * speed_out_px), -1.0, 1.0)
                turn_angle_deg = math.degrees(math.acos(cos_angle))
            else:
                turn_angle_deg = 0.0

            dt_eff = 0.5 * (dt_in + dt_out)
            dv_px = math.hypot(vx_out - vx_in, vy_out - vy_in)
            fd_impulse_norm_s2 = dv_px / (diag * dt_eff)

            ax_kf = _finite_float(pt_curr.get("ax_px_s2")) or 0.0
            ay_kf = _finite_float(pt_curr.get("ay_px_s2")) or 0.0
            kf_accel_norm_s2 = math.hypot(ax_kf, ay_kf) / diag
            impulse_norm_s2 = max(fd_impulse_norm_s2, kf_accel_norm_s2)

            if impulse_norm_s2 < min_impulse_norm_s2:
                continue

            serve_toss_accel = speed_in_norm < 0.05 and speed_out_norm >= max(min_ball_speed_norm_s, 0.20)
            if not serve_toss_accel and turn_angle_deg < min_turn_angle_deg:
                continue

            racket_dist_norm, racket_source, racket_bbox, wrist_side_prox = _closest_racket_distance(
                x_curr,
                y_curr,
                f_curr,
                t_curr,
                racket_list,
                wrist_list,
                width,
                height,
                diag,
                max_distance_norm=max_racket_distance_norm,
            )
            if require_racket_or_wrist_proximity:
                if racket_dist_norm is None or racket_dist_norm > max_racket_distance_norm:
                    continue

            wrist_speed_norm_s, wrist_side_vel = _local_wrist_speed_norm_s(
                wrist_list,
                t_curr,
                wrist_window_ms,
                diag,
                default_fps,
            )
            if wrist_speed_norm_s is not None and wrist_speed_norm_s < min_wrist_speed_norm_s * 0.35:
                continue

            # Compute interpretable sub-scores and fuse into candidate confidence.
            turn_score = 0.80 if serve_toss_accel else clamp(turn_angle_deg / 120.0, 0.0, 1.0)
            impulse_score = clamp(impulse_norm_s2 / max(min_impulse_norm_s2 * 3.0, 1e-6), 0.0, 1.0)
            if racket_dist_norm is not None:
                prox_score = clamp(1.0 - (racket_dist_norm / max(max_racket_distance_norm, 1e-6)), 0.0, 1.0)
                if racket_source == "wrist_fallback":
                    prox_score *= 0.90
            else:
                prox_score = 0.0

            if wrist_speed_norm_s is not None:
                wrist_score = clamp(wrist_speed_norm_s / max(min_wrist_speed_norm_s * 2.0, 1e-6), 0.0, 1.0)
                confidence = 0.30 * turn_score + 0.30 * impulse_score + 0.25 * prox_score + 0.15 * wrist_score
            else:
                confidence = 0.35 * turn_score + 0.35 * impulse_score + 0.30 * prox_score
            confidence = round(clamp(confidence, 0.0, 1.0), 4)
            if confidence < min_confidence:
                continue

            raw_candidates.append(
                {
                    "confirmed_contact": False,
                    "semantics": CONTACT_CANDIDATE_SEMANTICS,
                    "confidence": confidence,
                    "frame_index": f_curr,
                    "timestamp_ms": t_curr,
                    "ball_track_id": track_id,
                    "ball_point_source": str(pt_curr.get("point_source", "observed")),
                    "ball_turn_angle_deg": round(turn_angle_deg, 3),
                    "impulse_norm_s2": round(impulse_norm_s2, 4),
                    "racket_distance_norm": round(racket_dist_norm, 6) if racket_dist_norm is not None else None,
                    "racket_evidence_source": racket_source,
                    "local_wrist_speed_norm_s": round(wrist_speed_norm_s, 4) if wrist_speed_norm_s is not None else None,
                    "evidence": {
                        "speed_in_norm_s": round(speed_in_norm, 4),
                        "speed_out_norm_s": round(speed_out_norm, 4),
                        "kalman_accel_norm_s2": round(kf_accel_norm_s2, 4),
                        "racket_bbox_px": racket_bbox,
                        "wrist_side": wrist_side_vel or wrist_side_prox,
                        "wrist_window_ms": int(wrist_window_ms),
                    },
                }
            )

    # Non-maximum suppression within nms_window_ms per ball track.
    raw_candidates.sort(key=lambda item: (item["confidence"], item["impulse_norm_s2"]), reverse=True)
    kept: list[dict[str, Any]] = []
    for cand in raw_candidates:
        suppressed = any(
            existing["ball_track_id"] == cand["ball_track_id"]
            and abs(existing["timestamp_ms"] - cand["timestamp_ms"]) <= nms_window_ms
            for existing in kept
        )
        if not suppressed:
            kept.append(cand)
    kept.sort(key=lambda item: (item["timestamp_ms"], item["frame_index"]))
    return kept
