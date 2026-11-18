/* eslint-disable @next/next/no-html-link-for-pages -- Workspace links use native document navigation in vinext. */
"use client";

import { useEffect, useRef, useState } from "react";
import { AnimatedDisclosure } from "../AnimatedDisclosure";
import { ProductIcon } from "../ProductWorkspace";
import { useAuth } from "../auth/AuthProvider";
import { playPressFeedback } from "../lib/press-feedback";
import { TrainingExperience } from "./TrainingExperience";

const questions = [
  { title: "第一次使用，如何开始？", text: "申请账号时写明使用理由。审核通过后进入训练空间，选择视频，确认预览后点击「开始分析」。" },
  { title: "一份报告里能看什么？", text: "从原片回放和本次练习重点开始。展开后可查看五维动作评分、关键片段，以及这段视频成功识别的人体三维回放和球路轨迹。" },
  { title: "下次训练怎样接着练？", text: "在报告中保存一个关注点。提交下一段视频时可以继续关注这个问题，再回看两次训练的对应片段。" },
  { title: "可以保存或分享报告吗？", text: "在线分析记录随账号保存。可下载 HTML 报告或 JSON 备份，原视频请单独保留。训练备注和导入的备份保存在当前浏览器。" },
];

export default function MarketingLanding() {
  const [menuOpen, setMenuOpen] = useState(false);
  const header = useRef<HTMLElement>(null);
  const menuButton = useRef<HTMLButtonElement>(null);
  const nav = useRef<HTMLElement>(null);
  const { user } = useAuth();

  useEffect(() => {
    if (!menuOpen) return;
    nav.current?.querySelector("a")?.focus({ preventScroll: true });
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setMenuOpen(false);
      menuButton.current?.focus({ preventScroll: true });
    };
    const closeOutside = (event: PointerEvent | FocusEvent) => {
      if (event.target instanceof Node && !header.current?.contains(event.target)) setMenuOpen(false);
    };
    const desktop = window.matchMedia("(min-width: 701px)");
    const closeOnResize = () => { if (desktop.matches) setMenuOpen(false); };
    document.addEventListener("keydown", closeOnEscape);
    document.addEventListener("pointerdown", closeOutside);
    document.addEventListener("focusin", closeOutside);
    desktop.addEventListener("change", closeOnResize);
    return () => {
      document.removeEventListener("keydown", closeOnEscape);
      document.removeEventListener("pointerdown", closeOutside);
      document.removeEventListener("focusin", closeOutside);
      desktop.removeEventListener("change", closeOnResize);
    };
  }, [menuOpen]);

  return <div className="marketing-page" onClickCapture={playPressFeedback}>
    <a className="mk-skip" href="#marketing-main">跳到主要内容</a>
    <header className="mk-header" ref={header}>
      <div className="mk-masthead mk-wrap">
        <a className="mk-brand" href="/welcome" aria-label="RallyMate 首页"><ProductIcon name="tennis" size={29} />RallyMate</a>
        <nav className={`mk-nav${menuOpen ? " is-open" : ""}`} id="marketing-nav" ref={nav} aria-label="首页导航">
          <a href="#experience" onClick={() => setMenuOpen(false)}>复盘示范</a>
          <a href="#questions" onClick={() => setMenuOpen(false)}>使用帮助</a>
          <a href="/?view=overview">训练空间</a>
        </nav>
        <div className="mk-account">
          {!user && <a href="/login">登录</a>}
          <button type="button" className="mk-menu-toggle" aria-expanded={menuOpen} aria-controls="marketing-nav"
            ref={menuButton} onClick={() => setMenuOpen(open => !open)}>
            <span>{menuOpen ? "关闭" : "菜单"}</span><i aria-hidden="true" />
          </button>
        </div>
      </div>
    </header>

    <main id="marketing-main">
      <section className="mk-cover mk-wrap" aria-labelledby="mk-hero-title">
        <div className="mk-cover-copy">
          <p className="mk-topic">网球训练复盘</p>
          <h1 id="mk-hero-title"><span>回看</span><span>这一拍<i>。</i></span></h1>
          <p className="mk-intro">回到训练原片，看清脚步、转体和挥拍。<br className="mk-wide-break" />带着一个具体的练习重点，再上场。</p>
          <a className="mk-primary" href="/?view=analysis">分析我的视频<ProductIcon name="arrow-right" size={23} /></a>
        </div>
        <figure className="mk-cover-frame">
          <div className="mk-cover-photo">
            {/* eslint-disable-next-line @next/next/no-img-element -- Local editorial asset is already encoded for the browser. */}
            <img src="/marketing/landing-forehand.webp" alt="红土球场上一位球员正手挥拍" width="1402" height="1122" fetchPriority="high" />
          </div>
          <figcaption>正手击球 <span>红土场</span></figcaption>
        </figure>
      </section>

      <TrainingExperience />

      <section className="mk-help mk-wrap" id="questions" aria-labelledby="mk-faq-title">
        <div id="workflow" className="mk-help-intro">
          <h2 id="mk-faq-title">使用帮助</h2>
          <a className="mk-text-link" href={user ? "/?view=overview" : "/register"}>{user ? "查看我的训练" : "申请训练账号"} <ProductIcon name="arrow-right" size={17} /></a>
        </div>
        <div className="mk-questions">
          {questions.map(question => <AnimatedDisclosure key={question.title} className="mk-question" bodyClassName="mk-answer"
            summary={<><span>{question.title}</span><i aria-hidden="true" /></>}>
            <p>{question.text}</p>
          </AnimatedDisclosure>)}
        </div>
      </section>
    </main>

    <footer className="mk-footer">
      <div className="mk-wrap">
        <a className="mk-brand" href="/welcome" aria-label="RallyMate 首页">RallyMate</a>
        <a className="mk-back" href="#marketing-main">回到顶部 <ProductIcon name="arrow-up-right" size={15} /></a>
      </div>
    </footer>
  </div>;
}
