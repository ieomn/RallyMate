#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from rallymate_evaluation.fs09_phase_truth import probe_fs09_phase_review_clip
from rallymate_evaluation.pose_diagnostic_clip_truth import (
    ADJUDICATION_HEADERS,
    COVERAGE_HEADERS,
    PACK_VERSION,
    POSITIVE_HEADERS,
    POSE_EVIDENCE_VERSION,
    build_clip_plan,
    build_video_clip_pose_evidence,
    canonical_sha256,
    compile_clip_truth,
    file_sha256,
    initialize_empty_annotation_csvs,
    json_dump,
    source_binding,
    validate_clip_plan,
    validate_clip_truth_manifest_sources,
    validate_empty_clip_truth_report,
)


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "src" / "rallymate_evaluation" / "assets"


def _render_clips(
    *,
    ffmpeg: Path,
    source_video: Path,
    segments: list[dict],
    fps: float,
    outputs: list[Path],
) -> None:
    if len(segments) != len(outputs) or not segments:
        raise RuntimeError("clip render inputs are inconsistent")
    split_labels = "".join(f"[source{index}]" for index in range(len(segments)))
    filters: list[str] = [f"[0:v]split={len(segments)}{split_labels}"]
    command = [
        str(ffmpeg),
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(source_video),
    ]
    for index, segment in enumerate(segments):
        filters.append(
            f"[source{index}]trim=start_frame={segment['source_start_frame_index']}:"
            f"end_frame={segment['source_end_frame_index'] + 1},"
            f"setpts=N/({fps:.6f}*TB)[clip{index}]"
        )
    command.extend([
        "-filter_complex",
        ";".join(filters),
    ])
    for index, output in enumerate(outputs):
        command.extend(
            [
                "-map",
                f"[clip{index}]",
                "-an",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "19",
                "-pix_fmt",
                "yuv420p",
                "-r",
                f"{fps:.6f}",
                "-g",
                str(max(1, round(fps * 2))),
                "-movflags",
                "+faststart",
                str(output),
            ]
        )
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg clip render failed: {result.stderr.strip()}")


def _safe_segment(segment: dict, *, include_pose_evidence: bool = False) -> dict:
    output = {
        "clip_id": segment["clip_id"],
        "sequence_index": segment["sequence_index"],
        "source_start_frame_index": segment["source_start_frame_index"],
        "source_end_frame_index": segment["source_end_frame_index"],
        "source_timestamps_ms": segment["source_timestamps_ms"],
        "frame_count": segment["frame_count"],
        "reel_start_frame_index": segment["reel_start_frame_index"],
        "reel_end_frame_index": segment["reel_end_frame_index"],
        "media_url": segment["media_url"],
    }
    if include_pose_evidence:
        evidence = segment["adjudication_pose_evidence"]
        output["pose_evidence_url"] = evidence["url"]
        output["pose_evidence_sha256"] = evidence["sha256"]
    return output


def _html(title: str, body: str, bootstrap: dict, script_name: str) -> str:
    encoded = json.dumps(
        bootstrap, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).replace("</", "<\\/")
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title><link rel="stylesheet" href="pose-diagnostic-clip-truth.css"></head><body><main>{body}<script id="bootstrap" type="application/json">{encoded}</script><script src="{script_name}"></script></main></body></html>"""


def _review_html(bootstrap: dict) -> str:
    body = """
<h1>M80 Jump / 左右点交换独立盲标</h1>
<p class="warning">此页面只播放无骨架、无诊断标记、无候选帧提示的连续 H.264 片段。片段仅覆盖模型候选周围窗口，因此可以用于候选 precision 裁决，不能测完整时间线 recall。两名标注者必须分别使用不同 ID 独立完成并分别导出 CSV；不要在标注前打开 adjudicate.html。</p>
<div class="grid"><section><div class="toolbar"><label>视频 <select id="videoSel"></select></label><label>片段 <select id="clipSel"></select></label><button id="prevClip">上一片段</button><button id="nextClip">下一片段</button></div><video id="video" controls playsinline preload="metadata"></video><div class="toolbar"><button id="prevFrame">◀ 上一帧</button><button id="nextFrame">下一帧 ▶</button><strong id="frame"></strong></div><p class="muted">源帧映射来自绑定的 primary timeline；不假设固定 FPS，不显示模型候选位置。</p></section>
<section><fieldset><legend>独立标注者会话</legend><label>annotator ID <input id="annotator" type="text" autocomplete="off"></label><button id="lock">锁定独立会话</button><p id="progress" class="progress"></p></fieldset><fieldset><legend>当前片段完整覆盖</legend><label>诊断类型 <select id="typeSel"></select></label><label>不可观测原因 <input id="nullReason" type="text"></label><label>备注 <input id="coverageNotes" type="text"></label><div class="toolbar"><button id="complete">该片段/类型已完整盲审</button><button id="unobservable">该片段/类型不可观测</button></div><p>状态：<span id="coverageState"></span></p></fieldset><fieldset><legend>添加人工观察到的异常</legend><label>jump joint <input id="joint" type="text" placeholder="left_ankle"></label><label>swap 左/右 joint <input id="leftJoint" type="text" placeholder="left_knee"> <input id="rightJoint" type="text" placeholder="right_knee"></label><label>备注 <input id="positiveNotes" type="text"></label><button id="addPositive">在当前源帧添加人工 positive</button></fieldset><table><thead><tr><th>源帧</th><th>类型</th><th>关节</th><th>操作</th></tr></thead><tbody id="positiveRows"></tbody></table><p><button id="exportCoverage">导出本标注者 coverage CSV</button> <button id="exportPositives">导出本标注者 positives CSV</button></p></section></div>
"""
    return _html(
        "RallyMate M80 Pose 诊断独立盲标",
        body,
        bootstrap,
        "pose-diagnostic-clip-review.js",
    )


def _adjudicate_html(bootstrap: dict) -> str:
    body = """
<h1>M80 Jump / 左右点交换第三方动态骨架裁决</h1>
<p class="warning">只有两名独立标注者分别完成并导出 coverage/positives 后，第三方 reviewer 才能使用此页面。这里会显示密封的模型候选帧、目标关节以及逐帧模型骨架轨迹，因此绝不能作为第一阶段盲标入口。叠加层是待评测模型输出，不是真值；裁决结果仍不会自动改变评分门禁。</p>
<fieldset><legend>导入两名标注者的原始文件</legend><label>coverage CSV（选择两份）<input id="coverageFiles" type="file" accept=".csv" multiple></label><label>positives CSV（选择两份）<input id="positiveFiles" type="file" accept=".csv" multiple></label><label>独立 reviewer ID <input id="reviewer" type="text"></label></fieldset>
<div class="grid"><section><div class="video-stage"><video id="video" controls playsinline preload="metadata"></video><canvas id="poseOverlay" aria-label="模型姿态叠加层"></canvas></div><div class="toolbar"><label><input id="showSkeleton" type="checkbox" checked>完整骨架</label><label><input id="showTrail" type="checkbox" checked>目标轨迹</label><label>置信度 <input id="confidence" type="range" min="0" max="1" step="0.05" value="0.15"> <span id="confidenceValue">0.15</span></label><button id="candidateFrame">回到候选帧</button></div><p id="overlayState" class="muted">正在加载逐帧模型证据…</p><p id="task"></p><p id="sources"></p></section><section><div class="toolbar"><button id="prev">上一候选</button><strong id="counter"></strong><button id="next">下一候选</button></div><label>裁决 <select id="decision"><option value="pending">pending</option><option value="confirmed_true">confirmed_true</option><option value="confirmed_false">confirmed_false</option><option value="unobservable">unobservable</option></select></label><label>不可观测原因 <input id="reason" type="text"></label><label>备注 <textarea id="notes"></textarea></label><button id="save">保存当前裁决</button><p id="progress" class="progress"></p><button id="export">导出 candidate-adjudications.csv</button></section></div>
"""
    return _html(
        "RallyMate M80 Pose 诊断第三方裁决",
        body,
        bootstrap,
        "pose-diagnostic-clip-adjudicate.js",
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build immutable M80 blind jump/swap clip truth pack with adjudication pose evidence"
    )
    parser.add_argument("--queue", type=Path, action="append", required=True)
    parser.add_argument("--m78-audit", type=Path, required=True)
    parser.add_argument("--ffmpeg", type=Path, required=True)
    parser.add_argument(
        "--reuse-media-from",
        type=Path,
        help="Reuse already verified blind H.264 clips when source intervals match exactly",
    )
    parser.add_argument("--output-directory", type=Path, required=True)
    args = parser.parse_args()
    ffmpeg = args.ffmpeg.resolve()
    final = args.output_directory.resolve()
    staging = final.with_name(f".{final.name}.building")
    if final.exists() or staging.exists():
        parser.error("output or staging directory already exists")
    if not ffmpeg.is_file():
        parser.error("ffmpeg executable does not exist")
    reuse_segments: dict[str, dict] = {}
    if args.reuse_media_from is not None:
        reuse_root = args.reuse_media_from.resolve()
        reuse_manifest_path = reuse_root / "manifest.json"
        if not reuse_manifest_path.is_file():
            parser.error("reuse-media-from manifest.json does not exist")
        reuse_manifest = json.loads(reuse_manifest_path.read_text(encoding="utf-8"))
        reuse_segments = {
            segment["clip_id"]: segment
            for video in reuse_manifest["plan"]["videos"]
            for segment in video["segments"]
        }
    plan, sealed = build_clip_plan(
        [value.resolve() for value in args.queue],
        m78_audit_path=args.m78_audit.resolve(),
    )
    validate_clip_plan(plan, sealed)
    staging.mkdir(parents=True)
    published = False
    try:
        media = staging / "media"
        media.mkdir()
        pose_evidence_directory = staging / "pose-evidence"
        pose_evidence_directory.mkdir()
        for video in plan["videos"]:
            clip_outputs: list[Path] = []
            for segment in video["segments"]:
                clip_name = f"{segment['clip_id']}-browser.mp4"
                clip_outputs.append(media / clip_name)
                segment["media_url"] = f"media/{clip_name}"
            if reuse_segments:
                for segment, output in zip(video["segments"], clip_outputs):
                    previous = reuse_segments.get(segment["clip_id"])
                    if previous is None or any(
                        previous.get(field) != segment.get(field)
                        for field in (
                            "source_start_frame_index",
                            "source_end_frame_index",
                            "source_timestamps_ms",
                            "frame_count",
                        )
                    ):
                        raise RuntimeError(
                            f"reused clip source interval mismatch: {segment['clip_id']}"
                        )
                    previous_media = Path(previous["clip_media"]["path"])
                    if file_sha256(previous_media) != previous["clip_media"]["sha256"]:
                        raise RuntimeError(
                            f"reused clip source hash mismatch: {segment['clip_id']}"
                        )
                    shutil.copyfile(previous_media, output)
            else:
                _render_clips(
                    ffmpeg=ffmpeg,
                    source_video=Path(video["source_video"]["path"]),
                    segments=video["segments"],
                    fps=float(video["fps"]),
                    outputs=clip_outputs,
                )
            for segment, clip_output in zip(video["segments"], clip_outputs):
                clip_probe = probe_fs09_phase_review_clip(clip_output)
                if clip_probe["frame_count"] != segment["frame_count"]:
                    raise RuntimeError(
                        f"rendered clip frame count mismatch for {segment['clip_id']}: "
                        f"{clip_probe['frame_count']} != {segment['frame_count']}"
                    )
                segment["clip_media"] = {
                    "path": str((final / "media" / clip_output.name).resolve()),
                    "sha256": file_sha256(clip_output),
                    "probe": clip_probe,
                    "semantics": "blind_candidate_window_without_pose_or_diagnostic_overlay",
                }
            pose_evidence_by_clip = build_video_clip_pose_evidence(video)
            for segment in video["segments"]:
                evidence = pose_evidence_by_clip[segment["clip_id"]]
                evidence_name = f"{segment['clip_id']}-pose-evidence.json"
                evidence_path = pose_evidence_directory / evidence_name
                evidence_path.write_text(json_dump(evidence), encoding="utf-8")
                segment["adjudication_pose_evidence"] = {
                    "path": str(
                        (final / "pose-evidence" / evidence_name).resolve()
                    ),
                    "url": f"pose-evidence/{evidence_name}",
                    "sha256": file_sha256(evidence_path),
                    "canonical_sha256": canonical_sha256(evidence),
                    "frame_count": len(evidence["frames"]),
                    "pose_frame_count": sum(
                        bool(row["pose_present"]) for row in evidence["frames"]
                    ),
                    "keypoint_format": evidence["keypoint_format"],
                    "semantics": "adjudicator_only_model_pose_not_ground_truth",
                }
        sealed_path = staging / "sealed-candidates.json"
        sealed_path.write_text(json_dump(sealed), encoding="utf-8")
        blind_videos = [
            {
                "video_id": video["video_id"],
                "fps": video["fps"],
                "segments": [_safe_segment(segment) for segment in video["segments"]],
            }
            for video in plan["videos"]
        ]
        blind_segment_by_id = {
            segment["clip_id"]: segment
            for video in blind_videos
            for segment in video["segments"]
        }
        adjudication_videos = [
            {
                "video_id": video["video_id"],
                "fps": video["fps"],
                "segments": [
                    _safe_segment(segment, include_pose_evidence=True)
                    for segment in video["segments"]
                ],
            }
            for video in plan["videos"]
        ]
        adjudication_segment_by_id = {
            segment["clip_id"]: segment
            for video in adjudication_videos
            for segment in video["segments"]
        }
        blind_bootstrap = {
            "schema_version": "1.0.0",
            "binding": canonical_sha256(
                {"plan": plan, "blind_candidate_details_included": False}
            ),
            "diagnostic_types": plan["diagnostic_types"],
            "videos": blind_videos,
            "segment_by_id": blind_segment_by_id,
            "total_clip_type_units": plan["counts"]["clips"]
            * len(plan["diagnostic_types"]),
            "coverage_headers": list(COVERAGE_HEADERS),
            "positive_headers": list(POSITIVE_HEADERS),
            "safety": {
                "candidate_task_ids_included": False,
                "candidate_frames_included": False,
                "candidate_joints_included": False,
                "pose_overlay_included": False,
            },
        }
        adjudication_bootstrap = {
            "schema_version": "1.0.0",
            "binding": canonical_sha256(sealed),
            "tasks": sealed["tasks"],
            "videos": adjudication_videos,
            "video_by_id": {
                video["video_id"]: video for video in adjudication_videos
            },
            "segment_by_id": adjudication_segment_by_id,
            "adjudication_headers": list(ADJUDICATION_HEADERS),
            "pose_evidence_version": POSE_EVIDENCE_VERSION,
            "safety": {
                "model_pose_is_ground_truth": False,
                "available_to_blind_review_page": False,
            },
        }
        (staging / "review.html").write_text(
            _review_html(blind_bootstrap), encoding="utf-8"
        )
        (staging / "adjudicate.html").write_text(
            _adjudicate_html(adjudication_bootstrap), encoding="utf-8"
        )
        for asset_name in (
            "pose-diagnostic-clip-truth.css",
            "pose-diagnostic-clip-review.js",
            "pose-diagnostic-clip-adjudicate.js",
        ):
            shutil.copyfile(ASSETS / asset_name, staging / asset_name)
        initialize_empty_annotation_csvs(staging)
        manifest = {
            "schema_version": "1.0.0",
            "pack_version": PACK_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "status": "annotation_required",
            "plan": plan,
            "renderer": {
                "version": "m80-blind-clips-plus-adjudication-overlay-v1.0.0",
                "ffmpeg": source_binding(ffmpeg),
                "encoding": "H.264/yuv420p/no-audio/faststart",
            },
            "artifacts": {
                "sealed_candidates": {
                    "path": str((final / "sealed-candidates.json").resolve()),
                    "sha256": file_sha256(sealed_path),
                    "canonical_sha256": canonical_sha256(sealed),
                },
                "blind_review_html": {
                    "path": str((final / "review.html").resolve()),
                    "sha256": file_sha256(staging / "review.html"),
                },
                "adjudication_html": {
                    "path": str((final / "adjudicate.html").resolve()),
                    "sha256": file_sha256(staging / "adjudicate.html"),
                },
            },
            "truth_contract": {
                "independent_annotators_per_clip_type": 2,
                "independent_reviewer_required": True,
                "candidate_precision_only": True,
                "full_timeline_recall_available": False,
                "partial_annotation_metrics_available": False,
                "adjudication_pose_overlay_required": True,
                "adjudication_pose_overlay_is_ground_truth": False,
            },
            "safety": {
                "manual_truth_present": False,
                "candidate_tasks_are_truth": False,
                "candidate_details_loaded_by_blind_page": False,
                "pose_or_diagnostic_overlay_rendered": False,
                "quality_gate_modified": False,
                "grade_generated": False,
                "threshold_generated": False,
                "accuracy_claim": False,
            },
        }
        (staging / "manifest.json").write_text(json_dump(manifest), encoding="utf-8")
        staging.replace(final)
        published = True
        validate_clip_truth_manifest_sources(manifest)
        report = compile_clip_truth(final)
        validate_empty_clip_truth_report(report)
        compiled = final / "compiled"
        compiled.mkdir()
        (compiled / "evaluation.json").write_text(
            json_dump(report), encoding="utf-8"
        )
    except Exception:
        target = final if published else staging
        if target.exists():
            shutil.rmtree(target)
        raise
    print(
        json.dumps(
            {
                "status": "annotation_required",
                "output": str(final),
                "clips": plan["counts"]["clips"],
                "candidate_tasks": plan["counts"]["candidate_tasks"],
                "unique_affected_indicator_instances": plan["counts"][
                    "unique_affected_indicator_instances"
                ],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
