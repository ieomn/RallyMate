"""Build a self-contained, reviewable evaluation page from measured JSON results."""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path


NAMES = {
    "development-ordinary-a": "普通 A · 开发集", "development-sharapova": "Sharapova · 开发集",
    "regression-ordinary-b": "普通 B · 既有回归", "regression-nadal": "Nadal · 既有回归",
    "repeat-ordinary-a": "普通 A · 重复推理", "holdout-ordinary-indoor-white": "普通 · 室内白裙",
    "holdout-ordinary-lowres-close": "普通 · 低清近景", "holdout-ordinary-outdoor-side": "普通 · 室外侧拍",
    "holdout-ordinary-night": "普通 · 夜间", "holdout-ordinary-indoor-pink": "普通 · 室内粉衣",
    "holdout-ordinary-user-existing": "普通 · 用户既有视频", "white-360p-crf35": "白裙 · 360p 压缩",
    "white-mirror": "白裙 · 水平镜像", "rola-ncaa-2013": "Rola · 2013 大学赛事",
    "tsitsipas-alcaraz-2023": "Tsitsipas / Alcaraz · 远景",
    "davis-cup-2018-04": "戴维斯杯 · 双打 04", "davis-cup-2018-05": "戴维斯杯 · 双打 05（同源）",
    "mendeley-outdoor-side-view-OCS01": "双机位 · 室外侧视", "mendeley-outdoor-top-view-OCT01": "双机位 · 室外顶视",
    "mendeley-indoor-side-view-ICS01": "双机位 · 室内侧视", "mendeley-indoor-top-view-ICT01": "双机位 · 室内顶视",
    "djokovic-usopen-2012": "Djokovic 比赛来源 · 2012 美网", "schiavone-usopen-2012": "Schiavone 比赛来源 · 2012 美网",
    "federer-cilic-usopen-2012": "Federer / Cilic 训练来源", "dimitrov-tiafoe-usopen-2024": "Dimitrov / Tiafoe 比赛来源",
}


def validate_comparison(before, after):
    """Never present changes in source pixels/poses as a rubric improvement."""
    previous = {r["sample_id"]: r for r in before["samples"]}
    if set(previous) != {r["sample_id"] for r in after["samples"]}:
        raise ValueError("before/after sample sets differ")
    for row in after["samples"]:
        old = previous[row["sample_id"]]
        if any(r.get("status") in {"queued", "running", "pending"} for r in (old, row)):
            raise ValueError(f"pending sample is not a final result: {row['sample_id']}")
        if old.get("evaluation_status") != row.get("evaluation_status"):
            raise ValueError(f"before/after completion status differs: {row['sample_id']}")
        a, b = old.get("source_video_sha256"), row.get("source_video_sha256")
        if (a or b) and str(a).lower() != str(b).lower():
            raise ValueError(f"source video SHA mismatch: {row['sample_id']}")
        if row.get("evaluation_status") == "evaluated" and (not a or not old.get("artifact_sha256") or not row.get("artifact_sha256")):
            raise ValueError(f"evaluated sample requires source and pose artifact SHA: {row['sample_id']}")
        if old.get("artifact_sha256") != row.get("artifact_sha256"):
            raise ValueError(f"pose artifact SHA mismatch: {row['sample_id']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--supplement", type=Path)
    args = parser.parse_args()
    before = json.loads(args.before.read_text(encoding="utf-8"))
    after = json.loads(args.after.read_text(encoding="utf-8"))
    validate_comparison(before, after)
    old = {row["sample_id"]: row for row in before["samples"]}
    keys = ("sample_id", "job_id", "source_group", "split", "evaluation_label", "source_video_sha256", "evaluation_status",
            "status", "score", "episodes", "dimension_scores", "metric_scores", "metric_values", "families", "arms", "reason",
            "rejection_reason", "checks", "variants", "timings", "strengths", "improvements", "source_url", "source_license",
            "media", "video", "include_in_distribution", "pre_score_observations", "verified_identity", "paired_reference_job", "candidate_accounting",
            "artifact_sha256", "decoder_time_bases", "first_selected_decoder_pts", "last_selected_decoder_pts",
            "metric_observation_counts", "episode_quality_reason_counts", "evaluation_validity", "invalid_reason", "quality_warning")
    rows = []
    for item in after["samples"]:
        row = {key: item[key] for key in keys if key in item}
        row["name"] = NAMES.get(item["sample_id"], item.get("display_name", item["sample_id"]))
        row["before"] = {key: old.get(item["sample_id"], {}).get(key) for key in ("score", "episodes", "dimension_scores", "metric_scores", "timings")}
        rows.append(row)
    payload = {"before_distribution": before["distributions"], "after_distribution": after["distributions"],
               "rubric_version": after["rubric_version"], "engine_sha256": after["engine_sha256"],
               "manifest_sha256": after["manifest_sha256"], "created_at_utc": after["created_at_utc"], "samples": rows,
               "supplement": json.loads(args.supplement.read_text(encoding="utf-8")) if args.supplement else {}}
    serialized = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c")
    page = TEMPLATE.replace("__DATA__", serialized).replace("__VERSION__", html.escape(after["rubric_version"]))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(page, encoding="utf-8")
    args.output.with_suffix(".json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(args.output)


TEMPLATE = r'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>RallyMate · 评分验证 2026-10-26</title>
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<style>
:root{color-scheme:light;--ink:#1e302b;--muted:#5e6d67;--line:#d6ded7;--accent:#366a4d;--paper:#f5f6f0;--wash:#e8ede3;--warn:#80572b}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:15px/1.65 system-ui,-apple-system,"Microsoft Yahei",sans-serif}main{max-width:1200px;margin:auto;padding:36px 24px 80px}header{border-bottom:1px solid var(--line);padding-bottom:24px}.eyebrow{color:var(--accent);letter-spacing:.12em;font-size:12px;font-weight:700}h1{font-size:30px;line-height:1.3;margin:10px 0}h2{font-size:21px;margin:24px 0 12px}h3{font-size:16px;margin:16px 0 4px}p{margin:8px 0}a{color:var(--accent)}.lede{max-width:850px;color:var(--muted)}.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:20px;padding:24px 0;border-bottom:1px solid var(--line)}.stat strong{display:block;font-size:27px;font-weight:650}.stat span,.muted{color:var(--muted);font-size:13px}.note{background:var(--wash);padding:18px 20px;margin:24px 0;border-left:3px solid var(--accent)}.toolbar{display:flex;gap:12px;flex-wrap:wrap;align-items:center;margin:16px 0}select,button{font:inherit;border:1px solid var(--line);background:white;color:var(--ink);padding:7px 12px;border-radius:4px}button{cursor:pointer}button:hover{background:var(--wash)}.table-scroll{overflow:auto}table{width:100%;border-collapse:collapse;white-space:nowrap}th,td{text-align:left;padding:12px;border-bottom:1px solid var(--line)}th{font-size:12px;color:var(--muted);font-weight:600}td:first-child{white-space:normal;min-width:190px}.number{font-variant-numeric:tabular-nums;font-weight:650}.bar{height:4px;background:var(--line);min-width:75px}.bar i{display:block;height:100%;background:var(--accent)}.warn{color:var(--warn)}.detail{border:1px solid var(--line);padding:24px;margin-top:24px;background:white}.detail[hidden]{display:none}.two{display:grid;grid-template-columns:1fr 1fr;gap:28px}.detail dl{display:grid;grid-template-columns:auto 1fr;gap:4px 14px;font-size:13px}.detail dd{margin:0;overflow-wrap:anywhere}.detail dt{color:var(--muted)}details{margin-top:20px;border-top:1px solid var(--line);padding-top:12px}summary{cursor:pointer;font-weight:600}.metric-list{font-size:13px}.footer{margin-top:28px;font-size:12px;color:var(--muted);overflow-wrap:anywhere}.pill{display:inline-block;font-size:11px;padding:1px 6px;background:var(--wash);margin-left:6px}.method{display:grid;grid-template-columns:1fr 1fr;gap:30px}.method p{color:var(--muted)}ul{padding-left:22px}.unscored{color:var(--warn)}@media(max-width:700px){main{padding:22px 16px 48px}h1{font-size:25px}.stats{grid-template-columns:1fr 1fr;gap:12px}.two,.method{grid-template-columns:1fr}.detail{padding:16px}th,td{padding:9px}.stat strong{font-size:23px}}
</style><main><header><div class="eyebrow">RALLYMATE / 真实样本验证 / 2026.10.26</div><h1>让分数对应动作证据</h1><p class="lede">旧版与新版使用相同保存姿态。新增来源在查看评分前划为留出集；失败片段、同源复测和画质变体全部保留。这里的分数描述本段可见动作，不代表竞技等级、真实发力或球路效果。</p></header>
<section class="stats" id="stats"></section><section class="note" id="conclusions"></section>
<h2>每份样本的结果</h2><p class="muted">点击“查看分析”可看五维变化、主要动作结论和具体训练。分布以来源组等权计算；相邻片段和画质变体不增加独立样本数。</p>
<div class="toolbar"><label>数据划分 <select id="split"><option value="all">全部记录</option><option value="holdout">新增留出</option><option value="development">开发集</option><option value="regression">既有回归</option><option value="robustness">重复 / 鲁棒性</option></select></label><label>来源标签 <select id="label"><option value="all">全部</option><option value="ordinary">普通用户</option><option value="professional">职业来源</option><option value="unknown">身份未定 / 精英参考</option></select></label><span class="muted" id="count"></span></div>
<div class="table-scroll"><table><thead><tr><th>样本</th><th>划分</th><th>旧版</th><th>新版</th><th>动作数</th><th>结果分析</th></tr></thead><tbody id="rows"></tbody></table></div>
<section class="detail" id="detail" hidden></section>
<h2>什么得到验证，什么仍然不足</h2><div class="method"><section><h3>评分变化的依据</h3><p>减少“达到一次最低姿态就全段满分”的影响，加入同一条腿的伸展连贯性与随挥速度衔接。保留五维等权及动作间等权；不使用职业身份、姿态置信度或未测球速作为加分项。</p><h3>测试覆盖</h3><p id="verification"></p><p>坐标变换不变性只验证计算几何。真实视频压缩、镜像及双机位会重新经过人体估计，结果在下方单列，不能用数学不变性替代画面鲁棒性。</p></section><section><h3>解读限制</h3><p>样本量小、镜头不匹配，职业片段也可能因人物太小、遮挡或动作不完整而无法评价。普通用户标签来自用户说明，未以画面或排名猜测身份。不能将本轮排序当作竞技能力分类准确率。</p><h3>真实推理成本</h3><p id="performance"></p></section></div>
<h2>重复性与画面条件</h2><div id="robustness"></div>
<details><summary>来源、许可证与复现信息</summary><div id="sources"></div><p class="muted">私人视频保持在本机，不随评测报告发布。公开素材按链接中的许可记录作者和改动。阈值仍属于工程规则，尚未完成教练盲标校准。</p><div class="footer" id="provenance"></div></details>
<div class="footer">__VERSION__ · 评分评测页为一次冻结结果快照。打开单份结果可查看当前服务版本。</div></main>
<script id="data" type="application/json">__DATA__</script><script>
const D=JSON.parse(document.getElementById('data').textContent),$=s=>document.querySelector(s);
const text=(tag,value,cls)=>{const x=document.createElement(tag);x.textContent=value;if(cls)x.className=cls;return x};
const invalid=r=>r.evaluation_validity&&r.evaluation_validity!=='valid';
const fmt=x=>typeof x==='number'?x.toFixed(2):'未评分';
const splits={development:'开发',regression:'回归',holdout:'留出',robustness:'重复 / 画面'};
const dims={movement:'移动支撑',preparation:'准备引拍',coordination:'动作协调',striking:'击球姿态',follow_through:'随挥恢复'};
const measured=D.samples.filter(r=>typeof r.score==='number'),ind=D.after_distribution.all;
for(const [v,k]of [[D.samples.length,'评测记录（包含失败与重复）'],[ind.groups,'可评分来源组'],[measured.reduce((a,r)=>a+(r.episodes||0),0),'模型检出动作（含重复 / 无效）'],[`${D.before_distribution.all.at_least_90} → ${ind.at_least_90}`,'90分以上来源组 · 旧 → 新']]){const e=text('div','','stat');e.append(text('strong',String(v)),text('span',k));$('#stats').append(e)}
const supplement=D.supplement||{};
for(const p of supplement.conclusions||['本页如实保留分数接近、排序倒置和无法评分的结果；职业身份不会触发奖励。','工程评分有改进，不等于已证明能稳定区分普通用户与职业球员。'])$('#conclusions').append(text('p',p));
function render(){const list=D.samples.filter(r=>($('#split').value==='all'||r.split===$('#split').value)&&($('#label').value==='all'||r.evaluation_label===$('#label').value));$('#rows').replaceChildren();$('#count').textContent=`${list.length} 条记录`;for(const r of list){const tr=document.createElement('tr');const name=text('td',r.name);if(!r.include_in_distribution)name.append(text('span','不增独立数','pill'));tr.append(name,text('td',splits[r.split]||r.split),text('td',invalid(r)?'测量无效':fmt(r.before.score),invalid(r)?'unscored':'number'));const score=text('td',invalid(r)?'测量无效':fmt(r.score),r.score==null||invalid(r)?'unscored':'number');if(r.score!=null&&!invalid(r)){const bar=text('div','','bar'),i=document.createElement('i');i.style.width=`${r.score}%`;bar.append(i);score.append(bar)}tr.append(score,text('td',String(r.episodes??0)));const td=document.createElement('td'),b=text('button','查看分析');b.onclick=()=>detail(r);td.append(b);tr.append(td);$('#rows').append(tr)}}
function detail(r){const box=$('#detail');box.hidden=false;box.replaceChildren(text('h2',r.name),text('p',`旧版 ${fmt(r.before.score)} → 新版 ${fmt(r.score)} · ${r.episodes||0} 个模型检出片段`));if(invalid(r))box.append(text('p',`测量有效性核验未通过：${r.invalid_reason||'选中人物不是来源所指球员'}。上面的分数为原始引擎输出，不计入该球员的有效评分或分组排序。`,'warn'));if(r.job_id){const a=text('a','打开完整动作结果与回放 ↗');a.href='/?job='+encodeURIComponent(r.job_id);a.target='_blank';a.rel='noopener';box.append(a)}const two=text('div','','two'),left=document.createElement('section'),right=document.createElement('section');left.append(text('h3','五维变化'));for(const [id,label]of Object.entries(dims))left.append(text('p',`${label}：${fmt(r.before.dimension_scores?.[id])} → ${fmt(r.dimension_scores?.[id])}`));right.append(text('h3',r.score==null?'未评分原因':'主要动作结论'));if(r.score==null)right.append(text('p',r.rejection_reason||r.reason||'当前来源没有可可靠评价的完整动作。'));for(const s of (invalid(r)?[]:r.strengths||[]))right.append(text('p',s.observation));two.append(left,right);box.append(two);for(const a of (invalid(r)?[]:r.improvements||[])){box.append(text('h3',a.title||'下一步练习'));for(const key of ['observation','action','drill','retest'])if(a[key])box.append(text('p',a[key]));if(a.start_ms!=null)box.append(text('p',`对应片段 ${(a.start_ms/1000).toFixed(1)}–${(a.end_ms/1000).toFixed(1)} 秒`,'muted'))}const details=document.createElement('details');details.append(text('summary','原始指标与计算依据'));for(const[id,value]of Object.entries(r.metric_values||{}))details.append(text('p',`${id}：测量值 ${Number(value).toFixed(4)}；得分 ${fmt(r.metric_scores?.[id])}`,'metric-list'));details.append(text('p','输入 SHA-256：'+(r.source_video_sha256||'未生成'),'footer'));box.append(details);box.scrollIntoView({behavior:'smooth',block:'start'})}
$('#split').onchange=render;$('#label').onchange=render;render();
const checked=D.samples.filter(r=>r.checks),variants=checked.flatMap(r=>Object.values(r.variants||{}));$('#verification').textContent=`${checked.length} 份保存姿态做确定性重算与公开构建器核对；${checked.filter(r=>Object.values(r.checks).every(Boolean)).length} 份全部通过。几何变换、静止反事实和错误来源检查：${variants.filter(r=>r.passed).length}/${variants.length} 通过。`;
const times=checked.map(r=>r.timings.pure_cpu_median_seconds).sort((a,b)=>a-b);$('#performance').textContent=supplement.performance_summary||`保存姿态的 CPU 评分中位耗时范围 ${times.length?times[0].toFixed(3):'—'}–${times.length?times.at(-1).toFixed(3):'—'} 秒。此数值不包含视频解码与 GPU 人体推理。`;
for(const row of supplement.robustness||[]){const p=text('p',row);$('#robustness').append(p)}if(!supplement.robustness?.length)$('#robustness').append(text('p','见上表“重复 / 画面”记录。'));
for(const r of D.samples.filter(r=>r.source_url)){const p=document.createElement('p'),a=text('a',r.name);a.href=r.source_url;a.target='_blank';a.rel='noopener';p.append(a,document.createTextNode(` · ${r.source_license?.author||''} · ${r.source_license?.name||''}`));$('#sources').append(p)}
$('#provenance').textContent=`清单 SHA-256：${D.manifest_sha256}；生成时间（UTC）：${D.created_at_utc}；引擎摘要：${JSON.stringify(D.engine_sha256)}`;
</script></html>'''


if __name__ == "__main__":
    main()
