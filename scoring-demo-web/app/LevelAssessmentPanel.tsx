"use client";

import { useState } from "react";
import { formatTennisScore } from "./lib/tennis-assessment";
import { DOMAINS, type GateKey, type LevelAssessment } from "./scoring/level-grading";
import "./level-assessment.css";

const names = { GS: "底线基础", FS: "步伐移动", SS: "发球", RS: "接发", NS: "网前", DS: "防守", OS: "高压", TS: "战术" };
const gateNames: Record<GateKey, string> = { non_dominant_hand_ok: "非持拍手参与", stroke_continuity_ok: "击球连贯", body_coordination_ok: "身体协调", contact_point_reasonable: "击球点与时机", serve_fluency_ok: "发球流畅", ball_control_consistency_ok: "球速、转速与落点一致性" };
export default function LevelAssessmentPanel({ assessments }: { assessments: LevelAssessment[] }) {
  const [selected, setSelected] = useState<number | null>(null);
  const current = assessments.find(row => row.target_level === selected)
    ?? assessments.find(row => row.overall_score !== null) ?? assessments[0];
  if (!current) return null;
  const measured = DOMAINS.filter(domain => current.domains[domain].score !== null);
  const missing = DOMAINS.filter(domain => current.domains[domain].score === null && current.domains[domain].module_weight > 0);
  return <div className="level-assessment">
    <div className="level-assessment-heading">
      <label>评分等级 <select value={current.target_level} onChange={event => setSelected(Number(event.target.value))}>
        {assessments.map(row => <option key={row.target_level} value={row.target_level}>Lv{row.target_level}</option>)}
      </select></label>
      <div className="level-assessment-total" aria-live="polite">
        {current.overall_score === null ? <span>本级暂无可计分项目</span> : <><strong>{formatTennisScore(current.overall_score)}</strong> / 100</>}
      </div>
    </div>
    {measured.length > 0 && <ul className="level-assessment-domains">{measured.map(domain => {
      const row = current.domains[domain];
      if (row.score === null) return null;
      return <li key={domain}><div><strong>{names[domain]}</strong>
        <small>{row.score_source === "observed_family" ? "按该类动作整体测量" : `实测子项 ${row.measured_techniques.join("、")}`}
          {" · "}{row.module_weight > 0 ? `本级权重 ${(row.module_weight * 100).toFixed(0)}%` : "本级不计入总分"}</small></div>
        <span>{formatTennisScore(row.score)}</span></li>;
    })}</ul>}
    {missing.length > 0 && <p className="level-assessment-note">本级缺少{missing.map(domain => names[domain]).join("、")}的测量。</p>}
    {current.overall_score !== null && <p className="level-assessment-note">总分按该等级已测项目的相对权重计算。</p>}
    {current.target_level >= 3 && <details><summary>晋级条件 · {current.eligible_level === current.target_level ? "已满足本次检查" : "仍有待满足或待观察的条件"}</summary>
      {Object.entries(current.gatekeepers).filter(([name]) => name === "intermediate" || current.target_level >= 4).map(([name, gate]) => <div key={name} className="level-assessment-gates">
        <h4>{name === "intermediate" ? "进入 Lv3" : "进入 Lv4–Lv5"}</h4>
        {Object.entries(gate.checks).map(([key, value]) => <p key={key}><span>{gateNames[key as GateKey]}</span><strong>{value === true ? "已满足" : value === false ? "待提升" : "待观察"}</strong></p>)}
      </div>)}
    </details>}
  </div>;
}
