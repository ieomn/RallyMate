/** Cloudflare Worker entry point for the vinext-starter template. */
import { handleImageOptimization, DEFAULT_DEVICE_SIZES, DEFAULT_IMAGE_SIZES } from "vinext/server/image-optimization";
import handler from "vinext/server/app-router-entry";
import { requireAccess, proxyAnalysis, type GatewayEnv } from "./gateway";

interface Env extends GatewayEnv {
  ASSETS: Fetcher;
  DB: D1Database;
  RALLYMATE_API_ORIGIN?: string;
  RALLYMATE_API_KEY?: string;
  IMAGES: {
    input(stream: ReadableStream): {
      transform(options: Record<string, unknown>): {
        output(options: { format: string; quality: number }): Promise<{ response(): Response }>;
      };
    };
  };
}

interface ExecutionContext {
  waitUntil(promise: Promise<unknown>): void;
  passThroughOnException(): void;
}

// Image security config. SVG sources with .svg extension auto-skip the
// optimization endpoint on the client side (served directly, no proxy).
// To route SVGs through the optimizer (with security headers), set
// dangerouslyAllowSVG: true in next.config.js and uncomment below:
// const imageConfig: ImageConfig = { dangerouslyAllowSVG: true };

const worker = {
  async fetch(request: Request, env: Env = {} as Env, ctx: ExecutionContext): Promise<Response> {
    const url = new URL(request.url);
    // vinext's Node production server does not supply Cloudflare bindings.
    // Copy only the server settings the application uses from process.env.
    const processEnv: Record<string, string | undefined> = typeof process !== "undefined" ? process.env : {};
    const names = ["RALLYMATE_API_ORIGIN", "RALLYMATE_API_KEY", "RALLYMATE_WEB_USER", "RALLYMATE_WEB_PASSWORD", "MIMO_API_KEY", "MIMO_PROVIDER", "MIMO_MODEL", "MIMO_BASE_URL", "MIMO_ALLOWED_ORIGIN"];
    env = { ...Object.fromEntries(names.filter(name => processEnv[name] !== undefined).map(name => [name, processEnv[name]])), ...env } as Env;

    const denied = await requireAccess(request, env);
    if (denied) return denied;

    if (url.pathname === "/_vinext/image") {
      const allowedWidths = [...DEFAULT_DEVICE_SIZES, ...DEFAULT_IMAGE_SIZES];
      return handleImageOptimization(request, {
        fetchAsset: (path) => env.ASSETS.fetch(new Request(new URL(path, request.url))),
        transformImage: async (body, { width, format, quality }) => {
          const result = await env.IMAGES.input(body).transform(width > 0 ? { width } : {}).output({ format, quality });
          return result.response();
        },
      }, allowedWidths);
    }

    (globalThis as typeof globalThis & { __env?: Record<string, unknown> }).__env = env as unknown as Record<string, unknown>;
    if (url.pathname.startsWith("/v1/") || url.pathname.startsWith("/health/")) return proxyAnalysis(request, env);
    return handler.fetch(request, env, ctx);
  },
};

export default worker;
