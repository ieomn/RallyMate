"""Public source-review adapter; existing registry/scoring paths are unchanged."""
from rallymate_features.source_alignment import (
    SourceAlignedContext,
    clear_source_aligned_feature_cache,
    compute_source_aligned_features,
    prepare_source_aligned_context,
)

__all__ = ["SourceAlignedContext", "clear_source_aligned_feature_cache",
           "compute_source_aligned_features", "prepare_source_aligned_context"]
