import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "RallyMate · 动作识别工作台",
  description: "上传网球动作视频，查看动作识别、动作参考分与证据回放。",
  icons: { icon: "/favicon.svg", shortcut: "/favicon.svg" },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="zh-CN"><body>{children}</body></html>;
}
