import { techniqueExamplesForCard } from "./lib/technique-examples";

export default function TechniqueExamples({ indicatorId }: { indicatorId: string }) {
  const examples = techniqueExamplesForCard(indicatorId);
  if (!examples.length) return null;
  return <details className="card-technique-examples">
    <summary>官方教学示范（{examples.length}）</summary>
    {examples.map(example => <article key={example.id}>
      <h4>{example.title}</h4>
      <p>{example.focus}</p>
      <p><a href={example.source.url} target="_blank" rel="noopener noreferrer">查看 {example.source.provider} 教学示范</a></p>
      <p><small>{example.scope === "technique_practice" ? "相关技术练习" : "相关动作讲解"} · 练习主题：{example.sourceSection}</small></p>
    </article>)}
    <p className="indicator-grade-note">教学资料用于理解动作。评分仍依据你的本次视频测量；可将示范用于安排对应的技能练习。</p>
  </details>;
}
