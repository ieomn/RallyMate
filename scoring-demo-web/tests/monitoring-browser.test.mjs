import assert from "node:assert/strict";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import test from "node:test";

let playwrightModule = process.env.RALLYMATE_PLAYWRIGHT_MODULE;
if (!playwrightModule) {
  try { playwrightModule = import.meta.resolve("playwright"); } catch { /* Browser tests also accept an existing host installation. */ }
}

test("real browser: disabled monitoring, global errors, React fallback, transactions and account UUID lifecycle", {
  skip: playwrightModule ? false : "Set RALLYMATE_PLAYWRIGHT_MODULE to an installed Playwright module for browser verification",
  timeout: 90000,
}, async () => {
  const { chromium } = await import(playwrightModule.startsWith("file:") ? playwrightModule : pathToFileURL(playwrightModule).href);
  const { createServer } = await import("vite");
  const { default: react } = await import("@vitejs/plugin-react");
  const project = fileURLToPath(new URL("..", import.meta.url));
  const scratch = path.join(project, ".codex_tmp");
  await mkdir(scratch, { recursive: true });
  const root = await mkdtemp(path.join(scratch, "monitoring-browser-"));
  let server, browser;
  const secret = "PRIVATE_BROWSER_PASSWORD_COOKIE_TOKEN_REASON_VIDEO";
  const uuid = "64ac6e31-0b6c-4ba7-9342-c959fcaa9ff1";
  try {
    await writeFile(path.join(root, "index.html"), '<!doctype html><html lang="zh"><body><div id="root"></div><script type="module" src="/main.tsx"></script></body></html>');
    await writeFile(path.join(root, "main.tsx"), `
      import React, { useState } from 'react';
      import { createRoot } from 'react-dom/client';
      import * as Sentry from '@sentry/react';
      import { initializeBrowserMonitoring } from '../../app/lib/monitoring';
      import FrontendMonitoring from '../../app/FrontendMonitoring';
      import { AuthProvider, useAuth } from '../../app/auth/AuthProvider';
      const envelopes = [];
      window.probe = {
        envelopes, client: () => !!Sentry.getClient(), flush: () => Sentry.flush(2000),
        user: () => Sentry.getIsolationScope().getUser(),
        options: () => ({ transport: Sentry.getClient()?.getOptions().transportOptions, propagation: Sentry.getClient()?.getOptions().tracePropagationTargets }),
        init: dsn => initializeBrowserMonitoring({ VITE_SENTRY_DSN: dsn, VITE_SENTRY_TRACES_SAMPLE_RATE: '1' }, () => ({ send: async e => { envelopes.push(e); return { statusCode: 200 }; }, flush: async () => true })),
        globalError: () => setTimeout(() => { throw new TypeError('${secret}'); }, 0),
        rejection: () => { Promise.reject(new RangeError('${secret}')); },
        transaction: () => Sentry.startSpan({ name: '/admin?password=${secret}', op: 'navigation', forceTransaction: true }, () => {
          Sentry.startSpan({ name: '/api/auth/login?password=${secret}', op: 'http.client' }, span => span.setAttribute('password', '${secret}'));
        }),
      };
      function Broken() { throw new SyntaxError('${secret}'); }
      function Controls() {
        const auth = useAuth(); const [crash, setCrash] = useState(false);
        if (crash) return <Broken />;
        return <><p data-testid="account">{auth.loading ? 'loading' : auth.user?.id ?? 'anonymous'}</p>
          <button onClick={() => auth.login('test@example.test', '${secret}')}>login</button>
          <button onClick={() => auth.logout()}>logout</button>
          <button onClick={() => setCrash(true)}>crash</button></>;
      }
      createRoot(document.getElementById('root')).render(<FrontendMonitoring><AuthProvider><Controls /></AuthProvider></FrontendMonitoring>);
    `);
    server = await createServer({ configFile: false, root, plugins: [react()], logLevel: "silent", server: { host: "127.0.0.1", port: 0, forwardConsole: false, fs: { allow: [project] } },
      define: { "import.meta.env.VITE_SENTRY_DSN": JSON.stringify("") } });
    await server.listen();
    const origin = `http://127.0.0.1:${server.httpServer.address().port}`;
    browser = await chromium.launch({ headless: true });
    const external = [];
    async function pageWithAuth() {
      const context = await browser.newContext();
      let loggedIn = false;
      await context.route("**/*", async (route) => {
        const url = new URL(route.request().url());
        if (url.origin !== origin) { external.push(url.origin); await route.abort(); return; }
        if (!url.pathname.startsWith("/api/auth/")) { await route.continue(); return; }
        if (url.pathname.endsWith("/login")) loggedIn = true;
        if (url.pathname.endsWith("/logout")) { loggedIn = false; await route.fulfill({ status: 200, contentType: "application/json", body: '{"ok":true}' }); return; }
        await route.fulfill({ status: loggedIn ? 200 : 401, contentType: "application/json", body: JSON.stringify(loggedIn ? {
          user: { id: uuid, email: `${secret}@example.test`, username: secret, role: "ADMIN", status: "APPROVED", must_change_password: false }, csrf_token: secret,
        } : { detail: { code: "session_required", message: "Sign in" } }) });
      });
      const page = await context.newPage();
      await page.goto(origin);
      await page.getByTestId("account").filter({ hasText: "anonymous" }).waitFor();
      return { page, context };
    }
    const disabled = await pageWithAuth();
    assert.equal(await disabled.page.evaluate(() => window.probe.client()), false);
    await disabled.page.evaluate(() => { window.probe.init(""); window.probe.globalError(); window.probe.rejection(); });
    await disabled.page.getByRole("button", { name: "crash", exact: true }).click();
    await disabled.page.getByRole("heading", { name: "页面暂时遇到了问题" }).waitFor();
    assert.equal(await disabled.page.evaluate(() => window.probe.client()), false);
    assert.deepEqual(await disabled.page.evaluate(() => window.probe.envelopes), []);
    assert.doesNotMatch(await disabled.page.locator("body").innerText(), new RegExp(secret));
    await disabled.context.close();

    const enabled = await pageWithAuth();
    const page = enabled.page;
    await page.evaluate(() => window.probe.init("http://public@127.0.0.1:9/1"));
    assert.equal(await page.evaluate(() => window.probe.client()), true);
    assert.deepEqual(await page.evaluate(() => window.probe.options()), { transport: { fetchOptions: { credentials: "omit", referrerPolicy: "no-referrer" } }, propagation: [] });
    await page.getByRole("button", { name: "login", exact: true }).click();
    await page.getByTestId("account").filter({ hasText: uuid }).waitFor();
    assert.deepEqual(await page.evaluate(() => window.probe.user()), { id: uuid });
    await page.evaluate(() => window.probe.globalError());
    await page.waitForFunction(() => window.probe.envelopes.some(e => e[1].some(i => i[1].exception?.values.some(v => v.type === "TypeError"))));
    await page.evaluate(() => window.probe.rejection());
    await page.waitForFunction(() => window.probe.envelopes.some(e => e[1].some(i => i[1].exception?.values.some(v => v.type === "RangeError"))));
    await page.evaluate(() => window.probe.transaction());
    await page.waitForFunction(() => window.probe.envelopes.some(e => e[1].some(i => i[0].type === "transaction")));
    await page.getByRole("button", { name: "logout", exact: true }).click();
    await page.getByTestId("account").filter({ hasText: "anonymous" }).waitFor();
    assert.ok(Object.values(await page.evaluate(() => window.probe.user())).every(value => value === undefined));
    await page.getByRole("button", { name: "crash", exact: true }).click();
    await page.getByRole("heading", { name: "页面暂时遇到了问题" }).waitFor();
    await page.evaluate(() => window.probe.flush());
    const envelopes = await page.evaluate(() => window.probe.envelopes);
    assert.doesNotMatch(JSON.stringify(envelopes), new RegExp(secret));
    const events = envelopes.flatMap(e => e[1].map(i => i[1]));
    assert.ok(events.some(e => e.exception?.values.some(v => v.type === "SyntaxError" && v.stacktrace?.frames.length > 0)));
    assert.ok(events.some(e => e.user?.id === uuid));
    assert.ok(events.some(e => e.type === "transaction" && e.transaction === "/admin"));
    assert.ok(events.some(e => e.exception?.values.some(v => v.type === "SyntaxError") && !e.user));
    assert.doesNotMatch(await page.locator("body").innerText(), new RegExp(secret));
    await page.getByRole("button", { name: "重试", exact: true }).click();
    await page.getByTestId("account").filter({ hasText: "anonymous" }).waitFor();
    assert.deepEqual(external, []);
    await enabled.context.close();
  } finally {
    await browser?.close();
    await server?.close();
    assert.ok(path.resolve(root).startsWith(path.resolve(scratch) + path.sep));
    await rm(root, { recursive: true, force: true });
  }
});
