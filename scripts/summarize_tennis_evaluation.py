"""Summarize real evaluation runs, including lost actions in pixel perturbations."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import statistics

if __package__:
    from .evaluate_tennis_assessment import distributions
else:
    from evaluate_tennis_assessment import distributions


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def action_score(assessment, episode):
    dimensions = []
    for dimension in assessment["dimensions"]:
        values = [episode["metrics"][m["id"]]["score"] for m in dimension["metrics"] if m["id"] in episode["metrics"]]
        if values:
            dimensions.append(statistics.mean(values))
    return statistics.mean(dimensions) if dimensions else None


def paired_actions(reference, variant):
    candidates = []
    for i, a in enumerate(reference["episodes"]):
        for j, b in enumerate(variant["episodes"]):
            intersection = max(0, min(a["end_ms"], b["end_ms"]) - max(a["start_ms"], b["start_ms"]))
            union = max(a["end_ms"], b["end_ms"]) - min(a["start_ms"], b["start_ms"])
            iou = intersection / union if union else 0
            peak_delta = abs(a["peak_ms"] - b["peak_ms"])
            if a["family"] == b["family"] and iou >= .35 and peak_delta <= 500:
                candidates.append((iou, i, j, peak_delta))
    used_a, used_b, matches = set(), set(), []
    for iou, i, j, delta in sorted(candidates, reverse=True):
        if i in used_a or j in used_b:
            continue
        used_a.add(i)
        used_b.add(j)
        a, b = reference["episodes"][i], variant["episodes"][j]
        old, new = action_score(reference, a), action_score(variant, b)
        matches.append({"reference_episode": a["episode_id"], "variant_episode": b["episode_id"],
                        "window_iou": round(iou, 4), "peak_delta_ms": round(delta, 3),
                        "reference_arm": a["active_arm"], "variant_arm": b["active_arm"],
                        "reference_window_ms": [a["start_ms"], a["peak_ms"], a["end_ms"]],
                        "variant_window_ms": [b["start_ms"], b["peak_ms"], b["end_ms"]],
                        "reference_score": round(old, 3), "variant_score": round(new, 3), "delta": round(new - old, 3)})
    return {"method": "one_to_one_greedy_maximum_window_IoU_at_least_0.35_same_family_peak_difference_at_most_500ms",
            "reference_actions": len(reference["episodes"]), "variant_actions": len(variant["episodes"]),
            "matched_actions": len(matches), "unmatched_reference_actions": len(reference["episodes"]) - len(used_a),
            "unmatched_variant_actions": len(variant["episodes"]) - len(used_b), "matches": matches}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--before", type=Path, required=True)
    p.add_argument("--after", type=Path, required=True)
    p.add_argument("--performance", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    before, after, performance = read(args.before), read(args.after), read(args.performance)
    old_by_id = {r["sample_id"]: r for r in before["samples"]}
    by_job = {r.get("job_id"): r for r in after["samples"]}
    report = {"conclusions": [], "robustness": [], "paired_actions": [], "performance": performance}
    for row in after["samples"]:
        reference = by_job.get(row.get("paired_reference_job"))
        if reference and row.get("evaluation_status") == "evaluated" and reference.get("evaluation_status") == "evaluated":
            a = read(args.after.parent / f"{reference['sample_id']}-assessment.json")
            b = read(args.after.parent / f"{row['sample_id']}-assessment.json")
            paired = {"sample_id": row["sample_id"], **paired_actions(a, b)}
            report["paired_actions"].append(paired)
            deltas = [abs(m["delta"]) for m in paired["matches"]]
            changed_arms = sum(m["reference_arm"] != m["variant_arm"] for m in paired["matches"])
            shortened = max((m["reference_window_ms"][2] - m["variant_window_ms"][2] for m in paired["matches"]), default=0)
            report["robustness"].append(
                f"{row['sample_id']}：原片 {paired['reference_actions']} 动作，变体 {paired['variant_actions']} 动作，"
                f"按同一时间窗配对 {paired['matched_actions']} 个；丢失或无法配对 {paired['unmatched_reference_actions']} 个原片动作。"
                + (f"配对动作最大绝对分差 {max(deltas):.2f} 分，挥拍手识别改变 {changed_arms} 个，结束边界最多提前 {max(0, shortened)/1000:.2f} 秒。" if deltas else "没有可比较的同一动作分数。")
                + ("水平镜像预期左右手对调。" if row.get("transform") == "hflip" else "画质变更没有改变真实挥拍手，识别改变属于测量不稳定。"))
    # Short paired camera clips are still real attempts, including rejection.
    camera_pairs = {}
    for row in after["samples"]:
        if row.get("pair_id"):
            camera_pairs.setdefault(row["pair_id"], []).append(row)
    for pair, rows in camera_pairs.items():
        report["robustness"].append(pair + "：" + "；".join(
            f"{r['sample_id']} {r.get('episodes', 0)} 动作 / {r.get('score') if r.get('score') is not None else '未评分'}"
            for r in rows) + "。这是短片完整度与视角覆盖测试，不能据此声称角度不影响评分。")
    valid = [r for r in after["samples"] if r.get("include_in_distribution", True)
             and r.get("evaluation_validity", "valid") == "valid" and r["split"] != "robustness"]
    common, old_saturated, new_saturated = 0, 0, 0
    for row in valid:
        old_file = args.before.parent / f"{row['sample_id']}-assessment.json"
        new_file = args.after.parent / f"{row['sample_id']}-assessment.json"
        if not old_file.is_file() or not new_file.is_file():
            continue
        old_assessment, new_assessment = read(old_file), read(new_file)
        old_episodes = {e["episode_id"]: e for e in old_assessment["episodes"]}
        for episode in new_assessment["episodes"]:
            old_episode = old_episodes.get(episode["episode_id"])
            if old_episode:
                for key in episode["metrics"].keys() & old_episode["metrics"].keys():
                    common += 1
                    old_saturated += old_episode["metrics"][key]["score"] >= 99
                    new_saturated += episode["metrics"][key]["score"] >= 99
    report["common_metric_saturation"] = {"observations": common, "old_at_least_99": old_saturated, "new_at_least_99": new_saturated}
    raw_rows = []
    for row in after["samples"]:
        item = dict(row)
        if item.get("evaluation_validity", "valid") != "valid":
            item["evaluation_validity"] = "valid"
            item["include_in_distribution"] = item["split"] != "robustness"
        raw_rows.append(item)
    report["raw_identity_label_sensitivity"] = distributions(raw_rows)
    old, new = before["distributions"]["all"], after["distributions"]["all"]
    report["conclusions"].append(f"可评分来源组中，90 分以上由 {old['at_least_90']}/{old['groups']} 变为 {new['at_least_90']}/{new['groups']}。"
                                 f"同一动作的 {common} 项共有指标中，99 分以上由 {old_saturated} 项变为 {new_saturated} 项；饱和有所缓解，仍未消除。")
    h = after["distributions"]["holdout"]
    previous_holdout = before["distributions"]["holdout"]
    report["conclusions"].append(f"新增留出尝试 {h['attempted_groups']} 个来源组，{h['groups']} 组有可归属的评分；"
                                 f"其余 {h['unscored_or_invalid_groups']} 组未评分或主目标归属失败，均保留。"
                                 f"有效职业来源组 {h['professional_groups']}，普通来源组 {h['ordinary_groups']}。")
    if h["pairs"]:
        previous_rank = previous_holdout["professional_above_ordinary_pair_fraction"]
        report["conclusions"].append(f"留出集按来源组均分两两比较，职业高于普通的比例从旧版 {previous_rank:.1%} 变为新版 {h['professional_above_ordinary_pair_fraction']:.1%}（{h['pairs']} 对）。"
                                     "这是小样本描述性排序，不是竞技等级识别准确率；未为排序重新调阈值。")
    else:
        report["conclusions"].append("有效职业留出覆盖不足，无法从本轮计算可信的职业/普通分组排序。")
    raw = report["raw_identity_label_sensitivity"]["holdout"]
    if raw["pairs"]:
        report["conclusions"].append(f"作为敏感性检查，若连主目标核验失败的原始分数也按来源标签计入，职业高于普通为 {raw['professional_above_ordinary_pair_fraction']:.1%}（{raw['pairs']} 对）。"
                                     "该结果含混入其他人物的输出，只用于披露剔除的影响，不作为有效球员评价。")
    report["conclusions"].append("分数衡量可见动作的部分质量。普通与职业仍可能重叠；击球姿态等指标仍偏饱和，单目骨架没有提供真实发力、球速、落点或比赛决策证据。")
    report["conclusions"].insert(0, "本轮未通过职业/普通等级区分验证。分数饱和减少，但独立留出排序没有改善；新版仅作为可见动作的试验评分，不用于竞技等级排名。")
    timings = [r["timings"]["pure_cpu_median_seconds"] for r in after["samples"] if r.get("timings")]
    paired_times = [(old_by_id[r["sample_id"]]["timings"]["pure_cpu_median_seconds"], r["timings"]["pure_cpu_median_seconds"])
                    for r in after["samples"] if r.get("timings") and old_by_id.get(r["sample_id"], {}).get("timings")]
    fresh = [r for r in performance if not r.get("reused_saved_pose") and r.get("end_to_end_seconds") is not None]
    report["cpu_paired_timing"] = {"samples": len(paired_times), "median_before_seconds": statistics.median(a for a, _ in paired_times),
                                   "median_after_seconds": statistics.median(b for _, b in paired_times),
                                   "median_increment_seconds": statistics.median(b - a for a, b in paired_times)}
    t = report["cpu_paired_timing"]
    scored_timings = [r["timings"] for r in after["samples"] if r.get("score") is not None]
    report["performance_summary"] = (f"本机 RTX 5070 Ti / Core Ultra 7 265K。保存姿态 CPU 评分耗时中位数范围 {min(timings):.3f}–{max(timings):.3f} 秒；"
        f"同输入旧版/新版的中位耗时 {t['median_before_seconds']:.3f}/{t['median_after_seconds']:.3f} 秒，逐样本增量中位数 {t['median_increment_seconds']:+.3f} 秒。"
        + (f"{len(fresh)} 个新增视频完整分析耗时 {min(r['end_to_end_seconds'] for r in fresh):.1f}–{max(r['end_to_end_seconds'] for r in fresh):.1f} 秒（不含排队，包含解码、推理和产物生成）。" if fresh else "")
        + f"其中 {len(scored_timings)} 份产出分数的计算为 {min(r['pure_cpu_median_seconds'] for r in scored_timings):.3f}–{max(r['pure_cpu_median_seconds'] for r in scored_timings):.3f} 秒；含姿态文件读取的首次构建为 {min(r['uncached_builder_seconds'] for r in scored_timings):.3f}–{max(r['uncached_builder_seconds'] for r in scored_timings):.3f} 秒。"
        + "新增评分本身不调用 GPU 模型；CPU 测量未清除操作系统文件缓存，测量时后台3D预览可能并行运行，微小耗时差不能视为严格性能提升。")
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("conclusions", "robustness", "performance_summary")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
