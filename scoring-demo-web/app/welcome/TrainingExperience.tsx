"use client";

import { useEffect, useId, useRef, useState, type CSSProperties } from "react";

const phases = [
  { name: "准备", position: 15, cue: "让下一拍，提前发生。", description: "回看启动前的一瞬间。准备姿态、移动方向与来球，值得放在一起观察。", detail: "观察准备与启动", number: "01" },
  { name: "挥拍", position: 50, cue: "看清那个关键瞬间。", description: "把连续动作慢下来。在回放中找到关注的片段，连接脚步、转体与挥拍。", detail: "定位关注的动作", number: "02" },
  { name: "恢复", position: 85, cue: "这一拍之后，还有下一拍。", description: "观察击球后的回位与衔接。完整看完一个动作，才更容易理解整段训练。", detail: "回看动作的衔接", number: "03" },
] as const;

function ballPosition(progress: number) {
  const t = progress / 100;
  const inverse = 1 - t;
  return {
    x: inverse * inverse * 224 + 2 * inverse * t * 436 + t * t * 595,
    y: inverse * inverse * 347 + 2 * inverse * t * 10 + t * t * 167,
  };
}

function ControlIcon({ kind }: { kind: "play" | "pause" | "previous" | "next" }) {
  return <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden="true">
    {kind === "play" && <path d="m9 5 10 7-10 7V5Z" fill="currentColor" />}
    {kind === "pause" && <><path d="M8 5v14m8-14v14" stroke="currentColor" strokeWidth="3" strokeLinecap="round" /></>}
    {kind === "previous" && <path d="M7 5v14m11-14-9 7 9 7" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" />}
    {kind === "next" && <path d="M17 5v14M6 5l9 7-9 7" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" />}
  </svg>;
}

export function TrainingExperience() {
  const [progress, setProgress] = useState(50);
  const [playing, setPlaying] = useState(false);
  const [reduceMotion, setReduceMotion] = useState(false);
  const sectionRef = useRef<HTMLElement>(null);
  const progressRef = useRef(progress);
  const identifier = useId().replace(/:/g, "");
  const stage = progress < 33.34 ? 0 : progress < 66.67 ? 1 : 2;
  const phase = phases[stage];
  const ball = ballPosition(progress);
  // Split the quadratic at the same parameter as the ball so the illuminated
  // trail always ends exactly at its center, independent of curve arc length.
  const trailControl = { x: 224 + (436 - 224) * progress / 100, y: 347 + (10 - 347) * progress / 100 };

  useEffect(() => { progressRef.current = progress; }, [progress]);

  useEffect(() => {
    const motion = window.matchMedia("(prefers-reduced-motion: reduce)");
    const handleMotion = () => {
      setReduceMotion(motion.matches);
      if (motion.matches) setPlaying(false);
    };
    const initialCheck = requestAnimationFrame(handleMotion);
    motion.addEventListener("change", handleMotion);
    const handleVisibility = () => { if (document.hidden) setPlaying(false); };
    document.addEventListener("visibilitychange", handleVisibility);
    const observer = new IntersectionObserver(([entry]) => {
      if (!entry.isIntersecting) setPlaying(false);
    }, { threshold: 0.12 });
    if (sectionRef.current) observer.observe(sectionRef.current);
    return () => {
      cancelAnimationFrame(initialCheck);
      motion.removeEventListener("change", handleMotion);
      document.removeEventListener("visibilitychange", handleVisibility);
      observer.disconnect();
    };
  }, []);

  useEffect(() => {
    if (!playing || reduceMotion) return;
    let frame = 0;
    let previousTime = 0;
    let lastPaint = 0;
    let position = progressRef.current;
    const animate = (time: number) => {
      if (previousTime) position += Math.min(time - previousTime, 100) / 90;
      previousTime = time;
      if (position >= 100) {
        setProgress(100);
        setPlaying(false);
        return;
      }
      if (time - lastPaint >= 32) {
        setProgress(position);
        lastPaint = time;
      }
      frame = requestAnimationFrame(animate);
    };
    frame = requestAnimationFrame(animate);
    return () => cancelAnimationFrame(frame);
  }, [playing, reduceMotion]);

  const seek = (position: number) => {
    setPlaying(false);
    setProgress(Math.min(100, Math.max(0, position)));
  };
  const togglePlayback = () => {
    if (playing) { setPlaying(false); return; }
    if (reduceMotion || document.hidden) return;
    if (progress >= 100) {
      progressRef.current = 0;
      setProgress(0);
    }
    setPlaying(true);
  };

  return <section className="marketing-experience" id="experience" ref={sectionRef} aria-labelledby={`${identifier}-heading`}>
    <div className="mx-heading">
      <div><p className="mx-eyebrow">A CLOSER LOOK</p><h2 id={`${identifier}-heading`}>有些进步，<br /><span>回看才看得见。</span></h2></div>
      <p className="mx-heading-copy">好的训练，不止于多打一拍。<br />还在于看懂这一拍。<br /><span>动动手，感受回看的节奏。</span></p>
    </div>

    <div className="mx-explorer">
      <div className="mx-court-panel">
        <div className="mx-court-top"><span className="mx-live-label"><i />THE RALLY, RECONSIDERED</span><span className="mx-diagram-label">动作回看示意</span></div>
        <svg className="mx-court" viewBox="0 0 820 520" aria-hidden="true" fill="none">
          <defs>
            <linearGradient id={`${identifier}-surface`} x1="250" y1="60" x2="510" y2="475" gradientUnits="userSpaceOnUse"><stop stopColor="#24352a" /><stop offset="1" stopColor="#131e18" /></linearGradient>
            <radialGradient id={`${identifier}-light`}><stop stopColor="#8ea879" stopOpacity=".16" /><stop offset="1" stopColor="#8ea879" stopOpacity="0" /></radialGradient>
            <linearGradient id={`${identifier}-trajectory`} x1="224" y1="347" x2="595" y2="167" gradientUnits="userSpaceOnUse"><stop stopColor="#d8fc75" stopOpacity=".15" /><stop offset="1" stopColor="#d8fc75" /></linearGradient>
            <filter id={`${identifier}-shadow`} x="-100%" y="-100%" width="300%" height="300%"><feGaussianBlur stdDeviation="6" /></filter>
          </defs>
          <ellipse cx="430" cy="287" rx="380" ry="220" fill={`url(#${identifier}-light)`} />
          <path d="M239 93h350l143 352H91Z" fill={`url(#${identifier}-surface)`} stroke="#66826a" strokeOpacity=".22" />
          <g stroke="#bfcebb" strokeWidth="1.1" strokeOpacity=".5">
            <path d="M267 116h296l114 295H148Z" />
            <path d="M291 116 195 411m344-295 89 295M225 224h378M182 325h460M414 224v101" />
            <path d="M412 116v9m0 274v12" />
          </g>
          <g stroke="#dbe4d6"><path d="M199 273h425" strokeOpacity=".7" strokeWidth="2" /><path d="M199 273v19m425-19v19" strokeWidth="3" strokeLinecap="round" /><path d="M200 280h423m-423 5h423" strokeOpacity=".12" /></g>
          <g className="mx-court-details" stroke="#799177" strokeOpacity=".32"><path d="M106 105h18m-9-9v18M695 400h18m-9-9v18" /><path d="M622 96h56M91 445h49" /></g>
          <path d="m224 365 70-27 117 18" stroke="#b5c4aa" strokeOpacity={stage === 0 || stage === 2 ? ".6" : ".22"} strokeWidth="1.5" strokeDasharray="3 7" />
          <circle cx="224" cy="365" r="22" stroke="#d8fc75" strokeOpacity={stage === 0 ? ".5" : ".15"} />
          <circle cx="224" cy="365" r="4" fill="#d8fc75" fillOpacity={stage === 0 ? "1" : ".4"} />
          <circle cx="411" cy="356" r="22" stroke="#d8fc75" strokeOpacity={stage === 2 ? ".5" : ".15"} />
          <circle cx="411" cy="356" r="4" fill="#d8fc75" fillOpacity={stage === 2 ? "1" : ".4"} />
          <path d="M224 347Q436 10 595 167" stroke="#d8fc75" strokeOpacity=".13" strokeWidth="1.5" strokeDasharray="3 7" />
          <path d={`M224 347Q${trailControl.x} ${trailControl.y} ${ball.x} ${ball.y}`} stroke={`url(#${identifier}-trajectory)`} strokeWidth="2.4" strokeLinecap="round" />
          {[15, 50, 85].map((position, index) => {
            const point = ballPosition(position);
            return <g key={position}><circle cx={point.x} cy={point.y} r={stage === index ? 12 : 5} stroke="#d8fc75" strokeOpacity={stage === index ? ".38" : ".2"} /><circle cx={point.x} cy={point.y} r="2" fill="#d8fc75" fillOpacity=".55" /></g>;
          })}
          <ellipse cx={ball.x + 5} cy={ball.y + 23} rx="10" ry="4" fill="#050a05" opacity=".7" filter={`url(#${identifier}-shadow)`} />
          <circle cx={ball.x} cy={ball.y} r="19" fill="#d8fc75" fillOpacity=".06" />
          <g transform={`translate(${ball.x} ${ball.y})`}><circle r="7.5" fill="#d8fc75" /><path d="M-6-4c6 0 6 8 12 8" stroke="#7c9c35" strokeWidth="1" strokeLinecap="round" /></g>
          <g className="mx-court-annotation" fill="#a5b79e"><text x="179" y="410">准备位置</text><text x="393" y="401">回位</text><text x="587" y="207" fill="#d8fc75" fillOpacity=".65">动作衔接</text></g>
        </svg>
        <div className="mx-court-bottom"><span><span className="mx-stage-index">0{stage + 1}</span> / 03</span><p>交互示意 · 非真实视频测量</p><span className="mx-drag-hint">拖动下方时间轴 <span aria-hidden="true">↔</span></span></div>
      </div>

      <div className="mx-controls-panel">
        <div className="mx-phase-tabs" role="group" aria-label="选择观察阶段">{phases.map((item, index) => <button type="button" key={item.name} aria-pressed={stage === index} onClick={() => seek(item.position)}><span>{item.number}</span>{item.name}</button>)}</div>
        <div className="mx-phase-copy" key={phase.name}>
          <span className="mx-detail-label"><i />{phase.detail}</span>
          <h3>{phase.cue}</h3>
          <p>{phase.description}</p>
        </div>
        <div className="mx-scrubber">
          <div className="mx-scrubber-label"><label htmlFor={`${identifier}-scrubber`}>掌握你的回看节奏</label><span>{Math.round(progress).toString().padStart(2, "0")} <span>/ 100</span></span></div>
          <input id={`${identifier}-scrubber`} className="mx-range" type="range" min="0" max="100" step="1" value={progress} onChange={(event) => seek(Number(event.target.value))} aria-label="拖动动作回看示意进度" aria-valuetext={`${Math.round(progress)}%，${phase.name}阶段`} style={{ "--mx-progress": `${progress}%` } as CSSProperties} />
          <div className="mx-range-labels" aria-hidden="true"><span>准备</span><span>挥拍</span><span>恢复</span></div>
          <div className="mx-playback-controls">
            <button className="mx-step" type="button" onClick={() => seek(progress - 5)} aria-label="示意后退 5%" disabled={progress <= 0}><ControlIcon kind="previous" /></button>
            <button className="mx-play" type="button" onClick={togglePlayback} disabled={reduceMotion} aria-label={playing ? "暂停动作示意" : "播放动作示意"}><ControlIcon kind={playing ? "pause" : "play"} /><span>{playing ? "暂停示意" : "播放示意"}</span></button>
            <button className="mx-step" type="button" onClick={() => seek(progress + 5)} aria-label="示意前进 5%" disabled={progress >= 100}><ControlIcon kind="next" /></button>
          </div>
          <p className="mx-control-help">{reduceMotion ? "已遵循减少动态效果设置，可手动拖动体验。" : "也可以用键盘方向键，逐步探索。"}</p>
        </div>
      </div>
    </div>
    <p className="mx-footnote">这里展示回看的交互方式。上传训练视频后，你将看到属于自己的训练片段与分析结果。</p>
  </section>;
}
