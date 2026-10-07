// Execute the frozen, unmodified main 6045752 client parsers. Reading fixtures
// makes this regression independent of newer clients on development branches.
import fs from "node:fs";
import { stripTypeScriptTypes } from "node:module";
import { fileURLToPath } from "node:url";

async function load(name) {
  const path = new URL(`../fixtures/legacy-web-v1/${name}.ts`, import.meta.url);
  const source = fs.readFileSync(path, "utf8");
  const js = stripTypeScriptTypes(source, { sourceUrl: fileURLToPath(path) });
  return import(`data:text/javascript;base64,${Buffer.from(js).toString("base64")}`);
}
const { footworkEpisodes } = await load("footwork-review");
const { motionAnalysisOf } = await load("motion-analysis");
const payload = JSON.parse(fs.readFileSync(0, "utf8"));
const result = {};
for (const [name, value] of Object.entries(payload)) {
  result[name] = {
    footwork: footworkEpisodes(value.footwork_review),
    motion: motionAnalysisOf(value.result ?? null, value.assessment ?? null, value.summary ?? null),
  };
}
process.stdout.write(JSON.stringify(result));
