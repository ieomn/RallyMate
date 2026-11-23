"""Tests for two-stage active-window SAM 3D pre-filtering and provenance."""
from __future__ import annotations

from contextlib import ExitStack
import copy
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from rallymate_vision import pipeline, sam3d_evaluation, sam3d_runner
from rallymate_vision.contracts import ModelConfig, PipelineRequest, ProcessingConfig
from rallymate_vision.sam3d_active_windows import (
    ACTIVE_WINDOW_GATE_ENV,
    ActiveWindowConfig,
    ActiveWindowGate,
    ActiveWindowPoseStage,
    active_window_gate_enabled,
    build_idle_skipped_sam_records,
    select_active_sam_windows,
)
from rallymate_vision.sam3d_evaluation import evaluate_sam3d_records
from rallymate_vision.sam3d_pipeline_batch import sam_pose_observations


VIDEO_SHA = "a" * 64


def _player(track_id: int, bbox: list[float]) -> dict:
    return {"track_id": track_id, "class_name": "player", "bbox_px": bbox, "confidence": 0.92}


def _ball(bbox: list[float], track_id: int = 101) -> dict:
    return {"track_id": track_id, "class_name": "ball", "bbox_px": bbox, "confidence": 0.85}


def _racket(bbox: list[float], track_id: int = 201) -> dict:
    return {"track_id": track_id, "class_name": "racket", "bbox_px": bbox, "confidence": 0.88}


def _make_observation(index: int, timestamp_ms: int, detections: list[dict], *, primary_track_id: int | None = 1) -> dict:
    players = [d for d in detections if d.get("class_name") == "player"]
    # Exact 30 fps PTS in 1/90000 timebase.
    pts = timestamp_ms * 90
    return {
        "image": np.full((16, 20, 3), index % 255, dtype=np.uint8),
        "detections": detections,
        "players": players,
        "subject_identity": {"source_track_id": primary_track_id if players else None},
        "decoded_binding": {
            "pts": pts,
            "time_base_num": 1,
            "time_base_den": 90000,
            "timestamp_ms": float(timestamp_ms),
        },
        "source_frame_index": index,
        "camera_motion": {"reference_epoch": 1},
        "timestamp_ms": timestamp_ms,
        "timestamp_source": "decoder_pts",
    }


class _FakeSamPoseStage:
    def __init__(self) -> None:
        self.single_calls: list[dict] = []
        self.batch_calls: list[list[dict]] = []
        self._last_records: list[dict] = []
        self.last_pose_association_diagnostics = {"fake": True}

    @staticmethod
    def _make_ok(req: dict) -> dict:
        binding = copy.deepcopy(req["source_binding"])
        frame_index = binding["frame_index"]
        records = [
            {
                **binding,
                "schema_version": "sam3d-frame-v1",
                "status": "ok",
                "person_track_id": p["track_id"],
                "selection_epoch": 0,
                "image_size": [req["frame"].shape[1], req["frame"].shape[0]],
            }
            for p in req["player_detections"]
        ]
        poses = [{"frame_index": frame_index, "person_track_id": p["track_id"]} for p in req["player_detections"]]
        return {"poses": poses, "records": records}

    def estimate_poses(self, frame, player_detections, max_players, *, source_binding):
        req = {
            "frame": frame,
            "player_detections": player_detections,
            "max_players": max_players,
            "source_binding": source_binding,
        }
        self.single_calls.append(req)
        result = self._make_ok(req)
        self._last_records = result["records"]
        return result["poses"]

    def pose_frame_records(self):
        return list(self._last_records)

    def estimate_pose_batch(self, requests):
        self.batch_calls.append(list(requests))
        return [self._make_ok(req) for req in requests]


class ActiveWindowConfigTests(unittest.TestCase):
    def test_opt_in_default_is_disabled_and_invalid_env_raises(self):
        self.assertFalse(active_window_gate_enabled({}))
        self.assertFalse(active_window_gate_enabled({ACTIVE_WINDOW_GATE_ENV: "0"}))
        self.assertTrue(active_window_gate_enabled({ACTIVE_WINDOW_GATE_ENV: "1"}))
        for bad in ("true", "yes", "2", ""):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                active_window_gate_enabled({ACTIVE_WINDOW_GATE_ENV: bad})

    def test_from_env_parses_and_validates_bounds(self):
        cfg = ActiveWindowConfig.from_env(
            {
                "RALLYMATE_SAM3D_ACTIVE_LEAD_MS": "1200",
                "RALLYMATE_SAM3D_ACTIVE_TRAIL_MS": "1800",
                "RALLYMATE_SAM3D_ACTIVE_MERGE_GAP_MS": "800",
                "RALLYMATE_SAM3D_ACTIVE_MAX_BUFFER_MB": "128",
            }
        )
        self.assertEqual(cfg.lead_ms, 1200)
        self.assertEqual(cfg.trail_ms, 1800)
        self.assertEqual(cfg.merge_gap_ms, 800)
        self.assertEqual(cfg.max_buffer_bytes, 128 * 1024 * 1024)
        with self.assertRaises(ValueError):
            ActiveWindowConfig.from_env({"RALLYMATE_SAM3D_ACTIVE_LEAD_MS": "-1"})
        with self.assertRaises(ValueError):
            ActiveWindowConfig.from_env({"RALLYMATE_SAM3D_ACTIVE_MAX_BUFFER_MB": "0"})


class ActiveWindowSelectionTests(unittest.TestCase):
    def test_empty_court_and_stationary_jitter_remain_idle(self):
        frames = []
        # 30 frames empty court (0..1000ms), then 60 frames standing still with +-1.5px jitter.
        for i in range(90):
            ts = i * 33
            if i < 30:
                dets = []
            else:
                jitter = 1.5 if i % 2 == 0 else -1.5
                dets = [_player(1, [200.0 + jitter, 100.0, 280.0 + jitter, 300.0])]
            frames.append(_make_observation(i, ts, dets))

        result = select_active_sam_windows(frames)
        self.assertEqual(result.stats["total_frames"], 90)
        self.assertEqual(result.stats["active_frames"], 0)
        self.assertEqual(result.stats["skipped_idle_frames"], 90)
        self.assertEqual(result.stats["active_ratio"], 0.0)
        self.assertEqual(result.windows, [])

    def test_swing_burst_applies_lead_and_trail_padding_and_skips_dead_time(self):
        # 20 seconds at 10 fps (200 frames, 100ms step).
        # Dead time: 0..6000ms, active swing/run: 6000..8000ms, dead time: 8000..20000ms.
        frames = []
        for i in range(200):
            ts = i * 100
            if 60 <= i <= 80:
                # Moving 20px per 100ms = 200px/s on 200px player = 1.0 heights/s + width expansion.
                dx = (i - 60) * 20.0
                arm_ext = 25.0 if 68 <= i <= 74 else 0.0
                bbox = [200.0 + dx, 100.0, 280.0 + dx + arm_ext, 300.0]
            elif i < 60:
                bbox = [200.0, 100.0, 280.0, 300.0]
            else:
                bbox = [600.0, 100.0, 680.0, 300.0]
            frames.append(_make_observation(i, ts, [_player(1, bbox)]))

        cfg = ActiveWindowConfig(lead_ms=1500, trail_ms=1500, merge_gap_ms=500)
        result = select_active_sam_windows(frames, cfg)
        self.assertEqual(result.stats["window_count"], 1)
        win = result.windows[0]
        # Lead padding covers preparation before t=6000ms (~4600..4700ms).
        self.assertLessEqual(win.start_ms, 5000)
        # Trail padding covers follow-through/recovery after t=8000ms (~9500ms).
        self.assertGreaterEqual(win.end_ms, 9500)
        # Early and late dead time stays skipped.
        self.assertFalse(result.active_mask[0])
        self.assertFalse(result.active_mask[30])
        self.assertFalse(result.active_mask[150])
        self.assertFalse(result.active_mask[199])
        self.assertLess(result.stats["active_ratio"], 0.4)

    def test_ball_and_racket_motion_trigger_active_window_for_stationary_ready_player(self):
        # Player stays mostly planted while ball flies rapidly across court & racket swings.
        frames = []
        for i in range(60):
            ts = i * 100
            dets = [_player(1, [300.0, 200.0, 380.0, 400.0])]
            if 25 <= i <= 30:
                bx = 100.0 + (i - 25) * 40.0
                dets.append(_ball([bx, 220.0, bx + 12.0, 232.0]))
                rx = 340.0 + (i - 25) * 18.0
                dets.append(_racket([rx, 240.0, rx + 35.0, 290.0]))
            frames.append(_make_observation(i, ts, dets))

        result = select_active_sam_windows(frames, ActiveWindowConfig(lead_ms=500, trail_ms=500))
        self.assertEqual(result.stats["window_count"], 1)
        self.assertTrue(result.active_mask[27])
        self.assertTrue(result.active_mask[22])  # lead padding
        self.assertTrue(result.active_mask[33])  # trail padding
        self.assertFalse(result.active_mask[0])
        self.assertFalse(result.active_mask[59])

    def test_close_active_bursts_merge_while_far_bursts_remain_separate(self):
        # Burst 1: 3000..3500ms; Burst 2: 5500..6000ms (merged with lead=1000, trail=1000, merge=500);
        # Burst 3: 14000..14500ms (separate after long break).
        frames = []
        x = 200.0
        for i in range(180):
            ts = i * 100
            if 30 <= i <= 35 or 55 <= i <= 60 or 140 <= i <= 145:
                x += 22.0
            frames.append(_make_observation(i, ts, [_player(1, [x, 100.0, x + 80.0, 300.0])]))

        cfg = ActiveWindowConfig(lead_ms=1000, trail_ms=1000, merge_gap_ms=500)
        result = select_active_sam_windows(frames, cfg)
        self.assertEqual(result.stats["window_count"], 2)
        self.assertTrue(result.active_mask[45])  # gap between burst 1 and 2 merged
        self.assertFalse(result.active_mask[100])  # long dead time stays idle

    def test_streaming_gate_matches_offline_selection_exactly(self):
        frames = []
        x = 150.0
        for i in range(120):
            ts = i * 100
            if 20 <= i <= 26 or 80 <= i <= 88:
                x += 25.0
            frames.append(_make_observation(i, ts, [_player(1, [x, 100.0, x + 80.0, 300.0])]))

        cfg = ActiveWindowConfig(lead_ms=800, trail_ms=800, merge_gap_ms=400, max_buffer_frames=60)
        offline = select_active_sam_windows(frames, cfg)

        gate = ActiveWindowGate(cfg)
        emitted = list(gate.stream(frames))
        self.assertEqual([obs["source_frame_index"] for obs in emitted], [f["source_frame_index"] for f in frames])
        streaming_mask = [gate.consume_decision(obs["source_frame_index"])[0] for obs in emitted]
        self.assertEqual(streaming_mask, offline.active_mask)
        summary = gate.summary()
        self.assertEqual(summary["active_frames"], offline.stats["active_frames"])
        self.assertEqual(summary["skipped_idle_frames"], offline.stats["skipped_idle_frames"])
        self.assertEqual(summary["active_ratio"], offline.stats["active_ratio"])
        self.assertEqual(summary["window_count"], offline.stats["window_count"])


class ActiveWindowPipelineIntegrationTests(unittest.TestCase):
    def _build_mixed_sequence(self):
        # 50 frames at 100ms (5s): idle 0..19, active 20..26, idle 27..49 (with small lead/trail=300ms).
        frames = []
        x = 200.0
        for i in range(50):
            ts = i * 100
            if i < 5:
                dets = []  # empty court
            else:
                if 20 <= i <= 26:
                    x += 25.0
                dets = [_player(1, [x, 100.0, x + 80.0, 300.0])]
            frames.append(_make_observation(i, ts, dets))
        return frames

    def test_pose_stage_skips_gpu_on_idle_frames_and_emits_provenance_honest_records(self):
        for batch_frames in (1, 4):
            with self.subTest(batch_frames=batch_frames):
                frames = self._build_mixed_sequence()
                cfg = ActiveWindowConfig(lead_ms=300, trail_ms=300, merge_gap_ms=200)
                gate = ActiveWindowGate(cfg)
                fake_stage = _FakeSamPoseStage()
                wrapped_stage = ActiveWindowPoseStage(fake_stage, gate)
                timings, stats = {}, {}

                stream = sam_pose_observations(
                    gate.stream(frames),
                    wrapped_stage,
                    max_players=2,
                    video_sha256=VIDEO_SHA,
                    batch_frames=batch_frames,
                    timings=timings,
                    statistics=stats,
                    check_cancelled=lambda: None,
                )
                results = list(stream)
                self.assertEqual(len(results), len(frames))

                gate_summary = gate.summary()
                self.assertGreater(gate_summary["active_frames"], 0)
                self.assertGreater(gate_summary["skipped_idle_frames"], 0)
                self.assertEqual(
                    gate_summary["active_frames"] + gate_summary["skipped_idle_frames"],
                    len(frames),
                )

                # Underlying SAM stage only ran on active frames, NEVER on idle frames.
                executed_frames = len(fake_stage.single_calls) + sum(len(b) for b in fake_stage.batch_calls)
                self.assertEqual(executed_frames, gate_summary["active_frames"])

                # Inspect every emitted SAM record for provenance & exact PTS preservation.
                all_records = []
                skipped_with_player = []
                for idx, row in enumerate(results):
                    recs = row["sam_records"]
                    self.assertEqual(len(recs), 1)
                    rec = recs[0]
                    all_records.append(rec)
                    self.assertEqual(rec["video_sha256"], VIDEO_SHA)
                    self.assertEqual(rec["frame_index"], idx)
                    self.assertEqual(rec["processed_index"], idx)
                    self.assertEqual(rec["pts"], idx * 100 * 90)
                    self.assertEqual(rec["time_base_num"], 1)
                    self.assertEqual(rec["time_base_den"], 90000)
                    self.assertEqual(rec["timestamp_ms"], idx * 100)
                    if rec["status"] == "skipped":
                        self.assertEqual(rec["reason"], "idle_window_skipped")
                        self.assertEqual(row["sam_poses"], [])
                        if rec["person_track_id"] is not None:
                            skipped_with_player.append(rec)
                    else:
                        self.assertEqual(rec["status"], "ok")
                        self.assertEqual(len(row["sam_poses"]), 1)

                # Skipped records with a tracked player pass sam3d_evaluation with 0 invalid bindings.
                self.assertTrue(skipped_with_player)
                evaluation = evaluate_sam3d_records(skipped_with_player)
                self.assertEqual(evaluation["counts"]["invalid_binding"], 0)
                self.assertEqual(evaluation["counts"]["valid_binding"], len(skipped_with_player))
                self.assertEqual(evaluation["counts"]["status_skipped"], len(skipped_with_player))

    def test_buffer_byte_limit_evicts_early_and_cancellation_propagates(self):
        frames = [_make_observation(i, i * 100, [_player(1, [100.0, 100.0, 180.0, 300.0])]) for i in range(10)]
        # Each 16x20x3 frame is 960 bytes; set max_buffer_bytes=2000 so max ~2 frames buffered.
        gate = ActiveWindowGate(ActiveWindowConfig(lead_ms=1500, trail_ms=1500, max_buffer_bytes=2000))
        emitted = list(gate.stream(frames))
        self.assertEqual(len(emitted), 10)
        self.assertGreater(gate.buffer_evicted_early_frames, 0)

        class Cancelled(RuntimeError):
            pass

        calls = 0

        def cancel():
            nonlocal calls
            calls += 1
            if calls >= 3:
                raise Cancelled("stopped")

        gate2 = ActiveWindowGate()
        with self.assertRaises(Cancelled):
            list(gate2.stream(frames, check_cancelled=cancel))

    def test_idle_skipped_record_normalizes_float_timestamp_from_exact_pts(self):
        recs = build_idle_skipped_sam_records(
            {
                "video_sha256": VIDEO_SHA,
                "frame_index": 7,
                "processed_index": 7,
                "pts": 100,
                "time_base_num": 1,
                "time_base_den": 3000,
                "timestamp_ms": 33.333333,
                "camera_reference_epoch": 1,
            },
            [_player(3, [10.0, 20.0, 50.0, 100.0])],
            (640, 360),
        )
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["status"], "skipped")
        self.assertEqual(recs[0]["reason"], "idle_window_skipped")
        self.assertEqual(recs[0]["timestamp_ms"], 33)
        self.assertEqual(recs[0]["person_track_id"], 3)

    def test_run_pipeline_wires_active_window_gate_when_opted_in(self):
        class ControlledDecoder:
            def __init__(self, count: int):
                self.count = count
                self.index = -1
                self.binding = {}

            def read(self):
                self.index += 1
                if self.index >= self.count:
                    return False, None
                ts = self.index * 100
                self.binding.update(pts=ts, time_base_num=1, time_base_den=1000, timestamp_ms=ts)
                return True, np.full((64, 64, 3), self.index % 255, dtype=np.uint8)

            def get(self, _prop):
                return self.binding["timestamp_ms"]

            def release(self):
                pass

        class ReachedScoring(Exception):
            pass

        def run_with_gate(enabled: str):
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                source = root / "video.mp4"
                source.write_bytes(b"synthetic video for active-window pipeline wiring")
                request = PipelineRequest(
                    "active-window-wiring",
                    source,
                    root / "out",
                    ModelConfig(
                        root / "detect",
                        root / "pose",
                        device="cpu",
                        pose_backend="sam3d",
                        pose_native_keypoint_format="mhr70",
                    ),
                    processing=ProcessingConfig(write_annotated_video=False),
                )
                capture = ControlledDecoder(12)
                perception = Mock()
                perception.detect_model_path = request.models.detect
                perception.pose_model_path = request.models.pose
                perception.pose_metadata.return_value = {
                    "backend": "sam3d",
                    "runtime": "pytorch",
                    "profile": "realtime",
                    "native_keypoint_format": "mhr70",
                }
                perception.last_pose_association_diagnostics = None
                perception.last_detection_timing = {}
                perception.last_detection_timings = []
                perception.pose_frame_records.return_value = []
                perception.detect.side_effect = lambda _img: []
                pose_calls = []

                def fake_pose(_img, _players, _max_players, *, source_binding):
                    pose_calls.append(source_binding["frame_index"])
                    perception.pose_frame_records.return_value = [
                        {
                            **source_binding,
                            "schema_version": "sam3d-frame-v1",
                            "status": "skipped",
                            "reason": "no_detected_subject",
                            "person_track_id": None,
                            "selection_epoch": None,
                            "image_size": [64, 64],
                        }
                    ]
                    return []

                perception.estimate_poses.side_effect = fake_pose
                tracker = Mock()
                tracker.update.side_effect = lambda dets, _w, _h, *, timestamp_ms: dets
                metadata = SimpleNamespace(fps=10.0, frame_count=12, width=64, height=64, duration_ms=1200)

                with ExitStack() as stack:
                    stack.enter_context(
                        patch.dict(
                            os.environ,
                            {
                                ACTIVE_WINDOW_GATE_ENV: enabled,
                                "RALLYMATE_DETECT_BATCH_SIZE": "1",
                                "RALLYMATE_SAM3D_BATCH_FRAMES": "1",
                                "RALLYMATE_SAM3D_PREFETCH_FRAMES": "0",
                                "RALLYMATE_SAM3D_ASYNC_MESH_ARCHIVE": "0",
                                "RALLYMATE_SAM3D_RUNTIME": str(root / "runtime"),
                            },
                        )
                    )
                    stack.enter_context(patch.object(pipeline, "probe_video", return_value=metadata))
                    stack.enter_context(patch.object(pipeline, "SimpleMultiClassTracker", return_value=tracker))
                    stack.enter_context(patch.object(sam3d_runner, "SourcePTSDecoder", return_value=capture))
                    stack.enter_context(
                        patch.object(
                            sam3d_evaluation,
                            "evaluate_sam3d_jsonl",
                            return_value={"runtime": {}, "status": "fixture", "counts": {}},
                        )
                    )
                    stack.enter_context(
                        patch.object(pipeline, "build_primary_player_artifacts", side_effect=ReachedScoring())
                    )
                    with self.assertRaises(ReachedScoring):
                        pipeline.run_pipeline(request, perception=perception)

                sam_rows = [
                    json.loads(line)
                    for line in (root / "out" / "sam3d_frames.jsonl").read_text(encoding="utf-8").splitlines()
                ]
                return pose_calls, sam_rows

        calls_off, rows_off = run_with_gate("0")
        self.assertEqual(len(calls_off), 12)
        self.assertTrue(all(row["reason"] == "no_detected_subject" for row in rows_off))

        calls_on, rows_on = run_with_gate("1")
        self.assertEqual(calls_on, [])
        self.assertEqual(len(rows_on), 12)
        self.assertTrue(all(row["status"] == "skipped" and row["reason"] == "idle_window_skipped" for row in rows_on))


if __name__ == "__main__":
    unittest.main()
