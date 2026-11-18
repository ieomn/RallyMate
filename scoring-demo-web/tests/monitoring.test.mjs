import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { PRIVATE_DATA_COLLECTION, monitoringSampleRate, monitoringUser, privateTransport, safeUrl, scrubBreadcrumb, scrubEvent } from "../observability/privacy.mjs";

const uuid = "64ac6e31-0b6c-4ba7-9342-c959fcaa9ff1";
const secret = "PRIVATE_PASSWORD_COOKIE_TOKEN_REASON_VIDEO";
const dirtyEvent = {
  event_id: "a".repeat(32), level: "error", platform: "javascript", release: "rallymate@1.0.0", environment: "test",
  timestamp: 123, message: secret, logentry: { message: secret }, extra: { password: secret }, tags: { application_reason: secret },
  user: { id: uuid, email: secret, username: secret, ip_address: "192.168.1.1" },
  request: { url: `https://user:${secret}@example.test/api/admin/users/${uuid}?temporary_password=${secret}#${secret}`, method: "POST", cookies: secret, headers: { authorization: secret }, data: { password: secret } },
  contexts: { browser: { name: secret }, react: { componentStack: secret }, trace: { trace_id: "b".repeat(32), span_id: "c".repeat(16), op: "http.client", data: { password: secret } } },
  exception: { values: [{ type: "TypeError", value: secret, mechanism: { type: secret, data: { password: secret }, handled: false }, stacktrace: { frames: [
    { filename: `D:/Users/${secret}/rallymate/app/page.tsx?token=${secret}`, function: "render", lineno: 42, colno: 7, vars: { password: secret }, pre_context: [secret], context_line: secret, post_context: [secret] },
    { filename: `https://example.test/assets/index.js?token=${secret}`, lineno: 3 },
    { filename: `D:/data/uploads/${secret}.mp4`, lineno: 1 },
  ] } }] },
  breadcrumbs: [
    { category: "console", message: secret, data: { arguments: [secret] } },
    { category: "ui.click", message: secret, data: { target: secret } },
    { category: "fetch", message: secret, data: { url: `/api/auth/login?password=${secret}`, method: "POST", status_code: 403, body: secret, headers: { cookie: secret } } },
  ],
};

test("privacy allowlist keeps UUID and source locations while removing sensitive payloads", () => {
  const clean = scrubEvent(dirtyEvent);
  assert.doesNotMatch(JSON.stringify(clean), new RegExp(secret));
  assert.deepEqual(clean.user, { id: uuid });
  assert.deepEqual(clean.request, { url: "/api/admin/users/:id", method: "POST" });
  assert.equal(clean.exception.values[0].type, "TypeError");
  assert.equal(clean.exception.values[0].value, "Error details removed");
  assert.deepEqual(clean.exception.values[0].stacktrace.frames, [{ filename: "app/page.tsx", function: "render", lineno: 42, colno: 7 }, { filename: "assets/index.js", lineno: 3 }]);
  assert.deepEqual(clean.breadcrumbs, [{ category: "fetch", data: { url: "/api/auth/login", method: "POST", status_code: 403 } }]);
  assert.equal(clean.extra, undefined);
  assert.equal(clean.contexts.react, undefined);
  assert.deepEqual(scrubEvent(clean), clean);
});

test("trace payloads lose descriptions, arbitrary span attributes and query strings", () => {
  const clean = scrubEvent({ ...dirtyEvent, type: "transaction", transaction: `/admin?password=${secret}`, spans: [{ trace_id: "a".repeat(32), span_id: "b".repeat(16), op: "http.client", description: `POST /api/auth/login?password=${secret}`, start_timestamp: 1, timestamp: 2, data: { password: secret, "http.request.body": secret } }] });
  assert.equal(clean.transaction, "/admin");
  assert.equal(clean.spans[0].description, "/api/auth/login");
  assert.equal(clean.spans[0].op, "http.client");
  assert.doesNotMatch(JSON.stringify(clean), new RegExp(secret));
});

test("URL filtering removes storage filenames, dynamic IDs and all origins", () => {
  for (const input of [`/uploads/${secret}.mp4`, `/v1/jobs/a/report/${secret}.json`, `https://host/runs/${secret}/plot.png`]) assert.equal(safeUrl(input), "/:private-file");
  assert.equal(safeUrl(`https://${secret}.example/admin?token=${secret}`), "/admin");
  assert.equal(safeUrl(`/api/jobs/${secret}`), "/api/jobs/:id");
  assert.equal(safeUrl(`file:///D:/Users/${secret}/report.json`), "/:route");
  assert.equal(scrubBreadcrumb({ category: "console", message: secret }), null);
  assert.equal(monitoringUser(secret), null);
  assert.equal(monitoringUser(null), null);
});

test("disabled sensitive collection and sample-rate defaults are explicit", () => {
  assert.deepEqual(PRIVATE_DATA_COLLECTION.httpBodies, []);
  for (const key of ["cookies", "httpHeaders", "urlQueryParams", "userInfo", "stackFrameVariables"]) assert.equal(PRIVATE_DATA_COLLECTION[key], false);
  assert.equal(PRIVATE_DATA_COLLECTION.frameContextLines, 0);
  for (const value of [undefined, "", "invalid", "-1", "2"]) assert.equal(monitoringSampleRate(value), 0.05);
  assert.equal(monitoringSampleRate("0"), 0);
  assert.equal(monitoringSampleRate("1"), 1);
});

test("transport filters attachments, replay, logs, metrics, sessions and envelope metadata", async () => {
  const sent = [];
  const transport = privateTransport(() => ({ send: async (value) => { sent.push(value); return { statusCode: 200 }; }, flush: async () => true }))({});
  await transport.send([{ event_id: "a".repeat(32), trace: { transaction: secret }, sdk: { secret } }, [
    [{ type: "event", filename: secret }, dirtyEvent],
    ...["attachment", "replay_event", "replay_recording", "log", "trace_metric", "session", "span"].map((type) => [{ type }, secret]),
  ]]);
  assert.equal(sent.length, 1);
  assert.equal(sent[0][1].length, 1);
  assert.deepEqual(sent[0][0], { event_id: "a".repeat(32) });
  assert.doesNotMatch(JSON.stringify(sent), new RegExp(secret));
  await transport.send([{}, [[{ type: "attachment" }, secret]]]);
  assert.equal(sent.length, 1);
  assert.equal(await transport.flush(100), true);
});

function runNode(source) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, ["--input-type=module", "-e", source], { cwd: fileURLToPath(new URL("..", import.meta.url)), env: { ...process.env, SENTRY_FRONTEND_DSN: "" }, stdio: ["ignore", "pipe", "pipe"] });
    let stdout = "", stderr = "";
    child.stdout.on("data", (chunk) => { stdout += chunk; });
    child.stderr.on("data", (chunk) => { stderr += chunk; });
    child.on("error", reject);
    child.on("close", (code) => resolve({ code, stdout, stderr }));
  });
}

test("empty server DSN installs no handlers and never creates a transport", async () => {
  const result = await runNode(`
    import assert from 'node:assert/strict';
    import { initializeServerMonitoring } from './observability/server.mjs';
    const before = process.listenerCount('uncaughtException');
    let created = 0;
    const client = await initializeServerMonitoring({}, () => { created++; throw Error('unexpected transport'); });
    assert.equal(client, undefined); assert.equal(created, 0);
    assert.equal(process.listenerCount('uncaughtException'), before);
  `);
  assert.equal(result.code, 0, result.stderr);
});

test("real Node SDK captures SSR console errors through a private fake transport", async () => {
  const result = await runNode(`
    import assert from 'node:assert/strict';
    import { initializeServerMonitoring } from './observability/server.mjs';
    import * as Sentry from '@sentry/node';
    const envelopes = [];
    await initializeServerMonitoring({ SENTRY_FRONTEND_DSN: 'http://public@127.0.0.1:9/1', SENTRY_FRONTEND_TRACES_SAMPLE_RATE: '1' }, () => ({ send: async e => { envelopes.push(e); return { statusCode: 200 }; }, flush: async () => true }));
    Sentry.setUser({ id: '${uuid}', email: '${secret}' });
    console.error(new TypeError('${secret}'));
    Sentry.captureEvent(${JSON.stringify(dirtyEvent)});
    await Sentry.flush(2000);
    const events = envelopes.flatMap(e => e[1].map(i => i[1]));
    assert.ok(events.length >= 2);
    assert.doesNotMatch(JSON.stringify(envelopes), /${secret}/);
    assert.ok(events.some(e => e.exception?.values?.[0].type === 'TypeError'));
    assert.ok(events.some(e => e.user?.id === '${uuid}'));
    assert.equal(Sentry.getClient().getOptions().traceLifecycle, 'static');
    await Sentry.close(2000);
  `);
  assert.equal(result.code, 0, result.stderr.replaceAll(secret, "[test secret]"));
});

for (const failure of ["uncaught", "rejection"]) test(`real Node SDK records ${failure} failures before process exit`, async () => {
  const result = await runNode(`
    import { initializeServerMonitoring } from './observability/server.mjs';
    const entries = [];
    await initializeServerMonitoring({ SENTRY_FRONTEND_DSN: 'http://public@127.0.0.1:9/1' }, () => ({ send: async e => { entries.push(e); return { statusCode: 200 }; }, flush: async () => true }));
    process.on('exit', () => { process.stdout.write(JSON.stringify({ captured: entries.some(e => e[1].some(i => i[1].exception)), safe: !JSON.stringify(entries).includes('${secret}') })); });
    setTimeout(() => { ${failure === "uncaught" ? `throw new Error('${secret}');` : `Promise.reject(new Error('${secret}'));`} }, 0);
  `);
  assert.equal(result.code, 1);
  assert.deepEqual(JSON.parse(result.stdout), { captured: true, safe: true });
});
