from __future__ import annotations

import json
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2
import numpy as np


TARGET_JOINTS = (
    "left_shoulder",
    "right_shoulder",
    "left_hip",
    "right_hip",
    "left_knee",
    "right_knee",
    "left_ankle",
    "right_ankle",
)
POSE_CONNECTIONS = (
    (5, 6), (5, 7), (7, 9), (6, 8), (8, 10), (5, 11),
    (6, 12), (11, 12), (11, 13), (13, 15), (12, 14), (14, 16),
)
EVIDENCE = (
    ("3ae77ee3271d67de171585a5c39ddd69", 23, "low_confidence+jump"),
    ("850cb0006b406c7176eeda8d711cd065", 897, "jump"),
    ("8d7754d0de6d315674013d5b69a0b6ba", 4325, "low_confidence+jump"),
    ("8d7754d0de6d315674013d5b69a0b6ba", 11515, "temporal_tail"),
)


def _round(value: float) -> float:
    return round(float(value), 6)


def _diagnostic_metrics(analysis: dict[str, Any]) -> dict[str, Any]:
    track_frames = int(analysis["tracking"]["primary_pose_track_frames"])
    valid = analysis["keypoints"]["valid_fraction_on_primary_track"]
    temporal = analysis["keypoints"]["temporal_diagnostics"]
    jitter = [
        temporal[joint]["jitter_residual_body"]["p50"]
        for joint in TARGET_JOINTS
        if temporal[joint]["jitter_residual_body"]["p50"] is not None
    ]
    swaps = analysis["keypoints"]["left_right_swap_diagnostics"]["candidate_count"]
    jumps = analysis["keypoints"]["jump_diagnostics"]["candidate_frame_count"]
    return {
        "pose_frame_fraction": analysis["existing_run"]["pose_frame_fraction"],
        "primary_pose_track_fraction": analysis["tracking"]["primary_pose_track_fraction"],
        "primary_pose_track_frames": track_frames,
        "pose_track_count": analysis["tracking"]["pose_track_count"],
        "target_joint_valid_fraction_min": min(valid[joint] for joint in TARGET_JOINTS),
        "swap_candidate_count": swaps,
        "swap_candidates_per_1000_primary_track_frames": _round(
            swaps / max(track_frames, 1) * 1000
        ),
        "jump_candidate_frame_count": jumps,
        "jump_candidate_frames_per_1000_primary_track_frames": _round(
            jumps / max(track_frames, 1) * 1000
        ),
        "target_joint_jitter_p50_body_mean": _round(statistics.mean(jitter)),
        "longest_primary_pose_missing_frames": analysis["tracking"][
            "longest_primary_pose_missing_frames_over_video"
        ],
        "feature_distributions": analysis["features"]["distributions"],
    }


def _aggregate(videos: dict[str, dict[str, Any]]) -> dict[str, float]:
    fields = (
        "pose_frame_fraction",
        "primary_pose_track_fraction",
        "target_joint_valid_fraction_min",
        "swap_candidates_per_1000_primary_track_frames",
        "jump_candidate_frames_per_1000_primary_track_frames",
        "target_joint_jitter_p50_body_mean",
    )
    return {
        field: _round(statistics.mean(item[field] for item in videos.values()))
        for field in fields
    }


def _read_selected(path: Path, indexes: set[int]) -> dict[int, dict]:
    selected = {}
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            record = json.loads(line)
            index = int(record["frame"]["processed_index"])
            if index in indexes:
                selected[index] = record
                if len(selected) == len(indexes):
                    break
    return selected


def _draw_pose(image: np.ndarray, poses: list[dict], color: tuple[int, int, int]) -> None:
    if not poses:
        return
    pose = max(poses, key=lambda item: item.get("confidence", 0.0))
    points = pose["keypoints"]
    for first, second in POSE_CONNECTIONS:
        if points[first]["confidence"] < 0.25 or points[second]["confidence"] < 0.25:
            continue
        a = (int(points[first]["x_px"]), int(points[first]["y_px"]))
        b = (int(points[second]["x_px"]), int(points[second]["y_px"]))
        cv2.line(image, a, b, color, 3, cv2.LINE_AA)
    for index, point in enumerate(points):
        if point["confidence"] < 0.25:
            continue
        radius = 4 if index < 17 else 3
        cv2.circle(
            image,
            (int(point["x_px"]), int(point["y_px"])),
            radius,
            color if index < 17 else (0, 255, 255),
            -1,
            cv2.LINE_AA,
        )


def _evidence_sheet(root: Path, candidates: list[str], output: Path) -> None:
    by_video: dict[str, set[int]] = {}
    for video_id, processed_index, _ in EVIDENCE:
        by_video.setdefault(video_id, set()).add(processed_index)
    records: dict[tuple[str, str], dict[int, dict]] = {}
    for video_id, indexes in by_video.items():
        records[("yolo", video_id)] = _read_selected(
            root / "runs" / "full-test" / video_id / "frames.jsonl", indexes
        )
        for candidate in candidates:
            records[(candidate, video_id)] = _read_selected(
                root / "runs" / "pose-ab" / candidate / video_id / "frames.jsonl",
                indexes,
            )
    labels = ["YOLO"] + [
        value.replace("rtmpose-", "").replace("-halpe26", "")
        for value in candidates
    ]
    colors = [(255, 90, 210), (70, 220, 70), (50, 180, 255), (255, 170, 40)]
    rows = []
    for video_id, processed_index, reason in EVIDENCE:
        capture = cv2.VideoCapture(str(root / "FULL-TEST" / f"{video_id}.mp4"))
        capture.set(cv2.CAP_PROP_POS_FRAMES, processed_index)
        ok, frame = capture.read()
        capture.release()
        if not ok:
            raise RuntimeError(f"could not decode evidence frame {video_id}:{processed_index}")
        cells = []
        model_ids = ["yolo"] + candidates
        for model_id, label, color in zip(model_ids, labels, colors):
            cell = frame.copy()
            record = records[(model_id, video_id)][processed_index]
            _draw_pose(cell, record["poses"], color)
            cell = cv2.resize(cell, (320, 180), interpolation=cv2.INTER_AREA)
            cv2.rectangle(cell, (0, 0), (320, 26), (0, 0, 0), -1)
            cv2.putText(cell, label, (7, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)
            cells.append(cell)
        row = np.hstack(cells)
        cv2.putText(
            row,
            f"{video_id[:6]} frame={processed_index} {reason}",
            (8, 174),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )
        rows.append(row)
    output.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(output), np.vstack(rows), [cv2.IMWRITE_JPEG_QUALITY, 92])


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        "# RallyMate Pose Backend A/B 报告",
        "",
        f"版本：`{report['report_version']}`  ",
        f"状态：`{report['status']}`  ",
        "默认 Backend：`yolo`（未切换）",
        "",
        "> 覆盖率、置信度、swap/jump 启发式和特征分布不是准确率。没有人工关键点/事件真值，因此 MAE、P95、PCK、特征误差和正式晋级结论均不能伪造。",
        "",
        "## 性能抽样",
        "",
        "| Backend | 调用 P50/P95 ms | 每 ROI P50/P95 ms | warm-up allocated MiB | peak allocated MiB |",
        "|---|---:|---:|---:|---:|",
    ]
    for model_id, values in report["performance"].items():
        memory = values["cuda_memory_bytes"]
        if model_id == "yolo":
            warm = memory["allocated_after_pose_warmup"]
            peak = memory["peak_allocated_during_pose_benchmark"]
        else:
            warm = memory["after_warmup"]["allocated"]
            peak = memory["peak_allocated"]
        lines.append(
            f"| `{model_id}` | {values['latency_ms']['p50']:.3f} / {values['latency_ms']['p95']:.3f} | "
            f"{values['latency_per_roi_ms']['p50']:.3f} / {values['latency_per_roi_ms']['p95']:.3f} | "
            f"{warm / 1048576:.2f} | {peak / 1048576:.2f} |"
        )
    lines.extend(
        [
            "",
            "抽样复用同一冻结 Player ROI，每视频请求 60 帧、实际 177 次调用/282 个原始 ROI 计数。RTMPose 当前为同帧多 ROI 顺序执行；该表不是完整 Detect + Track + Pose 端到端吞吐。",
            "",
            "## 整段时序诊断（3 视频非加权平均）",
            "",
            "| Backend | Pose 帧覆盖 | 主 Track | 目标点最小有效率 | swap/千帧 | jump/千帧 | jitter P50 body |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for model_id, values in report["temporal_aggregate"].items():
        lines.append(
            f"| `{model_id}` | {values['pose_frame_fraction']:.2%} | "
            f"{values['primary_pose_track_fraction']:.2%} | "
            f"{values['target_joint_valid_fraction_min']:.2%} | "
            f"{values['swap_candidates_per_1000_primary_track_frames']:.2f} | "
            f"{values['jump_candidate_frames_per_1000_primary_track_frames']:.2f} | "
            f"{values['target_joint_jitter_p50_body_mean']:.5f} |"
        )
    lines.extend(
        [
            "",
            "RTMPose 总体提高有效率并降低 jitter，但 jump 候选明显增加，swap 也因模型/视频而异；最长缺失仍由冻结 Detection Track 碎片主导。混合结果不足以声称模型更准确。",
            "",
            "## 晋级门禁",
            "",
            "| 门禁 | 状态 | 结论 |",
            "|---|---|---|",
        ]
    )
    for gate in report["promotion_gates"]:
        lines.append(f"| {gate['gate']} | `{gate['status']}` | {gate['reason']} |")
    lines.extend(
        [
            "",
            "## 决策",
            "",
            "三个候选均为 `not_promoted`，默认保持 YOLO-Pose。阻断项是人工关键点误差、人工事件边界、特征误差和独立测试，而不是工程推理失败。没有执行默认切换、ONNX/TensorRT 导出、Canary 或删除回滚模型。",
            "",
            "定性证据图：`reports/pose-ab-evidence.jpg`。图只用于人工审阅，不作为真值。机器报告保留逐视频时序和十项特征分布。",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    root = Path.cwd()
    yolo = json.loads((root / "reports" / "pose-model-baseline.json").read_text(encoding="utf-8"))
    rtmpose = json.loads((root / "reports" / "rtmpose-candidate-smoke.json").read_text(encoding="utf-8"))
    video_ids = [item["id"] for item in yolo["videos"]]
    candidates = [item["candidate_id"] for item in rtmpose["candidates"]]
    temporal = {
        "yolo": {
            item["id"]: _diagnostic_metrics(item["analysis"])
            for item in yolo["videos"]
        }
    }
    for candidate in candidates:
        temporal[candidate] = {}
        for video_id in video_ids:
            analysis = json.loads(
                (
                    root
                    / "runs"
                    / "pose-ab"
                    / candidate
                    / video_id
                    / "pose-diagnostics.json"
                ).read_text(encoding="utf-8")
            )
            temporal[candidate][video_id] = _diagnostic_metrics(analysis)

    yolo_benchmark = yolo["performance_benchmark"]
    performance = {
        "yolo": {
            "latency_ms": yolo_benchmark["pose_call_latency_ms"],
            "latency_per_roi_ms": yolo_benchmark["latency_per_roi_ms"],
            "cuda_memory_bytes": yolo_benchmark["cuda_memory_bytes"],
            "protocol_match": "same_frozen_ROI_selection_60_requested_per_video",
        }
    }
    for item in rtmpose["candidates"]:
        performance[item["candidate_id"]] = {
            "latency_ms": item["latency_ms"],
            "latency_per_roi_ms": item["latency_per_roi_ms"],
            "cuda_memory_bytes": item["cuda_memory_bytes"],
            "protocol_match": "same_frozen_ROI_selection_60_requested_per_video",
        }

    gates = [
        {
            "gate": "hip/knee/ankle keypoint MAE/P95/PCK improves",
            "status": "ground_truth_required",
            "reason": "79-frame labeling manifest exists but corrected keypoints are not populated",
        },
        {
            "gate": "six-indicator feature MAE/P95/Bias improves",
            "status": "ground_truth_required",
            "reason": "manual corrected keypoints and event boundaries are absent",
        },
        {
            "gate": "temporal diagnostics do not degrade",
            "status": "mixed_diagnostic_not_accuracy",
            "reason": "jitter improves, but jump candidates rise and swap is model/video dependent",
        },
        {
            "gate": "realtime end-to-end throughput >= 85% of YOLO",
            "status": "not_evaluable_end_to_end_required",
            "reason": "pose-stage sample is faster, but full Detect+Track+Pose matched rerun is not yet the comparison source",
        },
        {
            "gate": "contract, rollback and regression tests",
            "status": "passed",
            "reason": "YOLO rollback/default preserved; Halpe26 schema and 34 tests pass",
        },
    ]
    report = {
        "schema_version": "1.0.0",
        "report_version": "pose-backend-ab-2026-08-13.1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "no_candidate_promoted_ground_truth_required",
        "default_backend": "yolo",
        "protocol": {
            "videos": video_ids,
            "detection_and_track_source": "frozen YOLO detections/tracks from runs/full-test",
            "full_sequence_pose_replay": True,
            "performance_samples_per_video_requested": 60,
            "ground_truth_available": False,
        },
        "performance": performance,
        "temporal_by_video": temporal,
        "temporal_aggregate": {
            model_id: _aggregate(videos) for model_id, videos in temporal.items()
        },
        "ground_truth_evaluation": {
            "status": "ground_truth_required",
            "keypoint_mae": None,
            "keypoint_p95": None,
            "pck": None,
            "feature_mae": None,
            "feature_p95": None,
            "feature_bias": None,
        },
        "promotion_gates": gates,
        "candidate_decisions": {
            candidate: {
                "decision": "not_promoted",
                "reason_codes": [
                    "manual_keypoint_ground_truth_missing",
                    "manual_event_boundaries_missing",
                    "feature_error_not_evaluated",
                    "independent_test_not_run",
                ],
            }
            for candidate in candidates
        },
        "qualitative_evidence": {
            "status": "manual_review_only_not_ground_truth",
            "frames": [
                {"video_id": video_id, "processed_index": index, "reason": reason}
                for video_id, index, reason in EVIDENCE
            ],
            "image": "reports/pose-ab-evidence.jpg",
        },
    }
    output_json = root / "reports" / "pose-backend-ab.json"
    output_md = root / "docs" / "POSE_BACKEND_AB_REPORT.md"
    output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    output_md.write_text(_markdown(report), encoding="utf-8")
    _evidence_sheet(root, candidates, root / "reports" / "pose-ab-evidence.jpg")
    print(json.dumps({"json": str(output_json), "markdown": str(output_md), "status": report["status"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
