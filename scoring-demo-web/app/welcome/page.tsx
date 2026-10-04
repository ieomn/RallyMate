import type { Metadata } from "next";
import MarketingLanding from "./MarketingLanding";

export const metadata: Metadata = {
  title: "RallyMate — 下一拍，更有方向。",
  description: "用一段训练视频，回到值得看清的瞬间。体验 RallyMate 网球训练复盘，带着更具体的问题，走向下一次上场。",
};

export default function WelcomePage() {
  return <MarketingLanding />;
}
