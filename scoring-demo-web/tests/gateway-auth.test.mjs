import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import ts from "typescript";

const source = fs.readFileSync(new URL("../worker/gateway.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const moduleUrl = `data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`;
const { proxyAnalysis, isAnalysisRoute } = await import(moduleUrl);
const env = { RALLYMATE_API_ORIGIN: "http://127.0.0.1:8001", RALLYMATE_API_KEY: "server-maintenance-secret" };
const identifier = "0d492390-d77d-4a17-a742-ad3d54d431fa";
const credentials = { cookie: "rallymate_session=user-session; ui=dark", origin: "http://localhost", "x-csrf-token": "user-csrf" };

test("account and management routes have an exact method and path allowlist", () => {
  const accepted = [
    ["POST", "/api/auth/register"], ["POST", "/api/auth/login"], ["POST", "/api/auth/logout"],
    ["POST", "/api/auth/change-password"], ["GET", "/api/auth/me"], ["GET", "/api/admin/users"],
    ["GET", "/api/admin/stats"], ["PATCH", `/api/admin/users/${identifier}/status`],
    ["DELETE", `/api/admin/users/${identifier}`], ["POST", `/api/admin/users/${identifier}/reset-password`],
    ["GET", "/api/records"], ["GET", "/v1/jobs"], ["DELETE", `/v1/jobs/${identifier}`],
  ];
  for (const [method, path] of accepted) assert.equal(isAnalysisRoute(path, method), true, `${method} ${path}`);
  for (const [method, path] of [
    ["GET", "/api/auth/login"], ["POST", "/api/auth/me"], ["PUT", `/api/admin/users/${identifier}/status`],
    ["GET", `/api/admin/users/${identifier}/password_hash`], ["POST", "/api/admin/bootstrap"],
    ["DELETE", "/api/records"], ["GET", "/api/records/extra"], ["POST", "/internal/maintenance/stop-executor"],
    ["POST", "/api/auth/login/extra"], ["GET", "/api/admin/users%2f..%2fprivate"],
  ]) assert.equal(isAnalysisRoute(path, method), false, `${method} ${path}`);
});

test("anonymous requests cannot borrow the gateway key or consume an upload body", async t => {
  let consumed = false;
  t.mock.method(globalThis, "fetch", () => assert.fail("anonymous request reached upstream"));
  const body = new ReadableStream({ pull() { consumed = true; } }, { highWaterMark: 0 });
  const response = await proxyAnalysis(new Request(`http://localhost/v1/uploads/${identifier}/chunks/0`, {
    method: "PUT", body, duplex: "half", headers: { origin: "http://localhost" },
  }), env);
  assert.equal(response.status, 401);
  assert.equal(consumed, false);
  for (const path of ["/v1/jobs", "/api/auth/me", "/api/admin/users", "/api/records"]) {
    assert.equal((await proxyAnalysis(new Request(`http://localhost${path}`), env)).status, 401);
  }
});

test("user cookie, original bearer, Origin and CSRF reach PATCH and DELETE unchanged", async t => {
  const calls = [];
  t.mock.method(globalThis, "fetch", async (url, init) => {
    calls.push([String(url), init.method]);
    for (const [name, value] of Object.entries(credentials)) assert.equal(init.headers.get(name), value);
    assert.equal(init.headers.get("authorization"), "Bearer original-user-jwt");
    assert.equal(init.redirect, "manual");
    if (init.body) assert.equal(await new Response(init.body).text(), '{"status":"APPROVED"}');
    return Response.json({ ok: true });
  });
  for (const [method, path] of [["PATCH", `/api/admin/users/${identifier}/status`], ["DELETE", `/v1/jobs/${identifier}`]]) {
    const response = await proxyAnalysis(new Request(`http://localhost${path}`, {
      method, headers: { ...credentials, authorization: "Bearer original-user-jwt" },
      body: method === "PATCH" ? '{"status":"APPROVED"}' : null,
    }), env);
    assert.equal(response.status, 200);
    assert.equal(response.headers.get("cache-control"), "private, no-store");
  }
  assert.equal(calls.length, 2);
});

test("cross-origin mutations and missing cookie Origin are rejected before upstream", async t => {
  t.mock.method(globalThis, "fetch", () => assert.fail("cross-origin request reached upstream"));
  for (const method of ["PATCH", "DELETE"]) {
    const path = method === "PATCH" ? `/api/admin/users/${identifier}/status` : `/api/admin/users/${identifier}`;
    for (const headers of [{ ...credentials, origin: "https://attacker.example" }, { cookie: credentials.cookie, "x-csrf-token": "user-csrf" }]) {
      assert.equal((await proxyAnalysis(new Request(`http://localhost${path}`, { method, headers }), env)).status, 403);
    }
  }
});

test("login forwards separate Set-Cookie values and logout forwards their removal", async t => {
  const cookies = [
    "rallymate_session=signed; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=604800",
    "preference=dark; Expires=Wed, 21 Oct 2030 07:28:00 GMT; Path=/",
  ];
  t.mock.method(globalThis, "fetch", async (_url, init) => {
    assert.equal(init.headers.get("authorization"), null);
    const headers = new Headers({ "content-type": "application/json" });
    for (const cookie of cookies) headers.append("set-cookie", cookie);
    return new Response("{}", { headers });
  });
  const response = await proxyAnalysis(new Request("http://localhost/api/auth/login", {
    method: "POST", headers: { origin: "http://localhost", "content-type": "application/json" }, body: "{}",
  }), env);
  assert.equal(response.status, 200);
  assert.deepEqual(response.headers.getSetCookie(), cookies);
  assert.equal(response.headers.get("cache-control"), "private, no-store");
  cookies.splice(0, cookies.length, "rallymate_session=; HttpOnly; Path=/; Max-Age=0; SameSite=Lax");
  const logout = await proxyAnalysis(new Request("http://localhost/api/auth/logout", { method: "POST", headers: credentials }), env);
  assert.deepEqual(logout.headers.getSetCookie(), cookies);
});

test("health never borrows the server key: live is public, ready needs the caller's own bearer", async t => {
  const calls = [];
  t.mock.method(globalThis, "fetch", async (url, init) => {
    calls.push([new URL(url).pathname, init.headers.get("authorization")]);
    return Response.json({ status: "ok" });
  });
  const anonymousReady = await proxyAnalysis(new Request("http://localhost/health/ready"), env);
  assert.equal(anonymousReady.status, 401);
  assert.deepEqual(await anonymousReady.json(), { error: "authentication_required" });
  // A browser session alone is not an operator credential and is not upgraded to one.
  assert.equal((await proxyAnalysis(new Request("http://localhost/health/ready", { headers: { cookie: credentials.cookie } }), env)).status, 401);
  assert.equal((await proxyAnalysis(new Request("http://localhost/health/live"), env)).status, 200);
  assert.equal((await proxyAnalysis(new Request("http://localhost/health/ready", { headers: { authorization: "Bearer operator-supplied" } }), env)).status, 200);
  assert.deepEqual(calls, [["/health/live", null], ["/health/ready", "Bearer operator-supplied"]]);
  assert.ok(calls.every(([, authorization]) => authorization !== "Bearer server-maintenance-secret"));
  assert.equal(isAnalysisRoute("/health/other", "GET"), false);
  assert.equal((await proxyAnalysis(new Request("http://localhost/internal/maintenance/stop-executor", { method: "POST", headers: credentials }), env)).status, 404);
});

test("an incomplete upload can be cancelled by its owner through the gateway", async t => {
  const calls = [];
  assert.equal(isAnalysisRoute(`/v1/uploads/${identifier}`, "DELETE"), true);
  for (const path of [`/v1/uploads/${identifier}/chunks/0`, `/v1/uploads/${identifier}/complete`, "/v1/uploads", "/v1/uploads/short"]) {
    assert.equal(isAnalysisRoute(path, "DELETE"), false, path);
  }
  t.mock.method(globalThis, "fetch", async (url, init) => {
    calls.push([new URL(url).pathname, init.method, init.headers.get("cookie"), init.headers.get("x-csrf-token")]);
    return Response.json({ upload_id: identifier, cancelled: true });
  });
  assert.equal((await proxyAnalysis(new Request(`http://localhost/v1/uploads/${identifier}`, { method: "DELETE", headers: credentials }), env)).status, 200);
  assert.equal((await proxyAnalysis(new Request(`http://localhost/v1/uploads/${identifier}`, { method: "DELETE", headers: { ...credentials, origin: "https://attacker.example" } }), env)).status, 403);
  assert.equal((await proxyAnalysis(new Request(`http://localhost/v1/uploads/${identifier}`, { method: "DELETE", headers: { origin: "http://localhost" } }), env)).status, 401);
  assert.deepEqual(calls, [[`/v1/uploads/${identifier}`, "DELETE", credentials.cookie, "user-csrf"]]);
});

test("Worker dispatches account paths to gateway and leaves advice with the app router", async () => {
  let entry = fs.readFileSync(new URL("../worker/index.ts", import.meta.url), "utf8");
  entry = entry.replace(/import \{ handleImageOptimization[^;]+;/, "const handleImageOptimization = () => new Response(); const DEFAULT_DEVICE_SIZES = []; const DEFAULT_IMAGE_SIZES = [];")
    .replace(/import handler[^;]+;/, 'const handler = { fetch: () => Response.json({ route: "app" }) };')
    .replace(/import \{ proxyAnalysis[^;]+;/, 'const proxyAnalysis = () => Response.json({ route: "gateway" });');
  const js = ts.transpileModule(entry, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
  const { default: worker } = await import(`data:text/javascript;base64,${Buffer.from(js).toString("base64")}`);
  for (const path of ["/api/auth/me", "/api/admin/users", "/api/records", "/v1/jobs", "/health/live"]) {
    assert.deepEqual(await (await worker.fetch(new Request("http://localhost" + path), {}, {})).json(), { route: "gateway" });
  }
  assert.deepEqual(await (await worker.fetch(new Request("http://localhost/api/advice"), {}, {})).json(), { route: "app" });
});

test("internal API trust accepts only the fixed Docker service configured on the server", async t => {
  let calls = 0;
  t.mock.method(globalThis, "fetch", async (url, init) => {
    calls++;
    assert.equal(String(url), "http://api:8000/api/auth/me");
    assert.equal(init.headers.get("authorization"), "Bearer user-token");
    return Response.json({ ok: true });
  });
  const request = () => new Request("https://app.example.com/api/auth/me", { headers: { authorization: "Bearer user-token" } });
  for (const origin of ["http://api:8000", "http://api:8000/"]) {
    assert.equal((await proxyAnalysis(request(), { RALLYMATE_INTERNAL_API_ORIGIN: origin })).status, 200);
  }
  for (const origin of ["http://attacker:8000", "http://api:8001", "http://api:8000/private", "http://api:8000?url=http://attacker", "http://user:pass@api:8000", "http://127.0.0.1:8000", "https://api:8000", "http://api:8000@attacker"] ) {
    assert.equal((await proxyAnalysis(request(), { RALLYMATE_INTERNAL_API_ORIGIN: origin })).status, 503);
  }
  assert.equal((await proxyAnalysis(request(), { RALLYMATE_API_ORIGIN: "http://api:8000" })).status, 503);
  assert.equal(calls, 2);
});
