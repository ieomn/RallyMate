"""Coach-free reference-population rubric calibration from tiered SAM 3D cohorts.

This module derives auditable biomechanics rubric knots from tiered reference
video cohorts (``pro``, ``advanced``, ``intermediate``, ``beginner``) combined
with public Word indicator mappings. Because no human tennis coaches label
these cohorts, every exported policy keeps ``coach_calibrated: False`` and
``calibration_origin: "reference_population_quantiles_v1"``.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any

import numpy as np


CALIBRATION_SCHEMA_VERSION = "tennis-reference-calibration-v1"
COHORT_MANIFEST_SCHEMA_VERSION = "tennis-reference-cohort-v1"
RUBRIC_POLICY_SCHEMA_VERSION = "tennis-rubric-policy-v1"
TARGET_RUBRIC_VERSION = "tennis-action-rubric-v1.4.0"
CALIBRATION_ORIGIN = "reference_population_quantiles_v1"

TIERS = ("beginner", "intermediate", "advanced", "pro")
TIER_RANK = {tier: index for index, tier in enumerate(TIERS)}
UPPER_TIERS = ("pro", "advanced")
LOWER_TIERS = ("intermediate", "beginner")
CALIBRATABLE_METRICS = ("knee_load", "support_control", "coil", "arm_space")

_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_INTERNAL_DOC_RE = re.compile(r"(?:\bVIS-[A-Z]{2}\b|\bP\d{4}\b|internal_sources)")
MAX_MANIFEST_BYTES = 32 * 1024 * 1024

WORD_INDICATOR_LINKS: dict[str, dict[str, Any]] = {
    "knee_load": {
        "dimension": "movement",
        "unit": "deg",
        "indicator_ids": ["FS01-M02", "GS01-M01-03"],
        "indicator_titles_zh": ["重心预加载", "降低重心"],
        "phase": "preparation",
    },
    "support_control": {
        "dimension": "movement",
        "unit": "hip_width",
        "indicator_ids": ["FS01-M04", "GS01-M04-03"],
        "indicator_titles_zh": ["双脚分开落地", "稳定支撑"],
        "phase": "preparation",
    },
    "coil": {
        "dimension": "preparation",
        "unit": "deg",
        "indicator_ids": ["GS01-M03-01", "GS01-M05-04"],
        "indicator_titles_zh": ["完成单位转体", "建立肩髋分离"],
        "phase": "last_half_preparation",
    },
    "arm_space": {
        "dimension": "striking",
        "unit": "arm_length",
        "indicator_ids": ["GS01-M08-01", "GS01-M07-01"],
        "indicator_titles_zh": ["保持稳定击球姿态", "持拍手主动加速"],
        "phase": "acceleration_onset_to_peak_plus_80ms",
    },
}

METRIC_SPECS: dict[str, dict[str, Any]] = {
    "knee_load": {
        "curve": "time_mean_increasing_saturating_ramp",
        "knot_count": 2,
        "low_bound": 0.0,
        "high_bound": 140.0,
        "obs_min": 0.0,
        "obs_max": 180.0,
        "min_edge_span": 1.0,
        "decimals": 3,
        "rule_metadata": {
            "measurement": "full_preparation_per_leg_time_mean_then_best_observed_leg",
            "semantics": "preparation_participation_not_unique_optimal_knee_depth",
        },
    },
    "support_control": {
        "curve": "time_mean_trapezoid",
        "knot_count": 4,
        "low_bound": 0.1,
        "high_bound": 6.0,
        "obs_min": 0.01,
        "obs_max": 10.0,
        "min_edge_span": 0.05,
        "decimals": 4,
        "rule_metadata": {
            "geometry": "three_dimensional_ankle_distance_divided_by_hip_width",
            "semantics": "bounded_ready_stance_width_not_balance_or_ground_reaction",
            "families": ["baseline", "return"],
        },
    },
    "coil": {
        "curve": "time_mean_trapezoid",
        "knot_count": 4,
        "low_bound": 0.0,
        "high_bound": 90.0,
        "obs_min": 0.0,
        "obs_max": 90.0,
        "min_edge_span": 1.0,
        "decimals": 3,
        "rule_metadata": {
            "phase": "last_half_preparation",
        },
    },
    "arm_space": {
        "curve": "time_mean_trapezoid",
        "knot_count": 4,
        "low_bound": 0.05,
        "high_bound": 1.0,
        "max_good_high": 0.97,
        "obs_min": 0.01,
        "obs_max": 1.0,
        "min_edge_span": 0.01,
        "decimals": 4,
        "rule_metadata": {
            "phase": "acceleration_onset_to_peak_plus_80ms",
        },
    },
}


class ReferenceCalibrationError(ValueError):
    """Raised when cohort provenance, bounds, or calibration validation fail."""


class InsufficientCohortError(ReferenceCalibrationError):
    """Raised fail-closed when sample or video counts are below guardrails."""


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize deterministically with strict rejection of NaN/Infinity."""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    """Compute SHA-256 hex digest of a canonical JSON payload."""
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _reject_nonfinite(token: str) -> None:
    raise ReferenceCalibrationError(f"non-finite JSON constant rejected: {token}")


def _finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ReferenceCalibrationError(f"{field} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise ReferenceCalibrationError(f"{field} must be finite")
    return result


def _require_nonempty_str(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReferenceCalibrationError(f"{field} must be a non-empty string")
    if _INTERNAL_DOC_RE.search(value):
        raise ReferenceCalibrationError(f"{field} contains forbidden internal document locator")
    return value.strip()


def _require_sha256(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise ReferenceCalibrationError(f"{field} must be a 64-character hex SHA-256")
    return value.lower()


def _load_json_file(path: Path, *, max_bytes: int = MAX_MANIFEST_BYTES) -> dict[str, Any]:
    if not path.is_file():
        raise ReferenceCalibrationError(f"file not found: {path}")
    if path.stat().st_size > max_bytes:
        raise ReferenceCalibrationError(f"file exceeds size limit: {path}")
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"), parse_constant=_reject_nonfinite)
    except json.JSONDecodeError as exc:
        raise ReferenceCalibrationError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(loaded, dict):
        raise ReferenceCalibrationError(f"JSON root in {path} must be an object")
    return loaded


def _score_value(value: float, curve: str, knots: Sequence[float]) -> float:
    if curve == "time_mean_increasing_saturating_ramp":
        low, high = knots
        return 100.0 * float(np.clip((value - low) / (high - low), 0.0, 1.0))
    if curve == "time_mean_trapezoid":
        low, good_low, good_high, high = knots
        up = 100.0 * float(np.clip((value - low) / (good_low - low), 0.0, 1.0))
        down = 100.0 - 100.0 * float(np.clip((value - good_high) / (high - good_high), 0.0, 1.0))
        return min(up, down)
    raise ReferenceCalibrationError(f"unsupported curve: {curve}")


def summarize_distribution(
    values: Sequence[float], *, n_videos: int | None = None
) -> dict[str, Any] | None:
    """Compute robust quantile summary (n, p10, p25, p50, p75, p90, iqr)."""
    if not values:
        return None
    arr = np.asarray([_finite_number(v, "distribution_value") for v in values], dtype=np.float64)
    p10, p25, p50, p75, p90 = [float(x) for x in np.percentile(arr, [10, 25, 50, 75, 90])]
    summary: dict[str, Any] = {
        "n": int(arr.size),
        "p10": round(p10, 6),
        "p25": round(p25, 6),
        "p50": round(p50, 6),
        "p75": round(p75, 6),
        "p90": round(p90, 6),
        "iqr": round(p75 - p25, 6),
        "min": round(float(np.min(arr)), 6),
        "max": round(float(np.max(arr)), 6),
    }
    if n_videos is not None:
        summary["n_videos"] = int(n_videos)
    return summary


def _cliffs_effect_size_label(delta: float) -> str:
    magnitude = abs(delta)
    if magnitude < 0.147:
        return "negligible"
    if magnitude < 0.33:
        return "small"
    if magnitude < 0.474:
        return "medium"
    return "large"


def _exact_two_sided_mwu_pvalue(doubled_ranks: list[int], n1: int, n2: int, obs_dev: int) -> float:
    total_n = n1 + n2
    choose_k = min(n1, n2)
    max_sum = sum(sorted(doubled_ranks, reverse=True)[:choose_k])
    dp = np.zeros((choose_k + 1, max_sum + 1), dtype=np.int64)
    dp[0, 0] = 1
    running_max = 0
    for r in doubled_ranks:
        upper_k = min(choose_k, running_max + 1)
        for k in range(upper_k, 0, -1):
            dp[k, r:] += dp[k - 1, :-r]
        running_max = min(choose_k, running_max + 1)
    counts = dp[choose_k]
    total_ways = int(counts.sum())
    if total_ways <= 0:
        return 1.0
    mu_s = choose_k * (total_n + 1)
    sums = np.arange(max_sum + 1, dtype=np.int64)
    extreme = int(counts[np.abs(sums - mu_s) >= obs_dev].sum())
    return min(1.0, float(extreme / total_ways))


def mann_whitney_cliffs(upper: Sequence[float], lower: Sequence[float]) -> dict[str, Any]:
    """Compute exact Mann-Whitney U, Cliff's delta, and two-sided p-value."""
    if not upper or not lower:
        raise ReferenceCalibrationError("upper and lower samples must both be non-empty")
    x = np.asarray([_finite_number(v, "upper") for v in upper], dtype=np.float64)
    y = np.asarray([_finite_number(v, "lower") for v in lower], dtype=np.float64)
    n1, n2 = int(x.size), int(y.size)
    # Pairwise matrix (n1, n2) is fast and exact for cohort sizes without scipy.
    diff = x[:, None] - y[None, :]
    greater = int(np.sum(diff > 0.0))
    less = int(np.sum(diff < 0.0))
    ties = int(n1 * n2 - greater - less)
    u_upper = greater + 0.5 * ties
    u_lower = float(n1 * n2) - u_upper
    cliffs_delta = float((greater - less) / (n1 * n2))

    combined = sorted([(float(v), 0) for v in x] + [(float(v), 1) for v in y], key=lambda item: item[0])
    total_n = n1 + n2
    doubled_ranks: list[int] = [0] * total_n
    tie_counts: list[int] = []
    idx = 0
    while idx < total_n:
        end = idx + 1
        while end < total_n and combined[end][0] == combined[idx][0]:
            end += 1
        m = end - idx
        if m > 1:
            tie_counts.append(m)
        doubled_midrank = (idx + 1) + end
        for pos in range(idx, end):
            doubled_ranks[pos] = doubled_midrank
        idx = end

    if total_n <= 30:
        obs_dev = abs(2 * greater + ties - n1 * n2)
        p_value = _exact_two_sided_mwu_pvalue(doubled_ranks, n1, n2, obs_dev)
        method = "exact_midrank_dp"
    else:
        tie_term = sum(t * t * t - t for t in tie_counts)
        variance = (n1 * n2 / 12.0) * ((total_n + 1.0) - tie_term / (total_n * (total_n - 1.0)))
        if variance <= 1e-15:
            p_value = 1.0
        else:
            sigma_u = math.sqrt(variance)
            z = max(0.0, abs(u_upper - 0.5 * n1 * n2) - 0.5) / sigma_u
            p_value = math.erfc(z / math.sqrt(2.0))
        method = "normal_approx_tie_corrected"

    return {
        "n_upper": n1,
        "n_lower": n2,
        "mann_whitney_u": round(float(u_upper), 6),
        "mann_whitney_u_lower": round(float(u_lower), 6),
        "cliffs_delta": round(cliffs_delta, 6),
        "abs_cliffs_delta": round(abs(cliffs_delta), 6),
        "effect_size": _cliffs_effect_size_label(cliffs_delta),
        "p_value": round(float(p_value), 8),
        "method": method,
    }


def _extract_episodes_from_entry(entry: Mapping[str, Any], sha: str, base_dir: Path | None) -> list[Mapping[str, Any]]:
    if "episodes" in entry:
        episodes = entry["episodes"]
        if not isinstance(episodes, list):
            raise ReferenceCalibrationError("entry.episodes must be a list")
        return episodes
    assessment: Mapping[str, Any] | None = None
    if "assessment" in entry:
        raw_assessment = entry["assessment"]
        if not isinstance(raw_assessment, Mapping):
            raise ReferenceCalibrationError("entry.assessment must be an object")
        assessment = raw_assessment
    elif "assessment_path" in entry:
        rel_or_abs = Path(_require_nonempty_str(entry["assessment_path"], "entry.assessment_path"))
        target = rel_or_abs if rel_or_abs.is_absolute() or base_dir is None else (base_dir / rel_or_abs)
        assessment = _load_json_file(target)
    if assessment is None:
        raise ReferenceCalibrationError("manifest entry must define episodes, assessment, or assessment_path")
    if "source_video_sha256" in assessment:
        assessed_sha = _require_sha256(assessment["source_video_sha256"], "assessment.source_video_sha256")
        if assessed_sha != sha:
            raise ReferenceCalibrationError("assessment source_video_sha256 disagrees with manifest entry")
    episodes = assessment.get("episodes", [])
    if not isinstance(episodes, list):
        raise ReferenceCalibrationError("assessment.episodes must be a list")
    return episodes


def _validate_indicator_links(raw_links: Any) -> dict[str, dict[str, Any]]:
    links = {
        metric: {
            "dimension": info["dimension"],
            "unit": info["unit"],
            "indicator_ids": list(info["indicator_ids"]),
            "indicator_titles_zh": list(info["indicator_titles_zh"]),
            "phase": info["phase"],
        }
        for metric, info in WORD_INDICATOR_LINKS.items()
    }
    if raw_links is None:
        return links
    if not isinstance(raw_links, Mapping):
        raise ReferenceCalibrationError("word_indicator_links must be a mapping")
    for metric_id, override in raw_links.items():
        if metric_id not in METRIC_SPECS:
            raise ReferenceCalibrationError(f"unsupported metric in word_indicator_links: {metric_id}")
        if not isinstance(override, Mapping):
            raise ReferenceCalibrationError(f"word_indicator_links.{metric_id} must be a mapping")
        ids = override.get("indicator_ids")
        if ids is not None:
            if not isinstance(ids, list) or not ids or not all(isinstance(x, str) and x.strip() for x in ids):
                raise ReferenceCalibrationError(f"word_indicator_links.{metric_id}.indicator_ids invalid")
            for item in ids:
                _require_nonempty_str(item, f"word_indicator_links.{metric_id}.indicator_ids")
            links[metric_id]["indicator_ids"] = [x.strip() for x in ids]
        titles = override.get("indicator_titles_zh")
        if titles is not None:
            if not isinstance(titles, list) or not all(isinstance(x, str) and x.strip() for x in titles):
                raise ReferenceCalibrationError(f"word_indicator_links.{metric_id}.indicator_titles_zh invalid")
            for item in titles:
                _require_nonempty_str(item, f"word_indicator_links.{metric_id}.indicator_titles_zh")
            links[metric_id]["indicator_titles_zh"] = [x.strip() for x in titles]
    return links


def load_cohort_manifest(manifest_or_path: Mapping[str, Any] | str | Path) -> dict[str, Any]:
    """Load and provenance-validate a tiered SAM 3D reference cohort manifest."""
    base_dir: Path | None = None
    if isinstance(manifest_or_path, (str, Path)):
        path = Path(manifest_or_path)
        base_dir = path.parent
        manifest = _load_json_file(path)
    elif isinstance(manifest_or_path, Mapping):
        manifest = dict(manifest_or_path)
    else:
        raise ReferenceCalibrationError("manifest must be a mapping or file path")

    schema = manifest.get("schema_version")
    if schema is not None and schema != COHORT_MANIFEST_SCHEMA_VERSION:
        raise ReferenceCalibrationError(f"unsupported cohort schema_version: {schema}")

    raw_entries = manifest.get("entries", manifest.get("samples", manifest.get("videos")))
    if not isinstance(raw_entries, list) or not raw_entries:
        raise ReferenceCalibrationError("cohort manifest requires a non-empty entries/samples/videos list")

    indicator_links = _validate_indicator_links(manifest.get("word_indicator_links"))
    seen_ids: set[str] = set()
    seen_shas: set[str] = set()
    group_tiers: dict[str, str] = {}
    entries: list[dict[str, Any]] = []

    for idx, raw in enumerate(raw_entries):
        if not isinstance(raw, Mapping):
            raise ReferenceCalibrationError(f"entry[{idx}] must be an object")
        sample_id = _require_nonempty_str(raw.get("sample_id", raw.get("video_id")), f"entry[{idx}].sample_id")
        if sample_id in seen_ids:
            raise ReferenceCalibrationError(f"duplicate sample_id: {sample_id}")
        seen_ids.add(sample_id)

        tier = raw.get("tier")
        if tier not in TIER_RANK:
            raise ReferenceCalibrationError(f"entry[{idx}].tier must be one of {TIERS}, got {tier!r}")

        sha = _require_sha256(raw.get("source_video_sha256"), f"entry[{idx}].source_video_sha256")
        if sha in seen_shas:
            raise ReferenceCalibrationError(f"duplicate source_video_sha256 across cohort entries: {sha}")
        seen_shas.add(sha)

        pose_source = raw.get("pose_source", "sam3d")
        if pose_source != "sam3d":
            raise ReferenceCalibrationError(f"entry[{idx}].pose_source must be 'sam3d', got {pose_source!r}")

        group = raw.get("subject_group")
        if group is not None:
            group_name = _require_nonempty_str(group, f"entry[{idx}].subject_group")
            prev = group_tiers.setdefault(group_name, tier)
            if prev != tier:
                raise ReferenceCalibrationError(f"subject_group {group_name!r} spans conflicting tiers")

        episodes_raw = _extract_episodes_from_entry(raw, sha, base_dir)
        extracted_episodes: list[dict[str, Any]] = []
        seen_ep_ids: set[str] = set()
        for ep_idx, ep in enumerate(episodes_raw):
            if not isinstance(ep, Mapping):
                raise ReferenceCalibrationError(f"entry[{idx}].episodes[{ep_idx}] must be an object")
            ep_id = _require_nonempty_str(ep.get("episode_id", f"{sample_id}-ep-{ep_idx}"), f"episode_id")
            if ep_id in seen_ep_ids:
                raise ReferenceCalibrationError(f"duplicate episode_id {ep_id!r} in sample {sample_id!r}")
            seen_ep_ids.add(ep_id)
            raw_metrics = ep.get("metrics")
            if not isinstance(raw_metrics, Mapping):
                raise ReferenceCalibrationError(f"entry[{idx}].episodes[{ep_idx}].metrics must be a mapping")
            values: dict[str, float] = {}
            for metric_id, spec in METRIC_SPECS.items():
                if metric_id not in raw_metrics:
                    continue
                item = raw_metrics[metric_id]
                raw_val = item.get("value") if isinstance(item, Mapping) else item
                if raw_val is None:
                    continue
                val = _finite_number(raw_val, f"{sample_id}.{ep_id}.{metric_id}")
                if not (spec["obs_min"] <= val <= spec["obs_max"]):
                    raise ReferenceCalibrationError(
                        f"{sample_id}.{ep_id}.{metric_id}={val} outside physical range [{spec['obs_min']}, {spec['obs_max']}]"
                    )
                values[metric_id] = val
            extracted_episodes.append({"episode_id": ep_id, "metrics": values})

        entries.append(
            {
                "sample_id": sample_id,
                "tier": tier,
                "source_video_sha256": sha,
                "pose_source": pose_source,
                "episodes": extracted_episodes,
            }
        )

    manifest_digest = canonical_sha256(
        {
            "schema_version": COHORT_MANIFEST_SCHEMA_VERSION,
            "entries": [
                {
                    "sample_id": e["sample_id"],
                    "tier": e["tier"],
                    "source_video_sha256": e["source_video_sha256"],
                    "episodes": e["episodes"],
                }
                for e in sorted(entries, key=lambda row: row["source_video_sha256"])
            ],
        }
    )
    return {
        "schema_version": COHORT_MANIFEST_SCHEMA_VERSION,
        "manifest_sha256": manifest_digest,
        "indicator_links": indicator_links,
        "entries": entries,
    }


def _evaluate_loaded_cohort(
    cohort: Mapping[str, Any],
    *,
    metrics: Sequence[str] = CALIBRATABLE_METRICS,
) -> dict[str, Any]:
    metric_list = tuple(metrics)
    for metric_id in metric_list:
        if metric_id not in METRIC_SPECS:
            raise ReferenceCalibrationError(f"unsupported metric: {metric_id}")

    tier_videos: dict[str, set[str]] = defaultdict(set)
    tier_metric_videos: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    tier_values: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    tier_episode_counts: Counter[str] = Counter()

    for entry in cohort["entries"]:
        tier = entry["tier"]
        sha = entry["source_video_sha256"]
        tier_videos[tier].add(sha)
        for ep in entry["episodes"]:
            tier_episode_counts[tier] += 1
            for metric_id in metric_list:
                if metric_id in ep["metrics"]:
                    tier_values[metric_id][tier].append(ep["metrics"][metric_id])
                    tier_metric_videos[metric_id][tier].add(sha)

    metric_reports: dict[str, Any] = {}
    for metric_id in metric_list:
        by_tier: dict[str, Any] = {}
        for tier in TIERS:
            vals = tier_values[metric_id][tier]
            if vals:
                by_tier[tier] = summarize_distribution(
                    vals, n_videos=len(tier_metric_videos[metric_id][tier])
                )

        present_upper = [t for t in UPPER_TIERS if tier_values[metric_id][t]]
        present_lower = [t for t in LOWER_TIERS if tier_values[metric_id][t]]
        upper_vals = [v for t in present_upper for v in tier_values[metric_id][t]]
        lower_vals = [v for t in present_lower for v in tier_values[metric_id][t]]

        upper_summary = summarize_distribution(
            upper_vals,
            n_videos=len({s for t in present_upper for s in tier_metric_videos[metric_id][t]}),
        )
        lower_summary = summarize_distribution(
            lower_vals,
            n_videos=len({s for t in present_lower for s in tier_metric_videos[metric_id][t]}),
        )

        upper_vs_lower: dict[str, Any] | None = None
        if upper_vals and lower_vals:
            raw_sep = mann_whitney_cliffs(upper_vals, lower_vals)
            ref_median = upper_summary["p50"]
            upper_prox = [-abs(v - ref_median) for v in upper_vals]
            lower_prox = [-abs(v - ref_median) for v in lower_vals]
            prox_sep = mann_whitney_cliffs(upper_prox, lower_prox)
            upper_vs_lower = {
                "upper_tiers": present_upper,
                "lower_tiers": present_lower,
                "raw_separation": raw_sep,
                "proximity_to_reference_median_separation": prox_sep,
            }

        pairwise: dict[str, Any] = {}
        for hi_idx in range(len(TIERS) - 1, -1, -1):
            for lo_idx in range(hi_idx):
                hi_tier, lo_tier = TIERS[hi_idx], TIERS[lo_idx]
                if tier_values[metric_id][hi_tier] and tier_values[metric_id][lo_tier]:
                    pairwise[f"{hi_tier}_vs_{lo_tier}"] = mann_whitney_cliffs(
                        tier_values[metric_id][hi_tier], tier_values[metric_id][lo_tier]
                    )

        metric_reports[metric_id] = {
            "word_indicator_links": cohort["indicator_links"][metric_id],
            "by_tier": by_tier,
            "upper_group_summary": upper_summary,
            "lower_group_summary": lower_summary,
            "upper_vs_lower": upper_vs_lower,
            "pairwise_separation": pairwise,
        }

    return {
        "schema_version": CALIBRATION_SCHEMA_VERSION,
        "cohort_manifest_sha256": cohort["manifest_sha256"],
        "total_videos": len(cohort["entries"]),
        "videos_by_tier": {t: len(tier_videos[t]) for t in TIERS if tier_videos[t]},
        "episodes_by_tier": {t: int(tier_episode_counts[t]) for t in TIERS if tier_episode_counts[t]},
        "source_video_sha256s": sorted(e["source_video_sha256"] for e in cohort["entries"]),
        "indicator_links": cohort["indicator_links"],
        "metrics": metric_reports,
    }


def evaluate_cohort(
    manifest_or_path: Mapping[str, Any] | str | Path,
    *,
    metrics: Sequence[str] = CALIBRATABLE_METRICS,
) -> dict[str, Any]:
    """Compute per-metric per-tier distribution summaries and nonparametric separations."""
    cohort = load_cohort_manifest(manifest_or_path)
    return _evaluate_loaded_cohort(cohort, metrics=metrics)


def _derive_metric_rule(
    metric_id: str,
    metric_report: Mapping[str, Any],
    *,
    min_reference_episodes: int,
    min_comparison_episodes: int,
    min_reference_videos: int,
    min_comparison_videos: int,
    min_iqr: float,
    min_separation_delta: float,
    require_lower_tier: bool,
) -> dict[str, Any]:
    spec = METRIC_SPECS[metric_id]
    upper_summary = metric_report["upper_group_summary"]
    lower_summary = metric_report["lower_group_summary"]
    upper_vs_lower = metric_report["upper_vs_lower"]

    if upper_summary is None:
        raise InsufficientCohortError(f"{metric_id}: no reference (pro/advanced) observations present")
    if upper_summary["n"] < min_reference_episodes:
        raise InsufficientCohortError(
            f"{metric_id}: reference episodes {upper_summary['n']} < minimum {min_reference_episodes}"
        )
    if upper_summary["n_videos"] < min_reference_videos:
        raise InsufficientCohortError(
            f"{metric_id}: reference distinct videos {upper_summary['n_videos']} < minimum {min_reference_videos}"
        )
    if require_lower_tier:
        if lower_summary is None or upper_vs_lower is None:
            raise InsufficientCohortError(f"{metric_id}: comparison (intermediate/beginner) tier required")
        if lower_summary["n"] < min_comparison_episodes:
            raise InsufficientCohortError(
                f"{metric_id}: comparison episodes {lower_summary['n']} < minimum {min_comparison_episodes}"
            )
        if lower_summary["n_videos"] < min_comparison_videos:
            raise InsufficientCohortError(
                f"{metric_id}: comparison distinct videos {lower_summary['n_videos']} < minimum {min_comparison_videos}"
            )

    decimals = spec["decimals"]
    curve = spec["curve"]

    if curve == "time_mean_increasing_saturating_ramp":
        if lower_summary is not None:
            if upper_summary["p50"] <= lower_summary["p50"]:
                raise ReferenceCalibrationError(
                    f"{metric_id}: upper tier median {upper_summary['p50']} does not exceed lower tier median {lower_summary['p50']}"
                )
            low_raw = max(spec["low_bound"], lower_summary["p25"])
        else:
            low_raw = max(spec["low_bound"], upper_summary["p10"] - max(upper_summary["iqr"], spec["min_edge_span"]))
        high_raw = min(spec["high_bound"], upper_summary["p25"])
        knots = [round(low_raw, decimals), round(high_raw, decimals)]
        a, b = knots
        if not (spec["low_bound"] <= a < b <= spec["high_bound"]) or (b - a) < spec["min_edge_span"]:
            raise ReferenceCalibrationError(f"{metric_id}: derived ramp knots invalid or non-monotonic: {knots}")
    elif curve == "time_mean_trapezoid":
        good_low_raw = upper_summary["p10"]
        good_high_raw = upper_summary["p90"]
        spread = max(upper_summary["iqr"], 0.5 * (good_high_raw - good_low_raw))
        if spread < min_iqr:
            raise ReferenceCalibrationError(f"{metric_id}: degenerate near-zero spread ({spread}) in reference tier")
        if metric_id == "arm_space" and good_high_raw > spec["max_good_high"]:
            raise ReferenceCalibrationError(
                f"arm_space: reference p90={good_high_raw} exceeds non-locked physical bound {spec['max_good_high']}"
            )
        low_raw = max(spec["low_bound"], good_low_raw - 1.25 * spread)
        high_raw = min(spec["high_bound"], good_high_raw + 1.25 * spread)
        knots = [
            round(low_raw, decimals),
            round(good_low_raw, decimals),
            round(good_high_raw, decimals),
            round(high_raw, decimals),
        ]
        a, b, c, d = knots
        if not (spec["low_bound"] <= a < b <= c < d <= spec["high_bound"]):
            raise ReferenceCalibrationError(f"{metric_id}: derived trapezoid knots out of bounds or non-monotonic: {knots}")
        if (b - a) < spec["min_edge_span"] or (d - c) < spec["min_edge_span"]:
            raise ReferenceCalibrationError(f"{metric_id}: derived trapezoid edge ramp too narrow: {knots}")
        if metric_id == "arm_space" and c >= 1.0:
            raise ReferenceCalibrationError(f"arm_space: plateau upper knot {c} must remain strictly < 1.0")
    else:
        raise ReferenceCalibrationError(f"unsupported calibration curve: {curve}")

    rule = {"curve": curve, "knots": knots, **spec["rule_metadata"]}
    score_sep = None
    if upper_vs_lower is not None:
        raw_sep = upper_vs_lower["raw_separation"]
        # Re-read tier raw values via separation sizes? Better: compute score separation if caller passes values.
        effective_delta = (
            raw_sep["cliffs_delta"]
            if curve == "time_mean_increasing_saturating_ramp"
            else max(raw_sep["abs_cliffs_delta"], upper_vs_lower["proximity_to_reference_median_separation"]["cliffs_delta"])
        )
        if effective_delta < min_separation_delta:
            raise ReferenceCalibrationError(
                f"{metric_id}: discrimination Cliff's delta {effective_delta:.4f} < threshold {min_separation_delta:.4f}"
            )
        score_sep = effective_delta
    return {"rule": rule, "effective_cliffs_delta": score_sep}


def verify_rubric_policy_signature(policy: Mapping[str, Any]) -> bool:
    """Verify canonical SHA-256 integrity seal on an exported rubric policy."""
    if not isinstance(policy, Mapping):
        return False
    declared = policy.get("policy_sha256")
    if not isinstance(declared, str) or not _SHA256_RE.fullmatch(declared):
        return False
    body = {k: v for k, v in policy.items() if k != "policy_sha256"}
    try:
        return declared.lower() == canonical_sha256(body)
    except (TypeError, ValueError):
        return False


def calibrate_reference_population(
    manifest_or_path: Mapping[str, Any] | str | Path,
    *,
    metrics: Sequence[str] = CALIBRATABLE_METRICS,
    min_reference_episodes: int = 10,
    min_comparison_episodes: int = 10,
    min_reference_videos: int = 3,
    min_comparison_videos: int = 2,
    min_iqr: float = 1e-4,
    min_separation_delta: float = 0.0,
    require_lower_tier: bool = True,
) -> dict[str, Any]:
    """Derive and sign a coach-free rubric policy JSON from tiered SAM 3D cohorts."""
    if min_reference_episodes < 2 or min_reference_videos < 1:
        raise ReferenceCalibrationError("minimum reference thresholds must be positive")
    cohort = load_cohort_manifest(manifest_or_path)
    cohort_eval = _evaluate_loaded_cohort(cohort, metrics=metrics)

    tier_metric_values: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for entry in cohort["entries"]:
        tier = entry["tier"]
        for ep in entry["episodes"]:
            for metric_id, val in ep["metrics"].items():
                tier_metric_values[metric_id][tier].append(val)

    derived_rules: dict[str, Any] = {}
    metric_calibration_details: dict[str, Any] = {}
    for metric_id in metrics:
        report = cohort_eval["metrics"][metric_id]
        derived = _derive_metric_rule(
            metric_id,
            report,
            min_reference_episodes=min_reference_episodes,
            min_comparison_episodes=min_comparison_episodes,
            min_reference_videos=min_reference_videos,
            min_comparison_videos=min_comparison_videos,
            min_iqr=min_iqr,
            min_separation_delta=min_separation_delta,
            require_lower_tier=require_lower_tier,
        )
        rule = derived["rule"]
        derived_rules[metric_id] = rule

        upper_vals = [v for t in UPPER_TIERS for v in tier_metric_values[metric_id][t]]
        lower_vals = [v for t in LOWER_TIERS for v in tier_metric_values[metric_id][t]]
        score_separation = None
        if upper_vals and lower_vals:
            upper_scores = [_score_value(v, rule["curve"], rule["knots"]) for v in upper_vals]
            lower_scores = [_score_value(v, rule["curve"], rule["knots"]) for v in lower_vals]
            score_separation = mann_whitney_cliffs(upper_scores, lower_scores)
            if score_separation["cliffs_delta"] < min_separation_delta:
                raise ReferenceCalibrationError(
                    f"{metric_id}: proposed rule score Cliff's delta {score_separation['cliffs_delta']:.4f} < {min_separation_delta:.4f}"
                )

        metric_calibration_details[metric_id] = {
            **report,
            "derived_rule": rule,
            "proposed_score_separation": score_separation,
        }

    generated_at = datetime.now(timezone.utc).isoformat()
    policy_body = {
        "schema_version": RUBRIC_POLICY_SCHEMA_VERSION,
        "rubric_version": TARGET_RUBRIC_VERSION,
        "calibration_origin": CALIBRATION_ORIGIN,
        "numeric_rule_origin": CALIBRATION_ORIGIN,
        "coach_calibrated": False,
        "generated_at_utc": generated_at,
        "indicator_links": {m: cohort_eval["indicator_links"][m] for m in metrics},
        "cohort_summary": {
            "cohort_manifest_sha256": cohort_eval["cohort_manifest_sha256"],
            "total_videos": cohort_eval["total_videos"],
            "videos_by_tier": cohort_eval["videos_by_tier"],
            "episodes_by_tier": cohort_eval["episodes_by_tier"],
            "source_video_sha256s": cohort_eval["source_video_sha256s"],
        },
        "rules": derived_rules,
    }
    policy_sha256 = canonical_sha256(policy_body)
    policy = {**policy_body, "policy_sha256": policy_sha256}

    audit_report = {
        "schema_version": CALIBRATION_SCHEMA_VERSION,
        "status": "calibrated",
        "coach_calibrated": False,
        "calibration_origin": CALIBRATION_ORIGIN,
        "rubric_version": TARGET_RUBRIC_VERSION,
        "policy_sha256": policy_sha256,
        "generated_at_utc": generated_at,
        "guardrails": {
            "min_reference_episodes": min_reference_episodes,
            "min_comparison_episodes": min_comparison_episodes,
            "min_reference_videos": min_reference_videos,
            "min_comparison_videos": min_comparison_videos,
            "min_iqr": min_iqr,
            "min_separation_delta": min_separation_delta,
            "require_lower_tier": require_lower_tier,
        },
        "cohort_manifest_sha256": cohort_eval["cohort_manifest_sha256"],
        "total_videos": cohort_eval["total_videos"],
        "videos_by_tier": cohort_eval["videos_by_tier"],
        "episodes_by_tier": cohort_eval["episodes_by_tier"],
        "metrics": metric_calibration_details,
    }
    return {"policy": policy, "report": audit_report}


def export_calibration_artifacts(
    calibration_result: Mapping[str, Any],
    *,
    policy_path: str | Path | None = None,
    report_path: str | Path | None = None,
) -> dict[str, Path]:
    """Write policy and/or audit report JSON deterministically to disk."""
    written: dict[str, Path] = {}
    if policy_path is not None:
        target = Path(policy_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(calibration_result["policy"], ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        written["policy"] = target
    if report_path is not None:
        target = Path(report_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(calibration_result["report"], ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        written["report"] = target
    return written
