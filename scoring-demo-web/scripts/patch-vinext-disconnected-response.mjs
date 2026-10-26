/** Pinned vinext compatibility patch. Re-review this patch on a vinext upgrade.
 * A fetch may finish after the browser has already closed its Node response.
 * Do not pipe that body into a destroyed destination; cancel its upstream work.
 */
import fs from "node:fs";
import { createHash } from "node:crypto";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

export const VINEXT_VERSION = "1.0.0-beta.2";
const ORIGINAL_SHA256 = "8f4e3d3d04482d2c0fa0d566f0e3c76e66da6208bd86104879333b1c26f9f0bb";
const PATCHED_SHA256 = "62b9f44c7190e6697b30c2f0df41050e8aec7790ef8a4fced47706c1232f2ad8";
const ANCHOR = "async function sendWebResponse(webResponse, req, res, compress) {\n";
const REPLACEMENT = ANCHOR
  + "\t// RallyMate: a completed fetch can outlive its disconnected Node response.\n"
  + "\tif (res.destroyed || res.writableEnded || req.aborted) {\n"
  + "\t\tcancelResponseBody(webResponse);\n"
  + "\t\treturn;\n"
  + "\t}\n";
const sha256 = source => createHash("sha256").update(source).digest("hex");

export function patchedVinextSource(source, version) {
  if (version !== VINEXT_VERSION) throw new Error(`vinext response patch requires ${VINEXT_VERSION}; review the patch before upgrading.`);
  const digest = sha256(source);
  if (digest === PATCHED_SHA256) return source;
  if (digest !== ORIGINAL_SHA256 || source.split(ANCHOR).length !== 2) {
    throw new Error("vinext production server differs from the pinned source; review the response patch instead of applying it blindly.");
  }
  const patched = source.replace(ANCHOR, REPLACEMENT);
  if (sha256(patched) !== PATCHED_SHA256) throw new Error("vinext response patch output verification failed.");
  return patched;
}

export function patchInstalledVinext() {
  const target = new URL(import.meta.resolve("vinext/server/prod-server"));
  const packageJson = JSON.parse(fs.readFileSync(new URL("../../package.json", target), "utf8"));
  const original = fs.readFileSync(target, "utf8");
  const patched = patchedVinextSource(original, packageJson.version);
  if (patched === original) return false;
  fs.writeFileSync(fileURLToPath(target), patched, "utf8");
  return true;
}

if (process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url) {
  const changed = patchInstalledVinext();
  console.log(`vinext ${VINEXT_VERSION}: disconnected-response patch ${changed ? "applied" : "already verified"}.`);
}
