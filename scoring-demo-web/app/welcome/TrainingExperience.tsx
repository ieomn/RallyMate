/* eslint-disable @next/next/no-html-link-for-pages -- Enter the workspace with a full page load in vinext. */
"use client";

import { ProductIcon } from "../ProductWorkspace";

const features = [
  { icon: "play" as const, title: "原片与三维回放", text: "慢放或循环看同一拍。有三维回放时，可以换个角度看转体和挥拍。" },
  { icon: "frame" as const, title: "五维动作评分", text: "展开详细数据，查看移动支撑、准备引拍、动作协调、击球姿态和随挥恢复的评分与依据。" },
  { icon: "tennis" as const, title: "球路与场地回看", text: "识别出的球路会跟随原片播放。完成场地标定后，还可以从俯视图看移动路线。" },
  { icon: "sessions" as const, title: "训练建议与记录", text: "在报告中保存练习重点，训练记录里可以再查。也可以下载报告给教练看。" },
];

export function TrainingExperience() {
  return <section className="marketing-experience" id="experience" aria-labelledby="training-features-heading">
    <div className="mx-heading">
      <h2 id="training-features-heading">打开报告，<br /><span>先看关键片段。</span></h2>
      <p className="mx-heading-copy">想知道练习建议从哪儿来？<br />展开详细数据就能看动作测量。</p>
    </div>
    <div className="mx-feature-intro">
      <div>
        <span className="mx-feature-label">动作回放</span>
        <h3>这一拍，哪里可以改？</h3>
        <p>点选片段，原片会停在对应的位置。对照旁边的建议，看完就能记下下次要练的动作。</p>
        <a href="/?view=analysis" className="mx-feature-link">分析我的训练视频 <ProductIcon name="arrow-up-right" size={18} /></a>
      </div>
      <ol>
        <li><span aria-hidden="true">01</span><div><strong>上传训练视频</strong><small>确认预览后提交，可随时查看进度</small></div></li>
        <li><span aria-hidden="true">02</span><div><strong>回看动作片段</strong><small>拖动时间轴，或放慢速度反复看</small></div></li>
        <li><span aria-hidden="true">03</span><div><strong>保存练习重点</strong><small>下次训练前，从记录里打开</small></div></li>
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
