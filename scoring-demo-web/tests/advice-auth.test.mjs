import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import ts from "typescript";

const modules = new Map();
function compiledUrl(file, supplied) {
  if (supplied === undefined && modules.has(file.href)) return modules.get(file.href);
  let js = file.pathname.endsWith(".json") ? `export default ${fs.readFileSync(file, "utf8")};`
    : ts.transpileModule(supplied ?? fs.readFileSync(file, "utf8"), { fileName: file.pathname,
      compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
  js = js.replace(/from "([^"]+)"/g, (_match, name) => `from ${JSON.stringify(name.startsWith(".")
    ? compiledUrl(new URL(name.endsWith(".json") ? name : `${name}.ts`, file)) : import.meta.resolve(name))}`);
  const url = `data:text/javascript;base64,${Buffer.from(js).toString("base64")}`;
  if (supplied === undefined) modules.set(file.href, url);
  return url;
}
async function route() {
  const file = new URL("../app/api/advice/route.ts", import.meta.url);
  return import(compiledUrl(file, fs.readFileSync(file, "utf8") + `\n// ${Math.random()}`));
}
const user = { id: "current-user", role: "USER", status: "APPROVED", must_change_password: false };
const session = () => Response.json({ user, csrf_token: "session-csrf" });
const cookieHeaders = { cookie: "rallymate_session=signed-user-cookie", origin: "http://localhost", "x-csrf-token": "session-csrf" };
const input = { technique: "步伐", skillLevel: "业余进阶", sessionGoal: "稳定性", observations: "怎样保持回位平衡？" };
const jobId = "job_12345678";
const evidence = { advice_context_version: "advice-context-v1", technique: "步伐", status: "ready", job_id: jobId,
  technique_assessment: { family_summary: { footwork: { observed_count: 1 } }, techniques: [{ family: "footwork", name_zh: "回位", status: "ready", observed: true, phase_statuses: [] }] } };
const request = (body = input, headers = cookieHeaders) => new Request("http://localhost/api/advice", {
  method: "POST", headers: { "content-type": "application/json", ...headers }, body: JSON.stringify(body),
});
function runtime(t) {
  const previous = globalThis.__env;
  globalThis.__env = { MIMO_ADVICE_ENABLED: "1", MIMO_PROVIDER: "openai", MIMO_API_KEY: "provider-test-key",
    MIMO_BASE_URL: "https://api.xiaomimimo.com/v1", RALLYMATE_API_ORIGIN: "http://trusted-backend.test",
    RALLYMATE_API_KEY: "must-never-read-user-data" };
  t.after(() => { globalThis.__env = previous; });
}

test("Docker advice uses the fixed internal API for session and evidence without a public round trip", async t => {
  runtime(t);
  globalThis.__env.RALLYMATE_INTERNAL_API_ORIGIN = "http://api:8000";
  globalThis.__env.RALLYMATE_API_ORIGIN = "https://public.example.com";
  const paths = [];
  t.mock.method(globalThis, "fetch", async (url, init) => {
    const target = new URL(url);
    assert.equal(target.origin, "http://api:8000");
    assert.equal(init.headers.cookie, cookieHeaders.cookie);
    paths.push(target.pathname);
    if (target.pathname === "/api/auth/me") return session();
    return Response.json({ status: "unavailable" });
  });
  const app = await route();
  assert.equal((await app.POST(request({ ...input, jobId }))).status, 200);
  assert.ok(paths.includes("/api/auth/me"));
  assert.ok(paths.some(path => path.startsWith(`/v1/jobs/${jobId}/`)));
});

test("invalid internal API configuration fails before any credential-bearing fetch", async t => {
  runtime(t);
  t.mock.method(globalThis, "fetch", () => assert.fail("invalid private upstream received credentials"));
  for (const origin of ["http://attacker:8000", "http://api:8000/private", "http://user:password@api:8000", "http://api:8000?next=https://attacker"]) {
    globalThis.__env.RALLYMATE_INTERNAL_API_ORIGIN = origin;
    const app = await route();
    const response = await app.POST(request());
    assert.equal(response.status, 503);
    assert.deepEqual(await response.json(), { error: "invalid_internal_analysis_origin" });
  }
});

test("anonymous advice cannot read the request body or call a backend/provider", async t => {
  runtime(t);
  let read = false;
  t.mock.method(globalThis, "fetch", () => assert.fail("anonymous advice performed a fetch"));
  const body = new ReadableStream({ pull() { read = true; } }, { highWaterMark: 0 });
  const app = await route();
  const response = await app.POST(new Request("http://localhost/api/advice", { method: "POST", body, duplex: "half",
    headers: { "content-type": "application/json", origin: "http://localhost" } }));
  assert.equal(response.status, 401);
  assert.equal(read, false);
  assert.equal(response.headers.get("cache-control"), "private, no-store");
});

test("expired, pending, suspended and temporary-password accounts are rejected before body parsing", async t => {
  runtime(t);
  for (const [me, expected] of [
    [() => Response.json({}, { status: 401 }), 401],
    [() => Response.json({ user: { ...user, status: "PENDING" }, csrf_token: "session-csrf" }), 403],
    [() => Response.json({ user: { ...user, status: "SUSPENDED" }, csrf_token: "session-csrf" }), 403],
    [() => Response.json({ user: { ...user, must_change_password: true }, csrf_token: "session-csrf" }), 403],
  ]) {
    let read = false;
    t.mock.method(globalThis, "fetch", async url => { assert.equal(new URL(url).pathname, "/api/auth/me"); return me(); });
    const app = await route();
    const response = await app.POST(new Request("http://localhost/api/advice", { method: "POST", duplex: "half",
      body: new ReadableStream({ pull() { read = true; } }, { highWaterMark: 0 }),
      headers: { "content-type": "application/json", ...cookieHeaders } }));
    assert.equal(response.status, expected);
    assert.equal(read, false);
  }
});

test("cookie advice requires trusted Origin and a CSRF token from the current session", async t => {
  runtime(t);
  t.mock.method(globalThis, "fetch", async url => { assert.equal(new URL(url).pathname, "/api/auth/me"); return session(); });
  const app = await route();
  for (const headers of [
    { ...cookieHeaders, origin: "https://attacker.example" },
    { cookie: cookieHeaders.cookie, "x-csrf-token": "session-csrf" },
    { cookie: cookieHeaders.cookie, origin: "http://localhost" },
    { ...cookieHeaders, "x-csrf-token": "another-session-csrf" },
  ]) assert.equal((await app.POST(request(input, headers))).status, 403);
  assert.equal((await app.POST(request())).status, 200);
});

test("bearer-only clients retain their user JWT and never receive the shared gateway key", async t => {
  runtime(t);
  t.mock.method(globalThis, "fetch", async (url, init) => {
    assert.equal(new URL(url).pathname, "/api/auth/me");
    assert.equal(init.headers.authorization, "Bearer current-user-jwt");
    assert.equal(init.headers.cookie, undefined);
    assert.equal(init.redirect, "manual");
    return session();
  });
  const app = await route();
  assert.equal((await app.POST(request(input, { authorization: "Bearer current-user-jwt", origin: "http://localhost" }))).status, 200);
});

test("unreadable foreign jobs and newly invalid sessions return 401/403/404 without a provider call", async t => {
  runtime(t);
  for (const denied of [401, 403, 404]) {
    let evidenceCalls = 0;
    t.mock.method(globalThis, "fetch", async (url, init) => {
      assert.equal(new URL(url).host, "trusted-backend.test");
      assert.equal(init.headers.cookie, cookieHeaders.cookie);
      if (new URL(url).pathname === "/api/auth/me") return session();
      evidenceCalls++;
      return Response.json({}, { status: denied });
    });
    const app = await route();
    assert.equal((await app.POST(request({ ...input, jobId }))).status, denied);
    assert.ok(evidenceCalls >= 1);
  }
});

test("session revocation during evidence reads is checked before external LLM dispatch", async t => {
  runtime(t);
  let checks = 0;
  t.mock.method(globalThis, "fetch", async url => {
    assert.equal(new URL(url).host, "trusted-backend.test", "LLM was called after revocation");
    const path = new URL(url).pathname;
    if (path === "/api/auth/me") return ++checks === 1 ? session() : Response.json({}, { status: 401 });
    if (path.endsWith("advice-evidence")) return Response.json(evidence);
    return Response.json({}, { status: 503 });
  });
  const app = await route();
  assert.equal((await app.POST(request({ ...input, jobId }))).status, 401);
  assert.equal(checks, 2);
});

test("authorized evidence receives user credentials while provider receives only its own key", async t => {
  runtime(t);
  let providerCalls = 0, checks = 0;
  t.mock.method(globalThis, "fetch", async (url, init) => {
    const target = new URL(url);
    if (target.host === "trusted-backend.test") {
      assert.equal(init.headers.cookie, cookieHeaders.cookie);
      assert.equal(init.headers["x-csrf-token"], "session-csrf");
      assert.equal(init.headers.authorization, undefined);
      if (target.pathname === "/api/auth/me") { checks++; return session(); }
      if (target.pathname.endsWith("advice-evidence")) return Response.json(evidence);
      return Response.json({}, { status: 503 });
    }
    providerCalls++;
    assert.equal(target.host, "api.xiaomimimo.com");
    assert.equal(init.headers["api-key"], "provider-test-key");
    assert.equal(init.headers.cookie, undefined);
    assert.equal(init.headers.authorization, undefined);
    assert.doesNotMatch(init.body, /signed-user-cookie|session-csrf|must-never-read-user-data/);
    return Response.json({ choices: [{ message: { content: '{"evidenceFactIds":["fact_0"],"nextStepIds":["replay"]}' } }] });
  });
  const app = await route();
  const response = await app.POST(request({ ...input, jobId }));
  assert.equal(response.status, 200);
  assert.equal((await response.json()).source, "mimo");
  assert.equal(providerCalls, 1);
  assert.equal(checks, 2);
});

test("authenticated oversized requests stop before evidence and redirects do not move credentials", async t => {
  runtime(t);
  t.mock.method(globalThis, "fetch", async (url, init) => {
    assert.equal(new URL(url).pathname, "/api/auth/me");
    assert.equal(init.redirect, "manual");
    return session();
  });
  const app = await route();
  assert.equal((await app.POST(request({ ...input, observations: "x".repeat(9000) }))).status, 413);
  t.mock.method(globalThis, "fetch", async () => new Response(null, { status: 302, headers: { location: "https://attacker.example" } }));
  assert.equal((await app.POST(request())).status, 503);
});

test("advice quota is keyed to the verified account, not a spoofable client address", async t => {
  runtime(t);
  let identity = "user-a";
  t.mock.method(globalThis, "fetch", async url => {
    assert.equal(new URL(url).pathname, "/api/auth/me");
    return Response.json({ user: { ...user, id: identity }, csrf_token: "session-csrf" });
  });
  const app = await route();
  const bearer = ip => ({ authorization: "Bearer current-user-jwt", origin: "http://localhost", "cf-connecting-ip": ip });
  // Rotating the forwarded address cannot mint a fresh quota for the same account.
  for (let i = 0; i < 5; i++) assert.equal((await app.POST(request(input, bearer(`203.0.113.${i}`)))).status, 200);
  const limited = await app.POST(request(input, bearer("198.51.100.77")));
  assert.equal(limited.status, 429);
  assert.deepEqual(await limited.json(), { error: "rate_limit_exceeded" });
  assert.ok(Number(limited.headers.get("retry-after")) > 0);
  // Another account sharing one address (NAT, missing header) is not locked out.
  identity = "user-b";
  for (let i = 0; i < 5; i++) assert.equal((await app.POST(request(input, bearer("203.0.113.0")))).status, 200);
  assert.equal((await app.POST(request(input, { authorization: "Bearer current-user-jwt", origin: "http://localhost" }))).status, 429);
});

test("invalid input does not consume quota and an exhausted account is refused before body reads", async t => {
  runtime(t);
  t.mock.method(globalThis, "fetch", async url => { assert.equal(new URL(url).pathname, "/api/auth/me"); return session(); });
  const app = await route();
  for (let i = 0; i < 7; i++) assert.equal((await app.POST(request({ ...input, technique: "invalid" }))).status, 400);
  for (let i = 0; i < 5; i++) assert.equal((await app.POST(request())).status, 200);
  let read = false;
  const response = await app.POST(new Request("http://localhost/api/advice", { method: "POST", duplex: "half",
    body: new ReadableStream({ pull() { read = true; } }, { highWaterMark: 0 }),
    headers: { "content-type": "application/json", ...cookieHeaders } }));
  assert.equal(response.status, 429);
  assert.equal(read, false);
});

test("CSRF comparison rejects equal-prefix, longer and oversized tokens", async t => {
  runtime(t);
  t.mock.method(globalThis, "fetch", async () => session());
  const app = await route();
  for (const token of ["session-csr", "session-csrf-extra", "x".repeat(129), "SESSION-CSRF"]) {
    const response = await app.POST(request(input, { ...cookieHeaders, "x-csrf-token": token }));
    assert.equal(response.status, 403);
    assert.deepEqual(await response.json(), { error: "csrf_invalid" });
  }
});
