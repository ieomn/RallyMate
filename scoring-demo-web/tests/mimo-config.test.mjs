import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import ts from "typescript";

const source = fs.readFileSync(new URL("../app/lib/mimo-config.ts", import.meta.url), "utf8");
const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const { resolveMimoConfig } = await import(`data:text/javascript;base64,${Buffer.from(js).toString("base64")}`);
const enabled = { MIMO_ADVICE_ENABLED: "1", MIMO_API_KEY: "test-paygo-placeholder" };

test("existing credentials do not enable billable advice without explicit opt-in", () => {
  for (const flag of [undefined, "", "0", "true", "yes"]) {
    assert.deepEqual(resolveMimoConfig({ ...enabled, MIMO_ADVICE_ENABLED: flag }), { status: "disabled" });
  }
});

test("enabled advice resolves only canonical paid API endpoints with the existing model", () => {
  const openai = resolveMimoConfig(enabled);
  assert.equal(openai.status, "ready");
  assert.equal(openai.config.url, "https://api.xiaomimimo.com/v1/chat/completions");
  assert.equal(openai.config.model, "mimo-v2.5-pro");
  const anthropic = resolveMimoConfig({ ...enabled, MIMO_PROVIDER: "anthropic" });
  assert.equal(anthropic.status, "ready");
  assert.equal(anthropic.config.url, "https://api.xiaomimimo.com/anthropic/v1/messages");
  assert.equal(resolveMimoConfig({ MIMO_ADVICE_ENABLED: "1" }).status, "not_configured");
  assert.equal(resolveMimoConfig({ ...enabled, MIMO_PROVIDER: "custom" }).status, "invalid_configuration");
});

test("Token Plan hosts and subscription credentials require a separate billing configuration", () => {
  for (const key of ["tp-placeholder", "ttp-placeholder", "TP-placeholder", " TTP-placeholder "]) {
    assert.deepEqual(resolveMimoConfig({ ...enabled, MIMO_API_KEY: key }), { status: "billing_configuration_required" });
  }
  for (const host of ["token-plan-cn.xiaomimimo.com", "token-plan.xiaomimimo.com"]) {
    for (const suffix of ["/v1", "/anthropic"]) {
      assert.deepEqual(resolveMimoConfig({ ...enabled, MIMO_BASE_URL: `https://${host}${suffix}` }), { status: "billing_configuration_required" });
    }
  }
});

test("URL normalization cannot smuggle alternate ports, credentials or redirect destinations", () => {
  const rejected = [
    "https://attacker.example/v1",
    "http://api.xiaomimimo.com/v1",
    "https://api.xiaomimimo.com:443/v1",
    "https://api.xiaomimimo.com:8443/v1",
    "https://name:password@api.xiaomimimo.com/v1",
    "https://api.xiaomimimo.com/v1?redirect=https://attacker.example",
    "https://api.xiaomimimo.com/v1#https://attacker.example",
    "https://api.xiaomimimo.com/v1/",
    "https://api.xiaomimimo.com/v1/chat/completions",
    "https://api.xiaomimimo.com/unused/../v1",
    "https://api.xiaomimimo.com/%76%31",
    "https://api.xiaomimimo.com.evil.example/v1",
    "https://api.xiaomimimo.com./v1",
    "https://api.xiaomimimo.com\\@attacker.example/v1",
    "not a URL",
  ];
  for (const base of rejected) {
    assert.deepEqual(resolveMimoConfig({ ...enabled, MIMO_BASE_URL: base }), { status: "invalid_configuration" }, base);
  }
  assert.equal(resolveMimoConfig({ ...enabled, MIMO_PROVIDER: "anthropic", MIMO_BASE_URL: "https://api.xiaomimimo.com/v1" }).status, "invalid_configuration");
});

test("configuration errors neither perform requests nor expose credentials in error results", () => {
  const previous = globalThis.fetch;
  let calls = 0;
  globalThis.fetch = () => { calls += 1; throw new Error("network forbidden in configuration resolution"); };
  try {
    const secret = ["ttp", "test-placeholder-never-real"].join("-");
    const result = resolveMimoConfig({ ...enabled, MIMO_API_KEY: secret });
    assert.equal(result.status, "billing_configuration_required");
    assert.ok(!JSON.stringify(result).includes(secret));
    assert.equal(resolveMimoConfig({ ...enabled, MIMO_API_KEY: "test\r\nInjected: value" }).status, "invalid_configuration");
    assert.equal(calls, 0);
  } finally { globalThis.fetch = previous; }
});
