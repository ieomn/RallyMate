import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import ts from "typescript";

const compile = source => `data:text/javascript;base64,${Buffer.from(ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText).toString("base64")}`;
const gateway = compile(fs.readFileSync(new URL("../worker/gateway.ts", import.meta.url), "utf8"));
let configSource = fs.readFileSync(new URL("../vite.config.ts", import.meta.url), "utf8");
configSource = configSource.replace(/import vinext[^;]+;/, "const vinext = () => ({});")
  .replace(/import \{ existsSync, readFileSync \}[^;]+;/, "const existsSync = () => false; const readFileSync = () => '';")
  .replace(/import \{ parseEnv \}[^;]+;/, "const parseEnv = () => ({});")
  .replace(/import \{ defineConfig, loadEnv[^;]+;/, "const defineConfig = config => config; const loadEnv = () => ({});")
  .replace(/import hostingConfig[^;]+;/, "const hostingConfig = {};")
  .replace(/import \{ sites \}[^;]+;/, "const sites = () => ({});")
  .replace('"./worker/gateway"', JSON.stringify(gateway))
  .replace('await import("@cloudflare/vite-plugin")', "{ cloudflare: () => ({}) }");
const { default: configure } = await import(compile(configSource));

async function withProxy(run, overrides = {}) {
  const values = { RALLYMATE_API_PROXY: "http://127.0.0.1:8001", RALLYMATE_API_KEY: "maintenance-key", RALLYMATE_LOCAL_TUNNEL: "0", RALLYMATE_INTERNAL_API_ORIGIN: "", ...overrides };
  const previous = Object.fromEntries(Object.keys(values).map(key => [key, process.env[key]]));
  Object.assign(process.env, values);
  try {
    const config = await configure({ mode: "test" });
    const [prefix, proxy] = Object.entries(config.server.proxy)[0];
    await run(proxy, new RegExp(prefix));
  } finally {
    for (const [key, value] of Object.entries(previous)) {
      if (value === undefined) delete process.env[key]; else process.env[key] = value;
    }
  }
}

function response() {
  return {
    writableEnded: false,
    writeHead(status, headers) { this.status = status; this.headers = headers; },
    end(body) { this.body = JSON.parse(body); this.writableEnded = true; },
  };
}

test("Vite shares the account allowlist and rejects anonymous SSR requests before proxying", async () => {
  await withProxy(async (proxy, prefix) => {
    assert.equal(proxy.target, "http://127.0.0.1:8001");
    for (const path of ["/api/auth/login", "/api/admin/users", "/api/records", "/api/records?limit=20&offset=0", "/v1/jobs", "/health/ready"]) assert.equal(prefix.test(path), true);
    for (const path of ["/api/advice", "/internal/maintenance/stop-executor", "/v10/jobs", "/api/records-extra"]) assert.equal(prefix.test(path), false);
    for (const path of ["/v1/jobs", "/api/records", "/api/records?limit=20&offset=0", "/api/admin/users", "/api/auth/me"]) {
      const result = response();
      assert.equal(await proxy.bypass({ method: "GET", url: path, headers: { host: "localhost:3017" } }, result), path);
      assert.equal(result.status, 401);
      assert.equal(result.writableEnded, true);
      assert.equal(result.headers["cache-control"], "private, no-store");
    }
    const invalid = response();
    await proxy.bypass({ method: "GET", url: "/api/auth/private", headers: { host: "localhost:3017", cookie: "rallymate_session=signed" } }, invalid);
    assert.equal(invalid.status, 404);
    for (const path of ["/api/auth?extra=1", "/api/admin?extra=1"]) {
      assert.equal(prefix.test(path), true);
      const result = response();
      await proxy.bypass({ method: "GET", url: path, headers: { host: "localhost:3017", cookie: "rallymate_session=signed" } }, result);
      assert.equal(result.status, 404);
    }
  });
});

test("Vite permits same-origin user mutations without altering credentials and refuses cross-origin writes", async () => {
  await withProxy(async proxy => {
    const headers = { host: "localhost:3017", cookie: "rallymate_session=signed", origin: "http://localhost:3017", authorization: "Bearer user-jwt", "x-csrf-token": "csrf" };
    const request = { method: "POST", url: "/api/auth/logout", headers };
    assert.equal(await proxy.bypass(request, response()), undefined);
    assert.deepEqual(request.headers, headers);
    for (const origin of [undefined, "https://attacker.example"]) {
      const result = response();
      await proxy.bypass({ ...request, headers: { ...headers, origin } }, result);
      assert.equal(result.status, 403);
    }
    assert.equal(await proxy.bypass({ method: "POST", url: "/api/auth/login", headers: { host: "localhost:3017", origin: "http://localhost:3017" } }, response()), undefined);
  });
});

test("Vite never lends the maintenance key, gates readiness on caller credentials and preserves response cookies", async () => {
  await withProxy(async proxy => {
    const listeners = {};
    proxy.configure({ on(name, callback) { listeners[name] = callback; } });
    // No request rewriting at all: every route, health included, keeps only the caller's credentials.
    assert.equal(listeners.proxyReq, undefined);
    const anonymousReady = response();
    assert.equal(await proxy.bypass({ method: "GET", url: "/health/ready", headers: { host: "localhost:3017" } }, anonymousReady), "/health/ready");
    assert.equal(anonymousReady.status, 401);
    assert.equal(await proxy.bypass({ method: "GET", url: "/health/ready", headers: { host: "localhost:3017", authorization: "Bearer operator-key" } }, response()), undefined);
    assert.equal(await proxy.bypass({ method: "GET", url: "/health/live", headers: { host: "localhost:3017" } }, response()), undefined);
    const cookies = ["rallymate_session=signed; HttpOnly; Path=/", "theme=dark; Expires=Wed, 21 Oct 2030 07:28:00 GMT; Path=/"];
    const upstream = { headers: { "set-cookie": cookies, "cache-control": "public" } };
    listeners.proxyRes(upstream);
    assert.deepEqual(upstream.headers["set-cookie"], cookies);
    assert.equal(upstream.headers["cache-control"], "private, no-store");
  });
});

test("Vite accepts only the fixed server-configured internal service", async () => {
  await withProxy(proxy => assert.equal(proxy.target, "http://api:8000"), { RALLYMATE_INTERNAL_API_ORIGIN: "http://api:8000" });
  await assert.rejects(withProxy(() => assert.fail("invalid internal proxy was configured"), {
    RALLYMATE_INTERNAL_API_ORIGIN: "http://api:8000/private",
  }), /invalid_internal_analysis_origin/);
});
