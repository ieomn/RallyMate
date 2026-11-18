import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { beforeEach, after, test } from "node:test";
import ts from "typescript";

function moduleUrl(source) {
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
  return `data:text/javascript;base64,${Buffer.from(code).toString("base64")}`;
}
const authUrl = moduleUrl(await readFile(new URL("../app/lib/auth-client.ts", import.meta.url), "utf8"));
const auth = await import(authUrl);
const storageUrl = moduleUrl((await readFile(new URL("../app/lib/account-storage.ts", import.meta.url), "utf8")).replace('"./auth-client"', JSON.stringify(authUrl)));
const { accountStorageKey } = await import(storageUrl);
const previousWindow = globalThis.window;
const browser = new EventTarget();
browser.location = { origin: "https://rallymate.example", replace: () => undefined };
globalThis.window = browser;
beforeEach(() => auth.setAccountRequestState({ userId: null, csrfToken: null }));
after(() => { auth.invalidateAccountRequests(); if (previousWindow === undefined) delete globalThis.window; else globalThis.window = previousWindow; });

test("mutation requests use same-origin cookies and the current in-memory CSRF token", async () => {
  auth.setAccountRequestState({ userId: "user-a", csrfToken: "csrf-a" });
  let request;
  const response = await auth.accountFetch("/api/jobs/job-a", { method: "DELETE", credentials: "omit", headers: { "x-existing": "kept" } }, async (url, init) => {
    request = { url, init };
    return Response.json({ ok: true });
  });
  assert.equal(request.init.credentials, "same-origin");
  assert.equal(request.init.cache, "no-store");
  assert.equal(request.init.headers.get("x-csrf-token"), "csrf-a");
  assert.equal(request.init.headers.get("x-existing"), "kept");
  assert.deepEqual(await response.json(), { ok: true });
});

test("GET does not add CSRF and Request inputs preserve their method and headers", async () => {
  auth.setAccountRequestState({ userId: "user-a", csrfToken: "csrf-a" });
  const seen = [];
  const implementation = async (_url, init) => { seen.push(init); return new Response(null, { status: 204 }); };
  await auth.accountFetch("/api/auth/me", {}, implementation);
  await auth.accountFetch(new Request("https://rallymate.example/api/admin/users/a/status", { method: "PATCH", headers: { "content-type": "application/json" }, body: '{"status":"APPROVED"}' }), {}, implementation);
  assert.equal(seen[0].headers.has("x-csrf-token"), false);
  assert.equal(seen[1].method, "PATCH");
  assert.equal(seen[1].headers.get("content-type"), "application/json");
  assert.equal(seen[1].headers.get("x-csrf-token"), "csrf-a");
});

test("cross-origin destinations cannot receive cookies or CSRF", async () => {
  let sent = false;
  auth.setAccountRequestState({ userId: "user-a", csrfToken: "csrf-a" });
  await assert.rejects(auth.accountFetch("https://outside.example/api/jobs", { method: "POST" }, async () => { sent = true; return new Response(); }), { code: "cross_origin_request" });
  assert.equal(sent, false);
});

test("account changes abort pending requests even when the fetch implementation ignores cancellation", async () => {
  auth.setAccountRequestState({ userId: "user-a", csrfToken: "csrf-a" });
  const identity = auth.captureAccountIdentity();
  let resolve;
  let signal;
  const waiting = auth.accountFetch("/api/jobs", {}, (_url, init) => { signal = init.signal; return new Promise((done) => { resolve = done; }); });
  auth.setAccountRequestState({ userId: "user-b", csrfToken: "csrf-b" });
  assert.equal(signal.aborted, true);
  assert.equal(auth.isAccountIdentityCurrent(identity), false);
  resolve(Response.json({ private: "account-a" }));
  await assert.rejects(waiting, { name: "AbortError" });
});

test("an identity switch also rejects a response body that arrives after its headers", async () => {
  auth.setAccountRequestState({ userId: "user-a", csrfToken: "csrf-a" });
  let canceled = false;
  const delayed = new ReadableStream({ cancel() { canceled = true; } });
  const response = await auth.accountFetch("/api/jobs/a/report", {}, async () => new Response(delayed));
  const reading = response.text();
  auth.setAccountRequestState({ userId: "user-b", csrfToken: "csrf-b" });
  await assert.rejects(reading, { name: "AbortError" });
  assert.equal(canceled, true);
});

test("caller abort signals remain connected while streaming a response", async () => {
  const upstream = new AbortController();
  const response = await auth.accountFetch("/api/jobs/a/report", { signal: upstream.signal }, async () => new Response(new ReadableStream()));
  const reading = response.arrayBuffer();
  upstream.abort();
  await assert.rejects(reading, { name: "AbortError" });
});

test("replacing the session for the same user invalidates old request identities", () => {
  auth.setAccountRequestState({ userId: "user-a", csrfToken: "first-session" });
  const previous = auth.captureAccountIdentity();
  auth.setAccountRequestState({ userId: "user-a", csrfToken: "second-session" });
  assert.equal(auth.isAccountIdentityCurrent(previous), false);
  const current = auth.captureAccountIdentity();
  auth.setAccountRequestState({ userId: "user-a", csrfToken: "second-session" });
  assert.equal(auth.isAccountIdentityCurrent(current), true);
});

test("protected 401 and account 403 notify authentication while me and login do not recurse", async () => {
  const events = [];
  const observe = (event) => events.push(event.detail);
  browser.addEventListener(auth.ACCOUNT_AUTH_REQUIRED_EVENT, observe);
  try {
    for (const [path, status, code] of [
      ["/api/jobs", 401, "invalid_session"], ["/api/jobs", 403, "account_pending"],
      ["/api/jobs", 403, "password_change_required"], ["/api/jobs", 403, "csrf_invalid"],
      ["/api/admin/users", 403, "admin_required"], ["/api/auth/me", 401, "invalid_session"],
      ["/api/auth/login", 401, "invalid_credentials"],
    ]) {
      const response = await auth.accountFetch(path, {}, async () => Response.json({ detail: { code } }, { status }));
      await response.text();
    }
  } finally { browser.removeEventListener(auth.ACCOUNT_AUTH_REQUIRED_EVENT, observe); }
  assert.deepEqual(events, [{ status: 401, code: undefined }, { status: 403, code: "account_pending" },
    { status: 403, code: "password_change_required" }, { status: 403, code: "csrf_invalid" }]);
});

test("API errors expose the server's safe message and preserve structured error codes", async () => {
  await assert.rejects(auth.accountResponse(Response.json({ detail: { code: "last_admin", message: "不能删除最后一个有效管理员。" } }, { status: 409 })),
    { name: "AccountError", code: "last_admin", status: 409, message: "不能删除最后一个有效管理员。" });
  await assert.rejects(auth.accountResponse(new Response("internal stack trace", { status: 500 })), { message: "请求未完成，请稍后重试。" });
});

test("gateway and advice account errors also refresh account state", async () => {
  const codes = [];
  const observe = (event) => codes.push(event.detail.code);
  browser.addEventListener(auth.ACCOUNT_AUTH_REQUIRED_EVENT, observe);
  try {
    const response = await auth.accountFetch("/api/advice", { method: "POST" }, async () => Response.json({ error: "account_not_approved" }, { status: 403 }));
    await response.text();
    const csrf = await auth.accountFetch("/api/auth/login", { method: "POST" }, async () => Response.json({ detail: { code: "csrf_invalid" } }, { status: 403 }));
    await csrf.text();
  } finally { browser.removeEventListener(auth.ACCOUNT_AUTH_REQUIRED_EVENT, observe); }
  assert.deepEqual(codes, ["account_not_approved", "csrf_invalid"]);
});

test("account storage never resolves to a legacy public key and follows explicit snapshots", () => {
  assert.equal(accountStorageKey("records"), "records:account:anonymous");
  auth.setAccountRequestState({ userId: "user-a", csrfToken: "csrf-a" });
  assert.equal(accountStorageKey("records"), "records:account:user-a");
  auth.setAccountRequestState({ userId: "user-b", csrfToken: "csrf-b" });
  assert.equal(accountStorageKey("records", "user-a"), "records:account:user-a");
  assert.equal(accountStorageKey("records", null), "records:account:anonymous");
  assert.notEqual(accountStorageKey("records", "a/b"), accountStorageKey("records", "a%2Fb"));
});

test("next destinations accept local workspace links and reject open redirects and auth loops", () => {
  assert.equal(auth.safeNextPath("/?view=analysis&job=job-a"), "/?view=analysis&job=job-a");
  assert.equal(auth.safeNextPath("/annotate?job=job-a"), "/annotate?job=job-a");
  for (const path of ["https://outside.example", "//outside.example", "/\\outside.example", "/%2foutside.example", "/%5coutside.example", "javascript:alert(1)", "/login", "/register?next=/admin", "/api/auth/me", "/%0d%0aoutside"]) {
    assert.equal(auth.safeNextPath(path), "/?view=overview", path);
  }
});

test("account destinations enforce password change and approval before honoring next", () => {
  assert.equal(auth.accountDestination({ status: "APPROVED", must_change_password: false }, "/admin"), "/admin");
  for (const status of ["PENDING", "REJECTED", "SUSPENDED"]) assert.equal(auth.accountDestination({ status, must_change_password: false }, "/admin"), "/account-status");
  assert.equal(auth.accountDestination({ status: "APPROVED", must_change_password: true }, "/admin"), "/change-password");
});

test("document navigation is restricted to safe same-origin relative destinations", () => {
  let destination;
  browser.location.replace = (value) => { destination = value; };
  auth.replaceAccountLocation("/login?next=%2Fadmin");
  assert.equal(destination, "/login?next=%2Fadmin");
  for (const path of ["//outside.example", "https://outside.example", "/\\outside.example", "/login\n"]) assert.throws(() => auth.replaceAccountLocation(path), { code: "invalid_destination" });
});
