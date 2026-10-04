import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import ts from "typescript";
import { createElement, isValidElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

const compiled = new Map();
function compiledUrl(fileUrl) {
  if (compiled.has(fileUrl.href)) return compiled.get(fileUrl.href);
  let js = ts.transpileModule(fs.readFileSync(fileUrl, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  // SSR assertions concern component output; CSS is bundled by the production test.
  js = js.replace(/^import\s+["'][^"']+\.css["'];?\s*$/gm, "");
  js = js.replace(/from "([^"]+)"/g, (_match, name) => {
    const local = new URL(`${name}.ts`, fileUrl);
    return `from ${JSON.stringify(name.startsWith(".") ? compiledUrl(fs.existsSync(local) ? local : new URL(`${name}.tsx`, fileUrl)) : import.meta.resolve(name))}`;
  });
  const url = `data:text/javascript;base64,${Buffer.from(js).toString("base64")}`;
  compiled.set(fileUrl.href, url);
  return url;
}
const { ProductShell, TrainingHome, SessionLibrary, CaptureGuide } = await import(compiledUrl(new URL("../app/ProductWorkspace.tsx", import.meta.url)));
const noop = () => {};
const render = (Component, props) => renderToStaticMarkup(createElement(Component, props));
const textOf = html => html.replace(/<[^>]*>/g, "");
const reports = [
  { id: "job-one-12345678", name: "底线回看.mp4", viewedAt: "2026-10-02T03:00:00Z" },
  { id: "job-two-12345678", name: "发球练习.mp4", viewedAt: "2026-10-04T03:00:00Z" },
  { id: "job-three-12345678", name: "移动练习.mov", viewedAt: "2026-10-03T03:00:00Z" },
];
const homeProps = { reports: [], onOpenReport: noop, onNewAnalysis: noop, onNavigate: noop };

test("overview empty state invites a real video without inventing reports or performance", () => {
  const html = render(TrainingHome, homeProps);
  const text = textOf(html);
  assert.match(text, /第一份训练记录，从这里开始/);
  assert.match(text, /上传训练视频/);
  assert.match(text, /最近的训练0/);
  assert.doesNotMatch(html, /class="pw-report-card"|\/v1\/jobs\//);
  assert.doesNotMatch(text, /综合分|技术评分|进步\s*\d|\d+\s*(?:分|小时|分钟|%)|演示报告/);
});

test("overview uses only validated supplied history and labels its timestamps as recently viewed", () => {
  const html = render(TrainingHome, { ...homeProps, reports: [
    ...reports, reports[0], { id: "../unsafe", name: "不应显示", viewedAt: reports[0].viewedAt },
    { id: "job-bad-12345678", name: "无效日期", viewedAt: "invalid" },
  ] });
  assert.equal((html.match(/class="pw-report-card"/g) || []).length, 3);
  assert.equal((textOf(html).match(/最近查看 · /g) || []).length, 3);
  assert.match(textOf(html), /最近的训练3/);
  assert.ok(html.indexOf("发球练习.mp4") < html.indexOf("移动练习.mov"));
  assert.ok(html.indexOf("移动练习.mov") < html.indexOf("底线回看.mp4"));
  for (const report of reports) assert.ok(html.includes(`/v1/jobs/${report.id}/artifacts/preview.jpg`));
  assert.doesNotMatch(textOf(html), /不应显示|无效日期|训练日期|拍摄于|技术评分/);
});

test("library shows the actual count and explains that view dates are not recording dates", () => {
  const html = render(SessionLibrary, { reports, onOpenReport: noop, onNewAnalysis: noop });
  const text = textOf(html);
  assert.match(text, /共 3 份最近记录/);
  assert.match(text, /最近查看/);
  assert.match(text, /这里的时间不是视频拍摄时间/);
  assert.match(html, /type="search"/);
  assert.match(html, /按训练视频名称搜索/);
  assert.match(html, /value="desc"/); assert.match(html, /value="asc"/);
  for (const report of reports) assert.ok(html.includes(`打开训练报告：${report.name}`));
  const empty = render(SessionLibrary, { reports: [], onOpenReport: noop, onNewAnalysis: noop });
  assert.match(textOf(empty), /共 0 份最近记录/);
  assert.match(textOf(empty), /第一份训练记录，从这里开始/);
  assert.doesNotMatch(empty, /class="pw-report-card"/);
});

function elements(value) {
  if (Array.isArray(value)) return value.flatMap(elements);
  if (!isValidElement(value)) return [];
  return [value, ...elements(value.props.children)];
}

test("desktop and mobile navigation call all four destinations and expose the current page", () => {
  const views = ["overview", "sessions", "analysis", "guide"];
  for (const current of views) {
    const selected = [];
    let newAnalyses = 0;
    const tree = ProductShell({ view: current, onNavigate: view => selected.push(view), onNewAnalysis: () => { newAnalyses += 1; }, children: "报告内容" });
    const nodes = elements(tree);
    for (const label of ["主导航", "移动主导航"]) {
      const nav = nodes.find(node => node.type === "nav" && node.props["aria-label"] === label);
      assert.ok(nav, label);
      const buttons = elements(nav.props.children).filter(node => node.type === "button");
      assert.equal(buttons.length, 4);
      assert.equal(buttons.filter(node => node.props["aria-current"] === "page").length, 1);
      assert.equal(buttons[views.indexOf(current)].props["aria-current"], "page");
      buttons.forEach(button => { assert.equal(button.props.disabled, undefined); button.props.onClick(); });
    }
    assert.deepEqual(selected, [...views, ...views]);
    const create = nodes.find(node => node.type === "button" && node.props.className?.includes("pw-topbar-action"));
    assert.equal(create.props.disabled, false); create.props.onClick(); assert.equal(newAnalyses, 1);
  }
});

test("every workspace view can return to the public homepage, including while analysis is busy", () => {
  for (const view of ["overview", "sessions", "analysis", "guide"]) {
    for (const busy of [false, true]) {
      const shell = ProductShell({ view, busy, onNavigate: noop, onNewAnalysis: noop, children: "报告内容" });
      const nodes = elements(shell);
      const brand = nodes.find(node => node.props.className === "pw-brand");
      const returnHome = nodes.find(node => node.props.className === "pw-home-link");
      for (const link of [brand, returnHome]) {
        assert.ok(link, `${view} must have both a brand and a visible return link`);
        // Native navigation must work without relying on the client router or an idle job.
        assert.equal(link.type, "a");
        assert.equal(link.props.href, "/welcome");
        assert.equal(link.props.disabled, undefined);
        assert.equal(link.props.onClick, undefined);
      }
      assert.match(textOf(renderToStaticMarkup(returnHome)), /返回首页/);
      const topbar = nodes.find(node => node.type === "header");
      assert.ok(elements(topbar).includes(returnHome), "return link is in the header, outside the desktop-only sidebar");
      const overview = nodes.filter(node => node.type === "button" && elements(node).some(child => child.type === "span" && child.props.children === "训练总览"));
      assert.equal(overview.length, 2, "returning to the homepage does not remove desktop or mobile overview navigation");
    }
  }
});

test("busy overview exposes progress while other navigation remains available", () => {
  const html = render(TrainingHome, { ...homeProps, busy: true, currentName: "正在处理.mp4" });
  assert.match(textOf(html), /你的训练视频正在分析/);
  assert.match(textOf(html), /正在处理.mp4/); assert.match(textOf(html), /查看进度/);
  const shell = render(ProductShell, { view: "overview", onNavigate: noop, onNewAnalysis: noop, busy: true, children: "进度" });
  assert.match(shell, /aria-label="分析进行中"/);
  assert.match(shell, /pw-topbar-action" disabled=""/);
  assert.match(shell, /aria-label="移动主导航"/);
});

test("capture checklist is optional guidance with a usable new-analysis action", () => {
  const html = render(CaptureGuide, { onNewAnalysis: noop });
  assert.match(textOf(html), /不会影响视频能否上传/);
  assert.match(textOf(html), /已检查 0 \/ 4 项/);
  assert.equal((html.match(/type="checkbox"/g) || []).length, 4);
  assert.match(html, /<button[^>]*>开始视频分析/);
});
