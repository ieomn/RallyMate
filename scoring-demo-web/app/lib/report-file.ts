/** Full evidence backups include source cards and event-bound observations. */
import { unpackReportBackup } from "./report-backup-codec";
import { MAX_REPORT_WIRE_BYTES } from "./report-limits";
export const MAX_REPORT_FILE_BYTES = MAX_REPORT_WIRE_BYTES;

export async function readReportJson(file: { size: number; text(): Promise<string> }): Promise<unknown> {
  if (!Number.isSafeInteger(file.size) || file.size < 0 || file.size > MAX_REPORT_FILE_BYTES) throw new Error("报告超过 128 MiB，请选择本站导出的单次训练报告。");
  const content = await file.text();
  if (new TextEncoder().encode(content).byteLength > MAX_REPORT_FILE_BYTES) throw new Error("报告超过 128 MiB，请选择本站导出的单次训练报告。");
  return unpackReportBackup(JSON.parse(content));
}
