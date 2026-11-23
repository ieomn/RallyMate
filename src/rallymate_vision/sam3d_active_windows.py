"""Two-stage active-window SAM 3D pre-filter ("fast screen + fine 3D").

Inspects fast per-frame YOLO detections (player bboxes + ball/racket cues) to
separate active rally/swing/footwork windows from idle dead time (empty court,
standing completely still, towel/walk breaks). Always opt-in via
``RALLYMATE_SAM3D_ACTIVE_WINDOW_GATE=1`` (default ``0``) so full-frame parity
and exact-count pipelines remain unchanged unless explicitly enabled.

Skipped idle frames emit explicit, provenance-honest ``sam3d-frame-v1`` records
with ``status="skipped"`` and ``reason="idle_window_skipped"`` preserving the
exact decoder PTS and source video SHA-256.
"""
from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass
from fractions import Fraction
import math
import os
import time
from typing import Any

from rallymate_vision.sam3d_evaluation import (
    COORDINATES_2D,
    COORDINATES_3D,
    FRAME_VERSION,
)


ACTIVE_WINDOW_GATE_ENV = "RALLYMATE_SAM3D_ACTIVE_WINDOW_GATE"
LEAD_MS_ENV = "RALLYMATE_SAM3D_ACTIVE_LEAD_MS"
TRAIL_MS_ENV = "RALLYMATE_SAM3D_ACTIVE_TRAIL_MS"
MERGE_GAP_MS_ENV = "RALLYMATE_SAM3D_ACTIVE_MERGE_GAP_MS"
MAX_BUFFER_MB_ENV = "RALLYMATE_SAM3D_ACTIVE_MAX_BUFFER_MB"

_PLAYER_CLASSES = frozenset({"player", "person"})
_BALL_CLASSES = frozenset({"ball", "sports_ball", "tennis_ball"})
_RACKET_CLASSES = frozenset({"racket", "tennis_racket", "racket_head"})


def _parse_int_env(env: Mapping[str, str], name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = env.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer between {minimum} and {maximum}") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def active_window_gate_enabled(env: Mapping[str, str] | None = None) -> bool:
    mapping = os.environ if env is None else env
    raw = mapping.get(ACTIVE_WINDOW_GATE_ENV, "0")
    if raw not in {"0", "1"}:
        raise ValueError(f"{ACTIVE_WINDOW_GATE_ENV} must be 0 or 1")
    return raw == "1"


@dataclass(frozen=True)
class ActiveWindowConfig:
    lead_ms: int = 1500
    trail_ms: int = 1500
    merge_gap_ms: int = 1000
    min_trigger_frames: int = 2
    centroid_speed_threshold: float = 0.35
    aspect_rate_threshold: float = 0.65
    area_rate_threshold: float = 0.55
    ball_speed_threshold: float = 0.50
    racket_speed_threshold: float = 0.35
    racket_proximity_heights: float = 1.6
    activity_score_threshold: float = 1.0
    strong_activity_threshold: float = 2.5
    ema_alpha: float = 0.45
    max_track_gap_ms: int = 1000
    max_buffer_frames: int = 120
    max_buffer_bytes: int = 256 * 1024 * 1024

    def __post_init__(self) -> None:
        if self.lead_ms < 0 or self.trail_ms < 0 or self.merge_gap_ms < 0:
            raise ValueError("active-window lead/trail/merge timings must be non-negative")
        if self.min_trigger_frames < 1:
            raise ValueError("min_trigger_frames must be at least 1")
        if not 0.0 < self.ema_alpha <= 1.0:
            raise ValueError("ema_alpha must be in (0, 1]")
        if self.max_buffer_frames < 1 or self.max_buffer_bytes < 1:
            raise ValueError("active-window buffer bounds must be positive")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> ActiveWindowConfig:
        mapping = os.environ if env is None else env
        lead_ms = _parse_int_env(mapping, LEAD_MS_ENV, 1500, minimum=0, maximum=10000)
        trail_ms = _parse_int_env(mapping, TRAIL_MS_ENV, 1500, minimum=0, maximum=10000)
        merge_gap_ms = _parse_int_env(mapping, MERGE_GAP_MS_ENV, 1000, minimum=0, maximum=10000)
        max_buffer_mb = _parse_int_env(mapping, MAX_BUFFER_MB_ENV, 256, minimum=1, maximum=2048)
        return cls(
            lead_ms=lead_ms,
            trail_ms=trail_ms,
            merge_gap_ms=merge_gap_ms,
            max_buffer_bytes=max_buffer_mb * 1024 * 1024,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FrameActivitySignal:
    frame_index: int
    timestamp_ms: int
    player_count: int
    player_centroid_speed: float
    player_aspect_rate: float
    player_area_rate: float
    ball_speed: float
    racket_speed: float
    racket_near_player: bool
    activity_score: float
    raw_active: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class ActiveWindow:
    start_ms: int
    end_ms: int
    start_frame_index: int
    end_frame_index: int
    frame_count: int

    def to_dict(self) -> dict[str, int]:
        return {
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "start_frame_index": self.start_frame_index,
            "end_frame_index": self.end_frame_index,
            "frame_count": self.frame_count,
        }


@dataclass(frozen=True)
class ActiveWindowResult:
    windows: list[ActiveWindow]
    active_mask: list[bool]
    signals: list[FrameActivitySignal]
    stats: dict[str, Any]


@dataclass
class _TrackKinematics:
    timestamp_ms: int
    cx: float
    cy: float
    width: float
    height: float


def _bbox_geometry(bbox: Sequence[float]) -> tuple[float, float, float, float] | None:
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        return None
    x1, y1, x2, y2 = (float(v) for v in bbox)
    if not all(math.isfinite(v) for v in (x1, y1, x2, y2)):
        return None
    width = x2 - x1
    height = y2 - y1
    if width <= 0.0 or height <= 0.0:
        return None
    return (x1 + x2) * 0.5, (y1 + y2) * 0.5, width, height


def _extract_frame_fields(record: Mapping[str, Any]) -> tuple[int, int, Sequence[Mapping[str, Any]], int | None, tuple[int, int] | None]:
    if "frame" in record and isinstance(record["frame"], Mapping):
        frame_meta = record["frame"]
        frame_index = int(frame_meta.get("index", 0))
        timestamp_ms = int(frame_meta.get("timestamp_ms", 0))
        width = frame_meta.get("width")
        height = frame_meta.get("height")
        image_size = (int(width), int(height)) if width and height else None
    else:
        frame_index = int(record.get("source_frame_index", record.get("frame_index", 0)))
        timestamp_ms = int(record.get("timestamp_ms", 0))
        image = record.get("image")
        if image is not None and hasattr(image, "shape") and len(image.shape) >= 2:
            image_size = (int(image.shape[1]), int(image.shape[0]))
        else:
            raw_size = record.get("image_size")
            image_size = (int(raw_size[0]), int(raw_size[1])) if isinstance(raw_size, (list, tuple)) and len(raw_size) == 2 else None
    detections = record.get("detections")
    if detections is None:
        detections = record.get("players", ())
    subject_identity = record.get("subject_identity")
    primary_track_id = (
        subject_identity.get("source_track_id")
        if isinstance(subject_identity, Mapping)
        else record.get("primary_track_id")
    )
    return frame_index, timestamp_ms, detections, primary_track_id, image_size


class _ActivityScorer:
    """Deterministic per-frame activity extractor with EMA jitter suppression."""

    def __init__(self, config: ActiveWindowConfig) -> None:
        self.config = config
        self._player_tracks: dict[int, _TrackKinematics] = {}
        self._last_ball: tuple[int, float, float] | None = None
        self._last_racket: tuple[int, float, float] | None = None

    def score_frame(
        self,
        *,
        frame_index: int,
        timestamp_ms: int,
        detections: Sequence[Mapping[str, Any]],
        primary_track_id: int | None = None,
        image_size: tuple[int, int] | None = None,
    ) -> FrameActivitySignal:
        cfg = self.config
        players: list[tuple[int, tuple[float, float, float, float]]] = []
        balls: list[tuple[float, float, float, float]] = []
        rackets: list[tuple[float, float, float, float]] = []

        for idx, det in enumerate(detections):
            if not isinstance(det, Mapping):
                continue
            cls_name = str(det.get("class_name", "")).lower()
            geom = _bbox_geometry(det.get("bbox_px"))
            if geom is None:
                continue
            if cls_name in _PLAYER_CLASSES:
                track = det.get("track_id")
                track_id = int(track) if isinstance(track, int) and not isinstance(track, bool) else -(idx + 1)
                players.append((track_id, geom))
            elif cls_name in _BALL_CLASSES:
                balls.append(geom)
            elif cls_name in _RACKET_CLASSES:
                rackets.append(geom)

        if not players:
            self._prune_stale(timestamp_ms)
            return FrameActivitySignal(
                frame_index=frame_index,
                timestamp_ms=timestamp_ms,
                player_count=0,
                player_centroid_speed=0.0,
                player_aspect_rate=0.0,
                player_area_rate=0.0,
                ball_speed=0.0,
                racket_speed=0.0,
                racket_near_player=False,
                activity_score=0.0,
                raw_active=False,
                reasons=("empty_court",),
            )

        max_centroid_speed = 0.0
        max_aspect_rate = 0.0
        max_area_rate = 0.0
        ref_height = 0.0
        alpha = cfg.ema_alpha
        max_gap_s = cfg.max_track_gap_ms / 1000.0

        for track_id, (cx, cy, width, height) in players:
            if track_id == primary_track_id:
                ref_height = height
            elif ref_height <= 0.0:
                ref_height = max(ref_height, height)

            prev = self._player_tracks.get(track_id)
            if prev is not None and 0 < timestamp_ms - prev.timestamp_ms <= cfg.max_track_gap_ms:
                dt_s = (timestamp_ms - prev.timestamp_ms) / 1000.0
                if dt_s <= max_gap_s:
                    ema_cx = alpha * cx + (1.0 - alpha) * prev.cx
                    ema_cy = alpha * cy + (1.0 - alpha) * prev.cy
                    ema_w = alpha * width + (1.0 - alpha) * prev.width
                    ema_h = alpha * height + (1.0 - alpha) * prev.height

                    denom_h = max(ema_h, prev.height, 1.0)
                    centroid_speed = math.hypot(ema_cx - prev.cx, ema_cy - prev.cy) / (denom_h * dt_s)
                    aspect_prev = prev.width / max(prev.height, 1.0)
                    aspect_now = ema_w / max(ema_h, 1.0)
                    aspect_rate = abs(aspect_now - aspect_prev) / dt_s
                    area_prev = prev.width * prev.height
                    area_now = ema_w * ema_h
                    area_rate = abs(area_now - area_prev) / (max(area_now, area_prev, 1.0) * dt_s)

                    max_centroid_speed = max(max_centroid_speed, centroid_speed)
                    max_aspect_rate = max(max_aspect_rate, aspect_rate)
                    max_area_rate = max(max_area_rate, area_rate)
                    self._player_tracks[track_id] = _TrackKinematics(
                        timestamp_ms=timestamp_ms, cx=ema_cx, cy=ema_cy, width=ema_w, height=ema_h,
                    )
                    continue
            self._player_tracks[track_id] = _TrackKinematics(
                timestamp_ms=timestamp_ms, cx=cx, cy=cy, width=width, height=height,
            )

        if ref_height <= 0.0:
            ref_height = (image_size[1] * 0.25) if image_size else 200.0

        ball_speed = 0.0
        if balls:
            if self._last_ball is not None and 0 < timestamp_ms - self._last_ball[0] <= cfg.max_track_gap_ms:
                prev_ts, prev_bx, prev_by = self._last_ball
                dt_s = (timestamp_ms - prev_ts) / 1000.0
                best = min(balls, key=lambda item: math.hypot(item[0] - prev_bx, item[1] - prev_by))
                ball_speed = math.hypot(best[0] - prev_bx, best[1] - prev_by) / (max(ref_height, 1.0) * dt_s)
                self._last_ball = (timestamp_ms, best[0], best[1])
            else:
                self._last_ball = (timestamp_ms, balls[0][0], balls[0][1])

        racket_near_player = False
        racket_speed = 0.0
        if rackets:
            nearest_racket = None
            nearest_norm_dist = float("inf")
            nearest_player_h = ref_height
            for rx, ry, _, _ in rackets:
                for _, (px, py, _, ph) in players:
                    norm_dist = math.hypot(rx - px, ry - py) / max(ph, 1.0)
                    if norm_dist < nearest_norm_dist:
                        nearest_norm_dist = norm_dist
                        nearest_racket = (rx, ry)
                        nearest_player_h = ph
            if nearest_racket is not None:
                racket_near_player = nearest_norm_dist <= cfg.racket_proximity_heights
                if self._last_racket is not None and 0 < timestamp_ms - self._last_racket[0] <= cfg.max_track_gap_ms:
                    prev_ts, prev_rx, prev_ry = self._last_racket
                    dt_s = (timestamp_ms - prev_ts) / 1000.0
                    racket_speed = math.hypot(
                        nearest_racket[0] - prev_rx, nearest_racket[1] - prev_ry
                    ) / (max(nearest_player_h, 1.0) * dt_s)
                self._last_racket = (timestamp_ms, nearest_racket[0], nearest_racket[1])

        self._prune_stale(timestamp_ms)

        centroid_ratio = max_centroid_speed / cfg.centroid_speed_threshold
        aspect_ratio = max_aspect_rate / cfg.aspect_rate_threshold
        area_ratio = max_area_rate / cfg.area_rate_threshold
        ball_ratio = ball_speed / cfg.ball_speed_threshold if ball_speed > 0.0 else 0.0
        racket_ratio = (
            racket_speed / cfg.racket_speed_threshold
            if racket_near_player and racket_speed > 0.0
            else 0.0
        )

        score = max(centroid_ratio, aspect_ratio, area_ratio, ball_ratio, racket_ratio)
        if racket_near_player and max(centroid_ratio, aspect_ratio, area_ratio) >= 0.5:
            score += 0.25
        if balls and max(centroid_ratio, aspect_ratio, area_ratio) >= 0.5:
            score += 0.25

        reasons: list[str] = []
        if centroid_ratio >= 1.0:
            reasons.append("player_centroid_motion")
        if aspect_ratio >= 1.0:
            reasons.append("player_aspect_change")
        if area_ratio >= 1.0:
            reasons.append("player_area_change")
        if ball_ratio >= 1.0:
            reasons.append("ball_motion")
        if racket_ratio >= 1.0:
            reasons.append("racket_swing_motion")
        if not reasons and score >= cfg.activity_score_threshold:
            reasons.append("combined_player_equipment_motion")
        if not reasons:
            reasons.append("idle_low_motion")

        return FrameActivitySignal(
            frame_index=frame_index,
            timestamp_ms=timestamp_ms,
            player_count=len(players),
            player_centroid_speed=round(max_centroid_speed, 4),
            player_aspect_rate=round(max_aspect_rate, 4),
            player_area_rate=round(max_area_rate, 4),
            ball_speed=round(ball_speed, 4),
            racket_speed=round(racket_speed, 4),
            racket_near_player=racket_near_player,
            activity_score=round(score, 4),
            raw_active=score >= cfg.activity_score_threshold,
            reasons=tuple(reasons),
        )

    def _prune_stale(self, timestamp_ms: int) -> None:
        cutoff = timestamp_ms - self.config.max_track_gap_ms
        stale = [tid for tid, state in self._player_tracks.items() if state.timestamp_ms < cutoff]
        for tid in stale:
            del self._player_tracks[tid]
        if self._last_ball is not None and self._last_ball[0] < cutoff:
            self._last_ball = None
        if self._last_racket is not None and self._last_racket[0] < cutoff:
            self._last_racket = None


def select_active_sam_windows(
    frames: Iterable[Mapping[str, Any]],
    config: ActiveWindowConfig | None = None,
) -> ActiveWindowResult:
    """Deterministically select merged active SAM windows over a frame sequence."""
    cfg = config or ActiveWindowConfig()
    scorer = _ActivityScorer(cfg)
    signals: list[FrameActivitySignal] = []

    for record in frames:
        frame_index, timestamp_ms, detections, primary_track_id, image_size = _extract_frame_fields(record)
        signals.append(
            scorer.score_frame(
                frame_index=frame_index,
                timestamp_ms=timestamp_ms,
                detections=detections,
                primary_track_id=primary_track_id,
                image_size=image_size,
            )
        )

    if not signals:
        return ActiveWindowResult(
            windows=[],
            active_mask=[],
            signals=[],
            stats={
                "total_frames": 0,
                "active_frames": 0,
                "skipped_idle_frames": 0,
                "active_ratio": 0.0,
                "window_count": 0,
                "windows": [],
            },
        )

    raw_intervals: list[tuple[int, int]] = []
    streak = 0
    for sig in signals:
        if sig.raw_active:
            streak += 1
        else:
            streak = 0
        if streak >= cfg.min_trigger_frames or sig.activity_score >= cfg.strong_activity_threshold:
            raw_intervals.append((sig.timestamp_ms - cfg.lead_ms, sig.timestamp_ms + cfg.trail_ms))

    merged: list[list[int]] = []
    for start_ms, end_ms in sorted(raw_intervals):
        if merged and start_ms <= merged[-1][1] + cfg.merge_gap_ms:
            merged[-1][1] = max(merged[-1][1], end_ms)
        else:
            merged.append([start_ms, end_ms])

    active_mask = [False] * len(signals)
    interval_idx = 0
    for idx, sig in enumerate(signals):
        ts = sig.timestamp_ms
        while interval_idx < len(merged) and ts > merged[interval_idx][1]:
            interval_idx += 1
        if interval_idx < len(merged) and merged[interval_idx][0] <= ts <= merged[interval_idx][1]:
            active_mask[idx] = True

    windows: list[ActiveWindow] = []
    win_start: int | None = None
    for idx, is_active in enumerate(active_mask):
        if is_active and win_start is None:
            win_start = idx
        elif not is_active and win_start is not None:
            windows.append(
                ActiveWindow(
                    start_ms=signals[win_start].timestamp_ms,
                    end_ms=signals[idx - 1].timestamp_ms,
                    start_frame_index=signals[win_start].frame_index,
                    end_frame_index=signals[idx - 1].frame_index,
                    frame_count=idx - win_start,
                )
            )
            win_start = None
    if win_start is not None:
        windows.append(
            ActiveWindow(
                start_ms=signals[win_start].timestamp_ms,
                end_ms=signals[-1].timestamp_ms,
                start_frame_index=signals[win_start].frame_index,
                end_frame_index=signals[-1].frame_index,
                frame_count=len(signals) - win_start,
            )
        )

    total = len(signals)
    active_count = sum(active_mask)
    skipped_count = total - active_count
    stats = {
        "total_frames": total,
        "active_frames": active_count,
        "skipped_idle_frames": skipped_count,
        "active_ratio": round(active_count / total, 4) if total else 0.0,
        "window_count": len(windows),
        "windows": [window.to_dict() for window in windows],
    }
    return ActiveWindowResult(
        windows=windows,
        active_mask=active_mask,
        signals=signals,
        stats=stats,
    )


@dataclass
class _BufferedEntry:
    observation: dict[str, Any]
    signal: FrameActivitySignal
    active: bool
    nbytes: int


def _normalize_source_binding(source_binding: Mapping[str, Any]) -> dict[str, Any]:
    source = dict(source_binding)
    pts = source.get("pts")
    num = source.get("time_base_num")
    den = source.get("time_base_den")
    if (
        type(pts) is int
        and type(num) is int and num >= 1
        and type(den) is int and den >= 1
    ):
        source["timestamp_ms"] = round(Fraction(pts * num * 1000, den))
    return source


def build_idle_skipped_sam_records(
    source_binding: Mapping[str, Any],
    player_detections: Sequence[Mapping[str, Any]],
    image_size: tuple[int, int],
    *,
    max_players: int = 2,
    signal: FrameActivitySignal | None = None,
) -> list[dict[str, Any]]:
    """Emit provenance-honest ``sam3d-frame-v1`` records for an idle skipped frame."""
    source = _normalize_source_binding(source_binding)
    width, height = int(image_size[0]), int(image_size[1])
    gate_meta = (
        {
            "active": False,
            "activity_score": signal.activity_score,
            "reasons": list(signal.reasons),
        }
        if signal is not None
        else {"active": False}
    )

    players = sorted(
        player_detections,
        key=lambda p: (
            (p["bbox_px"][2] - p["bbox_px"][0]) * (p["bbox_px"][3] - p["bbox_px"][1])
            if isinstance(p.get("bbox_px"), (list, tuple)) and len(p["bbox_px"]) == 4
            else 0.0
        ),
        reverse=True,
    )[:max_players]

    if not players:
        return [
            {
                **source,
                "schema_version": FRAME_VERSION,
                "status": "skipped",
                "reason": "idle_window_skipped",
                "person_track_id": None,
                "selection_epoch": None,
                "image_size": [width, height],
                "active_window_gate": gate_meta,
            }
        ]

    records: list[dict[str, Any]] = []
    for player in players:
        track_id = player.get("track_id")
        bbox = player.get("bbox_px")
        bbox_list = [float(v) for v in bbox] if isinstance(bbox, (list, tuple)) and len(bbox) == 4 else None
        records.append(
            {
                **source,
                "person_track_id": track_id,
                "selection_epoch": 0 if isinstance(track_id, int) and not isinstance(track_id, bool) else None,
                "schema_version": FRAME_VERSION,
                "image_size": [width, height],
                **({"bbox_px": bbox_list} if bbox_list is not None else {}),
                "status": "skipped",
                "reason": "idle_window_skipped",
                "confidence": None,
                "confidence_semantics": "not_provided_by_sam3d",
                "selection_semantics": "detector_track_epoch_primary_selection_applied_downstream",
                "coordinate_system_2d": COORDINATES_2D,
                "coordinate_system_3d": COORDINATES_3D,
                "units_3d": "model_units",
                "camera_intrinsics": "official_default_fov_uncalibrated",
                "observation_semantics": "monocular_model_estimate",
                "active_window_gate": gate_meta,
            }
        )
    return records


class ActiveWindowGate:
    """Streaming-safe bounded-lookahead active-window pre-filter for SAM 3D."""

    def __init__(self, config: ActiveWindowConfig | None = None) -> None:
        self.config = config or ActiveWindowConfig()
        self._scorer = _ActivityScorer(self.config)
        self._buffer: deque[_BufferedEntry] = deque()
        self._buffer_bytes = 0
        self._streak = 0
        self._active_until_ms: int | None = None
        self._last_active_end_ms: int | None = None
        self._pending_decisions: dict[int, tuple[bool, FrameActivitySignal]] = {}
        self._open_window: dict[str, int] | None = None
        self._windows: list[dict[str, int]] = []
        self.total_frames = 0
        self.active_frames = 0
        self.skipped_idle_frames = 0
        self.buffer_evicted_early_frames = 0
        self.elapsed_seconds = 0.0

    def _emit_entry(self, entry: _BufferedEntry) -> dict[str, Any]:
        self._buffer_bytes -= entry.nbytes
        self.total_frames += 1
        sig = entry.signal
        if entry.active:
            self.active_frames += 1
            if self._open_window is None:
                self._open_window = {
                    "start_ms": sig.timestamp_ms,
                    "end_ms": sig.timestamp_ms,
                    "start_frame_index": sig.frame_index,
                    "end_frame_index": sig.frame_index,
                    "frame_count": 1,
                }
            else:
                self._open_window["end_ms"] = sig.timestamp_ms
                self._open_window["end_frame_index"] = sig.frame_index
                self._open_window["frame_count"] += 1
        else:
            self.skipped_idle_frames += 1
            if self._open_window is not None:
                self._windows.append(self._open_window)
                self._open_window = None
        self._pending_decisions[sig.frame_index] = (entry.active, sig)
        return entry.observation

    def consume_decision(self, frame_index: int) -> tuple[bool, FrameActivitySignal | None]:
        decision = self._pending_decisions.pop(frame_index, None)
        if decision is None:
            return True, None
        return decision

    def stream(
        self,
        observations: Iterable[dict[str, Any]],
        *,
        check_cancelled: Callable[[], None] | None = None,
    ) -> Iterator[dict[str, Any]]:
        cfg = self.config
        lookahead_ms = cfg.lead_ms + cfg.merge_gap_ms
        for observation in observations:
            if check_cancelled is not None:
                check_cancelled()
            started = time.perf_counter()
            frame_index, timestamp_ms, detections, primary_track_id, image_size = _extract_frame_fields(observation)
            sig = self._scorer.score_frame(
                frame_index=frame_index,
                timestamp_ms=timestamp_ms,
                detections=detections,
                primary_track_id=primary_track_id,
                image_size=image_size,
            )
            if sig.raw_active:
                self._streak += 1
            else:
                self._streak = 0
            triggered = (
                self._streak >= cfg.min_trigger_frames
                or sig.activity_score >= cfg.strong_activity_threshold
            )

            active = bool(self._active_until_ms is not None and timestamp_ms <= self._active_until_ms)
            if triggered:
                active = True
                lead_start = timestamp_ms - cfg.lead_ms
                merge_start = lead_start
                if (
                    self._last_active_end_ms is not None
                    and lead_start <= self._last_active_end_ms + cfg.merge_gap_ms
                ):
                    merge_start = min(merge_start, self._last_active_end_ms)
                for buffered in self._buffer:
                    if buffered.signal.timestamp_ms >= merge_start:
                        buffered.active = True
                new_end = timestamp_ms + cfg.trail_ms
                self._active_until_ms = max(self._active_until_ms or new_end, new_end)
                self._last_active_end_ms = self._active_until_ms

            image = observation.get("image")
            nbytes = int(getattr(image, "nbytes", 0)) if image is not None else 0
            self._buffer.append(_BufferedEntry(observation=observation, signal=sig, active=active, nbytes=nbytes))
            self._buffer_bytes += nbytes

            ready: list[dict[str, Any]] = []
            while len(self._buffer) > 1:
                oldest = self._buffer[0]
                horizon_exceeded = timestamp_ms - oldest.signal.timestamp_ms > lookahead_ms
                count_exceeded = len(self._buffer) > cfg.max_buffer_frames
                bytes_exceeded = self._buffer_bytes > cfg.max_buffer_bytes
                if not (horizon_exceeded or count_exceeded or bytes_exceeded):
                    break
                if (count_exceeded or bytes_exceeded) and not horizon_exceeded:
                    self.buffer_evicted_early_frames += 1
                ready.append(self._emit_entry(self._buffer.popleft()))
            self.elapsed_seconds += time.perf_counter() - started
            for item in ready:
                if check_cancelled is not None:
                    check_cancelled()
                yield item

        started = time.perf_counter()
        tail = [self._emit_entry(self._buffer.popleft()) for _ in range(len(self._buffer))]
        if self._open_window is not None:
            self._windows.append(self._open_window)
            self._open_window = None
        self.elapsed_seconds += time.perf_counter() - started
        for item in tail:
            if check_cancelled is not None:
                check_cancelled()
            yield item

    def summary(self) -> dict[str, Any]:
        windows = list(self._windows)
        if self._open_window is not None:
            windows.append(dict(self._open_window))
        active_ratio = round(self.active_frames / self.total_frames, 4) if self.total_frames else 0.0
        return {
            "enabled": True,
            "policy": "active_window_two_stage_prefilter_v1",
            "total_frames": self.total_frames,
            "active_frames": self.active_frames,
            "skipped_idle_frames": self.skipped_idle_frames,
            "active_ratio": active_ratio,
            "window_count": len(windows),
            "windows": windows,
            "buffer_evicted_early_frames": self.buffer_evicted_early_frames,
            "config": self.config.to_dict(),
        }


class ActiveWindowPoseStage:
    """Wrap a SAM pose stage so idle-window frames skip GPU inference cleanly."""

    def __init__(self, pose_stage: Any, gate: ActiveWindowGate) -> None:
        self._pose_stage = pose_stage
        self._gate = gate
        self._last_skipped_records: list[dict[str, Any]] | None = None

    @property
    def last_pose_association_diagnostics(self) -> Any:
        if self._last_skipped_records is not None:
            return None
        return getattr(self._pose_stage, "last_pose_association_diagnostics", None)

    def estimate_poses(
        self,
        frame: Any,
        player_detections: Sequence[Mapping[str, Any]],
        max_players: int,
        *,
        source_binding: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        frame_index = int(source_binding.get("frame_index", 0))
        active, signal = self._gate.consume_decision(frame_index)
        if not active:
            height, width = int(frame.shape[0]), int(frame.shape[1])
            self._last_skipped_records = build_idle_skipped_sam_records(
                source_binding,
                player_detections,
                (width, height),
                max_players=max_players,
                signal=signal,
            )
            return []
        self._last_skipped_records = None
        return self._pose_stage.estimate_poses(
            frame, player_detections, max_players, source_binding=source_binding,
        )

    def pose_frame_records(self) -> list[dict[str, Any]]:
        if self._last_skipped_records is not None:
            return list(self._last_skipped_records)
        return self._pose_stage.pose_frame_records()

    def estimate_pose_batch(self, requests: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        self._last_skipped_records = None
        slots: list[dict[str, Any] | None] = [None] * len(requests)
        active_indices: list[int] = []
        active_requests: list[Mapping[str, Any]] = []

        for idx, req in enumerate(requests):
            source_binding = req["source_binding"]
            frame_index = int(source_binding.get("frame_index", 0))
            active, signal = self._gate.consume_decision(frame_index)
            if active:
                active_indices.append(idx)
                active_requests.append(req)
            else:
                frame = req["frame"]
                height, width = int(frame.shape[0]), int(frame.shape[1])
                records = build_idle_skipped_sam_records(
                    source_binding,
                    req["player_detections"],
                    (width, height),
                    max_players=int(req.get("max_players", 2)),
                    signal=signal,
                )
                slots[idx] = {"poses": [], "records": records}

        if len(active_requests) == 1:
            req = active_requests[0]
            poses = self._pose_stage.estimate_poses(
                req["frame"],
                req["player_detections"],
                req["max_players"],
                source_binding=req["source_binding"],
            )
            slots[active_indices[0]] = {
                "poses": poses,
                "records": self._pose_stage.pose_frame_records(),
            }
        elif len(active_requests) > 1:
            batch_results = self._pose_stage.estimate_pose_batch(active_requests)
            for idx, result in zip(active_indices, batch_results):
                slots[idx] = result

        return [slot for slot in slots if slot is not None]
