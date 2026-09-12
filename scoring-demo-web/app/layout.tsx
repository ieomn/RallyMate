import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "RallyMate Score Lab · 网球动作评分系统",
  description: "将 298 条兼容评分规则与 24 项最新技术定义连接到视频证据、球轨迹预览和球拍观测。",
  openGraph: {
    title: "RallyMate Score Lab",
    description: "视频上传、证据回放、球轨迹预览与球拍 bbox 观测，统一在一套证据优先的网球动作评分工作台。",
    images: [{ url: "/og.png", width: 1536, height: 1024, alt: "RallyMate Score Lab" }],
  },
  icons: {
    icon: "/favicon.svg",
    shortcut: "/favicon.svg",
  },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
