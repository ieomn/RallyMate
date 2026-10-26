"""Conservative, sequence-local subject association, independent of pose quality.

Clothing appearance and image continuity are association evidence, not biometric
identity or a calibrated probability. A missing target never causes a new person
to inherit the target label. The same lock is used before SAM and after decoding.
"""
from __future__ import annotations

import math
from collections import deque
from typing import Any

import numpy as np

from rallymate_vision.utils import box_iou

SUBJECT_IDENTITY_VERSION = "subject-lock-v1"
# Elapsed source time, not a number of frames. A longer gap needs appearance.
CONTINUITY_GAP_MS = 250
APPEARANCE_MATCH_MAX = .30
APPEARANCE_VETO_MIN = .58
REIDENTIFICATION_MARGIN = .10
REIDENTIFICATION_SUPPORT_MS = 80


def valid_subject_identity(value: Any, player_tracks: set[int]) -> bool:
    if not isinstance(value, dict) or value.get("version") != SUBJECT_IDENTITY_VERSION:
        return False
    if not isinstance(value.get("evidence"), list) or not all(isinstance(v, str) for v in value["evidence"]):
        return False
    if value.get("status") == "unknown":
        return (value.get("subject_id") is None and value.get("source_track_id") is None
                and isinstance(value.get("reason_code"), str) and bool(value["reason_code"]))
    return (value.get("status") in {"locked", "reidentified"} and value.get("subject_id") == "subject-1"
            and type(value.get("source_track_id")) is int and value["source_track_id"] in player_tracks
            and bool(value["evidence"]) and value.get("reason_code") is None)


def clothing_descriptor(image: np.ndarray | None, box: list[float]) -> list[float] | None:
    """Two coarse HSV clothing histograms; no face recognition or embeddings."""
    if image is None:
        return None
    import cv2

    h, w = image.shape[:2]
    x1, y1, x2, y2 = box
    bw, bh = x2 - x1, y2 - y1
    if bw < 12 or bh < 40 or x1 < 0 or y1 < 0 or x2 > w or y2 > h:
        return None
    parts = []
    for top, bottom in ((.18, .48), (.50, .72)):
        crop = image[int(y1 + top * bh):int(y1 + bottom * bh),
                     int(x1 + .28 * bw):int(x1 + .72 * bw)]
        if crop.size < 36:
            return None
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        # For colored clothing, illumination should not change the identity
        # template merely by crossing a floodlight. Retain value for neutral
        # clothing so white and black do not collapse into the same bin.
        pixels = hsv.reshape(-1, 3).astype(int)
        bins = (pixels[:, 0] // 15) * 3 + np.minimum(pixels[:, 1] // 86, 2)
        neutral = pixels[:, 1] < 45
        bins[neutral] = 36 + np.minimum(pixels[neutral, 2] // 64, 3)
        hist = np.bincount(bins, minlength=40).astype(float)
        parts.extend((hist / max(float(hist.sum()), 1.)).tolist())
    return parts


def appearance_distance(a: list[float] | None, b: list[float] | None) -> float | None:
    if a is None or b is None:
        return None
    left, right = np.asarray(a), np.asarray(b)
    if left.shape != (80,) or right.shape != (80,):
        return None
    # Mean Hellinger distance across upper/lower clothing, each normalized.
    return float(np.sqrt(np.maximum(0., 1. - np.sqrt(left * right).reshape(2, -1).sum(axis=1))).mean())


def racket_owners(detections: list[dict]) -> set[int]:
    """Only uniquely nearby generic racket boxes support choosing an athlete."""
    players = [d for d in detections if d.get("class_name") == "player"]
    owners = set()
    for racket in detections:
        if racket.get("class_name") != "racket" or racket.get("confidence", 0.) < .35:
            continue
        rx1, ry1, rx2, ry2 = racket["bbox_px"]
        rx, ry = (rx1 + rx2) / 2, (ry1 + ry2) / 2
        choices = []
        for player in players:
            x1, y1, x2, y2 = player["bbox_px"]
            height = max(y2 - y1, 1.)
            if ry2 - ry1 > height or ry < y1 - height * .25 or ry > y2:
                continue
            distance = math.hypot(max(x1 - rx, 0., rx - x2), max(y1 - ry, 0., ry - y2)) / height
            choices.append((distance, player["track_id"]))
        choices.sort()
        if choices and choices[0][0] <= .35 and (len(choices) == 1 or choices[1][0] - choices[0][0] >= .15):
            owners.add(choices[0][1])
    return owners


class SubjectLock:
    """Lock one target; reject unsupported track changes and wait for re-entry."""

    def __init__(self):
        self.last: dict | None = None
        self.gallery: deque[list[float]] = deque(maxlen=16)
        self.anchor: list[float] | None = None
        self.pending: tuple[int, float] | None = None
        self.pending_count = 0
        self.pending_last_ms = -math.inf
        self.last_gallery_ms = -math.inf
        self.crowded_scene = False
        self.initial_motion: dict[int, deque] = {}
        self.last_uncropped_height: float | None = None
        self.initial_tracks: set[int] | None = None

    @staticmethod
    def unknown(reason: str, **evidence: Any) -> dict:
        return {"version": SUBJECT_IDENTITY_VERSION, "subject_id": None,
                "status": "unknown", "source_track_id": None, "evidence": [],
                "reason_code": reason, **evidence}

    def _distance(self, descriptor) -> float | None:
        distances = [appearance_distance(value, descriptor) for value in [self.anchor, *self.gallery]]
        distances = [value for value in distances if value is not None]
        return min(distances) if distances else None

    def _accept(self, candidate, timestamp, camera_epoch, status, evidence, distance=None):
        descriptor = candidate["descriptor"]
        if descriptor is not None:
            if self.anchor is None:
                self.anchor = descriptor
            anchor_distance = appearance_distance(self.anchor, descriptor)
            recent_distance = appearance_distance(self.last["descriptor"], descriptor) if self.last else None
            # Freeze the original clothing anchor. Never learn a mismatching
            # person merely because the detector kept the same integer ID.
            gradual = (self.last is not None and self.last["track_id"] == candidate["track_id"]
                       and recent_distance is not None and recent_distance <= .25)
            if timestamp - self.last_gallery_ms >= 200 and (anchor_distance <= .42 or gradual):
                self.gallery.append(descriptor)
                self.last_gallery_ms = timestamp
        if not candidate.get("clipped") or self.last_uncropped_height is None:
            self.last_uncropped_height = candidate["bbox_px"][3] - candidate["bbox_px"][1]
        self.last = {**candidate, "timestamp_ms": timestamp, "camera_epoch": camera_epoch}
        self.pending = None
        self.pending_count = 0
        return {"version": SUBJECT_IDENTITY_VERSION, "subject_id": "subject-1",
                "status": status, "source_track_id": candidate["track_id"],
                "evidence": evidence, "reason_code": None,
                "appearance_distance": round(distance, 6) if distance is not None else None,
                "semantics": "sequence_local_visual_association_not_verified_person_identity"}

    def update(self, detections: list[dict], *, timestamp_ms: float, width: int, height: int,
               image: np.ndarray | None = None, camera_epoch: int | None = None,
               camera_motion: dict | None = None,
               preferred_track_id: int | None = None, initial_ambiguous: bool = False) -> dict:
        candidates = []
        for detection in detections:
            if detection.get("class_name") != "player" or type(detection.get("track_id")) is not int:
                continue
            box = detection.get("bbox_px", [])
            if len(box) != 4 or not all(math.isfinite(float(v)) for v in box):
                continue
            x1, y1, x2, y2 = box
            if x2 <= x1 or y2 <= y1:
                continue
            descriptor = clothing_descriptor(image, box)
            clipped = x1 <= .5 or y1 <= .5 or x2 >= width - .5 or y2 >= height - .5
            # Feet or a racket-side arm touching the image edge do not erase
            # observable clothing. Reject a cropped remnant only when the
            # remaining box lost most of the last unclipped body's height.
            remnant = clipped and self.last_uncropped_height is not None and y2 - y1 < self.last_uncropped_height * .65
            usable = image is None or (descriptor is not None and not remnant)
            candidates.append({**detection, "descriptor": descriptor, "identity_usable": usable, "clipped": clipped})
        candidates.sort(key=lambda c: c["track_id"])
        if not candidates:
            self.pending = None
            self.pending_count = 0
            return self.unknown("target_not_detected")
        if self.last is None:
            if image is None:
                chosen = next((c for c in candidates if c["track_id"] == preferred_track_id), candidates[0])
                if initial_ambiguous:
                    return self.unknown("initial_target_ambiguous")
                return self._accept(chosen, timestamp_ms, camera_epoch, "locked", ["initial_detector_track_anchor"])
            owners = racket_owners(detections)
            if self.initial_tracks is None:
                self.initial_tracks = {c["track_id"] for c in candidates}
            self.crowded_scene |= len(candidates) >= 6
            visible_body_scores = {
                c["track_id"]: (c["bbox_px"][2] - c["bbox_px"][0]) * (c["bbox_px"][3] - c["bbox_px"][1]) * c.get("confidence", 0.)
                for c in candidates if c["descriptor"] is not None
                and .15 <= (c["bbox_px"][2] - c["bbox_px"][0]) / (c["bbox_px"][3] - c["bbox_px"][1]) <= .95
                and c.get("confidence", 0.) >= .45
            }
            eligible = []
            for c in candidates:
                x1, y1, x2, y2 = c["bbox_px"]
                bw, bh = x2 - x1, y2 - y1
                history = self.initial_motion.setdefault(c["track_id"], deque())
                center = np.asarray([(x1 + x2) / 2, (y1 + y2) / 2, 1.])
                compensated = False
                if camera_motion and camera_motion.get("compensation_valid"):
                    matrix = np.asarray(camera_motion.get("matrix_to_reference"), dtype=float)
                    if matrix.shape == (2, 3) and np.isfinite(matrix).all():
                        center = matrix @ center
                        compensated = True
                history.append((timestamp_ms, camera_epoch, center[:2], bw / bh, bh, compensated))
                while history and timestamp_ms - history[0][0] > 1200:
                    history.popleft()
                stable = [r for r in history if r[1] == camera_epoch]
                active = False
                if len(stable) >= 3 and stable[-1][0] - stable[0][0] >= 200:
                    # Camera pan is not athlete motion. Box articulation can be
                    # checked without camera translation; center displacement
                    # needs independently valid background compensation.
                    shape_change = float(np.percentile([r[3] for r in stable], 90) - np.percentile([r[3] for r in stable], 10))
                    displacement = (float(np.linalg.norm(stable[-1][2] - stable[0][2])) / max(bh, 1.)
                                    if all(r[5] for r in stable) else 0.)
                    active = shape_change >= .18 or displacement >= .25
                # A cropped/sideways spectator is not a full-body target merely
                # because their box is large. Small/crowded scenes need racket
                # evidence; size alone is not sufficient athlete identification.
                # Do not seed clothing from a sideways torso fragment. Feet at
                # the bottom edge are allowed because both clothing crops lie
                # above that edge; subsequent association handles partial exits.
                full = c["identity_usable"] and x1 > width * .005 and x2 < width * .995 and y1 > 0
                if not full or not .15 <= bw / bh <= .95 or c.get("confidence", 0.) < .45:
                    continue
                if c["track_id"] not in owners and bh / height < .18:
                    continue
                score = bw * bh * c.get("confidence", 0.)
                others = [score_other for track, score_other in visible_body_scores.items() if track != c["track_id"]]
                clearly_foreground = (score >= max(others, default=0.) * 2 and bh / height >= .18
                                      and x1 > width * .01 and x2 < width * .99 and y2 < height * .95
                                      and c.get("confidence", 0.) >= .8 and c["track_id"] in self.initial_tracks)
                if self.crowded_scene and not clearly_foreground and (c["track_id"] not in owners or not active):
                    continue
                # In a crowd, a new boundary-adjacent body can be a foreground
                # spectator. Do not establish athlete role from its apparent
                # size/shape change as the camera crops it. Once a target is
                # established, mild foot cropping is handled separately above.
                if self.crowded_scene and not clearly_foreground and y2 >= height * .98:
                    continue
                c["initial_role_evidence"] = ("dominant_visible_target" if clearly_foreground else
                                              "uniquely_associated_racket_with_activity" if self.crowded_scene else
                                              "uniquely_associated_racket" if c["track_id"] in owners else "dominant_visible_target")
                eligible.append((int(c["track_id"] in owners), score, c))
            eligible.sort(key=lambda v: (-v[0], -v[1], v[2]["track_id"]))
            if not eligible:
                return self.unknown("athlete_role_not_established")
            has_racket, score, chosen = eligible[0]
            if len(eligible) > 1 and eligible[1][0] == has_racket and score < eligible[1][1] * 1.7:
                return self.unknown("initial_target_ambiguous")
            return self._accept(chosen, timestamp_ms, camera_epoch, "locked",
                                [chosen["initial_role_evidence"], "clothing_anchor"])

        elapsed = timestamp_ms - self.last["timestamp_ms"]
        previous_box = self.last["bbox_px"]
        previous_height = previous_box[3] - previous_box[1]
        same_epoch = camera_epoch == self.last["camera_epoch"]
        spatial = []
        for c in candidates:
            box = c["bbox_px"]
            distance = self._distance(c["descriptor"])
            c["appearance_distance"] = distance
            if not c["identity_usable"]:
                c["appearance_distance"] = None
                continue
            center_step = math.hypot((box[0] + box[2] - previous_box[0] - previous_box[2]) / 2,
                                     (box[1] + box[3] - previous_box[1] - previous_box[3]) / 2) / max(previous_height, 1)
            scale = (box[3] - box[1]) / max(previous_height, 1)
            same_track = c["track_id"] == self.last["track_id"]
            near = (0 <= elapsed <= CONTINUITY_GAP_MS and .65 <= scale <= 1.55
                    and center_step <= .60 and box_iou(previous_box, box) >= .15)
            # A changed ID requires immediate overlap, same camera epoch, and no
            # competing same-ID detection; a distant larger player never wins.
            fragmented = (same_epoch and box_iou(previous_box, box) >= .5
                          and not any(p["track_id"] == self.last["track_id"] for p in candidates))
            appearance_limit = APPEARANCE_VETO_MIN if same_track else APPEARANCE_MATCH_MAX
            recent_distance = appearance_distance(self.last["descriptor"], c["descriptor"])
            gradual = same_track and recent_distance is not None and recent_distance <= .25
            if near and (same_track or fragmented) and (distance is None or distance < appearance_limit or gradual):
                spatial.append((same_track, -center_step, c))
        spatial.sort(key=lambda v: (-int(v[0]), -v[1], v[2]["track_id"]))
        if len(spatial) == 1:
            same_track, _, chosen = spatial[0]
            return self._accept(chosen, timestamp_ms, camera_epoch, "locked",
                                ["continuous_track_and_box" if same_track else "unique_overlapping_tracklet"],
                                chosen["appearance_distance"])
        if len(spatial) > 1:
            return self.unknown("overlapping_identity_candidates")

        matches = sorted([(c["appearance_distance"], c) for c in candidates
                          if c["appearance_distance"] is not None], key=lambda v: (v[0], v[1]["track_id"]))
        if not matches or matches[0][0] > APPEARANCE_MATCH_MAX:
            self.pending = None
            self.pending_count = 0
            return self.unknown("target_identity_unmatched")
        if len(matches) > 1 and matches[1][0] - matches[0][0] < REIDENTIFICATION_MARGIN:
            self.pending = None
            self.pending_count = 0
            return self.unknown("appearance_match_not_unique")
        distance, chosen = matches[0]
        if (self.pending is None or self.pending[0] != chosen["track_id"]
                or not 0 < timestamp_ms - self.pending_last_ms <= CONTINUITY_GAP_MS):
            self.pending = (chosen["track_id"], timestamp_ms)
            self.pending_count = 1
        else:
            self.pending_count += 1
        self.pending_last_ms = timestamp_ms
        if timestamp_ms - self.pending[1] < REIDENTIFICATION_SUPPORT_MS or self.pending_count < 3:
            return self.unknown("appearance_match_awaiting_temporal_support", candidate_source_track_id=chosen["track_id"])
        return self._accept(chosen, timestamp_ms, camera_epoch, "reidentified",
                            ["unique_upper_lower_clothing_match", "temporally_supported_reentry"], distance)
