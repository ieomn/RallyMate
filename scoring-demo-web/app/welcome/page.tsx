import type { Metadata } from "next";
import MarketingLanding from "./MarketingLanding";

export const metadata: Metadata = {
  title: "RallyMate · 回看这一拍",
  description: "上传网球训练视频，沿原片回看准备、击球与随挥，保存下一次想练的动作。也可以先切换首页的三个发球定格，体验动作复盘。",
};

export default function WelcomePage() {
  return <MarketingLanding />;
}
