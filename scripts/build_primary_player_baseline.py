from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from rallymate_tracking import build_primary_player_artifacts


VIDEO_IDS = (
    "3ae77ee3271d67de171585a5c39ddd69",
    "850cb0006b406c7176eeda8d711cd065",
    "8d7754d0de6d315674013d5b69a0b6ba",
)


def _markdown(report: dict) -> str:
    lines = [
        "# RallyMate 主球员时序基线",
        "",
        f"版本：`{report['report_version']}`  ",
        "语义主球员：所有视频/事件区间使用稳定 `primary_player_id=1`；原始 Track ID 作为可追溯来源保留。",
        "",
        "> source Track 切换、左右交换和跳点均为诊断候选，不是有身份/关键点真值的准确率。",
        "",
        "| 视频 | Track 覆盖 | Pose 覆盖 | 来源 Track 数 | 来源切换候选 | 关键点有效率 | swap 帧 | jump 帧 | 最长 Pose 缺失 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in report["videos"]:
        diagnostics = item["diagnostics"]
        lines.append(
            f"| `{item['video_id']}` | {diagnostics['track_coverage_fraction']:.2%} | "
            f"{diagnostics['pose_coverage_fraction']:.2%} | {len(diagnostics['source_track_ids'])} | "
            f"{diagnostics['source_track_switch_candidate_count']} | "
            f"{diagnostics['keypoint_valid_fraction']:.2%} | "
            f"{len(diagnostics['left_right_swap_candidate_frames'])} | "
            f"{len(diagnostics['keypoint_jump_candidate_frames'])} | "
            f"{diagnostics['longest_pose_missing_frames']} 帧 / {diagnostics['longest_pose_missing_ms']} ms |"
        )
    lines.extend(
        [
            "",
            "选择器综合 Track 全区间覆盖、Pose 有效率、运动量、框面积和逐帧空间连续性；框面积权重仅 10%，不再使用逐帧最大框直接决定主球员。",
            "",
            "`confirmed_id_switch_count` 在没有人工身份真值时保持 `null` / `ground_truth_required`。事件层应使用 `primary_player_id`，并通过 `source_track_id` 回溯原检测和 Pose。",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    root = Path.cwd()
    videos = []
    for video_id in VIDEO_IDS:
        output_dir = root / "reports" / "primary-player" / video_id
        output_dir.mkdir(parents=True, exist_ok=True)
        summary = build_primary_player_artifacts(
            root / "runs" / "full-test" / video_id / "frames.jsonl",
            output_dir / "primary-player.jsonl",
            output_dir / "primary-player-summary.json",
        )
        videos.append(
            {
                "video_id": video_id,
                "timeline": str(output_dir / "primary-player.jsonl"),
                "summary": str(output_dir / "primary-player-summary.json"),
                "diagnostics": summary["diagnostics"],
            }
        )
    report = {
        "schema_version": "1.0.0",
        "report_version": "primary-player-baseline-2026-08-13.1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "diagnostic_baseline_identity_ground_truth_required",
        "videos": videos,
    }
    report_path = root / "reports" / "primary-player-baseline.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (root / "docs" / "PRIMARY_PLAYER_BASELINE.md").write_text(
        _markdown(report), encoding="utf-8"
    )
    print(json.dumps({"report": str(report_path), "videos": len(videos)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
