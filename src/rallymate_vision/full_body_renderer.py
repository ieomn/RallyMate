"""A pose-driven illustrative body surface, with no inferred 3-D measurements.

The input landmarks stay fixed in the image plane. Anatomical thickness and
lighting are a generic presentation skin, not recovered body shape or depth.
"""
from __future__ import annotations

from typing import Any

import cv2
import numpy as np


FULL_BODY_STYLE = "continuous_full_body_white_mannequin_2_5d"
FULL_BODY_RENDERER_VERSION = "full-body-surface-v1.0.1"
LEGACY_STYLE = "white_capsule_mannequin_on_neutral_background"
# Includes the head surface, palm extension and generic side-profile thickness.
FULL_BODY_PADDING_TORSOS = .55


def _smooth_profile(phase: np.ndarray, profile: tuple[tuple[float, float], ...]) -> np.ndarray:
    """Shape-preserving cubic radius profile, avoiding hard rings in the skin."""
    x, y = np.array(profile, dtype=np.float32).T
    steps, secants = np.diff(x), np.diff(y) / np.diff(x)
    slopes = np.zeros_like(y)
    slopes[0], slopes[-1] = secants[0], secants[-1]
    for i in range(1, len(y) - 1):
        if secants[i - 1] * secants[i] > 0:
            w1, w2 = 2 * steps[i] + steps[i - 1], steps[i] + 2 * steps[i - 1]
            slopes[i] = (w1 + w2) / (w1 / secants[i - 1] + w2 / secants[i])
    index = np.clip(np.searchsorted(x, phase, side="right") - 1, 0, len(x) - 2)
    distance = x[index + 1] - x[index]
    t = (phase - x[index]) / distance
    return ((2 * t ** 3 - 3 * t ** 2 + 1) * y[index]
            + (t ** 3 - 2 * t ** 2 + t) * distance * slopes[index]
            + (-2 * t ** 3 + 3 * t ** 2) * y[index + 1]
            + (t ** 3 - t ** 2) * distance * slopes[index + 1])


class _Surface:
    """Small software height-field renderer for smoothly joined body volumes."""

    def __init__(self, width: int, height: int, scale: float):
        self.width, self.height, self.scale = width, height, scale
        self.depth = np.full((height, width), -10000, dtype=np.float32)
        self.mask = np.zeros((height, width), dtype=np.float32)

    def tube(self, a: np.ndarray, b: np.ndarray, profile: tuple[tuple[float, float], ...],
             *, z: float = 0, thickness: float = 1, blend: float = .04) -> None:
        """Rounded, variably tapered volume; all profile radii are pixel units."""
        radius = max(value for _, value in profile)
        lower = np.floor(np.minimum(a, b) - radius - 3).astype(int)
        upper = np.ceil(np.maximum(a, b) + radius + 3).astype(int)
        x0, y0 = np.maximum(lower, 0)
        x1, y1 = np.minimum(upper, [self.width, self.height])
        if x1 <= x0 or y1 <= y0:
            return
        yy, xx = np.mgrid[y0:y1, x0:x1].astype(np.float32)
        delta = b - a
        length2 = float(delta @ delta)
        phase = np.clip(((xx - a[0]) * delta[0] + (yy - a[1]) * delta[1]) / max(length2, .001), 0, 1)
        local_r = _smooth_profile(phase, profile)
        distance2 = (xx - a[0] - phase * delta[0]) ** 2 + (yy - a[1] - phase * delta[1]) ** 2
        signed = local_r - np.sqrt(distance2)
        alpha = np.clip(signed + .5, 0, 1).astype(np.float32)
        depth = z + np.sqrt(np.maximum(local_r ** 2 - distance2, 0)) * thickness
        old = self.depth[y0:y1, x0:x1]
        # A smooth maximum fuses shoulders, elbows, knees and hip connections
        # without drawing separate joint balls or changing landmark positions.
        k = max(.5, self.scale * blend)
        h = np.maximum(k - np.abs(old - depth), 0) / k
        merged = np.maximum(old, depth) + h * h * k * .25
        self.depth[y0:y1, x0:x1] = np.where(alpha > 0, merged, old)
        self.mask[y0:y1, x0:x1] = np.maximum(self.mask[y0:y1, x0:x1], alpha)

    def ellipsoid(self, center: np.ndarray, along: np.ndarray, length: float, radius: float,
                  *, z: float = 0, thickness: float = 1) -> None:
        direction = along / max(float(np.linalg.norm(along)), .001)
        normal = np.array([direction[1], -direction[0]])
        bound = max(length, radius) + 3
        x0, y0 = np.maximum(np.floor(center - bound).astype(int), 0)
        x1, y1 = np.minimum(np.ceil(center + bound).astype(int), [self.width, self.height])
        if x1 <= x0 or y1 <= y0:
            return
        yy, xx = np.mgrid[y0:y1, x0:x1].astype(np.float32)
        u = ((xx - center[0]) * direction[0] + (yy - center[1]) * direction[1]) / length
        v = ((xx - center[0]) * normal[0] + (yy - center[1]) * normal[1]) / radius
        norm2 = u * u + v * v
        alpha = np.clip((1 - np.sqrt(norm2)) * min(radius, length) + .5, 0, 1)
        depth = z + np.sqrt(np.maximum(1 - norm2, 0)) * radius * thickness
        old = self.depth[y0:y1, x0:x1]
        k = max(.5, self.scale * .04)
        h = np.maximum(k - np.abs(old - depth), 0) / k
        self.depth[y0:y1, x0:x1] = np.where(alpha > 0, np.maximum(old, depth) + h * h * k * .25, old)
        self.mask[y0:y1, x0:x1] = np.maximum(self.mask[y0:y1, x0:x1], alpha)

    def shade(self) -> np.ndarray:
        yy, xx = np.mgrid[:self.height, :self.width].astype(np.float32)
        # Neutral studio backdrop with a very subtle fixed vignette.
        vignette = np.clip(1 - ((xx / self.width - .48) ** 2 + (yy / self.height - .42) ** 2), .5, 1)
        canvas = np.stack([49 + 9 * vignette, 44 + 9 * vignette, 40 + 9 * vignette], axis=-1)
        visible = self.mask > 0
        if not visible.any():
            return canvas.astype(np.uint8)
        # Continue depth into a 2-pixel rim so the gradient does not differentiate
        # against the sentinel background at the silhouette.
        depth = np.where(visible, self.depth, 0)
        depth = cv2.GaussianBlur(depth, (0, 0), .7)
        gy, gx = np.gradient(depth)
        length = np.sqrt(gx * gx + gy * gy + 1)
        nx, ny, nz = -gx / length, -gy / length, 1 / length
        light = np.array([-.40, -.50, .77], dtype=np.float32)
        light /= np.linalg.norm(light)
        diffuse = np.maximum(nx * light[0] + ny * light[1] + nz * light[2], 0)
        fill = np.maximum(nx * .65 + ny * .05 + nz * .75, 0)
        specular = np.maximum(nx * -.20 + ny * -.25 + nz * .948, 0) ** 24
        brightness = np.clip(.58 + .34 * diffuse + .08 * fill + .035 * specular, 0, 1)
        white = brightness[..., None] * np.array([244, 247, 249], dtype=np.float32)
        # Ambient studio shadow attaches the surface to the image without an
        # invented moving floor plane or per-frame camera adjustment.
        shadow = cv2.GaussianBlur(self.mask, (0, 0), max(2, self.scale * .035))
        shadow = cv2.warpAffine(shadow, np.float32([[1, 0, self.scale * .025], [0, 1, self.scale * .025]]),
                                (self.width, self.height))
        canvas *= 1 - shadow[..., None] * .20
        return np.clip(canvas * (1 - self.mask[..., None]) + white * self.mask[..., None], 0, 255).astype(np.uint8)


def render_full_body_frame(frame: dict[str, Any], render: dict[str, Any]) -> np.ndarray:
    """Render a generic fuller human skin anchored to existing 2-D landmarks."""
    width, height = render["width"], render["height"]
    size = max(2.0, render["body_scale_source_px"] * render["transform"]["scale"])
    surface = _Surface(width, height, size)
    points = {p["name"]: np.array([p["x_px"], p["y_px"]], dtype=np.float32) for p in frame["keypoints"]}
    if not points:
        return surface.shade()

    def limb(a: str, b: str, profile: tuple[tuple[float, float], ...], *, z: float = 0,
             thickness: float = 1) -> None:
        if a in points and b in points:
            surface.tube(points[a], points[b], tuple((t, r * size) for t, r in profile),
                         z=z * size, thickness=thickness)

    torso_names = ("left_shoulder", "right_shoulder", "left_hip", "right_hip")
    has_torso = all(name in points for name in torso_names)
    if has_torso:
        ls, rs, lh, rh = (points[name] for name in torso_names)
        shoulders, hips = (ls + rs) / 2, (lh + rh) / 2
        axis = hips - shoulders
        unit = axis / max(float(np.linalg.norm(axis)), .001)
        normal = np.array([unit[1], -unit[0]])
        # Projected shoulder/hip width alone collapses to zero in a side view.
        # A fixed anatomical depth prior keeps a rib cage and pelvis visible.
        chest_width = max(abs(float((rs - ls) @ normal)) / 2 + size * .035, size * .23)
        hip_width = max(abs(float((rh - lh) @ normal)) / 2 + size * .07, size * .19)
        waist_width = min(chest_width * .79, hip_width * .89)
        start, end = shoulders + axis * .075, hips - axis * .08
        surface.tube(start, end,
                     ((0, chest_width * .79), (.15, chest_width), (.37, chest_width * .93),
                      (.63, waist_width), (.86, hip_width), (1, hip_width * .94)),
                     thickness=.79, blend=.07)
        # Blend the shoulder girdle and pelvis into the central trunk so arms
        # and legs start on a continuous surface even in oblique poses.
        surface.tube(ls, rs, ((0, size * .135), (.5, size * .16), (1, size * .135)), thickness=.9)
        surface.tube(lh, rh, ((0, size * .155), (.5, size * .19), (1, size * .155)), thickness=.85)
    else:
        shoulders = hips = axis = None

    for side in ("right", "left"):
        limb(f"{side}_hip", f"{side}_knee", ((0, .155), (.22, .175), (.53, .15), (.86, .10), (1, .095)),
             z=.005 if side == "right" else .015, thickness=.88)
        limb(f"{side}_knee", f"{side}_ankle", ((0, .09), (.24, .122), (.43, .114), (.73, .078), (1, .055)),
             z=.005 if side == "right" else .015)
        ankle = points.get(f"{side}_ankle")
        heel = points.get(f"{side}_heel")
        toes = [points[n] for n in (f"{side}_big_toe", f"{side}_small_toe") if n in points]
        if ankle is not None and toes:
            toe = np.mean(toes, axis=0)
            rear = heel if heel is not None else ankle
            surface.tube(rear, toe, ((0, size * .062), (.25, size * .075), (.8, size * .066), (1, size * .055)), thickness=.72)
            surface.tube(ankle, rear * .5 + toe * .5, ((0, size * .055), (1, size * .065)), thickness=.8)
        elif ankle is not None and heel is not None:
            # Keep the observed rear-foot segment when toe confidence drops.
            # No toe position or unobserved forward foot extension is invented.
            surface.tube(ankle, heel, ((0, size * .055), (1, size * .062)), thickness=.72)

    # Arm surface is slightly forward for legibility where projections overlap;
    # this is a declared, fixed artistic depth order, not a depth estimate.
    for side in ("right", "left"):
        z = .15 if side == "right" else .19
        limb(f"{side}_shoulder", f"{side}_elbow", ((0, .122), (.22, .12), (.53, .103), (.86, .079), (1, .077)), z=z)
        limb(f"{side}_elbow", f"{side}_wrist", ((0, .078), (.25, .093), (.5, .079), (.82, .055), (1, .046)), z=z)
        elbow, wrist = points.get(f"{side}_elbow"), points.get(f"{side}_wrist")
        if elbow is not None and wrist is not None:
            direction = wrist - elbow
            direction /= max(float(np.linalg.norm(direction)), .001)
            # The palm is a generic closed mitten aligned with the forearm.
            # Its end is a skin vertex, never an extra observed keypoint.
            surface.tube(wrist, wrist + direction * size * .135,
                         ((0, size * .043), (.32, size * .064), (.68, size * .062), (1, size * .038)),
                         z=z * size, thickness=.68)

    head, neck = points.get("head"), points.get("neck")
    if neck is None and shoulders is not None:
        neck = shoulders - axis / max(float(np.linalg.norm(axis)), .001) * size * .12
    head_rendered = False
    if head is not None and neck is not None:
        # HALPE's head landmark is the crown, not the center of an extra ball.
        head_axis = neck - head
        head_length = float(np.linalg.norm(head_axis))
        if head_length > size * .1:
            head_center = head + head_axis * .45
            half_height = np.clip(head_length * .49, size * .185, size * .26)
            head_radius = np.clip(head_length * .33, size * .13, size * .17)
            if shoulders is not None:
                surface.tube(neck, shoulders, ((0, size * .072), (1, size * .105)), thickness=.86)
            surface.tube(head_center + head_axis * .23, neck,
                         ((0, size * .07), (1, size * .075)), thickness=.84)
            surface.ellipsoid(head_center, head_axis, float(half_height), float(head_radius), thickness=.9)
            head_rendered = True
    if not head_rendered and neck is not None and "nose" in points:
        # Keep the earlier renderer's face-landmark fallback for an unobserved
        # or degenerate crown. These are decorative surface vertices, not filled
        # pose data; the still-observed face supplies the fallback location.
        face = [points[name] for name in ("left_eye", "right_eye", "left_ear", "right_ear") if name in points]
        center = np.mean(face, axis=0) if face else points["nose"]
        if shoulders is not None:
            surface.tube(neck, shoulders, ((0, size * .072), (1, size * .105)), thickness=.86)
        surface.tube(center, neck, ((0, size * .07), (1, size * .075)), thickness=.84)
        surface.ellipsoid(center, neck - center, size * .215, size * .145, thickness=.9)
    return surface.shade()
