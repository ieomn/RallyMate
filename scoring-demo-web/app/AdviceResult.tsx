import type { AdviceResponse } from "./lib/advice-display";

export default function AdviceResult({ technique, response }: { technique: string; response: AdviceResponse | null }) {
  const advice = response?.advice;
  const evidence = response?.evidence;
  const unsupported = evidence && ["no_video", "analysis_unavailable", "insufficient_evidence"].includes(evidence.status);
  return <aside className="coach-result" aria-label="建议与识别依据">
    <span className="card-kicker">{technique} · {unsupported ? "识别证据说明" : "解释与建议"}</span>
    <h3>{advice?.summary ?? "先确认识别依据，再给出建议。"}</h3>
    {!advice && <p className="coach-evidence-copy">选择动作并提交问题后，会说明本次识别到了什么、哪些部分还不能判断，以及可以怎样补充证据。未识别不等于动作错误。</p>}
    {evidence && <div className="coach-evidence" aria-label="本次所选动作的证据">
      {evidence.explanation !== advice?.summary && <p>{evidence.explanation}</p>}
      {evidence.availableFacts.length > 0 && <><b>本次可确认的内容</b><ul>{evidence.availableFacts.map(fact => <li key={fact}>{fact}</li>)}</ul></>}
      {evidence.limitations.length > 0 && <><b>尚不能判断的部分</b><ul>{evidence.limitations.map(note => <li key={note}>{note}</li>)}</ul></>}
    </div>}
    {advice && <div className={`coach-columns${advice.drills.length ? "" : " coach-columns-single"}`}>
      <div><b>{unsupported ? "如何补充证据" : "下一步建议"}</b>{advice.nextSteps.map(item => <p key={item}>{item}</p>)}</div>
      {advice.drills.length > 0 && <div><b>通用练习提示</b>{advice.drills.map(drill => <div key={drill.name}><p>{drill.name} · {drill.durationMin} 分钟</p><ol>{drill.steps.map(step => <li key={step}>{step}</li>)}</ol></div>)}</div>}
    </div>}
    {advice?.safetyNotes[0] && <small>{advice.safetyNotes[0]}</small>}
  </aside>;
}
