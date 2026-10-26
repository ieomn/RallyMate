"""Read-only audit of same-manifest pose benchmark ledgers (no inference).

Raw image geometry and time/subject bindings are independently checked before
any comparison. Temporal motion includes real player movement and model error;
it is not ground-truth jitter, anatomical accuracy, or a technical grade.
"""
from __future__ import annotations

import argparse
from collections import Counter
from fractions import Fraction
import hashlib
import itertools
import json
import math
from pathlib import Path
import statistics
from types import SimpleNamespace

from rallymate_vision.sam3d_evaluation import (
    RTMPOSE_RAW_SCORE_SEMANTICS, RTMPOSE_SCORE_NORMALIZATION, normalized_baseline_score,
)

BODY = ("nose", "left_eye", "right_eye", "left_ear", "right_ear", "left_shoulder", "right_shoulder",
        "left_elbow", "right_elbow", "left_wrist", "right_wrist", "left_hip", "right_hip",
        "left_knee", "right_knee", "left_ankle", "right_ankle")
BINDING = ("video_sha256", "frame_index", "processed_index", "pts", "time_base_num", "time_base_den", "timestamp_ms",
           "person_track_id", "selection_epoch", "camera_reference_epoch", "image_size", "bbox_px", "rgb_sha256")
EPOCH = ("video_sha256", "person_track_id", "selection_epoch", "camera_reference_epoch")
LIMITATIONS = [
    "同一轨迹编号、人物框和输入像素仅验证输入绑定，不证明真实人物身份或无跟踪错误。",
    "SAM没有关节置信度或可见性；在画面内的预测坐标仅为模型估计，不能当作已验证可见关节。",
    "显式RTMPose原生响应按既有adapter裁剪到[0,1]后使用>=0.35门槛，raw值保留；未知分数仍须在[0,1]内。该分数不是校准概率，覆盖率不能直接排行准确率。",
    "位移、速度和三帧线性残差同时包含真实动作及估计误差；没有人工真值不能称为抖动误差或识别准确率。",
    "本审计不确定Word正式阶段，不生成技术分或等级，也不因推理成功就认定全部指标可测。",
    "各模型all_joint_temporal及躯干可用帧分母不同，不能直接排行稳定度；paired三帧残差才使用同关节同三帧交集及共同图像对角线。",
    "结果仅适用于manifest声明的请求范围；90帧18–21秒结果不代表完整97秒视频。总请求含skipped，时序不跨人物/selection epoch/机位/缺帧。",
]


def finite(value):
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        return False


def stats(values):
    values = sorted(value for value in values if finite(value))
    if not values:
        return {"count": 0, "mean": None, "median": None, "p95": None, "max": None}
    # Explicit linear interpolation, unlike nearest-rank report conventions.
    position = .95 * (len(values) - 1)
    low, high = math.floor(position), math.ceil(position)
    return {"count": len(values), "mean": statistics.fmean(values), "median": statistics.median(values),
            "p95": values[low] + (values[high] - values[low]) * (position - low), "max": values[-1]}


def source_time(row):
    values = [row.get(key) for key in ("pts", "time_base_num", "time_base_den")]
    if any(not isinstance(value, int) or isinstance(value, bool) for value in values) or values[0] < 0 or min(values[1:]) <= 0:
        return None
    return Fraction(values[0] * values[1], values[2])


def raw_points(row, *, apply_confidence=True):
    """Exact common anatomical joints only; do not clip off-image geometry."""
    points = row.get("keypoints_2d", {})
    scores = row.get("keypoint_scores")
    valid, counts = {}, Counter()
    dimensions = row.get("image_size")
    if not isinstance(dimensions, (tuple, list)) or len(dimensions) != 2 or not all(
            isinstance(value, int) and not isinstance(value, bool) and value > 0 for value in dimensions):
        return {}, {"invalid_image_dimensions": len(BODY)}
    width, height = dimensions
    for name in BODY:
        point = points.get(name) if isinstance(points, dict) else None
        if not isinstance(point, (list, tuple)) or len(point) != 2 or not all(finite(v) for v in point):
            counts["missing_or_nonfinite"] += 1
            continue
        counts["finite"] += 1
        if not 0 <= point[0] < width or not 0 <= point[1] < height:
            counts["out_of_frame"] += 1
            continue
        counts["in_frame"] += 1
        score = scores.get(name) if isinstance(scores, dict) else None
        if apply_confidence and row.get("model_kind") == "rtmpose":
            raw_response = row.get("confidence_semantics") == RTMPOSE_RAW_SCORE_SEMANTICS
            normalized_score = normalized_baseline_score(score, raw_rtmpose_response=raw_response)
            if normalized_score is None or normalized_score < .35:
                counts["confidence_gate_rejected"] += 1
                continue
            if raw_response and score > 1:
                counts["declared_raw_response_above_one_eligible"] += 1
        valid[name] = tuple(point)
    return valid, dict(counts)


def normalized_geometry(points):
    if any(name not in points for name in ("left_shoulder", "right_shoulder", "left_hip", "right_hip")):
        return None
    def middle(a, b):
        return ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
    center = middle(points["left_shoulder"], points["right_shoulder"])
    hip = middle(points["left_hip"], points["right_hip"])
    scale = max(math.dist(center, hip), math.dist(points["left_shoulder"], points["right_shoulder"]) * .75)
    if scale < 12:
        return None
    return {"scale_px": scale, "center_px": center,
            "points": {name: ((point[0] - center[0]) / scale, (point[1] - center[1]) / scale) for name, point in points.items()}}


def continuous(a, b, stride=1):
    ta, tb = source_time(a), source_time(b)
    return (ta is not None and tb is not None and 0 < tb - ta <= Fraction(160, 1000)
            and b["frame_index"] - a["frame_index"] == stride
            and a.get("image_size") == b.get("image_size")
            and all(a[key] == b[key] for key in EPOCH))


def decoder_mode_valid(trace, mode):
    if not isinstance(trace, dict) or trace.get("requested_mode") != mode or trace.get("mode_assertion_passed") is not True:
        return False
    calls = trace.get("observed_decoder_calls")
    if not isinstance(calls, dict) or type(calls.get("body")) is not int or type(calls.get("hand")) is not int:
        return False
    if trace.get("full_hand_boxes_returned") is not (mode == "full"):
        return False
    return (mode == "body" and calls == {"body": 1, "hand": 0}
            or mode == "full" and calls["body"] >= 1 and calls["hand"] == 2)


def manifest_scope(manifest):
    requests = manifest.get("requests", [])
    segments = []
    previous = None
    for row in requests:
        state = {key: row.get(key) for key in (*EPOCH, "request_status", "request_reason")}
        if previous is None or segments[-1]["state"] != state or not continuous(previous,row,manifest.get("protocol",{}).get("stride",1)):
            segments.append({"first_frame":row["frame_index"],"last_frame":row["frame_index"],"count":0,
                             "start_ms":row.get("timestamp_ms"),"end_ms":row.get("timestamp_ms"),"state":state})
        segments[-1].update(last_frame=row["frame_index"],end_ms=row.get("timestamp_ms"),count=segments[-1]["count"]+1)
        previous = row
    return {"total_requested_frames":len(requests),
            "request_statuses":dict(Counter(row.get("request_status","unspecified") for row in requests)),
            "skip_reasons":dict(Counter(str(row.get("request_reason") or "unspecified") for row in requests if row.get("request_status")=="skipped")),
            "source_track_ids":sorted({row.get("person_track_id") for row in requests if row.get("person_track_id") is not None},key=str),
            "segments":segments,
            "scope":"manifest_requests_only_not_evidence_of_executed_inference_or_correct_person_identity"}


def audit_ledger(manifest, records, *, report=None, manifest_sha256=None):
    requests = manifest.get("requests", [])
    expected = {row["frame_index"]: row for row in requests}
    counts = Counter(row.get("frame_index") for row in records)
    issues = []
    for a, b in zip(records, records[1:]):
        if source_time(a) is not None and source_time(b) is not None and source_time(b) <= source_time(a):
            issues.append({"code": "nonmonotonic_ledger_pts", "frame_index": b.get("frame_index")})
    if len(expected) != len(requests):
        issues.append({"code": "duplicate_manifest_frame"})
    for frame_index, count in counts.items():
        if count != 1:
            issues.append({"code": "duplicate_ledger_frame", "frame_index": frame_index, "count": count})
    for index in sorted(set(expected) - set(counts)):
        issues.append({"code": "requested_frame_missing", "frame_index": index})
    selected, geometry, per_frame, joint_counts, native_counts = {}, {}, [], Counter(), Counter()
    for row in records:
        index = row.get("frame_index")
        if index not in expected:
            issues.append({"code": "unexpected_frame", "frame_index": index})
            continue
        mismatches = [key for key in BINDING if key not in row or row[key] != expected[index].get(key)]
        timestamp = source_time(row)
        if mismatches:
            issues.append({"code": "source_binding_mismatch", "frame_index": index, "fields": mismatches})
        if timestamp is None or not finite(row.get("timestamp_ms")) or abs(float(timestamp * 1000) - row["timestamp_ms"]) > .51:
            issues.append({"code": "invalid_pts_time", "frame_index": index})
            continue
        if mismatches or counts[index] != 1:
            continue
        if expected[index].get("request_status") == "skipped":
            if row.get("status") != "skipped":
                issues.append({"code": "manifest_skip_not_preserved", "frame_index": index})
            continue
        if row.get("status") != "ok":
            continue
        if row.get("coordinate_system_2d") != "image_pixels_x_right_y_down":
            issues.append({"code": "wrong_2d_units", "frame_index": index})
            continue
        if row.get("model_kind") == "sam3d" and (row.get("confidence_semantics") != "not_provided_by_sam3d"
                or "confidence" not in row or row["confidence"] is not None or row.get("keypoint_scores") is not None):
            issues.append({"code": "fabricated_sam_confidence", "frame_index": index})
            continue
        if row.get("model_kind") not in {"sam3d", "rtmpose"}:
            issues.append({"code": "unknown_model_kind", "frame_index": index})
            continue
        points, point_counts = raw_points(row)
        width, height = row["image_size"]
        for point in row.get("keypoints_2d", {}).values():
            native_counts["total"] += 1
            if not isinstance(point, (tuple, list)) or len(point) != 2 or not all(finite(value) for value in point):
                native_counts["missing_or_nonfinite"] += 1
            else:
                native_counts["finite"] += 1
                native_counts["in_frame" if 0 <= point[0] < width and 0 <= point[1] < height else "out_of_frame"] += 1
        normal = normalized_geometry(points)
        selected[index] = row
        geometry[index] = normal
        joint_counts.update(points.keys())
        per_frame.append({"frame_index": index, "timestamp_ms": row["timestamp_ms"], "raw_common_joint_counts": point_counts,
                          "eligible_2d_joint_count": len(points), "bilateral_torso_normalization": normal is not None})
    indexes = sorted(selected)
    movement, speed, residual, steps, triples = {}, {}, {}, [], []
    for name in BODY:
        movement[name], speed[name], residual[name] = [], [], []
    stride = manifest.get("protocol", {}).get("stride", 1)
    for a_index, b_index in zip(indexes, indexes[1:]):
        a, b = selected[a_index], selected[b_index]
        if not continuous(a, b, stride):
            continue
        ga, gb = geometry[a_index], geometry[b_index]
        if ga is None or gb is None:
            continue
        dt = float(source_time(b) - source_time(a))
        for name in BODY:
            if name not in ga["points"] or name not in gb["points"]:
                continue
            displacement = math.dist(ga["points"][name], gb["points"][name])
            movement[name].append(displacement)
            speed[name].append(displacement / dt)
            steps.append({"joint": name, "from_frame": a_index, "to_frame": b_index, "delta_ms": dt * 1000,
                          "displacement_torso": displacement, "speed_torso_per_s": displacement / dt})
    for a_index, b_index, c_index in zip(indexes, indexes[1:], indexes[2:]):
        a, b, c = (selected[index] for index in (a_index, b_index, c_index))
        if not continuous(a, b, stride) or not continuous(b, c, stride):
            continue
        ga, gb, gc = (geometry[index] for index in (a_index, b_index, c_index))
        if any(value is None for value in (ga, gb, gc)):
            continue
        alpha = float((source_time(b) - source_time(a)) / (source_time(c) - source_time(a)))
        for name in BODY:
            if any(name not in value["points"] for value in (ga, gb, gc)):
                continue
            interpolated = tuple(x + alpha * (y - x) for x, y in zip(ga["points"][name], gc["points"][name]))
            error = math.dist(gb["points"][name], interpolated)
            residual[name].append(error)
            triples.append({"joint": name, "frames": [a_index, b_index, c_index], "linear_residual_torso": error})
    # Prospective geometry eligibility only: no formal stage is invented here.
    from rallymate_scoring.indicator_observations import MEASUREMENTS, _value
    measurement_counts = Counter()
    for normal in geometry.values():
        if normal is None:
            continue
        sample = SimpleNamespace(points=normal["points"], point_confidences={})
        for name, (joints, _unit, kind, _definition) in MEASUREMENTS.items():
            if _value(sample, joints, kind) is not None:
                measurement_counts[name] += 1
    report_checks = {}
    if report is not None:
        actual_statuses = dict(Counter(row.get("status", "missing") for row in records))
        report_checks = {"manifest_sha256_matches": report.get("manifest_sha256") == manifest_sha256,
                         "source_video_sha256_matches": report.get("video", {}).get("sha256") == manifest.get("video", {}).get("sha256"),
                         "requested_count_matches": report.get("counts", {}).get("requested") == len(requests),
                         "status_counts_match": all(report.get("counts", {}).get(key, 0) == value for key, value in actual_statuses.items())}
        if report.get("kind") == "sam3d":
            mode = report.get("model", {}).get("mode")
            report_checks["decoder_mode_evidence_matches"] = mode in {"body", "full"} and all(
                decoder_mode_valid(row.get("execution_trace"), mode) for row in selected.values())
        for key, passed in report_checks.items():
            if not passed:
                issues.append({"code": "report_inconsistent", "check": key})
    raw_counts = Counter()
    for row in per_frame:
        raw_counts.update(row["raw_common_joint_counts"])
    top_steps = sorted(steps, key=lambda row: row["displacement_torso"], reverse=True)[:20]
    return {"binding_status": "passed" if not issues else "failed", "issues": issues,
            "counts": {"manifest_requests": len(requests), "ledger_rows": len(records), "unique_frames": len(counts),
                       "manifest_request_statuses": dict(Counter(row.get("request_status", "unspecified") for row in requests)),
                       "statuses": dict(Counter(row.get("status", "missing") for row in records)),
                       "skipped_reasons": dict(Counter(str(row.get("reason") or row.get("request_reason") or "unspecified")
                                                       for row in records if row.get("status") == "skipped")),
                       "source_valid_ok_frames": len(selected), "bilateral_torso_frames": sum(value is not None for value in geometry.values())},
            "coverage_denominator": {"all_manifest_requests_including_skipped": len(requests),
                                     "source_valid_ok_fraction": len(selected) / len(requests) if requests else None},
            "score_gate": {"threshold": .35, "rtmpose_raw_response_semantics": RTMPOSE_RAW_SCORE_SEMANTICS,
                           "rtmpose_normalization": RTMPOSE_SCORE_NORMALIZATION, "sam_confidence": None},
            "report_checks": report_checks,
            "source_tracks": sorted({row["person_track_id"] for row in selected.values()}),
            "source_selection_epochs": sorted({row["selection_epoch"] for row in selected.values()}),
            "manifest_source_tracks": sorted({row.get("person_track_id") for row in requests if row.get("person_track_id") is not None}, key=str),
            "manifest_selection_epochs": sorted({row.get("selection_epoch") for row in requests if row.get("selection_epoch") is not None}),
            "camera_reference_epochs": sorted({row["camera_reference_epoch"] for row in selected.values()}),
            "actual_identity_correctness_verified": False,
            "common_body_joint_count": len(BODY), "raw_geometry_counts": dict(raw_counts),
            "all_native_keypoint_raw_geometry_counts": dict(native_counts),
            "eligible_joint_frame_counts": dict(joint_counts),
            "measurement_eligible_frame_counts": dict(measurement_counts),
            "measurement_coverage_scope": "per-frame geometry prerequisites only; no formal phase or grade",
            "normalization": "bilateral shoulder midpoint; max(shoulder-midpoint to hip-midpoint,0.75*shoulder-width); minimum12px; no unilateral fallback",
            "temporal_semantics": "body_relative_observed_change_includes_real_motion_not_jitter_error",
            "per_joint_temporal": {name: {"step_torso": stats(movement[name]), "speed_torso_per_s": stats(speed[name]),
                                          "three_frame_linear_residual_torso": stats(residual[name])} for name in BODY},
            "all_joint_temporal": {"step_torso": stats([value for values in movement.values() for value in values]),
                                   "three_frame_linear_residual_torso": stats([value for values in residual.values() for value in values]),
                                   "cross_model_ranking_allowed": False},
            "engineering_wrist_jump_threshold_torso": 1.5,
            "engineering_wrist_jump_events": [row for row in steps if row["joint"].endswith("wrist") and row["displacement_torso"] > 1.5],
            "largest_steps_for_manual_review": top_steps,
            "largest_three_frame_residuals_for_manual_review": sorted(triples, key=lambda row: row["linear_residual_torso"], reverse=True)[:20],
            "inference_ms": stats([row.get("inference_ms") for row in records]),
            "peak_torch_allocated_bytes": max((row.get("peak_memory_bytes", 0) for row in records), default=None),
            "per_frame": per_frame}


def paired_disagreement(first, second, *, eligible_frame_indexes=None, stride=1):
    def unique(records):
        counts = Counter(row.get("frame_index") for row in records)
        return {row["frame_index"]: row for row in records if row.get("status") == "ok" and counts[row.get("frame_index")] == 1}
    left, right = (unique(records) for records in (first, second))
    values, frames, excluded, paired, common_frames = [], 0, 0, {}, {}
    for index in sorted(left.keys() & right.keys()):
        a, b = left[index], right[index]
        if (eligible_frame_indexes is not None and index not in eligible_frame_indexes
                or any(key not in a or key not in b or a[key] != b[key] for key in BINDING)):
            excluded += 1
            continue
        pa, _ = raw_points(a)
        pb, _ = raw_points(b)
        frames += 1
        common_frames[index] = (a, b, pa, pb)
        values.extend(math.dist(pa[name], pb[name]) for name in BODY if name in pa and name in pb)
        ga,gb = normalized_geometry(pa),normalized_geometry(pb)
        if ga is not None and gb is not None:
            paired[index] = (a,b,ga,gb)
    matched_steps = {name: {"first": [], "second": []} for name in BODY}
    for a_index,b_index in zip(sorted(paired),sorted(paired)[1:]):
        a,b = paired[a_index],paired[b_index]
        if not continuous(a[0],b[0],stride) or not continuous(a[1],b[1],stride):
            continue
        for name in BODY:
            if all(name in geometry["points"] for geometry in (a[2],a[3],b[2],b[3])):
                matched_steps[name]["first"].append(math.dist(a[2]["points"][name],b[2]["points"][name]))
                matched_steps[name]["second"].append(math.dist(a[3]["points"][name],b[3]["points"][name]))
    residuals = {name: {"first": [], "second": []} for name in BODY}
    matched_triples = []
    indexes = sorted(common_frames)
    for triple in zip(indexes, indexes[1:], indexes[2:]):
        samples = [common_frames[index] for index in triple]
        if any(not continuous(samples[i][model], samples[i+1][model], stride) for i in (0,1) for model in (0,1)):
            continue
        joints = [name for name in BODY if all(name in sample[model+2] for sample in samples for model in (0,1))]
        if not joints:
            continue
        alpha = float((source_time(samples[1][0]) - source_time(samples[0][0])) /
                      (source_time(samples[2][0]) - source_time(samples[0][0])))
        scale = math.hypot(*samples[0][0]["image_size"])
        for name in joints:
            for model, label in ((0,"first"),(1,"second")):
                a,b,c = (sample[model+2][name] for sample in samples)
                prediction = tuple(x + alpha*(y-x) for x,y in zip(a,c))
                residuals[name][label].append(math.dist(b,prediction)/scale)
        matched_triples.append({"frames":list(triple),"joint_count":len(joints)})
    return {"source_bound_frame_pairs": frames, "excluded_binding_pairs": excluded,
            "common_eligible_joint_disagreement_px": stats(values),
            "matched_normalized_step_torso": {name:{label:stats(values) for label,values in paired_stats.items()}
                for name,paired_stats in matched_steps.items()},
            "matched_temporal_scope": "same adjacent source frames and same valid joint in both models; separate model torso scales; no confidence calibration or ground-truth error",
            "matched_three_frame_residual_image_diagonal": {
                "normalization": "common_source_image_diagonal", "shared_frame_triples":len(matched_triples),
                "shared_joint_triplet_samples":sum(item["joint_count"] for item in matched_triples),
                "samples":matched_triples,
                "per_joint":{name:{label:stats(values) for label,values in pair.items()} for name,pair in residuals.items()},
                "all_joints":{label:stats([value for pair in residuals.values() for value in pair[label]]) for label in ("first","second")},
                "semantics":"same_joint_same_three_source_frames_intersection; real_player_or_camera_acceleration_also_causes_residual; not_accuracy_error"},
            "semantics": "model_disagreement_not_error_against_ground_truth"}


def audit_embedded_baseline(records, baseline):
    indexed = {row["frame_index"]: row for row in baseline}
    mismatches, checked, skipped, unavailable = [], 0, 0, 0
    for row in records:
        if row.get("status") != "ok":
            skipped += 1
            continue
        reference, embedded = indexed.get(row["frame_index"]), row.get("baseline", {})
        if reference is not None and reference.get("status") != "ok" and not embedded:
            unavailable += 1
            continue
        checked += 1
        if (reference is None or not isinstance(embedded,dict) or not isinstance(embedded.get("source_binding"),dict)
                or any(embedded["source_binding"].get(key) != reference.get(key) for key in BINDING)):
            mismatches.append({"frame_index": row["frame_index"], "reason": "baseline_source_binding"})
        elif any(embedded.get(key) != reference.get(key) for key in ("keypoints_2d", "keypoint_scores", "coordinate_system_2d")):
            mismatches.append({"frame_index": row["frame_index"], "reason": "baseline_coordinates_or_scores"})
    return {"input_rows": len(records), "checked_rows": checked, "skipped_non_ok_rows": skipped,
            "independent_baseline_non_ok_rows": unavailable,
            "matches": checked - len(mismatches), "mismatches": mismatches}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--ledger", action="append", required=True, help="label=path.jsonl")
    parser.add_argument("--report", action="append", default=[], help="label=report.json")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    digest = hashlib.sha256(args.manifest.read_bytes()).hexdigest()
    reports = {label: json.loads(Path(path).read_text(encoding="utf-8")) for label, path in (value.split("=", 1) for value in args.report)}
    rows, audits, hashes = {}, {}, {}
    for label, path in (value.split("=", 1) for value in args.ledger):
        source = Path(path)
        rows[label] = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines() if line.strip()]
        hashes[label] = hashlib.sha256(source.read_bytes()).hexdigest()
        audits[label] = audit_ledger(manifest, rows[label], report=reports.get(label), manifest_sha256=digest)
    requests = manifest["requests"]
    if "rtmpose" in rows:
        for label, records in rows.items():
            if label != "rtmpose":
                baseline_check = audit_embedded_baseline(records, rows["rtmpose"])
                audits[label]["embedded_baseline_check"] = baseline_check
                if baseline_check["mismatches"]:
                    audits[label]["binding_status"] = "failed"
                    audits[label]["issues"].append({"code":"embedded_baseline_mismatch", "count":len(baseline_check["mismatches"])})
    boxes_at_border = sum(min(row["bbox_px"][0], row["bbox_px"][1], row["image_size"][0] - row["bbox_px"][2],
                              row["image_size"][1] - row["bbox_px"][3]) < 2 for row in requests if row.get("request_status") == "ready")
    output = {"version": "same-source-pose-benchmark-independent-audit-v1", "gpu_inference_performed": False,
              "ground_truth_used": False, "accuracy_validated": False, "technical_score": None,
              "manifest_sha256": digest, "ledger_sha256": hashes, "video": manifest["video"], "protocol": manifest["protocol"],
              "request_scope":manifest_scope(manifest),
              "source_bbox_border_distance_lt_2px_frames": boxes_at_border, "datasets": audits,
              "paired_comparisons": {f"{a}__{b}": paired_disagreement(rows[a], rows[b], eligible_frame_indexes=
                  {row["frame_index"] for row in audits[a]["per_frame"]} & {row["frame_index"] for row in audits[b]["per_frame"]},
                  stride=manifest.get("protocol",{}).get("stride",1))
                  for a, b in itertools.combinations(rows, 2)},
              "limitations_zh": LIMITATIONS}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "datasets": {key: value["binding_status"] for key, value in audits.items()}}, ensure_ascii=False))
    return 0 if all(value["binding_status"] == "passed" for value in audits.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
