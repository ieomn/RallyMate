"""Bounded source-pixel lookahead before the first athlete identity is known.

Only detector observations are buffered. Every released frame is still sent to
the pose backend in source order with its own pixels and decoder binding.
"""
from __future__ import annotations

from collections import deque
import math

from .subject_lock import (APPEARANCE_MATCH_MAX, REIDENTIFICATION_MARGIN,
                           appearance_distance, clothing_descriptor)
from rallymate_vision.utils import box_iou

VERSION = "initial-subject-prefix-v1"


class InitialSubjectBuffer:
    def __init__(self, *, max_duration_ms=1200, max_frames=96, max_bytes=192 * 1024 * 1024):
        self.max_duration_ms = max_duration_ms
        self.max_frames = max_frames
        self.max_bytes = max_bytes
        self.pending = deque()
        self.bytes = 0
        self.finished = False

    def _pop(self):
        item = self.pending.popleft()
        self.bytes -= item["image"].nbytes
        return item

    def push(self, item):
        if self.finished:
            return [item]
        self.pending.append(item)
        self.bytes += item["image"].nbytes
        released = []
        while self.pending and (len(self.pending) > self.max_frames or self.bytes > self.max_bytes
               or item["timestamp_ms"] - self.pending[0]["timestamp_ms"] > self.max_duration_ms):
            released.append(self._pop())
        identity = item["subject_identity"]
        if identity.get("subject_id") is not None:
            self._confirm_prefix(item)
            self.finished = True
            released.extend(self.finish())
        return released

    def finish(self):
        result = []
        while self.pending:
            result.append(self._pop())
        return result

    def _confirm_prefix(self, anchor):
        identity = anchor["subject_identity"]
        if (anchor["timestamp_source"] != "decoder_pts"
                or not anchor["camera_motion"].get("compensation_valid")
                or anchor["camera_motion"].get("status") not in {"reference", "stationary", "moving"}):
            return
        track = identity["source_track_id"]
        candidates = [d for d in anchor["detections"] if d.get("class_name") == "player" and d.get("track_id") == track]
        if len(candidates) != 1:
            return
        next_item, next_box = anchor, candidates[0]["bbox_px"]
        anchor_descriptor = clothing_descriptor(anchor["image"], next_box)
        next_descriptor = anchor_descriptor
        if anchor_descriptor is None:
            return
        for item in reversed(list(self.pending)[:-1]):
            original = item["subject_identity"]
            # This is first-lock lookahead, never reacquisition across an exit.
            if original.get("status") != "unknown" or original.get("reason_code") not in {
                    "athlete_role_not_established", "initial_target_ambiguous"}:
                break
            if (item["timestamp_source"] != "decoder_pts"
                    or item["camera_motion"].get("reference_epoch") != anchor["camera_motion"].get("reference_epoch")
                    or item["camera_motion"].get("status") not in {"reference", "stationary", "moving"}
                    or not item["camera_motion"].get("compensation_valid")
                    or not 0 < next_item["timestamp_ms"] - item["timestamp_ms"] <= 160
                    or next_item["source_frame_index"] - item["source_frame_index"] != item["frame_stride"]):
                break
            players = [d for d in item["detections"] if d.get("class_name") == "player"]
            matches = [d for d in players if d.get("track_id") == track]
            if len(matches) != 1:
                break
            box = matches[0]["bbox_px"]
            descriptor = clothing_descriptor(item["image"], box)
            distance = appearance_distance(anchor_descriptor, descriptor)
            recent = appearance_distance(next_descriptor, descriptor)
            height, next_height = box[3] - box[1], next_box[3] - next_box[1]
            step = math.hypot((box[0] + box[2] - next_box[0] - next_box[2]) / 2,
                              (box[1] + box[3] - next_box[1] - next_box[3]) / 2) / max(next_height, 1)
            if (distance is None or recent is None or distance > APPEARANCE_MATCH_MAX or recent > APPEARANCE_MATCH_MAX
                    or not .65 <= height / max(next_height, 1) <= 1.55 or step > .60 or box_iou(box, next_box) < .5):
                break
            # Similar nearby people make the earlier ownership ambiguous even
            # when a detector happened to preserve one integer track ID.
            ambiguous = False
            for player in players:
                if player.get("track_id") == track:
                    continue
                other_box = player["bbox_px"]
                other_distance = appearance_distance(anchor_descriptor, clothing_descriptor(item["image"], other_box))
                if box_iou(box, other_box) >= .15 and (other_distance is None or other_distance - distance < REIDENTIFICATION_MARGIN):
                    ambiguous = True
                    break
            if ambiguous:
                break
            item["subject_identity"] = {**identity, "status": "locked",
                "evidence": ["later_athlete_role_anchor", "same_track_camera_and_unique_clothing_prefix"],
                "appearance_distance": round(distance, 6),
                "initialization_confirmation": {"version": VERSION,
                    "original_status": original["status"], "original_reason_code": original["reason_code"],
                    "confirmation_source_frame_index": anchor["source_frame_index"],
                    "confirmation_timestamp_ms": anchor["timestamp_ms"],
                    "lookahead_ms": anchor["timestamp_ms"] - item["timestamp_ms"]}}
            next_item, next_box, next_descriptor = item, box, descriptor
