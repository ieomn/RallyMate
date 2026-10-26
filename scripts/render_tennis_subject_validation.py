"""Render subject-scoped regression evidence without changing scores or labels."""
import argparse
import json
from pathlib import Path

from render_tennis_evaluation_report import NAMES


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--offline', type=Path, required=True)
    parser.add_argument('--gpu', type=Path, required=True)
    parser.add_argument('--gpu-audited', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--coverage', type=Path)
    args = parser.parse_args()
    offline, gpu = read(args.offline), read(args.gpu)
    if isinstance(gpu, dict):
        if gpu.get('all_artifact_hashes_match_files') is not True:
            raise ValueError('GPU artifact hashes have not been verified')
        gpu = gpu['runs']
    audited = read(args.gpu_audited) if args.gpu_audited else {'samples': []}
    by_sample = {row['sample']: row for row in audited['samples']}
    if args.gpu_audited:
        if audited.get('code_changed_during_execution') or set(by_sample) != {row['sample'] for row in gpu}:
            raise ValueError('Audited GPU result set does not match the verified runs')
        for row in gpu:
            if row['source_sha256'].lower() != by_sample[row['sample']]['source_video_sha256'].lower():
                raise ValueError('Audited GPU source identity mismatch')
    if offline.get('code_changed_during_execution') or offline.get('error_count'):
        raise ValueError('Final evaluation requires unchanged code and no execution errors')
    keys = ('sample_id', 'job_id', 'source_group', 'source_video_sha256', 'status', 'score', 'episodes',
            'old_score', 'old_scored_actions', 'candidate_accounting', 'episode_validity', 'subject_scope',
            'pattern_coverage', 'quality_reason_counts', 'selected_frames', 'selected_frames_with_saved_native',
            'selected_frames_missing_saved_native', 'selected_tracks', 'context_actions', 'frames_jsonl_sha256',
            'action_context_sha256', 'input_sha256', 'output_timeline_sha256', 'elapsed_seconds')
    samples = [{**{k: row[k] for k in keys if k in row}, 'name': NAMES.get(row['sample_id'], row['sample_id'])}
               for row in offline['samples']]
    gpu_keys = ('sample', 'job_id', 'original_job_id', 'completed_at', 'duration_seconds', 'source_sha256',
                'frame_count', 'selected_frames', 'native_ok', 'native_track_counts', 'selection_segments',
                'candidate_count', 'candidates_by_family', 'native_inference_seconds', 'artifact_sha256',
                'validation_scope', 'timeline_postprocessing')
    payload = {'schema_version': 'tennis-subject-validation-v1', 'assessment_version': offline['assessment_version'],
               'not_independent_holdout': True, 'identity_labels_used_for_scoring': False,
               'code_sha256': offline.get('code_hashes_at_end'), 'samples': samples,
               'gpu_runs': [{**{k: row[k] for k in gpu_keys if k in row},
                             'audited_score': by_sample.get(row['sample'], {}).get('score'),
                             'audited_episodes': by_sample.get(row['sample'], {}).get('episodes')}
                            for row in gpu],
               'coverage_audit': read(args.coverage) if args.coverage else None}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.with_suffix('.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    embedded = json.dumps(payload, ensure_ascii=False).replace('<', '\\u003c')
    args.output.write_text(TEMPLATE.replace('__DATA__', embedded), encoding='utf-8')
    print(args.output)


TEMPLATE = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>RallyMate · 人物归属与动作模式验证</title><link rel="icon" href="/favicon.svg" type="image/svg+xml">
<style>
:root{color-scheme:light;--paper:#f5f6f0;--ink:#21392e;--muted:#59675f;--line:#cdd6cd;--accent:#366a4d}
*{box-sizing:border-box}body{margin:0;color:var(--ink);background:var(--paper);font:15px/1.65 system-ui,"Microsoft Yahei",sans-serif}main{max-width:1180px;margin:auto;padding:34px 24px 64px}h1{font-size:28px;line-height:1.35}h2{font-size:20px;margin-top:30px}p{max-width:960px}.muted,small{color:var(--muted)}a{color:var(--accent)}header{border-bottom:1px solid var(--line);padding-bottom:20px}.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;padding:24px 0}.stats b{display:block;font-size:27px}.note{padding:18px;background:#e7eddf;border-left:3px solid var(--accent)}.scroll{overflow:auto}table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:10px;border-bottom:1px solid var(--line);vertical-align:top}th{font-size:12px;color:var(--muted)}td:first-child{min-width:170px}button,select{font:inherit;border:1px solid var(--line);padding:6px 11px;background:white;color:var(--ink);cursor:pointer}button:hover{background:#e7eddf}.toolbar{display:flex;gap:12px;align-items:center;margin:16px 0}#detail{margin-top:24px;padding:20px;background:white;border:1px solid var(--line)}#detail[hidden]{display:none}details{padding:10px 0;border-top:1px solid var(--line)}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}code{overflow-wrap:anywhere}@media(max-width:700px){.stats{grid-template-columns:1fr 1fr}main{padding:20px 14px}h1{font-size:23px}}
</style><main><header><small>RallyMate / 2026-10-27 / 可复核的工程验证</small><h1>分清评分对象，再检验动作模式</h1>
<p>本轮把目标锁定提前到姿态推理之前；总分只汇总同一目标的有效片段。逐项显示类型、轨迹与时序要求中已测和未测的部分。</p>
<p class="muted">复用此前 25 条视频作回归，并另外重新执行原视频的检测与 GPU 姿态推理。它们不是新的盲测集；职业／普通标签未进入计分，也没有为了拉开分差修改数值规则。</p></header>
<section class="stats" id="stats"></section>
<div class="note"><strong>如何读结果</strong><p>“未评分”仍可能来自目标无法确认、完整阶段缺失或旧姿态里没有正确目标。其他人在画面中出现，不再自动使整段视频失效。动作模式覆盖率包含未测要求，不能把姿态分当作真实受力、触球质量或竞技等级。</p></div>
<h2>原视频重新推理</h2><div class="scroll"><table><thead><tr><th>样本</th><th>源帧／新姿态帧</th><th>实际推理的轨迹</th><th>候选／有效片段</th><th>核验后分数</th><th>耗时</th></tr></thead><tbody id="gpu"></tbody></table></div>
<p class="muted">此处重新运行了解码、人物检测、相机运动检查、目标锁定、SAM 姿态与候选识别；未生成耗时的网格预览。候选数不等于有效击球数或最终评分数。</p>
<h2>25 条保存姿态的回归</h2><div class="toolbar"><label>显示 <select id="filter"><option value="all">全部样本</option><option value="scored">有评分</option><option value="unscored">未评分</option><option value="missing">正确目标缺旧姿态</option></select></label><span class="muted" id="count"></span></div>
<div class="scroll"><table><thead><tr><th>样本</th><th>旧→新评分片段</th><th>旧→新分数</th><th>已测／适用模式要求</th><th>旧姿态缺失帧</th><th>逐动作核验</th></tr></thead><tbody id="rows"></tbody></table></div><section id="detail" hidden></section>
<h2>已接入与尚未测得</h2><p>按正手、双反、单反、切削、发球及接发球选择适用规则。新增指标包括转体引拍配合、髋肩腕峰时序、双腕运动配合、向前与前下轨迹、发球上下臂衔接、单反前后收拍和阶段连续性。重叠证据使用替换关系，避免重复加权。</p>
<p>方向性指标需要独立的球场／竖直参考和相机补偿；不能拿同一次手腕轨迹反推“正确方向”。具体类型不明确时只使用通用规则。人工核验的类别、持拍手和方向有明确来源绑定，不能当作自动分类准确率。</p>
<p>资料中的真实蹬地力、真实重心、握拍、球拍和触球、来球压力仍未完整测得。完整发球在原片可见却被候选切短的例子仍保留为分段缺陷；没有用人工重分段冒充自动识别成功。</p>
<details><summary>真实类型覆盖审计</summary><pre id="coverage"></pre></details>
<p class="muted"><a href="tennis-score-20261027.json">下载本轮证据 JSON</a> · <a href="tennis-score-20261026.html">查看上一轮冻结报告</a></p></main>
<script>const D=__DATA__;const reasons={candidate_source_or_complete_phase_unverified:'目标来源或完整动作阶段未确认',candidate_lacks_continuous_complete_motion:'连续姿态不足，未覆盖完整动作',candidate_overlap_duplicate:'与另一动作候选重复',reviewed_action_incomplete_or_invalid:'画面复核确认不完整或不是一次击球',different_subject_not_aggregated:'属于其他人，不合并评分',incomplete_serve_preparation_only:'只有发球准备或抛球',incomplete_serve_follow_through_unobserved:'未覆盖发球随挥',preparation_only:'只有准备或引拍',duplicate_recovery_or_ball_retrieval:'转身或捡球，不是第二次挥拍',follow_recovery_clipped_by_camera_pan:'相机移动截断随挥恢复'};const $=s=>document.querySelector(s),fmt=v=>v==null?'未评分':Number(v).toFixed(2),el=(t,v)=>{const x=document.createElement(t);x.textContent=v;return x};
for(const [v,l]of [[D.samples.length,'回归记录'],[D.gpu_runs.length,'原视频新推理'],[D.samples.filter(r=>r.episodes>0).length,'回归中有有效片段'],[D.samples.reduce((s,r)=>s+(r.episodes||0),0),'目标有效片段']]){const x=el('div','');x.append(el('b',v),el('span',l));$('#stats').append(x)}
for(const r of D.gpu_runs){const tr=document.createElement('tr');for(const v of [r.sample,`${r.frame_count} / ${r.native_ok}`,Object.entries(r.native_track_counts||{}).map(([k,n])=>`轨迹 ${k}: ${n} 帧`).join('；'),`${r.candidate_count} / ${r.audited_episodes??'待核验'}`,fmt(r.audited_score),`${r.duration_seconds}s`])tr.append(el('td',v));$('#gpu').append(tr)}
function detail(r){const box=$('#detail');box.hidden=false;box.replaceChildren(el('h2',r.name),el('p',`目标：${r.subject_scope?.selected_subject_id||'未确认'}；有效片段 ${r.episodes||0}。`));box.append(el('p',`原视频 SHA-256：${r.source_video_sha256}`));for(const a of r.episode_validity||[]){const label={scored:'已计入目标',other_subject:'另一个人，不合并',rejected:'未评分'}[a.status]||a.status;box.append(el('p',`${a.episode_id} · ${label} · ${(a.start_ms/1000).toFixed(3)}–${(a.end_ms/1000).toFixed(3)}s · ${(a.reason_codes||[]).concat(a.review_reason_codes||[]).map(k=>reasons[k]||k).join('；')}`))}const details=document.createElement('details');details.append(el('summary','对象、覆盖与原因明细'),el('pre',JSON.stringify({subject_scope:r.subject_scope,pattern_coverage:r.pattern_coverage,quality_reason_counts:r.quality_reason_counts,candidate_accounting:r.candidate_accounting},null,2)));box.append(details);box.scrollIntoView({behavior:'smooth',block:'start'})}
function render(){const mode=$('#filter').value;const rows=D.samples.filter(r=>mode==='all'||mode==='scored'&&r.episodes>0||mode==='unscored'&&!r.episodes||mode==='missing'&&r.selected_frames_missing_saved_native>0);$('#count').textContent=`${rows.length} 条` ;$('#rows').replaceChildren();for(const r of rows){const tr=document.createElement('tr'),c=r.pattern_coverage;for(const v of [r.name,`${r.old_scored_actions} → ${r.episodes||0}`,`${fmt(r.old_score)} → ${fmt(r.score)}`,c?`${c.measured_requirements} / ${c.applicable_requirements}`:'未测',r.selected_frames_missing_saved_native??'—'])tr.append(el('td',v));const td=document.createElement('td'),b=el('button','查看');b.onclick=()=>detail(r);td.append(b);tr.append(td);$('#rows').append(tr)}}$('#filter').onchange=render;$('#coverage').textContent=JSON.stringify(D.coverage_audit,null,2);render();</script></html>'''


if __name__ == '__main__':
    main()
