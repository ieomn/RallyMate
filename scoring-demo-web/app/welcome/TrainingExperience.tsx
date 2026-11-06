/* eslint-disable @next/next/no-html-link-for-pages -- Enter the workspace with a full page load in vinext. */
"use client";

import { ProductIcon } from "../ProductWorkspace";

const features = [
  { icon: "play" as const, title: "原片与三维回放", text: "慢放、片段定位、循环播放，结合关键点叠加与人体三维回放，看清脚步、转体和挥拍细节。" },
  { icon: "frame" as const, title: "五维动作评分", text: "从移动支撑、准备引拍、动作协调、击球姿态、随挥恢复五个环节查看已测动作表现，找到对应片段与测量依据。" },
  { icon: "tennis" as const, title: "球路与场地回看", text: "让已识别的球路轨迹随视频同步显示，结合球场标定与俯视图，观察移动路径和击球统计。" },
  { icon: "sessions" as const, title: "训练建议与记录", text: "获取具体练习建议，保存下次训练的关注点。导出训练报告和备份，把每次复盘积累下来。" },
];

export function TrainingExperience() {
  return <section className="marketing-experience" id="experience" aria-labelledby="training-features-heading">
    <div className="mx-heading">
      <h2 id="training-features-heading">一段训练视频，<br /><span>看清自己的表现。</span></h2>
      <p className="mx-heading-copy">动作分析、视频回放、训练建议，<br />在一份报告里连起来。</p>
    </div>
    <div className="mx-feature-intro">
      <div>
        <span className="mx-feature-label">视频分析 · 动作回放 · 训练指导</span>
        <h3>从这次表现，找到下一次练习的重点。</h3>
        <p>上传你的网球训练视频，查看动作评分、人体三维回放和球路轨迹。结合对应片段理解动作细节，再把练习建议带到下一次上场。</p>
        <a href="/?view=analysis" className="mx-feature-link">分析我的训练视频 <ProductIcon name="arrow-up-right" size={18} /></a>
      </div>
      <ol>
        <li><span aria-hidden="true">01</span><div><strong>上传训练视频</strong><small>支持断点续传，实时查看分析进度</small></div></li>
        <li><span aria-hidden="true">02</span><div><strong>回看动作表现</strong><small>动作评分、关键片段与视频回放一起查看</small></div></li>
        <li><span aria-hidden="true">03</span><div><strong>保存练习重点</strong><small>获取训练建议，导出报告，记录每次复盘</small></div></li>
      </ol>
    </div>
    <div className="mx-feature-grid">
      {features.map(feature => <article className="mx-feature" key={feature.title}>
        <ProductIcon name={feature.icon} size={25} />
        <h3>{feature.title}</h3>
        <p>{feature.text}</p>
      </article>)}
    </div>
  </section>;
}
