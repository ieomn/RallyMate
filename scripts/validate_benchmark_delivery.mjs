// Use the same parsers as the results screen, including complete proof decoding.
// This entry point reads bounded saved responses; it does not contact a service.
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath, pathToFileURL } from "node:url";

async function main() {
  const [stage, directory, jobId, digest] = process.argv.slice(2);
  if (!["summary", "report", "detail"].includes(stage) || !directory || !jobId || !/^[a-f0-9]{64}$/.test(digest ?? "")) throw new Error("Invalid validation arguments");
  const project = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
  const require = createRequire(path.join(project, "scoring-demo-web", "package.json"));
  const ts = require("typescript");
  const modules = new Map();
  const compiledUrl = file => {
    if (modules.has(file)) return modules.get(file);
    let js = file.endsWith(".json") ? `export default ${fs.readFileSync(file, "utf8")}`
      : ts.transpileModule(fs.readFileSync(file, "utf8"), { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
    js = js.replace(/from "([^"]+)"/g, (_match, name) => `from ${JSON.stringify(name.startsWith(".")
      ? compiledUrl(path.resolve(path.dirname(file), name.endsWith(".json") ? name : `${name}.ts`))
      : pathToFileURL(require.resolve(name)).href)}`);
    const url = `data:text/javascript;base64,${Buffer.from(js).toString("base64")}`;
    modules.set(file, url); return url;
  };
  const { parseReportSummary, parseReportOverview, parseReportActions, parseReportActionDetail } =
    await import(compiledUrl(path.join(project, "scoring-demo-web", "app", "lib", "segmented-report.ts")));
  const read = (name, limit) => {
    const file = path.join(directory, name + ".json");
    if (fs.statSync(file).size > limit) throw new Error(`${name} exceeds validation byte limit`);
    return JSON.parse(fs.readFileSync(file, "utf8"));
  };
  // Saved JSON is indented for review and can exceed its original wire length.
  // Retain finite read limits while the HTTP client enforces exact wire budgets.
  const summary = parseReportSummary(read("summary", 512 * 1024), jobId);
  if (summary.source.video_sha256.toLowerCase() !== digest) throw new Error("Summary video digest differs from uploaded file");
  let selectedAction = null, detail = null;
  if (stage !== "summary") {
    parseReportOverview(read("overview", 8 * 1024 * 1024), jobId, summary.source);
    const page = parseReportActions(read("actions", 2 * 1024 * 1024), jobId, summary.source, 0);
    selectedAction = page.actions.find(action => action.evidence_status === "available") ?? null;
    if (!selectedAction) throw new Error("First action page has no available evidence to verify");
    if (stage === "detail") detail = parseReportActionDetail(read("action-detail", 64 * 1024 * 1024), jobId, summary.source, selectedAction, 0);
  }
  process.stdout.write(JSON.stringify({ status: "validated", job_id: jobId, stage,
    source: summary.source, selected_action: selectedAction, pagination: detail?.pagination ?? null }));
}

main().catch(error => {
  // Do not dump data-URL stacks or proof bodies when a contract rejects input.
  process.stderr.write(`Delivery validation failed: ${String(error?.message ?? "unknown error").slice(0, 600)}\n`);
  process.exitCode = 1;
});
