// Read-only contract diagnostics: report rejected source lines without changing validation.
import fs from "node:fs";
import path from "node:path";
import ts from "typescript";

const cache = new Map();
function compiledUrl(url) {
  if (cache.has(url.href)) return cache.get(url.href);
  let source = fs.readFileSync(url, "utf8");
  if (!url.pathname.endsWith(".json")) source = source.split("\n").map((line, index) =>
    line.replaceAll("return null;", `return (console.log(${JSON.stringify(`${path.basename(url.pathname)}:${index + 1}`)}), null);`)).join("\n");
  let js = url.pathname.endsWith(".json") ? `export default ${source}` : ts.transpileModule(source, {
    fileName: url.pathname, compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  js = js.replace(/from "([^"]+)"/g, (_, name) => `from ${JSON.stringify(name.startsWith(".")
    ? compiledUrl(new URL(name.endsWith(".json") ? name : `${name}.ts`, url)) : import.meta.resolve(name))}`);
  const result = `data:text/javascript;base64,${Buffer.from(js).toString("base64")}`;
  cache.set(url.href, result); return result;
}
const { validatedTennisAssessment } = await import(compiledUrl(new URL("../app/lib/tennis-assessment.ts", import.meta.url)));
const raw = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const context = { jobId: raw.source_run_id, videoSha256: raw.source_video_sha256, durationMs: raw.source_duration_ms };
console.log(JSON.stringify({ accepted: Boolean(validatedTennisAssessment(raw, context)), source: path.basename(process.argv[2]) }));
