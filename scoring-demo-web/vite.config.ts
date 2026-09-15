import vinext from "vinext";
import { existsSync, readFileSync } from "node:fs";
import { parseEnv } from "node:util";
import { defineConfig, loadEnv } from "vite";
import hostingConfig from "./.openai/hosting.json";
import { sites } from "./sites-vite-plugin";

const SITE_CREATOR_PLACEHOLDER_DATABASE_ID =
  "00000000-0000-4000-8000-000000000000";

const { d1, r2 } = hostingConfig;

// Restricted local preview environments may block FSEvents, so use polling for HMR.
const isCodexSeatbeltSandbox = process.env.CODEX_SANDBOX === "seatbelt";

const localBindingConfig = {
  main: "./worker/index.ts",
  compatibility_flags: ["nodejs_compat"],
  d1_databases: d1
    ? [
        {
          binding: d1,
          database_name: "site-creator-d1",
          database_id: SITE_CREATOR_PLACEHOLDER_DATABASE_ID,
        },
      ]
    : [],
  r2_buckets: r2
    ? [
        {
          binding: r2,
          bucket_name: "site-creator-r2",
        },
      ]
    : [],
};

export default defineConfig(async ({ mode }) => {
  // Vite does not populate process.env from .env files.  Load the complete
  // environment explicitly so the documented proxy override works in local
  // dev while still allowing a shell variable to win.
  const workerDevEnv = existsSync(".dev.vars") ? parseEnv(readFileSync(".dev.vars", "utf8")) : {};
  const loadedEnv = { ...workerDevEnv, ...loadEnv(mode, process.cwd(), "") };
  const localApiProxy = (
    process.env.RALLYMATE_API_PROXY ||
    process.env.RALLYMATE_API_ORIGIN ||
    loadedEnv.RALLYMATE_API_PROXY ||
    loadedEnv.RALLYMATE_API_ORIGIN ||
    "http://127.0.0.1:8000"
  ).replace(/\/+$/, "");
  const localApiKey = process.env.RALLYMATE_API_KEY || loadedEnv.RALLYMATE_API_KEY;
  // Keep Wrangler and Miniflare state project-local. These are non-secret tool
  // settings; application environment belongs in ignored `.env*` files.
  process.env.WRANGLER_WRITE_LOGS ??= "false";
  process.env.WRANGLER_LOG_PATH ??= ".wrangler/logs";
  process.env.MINIFLARE_REGISTRY_PATH ??= ".wrangler/registry";

  // Wrangler snapshots its log path while the Cloudflare plugin is imported.
  const { cloudflare } = await import("@cloudflare/vite-plugin");

  return {
    server: {
      ...(isCodexSeatbeltSandbox
        ? { watch: { useFsEvents: false, usePolling: true } }
        : {}),
      // Local development keeps the browser same-origin while the Python API
      // remains independently deployable on AutoDL or behind a domain.
      proxy: {
        "/v1": { target: localApiProxy, changeOrigin: true, headers: localApiKey ? { Authorization: `Bearer ${localApiKey}` } : undefined },
        "/health": { target: localApiProxy, changeOrigin: true, headers: localApiKey ? { Authorization: `Bearer ${localApiKey}` } : undefined },
      },
    },
    plugins: [
      vinext(),
      sites(),
      cloudflare({
        viteEnvironment: { name: "rsc", childEnvironments: ["ssr"] },
        config: localBindingConfig,
      }),
    ],
  };
});
