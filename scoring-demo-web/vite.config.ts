import vinext from "vinext";
import { existsSync, readFileSync } from "node:fs";
import { parseEnv } from "node:util";
import { defineConfig, loadEnv, type ProxyOptions } from "vite";
import { analysisRequestRejection, internalAnalysisOrigin } from "./worker/gateway";

// Restricted local preview environments may block FSEvents, so use polling for HMR.
const isCodexSeatbeltSandbox = process.env.CODEX_SANDBOX === "seatbelt";

const localBindingConfig = {
  main: "./worker/index.ts",
  compatibility_flags: ["nodejs_compat"],
};

export default defineConfig(async ({ mode }) => {
  // Vite does not populate process.env from .env files.  Load the complete
  // environment explicitly so the documented proxy override works in local
  // dev while still allowing a shell variable to win.
  const workerDevEnv = existsSync(".dev.vars") ? parseEnv(readFileSync(".dev.vars", "utf8")) : {};
  const loadedEnv = { ...workerDevEnv, ...loadEnv(mode, process.cwd(), "") };
  const localApiProxy = (
    internalAnalysisOrigin(process.env.RALLYMATE_INTERNAL_API_ORIGIN ?? loadedEnv.RALLYMATE_INTERNAL_API_ORIGIN) ||
    process.env.RALLYMATE_API_PROXY ||
    process.env.RALLYMATE_API_ORIGIN ||
    loadedEnv.RALLYMATE_API_PROXY ||
    loadedEnv.RALLYMATE_API_ORIGIN ||
    "http://127.0.0.1:8000"
  ).replace(/\/+$/, "");
  const localTunnel = process.env.RALLYMATE_LOCAL_TUNNEL || loadedEnv.RALLYMATE_LOCAL_TUNNEL;
  const accountProxy: ProxyOptions = {
    target: localApiProxy,
    changeOrigin: true,
    async bypass(request, response) {
      const headers = new Headers();
      for (const [name, value] of Object.entries(request.headers)) {
        if (value !== undefined) headers.set(name, Array.isArray(value) ? value.join(", ") : value);
      }
      const origin = `http://${request.headers.host || "localhost"}`;
      const url = new URL(request.url || "/", origin);
      if (url.origin !== origin) return false;
      const rejection = analysisRequestRejection(request.method || "GET", url, headers, { RALLYMATE_LOCAL_TUNNEL: localTunnel });
      if (!rejection) return;
      if (!response) return false;
      response.writeHead(rejection.status, Object.fromEntries(rejection.headers));
      response.end(await rejection.text());
      return request.url || "/";
    },
    configure(proxy) {
      // Credentials are always the caller's own: the dev proxy never lends a
      // server maintenance key to /health/ready or any other route.
      proxy.on("proxyRes", response => {
        response.headers["cache-control"] = "private, no-store";
        response.headers["x-content-type-options"] = "nosniff";
      });
    },
  };
  // Keep Wrangler and Miniflare state project-local. These are non-secret tool
  // settings; application environment belongs in ignored `.env*` files.
  process.env.WRANGLER_WRITE_LOGS ??= "false";
  process.env.WRANGLER_LOG_PATH ??= ".wrangler/logs";
  process.env.MINIFLARE_REGISTRY_PATH ??= ".wrangler/registry";

  // Wrangler snapshots its log path while the Cloudflare plugin is imported.
  const { cloudflare } = await import("@cloudflare/vite-plugin");

  return {
    // Keep content-hashed assets used by the running server and open browser
    // tabs during an in-place build; vinext's CLI reads this flag too.
    build: { emptyOutDir: false },
    server: {
      ...(isCodexSeatbeltSandbox
        ? { watch: { useFsEvents: false, usePolling: true } }
        : {}),
      // Local development keeps the browser same-origin while the Python API
      // remains independently deployable on AutoDL or behind a domain.
      proxy: {
        "^/(?:v1(?:[/?]|$)|health(?:[/?]|$)|api/(?:auth(?:[/?]|$)|admin(?:[/?]|$)|records(?:[/?]|$)))": accountProxy,
      },
    },
    plugins: [
      vinext(),
      cloudflare({
        viteEnvironment: { name: "rsc", childEnvironments: ["ssr"] },
        config: localBindingConfig,
      }),
    ],
  };
});
