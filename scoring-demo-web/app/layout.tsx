import type { Metadata } from "next";
import "./globals.css";
import "./product-workspace.css";
import "./product-report.css";
import "./product-analysis.css";
import "./product-motion.css";

export const metadata: Metadata = {
  title: "RallyMate · 网球训练空间",
  description: "记录每一次练习。上传网球视频，回看关键动作，让下一次训练更有方向。",
  icons: { icon: "/favicon.svg", shortcut: "/favicon.svg" },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="zh-CN"><body>{children}</body></html>;
}
