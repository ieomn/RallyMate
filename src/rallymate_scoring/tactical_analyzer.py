"""Spatial placement labels over bound court evidence, not inferred intent."""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence


VERSION = "court-placement-tactics-v1.0.0"
TS08_LABELS = {
    "TS08-01": "斜线", "TS08-02": "直线", "TS08-03": "中路", "TS08-04": "深区",
    "TS08-05": "浅区", "TS08-06": "大角度", "TS08-07": "追身", "TS08-08": "空档",
}
FAMILY_MODULES = {"serve": "TS01", "return": "TS02", "baseline": "TS03",
                  "attack_transition": "TS04", "defense": "TS05", "volley": "TS06", "overhead": "TS07"}
# Published normalized-court engineering bins. These are descriptive bins,
# never source-authorized technical-grade or psychological thresholds.
SPATIAL_BINS = {"center_half_width": .15, "line_max_dx": .12,
                "crosscourt_min_dx": .20, "deep_depth_fraction": .70,
                "short_depth_fraction": .30, "wide_edge_fraction": .15,
                "body_radius": .10, "open_court_distance": .35}


def _point(value):
    if not isinstance(value, Mapping):
        return None
    x, y = value.get("x"), value.get("y")
    if any(isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v) for v in (x, y)):
        return None
    return float(x), float(y)


def analyze_shot_placement(context: Mapping) -> dict:
    """Require actual bounce evidence before calling a projection a landing.

    Court coordinates map far-left baseline to (0, 0), near-right baseline to
    (1, 1). ``contact_position_court`` is the player's ground-plane position,
    never an airborne ball projected through a planar homography.
    """
    result = {"version": VERSION, "episode_id": context.get("episode_id"), "tags": [],
              "status": "insufficient_placement_evidence", "module": FAMILY_MODULES.get(context.get("family")),
              "semantics": "observed_placement_pattern_not_tactical_intent", "score": None}
    contact = _point(context.get("contact_position_court"))
    landing = _point(context.get("landing_position_court"))
    proof = context.get("landing_evidence") or {}
    if contact is None or landing is None or proof.get("bounce_confirmed") is not True:
        return {**result, "missing": [key for key, condition in (
            ("player_ground_position_at_contact", contact is not None),
            ("confirmed_bounce_court_position", landing is not None and proof.get("bounce_confirmed") is True)) if not condition]}
    if type(proof.get("source_frame")) is not int or proof["source_frame"] < 0:
        return {**result, "missing": ["bounce_source_frame_binding"]}
    near_player = contact[1] >= .5
    opposite_half = landing[1] < .5 if near_player else landing[1] > .5
    if not opposite_half:
        return {**result, "status": "observed_same_half_landing", "missing": [], "landing_in_opponent_half": False}
    dx = abs(landing[0] - contact[0])
    depth = abs(landing[1] - .5) * 2
    tags = []
    if (landing[0] - .5) * (contact[0] - .5) < 0 and dx >= SPATIAL_BINS["crosscourt_min_dx"]:
        tags.append("TS08-01")
    if dx <= SPATIAL_BINS["line_max_dx"]:
        tags.append("TS08-02")
    if abs(landing[0] - .5) <= SPATIAL_BINS["center_half_width"]:
        tags.append("TS08-03")
    if depth >= SPATIAL_BINS["deep_depth_fraction"]:
        tags.append("TS08-04")
    if depth <= SPATIAL_BINS["short_depth_fraction"]:
        tags.append("TS08-05")
    if min(landing[0], 1 - landing[0]) <= SPATIAL_BINS["wide_edge_fraction"] and dx > .20:
        tags.append("TS08-06")
    opponent = _point(context.get("opponent_position_court"))
    if opponent is not None:
        distance = math.dist(landing, opponent)
        if distance <= SPATIAL_BINS["body_radius"]:
            tags.append("TS08-07")
        if distance >= SPATIAL_BINS["open_court_distance"]:
            tags.append("TS08-08")
    return {**result, "status": "measured_placement", "tags": [{"id": code, "name_zh": TS08_LABELS[code]} for code in tags],
            "missing": [], "landing_in_opponent_half": True, "landing_in_court": all(0 <= v <= 1 for v in landing),
            "court_position": {"contact": list(contact), "landing": list(landing)},
            "source_frames": [proof["source_frame"]], "opponent_position_available": opponent is not None}


def analyze_tactics(contexts: Sequence[Mapping]) -> dict:
    rows = [analyze_shot_placement(context) for context in contexts]
    counts = {code: sum(any(tag["id"] == code for tag in row["tags"]) for row in rows) for code in TS08_LABELS}
    return {"version": VERSION, "shots": rows, "placement_counts": counts,
            "module_counts": {module: sum(row["module"] == module for row in rows) for module in FAMILY_MODULES.values()},
            "spatial_bins": dict(SPATIAL_BINS), "measured_placement_count": sum(row["status"] == "measured_placement" for row in rows),
            "semantics": "descriptive_counts_no_intent_or_point_outcome_inference"}


def contextual_technique_profile(context: Mapping) -> dict:
    """Route verified context with opposite short-swing rules for defense/net.

    The caller supplies measured/confirmed booleans; an unknown bounce is not
    treated as a no-bounce volley. A high arm alone never establishes serve.
    """
    family = context.get("family")
    module = {"serve": "SS", "return": "RS", "baseline": "GS", "volley": "NS", "defense": "DS", "overhead": "OS"}.get(family)
    result = {"module": module, "technique": None,
              "swing_policy": "compact_control" if family in {"defense", "volley", "return"} else "full_chain",
              "classification_scope": "context_conditioned_not_video_verified", "reasons": []}
    if family == "serve":
        # Spin class and first/second serve are separate observations.
        if context.get("second_serve_confirmed") is True:
            result["technique"] = "SS04"
        elif context.get("serve_spin_type_verified") in {"flat", "slice", "kick"}:
            result["technique"] = {"flat": "SS01", "slice": "SS02", "kick": "SS03"}[context["serve_spin_type_verified"]]
        result["shared_modules"] = ["SS05"]
    elif family == "volley":
        delay = context.get("confirmed_bounce_to_contact_ms")
        if isinstance(delay, (float, int)) and not isinstance(delay, bool) and 0 < delay <= 180:
            result["technique"] = "NS03"
        elif context.get("no_precontact_bounce_confirmed") is True:
            height = context.get("contact_height_zone")
            if height == "below_waist":
                result["technique"] = "NS04"
            elif height == "chest_shoulder" and context.get("compact_backswing") is True:
                result["technique"] = "NS05"
            elif context.get("stroke_side") in {"forehand", "backhand"}:
                result["technique"] = "NS01" if context["stroke_side"] == "forehand" else "NS02"
    elif family == "overhead" and context.get("overhead_chain_observed") is True:
        for key, code in (("emergency_balance", "OS04"), ("airborne_contact_confirmed", "OS03"), ("backward_approach", "OS02")):
            if context.get(key) is True:
                result["technique"] = code
                break
        if result["technique"] is None and context.get("stable_support_observed") is True:
            result["technique"] = "OS01"
    elif family == "defense":
        for key, code in (("body_jam_observed", "DS06"), ("low_slice_observed", "DS05"),
                          ("defensive_lob_observed", "DS04"), ("running_rescue_observed", "DS03")):
            if context.get(key) is True:
                result["technique"] = code
                break
        if result["technique"] is None and context.get("stroke_side") in {"forehand", "backhand"}:
            result["technique"] = "DS01" if context["stroke_side"] == "forehand" else "DS02"
    elif family in {"baseline", "return"}:
        prefix = "GS" if family == "baseline" else "RS"
        side = context.get("stroke_side")
        if context.get("slice_observed") is True and side in {"forehand", "backhand"}:
            result["technique"] = prefix + ("05" if side == "forehand" else "04")
        elif side == "forehand":
            result["technique"] = prefix + "01"
        elif side == "backhand" and context.get("backhand_style") in {"two_handed", "one_handed"}:
            result["technique"] = prefix + ("02" if context["backhand_style"] == "two_handed" else "03")
    if result["technique"] is None:
        result["reasons"].append("subtype_evidence_missing_preserve_family_score")
    return result


def score_compact_control(*, family: str, preparation_path_body: float | None,
                          striking_path_body: float | None, follow_through_path_body: float | None,
                          terminal_speed_ratio: float | None = None) -> dict:
    """Numerical short-swing rubric for observed return/net/defensive actions.

    Paths must come from the same wrist, body scale and source-bound phase
    sequence. Larger backswing/follow-through is penalized here, reversing a
    full-swing amplitude reward. A still pose cannot earn a compactness score.
    """
    result = {"version": "compact-control-rubric-v1.0.0", "family": family,
              "score": None, "metrics": [], "status": "insufficient_motion_evidence",
              "semantics": "engineering_action_quality_not_coach_calibrated_grade"}
    if family not in {"return", "volley", "defense"}:
        return {**result, "status": "not_applicable"}
    finite = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v >= 0
    if not finite(striking_path_body) or striking_path_body <= .02:
        return result
    metrics = []
    for name, path, start, end in (("compact_preparation", preparation_path_body, .65, 1.8),
                                    ("controlled_short_follow_through", follow_through_path_body, .85, 2.0)):
        if finite(path):
            ratio = path / striking_path_body
            score = max(0., min(100., 100 * (end - ratio) / (end - start)))
            metrics.append({"name": name, "value": ratio, "unit": "path_ratio_to_striking",
                            "score": score, "curve": {"full_score_at_or_below": start, "zero_score_at_or_above": end}})
    if finite(terminal_speed_ratio):
        score = max(0., min(100., 100 * (1.0 - terminal_speed_ratio) / .8))
        metrics.append({"name": "terminal_motion_control", "value": terminal_speed_ratio,
                        "unit": "terminal_to_peak_speed_ratio", "score": score,
                        "curve": {"full_score_at_or_below": .2, "zero_score_at_or_above": 1.0}})
    if metrics:
        result.update(score=sum(item["score"] for item in metrics) / len(metrics), metrics=metrics,
                      status="scored", aggregation="equal_mean_of_observed_compact_control_metrics")
    return result
