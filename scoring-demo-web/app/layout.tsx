import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "RallyMate · 训练报告",
  description: "回看网球训练视频，查看测量证据参考分、步伐与转体的复核重点。",
  icons: { icon: "/favicon.svg", shortcut: "/favicon.svg" },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="zh-CN"><body>{children}</body></html>;
}
