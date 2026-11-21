import type { Metadata } from "next";
import MarketingLanding from "./MarketingLanding";

export const metadata: Metadata = {
  title: "RallyMate — 下一拍，更有方向。",
  description: "上传网球训练录像，慢放关键片段，查看动作分析，保存下次要练的动作。",
};

export default function WelcomePage() {
  return <MarketingLanding />;
}
