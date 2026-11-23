// Runs the frontend suites with Node's built-in runner.
//   node scripts/run-tests.mjs          every suite (expects a fresh `dist/`)
//   node scripts/run-tests.mjs --unit   skips suites that render the built server
import { spawnSync } from "node:child_process";
import { readdirSync } from "node:fs";

// Suites that import the vinext production bundle from dist/.
const BUILD_DEPENDENT = new Set(["rendered-html.test.mjs"]);
const unit = process.argv.includes("--unit");
const files = readdirSync(new URL("../tests/", import.meta.url))
  .filter(name => name.endsWith(".test.mjs") && !(unit && BUILD_DEPENDENT.has(name)))
  .sort()
  .map(name => `tests/${name}`);

const result = spawnSync(process.execPath, ["--test", ...files], { stdio: "inherit", cwd: new URL("../", import.meta.url) });
process.exit(result.status ?? 1);
