// `vite.config.ts` keeps `build.emptyOutDir: false` so a deploy can rebuild in
// place while the running server and open tabs still reference the previous
// content-hashed assets. Verification builds have no such reader; without this
// step every `npm test` stacked another full copy of each chunk into dist/.
import { rm } from "node:fs/promises";
import { fileURLToPath } from "node:url";

const dist = fileURLToPath(new URL("../dist/", import.meta.url));
await rm(dist, { recursive: true, force: true, maxRetries: 3 });
