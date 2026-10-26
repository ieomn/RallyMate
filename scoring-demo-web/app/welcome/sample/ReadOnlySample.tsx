/* eslint-disable @next/next/no-html-link-for-pages -- Native links preserve workspace navigation in vinext. */
"use client";

import { useRef, useState } from "react";
import { ProductIcon } from "../../ProductWorkspace";

// All windows and example observations come from the repository's existing
// examples/coach-annotation-pilot/fictional-example.json, not a private report.
const windows = [
  { title: "恢复时的左脚移动", start: 13.4, end: 14.8, observation: "虚构示例记录了左脚向下一准备位置移动；脚底接触地面的精确瞬间不可见。", question: "回看这一步时，双脚与准备位置是否始终可见？", next: "下次用同一机位保留动作结束后的恢复过程，再和教练一起比较。", status: "观察的写法" },
  { title: "持拍手被身体遮挡", start: 12.1, end: 12.6, observation: "虚构示例中的持拍侧手腕被躯干遮挡，无法完整观察这一段持拍臂路径。", question: "换到侧面或斜后方，能否看清被遮住的持拍手？", next: "下次先试挥并检查画面，再选择能看清持拍手的固定机位。", status: "无法判断的写法" },
] as const;

export default function ReadOnlySample() {
  const [selected, setSelected] = useState(0);
  const [located, setLocated] = useState(false);
  const [questionOpen, setQuestionOpen] = useState(false);
  const playerRef = useRef<HTMLElement>(null);
  const item = windows[selected];
  function locate(index: number) {
    setSelected(index); setLocated(true); setQuestionOpen(false);
    playerRef.current?.scrollIntoView({ behavior: "auto", block: "center" });
    playerRef.current?.focus({ preventScroll: true });
  }
  return <div className="sample-report">
    <header className="sample-header"><a href="/welcome" className="sample-brand"><ProductIcon name="tennis" size={25} />RallyMate</a><a href="/?view=analysis" className="sample-primary">分析我的视频<ProductIcon name="arrow-up-right" size={17} /></a></header>
    <main className="sample-main">
      <a className="sample-back" href="/welcome#experience"><ProductIcon name="arrow-left" size={16} />回到首页</a>
      <div className="sample-heading"><span className="sample-kicker">只读交付样例 · 虚构格式演示</span><h1>先看懂片段，<br />再带着问题去练。</h1><p>一份报告会把可用观察、对应片段和下一步放在一起。点选下面的片段，体验它们如何关联。</p></div>
      <aside className="sample-honesty"><strong>这不是一次真实分析。</strong><p>本页沿用项目中已有的虚构教练标注示例，没有对应原视频；所有动作、时间与观察均为演示。当前没有获准公开的真实样片，因此原片不可播放，点击片段仅演示位置选择，不代表已回看真实画面。</p></aside>
      <div className="sample-layout"><section className="sample-media" ref={playerRef} tabIndex={-1} aria-label="原片位置示意"><div className="sample-no-video"><ProductIcon name="play" size={34} /><strong>原片未提供，无法播放</strong><p>真实报告会在这里显示你的视频。<br />本样例不包含任何私人训练画面。</p></div><div className="sample-timeline" aria-hidden="true"><span style={{ left: `${item.start * 5}%`, width: `${(item.end - item.start) * 5}%` }} /></div><p className="sample-location" aria-live="polite">{located ? "已选择示意片段" : "当前示意片段"} · {item.start.toFixed(1)}–{item.end.toFixed(1)} 秒</p><p className="sample-caption">时间来自虚构示例；不是已验证的视频定位。</p></section>
        <section className="sample-observations" aria-labelledby="sample-observations-title"><h2 id="sample-observations-title">先看这两项</h2>{windows.map((window, index) => <article className={selected === index ? "is-selected" : undefined} key={window.title}><div className="sample-card-top"><span>{window.status}</span><small>虚构示例</small></div><h3>{window.title}</h3><p>{window.observation}</p><button type="button" onClick={() => locate(index)} aria-pressed={selected === index}><ProductIcon name="play" size={16} />选择 {window.start.toFixed(1)}–{window.end.toFixed(1)} 秒示意位置<ProductIcon name="arrow-up-right" size={16} /></button></article>)}</section></div>
      <section className="sample-next"><div><span className="sample-kicker">问问这段动作</span><h2>让问题回到画面里</h2><p>下面是复盘提问方式，不是动作缺陷诊断。</p></div><div><button className="sample-question" type="button" aria-expanded={questionOpen} onClick={() => setQuestionOpen(value => !value)}>{item.question}<ProductIcon name="chevron-down" size={18} /></button>{questionOpen && <p className="sample-answer">样例没有原片，无法回答这个问题。真实报告中，应先回看对应片段，再区分看得见的动作、需要教练复核的问题和画面无法确认的部分。</p>}<div className="sample-next-action"><strong>下一步可以这样做</strong><p>{item.next}</p><small>真实报告中可以保存关注点，再关联到下一次分析。本页只读，不会创建训练记录。</small></div></div></section>
      <details className="sample-boundaries"><summary>深入查看：样例来源和判断边界</summary><p>来源：项目已有 coach-annotation-pilot / fictional-example.json（fictional_example，contains_real_annotations: false）。这些内容只用于格式演示，不用于训练、验证、准确性或校准。</p><p>示例中的触球不可见，不能确认触球时刻、触球质量或实际受力。移动可见也不等于恢复质量达标。本页不生成技术分、总分或个性化纠正动作。</p></details>
      <div className="sample-footer-cta"><p>准备好自己的完整动作片段后，<br />从真实画面开始第一次复盘。</p><a className="sample-primary" href="/?view=analysis">开始我的视频分析<ProductIcon name="arrow-right" size={18} /></a><a href="/?view=guide">先看拍摄指南</a></div>
    </main>
  </div>;
}
