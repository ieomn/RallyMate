"""Connect measured motion rubrics to the source document's level weights."""
from __future__ import annotations

from collections import defaultdict
from statistics import mean

from rallymate_scoring.action_aggregation import episode_dimension_scores
from rallymate_scoring.level_grading import grade_level

TECHNIQUES = {
    "baseline_forehand": "GS01", "baseline_two_hand_backhand": "GS02", "baseline_one_hand_backhand": "GS03",
    "backhand_slice": "GS04", "forehand_slice": "GS05", "return_forehand": "RS01",
    "return_two_hand_backhand": "RS02", "return_one_hand_backhand": "RS03", "return_backhand_slice": "RS04",
    "return_forehand_slice": "RS05",
}
GATE_THRESHOLD = 60.0


def build_level_assessments(report, *, backhand_style=None, technique_weights=None):
    """Keep subtype identity separate from a score for a shared movement family.

    A serve with no measured spin class contributes an SS family score; it is
    never fabricated into a flat serve or four copies of the same observation.
    The scoring assessor's five dimensions determine each episode's score.
    """
    technique_values, family_values, gate_values = defaultdict(list), defaultdict(list), defaultdict(list)
    counts = defaultdict(int)
    dimension_keys = {d["id"]: [m["id"] for m in d["metrics"]] for d in report.get("dimensions", [])}
    for episode in report.get("episodes", []):
        metrics = episode.get("metrics", {})
        def score_of(keys):
            return episode_dimension_scores(metrics, {"gate": keys})["gate"]
        dimensions = episode_dimension_scores(metrics, dimension_keys)
        observed = [v for v in dimensions.values() if v is not None]
        if not observed:
            continue
        score = mean(observed)
        domain = {"baseline": "GS", "serve": "SS", "return": "RS"}.get(episode.get("family"))
        profile = episode.get("motion_pattern", {}).get("profile_id")
        code = TECHNIQUES.get(profile)
        if code:
            technique_values[code].append(score)
            counts[code] += 1
        elif domain:
            # Family fallback describes genuinely unresolved subtypes. A known
            # subtype stays in technique_values, so a mutually exclusive route
            # or a zero weight at this level cannot re-enter as a family score.
            family_values[domain].append(score)
        # This is observed support during a stroke, not invented FS01..FS10 events.
        if dimensions.get("movement") is not None:
            family_values["FS"].append(dimensions["movement"])
        for name, keys in (
            ("non_dominant_hand_ok", ("pattern_two_hand_coupling", "serve_arm_coordination")),
            ("stroke_continuity_ok", ("pattern_phase_continuity", "follow_path")),
            ("body_coordination_ok", ("pattern_projected_sequence", "release_sequence", "serve_extension_sequence")),
        ):
            value = score_of(keys)
            if value is not None:
                gate_values[name].append(value)
        if episode.get("family") == "serve":
            value = score_of(("serve_extension_sequence", "pattern_serve_arm_sequence", "pattern_phase_continuity"))
            if value is not None:
                gate_values["serve_fluency_ok"].append(value)
    inferred_style = "one_handed" if counts["GS03"] + counts["RS03"] > counts["GS02"] + counts["RS02"] else "two_handed"
    style = backhand_style or inferred_style
    gates = {name: mean(values) >= GATE_THRESHOLD for name, values in gate_values.items() if values}
    techniques = {code: mean(values) for code, values in technique_values.items()}
    families = {domain: mean(values) for domain, values in family_values.items()}
    results = [grade_level(techniques, target_level=level, backhand_style=style, gates=gates,
                           technique_weights=technique_weights, domain_scores=families) for level in range(1, 6)]
    for result in results:
        result["source_video_sha256"] = report["source_video_sha256"]
        result["source_run_id"] = report["source_run_id"]
        result["subject_id"] = report.get("subject_scope", {}).get("selected_subject_id")
        result["backhand_style_source"] = "user_selected" if backhand_style else "most_observed_backhand_or_default_two_handed"
        result["gate_measurements"] = {name: {"mean_score": round(mean(values), 4), "observation_count": len(values),
                                             "threshold": GATE_THRESHOLD, "threshold_source": "engineering_config_v1"}
                                        for name, values in gate_values.items() if values}
        result["unmeasured_gate_requirements"] = ["physical_contact_position_and_timing", "ball_speed_spin_landing_consistency"]
        result["family_score_scope"] = {"FS": "observed_stroke_support_not_all_footwork_techniques",
                                        "SS": "observed_serve_motion_without_spin_subtype"}
    return results
