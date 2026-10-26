// Only transport-owned containers enter this registry; Object.isFrozen alone
// cannot establish that nested values are immutable.
const immutable = new WeakSet<object>();
export function freezeReportTransport<T>(value: T): T {
  if (value && typeof value === "object" && !immutable.has(value)) {
    for (const child of Object.values(value)) freezeReportTransport(child);
    Object.freeze(value); immutable.add(value);
  }
  return value;
}
export function isImmutableReportTransport(value: unknown): value is object {
  return !!value && typeof value === "object" && immutable.has(value);
}
