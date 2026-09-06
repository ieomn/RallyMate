from __future__ import annotations

import argparse
import json
import platform
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import torch
import ultralytics

from rallymate_pose_evaluation.baseline import (
    BASELINE_ANALYZER_VERSION,
    TARGET_JOINTS,
    analyze_pose_artifact,
    benchmark_yolo_pose,
    sha256_file,
)


DEFAULT_VIDEO_IDS = (
    "3ae77ee3271d67de171585a5c39ddd69",
    "850cb0006b406c7176eeda8d711cd065",
    "8d7754d0de6d315674013d5b69a0b6ba",
)


def _specs(root: Path) -> list[dict[str, Path | str]]:
    roles = ("short", "difficult", "long")
    return [
        {
            "id": video_id,
            "role": role,
            "video": root / "FULL-TEST" / f"{video_id}.mp4",
            "frames": root / "runs" / "full-test" / video_id / "frames.jsonl",
            "summary": root / "runs" / "full-test" / video_id / "summary.json",
        }
        for role, video_id in zip(roles, DEFAULT_VIDEO_IDS)
    ]


def _annotation_manifest(root: Path, analyses: list[dict[str, Any]]) -> dict[str, Any]:
    samples = []
    for analysis in analyses:
        for candidate in analysis["ground_truth"]["annotation_candidates"]:
            samples.append(
                {
                    "video_path": analysis["video"]["path"],
                    "video_sha256": analysis["video"]["sha256"],
                    **candidate,
                    "requested_joints": list(TARGET_JOINTS),
                    "annotation_status": "pending",
                    "review_status": "pending",
                    "annotator_id": None,
                    "corrected_keypoints": None,
                }
            )
    return {
        "schema_version": "0.1.0-draft",
        "manifest_version": "pose-baseline-2026-08-13.1",
        "status": "ground_truth_required",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "workspace": str(root),
        "coordinate_contract": {
            "origin": "top_left",
            "required_output": "original_frame_pixel_coordinates",
            "missing_representation": "null_with_visibility_reason_never_zero_fill",
            "left_right_semantics": "subject_anatomical_left_and_right",
        },
        "samples": samples,
    }


def _fmt(value: Any, digits: int = 3) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        "# RallyMate YOLO-Pose 基线",
        "",
        f"文档版本：`{report['report_version']}`  ",
        f"生成时间：{report['generated_at']}  ",
        "状态：已冻结工程/时序基线；人工关键点误差仍为 `ground_truth_required`",
        "",
        "> 本报告中的覆盖率、左右交换候选、跳点候选和模型置信度均不是准确率。特征分布是全 Track、无事件、无平滑的诊断值，不能用于 A～E 判级。",
        "",
        "## 1. 模型与环境",
        "",
        f"- Detect：`{report['models']['detect']['path']}`，SHA-256 `{report['models']['detect']['sha256']}`",
        f"- Pose：`{report['models']['pose']['path']}`，SHA-256 `{report['models']['pose']['sha256']}`",
        f"- Python {report['environment']['python']} / Torch {report['environment']['torch']} / Ultralytics {report['environment']['ultralytics']} / OpenCV {report['environment']['opencv']}",
        f"- GPU：{report['environment']['cuda_device_name']}",
        "- 推理参数：Detect 960/conf 0.15；Pose ROI 640/conf 0.25；最多 2 人；frame stride 1。",
        "",
        "## 2. 固定视频与历史端到端结果",
        "",
        "| 角色 | 视频 | 帧数 | 历史端到端 FPS | Pose 帧覆盖 | 主 Pose Track | Pose Track 数 | 最大框 Track 变化 |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for item in report["videos"]:
        analysis = item["analysis"]
        lines.append(
            "| {role} | `{id}` | {frames} | {fps} | {pose:.2%} | {primary:.2%} | {tracks} | {switches} |".format(
                role=item["role"],
                id=item["id"],
                frames=analysis["video"]["frame_count"],
                fps=_fmt(analysis["existing_run"]["effective_processed_fps"]),
                pose=float(analysis["existing_run"]["pose_frame_fraction"] or 0.0),
                primary=float(analysis["tracking"]["primary_pose_track_fraction"]),
                tracks=analysis["tracking"]["pose_track_count"],
                switches=analysis["tracking"]["largest_bbox_track_switches"],
            )
        )
    benchmark = report["performance_benchmark"]
    memory = benchmark["cuda_memory_bytes"]
    lines.extend(
        [
            "",
            "历史端到端 FPS 来自已有整段运行产物；本次未重跑整段推理。最大框 Track 变化只表示逐帧最大框策略的原始 Track 改变，不等于有人工身份真值的 ID Switch。",
            "",
            "## 3. 本次 Pose 性能抽样",
            "",
            f"- 抽样：每视频请求 {benchmark['sample_count_per_video_requested']} 帧；{benchmark['warmup_calls']} 次 warm-up；复用当前 Player ROI 与最多 2 人批次。",
            f"- 当前组合 Perception 构造时间：{_fmt(benchmark['load_seconds'])} s（Detect + Pose，尚未拆分 Backend；GPU 权重可能惰性物化）。",
            f"- Pose 调用 P50/P95：{_fmt(benchmark['pose_call_latency_ms']['p50'])} / {_fmt(benchmark['pose_call_latency_ms']['p95'])} ms。",
            f"- 每 ROI P50/P95：{_fmt(benchmark['latency_per_roi_ms']['p50'])} / {_fmt(benchmark['latency_per_roi_ms']['p95'])} ms。",
            f"- CUDA allocated 构造后 / warm-up 后 / peak：{_fmt(memory['allocated_after_combined_model_load'] / 1048576 if memory['allocated_after_combined_model_load'] is not None else None)} / {_fmt(memory['allocated_after_pose_warmup'] / 1048576 if memory['allocated_after_pose_warmup'] is not None else None)} / {_fmt(memory['peak_allocated_during_pose_benchmark'] / 1048576 if memory['peak_allocated_during_pose_benchmark'] is not None else None)} MiB。",
            f"- CUDA reserved 构造后 / warm-up 后 / peak：{_fmt(memory['reserved_after_combined_model_load'] / 1048576 if memory['reserved_after_combined_model_load'] is not None else None)} / {_fmt(memory['reserved_after_pose_warmup'] / 1048576 if memory['reserved_after_pose_warmup'] is not None else None)} / {_fmt(memory['peak_reserved_during_pose_benchmark'] / 1048576 if memory['peak_reserved_during_pose_benchmark'] is not None else None)} MiB。",
            "",
            "这些显存值是当前 Python 进程的 PyTorch allocator 指标，不是 `nvidia-smi` 的整卡占用。",
            "",
            "| 视频 | 调用样本 | ROI | 调用 P50 | 调用 P95 | 每 ROI P50 | 每 ROI P95 |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for video_id, values in benchmark["by_video"].items():
        lines.append(
            f"| `{video_id}` | {values['sample_count']} | {values['roi_count']} | "
            f"{_fmt(values['pose_call_latency_ms']['p50'])} | {_fmt(values['pose_call_latency_ms']['p95'])} | "
            f"{_fmt(values['latency_per_roi_ms']['p50'])} | {_fmt(values['latency_per_roi_ms']['p95'])} |"
        )
    lines.extend(
        [
            "",
            "## 4. 关键点有效率与时序诊断",
            "",
            "| 视频 | 肩最小有效率 | 髋最小有效率 | 膝最小有效率 | 踝最小有效率 | swap 候选 | jump 候选帧 | 主 Track 最长全视频缺失 |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for item in report["videos"]:
        analysis = item["analysis"]
        valid = analysis["keypoints"]["valid_fraction_on_primary_track"]
        temporal = analysis["keypoints"]["temporal_diagnostics"]
        jump_count = analysis["keypoints"]["jump_diagnostics"][
            "candidate_frame_count"
        ]
        lines.append(
            "| `{id}` | {shoulder:.2%} | {hip:.2%} | {knee:.2%} | {ankle:.2%} | {swap} | {jump} | {missing} 帧 |".format(
                id=item["id"],
                shoulder=min(valid.get("left_shoulder", 0), valid.get("right_shoulder", 0)),
                hip=min(valid.get("left_hip", 0), valid.get("right_hip", 0)),
                knee=min(valid.get("left_knee", 0), valid.get("right_knee", 0)),
                ankle=min(valid.get("left_ankle", 0), valid.get("right_ankle", 0)),
                swap=analysis["keypoints"]["left_right_swap_diagnostics"]["candidate_count"],
                jump=jump_count,
                missing=analysis["tracking"]["longest_primary_pose_missing_frames_over_video"],
            )
        )
    lines.extend(
        [
            "",
            "swap 采用相邻帧左右匹配代价启发式；jump 采用 body-scale 位移与 robust speed MAD 启发式。原始数值、参数、逐点 P50/P95、jitter residual 和最长缺失均在 `reports/pose-model-baseline.json`。这些结果用于新旧模型相对对照，不是真值准确率。",
            "",
            "## 5. 六指标基础特征诊断",
            "",
            "以下是主 Pose Track 全区间、归一化坐标、无事件、无平滑的 P50（括号内为有效率）。`stability_duration_ms` 因缺少事件边界和经真值验证的 stability envelope 保持 unavailable。",
            "",
            "| 特征 | short | difficult | long |",
            "|---|---:|---:|---:|",
        ]
    )
    for feature_name in report["feature_names"]:
        values = []
        for item in report["videos"]:
            result = item["analysis"]["features"]["distributions"][feature_name]
            if result["status"] == "unavailable":
                values.append("unavailable")
            else:
                values.append(
                    f"{_fmt(result['p50'])} ({float(result['valid_fraction']):.1%})"
                )
        lines.append(f"| `{feature_name}` | " + " | ".join(values) + " |")
    lines.extend(
        [
            "",
            "## 6. 人工真值状态",
            "",
            "- 当前没有肩、髋、膝、踝人工校正点，因此关键点 MAE/P95、PCK 和事件关键帧误差均为 `ground_truth_required`，未写 0。",
            f"- 已生成待标注清单：`data/annotations/pose-baseline-labeling-manifest.json`，共 {report['ground_truth']['annotation_sample_count']} 个候选帧。",
            "- 清单覆盖低置信、跳点候选和时间均匀样本；缺失点必须使用 `null + visibility/reason`，禁止 `[0,0]` 填充。",
            "- 未来模型不能用自身预测或另一模型未经人工确认的输出作为真值。",
            "",
            "## 7. 当前阻断项",
            "",
            "1. 尚无人工关键点真值，不能决定 RTMPose 是否降低网球关键点误差。",
            "2. 尚无稳定主球员身份层，最长 Pose Track 不能代表业务身份准确。",
            "3. 尚无 FS01/FS02/FS09 事件边界，当前特征分布不是事件内评分特征。",
            "4. 当前 Pose 与 Detect 共处 `Yolo26Perception`，加载时间和显存还不能精确拆分到单独 Backend。",
            "5. 当前环境未安装 ONNX Runtime/TensorRT/MMPose；下一里程碑先完成 Backend 接口与 YOLO 适配，再隔离 RTMPose runtime。",
            "",
            "## 8. 复现命令",
            "",
            "```powershell",
            "$env:PYTHONPATH = \"$PWD\\src\"",
            ".\\.venv\\Scripts\\python.exe .\\scripts\\build_pose_baseline.py --root . --benchmark",
            ".\\scripts\\run_tests.ps1",
            "```",
            "",
            "机器报告保留模型/视频/frames/summary 哈希、所有分布和性能采样细节。",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Build RallyMate YOLO-Pose baseline")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--benchmark", action="store_true")
    parser.add_argument("--samples-per-video", type=int, default=60)
    args = parser.parse_args()
    root = args.root.resolve()
    specs = _specs(root)
    for spec in specs:
        for key in ("video", "frames", "summary"):
            if not Path(spec[key]).exists():
                raise FileNotFoundError(f"missing {key}: {spec[key]}")
    analyses = [
        analyze_pose_artifact(spec["frames"], spec["summary"], spec["video"])
        for spec in specs
    ]
    detect_model = root / "models" / "yolo26n.pt"
    pose_model = root / "models" / "yolo26n-pose.pt"
    benchmark_path = root / "reports" / "pose-model-baseline-benchmark.json"
    if args.benchmark:
        performance = benchmark_yolo_pose(
            specs,
            detect_model,
            pose_model,
            sample_count_per_video=args.samples_per_video,
        )
        benchmark_path.parent.mkdir(parents=True, exist_ok=True)
        benchmark_path.write_text(
            json.dumps(performance, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    elif benchmark_path.exists():
        performance = json.loads(benchmark_path.read_text(encoding="utf-8"))
    else:
        raise RuntimeError("use --benchmark once or provide cached benchmark report")
    annotation_manifest = _annotation_manifest(root, analyses)
    annotation_path = root / "data" / "annotations" / "pose-baseline-labeling-manifest.json"
    annotation_path.parent.mkdir(parents=True, exist_ok=True)
    annotation_path.write_text(
        json.dumps(annotation_manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    report = {
        "schema_version": "0.1.0",
        "report_version": "yolo-pose-baseline-2026-08-13.1",
        "analyzer_version": BASELINE_ANALYZER_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "baseline_frozen_ground_truth_required",
        "models": {
            "detect": {"path": str(detect_model), "sha256": sha256_file(detect_model)},
            "pose": {"path": str(pose_model), "sha256": sha256_file(pose_model)},
        },
        "environment": {
            "python": platform.python_version(),
            "torch": torch.__version__,
            "ultralytics": ultralytics.__version__,
            "opencv": cv2.__version__,
            "numpy": np.__version__,
            "cuda_available": bool(torch.cuda.is_available()),
            "cuda_device_name": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
        },
        "protocol": {
            "keypoint_confidence_min": 0.25,
            "frame_stride": 1,
            "pose_input_size": 640,
            "max_players": 2,
            "benchmark_samples_per_video": args.samples_per_video,
            "benchmark_source": "existing_player_ROIs_from_fixed_frames_jsonl",
        },
        "feature_names": list(analyses[0]["features"]["distributions"]),
        "videos": [
            {"id": spec["id"], "role": spec["role"], "analysis": analysis}
            for spec, analysis in zip(specs, analyses)
        ],
        "performance_benchmark": performance,
        "ground_truth": {
            "status": "ground_truth_required",
            "annotation_manifest": str(annotation_path),
            "annotation_manifest_sha256": sha256_file(annotation_path),
            "annotation_sample_count": len(annotation_manifest["samples"]),
            "keypoint_mae": None,
            "keypoint_p95": None,
            "pck": None,
        },
        "blockers": [
            "manual_keypoint_ground_truth_missing",
            "stable_primary_player_identity_not_implemented",
            "event_segmentation_not_implemented",
            "versioned_event_feature_library_not_implemented",
        ],
    }
    report_path = root / "reports" / "pose-model-baseline.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    doc_path = root / "docs" / "POSE_MODEL_BASELINE.md"
    doc_path.write_text(_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "report": str(report_path),
                "document": str(doc_path),
                "annotation_manifest": str(annotation_path),
                "videos": len(analyses),
                "annotation_samples": len(annotation_manifest["samples"]),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
