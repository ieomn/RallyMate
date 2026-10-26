import contract from "./formal-card-scoring-contract";

export const formalScoringPolicyVersion = contract.version;
export const formalScoreSemantics = contract.semantics;
export const isFormalScoringPolicy = (value: unknown) => typeof value === "string" && (contract.supported_versions as readonly string[]).includes(value);
export function formalConditionScore(policy: unknown, passed: number, total: number, grade: keyof typeof contract.grade_points): number | null {
  if (!isFormalScoringPolicy(policy) || !Number.isSafeInteger(total) || total <= 0
      || !Number.isSafeInteger(passed) || passed < 0 || passed > total) return null;
  return policy === contract.version ? 100 * passed / total : contract.grade_points[grade];
}
export const isAdvisoryQualityFlag = (value: unknown) => typeof value === "string"
  && (contract.advisory_quality_flags as readonly string[]).includes(value);

/** A declared derived ledger must cover every original frame, not a subset. */
export function validDerivedSelectionLedger(values: unknown[], required = false, expected?: unknown): boolean {
  const rows = values.map(value => value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {});
  if (required && (!Number.isSafeInteger(expected) || Number(expected) < 0)) return false;
  if (!required && !rows.some(row => "derived_selection_epoch" in row)) return true;
  return rows.length > 0 && rows.every(row => Number.isSafeInteger(row.derived_selection_epoch) && Number(row.derived_selection_epoch) >= 0
    && row.derived_selection_epoch === rows[0].derived_selection_epoch && (!required || row.derived_selection_epoch === expected));
}
