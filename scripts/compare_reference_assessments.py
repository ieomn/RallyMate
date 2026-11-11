"""Render reproducible, same-technique comparisons from freshly rebuilt reports.

The pairs manifest selects actions explicitly, including unsuccessful pairs.
No athlete name or target score enters the numerical comparison.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from rallymate_scoring.action_aggregation import episode_dimension_scores
from rallymate_scoring.reference_comparison import compare_action_to_reference


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def episode_score(report, identifier):
    episode = next(e for e in report["episodes"] if e["episode_id"] == identifier)
    keys = {d["id"]: [m["id"] for m in d["metrics"]] for d in report["dimensions"]}
    dimensions = episode_dimension_scores(episode["metrics"], keys)
    available = [v for v in dimensions.values() if v is not None]
    return {"overall": round(sum(available) / len(available), 2) if available else None,
            "dimensions": dimensions}


def check_visual_review(review, review_id, report, episode_id, *, accepted):
    rows = [row for row in review.get("reviews", []) if row.get("review_id") == review_id]
    if len(rows) != 1:
        raise ValueError("visual_review_identity_invalid")
    row = rows[0]
    sources = [source for source in review.get("sources", []) if source.get("source_id") == row.get("source_id")]
    episode = next(e for e in report["episodes"] if e["episode_id"] == episode_id)
    if (len(sources) != 1 or sources[0].get("video_sha256") != report.get("source_video_sha256")
            or row.get("episode_id") != episode_id
            or any(row.get("source_window_ms", {}).get(key) != episode.get(key)
                   for key in ("start_ms", "peak_ms", "end_ms"))
            or any(row.get("report_subject_binding", {}).get(key) != episode.get(key)
                   for key in ("subject_id", "person_track_id", "source_selection_epoch", "camera_reference_epoch"))):
        raise ValueError("visual_review_source_binding_mismatch")
    status = "three_visible_phases_accepted" if accepted else "excluded_incomplete_stroke"
    if row.get("comparison_status") != status or row.get("complete_stroke_core_visible") is not accepted:
        raise ValueError("visual_review_comparison_status_mismatch")
    if accepted and row.get("visual_technique") != episode.get("motion_pattern", {}).get("profile_id"):
        raise ValueError("visual_review_technique_mismatch")


def build_comparisons(input_dir, manifest):
    receipt_path = input_dir / "validation-results.json"
    receipt_hash = sha(receipt_path)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if receipt.get("all_passed") is not True:
        raise ValueError("source_recomputation_not_passed")
    receipts = {row["sample_id"]: row for row in receipt.get("results", [])}
    if len(receipts) != len(receipt.get("results", [])):
        raise ValueError("duplicate_source_receipt")
    reports = {}
    hashes = {}
    pairs = manifest.get("pairs")
    if not isinstance(pairs, list) or not pairs:
        raise ValueError("comparison_pairs_missing")
    identifiers = [p["id"] for p in pairs]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("duplicate_comparison_id")
    selections = [(p["candidate"]["sample_id"], p["candidate"]["episode_id"],
                   p["reference"]["sample_id"], p["reference"]["episode_id"]) for p in pairs]
    if len(set(selections)) != len(selections):
        raise ValueError("duplicate_comparison_selection")
    for pair in pairs:
        for side in ("candidate", "reference"):
            sample = pair[side]["sample_id"]
            if not isinstance(sample, str) or Path(sample).name != sample or sample in {".", ".."}:
                raise ValueError("invalid_sample_id")
            if sample not in reports:
                path = input_dir / "assessments" / f"{sample}.json"
                reports[sample] = json.loads(path.read_text(encoding="utf-8"))
                hashes[sample] = sha(path)
                source = receipts.get(sample, {})
                if (source.get("assessment_recomputed") is not True
                        or source.get("assessment_path") != f"assessments/{sample}.json"
                        or source.get("assessment_sha256") != hashes[sample]
                        or source.get("video_sha256") != reports[sample].get("source_video_sha256")
                        or source.get("run_id") != reports[sample].get("source_run_id")):
                    raise ValueError("assessment_not_bound_to_recomputation_receipt")
    comparisons = []
    for pair in pairs:
        candidate, reference = pair["candidate"], pair["reference"]
        a, b = reports[candidate["sample_id"]], reports[reference["sample_id"]]
        if manifest.get("source_visual_review"):
            for side, report in (("candidate", a), ("reference", b)):
                check_visual_review(manifest["source_visual_review"], pair["visual_review"][f"{side}_review_id"],
                                    report, pair[side]["episode_id"], accepted=True)
        # Keep rejected pairs in the output. Input corruption is a failed run,
        # not a silently dropped sample or a score of zero.
        result = compare_action_to_reference(a, candidate["episode_id"], b, reference["episode_id"])
        expected_status = pair.get("expected_status", "scored")
        if expected_status not in {"scored", "unavailable"}:
            raise ValueError("expected_status_invalid")
        expected_reasons = pair.get("expected_reason_codes", [])
        comparisons.append({"id": pair["id"], "label": pair["label"],
                            "candidate_sample": candidate["sample_id"],
                            "reference_sample": reference["sample_id"],
                            "phase_labels": {p["phase_id"]: p.get("label", p["phase_id"]) for p in
                                next(e for e in a["episodes"] if e["episode_id"] == candidate["episode_id"])["joint_kinematics"]["phases"]},
                            "candidate_action_score": episode_score(a, candidate["episode_id"]),
                            "reference_action_score": episode_score(b, reference["episode_id"]),
                            "visual_review": pair.get("visual_review"), "comparison": result,
                            "source_review_verified": bool(manifest.get("source_visual_review")),
                            "accepted_as_source_reviewed_comparison": bool(manifest.get("source_visual_review"))
                            and result["status"] == "scored" and result["is_independent_comparison"],
                            "expected_status": expected_status,
                            "expected_status_matched": result["status"] == expected_status
                            and set(expected_reasons) <= set(result["reason_codes"])})
    if manifest.get("source_visual_review"):
        for excluded in manifest.get("excluded_candidates", []):
            check_visual_review(manifest["source_visual_review"], excluded["review_id"],
                                reports[excluded["sample_id"]], excluded["episode_id"], accepted=False)
    for sample, expected in hashes.items():
        if sha(input_dir / "assessments" / f"{sample}.json") != expected:
            raise ValueError("assessment_changed_during_comparison")
    if sha(receipt_path) != receipt_hash:
        raise ValueError("source_receipt_changed_during_comparison")
    return {"version": "reference-comparison-evaluation-v1", "created_at": datetime.now(timezone.utc).isoformat(),
            "source_receipt_sha256": receipt_hash, "assessment_file_sha256": hashes,
            "selection": manifest.get("selection"), "source_attribution": manifest.get("source_attribution"),
            "excluded_candidates": manifest.get("excluded_candidates", []),
            "comparison_count": len(comparisons), "comparisons": comparisons,
            "prior_results_seen": True, "identity_labels_used_in_scoring": False,
            "all_expected_statuses_matched": all(c["expected_status_matched"] for c in comparisons),
            "all_pairs_scored": all(c["comparison"]["status"] == "scored" for c in comparisons)}


def render(payload):
    serialized = json.dumps(payload, ensure_ascii=False, allow_nan=False).replace("<", "\\u003c")
    return PAGE.replace("__DATA__", serialized)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--pairs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads(args.pairs.read_text(encoding="utf-8"))
    selection = manifest.get("selection") or {}
    if selection.get("visual_review_file"):
        review_path = (args.pairs.parent / selection["visual_review_file"]).resolve()
        if sha(review_path) != selection.get("visual_review_sha256"):
            raise ValueError("visual_review_hash_mismatch")
        review = json.loads(review_path.read_text(encoding="utf-8"))
        manifest["source_visual_review"] = review
    payload = build_comparisons(args.input, manifest)
    payload["pairs_manifest_sha256"] = sha(args.pairs)
    payload["comparison_code_sha256"] = sha(ROOT / "src/rallymate_scoring/reference_comparison.py")
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "comparison.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    (args.output / "comparison.html").write_text(render(payload), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "comparison_count": payload["comparison_count"],
                      "all_pairs_scored": payload["all_pairs_scored"],
                      "all_expected_statuses_matched": payload["all_expected_statuses_matched"]}, ensure_ascii=False))
    return 0 if payload["all_expected_statuses_matched"] else 1


PAGE = r'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>德约参考动作 · 真实视频评分对照</title><style>
:root{color-scheme:light;--bg:#f5f5ef;--ink:#183b35;--muted:#5c726a;--line:#d4dfd8;--accent:#16725b}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.65 system-ui,'Microsoft Yahei',sans-serif}main{max-width:1180px;margin:auto;padding:32px 24px 60px}h1{font-size:30px;margin:10px 0}h2{font-size:21px}p{max-width:980px}.muted{color:var(--muted)}select,button{padding:10px;font:inherit;max-width:100%;border:1px solid var(--line);background:white;color:var(--ink)}.cards{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin:22px 0}.card{background:white;border:1px solid var(--line);padding:18px}.card strong{display:block;font-size:30px}.scroll{overflow:auto}table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}td,th{text-align:left;padding:10px;border-bottom:1px solid var(--line);white-space:nowrap}th{font-size:12px;color:var(--muted)}.note{border-left:4px solid var(--accent);padding:12px 18px;background:#e9eee5}.bar{height:5px;background:var(--line);width:120px}.bar i{display:block;height:100%;background:var(--accent)}details{background:white;padding:16px;margin:15px 0;border:1px solid var(--line)}summary{cursor:pointer}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}.evidence img{max-width:100%;height:auto}.evidence a,a{color:var(--accent)}@media(max-width:750px){.cards{grid-template-columns:1fr 1fr}main{padding:20px 14px}}
</style><main><p class="muted">RALLYMATE · 评分重构 · 真实视频复算</p><h1>德约参考动作与测试动作逐阶段对照</h1>
<p>动作分检查当前规则中的可见动作条件；参考匹配分衡量两段同类动作的关节测量接近程度。两者分别展示。参考来自已保存的比赛视频，测试使用原生姿态记录重新评分。</p>
<p class="note">匹配分采用明示的工程容差与权重。职业身份不参与计算；与参考动作自身比较的满分只用于检查数学一致性。阶段表示运动学观察窗，触球与受力不由这些关节数据推定。</p>
<label for="pair">选择配对动作 </label><select id="pair"></select><section class="cards" id="cards"></section><p id="scope" class="muted"></p>
<div id="review" class="evidence"></div><h2>阶段匹配与扣分</h2><div class="scroll"><table><thead><tr><th>阶段</th><th>原始时间（测试 / 参考，秒）</th><th>阶段权重</th><th>测量覆盖</th><th>匹配分</th><th>总扣分贡献</th></tr></thead><tbody id="phases"></tbody></table></div>
<h2>每项测量差值</h2><label for="phase">查看阶段 </label><select id="phase"></select><div class="scroll"><table><thead><tr><th>指标</th><th>单位</th><th>测试值</th><th>参考值</th><th>差值</th><th>总扣分贡献</th></tr></thead><tbody id="metrics"></tbody></table></div>
<h2>复核后排除的候选</h2><div id="excluded"></div><details><summary>测量来源与完整规则</summary><pre id="provenance"></pre></details><details><summary>样本选择与来源许可</summary><pre id="selection"></pre><p>参考原片：<a href="https://commons.wikimedia.org/wiki/File:2012_US_Open_Djokovic_point.webm">2012 US Open Djokovic point</a>，JC（demiace），CC BY-SA 2.0。当前 MP4 由原片转码，时序与裁剪保持原样。</p></details>
</main><script id="data" type="application/json">__DATA__</script><script>
const D=JSON.parse(document.getElementById('data').textContent),$=id=>document.getElementById(id),fmt=n=>typeof n==='number'?n.toFixed(2):'无可比分',percent=n=>typeof n==='number'?(100*n).toFixed(1)+'%':'—';
const names={left_knee_flexion_deg:'左膝屈曲',right_knee_flexion_deg:'右膝屈曲',left_elbow_flexion_deg:'左肘屈曲',right_elbow_flexion_deg:'右肘屈曲',left_shoulder_elevation_deg:'左肩高举',right_shoulder_elevation_deg:'右肩高举',shoulder_hip_separation_deg:'肩髋分离',stance_width_shoulders:'准备站距'};
function text(tag,value){const e=document.createElement(tag);e.textContent=value;return e}function option(select,label,value){const e=text('option',label);e.value=value;select.append(e)}
for(const [i,row]of D.comparisons.entries())option($('pair'),row.label,String(i));
function metrics(){const c=D.comparisons[Number($('pair').value)].comparison,p=c.phases.find(p=>p.phase_id===$('phase').value);$('metrics').replaceChildren();for(const m of p?.metrics||[]){const tr=document.createElement('tr');for(const v of [names[m.metric_id]||m.metric_id,({deg:'°',shoulder_width:'肩宽倍数'})[m.unit]||m.unit,fmt(m.candidate_value),fmt(m.reference_value),fmt(m.delta),fmt(m.weighted_deduction)])tr.append(text('td',v));$('metrics').append(tr)}}
function draw(){const row=D.comparisons[Number($('pair').value)],c=row.comparison;$('cards').replaceChildren();for(const [label,value]of [['测试动作分',fmt(row.candidate_action_score.overall)],['参考动作分',fmt(row.reference_action_score.overall)],['参考匹配分',fmt(c.reference_similarity_score)],['共同测量覆盖',percent(c.coverage)]]){const div=text('div','');div.className='card';div.append(text('span',label),text('strong',value));$('cards').append(div)}
const scopes={independent_video_source:'不同视频来源',same_source_holdout:'同一视频不同动作片段',self_reference:'自身对照，不计入独立验收'},reasons={measurement_hand_mismatch:'测量手不同，本次不配分',technique_profile_mismatch:'技术类型不同，本次不配分',classification_not_independently_reviewed:row.source_review_verified?'原报告自动分类已另经本轮原帧复核':'原报告技术类别来自规则推断',same_source_holdout:'片段间复测，不增加独立来源数',self_reference:'数学一致性检查',insufficient_comparison_coverage:'共同测量覆盖不足',required_comparison_phase_unavailable:'缺少必要阶段测量'};$('scope').textContent=`${scopes[c.comparison_scope]||c.comparison_scope} · ${(c.reason_codes||[]).map(x=>reasons[x]||x).join('；')}`;
$('review').replaceChildren();if(row.visual_review){$('review').append(text('p',row.visual_review.observation||''));for(const item of row.visual_review.images||[]){const img=document.createElement('img');img.src=item.path;img.alt=item.label||'原视频动作帧';$('review').append(img)}}
$('phases').replaceChildren();$('phase').replaceChildren();for(const p of c.phases){const phaseLabel=`${p.phase_id} · ${row.phase_labels[p.phase_id]||''}`;option($('phase'),phaseLabel,p.phase_id);const tr=document.createElement('tr');const ca=p.candidate_window,ra=p.reference_window;const tm=ca&&ra?`${fmt(ca.anchor_ms/1000)} / ${fmt(ra.anchor_ms/1000)}`:'—';for(const v of [phaseLabel,tm,percent(p.weight),percent(p.coverage),fmt(p.score),fmt(p.metrics.reduce((a,m)=>a+(m.weighted_deduction||0),0))])tr.append(text('td',v));$('phases').append(tr)}
$('provenance').textContent=JSON.stringify({candidate:c.candidate,reference:c.reference,policy:c.policy,comparison_code_sha256:D.comparison_code_sha256,source_receipt_sha256:D.source_receipt_sha256},null,2);metrics()}
$('selection').textContent=JSON.stringify({selection:D.selection,source_attribution:D.source_attribution,assessment_file_sha256:D.assessment_file_sha256},null,2);for(const e of D.excluded_candidates||[]){$('excluded').append(text('p',`${e.label}：${e.reason}`));for(const item of e.images||[]){const img=document.createElement('img');img.src=item.path;img.alt=item.label||e.label;img.style.maxWidth='100%';$('excluded').append(img)}}$('pair').onchange=draw;$('phase').onchange=metrics;draw();
</script></html>'''


if __name__ == "__main__":
    raise SystemExit(main())
