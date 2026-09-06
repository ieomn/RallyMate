from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from rallymate_features.event_features import FEATURE_DEFINITIONS
from rallymate_scoring.blocker_taxonomy import (
    LEAD_FOOT_SIDE_AMBIGUOUS_FLAG,
    PHASE_LOW_SAMPLE_PEAK_PREFIX,
    PHASE_RIGHT_CENSORED_PEAK_PREFIX,
    REQUIRED_PHASE_MISSING_PREFIX,
)
from rallymate_scoring.indicator_requirements import required_phase_keys


QUALITY_POLICY_VERSION = "indicator-event-quality-v1.6.0"
IRRELEVANT_JUMP_FLAG = "keypoint_jump_candidates_outside_indicator_joints"
IRRELEVANT_SWAP_FLAG = "left_right_swap_candidates_outside_indicator_joints"

# These flags describe maturity or truth availability.  They must remain
# visible in the result, but they do not invalidate an F2 measurement.
NON_BLOCKING_FLAGS = frozenset(
    {
        "event_ground_truth_missing",
        "provisional_rule_baseline",
    }
)

# These are emitted by the current event/primary-player diagnostics and mean
# that the event interval does not have enough trusted temporal evidence.
LOW_COVERAGE_HARD_FAIL_FLAGS = frozenset(
    {
        "primary_track_coverage_low",
    }
)

# Event-wide primary Pose coverage is broader than any one indicator's
# required-joint contract.  When every required feature function still has
# enough directly observed evidence, retain that F2 measurement for error
# analysis; formal scoring remains fail-closed until the sparse event has
# independent validation.  Actual invalid features are rejected separately by
# the scoring loop, so this does not turn missing values into measurements.
PRIMARY_POSE_SCORING_BLOCK_FLAGS = frozenset(
    {"primary_pose_coverage_low"}
)

# This diagnostic describes confidence in the automatically proposed event
# interval, not whether an indicator's required feature functions produced
# valid measurements inside that interval.  Preserve valid F2 feature values
# for error analysis, but fail closed before any production A--E output until
# the boundary evidence has been independently evaluated.
EVENT_BOUNDARY_SCORING_BLOCK_FLAGS = frozenset(
    {"pose_kinematic_coverage_low"}
)

# Candidate diagnostics are deliberately not promoted to confirmed failures.
ADVISORY_FLAGS = frozenset(
    {
        "keypoint_jump_candidates_present",
        "left_right_swap_candidates_present",
        "source_track_switch_candidates_present",
        "primary_identity_ambiguous",
        "low_motion_prominence",
        "tactical_target_direction_not_observed",
        LEAD_FOOT_SIDE_AMBIGUOUS_FLAG,
        IRRELEVANT_JUMP_FLAG,
        IRRELEVANT_SWAP_FLAG,
    }
)

# A source-track transition is only a candidate until identity truth or a
# validated stitching layer confirms continuity.  F2 feature measurement may
# remain available, but a production A--E grade must fail closed.
IDENTITY_SCORING_BLOCK_FLAGS = frozenset(
    {
        "source_track_switch_candidates_present",
        "primary_identity_ambiguous",
    }
)

LEAD_FOOT_SIDE_SCORING_BLOCK_INDICATORS = frozenset(
    {"FS02-M03", "FS02-M04", "FS02-M05"}
)

INDICATOR_SCORING_BLOCK_FLAGS = {
    # The current Pose event layer observes image-plane travel only.  It has no
    # tactical target vector at runtime, so FS02-M02 may retain F2 feature
    # measurements but cannot emit an A--E grade until that context exists.
    "FS02-M02": frozenset({"tactical_target_direction_not_observed"}),
}

# Phase fallbacks preserve a directly observed timestamp so F2 feature
# measurement remains auditable, but they do not prove the named semantic
# phase.  Block only indicators whose registry contract actually requires that
# phase; sibling indicators in the same event retain their independent gate.
PHASE_FALLBACK_SCORING_BLOCK_INDICATORS = {
    "landing_proxy_ms": frozenset({"FS01-M04"}),
    "first_step_slowdown_proxy_ms": frozenset({"FS02-M05"}),
}
POSE_DIAGNOSTIC_SCORING_BLOCK_FLAGS_BY_INDICATOR = {
    # Side/limb-specific or derivative-heavy measurements are especially
    # vulnerable to left/right swaps and frame-to-frame keypoint jumps.  Until
    # those diagnostics have independent precision/recall evidence, retain the
    # values for F2 audit but fail closed for formal A--E output.
    "left_right_swap_candidates_present": frozenset(
        {
            "FS01-M02",
            "FS01-M03",
            "FS01-M04",
            "FS02-M03",
            "FS02-M04",
            "FS02-M05",
            "FS09-M02",
            "FS09-M03",
        }
    ),
    "keypoint_jump_candidates_present": frozenset(
        {
            "FS01-M02",
            "FS01-M03",
            "FS01-M04",
            "FS01-M05",
            "FS02-M02",
            "FS02-M03",
            "FS02-M04",
            "FS02-M05",
            "FS09-M01",
            "FS09-M02",
            "FS09-M03",
            "FS09-M04",
            "FS09-M05",
        }
    ),
}

RESTABILIZATION_REQUIRED_INDICATORS = frozenset({"FS09-M04", "FS09-M05"})
_CONFIRMED_ID_SWITCH_FLAGS = frozenset(
    {
        "confirmed_id_switch",
        "confirmed_id_switch_detected",
        "confirmed_id_switch_present",
        "primary_player_id_switch_confirmed",
    }
)


def _indicator_required_joints(indicator: Mapping[str, Any]) -> set[str] | None:
    """Resolve the authoritative feature-joint dependency set.

    ``None`` means the indicator contract is incomplete or references an
    unknown feature.  Callers must then retain the broad diagnostic gate so a
    legacy or malformed record cannot obtain a grade by omitting dependency
    metadata.
    """

    feature_names = indicator.get("required_features")
    if not isinstance(feature_names, list) or not feature_names:
        return None
    joints: set[str] = set()
    for feature_name in feature_names:
        if not isinstance(feature_name, str) or not feature_name:
            return None
        definition = FEATURE_DEFINITIONS.get(feature_name)
        if not isinstance(definition, Mapping):
            return None
        required = definition.get("required_joints")
        if not isinstance(required, list) or any(
            not isinstance(joint, str) or not joint for joint in required
        ):
            return None
        joints.update(required)
    return joints or None


def _joint_scoped_pose_diagnostic_flags(
    flags: set[str],
    event: Mapping[str, Any],
    indicator: Mapping[str, Any],
) -> set[str]:
    """Demote only diagnostics proven unrelated to this indicator's joints.

    Older events lack joint-level provenance.  Malformed, empty or incomplete
    provenance deliberately keeps the original broad flag, preserving the
    fail-closed behavior used before quality-policy v1.3.0.
    """

    required_joints = _indicator_required_joints(indicator)
    diagnostics = event.get("track_diagnostics")
    if required_joints is None or not isinstance(diagnostics, Mapping):
        return flags

    if "keypoint_jump_candidates_present" in flags:
        candidate_joints = diagnostics.get("keypoint_jump_candidate_joints")
        if (
            isinstance(candidate_joints, list)
            and candidate_joints
            and all(isinstance(joint, str) and joint for joint in candidate_joints)
            and required_joints.isdisjoint(candidate_joints)
        ):
            flags.remove("keypoint_jump_candidates_present")
            flags.add(IRRELEVANT_JUMP_FLAG)

    if "left_right_swap_candidates_present" in flags:
        candidate_pairs = diagnostics.get("left_right_swap_candidate_joint_pairs")
        if isinstance(candidate_pairs, list) and candidate_pairs:
            candidate_swap_joints: set[str] = set()
            valid_pairs = True
            for pair in candidate_pairs:
                if not isinstance(pair, Mapping):
                    valid_pairs = False
                    break
                left = pair.get("left_joint")
                right = pair.get("right_joint")
                if not isinstance(left, str) or not left or not isinstance(
                    right, str
                ) or not right:
                    valid_pairs = False
                    break
                candidate_swap_joints.update((left, right))
            if valid_pairs and required_joints.isdisjoint(candidate_swap_joints):
                flags.remove("left_right_swap_candidates_present")
                flags.add(IRRELEVANT_SWAP_FLAG)
    return flags


def indicator_event_quality_flags(
    event: Mapping[str, Any], indicator: Mapping[str, Any]
) -> list[str]:
    """Add exact metric-phase evidence gates to event diagnostics.

    Candidate and accepted-manual events share this function so a missing
    required phase cannot be measured in one path and silently accepted in the
    other.  The registry remains the authority for which phases each metric
    needs; the event-wide diagnostic list remains unchanged for provenance.
    """

    supplied = event.get("quality_flags", [])
    if not isinstance(supplied, list) or any(
        not isinstance(flag, str) or not flag for flag in supplied
    ):
        raise ValueError("event.quality_flags must be a list of non-empty strings")
    flags = _joint_scoped_pose_diagnostic_flags(set(supplied), event, indicator)
    phases = event.get("key_phases_ms")
    if not isinstance(phases, Mapping):
        raise ValueError("event.key_phases_ms must be an object")
    for phase_key in required_phase_keys(indicator):
        if phases.get(phase_key) is None:
            flags.add(f"{REQUIRED_PHASE_MISSING_PREFIX}{phase_key}")
    return sorted(flags)


def _is_confirmed_id_switch(flag: str) -> bool:
    normalized = flag.lower()
    if normalized in _CONFIRMED_ID_SWITCH_FLAGS:
        return True
    return (
        "id_switch" in normalized
        and "confirm" in normalized
        and "candidate" not in normalized
        and "unconfirmed" not in normalized
    )


def evaluate_indicator_event_quality(
    indicator_id: str,
    quality_flags: Iterable[str] | None,
) -> dict[str, Any]:
    """Classify existing event flags for one indicator.

    This policy never infers a flag from coverage fractions or candidates.  It
    consumes only the supplied ``quality_flags`` and preserves every input flag
    in one of the output classifications for auditability.
    """

    if not isinstance(indicator_id, str) or not indicator_id:
        raise ValueError("indicator_id must be a non-empty string")
    supplied = [] if quality_flags is None else list(quality_flags)
    if any(not isinstance(flag, str) or not flag for flag in supplied):
        raise ValueError("quality_flags must contain non-empty strings")
    flags = sorted(set(supplied))
    hard_fail_flags: list[str] = []
    scoring_block_flags: list[str] = []
    advisory_flags: list[str] = []
    non_blocking_flags: list[str] = []
    unclassified_flags: list[str] = []

    for flag in flags:
        if flag.startswith(REQUIRED_PHASE_MISSING_PREFIX):
            phase_key = flag.removeprefix(REQUIRED_PHASE_MISSING_PREFIX)
            if not phase_key or not phase_key.endswith("_ms"):
                raise ValueError(
                    "required_phase_missing flags must name a non-empty *_ms phase"
                )
            hard_fail_flags.append(flag)
        elif flag.startswith(
            (PHASE_RIGHT_CENSORED_PEAK_PREFIX, PHASE_LOW_SAMPLE_PEAK_PREFIX)
        ):
            prefix = (
                PHASE_RIGHT_CENSORED_PEAK_PREFIX
                if flag.startswith(PHASE_RIGHT_CENSORED_PEAK_PREFIX)
                else PHASE_LOW_SAMPLE_PEAK_PREFIX
            )
            phase_key = flag.removeprefix(prefix)
            if not phase_key or not phase_key.endswith("_ms"):
                raise ValueError(
                    "phase proxy fallback flags must name a non-empty *_ms phase"
                )
            advisory_flags.append(flag)
            if indicator_id in PHASE_FALLBACK_SCORING_BLOCK_INDICATORS.get(
                phase_key, frozenset()
            ):
                scoring_block_flags.append(flag)
        elif flag in EVENT_BOUNDARY_SCORING_BLOCK_FLAGS:
            advisory_flags.append(flag)
            scoring_block_flags.append(flag)
        elif flag in PRIMARY_POSE_SCORING_BLOCK_FLAGS:
            advisory_flags.append(flag)
            scoring_block_flags.append(flag)
        elif flag in LOW_COVERAGE_HARD_FAIL_FLAGS or _is_confirmed_id_switch(flag):
            hard_fail_flags.append(flag)
        elif flag == "restabilization_not_observed":
            if indicator_id in RESTABILIZATION_REQUIRED_INDICATORS:
                hard_fail_flags.append(flag)
            else:
                advisory_flags.append(flag)
        elif flag in NON_BLOCKING_FLAGS:
            non_blocking_flags.append(flag)
        elif flag in ADVISORY_FLAGS:
            advisory_flags.append(flag)
            if flag in IDENTITY_SCORING_BLOCK_FLAGS or flag in (
                INDICATOR_SCORING_BLOCK_FLAGS.get(indicator_id, frozenset())
            ) or (
                flag == LEAD_FOOT_SIDE_AMBIGUOUS_FLAG
                and indicator_id in LEAD_FOOT_SIDE_SCORING_BLOCK_INDICATORS
            ) or indicator_id in POSE_DIAGNOSTIC_SCORING_BLOCK_FLAGS_BY_INDICATOR.get(
                flag, frozenset()
            ):
                scoring_block_flags.append(flag)
        else:
            # Unknown diagnostics are retained and treated conservatively as
            # advisory, never as an undocumented hard failure.
            advisory_flags.append(flag)
            unclassified_flags.append(flag)

    hard_fail = bool(hard_fail_flags)
    scoring_allowed = not hard_fail and not scoring_block_flags
    status = (
        "hard_fail"
        if hard_fail
        else "advisory"
        if advisory_flags
        else "pass"
    )
    return {
        "schema_version": "1.0.0",
        "policy_version": QUALITY_POLICY_VERSION,
        "indicator_id": indicator_id,
        "status": status,
        "hard_fail": hard_fail,
        "measurement_allowed": not hard_fail,
        "scoring_allowed": scoring_allowed,
        "input_quality_flags": flags,
        "hard_fail_flags": hard_fail_flags,
        "scoring_block_flags": scoring_block_flags,
        "advisory_flags": advisory_flags,
        "non_blocking_flags": non_blocking_flags,
        "unclassified_flags": unclassified_flags,
        "semantics": "quality_evidence_gate_only_no_A_to_E_thresholds",
    }
