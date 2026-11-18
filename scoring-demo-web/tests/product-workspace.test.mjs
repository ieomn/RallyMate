import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import ts from "typescript";
import { createElement, isValidElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

const compiled = new Map();
function compiledUrl(fileUrl) {
  if (fileUrl.pathname.endsWith(".mjs")) return fileUrl.href;
  // Keep source JSON imports shared with production, without a generated copy.
  if (fileUrl.pathname.endsWith(".json")) return `data:text/javascript;base64,${Buffer.from(`export default ${fs.readFileSync(fileUrl, "utf8")}`).toString("base64")}`;
  if (compiled.has(fileUrl.href)) return compiled.get(fileUrl.href);
  let js = ts.transpileModule(fs.readFileSync(fileUrl, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  // SSR assertions concern component output; CSS is bundled by the production test.
  js = js.replace(/^import\s+["'][^"']+\.css["'];?\s*$/gm, "");
  js = js.replace(/from "([^"]+)"/g, (_match, name) => {
    const local = new URL(/\.(?:json|mjs)$/.test(name) ? name : `${name}.ts`, fileUrl);
    return `from ${JSON.stringify(name.startsWith(".") ? compiledUrl(fs.existsSync(local) ? local : new URL(`${name}.tsx`, fileUrl)) : import.meta.resolve(name))}`;
  });
  const url = `data:text/javascript;base64,${Buffer.from(js).toString("base64")}`;
  compiled.set(fileUrl.href, url);
  return url;
}
const { ProductShell, TrainingHome } = await import(compiledUrl(new URL("../app/ProductWorkspace.tsx", import.meta.url)));
const { AuthProvider } = await import(compiledUrl(new URL("../app/auth/AuthProvider.tsx", import.meta.url)));
const noop = () => {};
const render = (Component, props) => renderToStaticMarkup(createElement(AuthProvider, null, createElement(Component, props)));
const textOf = html => html.replace(/<[^>]*>/g, "");
const reports = [
  { id: "job-one-12345678", name: "底线回看.mp4", viewedAt: "2026-10-02T03:00:00Z" },
  { id: "job-two-12345678", name: "发球练习.mp4", viewedAt: "2026-10-04T03:00:00Z" },
  { id: "job-three-12345678", name: "移动练习.mov", viewedAt: "2026-10-03T03:00:00Z" },
];
const homeProps = { reports: [], onOpenReport: noop, onNewAnalysis: noop, onNavigate: noop };

const offline = {
  source: "imported", id: `imported:${reports[0].id}%3Abackup.json%3A641`,
  name: "训练备份", viewedAt: "2026-11-19T16:00:00Z",
};
const screen = props => render(TrainingHome, { ...homeProps, ...props });
const rowFor = (html, name) => html.match(/<li>.*?<\/li>/gs)?.find(row => textOf(row).includes(name)) ?? "";

test("first visit offers one file picker while the empty history reports real account state", () => {
  const html = screen({});
  assert.equal((textOf(html).match(/选择视频/g) ?? []).length, 1);
  assert.match(textOf(html), /还没有训练记录/);
  assert.doesNotMatch(html, /\/v1\/jobs\/|拍摄指南|综合分|技术评分|演示报告/);
  for (const state of [{ loading: true }, { error: "服务未响应" }]) {
    const waiting = screen(state);
    assert.match(textOf(waiting), /选择视频/);
    assert.doesNotMatch(textOf(waiting), /还没有训练记录/);
  }
});

test("recent history sorts, deduplicates and validates its own rows without fetching thumbnails", () => {
  const html = screen({ reports: [...reports, reports[0],
    { id: "../unsafe", name: "不应显示", viewedAt: reports[0].viewedAt },
    { id: "job-bad-12345678", name: "无效日期", viewedAt: "invalid" },
  ] });
  assert.equal((html.match(/<li>/g) ?? []).length, 3);
  assert.equal((textOf(html).match(/最近查看 · /g) ?? []).length, 3);
  assert.ok(html.indexOf("发球练习.mp4") < html.indexOf("移动练习.mov"));
  assert.ok(html.indexOf("移动练习.mov") < html.indexOf("底线回看.mp4"));
  assert.doesNotMatch(html, /\/v1\/jobs\/|不应显示|无效日期|拍摄于|技术评分/);
});

test("active jobs remain visible separately from six recent records and failed tasks are not labelled reports", () => {
  const html = screen({ dateLabel: "提交时间", reports: [
    ...Array.from({ length: 14 }, (_, i) => ({ ...reports[1], id: `finished-job-${i}`, status: "succeeded" })),
    { ...reports[0], status: "queued" },
    { ...reports[2], status: "failed", viewedAt: "2026-10-20T10:00:00Z" },
  ] });
  assert.equal((html.match(/<li>/g) ?? []).length, 7);
  assert.ok(html.indexOf("底线回看.mp4") < html.indexOf("最近训练"));
  assert.match(textOf(rowFor(html, "底线回看.mp4")), /排队中.*提交时间.*查看进度/);
  assert.match(textOf(rowFor(html, "移动练习.mov")), /分析失败.*查看任务/);
  assert.match(textOf(rowFor(html, "发球练习.mp4")), /已完成.*查看报告/);
});

test("offline records use browser identity and disclose whether reopening requires the backup", () => {
  const html = screen({ reports: [{ ...reports[0], dateLabel: "提交时间" }, offline, { ...offline, id: "../invalid", name: "损坏记录" }] });
  assert.match(textOf(rowFor(html, offline.name)), /本地导入.*最近查看 · .*选择报告备份/);
  assert.match(textOf(rowFor(html, reports[0].name)), /提交时间 · /);
  assert.doesNotMatch(html, /\/v1\/jobs\/|损坏记录/);
  assert.match(textOf(screen({ reports: [offline], loadedImportId: offline.id })), /继续复盘/);
});

test("active work and drafts keep a dedicated resume control and cannot be overwritten by opening recent items", () => {
  const job = { ...reports[0], status: "running" };
  const done = { ...reports[1], status: "succeeded" };
  for (const state of [{ busy: true, activeJobId: job.id, stageLabel: "处理画面", currentName: job.name }, { draftName: "待提交片段.mp4" }]) {
    const html = screen({ ...state, reports: [job, done, offline] });
    if (state.busy) {
      assert.equal((textOf(html).match(/底线回看.mp4/g) ?? []).length, 1, "the polled task and server history refer to the same item");
      assert.match(textOf(rowFor(html, job.name)), /处理画面.*查看进度/);
      assert.match(html, /<button[^>]*disabled=""[^>]*>选择视频/);
    } else {
      const draftSection = html.match(/<section[^>]*aria-labelledby="home-draft-title"[^>]*>.*?<\/section>/s)?.[0] ?? "";
      assert.match(textOf(draftSection), /待提交.*待提交片段\.mp4.*已选视频.*继续提交/);
      assert.match(draftSection, /<button[^>]*>.*?继续提交.*?<\/button>/s);
    }
    const online = rowFor(html, done.name);
    assert.ok(online.includes(`href="/?view=analysis&amp;job=${done.id}"`));
    assert.match(online, /target="_blank"/);
    assert.match(rowFor(html, offline.name), /<button[^>]*disabled=""/);
  }
});

function elements(value) {
  if (Array.isArray(value)) return value.flatMap(elements);
  if (!isValidElement(value)) return [];
  return [value, ...elements(value.props.children)];
}

test("the shared desktop/mobile navigation has three text links, current-page semantics and native new-tab behavior", () => {
  const destinations = [["overview", "训练总览"], ["sessions", "训练记录"], ["analysis", "视频分析"]];
  for (const [current] of destinations) {
    const clicks = [];
    const shell = ProductShell({ view: current, analysisJobId: reports[0].id, onNavigate: next => clicks.push(next), children: "报告内容" });
    const nodes = elements(shell);
    assert.equal(nodes.filter(n => n.type === "nav").length, 1);
    const nav = nodes.find(n => n.type === "nav" && n.props["aria-label"] === "主导航");
    assert.equal(nav.props["data-view"], current);
    assert.equal(elements(nav.props.children).filter(n => n.props.className === "pw-nav-cursor" && n.props["aria-hidden"] === "true").length, 1);
    const links = elements(nav.props.children).filter(n => n.type === "a");
    assert.equal(links.length, 3);
    for (const [index, link] of links.entries()) {
      const [view, label] = destinations[index];
      const html = renderToStaticMarkup(link);
      assert.equal(textOf(html), label);
      assert.doesNotMatch(html, /<svg|<img|aria-hidden="true"/);
      assert.equal(link.props.href, `/?view=${view}${view === "analysis" ? `&job=${reports[0].id}` : ""}`);
      assert.equal(link.props["aria-current"], current === view ? "page" : undefined);
      let prevented = false;
      link.props.onClick({ button: 0, preventDefault() { prevented = true; } });
      assert.ok(prevented);
      for (const extra of [{ ctrlKey: true }, { metaKey: true }, { button: 1 }]) {
        link.props.onClick({ button: 0, ...extra, preventDefault() { assert.fail("modified link clicks use native navigation"); } });
      }
    }
    assert.deepEqual(clicks, destinations.map(([view]) => view).filter(view => view !== current));
    assert.ok(nodes.find(node => node.type === "a" && node.props.href === "/welcome"));
    assert.doesNotMatch(render(ProductShell, { view: current, onNavigate: noop }), /拍摄指南|新建分析/);
  }
});
