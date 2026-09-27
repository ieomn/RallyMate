"use client";

import { useState } from "react";
import { buildPracticeReport, buildReportBackup, getReportExportGate, renderReportHtml, renderReportMarkdown, reportDownloadName, type ReportExportInput } from "./lib/report-export";

export default function ReportExportActions({ input }: { input: ReportExportInput }) {
  const [error, setError] = useState("");
  const gate = getReportExportGate(input);
  function download(extension: "md" | "html" | "json") {
    try {
      const report = buildPracticeReport(input);
      const content = extension === "md" ? renderReportMarkdown(report) : extension === "html" ? renderReportHtml(report) : JSON.stringify(buildReportBackup(input, report.exportedAt), null, 2);
      const type = extension === "html" ? "text/html;charset=utf-8" : extension === "md" ? "text/markdown;charset=utf-8" : "application/json;charset=utf-8";
      const url = URL.createObjectURL(new Blob([content], { type }));
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = reportDownloadName(report, extension);
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      // Leave enough time for the browser to begin consuming the Blob.
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      setError("");
    } catch (caught) { setError(caught instanceof Error ? caught.message : "报告暂时无法导出，请重新读取分析结果。"); }
  }
  return <section className="report-actions" aria-label="导出分析报告">
    <div><strong>{input.mode === "demo" ? "导出演示报告" : "导出完整分析报告"}</strong><p role="status">{error || gate.reason}</p></div>
    <button className="ghost-button" disabled={!gate.allowed} onClick={() => download("md")}>下载 Markdown 报告 ↓</button>
    <button className="ghost-button" disabled={!gate.allowed} onClick={() => download("html")}>下载 HTML 报告 ↓</button>
    <details><summary>高级：JSON 数据备份</summary><p>JSON 用于保留分析数据；Markdown 与 HTML 可直接阅读。HTML 可在浏览器打开后打印为 PDF。</p><button className="ghost-button" disabled={!gate.allowed} onClick={() => download("json")}>下载 JSON 备份 ↓</button></details>
  </section>;
}
