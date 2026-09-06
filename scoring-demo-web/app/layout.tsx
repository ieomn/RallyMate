import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "RallyMate Score Lab · 网球动作评分系统",
  description: "将 GS 底线击球与 FS 步伐事件的 298 项指标转化为可追溯、可验收的评分系统 Demo。",
  openGraph: {
    title: "RallyMate Score Lab",
    description: "298 项可追溯的网球动作评分规则，一套完整的证据优先评分系统。",
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
