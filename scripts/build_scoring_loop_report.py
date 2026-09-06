from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from rallymate_scoring.feasibility import validate_feasibility_registry
from rallymate_scoring.scoring_loop_report import write_scoring_loop_report


VIDEO_IDS = (
    "3ae77ee3271d67de171585a5c39ddd69",
    "850cb0006b406c7176eeda8d711cd065",
    "8d7754d0de6d315674013d5b69a0b6ba",
)
HISTORICAL_REGISTRY_VERSION = "minimum-scoring-loop-2026-08-13.1"
HISTORICAL_REGISTRY_SHA256 = (
    "5f7952c6d9514856ad979e27a556cfa2d80b188eaeffd8333a25f32aacf5dfcc"
)
HISTORICAL_INDICATOR_IDS = (
    "FS01-M02",
    "FS01-M05",
    "FS02-M02",
    "FS09-M03",
    "FS09-M04",
    "FS09-M05",
)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render reports for the frozen 2026-08-13 six-indicator replay"
    )
    parser.add_argument(
        "--historical-replay",
        action="store_true",
        help="explicitly authorize the frozen six-indicator historical workflow",
    )
    args = parser.parse_args(argv)
    if not args.historical_replay:
        parser.error(
            "--historical-replay is required; this script uses the frozen "
            "2026-08-13 six-indicator registry, not the current registry"
        )
    return args


def _load_historical_registry(root: Path) -> dict:
    path = root / "metric-feasibility.json"
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != HISTORICAL_REGISTRY_SHA256:
        raise RuntimeError(
            "historical replay registry SHA-256 mismatch; refusing to substitute "
            "a current or modified registry"
        )
    registry = json.loads(raw.decode("utf-8"))
    validate_feasibility_registry(registry)
    indicator_ids = tuple(item["indicator_id"] for item in registry["indicators"])
    if (
        registry.get("registry_version") != HISTORICAL_REGISTRY_VERSION
        or indicator_ids != HISTORICAL_INDICATOR_IDS
    ):
        raise RuntimeError(
            "historical replay requires the frozen 2026-08-13 six-indicator registry"
        )
    return registry


def _historical_replay_metadata() -> dict:
    return {
        "execution_mode": "historical_replay",
        "registry_version": HISTORICAL_REGISTRY_VERSION,
        "registry_sha256": HISTORICAL_REGISTRY_SHA256,
        "indicator_ids": list(HISTORICAL_INDICATOR_IDS),
        "semantics": "frozen_six_indicator_baseline_not_current_production_registry",
    }


def _load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _require_historical_bundle(
    *,
    video_id: str,
    registry: dict,
    events: list[dict],
    records: list[dict],
    scores: list[dict],
    summary: dict,
) -> None:
    if summary.get("video_id") != video_id:
        raise RuntimeError("historical replay summary video_id mismatch")

    expected_by_event: dict[str, set[str]] = {}
    for indicator in registry["indicators"]:
        indicator_id = indicator["indicator_id"]
        expected_by_event.setdefault(indicator_id.split("-")[0], set()).add(
            indicator_id
        )

    events_by_id: dict[str, dict] = {}
    for event in events:
        event_id = event.get("event_id")
        event_code = event.get("event_code")
        if (
            not isinstance(event_id, str)
            or event_id in events_by_id
            or event_code not in expected_by_event
            or event.get("provenance", {}).get("source_id") != video_id
        ):
            raise RuntimeError(
                "historical replay event identity/provenance is outside the frozen scope"
            )
        events_by_id[event_id] = event

    record_keys: set[tuple[str, str, str]] = set()
    indicators_by_event_id: dict[str, set[str]] = {
        event_id: set() for event_id in events_by_id
    }
    for record in records:
        event_id = record.get("event_id")
        indicator_id = record.get("indicator_id")
        event = events_by_id.get(event_id)
        key = (video_id, str(event_id), str(indicator_id))
        if (
            record.get("video_id") != video_id
            or event is None
            or record.get("event_code") != event.get("event_code")
            or indicator_id not in expected_by_event[event["event_code"]]
            or record.get("provenance", {}).get(
                "feasibility_registry_version"
            )
            != HISTORICAL_REGISTRY_VERSION
            or key in record_keys
        ):
            raise RuntimeError(
                "historical replay indicator record is outside the frozen registry bundle"
            )
        record_keys.add(key)
        indicators_by_event_id[event_id].add(indicator_id)

    for event_id, event in events_by_id.items():
        if indicators_by_event_id[event_id] != expected_by_event[event["event_code"]]:
            raise RuntimeError(
                "historical replay indicator records do not cover the exact frozen set"
            )

    score_keys: set[tuple[str, str, str]] = set()
    for score in scores:
        event_id = score.get("event_id")
        indicator_id = score.get("indicator_id")
        event = events_by_id.get(event_id)
        registry_version = score.get("model_versions", {}).get(
            "feasibility_registry"
        )
        key = (video_id, str(event_id), str(indicator_id))
        if (
            score.get("video_id") != video_id
            or event is None
            or score.get("event_code") != event.get("event_code")
            or indicator_id not in expected_by_event[event["event_code"]]
            or key in score_keys
            or (registry_version is not None and registry_version != HISTORICAL_REGISTRY_VERSION)
            or score.get("grade") is not None
            or score.get("threshold_version") is not None
            or score.get("status") not in {"calibration_required", "unavailable"}
        ):
            raise RuntimeError(
                "historical replay score is outside the frozen uncalibrated bundle"
            )
        score_keys.add(key)

    if score_keys != record_keys:
        raise RuntimeError(
            "historical replay score and indicator-record identities do not match"
        )


def main(argv: list[str] | None = None) -> None:
    _parse_args(argv)
    root = Path.cwd()
    registry = _load_historical_registry(root)
    links = []
    aggregate_status: Counter[str] = Counter()
    for video_id in VIDEO_IDS:
        directory = root / "reports" / "scoring-loop" / video_id
        events = _load_jsonl(directory / "events.jsonl")
        records = _load_jsonl(directory / "indicator-features.jsonl")
        scores = _load_jsonl(directory / "scores.jsonl")
        old_summary = json.loads((directory / "summary.json").read_text(encoding="utf-8"))
        _require_historical_bundle(
            video_id=video_id,
            registry=registry,
            events=events,
            records=records,
            scores=scores,
            summary=old_summary,
        )
        status_counts = Counter(item["status"] for item in scores)
        aggregate_status.update(status_counts)
        model_versions = dict(scores[0]["model_versions"]) if scores else {}
        model_versions["historical_replay"] = _historical_replay_metadata()
        summary = {
            "video_id": video_id,
            "loop_version": "minimum-scoring-loop-v0.2.0",
            "execution_mode": "historical_replay",
            "historical_replay": _historical_replay_metadata(),
            "status": old_summary["status"],
            "event_evaluation": old_summary["event_evaluation"],
            "feature_error_evaluation": {
                "status": "ground_truth_required",
                "feature_metrics": {},
                "error_budget": {
                    "pose_error": None,
                    "event_boundary_error": None,
                    "smoothing_error": None,
                    "missing_value_impact": None,
                },
            },
            "score_status_counts": dict(status_counts),
            "grade_counts": dict(Counter(item["grade"] for item in scores if item["grade"])),
            "model_versions": model_versions,
        }
        result = {
            "summary": summary,
            "events": events,
            "indicator_records": records,
            "feasibility": registry,
        }
        report_path = directory / "scoring-loop-report.html"
        write_scoring_loop_report(result, report_path)
        links.append((video_id, report_path.relative_to(root / "reports"), len(events), dict(status_counts)))
    cards = "".join(
        f'<section><h2>{video_id}</h2><p>候选事件 {count}；状态 {status}</p>'
        f'<p><a href="{path.as_posix()}">打开该视频的事件时间轴、六指标特征与证据报告</a></p>'
        f'<iframe src="{path.as_posix()}" loading="lazy"></iframe></section>'
        for video_id, path, count, status in links
    )
    output = root / "reports" / "minimum-scoring-loop-report.html"
    output.write_text(
        f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>RallyMate 评分闭环基线报告</title>
<style>body{{font:14px/1.55 system-ui,sans-serif;max-width:1400px;margin:28px auto;padding:0 18px;background:#f5f5f0;color:#172019}}section{{background:#fff;border:1px solid #c9cec9;padding:16px;margin:16px 0}}iframe{{width:100%;height:1050px;border:1px solid #d8ddd8}}a{{font-weight:700;color:#1256a0}}</style></head><body>
<h1>RallyMate 最小可行评分闭环基线</h1><p><strong>历史回放（historical replay）</strong>：本页固定使用 2026-08-13 六指标注册表，不代表当前生产注册表。</p><p>三个冻结视频合计状态：<code>{dict(aggregate_status)}</code>。所有非空特征在无教练真值时保持 <code>calibration_required</code>，所有 grade 均为空。</p>{cards}</body></html>''',
        encoding="utf-8",
    )
    print(json.dumps({"output": str(output), "execution_mode": "historical_replay", "status_counts": dict(aggregate_status)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
