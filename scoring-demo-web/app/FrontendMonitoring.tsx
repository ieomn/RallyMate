"use client";

import { ErrorBoundary } from "@sentry/react";
import type { ReactNode } from "react";
import "./lib/monitoring";

export default function FrontendMonitoring({ children }: { children: ReactNode }) {
  return <ErrorBoundary fallback={({ resetError }) => (
    <main className="account-gate" role="alert">
      <h1>页面暂时遇到了问题</h1>
      <p>请重试，或返回首页重新进入训练空间。</p>
      <button type="button" onClick={resetError}>重试</button>
      {/* A full navigation also resets a failed Vinext client runtime. */}
      {/* eslint-disable-next-line @next/next/no-html-link-for-pages */}
      <a href="/">返回首页</a>
    </main>
  )}>{children}</ErrorBoundary>;
}
