import type { AdviceResponse } from "./lib/advice-display";
import "./advice-result.css";

const scoringDiagnostic = /(?:无法|不能|未能|不足以|尚不(?:能|可)|暂不(?:能|可)|不支持).{0,16}(?:评分|计分|打分|判断|评判|评价|(?:确认|确定).{0,12}(?:技术问题|技术缺陷|动作问题|动作错误))|(?:尚未|暂未|不能).{0,8}(?:形成|提供|进行).{0,8}(?:评分|评价)|不能把.*(?:动作错误|技术缺陷)|(?:证据|观测|测量|姿态记录)(?:仍|尚|还)?(?:不足|缺失|不完整)|缺(?:少|乏).*(?:证据|观测|测量)|缺测|(?:准备|随挥|动作)阶段(?:尚未覆盖|不完整)/u;
// Remove only scoring diagnostics. Keep measured values and candidate/proxy
// qualifications, including when they share a sentence with a hidden clause.
const visibleExplanation = (text: string) => text.split(/(?<=[。；！？])/u).map(sentence => {
  const ending = sentence.match(/[。；！？]$/u)?.[0] ?? "";
  const body = ending ? sentence.slice(0, -1) : sentence;
  const clauses = body.split("，").filter(clause => !scoringDiagnostic.test(clause));
  return clauses.length ? clauses.join("，") + ending : "";
}).join("").trim();

export default function AdviceResult({ technique, response, jobId }: { technique: string; response: AdviceResponse | null; jobId?: string }) {
  const advice = response?.advice;
  const evidence = response?.evidence;
  const coaching = advice?.coaching;
  const unsupported = evidence && ["no_video", "analysis_unavailable", "insufficient_evidence"].includes(evidence.status);
  const empty = unsupported || coaching?.status === "insufficient_evidence";
  const facts = (coaching?.evidence ?? evidence?.availableFacts ?? []).map(visibleExplanation).filter(Boolean);
  const rationale = coaching && !empty ? visibleExplanation(coaching.whyItMatters) : "";
  const practice = (coaching?.practice ?? []).map(visibleExplanation).filter(Boolean);
  const checkNextTime = coaching ? visibleExplanation(coaching.checkNextTime) : "";
  const reviewQuestion = coaching?.reviewQuestion ? visibleExplanation(coaching.reviewQuestion) : "";
  const nextSteps = (advice?.nextSteps ?? []).map(visibleExplanation).filter(Boolean);
  const timed = coaching && Number.isFinite(coaching.startMs) && Number.isFinite(coaching.endMs) && coaching.startMs! >= 0 && coaching.endMs! > coaching.startMs!;
  return <aside className="coach-result" aria-label={`${technique}建议与识别依据`}>
    {coaching ? <p className="coach-evidence-status">{response?.source === "mimo" ? "MiMo 辅助整理" : "本次证据解读"} · {coaching.status === "measured_focus" ? "基于动作测量的练习重点" : coaching.status === "review_focus" ? "待回放核对" : "识别证据说明"}</p>
      : unsupported && <p className="coach-evidence-status">识别证据说明</p>}
    <h3>{empty ? "暂无可展示的结果" : visibleExplanation(coaching?.focus ?? advice?.summary ?? "先确认识别依据，找到本次练习重点。") || `${technique}练习与回看`}</h3>
    {!advice && <p className="coach-evidence-copy">选择动作并提交问题后，这里会按“具体表现、视频依据、怎么练、如何复测”说明本次结果。</p>}
    {coaching && <div className="coach-explanation">
      {timed && <div className="coach-source-segment"><span>对应片段 {(coaching.startMs! / 1000).toFixed(1)}–{(coaching.endMs! / 1000).toFixed(1)} 秒</span>
        {jobId && <button type="button" onClick={() => window.dispatchEvent(new CustomEvent("rallymate:seek-video", { detail: { jobId, timestampMs: coaching.startMs } }))}>回看这段动作 ↗</button>}</div>}
      <dl>
        {facts.length > 0 && <div><dt>具体依据</dt><dd><ul>{facts.map(item => <li key={item}>{item}</li>)}</ul></dd></div>}
        {rationale && <div><dt>{coaching.status === "measured_focus" ? "为什么练这一项" : "先确认什么"}</dt><dd>{rationale}</dd></div>}
        {practice.length > 0 && <div><dt>{coaching.status === "insufficient_evidence" ? "如何补充证据" : "下一次怎么练"}</dt><dd><ol>{practice.map(item => <li key={item}>{item}</li>)}</ol></dd></div>}
        {checkNextTime && <div><dt>怎样看出进步</dt><dd>{checkNextTime}</dd></div>}
      </dl>
      {reviewQuestion && <div className="coach-review-question"><strong>回放时，重点核对</strong><p>{reviewQuestion}</p></div>}
    </div>}
    {!coaching && facts.length > 0 && <div className="coach-evidence" aria-label="本次所选动作的证据">
      <b>本次可确认的内容</b><ul>{facts.map(fact => <li key={fact}>{fact}</li>)}</ul>
    </div>}
    {advice && !coaching && <div className={`coach-columns${advice.drills.length ? "" : " coach-columns-single"}`}>
      {nextSteps.length > 0 && <div><b>{unsupported ? "如何补充证据" : "下一步建议"}</b>{nextSteps.map(item => <p key={item}>{item}</p>)}</div>}
      {advice.drills.length > 0 && <div><b>通用练习提示</b>{advice.drills.map(drill => <div key={drill.name}><p>{drill.name} · {drill.durationMin} 分钟</p><ol>{drill.steps.map(step => <li key={step}>{step}</li>)}</ol></div>)}</div>}
    </div>}
    {advice?.safetyNotes[0] && <small>{advice.safetyNotes[0]}</small>}
  </aside>;
}
