"""Model-granularity audit for the GS/FS scoring specification."""

from .granularity import analyze_scoring_readiness, static_model_capability

__all__ = ["analyze_scoring_readiness", "static_model_capability"]
_DIAGNOSTIC_POLICY_EXPORTS = (
    "review_pose_diagnostic_quality_gate",
    "validate_diagnostic_gate_acceptance_protocol",
    "validate_diagnostic_policy_review",
)
__all__ += list(_DIAGNOSTIC_POLICY_EXPORTS)
from rallymate_scoring.calculation_readiness import (
    build_indicator_calculation_readiness,
    validate_indicator_calculation_readiness,
)

__all__ += [
    "build_indicator_calculation_readiness",
    "validate_indicator_calculation_readiness",
]
from rallymate_scoring.measurement_portfolio import (
    build_indicator_measurement_portfolio,
    validate_indicator_measurement_portfolio,
)

__all__ += [
    "build_indicator_measurement_portfolio",
    "validate_indicator_measurement_portfolio",
]
from rallymate_scoring.cycle_measurement import (
    build_scoring_cycle_measurement,
    validate_scoring_cycle_measurement,
)

__all__ += [
    "build_scoring_cycle_measurement",
    "validate_scoring_cycle_measurement",
]
from rallymate_scoring.scoring_context import (
    build_scoring_reference_worklist,
    validate_scoring_reference_context,
)

__all__ += [
    "build_scoring_reference_worklist",
    "validate_scoring_reference_context",
]
from rallymate_scoring.indicator_feature_qualification import (
    DIAGNOSTIC_SOURCE_STATUS,
    INDICATOR_FEATURE_SOURCE_METADATA_VERSION,
    QUALIFICATION_SNAPSHOT_VERSION,
    SYNTHETIC_SOURCE_STATUS,
    VERIFIED_SOURCE_STATUS,
    VerifiedIndicatorFeatureSourceMetadata,
    verify_indicator_feature_source_metadata,
)

__all__ += [
    "DIAGNOSTIC_SOURCE_STATUS",
    "INDICATOR_FEATURE_SOURCE_METADATA_VERSION",
    "QUALIFICATION_SNAPSHOT_VERSION",
    "SYNTHETIC_SOURCE_STATUS",
    "VERIFIED_SOURCE_STATUS",
    "VerifiedIndicatorFeatureSourceMetadata",
    "verify_indicator_feature_source_metadata",
]


def __getattr__(name: str):
    """Lazily expose offline diagnostic policy review helpers without pulling research packages into runtime imports."""
    if name in _DIAGNOSTIC_POLICY_EXPORTS:
        from rallymate_scoring import diagnostic_policy_review

        return getattr(diagnostic_policy_review, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
