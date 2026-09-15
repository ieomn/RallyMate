export type GatewayEnv = Record<string, unknown> & { RALLYMATE_API_ORIGIN?: string; RALLYMATE_API_KEY?: string; MIMO_API_KEY?: string; RALLYMATE_WEB_USER?: string; RALLYMATE_WEB_PASSWORD?: string };
export const isLoopback = (host: string) => ["localhost", "127.0.0.1", "[::1]"].includes(host);
const reply = (error: string, status: number) => Response.json({ error }, { status, headers: { "cache-control": "no-store" } });

/** Private single-user gateway. Multi-user hosting needs tenant ownership. */
export async function requireAccess(request: Request, env: GatewayEnv): Promise<Response | null> {
  if (isLoopback(new URL(request.url).hostname)) return null;
  if (!env.RALLYMATE_WEB_PASSWORD || env.RALLYMATE_WEB_PASSWORD.length < 16 || !env.RALLYMATE_WEB_USER) return reply("private_gateway_not_configured", 503);
  const supplied = request.headers.get("authorization") || "";
  const bytes = new TextEncoder().encode(`${env.RALLYMATE_WEB_USER}:${env.RALLYMATE_WEB_PASSWORD}`);
  const expected = "Basic " + btoa(Array.from(bytes, byte => String.fromCharCode(byte)).join(""));
  const hash = async (value: string) => new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value)));
  const [a, b] = await Promise.all([hash(supplied), hash(expected)]);
  let difference = 0; for (let i = 0; i < a.length; i++) difference |= a[i] ^ b[i];
  return difference === 0 ? null : new Response("请登录私人网球工作台", { status: 401, headers: { "www-authenticate": 'Basic realm="RallyMate", charset="UTF-8"', "cache-control": "no-store" } });
}

export async function proxyAnalysis(request: Request, env: GatewayEnv): Promise<Response> {
  const url = new URL(request.url);
  const id = "[a-zA-Z0-9_-]{8,80}";
  const readPath = new RegExp(`^/(?:health/(?:live|ready)|v1/(?:meta|techniques|jobs/${id}(?:/(?:demo-result|trajectory|technique-assessment|artifacts/(?:summary\\.json|frames\\.jsonl|indicator-features\\.jsonl|annotated\\.mp4|preview\\.jpg)))?))$`);
  if (!(request.method === "GET" && readPath.test(url.pathname)) && !(request.method === "POST" && url.pathname === "/v1/jobs")) return reply("route_not_allowed", 404);
  if (request.method === "POST" && (request.headers.get("sec-fetch-site") === "cross-site" || (request.headers.has("origin") && request.headers.get("origin") !== url.origin))) return reply("origin_not_allowed", 403);
  const origin = env.RALLYMATE_API_ORIGIN;
  if (!origin) return reply("analysis_service_not_configured", 503);
  let base: URL;
  try {
    base = new URL(origin);
    if (base.username || base.password || base.search || base.hash || !["", "/"].includes(base.pathname) || !["https:", "http:"].includes(base.protocol)) throw new Error();
    if (!isLoopback(url.hostname) && (base.protocol !== "https:" || isLoopback(base.hostname))) throw new Error();
  } catch { return reply("invalid_analysis_origin", 503); }
  const headers = new Headers();
  for (const name of ["content-type", "accept", "range", "if-range", "x-request-id"]) { const value = request.headers.get(name); if (value) headers.set(name, value); }
  if (env.RALLYMATE_API_KEY) headers.set("authorization", `Bearer ${env.RALLYMATE_API_KEY}`);
  try {
    const init: RequestInit & { duplex?: string } = { method: request.method, headers, redirect: "manual", signal: AbortSignal.timeout(request.method === "POST" ? 300000 : 30000) };
    if (request.method === "POST") { init.body = request.body; init.duplex = "half"; }
    const response = await fetch(new URL(url.pathname + url.search, base), init);
    if (response.status >= 300 && response.status < 400) return reply("unexpected_upstream_redirect", 502);
    const output = new Headers({ "cache-control": "private, no-store", "x-content-type-options": "nosniff" });
    for (const name of ["content-type", "content-length", "content-range", "accept-ranges", "content-disposition", "retry-after"]) { const value = response.headers.get(name); if (value) output.set(name, value); }
    return new Response(response.body, { status: response.status, headers: output });
  } catch { return reply("analysis_service_unavailable", 502); }
}
