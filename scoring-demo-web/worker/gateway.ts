export type GatewayEnv = Record<string, unknown> & { RALLYMATE_API_ORIGIN?: string; RALLYMATE_API_KEY?: string; MIMO_API_KEY?: string; RALLYMATE_LOCAL_TUNNEL?: string };
export const isLoopback = (host: string) => ["localhost", "127.0.0.1", "[::1]"].includes(host);
const reply = (error: string, status: number) => Response.json({ error }, { status, headers: { "cache-control": "no-store" } });

export async function proxyAnalysis(request: Request, env: GatewayEnv): Promise<Response> {
  const url = new URL(request.url);
  const id = "[a-zA-Z0-9_-]{8,80}";
  const readPath = new RegExp(`^/(?:health/(?:live|ready)|v1/(?:meta|techniques|jobs/${id}(?:/(?:demo-result|trajectory|technique-assessment|artifacts/(?:summary\\.json|frames\\.jsonl|indicator-features\\.jsonl|annotated\\.mp4|preview\\.jpg)))?))$`);
  const uploadRead = new RegExp(`^/v1/uploads/${id}$`);
  const uploadWrite = new RegExp(`^/v1/uploads/${id}/(?:complete|chunks/[0-9]{1,6})$`);
  const uploadAllowed = (request.method === "GET" && uploadRead.test(url.pathname)) ||
    (request.method === "POST" && (url.pathname === "/v1/uploads" || (uploadWrite.test(url.pathname) && url.pathname.endsWith("/complete")))) ||
    (request.method === "PUT" && uploadWrite.test(url.pathname) && /\/chunks\/[0-9]+$/.test(url.pathname));
  if (!uploadAllowed && !(request.method === "GET" && readPath.test(url.pathname)) && !(request.method === "POST" && url.pathname === "/v1/jobs")) return reply("route_not_allowed", 404);
  const browserOrigin = request.headers.get("origin");
  const tunnelOrigin = env.RALLYMATE_LOCAL_TUNNEL === "1" ? `https://${url.host}` : null;
  if (["POST", "PUT"].includes(request.method) && (request.headers.get("sec-fetch-site") === "cross-site" || (browserOrigin && browserOrigin !== url.origin && browserOrigin !== tunnelOrigin))) return reply("origin_not_allowed", 403);
  const origin = env.RALLYMATE_API_ORIGIN;
  if (!origin) return reply("analysis_service_not_configured", 503);
  let base: URL;
  try {
    base = new URL(origin);
    if (base.username || base.password || base.search || base.hash || !["", "/"].includes(base.pathname) || !["https:", "http:"].includes(base.protocol)) throw new Error();
    const localTunnel = env.RALLYMATE_LOCAL_TUNNEL === "1" && base.protocol === "http:" && isLoopback(base.hostname);
    if (!isLoopback(url.hostname) && !localTunnel && (base.protocol !== "https:" || isLoopback(base.hostname))) throw new Error();
  } catch { return reply("invalid_analysis_origin", 503); }
  const headers = new Headers();
  for (const name of ["content-type", "accept", "range", "if-range", "x-request-id", "x-chunk-sha256"]) { const value = request.headers.get(name); if (value) headers.set(name, value); }
  if (env.RALLYMATE_API_KEY) headers.set("authorization", `Bearer ${env.RALLYMATE_API_KEY}`);
  try {
    const init: RequestInit & { duplex?: string } = { method: request.method, headers, redirect: "manual", signal: AbortSignal.timeout(["POST", "PUT"].includes(request.method) ? 100000 : 30000) };
    if (["POST", "PUT"].includes(request.method)) { init.body = request.body; init.duplex = "half"; }
    const response = await fetch(new URL(url.pathname + url.search, base), init);
    if (response.status >= 300 && response.status < 400) return reply("unexpected_upstream_redirect", 502);
    const output = new Headers({ "cache-control": "private, no-store", "x-content-type-options": "nosniff" });
    for (const name of ["content-type", "content-length", "content-range", "accept-ranges", "content-disposition", "retry-after"]) { const value = response.headers.get(name); if (value) output.set(name, value); }
    return new Response(response.body, { status: response.status, headers: output });
  } catch { return reply("analysis_service_unavailable", 502); }
}
