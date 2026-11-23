/* eslint-disable @next/next/no-html-link-for-pages -- Campaign links enter the separate workspace with a full page load; vinext beta client navigation is unavailable in this production bundle. */
"use client";

import { useEffect, useRef, useState, type PointerEvent } from "react";
import { AnimatedDisclosure } from "../AnimatedDisclosure";
import { ProductIcon } from "../ProductWorkspace";
import { useAuth } from "../auth/AuthProvider";
import { TrainingExperience } from "./TrainingExperience";

const steps = [
  { label: "上传视频", tag: "CAPTURE", title: "选一段\n最近的训练录像。", text: "选好原片，预览确认后就可以开始分析。", link: "上传训练视频", href: "/?view=analysis", icon: "camera" as const },
  { label: "回看动作", tag: "REVIEW", title: "看看击球前后，\n身体是怎么转的。", text: "打开报告会先看到关键片段。沿时间轴前后拖动，也可以放慢速度看。", link: "开始视频分析", href: "/?view=analysis", icon: "play" as const },
  { label: "保存练习", tag: "REFINE", title: "下次上场，\n先练一个动作。", text: "在报告里保存想练的那一项。练完再录一段同类动作，和上次的片段对照着看。", link: "查看我的训练", href: "/?view=overview", icon: "movement" as const },
];

const questions = [
  { title: "第一次使用，需要准备什么？", text: "用邮箱注册后，上传一段正常速度的训练录像就可以开始。" },
  { title: "打开报告先看哪里？", text: "先看关键动作片段和旁边的练习建议。展开「详细数据」，可以查看本次动作评分和测量依据。" },
  { title: "下次怎么接着练？", text: "在报告中点「保存练习」。下次录一段同类动作，用相近的机位，方便回看时比较。" },
  { title: "视频和报告怎么保存？", text: "在线分析记录随账号保存，训练备注和导入的备份存在当前浏览器里。可以下载 HTML 报告和 JSON 备份，原视频请另行保留。" },
];

export default function MarketingLanding() {
  const { user } = useAuth();
  const rootRef = useRef<HTMLDivElement>(null);
  const heroRef = useRef<HTMLElement>(null);
  const pointerFrame = useRef(0);
  const [menuOpen, setMenuOpen] = useState(false);
  const [activeSection, setActiveSection] = useState("");
  const [step, setStep] = useState(0);

  useEffect(() => {
    if (!menuOpen) return;
    const root = rootRef.current;
    root?.querySelector<HTMLAnchorElement>(".mk-nav a")?.focus({ preventScroll: true });
    const closeOutside = (event: Event) => {
      if (event.target instanceof Node && !root?.querySelector(".mk-header")?.contains(event.target)) setMenuOpen(false);
    };
    const desktop = window.matchMedia("(min-width: 761px)");
    const closeOnResize = () => { if (desktop.matches) setMenuOpen(false); };
    document.addEventListener("pointerdown", closeOutside);
    document.addEventListener("focusin", closeOutside);
    desktop.addEventListener("change", closeOnResize);
    return () => {
      document.removeEventListener("pointerdown", closeOutside);
      document.removeEventListener("focusin", closeOutside);
      desktop.removeEventListener("change", closeOnResize);
    };
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
        {[{ id: "experience", title: "功能介绍" }, { id: "workflow", title: "如何开始" }, { id: "questions", title: "常见问题" }].map(item => <a key={item.id} href={`#${item.id}`} aria-current={activeSection === item.id ? "location" : undefined} onClick={() => setMenuOpen(false)}>{item.title}</a>)}
        <a className="mk-nav-workspace" href="/?view=overview">训练空间<ProductIcon name="arrow-up-right" size={15} /></a>
        {!user && <><a href="/login">登录</a><a href="/register">注册</a></>}
      </nav>
      <div className="mk-header-actions"><a href="/?view=analysis" className="mk-button mk-button-lime mk-header-cta">开始训练<ProductIcon name="arrow-up-right" size={16} /></a><button className="mk-menu-toggle" type="button" aria-expanded={menuOpen} aria-controls="marketing-nav" aria-label={menuOpen ? "收起导航" : "展开导航"} onClick={() => setMenuOpen(value => !value)}><ProductIcon name={menuOpen ? "close" : "menu"} /></button></div>
      <div className="mk-reading-progress" aria-hidden="true" />
    </header>

    <main id="marketing-main">
      <section className="mk-hero" ref={heroRef} onPointerMove={moveHero} onPointerLeave={resetHero} aria-labelledby="mk-hero-title">
        {/* eslint-disable-next-line @next/next/no-img-element -- Original bundled campaign asset; one responsive object-fit surface without a remote optimizer. */}
        <img className="mk-hero-image" src="/marketing/rally-court-hero.png" width="1672" height="941" alt="暮色中的网球场，一名球员正手挥拍" fetchPriority="high" />
        <div className="mk-hero-shade" />
        <div className="mk-hero-content">
          <h1 id="mk-hero-title">下一拍，<br /><span>更有方向。</span></h1>
          <p className="mk-hero-description">上传训练录像，回看自己的挥拍。<br />看看哪一拍值得再练。</p>
          <div className="mk-hero-actions"><a href="/?view=analysis" className="mk-button mk-button-lime">分析我的视频<ProductIcon name="arrow-up-right" size={20} /></a><a href="#experience" className="mk-watch-link"><span><ProductIcon name="arrow-right" size={14} /></span>了解分析功能</a></div>
        </div>
        <div className="mk-hero-bottom"><a href="#experience"><span className="mk-scroll-line" aria-hidden="true" />往下看</a></div>
      </section>

      <section className="mk-manifesto mk-wrap mk-reveal" aria-labelledby="mk-manifesto-title">
        <div><h2 id="mk-manifesto-title">击球的那一刻，<br /><span>你的脚站在哪儿？</span></h2><p>把视频往前拖一点，看准备时双脚的位置。<br className="mk-desktop-break" />再慢放到击球，看重心怎么移动。</p></div>
        <div className="mk-values"><div><ProductIcon name="frame" size={23} /><span>定位关键片段</span></div><div><ProductIcon name="play" size={22} /><span>慢放原片</span></div><div><ProductIcon name="sessions" size={22} /><span>保存练习</span></div></div>
      </section>

      <TrainingExperience />

      <section id="workflow" className="mk-workflow mk-wrap" aria-labelledby="mk-workflow-title">
        <div className="mk-section-heading mk-reveal"><div><h2 id="mk-workflow-title">第一次<br /><span>复盘训练？</span></h2></div><p>准备一段训练原片，<br />按这三步开始。</p></div>
        <div className="mk-workflow-layout mk-reveal">
          <div className="mk-step-list" role="tablist" aria-label="开始复盘的三个步骤">{steps.map((item, index) => <button id={`mk-step-${index}`} key={item.tag} type="button" role="tab" aria-selected={step === index} aria-controls="mk-step-panel" tabIndex={step === index ? 0 : -1} onClick={() => setStep(index)} onKeyDown={event => { let next = index; if (event.key === "ArrowDown" || event.key === "ArrowRight") next = (index + 1) % steps.length; else if (event.key === "ArrowUp" || event.key === "ArrowLeft") next = (index + steps.length - 1) % steps.length; else if (event.key === "Home") next = 0; else if (event.key === "End") next = steps.length - 1; else return; event.preventDefault(); setStep(next); document.getElementById(`mk-step-${next}`)?.focus(); }}><strong>{item.label}</strong><ProductIcon name="arrow-up-right" size={24} /></button>)}</div>
          <div id="mk-step-panel" className="mk-step-panel" role="tabpanel" tabIndex={0} aria-labelledby={`mk-step-${step}`}>
            <div className={`mk-step-art mk-step-art-${step}`} aria-hidden="true"><div className="mk-orbit mk-orbit-one" /><div className="mk-orbit mk-orbit-two" /><div className="mk-art-court"><i /><i /><i /></div><div className="mk-art-icon"><ProductIcon name={steps[step].icon} size={42} /></div></div>
            <div key={step} className="mk-step-copy"><h3>{steps[step].title.split("\n").map((line, index) => <span key={line}>{index > 0 && <br />}{line}</span>)}</h3><p>{steps[step].text}</p><a href={steps[step].href} className="mk-text-link">{steps[step].link}<ProductIcon name="arrow-right" size={18} /></a></div>
          </div>
        </div>
      </section>

      <section className="mk-belief" aria-labelledby="mk-belief-title"><div className="mk-wrap mk-reveal"><h2 id="mk-belief-title">和教练一起，<br /><em>再看一遍这球。</em></h2><div className="mk-belief-bottom"><span><ProductIcon name="tennis" size={30} />RallyMate</span><p>把原片停在想问的地方。<br />脚步、转肩还是随挥，指着画面聊。</p><a href="/?view=analysis" className="mk-button mk-button-dark">分析我的视频<ProductIcon name="arrow-up-right" size={18} /></a></div></div></section>

      <section id="questions" className="mk-faq mk-wrap" aria-labelledby="mk-faq-title"><div className="mk-faq-heading mk-reveal"><h2 id="mk-faq-title">还有些问题？</h2><a className="mk-text-link" href="/?view=overview">进入训练空间<ProductIcon name="arrow-up-right" size={17} /></a></div><div className="mk-faq-list mk-reveal">{questions.map((question) => <AnimatedDisclosure key={question.title} className="mk-faq-item" bodyClassName="mk-faq-answer" summary={<><strong>{question.title}</strong><ProductIcon name="plus" size={18} /></>}><p>{question.text}</p></AnimatedDisclosure>)}</div></section>

      <section className="mk-last-call mk-wrap mk-reveal" aria-labelledby="mk-last-title"><div><h2 id="mk-last-title">选一段视频，<span>开始复盘。</span></h2></div><a href="/?view=analysis" className="mk-round-cta" aria-label="上传视频，开始我的复盘"><ProductIcon name="arrow-up-right" size={48} /></a><div className="mk-last-line"><a href="/?view=overview">进入训练空间<ProductIcon name="arrow-up-right" size={15} /></a></div></section>
    </main>

    <footer className="mk-footer"><div className="mk-wrap"><div className="mk-footer-top"><a href="/welcome" className="mk-brand"><ProductIcon name="tennis" size={23} /><span>RallyMate</span></a><a href="#marketing-main" className="mk-back-top">回到顶部<ProductIcon name="arrow-up-right" size={16} /></a></div><div className="mk-wordmark" aria-hidden="true">RallyMate<span>↗</span></div><div className="mk-footer-bottom"><p>© 2026 RallyMate</p><p>网球训练视频分析</p><a href="/?view=overview">训练工作台<ProductIcon name="arrow-up-right" size={14} /></a></div></div></footer>
  </div>;
}
