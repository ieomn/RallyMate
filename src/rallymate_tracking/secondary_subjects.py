"""Conservative secondary-person association before expensive pose inference.

Detector track integers are not identities: a far player's track can move onto
a small background object. Keep a body-size anchor, require evidence before a
new association, and never fill an absent player's slot with the next largest
box. This selector does not change the independently established primary lock.
"""
from __future__ import annotations

import math
from collections import deque

from .subject_lock import appearance_distance, clothing_descriptor

VERSION = "secondary-subject-selection-v1"
CONFIRM_MS = 80.0
SUPPORT_GAP_MS = 250.0


class SecondarySubjectSelector:
    def __init__(self, *, max_secondaries: int = 1):
        self.max_secondaries = max(0, int(max_secondaries))
        self.slots: list[dict] = []
        self.pending: dict[tuple, dict] = {}
        self.last_timestamp: float | None = None
        self.camera_epoch = None

    @staticmethod
    def _candidate(player, width, height, image):
        box = player.get("bbox_px")
        confidence = player.get("confidence")
        if (type(player.get("track_id")) is not int or not isinstance(box, (list, tuple))
                or len(box) != 4 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in box)
                or not (0 <= box[0] < box[2] <= width and 0 <= box[1] < box[3] <= height)
                or not isinstance(confidence, (int, float)) or not math.isfinite(confidence)
                or not 0 <= confidence <= 1):
            return None
        x1, y1, x2, y2 = box
        return {"player": player, "track": player["track_id"], "box": list(box),
                "height": y2 - y1, "width": x2 - x1,
                "center": ((x1 + x2) / 2, (y1 + y2) / 2),
                "confidence": confidence,
                # A 50 px far player leaves only a handful of compressed shirt
                # pixels. Their histogram changes with racket/net occlusion and
                # cannot veto otherwise coherent body geometry.
                "descriptor": clothing_descriptor(image, box) if y2 - y1 >= 80 else None}

    def _supported(self, key, candidate, timestamp, *, eligible):
        if not eligible:
            self.pending.pop(key, None)
            return False
        previous = self.pending.get(key)
        if previous is not None:
            # Even a recycled integer must build support for one coherent box.
            movement = math.dist(previous["center"], candidate["center"])
            scale = candidate["height"] / previous["height"]
            if (timestamp - previous["last"] > SUPPORT_GAP_MS or not .65 <= scale <= 1.55
                    or movement > max(previous["height"], candidate["height"]) * .75):
                previous = None
        if previous is None:
            previous = {"first": timestamp, "count": 0}
        previous.update(last=timestamp, count=previous["count"] + 1,
                        center=candidate["center"], height=candidate["height"])
        self.pending[key] = previous
        return previous["count"] >= 3 and timestamp - previous["first"] >= CONFIRM_MS

    @staticmethod
    def _association(slot, candidate, timestamp):
        anchor_height = sorted(slot["heights"])[len(slot["heights"]) // 2]
        height_ratio = candidate["height"] / anchor_height
        old_box, box = slot["box"], candidate["box"]
        gap = timestamp - slot["last"]
        distance = math.dist(slot["center"], candidate["center"]) / anchor_height
        # An upper-body detection may retain the head while legs are occluded.
        # A lower remnant (the recurring red-object false positive) does not.
        upper_fragment = (.30 <= height_ratio < .65
                          and abs(box[1] - old_box[1]) <= anchor_height * .18)
        if height_ratio < .65 and not upper_fragment:
            return False, distance, "body_height_collapsed"
        if height_ratio > 1.8:
            return False, distance, "body_scale_changed"
        # Permit motion over missing source time, bounded even after long gaps.
        if distance > min(5., .65 + max(gap, 0.) * .002):
            return False, distance, "position_discontinuous"
        appearance = appearance_distance(slot["descriptor"], candidate["descriptor"])
        if appearance is not None and appearance > .58:
            return False, distance, "appearance_changed"
        return True, distance, "upper_body_continuity" if upper_fragment else "body_continuity"

    @staticmethod
    def _accept(slot, candidate, timestamp):
        previous_height = sorted(slot["heights"])[len(slot["heights"]) // 2]
        # Do not let a succession of partial boxes shrink the person template.
        if candidate["height"] >= previous_height * .75:
            slot["heights"].append(candidate["height"])
            slot["box"] = candidate["box"]
        slot.update(track=candidate["track"], center=candidate["center"], last=timestamp)
        if candidate["descriptor"] is not None:
            if slot["descriptor"] is None:
                slot["descriptor"] = candidate["descriptor"]
            elif appearance_distance(slot["descriptor"], candidate["descriptor"]) <= .30:
                slot["descriptor"] = candidate["descriptor"]

    def update(self, players, *, primary_track_id, timestamp_ms, width, height,
               image=None, camera_epoch=None):
        """Return selected detections and a JSON-safe decision receipt.

        Call in source order, independently of model batching. Unknown detector
        confidence cannot establish a secondary person; it remains diagnostic
        evidence rather than being fabricated as a high confidence value.
        """
        timestamp = float(timestamp_ms)
        if not math.isfinite(timestamp):
            raise ValueError("secondary subject timestamp must be finite")
        reset = (self.last_timestamp is not None and
                 (timestamp <= self.last_timestamp or camera_epoch != self.camera_epoch))
        if reset:
            self.slots.clear()
            self.pending.clear()
        self.last_timestamp, self.camera_epoch = timestamp, camera_epoch
        self.pending = {key: value for key, value in self.pending.items()
                        if timestamp - value["last"] <= SUPPORT_GAP_MS}
        primary = ([p for p in players if p.get("track_id") == primary_track_id]
                   if type(primary_track_id) is int else [])
        receipt = {"version": VERSION, "primary_track_id": primary_track_id,
                   "selected_track_ids": [], "secondary_track_ids": [],
                   "reset": bool(reset), "decisions": []}
        if len(primary) != 1:
            receipt["reason"] = "primary_not_visible"
            return [], receipt
        selected = primary[:1]
        receipt["selected_track_ids"] = [primary_track_id]
        if not self.max_secondaries:
            return selected, receipt
        candidates = []
        for player in players:
            if player.get("track_id") == primary_track_id:
                continue
            candidate = self._candidate(player, width, height, image)
            if candidate is None:
                receipt["decisions"].append({"track_id": player.get("track_id"),
                    "decision": "rejected", "reason": "invalid_secondary_detection"})
            else:
                candidates.append(candidate)
        # Determinism is independent of detector list order and inference batch.
        candidates.sort(key=lambda c: c["track"])
        used, reasons = set(), {}
        for slot_index, slot in enumerate(self.slots):
            matches = []
            for candidate in candidates:
                if candidate["track"] in used:
                    continue
                compatible, distance, reason = self._association(slot, candidate, timestamp)
                reasons.setdefault(candidate["track"], reason)
                if compatible:
                    matches.append((distance, candidate["track"], candidate, reason))
            matches.sort(key=lambda item: item[:2])
            # Close alternatives require waiting; an integer ID is not proof.
            if len(matches) > 1 and matches[1][0] - matches[0][0] < .15:
                for _, track, _, _ in matches:
                    reasons[track] = "secondary_association_ambiguous"
                continue
            if not matches:
                continue
            _, track, candidate, reason = matches[0]
            continuous = track == slot["track"] and timestamp - slot["last"] <= SUPPORT_GAP_MS
            confirmed = continuous or self._supported((slot_index, track), candidate, timestamp,
                                                       eligible=candidate["confidence"] >= .15)
            if not confirmed:
                reasons[track] = "secondary_reidentification_pending"
                continue
            self._accept(slot, candidate, timestamp)
            selected.append(candidate["player"])
            used.add(track)
            reasons[track] = reason
        # Established missing identities retain their slot. A bystander or a
        # background detection cannot inherit it by being the largest leftover.
        available = self.max_secondaries - len(self.slots)
        if available > 0:
            confirmed = []
            for candidate in candidates:
                if candidate["track"] in used:
                    continue
                # Use both source-relative size and the pixels actually present:
                # upscaling a frame must not turn a weak tiny object into a new
                # confidently established person.
                tiny = (candidate["height"] < max(40., height * .055)
                        or candidate["width"] < max(12., width * .008))
                eligible = candidate["confidence"] >= (.75 if tiny else .45)
                supported = self._supported(("new", candidate["track"]), candidate, timestamp,
                                             eligible=eligible)
                reasons[candidate["track"]] = ("secondary_confirmation_pending" if eligible
                                                else "insufficient_new_person_evidence")
                if supported:
                    confirmed.append(candidate)
            confirmed.sort(key=lambda c: (-c["height"] * c["width"], c["track"]))
            for candidate in confirmed[:available]:
                self.slots.append({"track": candidate["track"], "box": candidate["box"],
                    "center": candidate["center"], "last": timestamp,
                    "heights": deque([candidate["height"]], maxlen=16),
                    "descriptor": candidate["descriptor"]})
                selected.append(candidate["player"])
                used.add(candidate["track"])
                reasons[candidate["track"]] = "secondary_confirmed"
        for candidate in candidates:
            track = candidate["track"]
            receipt["decisions"].append({"track_id": track,
                "decision": "selected" if track in used else "rejected",
                "reason": reasons.get(track, "secondary_slot_reserved")})
        receipt["secondary_track_ids"] = [p["track_id"] for p in selected[1:]]
        receipt["selected_track_ids"] += receipt["secondary_track_ids"]
        return selected, receipt
