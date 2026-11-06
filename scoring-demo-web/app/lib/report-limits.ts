/** Same finite wire limit for report API bodies and exported/imported backups. */
export const MAX_REPORT_WIRE_BYTES = 128 * 1024 * 1024;

/** Server preparation is bounded at 300s; reserve 60s for transfer and decode. */
export const REPORT_READ_BUDGET_MS = 360_000;
