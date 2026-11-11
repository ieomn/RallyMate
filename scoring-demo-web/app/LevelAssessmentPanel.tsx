"use client";

import { useState } from "react";
import { levelAssessmentOf, levelAssessmentsOf } from "./lib/level-assessment";
import { DOMAINS, type GateKey } from "./scoring/level-grading";
import "./level-assessment.css";

const names = { GS: "底线基础", FS: "步伐移动", SS: "发球", RS: "接发", NS: "网前", DS: "防守", OS: "高压", TS: "战术" };
const gateNames: Record<GateKey, string> = { non_dominant_hand_ok: "非持拍手参与", stroke_continuity_ok: "击球连贯", body_coordination_ok: "身体协调", contact_point_reasonable: "击球点与时机", serve_fluency_ok: "发球流畅", ball_control_consistency_ok: "球速、转速与落点一致性" };
const scoreText = (score: number | null) => score === null ? "未观测" : score.toFixed(1);

export default function LevelAssessmentPanel({ assessment, assessments }: { assessment?: unknown; assessments?: unknown }) {
  const preferred = levelAssessmentOf(assessment), list = levelAssessmentsOf(assessments);
  const [selected, setSelected] = useState<number | null>(null);
  const current = list.find(row => row.target_level === selected) ?? preferred ?? list.find(row => row.overall_score !== null) ?? list[0];
  if (!current) return null;
  return <section className="level-assessment" aria-label="分级技术评分">
    <div className="level-assessment-heading"><div><h3>分级技术评分</h3><p>按当前训练等级查看已观测动作的得分。</p></div>
      {list.length > 1 && <label>训练等级 <select value={current.target_level} onChange={event => setSelected(Number(event.target.value))}>
        {list.map(row => <option key={row.target_level} value={row.target_level}>Lv{row.target_level}</option>)}
      </select></label>}
    </div>
    <div className="level-assessment-total"><strong>{current.overall_score === null ? "本级未设权重" : current.overall_score.toFixed(1)}</strong>
      <span>{current.overall_score !== null ? "/ 100 · 已观测技术" : "可在下方查看实测分项"}</span>
      <small>当前反手路线：{current.backhand_style === "two_handed" ? "双手反手" : "单手反手"}</small>
    </div>
    <div className="level-assessment-domains">{DOMAINS.map(domain => {
      const row = current.domains[domain];
      return <div key={domain}><span>{names[domain]} <small>{(row.module_weight * 100).toFixed(0)}%</small></span><strong>{scoreText(row.score)}</strong>
        <small>{row.score_source === "observed_family" ? "已评动作类别，子技术待细分" : row.score !== null ? `${row.measured_techniques.length} 项子技术已评` : "当前视频未提供该项测量"}</small></div>;
    })}</div>
    <p className="level-assessment-note">未出现的技术不作零分；已观测系统按本级权重重新归一化。系统内权重采用可配置的工程规则。</p>
    {current.target_level >= 3 && <details><summary>晋级条件 · {current.eligible_level === current.target_level ? "已满足本次检查" : "仍有待满足或待观察的条件"}</summary>
      {Object.entries(current.gatekeepers).filter(([name]) => name === "intermediate" || current.target_level >= 4).map(([name, gate]) => <div key={name} className="level-assessment-gates">
        <h4>{name === "intermediate" ? "进入 Lv3" : "进入 Lv4–Lv5"}</h4>
        {Object.entries(gate.checks).map(([key, value]) => <p key={key}><span>{gateNames[key as GateKey]}</span><strong>{value === true ? "已满足" : value === false ? "待提升" : "待观察"}</strong></p>)}
      </div>)}
      <p>晋级条件单独检查，已有动作评分保留。训练等级选择不代表水平认证。</p>
    </details>}
  </section>;
}
