import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { createServer } from "vite";
import rsc from "@vitejs/plugin-rsc";

const require = createRequire(import.meta.url);
const project = fileURLToPath(new URL("..", import.meta.url));

test("RSC server, SSR and browser resolve the patched project package instead of the bundled fallback", async () => {
  const server = await createServer({ configFile: false, root: project, plugins: [rsc({ serverHandler: false })], logLevel: "silent", server: { middlewareMode: true, watch: null } });
  try {
    for (const [environment, entry] of [["rsc", "server.edge"], ["ssr", "client.edge"], ["client", "client.browser"]]) {
      const resolved = await server.environments[environment].pluginContainer.resolveId(`@vitejs/plugin-rsc/vendor/react-server-dom/${entry}`, undefined, { scan: true });
      assert.ok(resolved?.id, `${environment} entry should resolve`);
      assert.match(resolved.id.replaceAll("\\", "/"), /\/node_modules\/react-server-dom-webpack\//);
      assert.doesNotMatch(resolved.id, /vendor\/react-server-dom/);
    }
    const versions = await Promise.all(["react", "react-dom", "react-server-dom-webpack"].map(async (name) => JSON.parse(await readFile(require.resolve(`${name}/package.json`), "utf8")).version));
    // These three packages must stay aligned, including security patch releases.
    assert.deepEqual(versions, ["19.2.9", "19.2.9", "19.2.9"]);
  } finally { await server.close(); }
});
