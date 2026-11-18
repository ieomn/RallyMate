"use client";

import { useRef, useState, type CSSProperties, type KeyboardEvent } from "react";

// Still times refer to the 25 fps Pexels serve clip 4902773 in scripts/download_assets.py.
const moments = [
  { name: "准备", timestamp: "00:07.12", image: "set", alt: "球员双手向前，球和拍面在身前靠近", cue: "看双手的起点。球和拍面在身前靠近，随后两臂分开。" },
  { name: "抛球", timestamp: "00:07.92", image: "toss", alt: "球员的抛球手向上伸展，持拍手在身体后方", cue: "跟住抛球手。目光向上时，另一只手把球拍带向身后。" },
  { name: "随挥", timestamp: "00:08.52", image: "finish", alt: "球员身体向前，挥拍手顺势向下", cue: "留意击球之后。身体随挥拍向前移动，手臂顺势落下。" },
];

export function TrainingExperience() {
  const [selected, setSelected] = useState(0);
  const tabs = useRef<(HTMLButtonElement | null)[]>([]);
  const moment = moments[selected];
  function navigate(event: KeyboardEvent, index: number) {
    let next;
    if (event.key === "Home") next = 0;
    else if (event.key === "End") next = moments.length - 1;
    else if (event.key === "ArrowRight") next = (index + 1) % moments.length;
    else if (event.key === "ArrowLeft") next = (index + moments.length - 1) % moments.length;
    else return;
    event.preventDefault();
    setSelected(next);
    tabs.current[next]?.focus();
  }
  return <section className="mk-demo" id="experience" aria-labelledby="mk-demo-title">
    <div className="mk-demo-inner mk-wrap">
      <div className="mk-demo-intro">
        <p className="mk-topic">复盘示范</p>
        <h2 id="mk-demo-title">拆开看，<br />一次发球。</h2>
        <p>切换三个定格，跟着同一次发球看双手和身体的位置。</p>
      </div>
      <div className="mk-viewer">
        <div className="mk-frame-tabs" role="tablist" aria-label="发球定格" style={{ "--frame": selected } as CSSProperties}>
          {moments.map((item, index) => <button type="button" role="tab" key={item.image} id={`frame-tab-${item.image}`}
            ref={node => { tabs.current[index] = node; }}
            aria-selected={index === selected} aria-controls={`frame-panel-${item.image}`} tabIndex={index === selected ? 0 : -1}
            onClick={() => setSelected(index)} onKeyDown={event => navigate(event, index)}>
            {item.name}
          </button>)}
        </div>
        <div className="mk-frame-stack">
          {moments.map((item, index) => <div className="mk-still" key={item.image} role="tabpanel" id={`frame-panel-${item.image}`}
            aria-labelledby={`frame-tab-${item.image}`} aria-hidden={index !== selected} data-active={index === selected} tabIndex={index === selected ? 0 : -1}>
            {/* eslint-disable-next-line @next/next/no-img-element -- Three locally encoded stills crossfade in the same frame. */}
            <img loading="lazy" src={`/marketing/serve-${item.image}.webp`} alt={item.alt} width="1056" height="594" />
            <span className="mk-time">{item.timestamp}</span>
          </div>)}
        </div>
        <p className="mk-frame-note" aria-live="polite" aria-atomic="true"><span key={moment.image}>{moment.cue}</span></p>
      </div>
    </div>
  </section>;
}
