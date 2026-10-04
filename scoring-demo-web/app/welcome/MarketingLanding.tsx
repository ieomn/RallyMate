/* eslint-disable @next/next/no-html-link-for-pages -- Campaign links enter the separate workspace with a full page load; vinext beta client navigation is unavailable in this production bundle. */
"use client";

import { useEffect, useRef, useState, type PointerEvent } from "react";
import { AnimatedDisclosure } from "../AnimatedDisclosure";
import { ProductIcon } from "../ProductWorkspace";
import { TrainingExperience } from "./TrainingExperience";

const steps = [
  { label: "留住这一拍", tag: "CAPTURE", title: "带上手机。\n也带上完整的动作。", text: "固定机位，把全身、双脚和挥拍空间留在画面里。从准备到恢复，保留一拍的完整过程。", link: "查看拍摄指南", href: "/?view=guide", icon: "camera" as const },
  { label: "回到关键处", tag: "REVIEW", title: "慢一点看。\n多一点发现。", text: "上传原视频，沿时间线回到值得复核的片段。拖动、慢放，把当时来不及留意的细节再看一遍。", link: "开始视频分析", href: "/?view=analysis", icon: "play" as const },
  { label: "带向下一场", tag: "REFINE", title: "把具体问题，\n带给下一次训练。", text: "保存训练报告，把片段与观察带给教练。让每一次讨论，都从看得见的画面开始。", link: "进入训练空间", href: "/?view=overview", icon: "movement" as const },
];

const questions = [
  { title: "第一次使用，需要准备什么？", text: "一段正常速度的网球训练原视频就可以开始。固定手机，尽量让全身和双脚始终入镜，保留动作前后的过程。可以先查看拍摄指南，再上传视频。" },
  { title: "这里的交互演示是真实分析结果吗？", text: "宣传页中的球场、路径和阶段变化用于演示复盘方式。上传自己的视频后，训练工作台会显示对应的实际画面与可用测量；品牌主视觉是原创概念图。" },
  { title: "分析报告能代替教练的判断吗？", text: "RallyMate 帮你回看画面、定位片段，并整理可观察的测量信息。动作是否合理、应该怎样调整，仍需结合场景和教练判断。测量证据参考分不等同于正式技术评分。" },
  { title: "训练结束后，可以留下什么？", text: "可以下载 HTML 报告和 JSON 备份，之后导入备份继续查看。当前训练记录保存在此浏览器中；备份本身不包含原视频，建议同时保留原片。" },
];

export default function MarketingLanding() {
  const rootRef = useRef<HTMLDivElement>(null);
  const heroRef = useRef<HTMLElement>(null);
  const pointerFrame = useRef(0);
  const [menuOpen, setMenuOpen] = useState(false);
  const [activeSection, setActiveSection] = useState("");
  const [step, setStep] = useState(0);

  useEffect(() => {
    if (menuOpen) rootRef.current?.querySelector<HTMLAnchorElement>(".mk-nav a")?.focus();
  }, [menuOpen]);

  useEffect(() => {
    const root = rootRef.current;
    if (!root) return;
    const preference = window.matchMedia("(prefers-reduced-motion: reduce)");
    const animations = new Set<Animation>();
    let frame = 0;
    const updateProgress = () => {
      frame = 0;
      const distance = document.documentElement.scrollHeight - window.innerHeight;
      root.style.setProperty("--mk-progress", String(distance > 0 ? window.scrollY / distance : 0));
      root.dataset.scrolled = String(window.scrollY > 30);
    };
    const onScroll = () => { if (!frame) frame = requestAnimationFrame(updateProgress); };
    updateProgress();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    const closeMenu = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setMenuOpen(false);
      if (root.querySelector(".mk-nav.is-open")) root.querySelector<HTMLButtonElement>(".mk-menu-toggle")?.focus();
    };
    window.addEventListener("keydown", closeMenu);
    const reveal = new IntersectionObserver(entries => entries.forEach(entry => {
      if (!entry.isIntersecting) return;
      reveal.unobserve(entry.target);
      if (preference.matches || typeof entry.target.animate !== "function") return;
      const animation = entry.target.animate([{ opacity: .25, transform: "translateY(24px)" }, { opacity: 1, transform: "translateY(0)" }], { duration: 650, easing: "cubic-bezier(.22,1,.36,1)" });
      animations.add(animation);
      animation.onfinish = () => animations.delete(animation);
    }), { threshold: .12 });
    root.querySelectorAll(".mk-reveal").forEach(element => reveal.observe(element));
    const sections = new IntersectionObserver(entries => entries.forEach(entry => {
      if (entry.isIntersecting) setActiveSection(entry.target.id);
    }), { rootMargin: "-20% 0px -55% 0px" });
    root.querySelectorAll("#experience, #workflow, #questions").forEach(element => sections.observe(element));
    const reduce = () => { if (preference.matches) { animations.forEach(animation => animation.cancel()); animations.clear(); root.style.setProperty("--mk-pointer-x", "0px"); root.style.setProperty("--mk-pointer-y", "0px"); } };
    preference.addEventListener("change", reduce);
    return () => { window.removeEventListener("scroll", onScroll); window.removeEventListener("resize", onScroll); window.removeEventListener("keydown", closeMenu); preference.removeEventListener("change", reduce); reveal.disconnect(); sections.disconnect(); animations.forEach(animation => animation.cancel()); cancelAnimationFrame(frame); cancelAnimationFrame(pointerFrame.current); };
  }, []);

  function moveHero(event: PointerEvent<HTMLElement>) {
    if (event.pointerType !== "mouse" || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const bounds = heroRef.current?.getBoundingClientRect();
    if (!bounds) return;
    const x = ((event.clientX - bounds.left) / bounds.width - .5) * 12;
    const y = ((event.clientY - bounds.top) / bounds.height - .5) * 8;
    cancelAnimationFrame(pointerFrame.current);
    pointerFrame.current = requestAnimationFrame(() => {
      rootRef.current?.style.setProperty("--mk-pointer-x", `${x}px`);
      rootRef.current?.style.setProperty("--mk-pointer-y", `${y}px`);
    });
  }
  function resetHero() {
    cancelAnimationFrame(pointerFrame.current);
    rootRef.current?.style.setProperty("--mk-pointer-x", "0px");
    rootRef.current?.style.setProperty("--mk-pointer-y", "0px");
  }

  return <div className="marketing-page" ref={rootRef}>
    <a href="#marketing-main" className="mk-skip">跳到主要内容</a>
    <header className="mk-header">
      <a href="/welcome" className="mk-brand" aria-label="RallyMate 品牌首页"><ProductIcon name="tennis" size={28} /><span>RallyMate</span></a>
      <nav id="marketing-nav" className={`mk-nav${menuOpen ? " is-open" : ""}`} aria-label="品牌导航">
        {[{ id: "experience", title: "回看体验" }, { id: "workflow", title: "如何开始" }, { id: "questions", title: "常见问题" }].map(item => <a key={item.id} href={`#${item.id}`} aria-current={activeSection === item.id ? "location" : undefined} onClick={() => setMenuOpen(false)}>{item.title}</a>)}
        <a className="mk-nav-workspace" href="/?view=overview">训练空间<ProductIcon name="arrow-up-right" size={15} /></a>
      </nav>
      <div className="mk-header-actions"><a href="/?view=analysis" className="mk-button mk-button-lime mk-header-cta">开始体验<ProductIcon name="arrow-up-right" size={16} /></a><button className="mk-menu-toggle" type="button" aria-expanded={menuOpen} aria-controls="marketing-nav" aria-label={menuOpen ? "收起导航" : "展开导航"} onClick={() => setMenuOpen(value => !value)}><ProductIcon name={menuOpen ? "close" : "menu"} /></button></div>
      <div className="mk-reading-progress" aria-hidden="true" />
    </header>

    <main id="marketing-main">
      <section className="mk-hero" ref={heroRef} onPointerMove={moveHero} onPointerLeave={resetHero} aria-labelledby="mk-hero-title">
        {/* eslint-disable-next-line @next/next/no-img-element -- Original bundled campaign asset; one responsive object-fit surface without a remote optimizer. */}
        <img className="mk-hero-image" src="/marketing/rally-court-hero.png" width="1672" height="941" alt="暮色球场上，一位球员专注完成正手挥拍的品牌概念画面" fetchPriority="high" />
        <div className="mk-hero-shade" />
        <div className="mk-hero-content">
          <h1 id="mk-hero-title">下一拍，<br /><span>更有方向。</span></h1>
          <p className="mk-hero-description">用一段训练视频，<br />回到值得看清的瞬间。</p>
          <div className="mk-hero-actions"><a href="/?view=analysis" className="mk-button mk-button-lime">开启我的复盘<ProductIcon name="arrow-up-right" size={20} /></a><a href="#experience" className="mk-watch-link"><span><ProductIcon name="play" size={14} /></span>先体验一下</a></div>
        </div>
        <div className="mk-hero-bottom"><a href="#experience"><span className="mk-scroll-line" aria-hidden="true" />向下探索</a></div>
      </section>

      <section className="mk-manifesto mk-wrap mk-reveal" aria-labelledby="mk-manifesto-title">
        <div><h2 id="mk-manifesto-title">热爱，让你走上球场。<br /><span>看清，让下一步更坚定。</span></h2><p>那些来不及留意的脚步、挥拍与恢复，<br className="mk-desktop-break" />值得在场下，再认真看一次。</p></div>
        <div className="mk-values"><div><ProductIcon name="frame" size={23} /><span>回到关键片段</span></div><div><ProductIcon name="play" size={22} /><span>放慢动作细节</span></div><div><ProductIcon name="sessions" size={22} /><span>留下每次复盘</span></div></div>
      </section>

      <TrainingExperience />

      <section id="workflow" className="mk-workflow mk-wrap" aria-labelledby="mk-workflow-title">
        <div className="mk-section-heading mk-reveal"><div><h2 id="mk-workflow-title">从这一拍，<br /><span>走向下一次上场。</span></h2></div><p>无需改变你热爱的训练。<br />只多留一点，回看的时间。</p></div>
        <div className="mk-workflow-layout mk-reveal">
          <div className="mk-step-list" role="tablist" aria-label="开始复盘的三个步骤">{steps.map((item, index) => <button id={`mk-step-${index}`} key={item.tag} type="button" role="tab" aria-selected={step === index} aria-controls="mk-step-panel" tabIndex={step === index ? 0 : -1} onClick={() => setStep(index)} onKeyDown={event => { let next = index; if (event.key === "ArrowDown" || event.key === "ArrowRight") next = (index + 1) % steps.length; else if (event.key === "ArrowUp" || event.key === "ArrowLeft") next = (index + steps.length - 1) % steps.length; else if (event.key === "Home") next = 0; else if (event.key === "End") next = steps.length - 1; else return; event.preventDefault(); setStep(next); document.getElementById(`mk-step-${next}`)?.focus(); }}><strong>{item.label}</strong><ProductIcon name="arrow-up-right" size={24} /></button>)}</div>
          <div id="mk-step-panel" className="mk-step-panel" role="tabpanel" tabIndex={0} aria-labelledby={`mk-step-${step}`}>
            <div className={`mk-step-art mk-step-art-${step}`} aria-hidden="true"><div className="mk-orbit mk-orbit-one" /><div className="mk-orbit mk-orbit-two" /><div className="mk-art-court"><i /><i /><i /></div><div className="mk-art-icon"><ProductIcon name={steps[step].icon} size={42} /></div></div>
            <div key={step} className="mk-step-copy"><h3>{steps[step].title.split("\n").map((line, index) => <span key={line}>{index > 0 && <br />}{line}</span>)}</h3><p>{steps[step].text}</p><a href={steps[step].href} className="mk-text-link">{steps[step].link}<ProductIcon name="arrow-right" size={18} /></a></div>
          </div>
        </div>
      </section>

      <section className="mk-belief" aria-labelledby="mk-belief-title"><div className="mk-wrap mk-reveal"><h2 id="mk-belief-title">不止看一拍。<br />是为了，<em>更懂自己的球。</em></h2><div className="mk-belief-bottom"><span><ProductIcon name="tennis" size={30} />RallyMate</span><p>把画面留下，把问题说清。<br />和教练一起，让复盘回到训练里。</p><a href="/?view=analysis" className="mk-button mk-button-dark">从我的视频开始<ProductIcon name="arrow-up-right" size={18} /></a></div></div></section>

      <section id="questions" className="mk-faq mk-wrap" aria-labelledby="mk-faq-title"><div className="mk-faq-heading mk-reveal"><h2 id="mk-faq-title">还有些问题？</h2><a className="mk-text-link" href="/?view=guide">查看完整拍摄指南<ProductIcon name="arrow-up-right" size={17} /></a></div><div className="mk-faq-list mk-reveal">{questions.map((question) => <AnimatedDisclosure key={question.title} className="mk-faq-item" bodyClassName="mk-faq-answer" summary={<><strong>{question.title}</strong><ProductIcon name="plus" size={18} /></>}><p>{question.text}</p></AnimatedDisclosure>)}</div></section>

      <section className="mk-last-call mk-wrap mk-reveal" aria-labelledby="mk-last-title"><div><h2 id="mk-last-title">下一拍，<span>从这里开始。</span></h2></div><a href="/?view=analysis" className="mk-round-cta" aria-label="上传视频，开始我的复盘"><ProductIcon name="arrow-up-right" size={48} /></a><div className="mk-last-line"><a href="/?view=overview">进入训练空间<ProductIcon name="arrow-up-right" size={15} /></a></div></section>
    </main>

    <footer className="mk-footer"><div className="mk-wrap"><div className="mk-footer-top"><a href="/welcome" className="mk-brand"><ProductIcon name="tennis" size={23} /><span>RallyMate</span></a><a href="#marketing-main" className="mk-back-top">回到顶部<ProductIcon name="arrow-up-right" size={16} /></a></div><div className="mk-wordmark" aria-hidden="true">RallyMate<span>↗</span></div><div className="mk-footer-bottom"><p>© 2026 RallyMate</p><p>原创品牌概念视觉 · 演示内容为交互示意</p><a href="/?view=overview">训练工作台<ProductIcon name="arrow-up-right" size={14} /></a></div></div></footer>
  </div>;
}
