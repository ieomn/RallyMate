from __future__ import annotations

import contextlib
import csv
import io
import json
import tempfile
import unittest
from pathlib import Path

from rallymate_training.cli import build_parser, main as cli_main
from rallymate_training.tennis_ball_racket import (
    BOOTSTRAP_LABEL_ORIGIN,
    TENNIS_DETECTION_CLASSES,
    TennisBallRacketError,
    bootstrap_pseudo_labels_from_frames,
    load_tennis_ball_racket_config,
    validate_tennis_ball_racket_manifest,
)
from rallymate_vision.ball_racket_tracker import (
    CONTACT_CANDIDATE_SEMANTICS,
    KalmanBallTracker,
    detect_contact_candidates,
)


def _ball_det(cx: float, cy: float, *, conf: float = 0.85, radius: float = 8.0) -> dict:
    return {
        "class_name": "ball",
        "confidence": conf,
        "bbox_px": [cx - radius, cy - radius, cx + radius, cy + radius],
    }


def _racket_det(cx: float, cy: float, *, cls_name: str = "racket_head", conf: float = 0.9, half: float = 28.0) -> dict:
    return {
        "class_name": cls_name,
        "confidence": conf,
        "bbox_px": [cx - half, cy - half, cx + half, cy + half],
    }


class KalmanAndContactTests(unittest.TestCase):
    def test_kalman_tracker_coasts_across_3_frame_occlusion_gap_with_stable_track_id(self) -> None:
        tracker = KalmanBallTracker(max_coast_frames=6, default_fps=30.0)
        width, height = 1280, 720
        dt_ms = 33

        # Constant-acceleration parabolic flight; frames 3, 4, 5 are dropped out (motion blur occlusion).
        all_points: list[dict] = []
        observed_x = lambda k: 180.0 + 38.0 * k
        observed_y = lambda k: 420.0 - 22.0 * k + 1.8 * (k ** 2)

        for frame_idx in range(10):
            ts = frame_idx * dt_ms
            if frame_idx in {3, 4, 5}:
                dets = []
            else:
                dets = [_ball_det(observed_x(frame_idx), observed_y(frame_idx))]
            step_out = tracker.update(
                dets,
                width,
                height,
                frame_index=frame_idx,
                timestamp_ms=ts,
            )
            self.assertEqual(len(step_out), 1)
            all_points.extend(step_out)

        self.assertEqual({pt["track_id"] for pt in all_points}, {1})
        self.assertEqual(
            [pt["point_source"] for pt in all_points],
            [
                "observed",
                "observed",
                "observed",
                "kalman_predicted",
                "kalman_predicted",
                "kalman_predicted",
                "observed",
                "observed",
                "observed",
                "observed",
            ],
        )
        for idx in (3, 4, 5):
            self.assertFalse(all_points[idx]["observed"])
            self.assertIsNone(all_points[idx]["confidence"])
            self.assertEqual(all_points[idx]["coast_age"], idx - 2)
            self.assertLess(abs(all_points[idx]["x_px"] - observed_x(idx)), 30.0)

    def test_kalman_tracker_keeps_track_id_across_fast_forehand_reversal_and_retires_long_drop(self) -> None:
        tracker = KalmanBallTracker(max_coast_frames=2, default_fps=30.0)
        width, height = 1280, 720
        # Incoming rightwards, sharp hit at frame 4, outgoing leftwards.
        xs = [400, 425, 450, 475, 500, 465, 430, 395, 360]
        ys = [360, 362, 364, 366, 368, 355, 342, 330, 318]
        ids = []
        for idx, (x, y) in enumerate(zip(xs, ys)):
            out = tracker.update([_ball_det(x, y)], width, height, frame_index=idx, timestamp_ms=idx * 33)
            ids.append(out[0]["track_id"])
        self.assertEqual(set(ids), {1})

        # Exceeding max_coast_frames retires track 1 and allocates track 2 on distant return.
        for gap_idx in range(9, 12):
            tracker.update([], width, height, frame_index=gap_idx, timestamp_ms=gap_idx * 33)
        reappear = tracker.update([_ball_det(900, 200)], width, height, frame_index=12, timestamp_ms=12 * 33)
        self.assertEqual(reappear[0]["track_id"], 2)

    def test_contact_detection_on_synthetic_forehand_reversal_and_zero_false_positives_on_steady_flight(self) -> None:
        width, height = 1280, 720
        tracker = KalmanBallTracker(max_coast_frames=6, default_fps=30.0)

        # Case 1: Straight steady flight across 12 frames -> ZERO contact candidates (even with nearby static racket).
        steady_ball = []
        steady_rackets = []
        steady_wrists = []
        for idx in range(12):
            ts = idx * 33
            bx = 160.0 + 42.0 * idx
            by = 300.0 + 3.0 * idx
            steady_ball.extend(tracker.update([_ball_det(bx, by)], width, height, frame_index=idx, timestamp_ms=ts))
            steady_rackets.append({"frame_index": idx, "timestamp_ms": ts, **_racket_det(410.0, 318.0)})
            steady_wrists.append({"frame_index": idx, "timestamp_ms": ts, "side": "right", "x_px": 400.0, "y_px": 360.0})

        self.assertEqual(
            detect_contact_candidates(
                steady_ball,
                steady_rackets,
                steady_wrists,
                frame_width=width,
                frame_height=height,
            ),
            [],
        )

        # Case 2: Synthetic forehand velocity reversal + racket proximity + local wrist swing speed.
        tracker.reset()
        forehand_ball = []
        forehand_rackets = []
        forehand_wrists = []
        contact_frame = 5
        for idx in range(11):
            ts = idx * 33
            if idx <= contact_frame:
                bx = 350.0 + 35.0 * idx
                by = 380.0 + 4.0 * idx
            else:
                k = idx - contact_frame
                bx = 525.0 - 46.0 * k
                by = 400.0 - 14.0 * k
            forehand_ball.extend(tracker.update([_ball_det(bx, by)], width, height, frame_index=idx, timestamp_ms=ts))
            # Moving racket head passes through (525, 400) right at frame 5.
            rx = 460.0 + 13.0 * idx
            ry = 435.0 - 7.0 * idx
            forehand_rackets.append({"frame_index": idx, "timestamp_ms": ts, **_racket_det(rx, ry, cls_name="racket_head")})
            forehand_wrists.append(
                {
                    "frame_index": idx,
                    "timestamp_ms": ts,
                    "person_track_id": 1,
                    "side": "right",
                    "x_px": 430.0 + 18.0 * idx,
                    "y_px": 465.0 - 10.0 * idx,
                }
            )

        candidates = detect_contact_candidates(
            forehand_ball,
            forehand_rackets,
            forehand_wrists,
            frame_width=width,
            frame_height=height,
        )
        self.assertEqual(len(candidates), 1)
        cand = candidates[0]
        self.assertFalse(cand["confirmed_contact"])
        self.assertEqual(cand["semantics"], CONTACT_CANDIDATE_SEMANTICS)
        self.assertEqual(cand["frame_index"], contact_frame)
        self.assertEqual(cand["timestamp_ms"], contact_frame * 33)
        self.assertGreater(cand["confidence"], 0.6)
        self.assertGreater(cand["ball_turn_angle_deg"], 120.0)
        self.assertGreater(cand["impulse_norm_s2"], 5.0)
        self.assertLessEqual(cand["racket_distance_norm"], 0.05)
        self.assertEqual(cand["racket_evidence_source"], "racket_head")
        self.assertIsNotNone(cand["local_wrist_speed_norm_s"])

        # Case 3: Same ball reversal WITHOUT racket/wrist nearby (open-court bounce) -> rejected!
        far_rackets = [{"frame_index": idx, "timestamp_ms": idx * 33, **_racket_det(100.0, 100.0)} for idx in range(11)]
        self.assertEqual(
            detect_contact_candidates(forehand_ball, far_rackets, frame_width=width, frame_height=height),
            [],
        )

        # Case 4: Wrist fallback when racket bbox is absent/occluded, and distant background racket_head does not shadow near tennis_racket.
        wrist_only_candidates = detect_contact_candidates(
            forehand_ball,
            None,
            forehand_wrists,
            frame_width=width,
            frame_height=height,
        )
        self.assertEqual(len(wrist_only_candidates), 1)
        self.assertEqual(wrist_only_candidates[0]["racket_evidence_source"], "wrist_fallback")

        mixed_rackets = [
            {"frame_index": idx, "timestamp_ms": idx * 33, **_racket_det(100.0, 100.0, cls_name="racket_head")}
            for idx in range(11)
        ] + [
            {"frame_index": idx, "timestamp_ms": idx * 33, **_racket_det(460.0 + 13.0 * idx, 435.0 - 7.0 * idx, cls_name="tennis_racket")}
            for idx in range(11)
        ]
        mixed_candidates = detect_contact_candidates(
            forehand_ball,
            mixed_rackets,
            forehand_wrists,
            frame_width=width,
            frame_height=height,
        )
        self.assertEqual(len(mixed_candidates), 1)
        self.assertEqual(mixed_candidates[0]["racket_evidence_source"], "tennis_racket")

    def test_tennis_ball_racket_config_manifest_validation_and_bootstrapper(self) -> None:
        cfg = load_tennis_ball_racket_config("training/configs/tennis_ball_racket.yaml")
        self.assertEqual(cfg.classes, TENNIS_DETECTION_CLASSES)
        self.assertEqual(cfg.image_size, 1280)
        self.assertGreaterEqual(cfg.augmentations.mosaic, 0.5)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frames_path = root / "frames.jsonl"
            records = []
            for idx in range(6):
                dets = [
                    {
                        "class_name": "ball",
                        "confidence": 0.72,
                        "bbox_px": [200.0 + idx * 24.0, 300.0, 214.0 + idx * 24.0, 314.0],
                        "track_id": 10,
                    },
                    {
                        "class_name": "racket",
                        "confidence": 0.85,
                        "bbox_px": [180.0 + idx * 10.0, 320.0, 250.0 + idx * 10.0, 410.0],
                        "track_id": 11,
                    },
                ]
                # Inject an isolated 1-frame noise ball at frame 2 that must be filtered out.
                if idx == 2:
                    dets.append(
                        {
                            "class_name": "ball",
                            "confidence": 0.65,
                            "bbox_px": [1050.0, 600.0, 1064.0, 614.0],
                            "track_id": 99,
                        }
                    )
                records.append(
                    {
                        "schema_version": "1.1.0",
                        "frame": {"index": idx, "processed_index": idx, "timestamp_ms": idx * 33, "width": 1280, "height": 720},
                        "detections": dets,
                    }
                )
            frames_path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")

            out_dir = root / "bootstrap"
            summary = bootstrap_pseudo_labels_from_frames(
                frames_path,
                out_dir,
                min_ball_confidence=0.35,
                min_racket_confidence=0.35,
                min_track_length=3,
            )
            self.assertEqual(summary["label_origin"], BOOTSTRAP_LABEL_ORIGIN)
            self.assertFalse(summary["verified_ground_truth"])
            self.assertEqual(summary["labeled_frames"], 6)
            self.assertEqual(summary["label_counts"]["tennis_ball"], 6)
            self.assertEqual(summary["label_counts"]["tennis_racket"], 6)

            # Frame 2 must only have 2 boxes (noise ball dropped!).
            frame2_lines = (out_dir / "labels" / "frame_00000002.txt").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(frame2_lines), 2)

            # Unverified bootstrap dataset.yaml must be rejected unless allow_unverified_bootstrap=True!
            with self.assertRaises(TennisBallRacketError):
                validate_tennis_ball_racket_manifest(out_dir / "dataset.yaml", require_files=False)
            allowed = validate_tennis_ball_racket_manifest(
                out_dir / "dataset.yaml",
                require_files=False,
                allow_unverified_bootstrap=True,
            )
            self.assertEqual(allowed["label_origin"], BOOTSTRAP_LABEL_ORIGIN)

            # Valid governed CSV manifest for 3-class training passes cleanly.
            img = root / "dataset" / "images" / "s1" / "f0.jpg"
            lbl = root / "dataset" / "labels" / "s1" / "f0.txt"
            img.parent.mkdir(parents=True, exist_ok=True)
            lbl.parent.mkdir(parents=True, exist_ok=True)
            img.write_bytes(b"img")
            lbl.write_text("0 0.5 0.5 0.02 0.02\n1 0.4 0.6 0.1 0.15\n2 0.42 0.55 0.05 0.06\n", encoding="utf-8")
            csv_path = root / "manifest.csv"
            with csv_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=["image_path", "label_path", "session_id", "subject_id", "camera_id", "consent", "license"],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "image_path": str(img),
                        "label_path": str(lbl),
                        "session_id": "s1",
                        "subject_id": "p1",
                        "camera_id": "cam1",
                        "consent": "yes",
                        "license": "owned",
                    }
                )
            csv_audit = validate_tennis_ball_racket_manifest(csv_path)
            self.assertEqual(csv_audit["audit"]["labels"], 3)

    def test_cli_parser_and_dry_run_for_tennis_ball_racket(self) -> None:
        parser = build_parser()
        parsed = parser.parse_args(
            [
                "tennis-ball-racket",
                "--config",
                "training/configs/tennis_ball_racket.yaml",
                "--dry-run",
            ]
        )
        self.assertEqual(parsed.command, "tennis-ball-racket")
        self.assertTrue(parsed.dry_run)

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            cli_main(["tennis-ball-racket", "--config", "training/configs/tennis_ball_racket.yaml", "--dry-run"])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["result"]["mode"], "dry_run")
        self.assertEqual(payload["result"]["classes"], list(TENNIS_DETECTION_CLASSES))


if __name__ == "__main__":
    unittest.main()
