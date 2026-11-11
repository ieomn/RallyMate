"""Ten source-localized motion phases from native kinematic landmarks.

These are overlapping candidate windows around observed landmarks, not ten
equally spaced slices. The pose-only release anchor is never called a measured
ball contact, toss release, racket-face orientation or ground contact.
"""
from __future__ import annotations

import numpy as np

VERSION = "kinematic-ten-phase-segmentation-v1"
_GROUND = ("准备观察姿态", "启动与转体", "转肩引拍", "下肢伸展", "肩髋分离", "躯干释放", "挥拍加速", "主挥拍窗口", "随挥", "恢复准备")
_SLICE = ("准备观察姿态", "启动与转体", "转肩引拍", "重心支撑姿态", "高位引拍", "挥出准备", "前下挥拍", "主挥拍窗口", "低位随挥", "恢复准备")
_SERVE = ("站位", "双臂准备", "非持拍臂上举", "抛球观察窗口", "持拍臂引拍", "蓄力姿态", "下肢上伸", "上方挥拍窗口", "随挥", "恢复准备")


def segment_motion_phases(rows, *, wrist, prep_end, peak, family="baseline", technique=None, active_side="right"):
    if len(rows) < 5 or not 0 < prep_end <= peak < len(rows) - 1:
        return []
    t = np.asarray([r["time"] for r in rows], dtype=float)
    wrist = np.asarray(wrist, dtype=float)
    if wrist.shape != (len(rows), 3) or not np.isfinite(wrist).all() or not np.isfinite(t).all() or np.any(np.diff(t) <= 0):
        return []
    speed = np.r_[0., np.linalg.norm(np.diff(wrist, axis=0), axis=1) / (np.diff(t) / 1000)]
    separation = np.asarray([r["separation"] for r in rows])
    coil = int(np.argmax(separation[:peak + 1]))
    knee = np.asarray([r["knee"] if r.get("knee") is not None else np.nan for r in rows])
    loaded = int(np.nanargmax(knee[:peak + 1])) if np.isfinite(knee[:peak + 1]).any() else prep_end - 1
    release = int(np.argmin(np.diff(separation[:peak + 1]) / np.diff(t[:peak + 1]))) + 1
    travel = np.r_[0., np.cumsum(np.linalg.norm(np.diff(wrist[:prep_end + 1], axis=0), axis=1))]
    onset = int(np.searchsorted(travel, travel[-1] * .15))
    recovery_start = min(len(rows) - 1, peak + 2)
    deceleration = recovery_start + int(np.argmin(speed[recovery_start:]))
    anchors = [0, onset, prep_end - 1, loaded, coil, release, max(prep_end, peak - 1), peak, min(len(rows) - 1, peak + 1), deceleration]
    names = _SERVE if family == "serve" else _SLICE if technique and "slice" in technique else _GROUND
    methods = ["source_window_start", "wrist_path_onset", "source_preparation_end", "maximum_knee_flexion",
               "maximum_shoulder_hip_separation", "maximum_separation_release_rate", "forward_acceleration_window",
               "source_bound_primary_motion_peak", "post_peak_path", "post_peak_speed_minimum"]
    required = {}
    if family == "serve":
        from rallymate_features.gs_features import joint_angle
        other = "left" if active_side == "right" else "right"
        heights = np.array([r["local"].get(f"{other}_wrist")[1] if r["local"].get(f"{other}_wrist") is not None else np.nan
                            for r in rows[:peak + 1]])
        toss_arm = int(np.nanargmax(heights)) if np.isfinite(heights).any() else None
        trophies = []
        for row in rows[:peak]:
            p = row["local"]
            elbow = joint_angle(*(p.get(f"{active_side}_{joint}") for joint in ("shoulder", "elbow", "wrist")))
            upper = joint_angle(*(p.get(f"{active_side}_{joint}") for joint in ("hip", "shoulder", "elbow")))
            trophies.append(min(180 - elbow, upper) if elbow is not None and upper is not None else -np.inf)
        trophy = int(np.argmax(trophies)) if np.isfinite(trophies).any() else prep_end - 1
        extension = np.diff(knee[:peak + 1]) / np.diff(t[:peak + 1])
        extend = int(np.nanargmin(extension)) + 1 if np.isfinite(extension).any() else loaded
        anchors[2:7] = [toss_arm if toss_arm is not None else prep_end - 1,
                        toss_arm if toss_arm is not None else prep_end - 1,
                        int(np.argmin(wrist[:peak, 1])), trophy, extend]
        methods[2:7] = ["non_active_wrist_height_maximum" if toss_arm is not None else "source_preparation_end",
                        "non_active_wrist_observation_not_ball_release", "active_wrist_vertical_preparation_minimum",
                        "bent_elbow_and_upper_arm_elevation", "maximum_knee_extension_rate"]
        required[3] = ["ball_hand_separation_for_toss_release"]
    elif technique and "slice" in technique:
        anchors[4], anchors[5] = int(np.argmax(wrist[:peak + 1, 1])), prep_end - 1
        methods[4], methods[5] = "active_wrist_preparation_height_maximum", "source_preparation_end_not_racket_face"
        required[5] = ["racket_face_keypoints"]
    required[7] = ["ball_racket_contact_for_physical_impact"]
    output = []
    for index, (anchor, label, method) in enumerate(zip(anchors, names, methods)):
        if index <= 1:
            a, b = max(0, anchor - 2), min(prep_end, anchor + 2)
        elif index <= 5:
            a, b = max(0, anchor - 2), min(peak, anchor + 2)
        elif index == 6:
            a, b = max(0, prep_end - 1), peak
        elif index == 7:
            a, b = max(0, peak - 1), min(len(rows) - 1, peak + 1)
        elif index == 8:
            a, b = peak, len(rows) - 1
        else:
            a, b = max(peak, anchor - 2), min(len(rows) - 1, anchor + 2)
        # Shared biomechanical actions overlap; these windows do not assert a
        # strict observed ground/hip/chest/contact state transition sequence.
        a, b = min(a, anchor), max(b, anchor)
        output.append({"phase_id": f"M{index + 1:02d}", "label": label, "status": "kinematic_candidate",
                       "anchor_ms": round(float(t[anchor]), 3), "start_ms": round(float(t[a]), 3), "end_ms": round(float(t[b]), 3),
                       "source_frame": rows[anchor]["frame"], "source_start_frame": rows[a]["frame"], "source_end_frame": rows[b]["frame"],
                       "method": method, "physical_event_verified": False,
                       "window_semantics": "overlapping_kinematic_observation_not_strict_state_transition",
                       "required_for_physical_event": required.get(index, [])})
    return output
