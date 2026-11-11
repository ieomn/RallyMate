"""Shared, measurement-only aggregation for action reports and level adapters.

Each observed episode has equal weight within a dimension, regardless of how
many metrics it exposes. Missing measurements do not become zero, and a valid
zero is retained. Model confidence and classification labels are not inputs.
"""
from __future__ import annotations

from collections.abc import Mapping
import math


def measurement_pair(metric):
    """Read a public measurement or the scorer's unrounded (value, score) pair."""
    if isinstance(metric, Mapping):
        pair = (metric.get("value"), metric.get("score"))
    elif isinstance(metric, (list, tuple)) and len(metric) == 2:
        pair = tuple(metric)
    else:
        return None
    if not all(type(value) in (int, float) and math.isfinite(value) for value in pair):
        return None
    return pair if 0 <= pair[1] <= 100 else None


def episode_dimension_scores(metrics, dimension_keys):
    """Return dimension means by id, using only finite, bounded measurements."""
    result = {}
    seen = set()
    for dimension, keys in dimension_keys.items():
        values = []
        for key in keys:
            if key in seen:
                raise ValueError(f"Metric belongs to multiple dimensions: {key}")
            seen.add(key)
            pair = measurement_pair(metrics.get(key))
            if pair is not None:
                values.append(pair[1])
        result[dimension] = math.fsum(values) / len(values) if values else None
    return result


def aggregate_action_dimensions(episodes, definitions):
    """Project episode means into explainable metric weights and five axes.

    Definitions are (dimension id, label, metric rows) tuples. A metric row is
    (id, label, unit, target), matching the existing versioned action rubrics.
    Public rounding happens after weighted aggregation, as in the action report.
    """
    definitions = list(definitions)
    keys = {identifier: [row[0] for row in rows] for identifier, _, rows in definitions}
    if len(keys) != len(definitions):
        raise ValueError("Dimension identifiers must be unique")
    # Validate membership even when there are no episodes.
    episode_dimension_scores({}, keys)
    known = {key for group in keys.values() for key in group}
    for episode in episodes:
        if set(episode["metrics"]) - known:
            raise ValueError("Episode contains a metric without a dimension")
    result = []
    for identifier, label, rows in definitions:
        available = [{key: pair for key in keys[identifier]
                      if (pair := measurement_pair(episode["metrics"].get(key))) is not None}
                     for episode in episodes]
        count = sum(bool(values) for values in available)
        metrics = []
        for key, title, unit, target in rows:
            observations = [(values[key], 1 / len(values)) for values in available if key in values]
            mass = math.fsum(weight for _, weight in observations)
            metrics.append({"id": key, "label": title, "unit": unit, "target": target,
                            "weight": round(mass / count, 8) if observations else 1,
                            "value": round(math.fsum(pair[0] * weight for pair, weight in observations) / mass, 5) if observations else None,
                            "score": round(math.fsum(pair[1] * weight for pair, weight in observations) / mass, 2) if observations else None,
                            "observation_count": len(observations)})
        measured = [metric for metric in metrics if metric["score"] is not None]
        score = (math.fsum(metric["score"] * metric["weight"] for metric in measured)
                 / math.fsum(metric["weight"] for metric in measured)) if measured else None
        result.append({"id": identifier, "label": label, "score": round(score, 2) if score is not None else None,
                       "observation_count": count, "metrics": metrics})
    return result
