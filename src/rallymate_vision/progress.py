"""Work-based progress for the stages after frame inference."""

from __future__ import annotations


POSTPROCESS_STAGES = {
    "preparing_replay": (85.0, 88.0, "正在准备视频回放"),
    "tracking_player": (88.0, 89.0, "正在整理人物轨迹"),
    "recognizing_strokes": (89.0, 90.0, "正在识别挥拍片段"),
    "detecting_events": (90.0, 92.0, "正在划分动作片段"),
    "extracting_features": (92.0, 96.0, "正在计算动作测量"),
    "scoring": (96.0, 97.0, "正在整理测量结果"),
    "writing_results": (97.0, 98.0, "正在保存测量结果"),
    "finalizing": (98.0, 99.0, "正在生成训练报告"),
    "scoring_readiness": (99.0, 99.5, "正在校验报告完整性"),
}


def postprocess_progress(
    phase: str,
    *,
    processed_frames: int,
    total_frames: int,
    completed: int | None = None,
    total: int | None = None,
) -> dict:
    """Describe completed work; never interpolate time or claim job completion."""
    start, end, message = POSTPROCESS_STAGES[phase]
    payload = {
        "phase": phase,
        "percent": start,
        "processed_frames": processed_frames,
        "total_frames": total_frames,
        "message": message,
    }
    if isinstance(total, int) and not isinstance(total, bool) and total > 0:
        done = min(total, max(0, completed or 0))
        payload.update(
            percent=round(start + (end - start) * done / total, 1),
            completed_items=done,
            total_items=total,
            message=f"{message}（{done}/{total}）",
        )
    return payload
