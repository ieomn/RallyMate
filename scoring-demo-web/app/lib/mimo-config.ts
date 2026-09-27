export type MimoProvider = "openai" | "anthropic";
export type MimoConfig = {
  provider: MimoProvider;
  model: string;
  key: string;
  baseUrl: string;
  url: string;
};
export type MimoConfigResult =
  | { status: "ready"; config: MimoConfig }
  | { status: "disabled" | "not_configured" | "billing_configuration_required" | "invalid_configuration" };

const ENDPOINTS = {
  openai: { baseUrl: "https://api.xiaomimimo.com/v1", url: "https://api.xiaomimimo.com/v1/chat/completions" },
  anthropic: { baseUrl: "https://api.xiaomimimo.com/anthropic", url: "https://api.xiaomimimo.com/anthropic/v1/messages" },
} as const;
const SUBSCRIPTION_HOSTS = new Set(["token-plan.xiaomimimo.com", "token-plan-cn.xiaomimimo.com"]);

/** Server-only configuration. Resolving configuration never performs a request. */
export function resolveMimoConfig(runtime: Readonly<Record<string, string | undefined>>): MimoConfigResult {
  // An existing key alone must not enable billable website advice.
  if (runtime.MIMO_ADVICE_ENABLED?.trim() !== "1") return { status: "disabled" };
  const rawProvider = runtime.MIMO_PROVIDER?.trim().toLowerCase() || "openai";
  if (rawProvider !== "openai" && rawProvider !== "anthropic") return { status: "invalid_configuration" };
  const provider: MimoProvider = rawProvider;
  const endpoint = ENDPOINTS[provider];
  const configuredBase = runtime.MIMO_BASE_URL?.trim() || endpoint.baseUrl;
  const key = runtime.MIMO_API_KEY?.trim();

  // Token Plan subscriptions are not credentials for this website backend.
  if (key && /^(?:tp|ttp)-/i.test(key)) return { status: "billing_configuration_required" };
  try {
    if (SUBSCRIPTION_HOSTS.has(new URL(configuredBase).hostname.toLowerCase())) {
      return { status: "billing_configuration_required" };
    }
  } catch { return { status: "invalid_configuration" }; }

  // Exact matching also rejects explicit default ports, userinfo, fragments,
  // queries, encoded path variants and suffixes normalized by URL parsers.
  // The request URL below is always a constant, never the supplied string.
  if (configuredBase !== endpoint.baseUrl) return { status: "invalid_configuration" };
  if (!key) return { status: "not_configured" };
  if (/\s/.test(key)) return { status: "invalid_configuration" };
  const model = runtime.MIMO_MODEL?.trim() || "mimo-v2.5-pro";
  if (!/^[a-zA-Z0-9._:-]{1,128}$/.test(model)) return { status: "invalid_configuration" };
  return { status: "ready", config: { provider, model, key, ...endpoint } };
}
